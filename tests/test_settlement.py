"""Tests for settlement logic."""
from __future__ import annotations

from decimal import Decimal

import pytest

from polymarket_round_bot.models import (
    DistanceBucket,
    PaperPosition,
    PositionStatus,
    RuleMatchType,
    SettlementSource,
    Side,
    Stage,
    TradeQuality,
    VolatilityBucket,
)
from polymarket_round_bot.settlement import mark_position_settled, settle_position


def _position(*, side: Side = Side.UP, round_open: Decimal = Decimal("100"), entry_price: Decimal = Decimal("0.65"), size: Decimal = Decimal("5")):
    return PaperPosition(
        position_id="p1",
        decision_id="d1",
        market_slug="btc-updown-15m-1700000000",
        event_url="https://polymarket.com/event/btc-updown-15m-1700000000",
        selected_side=side,
        token_id="up_token",
        entry_timestamp_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        entry_price=entry_price,
        entry_best_ask=entry_price,
        entry_best_bid=entry_price - Decimal("0.02"),
        entry_spread=Decimal("0.02"),
        entry_size_usd=size,
        shares=size / entry_price,
        fair_price_at_entry=Decimal("0.85"),
        max_buy_price_at_entry=Decimal("0.81"),
        edge_at_entry=Decimal("0.20"),
        round_open_price=round_open,
        btc_price_at_entry=round_open + Decimal("0.10"),
        distance_bucket_at_entry=DistanceBucket.D_010_020pct,
        volatility_bucket_at_entry=VolatilityBucket.VOL_LOW,
        pattern_at_entry="normal_bull",
        stage_at_entry=Stage.AFTER_10M,
        seconds_to_expiry_at_entry=600,
        current_side_at_entry=__import__("polymarket_round_bot.models", fromlist=["CurrentSide"]).CurrentSide.ABOVE_OPEN,
        status=PositionStatus.OPEN,
        rule_id="r1",
        rule_match_type=RuleMatchType.EXACT,
        historical_probability_at_entry=Decimal("0.85"),
        samples_at_entry=100,
    )


def test_settles_winning_up():
    p = _position(side=Side.UP, round_open=Decimal("100"))
    # BTC went up: 100 -> 101
    s = settle_position(
        position=p,
        polymarket_resolved=Side.UP,
        final_btc_price=Decimal("101"),
        round_close_price=Decimal("101"),
    )
    assert s.won is True
    assert s.payout_usd == p.shares
    assert s.realized_pnl_usd == p.shares - p.entry_size_usd
    assert s.trade_quality == TradeQuality.GOOD_WIN
    assert s.settlement_source == SettlementSource.POLYMARKET_API


def test_settles_losing_up():
    p = _position(side=Side.UP, round_open=Decimal("100"))
    s = settle_position(
        position=p,
        polymarket_resolved=Side.DOWN,
        final_btc_price=Decimal("99"),
        round_close_price=Decimal("99"),
    )
    assert s.won is False
    assert s.payout_usd == Decimal("0")
    assert s.realized_pnl_usd == -p.entry_size_usd


def test_settles_winning_down():
    p = _position(side=Side.DOWN, round_open=Decimal("100"))
    s = settle_position(
        position=p,
        polymarket_resolved=Side.DOWN,
        final_btc_price=Decimal("99"),
        round_close_price=Decimal("99"),
    )
    assert s.won is True
    assert s.payout_usd == p.shares


def test_settles_losing_down():
    p = _position(side=Side.DOWN, round_open=Decimal("100"))
    s = settle_position(
        position=p,
        polymarket_resolved=Side.UP,
        final_btc_price=Decimal("101"),
        round_close_price=Decimal("101"),
    )
    assert s.won is False


def test_computes_payout():
    p = _position(side=Side.UP, entry_price=Decimal("0.50"), size=Decimal("10"))
    # shares = 10/0.5 = 20
    # UP won -> payout = 20
    s = settle_position(
        position=p,
        polymarket_resolved=Side.UP,
        final_btc_price=Decimal("101"),
        round_close_price=Decimal("101"),
    )
    assert s.shares == Decimal("20")
    assert s.payout_usd == Decimal("20")
    assert s.realized_pnl_usd == Decimal("20") - Decimal("10")


def test_computes_pnl_for_loss():
    p = _position(side=Side.UP, entry_price=Decimal("0.50"), size=Decimal("10"))
    s = settle_position(
        position=p,
        polymarket_resolved=Side.DOWN,
        final_btc_price=Decimal("99"),
        round_close_price=Decimal("99"),
    )
    # Lost -> payout 0, pnl = -cost = -10
    assert s.realized_pnl_usd == Decimal("-10")


def test_marks_settlement_source_polymarket_when_resolved():
    p = _position()
    s = settle_position(
        position=p,
        polymarket_resolved=Side.UP,
        final_btc_price=Decimal("101"),
        round_close_price=Decimal("101"),
    )
    assert s.settlement_source == SettlementSource.POLYMARKET_API


def test_marks_settlement_source_binance_when_unresolved():
    p = _position()
    s = settle_position(
        position=p,
        polymarket_resolved=None,
        final_btc_price=Decimal("101"),
        round_close_price=Decimal("101"),
    )
    assert s.settlement_source == SettlementSource.BINANCE_FALLBACK


def test_cannot_settle_non_open_position():
    p = _position()
    settled = mark_position_settled(p)
    with pytest.raises(ValueError):
        settle_position(
            position=settled,
            polymarket_resolved=Side.UP,
            final_btc_price=Decimal("101"),
            round_close_price=Decimal("101"),
        )
