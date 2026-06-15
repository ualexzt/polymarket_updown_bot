"""Tests for investigate_vol_mean: per-pair analysis and categorization."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import Candle
from polymarket_round_bot.round_state import _VOL_LOW_MAX, _VOL_NORMAL_MAX

from scripts.investigate_vol_mean import (
    parse_round_start_from_slug,
    load_mismatched_pairs,
    analyze_pair,
    CATEGORIES,
)


def make_candle(open_time: datetime, open: str, close: str | None = None,
                high: str | None = None, low: str | None = None) -> Candle:
    o = Decimal(open)
    c = Decimal(close if close is not None else open)
    h = Decimal(high if high is not None else open)
    l = Decimal(low if low is not None else open)
    return Candle(
        open_time_utc=open_time if open_time.tzinfo else open_time.replace(tzinfo=UTC),
        open=o, high=h, low=l, close=c, volume=Decimal("10"), is_closed=True,
    )


def test_parse_round_start_from_slug():
    slug = "btc-updown-15m-1781526600"  # 2026-06-15 12:30 UTC
    rs = parse_round_start_from_slug(slug)
    assert rs == datetime(2026, 6, 15, 12, 30, tzinfo=UTC)


def test_analyze_pair_identical_vol_means():
    """When both perspectives return the same vol_mean, the analysis runs and categorizes."""
    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    round_start = start + timedelta(hours=4)
    candles = []
    for r in range(16):  # 16 prior rounds
        c0_time = start + timedelta(minutes=15 * r)
        candles.append(make_candle(c0_time, "50000", "50000"))
        candles.append(make_candle(c0_time + timedelta(minutes=5), "50010", "50010"))
        candles.append(make_candle(c0_time + timedelta(minutes=10), "50020", "50020"))
    result = analyze_pair(
        market_slug="btc-updown-15m-X",
        round_start=round_start,
        live_vol="VOL_NORMAL", backtest_vol="VOL_HIGH",
        candles=candles,
    )
    assert result["live_vol_mean"] is not None
    assert result["backtest_vol_mean"] is not None
    assert result["live_vol_mean"] != "None"
    assert result["backtest_vol_mean"] != "None"
    assert result["category"] in CATEGORIES


def test_analyze_pair_edge_case_threshold():
    """When vol_mean is within 1e-5 of a threshold, category is 'edge_threshold'."""
    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    round_start = start + timedelta(hours=4)
    candles = []
    # Use exactly the threshold value: round_abs_return = (c2 - c0) / c0
    # threshold = 0.000897 → c2 = c0 * (1 + 0.000897)
    # For c0=50000, c2 = 50000 + 44.85 → use 50045 (slight over, but within 1e-5)
    # Better: use small enough c0 that c2 is exactly an integer
    for r in range(16):
        c0_time = start + timedelta(minutes=15 * r)
        c0_open = 1000000
        # abs_return = 0.000897 exactly: c2 = 1000000 * 1.000897 = 1000897
        c2_close = c0_open + int(c0_open * _VOL_LOW_MAX)  # c0 + c0*threshold
        candles.append(make_candle(c0_time, str(c0_open), str(c0_open)))
        candles.append(make_candle(c0_time + timedelta(minutes=5),
                                    str(c0_open), str(c0_open + 1)))
        candles.append(make_candle(c0_time + timedelta(minutes=10),
                                    str(c0_open + 1), str(c2_close)))
    result = analyze_pair(
        market_slug="btc-updown-15m-X",
        round_start=round_start,
        live_vol="VOL_NORMAL", backtest_vol="VOL_LOW",
        candles=candles,
    )
    # vol_mean should be a string (Decimal or "None")
    assert isinstance(result["live_vol_mean"], str)
    # vol_mean should be very close to VOL_LOW_MAX
    live_mean = Decimal(result["live_vol_mean"])
    assert abs(live_mean - _VOL_LOW_MAX) < Decimal("0.00001")
    assert result["category"] == "edge_threshold"


def test_analyze_pair_insufficient_data():
    """When < 16 prior rounds exist, vol_mean is None → category 'unknown'."""
    round_start = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
    candles = [
        make_candle(datetime(2026, 6, 1, 0, 0, tzinfo=UTC), "50000", "50020"),
        make_candle(datetime(2026, 6, 1, 0, 5, tzinfo=UTC), "50010", "50010"),
        make_candle(datetime(2026, 6, 1, 0, 10, tzinfo=UTC), "50020", "50020"),
    ]
    result = analyze_pair(
        market_slug="btc-updown-15m-X",
        round_start=round_start,
        live_vol="VOL_NORMAL", backtest_vol="VOL_HIGH",
        candles=candles,
    )
    # Result stores "None" as a string for CSV compatibility
    assert result["live_vol_mean"] == "None"
    assert result["backtest_vol_mean"] == "None"
    assert result["category"] == "unknown"


def test_analyze_pair_categorization_includes_required_keys():
    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    round_start = start + timedelta(hours=4)
    candles = [make_candle(start, "50000", "50000")] * 50
    result = analyze_pair(
        market_slug="test-slug",
        round_start=round_start,
        live_vol="VOL_NORMAL", backtest_vol="VOL_HIGH",
        candles=candles,
    )
    for key in ("market_slug", "round_start_utc", "live_vol_bucket",
                "backtest_vol_bucket", "live_vol_mean", "backtest_vol_mean",
                "vol_mean_diff", "category"):
        assert key in result


def test_categories_constant_complete():
    for cat in ("edge_threshold", "candle_selection", "identical", "unknown"):
        assert cat in CATEGORIES
