"""Polymarket Gamma API discovery layer.

Verifies real BTC UP/DOWN markets via direct slug lookup, extracts
condition id, clob token ids, and validates the slug timestamp against
market metadata startDate / endDate.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import httpx

from .models import (
    MarketMetadata,
    Side,
    Stage,
    TimestampAlignment,
)
from .url_parser import UrlParserError, parse_market_url


class DiscoveryError(RuntimeError):
    """Raised when market discovery fails or is invalid for our strategy."""


GAMMA_BASE_URL: Final[str] = "https://gamma-api.polymarket.com"


def fetch_market_by_slug(
    slug: str,
    *,
    timeout: int = 15,
    user_agent: str = "polymarket-round-bot/0.1",
) -> dict[str, Any]:
    """Direct lookup via /markets?slug=... — the only reliable way to
    find a specific BTC UP/DOWN market (verified 2026-06-04)."""
    url = f"{GAMMA_BASE_URL}/markets"
    params = {"slug": slug}
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, params=params, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list) or not data:
        raise DiscoveryError(f"no market found for slug {slug!r}")
    first: dict[str, Any] = data[0]
    return first


def _parse_iso_utc(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_token_ids(raw: str | list[str]) -> list[str]:
    """Polymarket returns clobTokenIds as a JSON string."""
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise DiscoveryError(f"malformed clobTokenIds: {raw!r}") from e
        return [str(x) for x in parsed]
    raise DiscoveryError(f"unexpected clobTokenIds type: {type(raw).__name__}")


def _parse_outcomes(raw: str | list[str]) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        return [x for x in json.loads(raw)]
    raise DiscoveryError(f"unexpected outcomes type: {type(raw).__name__}")


def _alignment(
    slug_ts: int, market_start: datetime, market_end: datetime
) -> str:
    start_ts = int(market_start.timestamp())
    end_ts = int(market_end.timestamp())
    if slug_ts == start_ts:
        return "MATCHES_START"
    if slug_ts == end_ts:
        return "MATCHES_END"
    diff_start = abs(slug_ts - start_ts)
    diff_end = abs(slug_ts - end_ts)
    if min(diff_start, diff_end) <= 60:
        return f"OFFSET|start_diff={diff_start}s|end_diff={diff_end}s"
    return "UNKNOWN"


def market_metadata_from_payload(
    payload: dict[str, Any], *, slug_for_validation: str
) -> tuple[MarketMetadata, TimestampAlignment]:
    """Convert a raw Gamma market dict into MarketMetadata + alignment.

    Camelcase-only: real Polymarket response uses camelCase (endDate,
    clobTokenIds, acceptingOrders, ...).
    """
    slug = str(payload.get("slug") or slug_for_validation)
    question = str(payload.get("question") or "")
    condition_id = str(payload.get("conditionId") or "")
    market_id = str(payload.get("id") or "")
    if not (condition_id and market_id):
        raise DiscoveryError(f"market {slug!r} missing conditionId or id")

    # Dates
    # NOTE: Gamma API's `startDate` is the market CREATION time (~24h
    # before the trading window). The actual window start is in
    # `eventStartTime` (or events[0].startTime). Use those for window
    # start. (verified 2026-06-05)
    try:
        end_dt = _parse_iso_utc(str(payload["endDate"]))
    except (KeyError, ValueError) as e:
        raise DiscoveryError(f"market {slug!r} missing/invalid endDate: {e}") from e
    # Prefer eventStartTime (window start) over startDate (creation)
    events = payload.get("events") or []
    window_start: str | None = None
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict):
            window_start = first.get("startTime") or first.get("eventStartTime")
    start_dt = _parse_iso_utc(str(window_start or payload.get("eventStartTime") or payload["startDate"]))

    # Outcomes / token ids
    outcomes = _parse_outcomes(payload.get("outcomes") or "[]")
    token_ids = _parse_token_ids(payload.get("clobTokenIds") or "[]")
    if len(outcomes) < 2 or len(token_ids) < 2:
        raise DiscoveryError(
            f"market {slug!r} must have >=2 outcomes and >=2 token ids"
        )

    # Map outcomes to token ids by position. Polymarket stores them
    # in the same order: ["Up", "Down"] -> [up_token, down_token].
    up_idx, down_idx = _locate_outcome_indices(outcomes)
    up_token = token_ids[up_idx]
    down_token = token_ids[down_idx]

    # Status flags
    active = bool(payload.get("active"))
    closed = bool(payload.get("closed"))
    accepting = bool(payload.get("acceptingOrders"))

    # Liquidity (optional)
    liq_raw = payload.get("liquidityNum") or payload.get("liquidity")
    liquidity_usd = None
    if liq_raw is not None:
        try:
            liquidity_usd = Decimal(str(liq_raw))
        except Exception:
            liquidity_usd = None

    # Fee schedule (optional, for logging)
    fee_rate = None
    fee_schedule = payload.get("feeSchedule") or {}
    if isinstance(fee_schedule, dict) and "rate" in fee_schedule:
        try:
            fee_rate = Decimal(str(fee_schedule["rate"]))
        except Exception:
            fee_rate = None

    # Resolved outcome (only for closed markets)
    resolved = None
    raw_outcomes_prices = payload.get("outcomePrices")
    if closed and raw_outcomes_prices:
        try:
            prices = json.loads(raw_outcomes_prices)
        except json.JSONDecodeError:
            prices = None
        if prices is not None:
            try:
                up_price = Decimal(str(prices[up_idx]))
            except Exception:
                up_price = None
            if up_price is not None and up_price >= Decimal("0.999"):
                resolved = Side.UP
            elif up_price is not None and up_price <= Decimal("0.001"):
                resolved = Side.DOWN

    # Event slug (optional)
    events = payload.get("events") or []
    event_slug = None
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict):
            event_slug = first.get("slug")

    metadata = MarketMetadata(
        market_id=market_id,
        condition_id=condition_id,
        question=question,
        slug=slug,
        event_slug=event_slug,
        up_token_id=up_token,
        down_token_id=down_token,
        outcomes=outcomes,
        start_ts=start_dt,
        end_ts=end_dt,
        active=active,
        closed=closed,
        accepting_orders=accepting,
        resolved_outcome=resolved,
        liquidity_usd=liquidity_usd,
        fee_rate=fee_rate,
    )
    return metadata, _validate_slug_vs_metadata(slug, metadata)


def _locate_outcome_indices(outcomes: list[str]) -> tuple[int, int]:
    """Find Up / Down outcome positions. Falls back to first/second."""
    lower = [o.lower() for o in outcomes]
    up_idx = next(
        (i for i, o in enumerate(lower) if o in ("up", "yes", "true")), 0
    )
    down_idx = next(
        (i for i, o in enumerate(lower) if o in ("down", "no", "false")), 1
    )
    if up_idx == down_idx:
        # Both defaulted — first is up, second is down
        up_idx, down_idx = 0, 1
    return up_idx, down_idx


def _validate_slug_vs_metadata(
    slug: str, metadata: MarketMetadata
) -> TimestampAlignment:
    try:
        parsed = parse_market_url(slug)
    except UrlParserError as e:
        return TimestampAlignment(
            slug_timestamp=0,
            market_start_ts=metadata.start_ts,
            market_end_ts=metadata.end_ts,
            alignment=f"UNKNOWN|{e}",
        )

    return TimestampAlignment(
        slug_timestamp=parsed.timestamp,
        market_start_ts=metadata.start_ts,
        market_end_ts=metadata.end_ts,
        alignment=_alignment(
            parsed.timestamp, metadata.start_ts, metadata.end_ts
        ),
    )


def discover_market(slug: str, **kwargs: Any) -> tuple[MarketMetadata, TimestampAlignment]:
    """Top-level helper: fetch + parse + validate."""
    payload = fetch_market_by_slug(slug, **kwargs)
    return market_metadata_from_payload(payload, slug_for_validation=slug)


# Keep Stage import to avoid linter complaint
_ = Stage
