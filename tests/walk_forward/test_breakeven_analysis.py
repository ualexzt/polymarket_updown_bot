"""Tests for breakeven_analysis: sensitivity table, rule rankings, regime breakdown."""
from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.breakeven_analysis import (
    breakeven_sensitivity,
    rule_performance,
    regime_breakdown,
    write_breakeven_csv,
    write_rule_ranking_csv,
)


@pytest.fixture
def sample_trades() -> list[dict]:
    """3 UP wins @ 0.55, 2 DOWN losses @ 0.65, 1 UP win @ 0.70."""
    base = {
        "fold_id": 0, "stage": "AFTER_10M", "rule_id": "r1",
        "recommended_side": "UP", "entry_price": "0.55", "historical_probability": "0.60",
        "distance_bucket": "D_0_005pct", "volatility_bucket": "VOL_LOW",
        "pattern": "x", "current_side": "BELOW_OPEN",
        "round_open_price": "50000", "current_btc_price": "50050",
    }
    trades = []
    for i, (won, entry, side) in enumerate([
        (True, "0.55", "UP"), (True, "0.55", "UP"), (True, "0.55", "UP"),
        (False, "0.65", "DOWN"), (False, "0.65", "DOWN"),
        (True, "0.70", "UP"),
    ]):
        t = {**base, "rule_id": f"r{i}", "entry_price": entry, "recommended_side": side, "won": won}
        trades.append(t)
    return trades


def test_breakeven_sensitivity_basic(sample_trades):
    # Bins are [lo, hi) so 0.55 falls into the 0.54-0.59 bin.
    rows = breakeven_sensitivity(sample_trades, entry_bins=[Decimal("0.54"), Decimal("0.60"), Decimal("0.65")])
    assert len(rows) == 3
    # First row covers 3 UP wins @ 0.55
    assert rows[0]["n_trades"] == 3
    # Each row has a breakeven_wr field
    for r in rows:
        assert "breakeven_wr" in r
        assert "wr_minus_breakeven" in r


def test_rule_performance_groups_by_rule_id(sample_trades):
    rows = rule_performance(sample_trades)
    assert len(rows) == 6  # each trade has a unique rule_id
    for r in rows:
        assert "rule_id" in r
        assert "n" in r
        assert "wins" in r
        assert "pnl" in r
        assert "wr" in r


def test_write_breakeven_csv_round_trip(tmp_path: Path, sample_trades):
    # Bins [0.50, 0.55) and [0.60, 0.65) — none of our trades fall in those (entry 0.55 and 0.65 are upper bounds, exclusive).
    rows = breakeven_sensitivity(sample_trades, entry_bins=[Decimal("0.50"), Decimal("0.60")])
    out = tmp_path / "be.csv"
    write_breakeven_csv(rows, out)
    text = out.read_text()
    assert "entry_price_bin" in text
    assert "n_trades" in text
    assert "wr" in text
    with out.open() as f:
        loaded = list(csv.DictReader(f))
    assert len(loaded) == 2
    # All bins have 0 trades (boundary cases)
    assert all(int(r["n_trades"]) == 0 for r in loaded)


def test_write_rule_ranking_csv_round_trip(tmp_path: Path, sample_trades):
    rows = rule_performance(sample_trades, min_trades=1)
    out = tmp_path / "rr.csv"
    write_rule_ranking_csv(rows, out)
    text = out.read_text()
    assert "rule_id" in text
    assert "n" in text
    assert "pnl" in text
    with out.open() as f:
        loaded = list(csv.DictReader(f))
    assert len(loaded) == 6


def test_regime_breakdown_returns_sections(sample_trades):
    out = regime_breakdown(sample_trades)
    assert "by_volatility" in out
    assert "by_distance" in out
    assert "by_pattern" in out
    assert "by_hour" in out
    assert "by_dow" in out
    assert "by_side" in out
