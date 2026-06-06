"""Tests for Runner behaviour, slug refresh, and stale-position settlement.

Covers the bug discovered on 2026-06-06: ``run_continuously()`` did
not advance ``self._slug`` at :00/:15/:30/:45 UTC boundaries, so the
bot kept polling an expired market for 3+ hours and the open position
was never settled.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

from polymarket_round_bot.config import Settings
from polymarket_round_bot.models import (
    PaperPosition,
    PositionStatus,
    Side,
    Stage,
    Timeframe,
)
from polymarket_round_bot.paper_broker import PaperBroker
from polymarket_round_bot.polymarket_discovery import DiscoveryError
from polymarket_round_bot.probability_rules import ProbabilityRules
from polymarket_round_bot.risk_manager import RiskManager
from polymarket_round_bot.runner import (
    Runner,
    current_expected_slug,
    slug_window,
)
from polymarket_round_bot.storage import Storage

# --- helpers ---


def _settings(tmp_path) -> Settings:
    s = Settings(
        database_path=str(tmp_path / "test.sqlite"),
        state_rules_path="config/btc_updown_state_rules_15m.json",
    )
    return s


def _storage(tmp_path) -> Storage:
    return Storage(tmp_path / "test.sqlite")


def _rules() -> ProbabilityRules:
    from pathlib import Path

    return ProbabilityRules.from_file(
        Path(__file__).resolve().parents[1] / "config" / "btc_updown_state_rules_15m.json"
    )


def _broker() -> PaperBroker:
    return PaperBroker()


def _risk(settings: Settings) -> RiskManager:
    return RiskManager(settings)


# === current_expected_slug ===


def test_current_expected_slug_15m_at_explicit_boundary():
    """At an exact :00/:15/:30/:45 UTC second, slug ts == now."""
    at_boundary = datetime.fromtimestamp(1780734600, tz=UTC)  # 08:30:00
    slug = current_expected_slug(Timeframe.M15, now_utc=at_boundary)
    assert slug == "btc-updown-15m-1780734600"


def test_current_expected_slug_15m_floors_down():
    """14:59:59 UTC -> slug of 14:45, not 15:00."""
    just_before = datetime.fromtimestamp(1780734599, tz=UTC)  # 08:29:59
    slug = current_expected_slug(Timeframe.M15, now_utc=just_before)
    assert slug == "btc-updown-15m-1780733700"  # 08:20:00, the previous boundary


def test_current_expected_slug_5m_floors_to_5m():
    """5m timeframe uses 300s intervals."""
    at_boundary = datetime.fromtimestamp(1780733700, tz=UTC)  # 08:20:00
    slug = current_expected_slug(Timeframe.M5, now_utc=at_boundary)
    assert slug == "btc-updown-5m-1780733700"

    one_sec_after = datetime.fromtimestamp(1780733701, tz=UTC)
    slug2 = current_expected_slug(Timeframe.M5, now_utc=one_sec_after)
    assert slug2 == "btc-updown-5m-1780733700"  # still in the same 5m window


# === slug_window ===


def test_slug_window_15m():
    """slug -> (Timeframe, start, end) parsing."""
    tf, start, end = slug_window("btc-updown-15m-1780733700")
    assert tf == Timeframe.M15
    assert start == datetime.fromtimestamp(1780733700, tz=UTC)
    assert end == datetime.fromtimestamp(1780734600, tz=UTC)  # +15 min


def test_slug_window_5m():
    tf, start, end = slug_window("btc-updown-5m-1780688700")
    assert tf == Timeframe.M5
    assert start == datetime.fromtimestamp(1780688700, tz=UTC)
    assert end == datetime.fromtimestamp(1780689000, tz=UTC)  # +5 min


# === Runner._maybe_refresh_slug ===


def test_runner_advances_slug_at_15m_boundary(tmp_path):
    """When the wall clock crosses a 15-min boundary, the runner
    must update self._slug to the new window before the next cycle.
    """
    settings = _settings(tmp_path)
    runner = Runner(
        settings=settings,
        storage=_storage(tmp_path),
        rules=_rules(),
        broker=_broker(),
        risk=_risk(settings),
        slug="btc-updown-15m-1780733700",  # 08:20:00
        timeframe=Timeframe.M15,
    )
    assert runner._slug == "btc-updown-15m-1780733700"

    # Simulate we are now 08:35:01 (well past 08:30 boundary).
    fake_now = datetime.fromtimestamp(1780734601, tz=UTC)
    with patch(
        "polymarket_round_bot.runner.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = fake_now
        runner._maybe_refresh_slug()

    assert runner._slug == "btc-updown-15m-1780734600"  # 08:30:00


def test_runner_does_not_change_slug_inside_same_window(tmp_path):
    """If the wall clock is still in the same 15-min window, slug stays."""
    settings = _settings(tmp_path)
    runner = Runner(
        settings=settings,
        storage=_storage(tmp_path),
        rules=_rules(),
        broker=_broker(),
        risk=_risk(settings),
        slug="btc-updown-15m-1780733700",
        timeframe=Timeframe.M15,
    )
    # 08:25:00 — same window (08:20-08:35). 1780733700 + 300 = 1780734000.
    fake_now = datetime.fromtimestamp(1780734000, tz=UTC)
    with patch("polymarket_round_bot.runner.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        runner._maybe_refresh_slug()

    assert runner._slug == "btc-updown-15m-1780733700"


def test_runner_does_not_refresh_when_timeframe_is_none(tmp_path):
    """Explicit-URL mode (no timeframe) must not auto-advance slug."""
    settings = _settings(tmp_path)
    runner = Runner(
        settings=settings,
        storage=_storage(tmp_path),
        rules=_rules(),
        broker=_broker(),
        risk=_risk(settings),
        slug="btc-updown-15m-1780733700",
        timeframe=None,
    )
    fake_now = datetime.fromtimestamp(1780734601, tz=UTC)
    with patch("polymarket_round_bot.runner.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        runner._maybe_refresh_slug()

    assert runner._slug == "btc-updown-15m-1780733700"


def test_runner_advances_slug_for_5m_timeframe(tmp_path):
    """5m timeframe also gets advanced on every 5m boundary."""
    settings = _settings(tmp_path)
    runner = Runner(
        settings=settings,
        storage=_storage(tmp_path),
        rules=_rules(),
        broker=_broker(),
        risk=_risk(settings),
        slug="btc-updown-5m-1780733700",  # 08:20:00
        timeframe=Timeframe.M5,
    )
    # 08:20:01 → next 5m boundary is 08:25:00
    fake_now = datetime.fromtimestamp(1780736401, tz=UTC)
    with patch("polymarket_round_bot.runner.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        runner._maybe_refresh_slug()

    assert runner._slug == "btc-updown-5m-1780736400"  # 08:30:00


# === Stale-position settlement (Binance fallback) ===


def _open_position(
    slug: str,
    *,
    side: Side = Side.DOWN,
    round_open: Decimal = Decimal("60000"),
    entry_price: Decimal = Decimal("0.53"),
    shares: Decimal = Decimal("9.43"),
) -> PaperPosition:
    return PaperPosition(
        position_id="pos_test123",
        decision_id="dec_test123",
        market_slug=slug,
        event_url=None,
        selected_side=side,
        token_id="0xtoken",
        entry_timestamp_utc=datetime.fromtimestamp(1780734057, tz=UTC),  # 08:20:57
        entry_price=entry_price,
        entry_best_ask=entry_price,
        entry_best_bid=entry_price - Decimal("0.01"),
        entry_spread=Decimal("0.01"),
        entry_size_usd=entry_price * shares,
        shares=shares,
        fair_price_at_entry=Decimal("0.85"),
        max_buy_price_at_entry=Decimal("0.81"),
        edge_at_entry=Decimal("0.28"),
        round_open_price=round_open,
        btc_price_at_entry=round_open - Decimal("50"),
        distance_bucket_at_entry=__import__(
            "polymarket_round_bot.models", fromlist=["DistanceBucket"]
        ).DistanceBucket.D_005_010pct,
        volatility_bucket_at_entry=__import__(
            "polymarket_round_bot.models", fromlist=["VolatilityBucket"]
        ).VolatilityBucket.VOL_LOW,
        pattern_at_entry="strong_bear_close_near_low",
        stage_at_entry=Stage.AFTER_5M,
        seconds_to_expiry_at_entry=543,
        current_side_at_entry=__import__(
            "polymarket_round_bot.models", fromlist=["CurrentSide"]
        ).CurrentSide.BELOW_OPEN,
        status=PositionStatus.OPEN,
        rule_id="rule_x",
        rule_match_type=__import__(
            "polymarket_round_bot.models", fromlist=["RuleMatchType"]
        ).RuleMatchType.FALLBACK_NO_PATTERN,
        historical_probability_at_entry=Decimal("0.85"),
        samples_at_entry=347,
    )


def test_settle_due_positions_uses_binance_fallback(tmp_path):
    """When Gamma has dropped the market and the grace period has
    elapsed, the position must be settled via Binance close price.
    """
    settings = _settings(tmp_path)
    storage = _storage(tmp_path)
    broker = _broker()
    pos = _open_position("btc-updown-15m-1780733700")
    storage.upsert_position(pos)
    broker._open[(pos.market_slug, pos.selected_side)] = pos

    runner = Runner(
        settings=settings,
        storage=storage,
        rules=_rules(),
        broker=broker,
        risk=_risk(settings),
        slug="btc-updown-15m-1780737300",  # 11:35:00 (well past 08:30:00 window end)
        timeframe=Timeframe.M15,
    )

    # Pretend Gamma has dropped the old market.
    # Pretend Binance has data: final close = 60100 (UP wins, since
    # round_open=60000 and DOWN position should lose).
    with (
        patch(
            "polymarket_round_bot.runner.discover_market",
            side_effect=DiscoveryError("no market found"),
        ),
        patch(
            "polymarket_round_bot.runner.fetch_5m_close_at",
            return_value=Decimal("60100"),
        ),
    ):
        runner._settle_due_positions()

    # Position should be settled.
    final_pos = storage.get_position(pos.position_id)
    assert final_pos is not None
    assert final_pos.status == PositionStatus.SETTLED
    # DOWN position with final_btc=60100 > round_open=60000 → LOST.
    settlements = storage.list_settlements()
    assert len(settlements) == 1
    s = settlements[0]
    assert s.won is False
    assert s.settlement_source.value == "BINANCE_FALLBACK"
    assert s.final_btc_price == Decimal("60100")


def test_settle_due_positions_does_not_settle_within_grace(tmp_path):
    """Within the grace period, even if Gamma has dropped the market,
    we must NOT settle (the window has not fully ended).
    """
    settings = Settings(
        database_path=str(tmp_path / "test.sqlite"),
        state_rules_path="config/btc_updown_state_rules_15m.json",
        binance_fallback_grace_seconds=600,  # 10 min grace
    )
    storage = _storage(tmp_path)
    broker = _broker()
    # Use a far-future window so the current time is always < win_end + grace.
    pos = _open_position("btc-updown-15m-2835000000")  # year ~2059
    storage.upsert_position(pos)
    broker._open[(pos.market_slug, pos.selected_side)] = pos

    runner = Runner(
        settings=settings,
        storage=storage,
        rules=_rules(),
        broker=broker,
        risk=_risk(settings),
        slug="btc-updown-15m-2835000000",
        timeframe=Timeframe.M15,
    )

    with patch(
        "polymarket_round_bot.runner.discover_market",
        side_effect=DiscoveryError("no market found"),
    ):
        runner._settle_due_positions()

    # Position should still be OPEN (window in the future).
    final_pos = storage.get_position(pos.position_id)
    assert final_pos is not None
    assert final_pos.status == PositionStatus.OPEN
    assert len(storage.list_settlements()) == 0


def test_settle_due_positions_includes_storage_loaded_positions(tmp_path):
    """Positions persisted from a previous run (not in the in-memory
    broker) must also be eligible for settlement. This is the
    restart-recovery path.
    """
    settings = _settings(tmp_path)
    storage = _storage(tmp_path)
    # Pre-populate storage with an open position; broker is empty.
    pos = _open_position("btc-updown-15m-1780733700")
    storage.upsert_position(pos)
    broker = _broker()  # empty
    assert broker.open_positions == []

    runner = Runner(
        settings=settings,
        storage=storage,
        rules=_rules(),
        broker=broker,
        risk=_risk(settings),
        slug="btc-updown-15m-1780737300",
        timeframe=Timeframe.M15,
    )

    with (
        patch(
            "polymarket_round_bot.runner.discover_market",
            side_effect=DiscoveryError("no market found"),
        ),
        patch(
            "polymarket_round_bot.runner.fetch_5m_close_at",
            return_value=Decimal("60100"),
        ),
    ):
        runner._settle_due_positions()

    final_pos = storage.get_position(pos.position_id)
    assert final_pos is not None
    assert final_pos.status == PositionStatus.SETTLED


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
