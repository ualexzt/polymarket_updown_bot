"""Tests for signal engine TRADE/SKIP decisions."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from polymarket_round_bot.config import Settings
from polymarket_round_bot.models import (
    CurrentSide,
    DecisionKind,
    DistanceBucket,
    PairOrderbook,
    RuleLookupResult,
    RuleMatchType,
    Side,
    Stage,
    VolatilityBucket,
)
from polymarket_round_bot.signal_engine import build_decision


def _state(*, stage: Stage = Stage.AFTER_10M, pattern: str = "normal_bull -> strong_bull_close_near_high", seconds_to_expiry: int = 120, timeframe_override=None):
    from datetime import datetime

    from polymarket_round_bot.models import Candle, RoundState, Timeframe

    c0 = Candle(
        open_time_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("100.2"),
        low=Decimal("99.9"),
        close=Decimal("100.10"),
        volume=Decimal("100"),
        is_closed=True,
    )
    return RoundState(
        timeframe=timeframe_override or Timeframe.M15,
        stage=stage,
        round_open_price=Decimal("100"),
        round_close_price=None,
        current_btc_price=Decimal("100.10"),
        current_side=CurrentSide.ABOVE_OPEN,
        distance_pct=Decimal("0.001"),
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        prev_16_abs_return_mean=Decimal("0.0005"),
        candle_pattern=pattern,
        pattern_combo=pattern if "->" in pattern else None,
        seconds_to_expiry=seconds_to_expiry,
        c0=c0,
        c1=None,
        c2=None,
    )


def _market():
    from polymarket_round_bot.models import MarketMetadata

    return MarketMetadata(
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


def _orderbook(*, ask: Decimal = Decimal("0.65"), bid: Decimal = Decimal("0.62"), ask_size: Decimal = Decimal("1000"), liquidity: Decimal = Decimal("1000")):
    from polymarket_round_bot.models import OrderbookSnapshot

    now = datetime.now(UTC)
    up = OrderbookSnapshot(
        token_id="up",
        best_bid=bid,
        best_ask=ask,
        spread=ask - bid,
        bid_size=ask_size,
        ask_size=ask_size,
        liquidity_usd_estimate=liquidity,
        received_at_utc=now,
    )
    down = OrderbookSnapshot(
        token_id="down",
        best_bid=Decimal("0.32"),
        best_ask=Decimal("0.35"),
        spread=Decimal("0.03"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        liquidity_usd_estimate=Decimal("500"),
        received_at_utc=now,
    )
    return PairOrderbook(up=up, down=down, received_at_utc=now)


def _lookup(*, prob: Decimal = Decimal("0.85"), samples: int = 200, side: Side = Side.UP, no_trade: list[str] | None = None, return_aligned: bool = True, _match_type: RuleMatchType = RuleMatchType.EXACT):
    return RuleLookupResult(
        rule=None,
        match_type=_match_type,
        historical_probability=prob,
        recommended_side=side,
        samples=samples,
        no_trade_reasons=no_trade or [],
    )


def test_trade_when_ask_leq_max_buy_price():
    s = Settings()  # safety_buffer=0.05, min_edge=0.05
    # fair=0.85, safety=0.05 -> max_buy=0.80, ask=0.65 -> edge=0.20 -> TRADE
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.65"), bid=Decimal("0.62")),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.TRADE
    assert decision.side == Side.UP
    assert decision.max_buy_price == Decimal("0.80")
    assert decision.edge_vs_ask == Decimal("0.20")


def test_skip_when_ask_above_max_buy_price():
    s = Settings()
    # fair=0.85, safety=0.05 -> max_buy=0.80, ask=0.85 -> edge=0 -> SKIP
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.85")),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert decision.reason == "ask_above_max_buy_price"


def test_skip_when_edge_below_min_5c():
    """MIN_EDGE is an independent gate: with safety_buffer=0.04 but
    min_edge=0.05, an ask of 0.79 against fair 0.83 has edge=0.04
    which passes max_buy (= 0.79) but fails min_edge (= 0.05).

    Note: in production v1 both safety_buffer and min_edge are 0.05,
    so the two checks collapse to one and edge_below_min is never
    the binding constraint. This test pins down that the independent
    gate still works when they differ.
    """
    s = Settings(safety_buffer=Decimal("0.04"), min_edge=Decimal("0.05"))
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.79"), bid=Decimal("0.76")),
        lookup=_lookup(prob=Decimal("0.83")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert "edge_below_min" in decision.reason


def test_v1_collapse_max_buy_and_min_edge_when_equal():
    """At strict v1 (safety=min_edge=0.05), an ask of 0.80 with fair
    0.85 produces max_buy=0.80 and edge=0.05, both exactly at
    threshold. Must TRADE. Confirms the two checks collapse to one
    condition without leaving a tiny dead zone.
    """
    s = Settings()  # both = 0.05
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.80"), bid=Decimal("0.77")),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.TRADE
    assert decision.max_buy_price == Decimal("0.80")
    assert decision.edge_vs_ask == Decimal("0.05")


def test_skip_when_ask_above_max_entry_ask():
    """Absolute cap: even with great edge, an ask above max_entry_ask
    (default 0.80) is forbidden. Protects against overfit high-prob
    rules where 0.85+ asks are tempting but break-even WR is too
    high to actually achieve live.
    """
    s = Settings()  # max_entry_ask=0.80
    # fair=0.95, ask=0.85 -> max_buy=0.90 passes, edge=0.10 passes
    # but ask > max_entry_ask=0.80 -> SKIP
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.85"), bid=Decimal("0.82")),
        lookup=_lookup(prob=Decimal("0.95")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert "ask_above_max_entry_ask" in decision.reason


def test_trade_at_max_entry_ask_boundary():
    """Sanity: ask exactly at max_entry_ask is allowed (<=)."""
    s = Settings()  # max_entry_ask=0.80
    # fair=0.85, ask=0.80 -> max_buy=0.80, ask==max_buy, edge=0.05
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.80"), bid=Decimal("0.77")),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.TRADE
    assert decision.max_buy_price == Decimal("0.80")


def test_skip_when_spread_too_wide():
    s = Settings()  # max_spread=0.03
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.70"), bid=Decimal("0.60")),  # spread=0.10
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert "spread_too_wide" in decision.reason


def test_skip_when_liquidity_too_low():
    s = Settings()  # min_liquidity_usd=25
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(liquidity=Decimal("10")),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert "liquidity_too_low" in decision.reason


def test_skip_when_market_inactive():
    s = Settings()
    m = _market()
    object.__setattr__(m, "active", False)
    decision = build_decision(
        settings=s,
        state=_state(),
        market=m,
        orderbook=_orderbook(),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert decision.reason == "market_not_active"


def test_skip_when_data_stale():
    """Orderbook 60s old is stale (POLY_ORDERBOOK_MAX_AGE_SECONDS=5)."""
    from datetime import timedelta

    from polymarket_round_bot.models import OrderbookSnapshot

    s = Settings()
    now = datetime.now(UTC)
    old_ob_time = now - timedelta(seconds=60)
    up = OrderbookSnapshot(
        token_id="up",
        best_bid=Decimal("0.62"),
        best_ask=Decimal("0.65"),
        spread=Decimal("0.03"),
        bid_size=Decimal("1000"),
        ask_size=Decimal("1000"),
        liquidity_usd_estimate=Decimal("1000"),
        received_at_utc=old_ob_time,
    )
    down = OrderbookSnapshot(
        token_id="down",
        best_bid=Decimal("0.32"),
        best_ask=Decimal("0.35"),
        spread=Decimal("0.03"),
        bid_size=Decimal("1000"),
        ask_size=Decimal("1000"),
        liquidity_usd_estimate=Decimal("1000"),
        received_at_utc=old_ob_time,
    )
    ob = PairOrderbook(up=up, down=down, received_at_utc=old_ob_time)
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=ob,
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=now,
        binance_received_at_utc=now,
    )
    assert decision.decision == DecisionKind.SKIP
    assert decision.reason == "stale_orderbook"


def test_skip_when_rule_filtered():
    s = Settings()
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(),
        lookup=_lookup(prob=Decimal("0.85"), no_trade=["samples_below_threshold:10<60"]),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert "rule_filtered" in decision.reason


def test_skip_when_risk_rejected():
    s = Settings()
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=False,
        risk_reject_reason="max_open_positions_reached:1>=1",
        open_positions_count=1,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert "risk_rejected" in decision.reason


# === Audit-fix gates (2026-06-06) ===


def test_skip_when_binance_stale():
    """Binance data older than BINANCE_PRICE_MAX_AGE_SECONDS=10 must SKIP."""
    from datetime import timedelta

    s = Settings()
    now = datetime.now(UTC)
    old_bn_time = now - timedelta(seconds=60)
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=now,
        binance_received_at_utc=old_bn_time,
    )
    assert decision.decision == DecisionKind.SKIP
    assert decision.reason == "stale_binance_data"


def test_skip_when_fallback_match_and_disallowed():
    """FALLBACK_NO_PATTERN match with allow_fallback_trading=False must SKIP."""
    s = Settings(allow_fallback_trading=False)
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(),
        lookup=_lookup(prob=Decimal("0.85"), _match_type=RuleMatchType.FALLBACK_NO_PATTERN),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert decision.reason == "fallback_rule_not_tradeable_in_v1"


def test_trade_on_fallback_match_when_explicitly_allowed():
    """FALLBACK match with allow_fallback_trading=True can TRADE."""
    s = Settings(allow_fallback_trading=True)
    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(),
        lookup=_lookup(prob=Decimal("0.85"), _match_type=RuleMatchType.FALLBACK_NO_PATTERN),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.TRADE


def test_skip_when_seconds_to_expiry_too_early_after_5m():
    """AFTER_5M with sec_to_expiry < min (300) must SKIP."""
    s = Settings()
    decision = build_decision(
        settings=s,
        state=_state(stage=Stage.AFTER_5M, pattern="strong_bull_close_near_high", seconds_to_expiry=100),
        market=_market(),
        orderbook=_orderbook(),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert "seconds_to_expiry_out_of_range" in decision.reason
    assert "100" in decision.reason


def test_skip_when_seconds_to_expiry_too_late_after_10m():
    """AFTER_10M with sec_to_expiry > max (300) must SKIP."""
    s = Settings()
    decision = build_decision(
        settings=s,
        state=_state(stage=Stage.AFTER_10M, seconds_to_expiry=500),
        market=_market(),
        orderbook=_orderbook(),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert "seconds_to_expiry_out_of_range" in decision.reason
    assert "500" in decision.reason


def test_trade_when_seconds_to_expiry_in_window():
    """AFTER_5M with sec_to_expiry=500 (in [300, 600]) and good ask -> TRADE."""
    s = Settings()
    decision = build_decision(
        settings=s,
        state=_state(stage=Stage.AFTER_5M, pattern="strong_bull_close_near_high", seconds_to_expiry=500),
        market=_market(),
        orderbook=_orderbook(),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.TRADE


def test_skip_5m_when_disallowed():
    """5m market with allow_5m_trading=False must SKIP with explicit reason."""
    from polymarket_round_bot.models import Timeframe

    s = Settings(allow_5m_trading=False)
    decision = build_decision(
        settings=s,
        state=_state(timeframe_override=Timeframe.M5),
        market=_market(),
        orderbook=_orderbook(),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime.now(UTC),
        binance_received_at_utc=datetime.now(UTC),
    )
    assert decision.decision == DecisionKind.SKIP
    assert decision.reason == "5m_trading_disabled_in_v1"

