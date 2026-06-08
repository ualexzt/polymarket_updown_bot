"""Convert research CSV (state-bucket report) to JSON rules file.

Source:
  /home/alex/Project/poly_bot_system/out_rounds_v2/BTCUSDT_5m_180d_state_bucket_report.csv

Output:
  config/btc_updown_state_rules_15m.json

The CSV has 2014 rules. We KEEP ALL ROWS (including return_aligned=False
and usable_signal=False) so the rules layer can apply filters. The
ProbabilityRules class + signal engine will skip rules that fail the
no-trade conditions.

Rule ID format: btc_15m_{stage}_{side}_{distance}_{vol}_{pattern}
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_round_bot.models import (
    Asset,
    CurrentSide,
    DistanceBucket,
    ProbabilityRule,
    Side,
    Stage,
    Timeframe,
    VolatilityBucket,
)
from polymarket_round_bot.probability_rules import build_rule_id


def _parse_decimal(s: str) -> Decimal:
    return Decimal(str(s))


def _build_rule_from_row(row: dict[str, str], *, timeframe: Timeframe) -> ProbabilityRule:
    stage = Stage(row["stage"])
    current_side = CurrentSide(row["current_side"])
    distance_bucket = DistanceBucket(row["distance_bucket"])
    volatility_bucket = VolatilityBucket(row["volatility_bucket"])
    pattern = row["pattern"]
    recommended_side = Side(row["recommended_side"])
    historical_probability = _parse_decimal(row["historical_probability"])
    samples = int(row["samples"])
    median_round_return = _parse_decimal(row["median_round_return"])
    return_aligned = row["return_aligned"] == "True"
    usable_signal = row["usable_signal"] == "True"

    rule_id = build_rule_id(
        asset=Asset.BTC.value,
        timeframe=timeframe.value,
        stage=stage,
        current_side=current_side,
        distance_bucket=distance_bucket,
        volatility_bucket=volatility_bucket,
        pattern=pattern,
    )
    return ProbabilityRule(
        rule_id=rule_id,
        stage=stage,
        current_side=current_side,
        distance_bucket=distance_bucket,
        volatility_bucket=volatility_bucket,
        pattern=pattern,
        recommended_side=recommended_side,
        historical_probability=historical_probability,
        samples=samples,
        median_round_return=median_round_return,
        return_aligned=return_aligned,
        usable_signal=usable_signal,
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--csv",
        default=(
            "/home/alex/Project/poly_bot_system/out_rounds_v2/"
            "BTCUSDT_5m_180d_state_bucket_report.csv"
        ),
    )
    p.add_argument(
        "--out",
        default="config/btc_updown_state_rules_15m.json",
    )
    p.add_argument("--timeframe", default=Timeframe.M15.value)
    args = p.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 1
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tf = Timeframe(args.timeframe)
    rules: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            r = _build_rule_from_row(row, timeframe=tf)
            # Serialize Decimal as string for JSON readability
            rules.append(
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
            )

    out_path.write_text(json.dumps(rules, indent=2), encoding="utf-8")
    print(f"wrote {len(rules)} rules to {out_path}")
    # Summary
    usable = sum(1 for r in rules if r["usable_signal"] and r["return_aligned"])
    strong = sum(
        1
        for r in rules
        if r["usable_signal"]
        and r["return_aligned"]
        and r["samples"] >= 60
        and Decimal(r["historical_probability"]) >= Decimal("0.60")
    )
    print(f"  usable + return_aligned: {usable}")
    print(f"  strong (>=60 samples, >=0.60 prob): {strong}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
