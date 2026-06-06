"""URL / slug parser for Polymarket BTC UP/DOWN markets.

Accepts:
  - https://polymarket.com/uk/event/btc-updown-5m-1780652400
  - https://polymarket.com/event/btc-updown-5m-1780652400
  - https://polymarket.com/event/btc-updown-15m-1780652400
  - btc-updown-5m-1780652400
  - btc-updown-15m-1780652400

Returns ParsedSlug or raises UrlParserError.
"""
from __future__ import annotations

import re
from typing import Final

from .models import Asset, MarketType, ParsedSlug, Timeframe


class UrlParserError(ValueError):
    """Raised when input is not a recognised BTC UP/DOWN slug/url."""


# Capture groups: 1=asset, 2=timeframe, 3=timestamp
_SLUG_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<asset>btc|eth|sol)-(?P<market_type>updown)-(?P<timeframe>\d+m)-(?P<ts>\d+)$",
    re.IGNORECASE,
)

_SUPPORTED_TIMEFRAMES: Final[frozenset[str]] = frozenset({"5m", "15m"})
_SUPPORTED_ASSETS: Final[frozenset[str]] = frozenset({"btc"})


def _extract_slug(input_str: str) -> str:
    """Pull the last path segment that looks like a slug."""
    s = input_str.strip()
    if not s:
        raise UrlParserError("empty input")
    # Strip scheme/host
    if "://" in s:
        # find first '/' after the host
        scheme_end = s.index("://") + 3
        host_end = s.find("/", scheme_end)
        s = s[host_end + 1 :] if host_end != -1 else ""
    # Drop query / fragment
    for sep in ("?", "#"):
        if sep in s:
            s = s.split(sep, 1)[0]
    # Take last non-empty segment
    parts = [p for p in s.split("/") if p]
    if not parts:
        raise UrlParserError(f"no path segments in input: {input_str!r}")
    return parts[-1]


def parse_market_url(input_str: str) -> ParsedSlug:
    """Parse any supported URL form or bare slug into a ParsedSlug."""
    slug = _extract_slug(input_str)
    m = _SLUG_RE.match(slug.lower())
    if not m:
        raise UrlParserError(
            f"input does not look like a BTC/ETH/SOL updown slug: {input_str!r}"
        )

    asset_str = m.group("asset").lower()
    timeframe_str = m.group("timeframe").lower()
    ts_str = m.group("ts")
    market_type = m.group("market_type").lower()

    if asset_str not in _SUPPORTED_ASSETS:
        raise UrlParserError(
            f"unsupported asset: {asset_str!r} (supported: {sorted(_SUPPORTED_ASSETS)})"
        )
    if market_type != "updown":
        raise UrlParserError(f"unsupported market_type: {market_type!r} (expected 'updown')")
    if timeframe_str not in _SUPPORTED_TIMEFRAMES:
        raise UrlParserError(
            f"unsupported timeframe: {timeframe_str!r} (supported: {sorted(_SUPPORTED_TIMEFRAMES)})"
        )

    try:
        ts = int(ts_str)
    except ValueError as e:
        raise UrlParserError(f"timestamp not an integer: {ts_str!r}") from e

    return ParsedSlug(
        asset=Asset.BTC,
        market_type=MarketType.UPDOWN,
        timeframe=Timeframe(timeframe_str),
        timestamp=ts,
        slug=slug,
    )
