"""Main runner — orchestrates discovery -> state -> rule -> decision -> paper trade.

One-shot mode (--once): run a single decision cycle and exit.
Continuous mode: loop, sleep between cycles, manage mark-to-market
and settlement.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from .binance_client import fetch_5m_close_at, fetch_recent_5m_klines
from .config import Settings
from .models import (
    DecisionKind,
    DecisionSnapshot,
    MarkToMarket,
    PositionStatus,
    Side,
    Stage,
    Timeframe,
)
from .paper_broker import PaperBroker, PaperBrokerError
from .polymarket_clob_client import fetch_pair_orderbook, levels_to_json
from .orderbook_stream import OrderbookStream
from .polymarket_discovery import discover_market
from .probability_rules import ProbabilityRules
from .risk_manager import RiskManager
from .round_state import build_round_state
from .rule_whitelist import RuleWhitelist
from .settlement import mark_position_settled, settle_position
from .signal_engine import build_decision
from .storage import Storage
from .telegram_reports import TelegramReportService
from .url_parser import UrlParserError, parse_market_url

log = logging.getLogger("polymarket_round_bot")

# Need 16 completed 15m rounds (48 closed 5m candles) plus current in-round
# candle(s) for research-compatible volatility and state construction.
BINANCE_KLINE_LIMIT: Final[int] = 60


class RunnerError(RuntimeError):
    """Raised on a non-recoverable runner error."""


def current_expected_slug(timeframe: Timeframe, *, now_utc: datetime | None = None) -> str:
    """Slug of the currently active market for ``timeframe``.

    Slug timestamp = window START (verified 2026-06-04).
    Boundary is floored: 08:29:59 still belongs to the 08:15 window.
    """
    interval_seconds = 5 * 60 if timeframe == Timeframe.M5 else 15 * 60
    now = int((now_utc or datetime.now(UTC)).timestamp())
    boundary = (now // interval_seconds) * interval_seconds
    return f"btc-updown-{timeframe.value}-{boundary}"


def slug_window(slug: str) -> tuple[Timeframe, datetime, datetime]:
    """Parse ``slug`` and return (timeframe, window_start_utc, window_end_utc).

    Used by stale-position settlement to determine when a market window
    has fully ended (and Binance fallback is therefore safe to apply).
    """
    parsed = parse_market_url(slug)
    interval_seconds = 5 * 60 if parsed.timeframe == Timeframe.M5 else 15 * 60
    start = datetime.fromtimestamp(parsed.timestamp, tz=UTC)
    end = datetime.fromtimestamp(parsed.timestamp + interval_seconds, tz=UTC)
    return parsed.timeframe, start, end


class Runner:
    def __init__(
        self,
        *,
        settings: Settings,
        storage: Storage,
        rules: ProbabilityRules,
        broker: PaperBroker,
        risk: RiskManager,
        slug: str,
        timeframe: Timeframe | None = None,
        rule_policy: RuleWhitelist | None = None,
        orderbook_stream: OrderbookStream | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._rules = rules
        self._broker = broker
        self._risk = risk
        self._slug = slug
        # When set, run_continuously() advances self._slug to the
        # current window on every cycle. None = explicit-URL mode
        # (slug stays fixed for the lifetime of the runner).
        self._timeframe = timeframe
        self._rule_policy = rule_policy
        self._orderbook_stream = orderbook_stream
        self._telegram_reports = TelegramReportService(settings, storage)
        self._current_market_tokens: tuple[str, str] | None = None

    # === Orderbook fetching ===

    def _fetch_orderbook(self, market: Any) -> Any:
        """Fetch orderbook: WS cache first, REST fallback."""
        from .models import OrderbookLevel, OrderbookSnapshot, PairOrderbook

        # Update WS subscription if market changed
        if self._orderbook_stream:
            new_tokens = (market.up_token_id, market.down_token_id)
            if self._current_market_tokens != new_tokens:
                if self._current_market_tokens:
                    self._orderbook_stream.unsubscribe(list(self._current_market_tokens))
                self._orderbook_stream.subscribe(list(new_tokens))
                self._current_market_tokens = new_tokens

        # Try WS cache first
        if self._orderbook_stream and self._orderbook_stream.is_connected():
            up_book = self._orderbook_stream.get_book(market.up_token_id)
            down_book = self._orderbook_stream.get_book(market.down_token_id)
            if up_book and down_book and up_book.best_ask is not None and down_book.best_ask is not None:
                now = datetime.now(UTC)
                up = OrderbookSnapshot(
                    token_id=market.up_token_id,
                    best_bid=up_book.best_bid,
                    best_ask=up_book.best_ask,
                    spread=up_book.spread,
                    bid_size=None,
                    ask_size=None,
                    top_5_bids=[],
                    top_5_asks=[],
                    liquidity_usd_estimate=None,
                    received_at_utc=now,
                )
                down = OrderbookSnapshot(
                    token_id=market.down_token_id,
                    best_bid=down_book.best_bid,
                    best_ask=down_book.best_ask,
                    spread=down_book.spread,
                    bid_size=None,
                    ask_size=None,
                    top_5_bids=[],
                    top_5_asks=[],
                    liquidity_usd_estimate=None,
                    received_at_utc=now,
                )
                return PairOrderbook(up=up, down=down, received_at_utc=now)

        # Fallback to REST
        return fetch_pair_orderbook(
            market.up_token_id,
            market.down_token_id,
            timeout=self._settings.http_timeout_seconds,
            user_agent=self._settings.http_user_agent,
        )

    # === One cycle ===

    def run_one_cycle(self, *, now_utc: datetime | None = None) -> DecisionSnapshot:
        now = now_utc or datetime.now(UTC)
        log.info("run_one_cycle slug=%s", self._slug)

        # 1. Discovery
        market, alignment = discover_market(
            self._slug,
            timeout=self._settings.http_timeout_seconds,
            user_agent=self._settings.http_user_agent,
        )
        log.info("market_discovered slug=%s alignment=%s", market.slug, alignment.alignment)

        # 2. Binance state
        binance = fetch_recent_5m_klines(
            self._settings.btc_symbol,
            limit=BINANCE_KLINE_LIMIT,
            timeout=self._settings.http_timeout_seconds,
            user_agent=self._settings.http_user_agent,
        )
        log.info(
            "binance_loaded symbol=%s candles=%d current=%.2f",
            binance.symbol,
            len(binance.candles),
            float(binance.current_price),
        )

        # 3. Round state
        state = build_round_state(binance, market, now_utc=now)
        log.info(
            "round_state stage=%s side=%s dist_bucket=%s vol_bucket=%s pattern=%s secs_to_expiry=%d",
            state.stage.value,
            state.current_side.value,
            state.distance_bucket.value,
            state.volatility_bucket.value,
            state.candle_pattern,
            state.seconds_to_expiry,
        )

        # 4. Rule lookup
        # CUSTOM_5M_STATE has no internal candles; lookup will miss all
        # patterns unless rules for that stage exist.
        lookup = self._rules.lookup(
            stage=state.stage,
            current_side=state.current_side,
            distance_bucket=state.distance_bucket,
            volatility_bucket=state.volatility_bucket,
            pattern=state.candle_pattern,
            min_samples=self._settings.min_samples,
            min_historical_probability=self._settings.min_historical_probability,
            require_usable_signal=self._settings.require_usable_signal,
        )
        log.info(
            "rule_lookup match_type=%s rule_id=%s prob=%s samples=%d no_trade_reasons=%s",
            lookup.match_type.value,
            lookup.rule.rule_id if lookup.rule else None,
            lookup.historical_probability,
            lookup.samples,
            lookup.no_trade_reasons,
        )

        # 5. Orderbook — WS cache first, REST fallback
        pair = self._fetch_orderbook(market)
        log.info(
            "orderbook up_bid=%s up_ask=%s down_bid=%s down_ask=%s ws=%s",
            pair.up.best_bid,
            pair.up.best_ask,
            pair.down.best_bid,
            pair.down.best_ask,
            "ws" if self._orderbook_stream and self._orderbook_stream.is_connected() else "rest",
        )

        # 6. Risk
        # Build the open-positions list from BOTH the in-memory broker
        # AND persistent storage. The storage list is critical for
        # restart safety: if the bot was restarted, _broker._open is
        # empty but storage still holds any OPEN position, and a new
        # trade on that slug must be rejected. Deduped by (slug, side).
        open_positions_set: set[tuple[str, Side]] = set()
        for p in self._broker.open_positions:
            open_positions_set.add((p.market_slug, p.selected_side))
        for p in self._storage.list_open_positions():
            open_positions_set.add((p.market_slug, p.selected_side))
        open_positions = list(open_positions_set)
        # Daily realised PnL = sum of today's settlements
        today_iso = now.date().isoformat()
        daily_pnl = self._daily_realized_pnl(today_iso)
        risk_decision = self._risk.evaluate(
            candidate_market_slug=market.slug,
            candidate_side=lookup.recommended_side or Side.UP,
            open_positions=open_positions,
            daily_realized_pnl=daily_pnl,
        )
        log.info(
            "risk allowed=%s reject=%s open=%d daily_pnl=%.4f",
            risk_decision.allowed,
            risk_decision.reject_reason,
            risk_decision.open_positions_count,
            float(daily_pnl),
        )

        # 7. Decision
        # For CUSTOM_5M_STATE we still allow the engine to try, but
        # rule lookup will most likely return no_match.
        if state.stage == Stage.CUSTOM_5M_STATE:
            # 5m stage is always allowed to attempt lookup; engine
            # will skip with no_rule_for_state if no rule.
            pass

        decision = build_decision(
            settings=self._settings,
            state=state,
            market=market,
            orderbook=pair,
            lookup=lookup,
            risk_allowed=risk_decision.allowed,
            risk_reject_reason=risk_decision.reject_reason,
            open_positions_count=risk_decision.open_positions_count,
            daily_realized_pnl=daily_pnl,
            metadata_received_at_utc=datetime.now(UTC),
            binance_received_at_utc=binance.received_at_utc,
            now_utc=now,
            rule_policy=self._rule_policy,
        )
        log.info("decision=%s reason=%s", decision.decision.value, decision.reason)

        # 8. Open position if TRADE
        if decision.decision == DecisionKind.TRADE and decision.side is not None:
            sel_ob = pair.up if decision.side == Side.UP else pair.down
            try:
                self._broker.open_position(
                    decision,
                    round_open_price=state.round_open_price,
                    btc_price_at_entry=state.current_btc_price,
                    distance_bucket=state.distance_bucket,
                    volatility_bucket=state.volatility_bucket,
                    pattern=state.candle_pattern,
                    stage=state.stage,
                    seconds_to_expiry=state.seconds_to_expiry,
                    entry_best_bid=sel_ob.best_bid or decision.market_ask or Decimal("0"),
                )
            except PaperBrokerError as e:
                # Strict v1: if the broker rejects (e.g. duplicate
                # position, edge case the risk check missed), convert
                # the decision to SKIP and persist it. The phantom
                # "TRADE in DB with no position" bug 2026-06-07 must
                # not happen again.
                log.warning("paper_broker_rejected err=%s", e)
                decision.decision = DecisionKind.SKIP
                decision.reason = f"paper_broker_rejected:{e}"
            except Exception as e:
                # Non-PaperBrokerError: log and re-raise so the cycle
                # loop catches it. Don't silently swallow.
                log.error("open_position_failed err=%s", e)
                raise

        # 9. Snapshot + persist
        snap = self._build_snapshot(
            state=state,
            market=market,
            orderbook=pair,
            lookup=lookup,
            decision=decision,
            risk_decision=risk_decision,
            binance=binance,
            metadata_received_at_utc=datetime.now(UTC),
            now=now,
        )
        self._storage.insert_decision(snap)

        # Persist position (if any)
        for p in self._broker.open_positions:
            if p.market_slug == market.slug and p.status == PositionStatus.OPEN:
                self._storage.upsert_position(p)

        return snap

    # === Continuous mode ===

    def run_continuously(
        self,
        *,
        poll_interval_seconds: int = 5,
        max_iterations: int | None = None,
    ) -> None:
        log.info("continuous_mode slug=%s poll=%ds", self._slug, poll_interval_seconds)
        i = 0
        while max_iterations is None or i < max_iterations:
            i += 1
            # Advance slug at :00/:15/:30/:45 boundaries (15m) or
            # every 5m boundary. Without this, the bot kept polling
            # the same expired market (bug 2026-06-06).
            self._maybe_refresh_slug()
            try:
                self.run_one_cycle()
                self._settle_due_positions()
                self._mark_open_positions()
            except Exception as e:
                log.exception("cycle_error err=%s", e)
            try:
                if self._telegram_reports.maybe_send():
                    log.info("telegram_report_sent")
            except Exception as e:
                log.exception("telegram_report_error err=%s", e)
            time.sleep(poll_interval_seconds)

    def _maybe_refresh_slug(self) -> None:
        """Update self._slug to the currently active window.

        Only active when the runner was constructed with ``timeframe``
        (auto-discovery mode). In explicit-URL mode, the slug stays
        fixed and this method is a no-op.
        """
        if self._timeframe is None:
            return
        expected = current_expected_slug(self._timeframe)
        if expected != self._slug:
            log.info("slug_advance from=%s to=%s", self._slug, expected)
            self._slug = expected

    # === Mark-to-market ===

    def _mark_open_positions(self) -> None:
        for p in self._broker.open_positions:
            try:
                book = fetch_pair_orderbook(
                    p.token_id, p.token_id,  # we just need one side for MtM
                    timeout=self._settings.http_timeout_seconds,
                    user_agent=self._settings.http_user_agent,
                )
            except Exception:
                # Fall back to the up/down books we have
                continue
            # We fetched only one token id; pick whichever side
            snap_ob = book.up if book.up.token_id == p.token_id else book.down
            best_bid = snap_ob.best_bid
            shares = p.shares
            exit_value = (best_bid * shares) if best_bid is not None else None
            unrealized = (exit_value - p.entry_size_usd) if exit_value is not None else None
            mtm = MarkToMarket(
                position_id=p.position_id,
                timestamp_utc=datetime.now(UTC),
                best_bid=best_bid,
                best_ask=snap_ob.best_ask,
                mid_price=(
                    (best_bid + snap_ob.best_ask) / 2
                    if best_bid is not None and snap_ob.best_ask is not None
                    else None
                ),
                estimated_exit_value_bid=exit_value,
                unrealized_pnl_bid=unrealized,
                btc_price=None,
                distance_from_round_open=None,
                seconds_to_expiry=None,
            )
            self._storage.insert_mtm(mtm)

    # === Settlement ===

    def _settle_due_positions(self) -> None:
        # Include positions loaded from storage but not yet in the
        # broker (e.g. from a previous bot run). Deduplicate by
        # position_id to avoid double-settling.
        seen: set[str] = set()
        candidates: list[Any] = []
        for p in list(self._broker.open_positions):
            if p.position_id not in seen:
                seen.add(p.position_id)
                candidates.append(p)
        for p in self._storage.list_open_positions():
            if p.position_id not in seen:
                seen.add(p.position_id)
                candidates.append(p)

        for p in candidates:
            settled = self._try_settle_one(p)
            if (
                settled is not None
                and (p.market_slug, p.selected_side) in self._broker._open
            ):
                self._broker.close_position(p.market_slug, p.selected_side)

    def _try_settle_one(self, p: Any) -> Any:
        """Try to settle one open position. Returns the Settlement, or None.

        Strategy:
        1. Re-discover the market via Gamma. If it returns and is closed,
           use the resolved outcome (authoritative).
        2. If Gamma has dropped the market (which happens ~24h after
           the round ended) AND the round window has fully ended AND
           the grace period has elapsed, fall back to Binance close
           of the last 5m candle at the window boundary. This prevents
           positions from being stuck open forever (bug 2026-06-06).
        3. If neither source is available, leave the position open
           and try again next cycle.
        """
        market = None
        try:
            market, _ = discover_market(
                p.market_slug,
                timeout=self._settings.http_timeout_seconds,
                user_agent=self._settings.http_user_agent,
            )
        except Exception as e:
            log.warning("settle_discover_failed slug=%s err=%s", p.market_slug, e)

        if market is not None and not market.closed:
            # Round still active in Gamma; not due yet.
            return None

        # Determine the final_btc_price.
        final_btc: Decimal | None = None
        resolved: Side | None = None
        if market is not None and market.closed:
            resolved = market.resolved_outcome
            final_btc = (
                market_resolved_btc_price(market, p.round_open_price)
                or p.round_open_price
            )
        else:
            # Gamma failed or returned stale data: try Binance fallback.
            now = datetime.now(UTC)
            try:
                _, win_start, win_end = slug_window(p.market_slug)
            except Exception as e:
                log.warning(
                    "settle_slug_parse_failed slug=%s err=%s", p.market_slug, e
                )
                return None
            grace = timedelta(seconds=self._settings.binance_fallback_grace_seconds)
            if now < win_end + grace:
                # Window not yet fully over (or still in grace period).
                return None
            binance_close = fetch_5m_close_at(
                self._settings.btc_symbol,
                target_utc=win_end,
                timeout=self._settings.http_timeout_seconds,
                user_agent=self._settings.http_user_agent,
            )
            if binance_close is None:
                log.warning(
                    "settle_no_binance_fallback slug=%s win_end=%s",
                    p.market_slug,
                    win_end.isoformat(),
                )
                return None
            final_btc = binance_close
            # resolve_outcome derived from final_btc vs round_open (see
            # settlement._resolve_outcome: UP if final > open, else DOWN).
            log.info(
                "settle_binance_fallback slug=%s round_open=%s final_btc=%s",
                p.market_slug,
                p.round_open_price,
                final_btc,
            )

        if final_btc is None:
            return None

        try:
            settlement = settle_position(
                position=p,
                polymarket_resolved=resolved,
                final_btc_price=final_btc,
                round_close_price=final_btc,
            )
        except ValueError as e:
            log.warning("settle_skipped slug=%s err=%s", p.market_slug, e)
            return None
        self._storage.insert_settlement(settlement)
        settled_pos = mark_position_settled(p)
        self._storage.upsert_position(settled_pos)
        log.info(
            "settled slug=%s side=%s won=%s pnl=%.4f quality=%s source=%s",
            p.market_slug,
            p.selected_side.value,
            settlement.won,
            float(settlement.realized_pnl_usd),
            settlement.trade_quality.value,
            settlement.settlement_source.value,
        )
        return settlement

    # === Helpers ===

    def _daily_realized_pnl(self, today_iso: str) -> Decimal:
        settlements = self._storage.list_settlements(since_iso=f"{today_iso}T00:00:00+00:00")
        return sum((s.realized_pnl_usd for s in settlements), Decimal("0"))

    def _build_snapshot(
        self,
        *,
        state: Any,
        market: Any,
        orderbook: Any,
        lookup: Any,
        decision: Any,
        risk_decision: Any,
        binance: Any,
        metadata_received_at_utc: datetime,
        now: datetime,
    ) -> DecisionSnapshot:
        # Pull the orderbook for the side we checked (recommended_side)
        side_checked = lookup.recommended_side or decision.side or Side.UP
        selected_ob = orderbook.up if side_checked == Side.UP else orderbook.down
        opposite_ob = orderbook.down if side_checked == Side.UP else orderbook.up

        # Binanace data age: from latest closed candle
        last_candle = binance.candles[-1] if binance.candles else None
        if last_candle is not None:
            candle_close_time = last_candle.open_time_utc + timedelta(minutes=5)
            binance_age = Decimal(str(max(0, (now - candle_close_time).total_seconds())))
        else:
            binance_age = Decimal("0")

        return DecisionSnapshot(
            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
            timestamp_utc=now,
            market_slug=market.slug,
            event_url=f"https://polymarket.com/event/{market.event_slug or market.slug}",
            timeframe=state.timeframe,
            round_start_ts=market.start_ts,
            round_end_ts=market.end_ts,
            seconds_to_expiry=state.seconds_to_expiry,
            stage=state.stage,
            side_checked=side_checked,
            selected_side=decision.side,
            outcome_token_id=selected_ob.token_id,
            opposite_token_id=opposite_ob.token_id,
            decision=decision.decision,
            skip_reason=None if decision.decision == DecisionKind.TRADE else decision.reason,
            round_open_price=state.round_open_price,
            current_btc_price=state.current_btc_price,
            current_side=state.current_side,
            distance_from_round_open=state.distance_pct,
            distance_bucket=state.distance_bucket,
            volatility_bucket=state.volatility_bucket,
            candle_pattern=state.candle_pattern,
            pattern_combo=state.pattern_combo,
            c0_open=state.c0.open if state.c0 else None,
            c0_high=state.c0.high if state.c0 else None,
            c0_low=state.c0.low if state.c0 else None,
            c0_close=state.c0.close if state.c0 else None,
            c0_volume=state.c0.volume if state.c0 else None,
            c1_open=state.c1.open if state.c1 else None,
            c1_high=state.c1.high if state.c1 else None,
            c1_low=state.c1.low if state.c1 else None,
            c1_close=state.c1.close if state.c1 else None,
            c1_volume=state.c1.volume if state.c1 else None,
            source_exchange="binance",
            source_symbol=binance.symbol,
            binance_data_received_at_utc=binance.received_at_utc,
            binance_data_age_seconds=binance_age,
            up_best_bid=orderbook.up.best_bid,
            up_best_ask=orderbook.up.best_ask,
            down_best_bid=orderbook.down.best_bid,
            down_best_ask=orderbook.down.best_ask,
            up_spread=orderbook.up.spread,
            down_spread=orderbook.down.spread,
            selected_best_bid=selected_ob.best_bid,
            selected_best_ask=selected_ob.best_ask,
            selected_spread=selected_ob.spread,
            selected_ask_size=selected_ob.ask_size,
            selected_bid_size=selected_ob.bid_size,
            orderbook_depth_top_5_json=(
                levels_to_json(selected_ob.top_5_bids)
                + "|"
                + levels_to_json(selected_ob.top_5_asks)
            ),
            liquidity_usd_estimate=selected_ob.liquidity_usd_estimate,
            market_active=market.active,
            market_closed=market.closed,
            market_accepting_orders=market.accepting_orders,
            orderbook_received_at_utc=orderbook.received_at_utc,
            orderbook_age_seconds=Decimal(
                str(max(0, (now - orderbook.received_at_utc).total_seconds()))
            ),
            metadata_received_at_utc=metadata_received_at_utc,
            metadata_age_seconds=Decimal(
                str(max(0, (now - metadata_received_at_utc).total_seconds()))
            ),
            rule_id=lookup.rule.rule_id if lookup.rule else None,
            rule_match_type=lookup.match_type,
            samples=lookup.samples,
            historical_probability=lookup.historical_probability,
            fair_price=decision.historical_probability,
            safety_buffer=decision.safety_buffer,
            max_buy_price=decision.max_buy_price,
            market_ask=decision.market_ask,
            edge_vs_ask=decision.edge_vs_ask,
            min_edge_required=self._settings.min_edge,
            recommended_side=lookup.recommended_side,
            return_aligned=lookup.rule.return_aligned if lookup.rule else True,
            requested_size_usd=decision.size_usd,
            max_position_usd=self._settings.max_position_usd,
            open_positions_count=risk_decision.open_positions_count,
            max_open_positions=risk_decision.max_open_positions,
            daily_realized_pnl=risk_decision.daily_realized_pnl,
            max_daily_loss_usd=risk_decision.max_daily_loss_usd,
            risk_allowed=risk_decision.allowed,
            risk_reject_reason=risk_decision.reject_reason,
        )


def market_resolved_btc_price(market: Any, round_open: Decimal) -> Decimal | None:
    """Best-effort final BTC price for fallback settlement.

    Not implemented in v1 (we rely on Polymarket API resolution). The
    Binance client would need to be queried for the close of the last
    5m candle in the round. Kept as a hook for future fallback use.
    """
    return None


def slug_from_url_or_slug(input_str: str) -> str:
    """Best-effort extraction of the slug from a URL or bare slug."""
    try:
        return parse_market_url(input_str).slug
    except UrlParserError:
        # If not a full URL/slug, return the original (caller may
        # re-discover and validate).
        return input_str


def settings_to_json(settings: Settings) -> str:
    """Render settings to JSON for storage in bot_runs.settings_json."""
    return json.dumps(settings.model_dump(mode="json"), default=str)
