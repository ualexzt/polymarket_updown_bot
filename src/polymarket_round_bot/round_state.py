"""Round state computation.

Aligns Binance 5m candles to the round window and produces a RoundState
with: round_open/current price, current_side, distance_bucket,
volatility_bucket, candle pattern (single or combo), and stage.

Stage assignment:
  - 15m round with c0 closed only            -> AFTER_5M
  - 15m round with c0 and c1 closed          -> AFTER_10M
  - 5m  round                                  -> CUSTOM_5M_STATE

The BinanceState already contains only CLOSED 5m candles (the in-flight
candle is dropped at fetch time). So `is_closed=True` for everything
in the list — we don't need to "promote" anything.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from .candle_features import compute_candle_features
from .models import (
    BinanceState,
    Candle,
    CurrentSide,
    DistanceBucket,
    MarketMetadata,
    RoundState,
    Stage,
    Timeframe,
    VolatilityBucket,
)

_AT_OPEN_THRESHOLD: Final[Decimal] = Decimal("0.00005")  # 0.5 bps
_VOL_WINDOW: Final[int] = 16
# Volatility bucket thresholds calibrated from research CSV:
# /home/alex/Project/poly_bot_system/out_rounds_v2/BTCUSDT_5m_180d_state_bucket_report.csv
# Distribution of avg_abs_round_return (2014 rules, 17273 rounds, 180d):
#   p10=0.000433, p25=0.000733, p33=0.000897, p50=0.001292,
#   p66=0.001871, p75=0.002561, p90=0.004769, p95=0.006564
# We use p33 / p66 as the LOW/NORMAL/HIGH split.
_VOL_LOW_MAX: Final[Decimal] = Decimal("0.000897")
_VOL_NORMAL_MAX: Final[Decimal] = Decimal("0.001871")

_DISTANCE_BUCKETS: Final[tuple[tuple[Decimal, DistanceBucket], ...]] = (
    (Decimal("0.0005"), DistanceBucket.D_0_005pct),
    (Decimal("0.0010"), DistanceBucket.D_005_010pct),
    (Decimal("0.0020"), DistanceBucket.D_010_020pct),
    (Decimal("0.0035"), DistanceBucket.D_020_035pct),
    (Decimal("0.0050"), DistanceBucket.D_035_050pct),
)

_NO_INTERNAL_CANDLES_PATTERN: Final[str] = "no_internal_candles"
_FIVE_MIN_SECONDS: Final[int] = 5 * 60


def _safe_div(num: Decimal, den: Decimal) -> Decimal:
    if den == Decimal("0"):
        return Decimal("0")
    return num / den


def _signed_distance_pct(current: Decimal, base: Decimal) -> Decimal:
    """Signed return = (current - base) / base (e.g. 0.0015 = +0.15%)."""
    num: Decimal = current - base
    den: Decimal = base
    if den == Decimal("0"):
        return Decimal("0")
    return num / den


def _classify_distance(distance_pct: Decimal) -> DistanceBucket:
    abs_d = abs(distance_pct)
    for upper, bucket in _DISTANCE_BUCKETS:
        if abs_d < upper:
            return bucket
    return DistanceBucket.D_GT_050pct


def _classify_current_side(distance_pct: Decimal) -> CurrentSide:
    if abs(distance_pct) < _AT_OPEN_THRESHOLD:
        return CurrentSide.AT_OPEN
    return CurrentSide.ABOVE_OPEN if distance_pct > 0 else CurrentSide.BELOW_OPEN


def _classify_volatility(prev_mean: Decimal | None) -> VolatilityBucket:
    if prev_mean is None:
        return VolatilityBucket.VOL_UNKNOWN
    if prev_mean < _VOL_LOW_MAX:
        return VolatilityBucket.VOL_LOW
    if prev_mean < _VOL_NORMAL_MAX:
        return VolatilityBucket.VOL_NORMAL
    return VolatilityBucket.VOL_HIGH


def _compute_prev_volatility_mean(
    candles: list[Candle], *, round_start_ts: datetime
) -> Decimal | None:
    """Mean of |close-to-close returns| over the last 16 candles that
    ended STRICTLY before the current round's start (no leakage)."""
    prior = [c for c in candles if c.is_closed and c.open_time_utc < round_start_ts]
    prior.sort(key=lambda c: c.open_time_utc)
    prior = prior[-_VOL_WINDOW:]
    if len(prior) < 2:
        return None
    rets = [
        abs(_safe_div(prior[i].close - prior[i - 1].close, prior[i - 1].close))
        for i in range(1, len(prior))
    ]
    if not rets:
        return None
    return sum(rets, Decimal("0")) / Decimal(len(rets))


def _candles_in_round(candles: list[Candle], market: MarketMetadata) -> list[Candle]:
    """5m candles whose open_time falls within [start_ts, end_ts)."""
    start = market.start_ts
    end = market.end_ts
    out = [c for c in candles if start <= c.open_time_utc < end]
    out.sort(key=lambda c: c.open_time_utc)
    return out


def _is_5m_market(market: MarketMetadata) -> bool:
    duration = (market.end_ts - market.start_ts).total_seconds()
    return duration <= _FIVE_MIN_SECONDS + 1


def _round_open_price(market: MarketMetadata, binance: BinanceState) -> Decimal:
    """Best-effort round_open_price.

    Primary: open of the first 5m candle in the round.
    Fallback: close of the most recent closed candle BEFORE the round.
    Last resort: current price.
    """
    in_round = _candles_in_round(binance.candles, market)
    if in_round:
        return in_round[0].open
    prior = [c for c in binance.candles if c.is_closed and c.open_time_utc < market.start_ts]
    prior.sort(key=lambda c: c.open_time_utc)
    if prior:
        return prior[-1].close
    return binance.current_price


def build_round_state(
    binance: BinanceState,
    market: MarketMetadata,
    *,
    now_utc: datetime | None = None,
) -> RoundState:
    """Compute RoundState for the given market at the current time."""
    now = now_utc or datetime.now(UTC)
    seconds_to_expiry = max(0, int((market.end_ts - now).total_seconds()))

    round_open = _round_open_price(market, binance)
    current = binance.current_price
    distance_pct = _signed_distance_pct(current, round_open)
    current_side = _classify_current_side(distance_pct)
    distance_bucket = _classify_distance(distance_pct)
    vol_mean = _compute_prev_volatility_mean(
        binance.candles, round_start_ts=market.start_ts
    )
    vol_bucket = _classify_volatility(vol_mean)

    if _is_5m_market(market):
        return _build_5m_state(
            round_open=round_open,
            current=current,
            distance_pct=distance_pct,
            current_side=current_side,
            distance_bucket=distance_bucket,
            vol_mean=vol_mean,
            vol_bucket=vol_bucket,
            seconds_to_expiry=seconds_to_expiry,
            market=market,
            binance=binance,
        )
    return _build_15m_state(
        round_open=round_open,
        current=current,
        distance_pct=distance_pct,
        current_side=current_side,
        distance_bucket=distance_bucket,
        vol_mean=vol_mean,
        vol_bucket=vol_bucket,
        seconds_to_expiry=seconds_to_expiry,
        market=market,
        binance=binance,
    )


def _build_15m_state(
    *,
    market: MarketMetadata,
    binance: BinanceState,
    round_open: Decimal,
    current: Decimal,
    distance_pct: Decimal,
    current_side: CurrentSide,
    distance_bucket: DistanceBucket,
    vol_mean: Decimal | None,
    vol_bucket: VolatilityBucket,
    seconds_to_expiry: int,
) -> RoundState:
    in_round = _candles_in_round(binance.candles, market)
    c0 = in_round[0] if len(in_round) >= 1 else None
    c1 = in_round[1] if len(in_round) >= 2 else None
    c2 = in_round[2] if len(in_round) >= 3 else None

    if c1 is not None and c0 is not None:
        # Both c0 and c1 closed in Binance: AFTER_10M with combo pattern
        f0 = compute_candle_features(c0)
        f1 = compute_candle_features(c1)
        pattern = f"{f0.pattern} -> {f1.pattern}"
        stage = Stage.AFTER_10M
    elif c0 is not None:
        # Only c0 closed: AFTER_5M with single pattern
        f0 = compute_candle_features(c0)
        pattern = f0.pattern
        stage = Stage.AFTER_5M
    else:
        # No in-round candle yet: c0 is the *previous* 5m candle from
        # earlier in the round window? No — the round just started and
        # no closed candle exists. Treat as AFTER_5M with a sentinel
        # pattern that will not match any rule.
        pattern = "no_closed_candle_yet"
        stage = Stage.AFTER_5M

    round_close = c2.close if c2 is not None else None

    return RoundState(
        timeframe=Timeframe.M15,
        stage=stage,
        round_open_price=round_open,
        round_close_price=round_close,
        current_btc_price=current,
        current_side=current_side,
        distance_pct=distance_pct,
        distance_bucket=distance_bucket,
        volatility_bucket=vol_bucket,
        prev_16_abs_return_mean=vol_mean,
        candle_pattern=pattern,
        pattern_combo=pattern if stage == Stage.AFTER_10M else None,
        seconds_to_expiry=seconds_to_expiry,
        c0=c0,
        c1=c1,
        c2=c2,
    )


def _build_5m_state(
    *,
    market: MarketMetadata,
    binance: BinanceState,
    round_open: Decimal,
    current: Decimal,
    distance_pct: Decimal,
    current_side: CurrentSide,
    distance_bucket: DistanceBucket,
    vol_mean: Decimal | None,
    vol_bucket: VolatilityBucket,
    seconds_to_expiry: int,
) -> RoundState:
    in_round = _candles_in_round(binance.candles, market)
    c0 = in_round[0] if in_round else None
    return RoundState(
        timeframe=Timeframe.M5,
        stage=Stage.CUSTOM_5M_STATE,
        round_open_price=round_open,
        round_close_price=c0.close if c0 is not None else None,
        current_btc_price=current,
        current_side=current_side,
        distance_pct=distance_pct,
        distance_bucket=distance_bucket,
        volatility_bucket=vol_bucket,
        prev_16_abs_return_mean=vol_mean,
        candle_pattern=_NO_INTERNAL_CANDLES_PATTERN,
        pattern_combo=None,
        seconds_to_expiry=seconds_to_expiry,
        c0=c0,
        c1=None,
        c2=None,
    )
