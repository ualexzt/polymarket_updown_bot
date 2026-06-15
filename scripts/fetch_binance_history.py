"""Download BTCUSDT 5m klines from Binance public API and save as CSV.

Usage:
  python scripts/fetch_binance_history.py --days 30 --out data/btc_5m_30d.csv
  python scripts/fetch_binance_history.py --resume
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CANDLE_COLUMNS: tuple[str, ...] = (
    "open_time_utc", "open", "high", "low", "close", "volume",
    "is_closed", "close_time_utc",
)

# Each Binance kline row has 12+ fields; we use first 7.
_KLINE_FIELDS: int = 7

# 1000 candles × 5 min = 5000 min = 83.33 hours per request
_CANDLES_PER_REQUEST: int = 1000
_SECONDS_PER_CANDLE: int = 5 * 60
_MAX_REQUESTS: int = 500
_RETRY_SLEEP_SECONDS: float = 1.0
_REQUEST_TIMEOUT_SECONDS: int = 15

# https://data-api.binance.vision is the docs-recommended mirror; api.binance.com is fallback.
_ENDPOINTS: tuple[str, ...] = (
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
)


def _ms_to_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def fetch_klines_page(
    *,
    symbol: str,
    end_time_ms: int,
    endpoint: str,
) -> list[tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal, datetime]]:
    """Fetch one page of up to 1000 candles ending at end_time_ms (inclusive).

    Returns tuples of (open_time, open, high, low, close, volume, close_time).
    """
    params = {"symbol": symbol, "interval": "5m", "limit": str(_CANDLES_PER_REQUEST), "endTime": str(end_time_ms)}
    headers = {"User-Agent": "polymarket-walk-forward/0.1", "Accept": "application/json"}
    last_error: Exception | None = None
    for ep in (endpoint, *_ENDPOINTS):
        for attempt in range(3):
            try:
                with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                    resp = client.get(ep, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list):
                    raise ValueError(f"expected list, got {type(data).__name__}")
                return [_row_to_tuple(r) for r in data]
            except (httpx.HTTPError, ValueError) as json_err:
                last_error = json_err
                if attempt < 2:
                    time.sleep(_RETRY_SLEEP_SECONDS * (attempt + 1))
    raise RuntimeError(f"all Binance endpoints failed: {last_error}")


def _row_to_tuple(row: list[object]) -> tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal, datetime]:
    if len(row) < _KLINE_FIELDS:
        raise ValueError(f"malformed kline row, got {len(row)} fields")
    open_ms = int(str(row[0]))
    close_ms = int(str(row[6])) if len(row) >= 7 else open_ms + _SECONDS_PER_CANDLE * 1000
    return (
        _ms_to_utc(open_ms),
        Decimal(str(row[1])),
        Decimal(str(row[2])),
        Decimal(str(row[3])),
        Decimal(str(row[4])),
        Decimal(str(row[5])),
        _ms_to_utc(close_ms),
    )


def rows_to_csv(
    rows: list[tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal, datetime]],
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CANDLE_COLUMNS)
        for r in rows:
            writer.writerow([
                r[0].isoformat(),
                str(r[1]),
                str(r[2]),
                str(r[3]),
                str(r[4]),
                str(r[5]),
                "True",
                r[6].isoformat(),
            ])


def write_meta(
    csv_path: Path,
    *,
    row_count: int,
    min_time: datetime,
    max_time: datetime,
    fetch_seconds: float,
) -> dict[str, Any]:
    """Write <csv_path>.meta.json and return the meta dict."""
    meta = {
        "row_count": row_count,
        "sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "min_time": min_time.isoformat(),
        "max_time": max_time.isoformat(),
        "fetch_seconds": fetch_seconds,
        "created_utc": datetime.now(UTC).isoformat(),
    }
    meta_path = csv_path.with_suffix(csv_path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta


def load_existing_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_all(
    *,
    days: int,
    symbol: str,
    out_path: Path,
    resume: bool,
) -> dict[str, Any]:
    """Download up to `days` days of 5m candles and write CSV+meta. Returns meta dict."""
    target_candles = days * 24 * 12  # 288 candles/day
    started = time.time()
    existing = load_existing_rows(out_path) if resume else []
    all_rows: list[tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal, datetime]] = []
    if existing:
        for r in existing:
            all_rows.append((
                datetime.fromisoformat(r["open_time_utc"]),
                Decimal(r["open"]),
                Decimal(r["high"]),
                Decimal(r["low"]),
                Decimal(r["close"]),
                Decimal(r["volume"]),
                datetime.fromisoformat(r["close_time_utc"]),
            ))
        # resume: end at last candle's close_time - 1ms
        end_time_ms = int(all_rows[-1][6].timestamp() * 1000) - 1
    else:
        end_time_ms = int(datetime.now(UTC).timestamp() * 1000)

    pages = 0
    while len(all_rows) < target_candles and pages < _MAX_REQUESTS:
        batch = fetch_klines_page(symbol=symbol, end_time_ms=end_time_ms, endpoint=_ENDPOINTS[0])
        if not batch:
            break
        all_rows.extend(batch)
        # Advance cursor to 1ms before the oldest candle in this batch
        end_time_ms = int(min(r[0] for r in batch).timestamp() * 1000) - 1
        pages += 1
        if pages % 10 == 0:
            print(f"  fetched {len(all_rows)} / {target_candles} candles ({pages} pages)", file=sys.stderr)
        time.sleep(_RETRY_SLEEP_SECONDS)

    # Dedup and sort
    seen: set[datetime] = set()
    deduped: list[tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal, datetime]] = []
    for r in all_rows:
        if r[0] in seen:
            continue
        seen.add(r[0])
        deduped.append(r)
    deduped.sort(key=lambda r: r[0])

    rows_to_csv(deduped, out_path)
    elapsed = time.time() - started
    min_time = deduped[0][0] if deduped else datetime.now(UTC)
    max_time = deduped[-1][0] if deduped else datetime.now(UTC)
    return write_meta(
        out_path,
        row_count=len(deduped),
        min_time=min_time,
        max_time=max_time,
        fetch_seconds=elapsed,
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=500)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--out", default=None, help="output CSV path")
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()

    out_path = Path(args.out) if args.out else Path(f"data/btc_5m_{args.days}d.csv")
    meta = fetch_all(days=args.days, symbol=args.symbol, out_path=out_path, resume=args.resume)
    print(f"OK: {meta['row_count']} candles, {meta['min_time']} → {meta['max_time']}, "
          f"{meta['fetch_seconds']:.1f}s, sha256={meta['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
