"""Render vol-mean investigation to a markdown report.

Usage:
  python scripts/investigation_report.py \\
    --in-dir results/investigation/ \\
    --out docs/analysis/2026-06-15-vol-mean-investigation.md
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_SECTIONS: tuple[str, ...] = (
    "# Vol-Mean Investigation Report",
    "## TL;DR",
    "## Setup",
    "## Per-pair analysis",
    "## Categorization",
    "## Edge cases",
    "## Recommendation",
    "## Appendix",
)


_RECOMMENDATIONS: dict[str, str] = {
    "edge_threshold": (
        "**Fix the bucket thresholds.** Pairs in this category have vol_mean within 1e-5 of a "
        "threshold. Bumping the threshold by 1e-5 absorbs them. Lowest-risk fix."
    ),
    "candle_selection": (
        "**Align candle selection between live and backtest.** Different candle sets produce "
        "different vol_mean values. Fix: have the backtest match live's 60-candle window."
    ),
    "identical": (
        "**The vol_mean is identical but buckets differ — this is the smoking gun.** Both "
        "perspectives return the same vol_mean, but the resulting vol_bucket is different. "
        "This is impossible if both use the same `_classify_volatility` function. Likely cause: "
        "the live server is running pre-fix code (commit 4e9a989) that uses 5m close-to-close "
        "returns instead of 16 prior 15m rounds. Verify by checking the running container's "
        "round_state.py against the local version; redeploy if needed."
    ),
    "unknown": (
        "**Insufficient data or unexpected condition.** Either vol_mean is None, or the diff "
        "is in an intermediate range. Investigate individually."
    ),
}


def categorization_table_markdown(categorization: dict[str, Any]) -> str:
    counts = categorization["counts_by_category"]
    total = sum(counts.values())
    lines = [
        "| Category | Count | % |",
        "|---|---:|---:|",
    ]
    for cat in ("edge_threshold", "candle_selection", "identical", "unknown"):
        c = counts.get(cat, 0)
        pct = (c / total * 100) if total else 0
        lines.append(f"| {cat} | {c} | {pct:.1f}% |")
    lines.append(f"| **Total** | **{total}** | 100.0% |")
    return "\n".join(lines)


def render_report(
    *,
    categorization: dict[str, Any],
    per_pair: list[dict[str, Any]],
    out_path: Path,
) -> None:
    counts = categorization["counts_by_category"]
    total = sum(counts.values())
    dominant = max(counts, key=counts.get) if counts else "unknown"
    threshold_text = (
        f"Thresholds: VOL_LOW_MAX={categorization['thresholds']['VOL_LOW_MAX']}, "
        f"VOL_NORMAL_MAX={categorization['thresholds']['VOL_NORMAL_MAX']}"
    )

    tl_dr = (
        f"Across {total} vol-bucket mismatches, the dominant category is **{dominant}** "
        f"({counts.get(dominant, 0)} / {total} = "
        f"{counts.get(dominant, 0) / total * 100 if total else 0:.1f}%).\n\n"
        f"{_RECOMMENDATIONS.get(dominant, '')}"
    )

    setup = (
        f"- **Inputs**: vol-bucket mismatches from `results/recon/matched_pairs.csv`\n"
        f"- **Candle source**: `data/btc_5m_500d.csv` (same CSV the backtest used)\n"
        f"- **Live perspective**: 60 most recent closed candles before `round_start_ts`\n"
        f"- **Backtest perspective**: 200 most recent closed candles before `round_start_ts`\n"
        f"- **vol_mean function**: production `_compute_prev_volatility_mean` from `round_state.py`\n"
        f"- **{threshold_text}**\n\n"
        f"**Critical methodological note**: Both perspectives call the same function with the "
        f"same candle source. If vol_mean comes out identical, the bucket classification must also "
        f"be identical — UNLESS the live server is running a different function than this local code."
    )

    if per_pair:
        per_pair_md = (
            f"See `results/investigation/vol_mean_per_pair.csv` for the full table "
            f"({len(per_pair)} rows).\n\n"
            "**First 5 rows (preview):**\n\n"
            "| market_slug | round_start | live_vol | backtest_vol | live_mean | backtest_mean | diff | category |\n"
            "|---|---|---|---|---|---|---|---|\n"
        )
        for r in per_pair[:5]:
            per_pair_md += (
                f"| {r['market_slug']} | {r['round_start_utc']} | "
                f"{r['live_vol_bucket']} | {r['backtest_vol_bucket']} | "
                f"{r['live_vol_mean']} | {r['backtest_vol_mean']} | "
                f"{r['vol_mean_diff']} | {r['category']} |\n"
            )
    else:
        per_pair_md = "No per-pair data available."

    cat_table = categorization_table_markdown(categorization)

    edge = [r for r in per_pair if r["category"] == "edge_threshold"]
    if edge:
        edge_md = (
            f"**{len(edge)} pairs have vol_mean within 1e-5 of a threshold.** "
            f"See `results/investigation/edge_case_summary.txt` for the full list."
        )
    else:
        edge_md = "No edge cases found in this run."

    rec_md = (
        f"**Primary action: {dominant}**\n\n"
        f"{_RECOMMENDATIONS.get(dominant, '')}\n\n"
        f"**Secondary actions** (if dominant category is not enough to close the gap):\n\n"
    )
    for cat in ("edge_threshold", "candle_selection", "identical", "unknown"):
        if cat != dominant and counts.get(cat, 0) > 0:
            rec_md += f"- **{cat}** ({counts[cat]}): {_RECOMMENDATIONS[cat].split('.')[0]}.\n"

    appendix = (
        "**Methodology**:\n"
        "- For each vol-bucket mismatch, parse `round_start_ts` from the market slug.\n"
        "- Filter candles with `open_time_utc < round_start_ts`.\n"
        "- Take the 60 most recent (live perspective) and 200 most recent (backtest perspective).\n"
        "- Call `_compute_prev_volatility_mean` on each set, capturing both vol_mean values.\n"
        "- Categorize based on diff, threshold proximity, and edge cases.\n\n"
        "**Limitations**:\n"
        "- Both perspectives use the same 500d candle CSV. The only difference is the "
        "selection window (60 vs 200). If the live candle feed diverges from this CSV, "
        "the analysis may miss that cause.\n"
        "- vol_mean numerical precision is bounded by Decimal; diffs < 1e-12 are not "
        "meaningful and may be reported as 'identical'.\n"
        "- The 1e-5 edge threshold is heuristic; the actual noise floor may be different.\n\n"
        "**Verification step**: For 'identical' cases, manually compute `live_vol_mean` with the "
        "5m close-to-close logic (the pre-fix volatility function) on the same 60 candles. "
        "If the result is close to `_VOL_LOW_MAX` or `_VOL_NORMAL_MAX`, this confirms the live "
        "server is using the older logic."
    )

    report = f"""# Vol-Mean Investigation Report

**Generated**: 2026-06-15

## TL;DR

{tl_dr}

## Setup

{setup}

## Per-pair analysis

{per_pair_md}

## Categorization

{cat_table}

## Edge cases

{edge_md}

## Recommendation

{rec_md}

## Appendix

{appendix}
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in-dir", default="results/investigation/")
    p.add_argument("--out", default="docs/analysis/2026-06-15-vol-mean-investigation.md")
    args = p.parse_args()

    in_dir = Path(args.in_dir)
    cat_path = in_dir / "mismatch_categorization.json"
    if not cat_path.exists():
        print(f"missing {cat_path}", file=sys.stderr)
        return 1
    categorization = json.loads(cat_path.read_text())

    per_pair: list[dict[str, Any]] = []
    per_pair_path = in_dir / "vol_mean_per_pair.csv"
    if per_pair_path.exists() and per_pair_path.stat().st_size > 0:
        with per_pair_path.open() as f:
            per_pair = list(csv.DictReader(f))

    render_report(categorization=categorization, per_pair=per_pair, out_path=Path(args.out))
    print(f"OK: report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
