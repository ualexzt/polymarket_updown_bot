"""Shared fixtures for walk-forward backtest tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import (
    BinanceState,
    Candle,
    CurrentSide,
    DistanceBucket,
    MarketMetadata,
    ProbabilityRule,
    Side,
    Stage,
    VolatilityBucket,
)


def make_candle(
    open_time: datetime,
    open: str,
    high: str | None = None,
    low: str | None = None,
    close: str | None = None,
    volume: str = "10",
) -> Candle:
    """Create a Candle with sensible defaults for OHLC."""
    o = Decimal(open)
    c = Decimal(close if close is not None else open)
    h = Decimal(high if high is not None else open)
    l = Decimal(low if low is not None else open)
    return Candle(
        open_time_utc=open_time if open_time.tzinfo else open_time.replace(tzinfo=UTC),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal(volume),
        is_closed=True,
    )


@pytest.fixture
def candle_factory():
    return make_candle


@pytest.fixture
def synthetic_5d_candles() -> list[Candle]:
    """5 days × 288 5m candles = 1440 candles, constant price 50000.

    Spans 2026-06-01 00:00 UTC to 2026-06-06 00:00 UTC.
    """
    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    candles = []
    for i in range(1441):
        candles.append(make_candle(start + timedelta(minutes=5 * i), "50000"))
    return candles


@pytest.fixture
def synthetic_market() -> MarketMetadata:
    """15m market starting at 2026-06-06 00:00 UTC."""
    return MarketMetadata(
        market_id="test",
        condition_id="test",
        question="test",
        slug="btc-updown-15m-1781520000",
        up_token_id="up",
        down_token_id="down",
        outcomes=["Up", "Down"],
        start_ts=datetime(2026, 6, 6, 0, 0, tzinfo=UTC),
        end_ts=datetime(2026, 6, 6, 0, 15, tzinfo=UTC),
        active=True,
        closed=False,
        accepting_orders=True,
    )


@pytest.fixture
def sample_rules() -> list[ProbabilityRule]:
    """3 hand-crafted rules covering distinct stages."""
    return [
        ProbabilityRule(
            rule_id="btc_15m_after_10m_below_open_d_0_005pct_vol_low_strong_bull_close_near_high",
            stage=Stage.AFTER_10M,
            current_side=CurrentSide.BELOW_OPEN,
            distance_bucket=DistanceBucket.D_0_005pct,
            volatility_bucket=VolatilityBucket.VOL_LOW,
            pattern="strong_bull_close_near_high -> normal_bull",
            recommended_side=Side.UP,
            historical_probability=Decimal("0.65"),
            samples=120,
            median_round_return=Decimal("0.001"),
            return_aligned=True,
            usable_signal=True,
        ),
        ProbabilityRule(
            rule_id="btc_15m_after_5m_above_open_d_005_010pct_vol_normal_normal_bear",
            stage=Stage.AFTER_5M,
            current_side=CurrentSide.ABOVE_OPEN,
            distance_bucket=DistanceBucket.D_005_010pct,
            volatility_bucket=VolatilityBucket.VOL_NORMAL,
            pattern="normal_bear",
            recommended_side=Side.DOWN,
            historical_probability=Decimal("0.55"),
            samples=80,
            median_round_return=Decimal("-0.001"),
            return_aligned=True,
            usable_signal=True,
        ),
        ProbabilityRule(
            rule_id="btc_15m_after_10m_below_open_d_0_005pct_vol_low_weak_bear",
            stage=Stage.AFTER_10M,
            current_side=CurrentSide.BELOW_OPEN,
            distance_bucket=DistanceBucket.D_0_005pct,
            volatility_bucket=VolatilityBucket.VOL_LOW,
            pattern="weak_bear -> flat",
            recommended_side=Side.DOWN,
            historical_probability=Decimal("0.45"),
            samples=30,  # below MIN_SAMPLES, must be filtered out
            median_round_return=Decimal("0.0005"),
            return_aligned=False,
            usable_signal=True,
        ),
    ]


@pytest.fixture
def tmp_results_dir(tmp_path: Path) -> Path:
    """Temporary directory for backtest outputs."""
    d = tmp_path / "results"
    d.mkdir()
    return d


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d
