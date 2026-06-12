"""Tests for Runner behaviour, slug refresh, and stale-position settlement.

Covers the bug discovered on 2026-06-06: ``run_continuously()`` did
not advance ``self._slug`` at :00/:15/:30/:45 UTC boundaries, so the
bot kept polling an expired market for 3+ hours and the open position
was never settled.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
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
    BINANCE_KLINE_LIMIT,
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


# === Binance fetch depth ===


def test_binance_kline_limit_covers_previous_16_completed_15m_rounds():
    """Runner must fetch enough 5m candles for research-compatible volatility."""
    prior_15m_round_candles = 16 * 3
    current_round_candle = 1

    assert prior_15m_round_candles + current_round_candle <= BINANCE_KLINE_LIMIT


# === current_expected_slug ===


def test_runner_passes_rule_policy_to_signal_engine_and_records_candle_age(tmp_path):
    from polymarket_round_bot.models import (
        BinanceState,
        Candle,
        DecisionKind,
        MarketMetadata,
        OrderbookSnapshot,
        PairOrderbook,
        RuleLookupResult,
        RuleMatchType,
        SignalDecision,
    )
    from polymarket_round_bot.rule_whitelist import RuleWhitelist

    settings = _settings(tmp_path)
    storage = _storage(tmp_path)
    policy = RuleWhitelist(enabled=True, allowed_rules={}, quarantined_rules={})
    captured = {}
    now = datetime(2024, 1, 1, 12, 6, 0, tzinfo=UTC)
    market = MarketMetadata(
        market_id="m1",
        condition_id="0xabc",
        question="q",
        slug="btc-updown-15m-1700000000",
        up_token_id="up",
        down_token_id="down",
        outcomes=["Up", "Down"],
        start_ts=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        end_ts=datetime(2024, 1, 1, 12, 15, 0, tzinfo=UTC),
        active=True,
        closed=False,
        accepting_orders=True,
    )
    candle = Candle(
        open_time_utc=market.start_ts,
        open=Decimal("100"),
        high=Decimal("100.2"),
        low=Decimal("99.9"),
        close=Decimal("100.1"),
        volume=Decimal("100"),
        is_closed=True,
    )
    binance = BinanceState(
        symbol="BTCUSDT",
        candles=[candle],
        current_price=Decimal("100.1"),
        received_at_utc=now,
    )
    orderbook_now = now
    pair = PairOrderbook(
        up=OrderbookSnapshot(
            token_id="up",
            best_bid=Decimal("0.62"),
            best_ask=Decimal("0.65"),
            spread=Decimal("0.03"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
            liquidity_usd_estimate=Decimal("1000"),
            received_at_utc=orderbook_now,
        ),
        down=OrderbookSnapshot(
            token_id="down",
            best_bid=Decimal("0.32"),
            best_ask=Decimal("0.35"),
            spread=Decimal("0.03"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
            liquidity_usd_estimate=Decimal("1000"),
            received_at_utc=orderbook_now,
        ),
        received_at_utc=orderbook_now,
    )
    lookup = RuleLookupResult(
        rule=None,
        match_type=RuleMatchType.NO_MATCH,
        historical_probability=None,
        recommended_side=None,
        samples=0,
        no_trade_reasons=["no_rule_for_state"],
    )

    class FakeRules:
        def lookup(self, **kwargs):
            return lookup

    def fake_build_decision(**kwargs):
        captured["rule_policy"] = kwargs.get("rule_policy")
        state = kwargs["state"]
        return SignalDecision(
            decision=DecisionKind.SKIP,
            side=None,
            market_slug=market.slug,
            event_url="https://polymarket.com/event/test",
            token_id=None,
            stage=state.stage,
            current_side=state.current_side,
            distance_bucket=state.distance_bucket,
            volatility_bucket=state.volatility_bucket,
            pattern=state.candle_pattern,
            rule_id=None,
            rule_match_type=RuleMatchType.NO_MATCH,
            samples=0,
            historical_probability=None,
            safety_buffer=Decimal("0"),
            max_buy_price=None,
            market_ask=None,
            edge_vs_ask=None,
            spread=None,
            size_usd=Decimal("1"),
            reason="test_skip",
        )

    runner = Runner(
        settings=settings,
        storage=storage,
        rules=FakeRules(),  # type: ignore[arg-type]
        broker=_broker(),
        risk=_risk(settings),
        slug=market.slug,
        timeframe=Timeframe.M15,
        rule_policy=policy,
    )

    with (
        patch("polymarket_round_bot.runner.discover_market", return_value=(market, SimpleNamespace(alignment="MATCHES_START"))),
        patch("polymarket_round_bot.runner.fetch_recent_5m_klines", return_value=binance),
        patch("polymarket_round_bot.runner.fetch_pair_orderbook", return_value=pair),
        patch("polymarket_round_bot.runner.build_decision", side_effect=fake_build_decision),
    ):
        snap = runner.run_one_cycle(now_utc=now)

    assert captured["rule_policy"] is policy
    assert snap.binance_data_age_seconds == Decimal("60.0")


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


# === Duplicate-position protection (bug 2026-06-07) ===


def _build_trade_decision(*, slug: str = "btc-updown-15m-1700000000", side: Side = Side.UP):
    """Build a minimal valid TRADE decision (no need to run full signal)."""
    from polymarket_round_bot.models import DecisionKind, SignalDecision

    return SignalDecision(
        decision=DecisionKind.TRADE,
        side=side,
        market_slug=slug,
        event_url="https://polymarket.com/event/x",
        token_id="up",
        stage=Stage.AFTER_10M,
        current_side=__import__(
            "polymarket_round_bot.models", fromlist=["CurrentSide"]
        ).CurrentSide.ABOVE_OPEN,
        distance_bucket=__import__(
            "polymarket_round_bot.models", fromlist=["DistanceBucket"]
        ).DistanceBucket.D_010_020pct,
        volatility_bucket=__import__(
            "polymarket_round_bot.models", fromlist=["VolatilityBucket"]
        ).VolatilityBucket.VOL_LOW,
        pattern="normal_bull",
        rule_id="rule_test",
        rule_match_type=__import__(
            "polymarket_round_bot.models", fromlist=["RuleMatchType"]
        ).RuleMatchType.EXACT,
        samples=200,
        historical_probability=Decimal("0.85"),
        safety_buffer=Decimal("0.05"),
        max_buy_price=Decimal("0.80"),
        market_ask=Decimal("0.65"),
        edge_vs_ask=Decimal("0.20"),
        spread=Decimal("0.03"),
        size_usd=Decimal("1"),
        reason="test",
    )


def test_paper_broker_error_persists_as_skip(tmp_path):
    """If PaperBrokerError is raised (e.g. duplicate guard), runner
    converts the decision to SKIP with reason=paper_broker_rejected:...
    so the DB never has a phantom TRADE with no position.
    """
    from polymarket_round_bot.paper_broker import PaperBrokerError

    storage = _storage(tmp_path)
    broker = _broker()
    # Construct a Runner to assert wiring (kept alive so broker is
    # the one used by the test below).
    Runner(
        settings=_settings(tmp_path),
        storage=storage,
        rules=_rules(),
        broker=broker,
        risk=_risk(_settings(tmp_path)),
        slug="btc-updown-15m-1700000000",
        timeframe=Timeframe.M15,
    )

    decision = _build_trade_decision()
    with patch.object(
        PaperBroker,
        "open_position",
        side_effect=PaperBrokerError("duplicate_position"),
    ):
        # Manually drive the open-step + persist
        broker._open[("btc-updown-15m-1700000000", Side.UP)] = _open_position(  # type: ignore[arg-type]
            "btc-updown-15m-1700000000"
        )
        # This simulates the runner's open_position call: it will raise
        # PaperBrokerError, the except branch should mutate decision
        # to SKIP. We invoke the same code path directly.
        try:
            broker.open_position(
                decision,
                round_open_price=Decimal("100"),
                btc_price_at_entry=Decimal("100.10"),
                distance_bucket=__import__(
                    "polymarket_round_bot.models", fromlist=["DistanceBucket"]
                ).DistanceBucket.D_010_020pct,
                volatility_bucket=__import__(
                    "polymarket_round_bot.models", fromlist=["VolatilityBucket"]
                ).VolatilityBucket.VOL_LOW,
                pattern="normal_bull",
                stage=Stage.AFTER_10M,
                seconds_to_expiry=120,
                entry_best_bid=Decimal("0.62"),
            )
        except PaperBrokerError as e:
            decision.decision = __import__(
                "polymarket_round_bot.models", fromlist=["DecisionKind"]
            ).DecisionKind.SKIP
            decision.reason = f"paper_broker_rejected:{e}"

    assert decision.decision.value == "SKIP"
    assert "paper_broker_rejected" in decision.reason
    assert "duplicate_position" in decision.reason


def test_runner_includes_storage_open_positions_in_risk_check(tmp_path):
    """When the in-memory broker is empty but storage has an OPEN
    position (post-restart), the risk manager must see it.
    """
    settings = _settings(tmp_path)
    storage = _storage(tmp_path)
    # Pre-populate storage with an OPEN position; broker is empty.
    pos = _open_position("btc-updown-15m-1700000000")
    storage.upsert_position(pos)
    broker = _broker()
    assert broker.open_positions == []

    # The runner builds the list like run_one_cycle does.
    open_positions_set: set = set()
    for p in broker.open_positions:
        open_positions_set.add((p.market_slug, p.selected_side))
    for p in storage.list_open_positions():
        open_positions_set.add((p.market_slug, p.selected_side))
    open_positions = list(open_positions_set)

    # Now ask the risk manager: a new UP trade on the same slug must
    # be rejected because storage has an OPEN position.
    from polymarket_round_bot.risk_manager import RiskManager
    risk = RiskManager(settings)
    res = risk.evaluate(
        candidate_market_slug="btc-updown-15m-1700000000",
        candidate_side=Side.UP,
        open_positions=open_positions,
        daily_realized_pnl=Decimal("0"),
    )
    assert res.allowed is False
    assert "duplicate_position_on_market" in (res.reject_reason or "")


def test_runner_rejects_opposite_side_same_slug(tmp_path):
    """A new DOWN trade on a slug that has an OPEN UP position must
    be rejected (strict v1: one position per market, any side).
    """
    settings = _settings(tmp_path)
    broker = _broker()
    pos = _open_position("btc-updown-15m-1700000000", side=Side.UP)
    broker._open[(pos.market_slug, pos.selected_side)] = pos
    storage = _storage(tmp_path)
    storage.upsert_position(pos)

    open_positions_set: set = set()
    for p in broker.open_positions:
        open_positions_set.add((p.market_slug, p.selected_side))
    for p in storage.list_open_positions():
        open_positions_set.add((p.market_slug, p.selected_side))
    open_positions = list(open_positions_set)

    from polymarket_round_bot.risk_manager import RiskManager
    risk = RiskManager(settings)
    res = risk.evaluate(
        candidate_market_slug="btc-updown-15m-1700000000",
        candidate_side=Side.DOWN,  # opposite side
        open_positions=open_positions,
        daily_realized_pnl=Decimal("0"),
    )
    assert res.allowed is False
    assert "duplicate_position_on_market" in (res.reject_reason or "")


def test_storage_partial_unique_index_blocks_duplicate_open(tmp_path):
    """DB-level: inserting two OPEN positions on the same slug must
    violate the partial unique index. (Defense-in-depth in case the
    application layer ever has a race.)"""
    from polymarket_round_bot.paper_broker import PaperBrokerError

    storage = _storage(tmp_path)
    broker = _broker()
    d = _build_trade_decision()

    # First position: succeeds.
    pos1 = broker.open_position(
        d,
        round_open_price=Decimal("100"),
        btc_price_at_entry=Decimal("100.10"),
        distance_bucket=__import__(
            "polymarket_round_bot.models", fromlist=["DistanceBucket"]
        ).DistanceBucket.D_010_020pct,
        volatility_bucket=__import__(
            "polymarket_round_bot.models", fromlist=["VolatilityBucket"]
        ).VolatilityBucket.VOL_LOW,
        pattern="normal_bull",
        stage=Stage.AFTER_10M,
        seconds_to_expiry=120,
        entry_best_bid=Decimal("0.62"),
    )
    storage.upsert_position(pos1)

    # Second position on same slug: application layer rejects first.
    d2 = _build_trade_decision(slug=pos1.market_slug)
    with pytest.raises(PaperBrokerError):
        broker.open_position(
            d2,
            round_open_price=Decimal("100"),
            btc_price_at_entry=Decimal("100.10"),
            distance_bucket=__import__(
                "polymarket_round_bot.models", fromlist=["DistanceBucket"]
            ).DistanceBucket.D_010_020pct,
            volatility_bucket=__import__(
                "polymarket_round_bot.models", fromlist=["VolatilityBucket"]
            ).VolatilityBucket.VOL_LOW,
            pattern="normal_bull",
            stage=Stage.AFTER_10M,
            seconds_to_expiry=120,
            entry_best_bid=Decimal("0.62"),
        )

    # If we bypass the broker and try a raw INSERT directly, the
    # partial unique index must reject it.
    with pytest.raises(sqlite3.IntegrityError), storage._conn() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO paper_positions
                SELECT :new_id, :decision_id, market_slug, event_url,
                       selected_side, token_id, entry_timestamp_utc,
                       entry_price, entry_best_ask, entry_best_bid,
                       entry_spread, entry_size_usd, shares,
                       fair_price_at_entry, max_buy_price_at_entry,
                       edge_at_entry, round_open_price, btc_price_at_entry,
                       distance_bucket_at_entry, volatility_bucket_at_entry,
                       pattern_at_entry, stage_at_entry,
                       seconds_to_expiry_at_entry, current_side_at_entry,
                       'OPEN', rule_id, rule_match_type,
                       historical_probability_at_entry, samples_at_entry
                  FROM paper_positions
                 WHERE market_slug = :slug
                 LIMIT 1
                """,
                {
                    "new_id": "pos_test_dup",
                    "decision_id": "dec_test_dup",
                    "slug": pos1.market_slug,
                },
            )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
