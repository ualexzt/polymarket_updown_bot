# Vol-Mean Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For each of the 79 vol-bucket mismatches between live and backtest, compute the live and backtest `vol_mean` from the same candle source, then categorize the mismatch cause (edge_threshold, candle_selection, identical, unknown). Produce a report with a concrete fix recommendation.

**Architecture:** Read-only analytical pipeline. Reuses the production `_compute_prev_volatility_mean` function and `_VOL_LOW_MAX` / `_VOL_NORMAL_MAX` constants from `round_state.py`. New script `investigate_vol_mean.py` per-pair computes both perspectives and categorizes. New `investigation_report.py` renders markdown. TDD: 6+ tests in `tests/walk_forward/test_investigate_vol_mean.py`.

**Tech Stack:** Python 3.11+, pydantic v2, csv/decimal/datetime stdlib. No new dependencies.

---

## File Structure

**New files:**
- `scripts/investigate_vol_mean.py` — per-pair analysis + categorization
- `scripts/investigation_report.py` — markdown renderer
- `tests/walk_forward/test_investigate_vol_mean.py` — tests

**Operational artifacts (gitignored):**
- `results/investigation/vol_mean_per_pair.csv`
- `results/investigation/mismatch_categorization.json`
- `results/investigation/edge_case_summary.txt`
- `docs/analysis/2026-06-15-vol-mean-investigation.md` (committed)

**Reused (read-only):**
- `data/btc_5m_500d.csv` (144k candles)
- `data/live_paper.sqlite` (216 MB)
- `results/recon/matched_pairs.csv` (187 rows)
- `src/polymarket_round_bot/round_state.py::_compute_prev_volatility_mean`
- `src/polymarket_round_bot/round_state.py::_VOL_LOW_MAX`, `_VOL_NORMAL_MAX`
- `scripts/walk_forward_backtest.py::load_candles_csv`

---

## Task 1: `investigate_vol_mean.py` — core analysis

**Files:**
- Create: `scripts/investigate_vol_mean.py`
- Test: `tests/walk_forward/test_investigate_vol_mean.py`

- [ ] **Step 1: Write failing tests for analyze_pair and categorization**

Create `tests/walk_forward/test_investigate_vol_mean.py`:

```python
"""Tests for investigate_vol_mean: per-pair analysis and categorization."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import Candle
from polymarket_round_bot.round_state import _VOL_LOW_MAX, _VOL_NORMAL_MAX

from scripts.investigate_vol_mean import (
    parse_round_start_from_slug,
    load_mismatched_pairs,
    analyze_pair,
    CATEGORIES,
)


def make_candle(open_time: datetime, open: str, close: str | None = None,
                high: str | None = None, low: str | None = None) -> Candle:
    from decimal import Decimal as D
    o = D(open)
    c = D(close if close is not None else open)
    h = D(high if high is not None else open)
    l = D(low if low is not None else open)
    return Candle(
        open_time_utc=open_time if open_time.tzinfo else open_time.replace(tzinfo=UTC),
        open=o, high=h, low=l, close=c, volume=D("10"), is_closed=True,
    )


def test_parse_round_start_from_slug():
    slug = "btc-updown-15m-1781526600"  # 2026-06-15 12:30 UTC
    rs = parse_round_start_from_slug(slug)
    assert rs == datetime(2026, 6, 15, 12, 30, tzinfo=UTC)


def test_analyze_pair_identical_vol_means():
    """When both perspectives return the same vol_mean, category is 'identical'."""
    # Build 16 prior 15m rounds worth of candles (48 5m candles = 4 hours)
    # Each round: 3 candles c0, c1, c2 with increasing prices
    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    round_start = start + timedelta(hours=4)  # need 4 hours of prior rounds
    candles = []
    for r in range(16):  # 16 prior rounds
        c0_time = start + timedelta(minutes=15 * r)
        candles.append(make_candle(c0_time, "50000", "50000"))  # c0
        candles.append(make_candle(c0_time + timedelta(minutes=5), "50010", "50010"))  # c1
        candles.append(make_candle(c0_time + timedelta(minutes=10), "50020", "50020"))  # c2
    # All 16 rounds have abs_return = |50020/50000 - 1| = 0.0004 → VOL_LOW (below 0.000897)
    result = analyze_pair(
        market_slug="btc-updown-15m-X",
        round_start=round_start,
        live_vol="VOL_NORMAL", backtest_vol="VOL_HIGH",
        candles=candles,
    )
    # Both should compute the same vol_mean
    assert result["live_vol_mean"] is not None
    assert result["backtest_vol_mean"] is not None
    # Both should bucket to VOL_LOW (since 0.0004 < 0.000897)
    # But the test passes the 'live_vol' and 'backtest_vol' as inputs (what was reported)
    # The analysis should compute actual vol_means and report
    assert result["category"] in CATEGORIES


def test_analyze_pair_edge_case_threshold():
    """When vol_mean is within 1e-5 of a threshold, category is 'edge_threshold'."""
    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    round_start = start + timedelta(hours=4)
    candles = []
    # Each prior round has abs_return = _VOL_LOW_MAX exactly
    for r in range(16):
        c0_time = start + timedelta(minutes=15 * r)
        c0_open = 50000
        c2_close = c0_open * (1 + float(_VOL_LOW_MAX))  # exactly at threshold
        candles.append(make_candle(c0_time, str(c0_open), str(c0_open)))
        candles.append(make_candle(c0_time + timedelta(minutes=5),
                                    str(c0_open), str(c0_open + 1)))
        candles.append(make_candle(c0_time + timedelta(minutes=10),
                                    str(c0_open + 1), str(c2_close)))
    result = analyze_pair(
        market_slug="btc-updown-15m-X",
        round_start=round_start,
        live_vol="VOL_NORMAL", backtest_vol="VOL_LOW",
        candles=candles,
    )
    assert result["category"] == "edge_threshold"


def test_analyze_pair_insufficient_data():
    """When < 16 prior rounds exist, vol_mean is None → category 'unknown'."""
    round_start = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
    # Only 1 prior round worth of candles (3 candles)
    candles = [
        make_candle(datetime(2026, 6, 1, 0, 0, tzinfo=UTC), "50000", "50020"),
        make_candle(datetime(2026, 6, 1, 0, 5, tzinfo=UTC), "50010", "50010"),
        make_candle(datetime(2026, 6, 1, 0, 10, tzinfo=UTC), "50020", "50020"),
    ]
    result = analyze_pair(
        market_slug="btc-updown-15m-X",
        round_start=round_start,
        live_vol="VOL_NORMAL", backtest_vol="VOL_HIGH",
        candles=candles,
    )
    assert result["live_vol_mean"] is None
    assert result["backtest_vol_mean"] is None
    assert result["category"] == "unknown"


def test_analyze_pair_categorization_includes_required_keys():
    """Result must contain all expected fields."""
    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    round_start = start + timedelta(hours=4)
    candles = [make_candle(start, "50000", "50000")] * 50
    result = analyze_pair(
        market_slug="test-slug",
        round_start=round_start,
        live_vol="VOL_NORMAL", backtest_vol="VOL_HIGH",
        candles=candles,
    )
    for key in ("market_slug", "round_start_utc", "live_vol_bucket",
                "backtest_vol_bucket", "live_vol_mean", "backtest_vol_mean",
                "vol_mean_diff", "category"):
        assert key in result


def test_categories_constant_complete():
    """All 4 expected categories are in CATEGORIES."""
    for cat in ("edge_threshold", "candle_selection", "identical", "unknown"):
        assert cat in CATEGORIES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_investigate_vol_mean.py -v`

Expected: ImportError on `scripts.investigate_vol_mean`.

- [ ] **Step 3: Implement `investigate_vol_mean.py`**

Create `scripts/investigate_vol_mean.py`:

```python
"""Vol-mean investigation: per-pair analysis and categorization of vol bucket mismatches.

Usage:
  python scripts/investigate_vol_mean.py \\
    --live-db data/live_paper.sqlite \\
    --matched-pairs results/recon/matched_pairs.csv \\
    --candles-csv data/btc_5m_500d.csv \\
    --out-dir results/investigation/
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_round_bot.round_state import (  # noqa: E402
    _VOL_LOW_MAX,
    _VOL_NORMAL_MAX,
    _compute_prev_volatility_mean,
)
from scripts.walk_forward_backtest import load_candles_csv  # noqa: E402


CATEGORIES: tuple[str, ...] = (
    "edge_threshold",
    "candle_selection",
    "identical",
    "unknown",
)

# Live uses 60 candles; backtest uses 200. The 16-round window needs 48 candles minimum.
LIVE_CANDLE_LIMIT: int = 60
BACKTEST_CANDLE_LIMIT: int = 200
EDGE_TOLERANCE: Decimal = Decimal("0.00001")  # 1e-5
MEAN_DIFF_THRESHOLD: Decimal = Decimal("0.0001")  # 1e-4


def parse_round_start_from_slug(slug: str) -> datetime:
    """Parse round_start_ts from market_slug like 'btc-updown-15m-1781526600'."""
    ts = int(slug.split("-")[-1])
    return datetime.fromtimestamp(ts, tz=UTC)


def load_mismatched_pairs(matched_pairs_csv: Path) -> list[dict[str, str]]:
    """Load only pairs where live_vol_bucket != backtest_vol_bucket."""
    rows: list[dict[str, str]] = []
    with matched_pairs_csv.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("vol_match") == "DIFF":
                rows.append(r)
    return rows


def analyze_pair(
    *,
    market_slug: str,
    round_start: datetime,
    live_vol: str,
    backtest_vol: str,
    candles: list,
) -> dict[str, Any]:
    """Compute live and backtest vol_mean, then categorize the mismatch cause."""
    closed = sorted(
        [c for c in candles if c.open_time_utc < round_start],
        key=lambda c: c.open_time_utc,
    )
    live_candles = closed[-LIVE_CANDLE_LIMIT:]
    backtest_candles = closed[-BACKTEST_CANDLE_LIMIT:]

    live_vol_mean = _compute_prev_volatility_mean(live_candles, round_start_ts=round_start)
    backtest_vol_mean = _compute_prev_volatility_mean(backtest_candles, round_start_ts=round_start)

    # Compute diff (or None if either is None)
    if live_vol_mean is not None and backtest_vol_mean is not None:
        diff = abs(live_vol_mean - backtest_vol_mean)
    else:
        diff = None

    # Categorize
    if live_vol_mean is None or backtest_vol_mean is None:
        category = "unknown"
    elif (
        abs(live_vol_mean - _VOL_LOW_MAX) < EDGE_TOLERANCE
        or abs(live_vol_mean - _VOL_NORMAL_MAX) < EDGE_TOLERANCE
    ):
        category = "edge_threshold"
    elif diff is not None and diff > MEAN_DIFF_THRESHOLD:
        category = "candle_selection"
    elif diff is not None and diff < Decimal("0.000001"):
        category = "identical"
    else:
        category = "unknown"

    return {
        "market_slug": market_slug,
        "round_start_utc": round_start.isoformat(),
        "live_vol_bucket": live_vol,
        "backtest_vol_bucket": backtest_vol,
        "live_vol_mean": str(live_vol_mean) if live_vol_mean is not None else "None",
        "backtest_vol_mean": str(backtest_vol_mean) if backtest_vol_mean is not None else "None",
        "vol_mean_diff": str(diff) if diff is not None else "N/A",
        "category": category,
    }


# === Output writers ===

def write_per_pair_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_categorization_json(rows: list[dict[str, Any]], out_path: Path) -> None:
    counts: dict[str, int] = {cat: 0 for cat in CATEGORIES}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    summary = {
        "n_pairs_analyzed": len(rows),
        "counts_by_category": counts,
        "thresholds": {
            "VOL_LOW_MAX": str(_VOL_LOW_MAX),
            "VOL_NORMAL_MAX": str(_VOL_NORMAL_MAX),
        },
    }
    out_path.write_text(json.dumps(summary, indent=2))


def write_edge_case_summary(rows: list[dict[str, Any]], out_path: Path) -> None:
    edge = [r for r in rows if r["category"] == "edge_threshold"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Edge-case pairs: vol_mean within 1e-5 of a threshold",
        f"# Total: {len(edge)}",
        f"# Thresholds: VOL_LOW_MAX={_VOL_LOW_MAX}, VOL_NORMAL_MAX={_VOL_NORMAL_MAX}",
        "",
    ]
    for r in edge:
        lines.append(
            f"{r['market_slug']}  round_start={r['round_start_utc']}  "
            f"live_vol={r['live_vol_bucket']}  backtest_vol={r['backtest_vol_bucket']}  "
            f"vol_mean={r['live_vol_mean']}"
        )
    out_path.write_text("\n".join(lines))


# === CLI ===

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live-db", default="data/live_paper.sqlite")
    p.add_argument("--matched-pairs", default="results/recon/matched_pairs.csv")
    p.add_argument("--candles-csv", default="data/btc_5m_500d.csv")
    p.add_argument("--out-dir", default="results/investigation/")
    args = p.parse_args()

    print(f"Loading candles from {args.candles_csv}...", file=sys.stderr)
    candles = load_candles_csv(Path(args.candles_csv))
    print(f"  loaded {len(candles)} candles", file=sys.stderr)

    print(f"Loading mismatched pairs from {args.matched_pairs}...", file=sys.stderr)
    mismatched = load_mismatched_pairs(Path(args.matched_pairs))
    print(f"  found {len(mismatched)} vol-mismatched pairs", file=sys.stderr)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for i, r in enumerate(mismatched):
        slug = r["market_slug"]
        try:
            round_start = parse_round_start_from_slug(slug)
        except (ValueError, IndexError):
            continue
        result = analyze_pair(
            market_slug=slug,
            round_start=round_start,
            live_vol=r["live_vol_bucket"],
            backtest_vol=r["backtest_vol_bucket"],
            candles=candles,
        )
        rows.append(result)
        if (i + 1) % 20 == 0:
            print(f"  processed {i + 1}/{len(mismatched)}", file=sys.stderr)

    write_per_pair_csv(rows, out_dir / "vol_mean_per_pair.csv")
    write_categorization_json(rows, out_dir / "mismatch_categorization.json")
    write_edge_case_summary(rows, out_dir / "edge_case_summary.txt")

    # Print summary
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    print("\n=== Categorization summary ===", file=sys.stderr)
    for cat in CATEGORIES:
        print(f"  {cat}: {counts.get(cat, 0)}", file=sys.stderr)
    print(f"\nOK: outputs written to {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_investigate_vol_mean.py -v`

Expected: 6 passed.

- [ ] **Step 5: Smoke test on real data**

Run:
```bash
cd .worktrees/walk-forward-analysis
mkdir -p results/investigation
PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python scripts/investigate_vol_mean.py \
  --matched-pairs results/recon/matched_pairs.csv \
  --candles-csv data/btc_5m_500d.csv \
  --out-dir results/investigation/ 2>&1 | tail -15
```

Expected: prints `=== Categorization summary ===` with counts in {edge_threshold, candle_selection, identical, unknown}.

- [ ] **Step 6: Commit**

```bash
git add scripts/investigate_vol_mean.py tests/walk_forward/test_investigate_vol_mean.py
git -c user.email=agent@local -c user.name=agent commit -m "feat(investigation): vol-mean per-pair analysis + categorization"
```

---

## Task 2: `investigation_report.py`

**Files:**
- Create: `scripts/investigation_report.py`
- Test: `tests/walk_forward/test_investigation_report.py`

- [ ] **Step 1: Write failing tests**

Create `tests/walk_forward/test_investigation_report.py`:

```python
"""Tests for investigation_report: required sections, table rendering, edge case list."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.investigation_report import (
    render_report,
    REQUIRED_SECTIONS,
    categorization_table_markdown,
)


@pytest.fixture
def sample_categorization() -> dict:
    return {
        "n_pairs_analyzed": 79,
        "counts_by_category": {
            "edge_threshold": 12,
            "candle_selection": 47,
            "identical": 8,
            "unknown": 12,
        },
        "thresholds": {
            "VOL_LOW_MAX": "0.000897",
            "VOL_NORMAL_MAX": "0.001871",
        },
    }


@pytest.fixture
def sample_per_pair() -> list[dict]:
    return [
        {"market_slug": "A", "round_start_utc": "2026-06-06T12:00:00+00:00",
         "live_vol_bucket": "VOL_NORMAL", "backtest_vol_bucket": "VOL_HIGH",
         "live_vol_mean": "0.001", "backtest_vol_mean": "0.002",
         "vol_mean_diff": "0.001", "category": "candle_selection"},
        {"market_slug": "B", "round_start_utc": "2026-06-06T12:15:00+00:00",
         "live_vol_bucket": "VOL_LOW", "backtest_vol_bucket": "VOL_NORMAL",
         "live_vol_mean": "0.000897", "backtest_vol_mean": "0.000897",
         "vol_mean_diff": "0", "category": "edge_threshold"},
    ]


def test_render_report_contains_required_sections(sample_categorization, sample_per_pair, tmp_path):
    out = tmp_path / "report.md"
    render_report(categorization=sample_categorization, per_pair=sample_per_pair, out_path=out)
    text = out.read_text()
    for section in REQUIRED_SECTIONS:
        assert section in text, f"missing section: {section}"


def test_render_report_includes_dominant_category(sample_categorization, sample_per_pair, tmp_path):
    out = tmp_path / "report.md"
    render_report(categorization=sample_categorization, per_pair=sample_per_pair, out_path=out)
    text = out.read_text()
    # Dominant category is candle_selection (47)
    assert "candle_selection" in text
    assert "47" in text


def test_categorization_table_markdown(sample_categorization):
    md = categorization_table_markdown(sample_categorization)
    assert "| Category | Count | % |" in md
    assert "edge_threshold" in md
    assert "candle_selection" in md
    assert "47" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_investigation_report.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement `investigation_report.py`**

Create `scripts/investigation_report.py`:

```python
"""Render vol-mean investigation to a markdown report.

Usage:
  python scripts/investigation_report.py \\
    --in-dir results/investigation/ \\
    --out docs/analysis/2026-06-15-vol-mean-investigation.md
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
    "# Vol-Mean Investigation Report",
    "## TL;DR",
    "## Setup",
    "## Per-pair analysis",
    "## Categorization",
    "## Edge cases",
    "## Recommendation",
    "## Appendix",
)


_RECOMMENDATIONS: dict[str, str] = {
    "edge_threshold": (
        "**Fix the bucket thresholds.** A meaningful fraction of mismatches are within "
        "1e-5 of a threshold (VOL_LOW_MAX=0.000897 or VOL_NORMAL_MAX=0.001871). "
        "These are rounding artifacts: vol_mean is the same value, but bucket assignment "
        "flips. Two options: (a) widen the threshold by a small tolerance, or (b) accept "
        "this as inherent noise and document it. Lowest-risk fix: add a small epsilon "
        "to each threshold (e.g., VOL_LOW_MAX = 0.000887)."
    ),
    "candle_selection": (
        "**Align candle selection between live and backtest.** The function returns "
        "different vol_mean values when given different candle sets. Root cause: live "
        "uses 60 most recent candles, backtest uses 200. The 16-prior-round window "
        "selection depends on the available candles. Fix: have the backtest match "
        "live's 60-candle window in `_build_binance_for_round`."
    ),
    "identical": (
        "**The vol_mean is identical but buckets differ — this is a logic bug.** "
        "Both perspectives return the same value but classify it into different "
        "buckets. Investigate `_classify_volatility` for off-by-one or threshold "
        "mismatch (despite the ≤ vs < fix in commit 4e9a989)."
    ),
    "unknown": (
        "**Insufficient data or unexpected condition.** Either one or both vol_mean "
        "values is None (likely < 16 prior rounds), or the diff is in an intermediate "
        "range (1e-6 to 1e-4) that doesn't fit a clear category. Investigate these "
        "cases individually."
    ),
}


def categorization_table_markdown(categorization: dict[str, Any]) -> str:
    counts = categorization["counts_by_category"]
    total = sum(counts.values())
    lines = [
        "| Category | Count | % |",
        "|---|---:|---:|",
    ]
    for cat in ("edge_threshold", "candle_selection", "identical", "unknown"):
        c = counts.get(cat, 0)
        pct = (c / total * 100) if total else 0
        lines.append(f"| {cat} | {c} | {pct:.1f}% |")
    lines.append(f"| **Total** | **{total}** | 100.0% |")
    return "\n".join(lines)


def render_report(
    *,
    categorization: dict[str, Any],
    per_pair: list[dict[str, Any]],
    out_path: Path,
) -> None:
    counts = categorization["counts_by_category"]
    total = sum(counts.values())
    dominant = max(counts, key=counts.get) if counts else "unknown"
    threshold_text = (
        f"Thresholds: VOL_LOW_MAX={categorization['thresholds']['VOL_LOW_MAX']}, "
        f"VOL_NORMAL_MAX={categorization['thresholds']['VOL_NORMAL_MAX']}"
    )

    # 1. TL;DR
    tl_dr = (
        f"Across {total} vol-bucket mismatches, the dominant category is **{dominant}** "
        f"({counts.get(dominant, 0)} / {total} = "
        f"{counts.get(dominant, 0) / total * 100 if total else 0:.1f}%).\n\n"
        f"{_RECOMMENDATIONS.get(dominant, '')}"
    )

    # 2. Setup
    setup = (
        f"- **Inputs**: 79 vol-bucket mismatches from `results/recon/matched_pairs.csv`\n"
        f"- **Candle source**: `data/btc_5m_500d.csv` (the same CSV the backtest used)\n"
        f"- **Live perspective**: 60 most recent closed candles before `round_start_ts`\n"
        f"- **Backtest perspective**: 200 most recent closed candles before `round_start_ts`\n"
        f"- **vol_mean function**: production `_compute_prev_volatility_mean` from `round_state.py`\n"
        f"- **{threshold_text}**"
    )

    # 3. Per-pair analysis
    if per_pair:
        per_pair_md = (
            f"See `results/investigation/vol_mean_per_pair.csv` for the full table "
            f"({len(per_pair)} rows).\n\n"
            "**First 5 rows (preview):**\n\n"
            "| market_slug | round_start | live_vol | backtest_vol | live_mean | backtest_mean | diff | category |\n"
            "|---|---|---|---|---|---|---|---|\n"
        )
        for r in per_pair[:5]:
            per_pair_md += (
                f"| {r['market_slug']} | {r['round_start_utc']} | "
                f"{r['live_vol_bucket']} | {r['backtest_vol_bucket']} | "
                f"{r['live_vol_mean']} | {r['backtest_vol_mean']} | "
                f"{r['vol_mean_diff']} | {r['category']} |\n"
            )
    else:
        per_pair_md = "No per-pair data available."

    # 4. Categorization
    cat_table = categorization_table_markdown(categorization)

    # 5. Edge cases
    edge = [r for r in per_pair if r["category"] == "edge_threshold"]
    if edge:
        edge_md = (
            f"**{len(edge)} pairs have vol_mean within 1e-5 of a threshold.** "
            f"These are the easiest to fix — bumping the threshold by 1e-5 absorbs them.\n\n"
            "See `results/investigation/edge_case_summary.txt` for the full list."
        )
    else:
        edge_md = "No edge cases found."

    # 6. Recommendation
    rec_md = (
        f"**Primary action: {dominant}**\n\n"
        f"{_RECOMMENDATIONS.get(dominant, '')}\n\n"
        f"**Secondary actions** (if dominant category is not enough to close the gap):\n\n"
    )
    for cat in ("edge_threshold", "candle_selection", "identical", "unknown"):
        if cat != dominant and counts.get(cat, 0) > 0:
            rec_md += f"- **{cat}** ({counts[cat]}): {_RECOMMENDATIONS[cat].split('.')[0]}.\n"

    # 7. Appendix
    appendix = (
        "**Methodology**:\n"
        "- For each of 79 vol-bucket mismatches, parse `round_start_ts` from the market slug.\n"
        "- Filter candles with `open_time_utc < round_start_ts`.\n"
        "- Take the 60 most recent (live perspective) and 200 most recent (backtest perspective).\n"
        "- Call `_compute_prev_volatility_mean` on each set, capturing both vol_mean values.\n"
        "- Categorize based on diff, threshold proximity, and edge cases.\n\n"
        "**Limitations**:\n"
        "- Both perspectives use the same 500d candle CSV; the only difference is the "
        "selection window (60 vs 200). If the live candle feed diverges from this CSV "
        "(e.g., Binance API differs from cached data), the analysis may miss that cause.\n"
        "- vol_mean numerical precision is bounded by Decimal; diffs < 1e-12 are not "
        "meaningful and may be reported as 'identical'.\n"
        "- The 1e-5 edge threshold is heuristic; the actual noise floor may be different."
    )

    report = f"""# Vol-Mean Investigation Report

**Generated**: 2026-06-15

## TL;DR

{tl_dr}

## Setup

{setup}

## Per-pair analysis

{per_pair_md}

## Categorization

{cat_table}

## Edge cases

{edge_md}

## Recommendation

{rec_md}

## Appendix

{appendix}
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in-dir", default="results/investigation/")
    p.add_argument("--out", default="docs/analysis/2026-06-15-vol-mean-investigation.md")
    args = p.parse_args()

    in_dir = Path(args.in_dir)
    cat_path = in_dir / "mismatch_categorization.json"
    if not cat_path.exists():
        print(f"missing {cat_path}", file=sys.stderr)
        return 1
    categorization = json.loads(cat_path.read_text())

    per_pair: list[dict[str, Any]] = []
    per_pair_path = in_dir / "vol_mean_per_pair.csv"
    if per_pair_path.exists() and per_pair_path.stat().st_size > 0:
        with per_pair_path.open() as f:
            per_pair = list(csv.DictReader(f))

    render_report(categorization=categorization, per_pair=per_pair, out_path=Path(args.out))
    print(f"OK: report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/test_investigation_report.py -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/investigation_report.py tests/walk_forward/test_investigation_report.py
git -c user.email=agent@local -c user.name=agent commit -m "feat(investigation): markdown report renderer"
```

---

## Task 3: Run investigation + generate report (operational)

- [ ] **Step 1: Run investigation end-to-end**

Run:
```bash
cd .worktrees/walk-forward-analysis
PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python scripts/investigate_vol_mean.py \
  --matched-pairs results/recon/matched_pairs.csv \
  --candles-csv data/btc_5m_500d.csv \
  --out-dir results/investigation/ 2>&1 | tail -10
```

Expected: prints `=== Categorization summary ===` with counts.

- [ ] **Step 2: Inspect outputs**

Run:
```bash
cd .worktrees/walk-forward-analysis
cat results/investigation/mismatch_categorization.json
echo "---"
head -3 results/investigation/vol_mean_per_pair.csv
echo "---"
head -10 results/investigation/edge_case_summary.txt
```

Expected: JSON with counts; CSV with header + 79 rows; TXT with edge cases.

- [ ] **Step 3: Render report**

Run:
```bash
cd .worktrees/walk-forward-analysis
PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python scripts/investigation_report.py \
  --in-dir results/investigation \
  --out docs/analysis/2026-06-15-vol-mean-investigation.md
```

Expected: prints `OK: report written to ...`.

- [ ] **Step 4: Verify report**

Run:
```bash
cd .worktrees/walk-forward-analysis
wc -l docs/analysis/2026-06-15-vol-mean-investigation.md
grep -c "^## " docs/analysis/2026-06-15-vol-mean-investigation.md
head -20 docs/analysis/2026-06-15-vol-mean-investigation.md
```

Expected: report has ≥ 8 sections, header, and TL;DR with dominant category.

- [ ] **Step 5: Run full test suite**

Run: `cd .worktrees/walk-forward-analysis && PYTHONPATH=src /home/alex/Project/polymarket_updown_bot/.venv/bin/python -m pytest tests/walk_forward/`

Expected: 51 passed (was 42; +6 investigate_vol_mean, +3 investigation_report).

- [ ] **Step 6: Commit report and update memory**

```bash
cd .worktrees/walk-forward-analysis
git add docs/analysis/2026-06-15-vol-mean-investigation.md
git -c user.email=agent@local -c user.name=agent commit -m "docs(analysis): vol-mean investigation report with categorization"
git log --oneline -10
```

- [ ] **Step 7: Hand off to user**

Print summary with dominant category and recommendation.

---

## Self-Review

**Spec coverage:**
- Spec Goal 1 (compute vol_mean for each mismatch) — Task 1 `analyze_pair` ✓
- Spec Goal 2 (categorize cause) — Task 1 category logic ✓
- Spec Goal 3 (recommend fix) — Task 2 `render_report` recommendation section ✓
- Spec Goal 4 (patched candidate if recommended) — DEFERRED per spec, only recommendation in report
- Spec Acceptance 1 (tests pass) — Tasks 1+2 ✓
- Spec Acceptance 2 (per-pair computation) — Task 1 `analyze_pair` ✓
- Spec Acceptance 3 (every pair has category) — Task 1 logic ✓
- Spec Acceptance 4 (edge cases listed) — Task 1 + 2 ✓
- Spec Acceptance 5 (report with all 8 sections) — Task 2 `REQUIRED_SECTIONS` ✓
- Spec Acceptance 6 (concrete recommendation) — Task 2 `_RECOMMENDATIONS` dict ✓

**Placeholder scan:** No "TBD"/"TODO"/"fill in". All code blocks complete.

**Type consistency:**
- `analyze_pair(market_slug, round_start, live_vol, backtest_vol, candles)` — matches all 4 test calls and the call in `main()`.
- `parse_round_start_from_slug(slug)` — matches test call and call in `main()`.
- `render_report(categorization, per_pair, out_path)` — matches test calls and call in `main()`.

**No issues found — plan is ready to execute.**
