# Vol-Mean Investigation Design

**Date:** 2026-06-15
**Project:** `polymarket_updown_bot`
**Branch:** `feature/walk-forward-analysis` (continues reconciliation work)
**Status:** Draft for user review

## Context

Walk-forward reconciliation on 2026-06-15 (commit `b941cc0`) revealed that **79 of 187 matched pairs (42%) have volatility bucket mismatches** between live and backtest. Volatility bucket drives rule selection; rule_id mismatches in 80 pairs (43%) are mostly downstream of vol bucket mismatches; pnl differs in 99% of matched pairs. Verdict was C (settlement dominance) but root cause is B (state construction), specifically volatility bucket.

We know:
- Both live and backtest call the same `_compute_prev_volatility_mean()` in `round_state.py`
- Both use the same threshold constants (`_VOL_LOW_MAX = 0.000897`, `_VOL_NORMAL_MAX = 0.001871`)
- Commit `4e9a989` already aligned live's volatility source to use 16 prior 15m rounds (matching reference)
- Live fetches 60 candles (5 hours), backtest uses 200 (16+ hours); both have enough for 16 prior rounds

**Open question:** If the function and inputs are the same, why does vol bucket differ?

Three plausible explanations:
1. **Round start time alignment:** live round start_ts is exact to the millisecond, backtest may be off by 1-2 seconds. If `round_start_ts` is even 1 second off, the "previous 16 rounds" set changes (rounds that ended at `round_start_ts - 1s` may be included or excluded).
2. **Candle selection differences:** live fetches candles from `data-api.binance.vision`; backtest uses a cached 500d CSV. If the two sources have different `open_time_utc` precision (millisecond vs second rounding), some candles may or may not match.
3. **Live has `fallback_no_pattern` match_type for 7 trades** that select rules from a different vol bucket than state. This isn't a true state mismatch — it's a fallback artifact. Backtest's `simulate_round` calls `_index.lookup()` which returns fallback rules if no exact match, but the **state vol bucket** is computed independently. So both can have same state, but live uses a different rule via fallback.

The 7 fallback cases (live rule_id contains vol_HIGH but state vol_bucket=VOL_NORMAL) explain the rule_id mismatches that are NOT explained by vol bucket state mismatches.

**The remaining 73 vol bucket state mismatches** (where live and backtest both have vol in rule name, just different values) need deeper investigation.

## Goal

Produce a single evidence-driven investigation report (`docs/analysis/2026-06-15-vol-mean-investigation.md`) that answers:

1. **For each vol bucket mismatch (79 pairs), compute the live and backtest `vol_mean` value** and quantify the difference.
2. **For each pair, identify the cause category:** (a) time alignment (round_start_ts drift), (b) candle selection (different candles used in the 16 prior rounds), (c) threshold edge case (vol_mean near 0.000897 or 0.001871), (d) unknown / other.
3. **Recommend a fix** (or confirm "no fix needed" if vol_mean differences are within numerical precision).
4. **If a fix is recommended:** also produce a patched `round_state.py` candidate and an updated `walk_forward_backtest.py` that reads candles the same way as live.

The deliverable is a report and a recommendation. Production code changes are deferred to a follow-up spec.

## Non-Goals

- No changes to live bot code, config, or rules in this iteration.
- No new backtest runs (use the existing 411 backtest trades from `results/recon/`).
- No retrain of rules.
- No position size or live tuning changes.
- This is **read-only analysis + report**: query live DB, query backtest results, and possibly a candidate patch as a *proposal* (not committed to live).

## Approach

A focused analytical script (`scripts/investigate_vol_mean.py`) that:

```
Live DB (data/live_paper.sqlite)
        ↓
  For each matched pair (187):
    - Get round_start_ts (from market_slug)
    - Get vol_bucket (live, from paper_positions)
    - Get rule_id (live, from settlements)
        ↓
Backtest results (results/recon/wf_fold_0_trades.csv)
    - Get vol_bucket (backtest, from CSV)
    - Get rule_id (backtest, from CSV)
        ↓
For each vol bucket mismatch:
    - Compute live vol_mean by replaying _compute_prev_volatility_mean
      with the SAME candles live used (from data/btc_5m_500d.csv)
    - Compute backtest vol_mean from the backtest engine's perspective
    - Compare both vol_means to thresholds
        ↓
Categorize:
    - "edge case" if |vol_mean - threshold| < 1e-5
    - "time alignment" if round_start_ts is non-UTC-quarter-hour
    - "candle selection" if the 16 prior rounds differ
    - "unknown" otherwise
        ↓
Write:
  results/investigation/vol_mean_per_pair.csv
  results/investigation/mismatch_categorization.csv
  results/investigation/edge_case_summary.txt
        ↓
Render report (scripts/investigation_report.py):
  docs/analysis/2026-06-15-vol-mean-investigation.md
```

**Why this design:**
- Reuses the existing `_compute_prev_volatility_mean` function (re-imported and called) — no risk of drift from production logic.
- Reads the same 500d CSV that backtest used, so the candle source is identical to what backtest saw.
- For live, we use the 500d CSV too (not a separate live candle feed) because the candles **at the time of the live trade** should match the candles **at the time of the backtest** (both 5m bars from Binance). The only difference is the **filtering**: live uses 60 most recent closed candles (`limit=60`), backtest uses 200 most recent (`closed[-200:]`).
- This isolates the "candle selection" hypothesis from the "round start time" hypothesis.

## Architecture

### Component 1: `scripts/investigate_vol_mean.py` (NEW)

**Purpose:** For each vol-bucket mismatch, compute the live and backtest `vol_mean` from the same candle source, then categorize the mismatch cause.

**Inputs:**
- `data/live_paper.sqlite` — for live `round_start_ts` and `vol_bucket_at_entry`
- `results/recon/matched_pairs.csv` — for the 187 matched pairs and their vol_buckets
- `data/btc_5m_500d.csv` — for candle data (the same source the backtest used)
- `src/polymarket_round_bot/round_state.py` — import `_compute_prev_volatility_mean`, `_VOL_LOW_MAX`, `_VOL_NORMAL_MAX`

**Per-pair computation:**

```python
def analyze_pair(market_slug: str, live_vol: str, backtest_vol: str, candles: list[Candle]):
    # Parse round_start_ts from slug
    ts = int(market_slug.split("-")[-1])
    round_start = datetime.fromtimestamp(ts, tz=UTC)
    
    # Live perspective: take 60 most recent closed candles before round_start
    live_candles = [c for c in candles if c.open_time_utc < round_start][-60:]
    live_vol_mean = _compute_prev_volatility_mean(live_candles, round_start_ts=round_start)
    
    # Backtest perspective: take 200 most recent closed candles
    backtest_candles = [c for c in candles if c.open_time_utc < round_start][-200:]
    backtest_vol_mean = _compute_prev_volatility_mean(backtest_candles, round_start_ts=round_start)
    
    # Both should match if (a) candle selection is not the issue, (b) time alignment is correct
    diff = abs(live_vol_mean - backtest_vol_mean) if (live_vol_mean and backtest_vol_mean) else None
    
    # Categorize
    if diff is None:
        category = "unknown"  # one or both returned None (insufficient prior rounds)
    elif diff < 1e-6:
        category = "identical"  # vol_mean same → mismatch is from something else (e.g., threshold edge)
    elif live_vol_mean and abs(live_vol_mean - _VOL_LOW_MAX) < 1e-5:
        category = "edge_LOW_threshold"
    elif live_vol_mean and abs(live_vol_mean - _VOL_NORMAL_MAX) < 1e-5:
        category = "edge_NORMAL_threshold"
    elif abs(live_vol_mean - backtest_vol_mean) > 1e-4:
        category = "candle_selection"  # different candles produce different means
    else:
        category = "unknown"
    
    return {
        "market_slug": market_slug,
        "round_start_utc": round_start.isoformat(),
        "live_vol_bucket": live_vol,
        "backtest_vol_bucket": backtest_vol,
        "live_vol_mean": str(live_vol_mean) if live_vol_mean else "None",
        "backtest_vol_mean": str(backtest_vol_mean) if backtest_vol_mean else "None",
        "vol_mean_diff": str(diff) if diff is not None else "N/A",
        "category": category,
    }
```

**CLI:**
- `--live-db` (default: `data/live_paper.sqlite`)
- `--matched-pairs` (default: `results/recon/matched_pairs.csv`)
- `--candles-csv` (default: `data/btc_5m_500d.csv`)
- `--out-dir` (default: `results/investigation/`)

**Output:**
- `results/investigation/vol_mean_per_pair.csv` — one row per vol-mismatch pair with live_mean, backtest_mean, diff, category
- `results/investigation/mismatch_categorization.json` — counts by category
- `results/investigation/edge_case_summary.txt` — list of pairs in edge_case categories

### Component 2: `scripts/investigation_report.py` (NEW)

**Purpose:** Render investigation results to a markdown report.

**Output:** `docs/analysis/2026-06-15-vol-mean-investigation.md` with sections:
1. **TL;DR** — one paragraph summary with dominant cause category
2. **Setup** — data sources, sample size, what was computed
3. **Per-pair analysis** — distribution of vol_mean values for live vs backtest, side-by-side
4. **Categorization** — count and % of each category
5. **Edge cases** — pairs where vol_mean is within 1e-5 of a threshold (these are the "easy fixes" — change threshold to remove the artifact)
6. **Recommendation** — concrete next step (e.g., "fix candle selection", "add tolerance to threshold", or "no fix needed; gap is acceptable")
7. **Appendix** — methodology, per-category detail

**Required sections (for testability):**
```python
REQUIRED_SECTIONS = (
    "# Vol-Mean Investigation Report",
    "## TL;DR",
    "## Setup",
    "## Per-pair analysis",
    "## Categorization",
    "## Edge cases",
    "## Recommendation",
    "## Appendix",
)
```

### Tests: `tests/walk_forward/test_investigate_vol_mean.py`

Unit tests for the analytical logic:
- `test_analyze_pair_identical_vol_means`: synthetic candles, both perspectives return same mean → category "identical"
- `test_analyze_pair_edge_case_threshold`: vol_mean within 1e-5 of `_VOL_LOW_MAX` → category "edge_LOW_threshold"
- `test_analyze_pair_candle_selection_differs`: live_candles (60) and backtest_candles (200) give different means → category "candle_selection"
- `test_analyze_pair_insufficient_data`: <16 prior rounds → category "unknown" with vol_mean=None
- `test_analyze_pair_round_start_ts_parse`: verify market_slug → datetime parsing
- `test_categorization_counts`: synthetic 10-pair data → verify counts add up

Pytest fixtures: small synthetic candle sets (20 candles spanning 4 hours).

## Methodology

### Step 1: Parse round_start_ts from market_slug

Market slugs are formatted as `btc-updown-15m-{unix_ts}` where `unix_ts` is the round start time in seconds. We can reconstruct `round_start_ts` directly:

```python
ts = int(market_slug.split("-")[-1])
round_start = datetime.fromtimestamp(ts, tz=UTC)
```

This is the same formula used in `url_parser.current_expected_slug()` and our backtest's `_build_market_for_round()`. No drift expected.

### Step 2: Re-run _compute_prev_volatility_mean from both perspectives

We import the actual production function and call it twice per pair:

```python
from polymarket_round_bot.round_state import _compute_prev_volatility_mean

live_candles = sorted_candles_filtered_to_last_60_before_round_start
backtest_candles = sorted_candles_filtered_to_last_200_before_round_start

live_vol_mean = _compute_prev_volatility_mean(live_candles, round_start_ts=round_start)
backtest_vol_mean = _compute_prev_volatility_mean(backtest_candles, round_start_ts=round_start)
```

If both functions are called on the same 16-round window, they MUST return the same value (the function is deterministic). Differences imply candle selection.

### Step 3: Compare vol_mean to thresholds

- `_VOL_LOW_MAX = 0.000897` (33rd percentile of historical vol)
- `_VOL_NORMAL_MAX = 0.001871` (66th percentile)

Edge case: if `|vol_mean - threshold| < 1e-5`, the rounding could plausibly flip the bucket. These are the "easy fixes" — moving a threshold by 1e-5 could reduce mismatches.

### Step 4: Categorize each mismatch

Categories (in order of priority):
1. **`edge_threshold`** (1e-5 from any threshold) — threshold tuning candidate
2. **`candle_selection`** (|live_mean - backtest_mean| > 1e-4) — likely 16-round window selection issue
3. **`identical`** (|live_mean - backtest_mean| < 1e-6) — same value but different buckets. This is impossible if both bucket via the same function and same mean — indicates one of them uses a different function or skip-list.
4. **`unknown`** (one or both returned None) — insufficient prior rounds; should not happen for matched pairs (we have 60 candles ≥ 4 hours)

### Step 5: Cross-check with rule_id

For each pair where live and backtest have the same `vol_mean` (or differ by < 1e-6), but their vol_buckets differ, the root cause is not in vol_mean — it's in:
- Bucket thresholds (live uses ≤, backtest uses <) — but commit 4e9a989 already fixed this to ≤
- Or fallback rules (live uses fallback_no_pattern; backtest also uses fallback but maybe different fallback order)

This is the secondary investigation: rule_id mismatches that aren't explained by vol_mean.

## Outputs

```
results/investigation/vol_mean_per_pair.csv          # one row per vol-mismatch
results/investigation/mismatch_categorization.json   # counts by category
results/investigation/edge_case_summary.txt          # list of edge cases
docs/analysis/2026-06-15-vol-mean-investigation.md   # main report (committed)
```

**Total artifact size:** ~80 rows, < 1 MB. Report < 100 KB.

## Acceptance Criteria

The investigation is "done" when:

1. **Tests pass:** `pytest tests/walk_forward/test_investigate_vol_mean.py` — at least 6 tests.
2. **Per-pair computation:** for each of the 79 vol-mismatches, both live_vol_mean and backtest_vol_mean are computed (or both None).
3. **Categorization:** every pair has a category from {`edge_threshold`, `candle_selection`, `identical`, `unknown`}.
4. **Edge cases listed:** `edge_case_summary.txt` lists all pairs within 1e-5 of a threshold with their vol_mean values.
5. **Report generated:** `docs/analysis/2026-06-15-vol-mean-investigation.md` exists with all 8 `REQUIRED_SECTIONS`.
6. **Recommendation is concrete:** the report's "Recommendation" section names ONE primary action: either "fix candle selection by X" or "add threshold tolerance Y" or "no fix; gap is within numerical precision".

## Out of Scope (Deferred)

- **Fixing vol_mean mismatch in code** (separate spec, separate PR).
- **Live config changes** (e.g., `MIN_SAMPLES` adjustment).
- **Approach 2 (retrain rules)** — still deferred until root cause is identified and (if needed) fixed.
- **Production deployment** of any investigation findings.

## Open Questions for User

None — this is a direct follow-up to the reconciliation finding. User already approved the direction ("так" on 2026-06-15).

## Start Here

For implementation:
1. Write `tests/walk_forward/test_investigate_vol_mean.py` first (TDD).
2. Implement `scripts/investigate_vol_mean.py` to pass those tests.
3. Run on real data (`results/recon/matched_pairs.csv`).
4. Implement `scripts/investigation_report.py` (TDD).
5. Render the report.
6. Hand off with a clear recommendation.
