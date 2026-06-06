"""Shared test fixtures."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

# Ensure src/ is on the path for `import polymarket_round_bot`
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import UTC

from polymarket_round_bot.models import (  # noqa: E402
    BinanceState,
    Candle,
    MarketMetadata,
    OrderbookSnapshot,
    PairOrderbook,
)


@pytest.fixture
def sample_market() -> MarketMetadata:
    from datetime import datetime

    return MarketMetadata(
        market_id="m1",
        condition_id="0xabc",
        question="BTC up/down 5m?",
        slug="btc-updown-5m-1700000000",
        up_token_id="up_token_1",
        down_token_id="down_token_1",
        outcomes=["Up", "Down"],
        start_ts=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        end_ts=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
        active=True,
        closed=False,
        accepting_orders=True,
    )


@pytest.fixture
def sample_market_15m() -> MarketMetadata:
    from datetime import datetime

    return MarketMetadata(
        market_id="m2",
        condition_id="0xdef",
        question="BTC up/down 15m?",
        slug="btc-updown-15m-1700000000",
        up_token_id="up_token_2",
        down_token_id="down_token_2",
        outcomes=["Up", "Down"],
        start_ts=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        end_ts=datetime(2024, 1, 1, 12, 15, 0, tzinfo=UTC),
        active=True,
        closed=False,
        accepting_orders=True,
    )


def _mk_candle(open_time_utc, o, h, lo, c, v="100") -> Candle:
    return Candle(
        open_time_utc=open_time_utc,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=Decimal(v),
        is_closed=True,
    )


@pytest.fixture
def bull_run_binance() -> BinanceState:
    from datetime import datetime, timedelta

    start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    candles = []
    price = 100.0
    for i in range(20):
        t = start + timedelta(minutes=5 * i)
        o = price
        c = price + 0.05  # small up move
        h = max(o, c) + 0.02
        lo = min(o, c) - 0.02
        candles.append(_mk_candle(t, o, h, lo, c))
        price = c
    return BinanceState(
        symbol="BTCUSDT",
        candles=candles,
        current_price=Decimal(str(price)),
        received_at_utc=datetime.now(UTC),
    )


@pytest.fixture
def sample_orderbook() -> PairOrderbook:
    from datetime import datetime

    up = OrderbookSnapshot(
        token_id="up_token_1",
        best_bid=Decimal("0.62"),
        best_ask=Decimal("0.65"),
        spread=Decimal("0.03"),
        bid_size=Decimal("100"),
        ask_size=Decimal("120"),
        received_at_utc=datetime.now(UTC),
    )
    down = OrderbookSnapshot(
        token_id="down_token_1",
        best_bid=Decimal("0.32"),
        best_ask=Decimal("0.35"),
        spread=Decimal("0.03"),
        bid_size=Decimal("80"),
        ask_size=Decimal("90"),
        received_at_utc=datetime.now(UTC),
    )
    return PairOrderbook(up=up, down=down, received_at_utc=datetime.now(UTC))
