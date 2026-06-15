"""Tests for reconciliation: data loading, slug matching, field comparison, verdict logic."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.reconcile_live_vs_backtest import (
    load_live_settlements,
    load_live_decisions,
    load_backtest_trades,
    match_by_slug,
    compare_pair,
    categorize_verdict,
    REQUIRED_LIVE_SETTLEMENT_COLS,
    REQUIRED_BACKTEST_TRADE_COLS,
)


@pytest.fixture
def live_db(tmp_path: Path) -> Path:
    """Create a tiny SQLite with settlements and paper_positions tables matching live schema."""
    db = tmp_path / "live.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE settlements (
            settlement_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            resolved_outcome TEXT NOT NULL,
            selected_side TEXT NOT NULL,
            won INTEGER NOT NULL,
            entry_price TEXT NOT NULL,
            shares TEXT NOT NULL,
            cost_usd TEXT NOT NULL,
            payout_usd TEXT NOT NULL,
            realized_pnl_usd TEXT NOT NULL,
            realized_roi_pct TEXT NOT NULL,
            settlement_source TEXT NOT NULL,
            round_open_price TEXT NOT NULL,
            round_close_price TEXT NOT NULL,
            final_btc_price TEXT NOT NULL,
            resolved_at_utc TEXT NOT NULL,
            trade_quality TEXT NOT NULL,
            edge_at_entry TEXT NOT NULL,
            spread_at_entry TEXT NOT NULL,
            rule_id TEXT,
            historical_probability_at_entry TEXT NOT NULL,
            seconds_to_expiry_at_entry INTEGER NOT NULL
        );
        CREATE TABLE paper_positions (
            position_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            event_url TEXT,
            selected_side TEXT NOT NULL,
            token_id TEXT NOT NULL,
            entry_timestamp_utc TEXT NOT NULL,
            entry_price TEXT NOT NULL,
            entry_best_ask TEXT NOT NULL,
            entry_best_bid TEXT NOT NULL,
            entry_spread TEXT NOT NULL,
            entry_size_usd TEXT NOT NULL,
            shares TEXT NOT NULL,
            fair_price_at_entry TEXT NOT NULL,
            max_buy_price_at_entry TEXT NOT NULL,
            edge_at_entry TEXT NOT NULL,
            round_open_price TEXT NOT NULL,
            btc_price_at_entry TEXT NOT NULL,
            distance_bucket_at_entry TEXT NOT NULL,
            volatility_bucket_at_entry TEXT NOT NULL,
            pattern_at_entry TEXT NOT NULL,
            stage_at_entry TEXT NOT NULL,
            seconds_to_expiry_at_entry INTEGER NOT NULL,
            current_side_at_entry TEXT NOT NULL,
            status TEXT NOT NULL,
            rule_id TEXT,
            rule_match_type TEXT NOT NULL,
            historical_probability_at_entry TEXT NOT NULL,
            samples_at_entry INTEGER NOT NULL
        );
        CREATE TABLE decisions (
            decision_id TEXT PRIMARY KEY,
            timestamp_utc TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            event_url TEXT,
            timeframe TEXT NOT NULL,
            round_start_ts TEXT NOT NULL,
            round_end_ts TEXT NOT NULL,
            seconds_to_expiry INTEGER NOT NULL,
            stage TEXT NOT NULL,
            side_checked TEXT NOT NULL,
            selected_side TEXT,
            outcome_token_id TEXT,
            opposite_token_id TEXT,
            decision TEXT NOT NULL,
            skip_reason TEXT,
            round_open_price TEXT NOT NULL,
            current_btc_price TEXT NOT NULL,
            current_side TEXT NOT NULL,
            distance_from_round_open TEXT NOT NULL,
            distance_bucket TEXT NOT NULL,
            volatility_bucket TEXT NOT NULL,
            candle_pattern TEXT NOT NULL,
            pattern_combo TEXT,
            c0_open TEXT, c0_high TEXT, c0_low TEXT, c0_close TEXT, c0_volume TEXT,
            c1_open TEXT, c1_high TEXT, c1_low TEXT, c1_close TEXT, c1_volume TEXT,
            source_exchange TEXT NOT NULL,
            source_symbol TEXT NOT NULL,
            binance_data_received_at_utc TEXT NOT NULL,
            binance_data_age_seconds TEXT NOT NULL,
            up_best_bid TEXT, up_best_ask TEXT, down_best_bid TEXT, down_best_ask TEXT,
            up_spread TEXT, down_spread TEXT,
            selected_best_bid TEXT, selected_best_ask TEXT, selected_spread TEXT,
            selected_ask_size TEXT, selected_bid_size TEXT,
            orderbook_depth_top_5_json TEXT NOT NULL,
            liquidity_usd_estimate TEXT,
            market_active INTEGER NOT NULL, market_closed INTEGER NOT NULL, market_accepting_orders INTEGER NOT NULL,
            orderbook_received_at_utc TEXT NOT NULL, orderbook_age_seconds TEXT NOT NULL,
            metadata_received_at_utc TEXT NOT NULL, metadata_age_seconds TEXT NOT NULL,
            rule_id TEXT, rule_match_type TEXT NOT NULL, samples INTEGER NOT NULL,
            historical_probability TEXT, fair_price TEXT, safety_buffer TEXT NOT NULL,
            max_buy_price TEXT, market_ask TEXT, edge_vs_ask TEXT, min_edge_required TEXT NOT NULL,
            recommended_side TEXT, return_aligned INTEGER NOT NULL,
            requested_size_usd TEXT NOT NULL, max_position_usd TEXT NOT NULL,
            open_positions_count INTEGER NOT NULL, max_open_positions INTEGER NOT NULL,
            daily_realized_pnl TEXT NOT NULL, max_daily_loss_usd TEXT NOT NULL,
            risk_allowed INTEGER NOT NULL, risk_reject_reason TEXT
        );
    """)
    con.commit()
    return db


def _insert_settlement(con, slug, won=1, entry="0.55", pnl="0.45", rule="r1", hist_prob="0.60"):
    con.execute(
        "INSERT INTO settlements (settlement_id, position_id, market_slug, resolved_outcome, selected_side, "
        "won, entry_price, shares, cost_usd, payout_usd, realized_pnl_usd, realized_roi_pct, "
        "settlement_source, round_open_price, round_close_price, final_btc_price, resolved_at_utc, "
        "trade_quality, edge_at_entry, spread_at_entry, rule_id, historical_probability_at_entry, "
        "seconds_to_expiry_at_entry) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"sett-{slug}", f"pos-{slug}", slug, "UP", "UP", won, entry, "1.0",
         entry, "1.0", pnl, "0.81", "BINANCE_FALLBACK", "50000", "50100", "50100",
         "2026-06-15T12:00:00+00:00", "GOOD_WIN", "0.05", "0.01", rule, hist_prob, 300),
    )


def _insert_position(con, slug, stage="AFTER_10M", vol="VOL_LOW", dist="D_0_005pct",
                    side="BELOW_OPEN", pattern="strong_bull -> normal_bull"):
    con.execute(
        "INSERT INTO paper_positions (position_id, decision_id, market_slug, event_url, selected_side, "
        "token_id, entry_timestamp_utc, entry_price, entry_best_ask, entry_best_bid, entry_spread, "
        "entry_size_usd, shares, fair_price_at_entry, max_buy_price_at_entry, edge_at_entry, "
        "round_open_price, btc_price_at_entry, distance_bucket_at_entry, volatility_bucket_at_entry, "
        "pattern_at_entry, stage_at_entry, seconds_to_expiry_at_entry, current_side_at_entry, status, "
        "rule_id, rule_match_type, historical_probability_at_entry, samples_at_entry) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"pos-{slug}", f"dec-{slug}", slug, None, "UP", "tok", "2026-06-15T11:50:00+00:00",
         "0.55", "0.55", "0.54", "0.01", "1.0", "1.0", "0.60", "0.55", "0.05",
         "50000", "50050", dist, vol, pattern, stage, 300, side, "SETTLED",
         "r1", "exact", "0.60", 120),
    )


def test_load_live_settlements_returns_rows(live_db):
    con = sqlite3.connect(live_db)
    _insert_position(con, "slug-A")
    _insert_settlement(con, "slug-A")
    _insert_position(con, "slug-B")
    _insert_settlement(con, "slug-B", won=0, pnl="-0.55")
    con.commit()
    con.close()

    rows = load_live_settlements(live_db, period_start=datetime(2026, 6, 1, tzinfo=UTC))
    assert len(rows) == 2
    for r in rows:
        assert "market_slug" in r
        assert "stage_at_entry" in r
        assert "pattern_at_entry" in r


def test_load_backtest_trades_from_csv(tmp_path: Path):
    csv_path = tmp_path / "trades.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(REQUIRED_BACKTEST_TRADE_COLS))
        w.writeheader()
        w.writerow({k: "x" for k in REQUIRED_BACKTEST_TRADE_COLS})
        w.writerow({k: "x" for k in REQUIRED_BACKTEST_TRADE_COLS})
    rows = load_backtest_trades(csv_path)
    assert len(rows) == 2


def test_match_by_slug_keys():
    live = [{"market_slug": "slug-A", "entry_price": "0.55"}, {"market_slug": "slug-B", "entry_price": "0.60"}]
    backtest = [{"market_slug": "slug-A", "entry_price": "0.50"}, {"market_slug": "slug-C", "entry_price": "0.65"}]
    matched, live_only, backtest_only = match_by_slug(live, backtest)
    assert len(matched) == 1
    assert matched[0][0]["market_slug"] == "slug-A"
    assert len(live_only) == 1
    assert live_only[0]["market_slug"] == "slug-B"
    assert len(backtest_only) == 1
    assert backtest_only[0]["market_slug"] == "slug-C"


def test_compare_pair_state_mismatch():
    live = {
        "market_slug": "slug-A", "selected_side": "UP", "won": 1,
        "entry_price": "0.55", "realized_pnl_usd": "0.45",
        "rule_id": "r1", "historical_probability_at_entry": "0.60",
        "round_open_price": "50000", "round_close_price": "50100",
        "stage_at_entry": "AFTER_5M",
        "volatility_bucket_at_entry": "VOL_LOW",
        "distance_bucket_at_entry": "D_0_005pct",
        "current_side_at_entry": "BELOW_OPEN",
        "pattern_at_entry": "weak_bear",
    }
    backtest = {
        "market_slug": "slug-A", "stage": "AFTER_10M", "current_side": "BELOW_OPEN",
        "distance_bucket": "D_0_005pct", "volatility_bucket": "VOL_LOW",
        "pattern": "strong_bull -> normal_bull",
        "rule_id": "r1", "recommended_side": "UP",
        "historical_probability": "0.60", "entry_price": "0.55",
        "won": True, "pnl": "0.45",
        "round_open_price": "50000", "round_close_price": "50100",
    }
    diff = compare_pair(live, backtest, entry_tolerance=Decimal("0.01"))
    assert diff["matched"] is True
    state_mismatches = [d for d in diff["field_diffs"] if d["category"] == "state"]
    assert len(state_mismatches) >= 2
    price_mismatches = [d for d in diff["field_diffs"] if d["category"] == "price"]
    assert len(price_mismatches) == 0


def test_compare_pair_entry_price_within_tolerance():
    live = {"market_slug": "A", "entry_price": "0.55", "selected_side": "UP", "won": 1,
            "realized_pnl_usd": "0.45", "rule_id": "r1", "historical_probability_at_entry": "0.60",
            "round_open_price": "50000", "round_close_price": "50100",
            "stage_at_entry": "AFTER_10M", "volatility_bucket_at_entry": "VOL_LOW",
            "distance_bucket_at_entry": "D_0_005pct", "current_side_at_entry": "BELOW_OPEN",
            "pattern_at_entry": "x -> y"}
    backtest = {"market_slug": "A", "entry_price": "0.555", "stage": "AFTER_10M", "current_side": "BELOW_OPEN",
                "distance_bucket": "D_0_005pct", "volatility_bucket": "VOL_LOW", "pattern": "x -> y",
                "rule_id": "r1", "recommended_side": "UP", "historical_probability": "0.60",
                "won": True, "pnl": "0.45", "round_open_price": "50000", "round_close_price": "50100"}
    diff = compare_pair(live, backtest, entry_tolerance=Decimal("0.01"))
    assert all(d["category"] != "price" for d in diff["field_diffs"])


def test_compare_pair_entry_price_outside_tolerance():
    live = {"market_slug": "A", "entry_price": "0.55", "selected_side": "UP", "won": 1,
            "realized_pnl_usd": "0.45", "rule_id": "r1", "historical_probability_at_entry": "0.60",
            "round_open_price": "50000", "round_close_price": "50100",
            "stage_at_entry": "AFTER_10M", "volatility_bucket_at_entry": "VOL_LOW",
            "distance_bucket_at_entry": "D_0_005pct", "current_side_at_entry": "BELOW_OPEN",
            "pattern_at_entry": "x -> y"}
    backtest = {"market_slug": "A", "entry_price": "0.65", "stage": "AFTER_10M", "current_side": "BELOW_OPEN",
                "distance_bucket": "D_0_005pct", "volatility_bucket": "VOL_LOW", "pattern": "x -> y",
                "rule_id": "r1", "recommended_side": "UP", "historical_probability": "0.60",
                "won": True, "pnl": "-0.65", "round_open_price": "50000", "round_close_price": "50100"}
    diff = compare_pair(live, backtest, entry_tolerance=Decimal("0.01"))
    price_mismatches = [d for d in diff["field_diffs"] if d["category"] == "price"]
    assert len(price_mismatches) >= 1


def test_categorize_verdict_state_dominant():
    diffs = [{"field_diffs": [{"category": "state"}, {"category": "state"}]}] * 4 + \
            [{"field_diffs": [{"category": "price"}]}] * 1
    result = categorize_verdict(diffs, n_matched=5, n_live_only=0, n_backtest_only=0)
    assert result["verdict"] == "B"


def test_categorize_verdict_insufficient_data():
    diffs = [{"field_diffs": []}] * 3
    result = categorize_verdict(diffs, n_matched=3, n_live_only=0, n_backtest_only=0)
    assert result["verdict"] == "D"


def test_categorize_verdict_filter_dominant():
    diffs = [{"field_diffs": []}] * 5
    result = categorize_verdict(diffs, n_matched=5, n_live_only=20, n_backtest_only=0)
    assert result["verdict"] == "A"


def test_categorize_verdict_settlement_dominant():
    diffs = [{"field_diffs": [{"category": "settlement"}, {"category": "settlement"}]}] * 4 + \
            [{"field_diffs": []}] * 1
    result = categorize_verdict(diffs, n_matched=5, n_live_only=0, n_backtest_only=0)
    assert result["verdict"] == "C"
