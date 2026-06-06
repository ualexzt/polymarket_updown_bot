"""Settlement logic.

Primary source: Polymarket market metadata (resolved_outcome).
Fallback: Binance final price vs round_open_price.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from .models import (
    PaperPosition,
    PositionStatus,
    Settlement,
    SettlementSource,
    Side,
    TradeQuality,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _resolve_outcome(
    *,
    position: PaperPosition,
    polymarket_resolved: Side | None,
    final_btc_price: Decimal,
) -> tuple[Side, SettlementSource]:
    """Returns (resolved_outcome, source)."""
    if polymarket_resolved is not None:
        return polymarket_resolved, SettlementSource.POLYMARKET_API
    if final_btc_price > position.round_open_price:
        return Side.UP, SettlementSource.BINANCE_FALLBACK
    if final_btc_price < position.round_open_price:
        return Side.DOWN, SettlementSource.BINANCE_FALLBACK
    # Exact tie at resolution time is rare; default to DOWN (matches
    # Polymarket's "if equal, do not resolve UP" convention in some markets).
    return Side.DOWN, SettlementSource.BINANCE_FALLBACK


def _classify_quality(
    *,
    won: bool,
    edge_at_entry: Decimal,
    spread_at_entry: Decimal,
    settlement_source: SettlementSource,
    return_aligned: bool,
) -> TradeQuality:
    soft_warnings = []
    if spread_at_entry > Decimal("0.02"):
        soft_warnings.append("spread_elevated")
    if edge_at_entry <= Decimal("0"):
        soft_warnings.append("no_edge_at_entry")
    if settlement_source == SettlementSource.BINANCE_FALLBACK:
        soft_warnings.append("non_authoritative_settlement")

    if won:
        return TradeQuality.BAD_WIN if soft_warnings else TradeQuality.GOOD_WIN

    # Lost
    if settlement_source == SettlementSource.BINANCE_FALLBACK or not return_aligned:
        return TradeQuality.EXECUTION_ERROR
    if soft_warnings:
        return TradeQuality.BAD_LOSS
    return TradeQuality.GOOD_LOSS


def settle_position(
    *,
    position: PaperPosition,
    polymarket_resolved: Side | None,
    final_btc_price: Decimal,
    round_close_price: Decimal,
    resolved_at_utc: datetime | None = None,
    return_aligned: bool = True,
) -> Settlement:
    """Settle a paper position. Returns a Settlement row (also caller
    is responsible for persisting it and updating the position status).
    """
    if position.status != PositionStatus.OPEN:
        raise ValueError(f"cannot settle position in status {position.status}")

    resolved_outcome, source = _resolve_outcome(
        position=position,
        polymarket_resolved=polymarket_resolved,
        final_btc_price=final_btc_price,
    )
    won = resolved_outcome == position.selected_side

    payout = position.shares * Decimal("1") if won else Decimal("0")
    cost = position.entry_size_usd
    pnl = payout - cost
    roi = pnl / cost if cost > 0 else Decimal("0")
    quality = _classify_quality(
        won=won,
        edge_at_entry=position.edge_at_entry,
        spread_at_entry=position.entry_spread,
        settlement_source=source,
        return_aligned=return_aligned,
    )

    return Settlement(
        settlement_id=_new_id("set"),
        position_id=position.position_id,
        market_slug=position.market_slug,
        resolved_outcome=resolved_outcome,
        selected_side=position.selected_side,
        won=won,
        entry_price=position.entry_price,
        shares=position.shares,
        cost_usd=cost,
        payout_usd=payout,
        realized_pnl_usd=pnl,
        realized_roi_pct=roi,
        settlement_source=source,
        round_open_price=position.round_open_price,
        round_close_price=round_close_price,
        final_btc_price=final_btc_price,
        resolved_at_utc=resolved_at_utc or datetime.now(UTC),
        trade_quality=quality,
        edge_at_entry=position.edge_at_entry,
        spread_at_entry=position.entry_spread,
        rule_id=position.rule_id,
        historical_probability_at_entry=position.historical_probability_at_entry,
        seconds_to_expiry_at_entry=position.seconds_to_expiry_at_entry,
    )


def mark_position_settled(position: PaperPosition) -> PaperPosition:
    """Return a new PaperPosition with status=SETTLED."""
    return position.model_copy(update={"status": PositionStatus.SETTLED})
