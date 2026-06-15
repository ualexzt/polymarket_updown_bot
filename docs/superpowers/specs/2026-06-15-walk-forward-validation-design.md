# Walk-Forward Validation + Breakeven Analysis Design

**Date:** 2026-06-15
**Project:** `polymarket_updown_bot`
**Branch:** `feature/walk-forward-analysis`
**Status:** Draft for user review

## Context

The paper bot is operationally stable (no errors, 124k log lines, container up 3 days) but is structurally unprofitable:

- 254 settlements, 150W/104L, **WR 59.1%, PnL −$49.57** over 9 days
- All-time avg PnL per trade **−$0.195**
- DOWN trades: 100 settled, 55W, **−$32.70** (worse than UP)
- Live bot uses 2,014 rules generated from a 180-day historical backtest in `poly_bot_system/polymarket_round_research_v2.py`
- Audits (`backtest-reference-compare.md`, `live-code-direction-audit.md`) found:
  - WR 60% × avg entry ~$0.65 is mathematically losing (breakeven requires WR > 65%)
  - 5+ mismatches between backtest reference and live state construction (volatility source, distance bucket boundaries, `AT_OPEN` handling, doji label)
  - But no clear directional sign inversion
- **Open question:** Are the 2,014 rules stable across time (out-of-sample), or did the 180d backtest window produce a favorable slice that does not generalize?

We have a small, focused opportunity: take the existing state-building and rule-lookup code, replay it against historical Binance 5m candles, and measure what the strategy *would have done* on out-of-sample periods. This is **historical replay**, not a new model — we are reusing `round_state.py::build_round_state()` and `probability_rules.py` to compute counterfactual trades.

A related audit gap is **"why does WR 60% lose money?"** — that requires a sensitivity/breakeven analysis comparing realized WR, entry price, and required breakeven WR.

## Goal

Produce a single evidence-driven report (`docs/analysis/2026-06-15-walk-forward.md`) that answers:

1. **Stability:** Do the 2,014 live rules deliver consistent WR and PnL across rolling out-of-sample windows, or is the all-time 59.1% WR a function of a specific period?
2. **Breakeven:** At the observed avg entry prices, what WR is required to break even? Where does the strategy sit relative to that line?
3. **Regime sensitivity:** Is the strategy +EV in some market regimes (volatility, distance bucket, hour-of-day, side) and −EV in others?
4. **Cross-check:** Does the backtested PnL of the most recent 11 days (overlapping with live settlements) match the live PnL? If yes, the backtest is a faithful model of live behavior; if no, the state-construction mismatches matter.

The deliverable is **a report and supporting CSVs**, not a code change to the live bot. The report will inform a follow-up decision (whitelist narrower set of rules, raise thresholds, stop strategy, etc.).

## Non-Goals

- No changes to live bot code (`runner.py`, `signal_engine.py`, `paper_broker.py`, `storage.py`).
- No changes to live config (`.env`, `config/btc_updown_state_rules_15m.json`).
- No deployment of new code to the server.
- No model retraining in this iteration. A "Підхід 2: retrain rules per fold" is explicitly deferred (see *Out of scope*).
- No live trading changes (this remains paper-only).
- No changes to `MAX_POSITION_USD`, `MIN_EDGE`, `MAX_ENTRY_ASK`, or any live threshold.
- No rewriting `evaluate_rule_performance.py` or `candle_features.py`. We import them.

## Approach

A 4-script offline pipeline:

```
Binance public API
    ↓
[1] scripts/fetch_binance_history.py
    ↓
data/btc_5m_<N>d.csv  (~1 row per closed 5m candle)
    ↓
[2] scripts/walk_forward_backtest.py
    ↓
results/wf_fold_<i>_trades.csv
results/wf_fold_<i>_summary.json
results/wf_aggregate_summary.json
    ↓
[3] scripts/breakeven_analysis.py
    ↓
results/breakeven_sensitivity.csv
results/rule_performance_ranked.csv
    ↓
[4] scripts/walk_forward_report.py
    ↓
docs/analysis/2026-06-15-walk-forward.md
```

All scripts are offline and read-only with respect to the live bot's data files (no shared state with the running container). Scripts accept `--data-dir`, `--config-dir`, `--out-dir` to make them composable and testable.

We download **as much history as the Binance endpoint reliably serves for 5m klines** in a reasonable time budget, capped at **500 days** (≈ 173k candles, ≈ 110 paginated requests, ≈ 10-15 minutes download). 500 days is roughly 1.5 years of daily seasonality, which is enough to span multiple vol regimes, BTC halving cycles are not relevant at this granularity, and stays well under the 1000-candle-per-request × 500-request-per-hour soft rate limit.

The replay reuses the live state-building and rule-lookup code paths:

- `src/polymarket_round_bot/round_state.py::build_round_state()`
- `src/polymarket_round_bot/probability_rules.py::lookup_rule()`
- `src/polymarket_round_bot/candle_features.py::compute_candle_features()`
- `config/btc_updown_state_rules_15m.json` (read-only)

This is intentional: the goal is to measure what the live bot *would have done* on past data, not to invent a parallel research pipeline.

## Architecture

### Component 1: `scripts/fetch_binance_history.py`

**Purpose:** Download up to 500 days of BTCUSDT 5m klines from Binance public API, save as a single CSV with columns `open_time_utc, open, high, low, close, volume, is_closed, close_time_utc`.

**Behavior:**
- Endpoint: `https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=1000`
- Pagination: walk backwards from `end_time = now` in 1000-candle chunks (5000 minutes ≈ 3.47 days per request)
- Stop when: target days reached, API returns 0/empty, or HTTP error 3 times in a row
- Time budget: 15 minutes max wall clock, 1s sleep between requests
- Resume support: if `data/btc_5m_<N>d.csv.partial` exists with rows, resume from latest `open_time_utc + 5min`
- CLI:
  - `--days` (default: 500)
  - `--symbol` (default: BTCUSDT)
  - `--out` (default: `data/btc_5m_<days>d.csv`)
  - `--resume` (default: false)
- Output:
  - CSV, sorted ascending by `open_time_utc`
  - `data/btc_5m_<N>d.csv.meta.json` with row count, min/max time, sha256, fetch duration

**No-lookahead invariant:** none (raw data, only `is_closed=True` rows are kept from the API to be safe — Binance's klines endpoint always returns closed candles when queried with a finalized timestamp, but we drop any row whose `close_time_utc >= end_time` to be safe).

### Component 2: `scripts/walk_forward_backtest.py`

**Purpose:** For each 15m round in the historical window, simulate what the live bot would have done (state → rule lookup → counterfactual trade), and aggregate into fold-level metrics.

**Data flow:**

```
load CSV → index candles by open_time_utc
load config/btc_updown_state_rules_15m.json → build ProbabilityRule index
for each fold (see methodology):
    for each 15m round in test window (aligned to start_ts):
        build BinanceState (closed candles with open_time < round.start_ts)
        build MarketMetadata (synthetic, 15m duration, UP/DOWN tokens dummy)
        state = build_round_state(binance, market, now_utc=round.start_ts + 1s)
        lookup = rule_index.lookup(state)
        if not lookup or not lookup.usable_signal: skip
        if lookup.samples < 60: skip
        if Decimal(lookup.historical_probability) < Decimal("0.60"): skip
        if not lookup.return_aligned: skip
        if not in_trading_window(state.stage, state.seconds_to_expiry): skip
        entry_price = max_buy_price = prob - safety_buffer
        if entry_price > MAX_ENTRY_ASK (0.80): skip
        if duplicate_position_on_same_round (always false in batch backtest): skip
        record counterfactual trade: {round.start_ts, slug, fold_id, side, state, lookup, entry_price}
        settle at round.end_ts: round_close = candle.open_time_utc == round.end_ts - 5min, actually use c2.close if present
            up_won = (round_close > round_open)
            won = (recommended_side == UP) == up_won
            pnl = (1 - entry_price) if won else -entry_price
```

**Single-trade-per-round invariant:** for each round with `start_ts = T`, the backtest scans both `AFTER_5M` (at `now = T + 1s`) and `AFTER_10M` (at `now = T + 5m + 1s`) states. If a trade is recorded in either, the round is marked as traded and the other state is skipped — matching live's `MAX_OPEN_POSITIONS=1` and `duplicate_position_on_market` risk gate. (In practice, AFTER_10M is the more common case because c1 must close first; the AFTER_5M trade, if any, is the only one taken.) We unit-test this.

**No-lookahead invariant:** for each round with `start_ts = T`, the binance state uses only candles with `open_time_utc < T` (matching live's `fetch_recent_5m_klines` semantics that drop the in-flight candle). We unit-test this.

**CLI:**
- `--data` (path to CSV)
- `--rules` (path to JSON)
- `--out-dir` (default: `results/`)
- `--folds` (default: 5)
- `--train-days` (default: 90)
- `--test-days` (default: 30)
- `--position-usd` (default: 1.0, matches live `MAX_POSITION_USD`)
- `--safety-buffer` (default: 0.05)
- `--min-samples` (default: 60)
- `--min-prob` (default: 0.60)
- `--max-entry-ask` (default: 0.80)

**Output per fold:**
- `results/wf_fold_<i>_trades.csv`: one row per counterfactual trade (ts, slug, fold_id, side, state fields, entry_price, won, pnl, rule_id, recommended_side, historical_prob, samples, distance_bucket, vol_bucket, pattern)
- `results/wf_fold_<i>_summary.json`:
  ```json
  {
    "fold_id": 0,
    "train_start": "...", "train_end": "...",
    "test_start": "...", "test_end": "...",
    "n_rounds": 4123,
    "n_trades": 87,
    "wr": 0.586,
    "pnl": -3.42,
    "avg_pnl": -0.0393,
    "avg_entry": 0.612,
    "max_drawdown": 12.4,
    "n_by_stage": {"AFTER_5M": 12, "AFTER_10M": 75},
    "n_by_side": {"UP": 50, "DOWN": 37}
  }
  ```
- `results/wf_aggregate_summary.json`: per-fold metrics + cross-fold mean/stdev/min/max WR and PnL

### Component 3: `scripts/breakeven_analysis.py`

**Purpose:** From all counterfactual trades across folds, compute breakeven sensitivity and rule rankings.

**Computations:**

1. **Breakeven sensitivity table** — `results/breakeven_sensitivity.csv`:
   - Rows: avg_entry_price bins from 0.30 to 0.80 step 0.05
   - Columns: n_trades, win_rate, pnl, avg_pnl, breakeven_wr (= avg_entry_price, since payout=1, cost=entry), wr_minus_breakeven, pnl_if_wr_was_breakeven
   - One section per side (UP, DOWN, all)
2. **Rule performance ranked** — `results/rule_performance_ranked.csv`:
   - Group by `rule_id`, count trades, WR, PnL, avg PnL
   - Sorted by PnL desc; include min_trades=2 filter
   - Top 20 winners and bottom 20 losers printed to stdout
3. **Regime breakdown** — printed to stdout (not a file, since it goes into the report):
   - WR by `volatility_bucket` × `side`
   - WR by `distance_bucket` × `side`
   - WR by `pattern_combo` (top 30 combos by frequency)
   - WR by `hour_of_day` (UTC)
   - WR by `day_of_week` (UTC)
4. **Counterfactual filter simulations** — printed to stdout:
   - "What if we trade only rules with avg_historical_prob ≥ X?" for X in {0.55, 0.60, 0.65, 0.70, 0.75}
   - "What if we trade only UP rules?" / "only DOWN rules?"
   - "What if we restrict to volatility_bucket ∈ {LOW, NORMAL}?"

**CLI:**
- `--trades-glob` (default: `results/wf_fold_*_trades.csv`)
- `--out-dir` (default: `results/`)

### Component 4: `scripts/walk_forward_report.py`

**Purpose:** Compose a single markdown report from the JSON/CSV outputs.

**Output:** `docs/analysis/2026-06-15-walk-forward.md` with sections:
1. **TL;DR** — one paragraph summary: "Strategy is +EV / −EV / neutral on out-of-sample. Specific numbers."
2. **Setup** — data range, fold definitions, sample counts
3. **Per-fold results** — table with WR, PnL, n_trades, avg_entry per fold
4. **Stability** — σ(WR), σ(PnL) across folds, max drawdown
5. **Breakeven analysis** — sensitivity table, regime breakdowns
6. **Rule rankings** — top 10 ± rules
7. **Live cross-check** — comparison of last fold (overlap with live) to actual live settlements
8. **Findings & recommendations** — bulleted, each tied to a specific finding
9. **Appendix** — methodology, data sources, assumptions, limitations

No charts (markdown only). Tables use GitHub-flavored markdown.

**CLI:**
- `--in-dir` (default: `results/`)
- `--out` (default: `docs/analysis/2026-06-15-walk-forward.md`)
- `--data-start`, `--data-end` (informational, for header)

### Tests: `tests/test_walk_forward.py`

Unit tests for the pipeline:
- `test_no_lookahead`: for a synthetic round, verify that `build_round_state` is called only with candles whose `open_time_utc < round.start_ts`
- `test_settlement_correctness`: round that closes up at +0.5% with `recommended_side=UP` and entry=0.55 → won=True, pnl=+0.45
- `test_single_trade_per_round`: if both AFTER_5M and AFTER_10M states pass filters, only one trade is recorded per round
- `test_trade_filter_matches_live`: replicate a known live decision (a hardcoded synthetic fixture) and confirm the backtest filter accepts/rejects it identically
- `test_fold_partition`: verify that folds are non-overlapping and cover the full date range
- `test_csv_schema`: each output CSV has the expected columns

Pytest fixtures load a small canned dataset (5 days of synthetic candles) so tests run in <5s.

## Methodology

### Walk-Forward Folds

With 500 days of data, the design uses **5 rolling folds** with `train_days=90, test_days=30`, then a final "overlap" fold that aligns with the live period (2026-06-04 to 2026-06-15).

```
Fold 0: train 2024-12-12..2025-03-12, test 2025-03-12..2025-04-11
Fold 1: train 2024-12-12..2025-04-11, test 2025-04-11..2025-05-11
Fold 2: train 2024-12-12..2025-05-11, test 2025-05-11..2025-06-10
Fold 3: train 2024-12-12..2025-06-10, test 2025-06-10..2025-07-10
Fold 4: train 2024-12-12..2025-07-10, test 2025-07-10..2025-08-09
Fold 5 (overlap): train 2024-12-12..2025-08-09, test 2026-06-04..2026-06-15
```

(Dates are illustrative; actual dates depend on `end_time = now` and downloaded range.)

Folds 0-4 are pure out-of-sample. Fold 5 overlaps with live — we compare its counterfactual PnL to the 9 days of live settlements (PnL −$3.72 on 7 settled trades as of 2026-06-15 14:04 UTC). **If the backtested PnL in fold 5 matches the live PnL within ±20%, we have a faithful model.** This is a sanity check, not a hard gate.

Why 5 folds × 30 days: gives 150 out-of-sample days, ~8640 15m rounds total, expected ~1500-3000 counterfactual trades based on live rate. This is enough to detect a 5% WR shift with reasonable confidence (binomial std error at WR=0.60 with n=300 is ~2.8%).

### Per-Round Logic

For each 15m round with `start_ts = T`:
1. Collect closed candles with `open_time_utc ≤ T` (capped at most recent 200 for memory).
2. Build a synthetic `MarketMetadata` with `start_ts=T, end_ts=T+15m, accepting_orders=True, closed=False`.
3. Build `BinanceState(candles=..., current_price=last_closed_candle.close, received_at_utc=T)`.
4. Call `state = build_round_state(binance, market, now_utc=T)`. This produces a `RoundState` with `c0`/`c1`/`c2` (where `c2` is the candle that closes at `end_ts`).
5. Call `lookup = probability_rules.lookup(state)`.
6. Apply live's filter chain in the same order as `signal_engine.py`:
   - `lookup is None` → skip (`no_rule`)
   - `not lookup.usable_signal` → skip (`rule_filtered`)
   - `lookup.samples < MIN_SAMPLES` → skip
   - `Decimal(lookup.historical_probability) < MIN_HISTORICAL_PROBABILITY` → skip
   - `not lookup.return_aligned` → skip
   - `not _in_trading_window(state.stage, state.seconds_to_expiry)` → skip
7. If passes: compute `entry_price = Decimal(lookup.historical_probability) - SAFETY_BUFFER`. If `entry_price > MAX_ENTRY_ASK` → skip.
8. Record trade.
9. At `now_utc = T + 15m`, settlement uses the close of the third 5m candle (`c2` in the state), which closes exactly at `end_ts`. `up_won = c2.close > c0.open`; `won = (recommended_side == UP) == up_won`; `pnl = (1 - entry_price) if won else -entry_price`.

This mirrors `signal_engine.py` and `settlement.py` semantics. We **do not** model:
- Spread (the ask/bid midpoint is unknown historically)
- Slippage
- Liquidity constraints
- Daily loss cap (we measure per-fold, not running PnL)
- Duplicate position on same round (irrelevant in batch backtest; only 1 position per round by construction)

## Assumptions and Limitations

**Assumptions:**
1. The historical Binance 5m klines are accurate and complete.
2. Live rule-lookup and state-building behavior is fully determined by the input state tuple and the JSON rules file. (We are not modeling code execution paths or process state.)
3. The 2,014 rules in `config/btc_updown_state_rules_15m.json` are the rules the live bot is using. (Verified: server HEAD `ce3d10f` has no rule changes since the file was last regenerated.)
4. Synthetic `MarketMetadata` (UP/DOWN token ids) does not affect state construction, only settlement lookup. (Verified: `build_round_state` does not use `up_token_id`/`down_token_id`.)

**Limitations (acknowledged in the report):**
1. **No orderbook modeling.** We use `entry_price = prob - safety_buffer`, which is the *intended* price. Real entry is `best_ask`, which can be worse. This makes the backtest **optimistic** by some unknown amount (probably 1-3% WR).
2. **No spread cost.** Payout is fixed at 1.0 per share by Polymarket, but the actual exit at settlement is 1.0 if won, 0.0 if lost — so spread is captured at entry only. Our `entry_price` should reflect this; we use the live formula.
3. **No daily loss cap.** Live caps at $10/day. In a 30-day fold, a single bad day would stop live trading. Our backtest counts trades that would have happened.
4. **No live time-of-day gating.** Live is continuous 24/7, so this is moot.
5. **Round start alignment is strict UTC.** Polymarket rounds start on the quarter-hour (00, 15, 30, 45). We align to that. If a live round has a slightly different start (e.g. 1781526600 instead of 1781527500), it would fall into a different test window. We use the same slug-derivation formula as `url_parser.current_expected_slug()`.
6. **Volatility regime requires 16 prior completed 15m rounds.** For the first 4 hours of fold 0, vol is `None` → `VOL_UNKNOWN`. These rounds will skip via no-rule-match (no rule has `VOL_UNKNOWN` bucket). We accept this as a minor coverage loss.

## Outputs

```
data/btc_5m_<N>d.csv                    # raw historical
data/btc_5m_<N>d.csv.meta.json          # metadata + checksum
results/wf_fold_0_trades.csv            # counterfactual trades
results/wf_fold_0_summary.json
results/wf_fold_1_trades.csv
results/wf_fold_1_summary.json
... (5 folds)
results/wf_aggregate_summary.json       # cross-fold stats
results/breakeven_sensitivity.csv       # WR × entry_price heatmap
results/rule_performance_ranked.csv     # per-rule perf
docs/analysis/2026-06-15-walk-forward.md   # main report
```

**Total artifact size estimate:** 500 days × 288 candles/day ≈ 144k rows × 8 fields ≈ 8 MB CSV. Trade CSVs: ~5000 rows × 20 cols ≈ 1 MB total. Report: <100 KB.

## Acceptance Criteria

The analysis is "done" when **all** of the following hold:

1. **Data:** `data/btc_5m_<N>d.csv` exists with N ≥ 365 days, ≤ 1% missing candles, sha256 logged.
2. **Folds:** All 5 folds (or 6 with overlap) processed; each fold CSV has > 0 trades and < 100% of rounds (i.e. filters are working).
3. **Tests:** `pytest tests/test_walk_forward.py` passes; specifically the no-lookahead test and the settlement-correctness test.
4. **Cross-check:** Fold 5 (overlap with live) backtested PnL is within ±50% of live PnL on the same period. (Loose threshold because of small live sample.)
5. **Breakeven table:** `results/breakeven_sensitivity.csv` covers entry_price ∈ [0.30, 0.80] in 0.05 steps; each cell has finite n_trades and a clearly labeled breakeven_WR.
6. **Rule ranking:** `results/rule_performance_ranked.csv` lists all rules that fired ≥ 2 times across all folds, sorted by PnL.
7. **Regime breakdown:** stdout output includes WR for each volatility bucket, each distance bucket, and a top-30 pattern_combo. At least 3 distinct values appear (i.e. data is not all one bucket).
8. **Report:** `docs/analysis/2026-06-15-walk-forward.md` is generated, includes TL;DR, all required sections, and ends with a "Findings & recommendations" section with ≥ 3 bullet points.
9. **Git:** Spec, plan, scripts, tests, and report are committed to `feature/walk-forward-analysis` branch in the new worktree. Report commit is signed with a clear message.

The analysis is **successful** (provides actionable signal) if it answers the 4 questions in *Goal* with concrete numbers. The analysis is **unsuccessful** (informative but inconclusive) if the cross-check fails or trade counts are too low (< 200 across all folds).

## Out of Scope (Deferred)

The following are explicitly deferred to a follow-up iteration:

1. **Підхід 2: retrain rules per fold.** Generate a fresh rules CSV from the train window of each fold, then test on the test window. This requires porting `poly_bot_system/polymarket_round_research_v2.py` (a separate research project) into our repo or making it importable. It would let us measure "is the rule-generation process stable?" separately from "are the specific 2,014 rules stable?". Estimated effort: 1-2 days.
2. **Slippage/spread modeling.** Would require pulling historical Polymarket CLOB orderbook data, which is not available historically in a convenient form. Out of scope unless we find a workable source.
3. **Position sizing optimization.** Currently fixed at $1; could try Kelly-fraction or volatility-targeted sizing. Not the bottleneck — even at 5× size the strategy loses proportionally.
4. **Side bias investigation.** Already covered partially in regime breakdown; a deeper dive into why DOWN is worse (−$0.33/settled vs −$0.11 for UP) is a separate analysis.
5. **State-construction fix.** If the cross-check fails by a large margin, the audit's volatility source / distance bucket mismatches are likely material. Fixing those is a code change and is deferred.
6. **Live deployment of any findings.** Even if the report shows rules X, Y, Z are profitable and should be kept while others should be cut, deploying that whitelist is a separate, conservative change with its own spec and review.

## Open Questions for User

Resolved in user reply (2026-06-15):
- Q1: How many days to download? → **Maximum available, capped at 500 days for safety**
- Q2: Do Approach 2 (retrain) now? → **After main stage is done**
- Q3: New branch? → **Yes, `feature/walk-forward-analysis`**

## Start Here

For implementation, the recommended order is:
1. `scripts/fetch_binance_history.py` — get the data first, since everything depends on it.
2. `scripts/walk_forward_backtest.py` + `tests/test_walk_forward.py` (TDD) — the core engine.
3. Run on 30 days of data as a smoke test before doing the full 500 days.
4. `scripts/breakeven_analysis.py` — the analysis.
5. `scripts/walk_forward_report.py` — the deliverable.
6. Commit, push branch, hand off to user for review.
