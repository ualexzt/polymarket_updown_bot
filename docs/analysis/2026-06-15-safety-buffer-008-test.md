# Safety Buffer Sensitivity Test

**Generated**: 2026-06-15

## TL;DR

Re-ran the 500-day walk-forward backtest with `safety_buffer=0.08` (vs default 0.05). Result: **WR went UP from 69.9% to 72.3%, PnL more than doubled (+$224 → +$521)**. However, this **does not directly help live** because the entry price formulas differ:

- **Backtest**: `entry = historical_probability - safety_buffer` (assumed)
- **Live**: `entry = orderbook.ask` (real, varies)

Live's actual avg ask is already lower than backtest's assumed entry (avg diff: -0.05 on post-fix matched pairs). So raising safety_buffer helps backtest (lower breakeven, same or higher WR) but doesn't fix the live-vs-backtest gap, because the gap is **not about entry price** — it's about **win/loss classification on the same rounds**.

## Setup

Same as the original walk-forward:
- **Data**: `data/btc_5m_500d.csv` (144k candles, 500 days)
- **Rules**: `config/btc_updown_state_rules_15m.json` (2014 rules)
- **Filters**: samples ≥ 60, prob ≥ 0.60, return_aligned=true, max_entry_ask ≤ 0.80
- **Folds**: 5 × 30 days, rolling

**Difference**: `--safety-buffer 0.08` (vs default 0.05)

## Results

### Per-fold comparison

| Fold | safety=0.05 trades | safety=0.05 WR | safety=0.05 PnL | safety=0.08 trades | safety=0.08 WR | safety=0.08 PnL |
|---|---:|---:|---:|---:|---:|---:|
| 0 | 1370 | 69.4% | +$34.96 | 1639 | 71.9% | +$93.72 |
| 1 | 1407 | 69.8% | +$41.75 | 1665 | 72.6% | +$108.87 |
| 2 | 1380 | 68.0% | +$18.28 | 1622 | 70.7% | +$79.04 |
| 3 | 1374 | 70.9% | +$57.60 | 1655 | 73.1% | +$113.26 |
| 4 | 1430 | 71.6% | +$71.60 | 1679 | 73.3% | +$126.24 |
| **Σ** | **6,961** | **69.9%** | **+$224.19** | **8,260** | **72.3%** | **+$521.12** |

### Why does safety_buffer=0.08 help backtest so much?

Lowering `entry = prob - safety_buffer` means:
- **Lower breakeven WR**: at safety=0.05 and avg_prob=0.70, breakeven = 0.65. At safety=0.08, breakeven = 0.62. The strategy has more headroom.
- **More trades pass `max_entry_ask`**: at safety=0.05, prob=0.85 → entry=0.80 (at limit, pass). At safety=0.08, prob=0.85 → entry=0.77 (pass comfortably). At safety=0.05, prob=0.83 → entry=0.78 (pass). At safety=0.08, prob=0.83 → entry=0.75 (pass with more headroom). More rules now qualify.
- **Higher payout on win**: win → +(1 - entry) = +(1 - 0.62) = +0.38 at safety=0.08 vs +(1 - 0.65) = +0.35 at safety=0.05. Small but compounding.
- **Smaller loss on loss**: loss → -entry = -0.62 vs -0.65.

So the **same WR (72%)** at safety=0.08 yields higher per-trade EV than at safety=0.05. The strategy is **structurally more profitable** with larger safety buffer — at least in the backtest.

### Why doesn't this help live?

Live's entry price comes from `decision.market_ask`, the **real orderbook ask**, not from `prob - safety_buffer`:

```python
# from src/polymarket_round_bot/paper_broker.py
entry_price = decision.market_ask
```

`decision.market_ask` is populated from `OrderbookSnapshot.best_ask` in `signal_engine.py`. The backtest, on the other hand, uses:

```python
# from scripts/walk_forward_backtest.py::_evaluate_state
entry_price = rule.historical_probability - safety_buffer
```

These two formulas can give very different entry prices for the same round. **Live's avg ask on the post-fix period is actually LOWER than backtest's `prob - 0.05` by 0.05 on average** (matched pairs analysis: `avg_entry_diff = -0.05`).

So live is **already entering cheaper** than backtest assumes. The fact that live's WR is **lower** despite lower entry prices means the gap is **not** caused by spread. Something else is going on.

### What is causing the live WR gap then?

Hypotheses, in order of likelihood:

1. **Sample selection bias**: live applies risk filters (`MAX_OPEN_POSITIONS`, daily loss cap, liquidity) that backtest doesn't. Live is trading a *selected subset* of rounds — possibly the worse ones if the filters are excluding the most profitable setups.

2. **Orderbook microstructure**: live's real ask at the moment of entry may be from a thin orderbook, where the ask is at the edge of a wide spread. Backtest assumes a mid-like price. If the ask is the top-of-book and the fill is uncertain, live may be experiencing adverse selection (filling at the ask when the round is about to resolve against).

3. **Different sample sizes**: backtest has 115 trades for the post-fix period, live has 59. The 18pp gap is within the 95% CI of live's WR (46.8%-71.8%), so statistical noise is plausible.

4. **Genuine strategy alpha = 0**: The backtest WR of 72-77% is real alpha. Live is executing the same strategy but with worse fills or worse selection, ending up at 59% (below breakeven 65%). The actual *strategy* alpha (after correct execution) is somewhere between 60% and 75%, and live is on the wrong end of it.

## Recommendation

**Don't deploy `safety_buffer=0.08`.** It would help backtest but not live, because live doesn't use the backtest's entry formula.

Instead, two concrete next steps:

1. **Add a `decision.market_ask` filter to backtest** so that the backtest entry price simulates live's real orderbook behavior. Without this, the backtest is a fundamentally different model than live. (The current 0.05 difference is small but compounds over 8,000+ trades.)

2. **Investigate live's filter chain** (`signal_engine.py`, `risk_manager.py`, `paper_broker.py`) to understand why live rejects trades that backtest accepts. Specifically:
   - Is `decision.market_ask` the live's true entry? Or does it get adjusted?
   - Are there liquidity or timing checks that filter out profitable setups?
   - Does `MAX_OPEN_POSITIONS=1` cause live to skip AFTER_10M opportunities that backtest would take?

**Output artifacts**:
- `results/sb08_backtest/wf_aggregate_summary.json` (this run)
- `results/full/wf_aggregate_summary.json` (safety=0.05 baseline)
- Comparison: `safety_buffer=0.08` increases trades by 18.6%, WR by 2.4pp, PnL by 132% — all in backtest.
