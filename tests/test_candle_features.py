"""Tests for candle_features pattern classification parity with research script.

These tests verify that the live classifier emits the same pattern names as
``polymarket_round_research_v2.py::candle_pattern`` for the same OHLC inputs.
The research script is the source of truth for the rules CSV.

Each test is annotated with the body/range, upper/range, lower/range values
so the boundary conditions are auditable.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from polymarket_round_bot.candle_features import compute_candle_features
from polymarket_round_bot.models import Candle


def _candle(*, o: float, h: float, lo: float, c: float) -> Candle:
    return Candle(
        open_time_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=Decimal("100"),
        is_closed=True,
    )


def test_strong_bull_close_near_high():
    """body/r=0.90, upper/r=0.10 (small), bull, no lower wick."""
    # open=100, high=101, low=100, close=100.9
    f = compute_candle_features(_candle(o=100, h=101, lo=100, c=100.9))
    assert f.pattern == "strong_bull_close_near_high"


def test_strong_bear_close_near_low():
    """body/r=0.90, lower/r=0.10 (small), bear, no upper wick."""
    # open=100, high=100, low=99, close=99.1
    f = compute_candle_features(_candle(o=100, h=100, lo=99, c=99.1))
    assert f.pattern == "strong_bear_close_near_low"


def test_weak_bull_small_body_no_long_wicks():
    """body/r=0.25 (small), upper/r=0.375, lower/r=0.375 (not long)."""
    # open=99.875, high=100.0, low=99.8, close=99.925
    f = compute_candle_features(_candle(o=99.875, h=100.0, lo=99.8, c=99.925))
    assert f.pattern == "weak_bull"


def test_weak_bear_small_body_no_long_wicks():
    """body/r=0.20 (small), upper/r=0.40, lower/r=0.40 (not long), bear."""
    # open=100.05, high=100.15, low=99.9, close=100
    f = compute_candle_features(_candle(o=100.05, h=100.15, lo=99.9, c=100))
    assert f.pattern == "weak_bear"


def test_bull_long_upper_wick_priority():
    """body/r=0.10+ (above doji), upper/r=0.77 (long) -> bull_long_upper_wick.
    Research path: wick checked BEFORE strong_bull_close_near_high check fails
    (small upper wick OK there), but long upper wick catches it first.
    """
    # open=100, high=110, low=99, close=101.5
    f = compute_candle_features(_candle(o=100, h=110, lo=99, c=101.5))
    assert f.pattern == "bull_long_upper_wick"


def test_bull_long_lower_wick():
    """body/r=0.10+, lower/r=0.87 (long), upper/r=0.03 (not long)."""
    # open=100, high=101.5, low=90, close=101.2
    f = compute_candle_features(_candle(o=100, h=101.5, lo=90, c=101.2))
    assert f.pattern == "bull_long_lower_wick"


def test_doji_long_upper_wick():
    """body/r=0.005 (doji), upper/r=0.98 (long), lower/r=0.01."""
    # open=100, high=110, low=99.9, close=100.05
    f = compute_candle_features(_candle(o=100, h=110, lo=99.9, c=100.05))
    assert f.pattern == "doji_long_upper_wick"


def test_doji_long_lower_wick():
    """body/r=0.005 (doji), lower/r=0.98 (long), upper/r=0.01."""
    # open=100, high=100.1, low=90, close=99.95
    f = compute_candle_features(_candle(o=100, h=100.1, lo=90, c=99.95))
    assert f.pattern == "doji_long_lower_wick"


def test_doji_two_long_wicks_becomes_flat():
    """body==0: research returns 'flat' (direction==0), bot matches."""
    # open=100, high=110, low=90, close=100 -> body=0
    f = compute_candle_features(_candle(o=100, h=110, lo=90, c=100))
    assert f.pattern == "flat"


def test_normal_bull_medium_body():
    """body/r in (0.25, 0.65) bull with no long wicks -> normal_bull."""
    # open=100, high=101, low=99.5, close=100.7
    # body=0.7, range=1.5, body/r=0.467
    # upper = 101 - 100.7 = 0.3, upper/r = 0.2 (not long, <0.45)
    # lower = 100 - 99.5 = 0.5, lower/r = 0.333 (not long)
    f = compute_candle_features(_candle(o=100, h=101, lo=99.5, c=100.7))
    assert f.pattern == "normal_bull"


def test_normal_bear_medium_body():
    """body/r in (0.25, 0.65) bear with no long wicks -> normal_bear."""
    # open=100.7, high=101, low=99.5, close=100
    # body=-0.7, range=1.5, body/r=0.467
    # upper = 101 - 100.7 = 0.3, upper/r = 0.2 (not long)
    # lower = 100 - 99.5 = 0.5, lower/r = 0.333 (not long)
    f = compute_candle_features(_candle(o=100.7, h=101, lo=99.5, c=100))
    assert f.pattern == "normal_bear"


def test_bear_long_upper_wick():
    """body/r=0.10+, bear, upper/r=0.77 (long)."""
    # open=101.5, high=110, low=99, close=100
    # body=-1.5, range=11, body/r=0.136
    # upper = 110 - 101.5 = 8.5, upper/r = 0.773 (long)
    # lower = 100 - 99 = 1, lower/r = 0.091 (not long)
    f = compute_candle_features(_candle(o=101.5, h=110, lo=99, c=100))
    assert f.pattern == "bear_long_upper_wick"


def test_bear_long_lower_wick():
    """body/r=0.10+, bear, lower/r=0.87 (long)."""
    # open=101, high=101.5, low=90, close=100
    # body=-1, range=11.5, body/r=0.087 (still doji! let me try more body)
    # Try: open=101.5, high=102, low=90, close=100
    # body=-1.5, range=12, body/r=0.125
    # upper = 102 - 101.5 = 0.5, upper/r = 0.042 (not long)
    # lower = 100 - 90 = 10, lower/r = 0.833 (long)
    f = compute_candle_features(_candle(o=101.5, h=102, lo=90, c=100))
    assert f.pattern == "bear_long_lower_wick"
