"""Tests for full fold simulation: round iterator, simulate_fold."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import Candle

from scripts.walk_forward_backtest import (
    Fold,
    iter_round_starts,
    simulate_fold,
    build_rule_index,
)


def test_iter_round_starts_aligned_to_15m():
    """Round starts should be aligned to the quarter-hour."""
    data_start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    data_end = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)  # 4h
    starts = list(iter_round_starts(data_start, data_end))
    # 4h / 15m = 16 rounds
    assert len(starts) == 16
    for s in starts:
        # Aligned to UTC quarter-hour
        assert s.minute in (0, 15, 30, 45)
        assert s.second == 0
        assert s.microsecond == 0


def test_iter_round_starts_skips_unaligned_start():
    """If data_start is at 12:07, we round up to 12:15."""
    data_start = datetime(2026, 6, 1, 12, 7, tzinfo=UTC)
    data_end = datetime(2026, 6, 1, 13, 0, tzinfo=UTC)
    starts = list(iter_round_starts(data_start, data_end))
    # First start should be 12:15; 13:00 - 15min = 12:45 is last
    assert len(starts) == 3  # 12:15, 12:30, 12:45
    assert starts[0] == datetime(2026, 6, 1, 12, 15, tzinfo=UTC)
    assert starts[-1] == datetime(2026, 6, 1, 12, 45, tzinfo=UTC)


def test_simulate_fold_returns_trades_list(synthetic_5d_candles, sample_rules):
    """End-to-end: feed 5 days of candles, get trades list and summary."""
    fold = Fold(
        fold_id=0,
        train_start=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        train_end=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        test_start=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        test_end=datetime(2026, 6, 6, 0, 0, tzinfo=UTC),
    )
    rules_index = build_rule_index(sample_rules)
    trades, summary = simulate_fold(
        fold=fold,
        candles=synthetic_5d_candles,
        rules_index=rules_index,
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
        safety_buffer=Decimal("0.05"),
        max_entry_ask=Decimal("0.80"),
    )
    assert isinstance(trades, list)
    assert "n_rounds" in summary
    assert "n_trades" in summary
    assert "wr" in summary
    assert "pnl" in summary
    # Our synthetic candles have constant price (no volatility pattern), so probably 0 trades.
    # That's fine — just verify the structure.
