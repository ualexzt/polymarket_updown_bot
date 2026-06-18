"""WebSocket orderbook stream — real-time best_bid_ask from Polymarket.

Maintains a live cache of best_bid/ask/spread per token via the
Polymarket Market Channel WebSocket.  Thread-safe for reads from the
synchronous runner.

Design:
  - Connects to wss://ws-subscriptions-clob.polymarket.com/ws/market
  - Subscribes with custom_feature_enabled=true for best_bid_ask events
  - Sends PING every 10s (server requires heartbeat)
  - Auto-reconnects with exponential backoff on disconnection
  - Falls back gracefully: is_connected() returns False → caller uses REST
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("polymarket_round_bot.orderbook_stream")

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL_S = 10
RECONNECT_BASE_S = 1
RECONNECT_MAX_S = 30


@dataclass
class TokenBook:
    """Best-of-book for a single token, updated from WS events."""
    token_id: str
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    spread: Decimal | None = None
    updated_at: float = 0.0  # time.monotonic()


class OrderbookStream:
    """Background WebSocket stream that maintains best_bid_ask per token.

    Thread-safe: reads from runner thread, writes from async WS loop.
    """

    def __init__(self) -> None:
        self._books: dict[str, TokenBook] = {}
        self._lock = threading.Lock()
        self._subscribed_ids: set[str] = set()
        self._pending_subscribe: set[str] = set()
        self._pending_unsubscribe: set[str] = set()
        self._connected = False
        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # === Public API (thread-safe, called from runner) ===

    def get_book(self, token_id: str) -> TokenBook | None:
        """Get cached orderbook for a token. Returns None if not available."""
        with self._lock:
            return self._books.get(token_id)

    def is_connected(self) -> bool:
        """True if WS is connected and receiving data."""
        return self._connected

    def subscribe(self, token_ids: list[str]) -> None:
        """Request subscription to new token IDs (non-blocking)."""
        with self._lock:
            for tid in token_ids:
                if tid not in self._subscribed_ids:
                    self._pending_subscribe.add(tid)

    def unsubscribe(self, token_ids: list[str]) -> None:
        """Request unsubscription from token IDs (non-blocking)."""
        with self._lock:
            for tid in token_ids:
                if tid in self._subscribed_ids:
                    self._pending_unsubscribe.add(tid)

    def set_tokens(self, token_ids: list[str]) -> None:
        """Set exact token list — subscribe to new, unsubscribe from old."""
        desired = set(token_ids)
        with self._lock:
            to_add = desired - self._subscribed_ids
            to_remove = self._subscribed_ids - desired
            self._pending_subscribe.update(to_add)
            self._pending_unsubscribe.update(to_remove)

    # === Lifecycle ===

    def start(self) -> None:
        """Start the background WS thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="orderbook-ws"
        )
        self._thread.start()
        log.info("orderbook_stream_started")

    def stop(self) -> None:
        """Stop the background WS thread."""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        log.info("orderbook_stream_stopped")

    # === Internal async loop ===

    def _run_loop(self) -> None:
        """Entry point for the background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._ws_loop())
        except Exception:
            log.exception("orderbook_stream_loop_crashed")
        finally:
            self._loop.close()

    async def _ws_loop(self) -> None:
        """Main WS loop with reconnect logic."""
        backoff = RECONNECT_BASE_S
        while self._running:
            try:
                async with websockets.connect(
                    WS_URL,
                    additional_headers={"User-Agent": "polymarket-round-bot/0.1"},
                    ping_interval=20,  # let library handle protocol-level pings
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    self._connected = True
                    backoff = RECONNECT_BASE_S
                    log.info("orderbook_ws_connected url=%s", WS_URL)

                    # Send initial subscription for any pending tokens
                    await self._flush_subscriptions(ws)

                    # Run reader + heartbeat concurrently
                    await asyncio.gather(
                        self._reader(ws),
                        self._heartbeat(ws),
                        self._subscription_manager(ws),
                    )
            except (ConnectionClosed, OSError, websockets.exceptions.WebSocketException) as e:
                self._connected = False
                log.warning("orderbook_ws_disconnected err=%s reconnect_in=%fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_S)
            except Exception as e:
                self._connected = False
                log.error("orderbook_ws_unexpected_error err=%s type=%s", e, type(e).__name__)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_S)

    async def _reader(self, ws: websockets.ClientConnection) -> None:
        """Read messages from WS and update cache."""
        async for raw in ws:
            # Handle plain-text PONG response
            if isinstance(raw, str) and not raw.startswith("{") and not raw.startswith("["):
                log.debug("orderbook_ws_text msg=%s", raw[:50])
                continue
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                log.debug("orderbook_ws_non_json len=%d", len(str(raw)[:50]))
                continue

            # Handle list responses (subscription confirmations)
            if isinstance(msg, list):
                log.debug("orderbook_ws_list len=%d", len(msg))
                for item in msg:
                    if isinstance(item, dict):
                        self._process_event(item)
                continue

            if isinstance(msg, dict):
                self._process_event(msg)

    def _process_event(self, msg: dict[str, Any]) -> None:
        """Process a single event message."""
        event_type = msg.get("event_type") or msg.get("type")
        log.debug("orderbook_ws_event type=%s keys=%s", event_type, list(msg.keys())[:5])

        if event_type == "book":
            self._handle_book(msg)
        elif event_type == "best_bid_ask":
            self._handle_best_bid_ask(msg)
        elif event_type == "price_change":
            self._handle_price_change(msg)
        elif event_type in ("last_trade_price", "tick_size_change"):
            pass  # informational, not needed for orderbook
        elif event_type in ("new_market", "market_resolved"):
            pass  # lifecycle, not needed here
        elif event_type in ("PONG", "pong", "ping"):
            pass  # heartbeat response/request
        else:
            log.debug("orderbook_ws_unknown_event type=%s", event_type)

    async def _heartbeat(self, ws: websockets.ClientConnection) -> None:
        """Send application-level PING every 5 seconds."""
        while True:
            await asyncio.sleep(5)
            try:
                await ws.send("PING")
                log.debug("orderbook_ws_ping_sent")
            except (ConnectionClosed, OSError):
                log.debug("orderbook_ws_ping_failed")
                break

    async def _subscription_manager(self, ws: websockets.ClientConnection) -> None:
        """Periodically flush pending subscribe/unsubscribe requests."""
        while True:
            await asyncio.sleep(0.5)  # check every 500ms
            await self._flush_subscriptions(ws)

    async def _flush_subscriptions(self, ws: websockets.ClientConnection) -> None:
        """Send pending subscribe/unsubscribe messages."""
        with self._lock:
            to_sub = list(self._pending_subscribe)
            to_unsub = list(self._pending_unsubscribe)
            self._pending_subscribe.clear()
            self._pending_unsubscribe.clear()

        if to_sub:
            msg = {
                "type": "market",
                "assets_ids": to_sub,
                "custom_feature_enabled": True,
            }
            try:
                payload = json.dumps(msg)
                log.info("orderbook_ws_sending_subscribe payload_len=%d", len(payload))
                await ws.send(payload)
                with self._lock:
                    self._subscribed_ids.update(to_sub)
                log.info("orderbook_ws_subscribed tokens=%d ids=%s", len(to_sub), to_sub[:3])
            except (ConnectionClosed, OSError):
                # Re-add to pending on failure
                with self._lock:
                    self._pending_subscribe.update(to_sub)

        if to_unsub:
            msg = {
                "operation": "unsubscribe",
                "assets_ids": to_unsub,
            }
            try:
                await ws.send(json.dumps(msg))
                with self._lock:
                    self._subscribed_ids -= set(to_unsub)
                log.info("orderbook_ws_unsubscribed tokens=%d", len(to_unsub))
            except (ConnectionClosed, OSError):
                with self._lock:
                    self._pending_unsubscribe.update(to_unsub)

    # === Event handlers ===

    def _handle_book(self, msg: dict[str, Any]) -> None:
        """Full book snapshot — extract best bid/ask."""
        token_id = msg.get("asset_id") or msg.get("id", "")
        bids = msg.get("bids") or []
        asks = msg.get("asks") or []

        best_bid = _best_price(bids, reverse=True)
        best_ask = _best_price(asks, reverse=False)
        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

        book = TokenBook(
            token_id=token_id,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            updated_at=time.monotonic(),
        )
        with self._lock:
            self._books[token_id] = book

    def _handle_best_bid_ask(self, msg: dict[str, Any]) -> None:
        """Top-of-book update — fastest path."""
        token_id = msg.get("asset_id", "")
        if not token_id:
            return

        best_bid = _dec(msg.get("best_bid"))
        best_ask = _dec(msg.get("best_ask"))
        spread = _dec(msg.get("spread"))
        if spread is None and best_bid is not None and best_ask is not None:
            spread = best_ask - best_bid

        with self._lock:
            existing = self._books.get(token_id)
            if existing:
                existing.best_bid = best_bid
                existing.best_ask = best_ask
                existing.spread = spread
                existing.updated_at = time.monotonic()
            else:
                self._books[token_id] = TokenBook(
                    token_id=token_id,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    spread=spread,
                    updated_at=time.monotonic(),
                )

    def _handle_price_change(self, msg: dict[str, Any]) -> None:
        """Price level update — extract best_bid/ask from changes."""
        token_id = msg.get("asset_id", "")
        if not token_id:
            return

        # price_change events include best_bid/best_ask in each change entry
        changes = msg.get("price_changes") or []
        if not changes:
            return

        first = changes[0]
        best_bid = _dec(first.get("best_bid"))
        best_ask = _dec(first.get("best_ask"))
        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

        with self._lock:
            existing = self._books.get(token_id)
            if existing:
                if best_bid is not None:
                    existing.best_bid = best_bid
                if best_ask is not None:
                    existing.best_ask = best_ask
                if spread is not None:
                    existing.spread = spread
                existing.updated_at = time.monotonic()
            else:
                self._books[token_id] = TokenBook(
                    token_id=token_id,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    spread=spread,
                    updated_at=time.monotonic(),
                )


# === Helpers ===

def _best_price(levels: list[dict], *, reverse: bool) -> Decimal | None:
    """Extract best price from bid/ask levels."""
    prices = []
    for lv in levels:
        p = _dec(lv.get("price"))
        if p is not None:
            prices.append(p)
    if not prices:
        return None
    return max(prices) if reverse else min(prices)


def _dec(v: Any) -> Decimal | None:
    """Safe Decimal conversion."""
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (ValueError, TypeError):
        return None
