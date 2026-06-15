"""Tests for investigation_report: required sections, table rendering."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.investigation_report import (
    render_report,
    REQUIRED_SECTIONS,
    categorization_table_markdown,
)


@pytest.fixture
def sample_categorization() -> dict:
    return {
        "n_pairs_analyzed": 79,
        "counts_by_category": {
            "edge_threshold": 1,
            "candle_selection": 0,
            "identical": 78,
            "unknown": 0,
        },
        "thresholds": {
            "VOL_LOW_MAX": "0.000897",
            "VOL_NORMAL_MAX": "0.001871",
        },
    }


@pytest.fixture
def sample_per_pair() -> list[dict]:
    return [
        {"market_slug": "A", "round_start_utc": "2026-06-06T12:00:00+00:00",
         "live_vol_bucket": "VOL_NORMAL", "backtest_vol_bucket": "VOL_HIGH",
         "live_vol_mean": "0.0012", "backtest_vol_mean": "0.0020",
         "vol_mean_diff": "0.0008", "category": "candle_selection"},
    ]


def test_render_report_contains_required_sections(sample_categorization, sample_per_pair, tmp_path):
    out = tmp_path / "report.md"
    render_report(categorization=sample_categorization, per_pair=sample_per_pair, out_path=out)
    text = out.read_text()
    for section in REQUIRED_SECTIONS:
        assert section in text, f"missing section: {section}"


def test_render_report_includes_dominant_category(sample_categorization, sample_per_pair, tmp_path):
    out = tmp_path / "report.md"
    render_report(categorization=sample_categorization, per_pair=sample_per_pair, out_path=out)
    text = out.read_text()
    assert "identical" in text
    assert "78" in text


def test_categorization_table_markdown(sample_categorization):
    md = categorization_table_markdown(sample_categorization)
    assert "| Category | Count | % |" in md
    assert "edge_threshold" in md
    assert "candle_selection" in md
    assert "47" not in md  # not in our fixture
