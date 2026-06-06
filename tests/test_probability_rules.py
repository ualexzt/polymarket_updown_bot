"""Tests for probability rules lookup and filtering."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import (
    CurrentSide,
    DistanceBucket,
    ProbabilityRule,
    RuleMatchType,
    Side,
    Stage,
    VolatilityBucket,
)
from polymarket_round_bot.probability_rules import ProbabilityRules, build_rule_id, load_rules


def _mk_rule(
    *,
    rule_id: str = "r1",
    stage: Stage = Stage.AFTER_10M,
    current_side: CurrentSide = CurrentSide.ABOVE_OPEN,
    distance_bucket: DistanceBucket = DistanceBucket.D_010_020pct,
    volatility_bucket: VolatilityBucket = VolatilityBucket.VOL_LOW,
    pattern: str = "normal_bull -> strong_bull_close_near_high",
    recommended_side: Side = Side.UP,
    historical_probability: Decimal = Decimal("0.85"),
    samples: int = 100,
    return_aligned: bool = True,
    usable_signal: bool = True,
) -> ProbabilityRule:
    return ProbabilityRule(
        rule_id=rule_id,
        stage=stage,
        current_side=current_side,
        distance_bucket=distance_bucket,
        volatility_bucket=volatility_bucket,
        pattern=pattern,
        recommended_side=recommended_side,
        historical_probability=historical_probability,
        samples=samples,
        median_round_return=Decimal("0.001"),
        return_aligned=return_aligned,
        usable_signal=usable_signal,
    )


def test_exact_rule_match():
    rule = _mk_rule()
    pr = ProbabilityRules([rule])
    res = pr.lookup(
        stage=Stage.AFTER_10M,
        current_side=CurrentSide.ABOVE_OPEN,
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="normal_bull -> strong_bull_close_near_high",
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
    )
    assert res.match_type == RuleMatchType.EXACT
    assert res.rule is not None
    assert res.rule.rule_id == "r1"
    assert res.no_trade_reasons == []


def test_fallback_no_volatility():
    rule = _mk_rule(volatility_bucket=VolatilityBucket.VOL_LOW)
    pr = ProbabilityRules([rule])
    res = pr.lookup(
        stage=Stage.AFTER_10M,
        current_side=CurrentSide.ABOVE_OPEN,
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_NORMAL,  # different
        pattern="normal_bull -> strong_bull_close_near_high",
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
    )
    # No exact vol match, but pattern+stage+side+distance match
    assert res.match_type == RuleMatchType.FALLBACK_NO_VOL
    assert res.rule is not None


def test_fallback_no_pattern():
    rule = _mk_rule()
    pr = ProbabilityRules([rule])
    res = pr.lookup(
        stage=Stage.AFTER_10M,
        current_side=CurrentSide.ABOVE_OPEN,
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="UNKNOWN_PATTERN",  # not in any rule
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
    )
    assert res.match_type == RuleMatchType.FALLBACK_NO_PATTERN


def test_no_match():
    pr = ProbabilityRules([_mk_rule()])
    res = pr.lookup(
        stage=Stage.AFTER_5M,  # different
        current_side=CurrentSide.BELOW_OPEN,  # different
        distance_bucket=DistanceBucket.D_GT_050pct,  # different
        volatility_bucket=VolatilityBucket.VOL_HIGH,
        pattern="anything",
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
    )
    assert res.match_type == RuleMatchType.NO_MATCH
    assert "no_rule_for_state" in res.no_trade_reasons


def test_no_trade_below_samples_threshold():
    rule = _mk_rule(samples=10)
    pr = ProbabilityRules([rule])
    res = pr.lookup(
        stage=Stage.AFTER_10M,
        current_side=CurrentSide.ABOVE_OPEN,
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="normal_bull -> strong_bull_close_near_high",
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
    )
    assert res.match_type == RuleMatchType.EXACT
    assert any("samples_below_threshold" in r for r in res.no_trade_reasons)


def test_no_trade_below_probability_threshold():
    rule = _mk_rule(historical_probability=Decimal("0.50"))
    pr = ProbabilityRules([rule])
    res = pr.lookup(
        stage=Stage.AFTER_10M,
        current_side=CurrentSide.ABOVE_OPEN,
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="normal_bull -> strong_bull_close_near_high",
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
    )
    assert any("probability_below_threshold" in r for r in res.no_trade_reasons)


def test_no_trade_when_return_not_aligned():
    rule = _mk_rule(return_aligned=False)
    pr = ProbabilityRules([rule])
    res = pr.lookup(
        stage=Stage.AFTER_10M,
        current_side=CurrentSide.ABOVE_OPEN,
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="normal_bull -> strong_bull_close_near_high",
        min_samples=60,
        min_historical_probability=Decimal("0.60"),
    )
    assert "return_not_aligned" in res.no_trade_reasons


def test_load_rules_from_generated_file():
    """Verify we can load the rules file that build_state_rules.py wrote."""
    rules_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "btc_updown_state_rules_15m.json"
    )
    if not rules_path.exists():
        pytest.skip("rules file not built; run scripts/build_state_rules.py first")
    rules = load_rules(rules_path)
    assert len(rules) > 0
    # All rules should have the expected fields
    for r in rules[:5]:
        assert r.rule_id.startswith("btc_15m_")
        assert r.samples > 0
        assert Decimal("0") <= r.historical_probability <= Decimal("1")


def test_build_rule_id_format():
    rid = build_rule_id(
        asset="BTC",
        timeframe="15m",
        stage=Stage.AFTER_5M,
        current_side=CurrentSide.ABOVE_OPEN,
        distance_bucket=DistanceBucket.D_005_010pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        pattern="normal_bull",
    )
    assert rid.startswith("btc_15m_after_5m_above_open_d_005_010pct_vol_low_")
    assert "normal_bull" in rid
