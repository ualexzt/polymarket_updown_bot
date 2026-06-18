"""Docker/runtime healthcheck for the paper bot."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a script from project root without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_round_bot.config import Settings
from polymarket_round_bot.healthcheck import check_database_health


def main() -> int:
    parser = argparse.ArgumentParser(description="Check paper bot DB health and liveness")
    parser.add_argument(
        "--max-decision-age-seconds",
        type=int,
        default=300,
        help="Fail if latest decision is older than this many seconds",
    )
    args = parser.parse_args()

    settings = Settings()
    result = check_database_health(
        settings.database_file,
        max_decision_age_seconds=args.max_decision_age_seconds,
    )
    if result.ok:
        print(
            "ok "
            f"database={settings.database_file} "
            f"last_decision_at={result.last_decision_at.isoformat() if result.last_decision_at else None} "
            f"age_seconds={result.last_decision_age_seconds}"
        )
        return 0

    print(
        "unhealthy "
        f"database={settings.database_file} "
        f"reason={result.reason} "
        f"last_decision_at={result.last_decision_at.isoformat() if result.last_decision_at else None} "
        f"age_seconds={result.last_decision_age_seconds} "
        f"error={result.error}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
