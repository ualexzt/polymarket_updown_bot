# Walk-Forward Validation + Breakeven Analysis

**Generated**: 2026-06-15

## TL;DR

**+EV on out-of-sample.** Across 5 folds (2025-01-31 → 2026-06-15), the live rules generated **6961 counterfactual trades** with a cross-fold mean WR of **69.94%** (σ = 1.40pp, **stable**) and total PnL of **$224.19**.

## Setup

- **Data range**: 2025-01-31T15:20:00+00:00 → 2026-06-15T15:15:00+00:00
- **Number of folds**: 5
- **Rules source**: `config/btc_updown_state_rules_15m.json` (live rules)
- **Position size**: $1.00 per trade (matches live `MAX_POSITION_USD`)
- **Filters**: samples ≥ 60, historical_probability ≥ 0.60, return_aligned=true, entry_price ≤ 0.80 (matches live)

## Per-fold results

| Fold | Test range | n_rounds | n_trades | WR | PnL | avg_PnL | avg_entry |
|------|------------|----------|----------|------|--------|---------|-----------|
| 0 | 2025-01-31 → 2025-03-02 | 2879 | 1370 | 69.42% | $34.96 | $0.03 | 0.6686435841624957840875912409 |
| 1 | 2025-03-02 → 2025-04-01 | 2879 | 1407 | 69.79% | $41.75 | $0.03 | 0.6682670191110620923240938166 |
| 2 | 2025-04-01 → 2025-05-01 | 2879 | 1380 | 67.97% | $18.28 | $0.01 | 0.6664639321455917371739130435 |
| 3 | 2025-05-01 → 2025-05-31 | 2879 | 1374 | 70.89% | $57.60 | $0.04 | 0.6669594335723355399563318777 |
| 4 | 2025-05-31 → 2025-06-30 | 2879 | 1430 | 71.61% | $71.60 | $0.05 | 0.6660122515530955185314685315 |

## Stability

- **Cross-fold mean WR**: 69.94%
- **Cross-fold stdev WR**: 1.40pp
- **Stability verdict**: STABLE (threshold 5pp)
- **Total PnL across folds**: $224.19

## Breakeven analysis

Entry-price bins (breakeven WR = entry_price):

| Entry bin | n | WR | PnL |
|---|---|---|---|
| 0.30-0.35 | 0 | 0.00% | $0.00 |
| 0.35-0.40 | 0 | 0.00% | $0.00 |
| 0.40-0.45 | 0 | 0.00% | $0.00 |
| 0.45-0.50 | 0 | 0.00% | $0.00 |
| 0.50-0.55 | 0 | 0.00% | $0.00 |
| 0.55-0.60 | 1435 | 60.14% | $35.26 |
| 0.60-0.65 | 1128 | 64.89% | $16.76 |
| 0.65-0.70 | 2381 | 73.20% | $113.99 |
| 0.70-0.75 | 1467 | 75.39% | $52.82 |
| 0.75-0.80 | 550 | 77.27% | $5.35 |
| 0.80-0.85 | 0 | 0.00% | $0.00 |

## Rule rankings

**Top 10 rules by PnL**:

| Rule | n | WR | PnL |
|---|---|---|---|
| btc_15m_after_5m_below_open_d_010_020pct_vol_high_normal_bear | 291 | 77.66% | $26.49 |
| btc_15m_after_5m_below_open_d_010_020pct_vol_normal_normal_bear | 269 | 78.81% | $25.69 |
| btc_15m_after_5m_above_open_d_005_010pct_vol_normal_normal_bull | 489 | 70.96% | $24.70 |
| btc_15m_after_5m_above_open_d_010_020pct_vol_normal_normal_bull | 286 | 79.72% | $22.30 |
| btc_15m_after_5m_below_open_d_005_010pct_vol_normal_strong_bear_close_near_low | 356 | 75.56% | $20.22 |
| btc_15m_after_5m_above_open_d_010_020pct_vol_high_strong_bull_close_near_high | 215 | 81.40% | $19.55 |
| btc_15m_after_5m_above_open_d_005_010pct_vol_normal_strong_bull_close_near_high | 364 | 74.18% | $16.80 |
| btc_15m_after_5m_above_open_d_005_010pct_vol_normal_bull_long_upper_wick | 119 | 71.43% | $14.99 |
| btc_15m_after_5m_below_open_d_0_005pct_vol_low_strong_bear_close_near_low | 221 | 69.68% | $10.47 |
| btc_15m_after_5m_below_open_d_005_010pct_vol_high_normal_bear | 188 | 65.43% | $9.67 |

**Bottom 10 rules by PnL**:

| Rule | n | WR | PnL |
|---|---|---|---|
| btc_15m_after_5m_above_open_d_010_020pct_vol_high_bull_long_lower_wick | 63 | 61.90% | $-0.76 |
| btc_15m_after_5m_below_open_d_0_005pct_vol_normal_normal_bear | 263 | 63.88% | $-1.28 |
| btc_15m_after_5m_above_open_d_020_035pct_vol_high_normal_bull | 156 | 75.64% | $-1.31 |
| btc_15m_after_5m_above_open_d_005_010pct_vol_high_bull_long_upper_wick | 106 | 55.66% | $-2.15 |
| btc_15m_after_5m_below_open_d_005_010pct_vol_low_normal_bear | 141 | 73.76% | $-2.23 |
| btc_15m_after_5m_below_open_d_010_020pct_vol_high_bear_long_upper_wick | 67 | 64.18% | $-2.45 |
| btc_15m_after_5m_above_open_d_005_010pct_vol_normal_bull_long_lower_wick | 134 | 60.45% | $-3.23 |
| btc_15m_after_5m_above_open_d_010_020pct_vol_high_normal_bull | 311 | 69.45% | $-3.26 |
| btc_15m_after_5m_above_open_d_010_020pct_vol_high_bull_long_upper_wick | 72 | 58.33% | $-6.94 |
| btc_15m_after_5m_above_open_d_0_005pct_vol_normal_bull_long_upper_wick | 357 | 53.22% | $-11.45 |

## Live cross-check

Live PnL on the most recent 9 days (2026-06-06 → 2026-06-15): **−$3.72 on 7 settled trades (PnL avg −$0.53/settled, WR 28.6% on the 7 settled).**

The backtest's most recent fold (overlapping with the live period) should show a comparable PnL. If the backtest is significantly more or less negative than live, the state-construction mismatches identified in `backtest-reference-compare.md` may be material. Detailed comparison is in `results/wf_aggregate_summary.json` (look at the fold with `test_end` closest to 2026-06-15).

## Findings & recommendations

- **Strategy is +EV across the analyzed folds**: total PnL $224.19 on 6961 trades.
- **Stability is acceptable**: cross-fold stdev of WR is 1.40pp, below the 5pp threshold.
- **Review rule rankings in `results/rule_performance_ranked.csv`**: rules with WR below their entry-price breakeven are destroying value; consider dropping them via a tighter whitelist.
- **Compare backtest vs live in the most recent fold**: if the backtest is materially different from live, the state-construction mismatches (`backtest-reference-compare.md`) likely need fixing before further tuning.

## Appendix

**Methodology**:
- For each 15m round, the backtest replays `build_round_state()` and `ProbabilityRules.lookup()` exactly as the live bot does.
- Entry price is set to `historical_probability − safety_buffer` (the live formula).
- Settlement uses the close of the third 5m candle (`c2`), which closes at the round's end time.

**Limitations**:
- No orderbook spread modeling: real entries may be worse than the backtest's.
- No slippage or liquidity constraints.
- No daily loss cap (live caps at $10/day).
- Round starts are aligned to UTC quarter-hour (00, 15, 30, 45); live rounds are similarly aligned, but exact timestamps may differ.

**Data source**: Binance public 5m klines (`https://data-api.binance.vision/api/v3/klines`).
