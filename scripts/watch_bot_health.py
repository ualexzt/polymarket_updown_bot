"""Telegram watchdog for the paper bot.

Runs either once (cron/systemd timer) or continuously (Docker Compose service).
It sends one Telegram alert when the bot becomes unhealthy and one recovery
message when health returns.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Allow running as a script from project root without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_round_bot.config import Settings
from polymarket_round_bot.telegram_alerts import TelegramHealthAlertService

log = logging.getLogger("polymarket_round_bot.watchdog")


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch bot DB liveness and send Telegram alerts")
    parser.add_argument("--loop", action="store_true", help="Run forever instead of one check")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=60,
        help="Seconds between checks in --loop mode",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    settings = Settings()
    service = TelegramHealthAlertService(settings)

    if not args.loop:
        sent = service.check_and_alert()
        log.info("watchdog_check_complete alert_sent=%s", sent)
        return 0

    log.info(
        "watchdog_started interval_seconds=%d database=%s alerts_enabled=%s token_configured=%s chat_configured=%s",
        args.interval_seconds,
        settings.database_file,
        settings.telegram_alerts_enabled,
        bool(settings.telegram_bot_token),
        bool(settings.telegram_chat_id),
    )
    while True:
        try:
            sent = service.check_and_alert()
            log.info("watchdog_check_complete alert_sent=%s", sent)
        except Exception as exc:  # noqa: BLE001
            # Keep the watchdog alive; transient Telegram/network failures should
            # not permanently kill incident monitoring.
            log.exception("watchdog_check_failed err=%s", exc)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
