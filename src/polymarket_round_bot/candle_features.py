"""Candle feature computation.

Given one Candle, returns CandleFeatures with body, wicks, ratios,
flags, and a single-candle pattern name.

Combos (c0 -> c1) are formed in round_state.py.
"""
from __future__ import annotations

from decimal import Decimal

from .models import Candle, CandleFeatures, PatternName

_ZERO = Decimal("0")
_DOJI_BODY_RATIO = Decimal("0.10")
_SMALL_BODY_RATIO = Decimal("0.25")
_STRONG_BODY_RATIO = Decimal("0.65")
_LONG_WICK_RATIO = Decimal("0.45")
_POSITION_NEAR_HIGH = Decimal("0.80")
_POSITION_NEAR_LOW = Decimal("0.20")


def _safe_div(num: Decimal, den: Decimal) -> Decimal:
    if den == _ZERO:
        return _ZERO
    return num / den


def compute_candle_features(c: Candle) -> CandleFeatures:
    body = c.close - c.open
    body_abs = abs(body)
    rng = c.high - c.low
    upper_wick = c.high - max(c.open, c.close)
    lower_wick = min(c.open, c.close) - c.low
    body_to_range = _safe_div(body_abs, rng)
    upper_wick_to_range = _safe_div(upper_wick, rng)
    lower_wick_to_range = _safe_div(lower_wick, rng)
    close_position = _safe_div(c.close - c.low, rng)

    is_doji = body_to_range <= _DOJI_BODY_RATIO
    is_small_body = body_to_range <= _SMALL_BODY_RATIO
    is_strong_body = body_to_range >= _STRONG_BODY_RATIO
    has_long_upper = upper_wick_to_range >= _LONG_WICK_RATIO
    has_long_lower = lower_wick_to_range >= _LONG_WICK_RATIO

    pattern = _classify_pattern(
        body=body,
        body_to_range=body_to_range,
        upper_wick_to_range=upper_wick_to_range,
        lower_wick_to_range=lower_wick_to_range,
        close_position=close_position,
        is_doji=is_doji,
        is_small_body=is_small_body,
        is_strong_body=is_strong_body,
        has_long_upper=has_long_upper,
        has_long_lower=has_long_lower,
    )

    return CandleFeatures(
        body=body,
        body_abs=body_abs,
        range=rng,
        upper_wick=upper_wick,
        lower_wick=lower_wick,
        body_to_range=body_to_range,
        upper_wick_to_range=upper_wick_to_range,
        lower_wick_to_range=lower_wick_to_range,
        close_position_in_range=close_position,
        is_doji=is_doji,
        is_small_body=is_small_body,
        is_strong_body=is_strong_body,
        has_long_upper_wick=has_long_upper,
        has_long_lower_wick=has_long_lower,
        pattern=pattern,
    )


def _classify_pattern(
    *,
    body: Decimal,
    body_to_range: Decimal,
    upper_wick_to_range: Decimal,
    lower_wick_to_range: Decimal,
    close_position: Decimal,
    is_doji: bool,
    is_small_body: bool,
    is_strong_body: bool,
    has_long_upper: bool,
    has_long_lower: bool,
) -> str:
    """Single-candle pattern name (combo = 'c0 -> c1' built in round_state)."""
    bull = body > _ZERO
    bear = body < _ZERO
    flat_body = body == _ZERO

    if is_doji:
        if has_long_upper and has_long_lower:
            return PatternName.DOJI_TWO_LONG_WICKS.value
        if has_long_upper:
            return PatternName.DOJI_LONG_UPPER_WICK.value
        if has_long_lower:
            return PatternName.DOJI_LONG_LOWER_WICK.value
        return PatternName.FLAT.value

    if is_strong_body and bull and close_position >= _POSITION_NEAR_HIGH:
        return PatternName.STRONG_BULL_CLOSE_NEAR_HIGH.value
    if is_strong_body and bear and close_position <= _POSITION_NEAR_LOW:
        return PatternName.STRONG_BEAR_CLOSE_NEAR_LOW.value

    if bull and has_long_lower:
        return PatternName.BULL_LONG_LOWER_WICK.value
    if bull and has_long_upper:
        return PatternName.BULL_LONG_UPPER_WICK.value
    if bear and has_long_lower:
        return PatternName.BEAR_LONG_LOWER_WICK.value
    if bear and has_long_upper:
        return PatternName.BEAR_LONG_UPPER_WICK.value

    if is_strong_body and bull:
        return PatternName.NORMAL_BULL.value
    if is_strong_body and bear:
        return PatternName.NORMAL_BEAR.value

    if is_small_body and bull:
        return PatternName.WEAK_BULL.value
    if is_small_body and bear:
        return PatternName.WEAK_BEAR.value

    if flat_body:
        return PatternName.FLAT.value
    return PatternName.FLAT.value
