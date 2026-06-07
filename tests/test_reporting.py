"""Tests for reporting, CSV export, trade inspection."""
from __future__ import annotations

import csv
from decimal import Decimal
from io import StringIO
from pathlib import Path

from polymarket_round_bot.models import (
    DecisionSnapshot,
    PositionStatus,
    Settlement,
    SettlementSource,
    Side,
    TradeQuality,
)
from polymarket_round_bot.reporting import (
    CSV_FIELDS,
    _classify_skip_reason,
    decision_funnel,
    export_trades_csv,
    inspect_trade,
    paper_summary,
)
from polymarket_round_bot.storage import Storage


def _dec(s: str) -> Decimal:
    return Decimal(s)


def test_csv_export_contains_required_fields():
    s = Settlement(
        settlement_id="set1",
        position_id="p1",
        market_slug="m1",
        resolved_outcome=Side.UP,
        selected_side=Side.UP,
        won=True,
        entry_price=_dec("0.65"),
        shares=_dec("7.692307"),
        cost_usd=_dec("5"),
        payout_usd=_dec("7.692307"),
        realized_pnl_usd=_dec("2.692307"),
        realized_roi_pct=_dec("0.538461"),
        settlement_source=SettlementSource.POLYMARKET_API,
        round_open_price=_dec("100"),
        round_close_price=_dec("101"),
        final_btc_price=_dec("101"),
        resolved_at_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        trade_quality=TradeQuality.GOOD_WIN,
        edge_at_entry=_dec("0.20"),
        spread_at_entry=_dec("0.02"),
        rule_id="r1",
        historical_probability_at_entry=_dec("0.85"),
        seconds_to_expiry_at_entry=600,
    )
    pos = _position_for(s)
    csv_text = export_trades_csv([s], {s.position_id: pos})
    # Parse and verify header
    reader = csv.DictReader(StringIO(csv_text))
    rows = list(reader)
    assert len(rows) == 1
    row = rows[0]
    for f in CSV_FIELDS:
        assert f in row, f"missing field {f}"
    assert row["won"] == "1"
    assert row["side"] == "UP"


def test_paper_report_aggregates_trades(tmp_path: Path):
    db = tmp_path / "test.sqlite"
    s = Storage(db)
    # Insert a decision snapshot and a settlement
    snap = _decision_snapshot("d1", "m1", DecisionKind.TRADE)
    s.insert_decision(snap)
    pos = _position_for_settlement("p1", won=True)
    s.upsert_position(pos)
    settlement = _settlement_winning("p1", "m1")
    s.insert_settlement(settlement)

    summary = paper_summary(s)
    assert summary["total_decisions"] == 1
    assert summary["total_trades"] == 1
    assert summary["settled_trades"] == 1
    assert summary["win_count"] == 1
    assert summary["loss_count"] == 0
    assert summary["total_realized_pnl"] > 0


def test_inspect_trade_includes_decision_reasoning(tmp_path: Path):
    db = tmp_path / "test.sqlite"
    s = Storage(db)
    pos = _position_for_settlement("p1", won=True)
    s.upsert_position(pos)
    settlement = _settlement_winning("p1", "m1")
    s.insert_settlement(settlement)

    out = inspect_trade(s, "p1")
    assert "position" in out
    assert "settlement" in out
    assert "explanation" in out
    explanation_text = "\n".join(out["explanation"])
    assert "TRADE fired" in explanation_text
    assert "Historical probability" in explanation_text
    assert "max_buy_price" in explanation_text


# === Helpers ===

from polymarket_round_bot.models import (  # noqa: E402
    CurrentSide,
    DecisionKind,
    DistanceBucket,
    PaperPosition,
    RuleMatchType,
    Stage,
    Timeframe,
    VolatilityBucket,
)


def _decision_snapshot(decision_id: str, slug: str, decision: DecisionKind) -> DecisionSnapshot:
    return DecisionSnapshot(
        decision_id=decision_id,
        timestamp_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        market_slug=slug,
        event_url=None,
        timeframe=Timeframe.M15,
        round_start_ts=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        round_end_ts=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        seconds_to_expiry=600,
        stage=Stage.AFTER_10M,
        side_checked=Side.UP,
        selected_side=Side.UP,
        outcome_token_id="up",
        opposite_token_id="down",
        decision=decision,
        skip_reason=None,
        round_open_price=_dec("100"),
        current_btc_price=_dec("100.10"),
        current_side=CurrentSide.ABOVE_OPEN,
        distance_from_round_open=_dec("0.001"),
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        candle_pattern="normal_bull",
        pattern_combo=None,
        c0_open=None, c0_high=None, c0_low=None, c0_close=None, c0_volume=None,
        c1_open=None, c1_high=None, c1_low=None, c1_close=None, c1_volume=None,
        source_exchange="binance",
        source_symbol="BTCUSDT",
        binance_data_received_at_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        binance_data_age_seconds=_dec("5"),
        up_best_bid=_dec("0.62"),
        up_best_ask=_dec("0.65"),
        down_best_bid=_dec("0.32"),
        down_best_ask=_dec("0.35"),
        up_spread=_dec("0.03"),
        down_spread=_dec("0.03"),
        selected_best_bid=_dec("0.62"),
        selected_best_ask=_dec("0.65"),
        selected_spread=_dec("0.03"),
        selected_ask_size=_dec("1000"),
        selected_bid_size=_dec("1000"),
        orderbook_depth_top_5_json="[]",
        liquidity_usd_estimate=_dec("1000"),
        market_active=True,
        market_closed=False,
        market_accepting_orders=True,
        orderbook_received_at_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        orderbook_age_seconds=_dec("1"),
        metadata_received_at_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        metadata_age_seconds=_dec("1"),
        rule_id="r1",
        rule_match_type=RuleMatchType.EXACT,
        samples=100,
        historical_probability=_dec("0.85"),
        fair_price=_dec("0.85"),
        safety_buffer=_dec("0.05"),
        max_buy_price=_dec("0.81"),
        market_ask=_dec("0.65"),
        edge_vs_ask=_dec("0.20"),
        min_edge_required=_dec("0.05"),
        recommended_side=Side.UP,
        return_aligned=True,
        requested_size_usd=_dec("5"),
        max_position_usd=_dec("5"),
        open_positions_count=0,
        max_open_positions=1,
        daily_realized_pnl=_dec("0"),
        max_daily_loss_usd=_dec("10"),
        risk_allowed=True,
        risk_reject_reason=None,
    )


def _position_for_settlement(pid: str, *, won: bool) -> PaperPosition:
    return PaperPosition(
        position_id=pid,
        decision_id="d1",
        market_slug="m1",
        event_url=None,
        selected_side=Side.UP,
        token_id="up",
        entry_timestamp_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        entry_price=_dec("0.65"),
        entry_best_ask=_dec("0.65"),
        entry_best_bid=_dec("0.62"),
        entry_spread=_dec("0.03"),
        entry_size_usd=_dec("5"),
        shares=_dec("7.692307"),
        fair_price_at_entry=_dec("0.85"),
        max_buy_price_at_entry=_dec("0.81"),
        edge_at_entry=_dec("0.20"),
        round_open_price=_dec("100"),
        btc_price_at_entry=_dec("100.10"),
        distance_bucket_at_entry=DistanceBucket.D_010_020pct,
        volatility_bucket_at_entry=VolatilityBucket.VOL_LOW,
        pattern_at_entry="normal_bull",
        stage_at_entry=Stage.AFTER_10M,
        seconds_to_expiry_at_entry=600,
        current_side_at_entry=CurrentSide.ABOVE_OPEN,
        status=PositionStatus.OPEN,
        rule_id="r1",
        rule_match_type=RuleMatchType.EXACT,
        historical_probability_at_entry=_dec("0.85"),
        samples_at_entry=100,
    )


def _position_for(s: Settlement) -> PaperPosition:
    return _position_for_settlement(s.position_id, won=s.won)


def _settlement_winning(pid: str, slug: str) -> Settlement:
    return Settlement(
        settlement_id="set1",
        position_id=pid,
        market_slug=slug,
        resolved_outcome=Side.UP,
        selected_side=Side.UP,
        won=True,
        entry_price=_dec("0.65"),
        shares=_dec("7.692307"),
        cost_usd=_dec("5"),
        payout_usd=_dec("7.692307"),
        realized_pnl_usd=_dec("2.692307"),
        realized_roi_pct=_dec("0.538461"),
        settlement_source=SettlementSource.POLYMARKET_API,
        round_open_price=_dec("100"),
        round_close_price=_dec("101"),
        final_btc_price=_dec("101"),
        resolved_at_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        trade_quality=TradeQuality.GOOD_WIN,
        edge_at_entry=_dec("0.20"),
        spread_at_entry=_dec("0.03"),
        rule_id="r1",
        historical_probability_at_entry=_dec("0.85"),
        seconds_to_expiry_at_entry=600,
    )

def test_classify_skip_reason_maps_to_funnel_stages():
    # Exact matches
    assert _classify_skip_reason("market_not_active") == "market_state"
    assert _classify_skip_reason("market_closed") == "market_state"
    assert _classify_skip_reason("no_in_round_candle") == "stage"
    assert _classify_skip_reason("stale_orderbook") == "data_freshness"
    assert _classify_skip_reason("no_rule_for_state") == "rule_lookup"
    assert _classify_skip_reason("missing_top_of_book") == "orderbook"
    # Dynamic strings (signal_engine emits "name:value" with ":")
    assert _classify_skip_reason("ask_above_max_buy_price:0.7>0.5") == "trade_conditions"
    assert _classify_skip_reason("edge_below_min:0.01<0.05") == "trade_conditions"
    assert _classify_skip_reason("spread_too_wide:0.10>0.05") == "trade_conditions"
    assert _classify_skip_reason("liquidity_too_low:None<1000") == "trade_conditions"
    assert _classify_skip_reason("insufficient_ask_size:0<5") == "trade_conditions"
    assert _classify_skip_reason("risk_rejected:max_open_positions") == "risk"
    # rule_filtered: from probability_rules (no_trade_reasons joined with ";")
    assert (
        _classify_skip_reason("rule_filtered:samples_below_threshold:41<60") == "rule_lookup"
    )
    assert (
        _classify_skip_reason(
            "rule_filtered:samples_below_threshold:10<60;probability_below_threshold:0.5<0.6"
        )
        == "rule_lookup"
    )
    # None / unknown
    assert _classify_skip_reason(None) == "other"
    assert _classify_skip_reason("something_new_we_dont_know") == "other"


def test_decision_funnel_counts_trades_and_skips_by_stage():
    decisions = [
        {"decision": "TRADE", "timeframe": "M15", "stage": "AFTER_5M"},
        {"decision": "TRADE", "timeframe": "M15", "stage": "AFTER_10M"},
        {"decision": "SKIP", "skip_reason": "no_rule_for_state", "timeframe": "M5", "stage": "CUSTOM_5M_STATE"},
        {"decision": "SKIP", "skip_reason": "market_closed", "timeframe": "M15", "stage": "AFTER_5M"},
        {"decision": "SKIP", "skip_reason": "ask_above_max_buy_price:0.7>0.5", "timeframe": "M15", "stage": "AFTER_10M"},
        {"decision": "SKIP", "skip_reason": "risk_rejected:max_open_positions", "timeframe": "M15", "stage": "AFTER_5M"},
    ]
    f = decision_funnel(decisions)
    assert f["total_decisions"] == 6
    assert f["traded"] == 2
    assert f["skipped_total"] == 4
    assert f["skipped_by_stage"] == {
        "rule_lookup": 1,
        "market_state": 1,
        "trade_conditions": 1,
        "risk": 1,
    }
    assert f["by_timeframe"]["M15"]["traded"] == 2
    assert f["by_timeframe"]["M5"]["traded"] == 0
    assert f["by_timeframe"]["M5"]["skipped_by_stage"]["rule_lookup"] == 1
    assert f["by_stage_label"]["CUSTOM_5M_STATE"]["skipped_by_stage"]["rule_lookup"] == 1
    assert f["by_stage_label"]["AFTER_10M"]["traded"] == 1


def test_decision_funnel_handles_empty_input():
    assert decision_funnel([]) == {
        "total_decisions": 0,
        "traded": 0,
        "skipped_total": 0,
        "skipped_by_stage": {},
        "by_timeframe": {},
        "by_stage_label": {},
    }

