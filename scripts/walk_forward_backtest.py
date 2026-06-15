"""Walk-forward backtest: replay historical Binance 5m candles through live rule-lookup.

Usage:
  python scripts/walk_forward_backtest.py --data data/btc_5m_500d.csv \\
    --rules config/btc_updown_state_rules_15m.json --out-dir results/
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_round_bot.models import BinanceState, Candle, MarketMetadata, Side  # noqa: E402, F401


# === Data loading ===

def load_candles_csv(path: Path) -> list[Candle]:
    """Load candles from the CSV written by fetch_binance_history.py."""
    candles: list[Candle] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append(Candle(
                open_time_utc=datetime.fromisoformat(row["open_time_utc"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                is_closed=True,
            ))
    candles.sort(key=lambda c: c.open_time_utc)
    return candles


# === Fold partitioning ===

@dataclass(frozen=True)
class Fold:
    fold_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def partition_folds(
    *,
    data_start: datetime,
    data_end: datetime,
    n_folds: int,
    test_days: int,
) -> list[Fold]:
    """Partition [data_start, data_end] into n_folds rolling test windows.

    Test windows are contiguous (not rolling): test_0 = [data_start, data_start+test_days),
    test_1 = [data_start+test_days, data_start+2*test_days), etc. Each fold's train window
    is [data_start, test_start] (cumulative).
    """
    folds: list[Fold] = []
    for i in range(n_folds):
        test_start = data_start + timedelta(days=i * test_days)
        test_end = test_start + timedelta(days=test_days)
        if test_end > data_end:
            test_end = data_end
        folds.append(Fold(
            fold_id=i,
            train_start=data_start,
            train_end=test_start,
            test_start=test_start,
            test_end=test_end,
        ))
        if test_end == data_end:
            break
    return folds


# === Rule index (delegates to live probability_rules) ===

class _LiveRuleIndexAdapter:
    """Adapter exposing the same .lookup(...) signature as the test's
    build_rule_index return value, backed by live ProbabilityRules.
    """

    def __init__(self, rules_index):  # noqa: ANN001 - duck-typed
        self._index = rules_index

    def lookup(self, *, stage, current_side, distance_bucket, volatility_bucket, pattern):  # noqa: ANN001
        from polymarket_round_bot.probability_rules import (  # noqa: PLC0415
            CurrentSide as CS, DistanceBucket as DB, Stage as ST, VolatilityBucket as VB,
        )
        rule, match_type = self._index._index.lookup(  # noqa: SLF001 - test adapter
            stage=ST(stage), current_side=CS(current_side),
            distance_bucket=DB(distance_bucket), volatility_bucket=VB(volatility_bucket),
            pattern=pattern,
        )
        return rule, match_type


def build_rule_index(rules):  # noqa: ANN001
    """Wrap a list[ProbabilityRule] into an object with .lookup(stage, current_side, distance_bucket, volatility_bucket, pattern) -> (rule, match_type)."""
    from polymarket_round_bot.probability_rules import ProbabilityRules  # noqa: PLC0415
    pr = ProbabilityRules(rules)
    return _LiveRuleIndexAdapter(pr)


# === CLI (placeholder; full simulation in Task 4-6) ===

# === Fold simulation ===

def iter_round_starts(data_start: datetime, data_end: datetime) -> list[datetime]:
    """Yield 15m round start times in [data_start, data_end), aligned to UTC quarter-hour."""
    # Snap data_start up to the next quarter-hour (round up, not down)
    minute = data_start.minute
    second = data_start.second
    micro = data_start.microsecond
    if minute % 15 != 0 or second > 0 or micro > 0:
        qh = ((minute // 15) + 1) * 15
        if qh >= 60:
            # overflow to next hour
            data_start = data_start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            data_start = data_start.replace(minute=qh, second=0, microsecond=0)
    starts: list[datetime] = []
    t = data_start
    while t + timedelta(minutes=15) <= data_end:
        starts.append(t)
        t = t + timedelta(minutes=15)
    return starts


def _build_binance_for_round(candles: list[Candle], round_start: datetime) -> BinanceState:
    """Build a BinanceState from candles with open_time_utc < round_start.

    Cap at the most recent 200 candles for performance.
    """
    closed = [c for c in candles if c.open_time_utc < round_start]
    closed.sort(key=lambda c: c.open_time_utc)
    closed = closed[-200:]
    if not closed:
        raise ValueError(f"no candles before {round_start}")
    return BinanceState(
        symbol="BTCUSDT",
        candles=closed,
        current_price=closed[-1].close,
        received_at_utc=round_start,
    )


def _settle_trade(trade: dict[str, Any], candles: list[Candle]) -> dict[str, Any]:
    """Settle a recorded trade by finding the c2 candle and computing PnL."""
    round_start = trade["round_start_ts"]
    if isinstance(round_start, str):
        round_start = datetime.fromisoformat(round_start)
    c2_time = round_start + timedelta(minutes=10)
    c2 = next((c for c in candles if c.open_time_utc == c2_time), None)
    if c2 is None:
        # Round didn't fully close in our data
        trade["won"] = None
        trade["pnl"] = None
        return trade
    settlement = settle_round(
        round_open=Decimal(trade["round_open_price"]),
        round_close=c2.close,
        recommended_side=Side(trade["recommended_side"]),
        entry_price=Decimal(trade["entry_price"]),
    )
    trade["won"] = settlement["won"]
    trade["pnl"] = str(settlement["pnl"])
    trade["round_close_price"] = settlement["round_close"]
    return trade


def simulate_fold(
    *,
    fold: Fold,
    candles: list[Candle],
    rules_index,
    min_samples: int,
    min_historical_probability: Decimal,
    safety_buffer: Decimal,
    max_entry_ask: Decimal,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the backtest for one fold; return (trades, summary)."""
    round_starts = iter_round_starts(fold.test_start, fold.test_end)
    trades: list[dict[str, Any]] = []
    for rs in round_starts:
        try:
            binance = _build_binance_for_round(candles, rs)
        except ValueError:
            continue  # skip rounds at the very start of the data
        market = _build_market_for_round(rs)
        try:
            trade = simulate_round(
                market=market, binance=binance, rules_index=rules_index,
                min_samples=min_samples, min_historical_probability=min_historical_probability,
                safety_buffer=safety_buffer, max_entry_ask=max_entry_ask,
            )
        except Exception:
            continue  # don't let a bad round kill the fold
        if trade is None:
            continue
        # Find c0 close to set round_open (already in trade), then settle
        try:
            trade = _settle_trade(trade, candles)
        except Exception:
            continue
        if trade.get("won") is None:
            continue  # round didn't close in our data
        trade["fold_id"] = fold.fold_id
        trades.append(trade)

    n_trades = len(trades)
    n_wins = sum(1 for t in trades if t.get("won") is True)
    pnl = sum(Decimal(t["pnl"]) for t in trades)
    wr = (Decimal(n_wins) / Decimal(n_trades)) if n_trades else Decimal("0")
    avg_pnl = (pnl / Decimal(n_trades)) if n_trades else Decimal("0")
    avg_entry = (sum(Decimal(t["entry_price"]) for t in trades) / Decimal(n_trades)) if n_trades else Decimal("0")
    n_by_stage: dict[str, int] = {}
    n_by_side: dict[str, int] = {}
    for t in trades:
        n_by_stage[t["stage"]] = n_by_stage.get(t["stage"], 0) + 1
        n_by_side[t["recommended_side"]] = n_by_side.get(t["recommended_side"], 0) + 1

    summary = {
        "fold_id": fold.fold_id,
        "train_start": fold.train_start.isoformat(),
        "train_end": fold.train_end.isoformat(),
        "test_start": fold.test_start.isoformat(),
        "test_end": fold.test_end.isoformat(),
        "n_rounds": len(round_starts),
        "n_trades": n_trades,
        "n_wins": n_wins,
        "wr": str(wr),
        "pnl": str(pnl),
        "avg_pnl": str(avg_pnl),
        "avg_entry_price": str(avg_entry),
        "n_by_stage": n_by_stage,
        "n_by_side": n_by_side,
    }
    return trades, summary

# === Per-round simulation ===

_TRADING_WINDOWS: dict = {
    "AFTER_5M": (300, 600),    # 5m to 10m elapsed; 600s to 300s remaining
    "AFTER_10M": (60, 300),    # 10m to 15m elapsed; 300s to 60s remaining
    "CUSTOM_5M_STATE": (0, 300),
}


def _in_trading_window(stage_str: str, seconds_to_expiry: int) -> bool:
    if stage_str not in _TRADING_WINDOWS:
        return False
    lo, hi = _TRADING_WINDOWS[stage_str]
    return lo <= seconds_to_expiry <= hi


def _build_market_for_round(start_ts: datetime) -> MarketMetadata:
    """Synthesize a 15m market metadata for a backtest round."""
    return MarketMetadata(
        market_id=f"backtest-{int(start_ts.timestamp())}",
        condition_id="backtest",
        question="backtest",
        slug=f"btc-updown-15m-{int(start_ts.timestamp()) // 900 * 900}",
        up_token_id="backtest-up",
        down_token_id="backtest-down",
        outcomes=["Up", "Down"],
        start_ts=start_ts,
        end_ts=start_ts + timedelta(minutes=15),
        active=True,
        closed=False,
        accepting_orders=True,
    )


def _evaluate_state(
    *,
    market: MarketMetadata,
    binance: BinanceState,
    now_utc: datetime,
    rules_index,
    min_samples: int,
    min_historical_probability: Decimal,
    safety_buffer: Decimal,
    max_entry_ask: Decimal,
) -> dict[str, Any] | None:
    """Build state, lookup rule, apply filters; return a trade dict or None."""
    from polymarket_round_bot.round_state import build_round_state  # noqa: PLC0415

    state = build_round_state(binance, market, now_utc=now_utc)
    if not _in_trading_window(state.stage.value, state.seconds_to_expiry):
        return None

    rule, match_type = rules_index.lookup(
        stage=state.stage.value,
        current_side=state.current_side.value,
        distance_bucket=state.distance_bucket.value,
        volatility_bucket=state.volatility_bucket.value,
        pattern=state.candle_pattern,
    )
    if rule is None:
        return None
    if not rule.usable_signal:
        return None
    if rule.samples < min_samples:
        return None
    if rule.historical_probability < min_historical_probability:
        return None
    if not rule.return_aligned:
        return None

    entry_price = rule.historical_probability - safety_buffer
    if entry_price <= Decimal("0") or entry_price > max_entry_ask:
        return None

    return {
        "market_slug": market.slug,
        "round_start_ts": market.start_ts,
        "round_end_ts": market.end_ts,
        "stage": state.stage.value,
        "current_side": state.current_side.value,
        "distance_bucket": state.distance_bucket.value,
        "volatility_bucket": state.volatility_bucket.value,
        "pattern": state.candle_pattern,
        "rule_id": rule.rule_id,
        "recommended_side": rule.recommended_side.value,
        "historical_probability": str(rule.historical_probability),
        "samples": rule.samples,
        "entry_price": str(entry_price),
        "round_open_price": str(state.round_open_price),
        "current_btc_price": str(state.current_btc_price),
    }


def simulate_round(
    *,
    market: MarketMetadata,
    binance: BinanceState,
    rules_index,
    min_samples: int,
    min_historical_probability: Decimal,
    safety_buffer: Decimal,
    max_entry_ask: Decimal,
) -> dict[str, Any] | None:
    """Try both AFTER_5M and AFTER_10M states; return the first trade found, or None.

    Single-trade-per-round invariant: at most one trade per round.
    """
    # AFTER_5M: 1 second after round start
    trade = _evaluate_state(
        market=market, binance=binance, now_utc=market.start_ts + timedelta(seconds=1),
        rules_index=rules_index, min_samples=min_samples,
        min_historical_probability=min_historical_probability,
        safety_buffer=safety_buffer, max_entry_ask=max_entry_ask,
    )
    if trade is not None:
        trade["entry_now_utc"] = (market.start_ts + timedelta(seconds=1)).isoformat()
        return trade
    # AFTER_10M: 5 min + 1 second after round start
    trade = _evaluate_state(
        market=market, binance=binance, now_utc=market.start_ts + timedelta(minutes=5, seconds=1),
        rules_index=rules_index, min_samples=min_samples,
        min_historical_probability=min_historical_probability,
        safety_buffer=safety_buffer, max_entry_ask=max_entry_ask,
    )
    if trade is not None:
        trade["entry_now_utc"] = (market.start_ts + timedelta(minutes=5, seconds=1)).isoformat()
        return trade
    return None


def settle_round(
    *,
    round_open: Decimal,
    round_close: Decimal,
    recommended_side: Side,
    entry_price: Decimal,
) -> dict[str, Any]:
    """Compute win/loss and PnL for a single counterfactual trade."""
    up_wins = round_close > round_open
    won = (recommended_side == Side.UP and up_wins) or (recommended_side == Side.DOWN and not up_wins)
    pnl = (Decimal("1") - entry_price) if won else -entry_price
    return {"won": won, "pnl": pnl, "round_close": str(round_close), "round_open": str(round_open)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--rules", required=True)
    p.add_argument("--out-dir", default="results/")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--test-days", type=int, default=30)
    args = p.parse_args()
    print(f"[stub] would load {args.data} and {args.rules}, write to {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
