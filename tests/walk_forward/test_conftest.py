"""Sanity check: fixtures produce valid objects and the synthetic dataset spans 5 days."""
from datetime import UTC, datetime, timedelta


def test_synthetic_5d_candles_count_and_range(synthetic_5d_candles):
    assert len(synthetic_5d_candles) == 1441
    assert synthetic_5d_candles[0].open_time_utc == datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    assert synthetic_5d_candles[-1].open_time_utc == datetime(2026, 6, 6, 0, 0, tzinfo=UTC)
    # monotonic
    for prev, curr in zip(synthetic_5d_candles, synthetic_5d_candles[1:]):
        assert curr.open_time_utc == prev.open_time_utc + timedelta(minutes=5)


def test_synthetic_market_is_15m(synthetic_market):
    duration = synthetic_market.end_ts - synthetic_market.start_ts
    assert duration == timedelta(minutes=15)


def test_sample_rules_have_distinct_stages(sample_rules):
    stages = {r.stage for r in sample_rules}
    assert len(stages) == 2  # AFTER_5M and AFTER_10M


def test_sample_rules_include_low_samples(sample_rules):
    low_sample = [r for r in sample_rules if r.samples < 60]
    assert len(low_sample) == 1
    assert low_sample[0].return_aligned is False
