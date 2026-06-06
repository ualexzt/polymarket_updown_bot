"""Reporting: paper performance aggregates, CSV export, per-trade inspection."""
from __future__ import annotations

import csv
import statistics
from collections import Counter
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Any, Final

from .models import (
    DecisionKind,
    MarkToMarket,
    PaperPosition,
    PositionStatus,
    Settlement,
)
from .storage import Storage

# === Aggregations ===

# Funnel: groups raw skip_reason strings into 6 high-level stages.
# Order matters: the first matching category is the one reported.
_FUNNEL_CATEGORIES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "market_state",
        (
            "market_not_active",
            "market_closed",
            "market_not_accepting_orders",
            "stale_market_metadata",
        ),
    ),
    (
        "stage",
        (
            "no_in_round_candle",
            "stage_gated:AFTER_5M_disabled",
            "stage_gated:AFTER_10M_disabled",
        ),
    ),
    ("data_freshness", ("stale_orderbook",)),
    (
        "rule_lookup",
        (
            "no_rule_for_state",
            "no_recommended_side",
            "rule_filtered",
        ),
    ),
    (
        "orderbook",
        (
            "missing_top_of_book",
            "ask_out_of_range",
        ),
    ),
    (
        "trade_conditions",
        # These are emitted as dynamic strings by signal_engine.py with the
        # same prefix; the funnel collapses the value (e.g. "ask_above_max_buy_price"
        # vs "ask_above_max_buy_price:...").
        (
            "ask_above_max_buy_price",
            "edge_below_min",
            "spread_too_wide",
            "liquidity_too_low",
            "insufficient_ask_size",
        ),
    ),
    ("risk", ("risk_rejected",)),
)


def _classify_skip_reason(reason: str | None) -> str:
    """Map a raw skip_reason string to a funnel stage, or 'other'."""
    if not reason:
        return "other"
    for stage_name, prefixes in _FUNNEL_CATEGORIES:
        for p in prefixes:
            if reason == p or reason.startswith(p + ":"):
                return stage_name
    return "other"


def _new_bucket() -> dict[str, Any]:
    return {
        "traded": 0,
        "skipped_total": 0,
        "skipped_by_stage": Counter(),
    }


def decision_funnel(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate decisions into a funnel: how many decisions passed each
    filtering stage, and where the bot is currently dropping them.

    Returns a dict with:
      - total_decisions
      - traded
      - skipped_total
      - skipped_by_stage: {stage_name: count}
      - by_timeframe: {timeframe: {traded, skipped_total, skipped_by_stage}}
      - by_stage_label: {stage value (AFTER_5M / ...): {traded, skipped_total}}
    """
    by_timeframe: dict[str, dict[str, Any]] = {}
    by_stage_label: dict[str, dict[str, Any]] = {}
    skipped_by_stage: Counter[str] = Counter()
    traded = 0
    skipped_total = 0
    for d in decisions:
        tf = str(d.get("timeframe") or "unknown")
        sl = str(d.get("stage") or "unknown")
        is_trade = d.get("decision") == DecisionKind.TRADE.value
        tf_bucket = by_timeframe.setdefault(tf, _new_bucket())
        sl_bucket = by_stage_label.setdefault(sl, _new_bucket())
        if is_trade:
            traded += 1
            tf_bucket["traded"] += 1
            sl_bucket["traded"] += 1
        else:
            stage = _classify_skip_reason(d.get("skip_reason"))
            skipped_by_stage[stage] += 1
            skipped_total += 1
            tf_bucket["skipped_total"] += 1
            tf_bucket["skipped_by_stage"][stage] += 1
            sl_bucket["skipped_total"] += 1
            sl_bucket["skipped_by_stage"][stage] += 1
    return {
        "total_decisions": len(decisions),
        "traded": traded,
        "skipped_total": skipped_total,
        "skipped_by_stage": dict(skipped_by_stage.most_common()),
        "by_timeframe": by_timeframe,
        "by_stage_label": by_stage_label,
    }


def paper_summary(storage: Storage, *, since: str | None = None) -> dict[str, Any]:
    decisions = storage.list_decisions(since_iso=since)
    settlements = storage.list_settlements(since_iso=since)
    positions = storage.list_all_positions()

    total_decisions = len(decisions)
    total_trades = sum(1 for d in decisions if d.get("decision") == DecisionKind.TRADE.value)
    total_skips = sum(1 for d in decisions if d.get("decision") == DecisionKind.SKIP.value)

    skip_reasons: Counter[str] = Counter()
    for d in decisions:
        if d.get("decision") == DecisionKind.SKIP.value:
            reason = d.get("skip_reason") or "unknown"
            skip_reasons[reason] += 1

    settled = [s for s in settlements]
    open_positions = [p for p in positions if p.status == PositionStatus.OPEN]

    win_count = sum(1 for s in settled if s.won)
    loss_count = sum(1 for s in settled if not s.won)
    total_realized = sum((s.realized_pnl_usd for s in settled), Decimal("0"))

    avg_pnl = (
        (total_realized / Decimal(len(settled))) if settled else Decimal("0")
    )
    pnls = [s.realized_pnl_usd for s in settled]
    median_pnl = (
        statistics.median([float(p) for p in pnls]) if pnls else 0.0
    )

    entry_prices = [s.entry_price for s in settled]
    fair_at_entry = [s.historical_probability_at_entry for s in settled]
    edges = [s.edge_at_entry for s in settled]
    spreads = [s.spread_at_entry for s in settled]
    sec_to_exp = [s.seconds_to_expiry_at_entry for s in settled]

    def _avg(xs: list[Decimal]) -> float:
        return float(sum(xs, Decimal("0"))) / len(xs) if xs else 0.0

    return {
        "since": since,
        "total_decisions": total_decisions,
        "total_trades": total_trades,
        "total_skips": total_skips,
        "skip_reasons": dict(skip_reasons.most_common()),
        "funnel": decision_funnel(decisions),
        "settled_trades": len(settled),
        "open_trades": len(open_positions),
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": (win_count / len(settled)) if settled else 0.0,
        "total_realized_pnl": float(total_realized),
        "average_realized_pnl": float(avg_pnl),
        "median_realized_pnl": median_pnl,
        "average_entry_price": _avg(entry_prices),
        "average_fair_price_at_entry": _avg(fair_at_entry),
        "average_edge_at_entry": _avg(edges),
        "average_spread_at_entry": _avg(spreads),
        "average_seconds_to_expiry_at_entry": (_avg([Decimal(x) for x in sec_to_exp]) if sec_to_exp else 0.0),
        "best_trade": _best_trade(settled),
        "worst_trade": _worst_trade(settled),
    }


def _best_trade(settled: list[Settlement]) -> dict[str, Any] | None:
    if not settled:
        return None
    s = max(settled, key=lambda x: x.realized_pnl_usd)
    return _trade_summary(s)


def _worst_trade(settled: list[Settlement]) -> dict[str, Any] | None:
    if not settled:
        return None
    s = min(settled, key=lambda x: x.realized_pnl_usd)
    return _trade_summary(s)


def _trade_summary(s: Settlement) -> dict[str, Any]:
    return {
        "position_id": s.position_id,
        "market_slug": s.market_slug,
        "pnl": float(s.realized_pnl_usd),
        "won": s.won,
        "trade_quality": s.trade_quality.value,
    }


# === CSV export ===

CSV_FIELDS: list[str] = [
    "position_id",
    "market_slug",
    "event_url",
    "entry_time",
    "settlement_time",
    "side",
    "entry_price",
    "shares",
    "cost_usd",
    "payout_usd",
    "realized_pnl_usd",
    "realized_roi_pct",
    "won",
    "trade_quality",
    "fair_price_at_entry",
    "max_buy_price_at_entry",
    "edge_at_entry",
    "spread_at_entry",
    "stage",
    "pattern",
    "current_side",
    "distance_bucket",
    "volatility_bucket",
    "historical_probability",
    "samples",
    "rule_id",
    "rule_match_type",
    "round_open_price",
    "btc_price_at_entry",
    "final_btc_price",
    "seconds_to_expiry_at_entry",
    "settlement_source",
]


def export_trades_csv(
    settlements: list[Settlement],
    positions_by_id: dict[str, PaperPosition],
    *,
    out: Path | None = None,
) -> str:
    """Render a CSV string of one row per settlement. If `out` is given,
    also write to that path. Returns the rendered string.
    """
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for s in settlements:
        pos = positions_by_id.get(s.position_id)
        writer.writerow(_csv_row(s, pos))
    csv_text = buf.getvalue()
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(csv_text, encoding="utf-8")
    return csv_text


def _csv_row(s: Settlement, pos: PaperPosition | None) -> dict[str, Any]:
    return {
        "position_id": s.position_id,
        "market_slug": s.market_slug,
        "event_url": pos.event_url if pos else None,
        "entry_time": pos.entry_timestamp_utc.isoformat() if pos else None,
        "settlement_time": s.resolved_at_utc.isoformat(),
        "side": s.selected_side.value,
        "entry_price": str(s.entry_price),
        "shares": str(s.shares),
        "cost_usd": str(s.cost_usd),
        "payout_usd": str(s.payout_usd),
        "realized_pnl_usd": str(s.realized_pnl_usd),
        "realized_roi_pct": str(s.realized_roi_pct),
        "won": int(s.won),
        "trade_quality": s.trade_quality.value,
        "fair_price_at_entry": str(s.historical_probability_at_entry),
        "max_buy_price_at_entry": str(pos.max_buy_price_at_entry) if pos else "",
        "edge_at_entry": str(s.edge_at_entry),
        "spread_at_entry": str(s.spread_at_entry),
        "stage": pos.stage_at_entry.value if pos else "",
        "pattern": pos.pattern_at_entry if pos else "",
        "current_side": pos.current_side_at_entry.value if pos else "",
        "distance_bucket": pos.distance_bucket_at_entry.value if pos else "",
        "volatility_bucket": pos.volatility_bucket_at_entry.value if pos else "",
        "historical_probability": str(s.historical_probability_at_entry),
        "samples": pos.samples_at_entry if pos else 0,
        "rule_id": s.rule_id or "",
        "rule_match_type": pos.rule_match_type.value if pos else "",
        "round_open_price": str(s.round_open_price),
        "btc_price_at_entry": str(pos.btc_price_at_entry) if pos else "",
        "final_btc_price": str(s.final_btc_price),
        "seconds_to_expiry_at_entry": s.seconds_to_expiry_at_entry,
        "settlement_source": s.settlement_source.value,
    }


# === Inspect one trade ===

def inspect_trade(storage: Storage, position_id: str) -> dict[str, Any]:
    position = storage.get_position(position_id)
    if position is None:
        raise KeyError(f"position_id not found: {position_id}")
    settlements = storage.list_settlements()
    settlement = next((s for s in settlements if s.position_id == position_id), None)
    mtms = storage.list_mtm(position_id)
    return {
        "position": position.model_dump(mode="json"),
        "settlement": settlement.model_dump(mode="json") if settlement else None,
        "mark_to_market": [m.model_dump(mode="json") for m in mtms],
        "explanation": _explanation(position, settlement, mtms),
    }


def _explanation(
    pos: PaperPosition,
    s: Settlement | None,
    mtms: list[MarkToMarket],
) -> list[str]:
    out: list[str] = []
    out.append(
        f"1. Entry reason: TRADE fired because ask ({pos.entry_best_ask}) "
        f"<= max_buy_price ({pos.max_buy_price_at_entry}) with edge "
        f"{pos.edge_at_entry}."
    )
    out.append(
        f"2. Rule: {pos.rule_id} (match_type={pos.rule_match_type.value}); "
        f"samples={pos.samples_at_entry}."
    )
    out.append(
        f"3. Historical probability at entry: {pos.historical_probability_at_entry}."
    )
    out.append(
        f"4. Best ask at entry: {pos.entry_best_ask}."
    )
    out.append(
        f"5. Max buy price at entry: {pos.max_buy_price_at_entry}."
    )
    out.append(
        f"6. Edge at entry: {pos.edge_at_entry}."
    )
    out.append(
        f"7. Spread at entry: {pos.entry_spread}."
    )
    out.append(
        f"8. BTC state at entry: stage={pos.stage_at_entry.value}, "
        f"distance_bucket={pos.distance_bucket_at_entry.value}, "
        f"volatility_bucket={pos.volatility_bucket_at_entry.value}, "
        f"pattern={pos.pattern_at_entry}."
    )
    if mtms:
        first = mtms[0]
        last = mtms[-1]
        out.append(
            f"9. Mark-to-market: {len(mtms)} snapshots; "
            f"first best_bid={first.best_bid}, last best_bid={last.best_bid}."
        )
    if s is not None:
        out.append(f"10. Resolution: outcome={s.resolved_outcome.value}, won={s.won}.")
        out.append(
            f"11. PnL: realized_pnl={s.realized_pnl_usd}, "
            f"roi={s.realized_roi_pct:.4f}, quality={s.trade_quality.value}."
        )
    else:
        out.append("10/11. Not yet settled.")
    if s is not None:
        out.append(
            f"12. Trade quality classification: {s.trade_quality.value}."
        )
    return out
