"""Continuous / one-shot PAPER runner for the BTC round bot.

Usage:
  python scripts/run_polymarket_round_paper.py \\
      --timeframe 5m --mode paper

  python scripts/run_polymarket_round_paper.py \\
      --event-url "https://polymarket.com/uk/event/btc-updown-5m-1780652400" \\
      --mode paper --once
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path

# Allow running as a script from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_round_bot.config import Settings
from polymarket_round_bot.models import Timeframe
from polymarket_round_bot.paper_broker import PaperBroker
from polymarket_round_bot.probability_rules import ProbabilityRules
from polymarket_round_bot.risk_manager import RiskManager
from polymarket_round_bot.runner import Runner, current_expected_slug
from polymarket_round_bot.storage import Storage
from polymarket_round_bot.url_parser import parse_market_url

log = logging.getLogger("polymarket_round_bot")


# Backward-compat re-export for tests that imported the script-local
# helper. The canonical implementation lives in polymarket_round_bot.runner.
_current_expected_slug = current_expected_slug


def _resolve_slug(args: argparse.Namespace, settings: Settings) -> str:
    if args.event_url:
        return parse_market_url(args.event_url).slug
    if args.slug:
        return parse_market_url(args.slug).slug
    if settings.polymarket_event_url:
        return parse_market_url(settings.polymarket_event_url).slug
    if settings.polymarket_event_slug:
        return parse_market_url(settings.polymarket_event_slug).slug
    return current_expected_slug(args.timeframe)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Polymarket BTC UP/DOWN state-pricing PAPER bot")
    p.add_argument("--mode", choices=["paper"], default="paper")
    p.add_argument("--timeframe", type=Timeframe, default=Timeframe.M15)
    p.add_argument("--event-url", dest="event_url", default=None)
    p.add_argument("--slug", default=None)
    p.add_argument("--once", action="store_true", help="Run a single decision cycle and exit")
    p.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Seconds between cycles in continuous mode",
    )
    p.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Max cycles before exiting (None = infinite)",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument(
        "--print-snapshot",
        action="store_true",
        help="Print the decision snapshot as JSON after each cycle",
    )
    args = p.parse_args()
    _setup_logging(args.verbose)

    settings = Settings()
    if settings.bot_mode != "paper":
        log.error(
            "BOT_MODE=%s is not paper. v1 only supports paper. Set BOT_MODE=paper.",
            settings.bot_mode,
        )
        return 2

    slug = _resolve_slug(args, settings)
    log.info("resolved_slug=%s", slug)

    storage = Storage(settings.database_file)
    try:
        rules = ProbabilityRules.from_file(settings.state_rules_file)
    except Exception as e:
        log.error("failed_to_load_rules path=%s err=%s", settings.state_rules_file, e)
        return 2
    broker = PaperBroker()
    risk = RiskManager(settings)

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    storage.start_run(
        run_id,
        bot_mode="paper",
        settings_json=json.dumps(settings.model_dump(mode="json"), default=str),
    )
    log.info("run_started run_id=%s", run_id)

    runner = Runner(
        settings=settings,
        storage=storage,
        rules=rules,
        broker=broker,
        risk=risk,
        slug=slug,
        timeframe=args.timeframe if not (args.event_url or args.slug) else None,
    )

    try:
        if args.once:
            snap = runner.run_one_cycle()
            if args.print_snapshot:
                print(json.dumps(snap.to_dict(), indent=2, default=str))
            return 0
        runner.run_continuously(
            poll_interval_seconds=args.poll_interval,
            max_iterations=args.max_iterations,
        )
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        storage.end_run(run_id, notes="graceful_shutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
