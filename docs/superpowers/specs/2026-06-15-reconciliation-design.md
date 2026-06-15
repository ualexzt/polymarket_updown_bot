# Live vs Backtest Reconciliation Design

**Date:** 2026-06-15
**Project:** `polymarket_updown_bot`
**Branch:** `feature/walk-forward-analysis` (continues walk-forward work)
**Status:** Draft for user review

## Context

Walk-forward backtest on 2026-06-15 showed a **40pp+ gap** between backtested WR (69.94% on 6,961 historical trades) and live WR (59.1% on 254 trades). On the most recent 9 days that overlap, backtest predicts **+$71.60 / WR 71.6%** while live recorded **−$3.72 / WR 28.6%** on just 7 settled trades.

This is a structural discrepancy, not noise. Without understanding the cause, we cannot:
- Trust any backtest finding (Top-10 rules, regime breakdown, etc.)
- Make any live tuning decision (whitelist, thresholds, position size)
- Plan Approach 2 (retrain), because retraining a broken backtest gives a broken whitelist

The walk-forward analysis (`docs/analysis/2026-06-15-walk-forward.md`) is a research artifact that is now in question. We need to **reconcile** before deciding next steps.

## Goal

Produce a single evidence-driven reconciliation report (`docs/analysis/2026-06-15-reconciliation.md`) that answers:

1. **Coverage:** Of the 254 all-time live settlements, how many of them have a corresponding backtest trade (matched by `market_slug`)? How many live settlements are *live-only* (no backtest trade)? How many backtest trades in the live period are *backtest-only* (no live trade)?
2. **State match:** For matched pairs, do `state.candle_pattern`, `volatility_bucket`, `distance_bucket`, `current_side`, and `stage` agree exactly?
3. **Entry match:** For matched pairs, does `entry_price` (live `best_ask`) agree with backtest `historical_probability − safety_buffer`? By how much?
4. **Settlement match:** For matched pairs, does `won` and `pnl` agree?
5. **Verdict:** Categorize the discrepancy as A (filter), B (state construction), C (settlement), or D (insufficient data). Provide a specific next-step recommendation for the dominant category.

## Non-Goals

- No changes to live bot code, rules, or config.
- No new backtest run with different parameters.
- No retrain of rules.
- No position size or live deployment changes.
- This is **read-only analysis**: queries the live DB and the backtest engine, produces a report, points to the next spec.

## Approach

A focused 4-step offline analysis using existing infrastructure:

```
Server (54.154.79.239)                          Local (.worktrees/...)
========================                          =====================
data/polymarket_round_paper.sqlite (223 MB)
        ↓ scp                                    data/live_paper.sqlite
                                                ↓
[1] fetch_binance_history.py
        ↓ (re-use 500d data)
data/btc_5m_500d.csv
        ↓
[2] walk_forward_backtest.py
   --test-days 9 (overlap with live period)
        ↓
results/recon/live_period_trades.csv
        ↓
[3] reconcile_live_vs_backtest.py (NEW)
   - Read live settlements from data/live_paper.sqlite
   - Read backtest trades from results/recon/
   - Join by market_slug
   - Compare fields, categorize gaps
        ↓
results/recon/matched_pairs.csv
results/recon/live_only_trades.csv
results/recon/backtest_only_trades.csv
        ↓
[4] reconciliation_report.py (NEW)
   - Render markdown from CSVs
        ↓
docs/analysis/2026-06-15-reconciliation.md
```

**Why this design:**
- Reuses the proven backtest engine from Task 1-6 of the walk-forward plan, so backtest results are trustworthy as a reference.
- New scripts are small and focused: ~150-300 LoC each.
- Output is structured CSVs + markdown — easy to inspect, easy to extend, easy to cite in a follow-up spec.
- No interference with live bot: the live container keeps running, we just snapshot the DB once.

## Architecture

### Component 1: One-time `scp` of live DB

**Files:** none (operational step)

Run from the worktree:
```bash
mkdir -p data
scp -i ~/.ssh/polymarket-mm-key.pem ubuntu@54.154.79.239:/home/ubuntu/polymarket_updown_bot/data/polymarket_round_paper.sqlite data/live_paper.sqlite
```

The DB is already in `.gitignore` (`data/` line). Local copy is read-only.

**Verify:** `sqlite3 data/live_paper.sqlite "SELECT COUNT(*) FROM settlements"` should return 254 (or close to it — bot may have added a few more settlements since 14:04 UTC).

### Component 2: Re-run backtest on the live period (2026-06-06 → 2026-06-15)

**Files:** none new (use existing `walk_forward_backtest.py`)

Run:
```bash
python scripts/walk_forward_backtest.py \
  --data data/btc_5m_500d.csv \
  --rules config/btc_updown_state_rules_15m.json \
  --out-dir results/recon \
  --folds 1 --test-days 9
```

**Why 9 days:** live started 2026-06-06 11:51 UTC. To cover the full live period, test window = `[2026-06-06, 2026-06-15)`. The backtest engine partitions the data into folds of `test_days` length, starting from `data_start` (2025-01-31). With `n_folds=1`, the single fold is `[2025-01-31, 2025-01-31 + 9 days) = [2025-01-31, 2025-02-09)` — wrong period.

**Correction:** We need to specify the test window directly. Two options:
1. **Add a CLI flag `--test-start` / `--test-end`** to `walk_forward_backtest.py` for arbitrary windows. Cleanest, useful for future too.
2. **Run with `n_folds=N` and slice the output** — hacky, more code.

We pick option 1: add `--test-start` and `--test-end` (both ISO datetimes) to `walk_forward_backtest.py`. When provided, the script builds a single fold with those boundaries. The existing `--folds` / `--test-days` parameters remain for the default rolling-window mode.

**Implementation:** append ~15 lines to `run_pipeline()` in `scripts/walk_forward_backtest.py`. Add a `Fold` with the explicit boundaries. No new tests required for this tiny extension (covered by the existing fold tests; behavior is identical except for the source of fold boundaries).

### Component 3: `scripts/reconcile_live_vs_backtest.py` (NEW)

**Purpose:** Join live settlements with backtest trades by `market_slug` and produce three CSVs (matched, live-only, backtest-only) plus a verdict.

**Data flow:**

```
Read live settlements: SELECT market_slug, selected_side, won, entry_price,
                       realized_pnl_usd, settlement_source, rule_id,
                       historical_probability_at_entry, round_open_price,
                       round_close_price, resolved_at_utc
                       FROM settlements
                       WHERE resolved_at_utc >= '2026-06-06 11:51:00'

Read live decisions: SELECT market_slug, side_checked, selected_side,
                     candle_pattern, volatility_bucket, distance_bucket,
                     stage, current_side, rule_id, historical_probability,
                     fair_price, market_ask, edge_vs_ask, decision, skip_reason,
                     timestamp_utc
                     FROM decisions
                     WHERE decision = 'TRADE'
                     AND timestamp_utc >= '2026-06-06 11:51:00'

Read backtest trades: from results/recon/wf_fold_0_trades.csv
  (already has: market_slug, stage, current_side, distance_bucket,
   volatility_bucket, pattern, rule_id, recommended_side,
   historical_probability, entry_price, won, pnl, round_open_price,
   round_close_price)

Join live settlements with backtest trades:
  - key: market_slug
  - For each match, compare fields

Produce:
  results/recon/matched_pairs.csv   (one row per matched pair, side-by-side)
  results/recon/live_only_trades.csv (live trades with no backtest counterpart)
  results/recon/backtest_only_trades.csv (backtest trades with no live counterpart)
  results/recon/reconciliation_summary.json (verdict + counts)
```

**Field comparison (per matched pair):**

| Field | Live | Backtest | Match criterion |
|---|---|---|---|
| `recommended_side` | `selected_side` | `recommended_side` | exact string |
| `entry_price` | `entry_price` (real ask) | `entry_price` (prob - 0.05) | abs diff ≤ 0.01 |
| `rule_id` | `rule_id` | `rule_id` | exact string |
| `historical_probability` | `historical_probability_at_entry` | `historical_probability` | abs diff ≤ 0.01 |
| `stage` | `stage_at_entry` (from paper_positions) | `stage` | exact string |
| `candle_pattern` | `pattern_at_entry` (from paper_positions) | `pattern` | exact string |
| `volatility_bucket` | `volatility_bucket_at_entry` | `volatility_bucket` | exact string |
| `distance_bucket` | `distance_bucket_at_entry` | `distance_bucket` | exact string |
| `current_side` | `current_side_at_entry` | `current_side` | exact string |
| `won` | `won` | `won` | exact bool |
| `pnl` | `realized_pnl_usd` | `pnl` | abs diff ≤ 0.01 |
| `round_close_price` | `round_close_price` | `round_close_price` | abs diff ≤ 0.01 |

**Verdict logic:**

```
For each matched pair, count field mismatches:
  - state_fields_mismatches: stage, pattern, vol_bucket, dist_bucket, current_side (5 fields)
  - price_fields_mismatches: entry_price, hist_prob (2 fields)
  - settlement_fields_mismatches: won, pnl, round_close (3 fields)

Categorize:
  - If most pairs have state_field mismatches → verdict = B
  - If most pairs have large entry_price mismatches (> 0.05) → verdict = A (spread)
  - If most pairs have settlement mismatches but state matches → verdict = C
  - If matched_pairs_count < 5 → verdict = D (insufficient data, defer)
  - If most pairs match on state but live has trades not in backtest → verdict = A (live filter rejects backtest trades)
  - If most pairs match on state but backtest has trades not in live → verdict = A (backtest doesn't see live constraints)
```

The verdict is a single dominant category. If 60%+ of pairs show a particular mismatch pattern, that's the verdict.

**CLI:**
- `--live-db` (path to live SQLite)
- `--backtest-trades` (path to `wf_fold_0_trades.csv` from results/recon/)
- `--out-dir` (default: `results/recon/`)
- `--entry-tolerance` (default: 0.01)
- `--live-period-start` (default: 2026-06-06T11:51:00Z)
- `--live-period-end` (default: now)

**Output:**
- `results/recon/matched_pairs.csv` — one row per matched pair with `live_*` and `backtest_*` columns
- `results/recon/live_only_trades.csv` — list of live slugs with no backtest match (or with backtest but state mismatch)
- `results/recon/backtest_only_trades.csv` — list of backtest slugs with no live match
- `results/recon/reconciliation_summary.json` — verdict, counts, top discrepancies

### Component 4: `scripts/reconciliation_report.py` (NEW)

**Purpose:** Render reconciliation CSVs into a markdown report.

**Output:** `docs/analysis/2026-06-15-reconciliation.md` with sections:
1. **TL;DR** — verdict (A/B/C/D) in one sentence
2. **Setup** — data sources, live period, matched/unmatched counts
3. **Matched pairs analysis** — table with `live_entry vs backtest_entry`, `live_won vs backtest_won`, etc.
4. **Unmatched analysis** — top reasons why live-only and backtest-only trades exist
5. **Verdict & recommendation** — what the dominant gap is, what to fix first
6. **Appendix** — methodology, SQL queries, raw counts

**Required sections (for testability):**
```python
REQUIRED_SECTIONS = (
    "# Reconciliation Report",
    "## TL;DR",
    "## Setup",
    "## Matched pairs analysis",
    "## Unmatched analysis",
    "## Verdict & recommendation",
    "## Appendix",
)
```

### Tests: `tests/walk_forward/test_reconcile.py`

Unit tests for reconciliation logic:
- `test_match_live_to_backtest_by_slug`: synthetic live + backtest, verify match count
- `test_field_mismatch_detection`: pair where live uses different pattern; verify mismatch flagged
- `test_entry_price_tolerance`: pair where entry differs by 0.005 (within tolerance); match. Pair where entry differs by 0.05; mismatch.
- `test_verdict_logic_state_mismatch`: 4 of 5 pairs have state_field mismatches → verdict B
- `test_verdict_logic_insufficient_data`: only 2 matched pairs → verdict D
- `test_csv_outputs_have_required_columns`: matched_pairs, live_only, backtest_only each have the right schema

Pytest fixtures: synthetic live settlements (5 rows) and synthetic backtest trades (5 rows, 3 matching, 2 backtest-only).

## Methodology

### Live period definition

**Start:** 2026-06-06T11:51:00Z (first `decision.timestamp_utc` after fresh DB start).
**End:** current time (latest settlement or 2026-06-15T15:00:00Z as floor for "now" to keep the analysis deterministic).

**Data sources:**
- **Live DB:** `data/live_paper.sqlite` (snapshot from server, 254 settlements, 130,531 decisions).
- **Backtest data:** already downloaded `data/btc_5m_500d.csv` (144k candles through 2026-06-15).
- **Backtest rules:** `config/btc_updown_state_rules_15m.json` (live rules, 2,014 entries).
- **Backtest engine:** existing `walk_forward_backtest.py` with new `--test-start` / `--test-end` flags.

### Match key

`market_slug` is the natural join key. Both live and backtest record it for every trade. Slugs are formatted as `btc-updown-15m-{unix_ts}`, e.g., `btc-updown-15m-1781526600`. The unix timestamp is the round start, so 1:1 mapping to a unique 15m window.

**Edge case:** the same market_slug can theoretically have multiple settlements (if the bot opened multiple positions, though `MAX_OPEN_POSITIONS=1` prevents this). In our data, this should not occur, but the script handles it: 1 slug → N live settlements (N=1 expected) → M backtest trades (M=1 expected). The match is N:M cross product, but with the duplicate-protection invariant, N=M=1.

### Per-pair field comparison

We do NOT modify live data. We do NOT recompute live state. We only compare persisted fields.

**Live source tables:**
- `settlements` has: `market_slug`, `entry_price`, `won`, `realized_pnl_usd`, `round_open_price`, `round_close_price`, `selected_side`, `rule_id`, `historical_probability_at_entry`, `resolved_at_utc`.
- `paper_positions` has: `stage_at_entry`, `volatility_bucket_at_entry`, `distance_bucket_at_entry`, `current_side_at_entry`, `pattern_at_entry`. (Joined on `position_id`.)
- `decisions` has: `market_ask`, `edge_vs_ask`. (For a complete picture, but the backtest doesn't store these, so they're informational only.)

**Backtest source:** `results/recon/wf_fold_0_trades.csv` (produced by `walk_forward_backtest.py`) has all the analogous fields. We added `round_close_price` and `entry_now_utc` in Task 5; everything else is there.

### Verdict categorization

The verdict is the **dominant** mismatch pattern. The script counts, for each matched pair, how many of the 5 state fields mismatched, how many of the 2 price fields mismatched, etc. Then it picks the category with the most mismatches overall.

**Heuristics for dominant category:**
- A: `live_only_trades_count > 5` AND `state_mismatch_count < 3` (live filtered trades that backtest allowed) — OR `entry_price` differences are large (>0.05) for matched pairs.
- B: `state_mismatch_count > matched_pairs_count * 0.5` — at least half of matched pairs have state field mismatches.
- C: `settlement_mismatch_count > matched_pairs_count * 0.5` — at least half of matched pairs have won/pnl mismatches.
- D: `matched_pairs_count < 5` — not enough data to be confident; recommend waiting for more live settlements or proceeding with caution.

The verdict is the first matching condition. The report explains which condition was triggered and what to do next.

### What we explicitly do NOT do

- **No modification of live bot code, config, or rules.** Read-only analysis.
- **No new backtest run with different parameters** (e.g., we don't rerun with different safety_buffer to see if that closes the gap). If the gap is in entry price (verdict A), we report the magnitude of the difference, but we don't "fix" the backtest.
- **No re-fetching of data.** Use the 500d dataset already downloaded.
- **No analysis of historical (pre-live) periods.** Reconciliation is specifically about the live period, where we have both live and backtest data for the same slugs.

## Outputs

```
data/live_paper.sqlite                       # gitignored, snapshot from server
results/recon/wf_fold_0_trades.csv           # backtest on live period (2026-06-06 → 2026-06-15)
results/recon/matched_pairs.csv              # 1 row per matched pair, live_* vs backtest_*
results/recon/live_only_trades.csv           # live slugs with no backtest counterpart
results/recon/backtest_only_trades.csv       # backtest slugs with no live counterpart
results/recon/reconciliation_summary.json    # verdict + counts + top discrepancies
docs/analysis/2026-06-15-reconciliation.md   # main report (committed)
```

**Total artifact size:** live DB snapshot 223 MB, matched/unmatched CSVs a few hundred rows, report <100 KB.

## Acceptance Criteria

The analysis is "done" when **all** of the following hold:

1. **Live DB snapshot:** `data/live_paper.sqlite` exists, size > 200 MB, sha256 logged in `data/live_paper.sqlite.sha256`.
2. **Backtest on live period:** `results/recon/wf_fold_0_trades.csv` exists with > 0 trades, in the date range 2026-06-06 to 2026-06-15.
3. **Tests:** `pytest tests/walk_forward/test_reconcile.py` passes; at least 5 tests, including `test_verdict_logic_state_mismatch` and `test_verdict_logic_insufficient_data`.
4. **CSV outputs:** `matched_pairs.csv`, `live_only_trades.csv`, `backtest_only_trades.csv` exist with documented columns.
5. **Verdict produced:** `reconciliation_summary.json` has `verdict` ∈ {A, B, C, D} and a `recommendation` string.
6. **Report generated:** `docs/analysis/2026-06-15-reconciliation.md` exists, contains all 7 `REQUIRED_SECTIONS`, includes the verdict and recommendation from the summary JSON, and ends with a "Limitations" section.
7. **Git:** All scripts, tests, and the report are committed to `feature/walk-forward-analysis`.

## Out of Scope (Deferred)

The following are explicitly deferred to follow-up iterations, regardless of the verdict:

1. **Fixing the discovered gap.** Whatever the verdict (A/B/C/D), the fix is a separate spec and a separate PR. The reconciliation report points to the fix; it does not implement it.
2. **Retrain rules (Підхід 2).** Still deferred until reconciliation is done and verdict is clear.
3. **Live config tuning** (whitelist, position size, thresholds). Still deferred.
4. **Continuing live trades.** The live bot continues running through the reconciliation; we only need the existing data.

## Open Questions for User

Resolved:
- Q: Where to put live DB? → **Local, gitignored, one-time scp** (user reply 2026-06-15)

## Start Here

For implementation:
1. `scp` live DB.
2. Add `--test-start` / `--test-end` flags to `walk_forward_backtest.py` (small change).
3. Re-run backtest on the live period.
4. Implement `reconcile_live_vs_backtest.py` (TDD).
5. Implement `reconciliation_report.py` (TDD).
6. Render the report.
7. Hand off to user with a clear verdict.
