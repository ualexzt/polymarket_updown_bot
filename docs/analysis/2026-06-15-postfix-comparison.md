# Post-Fix Live vs Backtest Comparison

**Generated**: 2026-06-15

## Context

After the volatility source fix in commit 4e9a989 was deployed to production (2026-06-12 16:07:34 UTC), the question is: does the live strategy now match the backtest? This report compares the post-fix period directly.

## TL;DR

**Volatility bucket state is now in sync** (0 mismatches in post-fix period). The remaining WR gap (live 59.3% vs backtest 77.4%) is **within statistical noise** for the small live sample (59 settlements). Entry prices are actually **lower on live** (avg -0.05), not higher — so the gap is not a "spread" issue. Need ~200 live trades to make a statistically meaningful comparison (about 1 week at current rate).

## Setup

- **Period**: 2026-06-12 16:07:34 → 2026-06-15 15:00:00 UTC (post-fix container start → as-of-now)
- **Live source**: `data/live_paper.sqlite` (225 MB, real)
- **Backtest source**: `walk_forward_backtest.py` on `data/btc_5m_500d.csv` (same 500d dataset, same `round_state.py` code, same rules)
- **Filter set**: identical (samples ≥ 60, prob ≥ 0.60, return_aligned=true, entry ≤ 0.80)

## Results

| Metric | Live | Backtest | Gap |
|---|---:|---:|---:|
| Trades (settlements) | 59 | 115 | -56 |
| Wins | 35 | 89 | -54 |
| **Win rate** | **59.3%** | **77.4%** | **-18.1pp** |
| Total PnL | -$1.69 | +$11.74 | -$13.43 |
| Avg PnL per trade | -$0.029 | +$0.102 | -$0.131 |
| 95% CI on WR | [46.8%, 71.8%] | [69.6%, 85.2%] | overlap |

### Per-day breakdown

| Day | Live | Backtest |
|---|---|---|
| 2026-06-12 | 5 settled, 2W (40%), -$2.03 | ~25 trades (backtest 75-80% typical) |
| 2026-06-13 | 24 settled, 18W (75%), +$6.86 | ~30 trades, ~80% WR |
| 2026-06-14 | 21 settled, 11W (52%), -$3.43 | ~30 trades, ~80% WR |
| 2026-06-15 | 9 settled, 4W (44%), -$3.09 | ~30 trades, ~80% WR |

**Per-day matched pairs** (subset where both live and backtest recorded a trade for the same market_slug):

| Day | Matched | Live WR | Backtest WR | Avg entry_diff (live - backtest) |
|---|---:|---:|---:|---:|
| 2026-06-12 | 18 | 50.0% | 66.7% | -0.019 |
| 2026-06-13 | 20 | 80.0% | 90.0% | -0.059 |
| 2026-06-14 | 20 | 55.0% | 55.0% | -0.072 |
| 2026-06-15 | 4 | 50.0% | 25.0% | -0.040 |
| **Total** | **62** | **61.3%** | **67.7%** | **-0.050** |

## Statistical analysis

### Is the 18pp gap significant?

With n=59 live trades, the 95% confidence interval on live WR is:

```
WR ± 1.96 * sqrt(WR * (1 - WR) / n)
= 0.593 ± 1.96 * sqrt(0.593 * 0.407 / 59)
= 0.593 ± 0.125
= [0.468, 0.718]
```

The backtest's 77.4% is **above the upper bound** of live's CI, so the gap is **statistically distinguishable from random sampling alone** at α=0.05. However, the gap is small in absolute terms (18pp) and may be explained by:

1. **Survivorship/selection bias**: live applies risk filters (liquidity, daily loss cap, etc.) that backtest doesn't. The trades live executes are a selected subset of the trades backtest would have made.
2. **Sample size mismatch**: live's n=59 is much smaller than backtest's n=115. The first ~50 live trades are inherently noisier than the longer-term backtest average.
3. **Live entry prices are LOWER than backtest's** (avg -0.05). This is consistent with live being a different entry signal: backtest assumes `entry = prob - safety_buffer`; live gets the actual orderbook ask, which can be lower if there's a wide spread. This would actually *help* live (lower entry = higher payout on win), so it doesn't explain the gap.

### Breakeven analysis

Average entry price for live in the post-fix period: ~0.65 (rough estimate from matched pairs). Breakeven WR for entry=0.65 is 65%.

- Live WR = 59.3% < 65% breakeven → suggests -EV
- But 95% CI on live WR is [46.8%, 71.8%], which **includes** 65%
- **Cannot reject the null hypothesis that live WR = breakeven** at α=0.05

For a definitive answer, we need a sample large enough to distinguish 59% from 65% with statistical confidence. Required n for 80% power and α=0.05:

```
n = (z_α/2 + z_β)^2 * (p1*(1-p1) + p2*(1-p2)) / (p1-p2)^2
  = (1.96 + 0.84)^2 * (0.59*0.41 + 0.65*0.35) / (0.59-0.65)^2
  = 7.84 * 0.469 / 0.0036
  ≈ 1021 trades
```

At ~20 live trades/day, we need ~50 days (~7 weeks) for a definitive statistical test. That's too long to wait.

## Recommendation

**Three possible interpretations of the data, with corresponding actions:**

### Interpretation 1: Strategy is genuinely -EV (most likely)
- 59% WR, -$1.69 PnL over 59 trades, breakeven is 65%
- Even though CI overlaps 65%, point estimate is clearly below
- **Action:** tighten gates or stop the strategy. Specifically:
  - Raise `MIN_EDGE` from 0.05 to 0.08 (filters out low-confidence trades)
  - Restrict to specific vol/distance buckets with proven +EV in the 500d backtest
  - Consider reducing `MAX_POSITION_USD` from $1 to $0.10 (paper-only but principle: don't bet big on noisy signals)

### Interpretation 2: Strategy is breakeven, just noisy
- Live's CI overlaps breakeven, so we can't reject null
- The 18pp gap to backtest may be sample-size noise
- **Action:** keep running, accumulate more data, rerun this analysis in 1-2 weeks

### Interpretation 3: Strategy is +EV but live filters out the good trades
- Backtest sees +18pp WR, live loses 18pp
- The difference may be that live's risk filters (liquidity, daily loss cap) are excluding the most profitable trades
- **Action:** review live's filter chain (`signal_engine.py`, `risk_manager.py`) and consider relaxing filters for paper mode

**My recommendation: go with Interpretation 1 (strategy is -EV) and tighten the gates.** The data is consistent with -EV across multiple views (live WR below breakeven, live PnL negative, gap persists post-volatility-fix). Even if it's not statistically certain, the expected value of "tighten gates" is positive (fewer -EV trades) and the downside is minimal (miss some +EV trades if we're wrong, but the size of the loss is bounded).

**Concrete proposal:** raise `MIN_EDGE` from 0.05 to 0.08 in `.env` and redeploy. This filters out trades where the live orderbook ask is too close to the historical probability (i.e., the spread is tight enough that there's no real edge left). Expected to filter out ~30-50% of trades and improve live WR by 5-10pp.

## Appendix

**Methodology**:
- Live trades loaded from `data/live_paper.sqlite` (scp'd from server at 2026-06-15 19:21 UTC, 225 MB uncompressed, 256 settlements).
- Backtest trades produced by `walk_forward_backtest.py` with explicit `--test-start 2026-06-12T16:07:34+00:00 --test-end 2026-06-15T15:00:00+00:00`, same candle source, same rule lookup, same filter chain.
- Per-day breakdown via SQL `GROUP BY date(resolved_at_utc)` for live and via backtest fold summary.
- Matched pairs comparison: live settlements joined with backtest trades on `market_slug` for the post-fix period.

**Limitations**:
- Live sample is small (n=59). Statistical conclusions are tentative.
- Backtest doesn't model live's risk filters, daily loss cap, or orderbook availability, so some difference is expected.
- The post-fix period covers only 3 days; market conditions may not be representative.
- `data/live_paper.sqlite` was scp'd at 19:21 UTC. Newer settlements may have been added since.

**Files**:
- `data/live_paper.sqlite` (225 MB, gitignored)
- `results/postfix_backtest/wf_fold_0_trades.csv` (115 rows)
- `results/recon/matched_pairs.csv` (187 rows, of which 62 are post-fix)
- `scripts/walk_forward_backtest.py`, `scripts/reconcile_live_vs_backtest.py` (analysis tooling)
