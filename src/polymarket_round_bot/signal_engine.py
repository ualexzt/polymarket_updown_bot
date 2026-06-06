"""Signal decision engine.

Combines round state + probability rule + Polymarket orderbook into a
TRADE / SKIP decision. Risk checks are folded in via the RiskManager
dependency.

Entry conditions (all must be true for TRADE):
  - market.active and not market.closed and market.accepting_orders
  - selected_best_ask in (0, 1)
  - selected_best_ask <= max_buy_price (= fair_price - safety_buffer)
  - edge_vs_ask >= MIN_EDGE
  - selected_spread <= MAX_SPREAD
  - liquidity_usd_estimate >= MIN_LIQUIDITY_USD
  - rule has samples >= MIN_SAMPLES and historical_probability >= MIN_HISTORICAL_PROBABILITY
  - rule.return_aligned
  - data not stale (Binance + orderbook + metadata)
  - risk allowed
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
    Side,
    SignalDecision,
    Stage,
)


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
    now_utc: datetime | None = None,
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

    # Market status
    if not market.active:
        return _skip(state, market, orderbook, lookup, "market_not_active", settings.max_position_usd)
    if market.closed:
        return _skip(state, market, orderbook, lookup, "market_closed", settings.max_position_usd)
    if not market.accepting_orders:
        return _skip(state, market, orderbook, lookup, "market_not_accepting_orders", settings.max_position_usd)

    # Freshness
    # We don't need the binance age here directly — the round state
    # already incorporates Binance data. We just need to ensure we
    # have at least one in-round candle to compute round_open.
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

    side = _select_side_for_observation(state, lookup)
    if side is None:
        return _skip(state, market, orderbook, lookup, "no_recommended_side", settings.max_position_usd)

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
    if edge_vs_ask < settings.min_edge:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"edge_below_min:{edge_vs_ask}<{settings.min_edge}",
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
