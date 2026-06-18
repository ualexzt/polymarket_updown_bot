# Code Context

## Files Retrieved
1. `/home/alex/Project/poly_bot_system/polymarket_round_research_v2.py` (lines 202-295, 298-458, 463-540, 543-675, 985-1045) - authoritative v2 research/backtest pipeline used to produce state-bucket rules.
2. `/home/alex/Project/poly_bot_system/out_rounds_v2/BTCUSDT_5m_180d_summary.txt` (lines 1-25, 82-100, 137-147) - saved 180d research summary and example rule outputs.
3. `/home/alex/Project/poly_bot_system/out_rounds_v2/BTCUSDT_5m_180d_state_bucket_report.csv` (lines 1-8 plus scripted inspection) - source CSV for live JSON rules.
4. `/home/alex/Project/polymarket_updown_bot/scripts/build_state_rules.py` (lines 1-130) - live conversion from reference CSV to JSON rules.
5. `/home/alex/Project/polymarket_updown_bot/config/btc_updown_state_rules_15m.json` (lines 1-40 plus scripted inspection) - generated live rules config.
6. `/home/alex/Project/polymarket_updown_bot/src/polymarket_round_bot/candle_features.py` (lines 1-136) - live candle pattern classifier.
7. `/home/alex/Project/polymarket_updown_bot/src/polymarket_round_bot/round_state.py` (lines 1-240) - live stage/current-side/distance/volatility/pattern state construction.
8. `/home/alex/Project/polymarket_updown_bot/src/polymarket_round_bot/binance_client.py` (lines 49-115) - confirms live uses only closed 5m candles and current price is last closed close.
9. `/home/alex/Project/polymarket_updown_bot/src/polymarket_round_bot/models.py` (lines 34-84, 273-290) - live enum labels for stages, sides, distance buckets, volatility buckets, patterns, UP/DOWN.
10. `/home/alex/Project/polymarket_updown_bot/src/polymarket_round_bot/probability_rules.py` (lines 1-260) - live rule loading/lookup and filters.
11. `/home/alex/Project/polymarket_updown_bot/src/polymarket_round_bot/signal_engine.py` (lines 60-70, 228-247) - live recommended-side selection and fair-price/ask math.
12. `/home/alex/Project/polymarket_updown_bot/src/polymarket_round_bot/polymarket_discovery.py` (lines 132-185) - live Polymarket outcome/token mapping and resolved outcome parsing.
13. `/home/alex/Project/polymarket_updown_bot/src/polymarket_round_bot/settlement.py` (lines 26-41) - live Binance fallback UP/DOWN settlement mapping.
14. `/home/alex/Project/poly_bot_system/polymarket_round_research.py` (lines 1-40 and grep around 305-507) - older round-pattern research, conceptually same round target but not state-bucket source.
15. `/home/alex/Project/poly_bot_system/main.py` (grep around 403-499, 731) - older next-candle predictor; not the live state-rule source.

## Key Code

### Reference/backtest rule generation

- v2 is the relevant reference for live rules. It builds complete 15m rounds from 3 closed 5m candles: `c0` slot 0, `c1` slot 1, `c2` slot 2 (`polymarket_round_research_v2.py:298-318`).
- Reference target: `round_open = c0.open`, `round_close = c2.close`; `target_up = 1` if `round_close > round_open`, `0` if lower, and exact ties are skipped (`polymarket_round_research_v2.py:320-328`).
- Reference pattern ordering: `AFTER_5M` uses `c0.pattern`; `AFTER_10M` uses `"c0_pattern -> c1_pattern"` (`polymarket_round_research_v2.py:359-365`).
- Reference distance/current side: after-5m/10m distance is absolute return from `round_open`; side is `ABOVE_OPEN` if close is greater, otherwise `BELOW_OPEN` (`polymarket_round_research_v2.py:342-343, 372-380`).
- Reference distance buckets use `pd.cut` labels: `D_0_005pct`, `D_005_010pct`, `D_010_020pct`, `D_020_035pct`, `D_035_050pct`, `D_GT_050pct` with bin cutoffs 0.0005/0.0010/0.0020/0.0035/0.0050 (`polymarket_round_research_v2.py:466-497`).
- Reference volatility source is previous completed **15m round** absolute returns: `prev_16_abs_return_mean = rounds["round_abs_return"].shift(1).rolling(16).mean()`; buckets use quantiles of that series (`polymarket_round_research_v2.py:456-458, 499-514`).
- Reference probability mapping: `recommended_side = UP` when `up_rate >= 0.5`, else `DOWN`; `historical_probability` is the selected side's rate; `fair_price = historical_probability`; `max_buy_price = fair_price - safety_buffer`; `return_aligned` checks median round return sign (`polymarket_round_research_v2.py:594-635`).

Saved v2 outputs: summary says 51,840 raw 5m candles and 17,273 15m rounds for 2025-12-07 to 2026-06-05 (`out_rounds_v2/...summary.txt:1-15`). Scripted inspection found `state_bucket_report.csv` has 2,014 rows; generated live JSON also has 2,014 rules, with no numeric/enum mismatches from CSV to JSON.

### Live implementation

- Live config is generated directly from the reference state-bucket CSV (`scripts/build_state_rules.py:1-15, 83-130`). It keeps all rows, including unusable/non-aligned rows.
- Live closed-candle handling matches the reference stage concept: Binance client drops the in-flight candle and sets `current_price` to latest closed candle close (`binance_client.py:57-62, 97-107`).
- Live 15m state uses `c0` only for `AFTER_5M`, and `c0 -> c1` for `AFTER_10M` (`round_state.py:207-229`).
- Live side/orderbook mapping: signal engine uses the rule's `recommended_side` only (`signal_engine.py:60-70`) and then selects UP or DOWN orderbook by side (`signal_engine.py:228-235`); pricing uses `fair_price = historical_probability`, `max_buy_price = fair_price - safety_buffer`, `edge_vs_ask = fair_price - best_ask` (`signal_engine.py:243-247`).
- Live Polymarket outcome mapping maps outcome labels/index order to token ids (`polymarket_discovery.py:132-144`) and resolved UP/DOWN from `outcomePrices` (`polymarket_discovery.py:169-185`).

## Architecture

Reference v2 data flow:

1. Download Binance 5m klines.
2. Compute 5m candle features and pattern labels.
3. Group every complete 3-candle block into one 15m round.
4. Build two stage observations per round: `AFTER_5M` from `c0`, `AFTER_10M` from `c0 -> c1`.
5. Add current side, absolute distance bucket, volatility bucket, and target UP/DOWN.
6. Group by `(stage, current_side, distance_bucket, volatility_bucket, pattern)` and compute historical probabilities.
7. Save `out_rounds_v2/BTCUSDT_5m_180d_state_bucket_report.csv`.
8. Live repo converts that CSV into `config/btc_updown_state_rules_15m.json` and looks up rules by the same state tuple.

## Mismatches / Findings

### 1. Directional pattern mismatch for plain doji label

Evidence:
- Reference `candle_pattern` returns `"doji"` for doji candles without long upper/lower wick (`polymarket_round_research_v2.py:258-265`).
- Live classifier returns `PatternName.FLAT.value` for the same non-flat doji/no-long-wick branch (`candle_features.py:102-109`).

Impact:
- Conceptual mismatch exists: reference label is `doji`, live label is `flat`.
- Saved 180d v2 files contained no plain `doji` pattern in raw/round/state-bucket inspection, so current generated rules appear unaffected unless future live data produces such a candle.

### 2. Current side mismatch near/equal to open

Evidence:
- Reference side is binary: `ABOVE_OPEN` if close/current > `round_open`, otherwise `BELOW_OPEN`; exact equality falls to `BELOW_OPEN` (`polymarket_round_research_v2.py:375-380`).
- Live has a third enum `AT_OPEN` (`models.py:41-44`) and classifies any absolute distance `< 0.00005` as `AT_OPEN` (`round_state.py:35, 81-84`).
- Reference CSV/rules inspection: current_side values are only `ABOVE_OPEN`/`BELOW_OPEN`; no `AT_OPEN` rules.

Impact:
- Live states within 0.5 bps of open can produce `AT_OPEN` and fail exact rule lookup, even though reference would have bucketed them as ABOVE/BELOW (or BELOW on equality).

### 3. Distance bucket boundary mismatch

Evidence:
- Reference uses `pd.cut(..., bins=[..., 0.0005, 0.0010, ...], include_lowest=True)`; pandas default is right-closed intervals, so exact boundaries go to the lower bucket (`polymarket_round_research_v2.py:466-497`).
- Live iterates upper bounds and uses `if abs_d < upper`, so exact boundaries go to the next higher bucket (`round_state.py:46-52, 73-78`).

Impact:
- Labels and cutoffs match in ordinary cases, but exact boundary values (0.0005, 0.0010, 0.0020, 0.0035, 0.0050) are assigned differently.

### 4. Volatility definition mismatch (major)

Evidence:
- Reference volatility is based on previous completed 15m rounds: `prev_16_abs_return_mean = round_abs_return.shift(1).rolling(16).mean()` where `round_abs_return = abs(c2.close / c0.open - 1)` (`polymarket_round_research_v2.py:355-356, 456-458`). Buckets use quantiles of this previous-round series (`polymarket_round_research_v2.py:499-514`).
- Live computes volatility from the last 16 **5m candles before current round start**, using close-to-close returns between those 5m candles (`round_state.py:97-113`), then applies hard-coded thresholds (`round_state.py:37-44, 87-94`).

Impact:
- This is not the same volatility feature used to generate the reference rules. Live may look up a different `VOL_LOW/NORMAL/HIGH` rule than the backtest intended.

### 5. Volatility boundary inclusivity mismatch

Evidence:
- Reference uses `<= q_low` and `<= q_high` (`polymarket_round_research_v2.py:505-512`).
- Live uses `< _VOL_LOW_MAX` and `< _VOL_NORMAL_MAX` (`round_state.py:87-94`).

Impact:
- Exact threshold values are assigned one bucket higher in live.

### 6. UP/DOWN target mapping mostly matches; tie handling differs

Evidence:
- Reference: UP when `round_close > round_open`, DOWN when `<`, ties skipped (`polymarket_round_research_v2.py:323-328`).
- Live Binance fallback settlement: UP when final price > round open, DOWN when <, exact tie defaults to DOWN (`settlement.py:35-41`).

Impact:
- No mismatch for non-tie UP/DOWN direction. Tie treatment differs: skipped in backtest, DOWN in live fallback.

### 7. Candle ordering / stage mapping: no mismatch found

Evidence:
- Reference orders c0/c1/c2 by slot in the 15m window and defines AFTER_10M as `c0 -> c1` (`polymarket_round_research_v2.py:301-318, 359-365`).
- Live sorts in-round candles by open time and builds the same `f0.pattern -> f1.pattern` combo for AFTER_10M (`round_state.py:116-122, 207-229`).
- Live Binance client drops in-flight candle, so AFTER_5M/AFTER_10M use closed candles like the reference (`binance_client.py:57-62, 97-107`).

### 8. Rule CSV to live JSON: no mismatch found

Evidence:
- `scripts/build_state_rules.py` reads `/home/alex/Project/poly_bot_system/out_rounds_v2/BTCUSDT_5m_180d_state_bucket_report.csv` and writes JSON fields directly (`scripts/build_state_rules.py:83-130`).
- Scripted inspection found 2,014 CSV rows and 2,014 JSON rules; all stage/current_side/distance_bucket/volatility_bucket/pattern/recommended_side/samples/probability/median/flags match numerically.

## Start Here

Start with `/home/alex/Project/polymarket_updown_bot/src/polymarket_round_bot/round_state.py`, because the main actionable mismatch is live state construction versus the reference state tuple: current-side `AT_OPEN`, distance boundary handling, and especially volatility source differ from `poly_bot_system/polymarket_round_research_v2.py`.
