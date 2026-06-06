"""Tests for Polymarket discovery layer.

Mocks httpx via monkeypatch to avoid network calls.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from polymarket_round_bot.polymarket_discovery import (
    DiscoveryError,
    discover_market,
    market_metadata_from_payload,
)

SAMPLE_PAYLOAD: dict = {
    "id": "12345",
    "conditionId": "0xabcdef",
    "slug": "btc-updown-5m-1700000000",
    "question": "BTC Up or Down 5m?",
    # 1700000000 = 2023-11-14T22:13:20Z. Use 5-min window from this timestamp.
    "endDate": "2023-11-14T22:18:20Z",
    "startDate": "2023-11-14T22:13:20Z",
    "outcomes": '["Up", "Down"]',
    "clobTokenIds": '["up_token_1", "down_token_1"]',
    "active": True,
    "closed": False,
    "acceptingOrders": True,
    "liquidityNum": "1000",
    "feeSchedule": {"rate": 0.07},
    "events": [{"slug": "btc-updown-5m-1700000000"}],
}


def test_parses_real_gamma_payload():
    metadata, alignment = market_metadata_from_payload(
        SAMPLE_PAYLOAD, slug_for_validation=SAMPLE_PAYLOAD["slug"]
    )
    assert metadata.market_id == "12345"
    assert metadata.condition_id == "0xabcdef"
    assert metadata.up_token_id == "up_token_1"
    assert metadata.down_token_id == "down_token_1"
    assert metadata.outcomes == ["Up", "Down"]
    assert metadata.active is True
    assert metadata.closed is False
    assert metadata.accepting_orders is True
    assert metadata.liquidity_usd == 1000
    assert metadata.fee_rate == Decimal("0.07")
    assert alignment.alignment == "MATCHES_START"
    assert alignment.slug_timestamp == 1700000000


def test_aligns_with_small_offset_returns_offset():
    """Slug timestamp within 60s of start/end -> OFFSET."""
    payload = dict(SAMPLE_PAYLOAD)
    # slug_ts=1700000000; start=1700000000-15=1699999985, end=1700000000+15=1700000015
    # diff_start=15, diff_end=15 -> OFFSET
    payload["endDate"] = "2023-11-14T22:13:35Z"  # 1700000015
    payload["startDate"] = "2023-11-14T22:13:05Z"  # 1699999985
    metadata, alignment = market_metadata_from_payload(
        payload, slug_for_validation="btc-updown-5m-1700000000"
    )
    assert alignment.alignment.startswith("OFFSET")


def test_resolves_outcome_up_via_price():
    payload = dict(SAMPLE_PAYLOAD)
    payload["closed"] = True
    payload["outcomePrices"] = '["0.999", "0.001"]'
    metadata, _ = market_metadata_from_payload(
        payload, slug_for_validation=payload["slug"]
    )
    assert metadata.resolved_outcome.value == "UP"


def test_rejects_market_without_outcomes():
    payload = dict(SAMPLE_PAYLOAD)
    payload["outcomes"] = "[]"
    payload["clobTokenIds"] = "[]"
    with pytest.raises(DiscoveryError):
        market_metadata_from_payload(payload, slug_for_validation=payload["slug"])


def test_rejects_market_missing_enddate():
    payload = dict(SAMPLE_PAYLOAD)
    del payload["endDate"]
    with pytest.raises(DiscoveryError):
        market_metadata_from_payload(payload, slug_for_validation=payload["slug"])


def test_discovers_market_with_mocked_http():
    """End-to-end: mocked httpx returns SAMPLE_PAYLOAD, we get back metadata."""
    with patch("polymarket_round_bot.polymarket_discovery.httpx.Client") as client_mock:
        client_inst = MagicMock()
        resp = MagicMock()
        resp.json.return_value = [SAMPLE_PAYLOAD]
        resp.raise_for_status.return_value = None
        client_inst.get.return_value = resp
        client_inst.__enter__.return_value = client_inst
        client_inst.__exit__.return_value = None
        client_mock.return_value = client_inst

        metadata, alignment = discover_market("btc-updown-5m-1700000000")
        assert metadata.slug == "btc-updown-5m-1700000000"
        assert alignment.alignment == "MATCHES_START"


def test_rejects_empty_market_list():
    with patch("polymarket_round_bot.polymarket_discovery.httpx.Client") as client_mock:
        client_inst = MagicMock()
        resp = MagicMock()
        resp.json.return_value = []
        resp.raise_for_status.return_value = None
        client_inst.get.return_value = resp
        client_inst.__enter__.return_value = client_inst
        client_inst.__exit__.return_value = None
        client_mock.return_value = client_inst

        with pytest.raises(DiscoveryError):
            discover_market("btc-updown-5m-1700000000")
