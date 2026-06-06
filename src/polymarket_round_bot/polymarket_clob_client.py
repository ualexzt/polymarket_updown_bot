"""Polymarket CLOB orderbook client.

Fetches /book for a given token id and normalises to OrderbookSnapshot.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import httpx

from .models import OrderbookLevel, OrderbookSnapshot, PairOrderbook


class ClobError(RuntimeError):
    """Raised when CLOB orderbook cannot be loaded."""


CLOB_BASE_URL: Final[str] = "https://clob.polymarket.com"


def fetch_orderbook(
    token_id: str,
    *,
    timeout: int = 10,
    user_agent: str = "polymarket-round-bot/0.1",
) -> OrderbookSnapshot:
    """Fetch /book for a single token id."""
    url = f"{CLOB_BASE_URL}/book"
    params = {"token_id": token_id}
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, params=params, headers=headers)
    resp.raise_for_status()
    payload = resp.json()
    return _parse_book(payload, token_id=token_id)


def _parse_book(payload: dict[str, Any], *, token_id: str) -> OrderbookSnapshot:
    bids_raw = payload.get("bids") or []
    asks_raw = payload.get("asks") or []

    def _levels(rows: list[dict[str, Any]]) -> list[OrderbookLevel]:
        out: list[OrderbookLevel] = []
        for r in rows:
            try:
                price = Decimal(str(r.get("price", "0")))
                size = Decimal(str(r.get("size", "0")))
            except Exception:
                continue
            out.append(OrderbookLevel(price=price, size=size))
        return out

    bids = _levels(bids_raw)
    asks = _levels(asks_raw)

    # Sort: bids desc, asks asc
    bids.sort(key=lambda lv: lv.price, reverse=True)
    asks.sort(key=lambda lv: lv.price)

    best_bid = bids[0].price if bids else None
    best_ask = asks[0].price if asks else None
    spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

    # Sizes at top of book (depth at best level)
    bid_size = bids[0].size if bids else None
    ask_size = asks[0].size if asks else None

    # Top-5 depths for transparency (as USD estimate: sum(price*size) of top 5)
    def _depth_usd(levels: list[OrderbookLevel]) -> Decimal:
        return sum((lv.price * lv.size for lv in levels[:5]), Decimal("0"))

    liquidity_estimate = _depth_usd(bids) + _depth_usd(asks)

    return OrderbookSnapshot(
        token_id=token_id,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        bid_size=bid_size,
        ask_size=ask_size,
        top_5_bids=bids[:5],
        top_5_asks=asks[:5],
        liquidity_usd_estimate=liquidity_estimate,
        received_at_utc=datetime.now(UTC),
    )


def fetch_pair_orderbook(
    up_token_id: str, down_token_id: str, **kwargs: Any
) -> PairOrderbook:
    """Fetch both orderbooks in sequence and bundle."""
    up = fetch_orderbook(up_token_id, **kwargs)
    down = fetch_orderbook(down_token_id, **kwargs)
    received = max(up.received_at_utc, down.received_at_utc)
    return PairOrderbook(up=up, down=down, received_at_utc=received)


def levels_to_json(levels: list[OrderbookLevel]) -> str:
    """Compact JSON for storage in snapshot field."""
    return json.dumps(
        [{"price": str(lv.price), "size": str(lv.size)} for lv in levels],
        separators=(",", ":"),
    )
