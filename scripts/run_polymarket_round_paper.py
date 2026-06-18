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
import os
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
from polymarket_round_bot.rule_whitelist import (
    EMPTY_RULE_WHITELIST,
    RuleWhitelistError,
    load_rule_whitelist,
)
from polymarket_round_bot.runner import Runner, current_expected_slug
from polymarket_round_bot.orderbook_stream import OrderbookStream
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


def _build_telemetry_writer() -> object | None:
    """Construct a control plane TelemetryWriter if configured.

    Returns None when CONTROL_PLANE_DATABASE_URL is unset so the bot
    keeps working in standalone mode. Failures to construct the
    writer are logged and treated as "telemetry disabled" — never
    break the bot for telemetry.
    """
    database_url = os.environ.get("CONTROL_PLANE_DATABASE_URL")
    if not database_url:
        return None
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from polymarket_control_plane_sdk import TelemetryWriter

        engine = create_engine(database_url)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        writer = TelemetryWriter(Session)
        writer.ensure_schema()
        log.info("control_plane_telemetry_enabled url=%s", database_url)
        return writer
    except Exception as exc:  # noqa: BLE001
        log.warning("control_plane_telemetry_init_failed err=%s", exc)
        return None


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

    storage = Storage(
        settings.database_file,
        telemetry_writer=_build_telemetry_writer(),
        strategy_id=os.environ.get("STRATEGY_ID", "polymarket-updown-paper"),
    )
    try:
        rules = ProbabilityRules.from_file(settings.state_rules_file)
    except Exception as e:
        log.error("failed_to_load_rules path=%s err=%s", settings.state_rules_file, e)
        return 2
    rule_policy = EMPTY_RULE_WHITELIST
    if settings.rule_whitelist_enabled:
        try:
            rule_policy = load_rule_whitelist(settings.rule_whitelist_file)
        except RuleWhitelistError as e:
            log.error(
                "failed_to_load_rule_whitelist path=%s err=%s",
                settings.rule_whitelist_file,
                e,
            )
            return 2

    broker = PaperBroker()
    risk = RiskManager(settings)

    # Start WebSocket orderbook stream
    orderbook_stream = OrderbookStream()
    orderbook_stream.start()
    log.info("orderbook_stream_initialized")

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
        rule_policy=rule_policy,
        orderbook_stream=orderbook_stream,
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
        orderbook_stream.stop()
        storage.end_run(run_id, notes="graceful_shutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
