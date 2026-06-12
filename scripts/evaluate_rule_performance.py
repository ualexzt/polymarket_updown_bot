"""Evaluate paper rule performance from SQLite settlements.

Usage:
  python scripts/evaluate_rule_performance.py --since 2026-06-08T17:41:19
  python scripts/evaluate_rule_performance.py --min-trades 2 --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_round_bot.config import Settings


def _dec(value: object) -> Decimal:
    return Decimal(str(value))


def _avg(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def summarize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["rule_id"]), str(row["selected_side"]), str(row["stage"]))].append(row)

    out: list[dict[str, Any]] = []
    for (rule_id, side, stage), items in grouped.items():
        pnl_values = [_dec(i["realized_pnl_usd"]) for i in items]
        entry_prices = [_dec(i["entry_price"]) for i in items]
        probs = [_dec(i["historical_probability_at_entry"]) for i in items if i.get("historical_probability_at_entry") is not None]
        edges = [_dec(i["edge_at_entry"]) for i in items if i.get("edge_at_entry") is not None]
        wins = sum(1 for i in items if int(i["won"]) == 1)
        n = len(items)
        out.append(
            {
                "rule_id": rule_id,
                "side": side,
                "stage": stage,
                "n": n,
                "wins": wins,
                "win_rate": Decimal(wins) / Decimal(n),
                "pnl": sum(pnl_values, Decimal("0")),
                "avg_pnl": _avg(pnl_values),
                "avg_entry_price": _avg(entry_prices),
                "breakeven_win_rate": _avg(entry_prices),
                "avg_historical_probability": _avg(probs),
                "avg_edge": _avg(edges),
            }
        )
    out.sort(key=lambda x: (x["pnl"], x["n"]))
    return out


def fetch_rows(database: Path, since: str | None) -> list[dict[str, Any]]:
    if not database.exists():
        return []
    con = sqlite3.connect(database)
    con.row_factory = sqlite3.Row
    where = "WHERE s.rule_id IS NOT NULL"
    params: list[object] = []
    if since:
        where += " AND s.resolved_at_utc >= ?"
        params.append(since)
    sql = f"""
        SELECT
            s.rule_id,
            s.selected_side,
            p.stage_at_entry AS stage,
            s.entry_price,
            s.historical_probability_at_entry,
            s.edge_at_entry,
            s.won,
            s.realized_pnl_usd
        FROM settlements s
        JOIN paper_positions p ON p.position_id = s.position_id
        {where}
    """
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=None)
    parser.add_argument("--min-trades", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    rows = fetch_rows(settings.database_file, args.since)
    summary = [r for r in summarize_rows(rows) if r["n"] >= args.min_trades]

    if args.json:
        print(json.dumps(summary, indent=2, default=_json_default))
        return 0

    print(f"=== Rule performance since={args.since or 'all'} min_trades={args.min_trades} ===")
    for r in summary:
        print(
            f"{r['pnl']:>8} n={r['n']:>3} w={r['wins']:>3} "
            f"wr={r['win_rate']:.3f} be={r['breakeven_win_rate']:.3f} "
            f"avg_entry={r['avg_entry_price']:.3f} side={r['side']:<4} stage={r['stage']:<9} {r['rule_id']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
