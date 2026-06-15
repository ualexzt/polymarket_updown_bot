"""Render reconciliation results to a markdown report.

Usage:
  python scripts/reconciliation_report.py \\
    --in-dir results/recon/ \\
    --out docs/analysis/2026-06-15-reconciliation.md
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
    "# Reconciliation Report",
    "## TL;DR",
    "## Setup",
    "## Matched pairs analysis",
    "## Unmatched analysis",
    "## Verdict & recommendation",
    "## Appendix",
)


_VERDICT_LABELS: dict[str, str] = {
    "A": "Live-vs-backtest filter gap (spread, liquidity, or backtest too permissive)",
    "B": "State-construction mismatch (volatility, distance bucket, AT_OPEN, pattern)",
    "C": "Settlement timing or tie handling divergence",
    "D": "Insufficient data (< 5 matched pairs)",
}


def summary_table_markdown(summary: dict[str, Any]) -> str:
    return (
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| Verdict | **{summary['verdict']}** |\n"
        f"| Matched pairs | {summary['n_matched']} |\n"
        f"| Live-only | {summary['n_live_only']} |\n"
        f"| Backtest-only | {summary['n_backtest_only']} |\n"
        f"| State mismatches | {summary['mismatch_counts']['state']} |\n"
        f"| Price mismatches | {summary['mismatch_counts']['price']} |\n"
        f"| Settlement mismatches | {summary['mismatch_counts']['settlement']} |\n"
        f"| Total mismatches | {summary['mismatch_counts']['total']} |\n"
    )


def render_report(
    *,
    summary: dict[str, Any],
    matched_pairs: list[dict[str, Any]],
    live_only: list[dict[str, Any]],
    backtest_only: list[dict[str, Any]],
    out_path: Path,
) -> None:
    verdict_label = _VERDICT_LABELS.get(summary["verdict"], "Unknown")

    tl_dr = (
        f"**Verdict: {summary['verdict']} — {verdict_label}.**\n\n"
        f"{summary['recommendation']}"
    )

    setup = (
        f"- **Live DB**: snapshot from server at reconciliation time\n"
        f"- **Backtest**: same live period (2026-06-06 → 2026-06-15), replayed through "
        f"`walk_forward_backtest.py` with live rules from `config/btc_updown_state_rules_15m.json`\n"
        f"- **Match key**: `market_slug`\n"
        f"- **Field comparison**: state (5 fields), price (2 fields), settlement (3 fields)"
    )

    matched_table = (
        f"See `results/recon/matched_pairs.csv` for the full side-by-side comparison.\n\n"
        + summary_table_markdown(summary)
    )

    unmatched_md = (
        f"- **Live-only trades** (no backtest match): {summary['n_live_only']}. "
        f"See `results/recon/live_only_trades.csv`.\n"
        f"- **Backtest-only trades** (no live match): {summary['n_backtest_only']}. "
        f"See `results/recon/backtest_only_trades.csv`."
    )
    if summary["n_live_only"] > 0 and summary["n_live_only"] <= 20:
        unmatched_md += "\n\n**Live-only trades (full list):**\n\n"
        unmatched_md += "| market_slug | resolved_at_utc | won | pnl | entry_price | rule_id |\n"
        unmatched_md += "|---|---|---|---|---|---|\n"
        for t in live_only[:20]:
            unresolved = t.get("resolved_at_utc", "")
            ts = unresolved[:19] if unresolved else ""
            unmatched_md += (
                f"| {t.get('market_slug', '?')} | {ts} | {t.get('won', '?')} | "
                f"{t.get('realized_pnl_usd', '?')} | {t.get('entry_price', '?')} | "
                f"{t.get('rule_id', '?')} |\n"
            )
    if summary["n_backtest_only"] > 0 and summary["n_backtest_only"] <= 20:
        unmatched_md += "\n\n**Backtest-only trades (first 20):**\n\n"
        unmatched_md += "| market_slug | won | pnl | entry_price | stage |\n"
        unmatched_md += "|---|---|---|---|---|\n"
        for t in backtest_only[:20]:
            unmatched_md += (
                f"| {t.get('market_slug', '?')} | {t.get('won', '?')} | "
                f"{t.get('pnl', '?')} | {t.get('entry_price', '?')} | "
                f"{t.get('stage', '?')} |\n"
            )

    verdict_md = (
        f"**{summary['verdict']} — {verdict_label}**\n\n"
        f"{summary['recommendation']}"
    )

    appendix = (
        "**Methodology**:\n"
        "- Live DB snapshot: scp from server, query settlements + paper_positions tables.\n"
        "- Backtest: `walk_forward_backtest.py` re-run on the live period with explicit "
        "`--test-start` / `--test-end` flags (added in this iteration).\n"
        "- Match by `market_slug`. 1:1 match expected (MAX_OPEN_POSITIONS=1 invariant).\n"
        "- Field comparison with entry tolerance = 0.01. State fields use exact string match.\n"
        "- Verdict logic in `categorize_verdict()`: A (filter) / B (state) / C (settlement) / D (insufficient data).\n\n"
        "**Limitations**:\n"
        "- Small live sample (only 7-9 days, 256 settlements). Statistical confidence is low.\n"
        "- The match depends on `market_slug` being identical between live and backtest. "
        "If live slugs differ from backtest slugs (e.g., different slug generation logic), "
        "matches would be missed.\n"
        "- `backtest-only` trades are computed from a backtest that has no concept of "
        "`MAX_OPEN_POSITIONS` over time (it processes one round at a time), so a backtest trade "
        "may have a live counterpart that was rejected by the risk manager on a different "
        "round that day."
    )

    report = f"""# Reconciliation Report

**Generated**: 2026-06-15

## TL;DR

{tl_dr}

## Setup

{setup}

## Matched pairs analysis

{matched_table}

## Unmatched analysis

{unmatched_md}

## Verdict & recommendation

{verdict_md}

## Appendix

{appendix}
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in-dir", default="results/recon/")
    p.add_argument("--out", default="docs/analysis/2026-06-15-reconciliation.md")
    args = p.parse_args()

    in_dir = Path(args.in_dir)
    summary_path = in_dir / "reconciliation_summary.json"
    if not summary_path.exists():
        print(f"missing {summary_path}", file=sys.stderr)
        return 1
    summary = json.loads(summary_path.read_text())

    matched = []
    matched_path = in_dir / "matched_pairs.csv"
    if matched_path.exists() and matched_path.stat().st_size > 0:
        with matched_path.open() as f:
            matched = list(csv.DictReader(f))

    live_only = []
    live_only_path = in_dir / "live_only_trades.csv"
    if live_only_path.exists() and live_only_path.stat().st_size > 0:
        with live_only_path.open() as f:
            live_only = list(csv.DictReader(f))

    backtest_only = []
    backtest_only_path = in_dir / "backtest_only_trades.csv"
    if backtest_only_path.exists() and backtest_only_path.stat().st_size > 0:
        with backtest_only_path.open() as f:
            backtest_only = list(csv.DictReader(f))

    render_report(
        summary=summary, matched_pairs=matched, live_only=live_only,
        backtest_only=backtest_only, out_path=Path(args.out),
    )
    print(f"OK: report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
