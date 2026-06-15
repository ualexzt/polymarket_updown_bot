"""Walk-forward backtest: replay historical Binance 5m candles through live rule-lookup.

Usage:
  python scripts/walk_forward_backtest.py --data data/btc_5m_500d.csv \\
    --rules config/btc_updown_state_rules_15m.json --out-dir results/
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_round_bot.models import Candle  # noqa: E402


# === Data loading ===

def load_candles_csv(path: Path) -> list[Candle]:
    """Load candles from the CSV written by fetch_binance_history.py."""
    candles: list[Candle] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append(Candle(
                open_time_utc=datetime.fromisoformat(row["open_time_utc"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                is_closed=True,
            ))
    candles.sort(key=lambda c: c.open_time_utc)
    return candles


# === Fold partitioning ===

@dataclass(frozen=True)
class Fold:
    fold_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def partition_folds(
    *,
    data_start: datetime,
    data_end: datetime,
    n_folds: int,
    test_days: int,
) -> list[Fold]:
    """Partition [data_start, data_end] into n_folds rolling test windows.

    Test windows are contiguous (not rolling): test_0 = [data_start, data_start+test_days),
    test_1 = [data_start+test_days, data_start+2*test_days), etc. Each fold's train window
    is [data_start, test_start] (cumulative).
    """
    folds: list[Fold] = []
    for i in range(n_folds):
        test_start = data_start + timedelta(days=i * test_days)
        test_end = test_start + timedelta(days=test_days)
        if test_end > data_end:
            test_end = data_end
        folds.append(Fold(
            fold_id=i,
            train_start=data_start,
            train_end=test_start,
            test_start=test_start,
            test_end=test_end,
        ))
        if test_end == data_end:
            break
    return folds


# === Rule index (delegates to live probability_rules) ===

class _LiveRuleIndexAdapter:
    """Adapter exposing the same .lookup(...) signature as the test's
    build_rule_index return value, backed by live ProbabilityRules.
    """

    def __init__(self, rules_index):  # noqa: ANN001 - duck-typed
        self._index = rules_index

    def lookup(self, *, stage, current_side, distance_bucket, volatility_bucket, pattern):  # noqa: ANN001
        from polymarket_round_bot.probability_rules import (  # noqa: PLC0415
            CurrentSide as CS, DistanceBucket as DB, Stage as ST, VolatilityBucket as VB,
        )
        rule, match_type = self._index._index.lookup(  # noqa: SLF001 - test adapter
            stage=ST(stage), current_side=CS(current_side),
            distance_bucket=DB(distance_bucket), volatility_bucket=VB(volatility_bucket),
            pattern=pattern,
        )
        return rule, match_type


def build_rule_index(rules):  # noqa: ANN001
    """Wrap a list[ProbabilityRule] into an object with .lookup(stage, current_side, distance_bucket, volatility_bucket, pattern) -> (rule, match_type)."""
    from polymarket_round_bot.probability_rules import ProbabilityRules  # noqa: PLC0415
    pr = ProbabilityRules(rules)
    return _LiveRuleIndexAdapter(pr)


# === CLI (placeholder; full simulation in Task 4-6) ===

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--rules", required=True)
    p.add_argument("--out-dir", default="results/")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--test-days", type=int, default=30)
    args = p.parse_args()
    print(f"[stub] would load {args.data} and {args.rules}, write to {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
