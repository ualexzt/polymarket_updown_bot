# Profitability Parity + Rule Whitelist Design

**Date:** 2026-06-12
**Project:** `polymarket_updown_bot`
**Status:** Draft for user review

## Context

The paper bot is stable operationally but not profitable enough to justify live trading or size increases. The latest server checkpoint at 2026-06-12 13:57 UTC showed:

- All-time settlements: 193 trades, 115W/78L, realized PnL -$46.17.
- Since 2026-06-08 17:41 UTC: 131 trades, realized PnL -$14.64.
- DOWN trades remain materially worse than UP trades over the full run.
- There is no evidence of a simple UP/DOWN sign inversion.
- The most important research/live mismatch is volatility bucketing:
  - Research rules used previous 16 completed 15m round absolute returns and research quantile thresholds.
  - Live code currently uses recent 5m close-to-close volatility with hard-coded thresholds.

This mismatch means the live bot can select a state-bucket rule that does not correspond to the research state used to generate the rule probability.

## Goal

Make the paper strategy evidence-driven and improve expected value by first restoring research/live state parity, then trading only a validated whitelist of profitable rules instead of every nominally strong rule.

## Non-goals

- No live trading changes.
- No increase to `MAX_POSITION_USD`.
- No martingale, averaging, DCA, or multi-position behavior.
- No broad refactor of unrelated runner, storage, or reporting code.
- No ad-hoc threshold tuning without an evaluation report.

## Proposed Approach

Use a staged approach:

1. Fix research/live parity for state construction.
2. Add an evaluation harness that scores rules against recorded decisions and settlements.
3. Generate a static whitelist/quarantine config from evidence.
4. Enforce whitelist and side-specific price gates in the signal path.
5. Deploy in paper mode and compare against the current baseline.

This is deliberately conservative: it prioritizes explaining why a rule should trade before allowing it to trade.

## Architecture

### 1. Research-Compatible State Construction

`round_state.py` will compute the live state tuple in the same way as the research script that generated `config/btc_updown_state_rules_15m.json`.

Changes:

- Volatility source becomes previous completed 15m rounds, not recent 5m close-to-close returns.
- Volatility buckets use the same thresholds as the research output, stored explicitly in config or in the generated rules metadata.
- Distance buckets use reference-compatible boundary behavior.
- Near-open states are mapped in a reference-compatible way so live does not produce untradeable `AT_OPEN` tuples for 15m rules that never existed in research.

Success criterion: for a shared candle fixture, the live tuple `(stage, current_side, distance_bucket, volatility_bucket, pattern)` matches the reference research tuple.

### 2. Evaluation Harness

Add a script that evaluates rules on historical paper data before we change trading behavior.

The script will read the SQLite DB and produce grouped performance tables by:

- `rule_id`
- selected side
- stage
- entry price bucket
- edge bucket
- recent time window

For each group it will report:

- trade count
- win count and win rate
- realized PnL
- average entry price
- breakeven win rate implied by entry price
- average historical probability
- average edge
- whether the sample is large enough to trust

The same script should support two views:

1. Actual paper trades from `settlements`.
2. Candidate/shadow decisions from `decisions` where enough data exists to estimate hypothetical outcome and entry price.

Success criterion: we can identify rules that are repeatedly negative and rules that remain positive after costs/ask price.

### 3. Rule Whitelist and Quarantine

Trading should require a rule to be explicitly allowed.

A new JSON config will define:

- allowed `rule_id`s
- optional side-specific overrides
- optional max entry ask by rule
- optional minimum edge by rule
- quarantined rules with reasons

Default behavior after this change:

- If whitelist mode is enabled and a rule is not whitelisted, the bot skips with a clear reason.
- If a rule is quarantined, the bot skips even if it otherwise passes probability and price gates.
- DOWN rules are not globally trusted; they must pass the same whitelist evidence as UP rules and can have stricter thresholds.

This avoids overreacting with a broad rule like “disable all DOWN” while still allowing us to block bad repeated DOWN rules.

### 4. Side-Specific and Rule-Specific Price Gates

Keep the existing global gates, but allow stricter overrides:

- `MIN_EDGE_UP`
- `MIN_EDGE_DOWN`
- `MAX_ENTRY_ASK_UP`
- `MAX_ENTRY_ASK_DOWN`
- per-rule `min_edge`
- per-rule `max_entry_ask`

The signal engine will apply the strictest applicable gate:

1. global default
2. side-specific override
3. rule-specific override

Success criterion: a rule cannot trade just because its historical probability is high; it must also have a live entry price that gives a plausible positive expected value.

### 5. Reporting and Deployment

Reports should make the new gates visible:

- whitelist allowed/blocked counts
- quarantine blocked counts
- side-specific price gate blocks
- per-rule PnL summary
- baseline-vs-current window comparison

Deployment remains PAPER-only:

1. Deploy parity + evaluation script first.
2. Run a report against the existing DB.
3. Generate the first whitelist from evidence.
4. Enable whitelist in paper mode at `$1` size.
5. Observe for 24-48 hours before any further changes.

## Data Flow

Runtime decision flow after the change:

1. Runner discovers the current market.
2. Binance candles are fetched.
3. `round_state.py` builds a research-compatible state tuple.
4. Probability rules lookup returns a candidate rule.
5. Signal engine checks normal rule filters.
6. Signal engine checks whitelist/quarantine.
7. Signal engine applies global, side-specific, and rule-specific price gates.
8. Paper broker opens a position only if every gate passes.
9. Storage persists every TRADE and SKIP reason for later evaluation.

## Error Handling

- Missing whitelist file with whitelist mode disabled: continue normally.
- Missing whitelist file with whitelist mode enabled: fail startup or skip all trades with an explicit configuration error.
- Malformed whitelist: fail fast at startup rather than trade with partial rules.
- Rule override with unknown `rule_id`: report in validation output and fail startup.
- Candidate evaluation gaps: mark rows as non-evaluable rather than silently excluding them.

## Testing Strategy

Tests should be added before implementation changes.

Required tests:

1. Research parity fixtures for volatility bucket, distance bucket, current side, stage, and pattern.
2. Whitelist loader accepts valid config and rejects malformed config.
3. Signal engine skips non-whitelisted rules when whitelist mode is enabled.
4. Signal engine skips quarantined rules even if normal gates pass.
5. Side-specific gates override global gates.
6. Rule-specific gates override side/global gates when stricter.
7. Evaluation script computes PnL and breakeven metrics correctly on a tiny SQLite fixture.
8. Existing full test suite remains green with default whitelist mode disabled.

## Rollout Plan

1. Implement parity fixes and tests.
2. Implement evaluation script and tests.
3. Run evaluation against copied live DB.
4. Create initial whitelist config from evaluation evidence.
5. Implement whitelist enforcement and tests.
6. Deploy to server in PAPER mode.
7. Monitor 12h, 24h, and 48h performance windows.
8. Only consider further size or live-trading discussion if paper metrics improve with stable behavior.

## Success Criteria

The work is successful if:

- Live state construction matches reference research fixtures.
- Rule evaluation can explain historical paper losses by rule/side/price bucket.
- The bot can run with whitelist mode enabled without crashes.
- Every blocked trade has an auditable skip reason.
- Paper performance after deployment is measured against the current baseline before any further risk increase.

## Open Decision for User Review

The recommended first whitelist policy is conservative:

- Allow only rules with positive realized paper PnL or strong research probability plus no negative live evidence.
- Quarantine rules with repeated negative live PnL.
- Keep DOWN enabled only through explicit whitelist entries, not globally.

If faster risk reduction is preferred, we can start by disabling all DOWN rules until the evaluator proves specific DOWN rules deserve inclusion.
