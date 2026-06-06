"""Export paper trades to CSV."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_round_bot.config import Settings
from polymarket_round_bot.reporting import export_trades_csv
from polymarket_round_bot.storage import Storage


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--format", choices=["csv"], default="csv")
    p.add_argument("--out", default="paper_trades.csv")
    p.add_argument("--since", default=None)
    args = p.parse_args()

    settings = Settings()
    storage = Storage(settings.database_file)
    settlements = storage.list_settlements(since_iso=args.since)
    positions = {p.position_id: p for p in storage.list_all_positions()}

    csv_text = export_trades_csv(settlements, positions, out=Path(args.out))
    print(f"wrote {len(settlements)} settlements to {args.out}")
    # Also print a header preview
    lines = csv_text.splitlines()
    for line in lines[:3]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
