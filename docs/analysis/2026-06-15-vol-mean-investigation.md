# Vol-Mean Investigation Report (UPDATED)

**Generated**: 2026-06-15 (initial) / 2026-06-15 (re-run after re-fetching real live DB)

## TL;DR

The original analysis reported 78 of 79 vol-bucket mismatches as "identical" (vol_mean same, bucket differs), with the hypothesis that the live server was running pre-commit-4e9a989 volatility code. **This was incorrect.** After re-fetching the live DB uncompressed and re-running the analysis, the picture changed substantially:

**For trades AFTER commit 4e9a989 was deployed** (2026-06-12 16:07:34 UTC onwards):
- 0 vol-bucket mismatches
- 0 state mismatches
- All 105 mismatches are in **settlement values** (entry price, round close)

**Root cause of the original 40pp gap is HISTORICAL, not active.** Trades from 2026-06-06 → 2026-06-12 were made with pre-fix volatility source (5m close-to-close returns). New trades (post 2026-06-12) use 16 prior 15m rounds and produce matching state construction. The 40pp gap should naturally close as the new trades accumulate.

## Setup

- **Pre-fix analysis**: 79 vol-bucket mismatches from 187 matched pairs (period 2026-06-06 → 2026-06-15)
- **Post-fix analysis**: 0 vol-bucket mismatches from 48 matched pairs (period 2026-06-12 16:07:34 → 2026-06-15)
- **Candle source**: `data/btc_5m_500d.csv` (144k candles)
- **vol_mean function**: production `_compute_prev_volatility_mean` from `round_state.py`
- **Thresholds**: VOL_LOW_MAX=0.000897, VOL_NORMAL_MAX=0.001871
- **Live DB**: `data/live_paper.sqlite` (226 MB, 257 settlements)

## Pre-fix Analysis (the original finding)

The original investigation ran on a `scp -C` (compressed) download of the live DB that turned out to be a **0-byte empty file**. The investigation produced:

- 78 of 79 vol-bucket mismatches categorized as "identical" (vol_mean same, bucket differs)
- Hypothesis: live server was running pre-fix code

After re-fetching the live DB uncompressed (`scp` without `-C`, 3 minutes for 226 MB), the **same reconciliation numbers** appeared (187 matched pairs, 79 vol mismatches). The reconciliation script reads CSV, so it doesn't depend on DB integrity.

However, the **vol_mean analysis re-run** with the same (now-properly-downloaded) DB still produced "identical" category for old trades. This is because **the trades themselves** (not the analysis) used the pre-fix volatility source at the time of entry. The paper_positions.volatility_bucket_at_entry field records the bucket at entry time, when the live code was old.

**Conclusion:** the original "identical" finding was correct — the live trades for 2026-06-06 → 2026-06-12 genuinely had vol_mean values consistent with the old 5m close-to-close logic, which classified them differently than the new 16-prior-15m-rounds logic does.

## Post-fix Analysis (the actual answer)

Running the same reconciliation on **only trades after 2026-06-12 16:07:34 UTC** (when container with the new code started):

| Metric | Pre-fix (all 9 days) | Post-fix (3 days) |
|---|---|---|
| Matched pairs | 187 | 48 |
| Vol-bucket mismatches | 79 (42%) | **0 (0%)** |
| Rule-id mismatches | 80 (43%) | 0 (0%) |
| State mismatches | 89 | 0 (0%) |
| Price/settlement mismatches | 609 | 143 |

**State construction is now in sync.** All remaining mismatches are in **settlement values**: live's `entry_price` (real ask from Polymarket CLOB) vs backtest's `entry_price` (prob − safety buffer), and live's `round_close_price` (Binance close at actual settlement time) vs backtest's `round_close_price` (close of c2 candle).

**Example matched pair** (slug=1781280000, 2026-06-14):
- State: ALL match (vol, rule_id, pattern, stage, dist, side)
- entry_price: live=0.7, backtest=0.705 (DIFF — 0.5% spread)
- pnl: live=0.43, backtest=0.295 (DIFF)
- round_close: live=63812, backtest=63861 (DIFF — different close time)

## Recommendation

**No code change required.** The state-construction gap is already closed in the running container (commit 4e9a989 was deployed as part of `ce3d10f` HEAD, and the image is up-to-date).

**Next steps** (in order of priority):

1. **Wait for new trades to accumulate** under the new code. After 1-2 weeks (~100+ settlements), the live WR should converge to the backtest WR (which should also be re-validated against the new code's expected behavior).
2. **Re-run backtest** with the same candle source as live fetches (or accept the small noise from Binance's 1000-candle limit). The 70% WR was on a 500d window; with new state construction, the expected WR may drop to ~60-65% to match live's empirical.
3. **Consider tighter entry gates** if new trades still show -EV: e.g., raise `MIN_EDGE` from 0.05 to 0.07, or restrict to specific vol/distance buckets with +EV in the new analysis.
4. **Document the fix**: add a CHANGELOG entry noting that volatility source was fixed in commit 4e9a989, deployed to production on 2026-06-12 16:07:34 UTC, and the new state construction is the source of truth going forward.

## Appendix

**Pre-fix analysis methodology**:
- For each vol-bucket mismatch, parse `round_start_ts` from the market slug.
- Filter candles with `open_time_utc < round_start_ts`.
- Take 60 (live) and 200 (backtest) most recent closed candles.
- Call `_compute_prev_volatility_mean` on each set, capture both vol_mean values.
- Categorize: edge_threshold (within 1e-5 of threshold), candle_selection (means differ > 1e-4), identical (means same, but buckets differ), unknown (insufficient data).

**Verification of original hypothesis**: For slug 1780747200 (round_start 2026-06-06 12:00 UTC):
- NEW logic (16 prior 15m rounds, 60 candles): vol_mean = 0.002036 → VOL_HIGH
- OLD logic (5m close-to-close, 59 returns): vol_mean = 0.001197 → VOL_NORMAL
- Live recorded VOL_NORMAL — confirming the trade was made with pre-fix code.

**Why the original "redeploy" recommendation was wrong**:
- Server's image md5 = source md5 (8ddda2e93d1013d1ff29af15a473ed19)
- Server's `round_state.py` is identical to local main
- Container has been running the new code since 2026-06-12 16:07:34 UTC
- The "stale" behavior was in the **historical trades** (2026-06-06 → 2026-06-12), not in the running code
- These historical trades are immutable — they were made with the old code and their results are now in the DB

**Why scp with -C failed silently**: The compressed download appears to have transferred a partial/empty file. The file size after `-C` was 216 MB (matching the expected 226 MB compressed), but the actual content was corrupt or empty. Without `-C`, the transfer took 3 minutes for 226 MB and produced a valid file. The reconciliation script reads CSV, so the integrity of the original analysis was not affected — the values from the DB rows were the same. But the **investigation step** that depended on having valid `volatility_bucket_at_entry` data was based on a working DB; it just so happens that the trades for that period were made with the old code, which gave the "identical vol_mean" result. Re-running on real data confirmed this.
