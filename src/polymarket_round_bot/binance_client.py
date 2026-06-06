"""Binance public kline client.

Primary: https://data-api.binance.vision/api/v3/klines
Fallback: https://api.binance.com/api/v3/klines
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import httpx

from .models import BinanceState, Candle


class BinanceError(RuntimeError):
    """Raised when Binance data cannot be retrieved."""


_BINANCE_KLINES_ENDPOINTS: Final[tuple[str, ...]] = (
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
)

# Each kline row has this shape (we only need first 7):
# [open_time, open, high, low, close, volume, close_time, ...]
_KLINE_FIELDS: Final[int] = 7


def _ms_to_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def _row_to_candle(row: list[object], *, is_closed: bool) -> Candle:
    open_time_ms = int(str(row[0]))
    return Candle(
        open_time_utc=_ms_to_utc(open_time_ms),
        open=Decimal(str(row[1])),
        high=Decimal(str(row[2])),
        low=Decimal(str(row[3])),
        close=Decimal(str(row[4])),
        volume=Decimal(str(row[5])),
        is_closed=is_closed,
    )


def fetch_recent_5m_klines(
    symbol: str,
    *,
    limit: int = 6,
    timeout: int = 15,
    user_agent: str = "polymarket-round-bot/0.1",
    max_attempts: int = 4,
) -> BinanceState:
    """Fetch the most recent N 5m klines from Binance.

    Returns BinanceState with the most recent CLOSED candles. The last
    in-flight candle is omitted for state analysis (we need closed bars
    to compute features). For current_price we use the close of the most
    recent closed candle.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")

    params = {"symbol": symbol, "interval": "5m", "limit": str(limit + 1)}
    headers = {"User-Agent": user_agent, "Accept": "application/json"}

    last_error: Exception | None = None
    for endpoint in _BINANCE_KLINES_ENDPOINTS:
        for attempt in range(1, max_attempts + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.get(endpoint, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list) or not data:
                    raise BinanceError(f"empty response from {endpoint}")
                return _parse_klines(data, symbol=symbol, requested=limit + 1)
            except (httpx.HTTPError, BinanceError) as e:
                last_error = e
                if attempt < max_attempts:
                    wait = min(10, attempt * 2)
                    time.sleep(wait)
        # If first endpoint failed all attempts, try next.

    raise BinanceError(
        f"Failed to fetch Binance klines for {symbol} after all retries: {last_error}"
    )


def _parse_klines(data: list[list[object]], *, symbol: str, requested: int) -> BinanceState:
    if len(data[0]) < _KLINE_FIELDS:
        raise BinanceError(f"malformed kline row, got {len(data[0])} fields")

    # The last row is the in-flight (still-open) candle. We need only
    # closed candles for round_state. Drop the last one.
    rows = list(data)
    closed_rows = rows[:-1] if len(rows) > 1 else rows  # safety: at least one closed

    candles = [
        _row_to_candle(r, is_closed=True) for r in closed_rows
    ]
    candles.sort(key=lambda c: c.open_time_utc)

    current_price = candles[-1].close
    received_at = datetime.now(UTC)

    return BinanceState(
        symbol=symbol,
        candles=candles,
        current_price=current_price,
        received_at_utc=received_at,
    )
