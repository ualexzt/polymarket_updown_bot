"""Paper report CLI.

Usage:
  python scripts/paper_report.py --since 2026-06-01
  python scripts/paper_report.py                    # report all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_round_bot.config import Settings
from polymarket_round_bot.reporting import paper_summary
from polymarket_round_bot.storage import Storage


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None, help="ISO date e.g. 2026-06-01")
    p.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = p.parse_args()

    settings = Settings()
    storage = Storage(settings.database_file)
    summary = paper_summary(storage, since=args.since)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return 0

    print(f"=== Paper report (since={args.since or 'all'}) ===")
    print(f"total_decisions       : {summary['total_decisions']}")
    print(f"total_trades          : {summary['total_trades']}")
    print(f"total_skips           : {summary['total_skips']}")
    print(f"settled_trades        : {summary['settled_trades']}")
    print(f"open_trades           : {summary['open_trades']}")
    print(f"win_count / loss_count: {summary['win_count']} / {summary['loss_count']}")
    print(f"win_rate              : {summary['win_rate']:.4f}")
    print(f"total_realized_pnl    : {summary['total_realized_pnl']:.4f}")
    print(f"average_realized_pnl  : {summary['average_realized_pnl']:.4f}")
    print(f"median_realized_pnl   : {summary['median_realized_pnl']:.4f}")
    print(f"average_entry_price   : {summary['average_entry_price']:.4f}")
    print(f"average_fair_at_entry : {summary['average_fair_price_at_entry']:.4f}")
    print(f"average_edge_at_entry : {summary['average_edge_at_entry']:.4f}")
    print(f"average_spread_entry  : {summary['average_spread_at_entry']:.4f}")
    print(f"avg_secs_to_expiry    : {summary['average_seconds_to_expiry_at_entry']:.2f}")
    print()
    print("--- decision funnel ---")
    funnel = summary["funnel"]
    print(f"  markets_seen     : {funnel['total_decisions']}")
    print(f"  TRADE            : {funnel['traded']}")
    print(f"  SKIP (all)       : {funnel['skipped_total']}")
    for stage_name, n in funnel["skipped_by_stage"].items():
        print(f"    {stage_name:<18}: {n}")
    print()
    print("--- by timeframe ---")
    for tf, b in sorted(funnel["by_timeframe"].items()):
        print(f"  {tf}: traded={b['traded']} skipped={b['skipped_total']}")
        for stage_name, n in b["skipped_by_stage"].items():
            print(f"    {stage_name:<18}: {n}")
    print()
    print("--- by stage label (rule-lookup outcome) ---")
    for sl, b in sorted(funnel["by_stage_label"].items()):
        print(f"  {sl}: traded={b['traded']} skipped={b['skipped_total']}")
    print()
    print("--- raw skip reason distribution ---")
    for reason, n in summary["skip_reasons"].items():
        print(f"  {reason}: {n}")
    print()
    if summary["best_trade"]:
        bt = summary["best_trade"]
        print(f"best_trade  : pos={bt['position_id']} slug={bt['market_slug']} pnl={bt['pnl']:.4f}")
    if summary["worst_trade"]:
        wt = summary["worst_trade"]
        print(f"worst_trade : pos={wt['position_id']} slug={wt['market_slug']} pnl={wt['pnl']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
