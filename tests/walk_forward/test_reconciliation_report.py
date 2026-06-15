"""Tests for reconciliation_report: required sections, summary table, verdict."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.reconciliation_report import (
    render_report,
    REQUIRED_SECTIONS,
    summary_table_markdown,
)


@pytest.fixture
def sample_summary() -> dict:
    return {
        "verdict": "B",
        "n_matched": 7,
        "n_live_only": 0,
        "n_backtest_only": 12,
        "mismatch_counts": {"state": 5, "price": 1, "settlement": 0, "total": 6},
        "recommendation": "State fields differ. Fix round_state.py.",
    }


def test_render_report_contains_required_sections(sample_summary, tmp_path):
    out = tmp_path / "report.md"
    render_report(summary=sample_summary, matched_pairs=[], live_only=[],
                  backtest_only=[], out_path=out)
    text = out.read_text()
    for section in REQUIRED_SECTIONS:
        assert section in text, f"missing section: {section}"


def test_render_report_includes_verdict(sample_summary, tmp_path):
    out = tmp_path / "report.md"
    render_report(summary=sample_summary, matched_pairs=[], live_only=[],
                  backtest_only=[], out_path=out)
    text = out.read_text()
    assert "Verdict: B" in text or "**B**" in text
    assert "Fix round_state.py" in text


def test_summary_table_markdown(sample_summary):
    md = summary_table_markdown(sample_summary)
    assert "Matched pairs" in md
    assert "Live-only" in md
    assert "Backtest-only" in md
    assert "Verdict" in md
    assert "7" in md
