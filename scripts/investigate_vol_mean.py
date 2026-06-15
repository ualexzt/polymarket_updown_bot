"""Vol-mean investigation: per-pair analysis and categorization of vol bucket mismatches.

Usage:
  python scripts/investigate_vol_mean.py \\
    --live-db data/live_paper.sqlite \\
    --matched-pairs results/recon/matched_pairs.csv \\
    --candles-csv data/btc_5m_500d.csv \\
    --out-dir results/investigation/
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_round_bot.round_state import (  # noqa: E402
    _VOL_LOW_MAX,
    _VOL_NORMAL_MAX,
    _compute_prev_volatility_mean,
)
from scripts.walk_forward_backtest import load_candles_csv  # noqa: E402


CATEGORIES: tuple[str, ...] = (
    "edge_threshold",
    "candle_selection",
    "identical",
    "unknown",
)

# Live uses 60 candles; backtest uses 200. The 16-round window needs 48 candles minimum.
LIVE_CANDLE_LIMIT: int = 60
BACKTEST_CANDLE_LIMIT: int = 200
EDGE_TOLERANCE: Decimal = Decimal("0.00001")  # 1e-5
MEAN_DIFF_THRESHOLD: Decimal = Decimal("0.0001")  # 1e-4


def parse_round_start_from_slug(slug: str) -> datetime:
    """Parse round_start_ts from market_slug like 'btc-updown-15m-1781526600'."""
    ts = int(slug.split("-")[-1])
    return datetime.fromtimestamp(ts, tz=UTC)


def load_mismatched_pairs(matched_pairs_csv: Path) -> list[dict[str, str]]:
    """Load only pairs where live_vol_bucket != backtest_vol_bucket."""
    rows: list[dict[str, str]] = []
    with matched_pairs_csv.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("vol_match") == "DIFF":
                rows.append(r)
    return rows


def analyze_pair(
    *,
    market_slug: str,
    round_start: datetime,
    live_vol: str,
    backtest_vol: str,
    candles: list,
) -> dict[str, Any]:
    """Compute live and backtest vol_mean, then categorize the mismatch cause."""
    closed = sorted(
        [c for c in candles if c.open_time_utc < round_start],
        key=lambda c: c.open_time_utc,
    )
    live_candles = closed[-LIVE_CANDLE_LIMIT:]
    backtest_candles = closed[-BACKTEST_CANDLE_LIMIT:]

    live_vol_mean = _compute_prev_volatility_mean(live_candles, round_start_ts=round_start)
    backtest_vol_mean = _compute_prev_volatility_mean(backtest_candles, round_start_ts=round_start)

    if live_vol_mean is not None and backtest_vol_mean is not None:
        diff = abs(live_vol_mean - backtest_vol_mean)
    else:
        diff = None

    if live_vol_mean is None or backtest_vol_mean is None:
        category = "unknown"
    elif (
        abs(live_vol_mean - _VOL_LOW_MAX) < EDGE_TOLERANCE
        or abs(live_vol_mean - _VOL_NORMAL_MAX) < EDGE_TOLERANCE
        or (backtest_vol_mean is not None and (
            abs(backtest_vol_mean - _VOL_LOW_MAX) < EDGE_TOLERANCE
            or abs(backtest_vol_mean - _VOL_NORMAL_MAX) < EDGE_TOLERANCE
        ))
    ):
        category = "edge_threshold"
    elif diff is not None and diff > MEAN_DIFF_THRESHOLD:
        category = "candle_selection"
    elif diff is not None and diff < Decimal("0.000001"):
        category = "identical"
    else:
        category = "unknown"

    return {
        "market_slug": market_slug,
        "round_start_utc": round_start.isoformat(),
        "live_vol_bucket": live_vol,
        "backtest_vol_bucket": backtest_vol,
        "live_vol_mean": str(live_vol_mean) if live_vol_mean is not None else "None",
        "backtest_vol_mean": str(backtest_vol_mean) if backtest_vol_mean is not None else "None",
        "vol_mean_diff": str(diff) if diff is not None else "N/A",
        "category": category,
    }


# === Output writers ===

def write_per_pair_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_categorization_json(rows: list[dict[str, Any]], out_path: Path) -> None:
    counts: dict[str, int] = {cat: 0 for cat in CATEGORIES}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    summary = {
        "n_pairs_analyzed": len(rows),
        "counts_by_category": counts,
        "thresholds": {
            "VOL_LOW_MAX": str(_VOL_LOW_MAX),
            "VOL_NORMAL_MAX": str(_VOL_NORMAL_MAX),
        },
    }
    out_path.write_text(json.dumps(summary, indent=2))


def write_edge_case_summary(rows: list[dict[str, Any]], out_path: Path) -> None:
    edge = [r for r in rows if r["category"] == "edge_threshold"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Edge-case pairs: vol_mean within 1e-5 of a threshold",
        f"# Total: {len(edge)}",
        f"# Thresholds: VOL_LOW_MAX={_VOL_LOW_MAX}, VOL_NORMAL_MAX={_VOL_NORMAL_MAX}",
        "",
    ]
    for r in edge:
        lines.append(
            f"{r['market_slug']}  round_start={r['round_start_utc']}  "
            f"live_vol={r['live_vol_bucket']}  backtest_vol={r['backtest_vol_bucket']}  "
            f"vol_mean={r['live_vol_mean']}"
        )
    out_path.write_text("\n".join(lines))


# === CLI ===

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live-db", default="data/live_paper.sqlite")
    p.add_argument("--matched-pairs", default="results/recon/matched_pairs.csv")
    p.add_argument("--candles-csv", default="data/btc_5m_500d.csv")
    p.add_argument("--out-dir", default="results/investigation/")
    args = p.parse_args()

    print(f"Loading candles from {args.candles_csv}...", file=sys.stderr)
    candles = load_candles_csv(Path(args.candles_csv))
    print(f"  loaded {len(candles)} candles", file=sys.stderr)

    print(f"Loading mismatched pairs from {args.matched_pairs}...", file=sys.stderr)
    mismatched = load_mismatched_pairs(Path(args.matched_pairs))
    print(f"  found {len(mismatched)} vol-mismatched pairs", file=sys.stderr)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for i, r in enumerate(mismatched):
        slug = r["market_slug"]
        try:
            round_start = parse_round_start_from_slug(slug)
        except (ValueError, IndexError):
            continue
        result = analyze_pair(
            market_slug=slug,
            round_start=round_start,
            live_vol=r["live_vol_bucket"],
            backtest_vol=r["backtest_vol_bucket"],
            candles=candles,
        )
        rows.append(result)
        if (i + 1) % 20 == 0:
            print(f"  processed {i + 1}/{len(mismatched)}", file=sys.stderr)

    write_per_pair_csv(rows, out_dir / "vol_mean_per_pair.csv")
    write_categorization_json(rows, out_dir / "mismatch_categorization.json")
    write_edge_case_summary(rows, out_dir / "edge_case_summary.txt")

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    print("\n=== Categorization summary ===", file=sys.stderr)
    for cat in CATEGORIES:
        print(f"  {cat}: {counts.get(cat, 0)}", file=sys.stderr)
    print(f"\nOK: outputs written to {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
