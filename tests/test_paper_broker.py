"""Tests for paper broker (position lifecycle)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from polymarket_round_bot.models import (
    DecisionKind,
    DistanceBucket,
    PositionStatus,
    Side,
    Stage,
    VolatilityBucket,
)
from polymarket_round_bot.paper_broker import PaperBroker, PaperBrokerError


def _decision(*, side: Side = Side.UP, ask: Decimal = Decimal("0.65"), size: Decimal = Decimal("5")):
    from polymarket_round_bot.models import CurrentSide, RuleMatchType, SignalDecision

    return SignalDecision(
        decision=DecisionKind.TRADE,
        side=side,
        market_slug="btc-updown-15m-1700000000",
        event_url="https://polymarket.com/event/btc-updown-15m-1700000000",
        token_id="up_token",
        stage=Stage.AFTER_10M,
        current_side=CurrentSide.ABOVE_OPEN,
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="normal_bull",
        rule_id="r1",
        rule_match_type=RuleMatchType.EXACT,
        samples=100,
        historical_probability=Decimal("0.85"),
        fair_price=Decimal("0.85"),
        safety_buffer=Decimal("0.04"),
        max_buy_price=Decimal("0.81"),
        market_ask=ask,
        edge_vs_ask=Decimal("0.20"),
        spread=Decimal("0.02"),
        size_usd=size,
        reason="test",
    )


def test_creates_paper_order_at_best_ask():
    b = PaperBroker()
    d = _decision(ask=Decimal("0.65"), size=Decimal("5"))
    p = b.open_position(
        d,
        round_open_price=Decimal("100"),
        btc_price_at_entry=Decimal("100.10"),
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="normal_bull",
        stage=Stage.AFTER_10M,
        seconds_to_expiry=600,
        entry_best_bid=Decimal("0.62"),
    )
    # shares = 5 / 0.65 = 7.6923...
    assert p.shares == Decimal("5") / Decimal("0.65")
    assert p.entry_price == Decimal("0.65")
    assert p.entry_size_usd == Decimal("5")
    assert p.status == PositionStatus.OPEN
    assert p.selected_side == Side.UP


def test_computes_shares_correctly():
    b = PaperBroker()
    d = _decision(ask=Decimal("0.50"), size=Decimal("10"))
    p = b.open_position(
        d,
        round_open_price=Decimal("100"),
        btc_price_at_entry=Decimal("100.10"),
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="normal_bull",
        stage=Stage.AFTER_10M,
        seconds_to_expiry=600,
        entry_best_bid=Decimal("0.48"),
    )
    assert p.shares == Decimal("20")  # 10 / 0.5


def test_prevents_duplicate_position():
    b = PaperBroker()
    d = _decision(side=Side.UP, ask=Decimal("0.65"))
    b.open_position(
        d,
        round_open_price=Decimal("100"),
        btc_price_at_entry=Decimal("100.10"),
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="normal_bull",
        stage=Stage.AFTER_10M,
        seconds_to_expiry=600,
        entry_best_bid=Decimal("0.62"),
    )
    with pytest.raises(PaperBrokerError):
        b.open_position(
            d,
            round_open_price=Decimal("100"),
            btc_price_at_entry=Decimal("100.10"),
            distance_bucket=DistanceBucket.D_010_020pct,
            volatility_bucket=VolatilityBucket.VOL_LOW,
            pattern="normal_bull",
            stage=Stage.AFTER_10M,
            seconds_to_expiry=600,
            entry_best_bid=Decimal("0.62"),
        )


def test_tracks_open_position():
    b = PaperBroker()
    d = _decision()
    b.open_position(
        d,
        round_open_price=Decimal("100"),
        btc_price_at_entry=Decimal("100.10"),
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="normal_bull",
        stage=Stage.AFTER_10M,
        seconds_to_expiry=600,
        entry_best_bid=Decimal("0.62"),
    )
    assert b.open_count() == 1
    assert b.has_open("btc-updown-15m-1700000000", Side.UP) is True
    b.close_position("btc-updown-15m-1700000000", Side.UP)
    assert b.open_count() == 0
