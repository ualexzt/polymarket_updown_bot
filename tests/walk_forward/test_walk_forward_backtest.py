"""Tests for walk_forward_backtest: data loading, fold partitioning, no-lookahead, settlement."""
from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import Candle

from scripts.walk_forward_backtest import (
    load_candles_csv,
    partition_folds,
    build_rule_index,
    run_pipeline,
)


def _write_candles_csv(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time_utc", "open", "high", "low", "close", "volume", "is_closed", "close_time_utc"])
        for c in candles:
            w.writerow([
                c.open_time_utc.isoformat(), str(c.open), str(c.high), str(c.low),
                str(c.close), str(c.volume), "True",
                (c.open_time_utc + timedelta(minutes=5)).isoformat(),
            ])


def test_load_candles_csv_round_trip(tmp_path: Path, candle_factory):
    candles = [candle_factory(datetime(2026, 1, 1, 0, 0, tzinfo=UTC) + timedelta(minutes=5 * i), "50000") for i in range(3)]
    p = tmp_path / "c.csv"
    _write_candles_csv(p, candles)
    loaded = load_candles_csv(p)
    assert len(loaded) == 3
    assert loaded[0].open == Decimal("50000")
    assert loaded[2].open_time_utc == datetime(2026, 1, 1, 0, 10, tzinfo=UTC)


def test_partition_folds_non_overlapping():
    data_start = datetime(2026, 1, 1, tzinfo=UTC)
    data_end = datetime(2026, 4, 11, tzinfo=UTC)  # 100 days
    folds = partition_folds(
        data_start=data_start,
        data_end=data_end,
        n_folds=5,
        test_days=20,
    )
    assert len(folds) == 5
    # Verify non-overlap
    for i in range(len(folds) - 1):
        assert folds[i].test_end <= folds[i + 1].test_start
    # Verify cover the data range
    assert folds[0].test_start >= data_start
    assert folds[-1].test_end <= data_end


def test_partition_folds_default_train_window_is_remainder():
    data_start = datetime(2026, 1, 1, tzinfo=UTC)
    data_end = datetime(2026, 4, 11, tzinfo=UTC)
    folds = partition_folds(
        data_start=data_start, data_end=data_end, n_folds=3, test_days=20,
    )
    # Each fold's train_start = data_start (cumulative), train_end = test_start
    for f in folds:
        assert f.train_start == data_start
        assert f.train_end == f.test_start


def test_build_rule_index_finds_exact_match(sample_rules):
    index = build_rule_index(sample_rules)
    rule, match_type = index.lookup(
        stage="AFTER_10M",
        current_side="BELOW_OPEN",
        distance_bucket="D_0_005pct",
        volatility_bucket="VOL_LOW",
        pattern="strong_bull_close_near_high -> normal_bull",
    )
    assert rule is not None
    assert rule.samples == 120
    assert match_type.value == "exact"


def test_run_pipeline_with_explicit_window(synthetic_5d_candles, sample_rules, tmp_path, tmp_results_dir):
    """When test_start/test_end are provided, the script builds a single fold with those boundaries."""
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps([
        {
            "rule_id": r.rule_id,
            "stage": r.stage.value,
            "current_side": r.current_side.value,
            "distance_bucket": r.distance_bucket.value,
            "volatility_bucket": r.volatility_bucket.value,
            "pattern": r.pattern,
            "recommended_side": r.recommended_side.value,
            "historical_probability": str(r.historical_probability),
            "samples": r.samples,
            "median_round_return": str(r.median_round_return),
            "return_aligned": r.return_aligned,
            "usable_signal": r.usable_signal,
        }
        for r in sample_rules
    ]))
    data_csv = tmp_path / "candles.csv"
    _write_candles_csv(data_csv, synthetic_5d_candles)

    summary = run_pipeline(
        data_csv=data_csv,
        rules_json=rules_path,
        out_dir=tmp_results_dir,
        n_folds=5, test_days=30,  # ignored when explicit window is set
        test_start=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        test_end=datetime(2026, 6, 3, 0, 0, tzinfo=UTC),
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
        safety_buffer=Decimal("0.05"),
        max_entry_ask=Decimal("0.80"),
    )
    assert len(summary["folds"]) == 1
    assert summary["folds"][0]["test_start"].startswith("2026-06-01")
    assert summary["folds"][0]["test_end"].startswith("2026-06-03")
