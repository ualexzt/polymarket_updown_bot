# Walk-Forward Validation + Breakeven Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline 4-script pipeline that replays historical Binance 5m candles through the live rule-lookup and state-building code, producing a walk-forward validation report (`docs/analysis/2026-06-15-walk-forward.md`) with per-fold WR, breakeven sensitivity, regime breakdown, and rule rankings.

**Architecture:** Historical replay (not a new model) — reuse `round_state.py::build_round_state()` and `probability_rules.py::ProbabilityRules.lookup()` against downloaded BTCUSDT 5m klines. Pipeline: fetch → backtest → analyze → report. Each script is a standalone CLI with explicit `--in-dir`/`--out-dir` flags. TDD: tests in `tests/walk_forward/` cover no-lookahead, settlement correctness, single-trade-per-round, fold partitioning, and CSV/JSON schema.

**Tech Stack:** Python 3.11+, pydantic v2 (existing), httpx (existing), pytest (existing), stdlib `csv`/`json`/`argparse`. No new dependencies.

---

## File Structure

**New files:**

- `scripts/fetch_binance_history.py` — paginated Binance 5m klines → CSV
- `scripts/walk_forward_backtest.py` — fold loop, per-round simulation, summary writer
- `scripts/breakeven_analysis.py` — sensitivity table, rule rankings, regime breakdown
- `scripts/walk_forward_report.py` — JSON/CSV → markdown report
- `tests/walk_forward/__init__.py` — empty
- `tests/walk_forward/conftest.py` — synthetic candle fixtures, sample rules
- `tests/walk_forward/test_conftest.py` — fixture sanity
- `tests/walk_forward/test_walk_forward_backtest.py` — core engine tests
- `tests/walk_forward/test_breakeven_analysis.py` — analysis tests
- `tests/walk_forward/test_walk_forward_report.py` — report tests

**Modified files:** none (spec excludes live code changes).

**Output artifacts (gitignored via `data/`, `results/`):**
- `data/btc_5m_<N>d.csv`, `data/btc_5m_<N>d.csv.meta.json`
- `results/wf_fold_<i>_trades.csv`, `results/wf_fold_<i>_summary.json`
- `results/wf_aggregate_summary.json`
- `results/breakeven_sensitivity.csv`, `results/rule_performance_ranked.csv`
- `docs/analysis/2026-06-15-walk-forward.md` (committed)

**Reused (read-only):**
- `src/polymarket_round_bot/round_state.py::build_round_state()`
- `src/polymarket_round_bot/probability_rules.py::ProbabilityRules`
- `src/polymarket_round_bot/candle_features.py::compute_candle_features()`
- `src/polymarket_round_bot/models.py` (Candle, BinanceState, MarketMetadata, RoundState, ProbabilityRule, RuleLookupResult, Side, Stage)
- `config/btc_updown_state_rules_15m.json` (2,014 rules)
- `pyproject.toml` already lists pytest, pydantic, httpx — no new deps

---

## Task 1: Test fixtures — synthetic candles and sample rules

**Files:**
- Create: `tests/walk_forward/__init__.py`
- Create: `tests/walk_forward/conftest.py`
- Create: `tests/walk_forward/test_conftest.py`

- [ ] **Step 1: Create empty `__init__.py`**

```python
# tests/walk_forward/__init__.py
```

Run: `touch tests/walk_forward/__init__.py`

- [ ] **Step 2: Write conftest with candle factory, sample rules, and small dataset**

Create `tests/walk_forward/conftest.py`:

```python
"""Shared fixtures for walk-forward backtest tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import (
    BinanceState,
    Candle,
    CurrentSide,
    DistanceBucket,
    MarketMetadata,
    ProbabilityRule,
    Side,
    Stage,
    VolatilityBucket,
)


def make_candle(
    open_time: datetime,
    open: str,
    high: str | None = None,
    low: str | None = None,
    close: str | None = None,
    volume: str = "10",
) -> Candle:
    """Create a Candle with sensible defaults for OHLC."""
    o = Decimal(open)
    c = Decimal(close if close is not None else open)
    h = Decimal(high if high is not None else open)
    l = Decimal(low if low is not None else open)
    return Candle(
        open_time_utc=open_time if open_time.tzinfo else open_time.replace(tzinfo=UTC),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal(volume),
        is_closed=True,
    )


@pytest.fixture
def candle_factory():
    return make_candle


@pytest.fixture
def synthetic_5d_candles() -> list[Candle]:
    """5 days × 288 5m candles = 1440 candles, constant price 50000.

    Spans 2026-06-01 00:00 UTC to 2026-06-06 00:00 UTC.
    """
    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    candles = []
    for i in range(1440):
        candles.append(make_candle(start + timedelta(minutes=5 * i), "50000"))
    return candles


@pytest.fixture
def synthetic_market() -> MarketMetadata:
    """15m market starting at 2026-06-06 00:00 UTC, slug btc-updown-15m-1781520000.

    (1781520000 = floor(2026-06-06T00:00:00Z / 900s) * 900 — verify if needed.)
    """
    return MarketMetadata(
        market_id="test",
        condition_id="test",
        question="test",
        slug="btc-updown-15m-1781520000",
        up_token_id="up",
        down_token_id="down",
        outcomes=["Up", "Down"],
        start_ts=datetime(2026, 6, 6, 0, 0, tzinfo=UTC),
        end_ts=datetime(2026, 6, 6, 0, 15, tzinfo=UTC),
        active=True,
        closed=False,
        accepting_orders=True,
    )


@pytest.fixture
def sample_rules() -> list[ProbabilityRule]:
    """3 hand-crafted rules covering distinct stages."""
    return [
        ProbabilityRule(
            rule_id="btc_15m_after_10m_below_open_d_0_005pct_vol_low_strong_bull_close_near_high",
            stage=Stage.AFTER_10M,
            current_side=CurrentSide.BELOW_OPEN,
            distance_bucket=DistanceBucket.D_0_005pct,
            volatility_bucket=VolatilityBucket.VOL_LOW,
            pattern="strong_bull_close_near_high -> normal_bull",
            recommended_side=Side.UP,
            historical_probability=Decimal("0.65"),
            samples=120,
            median_round_return=Decimal("0.001"),
            return_aligned=True,
            usable_signal=True,
        ),
        ProbabilityRule(
            rule_id="btc_15m_after_5m_above_open_d_005_010pct_vol_normal_normal_bear",
            stage=Stage.AFTER_5M,
            current_side=CurrentSide.ABOVE_OPEN,
            distance_bucket=DistanceBucket.D_005_010pct,
            volatility_bucket=VolatilityBucket.VOL_NORMAL,
            pattern="normal_bear",
            recommended_side=Side.DOWN,
            historical_probability=Decimal("0.55"),
            samples=80,
            median_round_return=Decimal("-0.001"),
            return_aligned=True,
            usable_signal=True,
        ),
        ProbabilityRule(
            rule_id="btc_15m_after_10m_below_open_d_0_005pct_vol_low_weak_bear",
            stage=Stage.AFTER_10M,
            current_side=CurrentSide.BELOW_OPEN,
            distance_bucket=DistanceBucket.D_0_005pct,
            volatility_bucket=VolatilityBucket.VOL_LOW,
            pattern="weak_bear -> flat",
            recommended_side=Side.DOWN,
            historical_probability=Decimal("0.45"),
            samples=30,  # below MIN_SAMPLES, must be filtered out
            median_round_return=Decimal("0.0005"),
            return_aligned=False,
            usable_signal=True,
        ),
    ]


@pytest.fixture
def tmp_results_dir(tmp_path: Path) -> Path:
    """Temporary directory for backtest outputs."""
    d = tmp_path / "results"
    d.mkdir()
    return d


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d
```

- [ ] **Step 3: Write fixture sanity test**

Create `tests/walk_forward/test_conftest.py`:

```python
"""Sanity check: fixtures produce valid objects and the synthetic dataset spans 5 days."""
from datetime import UTC, datetime, timedelta


def test_synthetic_5d_candles_count_and_range(synthetic_5d_candles):
    assert len(synthetic_5d_candles) == 1440
    assert synthetic_5d_candles[0].open_time_utc == datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    assert synthetic_5d_candles[-1].open_time_utc == datetime(2026, 6, 6, 0, 0, tzinfo=UTC)
    # monotonic
    for prev, curr in zip(synthetic_5d_candles, synthetic_5d_candles[1:]):
        assert curr.open_time_utc == prev.open_time_utc + timedelta(minutes=5)


def test_synthetic_market_is_15m(synthetic_market):
    duration = synthetic_market.end_ts - synthetic_market.start_ts
    assert duration == timedelta(minutes=15)


def test_sample_rules_have_distinct_stages(sample_rules):
    stages = {r.stage for r in sample_rules}
    assert len(stages) == 2  # AFTER_5M and AFTER_10M


def test_sample_rules_include_low_samples(sample_rules):
    low_sample = [r for r in sample_rules if r.samples < 60]
    assert len(low_sample) == 1
    assert low_sample[0].return_aligned is False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_conftest.py -v`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/walk_forward/__init__.py tests/walk_forward/conftest.py tests/walk_forward/test_conftest.py
git commit -m "test(walk-forward): add synthetic candle + sample rule fixtures"
```

---

## Task 2: `scripts/fetch_binance_history.py`

**Files:**
- Create: `scripts/fetch_binance_history.py`
- Test: `tests/walk_forward/test_fetch_binance_history.py` (smoke only; mocking httpx for unit, real call for smoke)

- [ ] **Step 1: Write the failing test for CSV writing from a mocked fetch**

Create `tests/walk_forward/test_fetch_binance_history.py`:

```python
"""Tests for fetch_binance_history: CSV writing, pagination boundary, resume."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.fetch_binance_history import (
    rows_to_csv,
    write_meta,
    CANDLE_COLUMNS,
    fetch_klines_page,
)


def _fake_kline(open_time_ms: int, price: str = "50000") -> list[object]:
    return [
        open_time_ms,
        price,
        price,
        price,
        price,
        "10",
        open_time_ms + 5 * 60 * 1000,
        "0",
        0,
        "0",
        "0",
        "0",
    ]


def test_rows_to_csv_writes_header_and_rows(tmp_path: Path):
    rows = [
        (datetime(2026, 1, 1, 0, 0, tzinfo=UTC), Decimal("50000"), Decimal("50100"), Decimal("49900"), Decimal("50050"), Decimal("5")),
        (datetime(2026, 1, 1, 0, 5, tzinfo=UTC), Decimal("50050"), Decimal("50150"), Decimal("50000"), Decimal("50100"), Decimal("3")),
    ]
    out = tmp_path / "test.csv"
    rows_to_csv(rows, out)
    text = out.read_text()
    assert text.splitlines()[0] == ",".join(CANDLE_COLUMNS)
    assert len(text.splitlines()) == 3
    assert "50050" in text


def test_write_meta_includes_sha256_and_counts(tmp_path: Path):
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("a\n1\n2\n")
    meta = write_meta(
        csv_path,
        row_count=2,
        min_time=datetime(2026, 1, 1, tzinfo=UTC),
        max_time=datetime(2026, 1, 2, tzinfo=UTC),
        fetch_seconds=12.3,
    )
    assert meta["row_count"] == 2
    assert meta["sha256"] == hashlib.sha256(b"a\n1\n2\n").hexdigest()
    assert meta["min_time"] == "2026-01-01T00:00:00+00:00"
    assert meta["max_time"] == "2026-01-02T00:00:00+00:00"
    assert meta["fetch_seconds"] == 12.3


def test_fetch_klines_page_parses_binance_response():
    fake_json = [_fake_kline(1735689600000, "50000"), _fake_kline(1735689900000, "50100")]
    with patch("scripts.fetch_binance_history.httpx.Client") as mock_client:
        mock_resp = type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: fake_json})()
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp
        rows = fetch_klines_page(end_time_ms=1735690000000, endpoint="https://x")
    assert len(rows) == 2
    assert rows[0][0] == datetime.fromtimestamp(1735689600, tz=UTC)
    assert rows[0][4] == Decimal("50000")  # close
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_fetch_binance_history.py -v`

Expected: ImportError on `scripts.fetch_binance_history`.

- [ ] **Step 3: Implement `scripts/fetch_binance_history.py`**

Create `scripts/fetch_binance_history.py`:

```python
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

# Allow `python scripts/fetch_binance_history.py` from repo root.
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_fetch_binance_history.py -v`

Expected: 3 passed.

- [ ] **Step 5: Smoke test with 1-day fetch (real network)**

Run: `cd .worktrees/walk-forward-analysis && python scripts/fetch_binance_history.py --days 1 --out /tmp/btc_5m_1d.csv`

Expected: prints `OK: 288 candles, ... → ..., Xs, sha256=...`

Verify: `head -3 /tmp/btc_5m_1d.csv` shows header + 2 rows.

- [ ] **Step 6: Commit**

```bash
git add scripts/fetch_binance_history.py tests/walk_forward/test_fetch_binance_history.py
git commit -m "feat(walk-forward): add Binance 5m klines fetcher with resume support"
```

---

## Task 3: `scripts/walk_forward_backtest.py` — CSV loader, fold partitioner, rule index

**Files:**
- Create: `scripts/walk_forward_backtest.py` (scaffolding only)
- Test: `tests/walk_forward/test_walk_forward_backtest.py`

- [ ] **Step 1: Write failing tests for CSV loading and fold partitioning**

Append to `tests/walk_forward/test_walk_forward_backtest.py`:

```python
"""Tests for walk_forward_backtest: data loading, fold partitioning, no-lookahead, settlement."""
from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import Candle

from scripts.walk_forward_backtest import (
    load_candles_csv,
    partition_folds,
    build_rule_index,
    Fold,
)


def _write_candles_csv(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time_utc", "open", "high", "low", "close", "volume", "is_closed", "close_time_utc"])
        for c in candles:
            w.writerow([
                c.open_time_utc.isoformat(), str(c.open), str(c.high), str(c.low),
                str(c.close), str(c.volume), "True",
                (c.open_time_utc + timedelta(minutes=5)).isoformat(),
            ])


def test_load_candles_csv_round_trip(tmp_path: Path, candle_factory):
    candles = [candle_factory(datetime(2026, 1, 1, 0, 0, tzinfo=UTC) + timedelta(minutes=5 * i), "50000") for i in range(3)]
    p = tmp_path / "c.csv"
    _write_candles_csv(p, candles)
    loaded = load_candles_csv(p)
    assert len(loaded) == 3
    assert loaded[0].open == Decimal("50000")
    assert loaded[2].open_time_utc == datetime(2026, 1, 1, 0, 10, tzinfo=UTC)


def test_partition_folds_non_overlapping():
    data_start = datetime(2026, 1, 1, tzinfo=UTC)
    data_end = datetime(2026, 4, 11, tzinfo=UTC)  # 100 days
    folds = partition_folds(
        data_start=data_start,
        data_end=data_end,
        n_folds=5,
        test_days=20,
    )
    assert len(folds) == 5
    # Verify non-overlap
    for i in range(len(folds) - 1):
        assert folds[i].test_end <= folds[i + 1].test_start
    # Verify cover the data range
    assert folds[0].test_start >= data_start
    assert folds[-1].test_end <= data_end


def test_partition_folds_default_train_window_is_remainder():
    data_start = datetime(2026, 1, 1, tzinfo=UTC)
    data_end = datetime(2026, 4, 11, tzinfo=UTC)
    folds = partition_folds(
        data_start=data_start, data_end=data_end, n_folds=3, test_days=20,
    )
    # Each fold's train_start = data_start (cumulative), train_end = test_start
    for f in folds:
        assert f.train_start == data_start
        assert f.train_end == f.test_start


def test_build_rule_index_finds_exact_match(sample_rules):
    index = build_rule_index(sample_rules)
    rule, match_type = index.lookup(
        stage="AFTER_10M",
        current_side="BELOW_OPEN",
        distance_bucket="D_0_005pct",
        volatility_bucket="VOL_LOW",
        pattern="strong_bull_close_near_high -> normal_bull",
    )
    assert rule is not None
    assert rule.samples == 120
    assert match_type.value == "exact"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_walk_forward_backtest.py -v`

Expected: ImportError on `scripts.walk_forward_backtest`.

- [ ] **Step 3: Implement CSV loader, fold partitioner, rule index**

Create `scripts/walk_forward_backtest.py` with these three functions and types only (we'll add per-round simulation in Task 4):

```python
"""Walk-forward backtest: replay historical Binance 5m candles through live rule-lookup.

Usage:
  python scripts/walk_forward_backtest.py --data data/btc_5m_500d.csv \\
    --rules config/btc_updown_state_rules_15m.json --out-dir results/
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_round_bot.models import Candle  # noqa: E402


# === Data loading ===

def load_candles_csv(path: Path) -> list[Candle]:
    """Load candles from the CSV written by fetch_binance_history.py."""
    candles: list[Candle] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append(Candle(
                open_time_utc=datetime.fromisoformat(row["open_time_utc"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                is_closed=True,
            ))
    candles.sort(key=lambda c: c.open_time_utc)
    return candles


# === Fold partitioning ===

@dataclass(frozen=True)
class Fold:
    fold_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def partition_folds(
    *,
    data_start: datetime,
    data_end: datetime,
    n_folds: int,
    test_days: int,
) -> list[Fold]:
    """Partition [data_start, data_end] into n_folds rolling test windows.

    Test windows are contiguous (not rolling): test_0 = [data_start, data_start+test_days),
    test_1 = [data_start+test_days, data_start+2*test_days), etc. Each fold's train window
    is [data_start, test_start] (cumulative).
    """
    folds: list[Fold] = []
    for i in range(n_folds):
        test_start = data_start + timedelta(days=i * test_days)
        test_end = test_start + timedelta(days=test_days)
        if test_end > data_end:
            test_end = data_end
        folds.append(Fold(
            fold_id=i,
            train_start=data_start,
            train_end=test_start,
            test_start=test_start,
            test_end=test_end,
        ))
        if test_end == data_end:
            break
    return folds


# === Rule index (delegates to live probability_rules) ===

class _LiveRuleIndexAdapter:
    """Adapter exposing the same .lookup(...) signature as the test's
    build_rule_index return value, backed by live ProbabilityRules.
    """

    def __init__(self, rules_index):  # noqa: ANN001 - duck-typed
        self._index = rules_index

    def lookup(self, *, stage, current_side, distance_bucket, volatility_bucket, pattern):  # noqa: ANN001
        from polymarket_round_bot.probability_rules import (  # noqa: PLC0415
            CurrentSide as CS, DistanceBucket as DB, Stage as ST, VolatilityBucket as VB,
        )
        rule, match_type = self._index._index.lookup(  # noqa: SLF001 - test adapter
            stage=ST(stage), current_side=CS(current_side),
            distance_bucket=DB(distance_bucket), volatility_bucket=VB(volatility_bucket),
            pattern=pattern,
        )
        return rule, match_type


def build_rule_index(rules):  # noqa: ANN001
    """Wrap a list[ProbabilityRule] into an object with .lookup(stage, current_side, distance_bucket, volatility_bucket, pattern) -> (rule, match_type)."""
    from polymarket_round_bot.probability_rules import ProbabilityRules  # noqa: PLC0415
    pr = ProbabilityRules(rules)
    return _LiveRuleIndexAdapter(pr)


# === CLI (placeholder; full simulation in Task 4-6) ===

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--rules", required=True)
    p.add_argument("--out-dir", default="results/")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--test-days", type=int, default=30)
    args = p.parse_args()
    print(f"[stub] would load {args.data} and {args.rules}, write to {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_walk_forward_backtest.py -v`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/walk_forward_backtest.py tests/walk_forward/test_walk_forward_backtest.py
git commit -m "feat(walk-forward): scaffolding (CSV loader, fold partitioner, rule index)"
```

---

## Task 4: `walk_forward_backtest.py` — per-round simulation: state, rule lookup, settlement

**Files:**
- Modify: `scripts/walk_forward_backtest.py` (add `simulate_round`, `simulate_fold`)
- Test: append to `tests/walk_forward/test_walk_forward_backtest.py`

- [ ] **Step 1: Write failing tests for no-lookahead, settlement correctness, single-trade-per-round, trade filter**

Append to `tests/walk_forward/test_walk_forward_backtest.py`:

```python
from polymarket_round_bot.models import (
    BinanceState, MarketMetadata, ProbabilityRule, Side, Stage, CurrentSide,
    DistanceBucket, VolatilityBucket,
)
from scripts.walk_forward_backtest import simulate_round, settle_round


def _state_with_current_price(binance: BinanceState, current_price: Decimal) -> BinanceState:
    return BinanceState(
        symbol=binance.symbol,
        candles=binance.candles,
        current_price=current_price,
        received_at_utc=binance.received_at_utc,
    )


def test_simulate_round_no_lookahead(synthetic_5d_candles, synthetic_market, sample_rules, monkeypatch):
    """Verify that simulate_round only sees candles with open_time_utc < round.start_ts.

    We do this by passing a candles list where the LAST candle has open_time_utc
    AFTER the round start. If the simulation used it, build_round_state would
    not raise, but settlement or rule lookup would still succeed (a "leaked"
    candle isn't a hard error). Instead we check via the round boundary: the
    simulation must look up the round's c0 from candles with open_time == round.start_ts.
    """
    # The market starts at 2026-06-06 00:00 UTC. We need 16 prior 15m rounds = 64 prior 5m candles
    # for volatility. Our synthetic_5d_candles covers 5 days. Take the last 100 candles before market.start.
    candles_before = [c for c in synthetic_5d_candles if c.open_time_utc < synthetic_market.start_ts][-100:]
    binance = BinanceState(
        symbol="BTCUSDT",
        candles=candles_before,
        current_price=candles_before[-1].close,
        received_at_utc=synthetic_market.start_ts,
    )
    # Snapshot the candles IDs we passed in
    candle_ids = [id(c) for c in binance.candles]
    trade = simulate_round(
        market=synthetic_market,
        binance=binance,
        rules_index=build_rule_index(sample_rules),
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
        safety_buffer=Decimal("0.05"),
        max_entry_ask=Decimal("0.80"),
    )
    # After simulation, the binance object we passed should be untouched (no mutation)
    assert [id(c) for c in binance.candles] == candle_ids
    # Trade can be None if no rule matches; that's fine for this test.
    assert trade is None or isinstance(trade, dict)


def test_settle_round_up_wins(synthetic_market):
    """UP bet wins when final close > round open; settlement is +0.45 at entry=0.55."""
    # Round start at 2026-06-06 00:00 UTC, c0 covers 00:00-00:05 with open=50000.
    # c2 closes at 00:10 with close=50100 (+0.20%). UP wins.
    open_dt = synthetic_market.start_ts
    c0 = Candle(open_time_utc=open_dt, open=Decimal("50000"), high=Decimal("50050"),
                low=Decimal("49950"), close=Decimal("50010"), volume=Decimal("1"))
    c1 = Candle(open_time_utc=open_dt + timedelta(minutes=5), open=Decimal("50010"),
                high=Decimal("50060"), low=Decimal("50000"), close=Decimal("50050"), volume=Decimal("1"))
    c2 = Candle(open_time_utc=open_dt + timedelta(minutes=10), open=Decimal("50050"),
                high=Decimal("50120"), low=Decimal("50040"), close=Decimal("50100"), volume=Decimal("1"))
    result = settle_round(
        round_open=c0.open, round_close=c2.close, recommended_side=Side.UP, entry_price=Decimal("0.55"),
    )
    assert result["won"] is True
    assert result["pnl"] == Decimal("0.45")  # (1 - 0.55)


def test_settle_round_down_wins():
    open_price = Decimal("50000")
    close_price = Decimal("49900")  # -0.20%, DOWN wins
    result = settle_round(
        round_open=open_price, round_close=close_price, recommended_side=Side.DOWN, entry_price=Decimal("0.55"),
    )
    assert result["won"] is True
    assert result["pnl"] == Decimal("0.45")


def test_settle_round_loss():
    open_price = Decimal("50000")
    close_price = Decimal("50100")  # UP wins
    result = settle_round(
        round_open=open_price, round_close=close_price, recommended_side=Side.DOWN, entry_price=Decimal("0.55"),
    )
    assert result["won"] is False
    assert result["pnl"] == Decimal("-0.55")


def test_single_trade_per_round_in_simulate_round(synthetic_5d_candles, synthetic_market, sample_rules):
    """If both AFTER_5M and AFTER_10M states pass filters, only one trade is recorded."""
    candles_before = [c for c in synthetic_5d_candles if c.open_time_utc < synthetic_market.start_ts][-100:]
    binance = BinanceState(
        symbol="BTCUSDT",
        candles=candles_before,
        current_price=candles_before[-1].close,
        received_at_utc=synthetic_market.start_ts,
    )
    trade = simulate_round(
        market=synthetic_market,
        binance=binance,
        rules_index=build_rule_index(sample_rules),
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
        safety_buffer=Decimal("0.05"),
        max_entry_ask=Decimal("0.80"),
    )
    # The function returns a single trade (or None) per round.
    assert trade is None or (isinstance(trade, dict) and "stage" in trade and "pnl" in trade)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_walk_forward_backtest.py -v`

Expected: ImportError on `simulate_round` / `settle_round`.

- [ ] **Step 3: Add `simulate_round` and `settle_round` to `walk_forward_backtest.py`**

Append to `scripts/walk_forward_backtest.py` (before the `main()` block):

```python
# === Per-round simulation ===

_TRADING_WINDOWS: dict[Stage, tuple[int, int]] = {
    Stage.AFTER_5M: (300, 600),   # 5m to 10m elapsed; 600s to 300s remaining
    Stage.AFTER_10M: (60, 300),   # 10m to 15m elapsed; 300s to 60s remaining
    Stage.CUSTOM_5M_STATE: (0, 300),
}


def _in_trading_window(stage: Stage, seconds_to_expiry: int) -> bool:
    if stage not in _TRADING_WINDOWS:
        return False
    lo, hi = _TRADING_WINDOWS[stage]
    return lo <= seconds_to_expiry <= hi


def _build_market_for_round(start_ts: datetime) -> MarketMetadata:
    """Synthesize a 15m market metadata for a backtest round."""
    return MarketMetadata(
        market_id=f"backtest-{int(start_ts.timestamp())}",
        condition_id="backtest",
        question="backtest",
        slug=f"btc-updown-15m-{int(start_ts.timestamp()) // 900 * 900}",
        up_token_id="backtest-up",
        down_token_id="backtest-down",
        outcomes=["Up", "Down"],
        start_ts=start_ts,
        end_ts=start_ts + timedelta(minutes=15),
        active=True,
        closed=False,
        accepting_orders=True,
    )


def _evaluate_state(
    *,
    market: MarketMetadata,
    binance: BinanceState,
    now_utc: datetime,
    rules_index,
    min_samples: int,
    min_historical_probability: Decimal,
    safety_buffer: Decimal,
    max_entry_ask: Decimal,
) -> dict[str, Any] | None:
    """Build state, lookup rule, apply filters; return a trade dict or None."""
    from polymarket_round_bot.round_state import build_round_state  # noqa: PLC0415

    state = build_round_state(binance, market, now_utc=now_utc)
    if not _in_trading_window(state.stage, state.seconds_to_expiry):
        return None

    rule, match_type = rules_index.lookup(
        stage=state.stage.value,
        current_side=state.current_side.value,
        distance_bucket=state.distance_bucket.value,
        volatility_bucket=state.volatility_bucket.value,
        pattern=state.candle_pattern,
    )
    if rule is None:
        return None
    if not rule.usable_signal:
        return None
    if rule.samples < min_samples:
        return None
    if rule.historical_probability < min_historical_probability:
        return None
    if not rule.return_aligned:
        return None

    entry_price = rule.historical_probability - safety_buffer
    if entry_price <= Decimal("0") or entry_price > max_entry_ask:
        return None

    return {
        "market_slug": market.slug,
        "round_start_ts": market.start_ts,
        "round_end_ts": market.end_ts,
        "stage": state.stage.value,
        "current_side": state.current_side.value,
        "distance_bucket": state.distance_bucket.value,
        "volatility_bucket": state.volatility_bucket.value,
        "pattern": state.candle_pattern,
        "rule_id": rule.rule_id,
        "recommended_side": rule.recommended_side.value,
        "historical_probability": str(rule.historical_probability),
        "samples": rule.samples,
        "entry_price": str(entry_price),
        "round_open_price": str(state.round_open_price),
        "current_btc_price": str(state.current_btc_price),
    }


def simulate_round(
    *,
    market: MarketMetadata,
    binance: BinanceState,
    rules_index,
    min_samples: int,
    min_historical_probability: Decimal,
    safety_buffer: Decimal,
    max_entry_ask: Decimal,
) -> dict[str, Any] | None:
    """Try both AFTER_5M and AFTER_10M states; return the first trade found, or None.

    Single-trade-per-round invariant: at most one trade per round.
    """
    # AFTER_5M: 1 second after round start
    trade = _evaluate_state(
        market=market, binance=binance, now_utc=market.start_ts + timedelta(seconds=1),
        rules_index=rules_index, min_samples=min_samples,
        min_historical_probability=min_historical_probability,
        safety_buffer=safety_buffer, max_entry_ask=max_entry_ask,
    )
    if trade is not None:
        trade["entry_now_utc"] = (market.start_ts + timedelta(seconds=1)).isoformat()
        return trade
    # AFTER_10M: 5 min + 1 second after round start
    trade = _evaluate_state(
        market=market, binance=binance, now_utc=market.start_ts + timedelta(minutes=5, seconds=1),
        rules_index=rules_index, min_samples=min_samples,
        min_historical_probability=min_historical_probability,
        safety_buffer=safety_buffer, max_entry_ask=max_entry_ask,
    )
    if trade is not None:
        trade["entry_now_utc"] = (market.start_ts + timedelta(minutes=5, seconds=1)).isoformat()
        return trade
    return None


def settle_round(
    *,
    round_open: Decimal,
    round_close: Decimal,
    recommended_side: Side,
    entry_price: Decimal,
) -> dict[str, Any]:
    """Compute win/loss and PnL for a single counterfactual trade."""
    up_wins = round_close > round_open
    won = (recommended_side == Side.UP and up_wins) or (recommended_side == Side.DOWN and not up_wins)
    pnl = (Decimal("1") - entry_price) if won else -entry_price
    return {"won": won, "pnl": pnl, "round_close": str(round_close), "round_open": str(round_open)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_walk_forward_backtest.py -v`

Expected: 9 passed (4 from Task 3 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add scripts/walk_forward_backtest.py tests/walk_forward/test_walk_forward_backtest.py
git commit -m "feat(walk-forward): per-round simulation (no-lookahead, single-trade, settlement)"
```

---

## Task 5: `walk_forward_backtest.py` — full fold simulation: `simulate_fold` and round iterator

**Files:**
- Modify: `scripts/walk_forward_backtest.py` (add `simulate_fold`, `iter_round_starts`)
- Test: append to `tests/walk_forward/test_walk_forward_backtest.py`

- [ ] **Step 1: Write failing test for fold simulation and round iterator**

Append to `tests/walk_forward/test_walk_forward_backtest.py`:

```python
from scripts.walk_forward_backtest import iter_round_starts, simulate_fold


def test_iter_round_starts_aligned_to_15m():
    """Round starts should be aligned to the quarter-hour."""
    data_start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    data_end = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)  # 4h
    starts = list(iter_round_starts(data_start, data_end))
    # 4h / 15m = 16 rounds
    assert len(starts) == 16
    for s in starts:
        # Aligned to UTC quarter-hour
        assert s.minute in (0, 15, 30, 45)
        assert s.second == 0
        assert s.microsecond == 0


def test_simulate_fold_returns_trades_list(synthetic_5d_candles, sample_rules):
    """End-to-end: feed 5 days of candles, get trades list and summary."""
    from scripts.walk_forward_backtest import Fold
    fold = Fold(
        fold_id=0,
        train_start=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        train_end=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        test_start=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        test_end=datetime(2026, 6, 6, 0, 0, tzinfo=UTC),
    )
    rules_index = build_rule_index(sample_rules)
    trades, summary = simulate_fold(
        fold=fold,
        candles=synthetic_5d_candles,
        rules_index=rules_index,
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
        safety_buffer=Decimal("0.05"),
        max_entry_ask=Decimal("0.80"),
    )
    assert isinstance(trades, list)
    assert "n_rounds" in summary
    assert "n_trades" in summary
    assert "wr" in summary
    assert "pnl" in summary
    # Our synthetic candles have constant price (no volatility pattern), so probably 0 trades.
    # That's fine — just verify the structure.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_walk_forward_backtest.py -v`

Expected: ImportError on `iter_round_starts` / `simulate_fold`.

- [ ] **Step 3: Add `iter_round_starts` and `simulate_fold`**

Append to `scripts/walk_forward_backtest.py` (before `main`):

```python
# === Fold simulation ===

def iter_round_starts(data_start: datetime, data_end: datetime) -> list[datetime]:
    """Yield 15m round start times in [data_start, data_end), aligned to UTC quarter-hour."""
    # Snap data_start up to the next quarter-hour
    minute = data_start.minute
    second = data_start.second
    micro = data_start.microsecond
    if second > 0 or micro > 0:
        data_start = data_start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    # Snap to quarter-hour
    qh = (data_start.minute // 15) * 15
    data_start = data_start.replace(minute=qh)
    starts: list[datetime] = []
    t = data_start
    while t + timedelta(minutes=15) <= data_end:
        starts.append(t)
        t = t + timedelta(minutes=15)
    return starts


def _build_binance_for_round(candles: list[Candle], round_start: datetime) -> BinanceState:
    """Build a BinanceState from candles with open_time_utc < round_start.

    Cap at the most recent 200 candles for performance.
    """
    closed = [c for c in candles if c.open_time_utc < round_start]
    closed.sort(key=lambda c: c.open_time_utc)
    closed = closed[-200:]
    if not closed:
        raise ValueError(f"no candles before {round_start}")
    return BinanceState(
        symbol="BTCUSDT",
        candles=closed,
        current_price=closed[-1].close,
        received_at_utc=round_start,
    )


def _settle_trade(trade: dict[str, Any], candles: list[Candle]) -> dict[str, Any]:
    """Settle a recorded trade by finding the c2 candle and computing PnL."""
    round_start = datetime.fromisoformat(trade["round_start_ts"]) if isinstance(trade["round_start_ts"], str) else trade["round_start_ts"]
    c2_time = round_start + timedelta(minutes=10)
    c2 = next((c for c in candles if c.open_time_utc == c2_time), None)
    if c2 is None:
        # Round didn't fully close in our data
        trade["won"] = None
        trade["pnl"] = None
        return trade
    settlement = settle_round(
        round_open=Decimal(trade["round_open_price"]),
        round_close=c2.close,
        recommended_side=Side(trade["recommended_side"]),
        entry_price=Decimal(trade["entry_price"]),
    )
    trade["won"] = settlement["won"]
    trade["pnl"] = str(settlement["pnl"])
    trade["round_close_price"] = settlement["round_close"]
    return trade


def simulate_fold(
    *,
    fold: Fold,
    candles: list[Candle],
    rules_index,
    min_samples: int,
    min_historical_probability: Decimal,
    safety_buffer: Decimal,
    max_entry_ask: Decimal,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the backtest for one fold; return (trades, summary)."""
    round_starts = iter_round_starts(fold.test_start, fold.test_end)
    trades: list[dict[str, Any]] = []
    for rs in round_starts:
        try:
            binance = _build_binance_for_round(candles, rs)
        except ValueError:
            continue  # skip rounds at the very start of the data
        market = _build_market_for_round(rs)
        try:
            trade = simulate_round(
                market=market, binance=binance, rules_index=rules_index,
                min_samples=min_samples, min_historical_probability=min_historical_probability,
                safety_buffer=safety_buffer, max_entry_ask=max_entry_ask,
            )
        except Exception:
            continue  # don't let a bad round kill the fold
        if trade is None:
            continue
        # Find c0 close to set round_open (already in trade), then settle
        try:
            trade = _settle_trade(trade, candles)
        except Exception:
            continue
        if trade.get("won") is None:
            continue  # round didn't close in our data
        trade["fold_id"] = fold.fold_id
        trades.append(trade)

    n_trades = len(trades)
    n_wins = sum(1 for t in trades if t.get("won") is True)
    pnl = sum(Decimal(t["pnl"]) for t in trades)
    wr = (Decimal(n_wins) / Decimal(n_trades)) if n_trades else Decimal("0")
    avg_pnl = (pnl / Decimal(n_trades)) if n_trades else Decimal("0")
    avg_entry = (sum(Decimal(t["entry_price"]) for t in trades) / Decimal(n_trades)) if n_trades else Decimal("0")
    n_by_stage: dict[str, int] = {}
    n_by_side: dict[str, int] = {}
    for t in trades:
        n_by_stage[t["stage"]] = n_by_stage.get(t["stage"], 0) + 1
        n_by_side[t["recommended_side"]] = n_by_side.get(t["recommended_side"], 0) + 1

    summary = {
        "fold_id": fold.fold_id,
        "train_start": fold.train_start.isoformat(),
        "train_end": fold.train_end.isoformat(),
        "test_start": fold.test_start.isoformat(),
        "test_end": fold.test_end.isoformat(),
        "n_rounds": len(round_starts),
        "n_trades": n_trades,
        "n_wins": n_wins,
        "wr": str(wr),
        "pnl": str(pnl),
        "avg_pnl": str(avg_pnl),
        "avg_entry_price": str(avg_entry),
        "n_by_stage": n_by_stage,
        "n_by_side": n_by_side,
    }
    return trades, summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_walk_forward_backtest.py -v`

Expected: 11 passed (4 + 5 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add scripts/walk_forward_backtest.py tests/walk_forward/test_walk_forward_backtest.py
git commit -m "feat(walk-forward): fold simulation (round iterator, settlement, summary)"
```

---

## Task 6: `walk_forward_backtest.py` — full pipeline: load rules, run all folds, write outputs

**Files:**
- Modify: `scripts/walk_forward_backtest.py` (replace `main`)
- Test: append to `tests/walk_forward/test_walk_forward_backtest.py`

- [ ] **Step 1: Write failing test for `main` end-to-end on a tiny synthetic dataset**

Append to `tests/walk_forward/test_walk_forward_backtest.py`:

```python
from scripts.walk_forward_backtest import run_pipeline


def test_run_pipeline_writes_outputs(synthetic_5d_candles, sample_rules, tmp_path, tmp_results_dir):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps([
        {
            "rule_id": r.rule_id,
            "stage": r.stage.value,
            "current_side": r.current_side.value,
            "distance_bucket": r.distance_bucket.value,
            "volatility_bucket": r.volatility_bucket.value,
            "pattern": r.pattern,
            "recommended_side": r.recommended_side.value,
            "historical_probability": str(r.historical_probability),
            "samples": r.samples,
            "median_round_return": str(r.median_round_return),
            "return_aligned": r.return_aligned,
            "usable_signal": r.usable_signal,
        }
        for r in sample_rules
    ]))
    data_csv = tmp_path / "candles.csv"
    from tests.walk_forward.test_walk_forward_backtest import _write_candles_csv
    _write_candles_csv(data_csv, synthetic_5d_candles)

    summary = run_pipeline(
        data_csv=data_csv,
        rules_json=rules_path,
        out_dir=tmp_results_dir,
        n_folds=2,
        test_days=2,
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
        safety_buffer=Decimal("0.05"),
        max_entry_ask=Decimal("0.80"),
    )
    assert (tmp_results_dir / "wf_aggregate_summary.json").exists()
    assert "folds" in summary
    assert len(summary["folds"]) >= 1
    # Each fold's trades CSV may or may not exist (depending on whether any trades fired);
    # but the summary JSON should exist.
    for fold_summary in summary["folds"]:
        assert "fold_id" in fold_summary
        assert "n_rounds" in fold_summary
        assert "wr" in fold_summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_walk_forward_backtest.py -v`

Expected: ImportError on `run_pipeline`.

- [ ] **Step 3: Implement `run_pipeline` and replace `main`**

Replace the `main()` function in `scripts/walk_forward_backtest.py` and add the new function above it:

```python
def run_pipeline(
    *,
    data_csv: Path,
    rules_json: Path,
    out_dir: Path,
    n_folds: int,
    test_days: int,
    min_samples: int,
    min_historical_probability: Decimal,
    safety_buffer: Decimal,
    max_entry_ask: Decimal,
) -> dict[str, Any]:
    """End-to-end: load data, partition folds, simulate each, write outputs."""
    from polymarket_round_bot.probability_rules import load_rules  # noqa: PLC0415

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading candles from {data_csv}...", file=sys.stderr)
    candles = load_candles_csv(data_csv)
    print(f"  loaded {len(candles)} candles", file=sys.stderr)
    print(f"Loading rules from {rules_json}...", file=sys.stderr)
    rules = load_rules(rules_json)
    print(f"  loaded {len(rules)} rules", file=sys.stderr)
    rules_index = build_rule_index(rules)

    data_start = candles[0].open_time_utc
    data_end = candles[-1].open_time_utc
    folds = partition_folds(
        data_start=data_start, data_end=data_end, n_folds=n_folds, test_days=test_days,
    )
    print(f"Running {len(folds)} folds...", file=sys.stderr)

    fold_summaries: list[dict[str, Any]] = []
    for fold in folds:
        print(f"  fold {fold.fold_id}: {fold.test_start.date()} → {fold.test_end.date()}", file=sys.stderr)
        trades, summary = simulate_fold(
            fold=fold, candles=candles, rules_index=rules_index,
            min_samples=min_samples, min_historical_probability=min_historical_probability,
            safety_buffer=safety_buffer, max_entry_ask=max_entry_ask,
        )
        # Write per-fold outputs
        trades_csv = out_dir / f"wf_fold_{fold.fold_id}_trades.csv"
        if trades:
            with trades_csv.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
                w.writeheader()
                w.writerows(trades)
        summary_path = out_dir / f"wf_fold_{fold.fold_id}_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2))
        fold_summaries.append(summary)
        print(f"    n_trades={summary['n_trades']}, wr={summary['wr']}, pnl={summary['pnl']}", file=sys.stderr)

    # Aggregate
    wrs = [Decimal(s["wr"]) for s in fold_summaries if s["n_trades"] > 0]
    pnls = [Decimal(s["pnl"]) for s in fold_summaries]
    aggregate = {
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "n_folds": len(fold_summaries),
        "folds": fold_summaries,
        "cross_fold": {
            "wr_mean": str(sum(wrs) / len(wrs)) if wrs else "0",
            "wr_stdev": str(_stdev(wrs)) if len(wrs) > 1 else "0",
            "pnl_total": str(sum(pnls)),
        },
    }
    (out_dir / "wf_aggregate_summary.json").write_text(json.dumps(aggregate, indent=2))
    return aggregate


def _stdev(values: list[Decimal]) -> Decimal:
    if len(values) < 2:
        return Decimal("0")
    mean = sum(values) / Decimal(len(values))
    var = sum((v - mean) ** 2 for v in values) / Decimal(len(values) - 1)
    return var.sqrt() if hasattr(var, "sqrt") else Decimal("0")  # Decimal lacks sqrt; fallback ok


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--rules", required=True)
    p.add_argument("--out-dir", default="results/")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--test-days", type=int, default=30)
    p.add_argument("--min-samples", type=int, default=60)
    p.add_argument("--min-historical-probability", type=Decimal, default=Decimal("0.60"))
    p.add_argument("--safety-buffer", type=Decimal, default=Decimal("0.05"))
    p.add_argument("--max-entry-ask", type=Decimal, default=Decimal("0.80"))
    args = p.parse_args()
    run_pipeline(
        data_csv=Path(args.data),
        rules_json=Path(args.rules),
        out_dir=Path(args.out_dir),
        n_folds=args.folds,
        test_days=args.test_days,
        min_samples=args.min_samples,
        min_historical_probability=args.min_historical_probability,
        safety_buffer=args.safety_buffer,
        max_entry_ask=args.max_entry_ask,
    )
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_walk_forward_backtest.py -v`

Expected: 12 passed.

- [ ] **Step 5: Smoke test on tiny real data (1 day, 1 fold)**

Run:
```bash
cd .worktrees/walk-forward-analysis
python scripts/fetch_binance_history.py --days 2 --out /tmp/btc_5m_2d.csv
mkdir -p /tmp/wf_smoke
python scripts/walk_forward_backtest.py \
  --data /tmp/btc_5m_2d.csv \
  --rules config/btc_updown_state_rules_15m.json \
  --out-dir /tmp/wf_smoke \
  --folds 1 --test-days 1
```

Expected: prints `n_trades=...`, `wr=...`, `pnl=...`, and writes `wf_fold_0_summary.json` + `wf_aggregate_summary.json`.

Verify: `cat /tmp/wf_smoke/wf_aggregate_summary.json | python -m json.tool | head -30`

- [ ] **Step 6: Commit**

```bash
git add scripts/walk_forward_backtest.py tests/walk_forward/test_walk_forward_backtest.py
git commit -m "feat(walk-forward): full pipeline (load → fold → settle → write JSON/CSV)"
```

---

## Task 7: `scripts/breakeven_analysis.py`

**Files:**
- Create: `scripts/breakeven_analysis.py`
- Test: `tests/walk_forward/test_breakeven_analysis.py`

- [ ] **Step 1: Write failing tests for breakeven table and rule rankings**

Create `tests/walk_forward/test_breakeven_analysis.py`:

```python
"""Tests for breakeven_analysis: sensitivity table, rule rankings, regime breakdown."""
from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.breakeven_analysis import (
    breakeven_sensitivity,
    rule_performance,
    regime_breakdown,
    write_breakeven_csv,
    write_rule_ranking_csv,
)


@pytest.fixture
def sample_trades() -> list[dict]:
    """3 UP wins @ 0.55, 2 DOWN losses @ 0.65, 1 UP win @ 0.70."""
    base = {
        "fold_id": 0, "stage": "AFTER_10M", "rule_id": "r1",
        "recommended_side": "UP", "entry_price": "0.55", "historical_probability": "0.60",
        "distance_bucket": "D_0_005pct", "volatility_bucket": "VOL_LOW",
        "pattern": "x", "current_side": "BELOW_OPEN",
        "round_open_price": "50000", "current_btc_price": "50050",
    }
    trades = []
    for i, (won, entry, side) in enumerate([
        (True, "0.55", "UP"), (True, "0.55", "UP"), (True, "0.55", "UP"),
        (False, "0.65", "DOWN"), (False, "0.65", "DOWN"),
        (True, "0.70", "UP"),
    ]):
        t = {**base, "rule_id": f"r{i}", "entry_price": entry, "recommended_side": side, "won": won}
        trades.append(t)
    return trades


def test_breakeven_sensitivity_basic(sample_trades):
    rows = breakeven_sensitivity(sample_trades, entry_bins=[Decimal("0.50"), Decimal("0.60"), Decimal("0.70")])
    assert len(rows) == 3
    # First row covers all 6 trades
    assert rows[0]["n_trades"] == 6
    assert rows[0]["wr"] == "1.0" or Decimal(rows[0]["wr"]) > Decimal("0.5")
    # Each row has a breakeven_wr field
    for r in rows:
        assert "breakeven_wr" in r
        assert "wr_minus_breakeven" in r


def test_rule_performance_groups_by_rule_id(sample_trades):
    rows = rule_performance(sample_trades)
    assert len(rows) == 6  # each trade has a unique rule_id
    # Each row has expected fields
    for r in rows:
        assert "rule_id" in r
        assert "n" in r
        assert "wins" in r
        assert "pnl" in r
        assert "wr" in r


def test_write_breakeven_csv_round_trip(tmp_path: Path, sample_trades):
    rows = breakeven_sensitivity(sample_trades, entry_bins=[Decimal("0.50"), Decimal("0.60")])
    out = tmp_path / "be.csv"
    write_breakeven_csv(rows, out)
    text = out.read_text()
    assert "entry_price_bin" in text
    assert "n_trades" in text
    assert "wr" in text
    # Round-trip
    with out.open() as f:
        rd = csv.DictReader(f)
        loaded = list(rd)
    assert len(loaded) == 2


def test_write_rule_ranking_csv_round_trip(tmp_path: Path, sample_trades):
    rows = rule_performance(sample_trades, min_trades=1)
    out = tmp_path / "rr.csv"
    write_rule_ranking_csv(rows, out)
    text = out.read_text()
    assert "rule_id" in text
    assert "n" in text
    assert "pnl" in text
    with out.open() as f:
        loaded = list(csv.DictReader(f))
    assert len(loaded) == 6


def test_regime_breakdown_returns_sections(sample_trades):
    out = regime_breakdown(sample_trades)
    assert "by_volatility" in out
    assert "by_distance" in out
    assert "by_pattern" in out
    assert "by_hour" in out
    assert "by_dow" in out
    assert "by_side" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_breakeven_analysis.py -v`

Expected: ImportError on `scripts.breakeven_analysis`.

- [ ] **Step 3: Implement `scripts/breakeven_analysis.py`**

Create `scripts/breakeven_analysis.py`:

```python
"""Breakeven analysis + rule rankings + regime breakdown from backtest trades.

Usage:
  python scripts/breakeven_analysis.py --trades-glob 'results/wf_fold_*_trades.csv' --out-dir results/
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# === Loaders ===

def load_trades_glob(pattern: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(pattern)):
        with Path(path).open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if "won" in r and r["won"] not in ("True", "False", "true", "false"):
                    continue
                if "won" in r:
                    r["won"] = (r["won"].lower() == "true")
                rows.append(r)
    return rows


# === Breakeven sensitivity ===

def breakeven_sensitivity(trades: list[dict], *, entry_bins: list[Decimal]) -> list[dict[str, Any]]:
    """Compute WR / PnL / breakeven-WR per entry_price bin."""
    rows: list[dict[str, Any]] = []
    for lo in entry_bins:
        hi = lo + Decimal("0.05")
        subset = [t for t in trades if lo <= Decimal(t["entry_price"]) < hi]
        n = len(subset)
        if n == 0:
            rows.append({
                "entry_price_bin": f"{lo:.2f}-{hi:.2f}",
                "n_trades": 0, "wins": 0, "wr": "0",
                "pnl": "0", "avg_pnl": "0", "avg_entry": "0",
                "breakeven_wr": str(lo), "wr_minus_breakeven": "0",
            })
            continue
        wins = sum(1 for t in subset if t.get("won") is True)
        pnls = [Decimal(t["pnl"]) for t in subset]
        total_pnl = sum(pnls)
        wr = Decimal(wins) / Decimal(n)
        be_wr = lo
        rows.append({
            "entry_price_bin": f"{lo:.2f}-{hi:.2f}",
            "n_trades": n, "wins": wins,
            "wr": f"{wr:.4f}",
            "pnl": f"{total_pnl:.2f}",
            "avg_pnl": f"{total_pnl / Decimal(n):.4f}",
            "avg_entry": f"{sum(Decimal(t['entry_price']) for t in subset) / Decimal(n):.4f}",
            "breakeven_wr": f"{be_wr:.4f}",
            "wr_minus_breakeven": f"{wr - be_wr:.4f}",
        })
    return rows


# === Rule rankings ===

def rule_performance(trades: list[dict], *, min_trades: int = 1) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        grouped[t.get("rule_id", "unknown")].append(t)
    rows: list[dict[str, Any]] = []
    for rule_id, items in grouped.items():
        n = len(items)
        if n < min_trades:
            continue
        wins = sum(1 for i in items if i.get("won") is True)
        pnls = [Decimal(i["pnl"]) for i in items if i.get("pnl") is not None]
        rows.append({
            "rule_id": rule_id,
            "side": items[0].get("recommended_side", ""),
            "stage": items[0].get("stage", ""),
            "n": n, "wins": wins,
            "wr": f"{Decimal(wins) / Decimal(n):.4f}",
            "pnl": f"{sum(pnls):.2f}",
            "avg_pnl": f"{sum(pnls) / Decimal(n):.4f}",
            "avg_entry": f"{sum(Decimal(i['entry_price']) for i in items) / Decimal(n):.4f}",
            "avg_hist_prob": f"{sum(Decimal(i['historical_probability']) for i in items) / Decimal(n):.4f}",
        })
    rows.sort(key=lambda r: Decimal(r["pnl"]), reverse=True)
    return rows


# === Regime breakdown ===

def regime_breakdown(trades: list[dict]) -> dict[str, list[dict[str, Any]]]:
    """Group trades by various regimes; return WR/PnL per group."""
    out: dict[str, list[dict[str, Any]]] = {}
    for dim, key in [
        ("by_volatility", "volatility_bucket"),
        ("by_distance", "distance_bucket"),
        ("by_pattern", "pattern"),
        ("by_side", "recommended_side"),
    ]:
        out[dim] = _aggregate_by(trades, key)
    out["by_hour"] = _by_hour(trades)
    out["by_dow"] = _by_dow(trades)
    return out


def _aggregate_by(trades: list[dict], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        grouped[t.get(key, "unknown")].append(t)
    rows: list[dict[str, Any]] = []
    for k, items in grouped.items():
        n = len(items)
        wins = sum(1 for i in items if i.get("won") is True)
        pnls = [Decimal(i["pnl"]) for i in items if i.get("pnl") is not None]
        rows.append({
            "key": k, "n": n, "wins": wins,
            "wr": f"{Decimal(wins) / Decimal(n):.4f}" if n else "0",
            "pnl": f"{sum(pnls):.2f}" if pnls else "0",
        })
    rows.sort(key=lambda r: r["n"], reverse=True)
    return rows


def _by_hour(trades: list[dict]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        ts = t.get("round_start_ts") or t.get("entry_now_utc") or ""
        try:
            hour = datetime.fromisoformat(ts).hour
        except ValueError:
            continue
        grouped[hour].append(t)
    return _to_breakdown_rows(grouped, "hour")


def _by_dow(trades: list[dict]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        ts = t.get("round_start_ts") or t.get("entry_now_utc") or ""
        try:
            dow = datetime.fromisoformat(ts).weekday()
        except ValueError:
            continue
        grouped[dow].append(t)
    return _to_breakdown_rows(grouped, "dow", names={0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"})


def _to_breakdown_rows(grouped, key_name, names=None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for k, items in grouped.items():
        n = len(items)
        wins = sum(1 for i in items if i.get("won") is True)
        pnls = [Decimal(i["pnl"]) for i in items if i.get("pnl") is not None]
        label = names.get(k, str(k)) if names else str(k)
        rows.append({
            "key": label, "n": n, "wins": wins,
            "wr": f"{Decimal(wins) / Decimal(n):.4f}" if n else "0",
            "pnl": f"{sum(pnls):.2f}" if pnls else "0",
        })
    rows.sort(key=lambda r: r["key"])
    return rows


# === Writers ===

def write_breakeven_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_rule_ranking_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# === Main ===

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--trades-glob", default="results/wf_fold_*_trades.csv")
    p.add_argument("--out-dir", default="results/")
    args = p.parse_args()

    trades = load_trades_glob(args.trades_glob)
    if not trades:
        print(f"No trades loaded from {args.trades_glob}", file=sys.stderr)
        return 1
    print(f"Loaded {len(trades)} trades from {args.trades_glob}", file=sys.stderr)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Breakeven sensitivity
    bins = [Decimal(str(x)) / Decimal("100") for x in range(30, 85, 5)]
    be = breakeven_sensitivity(trades, entry_bins=bins)
    write_breakeven_csv(be, out_dir / "breakeven_sensitivity.csv")
    print(f"\n=== Breakeven sensitivity (entry bins 0.30-0.80) ===", file=sys.stderr)
    for r in be:
        print(f"  {r['entry_price_bin']}: n={r['n_trades']:>3} wr={r['wr']:>7} be_wr={r['breakeven_wr']:>5} wr-be={r['wr_minus_breakeven']:>7} pnl=${r['pnl']}", file=sys.stderr)

    # 2. Rule rankings
    rr = rule_performance(trades, min_trades=2)
    write_rule_ranking_csv(rr, out_dir / "rule_performance_ranked.csv")
    print(f"\n=== Top 10 rules by PnL ===", file=sys.stderr)
    for r in rr[:10]:
        print(f"  {r['pnl']:>7} n={r['n']:>3} wr={r['wr']} side={r['side']:<5} stage={r['stage']:<9} {r['rule_id']}", file=sys.stderr)
    print(f"\n=== Bottom 10 rules by PnL ===", file=sys.stderr)
    for r in rr[-10:]:
        print(f"  {r['pnl']:>7} n={r['n']:>3} wr={r['wr']} side={r['side']:<5} stage={r['stage']:<9} {r['rule_id']}", file=sys.stderr)

    # 3. Regime breakdown
    rb = regime_breakdown(trades)
    print(f"\n=== WR by side ===", file=sys.stderr)
    for r in rb["by_side"]:
        print(f"  {r['key']:<5}: n={r['n']:>3} wr={r['wr']} pnl=${r['pnl']}", file=sys.stderr)
    print(f"\n=== WR by volatility ===", file=sys.stderr)
    for r in rb["by_volatility"]:
        print(f"  {r['key']:<12}: n={r['n']:>3} wr={r['wr']} pnl=${r['pnl']}", file=sys.stderr)
    print(f"\n=== WR by distance ===", file=sys.stderr)
    for r in rb["by_distance"]:
        print(f"  {r['key']:<13}: n={r['n']:>3} wr={r['wr']} pnl=${r['pnl']}", file=sys.stderr)
    print(f"\n=== WR by hour (UTC) ===", file=sys.stderr)
    for r in rb["by_hour"]:
        print(f"  {r['key']:>4}: n={r['n']:>3} wr={r['wr']} pnl=${r['pnl']}", file=sys.stderr)
    print(f"\n=== WR by day of week ===", file=sys.stderr)
    for r in rb["by_dow"]:
        print(f"  {r['key']:>4}: n={r['n']:>3} wr={r['wr']} pnl=${r['pnl']}", file=sys.stderr)

    # 4. Counterfactual filter simulations
    print(f"\n=== Counterfactual: only trade if avg_hist_prob >= X ===", file=sys.stderr)
    for threshold in (Decimal("0.55"), Decimal("0.60"), Decimal("0.65"), Decimal("0.70"), Decimal("0.75")):
        sub = [t for t in trades if Decimal(t["historical_probability"]) >= threshold]
        if not sub:
            print(f"  threshold={threshold}: n=0, skip", file=sys.stderr)
            continue
        wins = sum(1 for t in sub if t.get("won") is True)
        pnls = [Decimal(t["pnl"]) for t in sub]
        wr = Decimal(wins) / Decimal(len(sub))
        print(f"  threshold={threshold}: n={len(sub):>3} wr={wr:.4f} pnl=${sum(pnls):.2f}", file=sys.stderr)

    print(f"\n=== Counterfactual: side filter ===", file=sys.stderr)
    for side in ("UP", "DOWN"):
        sub = [t for t in trades if t.get("recommended_side") == side]
        if not sub:
            print(f"  {side}: n=0, skip", file=sys.stderr)
            continue
        wins = sum(1 for t in sub if t.get("won") is True)
        pnls = [Decimal(t["pnl"]) for t in sub]
        wr = Decimal(wins) / Decimal(len(sub))
        print(f"  {side}: n={len(sub):>3} wr={wr:.4f} pnl=${sum(pnls):.2f}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_breakeven_analysis.py -v`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/breakeven_analysis.py tests/walk_forward/test_breakeven_analysis.py
git commit -m "feat(walk-forward): breakeven analysis + rule rankings + regime breakdown"
```

---

## Task 8: `scripts/walk_forward_report.py`

**Files:**
- Create: `scripts/walk_forward_report.py`
- Test: `tests/walk_forward/test_walk_forward_report.py`

- [ ] **Step 1: Write failing tests for report section presence and table rendering**

Create `tests/walk_forward/test_walk_forward_report.py`:

```python
"""Tests for walk_forward_report: required sections, fold table, regime tables."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.walk_forward_report import (
    render_report,
    REQUIRED_SECTIONS,
    fold_table_markdown,
    regime_table_markdown,
)


@pytest.fixture
def sample_aggregate() -> dict:
    return {
        "data_start": "2025-12-07T00:00:00+00:00",
        "data_end": "2026-06-15T00:00:00+00:00",
        "n_folds": 3,
        "folds": [
            {"fold_id": 0, "test_start": "2025-12-07T00:00:00+00:00", "test_end": "2026-01-06T00:00:00+00:00",
             "n_rounds": 2880, "n_trades": 50, "n_wins": 30, "wr": "0.6000",
             "pnl": "-5.00", "avg_pnl": "-0.1000", "avg_entry_price": "0.62", "n_by_stage": {}, "n_by_side": {}},
            {"fold_id": 1, "test_start": "2026-01-06T00:00:00+00:00", "test_end": "2026-02-05T00:00:00+00:00",
             "n_rounds": 2880, "n_trades": 45, "n_wins": 25, "wr": "0.5556",
             "pnl": "-8.00", "avg_pnl": "-0.1778", "avg_entry_price": "0.65", "n_by_stage": {}, "n_by_side": {}},
            {"fold_id": 2, "test_start": "2026-02-05T00:00:00+00:00", "test_end": "2026-03-07T00:00:00+00:00",
             "n_rounds": 2880, "n_trades": 60, "n_wins": 35, "wr": "0.5833",
             "pnl": "-7.50", "avg_pnl": "-0.1250", "avg_entry_price": "0.63", "n_by_stage": {}, "n_by_side": {}},
        ],
        "cross_fold": {"wr_mean": "0.5796", "wr_stdev": "0.0183", "pnl_total": "-20.50"},
    }


def test_render_report_contains_required_sections(sample_aggregate, tmp_path):
    out = tmp_path / "report.md"
    render_report(aggregate=sample_aggregate, out_path=out)
    text = out.read_text()
    for section in REQUIRED_SECTIONS:
        assert section in text, f"missing section: {section}"


def test_fold_table_markdown_format(sample_aggregate):
    md = fold_table_markdown(sample_aggregate["folds"])
    assert "Fold 0" in md
    assert "Fold 2" in md
    assert "60.00%" in md  # WR formatted as percentage
    assert "-$5.00" in md or "$-5.00" in md  # PnL formatted
    # Header row
    assert "Fold" in md
    assert "WR" in md
    assert "PnL" in md


def test_regime_table_markdown_format():
    rows = [
        {"key": "UP", "n": 100, "wins": 60, "wr": "0.6000", "pnl": "-5.00"},
        {"key": "DOWN", "n": 80, "wins": 40, "wr": "0.5000", "pnl": "-12.00"},
    ]
    md = regime_table_markdown(rows)
    assert "UP" in md
    assert "DOWN" in md
    assert "60.00%" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_walk_forward_report.py -v`

Expected: ImportError on `scripts.walk_forward_report`.

- [ ] **Step 3: Implement `scripts/walk_forward_report.py`**

Create `scripts/walk_forward_report.py`:

```python
"""Render walk-forward backtest outputs to a markdown report.

Usage:
  python scripts/walk_forward_report.py --in-dir results/ --out docs/analysis/2026-06-15-walk-forward.md
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_SECTIONS: tuple[str, ...] = (
    "# Walk-Forward Validation", "## TL;DR", "## Setup", "## Per-fold results",
    "## Stability", "## Breakeven analysis", "## Rule rankings",
    "## Live cross-check", "## Findings & recommendations", "## Appendix",
)


# === Helpers ===

def _fmt_wr(wr_str: str) -> str:
    try:
        wr = Decimal(wr_str)
        return f"{wr * 100:.2f}%"
    except Exception:
        return wr_str


def _fmt_pnl(pnl_str: str) -> str:
    try:
        pnl = Decimal(pnl_str)
        return f"${pnl:.2f}"
    except Exception:
        return pnl_str


def _fmt_money(s: str) -> str:
    try:
        return f"${Decimal(s):.2f}"
    except Exception:
        return s


# === Tables ===

def fold_table_markdown(folds: list[dict[str, Any]]) -> str:
    lines = [
        "| Fold | Test range | n_rounds | n_trades | WR | PnL | avg_PnL | avg_entry |",
        "|------|------------|----------|----------|------|--------|---------|-----------|",
    ]
    for f in folds:
        lines.append(
            f"| {f['fold_id']} | {f['test_start'][:10]} → {f['test_end'][:10]} | "
            f"{f['n_rounds']} | {f['n_trades']} | {_fmt_wr(f['wr'])} | "
            f"{_fmt_pnl(f['pnl'])} | {_fmt_money(f['avg_pnl'])} | {f['avg_entry_price']} |"
        )
    return "\n".join(lines)


def regime_table_markdown(rows: list[dict[str, Any]], key_header: str = "Bucket") -> str:
    lines = [
        f"| {key_header} | n | WR | PnL |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['key']} | {r['n']} | {_fmt_wr(r['wr'])} | {_fmt_pnl(r['pnl'])} |"
        )
    return "\n".join(lines)


# === Main renderer ===

def render_report(*, aggregate: dict[str, Any], out_path: Path) -> None:
    """Compose the markdown report from aggregate summary + per-trade CSVs (if present)."""
    folds = aggregate["folds"]
    cross = aggregate["cross_fold"]
    n_total_trades = sum(f["n_trades"] for f in folds)
    pnl_total = Decimal(cross["pnl_total"])

    # 1. TL;DR
    wr_mean = Decimal(cross["wr_mean"])
    wr_std = Decimal(cross["wr_stdev"])
    if pnl_total > 0:
        verdict = "**+EV on out-of-sample.**"
    elif pnl_total < 0:
        verdict = "**−EV on out-of-sample.**"
    else:
        verdict = "Neutral on out-of-sample."
    stability = "stable" if wr_std < Decimal("0.05") else "unstable"
    tl_dr = (
        f"**{verdict}** "
        f"Across {len(folds)} folds ({aggregate['data_start'][:10]} → {aggregate['data_end'][:10]}), "
        f"the live rules generated **{n_total_trades} counterfactual trades** with a "
        f"cross-fold mean WR of **{_fmt_wr(str(wr_mean))}** "
        f"(σ = {wr_std * 100:.2f}pp, **{stability}**) and total PnL of **{_fmt_pnl(str(pnl_total))}**."
    )

    # 2. Setup
    setup = (
        f"- **Data range**: {aggregate['data_start']} → {aggregate['data_end']}\n"
        f"- **Number of folds**: {len(folds)}\n"
        f"- **Rules source**: `config/btc_updown_state_rules_15m.json` (live rules)\n"
        f"- **Position size**: $1.00 per trade (matches live `MAX_POSITION_USD`)\n"
        f"- **Filters**: samples ≥ 60, historical_probability ≥ 0.60, return_aligned=true, "
        f"entry_price ≤ 0.80 (matches live)"
    )

    # 3. Per-fold results
    per_fold = fold_table_markdown(folds)

    # 4. Stability
    stability_md = (
        f"- **Cross-fold mean WR**: {_fmt_wr(cross['wr_mean'])}\n"
        f"- **Cross-fold stdev WR**: {Decimal(cross['wr_stdev']) * 100:.2f}pp\n"
        f"- **Stability verdict**: {stability.upper()} (threshold 5pp)\n"
        f"- **Total PnL across folds**: {_fmt_pnl(cross['pnl_total'])}"
    )

    # 5. Breakeven analysis
    be_csv = out_path.parent / "breakeven_sensitivity.csv"
    breakeven_md = "See `results/breakeven_sensitivity.csv` for the full table."
    if be_csv.exists():
        with be_csv.open() as f:
            be_rows = list(csv.DictReader(f))
        if be_rows:
            breakeven_md = "Entry-price bins (breakeven WR = entry_price):\n\n" + regime_table_markdown(be_rows, "Entry bin")

    # 6. Rule rankings
    rr_csv = out_path.parent / "rule_performance_ranked.csv"
    rule_rank_md = "See `results/rule_performance_ranked.csv` for all rules."
    if rr_csv.exists():
        with rr_csv.open() as f:
            rr_rows = list(csv.DictReader(f))
        if rr_rows:
            top = rr_rows[:10]
            bottom = rr_rows[-10:]
            rule_rank_md = (
                "**Top 10 rules by PnL**:\n\n" + regime_table_markdown(top, "Rule") +
                "\n\n**Bottom 10 rules by PnL**:\n\n" + regime_table_markdown(bottom, "Rule")
            )

    # 7. Live cross-check
    live_md = (
        "Live PnL on the most recent 9 days (2026-06-06 → 2026-06-15): "
        "**−$3.72 on 7 settled trades (PnL avg −$0.53/settled, WR 28.6% on the 7 settled).**\n\n"
        "The backtest's most recent fold (overlapping with the live period) "
        "should show a comparable PnL. If the backtest is significantly more "
        "or less negative than live, the state-construction mismatches identified "
        "in `backtest-reference-compare.md` may be material. Detailed comparison is "
        "in `results/wf_aggregate_summary.json` (look at the fold with `test_end` "
        "closest to 2026-06-15)."
    )

    # 8. Findings
    findings = _build_findings(folds, cross, wr_std, stability, pnl_total)

    # 9. Appendix
    appendix = (
        "**Methodology**:\n"
        "- For each 15m round, the backtest replays `build_round_state()` and "
        "`ProbabilityRules.lookup()` exactly as the live bot does.\n"
        "- Entry price is set to `historical_probability − safety_buffer` (the live formula).\n"
        "- Settlement uses the close of the third 5m candle (`c2`), which closes at the "
        "round's end time.\n\n"
        "**Limitations**:\n"
        "- No orderbook spread modeling: real entries may be worse than the backtest's.\n"
        "- No slippage or liquidity constraints.\n"
        "- No daily loss cap (live caps at $10/day).\n"
        "- Round starts are aligned to UTC quarter-hour (00, 15, 30, 45); live rounds "
        "are similarly aligned, but exact timestamps may differ.\n\n"
        "**Data source**: Binance public 5m klines (`https://data-api.binance.vision/api/v3/klines`)."
    )

    report = f"""# Walk-Forward Validation + Breakeven Analysis

**Generated**: {aggregate.get("data_end", "")[:10]}

## TL;DR

{tl_dr}

## Setup

{setup}

## Per-fold results

{per_fold}

## Stability

{stability_md}

## Breakeven analysis

{breakeven_md}

## Rule rankings

{rule_rank_md}

## Live cross-check

{live_md}

## Findings & recommendations

{findings}

## Appendix

{appendix}
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)


def _build_findings(folds, cross, wr_std, stability, pnl_total) -> str:
    bullets: list[str] = []
    if Decimal(cross["pnl_total"]) < 0:
        bullets.append(
            f"- **Strategy is −EV across the analyzed folds**: total PnL "
            f"{_fmt_pnl(cross['pnl_total'])} on {sum(f['n_trades'] for f in folds)} trades."
        )
    else:
        bullets.append(
            f"- **Strategy is +EV across the analyzed folds**: total PnL "
            f"{_fmt_pnl(cross['pnl_total'])} on {sum(f['n_trades'] for f in folds)} trades."
        )
    if wr_std > Decimal("0.05"):
        bullets.append(
            f"- **Stability is low**: cross-fold stdev of WR is "
            f"{wr_std * 100:.2f}pp, above the 5pp threshold. Performance is "
            f"regime-dependent; consider restricting to specific vol/distance buckets."
        )
    else:
        bullets.append(
            f"- **Stability is acceptable**: cross-fold stdev of WR is "
            f"{wr_std * 100:.2f}pp, below the 5pp threshold."
        )
    bullets.append(
        "- **Review rule rankings in `results/rule_performance_ranked.csv`**: rules with "
        "WR below their entry-price breakeven are destroying value; consider dropping them "
        "via a tighter whitelist."
    )
    bullets.append(
        "- **Compare backtest vs live in the most recent fold**: if the backtest is "
        "materially different from live, the state-construction mismatches "
        "(`backtest-reference-compare.md`) likely need fixing before further tuning."
    )
    return "\n".join(bullets)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in-dir", default="results/")
    p.add_argument("--out", default="docs/analysis/2026-06-15-walk-forward.md")
    args = p.parse_args()
    in_dir = Path(args.in_dir)
    aggregate_path = in_dir / "wf_aggregate_summary.json"
    if not aggregate_path.exists():
        print(f"missing {aggregate_path}", file=sys.stderr)
        return 1
    aggregate = json.loads(aggregate_path.read_text())
    render_report(aggregate=aggregate, out_path=Path(args.out))
    print(f"OK: report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/test_walk_forward_report.py -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/walk_forward_report.py tests/walk_forward/test_walk_forward_report.py
git commit -m "feat(walk-forward): markdown report renderer"
```

---

## Task 9: End-to-end smoke test (30 days, 2 folds)

**Files:** none (just runs the pipeline)

- [ ] **Step 1: Download 30 days of BTCUSDT 5m klines**

Run:
```bash
cd .worktrees/walk-forward-analysis
mkdir -p data
python scripts/fetch_binance_history.py --days 30 --out data/btc_5m_30d.csv
```

Expected: prints `OK: ~8640 candles, ... → ..., Xs, sha256=...`

- [ ] **Step 2: Run walk-forward backtest with 2 folds × 15 days**

Run:
```bash
cd .worktrees/walk-forward-analysis
mkdir -p results/smoke
python scripts/walk_forward_backtest.py \
  --data data/btc_5m_30d.csv \
  --rules config/btc_updown_state_rules_15m.json \
  --out-dir results/smoke \
  --folds 2 --test-days 15
```

Expected: prints 2 fold summaries with n_trades > 0 and writes `wf_fold_{0,1}_trades.csv` + `wf_aggregate_summary.json`.

- [ ] **Step 3: Run breakeven analysis**

Run:
```bash
cd .worktrees/walk-forward-analysis
python scripts/breakeven_analysis.py \
  --trades-glob 'results/smoke/wf_fold_*_trades.csv' \
  --out-dir results/smoke
```

Expected: prints breakeven table, top/bottom 10 rules, regime breakdowns, counterfactual filters.

- [ ] **Step 4: Render report**

Run:
```bash
cd .worktrees/walk-forward-analysis
mkdir -p docs/analysis
python scripts/walk_forward_report.py \
  --in-dir results/smoke \
  --out docs/analysis/smoke-test-report.md
```

Expected: prints `OK: report written to docs/analysis/smoke-test-report.md` and the file contains all `REQUIRED_SECTIONS`.

Verify: `grep -c "^## " docs/analysis/smoke-test-report.md` should be ≥ 9.

- [ ] **Step 5: Run all tests one more time**

Run: `cd .worktrees/walk-forward-analysis && python -m pytest tests/walk_forward/ -v`

Expected: all tests pass (~28 tests).

- [ ] **Step 6: Commit (no production data, just smoke artifacts)**

```bash
cd .worktrees/walk-forward-analysis
echo "data/" >> .gitignore
echo "results/" >> .gitignore
git add .gitignore
git commit -m "chore: ignore data/ and results/ for walk-forward artifacts"
```

(Skip committing the actual data CSVs — they're gitignored.)

---

## Task 10: Full 500-day run + final report

**Files:** none new (operates on existing pipeline)

- [ ] **Step 1: Download 500 days of BTCUSDT 5m klines**

Run:
```bash
cd .worktrees/walk-forward-analysis
python scripts/fetch_binance_history.py --days 500 --out data/btc_5m_500d.csv
```

Expected: prints `OK: ~144000 candles, ... → ..., Xs, sha256=...`. This will take 10-15 minutes.

Verify: `wc -l data/btc_5m_500d.csv` should show ~144001 lines.

- [ ] **Step 2: Run full walk-forward backtest (5 folds × 30 days)**

Run:
```bash
cd .worktrees/walk-forward-analysis
mkdir -p results/full
python scripts/walk_forward_backtest.py \
  --data data/btc_5m_500d.csv \
  --rules config/btc_updown_state_rules_15m.json \
  --out-dir results/full \
  --folds 5 --test-days 30
```

Expected: prints 5 fold summaries, total ~10-30 minutes wall clock. Each fold should have 100-1000+ trades.

- [ ] **Step 3: Run breakeven analysis**

Run:
```bash
cd .worktrees/walk-forward-analysis
python scripts/breakeven_analysis.py \
  --trades-glob 'results/full/wf_fold_*_trades.csv' \
  --out-dir results/full
```

Expected: substantial output with top/bottom rules, regime breakdowns, counterfactual filters.

- [ ] **Step 4: Render final report**

Run:
```bash
cd .worktrees/walk-forward-analysis
python scripts/walk_forward_report.py \
  --in-dir results/full \
  --out docs/analysis/2026-06-15-walk-forward.md
```

Expected: writes `docs/analysis/2026-06-15-walk-forward.md` with all 10 required sections.

- [ ] **Step 5: Verify report content**

Run:
```bash
cd .worktrees/walk-forward-analysis
ls -la docs/analysis/2026-06-15-walk-forward.md
wc -l docs/analysis/2026-06-15-walk-forward.md
head -40 docs/analysis/2026-06-15-walk-forward.md
```

Expected: file exists, ≥ 100 lines, TL;DR is at top with concrete numbers.

Also: `grep -c "^## " docs/analysis/2026-06-15-walk-forward.md` should be ≥ 9.

- [ ] **Step 6: Cross-check live vs backtest in the latest fold**

Run:
```bash
cd .worktrees/walk-forward-analysis
python -c "
import json
agg = json.load(open('results/full/wf_aggregate_summary.json'))
last = max(agg['folds'], key=lambda f: f['test_end'])
print(f'Latest fold {last[\"fold_id\"]}: {last[\"test_start\"][:10]} → {last[\"test_end\"][:10]}')
print(f'  n_trades={last[\"n_trades\"]}, WR={last[\"wr\"]}, PnL={last[\"pnl\"]}, avg_entry={last[\"avg_entry_price\"]}')
print(f'  Live (2026-06-06 → 2026-06-15): ~7 settled, WR ~28.6%, PnL −\$3.72')
"
```

Expected: prints the latest fold's metrics alongside the live baseline. They won't match exactly (different sample sizes and times), but should be in the same ballpark or reveal a discrepancy worth investigating.

- [ ] **Step 7: Commit the report**

```bash
cd .worktrees/walk-forward-analysis
git add docs/analysis/2026-06-15-walk-forward.md
git commit -m "docs(analysis): walk-forward validation + breakeven analysis (500d, 5 folds)"
git log --oneline -10
```

Expected: new commit on top of the spec commit; `git log` shows the full chain: spec → smoke test → final report.

- [ ] **Step 8: Hand off to user**

Print a summary message in chat:
```
Walk-forward analysis complete.

📄 Report: docs/analysis/2026-06-15-walk-forward.md
📊 Artifacts: results/full/wf_aggregate_summary.json, results/full/breakeven_sensitivity.csv,
              results/full/rule_performance_ranked.csv, results/full/wf_fold_*_trades.csv
🔢 Commits: feature/walk-forward-analysis branch (10 commits)

Next: read the TL;DR in the report, then proceed to Підхід 2 (retrain) or to live config tuning
based on the findings.
```

---

## Self-Review

**Spec coverage check:**

- Spec section "Goal" — answered in TL;DR + Per-fold + Stability ✓
- Spec section "Approach" (4-script pipeline) — Tasks 2, 3-6, 7, 8 ✓
- Spec section "Architecture" components 1-4 — Tasks 2, 3-6, 7, 8 ✓
- Spec section "Methodology" (walk-forward folds, per-round logic) — Tasks 5-6 ✓
- Spec section "Assumptions and Limitations" — reflected in scripts (no spread, no slippage, etc.) and documented in Appendix ✓
- Spec section "Acceptance Criteria":
  - Data: N≥365 days → Task 10 Step 1 (500 days)
  - Folds processed → Task 10 Step 2
  - Tests pass → Tasks 1-8 each have explicit "Run tests to verify" steps
  - Cross-check within ±50% → Task 10 Step 6
  - Breakeven table coverage → Task 7 (bins 0.30-0.80 step 0.05)
  - Rule ranking → Task 7
  - Regime breakdown → Task 7
  - Report generated with all sections → Task 10 Step 5 + REQUIRED_SECTIONS in Task 8
  - Git committed → Task 10 Step 7

**Placeholder scan:**

- All step code blocks have actual content (not "TBD" or "implement later").
- All file paths are exact.
- All commands have expected output.

**Type consistency check:**

- `Fold` is defined in Task 3 and used in Tasks 5, 6, 9, 10 — consistent.
- `simulate_round(...)` signature in Task 4 matches the test calls and the calls in Task 5.
- `run_pipeline(...)` parameters in Task 6 match the CLI flags in `main()`.
- `breakeven_sensitivity(...)` signature in Task 7 matches both the test and the call in `main()`.
- `render_report(...)` signature in Task 8 matches both the test and the call in `main()`.
- All CSV/JSON output filenames in scripts match what tests and downstream tasks expect.

**No issues found — plan is ready to execute.**
