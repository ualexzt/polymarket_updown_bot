# Profitability Parity + Rule Whitelist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore research/live state parity, add evidence reporting, and trade only explicitly validated rules in PAPER mode.

**Architecture:** Keep runtime changes surgical. `round_state.py` becomes research-compatible, a standalone evaluator summarizes rule EV from SQLite, and `signal_engine.py` receives an optional whitelist object loaded by the CLI/runner. Default settings keep existing behavior unless whitelist mode is explicitly enabled.

**Tech Stack:** Python 3.14, Pydantic v2, SQLite, pytest, ruff, mypy strict.

---

## File Structure

- Modify `src/polymarket_round_bot/round_state.py`
  - Make distance bucket boundaries reference-compatible.
  - Make 15m current-side behavior reference-compatible near open.
  - Replace live 5m close-to-close volatility with previous completed 15m round absolute-return volatility.
- Modify `tests/test_round_state.py`
  - Add parity tests for distance boundaries, near-open classification, and previous-15m-round volatility.
- Create `src/polymarket_round_bot/rule_whitelist.py`
  - Load and validate a JSON whitelist/quarantine config.
  - Compute strictest applicable side/rule price gates.
- Create `tests/test_rule_whitelist.py`
  - Validate loader, malformed configs, quarantine, and gate resolution.
- Modify `src/polymarket_round_bot/config.py`
  - Add whitelist and side-specific gate settings, all disabled/backward-compatible by default.
- Modify `src/polymarket_round_bot/signal_engine.py`
  - Accept optional `rule_policy` parameter.
  - Enforce whitelist/quarantine before price gates.
  - Apply strictest global/side/rule `min_edge` and `max_entry_ask` gates.
- Modify `tests/test_signal_engine.py`
  - Add whitelist/quarantine/side/rule gate tests.
- Modify `src/polymarket_round_bot/runner.py`
  - Store optional rule policy on `Runner` and pass it to `build_decision`.
  - Fix persisted Binance candle age calculation.
- Modify `scripts/run_polymarket_round_paper.py`
  - Load whitelist config only when configured.
- Create `scripts/evaluate_rule_performance.py`
  - Report rule/side/stage/price-bucket performance from settlements.
- Create `tests/test_evaluate_rule_performance.py`
  - Test evaluator metrics on a tiny SQLite fixture.
- Create `config/rule_whitelist.example.json`
  - Document valid whitelist schema without enabling it in production.

---

## Task 1: Research-Compatible Round State

**Files:**
- Modify: `src/polymarket_round_bot/round_state.py`
- Test: `tests/test_round_state.py`

- [ ] **Step 1: Write failing distance boundary tests**

Add these imports/adjust existing imports in `tests/test_round_state.py`:

```python
from polymarket_round_bot.round_state import (
    build_round_state,
    _classify_distance,
)
```

Add this test near the existing distance bucket tests:

```python
def test_distance_bucket_boundaries_match_research_right_closed_bins():
    """Research used pandas.cut right-closed bins; exact boundaries stay in lower bucket."""
    cases = [
        (Decimal("0.0005"), DistanceBucket.D_0_005pct),
        (Decimal("0.0010"), DistanceBucket.D_005_010pct),
        (Decimal("0.0020"), DistanceBucket.D_010_020pct),
        (Decimal("0.0035"), DistanceBucket.D_020_035pct),
        (Decimal("0.0050"), DistanceBucket.D_035_050pct),
        (Decimal("0.0050001"), DistanceBucket.D_GT_050pct),
    ]
    for distance, expected in cases:
        assert _classify_distance(distance) == expected
        assert _classify_distance(-distance) == expected
```

- [ ] **Step 2: Run the targeted test and confirm it fails**

Run:

```bash
python -m pytest tests/test_round_state.py::test_distance_bucket_boundaries_match_research_right_closed_bins -q
```

Expected before implementation: FAIL because `_classify_distance()` uses `< upper`, assigning exact boundaries to the next bucket.

- [ ] **Step 3: Implement distance boundary parity**

In `src/polymarket_round_bot/round_state.py`, replace:

```python
def _classify_distance(distance_pct: Decimal) -> DistanceBucket:
    abs_d = abs(distance_pct)
    for upper, bucket in _DISTANCE_BUCKETS:
        if abs_d < upper:
            return bucket
    return DistanceBucket.D_GT_050pct
```

with:

```python
def _classify_distance(distance_pct: Decimal) -> DistanceBucket:
    """Classify absolute distance using research-compatible right-closed bins."""
    abs_d = abs(distance_pct)
    for upper, bucket in _DISTANCE_BUCKETS:
        if abs_d <= upper:
            return bucket
    return DistanceBucket.D_GT_050pct
```

- [ ] **Step 4: Verify distance boundary parity passes**

Run:

```bash
python -m pytest tests/test_round_state.py::test_distance_bucket_boundaries_match_research_right_closed_bins -q
```

Expected: PASS.

- [ ] **Step 5: Write failing current-side parity test for 15m near-open states**

Add this test to `tests/test_round_state.py`:

```python
def test_15m_near_open_maps_to_reference_binary_side():
    """Research had no AT_OPEN side; exact/near ties should not create untradeable 15m tuples."""
    market_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    market = _market_15m(market_start)
    candles = [
        _mk_candle(market_start, "100", "100.01", "99.99", "100.002"),
    ]
    binance = BinanceState(
        symbol="BTCUSDT",
        candles=candles,
        current_price=Decimal("100.002"),
        received_at_utc=market_start.replace(minute=6),
    )

    state = build_round_state(binance, market, now_utc=market_start.replace(minute=6))

    assert state.timeframe.value == "15m"
    assert state.current_side == CurrentSide.ABOVE_OPEN
```

If `_market_15m()` does not exist yet, add this helper beside `_market_5m()`:

```python
def _market_15m(start: datetime):
    from polymarket_round_bot.models import MarketMetadata

    return MarketMetadata(
        market_id="m1",
        condition_id="c1",
        slug=f"btc-updown-15m-{int(start.timestamp())}",
        event_slug=f"btc-updown-15m-{int(start.timestamp())}",
        question="BTC Up or Down?",
        up_token_id="up",
        down_token_id="down",
        start_ts=start,
        end_ts=start.replace(minute=start.minute + 15),
        active=True,
        closed=False,
        accepting_orders=True,
        resolved_outcome=None,
        liquidity_usd=Decimal("1000"),
        fee_rate=None,
        discovered_at_utc=start,
    )
```

- [ ] **Step 6: Run the targeted current-side test and confirm it fails**

Run:

```bash
python -m pytest tests/test_round_state.py::test_15m_near_open_maps_to_reference_binary_side -q
```

Expected before implementation: FAIL because live currently emits `CurrentSide.AT_OPEN` for `abs(distance) < 0.00005`.

- [ ] **Step 7: Implement reference-compatible current-side helper**

In `src/polymarket_round_bot/round_state.py`, replace `_classify_current_side()` with:

```python
def _classify_current_side(distance_pct: Decimal) -> CurrentSide:
    if abs(distance_pct) < _AT_OPEN_THRESHOLD:
        return CurrentSide.AT_OPEN
    return CurrentSide.ABOVE_OPEN if distance_pct > 0 else CurrentSide.BELOW_OPEN


def _classify_current_side_for_market(distance_pct: Decimal, market: MarketMetadata) -> CurrentSide:
    """Use reference-compatible binary side for calibrated 15m rules.

    Research generated only ABOVE_OPEN/BELOW_OPEN tuples. It used ABOVE when
    the observation close was greater than round open and BELOW otherwise.
    Keep AT_OPEN available for 5m/non-calibrated states, but do not emit it
    for 15m rule lookup states.
    """
    if _is_5m_market(market):
        return _classify_current_side(distance_pct)
    return CurrentSide.ABOVE_OPEN if distance_pct > Decimal("0") else CurrentSide.BELOW_OPEN
```

Then in `build_round_state()`, replace:

```python
current_side = _classify_current_side(distance_pct)
```

with:

```python
current_side = _classify_current_side_for_market(distance_pct, market)
```

- [ ] **Step 8: Verify current-side parity passes**

Run:

```bash
python -m pytest tests/test_round_state.py::test_15m_near_open_maps_to_reference_binary_side -q
```

Expected: PASS.

- [ ] **Step 9: Write failing previous-15m-round volatility test**

Add this test to `tests/test_round_state.py`:

```python
def test_volatility_uses_previous_completed_15m_round_returns():
    """Volatility must match research: mean of previous completed 15m round abs returns."""
    market_start = datetime(2024, 1, 1, 16, 0, 0, tzinfo=UTC)
    market = _market_15m(market_start)
    candles = []

    # 16 completed 15m rounds before market_start. Each round has c0/c1/c2.
    # Round return = abs(c2.close / c0.open - 1). Use 0.10% each -> VOL_NORMAL
    # with thresholds LOW <= 0.000897, NORMAL <= 0.001871.
    first_round = market_start.replace(hour=12, minute=0)
    for round_index in range(16):
        start = first_round.replace(minute=(first_round.minute + 15 * round_index) % 60,
                                    hour=first_round.hour + (first_round.minute + 15 * round_index) // 60)
        base = Decimal("100")
        close = Decimal("100.10")
        candles.extend([
            _mk_candle(start, base, "100.05", "99.95", "100.02"),
            _mk_candle(start.replace(minute=start.minute + 5), "100.02", "100.08", "99.98", "100.06"),
            _mk_candle(start.replace(minute=start.minute + 10), "100.06", "100.12", "100.00", close),
        ])

    # Current in-round c0 so build_round_state can produce AFTER_5M.
    candles.append(_mk_candle(market_start, "100", "100.2", "99.9", "100.1"))

    binance = BinanceState(
        symbol="BTCUSDT",
        candles=candles,
        current_price=Decimal("100.1"),
        received_at_utc=market_start.replace(minute=6),
    )

    state = build_round_state(binance, market, now_utc=market_start.replace(minute=6))

    assert state.prev_16_abs_return_mean == Decimal("0.001")
    assert state.volatility_bucket == VolatilityBucket.VOL_NORMAL
```

If direct `replace(minute=start.minute + 10)` creates invalid minute values in this test file, use `timedelta` instead:

```python
from datetime import timedelta
```

and compute each candle open as `start + timedelta(minutes=5)` and `start + timedelta(minutes=10)`.

- [ ] **Step 10: Run the volatility test and confirm it fails**

Run:

```bash
python -m pytest tests/test_round_state.py::test_volatility_uses_previous_completed_15m_round_returns -q
```

Expected before implementation: FAIL because current code uses 5m close-to-close returns rather than previous completed 15m round returns.

- [ ] **Step 11: Implement previous-15m-round volatility**

In `src/polymarket_round_bot/round_state.py`, add `timedelta` import:

```python
from datetime import UTC, datetime, timedelta
```

Replace `_compute_prev_volatility_mean()` with:

```python
def _compute_prev_volatility_mean(
    candles: list[Candle], *, round_start_ts: datetime
) -> Decimal | None:
    """Mean abs return of previous 16 completed 15m rounds.

    Matches polymarket_round_research_v2.py:
    round_abs_return = abs(c2.close / c0.open - 1)
    prev_16_abs_return_mean = shift(1).rolling(16).mean()
    """
    closed = [c for c in candles if c.is_closed and c.open_time_utc < round_start_ts]
    closed.sort(key=lambda c: c.open_time_utc)
    by_open = {c.open_time_utc: c for c in closed}

    returns: list[Decimal] = []
    for c0 in closed:
        c1 = by_open.get(c0.open_time_utc + timedelta(minutes=5))
        c2 = by_open.get(c0.open_time_utc + timedelta(minutes=10))
        round_end = c0.open_time_utc + timedelta(minutes=15)
        if c1 is None or c2 is None or round_end > round_start_ts:
            continue
        if c0.open == Decimal("0"):
            continue
        returns.append(abs(_safe_div(c2.close - c0.open, c0.open)))

    returns = returns[-_VOL_WINDOW:]
    if len(returns) < _VOL_WINDOW:
        return None
    return sum(returns, Decimal("0")) / Decimal(len(returns))
```

Also update `_classify_volatility()` to make threshold boundaries match research `<=` behavior:

```python
def _classify_volatility(prev_mean: Decimal | None) -> VolatilityBucket:
    if prev_mean is None:
        return VolatilityBucket.VOL_UNKNOWN
    if prev_mean <= _VOL_LOW_MAX:
        return VolatilityBucket.VOL_LOW
    if prev_mean <= _VOL_NORMAL_MAX:
        return VolatilityBucket.VOL_NORMAL
    return VolatilityBucket.VOL_HIGH
```

- [ ] **Step 12: Verify round-state tests**

Run:

```bash
python -m pytest tests/test_round_state.py -q
```

Expected: all `test_round_state.py` tests pass.

- [ ] **Step 13: Commit Task 1**

Run:

```bash
git add src/polymarket_round_bot/round_state.py tests/test_round_state.py
git commit -m "fix(state): align live buckets with research"
```

---

## Task 2: Rule Whitelist Loader and Gate Resolution

**Files:**
- Create: `src/polymarket_round_bot/rule_whitelist.py`
- Create: `tests/test_rule_whitelist.py`
- Create: `config/rule_whitelist.example.json`

- [ ] **Step 1: Write whitelist loader tests**

Create `tests/test_rule_whitelist.py`:

```python
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from polymarket_round_bot.models import Side
from polymarket_round_bot.rule_whitelist import (
    RuleGate,
    RuleWhitelist,
    RuleWhitelistError,
    load_rule_whitelist,
)


def test_load_rule_whitelist_valid_config(tmp_path):
    path = tmp_path / "whitelist.json"
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "allowed_rules": {
                    "rule_up": {"side": "UP", "max_entry_ask": "0.70", "min_edge": "0.08"}
                },
                "quarantined_rules": {
                    "rule_down": "live pnl <= -3 after 5 trades"
                },
            }
        )
    )

    whitelist = load_rule_whitelist(path)

    assert whitelist.enabled is True
    assert whitelist.is_allowed("rule_up", Side.UP) is True
    assert whitelist.is_allowed("rule_up", Side.DOWN) is False
    assert whitelist.quarantine_reason("rule_down") == "live pnl <= -3 after 5 trades"
    assert whitelist.gate_for("rule_up") == RuleGate(
        side=Side.UP,
        max_entry_ask=Decimal("0.70"),
        min_edge=Decimal("0.08"),
    )


def test_load_rule_whitelist_rejects_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{")

    with pytest.raises(RuleWhitelistError, match="malformed JSON"):
        load_rule_whitelist(path)


def test_enabled_whitelist_blocks_unknown_rule():
    whitelist = RuleWhitelist(enabled=True, allowed_rules={}, quarantined_rules={})

    assert whitelist.is_allowed("unknown_rule", Side.UP) is False


def test_disabled_whitelist_allows_unknown_non_quarantined_rule():
    whitelist = RuleWhitelist(enabled=False, allowed_rules={}, quarantined_rules={})

    assert whitelist.is_allowed("unknown_rule", Side.UP) is True


def test_quarantine_blocks_even_when_allowed():
    whitelist = RuleWhitelist(
        enabled=True,
        allowed_rules={"rule_1": RuleGate(side=None, max_entry_ask=None, min_edge=None)},
        quarantined_rules={"rule_1": "bad live pnl"},
    )

    assert whitelist.quarantine_reason("rule_1") == "bad live pnl"
    assert whitelist.is_allowed("rule_1", Side.UP) is False
```

- [ ] **Step 2: Run whitelist tests and confirm they fail**

Run:

```bash
python -m pytest tests/test_rule_whitelist.py -q
```

Expected before implementation: FAIL because `polymarket_round_bot.rule_whitelist` does not exist.

- [ ] **Step 3: Implement whitelist loader**

Create `src/polymarket_round_bot/rule_whitelist.py`:

```python
"""Rule whitelist and quarantine policy.

The policy is intentionally optional. With whitelist mode disabled, normal
rule evaluation is unchanged except quarantined rules can still be blocked if
a policy is explicitly supplied.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import Side


class RuleWhitelistError(Exception):
    """Raised when a whitelist file is missing or malformed."""


@dataclass(frozen=True)
class RuleGate:
    side: Side | None = None
    max_entry_ask: Decimal | None = None
    min_edge: Decimal | None = None


@dataclass(frozen=True)
class RuleWhitelist:
    enabled: bool
    allowed_rules: dict[str, RuleGate]
    quarantined_rules: dict[str, str]

    def quarantine_reason(self, rule_id: str | None) -> str | None:
        if rule_id is None:
            return None
        return self.quarantined_rules.get(rule_id)

    def is_allowed(self, rule_id: str | None, side: Side) -> bool:
        if rule_id is None:
            return not self.enabled
        if rule_id in self.quarantined_rules:
            return False
        if not self.enabled:
            return True
        gate = self.allowed_rules.get(rule_id)
        if gate is None:
            return False
        return gate.side is None or gate.side == side

    def gate_for(self, rule_id: str | None) -> RuleGate | None:
        if rule_id is None:
            return None
        return self.allowed_rules.get(rule_id)


EMPTY_RULE_WHITELIST = RuleWhitelist(
    enabled=False,
    allowed_rules={},
    quarantined_rules={},
)


def load_rule_whitelist(path: Path) -> RuleWhitelist:
    if not path.exists():
        raise RuleWhitelistError(f"whitelist file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuleWhitelistError(f"malformed JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise RuleWhitelistError("whitelist file must be a JSON object")

    enabled = bool(data.get("enabled", False))
    allowed_raw = data.get("allowed_rules", {})
    quarantined_raw = data.get("quarantined_rules", {})
    if not isinstance(allowed_raw, dict):
        raise RuleWhitelistError("allowed_rules must be an object")
    if not isinstance(quarantined_raw, dict):
        raise RuleWhitelistError("quarantined_rules must be an object")

    allowed: dict[str, RuleGate] = {}
    for rule_id, raw_gate in allowed_raw.items():
        if not isinstance(rule_id, str) or not rule_id:
            raise RuleWhitelistError("allowed_rules keys must be non-empty strings")
        if raw_gate is None:
            raw_gate = {}
        if not isinstance(raw_gate, dict):
            raise RuleWhitelistError(f"allowed_rules.{rule_id} must be an object")
        allowed[rule_id] = _parse_gate(raw_gate, rule_id)

    quarantined: dict[str, str] = {}
    for rule_id, reason in quarantined_raw.items():
        if not isinstance(rule_id, str) or not rule_id:
            raise RuleWhitelistError("quarantined_rules keys must be non-empty strings")
        quarantined[rule_id] = str(reason)

    return RuleWhitelist(
        enabled=enabled,
        allowed_rules=allowed,
        quarantined_rules=quarantined,
    )


def _parse_gate(raw: dict[str, Any], rule_id: str) -> RuleGate:
    side_raw = raw.get("side")
    side = Side(side_raw) if side_raw is not None else None
    max_entry_ask = _optional_decimal(raw.get("max_entry_ask"), rule_id, "max_entry_ask")
    min_edge = _optional_decimal(raw.get("min_edge"), rule_id, "min_edge")
    return RuleGate(side=side, max_entry_ask=max_entry_ask, min_edge=min_edge)


def _optional_decimal(value: object, rule_id: str, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception as e:
        raise RuleWhitelistError(f"allowed_rules.{rule_id}.{field} must be decimal-compatible") from e
```

- [ ] **Step 4: Add example whitelist config**

Create `config/rule_whitelist.example.json`:

```json
{
  "enabled": true,
  "allowed_rules": {
    "btc_15m_after_5m_above_open_d_005_010pct_vol_normal_bull_long_upper_wick": {
      "side": "UP",
      "max_entry_ask": "0.62",
      "min_edge": "0.08"
    }
  },
  "quarantined_rules": {
    "btc_15m_after_5m_below_open_d_010_020pct_vol_normal_strong_bear_close_near_low": "negative live paper PnL in June 2026 baseline"
  }
}
```

- [ ] **Step 5: Verify whitelist tests pass**

Run:

```bash
python -m pytest tests/test_rule_whitelist.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git add src/polymarket_round_bot/rule_whitelist.py tests/test_rule_whitelist.py config/rule_whitelist.example.json
git commit -m "feat(rules): add whitelist policy loader"
```

---

## Task 3: Config and Signal Engine Policy Gates

**Files:**
- Modify: `src/polymarket_round_bot/config.py`
- Modify: `src/polymarket_round_bot/signal_engine.py`
- Modify: `tests/test_signal_engine.py`

- [ ] **Step 1: Write failing signal-engine whitelist tests**

In `tests/test_signal_engine.py`, add import:

```python
from polymarket_round_bot.rule_whitelist import RuleGate, RuleWhitelist
```

Add these tests near the other rule gate tests:

```python
def test_skip_when_whitelist_enabled_and_rule_not_allowed():
    s = Settings()
    whitelist = RuleWhitelist(enabled=True, allowed_rules={}, quarantined_rules={})

    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.65"), bid=Decimal("0.62")),
        lookup=_lookup(prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        binance_received_at_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        now_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        rule_policy=whitelist,
    )

    assert decision.decision == DecisionKind.SKIP
    assert decision.reason == "rule_not_whitelisted"


def test_skip_when_rule_is_quarantined():
    s = Settings()
    whitelist = RuleWhitelist(
        enabled=False,
        allowed_rules={},
        quarantined_rules={"rule1": "bad live pnl"},
    )

    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.65"), bid=Decimal("0.62")),
        lookup=_lookup(rule_id="rule1", prob=Decimal("0.85")),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        binance_received_at_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        now_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        rule_policy=whitelist,
    )

    assert decision.decision == DecisionKind.SKIP
    assert decision.reason == "rule_quarantined:bad live pnl"


def test_side_specific_min_edge_blocks_trade():
    s = Settings(min_edge_up=Decimal("0.25"))

    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.65"), bid=Decimal("0.62")),
        lookup=_lookup(prob=Decimal("0.85"), side=Side.UP),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        binance_received_at_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        now_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )

    assert decision.decision == DecisionKind.SKIP
    assert decision.reason == "edge_below_min:0.20<0.25"


def test_rule_specific_max_entry_ask_blocks_trade():
    s = Settings()
    whitelist = RuleWhitelist(
        enabled=True,
        allowed_rules={"rule1": RuleGate(side=Side.UP, max_entry_ask=Decimal("0.60"), min_edge=None)},
        quarantined_rules={},
    )

    decision = build_decision(
        settings=s,
        state=_state(),
        market=_market(),
        orderbook=_orderbook(ask=Decimal("0.65"), bid=Decimal("0.62")),
        lookup=_lookup(rule_id="rule1", prob=Decimal("0.85"), side=Side.UP),
        risk_allowed=True,
        risk_reject_reason=None,
        open_positions_count=0,
        daily_realized_pnl=Decimal("0"),
        metadata_received_at_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        binance_received_at_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        now_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        rule_policy=whitelist,
    )

    assert decision.decision == DecisionKind.SKIP
    assert decision.reason == "ask_above_max_entry_ask:0.65>0.60"
```

If `_lookup()` does not currently accept `rule_id`, update the helper in the test file to accept it and set the `ProbabilityRule.rule_id` accordingly.

- [ ] **Step 2: Run targeted tests and confirm they fail**

Run:

```bash
python -m pytest tests/test_signal_engine.py -q
```

Expected before implementation: FAIL because `Settings.min_edge_up`, `rule_policy`, and whitelist enforcement do not exist.

- [ ] **Step 3: Add config settings**

In `src/polymarket_round_bot/config.py`, add under the value-entry thresholds:

```python
    # Optional side-specific gates. None means use the global value.
    min_edge_up: Decimal | None = Field(default=None)
    min_edge_down: Decimal | None = Field(default=None)
    max_entry_ask_up: Decimal | None = Field(default=None)
    max_entry_ask_down: Decimal | None = Field(default=None)
```

Add under paths:

```python
    rule_whitelist_path: str = Field(default="config/rule_whitelist.json")
    rule_whitelist_enabled: bool = Field(default=False)
```

Add property near `state_rules_file`:

```python
    @property
    def rule_whitelist_file(self) -> Path:
        return self.resolve("rule_whitelist_path")
```

- [ ] **Step 4: Add helper functions to signal engine**

In `src/polymarket_round_bot/signal_engine.py`, add import:

```python
from .rule_whitelist import RuleWhitelist
```

Update signature:

```python
def build_decision(
    *,
    settings: Settings,
    state: RoundState,
    market: MarketMetadata,
    orderbook: PairOrderbook,
    lookup: RuleLookupResult,
    risk_allowed: bool,
    risk_reject_reason: str | None,
    open_positions_count: int,
    daily_realized_pnl: Decimal,
    metadata_received_at_utc: datetime,
    binance_received_at_utc: datetime,
    now_utc: datetime | None = None,
    rule_policy: RuleWhitelist | None = None,
) -> SignalDecision:
```

Add these helpers before `build_decision()`:

```python
def _side_min_edge(settings: Settings, side: Side) -> Decimal:
    if side == Side.UP and settings.min_edge_up is not None:
        return settings.min_edge_up
    if side == Side.DOWN and settings.min_edge_down is not None:
        return settings.min_edge_down
    return settings.min_edge


def _side_max_entry_ask(settings: Settings, side: Side) -> Decimal:
    if side == Side.UP and settings.max_entry_ask_up is not None:
        return settings.max_entry_ask_up
    if side == Side.DOWN and settings.max_entry_ask_down is not None:
        return settings.max_entry_ask_down
    return settings.max_entry_ask


def _strictest_decimal(base: Decimal, override: Decimal | None) -> Decimal:
    if override is None:
        return base
    return max(base, override)


def _strictest_entry_cap(base: Decimal, override: Decimal | None) -> Decimal:
    if override is None:
        return base
    return min(base, override)
```

- [ ] **Step 5: Enforce whitelist after side selection and before orderbook price math**

In `build_decision()`, after:

```python
    side = _select_side_for_observation(state, lookup)
    if side is None:
        return _skip(state, market, orderbook, lookup, "no_recommended_side", settings.max_position_usd)
```

insert:

```python
    rule_id = lookup.rule.rule_id if lookup.rule else None
    if rule_policy is not None:
        quarantine_reason = rule_policy.quarantine_reason(rule_id)
        if quarantine_reason is not None:
            return _skip(
                state,
                market,
                orderbook,
                lookup,
                f"rule_quarantined:{quarantine_reason}",
                settings.max_position_usd,
                side,
                None,
            )
        if not rule_policy.is_allowed(rule_id, side):
            return _skip(
                state,
                market,
                orderbook,
                lookup,
                "rule_not_whitelisted",
                settings.max_position_usd,
                side,
                None,
            )
```

- [ ] **Step 6: Apply strictest price gates**

Replace:

```python
    safety_buffer = settings.safety_buffer
    max_buy_price = fair_price - safety_buffer
    edge_vs_ask = fair_price - best_ask  # positive = good for us
```

with:

```python
    safety_buffer = settings.safety_buffer
    max_buy_price = fair_price - safety_buffer
    edge_vs_ask = fair_price - best_ask  # positive = good for us
    min_edge_required = _side_min_edge(settings, side)
    max_entry_ask = _side_max_entry_ask(settings, side)
    if rule_policy is not None:
        gate = rule_policy.gate_for(lookup.rule.rule_id if lookup.rule else None)
        if gate is not None:
            min_edge_required = _strictest_decimal(min_edge_required, gate.min_edge)
            max_entry_ask = _strictest_entry_cap(max_entry_ask, gate.max_entry_ask)
```

Replace `settings.max_entry_ask` in the max-entry gate with `max_entry_ask`:

```python
    if best_ask > max_entry_ask:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"ask_above_max_entry_ask:{best_ask}>{max_entry_ask}",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )
```

Replace `settings.min_edge` in the edge gate with `min_edge_required`:

```python
    if edge_vs_ask < min_edge_required:
        return _skip(
            state,
            market,
            orderbook,
            lookup,
            f"edge_below_min:{edge_vs_ask}<{min_edge_required}",
            settings.max_position_usd,
            side,
            token_id,
            fair_price=fair_price,
            max_buy_price=max_buy_price,
            market_ask=best_ask,
            edge_vs_ask=edge_vs_ask,
        )
```

- [ ] **Step 7: Verify signal-engine tests**

Run:

```bash
python -m pytest tests/test_signal_engine.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

Run:

```bash
git add src/polymarket_round_bot/config.py src/polymarket_round_bot/signal_engine.py tests/test_signal_engine.py
git commit -m "feat(signal): enforce rule whitelist gates"
```

---

## Task 4: Runner and CLI Integration

**Files:**
- Modify: `src/polymarket_round_bot/runner.py`
- Modify: `scripts/run_polymarket_round_paper.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write failing runner pass-through test**

In `tests/test_runner.py`, add a focused unit test if runner mocks already exist. If existing tests patch `build_decision`, assert it receives `rule_policy`. Example pattern:

```python
def test_runner_passes_rule_policy_to_signal_engine(monkeypatch, tmp_path):
    from polymarket_round_bot.rule_whitelist import RuleWhitelist

    captured = {}
    policy = RuleWhitelist(enabled=True, allowed_rules={}, quarantined_rules={})

    def fake_build_decision(**kwargs):
        captured["rule_policy"] = kwargs.get("rule_policy")
        return _trade_decision_for_test()

    monkeypatch.setattr("polymarket_round_bot.runner.build_decision", fake_build_decision)
    runner = _runner_for_test(tmp_path, rule_policy=policy)

    runner.run_one_cycle(now_utc=_NOW)

    assert captured["rule_policy"] is policy
```

Use existing helper names in `tests/test_runner.py`; do not create a second runner fixture style if one already exists.

- [ ] **Step 2: Run runner test and confirm it fails**

Run:

```bash
python -m pytest tests/test_runner.py -q
```

Expected before implementation: FAIL because `Runner` does not accept/pass `rule_policy`.

- [ ] **Step 3: Add rule policy to Runner**

In `src/polymarket_round_bot/runner.py`, add import:

```python
from .rule_whitelist import RuleWhitelist
```

Update `Runner.__init__()` signature:

```python
        timeframe: Timeframe | None = None,
        rule_policy: RuleWhitelist | None = None,
```

Set instance field:

```python
        self._rule_policy = rule_policy
```

Pass it into `build_decision()`:

```python
            rule_policy=self._rule_policy,
```

- [ ] **Step 4: Fix persisted Binance candle age metadata**

In `_build_snapshot()` in `src/polymarket_round_bot/runner.py`, find the existing calculation that adds 300 seconds to the open-time difference. Replace that expression with:

```python
        last_candle = binance.candles[-1] if binance.candles else None
        if last_candle is not None:
            candle_close_time = last_candle.open_time_utc + timedelta(minutes=5)
            binance_age = Decimal(str(max(0, (now - candle_close_time).total_seconds())))
        else:
            binance_age = Decimal("0")
```

If `timedelta` is not imported in `runner.py`, update the import:

```python
from datetime import UTC, datetime, timedelta
```

- [ ] **Step 5: Load whitelist in CLI only when enabled**

In `scripts/run_polymarket_round_paper.py`, add import:

```python
from polymarket_round_bot.rule_whitelist import EMPTY_RULE_WHITELIST, RuleWhitelistError, load_rule_whitelist
```

After rules load and before `Runner(...)`, add:

```python
    rule_policy = EMPTY_RULE_WHITELIST
    if settings.rule_whitelist_enabled:
        try:
            rule_policy = load_rule_whitelist(settings.rule_whitelist_file)
        except RuleWhitelistError as e:
            log.error("failed_to_load_rule_whitelist path=%s err=%s", settings.rule_whitelist_file, e)
            return 2
```

Pass to runner:

```python
        rule_policy=rule_policy,
```

- [ ] **Step 6: Verify runner tests**

Run:

```bash
python -m pytest tests/test_runner.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

Run:

```bash
git add src/polymarket_round_bot/runner.py scripts/run_polymarket_round_paper.py tests/test_runner.py
git commit -m "feat(runner): wire rule whitelist policy"
```

---

## Task 5: Rule Performance Evaluator

**Files:**
- Create: `scripts/evaluate_rule_performance.py`
- Create: `tests/test_evaluate_rule_performance.py`

- [ ] **Step 1: Write evaluator tests**

Create `tests/test_evaluate_rule_performance.py`:

```python
from __future__ import annotations

from decimal import Decimal

from scripts.evaluate_rule_performance import summarize_rows


def test_summarize_rows_computes_rule_metrics():
    rows = [
        {
            "rule_id": "rule_a",
            "selected_side": "UP",
            "stage": "AFTER_5M",
            "entry_price": "0.50",
            "historical_probability_at_entry": "0.70",
            "edge_at_entry": "0.20",
            "won": 1,
            "realized_pnl_usd": "1.00",
        },
        {
            "rule_id": "rule_a",
            "selected_side": "UP",
            "stage": "AFTER_5M",
            "entry_price": "0.75",
            "historical_probability_at_entry": "0.70",
            "edge_at_entry": "-0.05",
            "won": 0,
            "realized_pnl_usd": "-1.00",
        },
    ]

    summary = summarize_rows(rows)

    assert len(summary) == 1
    item = summary[0]
    assert item["rule_id"] == "rule_a"
    assert item["n"] == 2
    assert item["wins"] == 1
    assert item["win_rate"] == Decimal("0.5")
    assert item["pnl"] == Decimal("0.00")
    assert item["avg_entry_price"] == Decimal("0.625")
    assert item["breakeven_win_rate"] == Decimal("0.625")
```

- [ ] **Step 2: Run evaluator test and confirm it fails**

Run:

```bash
python -m pytest tests/test_evaluate_rule_performance.py -q
```

Expected before implementation: FAIL because `scripts/evaluate_rule_performance.py` does not exist.

- [ ] **Step 3: Implement evaluator script**

Create `scripts/evaluate_rule_performance.py`:

```python
"""Evaluate paper rule performance from SQLite settlements.

Usage:
  python scripts/evaluate_rule_performance.py --since 2026-06-08T17:41:19
  python scripts/evaluate_rule_performance.py --min-trades 2 --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_round_bot.config import Settings


def _dec(value: object) -> Decimal:
    return Decimal(str(value))


def _avg(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def summarize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["rule_id"]), str(row["selected_side"]), str(row["stage"]))].append(row)

    out: list[dict[str, Any]] = []
    for (rule_id, side, stage), items in grouped.items():
        pnl_values = [_dec(i["realized_pnl_usd"]) for i in items]
        entry_prices = [_dec(i["entry_price"]) for i in items]
        probs = [_dec(i["historical_probability_at_entry"]) for i in items if i.get("historical_probability_at_entry") is not None]
        edges = [_dec(i["edge_at_entry"]) for i in items if i.get("edge_at_entry") is not None]
        wins = sum(1 for i in items if int(i["won"]) == 1)
        n = len(items)
        out.append(
            {
                "rule_id": rule_id,
                "side": side,
                "stage": stage,
                "n": n,
                "wins": wins,
                "win_rate": Decimal(wins) / Decimal(n),
                "pnl": sum(pnl_values, Decimal("0")),
                "avg_pnl": _avg(pnl_values),
                "avg_entry_price": _avg(entry_prices),
                "breakeven_win_rate": _avg(entry_prices),
                "avg_historical_probability": _avg(probs),
                "avg_edge": _avg(edges),
            }
        )
    out.sort(key=lambda x: (x["pnl"], x["n"]))
    return out


def fetch_rows(database: Path, since: str | None) -> list[dict[str, Any]]:
    con = sqlite3.connect(database)
    con.row_factory = sqlite3.Row
    where = "WHERE s.rule_id IS NOT NULL"
    params: list[object] = []
    if since:
        where += " AND s.resolved_at_utc >= ?"
        params.append(since)
    sql = f"""
        SELECT
            s.rule_id,
            s.selected_side,
            p.stage_at_entry AS stage,
            s.entry_price,
            s.historical_probability_at_entry,
            s.edge_at_entry,
            s.won,
            s.realized_pnl_usd
        FROM settlements s
        JOIN paper_positions p ON p.position_id = s.position_id
        {where}
    """
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=None)
    parser.add_argument("--min-trades", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    rows = fetch_rows(settings.database_file, args.since)
    summary = [r for r in summarize_rows(rows) if r["n"] >= args.min_trades]

    if args.json:
        print(json.dumps(summary, indent=2, default=_json_default))
        return 0

    print(f"=== Rule performance since={args.since or 'all'} min_trades={args.min_trades} ===")
    for r in summary:
        print(
            f"{r['pnl']:>8} n={r['n']:>3} w={r['wins']:>3} "
            f"wr={r['win_rate']:.3f} be={r['breakeven_win_rate']:.3f} "
            f"avg_entry={r['avg_entry_price']:.3f} side={r['side']:<4} stage={r['stage']:<9} {r['rule_id']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Verify evaluator tests pass**

Run:

```bash
python -m pytest tests/test_evaluate_rule_performance.py -q
```

Expected: PASS.

- [ ] **Step 5: Run evaluator against local DB if present**

Run:

```bash
python scripts/evaluate_rule_performance.py --min-trades 2
```

Expected: command exits 0. If local DB has no settlements, output header only.

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add scripts/evaluate_rule_performance.py tests/test_evaluate_rule_performance.py
git commit -m "feat(reporting): evaluate rule performance"
```

---

## Task 6: Full Verification and Deployment Prep

**Files:**
- Possibly modify: `README.md` only if commands/settings are undocumented after Tasks 1-5.

- [ ] **Step 1: Run full test suite**

Run:

```bash
python -m pytest tests -q
```

Expected: all tests pass.

- [ ] **Step 2: Run ruff**

Run:

```bash
ruff check src tests scripts
```

Expected: `All checks passed!`

- [ ] **Step 3: Run mypy**

Run:

```bash
mypy src
```

Expected: `Success: no issues found in ... source files`.

- [ ] **Step 4: Run evaluator JSON smoke test**

Run:

```bash
python scripts/evaluate_rule_performance.py --min-trades 2 --json
```

Expected: valid JSON list printed and command exits 0.

- [ ] **Step 5: Verify default runtime remains backward-compatible**

Run one local one-shot if network is available:

```bash
python scripts/run_polymarket_round_paper.py --timeframe 15m --mode paper --once --print-snapshot
```

Expected: command exits 0 and no whitelist file is required because `RULE_WHITELIST_ENABLED=false` by default.

- [ ] **Step 6: Commit documentation if changed**

If `README.md` was updated:

```bash
git add README.md
git commit -m "docs: document rule whitelist evaluation"
```

If `README.md` was not changed, do not create an empty commit.

- [ ] **Step 7: Push and deploy only after user approval**

Do not deploy automatically. Present verification output and ask for approval. Deployment command after approval:

```bash
git push origin main
ssh -i ~/.ssh/polymarket-mm-key.pem -o StrictHostKeyChecking=no -o ConnectTimeout=15 ubuntu@54.154.79.239 \
  'cd /home/ubuntu/polymarket_updown_bot && git pull --ff-only && docker compose build && docker compose up -d'
```

- [ ] **Step 8: Post-deploy paper checks**

After deploy, run:

```bash
ssh -i ~/.ssh/polymarket-mm-key.pem -o StrictHostKeyChecking=no -o ConnectTimeout=15 ubuntu@54.154.79.239 \
  'cd /home/ubuntu/polymarket_updown_bot && docker compose ps && docker logs polymarket_updown_bot 2>&1 | tail -120'
```

Expected:

- container is running;
- no Traceback;
- no whitelist load error;
- decisions continue to be persisted.

Then run evaluator on server:

```bash
ssh -i ~/.ssh/polymarket-mm-key.pem -o StrictHostKeyChecking=no -o ConnectTimeout=15 ubuntu@54.154.79.239 \
  'cd /home/ubuntu/polymarket_updown_bot && python3 scripts/evaluate_rule_performance.py --since 2026-06-08T17:41:19 --min-trades 2 | tail -40'
```

Expected: rule performance table printed.

---

## Self-Review Checklist

- Spec coverage:
  - Research/live parity: Task 1.
  - Evaluation harness: Task 5.
  - Rule whitelist/quarantine: Tasks 2-4.
  - Side/rule price gates: Task 3.
  - PAPER-only rollout: Task 6.
- Placeholder scan: no unresolved placeholder markers or deferred-work phrases.
- Type consistency:
  - `RuleWhitelist`, `RuleGate`, and `RuleWhitelistError` are defined before use.
  - `build_decision(..., rule_policy=...)` is optional and backward-compatible.
  - `Settings` side gates use `Decimal | None`, matching signal helpers.
- Risk control:
  - Defaults keep whitelist disabled.
  - Missing whitelist fails only when explicitly enabled.
  - No position size increase.
