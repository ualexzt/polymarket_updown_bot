"""Tests for walk_forward_report: required sections, fold table, regime tables."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.walk_forward_report import (
    render_report,
    REQUIRED_SECTIONS,
    fold_table_markdown,
    regime_table_markdown,
)


@pytest.fixture
def sample_aggregate() -> dict:
    return {
        "data_start": "2025-12-07T00:00:00+00:00",
        "data_end": "2026-06-15T00:00:00+00:00",
        "n_folds": 3,
        "folds": [
            {"fold_id": 0, "test_start": "2025-12-07T00:00:00+00:00", "test_end": "2026-01-06T00:00:00+00:00",
             "n_rounds": 2880, "n_trades": 50, "n_wins": 30, "wr": "0.6000",
             "pnl": "-5.00", "avg_pnl": "-0.1000", "avg_entry_price": "0.62", "n_by_stage": {}, "n_by_side": {}},
            {"fold_id": 1, "test_start": "2026-01-06T00:00:00+00:00", "test_end": "2026-02-05T00:00:00+00:00",
             "n_rounds": 2880, "n_trades": 45, "n_wins": 25, "wr": "0.5556",
             "pnl": "-8.00", "avg_pnl": "-0.1778", "avg_entry_price": "0.65", "n_by_stage": {}, "n_by_side": {}},
            {"fold_id": 2, "test_start": "2026-02-05T00:00:00+00:00", "test_end": "2026-03-07T00:00:00+00:00",
             "n_rounds": 2880, "n_trades": 60, "n_wins": 35, "wr": "0.5833",
             "pnl": "-7.50", "avg_pnl": "-0.1250", "avg_entry_price": "0.63", "n_by_stage": {}, "n_by_side": {}},
        ],
        "cross_fold": {"wr_mean": "0.5796", "wr_stdev": "0.0183", "pnl_total": "-20.50"},
    }


def test_render_report_contains_required_sections(sample_aggregate, tmp_path):
    out = tmp_path / "report.md"
    render_report(aggregate=sample_aggregate, out_path=out)
    text = out.read_text()
    for section in REQUIRED_SECTIONS:
        assert section in text, f"missing section: {section}"


def test_fold_table_markdown_format(sample_aggregate):
    md = fold_table_markdown(sample_aggregate["folds"])
    # Fold IDs appear as first column values
    assert "| 0 |" in md
    assert "| 2 |" in md
    assert "60.00%" in md  # WR formatted as percentage
    assert "PnL" in md
    # Header row
    assert "Fold" in md
    assert "WR" in md


def test_regime_table_markdown_format():
    rows = [
        {"key": "UP", "n": 100, "wins": 60, "wr": "0.6000", "pnl": "-5.00"},
        {"key": "DOWN", "n": 80, "wins": 40, "wr": "0.5000", "pnl": "-12.00"},
    ]
    md = regime_table_markdown(rows)
    assert "UP" in md
    assert "DOWN" in md
    assert "60.00%" in md
