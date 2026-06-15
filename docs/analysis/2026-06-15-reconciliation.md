# Reconciliation Report

**Generated**: 2026-06-15

## TL;DR

**Verdict: C — Settlement timing or tie handling divergence.**

Settlement fields (won, pnl) differ in 401 of 698 mismatches. Likely settlement timing or tie handling diverges. Recommended next step: compare `settlement.py` against the live settlement path.

## Setup

- **Live DB**: snapshot from server at reconciliation time
- **Backtest**: same live period (2026-06-06 → 2026-06-15), replayed through `walk_forward_backtest.py` with live rules from `config/btc_updown_state_rules_15m.json`
- **Match key**: `market_slug`
- **Field comparison**: state (5 fields), price (2 fields), settlement (3 fields)

## Matched pairs analysis

See `results/recon/matched_pairs.csv` for the full side-by-side comparison.

| Metric | Value |
|---|---|
| Verdict | **C** |
| Matched pairs | 187 |
| Live-only | 69 |
| Backtest-only | 224 |
| State mismatches | 89 |
| Price mismatches | 208 |
| Settlement mismatches | 401 |
| Total mismatches | 698 |


## Unmatched analysis

- **Live-only trades** (no backtest match): 69. See `results/recon/live_only_trades.csv`.
- **Backtest-only trades** (no live match): 224. See `results/recon/backtest_only_trades.csv`.

## Verdict & recommendation

**C — Settlement timing or tie handling divergence**

Settlement fields (won, pnl) differ in 401 of 698 mismatches. Likely settlement timing or tie handling diverges. Recommended next step: compare `settlement.py` against the live settlement path.

## Appendix

**Methodology**:
- Live DB snapshot: scp from server, query settlements + paper_positions tables.
- Backtest: `walk_forward_backtest.py` re-run on the live period with explicit `--test-start` / `--test-end` flags (added in this iteration).
- Match by `market_slug`. 1:1 match expected (MAX_OPEN_POSITIONS=1 invariant).
- Field comparison with entry tolerance = 0.01. State fields use exact string match.
- Verdict logic in `categorize_verdict()`: A (filter) / B (state) / C (settlement) / D (insufficient data).

**Limitations**:
- Small live sample (only 7-9 days, 256 settlements). Statistical confidence is low.
- The match depends on `market_slug` being identical between live and backtest. If live slugs differ from backtest slugs (e.g., different slug generation logic), matches would be missed.
- `backtest-only` trades are computed from a backtest that has no concept of `MAX_OPEN_POSITIONS` over time (it processes one round at a time), so a backtest trade may have a live counterpart that was rejected by the risk manager on a different round that day.
