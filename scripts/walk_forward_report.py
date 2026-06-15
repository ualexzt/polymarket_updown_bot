"""Render walk-forward backtest outputs to a markdown report.

Usage:
  python scripts/walk_forward_report.py --in-dir results/ --out docs/analysis/2026-06-15-walk-forward.md
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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_SECTIONS: tuple[str, ...] = (
    "# Walk-Forward Validation", "## TL;DR", "## Setup", "## Per-fold results",
    "## Stability", "## Breakeven analysis", "## Rule rankings",
    "## Live cross-check", "## Findings & recommendations", "## Appendix",
)


# === Helpers ===

def _fmt_wr(wr_str: str) -> str:
    try:
        wr = Decimal(wr_str)
        return f"{wr * 100:.2f}%"
    except Exception:
        return wr_str


def _fmt_pnl(pnl_str: str) -> str:
    try:
        pnl = Decimal(pnl_str)
        return f"${pnl:.2f}"
    except Exception:
        return pnl_str


def _fmt_money(s: str) -> str:
    try:
        return f"${Decimal(s):.2f}"
    except Exception:
        return s


# === Tables ===

def fold_table_markdown(folds: list[dict[str, Any]]) -> str:
    lines = [
        "| Fold | Test range | n_rounds | n_trades | WR | PnL | avg_PnL | avg_entry |",
        "|------|------------|----------|----------|------|--------|---------|-----------|",
    ]
    for f in folds:
        lines.append(
            f"| {f['fold_id']} | {f['test_start'][:10]} → {f['test_end'][:10]} | "
            f"{f['n_rounds']} | {f['n_trades']} | {_fmt_wr(f['wr'])} | "
            f"{_fmt_pnl(f['pnl'])} | {_fmt_money(f['avg_pnl'])} | {f['avg_entry_price']} |"
        )
    return "\n".join(lines)


def regime_table_markdown(rows: list[dict[str, Any]], key_header: str = "Bucket") -> str:
    lines = [
        f"| {key_header} | n | WR | PnL |",
        "|---|---|---|---|",
    ]
    for r in rows:
        # Use 'key' if present, else 'entry_price_bin' (for breakeven table) or 'rule_id' (for rule rankings)
        key = r.get("key") or r.get("entry_price_bin") or r.get("rule_id", "?")
        n = r.get("n") or r.get("n_trades", 0)
        wr = r.get("wr", "0")
        pnl = r.get("pnl", "0")
        lines.append(
            f"| {key} | {n} | {_fmt_wr(wr)} | {_fmt_pnl(pnl)} |"
        )
    return "\n".join(lines)


# === Main renderer ===

def render_report(*, aggregate: dict[str, Any], out_path: Path, in_dir: Path | None = None) -> None:
    """Compose the markdown report from aggregate summary + per-trade CSVs (if present)."""
    folds = aggregate["folds"]
    cross = aggregate["cross_fold"]
    n_total_trades = sum(f["n_trades"] for f in folds)
    pnl_total = Decimal(cross["pnl_total"])
    # CSVs are in the same dir as the aggregate summary (the results/ dir),
    # not next to the rendered report. If in_dir is provided, prefer it.
    csv_dir = in_dir if in_dir is not None else out_path.parent

    # 1. TL;DR
    wr_mean = Decimal(cross["wr_mean"])
    wr_std = Decimal(cross["wr_stdev"])
    if pnl_total > 0:
        verdict = "**+EV on out-of-sample.**"
    elif pnl_total < 0:
        verdict = "**−EV on out-of-sample.**"
    else:
        verdict = "Neutral on out-of-sample."
    stability = "stable" if wr_std < Decimal("0.05") else "unstable"
    tl_dr = (
        f"{verdict} "
        f"Across {len(folds)} folds ({aggregate['data_start'][:10]} → {aggregate['data_end'][:10]}), "
        f"the live rules generated **{n_total_trades} counterfactual trades** with a "
        f"cross-fold mean WR of **{_fmt_wr(str(wr_mean))}** "
        f"(σ = {wr_std * 100:.2f}pp, **{stability}**) and total PnL of **{_fmt_pnl(str(pnl_total))}**."
    )

    # 2. Setup
    setup = (
        f"- **Data range**: {aggregate['data_start']} → {aggregate['data_end']}\n"
        f"- **Number of folds**: {len(folds)}\n"
        f"- **Rules source**: `config/btc_updown_state_rules_15m.json` (live rules)\n"
        f"- **Position size**: $1.00 per trade (matches live `MAX_POSITION_USD`)\n"
        f"- **Filters**: samples ≥ 60, historical_probability ≥ 0.60, return_aligned=true, "
        f"entry_price ≤ 0.80 (matches live)"
    )

    # 3. Per-fold results
    per_fold = fold_table_markdown(folds)

    # 4. Stability
    stability_md = (
        f"- **Cross-fold mean WR**: {_fmt_wr(cross['wr_mean'])}\n"
        f"- **Cross-fold stdev WR**: {Decimal(cross['wr_stdev']) * 100:.2f}pp\n"
        f"- **Stability verdict**: {stability.upper()} (threshold 5pp)\n"
        f"- **Total PnL across folds**: {_fmt_pnl(cross['pnl_total'])}"
    )

    # 5. Breakeven analysis
    be_csv = csv_dir / "breakeven_sensitivity.csv"
    breakeven_md = "See `results/breakeven_sensitivity.csv` for the full table."
    if be_csv.exists():
        with be_csv.open() as f:
            be_rows = list(csv.DictReader(f))
        if be_rows:
            breakeven_md = "Entry-price bins (breakeven WR = entry_price):\n\n" + regime_table_markdown(be_rows, "Entry bin")

    # 6. Rule rankings
    rr_csv = csv_dir / "rule_performance_ranked.csv"
    rule_rank_md = "See `results/rule_performance_ranked.csv` for all rules."
    if rr_csv.exists():
        with rr_csv.open() as f:
            rr_rows = list(csv.DictReader(f))
        if rr_rows:
            top = rr_rows[:10]
            bottom = rr_rows[-10:]
            rule_rank_md = (
                "**Top 10 rules by PnL**:\n\n" + regime_table_markdown(top, "Rule") +
                "\n\n**Bottom 10 rules by PnL**:\n\n" + regime_table_markdown(bottom, "Rule")
            )

    # 7. Live cross-check
    live_md = (
        "Live PnL on the most recent 9 days (2026-06-06 → 2026-06-15): "
        "**−$3.72 on 7 settled trades (PnL avg −$0.53/settled, WR 28.6% on the 7 settled).**\n\n"
        "The backtest's most recent fold (overlapping with the live period) "
        "should show a comparable PnL. If the backtest is significantly more "
        "or less negative than live, the state-construction mismatches identified "
        "in `backtest-reference-compare.md` may be material. Detailed comparison is "
        "in `results/wf_aggregate_summary.json` (look at the fold with `test_end` "
        "closest to 2026-06-15)."
    )

    # 8. Findings
    findings = _build_findings(folds, cross, wr_std, stability, pnl_total)

    # 9. Appendix
    appendix = (
        "**Methodology**:\n"
        "- For each 15m round, the backtest replays `build_round_state()` and "
        "`ProbabilityRules.lookup()` exactly as the live bot does.\n"
        "- Entry price is set to `historical_probability − safety_buffer` (the live formula).\n"
        "- Settlement uses the close of the third 5m candle (`c2`), which closes at the "
        "round's end time.\n\n"
        "**Limitations**:\n"
        "- No orderbook spread modeling: real entries may be worse than the backtest's.\n"
        "- No slippage or liquidity constraints.\n"
        "- No daily loss cap (live caps at $10/day).\n"
        "- Round starts are aligned to UTC quarter-hour (00, 15, 30, 45); live rounds "
        "are similarly aligned, but exact timestamps may differ.\n\n"
        "**Data source**: Binance public 5m klines (`https://data-api.binance.vision/api/v3/klines`)."
    )

    report = f"""# Walk-Forward Validation + Breakeven Analysis

**Generated**: {aggregate.get("data_end", "")[:10]}

## TL;DR

{tl_dr}

## Setup

{setup}

## Per-fold results

{per_fold}

## Stability

{stability_md}

## Breakeven analysis

{breakeven_md}

## Rule rankings

{rule_rank_md}

## Live cross-check

{live_md}

## Findings & recommendations

{findings}

## Appendix

{appendix}
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)


def _build_findings(folds, cross, wr_std, stability, pnl_total) -> str:
    bullets: list[str] = []
    if Decimal(cross["pnl_total"]) < 0:
        bullets.append(
            f"- **Strategy is −EV across the analyzed folds**: total PnL "
            f"{_fmt_pnl(cross['pnl_total'])} on {sum(f['n_trades'] for f in folds)} trades."
        )
    else:
        bullets.append(
            f"- **Strategy is +EV across the analyzed folds**: total PnL "
            f"{_fmt_pnl(cross['pnl_total'])} on {sum(f['n_trades'] for f in folds)} trades."
        )
    if wr_std > Decimal("0.05"):
        bullets.append(
            f"- **Stability is low**: cross-fold stdev of WR is "
            f"{wr_std * 100:.2f}pp, above the 5pp threshold. Performance is "
            f"regime-dependent; consider restricting to specific vol/distance buckets."
        )
    else:
        bullets.append(
            f"- **Stability is acceptable**: cross-fold stdev of WR is "
            f"{wr_std * 100:.2f}pp, below the 5pp threshold."
        )
    bullets.append(
        "- **Review rule rankings in `results/rule_performance_ranked.csv`**: rules with "
        "WR below their entry-price breakeven are destroying value; consider dropping them "
        "via a tighter whitelist."
    )
    bullets.append(
        "- **Compare backtest vs live in the most recent fold**: if the backtest is "
        "materially different from live, the state-construction mismatches "
        "(`backtest-reference-compare.md`) likely need fixing before further tuning."
    )
    return "\n".join(bullets)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in-dir", default="results/")
    p.add_argument("--out", default="docs/analysis/2026-06-15-walk-forward.md")
    args = p.parse_args()
    in_dir = Path(args.in_dir)
    aggregate_path = in_dir / "wf_aggregate_summary.json"
    if not aggregate_path.exists():
        print(f"missing {aggregate_path}", file=sys.stderr)
        return 1
    aggregate = json.loads(aggregate_path.read_text())
    render_report(aggregate=aggregate, out_path=Path(args.out), in_dir=in_dir)
    print(f"OK: report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
