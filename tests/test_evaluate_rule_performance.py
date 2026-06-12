from __future__ import annotations

import sqlite3
from decimal import Decimal

from scripts.evaluate_rule_performance import fetch_rows, summarize_rows


def test_fetch_rows_returns_empty_when_database_missing(tmp_path):
    assert fetch_rows(tmp_path / "missing" / "paper.sqlite", since=None) == []


def test_fetch_rows_returns_empty_when_table_missing(tmp_path):
    db = tmp_path / "fresh.sqlite"
    sqlite3.connect(db).close()
    assert fetch_rows(db, since=None) == []


def test_fetch_rows_returns_empty_when_db_corrupted(tmp_path):
    db = tmp_path / "bad.sqlite"
    db.write_bytes(b"not a database")
    assert fetch_rows(db, since=None) == []


def test_summarize_rows_computes_rule_metrics():
    rows = [
        {
            "rule_id": "rule_a",
            "selected_side": "UP",
            "stage": "AFTER_5M",
            "entry_price": "0.50",
            "historical_probability_at_entry": "0.70",
            "edge_at_entry": "0.20",
            "won": 1,
            "realized_pnl_usd": "1.00",
        },
        {
            "rule_id": "rule_a",
            "selected_side": "UP",
            "stage": "AFTER_5M",
            "entry_price": "0.75",
            "historical_probability_at_entry": "0.70",
            "edge_at_entry": "-0.05",
            "won": 0,
            "realized_pnl_usd": "-1.00",
        },
    ]

    summary = summarize_rows(rows)

    assert len(summary) == 1
    item = summary[0]
    assert item["rule_id"] == "rule_a"
    assert item["n"] == 2
    assert item["wins"] == 1
    assert item["win_rate"] == Decimal("0.5")
    assert item["pnl"] == Decimal("0.00")
    assert item["avg_entry_price"] == Decimal("0.625")
    assert item["breakeven_win_rate"] == Decimal("0.625")
