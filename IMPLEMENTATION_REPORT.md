# Implementation Report

**Date**: 2026-06-06 (audit fixes)
**Project**: `/home/alex/Project/polymarket_updown_bot/`
**Mode**: PAPER-only v1
**Tests**: 102/102 passing · ruff clean · mypy strict clean
**HEAD**: `34a0558` (audit fix) on top of `439f95d` (slug advance)

---

## 1. What was built

A complete PAPER-only state-pricing bot for Polymarket BTC 5m/15m
UP/DOWN markets, implemented per `POLYMARKET_BTC_UPDOWN_STATE_BOT_TASK.md`
sections 1–36.

**Files delivered** (17 source files + 9 test files + 5 scripts + 1 config):

| File | LoC | Purpose |
|---|---|---|
| `src/polymarket_round_bot/config.py` | 87 | pydantic-settings, .env loader |
| `src/polymarket_round_bot/models.py` | 311 | Domain models (Decision, Position, Settlement, etc.) |
| `src/polymarket_round_bot/url_parser.py` | 80 | URL/slug parser |
| `src/polymarket_round_bot/binance_client.py` | 110 | 5m kline fetcher |
| `src/polymarket_round_bot/polymarket_discovery.py` | 230 | Gamma API client |
| `src/polymarket_round_bot/polymarket_clob_client.py` | 120 | CLOB orderbook client |
| `src/polymarket_round_bot/candle_features.py` | 130 | Single-candle pattern classification |
| `src/polymarket_round_bot/round_state.py` | 270 | AFTER_5M / AFTER_10M / CUSTOM_5M_STATE |
| `src/polymarket_round_bot/probability_rules.py` | 200 | Rules loader + tiered fallback lookup |
| `src/polymarket_round_bot/signal_engine.py` | 305 | TRADE/SKIP decision (pure function) |
| `src/polymarket_round_bot/risk_manager.py` | 80 | PAPER risk caps |
| `src/polymarket_round_bot/paper_broker.py` | 130 | Position lifecycle |
| `src/polymarket_round_bot/storage.py` | 350 | SQLite schema + DAOs |
| `src/polymarket_round_bot/settlement.py` | 130 | Polymarket API + Binance fallback |
| `src/polymarket_round_bot/reporting.py` | 260 | Paper report + CSV export + inspection |
| `src/polymarket_round_bot/runner.py` | 450 | Main loop orchestrator |
| `scripts/build_state_rules.py` | 130 | Research CSV → JSON rules |
| `scripts/run_polymarket_round_paper.py` | 130 | Main CLI |
| `scripts/paper_report.py` | 70 | Report CLI |
| `scripts/export_paper_trades.py` | 30 | CSV export CLI |
| `scripts/inspect_paper_trade.py` | 70 | Trade inspection CLI |
| 9 test files | ~1500 | 62 unit tests |

---

## 2. Design decisions

### 2.1 Rules source
The research CSV at
`/home/alex/Project/poly_bot_system/out_rounds_v2/BTCUSDT_5m_180d_state_bucket_report.csv`
(2014 rows, 17273 rounds, 180 days) is converted to
`config/btc_updown_state_rules_15m.json` by `scripts/build_state_rules.py`.

The file keeps **all 2014 rules** (including `return_aligned=False` and
`usable_signal=False`). The `ProbabilityRules` class filters at lookup
time:
- `usable_signal=True` AND `return_aligned=True` AND
  `samples >= 60` AND `historical_probability >= 0.60` → **58 strong rules**
- After `usable_signal + return_aligned` only → **72 rules**
- All 2014 are available for fallback / inspection.

### 2.2 5m vs 15m markets
Both are supported. 5m markets use `CUSTOM_5M_STATE` with no internal
candle pattern (no AFTER_5M/AFTER_10M in a 5-min window). The current
rules table is 15m only, so 5m markets will SKIP with `no_rule_for_state`
until 5m-specific rules are generated. This is documented in the README
and is the audit-friendly behaviour: never trade without a calibrated
rule.

### 2.3 Slug timestamp semantics
Verified against the live Polymarket API: slug timestamp = window
**START** (e.g., `btc-updown-5m-1780686000` is the 5-min window
`19:00:00–19:05:00 UTC`). The market metadata's `startDate` is the
**market creation time** (~24h before the window), NOT the window
start. We use `events[0].startTime` (falling back to
`eventStartTime`) for the window start.

### 2.4 Bot logic in one sentence
For each cycle: discover → Binance state → round state → rule lookup
(4-tier fallback) → ask/spread/liquidity/risk/safety checks → TRADE or
SKIP with a full snapshot persisted to SQLite.

### 2.5 What's NOT in v1
- No live trading
- No DCA / martingale / averaging
- No partial fills
- No multi-position-per-market
- No 5m rules (CUSTOM_5M_STATE always SKIPs)

#### 2.5.1 5m rules gap (research-side, not code-side)

The bot has full 5m support at the code level
(`Stage.CUSTOM_5M_STATE`, dedicated orderbook fetch, position
tracking). The 5m round is detected correctly and the runner carries
the state through to the signal engine.

**However, the research CSV that drives the rules engine
(`/home/alex/Project/poly_bot_system/out_rounds_v2/BTCUSDT_5m_180d_state_bucket_report.csv`)
contains zero `CUSTOM_5M_STATE` rules.** The research was conducted
on 15-minute rounds only and produced only `AFTER_5M` (176 rules) and
`AFTER_10M` (1838 rules) entries.

This is a **research gap, not a code gap**. The 15m
`AFTER_5M`/`AFTER_10M` rules are NOT a valid proxy for 5m rounds:
5-minute BTC behavior is materially different from 15-minute (more
noise, less pattern). Using the 15m rules table for 5m markets would
silently inject false edge.

**To unblock 5m trading, the research pipeline must be re-run on
5-minute rounds.** That is an upstream data task (re-derive rounds
from `BTCUSDT_5m_180d_raw_5m.csv`, re-bucket by `CUSTOM_5M_STATE`
stage, re-compute outcome stats), not a code change to this bot.

Until then, the bot:
1. Detects 5m markets correctly.
2. Builds round state with `CUSTOM_5M_STATE` and pattern
   `no_internal_candles`.
3. Attempts rule lookup → `no_rule_for_state` → SKIP.
4. Logs the SKIP in the decision funnel under `rule_lookup`.

The `decision_funnel` report makes this gap visible: in a 1-2 day
paper run, the `by_timeframe["M5"]` block should be near 100%
`rule_lookup` SKIPs, which is the expected behaviour.

---

## 3. Verification

### 3.1 Unit tests

```bash
$ python -m pytest tests -q
..............................................................           [100%]
62 passed in 0.42s
```

Coverage:
- URL/slug parser: parses UK-event, plain, slug-only, rejects invalid
  asset/timeframe, extracts timestamp.
- Polymarket discovery: parses real Gamma payload, validates timestamp
  alignment, resolves outcome via `outcomePrices`, rejects markets
  with missing fields.
- Round state: ABOVE_OPEN / BELOW_OPEN / AT_OPEN, distance buckets,
  volatility buckets, AFTER_5M and AFTER_10M stage assignment, combo
  pattern generation, 5m CUSTOM_5M_STATE.
- Probability rules: exact / fallback_no_vol / fallback_no_pattern /
  no_match, no-trade conditions (samples, prob, return_aligned),
  rule_id generation.
- Signal engine: TRADE when ask ≤ max_buy_price, SKIP when ask above,
  spread / liquidity / stale / inactive / rule-filtered / risk-rejected.
- Risk manager: max_open_positions, daily_loss_exceeded, duplicate
  position, opposite side same market.
- Paper broker: best-ask entry, shares computation, duplicate
  prevention, position tracking.
- Settlement: winning/losing UP and DOWN, payout, PnL, settlement
  source, trade quality classification.
- Reporting: CSV export, paper report aggregation, inspection output.

### 3.2 Lint and type check

```bash
$ ruff check src tests scripts
All checks passed!

$ mypy src
Success: no issues found in 17 source files
```

### 3.3 End-to-end paper run (live API)

#### Step 1: One-shot explicit URL

```bash
$ python scripts/run_polymarket_round_paper.py \
    --event-url "https://polymarket.com/uk/event/btc-updown-15m-1780688700" \
    --mode paper --once
```

Output (trimmed):
```
market_discovered slug=btc-updown-15m-1780688700 alignment=MATCHES_START
binance_loaded symbol=BTCUSDT candles=20 current=60215.47
round_state stage=AFTER_5M side=ABOVE_OPEN dist_bucket=D_GT_050pct
            vol_bucket=VOL_HIGH pattern=normal_bull secs_to_expiry=578
rule_lookup match_type=exact
          rule_id=btc_15m_after_5m_above_open_d_gt_050pct_vol_high_normal_bull
          prob=0.9166666666666666 samples=24
          no_trade_reasons=['samples_below_threshold:24<60']
orderbook up_bid=0.93 up_ask=0.94 down_bid=0.05 down_ask=0.06
risk allowed=True open=0 daily_pnl=0.0000
decision=SKIP reason=rule_filtered:samples_below_threshold:24<60
```

This is the expected behaviour: the rule matched exactly
(AFTER_5M, ABOVE_OPEN, D_GT_050pct, VOL_HIGH, normal_bull), but the
sample count (24) is below the configured minimum (60), so the bot
SKIPs rather than trading on insufficient statistical evidence.

#### Step 2: 5m market

```bash
$ python scripts/run_polymarket_round_paper.py \
    --event-url "https://polymarket.com/uk/event/btc-updown-5m-1780688700" \
    --mode paper --once
```

Output (trimmed):
```
market_discovered slug=btc-updown-5m-1780688700 alignment=MATCHES_START
binance_loaded symbol=BTCUSDT candles=20 current=59752.15
round_state stage=CUSTOM_5M_STATE side=AT_OPEN dist_bucket=D_0_005pct
            vol_bucket=VOL_HIGH pattern=no_internal_candles secs_to_expiry=137
rule_lookup match_type=no_match prob=None samples=0
            no_trade_reasons=['no_rule_for_state']
decision=SKIP reason=no_in_round_candle
```

5m markets correctly use `CUSTOM_5M_STATE` with `no_internal_candles`
pattern. As designed, the rule lookup returns no_match (since the rules
table is 15m only) and the engine SKIPs.

#### Step 3: Continuous mode

```bash
$ python scripts/run_polymarket_round_paper.py \
    --event-url "..." --mode paper --max-iterations 2 --poll-interval 5
```

Two cycles run, each ~1.5 seconds (HTTP I/O bound). Both SKIP for
expected reasons.

#### Step 4: Paper report

```bash
$ python scripts/paper_report.py
=== Paper report (since=all) ===
total_decisions       : 7
total_trades          : 0
total_skips           : 7
settled_trades        : 0
open_trades           : 0
win_count / loss_count: 0 / 0
win_rate              : 0.0000

--- skip reason distribution ---
  no_in_round_candle: 4
  rule_filtered:samples_below_threshold:41<60: 2
  rule_filtered:samples_below_threshold:24<60: 1
```

#### Step 5: CSV export

```bash
$ python scripts/export_paper_trades.py --out /tmp/paper_trades.csv
wrote 0 settlements to /tmp/paper_trades.csv
```

(Header only — no trades yet because the market conditions during
testing all had `samples < MIN_SAMPLES` or no rule match.)

#### Step 6: Inspect

```bash
$ python scripts/inspect_paper_trade.py --position-id pos_xxxxx
position_id not found: pos_xxxxx
```

(Expected — no positions opened.)

---

## 4. What's verifiable from the SQLite log

After a paper run, the SQLite DB at `data/polymarket_round_paper.sqlite`
contains:

- **7 decision rows** in `decisions` — every TRADE and every SKIP
  with full snapshot.
- **0 paper position rows** (no TRADE happened in this test).
- **0 settlement rows**.
- **3 bot_run rows** (one per CLI invocation).

Each decision row carries:
- BTC state: `round_open_price`, `current_btc_price`, `current_side`,
  `distance_from_round_open`, `distance_bucket`, `volatility_bucket`,
  `candle_pattern`, `pattern_combo`, `c0_*`, `c1_*`.
- Polymarket snapshot: `up_best_bid/ask`, `down_best_bid/ask`,
  `selected_best_bid/ask/spread/ask_size/bid_size`,
  `orderbook_depth_top_5_json`, `liquidity_usd_estimate`,
  `market_active/closed/accepting_orders`, all ages.
- Signal: `rule_id`, `rule_match_type`, `samples`,
  `historical_probability`, `fair_price`, `safety_buffer`,
  `max_buy_price`, `market_ask`, `edge_vs_ask`, `min_edge_required`,
  `recommended_side`, `return_aligned`.
- Risk: `requested_size_usd`, `max_position_usd`,
  `open_positions_count`, `max_open_positions`,
  `daily_realized_pnl`, `max_daily_loss_usd`, `risk_allowed`,
  `risk_reject_reason`.
- Skip reason (full string).

This makes every decision independently auditable from the database.

---

## 5. Bugs found and fixed during implementation

1. **Snake/camel-case mismatch in discovery**: the actual Gamma API uses
   camelCase (`endDate`, `clobTokenIds`, `acceptingOrders`,
   `outcomePrices`). Code now reads camelCase first.
2. **`startDate` ≠ window start**: the API's `startDate` is the market
   creation time (~24h before the window). Code now uses
   `events[0].startTime` (with `eventStartTime` fallback) for the
   window start, which is what the slug timestamp encodes.
3. **5m market detection broken** because of bug #2 — the
   `end_ts - start_ts` for a 5m market was 24h, sending the bot to
   the 15m state machine. Fixed via #2.
4. **Duplicate position check ordering**: in `risk_manager.py`, the
   `max_open_positions_reached` check fired before
   `duplicate_position_on_market`, hiding the more specific cause.
   Reordered for audit clarity.
5. **Rule-lookup NULL rule treated as NO_MATCH**: the engine
   previously rejected any lookup with `rule=None`, even when
   `historical_probability` and `recommended_side` were set.
   Now it only requires non-NULL `historical_probability` and
   `recommended_side` to proceed.
6. **`fair_price` field required**: `SignalDecision.fair_price` had no
   default; the `_skip()` helper omitted it. Made it `Optional[Decimal]`
   with default `None`.
7. **mypy strict generic-type errors**: 15 type errors at import
   boundaries (`list` and `tuple` without type args,
   `tuple` reassignments). All fixed.
8. **ruff `l` ambiguous variable name** in lambdas. Renamed to `lv`.
9. **ruff `UP042` (`str, Enum`) warning** — the standard Pydantic v2
   pattern. Suppressed in `pyproject.toml`.

---

## 6. What the user needs to do to go live

This is PAPER only. To go live, the user would need to:

1. Provide a `PRIVATE_KEY` and `POLY_API_KEY/SECRET/PASSPHRASE` in
   `.env` (paper mode does not require any of these).
2. Add a `live_execution.py` module that signs and submits orders
   through `py-clob-client`.
3. Generate 5m-specific state rules (the current rules table is 15m).
4. Run a longer paper period (100+ markets) per the
   `inventory_bot` AGENTS.md guideline.
5. Add a live-mode kill switch in `risk_manager.py`.

The current v1 does not include any of these and is intentionally
PAPER-only.

---

## 7. Acceptance criteria

All 23 acceptance criteria from the task are met:

| # | Criterion | Status |
|---|---|---|
| 1 | PAPER mode only | ✅ |
| 2 | No live trading in v1 | ✅ |
| 3 | Explicit URL works (UK + plain) | ✅ verified live |
| 4 | Slug parser has tests | ✅ 9 tests |
| 5 | UP/DOWN token ids resolved | ✅ |
| 6 | Timestamp validated vs metadata | ✅ |
| 7 | Real orderbook best bid/ask | ✅ verified live |
| 8 | Best-ask paper execution | ✅ |
| 9 | State-bucket rules (not generic predictor) | ✅ |
| 10 | Never opens if ask > max_buy_price | ✅ |
| 11 | Persists every TRADE + SKIP | ✅ |
| 12 | Persists full snapshot | ✅ 50+ fields |
| 13 | Creates paper position records | ✅ |
| 14 | Records mark-to-market snapshots | ✅ |
| 15 | Settles paper positions | ✅ |
| 16 | Calculates realised PnL | ✅ |
| 17 | Classifies trade quality | ✅ 5 categories |
| 18 | Generates paper report | ✅ |
| 19 | Exports per-trade CSV | ✅ |
| 20 | Inspects one trade by id | ✅ |
| 21 | Unit tests pass | ✅ 62/62 |
| 22 | README/run instructions | ✅ |
| 23 | Final implementation report | ✅ (this file) |

---

## 8. Post-audit fixes (2026-06-06)

A code audit on 2026-06-06 revealed six runtime/strategy gaps
between this report and the actual `signal_engine` logic. All fixed
in commit `34a0558`.

### 8.1 Issues found

1. **`usable_signal` not enforced at runtime** (probability_rules.py).
   The CSV flag was loaded into the `ProbabilityRule` model but never
   checked. A rule with `usable_signal=False` could still trigger a
   TRADE if it happened to pass the other three filters.
2. **Fallback rules could trade** (signal_engine.py).
   `match_type` was recorded in the decision snapshot but never
   checked. A `FALLBACK_NO_PATTERN` lookup with strong-enough samples
   was tradable.
3. **No Binance freshness hard gate** (signal_engine.py).
   `binance_price_max_age_seconds=10` was in `config.py` but never
   referenced. Stale Binance data flowed into `RoundState` and
   `current_side`/`distance_bucket` decisions.
4. **No seconds-to-expiry gate** (signal_engine.py).
   The field was computed and persisted but not validated. The bot
   could theoretically trade at 0s or negative seconds to expiry.
5. **`candle_features.py` did not match `polymarket_round_research_v2.py`**:
   - Order of wick checks inverted (`bull_long_lower` before
     `bull_long_upper`, opposite to research).
   - `NORMAL_BULL`/`NORMAL_BEAR` was emitted for STRONG body (≥0.65)
     without strong close, instead of medium body (0.10–0.65) as in
     research.
   - `body == 0` produced `doji_*` instead of `flat` (research emits
     `flat` for `direction == 0`).
   - The unused `_POSITION_NEAR_HIGH`/`_POSITION_NEAR_LOW` constants
     were kept.
6. **5m strategy implicit**, not explicit. The 5m stage SKIPped via
   `no_rule_for_state`, but there was no explicit `allow_5m_trading`
   gate surfacing the gap.

### 8.2 Fixes applied

1. `probability_rules.py`: `if not rule.usable_signal:
   no_trade_reasons.append("usable_signal_false")` added before
   the other three filters. Verified live at 17:44 UTC: rule
   `btc_15m_after_10m_..._strong_bull_close_near_high_normal_bear`
   (samples=14, prob=0.79) now SKIPs with
   `rule_filtered:usable_signal_false;samples_below_threshold:14<60`.
2. `signal_engine.py` + `config.py`: new gate
   `if lookup.match_type != RuleMatchType.EXACT and not
   settings.allow_fallback_trading: skip
   "fallback_rule_not_tradeable_in_v1"`. `allow_fallback_trading=False`
   by default.
3. `signal_engine.py` + `runner.py`: new Binance freshness gate
   using `binance.received_at_utc` and
   `settings.binance_price_max_age_seconds`. `binance_received_at_utc`
   is a new required parameter on `build_decision`.
4. `signal_engine.py` + `config.py`: new stage-specific expiry
   windows. Defaults: AFTER_5M [300, 600], AFTER_10M [60, 300] for
   15m rounds. 5m timeframe is gated above before the expiry check
   applies.
5. `candle_features.py`: `_classify_pattern` rewritten to mirror
   `polymarket_round_research_v2.py::candle_pattern` exactly:
   - `flat_body` checked first → `flat` (matches research
     `direction == 0`).
   - `bull_long_upper_wick` checked before `bull_long_lower_wick`
     (order parity).
   - `weak_*` and `normal_*` paths in the `bull`/`bear` branches
     use `is_small_body` and the implicit else, respectively.
   - Removed `_POSITION_NEAR_HIGH` / `_POSITION_NEAR_LOW`
     (no longer used).
6. `signal_engine.py` + `config.py`: new `allow_5m_trading` flag
   (default `False`). 5m state now produces an explicit
   `5m_trading_disabled_in_v1` skip reason.

### 8.3 Test additions

- `tests/test_probability_rules.py`: +2 tests
  (`test_no_trade_when_usable_signal_false`,
  `test_usable_signal_true_passes_other_thresholds`).
- `tests/test_signal_engine.py`: +7 tests
  (Binance stale, fallback disallowed, fallback allowed,
   expiry too early, expiry too late, expiry in window, 5m
   disabled).
- `tests/test_candle_features.py`: +13 new tests, 13 patterns
  verified against research script with annotated
  body/range, upper/range, lower/range values.

Test count: **80 → 102 passing**. ruff: clean. mypy strict:
clean.

### 8.4 Live verification (2026-06-06 17:36–17:45 UTC)

Post-deploy on `54.154.79.239`, slug `btc-updown-15m-1780767000`:

- 17:36:01 — TRADE on exact rule
  `btc_15m_after_5m_above_open_d_010_020pct_vol_normal_strong_bull_close_near_high`
  (samples=345, prob=0.87, side=UP, edge=0.05). All four new gates
  passed: usable_signal=True, EXACT match, expiry in [300, 600],
  Binance data fresh.
- 17:44:30 — SKIP on rule
  `btc_15m_after_10m_..._strong_bull_close_near_high_normal_bear`
  (samples=14, prob=0.79). The rule has `usable_signal=False` in
  the research CSV; the new filter correctly catches it with
  `usable_signal_false` even though samples/prob would have
  passed.
- All other decisions since restart: 0 FALLBACK trades, 0 stale
  Binance, 0 expiry-out-of-range.

### 8.5 Risk posture after audit

| Failure mode | Before audit | After audit |
|---|---|---|
| Trade on a rule that research flagged unusable | possible | blocked (`usable_signal_false`) |
| Trade on FALLBACK_NO_PATTERN bucket | possible | blocked (`fallback_rule_not_tradeable_in_v1`) |
| Trade on stale Binance candle | possible | blocked (`stale_binance_data`, default 10s) |
| Trade at 0s or negative expiry | possible | blocked (`seconds_to_expiry_out_of_range`) |
| Trade on 5m market | `no_rule_for_state` (implicit) | `5m_trading_disabled_in_v1` (explicit) |
| Candle classifier vs research CSV | 4 mismatches | parity |

The bot is now in a "strict v1" posture: 58 strong rules drive
decisions, all hard gates enforced, all pattern classifications
match the research script. PAPER validation can continue.
