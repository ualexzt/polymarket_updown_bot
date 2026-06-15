"""Entry-mode sensitivity: compare backtest WR/PnL across different entry-price assumptions.

Live uses `entry = orderbook.ask` (real); backtest uses `entry = prob - safety_buffer`
(assumed). This script runs the same backtest engine with three different entry
formulas to see which one best matches live's empirical behavior.

Usage:
  python scripts/entry_mode_backtest.py \\
    --data data/btc_5m_500d.csv \\
    --rules config/btc_updown_state_rules_15m.json \\
    --out-dir results/entry_modes
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_round_bot.models import Side  # noqa: E402
from scripts.walk_forward_backtest import (  # noqa: E402
    Fold,
    build_rule_index,
    iter_round_starts,
    load_candles_csv,
    _build_binance_for_round,
    _build_market_for_round,
    _settle_trade,
)


# === Entry mode definitions ===

@dataclass(frozen=True)
class EntryMode:
    name: str
    description: str
    ask_spread: Decimal  # added to (prob - safety_buffer) to get entry


ENTRY_MODES: tuple[EntryMode, ...] = (
    EntryMode("assume_mid", "Default: entry = prob - safety_buffer (backtest baseline)", Decimal("0")),
    EntryMode("assume_tight_ask", "Tight ask: entry = prob - safety_buffer + 0.02", Decimal("0.02")),
    EntryMode("assume_wide_ask", "Wide ask: entry = prob - safety_buffer + 0.05", Decimal("0.05")),
    EntryMode("assume_very_wide_ask", "Very wide ask: entry = prob - safety_buffer + 0.10", Decimal("0.10")),
)


def _evaluate_state_with_entry(
    *,
    market,
    binance,
    now_utc,
    rules_index,
    min_samples,
    min_historical_probability,
    safety_buffer,
    max_entry_ask,
    ask_spread,
):
    """Same as walk_forward_backtest._evaluate_state but with an ask_spread offset."""
    from polymarket_round_bot.round_state import build_round_state
    state = build_round_state(binance, market, now_utc=now_utc)
    from scripts.walk_forward_backtest import _in_trading_window
    if not _in_trading_window(state.stage.value, state.seconds_to_expiry):
        return None

    rule, match_type = rules_index.lookup(
        stage=state.stage.value,
        current_side=state.current_side.value,
        distance_bucket=state.distance_bucket.value,
        volatility_bucket=state.volatility_bucket.value,
        pattern=state.candle_pattern,
    )
    if rule is None or not rule.usable_signal:
        return None
    if rule.samples < min_samples:
        return None
    if rule.historical_probability < min_historical_probability:
        return None
    if not rule.return_aligned:
        return None

    entry_price = rule.historical_probability - safety_buffer + ask_spread
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


def simulate_round_with_entry(
    *,
    market,
    binance,
    rules_index,
    min_samples,
    min_historical_probability,
    safety_buffer,
    max_entry_ask,
    ask_spread,
):
    """Same as walk_forward_backtest.simulate_round but with ask_spread offset."""
    trade = _evaluate_state_with_entry(
        market=market, binance=binance, now_utc=market.start_ts + timedelta(seconds=1),
        rules_index=rules_index, min_samples=min_samples,
        min_historical_probability=min_historical_probability,
        safety_buffer=safety_buffer, max_entry_ask=max_entry_ask, ask_spread=ask_spread,
    )
    if trade is not None:
        trade["entry_now_utc"] = (market.start_ts + timedelta(seconds=1)).isoformat()
        return trade
    trade = _evaluate_state_with_entry(
        market=market, binance=binance, now_utc=market.start_ts + timedelta(minutes=5, seconds=1),
        rules_index=rules_index, min_samples=min_samples,
        min_historical_probability=min_historical_probability,
        safety_buffer=safety_buffer, max_entry_ask=max_entry_ask, ask_spread=ask_spread,
    )
    if trade is not None:
        trade["entry_now_utc"] = (market.start_ts + timedelta(minutes=5, seconds=1)).isoformat()
        return trade
    return None


def simulate_fold_for_mode(
    *,
    fold: Fold,
    candles: list,
    rules_index,
    min_samples: int,
    min_historical_probability: Decimal,
    safety_buffer: Decimal,
    max_entry_ask: Decimal,
    ask_spread: Decimal,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run one fold with a specific ask_spread. Returns (trades, summary)."""
    round_starts = iter_round_starts(fold.test_start, fold.test_end)
    trades: list[dict[str, Any]] = []
    for rs in round_starts:
        try:
            binance = _build_binance_for_round(candles, rs)
        except ValueError:
            continue
        market = _build_market_for_round(rs)
        try:
            trade = simulate_round_with_entry(
                market=market, binance=binance, rules_index=rules_index,
                min_samples=min_samples, min_historical_probability=min_historical_probability,
                safety_buffer=safety_buffer, max_entry_ask=max_entry_ask, ask_spread=ask_spread,
            )
        except Exception:
            continue
        if trade is None:
            continue
        try:
            trade = _settle_trade(trade, candles)
        except Exception:
            continue
        if trade.get("won") is None:
            continue
        trade["fold_id"] = fold.fold_id
        trades.append(trade)

    n_trades = len(trades)
    n_wins = sum(1 for t in trades if t.get("won") is True)
    pnl = sum(Decimal(t["pnl"]) for t in trades)
    wr = (Decimal(n_wins) / Decimal(n_trades)) if n_trades else Decimal("0")
    avg_pnl = (pnl / Decimal(n_trades)) if n_trades else Decimal("0")
    avg_entry = (sum(Decimal(t["entry_price"]) for t in trades) / Decimal(n_trades)) if n_trades else Decimal("0")

    summary = {
        "ask_spread": str(ask_spread),
        "n_rounds": len(round_starts),
        "n_trades": n_trades,
        "n_wins": n_wins,
        "wr": str(wr),
        "pnl": str(pnl),
        "avg_pnl": str(avg_pnl),
        "avg_entry_price": str(avg_entry),
    }
    return trades, summary


def run_all_modes(
    *,
    data_csv: Path,
    rules_json: Path,
    out_dir: Path,
    n_folds: int,
    test_days: int,
    min_samples: int,
    min_historical_probability: Decimal,
    safety_buffer: Decimal,
    max_entry_ask: Decimal,
) -> dict[str, Any]:
    from polymarket_round_bot.probability_rules import load_rules

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading candles from {data_csv}...", file=sys.stderr)
    candles = load_candles_csv(data_csv)
    print(f"  loaded {len(candles)} candles", file=sys.stderr)

    print(f"Loading rules from {rules_json}...", file=sys.stderr)
    rules = load_rules(rules_json)
    print(f"  loaded {len(rules)} rules", file=sys.stderr)
    rules_index = build_rule_index(rules)

    data_start = candles[0].open_time_utc
    data_end = candles[-1].open_time_utc

    # Build folds
    from scripts.walk_forward_backtest import partition_folds
    folds = partition_folds(data_start=data_start, data_end=data_end, n_folds=n_folds, test_days=test_days)
    print(f"Running {len(folds)} folds × {len(ENTRY_MODES)} entry modes...", file=sys.stderr)

    # Precompute round_starts and BinanceStates per fold (cache for all modes)
    fold_data: list[tuple[Fold, list[datetime], dict[datetime, Any]]] = []
    for fold in folds:
        round_starts = iter_round_starts(fold.test_start, fold.test_end)
        # Cache BinanceState per round_start
        binance_cache: dict[datetime, Any] = {}
        for rs in round_starts:
            try:
                binance_cache[rs] = _build_binance_for_round(candles, rs)
            except ValueError:
                continue
        fold_data.append((fold, round_starts, binance_cache))

    results: dict[str, list[dict[str, Any]]] = {}
    aggregate: dict[str, dict[str, Any]] = {}

    for mode in ENTRY_MODES:
        print(f"\n=== Entry mode: {mode.name} (ask_spread={mode.ask_spread}) ===", file=sys.stderr)
        mode_summaries: list[dict[str, Any]] = []
        for fold, round_starts, binance_cache in fold_data:
            trades: list[dict[str, Any]] = []
            for rs in round_starts:
                binance = binance_cache.get(rs)
                if binance is None:
                    continue
                market = _build_market_for_round(rs)
                try:
                    trade = simulate_round_with_entry(
                        market=market, binance=binance, rules_index=rules_index,
                        min_samples=min_samples, min_historical_probability=min_historical_probability,
                        safety_buffer=safety_buffer, max_entry_ask=max_entry_ask, ask_spread=mode.ask_spread,
                    )
                except Exception:
                    continue
                if trade is None:
                    continue
                try:
                    trade = _settle_trade(trade, candles)
                except Exception:
                    continue
                if trade.get("won") is None:
                    continue
                trade["fold_id"] = fold.fold_id
                trades.append(trade)

            n_trades = len(trades)
            n_wins = sum(1 for t in trades if t.get("won") is True)
            pnl = sum(Decimal(t["pnl"]) for t in trades)
            wr = (Decimal(n_wins) / Decimal(n_trades)) if n_trades else Decimal("0")
            avg_pnl = (pnl / Decimal(n_trades)) if n_trades else Decimal("0")
            avg_entry = (sum(Decimal(t["entry_price"]) for t in trades) / Decimal(n_trades)) if n_trades else Decimal("0")

            summary = {
                "ask_spread": str(mode.ask_spread),
                "n_rounds": len(round_starts),
                "n_trades": n_trades,
                "n_wins": n_wins,
                "wr": str(wr),
                "pnl": str(pnl),
                "avg_pnl": str(avg_pnl),
                "avg_entry_price": str(avg_entry),
            }
            mode_summaries.append(summary)
            print(f"  fold {summary['n_trades']} trades, WR={summary['wr'][:6]}, PnL={summary['pnl'][:8]}", file=sys.stderr)
        results[mode.name] = mode_summaries

        # Aggregate
        wrs = [Decimal(s["wr"]) for s in mode_summaries if s["n_trades"] > 0]
        pnls = [Decimal(s["pnl"]) for s in mode_summaries]
        total_trades = sum(s["n_trades"] for s in mode_summaries)
        total_pnl = sum(pnls)
        wr_mean = sum(wrs) / len(wrs) if wrs else Decimal("0")
        aggregate[mode.name] = {
            "description": mode.description,
            "ask_spread": str(mode.ask_spread),
            "n_trades_total": total_trades,
            "wr_mean": str(wr_mean),
            "pnl_total": str(total_pnl),
            "folds": mode_summaries,
        }

    out_path = out_dir / "entry_modes_aggregate.json"
    out_path.write_text(json.dumps(aggregate, indent=2))

    # Print comparison
    print("\n" + "=" * 80)
    print("ENTRY MODE COMPARISON")
    print("=" * 80)
    print(f"{'Mode':<25} {'Trades':>8} {'WR':>10} {'PnL':>15} {'avg_entry':>10}")
    print("-" * 80)
    for mode in ENTRY_MODES:
        agg = aggregate[mode.name]
        wr = float(agg["wr_mean"]) * 100
        pnl = float(agg["pnl_total"])
        avg_entry = float(agg["folds"][0]["avg_entry_price"]) if agg["folds"] else 0
        print(f"{mode.name:<25} {agg['n_trades_total']:>8} {wr:>9.2f}% {pnl:>15.2f} {avg_entry:>10.4f}")
    print("=" * 80)
    print(f"\nLive empirical (post-fix, 59 trades): WR 59.3%, PnL -$1.69")
    print(f"Reference: backtest with ask_spread=0 (assume_mid) gave WR 69.9%, PnL +$224")
    print(f"Goal: find ask_spread where backtest WR matches live's 59.3%")
    print(f"\nResults written to {out_path}", file=sys.stderr)
    return aggregate


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/btc_5m_500d.csv")
    p.add_argument("--rules", default="config/btc_updown_state_rules_15m.json")
    p.add_argument("--out-dir", default="results/entry_modes/")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--test-days", type=int, default=30)
    p.add_argument("--min-samples", type=int, default=60)
    p.add_argument("--min-historical-probability", type=Decimal, default=Decimal("0.60"))
    p.add_argument("--safety-buffer", type=Decimal, default=Decimal("0.05"))
    p.add_argument("--max-entry-ask", type=Decimal, default=Decimal("0.80"))
    args = p.parse_args()

    run_all_modes(
        data_csv=Path(args.data),
        rules_json=Path(args.rules),
        out_dir=Path(args.out_dir),
        n_folds=args.folds,
        test_days=args.test_days,
        min_samples=args.min_samples,
        min_historical_probability=args.min_historical_probability,
        safety_buffer=args.safety_buffer,
        max_entry_ask=args.max_entry_ask,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
