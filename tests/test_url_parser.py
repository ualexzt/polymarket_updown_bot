"""Tests for URL/slug parser."""
from __future__ import annotations

import pytest

from polymarket_round_bot.models import Asset, MarketType, Timeframe
from polymarket_round_bot.url_parser import UrlParserError, parse_market_url


def test_parses_uk_event_5m():
    p = parse_market_url("https://polymarket.com/uk/event/btc-updown-5m-1780652400")
    assert p.asset == Asset.BTC
    assert p.market_type == MarketType.UPDOWN
    assert p.timeframe == Timeframe.M5
    assert p.timestamp == 1780652400
    assert p.slug == "btc-updown-5m-1780652400"


def test_parses_no_uk_event_15m():
    p = parse_market_url("https://polymarket.com/event/btc-updown-15m-1780652400")
    assert p.timeframe == Timeframe.M15
    assert p.timestamp == 1780652400


def test_parses_bare_slug_5m():
    p = parse_market_url("btc-updown-5m-1780652400")
    assert p.timeframe == Timeframe.M5
    assert p.timestamp == 1780652400


def test_parses_bare_slug_15m():
    p = parse_market_url("btc-updown-15m-1780652400")
    assert p.timeframe == Timeframe.M15
    assert p.timestamp == 1780652400


def test_rejects_invalid_slug():
    with pytest.raises(UrlParserError):
        parse_market_url("https://polymarket.com/event/random-text")


def test_rejects_unsupported_timeframe():
    with pytest.raises(UrlParserError):
        parse_market_url("btc-updown-1h-1780652400")


def test_rejects_unsupported_asset():
    with pytest.raises(UrlParserError):
        parse_market_url("eth-updown-5m-1780652400")


def test_extracts_timestamp_as_int():
    p = parse_market_url("btc-updown-5m-1780652400")
    assert isinstance(p.timestamp, int)


def test_preserves_original_slug():
    s = "btc-updown-5m-1780652400"
    p = parse_market_url(s)
    assert p.slug == s
