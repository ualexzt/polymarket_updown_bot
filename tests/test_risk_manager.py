"""Tests for risk manager."""
from __future__ import annotations

from decimal import Decimal

from polymarket_round_bot.config import Settings
from polymarket_round_bot.models import Side
from polymarket_round_bot.risk_manager import RiskManager


def _settings(**overrides) -> Settings:
    s = Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_allows_valid_risk():
    s = _settings()
    rm = RiskManager(s)
    res = rm.evaluate(
        candidate_market_slug="m1",
        candidate_side=Side.UP,
        open_positions=[],
        daily_realized_pnl=Decimal("0"),
    )
    assert res.allowed is True


def test_rejects_when_max_open_reached():
    s = _settings(max_open_positions=1)
    rm = RiskManager(s)
    res = rm.evaluate(
        candidate_market_slug="m2",
        candidate_side=Side.UP,
        open_positions=[("m1", Side.UP)],
        daily_realized_pnl=Decimal("0"),
    )
    assert res.allowed is False
    assert "max_open_positions_reached" in res.reject_reason


def test_rejects_duplicate_position_on_same_market():
    s = _settings()
    rm = RiskManager(s)
    res = rm.evaluate(
        candidate_market_slug="m1",
        candidate_side=Side.UP,
        open_positions=[("m1", Side.UP)],
        daily_realized_pnl=Decimal("0"),
    )
    assert res.allowed is False
    assert "duplicate_position_on_market" in res.reject_reason


def test_rejects_daily_loss_exceeded():
    s = _settings(max_daily_loss_usd=Decimal("10"))
    rm = RiskManager(s)
    res = rm.evaluate(
        candidate_market_slug="m1",
        candidate_side=Side.UP,
        open_positions=[],
        daily_realized_pnl=Decimal("-10"),
    )
    assert res.allowed is False
    assert "daily_loss_exceeded" in res.reject_reason


def test_allows_opposite_side_same_market_when_capacity_available():
    """v1 policy: one position per (market, side). With capacity, opposite
    side on the same market is allowed."""
    s = _settings(max_open_positions=2)
    rm = RiskManager(s)
    res = rm.evaluate(
        candidate_market_slug="m1",
        candidate_side=Side.DOWN,
        open_positions=[("m1", Side.UP)],  # different side
        daily_realized_pnl=Decimal("0"),
    )
    assert res.allowed is True
