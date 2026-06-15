# Vol-Mean Investigation Report

**Generated**: 2026-06-15

## TL;DR

Across 79 vol-bucket mismatches, the dominant category is **identical** (78 / 79 = 98.7%).

**The vol_mean is identical but buckets differ — this is the smoking gun.** Both perspectives return the same vol_mean, but the resulting vol_bucket is different. This is impossible if both use the same `_classify_volatility` function. Likely cause: the live server is running pre-fix code (commit 4e9a989) that uses 5m close-to-close returns instead of 16 prior 15m rounds. Verify by checking the running container's round_state.py against the local version; redeploy if needed.

## Setup

- **Inputs**: vol-bucket mismatches from `results/recon/matched_pairs.csv`
- **Candle source**: `data/btc_5m_500d.csv` (same CSV the backtest used)
- **Live perspective**: 60 most recent closed candles before `round_start_ts`
- **Backtest perspective**: 200 most recent closed candles before `round_start_ts`
- **vol_mean function**: production `_compute_prev_volatility_mean` from `round_state.py`
- **Thresholds: VOL_LOW_MAX=0.000897, VOL_NORMAL_MAX=0.001871**

**Critical methodological note**: Both perspectives call the same function with the same candle source. If vol_mean comes out identical, the bucket classification must also be identical — UNLESS the live server is running a different function than this local code.

## Per-pair analysis

See `results/investigation/vol_mean_per_pair.csv` for the full table (79 rows).

**First 5 rows (preview):**

| market_slug | round_start | live_vol | backtest_vol | live_mean | backtest_mean | diff | category |
|---|---|---|---|---|---|---|---|
| btc-updown-15m-1780747200 | 2026-06-06T12:00:00+00:00 | VOL_NORMAL | VOL_HIGH | 0.002036301134957853196753998916 | 0.002036301134957853196753998916 | 0E-30 | identical |
| btc-updown-15m-1780751700 | 2026-06-06T13:15:00+00:00 | VOL_NORMAL | VOL_HIGH | 0.002349053121429549013548492403 | 0.002349053121429549013548492403 | 0E-30 | identical |
| btc-updown-15m-1780752600 | 2026-06-06T13:30:00+00:00 | VOL_NORMAL | VOL_HIGH | 0.002396355916864164135334360114 | 0.002396355916864164135334360114 | 0E-30 | identical |
| btc-updown-15m-1780753500 | 2026-06-06T13:45:00+00:00 | VOL_NORMAL | VOL_HIGH | 0.002203823038011175950826049241 | 0.002203823038011175950826049241 | 0E-30 | identical |
| btc-updown-15m-1780755300 | 2026-06-06T14:15:00+00:00 | VOL_NORMAL | VOL_HIGH | 0.001915796891392598733540739231 | 0.001915796891392598733540739231 | 0E-30 | identical |


## Categorization

| Category | Count | % |
|---|---:|---:|
| edge_threshold | 1 | 1.3% |
| candle_selection | 0 | 0.0% |
| identical | 78 | 98.7% |
| unknown | 0 | 0.0% |
| **Total** | **79** | 100.0% |

## Edge cases

**1 pairs have vol_mean within 1e-5 of a threshold.** See `results/investigation/edge_case_summary.txt` for the full list.

## Recommendation

**Primary action: identical**

**The vol_mean is identical but buckets differ — this is the smoking gun.** Both perspectives return the same vol_mean, but the resulting vol_bucket is different. This is impossible if both use the same `_classify_volatility` function. Likely cause: the live server is running pre-fix code (commit 4e9a989) that uses 5m close-to-close returns instead of 16 prior 15m rounds. Verify by checking the running container's round_state.py against the local version; redeploy if needed.

**Secondary actions** (if dominant category is not enough to close the gap):

- **edge_threshold** (1): **Fix the bucket thresholds.


## Appendix

**Methodology**:
- For each vol-bucket mismatch, parse `round_start_ts` from the market slug.
- Filter candles with `open_time_utc < round_start_ts`.
- Take the 60 most recent (live perspective) and 200 most recent (backtest perspective).
- Call `_compute_prev_volatility_mean` on each set, capturing both vol_mean values.
- Categorize based on diff, threshold proximity, and edge cases.

**Limitations**:
- Both perspectives use the same 500d candle CSV. The only difference is the selection window (60 vs 200). If the live candle feed diverges from this CSV, the analysis may miss that cause.
- vol_mean numerical precision is bounded by Decimal; diffs < 1e-12 are not meaningful and may be reported as 'identical'.
- The 1e-5 edge threshold is heuristic; the actual noise floor may be different.

**Verification step**: For 'identical' cases, manually compute `live_vol_mean` with the 5m close-to-close logic (the pre-fix volatility function) on the same 60 candles. If the result is close to `_VOL_LOW_MAX` or `_VOL_NORMAL_MAX`, this confirms the live server is using the older logic.
