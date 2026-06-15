"""Breakeven analysis + rule rankings + regime breakdown from backtest trades.

Usage:
  python scripts/breakeven_analysis.py --trades-glob 'results/wf_fold_*_trades.csv' --out-dir results/
"""
from __future__ import annotations

import argparse
import csv
import glob
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# === Loaders ===

def load_trades_glob(pattern: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(pattern)):
        with Path(path).open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if "won" in r and r["won"] not in ("True", "False", "true", "false"):
                    continue
                if "won" in r:
                    r["won"] = (r["won"].lower() == "true")
                rows.append(r)
    return rows


# === Breakeven sensitivity ===

def breakeven_sensitivity(trades: list[dict], *, entry_bins: list[Decimal]) -> list[dict[str, Any]]:
    """Compute WR / PnL / breakeven-WR per entry_price bin."""
    rows: list[dict[str, Any]] = []
    for lo in entry_bins:
        hi = lo + Decimal("0.05")
        subset = [t for t in trades if lo <= Decimal(t["entry_price"]) < hi]
        n = len(subset)
        if n == 0:
            rows.append({
                "entry_price_bin": f"{lo:.2f}-{hi:.2f}",
                "n_trades": 0, "wins": 0, "wr": "0",
                "pnl": "0", "avg_pnl": "0", "avg_entry": "0",
                "breakeven_wr": str(lo), "wr_minus_breakeven": "0",
            })
            continue
        wins = sum(1 for t in subset if t.get("won") is True)
        pnls = [Decimal(t["pnl"]) for t in subset if t.get("pnl") is not None]
        total_pnl = sum(pnls)
        wr = Decimal(wins) / Decimal(n)
        be_wr = lo
        rows.append({
            "entry_price_bin": f"{lo:.2f}-{hi:.2f}",
            "n_trades": n, "wins": wins,
            "wr": f"{wr:.4f}",
            "pnl": f"{total_pnl:.2f}",
            "avg_pnl": f"{total_pnl / Decimal(n):.4f}",
            "avg_entry": f"{sum(Decimal(t['entry_price']) for t in subset) / Decimal(n):.4f}",
            "breakeven_wr": f"{be_wr:.4f}",
            "wr_minus_breakeven": f"{wr - be_wr:.4f}",
        })
    return rows


# === Rule rankings ===

def rule_performance(trades: list[dict], *, min_trades: int = 1) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        grouped[t.get("rule_id", "unknown")].append(t)
    rows: list[dict[str, Any]] = []
    for rule_id, items in grouped.items():
        n = len(items)
        if n < min_trades:
            continue
        wins = sum(1 for i in items if i.get("won") is True)
        pnls = [Decimal(i["pnl"]) for i in items if i.get("pnl") is not None]
        rows.append({
            "rule_id": rule_id,
            "side": items[0].get("recommended_side", ""),
            "stage": items[0].get("stage", ""),
            "n": n, "wins": wins,
            "wr": f"{Decimal(wins) / Decimal(n):.4f}",
            "pnl": f"{sum(pnls):.2f}",
            "avg_pnl": f"{sum(pnls) / Decimal(n):.4f}",
            "avg_entry": f"{sum(Decimal(i['entry_price']) for i in items) / Decimal(n):.4f}",
            "avg_hist_prob": f"{sum(Decimal(i['historical_probability']) for i in items) / Decimal(n):.4f}",
        })
    rows.sort(key=lambda r: Decimal(r["pnl"]), reverse=True)
    return rows


# === Regime breakdown ===

def regime_breakdown(trades: list[dict]) -> dict[str, list[dict[str, Any]]]:
    """Group trades by various regimes; return WR/PnL per group."""
    out: dict[str, list[dict[str, Any]]] = {}
    for dim, key in [
        ("by_volatility", "volatility_bucket"),
        ("by_distance", "distance_bucket"),
        ("by_pattern", "pattern"),
        ("by_side", "recommended_side"),
    ]:
        out[dim] = _aggregate_by(trades, key)
    out["by_hour"] = _by_hour(trades)
    out["by_dow"] = _by_dow(trades)
    return out


def _aggregate_by(trades: list[dict], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        grouped[t.get(key, "unknown")].append(t)
    rows: list[dict[str, Any]] = []
    for k, items in grouped.items():
        n = len(items)
        wins = sum(1 for i in items if i.get("won") is True)
        pnls = [Decimal(i["pnl"]) for i in items if i.get("pnl") is not None]
        rows.append({
            "key": k, "n": n, "wins": wins,
            "wr": f"{Decimal(wins) / Decimal(n):.4f}" if n else "0",
            "pnl": f"{sum(pnls):.2f}" if pnls else "0",
        })
    rows.sort(key=lambda r: r["n"], reverse=True)
    return rows


def _by_hour(trades: list[dict]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        ts = t.get("round_start_ts") or t.get("entry_now_utc") or ""
        try:
            hour = datetime.fromisoformat(ts).hour
        except (ValueError, TypeError):
            continue
        grouped[hour].append(t)
    return _to_breakdown_rows(grouped, "hour")


def _by_dow(trades: list[dict]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        ts = t.get("round_start_ts") or t.get("entry_now_utc") or ""
        try:
            dow = datetime.fromisoformat(ts).weekday()
        except (ValueError, TypeError):
            continue
        grouped[dow].append(t)
    return _to_breakdown_rows(
        grouped, "dow",
        names={0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"},
    )


def _to_breakdown_rows(grouped, key_name, names=None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for k, items in grouped.items():
        n = len(items)
        wins = sum(1 for i in items if i.get("won") is True)
        pnls = [Decimal(i["pnl"]) for i in items if i.get("pnl") is not None]
        label = names.get(k, str(k)) if names else str(k)
        rows.append({
            "key": label, "n": n, "wins": wins,
            "wr": f"{Decimal(wins) / Decimal(n):.4f}" if n else "0",
            "pnl": f"{sum(pnls):.2f}" if pnls else "0",
        })
    rows.sort(key=lambda r: r["key"])
    return rows


# === Writers ===

def write_breakeven_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_rule_ranking_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# === Main ===

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--trades-glob", default="results/wf_fold_*_trades.csv")
    p.add_argument("--out-dir", default="results/")
    args = p.parse_args()

    trades = load_trades_glob(args.trades_glob)
    if not trades:
        print(f"No trades loaded from {args.trades_glob}", file=sys.stderr)
        return 1
    print(f"Loaded {len(trades)} trades from {args.trades_glob}", file=sys.stderr)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Breakeven sensitivity
    bins = [Decimal(str(x)) / Decimal("100") for x in range(30, 85, 5)]
    be = breakeven_sensitivity(trades, entry_bins=bins)
    write_breakeven_csv(be, out_dir / "breakeven_sensitivity.csv")
    print("\n=== Breakeven sensitivity (entry bins 0.30-0.80) ===", file=sys.stderr)
    for r in be:
        print(f"  {r['entry_price_bin']}: n={r['n_trades']:>3} wr={r['wr']:>7} be_wr={r['breakeven_wr']:>5} "
              f"wr-be={r['wr_minus_breakeven']:>7} pnl=${r['pnl']}", file=sys.stderr)

    # 2. Rule rankings
    rr = rule_performance(trades, min_trades=2)
    write_rule_ranking_csv(rr, out_dir / "rule_performance_ranked.csv")
    print("\n=== Top 10 rules by PnL ===", file=sys.stderr)
    for r in rr[:10]:
        print(f"  {r['pnl']:>7} n={r['n']:>3} wr={r['wr']} side={r['side']:<5} stage={r['stage']:<9} {r['rule_id']}", file=sys.stderr)
    print("\n=== Bottom 10 rules by PnL ===", file=sys.stderr)
    for r in rr[-10:]:
        print(f"  {r['pnl']:>7} n={r['n']:>3} wr={r['wr']} side={r['side']:<5} stage={r['stage']:<9} {r['rule_id']}", file=sys.stderr)

    # 3. Regime breakdown
    rb = regime_breakdown(trades)
    print("\n=== WR by side ===", file=sys.stderr)
    for r in rb["by_side"]:
        print(f"  {r['key']:<5}: n={r['n']:>3} wr={r['wr']} pnl=${r['pnl']}", file=sys.stderr)
    print("\n=== WR by volatility ===", file=sys.stderr)
    for r in rb["by_volatility"]:
        print(f"  {r['key']:<12}: n={r['n']:>3} wr={r['wr']} pnl=${r['pnl']}", file=sys.stderr)
    print("\n=== WR by distance ===", file=sys.stderr)
    for r in rb["by_distance"]:
        print(f"  {r['key']:<13}: n={r['n']:>3} wr={r['wr']} pnl=${r['pnl']}", file=sys.stderr)
    print("\n=== WR by hour (UTC) ===", file=sys.stderr)
    for r in rb["by_hour"]:
        print(f"  {r['key']:>4}: n={r['n']:>3} wr={r['wr']} pnl=${r['pnl']}", file=sys.stderr)
    print("\n=== WR by day of week ===", file=sys.stderr)
    for r in rb["by_dow"]:
        print(f"  {r['key']:>4}: n={r['n']:>3} wr={r['wr']} pnl=${r['pnl']}", file=sys.stderr)

    # 4. Counterfactual filter simulations
    print("\n=== Counterfactual: only trade if avg_hist_prob >= X ===", file=sys.stderr)
    for threshold in (Decimal("0.55"), Decimal("0.60"), Decimal("0.65"), Decimal("0.70"), Decimal("0.75")):
        sub = [t for t in trades if Decimal(t["historical_probability"]) >= threshold]
        if not sub:
            print(f"  threshold={threshold}: n=0, skip", file=sys.stderr)
            continue
        wins = sum(1 for t in sub if t.get("won") is True)
        pnls = [Decimal(t["pnl"]) for t in sub]
        wr = Decimal(wins) / Decimal(len(sub))
        print(f"  threshold={threshold}: n={len(sub):>3} wr={wr:.4f} pnl=${sum(pnls):.2f}", file=sys.stderr)

    print("\n=== Counterfactual: side filter ===", file=sys.stderr)
    for side in ("UP", "DOWN"):
        sub = [t for t in trades if t.get("recommended_side") == side]
        if not sub:
            print(f"  {side}: n=0, skip", file=sys.stderr)
            continue
        wins = sum(1 for t in sub if t.get("won") is True)
        pnls = [Decimal(t["pnl"]) for t in sub]
        wr = Decimal(wins) / Decimal(len(sub))
        print(f"  {side}: n={len(sub):>3} wr={wr:.4f} pnl=${sum(pnls):.2f}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
