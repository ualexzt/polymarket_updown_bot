"""Tests for run_pipeline: end-to-end on a tiny synthetic dataset."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import Candle

from scripts.walk_forward_backtest import run_pipeline, _write_candles_csv_for_test


def _write_candles(path: Path, candles: list[Candle]) -> None:
    _write_candles_csv_for_test(path, candles)


def test_run_pipeline_writes_outputs(synthetic_5d_candles, sample_rules, tmp_path, tmp_results_dir):
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
    _write_candles(data_csv, synthetic_5d_candles)

    summary = run_pipeline(
        data_csv=data_csv,
        rules_json=rules_path,
        out_dir=tmp_results_dir,
        n_folds=2,
        test_days=2,
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
        safety_buffer=Decimal("0.05"),
        max_entry_ask=Decimal("0.80"),
    )
    assert (tmp_results_dir / "wf_aggregate_summary.json").exists()
    assert "folds" in summary
    assert len(summary["folds"]) >= 1
    for fold_summary in summary["folds"]:
        assert "fold_id" in fold_summary
        assert "n_rounds" in fold_summary
        assert "wr" in fold_summary
