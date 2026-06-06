"""Inspect one paper trade by position_id."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_round_bot.config import Settings
from polymarket_round_bot.reporting import inspect_trade
from polymarket_round_bot.storage import Storage


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--position-id", dest="position_id", required=True)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    settings = Settings()
    storage = Storage(settings.database_file)
    try:
        result = inspect_trade(storage, args.position_id)
    except KeyError as e:
        print(str(e))
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    pos = result["position"]
    s = result["settlement"]
    print(f"=== Trade {args.position_id} ===")
    print(
        f"market={pos['market_slug']} side={pos['selected_side']} "
        f"entry_price={pos['entry_price']} size_usd={pos['entry_size_usd']} "
        f"shares={pos['shares']}"
    )
    print(
        f"stage={pos['stage_at_entry']} pattern={pos['pattern_at_entry']} "
        f"distance_bucket={pos['distance_bucket_at_entry']} "
        f"volatility_bucket={pos['volatility_bucket_at_entry']}"
    )
    print(
        f"rule_id={pos['rule_id']} match_type={pos['rule_match_type']} "
        f"historical_prob={pos['historical_probability_at_entry']} "
        f"samples={pos['samples_at_entry']}"
    )
    print(
        f"max_buy_price={pos['max_buy_price_at_entry']} "
        f"edge={pos['edge_at_entry']} spread={pos['entry_spread']}"
    )
    if s:
        print(
            f"\nresolved_outcome={s['resolved_outcome']} won={s['won']} "
            f"payout={s['payout_usd']} pnl={s['realized_pnl_usd']} "
            f"roi={s['realized_roi_pct']:.4f} quality={s['trade_quality']}"
        )
    else:
        print("\nNot yet settled.")
    print(
        f"\nMark-to-market snapshots: {len(result['mark_to_market'])} "
        f"(first best_bid={result['mark_to_market'][0].get('best_bid') if result['mark_to_market'] else None})"
    )
    print("\n--- Explanation ---")
    for line in result["explanation"]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
