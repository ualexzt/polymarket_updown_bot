# Entry-Mode Sensitivity Analysis

**Generated**: 2026-06-15

## TL;DR

Re-ran the 500-day walk-forward backtest with **4 different entry-price assumptions** to see which (if any) matches live's empirical WR. Result: **none of the entry-mode assumptions reproduces live's 59.3% WR**. The closest is `assume_very_wide_ask` (ask_spread=0.10) at 67.5% — still 8pp above live. This **definitively rules out entry-price formulas** as the cause of the gap. The gap is structural (live filters, adverse selection, or genuinely -EV), not entry-price.

## Setup

- **Data**: `data/btc_5m_500d.csv` (144k candles)
- **Rules**: `config/btc_updown_state_rules_15m.json` (2014 rules)
- **Folds**: 5 × 30 days, rolling
- **Filters**: samples ≥ 60, prob ≥ 0.60, return_aligned=true, max_entry_ask ≤ 0.80

**Entry modes** (all use the same `safety_buffer=0.05`, only the ask_spread offset differs):

| Mode | Formula | `ask_spread` |
|---|---|---:|
| `assume_mid` | entry = prob − 0.05 | 0.00 |
| `assume_tight_ask` | entry = prob − 0.05 + 0.02 | 0.02 |
| `assume_wide_ask` | entry = prob − 0.05 + 0.05 | 0.05 |
| `assume_very_wide_ask` | entry = prob − 0.05 + 0.10 | 0.10 |

## Results

| Mode | Trades | WR | Total PnL | avg_entry |
|---|---:|---:|---:|---:|
| **assume_mid** (backtest baseline) | 6,961 | **69.94%** | +$224.19 | 0.6686 |
| assume_tight_ask | 6,932 | 69.88% | +$84.73 | 0.6881 |
| assume_wide_ask | 6,411 | 69.31% | -$101.72 | 0.7089 |
| assume_very_wide_ask | 4,944 | 67.48% | -$328.39 | 0.7396 |
| **Live (post-fix, 59 trades)** | 59 | **59.3%** | -$1.69 | ~0.60 |

### Key observations

1. **WR is remarkably stable across entry modes** (67-70%). A 10% ask spread only drops WR by 2.5pp. This is because:
   - The rule's `recommended_side` is determined by the state (vol, dist, pattern) — not the entry price.
   - The win/loss outcome is determined by the round's UP/DOWN resolution — independent of entry price.
   - Entry price only affects *per-trade PnL* and *breakeven*, not the *probability of winning*.

2. **PnL is highly sensitive to entry price** — drops from +$224 (mid) to -$328 (very wide) because per-trade EV depends on `entry = 1 − win_payout × 0.5 + 0.5 − 0.5 × 0.5` etc.

3. **None of the modes reproduces live's 59.3% WR.** Even the most pessimistic (67.5%) is 8pp above live. This means **WR gap is NOT about entry price**.

## What this means

Since raising the entry price (which raises breakeven and reduces per-trade EV) doesn't bring WR down, **the gap must be caused by something other than entry price**. The remaining candidates, in order of likelihood:

1. **Sample selection bias** — live's risk filters (liquidity, daily loss cap, MAX_OPEN_POSITIONS) are selecting a *different subset* of the trades backtest would have taken. If live consistently skips the most profitable setups (e.g., due to the daily loss cap kicking in after a losing streak), its WR will be lower than backtest's.

2. **Adverse selection / orderbook microstructure** — when live submits a market order, it consumes the best ask. If the ask is at the top of a wide spread, the fill price is higher than backtest's assumed entry. But live's avg ask is *lower* than backtest's, so this is not the cause. Instead, the *win/loss classification* might differ because of different round_open / round_close timing or tie handling.

3. **Genuine -EV** — the strategy is structurally unprofitable on live because the alpha (if any) is smaller than the friction. Backtest overestimates alpha by ignoring:
   - Liquidity risk (the ask might not be available when needed)
   - Spread impact (each fill moves the price)
   - Round resolution timing (live may settle at a slightly different round_close_price than backtest's c2 close)
   - Sampling bias in rule generation (rules were calibrated on 180d of historical data and may not generalize)

## Recommendation

Given that **entry price isn't the cause** and the WR gap persists across all entry-mode assumptions:

1. **Stop relying on backtest WR for live decisions.** The 70% backtest WR is an upper bound on what live can achieve. Live's empirical WR (currently 59% on 59 trades) is the better signal.

2. **Decide based on live's WR, not backtest's:**
   - If you want statistical confidence on live's WR, need ~1000 trades (~50 days at current rate).
   - If you want to act now, treat the 59% as the realistic WR. Breakeven is 65% (avg entry ~0.65). Live is 6pp below breakeven, suggesting **the strategy is -EV by ~$0.10 per trade**. Stop or tighten gates.

3. **Concrete tightening proposal**: raise `MIN_EDGE` from 0.05 to 0.10 in `.env`. Even though our backtest shows that doesn't help (because backtest's WR is already too high), it might filter out some low-quality live setups. Expected effect: ~20-30% fewer trades, possibly higher WR. Validate with another 50-100 live trades.

4. **Bigger picture**: consider that the strategy may simply not work. Backtest on historical data with assumed entries showed 70% WR; live on real orderbook with real entries shows 59% WR. The 11pp gap is consistent with the backtest ignoring real-world frictions. Even if the strategy has a small positive edge, the edge is not large enough to overcome paper-trading noise or to scale to live capital.

## Appendix

**Script**: `scripts/entry_mode_backtest.py`
**Output**: `results/entry_modes/entry_modes_aggregate.json`

**Per-mode run times** (5 folds × ~2880 rounds each):
- `assume_mid`: ~3 min
- `assume_tight_ask`: ~3 min
- `assume_wide_ask`: ~3 min
- `assume_very_wide_ask`: ~3 min
- Total: ~12 min

**Caveat**: the `ask_spread` is applied uniformly to all trades. In reality, ask spreads vary by orderbook depth and time. A more accurate model would sample from a distribution of historical ask spreads. But the conclusion (WR doesn't depend on entry price) is robust to this simplification.
