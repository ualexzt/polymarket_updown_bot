"""Tests for Binance kline client helpers."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx

from polymarket_round_bot.binance_client import fetch_5m_close_at


class _Response:
    def __init__(self, data: list[list[object]]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[list[object]]:
        return self._data


class _FakeClient:
    calls: list[dict[str, str]] = []

    def __init__(self, *, timeout: int) -> None:
        self.timeout = timeout

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def get(
        self,
        endpoint: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> _Response:
        self.calls.append(dict(params))
        if "startTime" in params and "endTime" in params:
            return _Response(
                [
                    [1781641800000, "65721.39", "65783.55", "65720.34", "65774.25", "1", 1781642099999],
                    [1781642100000, "65774.25", "65814.00", "65746.00", "65804.69", "1", 1781642399999],
                    [1781642400000, "65804.69", "65844.00", "65802.55", "65843.99", "1", 1781642699999],
                ]
            )
        # Simulate the old recent-only request: all candles are after target.
        return _Response(
            [
                [1781676000000, "65900", "65910", "65890", "65905", "1", 1781676299999],
                [1781676300000, "65905", "65920", "65900", "65919", "1", 1781676599999],
                [1781676600000, "65919", "65930", "65910", "65925", "1", 1781676899999],
            ]
        )


def test_fetch_5m_close_at_fetches_historical_window(monkeypatch):
    _FakeClient.calls = []
    monkeypatch.setattr(httpx, "Client", _FakeClient)

    close = fetch_5m_close_at(
        "BTCUSDT",
        target_utc=datetime(2026, 6, 16, 20, 45, tzinfo=UTC),
    )

    assert close == Decimal("65843.99")
    assert _FakeClient.calls[0]["startTime"] == "1781641800000"
    assert _FakeClient.calls[0]["endTime"] == "1781642700000"
