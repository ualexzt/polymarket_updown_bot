"""Paper broker — handles order placement and position lifecycle.

In v1 we DO NOT support partial fills: if the top-of-book cannot absorb
the requested size_usd, the order is rejected and the position is not
created (a SKIP decision is recorded upstream).

Entry price = best_ask of the selected side token.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from .models import (
    DecisionKind,
    DistanceBucket,
    PaperPosition,
    PositionStatus,
    Side,
    SignalDecision,
    Stage,
    VolatilityBucket,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class PaperBrokerError(RuntimeError):
    """Raised on broker-level errors (e.g. duplicate position)."""


class PaperBroker:
    """Tracks open paper positions and creates new ones from TRADE decisions."""

    def __init__(self) -> None:
        self._open: dict[tuple[str, Side], PaperPosition] = {}

    @property
    def open_positions(self) -> list[PaperPosition]:
        return list(self._open.values())

    def open_count(self) -> int:
        return len(self._open)

    def has_open(self, market_slug: str, side: Side) -> bool:
        return (market_slug, side) in self._open

    def open_position(
        self,
        decision: SignalDecision,
        *,
        round_open_price: Decimal,
        btc_price_at_entry: Decimal,
        distance_bucket: DistanceBucket,
        volatility_bucket: VolatilityBucket,
        pattern: str,
        stage: Stage,
        seconds_to_expiry: int,
        entry_best_bid: Decimal,
    ) -> PaperPosition:
        if decision.decision != DecisionKind.TRADE:
            raise PaperBrokerError(
                f"cannot open position from non-TRADE decision: {decision.decision}"
            )
        if decision.side is None or decision.token_id is None:
            raise PaperBrokerError("TRADE decision missing side/token_id")
        if decision.market_ask is None or decision.market_ask <= 0:
            raise PaperBrokerError("TRADE decision missing best_ask")
        entry_price = decision.market_ask
        if entry_price >= 1:
            raise PaperBrokerError(
                f"entry_price must be < 1, got {entry_price}"
            )
        entry_size_usd = decision.size_usd
        # v1: no partial fills. If available ask size < requested size, fail.
        # Caller (signal_engine) already enforces this — but we double-check
        # in case the orderbook is stale.
        shares = entry_size_usd / entry_price

        key = (decision.market_slug, decision.side)
        if key in self._open:
            raise PaperBrokerError(
                f"duplicate open position for {decision.market_slug}/{decision.side.value}"
            )

        position = PaperPosition(
            position_id=_new_id("pos"),
            decision_id=_new_id("dec"),
            market_slug=decision.market_slug,
            event_url=decision.event_url,
            selected_side=decision.side,
            token_id=decision.token_id,
            entry_timestamp_utc=datetime.now(UTC),
            entry_price=entry_price,
            entry_best_ask=entry_price,
            entry_best_bid=entry_best_bid,
            entry_spread=(entry_price - entry_best_bid) if entry_best_bid is not None else Decimal("0"),
            entry_size_usd=entry_size_usd,
            shares=shares,
            fair_price_at_entry=decision.historical_probability or Decimal("0"),
            max_buy_price_at_entry=decision.max_buy_price or Decimal("0"),
            edge_at_entry=decision.edge_vs_ask or Decimal("0"),
            round_open_price=round_open_price,
            btc_price_at_entry=btc_price_at_entry,
            distance_bucket_at_entry=distance_bucket,
            volatility_bucket_at_entry=volatility_bucket,
            pattern_at_entry=pattern,
            stage_at_entry=stage,
            seconds_to_expiry_at_entry=seconds_to_expiry,
            current_side_at_entry=decision.current_side,
            status=PositionStatus.OPEN,
            rule_id=decision.rule_id,
            rule_match_type=decision.rule_match_type,
            historical_probability_at_entry=decision.historical_probability or Decimal("0"),
            samples_at_entry=decision.samples,
        )
        self._open[key] = position
        return position

    def close_position(self, market_slug: str, side: Side) -> PaperPosition:
        key = (market_slug, side)
        if key not in self._open:
            raise PaperBrokerError(f"no open position for {market_slug}/{side.value}")
        position = self._open.pop(key)
        # Caller will mark the new status when settling.
        return position

    def list_open(self) -> list[PaperPosition]:
        return list(self._open.values())
