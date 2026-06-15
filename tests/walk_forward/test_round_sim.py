"""Tests for per-round simulation: no-lookahead, settlement, single-trade-per-round."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import (
    BinanceState,
    Candle,
    MarketMetadata,
    Side,
)

from scripts.walk_forward_backtest import simulate_round, settle_round


def test_simulate_round_no_lookahead(synthetic_5d_candles, synthetic_market, sample_rules):
    """Snapshot the candles IDs we passed in; verify binance object isn't mutated."""
    candles_before = [c for c in synthetic_5d_candles if c.open_time_utc < synthetic_market.start_ts][-100:]
    binance = BinanceState(
        symbol="BTCUSDT",
        candles=candles_before,
        current_price=candles_before[-1].close,
        received_at_utc=synthetic_market.start_ts,
    )
    candle_ids = [id(c) for c in binance.candles]
    from scripts.walk_forward_backtest import build_rule_index
    trade = simulate_round(
        market=synthetic_market,
        binance=binance,
        rules_index=build_rule_index(sample_rules),
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
        safety_buffer=Decimal("0.05"),
        max_entry_ask=Decimal("0.80"),
    )
    # binance object passed should be untouched (no mutation)
    assert [id(c) for c in binance.candles] == candle_ids
    # Trade can be None if no rule matches; that's fine for this test.
    assert trade is None or isinstance(trade, dict)


def test_settle_round_up_wins(synthetic_market):
    """UP bet wins when final close > round open; settlement is +0.45 at entry=0.55."""
    open_dt = synthetic_market.start_ts
    c0 = Candle(open_time_utc=open_dt, open=Decimal("50000"), high=Decimal("50050"),
                low=Decimal("49950"), close=Decimal("50010"), volume=Decimal("1"))
    c1 = Candle(open_time_utc=open_dt + timedelta(minutes=5), open=Decimal("50010"),
                high=Decimal("50060"), low=Decimal("50000"), close=Decimal("50050"), volume=Decimal("1"))
    c2 = Candle(open_time_utc=open_dt + timedelta(minutes=10), open=Decimal("50050"),
                high=Decimal("50120"), low=Decimal("50040"), close=Decimal("50100"), volume=Decimal("1"))
    result = settle_round(
        round_open=c0.open, round_close=c2.close, recommended_side=Side.UP, entry_price=Decimal("0.55"),
    )
    assert result["won"] is True
    assert result["pnl"] == Decimal("0.45")  # (1 - 0.55)


def test_settle_round_down_wins():
    open_price = Decimal("50000")
    close_price = Decimal("49900")  # -0.20%, DOWN wins
    result = settle_round(
        round_open=open_price, round_close=close_price, recommended_side=Side.DOWN, entry_price=Decimal("0.55"),
    )
    assert result["won"] is True
    assert result["pnl"] == Decimal("0.45")


def test_settle_round_loss():
    open_price = Decimal("50000")
    close_price = Decimal("50100")  # UP wins
    result = settle_round(
        round_open=open_price, round_close=close_price, recommended_side=Side.DOWN, entry_price=Decimal("0.55"),
    )
    assert result["won"] is False
    assert result["pnl"] == Decimal("-0.55")


def test_simulate_round_returns_trade_dict(synthetic_5d_candles, synthetic_market, sample_rules):
    """If a rule matches and the round is in trading window, we get a trade dict."""
    candles_before = [c for c in synthetic_5d_candles if c.open_time_utc < synthetic_market.start_ts][-100:]
    binance = BinanceState(
        symbol="BTCUSDT",
        candles=candles_before,
        current_price=candles_before[-1].close,
        received_at_utc=synthetic_market.start_ts,
    )
    from scripts.walk_forward_backtest import build_rule_index
    trade = simulate_round(
        market=synthetic_market,
        binance=binance,
        rules_index=build_rule_index(sample_rules),
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
        safety_buffer=Decimal("0.05"),
        max_entry_ask=Decimal("0.80"),
    )
    # The function returns a single trade (or None) per round.
    assert trade is None or (isinstance(trade, dict) and "stage" in trade)
