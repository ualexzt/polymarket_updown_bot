"""Tests for fetch_binance_history: CSV writing, pagination boundary, resume."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from scripts.fetch_binance_history import (
    rows_to_csv,
    write_meta,
    CANDLE_COLUMNS,
    fetch_klines_page,
)


def _fake_kline(open_time_ms: int, price: str = "50000") -> list[object]:
    return [
        open_time_ms,
        price,
        price,
        price,
        price,
        "10",
        open_time_ms + 5 * 60 * 1000,
        "0",
        0,
        "0",
        "0",
        "0",
    ]


def test_rows_to_csv_writes_header_and_rows(tmp_path: Path):
    rows = [
        (datetime(2026, 1, 1, 0, 0, tzinfo=UTC), Decimal("50000"), Decimal("50100"), Decimal("49900"), Decimal("50050"), Decimal("5"),
         datetime(2026, 1, 1, 0, 5, tzinfo=UTC)),
        (datetime(2026, 1, 1, 0, 5, tzinfo=UTC), Decimal("50050"), Decimal("50150"), Decimal("50000"), Decimal("50100"), Decimal("3"),
         datetime(2026, 1, 1, 0, 10, tzinfo=UTC)),
    ]
    out = tmp_path / "test.csv"
    rows_to_csv(rows, out)
    text = out.read_text()
    assert text.splitlines()[0] == ",".join(CANDLE_COLUMNS)
    assert len(text.splitlines()) == 3
    assert "50050" in text


def test_write_meta_includes_sha256_and_counts(tmp_path: Path):
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("a\n1\n2\n")
    meta = write_meta(
        csv_path,
        row_count=2,
        min_time=datetime(2026, 1, 1, tzinfo=UTC),
        max_time=datetime(2026, 1, 2, tzinfo=UTC),
        fetch_seconds=12.3,
    )
    assert meta["row_count"] == 2
    assert meta["sha256"] == hashlib.sha256(b"a\n1\n2\n").hexdigest()
    assert meta["min_time"] == "2026-01-01T00:00:00+00:00"
    assert meta["max_time"] == "2026-01-02T00:00:00+00:00"
    assert meta["fetch_seconds"] == 12.3


def test_fetch_klines_page_parses_binance_response():
    fake_json = [_fake_kline(1735689600000, "50000"), _fake_kline(1735689900000, "50100")]
    with patch("scripts.fetch_binance_history.httpx.Client") as mock_client:
        mock_resp = type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: fake_json})()
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp
        rows = fetch_klines_page(symbol="BTCUSDT", end_time_ms=1735690000000, endpoint="https://x")
    assert len(rows) == 2
    assert rows[0][0] == datetime.fromtimestamp(1735689600, tz=UTC)
    assert rows[0][4] == Decimal("50000")  # close
