"""Tests for round state computation."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from polymarket_round_bot.models import (
    BinanceState,
    Candle,
    CurrentSide,
    DistanceBucket,
    Stage,
    VolatilityBucket,
)
from polymarket_round_bot.round_state import build_round_state


def _mk_candle(open_time_utc, o, h, lo, c):
    return Candle(
        open_time_utc=open_time_utc,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=Decimal("100"),
        is_closed=True,
    )


def test_at_open_side_when_distance_very_small():
    # round_open=100, current=100.002 -> distance 0.00002 < 0.00005 (0.5 bps)
    market_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    market = _market_5m(market_start)
    binance = _binance_with_open_and_current(
        round_open=Decimal("100"),
        current_price=Decimal("100.002"),
        market_start=market_start,
    )
    state = build_round_state(binance, market, now_utc=market_start.replace())
    assert state.current_side in (CurrentSide.AT_OPEN, CurrentSide.ABOVE_OPEN)


def test_above_open_when_current_above():
    market_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    market = _market_5m(market_start)
    binance = _binance_with_open_and_current(
        round_open=Decimal("100"),
        current_price=Decimal("100.10"),  # +0.1%
        market_start=market_start,
    )
    state = build_round_state(binance, market, now_utc=market_start.replace())
    assert state.current_side == CurrentSide.ABOVE_OPEN


def test_below_open_when_current_below():
    market_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    market = _market_5m(market_start)
    binance = _binance_with_open_and_current(
        round_open=Decimal("100"),
        current_price=Decimal("99.85"),  # -0.15%
        market_start=market_start,
    )
    state = build_round_state(binance, market, now_utc=market_start.replace())
    assert state.current_side == CurrentSide.BELOW_OPEN


def test_distance_bucket_assignment():
    """Each test case explicitly sets round_open=100, current=price."""
    market_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    market = _market_5m(market_start)

    cases = [
        (Decimal("100.020"), DistanceBucket.D_0_005pct),    # 0.02%
        (Decimal("100.070"), DistanceBucket.D_005_010pct),  # 0.07%
        (Decimal("100.150"), DistanceBucket.D_010_020pct),  # 0.15%
        (Decimal("100.250"), DistanceBucket.D_020_035pct),  # 0.25%
        (Decimal("100.450"), DistanceBucket.D_035_050pct),  # 0.45%
        (Decimal("100.800"), DistanceBucket.D_GT_050pct),   # 0.80%
    ]
    for price, expected in cases:
        binance = _binance_with_open_and_current(
            round_open=Decimal("100"),
            current_price=price,
            market_start=market_start,
        )
        state = build_round_state(binance, market, now_utc=market_start.replace())
        assert state.distance_bucket == expected, (
            f"price={price} expected={expected} got={state.distance_bucket}"
        )


def test_volatility_bucket_insufficient_data_returns_unknown():
    market_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    market = _market_5m(market_start)
    # Only 1 prior candle
    binance = BinanceState(
        symbol="BTCUSDT",
        candles=[_mk_candle(market_start, 100, 100, 100, 100)],
        current_price=Decimal("100.01"),
        received_at_utc=datetime.now(UTC),
    )
    state = build_round_state(binance, market, now_utc=market_start.replace())
    assert state.volatility_bucket == VolatilityBucket.VOL_UNKNOWN


def test_15m_after_5m_state():
    """At 19:03, only c0 (19:00 candle) is closed."""
    start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    market = _market_15m(start)
    # Closed c0 (12:00-12:05), no c1, no c2
    c0 = _mk_candle(start, 100, 100.2, 99.9, 100.15)  # bull
    binance = BinanceState(
        symbol="BTCUSDT",
        candles=[c0],
        current_price=Decimal("100.20"),
        received_at_utc=datetime.now(UTC),
    )
    state = build_round_state(
        binance, market, now_utc=start.replace()  # 12:00
    )
    assert state.stage == Stage.AFTER_5M
    assert state.c0 == c0
    assert state.c1 is None
    assert state.candle_pattern != "no_closed_candle_yet"


def test_15m_after_10m_state_uses_combo_pattern():
    """At 19:08, c0 and c1 are both closed -> AFTER_10M with combo pattern."""
    from datetime import timedelta

    start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    market = _market_15m(start)
    c0 = _mk_candle(start, 100, 100.2, 99.9, 100.15)  # bull
    c1 = _mk_candle(start + timedelta(minutes=5), 100.15, 100.5, 100.1, 100.30)  # bull
    binance = BinanceState(
        symbol="BTCUSDT",
        candles=[c0, c1],
        current_price=Decimal("100.35"),
        received_at_utc=datetime.now(UTC),
    )
    state = build_round_state(
        binance, market, now_utc=start + timedelta(minutes=8)
    )
    assert state.stage == Stage.AFTER_10M
    assert "->" in state.candle_pattern
    assert state.pattern_combo == state.candle_pattern


def test_5m_market_uses_custom_state():
    start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    market = _market_5m(start)
    c0 = _mk_candle(start, 100, 100.2, 99.9, 100.10)
    binance = BinanceState(
        symbol="BTCUSDT",
        candles=[c0],
        current_price=Decimal("100.10"),
        received_at_utc=datetime.now(UTC),
    )
    state = build_round_state(binance, market, now_utc=start.replace())
    assert state.stage == Stage.CUSTOM_5M_STATE
    assert state.candle_pattern == "no_internal_candles"


def _market_5m(start: datetime):
    from polymarket_round_bot.models import MarketMetadata

    return MarketMetadata(
        market_id="m1",
        condition_id="0xabc",
        question="q",
        slug="btc-updown-5m-1700000000",
        up_token_id="up",
        down_token_id="down",
        outcomes=["Up", "Down"],
        start_ts=start,
        end_ts=start.replace() + __import__("datetime").timedelta(minutes=5),
        active=True,
        closed=False,
        accepting_orders=True,
    )


def _market_15m(start: datetime):
    from datetime import timedelta

    from polymarket_round_bot.models import MarketMetadata

    return MarketMetadata(
        market_id="m2",
        condition_id="0xdef",
        question="q",
        slug="btc-updown-15m-1700000000",
        up_token_id="up",
        down_token_id="down",
        outcomes=["Up", "Down"],
        start_ts=start,
        end_ts=start + timedelta(minutes=15),
        active=True,
        closed=False,
        accepting_orders=True,
    )


def _binance_with_open_and_current(
    *, round_open: Decimal, current_price: Decimal, market_start: datetime
) -> BinanceState:
    """Build a Binance state where the in-round candle's open = round_open
    and current_price is whatever we want."""
    from datetime import timedelta

    candles = []
    # 19 prior candles leading up to the round, all closed
    p = float(round_open) - 0.5
    for i in range(19):
        t = market_start - timedelta(minutes=5 * (19 - i))
        o = p
        c = p + 0.025
        candles.append(_mk_candle(t, o, max(o, c) + 0.01, min(o, c) - 0.01, c))
        p = c
    # In-round candle at market_start with open=round_open, close=current_price
    candles.append(
        _mk_candle(
            market_start,
            float(round_open),
            float(max(round_open, current_price)) + 0.01,
            float(min(round_open, current_price)) - 0.01,
            float(current_price),
        )
    )
    return BinanceState(
        symbol="BTCUSDT",
        candles=candles,
        current_price=current_price,
        received_at_utc=datetime.now(UTC),
    )
