"""Paper risk manager.

Pure decision: given current state (open positions count, daily PnL)
and the candidate market/side, returns whether the trade is allowed.

In v1 there is no averaging, no DCA, one position per market.
"""
from __future__ import annotations

from decimal import Decimal

from .config import Settings
from .models import RiskDecision, Side


class RiskManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def max_position_usd(self) -> Decimal:
        return self._settings.max_position_usd

    @property
    def max_open_positions(self) -> int:
        return self._settings.max_open_positions

    def evaluate(
        self,
        *,
        candidate_market_slug: str,
        candidate_side: Side,
        open_positions: list[tuple[str, Side]],
        daily_realized_pnl: Decimal,
    ) -> RiskDecision:
        requested = self._settings.max_position_usd
        open_count = len(open_positions)
        max_open = self._settings.max_open_positions

        # Check duplicate (most specific) BEFORE general max_open
        for slug, side in open_positions:
            if slug == candidate_market_slug and side == candidate_side:
                return RiskDecision(
                    allowed=False,
                    reject_reason=f"duplicate_position_on_market:{slug}/{side.value}",
                    requested_size_usd=requested,
                    max_position_usd=self._settings.max_position_usd,
                    open_positions_count=open_count,
                    max_open_positions=max_open,
                    daily_realized_pnl=daily_realized_pnl,
                    max_daily_loss_usd=self._settings.max_daily_loss_usd,
                )

        if open_count >= max_open:
            return RiskDecision(
                allowed=False,
                reject_reason=f"max_open_positions_reached:{open_count}>={max_open}",
                requested_size_usd=requested,
                max_position_usd=self._settings.max_position_usd,
                open_positions_count=open_count,
                max_open_positions=max_open,
                daily_realized_pnl=daily_realized_pnl,
                max_daily_loss_usd=self._settings.max_daily_loss_usd,
            )

        if daily_realized_pnl <= -self._settings.max_daily_loss_usd:
            return RiskDecision(
                allowed=False,
                reject_reason=f"daily_loss_exceeded:{daily_realized_pnl}<=-{self._settings.max_daily_loss_usd}",
                requested_size_usd=requested,
                max_position_usd=self._settings.max_position_usd,
                open_positions_count=open_count,
                max_open_positions=max_open,
                daily_realized_pnl=daily_realized_pnl,
                max_daily_loss_usd=self._settings.max_daily_loss_usd,
            )

        return RiskDecision(
            allowed=True,
            reject_reason=None,
            requested_size_usd=requested,
            max_position_usd=self._settings.max_position_usd,
            open_positions_count=open_count,
            max_open_positions=max_open,
            daily_realized_pnl=daily_realized_pnl,
            max_daily_loss_usd=self._settings.max_daily_loss_usd,
        )
