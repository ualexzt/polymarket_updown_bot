"""Reconcile live settlements with backtest counterfactual trades.

Usage:
  python scripts/reconcile_live_vs_backtest.py \\
    --live-db data/live_paper.sqlite \\
    --backtest-trades results/recon/wf_fold_0_trades.csv \\
    --out-dir results/recon/
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_LIVE_SETTLEMENT_COLS: tuple[str, ...] = (
    "market_slug", "selected_side", "won", "entry_price", "realized_pnl_usd",
    "settlement_source", "rule_id", "historical_probability_at_entry",
    "round_open_price", "round_close_price", "resolved_at_utc",
    "stage_at_entry", "volatility_bucket_at_entry", "distance_bucket_at_entry",
    "current_side_at_entry", "pattern_at_entry",
)

REQUIRED_BACKTEST_TRADE_COLS: tuple[str, ...] = (
    "market_slug", "stage", "current_side", "distance_bucket", "volatility_bucket",
    "pattern", "rule_id", "recommended_side", "historical_probability",
    "entry_price", "won", "pnl", "round_open_price", "round_close_price",
)


# === Loaders ===

def load_live_settlements(live_db: Path, *, period_start: datetime) -> list[dict[str, Any]]:
    """Load live settlements + paper_positions fields for the period >= period_start.

    Joins settlements with paper_positions to get stage_at_entry, vol_bucket, etc.
    """
    if not live_db.exists():
        raise FileNotFoundError(f"live DB not found: {live_db}")
    con = sqlite3.connect(live_db)
    con.row_factory = sqlite3.Row
    try:
        cursor = con.execute(
            """
            SELECT s.market_slug, s.selected_side, s.won, s.entry_price, s.realized_pnl_usd,
                   s.settlement_source, s.rule_id, s.historical_probability_at_entry,
                   s.round_open_price, s.round_close_price, s.resolved_at_utc,
                   p.stage_at_entry, p.volatility_bucket_at_entry, p.distance_bucket_at_entry,
                   p.current_side_at_entry, p.pattern_at_entry
            FROM settlements s
            LEFT JOIN paper_positions p ON p.position_id = s.position_id
            WHERE s.resolved_at_utc >= ?
            ORDER BY s.resolved_at_utc
            """,
            (period_start.isoformat(),),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        con.close()


def load_live_decisions(live_db: Path, *, period_start: datetime) -> list[dict[str, Any]]:
    """Load live TRADE decisions (for diagnostics)."""
    if not live_db.exists():
        raise FileNotFoundError(f"live DB not found: {live_db}")
    con = sqlite3.connect(live_db)
    con.row_factory = sqlite3.Row
    try:
        cursor = con.execute(
            """
            SELECT market_slug, timestamp_utc, stage, side_checked, selected_side,
                   candle_pattern, volatility_bucket, distance_bucket, current_side,
                   rule_id, historical_probability, market_ask, edge_vs_ask,
                   decision, skip_reason
            FROM decisions
            WHERE decision = 'TRADE' AND timestamp_utc >= ?
            ORDER BY timestamp_utc
            """,
            (period_start.isoformat(),),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        con.close()


def load_backtest_trades(csv_path: Path) -> list[dict[str, Any]]:
    """Load backtest trades CSV (wf_fold_*_trades.csv)."""
    if not csv_path.exists():
        raise FileNotFoundError(f"backtest trades CSV not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            if "won" in r:
                r["won"] = (r["won"].lower() == "true") if r["won"] else None
            rows.append(r)
    return rows


def match_by_slug(
    live: list[dict[str, Any]],
    backtest: list[dict[str, Any]],
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Match live and backtest trades by market_slug.

    Returns (matched_pairs, live_only, backtest_only).
    """
    live_by_slug: dict[str, list[dict]] = {}
    for r in live:
        slug = r.get("market_slug", "")
        live_by_slug.setdefault(slug, []).append(r)
    backtest_by_slug: dict[str, list[dict]] = {}
    for r in backtest:
        slug = r.get("market_slug", "")
        backtest_by_slug.setdefault(slug, []).append(r)

    matched: list[tuple[dict, dict]] = []
    live_only: list[dict] = []
    backtest_only: list[dict] = []

    for slug, live_rows in live_by_slug.items():
        if slug in backtest_by_slug:
            # 1:1 match (MAX_OPEN_POSITIONS=1 invariant)
            live_row = live_rows[0]
            back_row = backtest_by_slug[slug][0]
            matched.append((live_row, back_row))
        else:
            live_only.extend(live_rows)

    for slug, back_rows in backtest_by_slug.items():
        if slug not in live_by_slug:
            backtest_only.extend(back_rows)

    return matched, live_only, backtest_only


# === Field comparison ===

_STATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("stage_at_entry", "stage"),
    ("pattern_at_entry", "pattern"),
    ("volatility_bucket_at_entry", "volatility_bucket"),
    ("distance_bucket_at_entry", "distance_bucket"),
    ("current_side_at_entry", "current_side"),
)

_PRICE_FIELDS: tuple[tuple[str, str], ...] = (
    ("entry_price", "entry_price"),
    ("historical_probability_at_entry", "historical_probability"),
)

_SETTLEMENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("won", "won"),
    ("realized_pnl_usd", "pnl"),
    ("round_close_price", "round_close_price"),
)


def compare_pair(
    live: dict[str, Any],
    backtest: dict[str, Any],
    *,
    entry_tolerance: Decimal,
) -> dict[str, Any]:
    """Compare a matched (live, backtest) pair, return a diff summary.

    Returns: {"matched": bool, "field_diffs": [{"field", "category", "live", "backtest"}], ...}
    """
    diffs: list[dict[str, Any]] = []

    # State fields: exact string match
    for live_key, back_key in _STATE_FIELDS:
        l_val = str(live.get(live_key) or "")
        b_val = str(backtest.get(back_key) or "")
        if l_val != b_val:
            diffs.append({"field": live_key, "category": "state",
                          "live": l_val, "backtest": b_val})

    # Price fields: tolerance check
    for live_key, back_key in _PRICE_FIELDS:
        try:
            l_dec = Decimal(str(live.get(live_key) or "0"))
            b_dec = Decimal(str(backtest.get(back_key) or "0"))
            if abs(l_dec - b_dec) > entry_tolerance:
                diffs.append({"field": live_key, "category": "price",
                              "live": str(l_dec), "backtest": str(b_dec)})
        except Exception:
            diffs.append({"field": live_key, "category": "price",
                          "live": str(live.get(live_key)), "backtest": str(backtest.get(back_key))})

    # Settlement fields: tolerance on pnl/close, exact on won
    for live_key, back_key in _SETTLEMENT_FIELDS:
        l_val = live.get(live_key)
        b_val = backtest.get(back_key)
        if live_key == "won":
            l_bool = bool(l_val)
            b_bool = bool(b_val)
            if l_bool != b_bool:
                diffs.append({"field": live_key, "category": "settlement",
                              "live": str(l_bool), "backtest": str(b_bool)})
        else:
            try:
                l_dec = Decimal(str(l_val or "0"))
                b_dec = Decimal(str(b_val or "0"))
                if abs(l_dec - b_dec) > entry_tolerance:
                    diffs.append({"field": live_key, "category": "settlement",
                                  "live": str(l_dec), "backtest": str(b_dec)})
            except Exception:
                diffs.append({"field": live_key, "category": "settlement",
                              "live": str(l_val), "backtest": str(b_val)})

    return {"matched": True, "field_diffs": diffs}


def categorize_verdict(
    pair_diffs: list[dict[str, Any]],
    *,
    n_matched: int,
    n_live_only: int,
    n_backtest_only: int,
) -> dict[str, Any]:
    """Categorize the reconciliation result into A/B/C/D."""
    counters: Counter = Counter()
    for d in pair_diffs:
        for diff in d.get("field_diffs", []):
            counters[diff["category"]] += 1

    state_count = counters.get("state", 0)
    price_count = counters.get("price", 0)
    settlement_count = counters.get("settlement", 0)
    total_mismatches = state_count + price_count + settlement_count

    if n_matched < 5:
        verdict = "D"
        recommendation = (
            f"Only {n_matched} matched pairs (need >= 5). Likely insufficient data. "
            f"Either wait for more live settlements or proceed with caution; "
            f"all observed live trades: {n_live_only}, all observed backtest trades: {n_backtest_only}."
        )
    elif n_live_only > 5 and total_mismatches < n_matched:
        verdict = "A"
        recommendation = (
            f"Live has {n_live_only} trades not present in backtest, but matched pairs agree. "
            f"Likely the live bot has additional filters (spread, liquidity) that the backtest ignores. "
            f"Recommended next step: add a spread model to the backtest and rerun."
        )
    elif n_backtest_only > 5 and total_mismatches < n_matched:
        verdict = "A"
        recommendation = (
            f"Backtest has {n_backtest_only} trades not present in live. "
            f"Likely the backtest is too permissive (it allows trades that live filters out). "
            f"Recommended next step: review live filters and reflect them in the backtest."
        )
    elif state_count > total_mismatches * 0.5 and n_matched >= 5:
        verdict = "B"
        recommendation = (
            f"State fields (stage, pattern, vol_bucket, dist_bucket, current_side) "
            f"differ in {state_count} of {total_mismatches} mismatches across {n_matched} pairs. "
            f"Likely the state-construction mismatches identified in `backtest-reference-compare.md` are material. "
            f"Recommended next step: fix `round_state.py` (volatility source, distance bucket, AT_OPEN) and rerun."
        )
    elif settlement_count > total_mismatches * 0.5 and n_matched >= 5:
        verdict = "C"
        recommendation = (
            f"Settlement fields (won, pnl) differ in {settlement_count} of {total_mismatches} mismatches. "
            f"Likely settlement timing or tie handling diverges. "
            f"Recommended next step: compare `settlement.py` against the live settlement path."
        )
    else:
        verdict = "A"
        recommendation = (
            f"Diff is spread across categories (state={state_count}, price={price_count}, "
            f"settlement={settlement_count}). Multiple factors at play. "
            f"Recommended next step: deep dive into the top discrepancies."
        )

    return {
        "verdict": verdict,
        "n_matched": n_matched,
        "n_live_only": n_live_only,
        "n_backtest_only": n_backtest_only,
        "mismatch_counts": {
            "state": state_count, "price": price_count, "settlement": settlement_count,
            "total": total_mismatches,
        },
        "recommendation": recommendation,
    }


# === Output writer ===

def write_outputs(
    *,
    out_dir: Path,
    matched: list[tuple[dict, dict]],
    live_only: list[dict],
    backtest_only: list[dict],
    pair_diffs: list[dict[str, Any]],
    summary: dict[str, Any],
    entry_tolerance: Decimal,
) -> None:
    """Write matched_pairs.csv, live_only_trades.csv, backtest_only_trades.csv, reconciliation_summary.json."""
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "matched_pairs.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "market_slug",
            "live_entry_price", "backtest_entry_price",
            "live_stage", "backtest_stage", "stage_match",
            "live_pattern", "backtest_pattern", "pattern_match",
            "live_vol_bucket", "backtest_vol_bucket", "vol_match",
            "live_dist_bucket", "backtest_dist_bucket", "dist_match",
            "live_current_side", "backtest_current_side", "side_match",
            "live_won", "backtest_won", "won_match",
            "live_pnl", "backtest_pnl", "pnl_diff",
            "live_round_close", "backtest_round_close", "round_close_diff",
            "live_rule_id", "backtest_rule_id", "rule_id_match",
            "n_field_diffs",
        ])
        for (live, back), diff in zip(matched, pair_diffs):
            def get(d, k, default=""):
                v = d.get(k)
                return str(v) if v is not None else default

            def is_match(l, b, tol=entry_tolerance):
                try:
                    return "match" if abs(Decimal(str(l)) - Decimal(str(b))) <= tol else "DIFF"
                except Exception:
                    return "DIFF" if str(l) != str(b) else "match"

            writer.writerow([
                get(live, "market_slug"),
                get(live, "entry_price"), get(back, "entry_price"),
                get(live, "stage_at_entry"), get(back, "stage"),
                "match" if get(live, "stage_at_entry") == get(back, "stage") else "DIFF",
                get(live, "pattern_at_entry"), get(back, "pattern"),
                "match" if get(live, "pattern_at_entry") == get(back, "pattern") else "DIFF",
                get(live, "volatility_bucket_at_entry"), get(back, "volatility_bucket"),
                "match" if get(live, "volatility_bucket_at_entry") == get(back, "volatility_bucket") else "DIFF",
                get(live, "distance_bucket_at_entry"), get(back, "distance_bucket"),
                "match" if get(live, "distance_bucket_at_entry") == get(back, "distance_bucket") else "DIFF",
                get(live, "current_side_at_entry"), get(back, "current_side"),
                "match" if get(live, "current_side_at_entry") == get(back, "current_side") else "DIFF",
                get(live, "won"), get(back, "won"),
                "match" if str(bool(live.get("won"))) == str(bool(back.get("won"))) else "DIFF",
                get(live, "realized_pnl_usd"), get(back, "pnl"),
                is_match(live.get("realized_pnl_usd"), back.get("pnl"), entry_tolerance),
                get(live, "round_close_price"), get(back, "round_close_price"),
                is_match(live.get("round_close_price"), back.get("round_close_price"), entry_tolerance),
                get(live, "rule_id"), get(back, "rule_id"),
                "match" if get(live, "rule_id") == get(back, "rule_id") else "DIFF",
                str(len(diff.get("field_diffs", []))),
            ])

    if live_only:
        with (out_dir / "live_only_trades.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(live_only[0].keys()))
            writer.writeheader()
            writer.writerows(live_only)
    else:
        (out_dir / "live_only_trades.csv").write_text("")

    if backtest_only:
        with (out_dir / "backtest_only_trades.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(backtest_only[0].keys()))
            writer.writeheader()
            writer.writerows(backtest_only)
    else:
        (out_dir / "backtest_only_trades.csv").write_text("")

    (out_dir / "reconciliation_summary.json").write_text(json.dumps(summary, indent=2))


# === CLI ===

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live-db", required=True)
    p.add_argument("--backtest-trades", required=True)
    p.add_argument("--out-dir", default="results/recon/")
    p.add_argument("--period-start", default="2026-06-06T11:51:00+00:00",
                   help="ISO datetime; only load live settlements after this")
    p.add_argument("--entry-tolerance", type=Decimal, default=Decimal("0.01"))
    args = p.parse_args()

    period_start = datetime.fromisoformat(args.period_start)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading live settlements from {args.live_db} (period >= {period_start})...", file=sys.stderr)
    live = load_live_settlements(Path(args.live_db), period_start=period_start)
    print(f"  loaded {len(live)} live settlements", file=sys.stderr)
    print(f"Loading backtest trades from {args.backtest_trades}...", file=sys.stderr)
    backtest = load_backtest_trades(Path(args.backtest_trades))
    print(f"  loaded {len(backtest)} backtest trades", file=sys.stderr)

    matched, live_only, backtest_only = match_by_slug(live, backtest)
    print(f"Matched: {len(matched)}, live_only: {len(live_only)}, "
          f"backtest_only: {len(backtest_only)}", file=sys.stderr)

    pair_diffs = [compare_pair(l, b, entry_tolerance=args.entry_tolerance) for l, b in matched]
    summary = categorize_verdict(
        pair_diffs,
        n_matched=len(matched),
        n_live_only=len(live_only),
        n_backtest_only=len(backtest_only),
    )
    print(f"Verdict: {summary['verdict']} — {summary['recommendation']}", file=sys.stderr)

    write_outputs(
        out_dir=out_dir, matched=matched, live_only=live_only, backtest_only=backtest_only,
        pair_diffs=pair_diffs, summary=summary, entry_tolerance=args.entry_tolerance,
    )
    print(f"OK: outputs written to {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
