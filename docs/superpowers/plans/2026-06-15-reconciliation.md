# Live vs Backtest Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Identify the source of the 40pp WR gap between live settlements and backtest counterfactual trades by joining them on `market_slug` and comparing state, entry, and settlement fields, producing a verdict (A: filter, B: state construction, C: settlement, D: insufficient data) and a markdown report.

**Architecture:** Read-only offline analysis. Reuse existing `walk_forward_backtest.py` (with new `--test-start` / `--test-end` flags) to run on the live period. New script `reconcile_live_vs_backtest.py` joins live settlements with backtest trades, compares 10+ fields, and categorizes the gap. New `reconciliation_report.py` renders a markdown verdict. TDD: `tests/walk_forward/test_reconcile.py` covers match logic, field comparison, verdict categorization.

**Tech Stack:** Python 3.11+, pydantic v2, sqlite3 stdlib, csv stdlib. No new dependencies.

---

## File Structure

**Modified files:**
- `scripts/walk_forward_backtest.py` — add `--test-start` / `--test-end` flags to `run_pipeline` and `main` (small extension)
- `tests/walk_forward/test_walk_forward_backtest.py` — add 1 test for explicit-window mode

**New files:**
- `scripts/reconcile_live_vs_backtest.py` — live↔backtest joiner, field comparison, verdict
- `scripts/reconciliation_report.py` — CSV/JSON → markdown
- `tests/walk_forward/test_reconcile.py` — match, comparison, verdict tests

**Operational artifacts (gitignored):**
- `data/live_paper.sqlite` — scp'd from server
- `data/live_paper.sqlite.sha256` — integrity check
- `results/recon/wf_fold_0_trades.csv` — backtest on live period
- `results/recon/matched_pairs.csv` — side-by-side comparison
- `results/recon/live_only_trades.csv` — live slugs with no backtest match
- `results/recon/backtest_only_trades.csv` — backtest slugs with no live match
- `results/recon/reconciliation_summary.json` — verdict + counts
- `docs/analysis/2026-06-15-reconciliation.md` — main report (committed)

**Reused (read-only):**
- `data/btc_5m_500d.csv` (144k candles, already downloaded)
- `config/btc_updown_state_rules_15m.json` (2,014 rules)
- `scripts/walk_forward_backtest.py::run_pipeline` (with new flags)

---

## Task 1: Pull live DB snapshot

**Files:** none (operational)

- [ ] **Step 1: Create local data/ directory and scp live DB**

Run:
```bash
cd .worktrees/walk-forward-analysis
mkdir -p data
scp -i ~/.ssh/polymarket-mm-key.pem \
  ubuntu@54.154.79.239:/home/ubuntu/polymarket_updown_bot/data/polymarket_round_paper.sqlite \
  data/live_paper.sqlite
```

Expected: prints `live_paper.sqlite 100% ...`, no error.

- [ ] **Step 2: Verify size and integrity**

Run:
```bash
ls -la data/live_paper.sqlite
sha256sum data/live_paper.sqlite | tee data/live_paper.sqlite.sha256
file data/live_paper.sqlite
```

Expected: file size > 200 MB (today's DB is 223 MB). `sha256` line starts with hex hash. `file` reports "SQLite 3.x database".

- [ ] **Step 3: Quick sanity check via SQL**

Run:
```bash
cd .worktrees/walk-forward-analysis
PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -c "
import sqlite3
db = sqlite3.connect('data/live_paper.sqlite')
c = db.cursor()
c.execute('SELECT COUNT(*) FROM settlements')
print(f'settlements: {c.fetchone()[0]}')
c.execute('SELECT MIN(resolved_at_utc), MAX(resolved_at_utc) FROM settlements')
print(f'settlements range: {c.fetchone()}')
c.execute('SELECT COUNT(*) FROM decisions')
print(f'decisions: {c.fetchone()[0]}')
"
```

Expected: `settlements: 254+` (could be a few more than 14:04 UTC if bot traded since then). `settlements range: 2026-06-06... → 2026-06-15...`. `decisions: 130k+`.

- [ ] **Step 4: Commit .gitignore update (data/ is already ignored, but confirm)**

Run:
```bash
cd .worktrees/walk-forward-analysis
grep -E "^(data|results)/" .gitignore
git status -s
```

Expected: `.gitignore` contains `data/` and `results/`. `git status -s` shows no untracked files in `data/` or `results/` (they're ignored).

If `data/` is NOT in `.gitignore`, append it:
```bash
echo "" >> .gitignore
echo "# Local data snapshots" >> .gitignore
echo "data/live_paper.sqlite" >> .gitignore
echo "data/live_paper.sqlite.sha256" >> .gitignore
git add .gitignore
git -c user.email=agent@local -c user.name=agent commit -m "chore: ignore live DB snapshot"
```

---

## Task 2: Add `--test-start` / `--test-end` flags to `walk_forward_backtest.py`

**Files:**
- Modify: `scripts/walk_forward_backtest.py` (add CLI flags, branch in `run_pipeline`)
- Test: append to `tests/walk_forward/test_walk_forward_backtest.py`

- [ ] **Step 1: Write failing test for explicit-window mode**

Append to `tests/walk_forward/test_walk_forward_backtest.py`:

```python
def test_run_pipeline_with_explicit_window(synthetic_5d_candles, sample_rules, tmp_path, tmp_results_dir):
    """When --test-start/--test-end are provided, the script builds a single fold with those boundaries."""
    from scripts.walk_forward_backtest import run_pipeline
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
        n_folds=5, test_days=30,  # ignored when explicit window is set
        test_start=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        test_end=datetime(2026, 6, 3, 0, 0, tzinfo=UTC),
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
        safety_buffer=Decimal("0.05"),
        max_entry_ask=Decimal("0.80"),
    )
    assert len(summary["folds"]) == 1
    assert summary["folds"][0]["test_start"].startswith("2026-06-01")
    assert summary["folds"][0]["test_end"].startswith("2026-06-03")
```

- [ ] **Step 2: Run test to verify it fails (function doesn't accept test_start/test_end yet)**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_walk_forward_backtest.py -v -k explicit_window`

Expected: TypeError: unexpected keyword argument.

- [ ] **Step 3: Add `test_start` / `test_end` params to `run_pipeline` and CLI flags**

In `scripts/walk_forward_backtest.py`, modify `run_pipeline` signature and the fold-building block:

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
    test_start: datetime | None = None,
    test_end: datetime | None = None,
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

    # If explicit test window is provided, build a single fold from it.
    if test_start is not None and test_end is not None:
        if test_start < data_start:
            test_start = data_start
        if test_end > data_end:
            test_end = data_end
        folds = [Fold(
            fold_id=0,
            train_start=data_start,
            train_end=test_start,
            test_start=test_start,
            test_end=test_end,
        )]
        print(f"Using explicit test window: {test_start} → {test_end}", file=sys.stderr)
    else:
        folds = partition_folds(
            data_start=data_start, data_end=data_end, n_folds=n_folds, test_days=test_days,
        )
    print(f"Running {len(folds)} folds...", file=sys.stderr)
    # ... (rest of function unchanged)
```

In the same file, modify `main()` to add the new CLI flags and pass them through:

```python
def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--rules", required=True)
    p.add_argument("--out-dir", default="results/")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--test-days", type=int, default=30)
    p.add_argument("--test-start", type=str, default=None,
                   help="ISO datetime, e.g. 2026-06-06T11:51:00+00:00. If set with --test-end, builds a single fold.")
    p.add_argument("--test-end", type=str, default=None,
                   help="ISO datetime. If set with --test-start, builds a single fold.")
    p.add_argument("--min-samples", type=int, default=60)
    p.add_argument("--min-historical-probability", type=Decimal, default=Decimal("0.60"))
    p.add_argument("--safety-buffer", type=Decimal, default=Decimal("0.05"))
    p.add_argument("--max-entry-ask", type=Decimal, default=Decimal("0.80"))
    args = p.parse_args()

    test_start_dt = datetime.fromisoformat(args.test_start) if args.test_start else None
    test_end_dt = datetime.fromisoformat(args.test_end) if args.test_end else None

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
        test_start=test_start_dt,
        test_end=test_end_dt,
    )
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_walk_forward_backtest.py -v -k explicit_window`

Expected: 1 passed.

- [ ] **Step 5: Run full test suite to verify no regression**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/`

Expected: 29 passed (was 28; +1 for explicit_window).

- [ ] **Step 6: Commit**

```bash
git add scripts/walk_forward_backtest.py tests/walk_forward/test_walk_forward_backtest.py
git commit -m "feat(walk-forward): add --test-start/--test-end flags for explicit windows"
```

---

## Task 3: Run backtest on the live period (operational)

**Files:** none new (uses existing scripts)

- [ ] **Step 1: Run backtest on 2026-06-06 → 2026-06-15**

Run:
```bash
cd .worktrees/walk-forward-analysis
mkdir -p results/recon
PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python scripts/walk_forward_backtest.py \
  --data data/btc_5m_500d.csv \
  --rules config/btc_updown_state_rules_15m.json \
  --out-dir results/recon \
  --test-start 2026-06-06T00:00:00+00:00 \
  --test-end 2026-06-15T15:00:00+00:00
```

Expected: prints `Using explicit test window: ...`, then `fold 0: ...`, with n_trades > 0.

- [ ] **Step 2: Verify output**

Run:
```bash
cd .worktrees/walk-forward-analysis
head -3 results/recon/wf_fold_0_trades.csv
wc -l results/recon/wf_fold_0_trades.csv
cat results/recon/wf_aggregate_summary.json | head -20
```

Expected: CSV has header + ≥ 1 row. `wf_aggregate_summary.json` has one fold with test dates in 2026-06-06 to 2026-06-15.

Note: results in `results/recon/` are gitignored. No commit needed.

---

## Task 4: `reconcile_live_vs_backtest.py` — data loading + matching

**Files:**
- Create: `scripts/reconcile_live_vs_backtest.py` (scaffolding)
- Test: `tests/walk_forward/test_reconcile.py`

- [ ] **Step 1: Write failing tests for data loading and slug matching**

Create `tests/walk_forward/test_reconcile.py`:

```python
"""Tests for reconciliation: data loading, slug matching, field comparison, verdict logic."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.reconcile_live_vs_backtest import (
    load_live_settlements,
    load_live_decisions,
    load_backtest_trades,
    match_by_slug,
    compare_pair,
    categorize_verdict,
    REQUIRED_LIVE_SETTLEMENT_COLS,
    REQUIRED_BACKTEST_TRADE_COLS,
)


@pytest.fixture
def live_db(tmp_path: Path) -> Path:
    """Create a tiny SQLite with settlements and paper_positions tables matching live schema."""
    db = tmp_path / "live.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE settlements (
            settlement_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            resolved_outcome TEXT NOT NULL,
            selected_side TEXT NOT NULL,
            won INTEGER NOT NULL,
            entry_price TEXT NOT NULL,
            shares TEXT NOT NULL,
            cost_usd TEXT NOT NULL,
            payout_usd TEXT NOT NULL,
            realized_pnl_usd TEXT NOT NULL,
            realized_roi_pct TEXT NOT NULL,
            settlement_source TEXT NOT NULL,
            round_open_price TEXT NOT NULL,
            round_close_price TEXT NOT NULL,
            final_btc_price TEXT NOT NULL,
            resolved_at_utc TEXT NOT NULL,
            trade_quality TEXT NOT NULL,
            edge_at_entry TEXT NOT NULL,
            spread_at_entry TEXT NOT NULL,
            rule_id TEXT,
            historical_probability_at_entry TEXT NOT NULL,
            seconds_to_expiry_at_entry INTEGER NOT NULL
        );
        CREATE TABLE paper_positions (
            position_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            event_url TEXT,
            selected_side TEXT NOT NULL,
            token_id TEXT NOT NULL,
            entry_timestamp_utc TEXT NOT NULL,
            entry_price TEXT NOT NULL,
            entry_best_ask TEXT NOT NULL,
            entry_best_bid TEXT NOT NULL,
            entry_spread TEXT NOT NULL,
            entry_size_usd TEXT NOT NULL,
            shares TEXT NOT NULL,
            fair_price_at_entry TEXT NOT NULL,
            max_buy_price_at_entry TEXT NOT NULL,
            edge_at_entry TEXT NOT NULL,
            round_open_price TEXT NOT NULL,
            btc_price_at_entry TEXT NOT NULL,
            distance_bucket_at_entry TEXT NOT NULL,
            volatility_bucket_at_entry TEXT NOT NULL,
            pattern_at_entry TEXT NOT NULL,
            stage_at_entry TEXT NOT NULL,
            seconds_to_expiry_at_entry INTEGER NOT NULL,
            current_side_at_entry TEXT NOT NULL,
            status TEXT NOT NULL,
            rule_id TEXT,
            rule_match_type TEXT NOT NULL,
            historical_probability_at_entry TEXT NOT NULL,
            samples_at_entry INTEGER NOT NULL
        );
        CREATE TABLE decisions (
            decision_id TEXT PRIMARY KEY,
            timestamp_utc TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            event_url TEXT,
            timeframe TEXT NOT NULL,
            round_start_ts TEXT NOT NULL,
            round_end_ts TEXT NOT NULL,
            seconds_to_expiry INTEGER NOT NULL,
            stage TEXT NOT NULL,
            side_checked TEXT NOT NULL,
            selected_side TEXT,
            outcome_token_id TEXT,
            opposite_token_id TEXT,
            decision TEXT NOT NULL,
            skip_reason TEXT,
            round_open_price TEXT NOT NULL,
            current_btc_price TEXT NOT NULL,
            current_side TEXT NOT NULL,
            distance_from_round_open TEXT NOT NULL,
            distance_bucket TEXT NOT NULL,
            volatility_bucket TEXT NOT NULL,
            candle_pattern TEXT NOT NULL,
            pattern_combo TEXT,
            c0_open TEXT, c0_high TEXT, c0_low TEXT, c0_close TEXT, c0_volume TEXT,
            c1_open TEXT, c1_high TEXT, c1_low TEXT, c1_close TEXT, c1_volume TEXT,
            source_exchange TEXT NOT NULL,
            source_symbol TEXT NOT NULL,
            binance_data_received_at_utc TEXT NOT NULL,
            binance_data_age_seconds TEXT NOT NULL,
            up_best_bid TEXT, up_best_ask TEXT, down_best_bid TEXT, down_best_ask TEXT,
            up_spread TEXT, down_spread TEXT,
            selected_best_bid TEXT, selected_best_ask TEXT, selected_spread TEXT,
            selected_ask_size TEXT, selected_bid_size TEXT,
            orderbook_depth_top_5_json TEXT NOT NULL,
            liquidity_usd_estimate TEXT,
            market_active INTEGER NOT NULL, market_closed INTEGER NOT NULL, market_accepting_orders INTEGER NOT NULL,
            orderbook_received_at_utc TEXT NOT NULL, orderbook_age_seconds TEXT NOT NULL,
            metadata_received_at_utc TEXT NOT NULL, metadata_age_seconds TEXT NOT NULL,
            rule_id TEXT, rule_match_type TEXT NOT NULL, samples INTEGER NOT NULL,
            historical_probability TEXT, fair_price TEXT, safety_buffer TEXT NOT NULL,
            max_buy_price TEXT, market_ask TEXT, edge_vs_ask TEXT, min_edge_required TEXT NOT NULL,
            recommended_side TEXT, return_aligned INTEGER NOT NULL,
            requested_size_usd TEXT NOT NULL, max_position_usd TEXT NOT NULL,
            open_positions_count INTEGER NOT NULL, max_open_positions INTEGER NOT NULL,
            daily_realized_pnl TEXT NOT NULL, max_daily_loss_usd TEXT NOT NULL,
            risk_allowed INTEGER NOT NULL, risk_reject_reason TEXT
        );
    """)
    con.commit()
    return db


def _insert_settlement(con, slug, won=1, entry="0.55", pnl="0.45", rule="r1", hist_prob="0.60"):
    con.execute(
        "INSERT INTO settlements (settlement_id, position_id, market_slug, resolved_outcome, selected_side, "
        "won, entry_price, shares, cost_usd, payout_usd, realized_pnl_usd, realized_roi_pct, "
        "settlement_source, round_open_price, round_close_price, final_btc_price, resolved_at_utc, "
        "trade_quality, edge_at_entry, spread_at_entry, rule_id, historical_probability_at_entry, "
        "seconds_to_expiry_at_entry) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"sett-{slug}", f"pos-{slug}", slug, "UP", "UP", won, entry, "1.0",
         entry, "1.0", pnl, "0.81", "BINANCE_FALLBACK", "50000", "50100", "50100",
         "2026-06-15T12:00:00+00:00", "GOOD_WIN", "0.05", "0.01", rule, hist_prob, 300),
    )


def _insert_position(con, slug, stage="AFTER_10M", vol="VOL_LOW", dist="D_0_005pct",
                    side="BELOW_OPEN", pattern="strong_bull -> normal_bull"):
    con.execute(
        "INSERT INTO paper_positions (position_id, decision_id, market_slug, event_url, selected_side, "
        "token_id, entry_timestamp_utc, entry_price, entry_best_ask, entry_best_bid, entry_spread, "
        "entry_size_usd, shares, fair_price_at_entry, max_buy_price_at_entry, edge_at_entry, "
        "round_open_price, btc_price_at_entry, distance_bucket_at_entry, volatility_bucket_at_entry, "
        "pattern_at_entry, stage_at_entry, seconds_to_expiry_at_entry, current_side_at_entry, status, "
        "rule_id, rule_match_type, historical_probability_at_entry, samples_at_entry) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"pos-{slug}", f"dec-{slug}", slug, None, "UP", "tok", "2026-06-15T11:50:00+00:00",
         "0.55", "0.55", "0.54", "0.01", "1.0", "1.0", "0.60", "0.55", "0.05",
         "50000", "50050", dist, vol, pattern, stage, 300, side, "SETTLED",
         "r1", "exact", "0.60", 120),
    )


def test_load_live_settlements_returns_rows(live_db):
    con = sqlite3.connect(live_db)
    _insert_position(con, "slug-A")
    _insert_settlement(con, "slug-A")
    _insert_position(con, "slug-B")
    _insert_settlement(con, "slug-B", won=0, pnl="-0.55")
    con.commit()
    con.close()

    rows = load_live_settlements(live_db, period_start=datetime(2026, 6, 1, tzinfo=UTC))
    assert len(rows) == 2
    for r in rows:
        assert "market_slug" in r
        assert "stage_at_entry" in r
        assert "pattern_at_entry" in r


def test_load_backtest_trades_from_csv(tmp_path: Path):
    csv_path = tmp_path / "trades.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(REQUIRED_BACKTEST_TRADE_COLS))
        w.writeheader()
        w.writerow({k: "x" for k in REQUIRED_BACKTEST_TRADE_COLS})
        w.writerow({k: "x" for k in REQUIRED_BACKTEST_TRADE_COLS})
    rows = load_backtest_trades(csv_path)
    assert len(rows) == 2


def test_match_by_slug_keys():
    live = [{"market_slug": "slug-A", "entry_price": "0.55"}, {"market_slug": "slug-B", "entry_price": "0.60"}]
    backtest = [{"market_slug": "slug-A", "entry_price": "0.50"}, {"market_slug": "slug-C", "entry_price": "0.65"}]
    matched, live_only, backtest_only = match_by_slug(live, backtest)
    assert len(matched) == 1
    assert matched[0][0]["market_slug"] == "slug-A"
    assert len(live_only) == 1
    assert live_only[0]["market_slug"] == "slug-B"
    assert len(backtest_only) == 1
    assert backtest_only[0]["market_slug"] == "slug-C"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_reconcile.py -v`

Expected: ImportError on `scripts.reconcile_live_vs_backtest`.

- [ ] **Step 3: Implement scaffolding (loaders + matcher only)**

Create `scripts/reconcile_live_vs_backtest.py`:

```python
"""Reconcile live settlements with backtest counterfactual trades.

Usage:
  python scripts/reconcile_live_vs_backtest.py \\
    --live-db data/live_paper.sqlite \\
    --backtest-trades results/recon/wf_fold_0_trades.csv \\
    --out-dir results/recon/
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_LIVE_SETTLEMENT_COLS: tuple[str, ...] = (
    "market_slug", "selected_side", "won", "entry_price", "realized_pnl_usd",
    "settlement_source", "rule_id", "historical_probability_at_entry",
    "round_open_price", "round_close_price", "resolved_at_utc",
    "stage_at_entry", "volatility_bucket_at_entry", "distance_bucket_at_entry",
    "current_side_at_entry", "pattern_at_entry",
)

REQUIRED_BACKTEST_TRADE_COLS: tuple[str, ...] = (
    "market_slug", "stage", "current_side", "distance_bucket", "volatility_bucket",
    "pattern", "rule_id", "recommended_side", "historical_probability",
    "entry_price", "won", "pnl", "round_open_price", "round_close_price",
)


def load_live_settlements(live_db: Path, *, period_start: datetime) -> list[dict[str, Any]]:
    """Load live settlements + paper_positions fields for the period >= period_start.

    Joins settlements with paper_positions to get stage_at_entry, vol_bucket, etc.
    """
    if not live_db.exists():
        raise FileNotFoundError(f"live DB not found: {live_db}")
    con = sqlite3.connect(live_db)
    con.row_factory = sqlite3.Row
    try:
        cursor = con.execute(
            """
            SELECT s.market_slug, s.selected_side, s.won, s.entry_price, s.realized_pnl_usd,
                   s.settlement_source, s.rule_id, s.historical_probability_at_entry,
                   s.round_open_price, s.round_close_price, s.resolved_at_utc,
                   p.stage_at_entry, p.volatility_bucket_at_entry, p.distance_bucket_at_entry,
                   p.current_side_at_entry, p.pattern_at_entry
            FROM settlements s
            LEFT JOIN paper_positions p ON p.position_id = s.position_id
            WHERE s.resolved_at_utc >= ?
            ORDER BY s.resolved_at_utc
            """,
            (period_start.isoformat(),),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        con.close()


def load_live_decisions(live_db: Path, *, period_start: datetime) -> list[dict[str, Any]]:
    """Load live TRADE decisions (for diagnostics)."""
    if not live_db.exists():
        raise FileNotFoundError(f"live DB not found: {live_db}")
    con = sqlite3.connect(live_db)
    con.row_factory = sqlite3.Row
    try:
        cursor = con.execute(
            """
            SELECT market_slug, timestamp_utc, stage, side_checked, selected_side,
                   candle_pattern, volatility_bucket, distance_bucket, current_side,
                   rule_id, historical_probability, market_ask, edge_vs_ask,
                   decision, skip_reason
            FROM decisions
            WHERE decision = 'TRADE' AND timestamp_utc >= ?
            ORDER BY timestamp_utc
            """,
            (period_start.isoformat(),),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        con.close()


def load_backtest_trades(csv_path: Path) -> list[dict[str, Any]]:
    """Load backtest trades CSV (wf_fold_*_trades.csv)."""
    if not csv_path.exists():
        raise FileNotFoundError(f"backtest trades CSV not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            # Coerce known boolean fields
            if "won" in r:
                r["won"] = (r["won"].lower() == "true") if r["won"] else None
            rows.append(r)
    return rows


def match_by_slug(
    live: list[dict[str, Any]],
    backtest: list[dict[str, Any]],
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Match live and backtest trades by market_slug.

    Returns (matched_pairs, live_only, backtest_only).
    """
    live_by_slug: dict[str, list[dict]] = {}
    for r in live:
        slug = r.get("market_slug", "")
        live_by_slug.setdefault(slug, []).append(r)
    backtest_by_slug: dict[str, list[dict]] = {}
    for r in backtest:
        slug = r.get("market_slug", "")
        backtest_by_slug.setdefault(slug, []).append(r)

    matched: list[tuple[dict, dict]] = []
    live_only: list[dict] = []
    backtest_only: list[dict] = []

    for slug, live_rows in live_by_slug.items():
        if slug in backtest_by_slug:
            # 1:1 match (MAX_OPEN_POSITIONS=1 invariant)
            live_row = live_rows[0]
            back_row = backtest_by_slug[slug][0]
            matched.append((live_row, back_row))
        else:
            live_only.extend(live_rows)

    for slug, back_rows in backtest_by_slug.items():
        if slug not in live_by_slug:
            backtest_only.extend(back_rows)

    return matched, live_only, backtest_only


# === CLI (scaffolding; field comparison + verdict in Task 5) ===

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live-db", required=True)
    p.add_argument("--backtest-trades", required=True)
    p.add_argument("--out-dir", default="results/recon/")
    p.add_argument("--period-start", default="2026-06-06T11:51:00+00:00",
                   help="ISO datetime; only load live settlements after this")
    p.add_argument("--entry-tolerance", type=Decimal, default=Decimal("0.01"))
    args = p.parse_args()

    period_start = datetime.fromisoformat(args.period_start)
    live = load_live_settlements(Path(args.live_db), period_start=period_start)
    backtest = load_backtest_trades(Path(args.backtest_trades))
    matched, live_only, backtest_only = match_by_slug(live, backtest)
    print(f"live: {len(live)}, backtest: {len(backtest)}, matched: {len(matched)}, "
          f"live_only: {len(live_only)}, backtest_only: {len(backtest_only)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_reconcile.py -v`

Expected: 3 passed.

- [ ] **Step 5: Smoke test against real live DB**

Run:
```bash
cd .worktrees/walk-forward-analysis
PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python scripts/reconcile_live_vs_backtest.py \
  --live-db data/live_paper.sqlite \
  --backtest-trades results/recon/wf_fold_0_trades.csv \
  --out-dir /tmp/recon_smoke
```

Expected: prints `live: ~254, backtest: ~N, matched: ~7, live_only: ~247, backtest_only: ~N-7`. The "live_only: ~247" reflects that the 247 historical settlements are from before 2026-06-06, OR the backtest on the live period didn't cover all 7. Either is informative.

- [ ] **Step 6: Commit**

```bash
git add scripts/reconcile_live_vs_backtest.py tests/walk_forward/test_reconcile.py
git commit -m "feat(reconcile): data loaders + slug matcher"
```

---

## Task 5: `reconcile_live_vs_backtest.py` — field comparison + verdict

**Files:**
- Modify: `scripts/reconcile_live_vs_backtest.py` (add `compare_pair`, `categorize_verdict`)
- Test: append to `tests/walk_forward/test_reconcile.py`

- [ ] **Step 1: Write failing tests for comparison and verdict**

Append to `tests/walk_forward/test_reconcile.py`:

```python
def test_compare_pair_state_mismatch():
    """If state fields differ, comparison should flag the diffs."""
    live = {
        "market_slug": "slug-A",
        "selected_side": "UP",
        "won": 1, "entry_price": "0.55", "realized_pnl_usd": "0.45",
        "rule_id": "r1", "historical_probability_at_entry": "0.60",
        "round_open_price": "50000", "round_close_price": "50100",
        "stage_at_entry": "AFTER_5M",  # ← differs from backtest
        "volatility_bucket_at_entry": "VOL_LOW",
        "distance_bucket_at_entry": "D_0_005pct",
        "current_side_at_entry": "BELOW_OPEN",
        "pattern_at_entry": "weak_bear",  # ← differs from backtest
    }
    backtest = {
        "market_slug": "slug-A",
        "stage": "AFTER_10M",  # ← differs
        "current_side": "BELOW_OPEN",
        "distance_bucket": "D_0_005pct",
        "volatility_bucket": "VOL_LOW",
        "pattern": "strong_bull -> normal_bull",  # ← differs
        "rule_id": "r1",
        "recommended_side": "UP",
        "historical_probability": "0.60",
        "entry_price": "0.55",
        "won": True, "pnl": "0.45",
        "round_open_price": "50000", "round_close_price": "50100",
    }
    diff = compare_pair(live, backtest, entry_tolerance=Decimal("0.01"))
    assert diff["matched"] is True
    # State mismatches
    state_mismatches = [d for d in diff["field_diffs"] if d["category"] == "state"]
    assert len(state_mismatches) >= 2  # stage + pattern
    # No price mismatch
    price_mismatches = [d for d in diff["field_diffs"] if d["category"] == "price"]
    assert len(price_mismatches) == 0


def test_compare_pair_entry_price_within_tolerance():
    """Entry price within 0.01 tolerance should match."""
    live = {"market_slug": "A", "entry_price": "0.55", "selected_side": "UP", "won": 1,
            "realized_pnl_usd": "0.45", "rule_id": "r1", "historical_probability_at_entry": "0.60",
            "round_open_price": "50000", "round_close_price": "50100",
            "stage_at_entry": "AFTER_10M", "volatility_bucket_at_entry": "VOL_LOW",
            "distance_bucket_at_entry": "D_0_005pct", "current_side_at_entry": "BELOW_OPEN",
            "pattern_at_entry": "x -> y"}
    backtest = {"market_slug": "A", "entry_price": "0.555", "stage": "AFTER_10M", "current_side": "BELOW_OPEN",
                "distance_bucket": "D_0_005pct", "volatility_bucket": "VOL_LOW", "pattern": "x -> y",
                "rule_id": "r1", "recommended_side": "UP", "historical_probability": "0.60",
                "won": True, "pnl": "0.45", "round_open_price": "50000", "round_close_price": "50100"}
    diff = compare_pair(live, backtest, entry_tolerance=Decimal("0.01"))
    assert all(d["category"] != "price" for d in diff["field_diffs"])


def test_compare_pair_entry_price_outside_tolerance():
    """Entry price differing by > 0.01 should be flagged as price mismatch."""
    live = {"market_slug": "A", "entry_price": "0.55", "selected_side": "UP", "won": 1,
            "realized_pnl_usd": "0.45", "rule_id": "r1", "historical_probability_at_entry": "0.60",
            "round_open_price": "50000", "round_close_price": "50100",
            "stage_at_entry": "AFTER_10M", "volatility_bucket_at_entry": "VOL_LOW",
            "distance_bucket_at_entry": "D_0_005pct", "current_side_at_entry": "BELOW_OPEN",
            "pattern_at_entry": "x -> y"}
    backtest = {"market_slug": "A", "entry_price": "0.65", "stage": "AFTER_10M", "current_side": "BELOW_OPEN",
                "distance_bucket": "D_0_005pct", "volatility_bucket": "VOL_LOW", "pattern": "x -> y",
                "rule_id": "r1", "recommended_side": "UP", "historical_probability": "0.60",
                "won": True, "pnl": "-0.65", "round_open_price": "50000", "round_close_price": "50100"}
    diff = compare_pair(live, backtest, entry_tolerance=Decimal("0.01"))
    price_mismatches = [d for d in diff["field_diffs"] if d["category"] == "price"]
    assert len(price_mismatches) >= 1


def test_categorize_verdict_state_dominant():
    """If > 50% of pairs have state mismatches, verdict = B."""
    diffs = [{"field_diffs": [{"category": "state"}, {"category": "state"}]}] * 3 + \
            [{"field_diffs": [{"category": "price"}]}] * 1
    result = categorize_verdict(diffs, n_matched=4, n_live_only=0, n_backtest_only=0)
    assert result["verdict"] == "B"


def test_categorize_verdict_insufficient_data():
    """If < 5 matched pairs, verdict = D."""
    diffs = [{"field_diffs": []}] * 3
    result = categorize_verdict(diffs, n_matched=3, n_live_only=0, n_backtest_only=0)
    assert result["verdict"] == "D"


def test_categorize_verdict_filter_dominant():
    """If many live_only trades and few state mismatches, verdict = A."""
    diffs = [{"field_diffs": []}] * 5  # all matched pairs agree
    result = categorize_verdict(diffs, n_matched=5, n_live_only=20, n_backtest_only=0)
    assert result["verdict"] == "A"


def test_categorize_verdict_settlement_dominant():
    """If matched pairs have won/pnl mismatches, verdict = C."""
    diffs = [{"field_diffs": [{"category": "settlement"}, {"category": "settlement"}]}] * 4 + \
            [{"field_diffs": []}] * 1
    result = categorize_verdict(diffs, n_matched=5, n_live_only=0, n_backtest_only=0)
    assert result["verdict"] == "C"
```

- [ ] **Step 2: Run tests to verify they fail (compare_pair and categorize_verdict don't exist yet)**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_reconcile.py -v -k "compare_pair or categorize_verdict"`

Expected: ImportError or AttributeError.

- [ ] **Step 3: Add `compare_pair` and `categorize_verdict` to `reconcile_live_vs_backtest.py`**

Append to `scripts/reconcile_live_vs_backtest.py` (before the `main()` block):

```python
# === Field comparison ===

_STATE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("stage_at_entry", "stage", "state"),
    ("pattern_at_entry", "pattern", "state"),
    ("volatility_bucket_at_entry", "volatility_bucket", "state"),
    ("distance_bucket_at_entry", "distance_bucket", "state"),
    ("current_side_at_entry", "current_side", "state"),
)

_PRICE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("entry_price", "entry_price", "price"),
    ("historical_probability_at_entry", "historical_probability", "price"),
)

_SETTLEMENT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("won", "won", "settlement"),
    ("realized_pnl_usd", "pnl", "settlement"),
    ("round_close_price", "round_close_price", "settlement"),
)


def compare_pair(
    live: dict[str, Any],
    backtest: dict[str, Any],
    *,
    entry_tolerance: Decimal,
) -> dict[str, Any]:
    """Compare a matched (live, backtest) pair, return a diff summary.

    Returns: {"matched": bool, "field_diffs": [{"field", "category", "live", "backtest"}], ...}
    """
    diffs: list[dict[str, Any]] = []

    # State fields: exact string match
    for live_key, back_key, category in _STATE_FIELDS:
        l_val = str(live.get(live_key) or "")
        b_val = str(backtest.get(back_key) or "")
        if l_val != b_val:
            diffs.append({"field": live_key, "category": category, "live": l_val, "backtest": b_val})

    # Price fields: tolerance check
    for live_key, back_key, category in _PRICE_FIELDS:
        try:
            l_dec = Decimal(str(live.get(live_key) or "0"))
            b_dec = Decimal(str(backtest.get(back_key) or "0"))
            if abs(l_dec - b_dec) > entry_tolerance:
                diffs.append({"field": live_key, "category": category,
                              "live": str(l_dec), "backtest": str(b_dec)})
        except Exception:
            diffs.append({"field": live_key, "category": category,
                          "live": str(live.get(live_key)), "backtest": str(backtest.get(back_key))})

    # Settlement fields: tolerance on pnl/close, exact on won
    for live_key, back_key, category in _SETTLEMENT_FIELDS:
        l_val = live.get(live_key)
        b_val = backtest.get(back_key)
        if live_key == "won":
            l_bool = bool(l_val)
            b_bool = bool(b_val)
            if l_bool != b_bool:
                diffs.append({"field": live_key, "category": category,
                              "live": str(l_bool), "backtest": str(b_bool)})
        else:
            try:
                l_dec = Decimal(str(l_val or "0"))
                b_dec = Decimal(str(b_val or "0"))
                if abs(l_dec - b_dec) > entry_tolerance:
                    diffs.append({"field": live_key, "category": category,
                                  "live": str(l_dec), "backtest": str(b_dec)})
            except Exception:
                diffs.append({"field": live_key, "category": category,
                              "live": str(l_val), "backtest": str(b_val)})

    return {"matched": True, "field_diffs": diffs}


def categorize_verdict(
    pair_diffs: list[dict[str, Any]],
    *,
    n_matched: int,
    n_live_only: int,
    n_backtest_only: int,
) -> dict[str, Any]:
    """Categorize the reconciliation result into A/B/C/D."""
    counters: Counter = Counter()
    for d in pair_diffs:
        for diff in d.get("field_diffs", []):
            counters[diff["category"]] += 1

    state_count = counters.get("state", 0)
    price_count = counters.get("price", 0)
    settlement_count = counters.get("settlement", 0)
    total_mismatches = state_count + price_count + settlement_count

    if n_matched < 5:
        verdict = "D"
        recommendation = (
            f"Only {n_matched} matched pairs (need >= 5). Likely insufficient data. "
            f"Either wait for more live settlements or proceed with caution; "
            f"all observed live trades: {n_live_only}, all observed backtest trades: {n_backtest_only}."
        )
    elif n_live_only > 5 and total_mismatches < n_matched:
        verdict = "A"
        recommendation = (
            f"Live has {n_live_only} trades not present in backtest, but matched pairs agree. "
            f"Likely the live bot has additional filters (spread, liquidity) that the backtest ignores. "
            f"Recommended next step: add a spread model to the backtest and rerun."
        )
    elif n_backtest_only > 5 and total_mismatches < n_matched:
        verdict = "A"
        recommendation = (
            f"Backtest has {n_backtest_only} trades not present in live. "
            f"Likely the backtest is too permissive (it allows trades that live filters out). "
            f"Recommended next step: review live filters and reflect them in the backtest."
        )
    elif state_count > total_mismatches * 0.5 and n_matched >= 5:
        verdict = "B"
        recommendation = (
            f"State fields (stage, pattern, vol_bucket, dist_bucket, current_side) "
            f"differ in {state_count} of {total_mismatches} mismatches across {n_matched} pairs. "
            f"Likely the state-construction mismatches identified in `backtest-reference-compare.md` are material. "
            f"Recommended next step: fix `round_state.py` (volatility source, distance bucket, AT_OPEN) and rerun."
        )
    elif settlement_count > total_mismatches * 0.5 and n_matched >= 5:
        verdict = "C"
        recommendation = (
            f"Settlement fields (won, pnl) differ in {settlement_count} of {total_mismatches} mismatches. "
            f"Likely settlement timing or tie handling diverges. "
            f"Recommended next step: compare `settlement.py` against the live settlement path."
        )
    else:
        verdict = "A"
        recommendation = (
            f"Diff is spread across categories (state={state_count}, price={price_count}, "
            f"settlement={settlement_count}). Multiple factors at play. "
            f"Recommended next step: deep dive into the top discrepancies."
        )

    return {
        "verdict": verdict,
        "n_matched": n_matched,
        "n_live_only": n_live_only,
        "n_backtest_only": n_backtest_only,
        "mismatch_counts": {
            "state": state_count, "price": price_count, "settlement": settlement_count,
            "total": total_mismatches,
        },
        "recommendation": recommendation,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_reconcile.py -v`

Expected: 10 passed (3 from Task 4 + 7 new).

- [ ] **Step 5: Commit**

```bash
git add scripts/reconcile_live_vs_backtest.py tests/walk_forward/test_reconcile.py
git commit -m "feat(reconcile): field comparison + verdict categorization (A/B/C/D)"
```

---

## Task 6: `reconcile_live_vs_backtest.py` — CSV writers + main CLI

**Files:**
- Modify: `scripts/reconcile_live_vs_backtest.py` (replace `main`, add writers)
- Test: append to `tests/walk_forward/test_reconcile.py`

- [ ] **Step 1: Write failing test for CSV outputs and main flow**

Append to `tests/walk_forward/test_reconcile.py`:

```python
def test_write_outputs_creates_three_csvs_and_summary(tmp_path: Path):
    from scripts.reconcile_live_vs_backtest import write_outputs
    matched = [(
        {"market_slug": "A", "entry_price": "0.55", "stage_at_entry": "AFTER_10M",
         "pattern_at_entry": "x", "volatility_bucket_at_entry": "VOL_LOW",
         "distance_bucket_at_entry": "D_0_005pct", "current_side_at_entry": "BELOW_OPEN",
         "won": 1, "realized_pnl_usd": "0.45", "round_close_price": "50100",
         "rule_id": "r1", "historical_probability_at_entry": "0.60"},
        {"market_slug": "A", "entry_price": "0.55", "stage": "AFTER_10M",
         "pattern": "x", "volatility_bucket": "VOL_LOW",
         "distance_bucket": "D_0_005pct", "current_side": "BELOW_OPEN",
         "won": True, "pnl": "0.45", "round_close_price": "50100",
         "rule_id": "r1", "historical_probability": "0.60"},
    )]
    pair_diffs = [{"matched": True, "field_diffs": []}]
    live_only = []
    backtest_only = [{"market_slug": "B", "entry_price": "0.60"}]
    summary = {
        "verdict": "A",
        "n_matched": 1, "n_live_only": 0, "n_backtest_only": 1,
        "mismatch_counts": {"state": 0, "price": 0, "settlement": 0, "total": 0},
        "recommendation": "Live has more trades than backtest. Add spread model.",
    }
    write_outputs(out_dir=tmp_path, matched=matched, live_only=live_only,
                  backtest_only=backtest_only, pair_diffs=pair_diffs, summary=summary,
                  entry_tolerance=Decimal("0.01"))
    assert (tmp_path / "matched_pairs.csv").exists()
    assert (tmp_path / "live_only_trades.csv").exists()
    assert (tmp_path / "backtest_only_trades.csv").exists()
    assert (tmp_path / "reconciliation_summary.json").exists()
    loaded = json.loads((tmp_path / "reconciliation_summary.json").read_text())
    assert loaded["verdict"] == "A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_reconcile.py -v -k write_outputs`

Expected: ImportError on `write_outputs`.

- [ ] **Step 3: Add `write_outputs` and replace `main()` with full pipeline**

In `scripts/reconcile_live_vs_backtest.py`, add `write_outputs` and replace `main()`:

```python
def write_outputs(
    *,
    out_dir: Path,
    matched: list[tuple[dict, dict]],
    live_only: list[dict],
    backtest_only: list[dict],
    pair_diffs: list[dict[str, Any]],
    summary: dict[str, Any],
    entry_tolerance: Decimal,
) -> None:
    """Write matched_pairs.csv, live_only_trades.csv, backtest_only_trades.csv, reconciliation_summary.json."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # matched_pairs.csv: one row per pair, with live_* and backtest_* columns
    with (out_dir / "matched_pairs.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "market_slug",
            "live_entry_price", "backtest_entry_price", "entry_price_diff",
            "live_stage", "backtest_stage", "stage_match",
            "live_pattern", "backtest_pattern", "pattern_match",
            "live_vol_bucket", "backtest_vol_bucket", "vol_match",
            "live_dist_bucket", "backtest_dist_bucket", "dist_match",
            "live_current_side", "backtest_current_side", "side_match",
            "live_won", "backtest_won", "won_match",
            "live_pnl", "backtest_pnl", "pnl_diff",
            "live_round_close", "backtest_round_close", "round_close_diff",
            "live_rule_id", "backtest_rule_id", "rule_id_match",
            "n_field_diffs",
        ])
        for (live, back), diff in zip(matched, pair_diffs):
            def get(d, k, default=""):
                v = d.get(k)
                return str(v) if v is not None else default

            def is_match(l, b, tol=entry_tolerance):
                try:
                    return "match" if abs(Decimal(str(l)) - Decimal(str(b))) <= tol else "DIFF"
                except Exception:
                    return "DIFF" if str(l) != str(b) else "match"

            writer.writerow([
                get(live, "market_slug"),
                get(live, "entry_price"), get(back, "entry_price"),
                is_match(live.get("entry_price"), back.get("entry_price")),
                get(live, "stage_at_entry"), get(back, "stage"),
                "match" if get(live, "stage_at_entry") == get(back, "stage") else "DIFF",
                get(live, "pattern_at_entry"), get(back, "pattern"),
                "match" if get(live, "pattern_at_entry") == get(back, "pattern") else "DIFF",
                get(live, "volatility_bucket_at_entry"), get(back, "volatility_bucket"),
                "match" if get(live, "volatility_bucket_at_entry") == get(back, "volatility_bucket") else "DIFF",
                get(live, "distance_bucket_at_entry"), get(back, "distance_bucket"),
                "match" if get(live, "distance_bucket_at_entry") == get(back, "distance_bucket") else "DIFF",
                get(live, "current_side_at_entry"), get(back, "current_side"),
                "match" if get(live, "current_side_at_entry") == get(back, "current_side") else "DIFF",
                get(live, "won"), get(back, "won"),
                "match" if str(bool(live.get("won"))) == str(bool(back.get("won"))) else "DIFF",
                get(live, "realized_pnl_usd"), get(back, "pnl"),
                is_match(live.get("realized_pnl_usd"), back.get("pnl")),
                get(live, "round_close_price"), get(back, "round_close_price"),
                is_match(live.get("round_close_price"), back.get("round_close_price")),
                get(live, "rule_id"), get(back, "rule_id"),
                "match" if get(live, "rule_id") == get(back, "rule_id") else "DIFF",
                str(len(diff.get("field_diffs", []))),
            ])

    # live_only_trades.csv
    if live_only:
        with (out_dir / "live_only_trades.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(live_only[0].keys()))
            writer.writeheader()
            writer.writerows(live_only)
    else:
        (out_dir / "live_only_trades.csv").write_text("")

    # backtest_only_trades.csv
    if backtest_only:
        with (out_dir / "backtest_only_trades.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(backtest_only[0].keys()))
            writer.writeheader()
            writer.writerows(backtest_only)
    else:
        (out_dir / "backtest_only_trades.csv").write_text("")

    # reconciliation_summary.json
    (out_dir / "reconciliation_summary.json").write_text(json.dumps(summary, indent=2))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live-db", required=True)
    p.add_argument("--backtest-trades", required=True)
    p.add_argument("--out-dir", default="results/recon/")
    p.add_argument("--period-start", default="2026-06-06T11:51:00+00:00",
                   help="ISO datetime; only load live settlements after this")
    p.add_argument("--entry-tolerance", type=Decimal, default=Decimal("0.01"))
    args = p.parse_args()

    period_start = datetime.fromisoformat(args.period_start)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading live settlements from {args.live_db} (period >= {period_start})...", file=sys.stderr)
    live = load_live_settlements(Path(args.live_db), period_start=period_start)
    print(f"  loaded {len(live)} live settlements", file=sys.stderr)
    print(f"Loading backtest trades from {args.backtest_trades}...", file=sys.stderr)
    backtest = load_backtest_trades(Path(args.backtest_trades))
    print(f"  loaded {len(backtest)} backtest trades", file=sys.stderr)

    matched, live_only, backtest_only = match_by_slug(live, backtest)
    print(f"Matched: {len(matched)}, live_only: {len(live_only)}, backtest_only: {len(backtest_only)}", file=sys.stderr)

    pair_diffs = [compare_pair(l, b, entry_tolerance=args.entry_tolerance) for l, b in matched]
    summary = categorize_verdict(
        pair_diffs,
        n_matched=len(matched),
        n_live_only=len(live_only),
        n_backtest_only=len(backtest_only),
    )
    print(f"Verdict: {summary['verdict']} — {summary['recommendation']}", file=sys.stderr)

    write_outputs(
        out_dir=out_dir, matched=matched, live_only=live_only, backtest_only=backtest_only,
        pair_diffs=pair_diffs, summary=summary, entry_tolerance=args.entry_tolerance,
    )
    print(f"OK: outputs written to {out_dir}", file=sys.stderr)
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_reconcile.py -v`

Expected: 11 passed (10 from Task 5 + 1 new).

- [ ] **Step 5: Commit**

```bash
git add scripts/reconcile_live_vs_backtest.py tests/walk_forward/test_reconcile.py
git commit -m "feat(reconcile): CSV writers + main CLI"
```

---

## Task 7: `scripts/reconciliation_report.py`

**Files:**
- Create: `scripts/reconciliation_report.py`
- Test: `tests/walk_forward/test_reconciliation_report.py`

- [ ] **Step 1: Write failing test for report rendering**

Create `tests/walk_forward/test_reconciliation_report.py`:

```python
"""Tests for reconciliation_report: required sections, summary table, verdict."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.reconciliation_report import (
    render_report,
    REQUIRED_SECTIONS,
    summary_table_markdown,
)


@pytest.fixture
def sample_summary() -> dict:
    return {
        "verdict": "B",
        "n_matched": 7,
        "n_live_only": 0,
        "n_backtest_only": 12,
        "mismatch_counts": {"state": 5, "price": 1, "settlement": 0, "total": 6},
        "recommendation": "State fields differ. Fix round_state.py.",
    }


def test_render_report_contains_required_sections(sample_summary, tmp_path):
    out = tmp_path / "report.md"
    render_report(summary=sample_summary, matched_pairs=[], live_only=[],
                  backtest_only=[], out_path=out)
    text = out.read_text()
    for section in REQUIRED_SECTIONS:
        assert section in text, f"missing section: {section}"


def test_render_report_includes_verdict(sample_summary, tmp_path):
    out = tmp_path / "report.md"
    render_report(summary=sample_summary, matched_pairs=[], live_only=[],
                  backtest_only=[], out_path=out)
    text = out.read_text()
    assert "Verdict: B" in text or "**B**" in text
    assert "Fix round_state.py" in text


def test_summary_table_markdown(sample_summary):
    md = summary_table_markdown(sample_summary)
    assert "Matched pairs" in md
    assert "Live-only" in md
    assert "Backtest-only" in md
    assert "Verdict" in md
    assert "7" in md  # n_matched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_reconciliation_report.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement `scripts/reconciliation_report.py`**

Create `scripts/reconciliation_report.py`:

```python
"""Render reconciliation results to a markdown report.

Usage:
  python scripts/reconciliation_report.py \\
    --in-dir results/recon/ \\
    --out docs/analysis/2026-06-15-reconciliation.md
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_SECTIONS: tuple[str, ...] = (
    "# Reconciliation Report",
    "## TL;DR",
    "## Setup",
    "## Matched pairs analysis",
    "## Unmatched analysis",
    "## Verdict & recommendation",
    "## Appendix",
)


_VERDICT_LABELS: dict[str, str] = {
    "A": "Live-vs-backtest filter gap (spread, liquidity, or backtest too permissive)",
    "B": "State-construction mismatch (volatility, distance bucket, AT_OPEN, pattern)",
    "C": "Settlement timing or tie handling divergence",
    "D": "Insufficient data (< 5 matched pairs)",
}


def summary_table_markdown(summary: dict[str, Any]) -> str:
    return (
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| Verdict | **{summary['verdict']}** |\n"
        f"| Matched pairs | {summary['n_matched']} |\n"
        f"| Live-only | {summary['n_live_only']} |\n"
        f"| Backtest-only | {summary['n_backtest_only']} |\n"
        f"| State mismatches | {summary['mismatch_counts']['state']} |\n"
        f"| Price mismatches | {summary['mismatch_counts']['price']} |\n"
        f"| Settlement mismatches | {summary['mismatch_counts']['settlement']} |\n"
        f"| Total mismatches | {summary['mismatch_counts']['total']} |\n"
    )


def render_report(
    *,
    summary: dict[str, Any],
    matched_pairs: list[dict[str, Any]],
    live_only: list[dict[str, Any]],
    backtest_only: list[dict[str, Any]],
    out_path: Path,
) -> None:
    verdict_label = _VERDICT_LABELS.get(summary["verdict"], "Unknown")

    # 1. TL;DR
    tl_dr = (
        f"**Verdict: {summary['verdict']} — {verdict_label}.**\n\n"
        f"{summary['recommendation']}"
    )

    # 2. Setup
    setup = (
        f"- **Live DB**: snapshot from server at reconciliation time\n"
        f"- **Backtest**: same live period (2026-06-06 → 2026-06-15), replayed through "
        f"`walk_forward_backtest.py` with live rules from `config/btc_updown_state_rules_15m.json`\n"
        f"- **Match key**: `market_slug`\n"
        f"- **Field comparison**: state (5 fields), price (2 fields), settlement (3 fields)"
    )

    # 3. Matched pairs analysis
    matched_table = (
        f"See `results/recon/matched_pairs.csv` for the full side-by-side comparison.\n\n"
        + summary_table_markdown(summary)
    )

    # 4. Unmatched analysis
    unmatched_md = (
        f"- **Live-only trades** (no backtest match): {summary['n_live_only']}. "
        f"See `results/recon/live_only_trades.csv`.\n"
        f"- **Backtest-only trades** (no live match): {summary['n_backtest_only']}. "
        f"See `results/recon/backtest_only_trades.csv`."
    )
    if summary["n_live_only"] > 0 and summary["n_live_only"] <= 20:
        unmatched_md += "\n\n**Live-only trades (full list):**\n\n"
        unmatched_md += "| market_slug | resolved_at_utc | won | pnl | entry_price | rule_id |\n"
        unmatched_md += "|---|---|---|---|---|---|\n"
        for t in live_only[:20]:
            unresolved = t.get("resolved_at_utc", "")
            ts = unresolved[:19] if unresolved else ""
            unmatched_md += (
                f"| {t.get('market_slug', '?')} | {ts} | {t.get('won', '?')} | "
                f"{t.get('realized_pnl_usd', '?')} | {t.get('entry_price', '?')} | "
                f"{t.get('rule_id', '?')} |\n"
            )
    if summary["n_backtest_only"] > 0 and summary["n_backtest_only"] <= 20:
        unmatched_md += "\n\n**Backtest-only trades (first 20):**\n\n"
        unmatched_md += "| market_slug | won | pnl | entry_price | stage |\n"
        unmatched_md += "|---|---|---|---|---|\n"
        for t in backtest_only[:20]:
            unmatched_md += (
                f"| {t.get('market_slug', '?')} | {t.get('won', '?')} | "
                f"{t.get('pnl', '?')} | {t.get('entry_price', '?')} | "
                f"{t.get('stage', '?')} |\n"
            )

    # 5. Verdict & recommendation
    verdict_md = (
        f"**{summary['verdict']} — {verdict_label}**\n\n"
        f"{summary['recommendation']}"
    )

    # 6. Appendix
    appendix = (
        "**Methodology**:\n"
        "- Live DB snapshot: scp from server, query settlements + paper_positions tables.\n"
        "- Backtest: `walk_forward_backtest.py` re-run on the live period with explicit "
        "`--test-start` / `--test-end` flags (added in this iteration).\n"
        "- Match by `market_slug`. 1:1 match expected (MAX_OPEN_POSITIONS=1 invariant).\n"
        "- Field comparison with entry tolerance = 0.01. State fields use exact string match.\n"
        "- Verdict logic in `categorize_verdict()`: A (filter) / B (state) / C (settlement) / D (insufficient data).\n\n"
        "**Limitations**:\n"
        "- Small live sample (only 7-9 days, 7+ settlements). Statistical confidence is low.\n"
        "- The match depends on `market_slug` being identical between live and backtest. "
        "If live slugs differ from backtest slugs (e.g., different slug generation logic), "
        "matches would be missed.\n"
        "- `backtest-only` trades are computed from a backtest that has no concept of "
        "`MAX_OPEN_POSITIONS` over time (it processes one round at a time), so a backtest trade "
        "may have a live counterpart that was rejected by the risk manager on a different "
        "round that day."
    )

    report = f"""# Reconciliation Report

**Generated**: 2026-06-15

## TL;DR

{tl_dr}

## Setup

{setup}

## Matched pairs analysis

{matched_table}

## Unmatched analysis

{unmatched_md}

## Verdict & recommendation

{verdict_md}

## Appendix

{appendix}
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in-dir", default="results/recon/")
    p.add_argument("--out", default="docs/analysis/2026-06-15-reconciliation.md")
    args = p.parse_args()

    in_dir = Path(args.in_dir)
    summary_path = in_dir / "reconciliation_summary.json"
    if not summary_path.exists():
        print(f"missing {summary_path}", file=sys.stderr)
        return 1
    summary = json.loads(summary_path.read_text())

    matched = []
    matched_path = in_dir / "matched_pairs.csv"
    if matched_path.exists() and matched_path.stat().st_size > 0:
        with matched_path.open() as f:
            matched = list(csv.DictReader(f))

    live_only = []
    live_only_path = in_dir / "live_only_trades.csv"
    if live_only_path.exists() and live_only_path.stat().st_size > 0:
        with live_only_path.open() as f:
            live_only = list(csv.DictReader(f))

    backtest_only = []
    backtest_only_path = in_dir / "backtest_only_trades.csv"
    if backtest_only_path.exists() and backtest_only_path.stat().st_size > 0:
        with backtest_only_path.open() as f:
            backtest_only = list(csv.DictReader(f))

    render_report(
        summary=summary, matched_pairs=matched, live_only=live_only,
        backtest_only=backtest_only, out_path=Path(args.out),
    )
    print(f"OK: report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_reconciliation_report.py -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/reconciliation_report.py tests/walk_forward/test_reconciliation_report.py
git commit -m "feat(reconcile): markdown report renderer"
```

---

## Task 8: Run reconciliation + generate report (operational)

**Files:** none new (uses existing scripts)

- [ ] **Step 1: Run reconciliation end-to-end**

Run:
```bash
cd .worktrees/walk-forward-analysis
PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python scripts/reconcile_live_vs_backtest.py \
  --live-db data/live_paper.sqlite \
  --backtest-trades results/recon/wf_fold_0_trades.csv \
  --out-dir results/recon \
  --period-start 2026-06-06T11:51:00+00:00
```

Expected: prints counts, verdict, and "OK: outputs written to results/recon".

- [ ] **Step 2: Inspect the summary**

Run:
```bash
cd .worktrees/walk-forward-analysis
cat results/recon/reconciliation_summary.json | head -30
echo "---"
head -3 results/recon/matched_pairs.csv
echo "---"
wc -l results/recon/*.csv
```

Expected: verdict ∈ {A, B, C, D}; matched_pairs.csv has at least header; CSVs exist with row counts > 0 for matched.

- [ ] **Step 3: Render the report**

Run:
```bash
cd .worktrees/walk-forward-analysis
mkdir -p docs/analysis
PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python scripts/reconciliation_report.py \
  --in-dir results/recon \
  --out docs/analysis/2026-06-15-reconciliation.md
```

Expected: prints `OK: report written to docs/analysis/2026-06-15-reconciliation.md`.

- [ ] **Step 4: Verify report content**

Run:
```bash
cd .worktrees/walk-forward-analysis
wc -l docs/analysis/2026-06-15-reconciliation.md
grep -c "^## " docs/analysis/2026-06-15-reconciliation.md
head -20 docs/analysis/2026-06-15-reconciliation.md
```

Expected: report exists, ≥ 7 sections, has TL;DR with verdict at top.

- [ ] **Step 5: Run full test suite one more time**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/`

Expected: 42 passed (was 28; +1 explicit_window, +11 reconcile, +3 reconciliation_report).

- [ ] **Step 6: Commit report and update memory**

```bash
cd .worktrees/walk-forward-analysis
git add docs/analysis/2026-06-15-reconciliation.md
git -c user.email=agent@local -c user.name=agent commit -m "docs(analysis): reconciliation report with verdict (A/B/C/D)"
git log --oneline -10
```

Expected: new commit on top of the spec commit. The verdict in the commit message should reflect the actual finding (look at `reconciliation_summary.json` before committing).

- [ ] **Step 7: Hand off to user**

Print a summary message in chat:
```
Reconciliation complete.

📄 Report: docs/analysis/2026-06-15-reconciliation.md
🔢 Verdict: <A/B/C/D> — <label>
📊 CSVs: results/recon/matched_pairs.csv, live_only_trades.csv, backtest_only_trades.csv
🔢 Tests: 42 passed
🔢 Commits: feature/walk-forward-analysis branch

Next: <based on verdict, e.g. "fix round_state.py volatility source" or "add spread model to backtest">
```

---

## Self-Review

**Spec coverage check:**
- Spec Goal 1 (coverage: matched/live-only/backtest-only counts) — Task 8 Step 1 + Step 2 ✓
- Spec Goal 2 (state match for paired trades) — Task 5 `compare_pair` ✓
- Spec Goal 3 (entry match) — Task 5 `compare_pair` with tolerance ✓
- Spec Goal 4 (settlement match) — Task 5 `compare_pair` settlement fields ✓
- Spec Goal 5 (verdict A/B/C/D) — Task 5 `categorize_verdict` + Task 6 `write_outputs` ✓
- Spec Component 1 (scp live DB) — Task 1 ✓
- Spec Component 2 (--test-start/--test-end flags) — Task 2 ✓
- Spec Component 3 (reconcile_live_vs_backtest.py) — Tasks 4-6 ✓
- Spec Component 4 (reconciliation_report.py) — Task 7 ✓
- Spec Acceptance 1 (live DB snapshot with sha256) — Task 1 Steps 1-2 ✓
- Spec Acceptance 2 (backtest on live period) — Task 3 ✓
- Spec Acceptance 3 (tests pass, ≥ 5 tests, verdict logic covered) — Tasks 4-7 (≥ 11 tests) ✓
- Spec Acceptance 4 (CSV outputs with documented columns) — Task 6 ✓
- Spec Acceptance 5 (verdict + recommendation in summary) — Task 5 ✓
- Spec Acceptance 6 (report with all 7 sections) — Task 7 + Task 8 ✓
- Spec Acceptance 7 (git commit) — Task 8 Step 6 ✓

**Placeholder scan:** No "TBD", "TODO", or vague placeholders in any step. All code blocks are complete.

**Type consistency:**
- `compare_pair(live, backtest, *, entry_tolerance=Decimal)` — matches all 7 test calls and the call in `main()`.
- `categorize_verdict(pair_diffs, *, n_matched, n_live_only, n_backtest_only)` — matches all 4 test calls and the call in `main()`.
- `write_outputs(out_dir, matched, live_only, backtest_only, pair_diffs, summary, entry_tolerance)` — matches the test call and the call in `main()`.
- `render_report(summary, matched_pairs, live_only, backtest_only, out_path)` — matches the test call and the call in `main()`.
- `REQUIRED_LIVE_SETTLEMENT_COLS` and `REQUIRED_BACKTEST_TRADE_COLS` are defined in Task 4 and used in Task 4 (loaders + tests). They are not used by `compare_pair` (which uses specific field names from `_STATE_FIELDS`, `_PRICE_FIELDS`, `_SETTLEMENT_FIELDS`). This is fine — they document the expected input schema.

**No issues found — plan is ready to execute.**
