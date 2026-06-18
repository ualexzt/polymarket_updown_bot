# Code Context

## Files Retrieved
1. `src/polymarket_round_bot/models.py` (lines 29-83, 278-413) - canonical enums/types for stage, current side, side, `RoundState`, `ProbabilityRule`, `SignalDecision`, and persisted `DecisionSnapshot` fields.
2. `src/polymarket_round_bot/round_state.py` (lines 64-190, 194-285) - constructs directional state: signed distance, current_side, distance bucket, round_open, c0/c1/c2 indexing, stage, and pattern_combo.
3. `src/polymarket_round_bot/binance_client.py` (lines 49-112) - fetches Binance 5m candles, drops in-flight candle, sets `current_price` from latest closed candle.
4. `src/polymarket_round_bot/probability_rules.py` (lines 64-200) - parses rules, builds exact/fallback indexes, returns `recommended_side` and no-trade filters.
5. `src/polymarket_round_bot/signal_engine.py` (lines 60-70, 77-225, 228-420) - selects UP/DOWN from `recommended_side`, gates trade timing/price/liquidity/risk.
6. `src/polymarket_round_bot/runner.py` (lines 47-69, 98-230, 490-595) - current slug/window handling, live dataflow, rule lookup, risk/decision call, snapshot side fields.
7. `src/polymarket_round_bot/polymarket_discovery.py` (lines 99-145, 169-185) - maps Gamma market window and outcome token order/resolution.
8. `src/polymarket_round_bot/settlement.py` (lines 26-41, 71-95) - final UP/DOWN settlement semantics against round_open.
9. `src/polymarket_round_bot/config.py` (lines 38-81, 86-105) - thresholds and default gates affecting live selection.
10. `scripts/run_polymarket_round_paper.py` (lines 40-49, 59-123) - entrypoint resolving active slug and constructing `Runner` with timeframe auto-refresh.
11. `scripts/build_state_rules.py` (lines 1-14, 45-80, 83-143) - generated rules source mapping from research CSV to JSON.
12. `config/btc_updown_state_rules_15m.json` (lines 1-31) - generated rule schema/sample; full-file stats inspected by script.
13. `tests/test_round_state.py` (lines 30-146) - guards current_side, distance buckets, 15m stage and pattern_combo behavior.
14. `tests/test_probability_rules.py` (lines 51-99, 147-191, 194-223) - guards exact/fallback lookup and rule no-trade filters.
15. `tests/test_signal_engine.py` (lines 102-132, 430-600) - guards side selection, fallback trading gate, expiry windows, DOWN-specific gates.
16. `tests/test_current_expected_slug.py` (lines 29-57) - guards 5m/15m slug floor boundaries.

## Key Code

### Direction/state definitions
`src/polymarket_round_bot/models.py` defines:

```py
class Stage(str, Enum):
    AFTER_5M = "AFTER_5M"
    AFTER_10M = "AFTER_10M"
    CUSTOM_5M_STATE = "CUSTOM_5M_STATE"

class CurrentSide(str, Enum):
    ABOVE_OPEN = "ABOVE_OPEN"
    BELOW_OPEN = "BELOW_OPEN"
    AT_OPEN = "AT_OPEN"

class Side(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
```

`RoundState.distance_pct` is documented as signed positive ABOVE / negative BELOW (`models.py` lines 283-286). `DecisionSnapshot.distance_from_round_open` persists this same signed value (`runner.py` lines 530-533).

### Round open/current side/distance
`src/polymarket_round_bot/round_state.py`:

```py
def _signed_distance_pct(current: Decimal, base: Decimal) -> Decimal:
    """Signed return = (current - base) / base"""
    return (current - base) / base

def _classify_current_side(distance_pct: Decimal) -> CurrentSide:
    if abs(distance_pct) < Decimal("0.00005"):
        return CurrentSide.AT_OPEN
    return CurrentSide.ABOVE_OPEN if distance_pct > 0 else CurrentSide.BELOW_OPEN
```

No sign inversion found here: `current > round_open` => positive distance => `ABOVE_OPEN`; settlement fallback also uses `final_btc_price > round_open_price` => `Side.UP` (`settlement.py` lines 35-38).

Round open is first in-round 5m candle open if available; otherwise prior closed candle close (`round_state.py` lines 130-144). In-round candles are selected by open time within `[market.start_ts, market.end_ts)` (`round_state.py` lines 116-122).

### Candle indexing and pattern_combo
For 15m markets (`round_state.py` lines 207-245):

- `c0 = in_round[0]` (round start to +5m)
- `c1 = in_round[1]` (+5m to +10m)
- `c2 = in_round[2]` (+10m to expiry; only closed at/after expiry)
- if c0+c1 exist: stage `AFTER_10M`, pattern `"{c0_pattern} -> {c1_pattern}"`, `pattern_combo=pattern`
- if only c0 exists: stage `AFTER_5M`, pattern is c0 single-candle pattern, `pattern_combo=None`

Rule lookup uses `state.candle_pattern`, not `state.pattern_combo` (`runner.py` lines 139-145). `pattern_combo` is only persisted/logged (`runner.py` lines 536-537).

### Binance current price source
`src/polymarket_round_bot/binance_client.py` drops the last kline row as in-flight and sets current price from the latest closed candle:

```py
closed_rows = rows[:-1] if len(rows) > 1 else rows
current_price = candles[-1].close
```

See lines 57-63 and 97-108. This is central to current_side: `build_round_state()` uses `current = binance.current_price` (`round_state.py` lines 157-160).

### Rule lookup and recommended side
Rules parse `recommended_side` directly from JSON (`probability_rules.py` lines 64-91). Exact key is:

```py
(stage, current_side, distance_bucket, volatility_bucket, pattern)
```

Fallbacks remove volatility, then pattern (`probability_rules.py` lines 100-130). No-trade filters are `usable_signal`, `samples`, `historical_probability`, `return_aligned` (`probability_rules.py` lines 180-191).

### Live UP/DOWN selection
`src/polymarket_round_bot/signal_engine.py` never derives side from `current_side`; it uses the rule recommendation:

```py
def _select_side_for_observation(state, lookup):
    if lookup.recommended_side is None:
        return None
    return lookup.recommended_side
```

Then it selects the matching orderbook (`Side.UP` => `pair.up`, else `pair.down`) and trades only if ask/edge/spread/liquidity/risk gates pass (`signal_engine.py` lines 207-247, 249-420).

Defaults affecting direction/trading:

- exact matches only by default (`config.py` lines 70-73; `signal_engine.py` lines 164-176)
- 15m AFTER_5M window: seconds_to_expiry 300-600; AFTER_10M: 60-300 (`config.py` lines 75-81; `signal_engine.py` lines 178-205)
- extra DOWN gates: AFTER_5M DOWN requires `seconds_to_expiry >= 540`, and DOWN ask must be `[0.55, 0.70)` (`signal_engine.py` lines 211-225, 282-310)

### Selected/recommended/current side persistence
In `_build_snapshot()` (`runner.py` lines 501-590):

- `side_checked = lookup.recommended_side or decision.side or Side.UP`
- `selected_side = decision.side`
- `recommended_side = lookup.recommended_side`
- `current_side = state.current_side`
- orderbook/token fields are for `side_checked`, not necessarily `selected_side` on skips.

For TRADE, `decision.side` is the selected/recommended side. For many SKIPs, `selected_side` is `None` but `side_checked` may still be the recommendation (or fallback `UP` if no rule).

### Config/rules observations
Full `config/btc_updown_state_rules_15m.json` stats inspected:

- 2014 rules total: 1838 `AFTER_10M`, 176 `AFTER_5M`.
- Pattern shapes match live construction: 1838 arrow combos for `AFTER_10M`, 176 single patterns for `AFTER_5M`.
- `recommended_side`: 1061 UP / 953 DOWN.
- `current_side`: 1009 ABOVE_OPEN / 1005 BELOW_OPEN; no AT_OPEN rules.
- `usable_signal && return_aligned`: 72 rules.
- After applying live default thresholds (`samples >= 60`, `historical_probability >= 0.60`) among usable+aligned rules: 58 rules; all are trend-aligned by current_side (`ABOVE_OPEN`=>UP, `BELOW_OPEN`=>DOWN).
- For all usable rules, `recommended_side` agrees with `median_round_return` sign. No obvious generated-rule sign inversion found.

## Architecture

Live dataflow:

1. `scripts/run_polymarket_round_paper.py` resolves the active slug using `current_expected_slug(args.timeframe)` unless explicit URL/slug is supplied (`run_polymarket_round_paper.py` lines 40-49, 115-123).
2. `Runner.run_one_cycle()` discovers market metadata, including actual window start/end and UP/DOWN token ids (`runner.py` lines 102-108; `polymarket_discovery.py` lines 114-144).
3. Runner fetches Binance 5m klines (`runner.py` lines 110-116); Binance client removes in-flight kline and returns latest closed close as `current_price` (`binance_client.py` lines 97-108).
4. `build_round_state()` computes round open, signed distance/current_side, volatility, c0/c1/c2, stage, and pattern (`round_state.py` lines 147-245).
5. Runner looks up a rule by exact state tuple using `state.candle_pattern` (`runner.py` lines 139-147; `probability_rules.py` lines 149-166).
6. Risk uses `lookup.recommended_side or Side.UP` as candidate side (`runner.py` lines 187-190).
7. Signal engine chooses the rule `recommended_side`, then gates timing, price, edge, spread, liquidity, and risk (`signal_engine.py` lines 150-420).
8. Runner opens/persists a paper position if TRADE; snapshot records state, recommended side, checked side, selected side, and orderbook (`runner.py` lines 225-260, 490-595).
9. Settlement resolves UP/DOWN from Polymarket if possible, otherwise final Binance close vs stored round_open (`settlement.py` lines 26-41).

## Suspicious mismatches / risks

1. **Most suspicious: `current_side`/`distance_from_round_open` are not live current price; they are latest CLOSED 5m close.**  
   `fetch_recent_5m_klines()` explicitly drops the in-flight candle and sets `current_price = candles[-1].close` (`binance_client.py` lines 57-63, 97-108). `build_round_state()` uses this as `current_btc_price`, `current_side`, and `distance_pct` (`round_state.py` lines 157-160). During the AFTER_5M and AFTER_10M trading windows this can be stale by up to almost 5 minutes. If research rules were meant to evaluate exactly at closed-candle boundaries, this is consistent; if the live bot intends current side/distance at entry time, this is a directional-state mismatch.

2. **Freshness gate checks fetch timestamp, not candle-close age.**  
   Signal engine gates `bn_age = now - binance_received_at_utc` (`signal_engine.py` lines 134-137), and runner passes `binance.received_at_utc` (`runner.py` lines 219-220). A freshly fetched but 4m-old closed candle passes the 10s freshness gate. This compounds risk #1.

3. **Persisted `binance_data_age_seconds` calculation appears wrong.**  
   `_build_snapshot()` computes `now - last_candle.open_time_utc + 300` (`runner.py` lines 506-511). If measuring age since candle close, it should conceptually be `now - (open_time + 300s)`, not plus 300. This is only persisted metadata, not the trade gate.

4. **`side_checked`/orderbook fields can look selected even on SKIP.**  
   Snapshot uses `side_checked = lookup.recommended_side or decision.side or Side.UP` and fills selected orderbook/token fields from `side_checked` (`runner.py` lines 501-527), while `selected_side=decision.side`. For no-rule skips this defaults side_checked to UP even though no side was selected. This is a reporting/analysis mismatch, not a live trade-side inversion.

5. **Fallback lookup can choose an unusable high-sample rule before filters.**  
   `_best()` picks max `(samples, historical_probability)` before `usable_signal`/`return_aligned` filters (`probability_rules.py` lines 133-135, 180-191). Exact keys in current config are unique, and default live trading forbids fallback matches, so this should not affect current live trades. If `allow_fallback_trading=True`, a fallback group could skip because the chosen rule is unusable even if another lower-sample usable rule exists.

6. **No AT_OPEN rules.**  
   Config has only ABOVE_OPEN/BELOW_OPEN. If `_classify_current_side()` returns `AT_OPEN` within 0.5 bps (`round_state.py` lines 81-84), rule lookup will miss. This is expected/visible as no-rule skips, not an inversion.

7. **No obvious UP/DOWN sign inversion found in code/config.**  
   Direction signs are internally consistent: positive distance => ABOVE_OPEN; settlement final > open => UP; generated usable rules have UP with positive median return and DOWN with negative median return.

## Start Here
Open `src/polymarket_round_bot/binance_client.py` lines 57-63 and 97-108 first, then `src/polymarket_round_bot/round_state.py` lines 147-245. These determine whether `current_side` means live price at entry or last closed 5m candle close, which is the main directional-state ambiguity.
