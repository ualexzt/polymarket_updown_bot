"""Signal decision engine.

Combines round state + probability rule + Polymarket orderbook into a
TRADE / SKIP decision. Risk checks are folded in via the RiskManager
dependency.

Entry conditions (all must be true for TRADE):
  - timeframe allowed (15m yes; 5m only if allow_5m_trading=True)
  - stage allowed (AFTER_5M and/or AFTER_10M per settings)
  - market.active and not market.closed and market.accepting_orders
  - Binance data not stale (<= BINANCE_PRICE_MAX_AGE_SECONDS)
  - at least one in-round candle (no_in_round_candle)
  - orderbook not stale (<= POLY_ORDERBOOK_MAX_AGE_SECONDS)
  - metadata not stale (<= POLY_MARKET_METADATA_MAX_AGE_SECONDS)
  - rule.usable_signal == True (post-2026-06-06 audit)
  - rule.samples >= MIN_SAMPLES
  - rule.historical_probability >= MIN_HISTORICAL_PROBABILITY
  - rule.return_aligned
  - rule.match_type == EXACT (v1; allow_fallback_trading=False by default)
  - seconds_to_expiry in stage-specific window (15m only)
  - selected_best_ask in (0, 1)
  - selected_best_ask <= max_buy_price (= fair_price - safety_buffer)
  - selected_best_ask <= max_entry_ask (absolute cap, default 0.80)
  - DOWN selected_best_ask in [0.55, 0.70) for paper forward test
  - edge_vs_ask >= MIN_EDGE
  - selected_spread <= MAX_SPREAD
  - liquidity_usd_estimate >= MIN_LIQUIDITY_USD
  - ask size sufficient for requested bet
  - risk allowed (max_open_positions, daily_loss, duplicate)
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from .config import Settings
from .models import (
    DecisionKind,
    MarketMetadata,
    OrderbookSnapshot,
    PairOrderbook,
    RoundState,
    RuleLookupResult,
    RuleMatchType,
    Side,
    SignalDecision,
    Stage,
    Timeframe,
)
from .rule_whitelist import RuleWhitelist

_MIN_DOWN_SECONDS_TO_EXPIRY_AFTER_5M = 540
_MIN_DOWN_ENTRY_ASK = Decimal("0.55")
_MAX_DOWN_ENTRY_ASK = Decimal("0.70")


def _age_seconds(now: datetime, then: datetime) -> Decimal:
    return Decimal(str(max(0.0, (now - then).total_seconds())))


def _select_side_for_observation(
    state: RoundState, lookup: RuleLookupResult
) -> Side | None:
    """We always pick the recommended_side from the rule.

    We do NOT also try the opposite side in v1 (that would require
    a separate lookup with the inverse current_side).
    """
    if lookup.recommended_side is None:
        return None
    return lookup.recommended_side


def _is_truthy(value: bool) -> bool:
    return bool(value)


def _side_min_edge(settings: Settings, side: Side) -> Decimal:
    if side == Side.UP and settings.min_edge_up is not None:
        return settings.min_edge_up
    if side == Side.DOWN and settings.min_edge_down is not None:
        return settings.min_edge_down
    return settings.min_edge


def _side_max_entry_ask(settings: Settings, side: Side) -> Decimal:
    if side == Side.UP and settings.max_entry_ask_up is not None:
        return settings.max_entry_ask_up
    if side == Side.DOWN and settings.max_entry_ask_down is not None:
        return settings.max_entry_ask_down
    return settings.max_entry_ask


def _strictest_decimal(base: Decimal, override: Decimal | None) -> Decimal:
    if override is None:
        return base
    return max(base, override)


def _strictest_entry_cap(base: Decimal, override: Decimal | None) -> Decimal:
    if override is None:
        return base
    return min(base, override)


def build_decision(
    *,
    settings: Settings,
    state: RoundState,
    market: MarketMetadata,
    orderbook: PairOrderbook,
    lookup: RuleLookupResult,
    risk_allowed: bool,
    risk_reject_reason: str | None,
    open_positions_count: int,
    daily_realized_pnl: Decimal,
    metadata_received_at_utc: datetime,
    binance_received_at_utc: datetime,
    now_utc: datetime | None = None,
    rule_policy: RuleWhitelist | None = None,
) -> SignalDecision:
    """Pure function: produce a TRADE or SKIP decision from inputs."""
    now = now_utc or datetime.now(UTC)

    # Stage gate
    if state.stage == Stage.AFTER_5M and not settings.allow_after_5m:
        return _skip(
            state=state,
            market=market,
            orderbook=orderbook,
            lookup=lookup,
            reason="stage_gated:AFTER_5M_disabled",
            size_usd=settings.max_position_usd,
        )
    if state.stage == Stage.AFTER_10M and not settings.allow_after_10m:
        return _skip(
            state=state,
            market=market,
            orderbook=orderbook,
            lookup=lookup,
            reason="stage_gated:AFTER_10M_disabled",
            size_usd=settings.max_position_usd,
        )

    # 5m timeframe gate (v1 has no calibrated 5m rules)
    if state.timeframe == Timeframe.M5 and not settings.allow_5m_trading:
        return _skip(
            state=state,
            market=market,
            orderbook=orderbook,
            lookup=lookup,
            reason="5m_trading_disabled_in_v1",
            size_usd=settings.max_position_usd,
        )

    # Market status
    if not market.active:
        return _skip(state, market, orderbook, lookup, "market_not_active", settings.max_position_usd)
    if market.closed:
        return _skip(state, market, orderbook, lookup, "market_closed", settings.max_position_usd)
    if not market.accepting_orders:
        return _skip(state, market, orderbook, lookup, "market_not_accepting_orders", settings.max_position_usd)

    # Freshness
    bn_age = _age_seconds(now, binance_received_at_utc)
    if bn_age > Decimal(settings.binance_price_max_age_seconds):
        return _skip(state, market, orderbook, lookup, "stale_binance_data", settings.max_position_usd)

    if state.c0 is None and state.c1 is None:
        return _skip(state, market, orderbook, lookup, "no_in_round_candle", settings.max_position_usd)

    ob_age = _age_seconds(now, orderbook.received_at_utc)
    if ob_age > Decimal(settings.poly_orderbook_max_age_seconds):
        return _skip(state, market, orderbook, lookup, "stale_orderbook", settings.max_position_usd)

    md_age = _age_seconds(now, metadata_received_at_utc)
    if md_age > Decimal(settings.poly_market_metadata_max_age_seconds):
        return _skip(state, market, orderbook, lookup, "stale_market_metadata", settings.max_position_usd)

    # No-trade rule filters
    if lookup.no_trade_reasons:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            "rule_filtered:" + ";".join(lookup.no_trade_reasons),
            settings.max_position_usd,
        )

    if lookup.recommended_side is None or lookup.historical_probability is None:
        return _skip(state, market, orderbook, lookup, "no_rule_for_state", settings.max_position_usd)

    # EXACT-only trading in v1. Fallback matches are surfaced in
    # match_type for inspection but never produce a TRADE.
    if lookup.match_type != RuleMatchType.EXACT and not settings.allow_fallback_trading:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            "fallback_rule_not_tradeable_in_v1",
            settings.max_position_usd,
            lookup.recommended_side,
            None,
        )

    # Seconds-to-expiry window (15m only; 5m is gated above).
    if state.timeframe == Timeframe.M15:
        window: tuple[int, int] | None
        if state.stage == Stage.AFTER_5M:
            window = (
                settings.min_seconds_to_expiry_15m_after_5m,
                settings.max_seconds_to_expiry_15m_after_5m,
            )
        elif state.stage == Stage.AFTER_10M:
            window = (
                settings.min_seconds_to_expiry_15m_after_10m,
                settings.max_seconds_to_expiry_15m_after_10m,
            )
        else:
            window = None
        if window is not None:
            lo, hi = window
            if state.seconds_to_expiry < lo or state.seconds_to_expiry > hi:
                return _skip(
                    state,
                    market,
                    orderbook,
                    lookup,
                    f"seconds_to_expiry_out_of_range:{state.seconds_to_expiry}_not_in_[{lo},{hi}]",
                    settings.max_position_usd,
                    lookup.recommended_side,
                    None,
                )

    side = _select_side_for_observation(state, lookup)
    if side is None:
        return _skip(state, market, orderbook, lookup, "no_recommended_side", settings.max_position_usd)

    rule_id = lookup.rule.rule_id if lookup.rule else None
    if rule_policy is not None:
        quarantine_reason = rule_policy.quarantine_reason(rule_id)
        if quarantine_reason is not None:
            return _skip(
                state,
                market,
                orderbook,
                lookup,
                f"rule_quarantined:{quarantine_reason}",
                settings.max_position_usd,
                side,
                None,
            )
        if not rule_policy.is_allowed(rule_id, side):
            return _skip(
                state,
                market,
                orderbook,
                lookup,
                "rule_not_whitelisted",
                settings.max_position_usd,
                side,
                None,
            )

    if (
        state.timeframe == Timeframe.M15
        and state.stage == Stage.AFTER_5M
        and side == Side.DOWN
        and state.seconds_to_expiry < _MIN_DOWN_SECONDS_TO_EXPIRY_AFTER_5M
    ):
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"late_down_after_5m_window:{state.seconds_to_expiry}<{_MIN_DOWN_SECONDS_TO_EXPIRY_AFTER_5M}",
            settings.max_position_usd,
            side,
            None,
        )

    # Pick the orderbook for the selected side
    selected_ob = _select_orderbook_for_side(orderbook, side, market)
    best_bid = selected_ob.best_bid
    best_ask = selected_ob.best_ask
    spread = selected_ob.spread
    ask_size = selected_ob.ask_size
    liquidity = selected_ob.liquidity_usd_estimate
    token_id = selected_ob.token_id

    # Basic orderbook validity
    if best_ask is None or best_bid is None:
        return _skip(state, market, orderbook, lookup, "missing_top_of_book", settings.max_position_usd, side, token_id)
    if not (Decimal("0") < best_ask < Decimal("1")):
        return _skip(state, market, orderbook, lookup, "ask_out_of_range", settings.max_position_usd, side, token_id)

    # Probability math
    fair_price = lookup.historical_probability
    safety_buffer = settings.safety_buffer
    max_buy_price = fair_price - safety_buffer
    edge_vs_ask = fair_price - best_ask  # positive = good for us
    min_edge_required = _side_min_edge(settings, side)
    max_entry_ask = _side_max_entry_ask(settings, side)
    if rule_policy is not None:
        gate = rule_policy.gate_for(lookup.rule.rule_id if lookup.rule else None)
        if gate is not None:
            min_edge_required = _strictest_decimal(min_edge_required, gate.min_edge)
            max_entry_ask = _strictest_entry_cap(max_entry_ask, gate.max_entry_ask)

    if best_ask > max_buy_price:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            "ask_above_max_buy_price",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )
    if best_ask > max_entry_ask:
        # Absolute cap independent of fair_price. Protects against
        # overfit rules where hist_prob is high but the implied WR
        # needed to break even at the ask is not actually met live.
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"ask_above_max_entry_ask:{best_ask}>{max_entry_ask}",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )
    if side == Side.DOWN and best_ask < _MIN_DOWN_ENTRY_ASK:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"down_entry_ask_below_min:{best_ask}<{_MIN_DOWN_ENTRY_ASK}",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )
    if side == Side.DOWN and best_ask >= _MAX_DOWN_ENTRY_ASK:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"down_entry_ask_above_max:{best_ask}>={_MAX_DOWN_ENTRY_ASK}",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )
    if edge_vs_ask < min_edge_required:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"edge_below_min:{edge_vs_ask}<{min_edge_required}",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )

    # Spread
    if spread is not None and spread > settings.max_spread:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"spread_too_wide:{spread}>{settings.max_spread}",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )

    # Liquidity
    if liquidity is None or liquidity < settings.min_liquidity_usd:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"liquidity_too_low:{liquidity}<{settings.min_liquidity_usd}",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )

    # Ask size: requested size_usd must be available at best ask
    requested_size = settings.max_position_usd
    if ask_size is not None and ask_size * best_ask < requested_size:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"insufficient_ask_size:{ask_size * best_ask}<{requested_size}",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )

    # Risk
    if not risk_allowed:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"risk_rejected:{risk_reject_reason or 'unknown'}",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )

    return SignalDecision(
        decision=DecisionKind.TRADE,
        side=side,
        market_slug=market.slug,
        event_url=f"https://polymarket.com/event/{market.event_slug or market.slug}",
        token_id=token_id,
        stage=state.stage,
        current_side=state.current_side,
        distance_bucket=state.distance_bucket,
        volatility_bucket=state.volatility_bucket,
        pattern=state.candle_pattern,
        rule_id=lookup.rule.rule_id if lookup.rule else None,
        rule_match_type=lookup.match_type,
        samples=lookup.samples,
        historical_probability=fair_price,
        safety_buffer=safety_buffer,
        max_buy_price=max_buy_price,
        market_ask=best_ask,
        edge_vs_ask=edge_vs_ask,
        spread=spread,
        size_usd=requested_size,
        reason="ask <= max_buy_price and all filters passed",
    )


def _select_orderbook_for_side(
    pair: PairOrderbook, side: Side, market: MarketMetadata
) -> OrderbookSnapshot:
    if side == Side.UP:
        return pair.up
    return pair.down


def _skip(
    state: RoundState,
    market: MarketMetadata,
    orderbook: PairOrderbook,
    lookup: RuleLookupResult,
    reason: str,
    size_usd: Decimal,
    side: Side | None = None,
    token_id: str | None = None,
    *,
    fair_price: Decimal | None = None,
    max_buy_price: Decimal | None = None,
    market_ask: Decimal | None = None,
    edge_vs_ask: Decimal | None = None,
) -> SignalDecision:
    return SignalDecision(
        decision=DecisionKind.SKIP,
        side=side,
        market_slug=market.slug,
        event_url=f"https://polymarket.com/event/{market.event_slug or market.slug}",
        token_id=token_id,
        stage=state.stage,
        current_side=state.current_side,
        distance_bucket=state.distance_bucket,
        volatility_bucket=state.volatility_bucket,
        pattern=state.candle_pattern,
        rule_id=lookup.rule.rule_id if lookup.rule else None,
        rule_match_type=lookup.match_type,
        samples=lookup.samples,
        historical_probability=fair_price,
        safety_buffer=Decimal("0"),
        max_buy_price=max_buy_price,
        market_ask=market_ask,
        edge_vs_ask=edge_vs_ask,
        spread=None,
        size_usd=size_usd,
        reason=reason,
    )
