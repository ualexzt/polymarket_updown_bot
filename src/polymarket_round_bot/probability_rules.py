"""Probability rules loader + lookup.

The rules file is generated from research CSV by scripts/build_state_rules.py.
Each rule encodes historical UP/DOWN outcome probability for a specific
state-bucket combination (stage + current_side + distance_bucket +
volatility_bucket + pattern).

Lookup hierarchy:
  1. Exact  : stage + current_side + distance_bucket + volatility_bucket + pattern
  2. Fallback (no volatility): stage + current_side + distance_bucket + pattern
  3. Fallback (no pattern):   stage + current_side + distance_bucket
  4. NO_MATCH (no rule for this state)

No-trade conditions (always enforced):
  - samples < MIN_SAMPLES
  - historical_probability < MIN_HISTORICAL_PROBABILITY
  - return_aligned != true
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from .models import (
    CurrentSide,
    DistanceBucket,
    ProbabilityRule,
    RuleLookupResult,
    RuleMatchType,
    Side,
    Stage,
    VolatilityBucket,
)


class RulesError(Exception):
    """Raised when rules file is missing or malformed."""


def load_rules(path: Path) -> list[ProbabilityRule]:
    """Load a JSON rules file and return a list of ProbabilityRule."""
    if not path.exists():
        raise RulesError(f"rules file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise RulesError(f"malformed JSON in {path}: {e}") from e
    if not isinstance(data, list):
        raise RulesError(f"rules file must be a JSON array, got {type(data).__name__}")
    rules: list[ProbabilityRule] = []
    for i, entry in enumerate(data):
        try:
            rules.append(_parse_rule(entry))
        except (KeyError, ValueError) as e:
            raise RulesError(f"rule #{i} invalid: {e}") from e
    return rules


def _parse_rule(entry: dict[str, object]) -> ProbabilityRule:
    rule_id = str(entry["rule_id"])
    stage = Stage(entry["stage"])
    current_side = CurrentSide(entry["current_side"])
    distance_bucket = DistanceBucket(entry["distance_bucket"])
    volatility_bucket = VolatilityBucket(entry["volatility_bucket"])
    pattern = str(entry["pattern"])
    recommended_side = Side(entry["recommended_side"])
    historical_probability = Decimal(str(entry["historical_probability"]))
    samples = int(str(entry["samples"]))
    median_round_return = Decimal(str(entry.get("median_round_return", "0")))
    return_aligned = bool(entry.get("return_aligned", True))
    usable_signal = bool(entry.get("usable_signal", True))
    # Always keep only the fields ProbabilityRule needs (it doesn't
    # store usable_signal; we filter at lookup time).
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
        median_round_return=median_round_return,
        return_aligned=return_aligned,
        usable_signal=usable_signal,
    )


# === Lookup index ===

class _Index:
    """In-memory rules index supporting tiered fallback lookup."""

    def __init__(self, rules: Iterable[ProbabilityRule]) -> None:
        self._exact: dict[tuple[Stage, CurrentSide, DistanceBucket, VolatilityBucket, str], list[ProbabilityRule]] = {}
        self._no_vol: dict[tuple[Stage, CurrentSide, DistanceBucket, str], list[ProbabilityRule]] = {}
        self._no_pattern: dict[tuple[Stage, CurrentSide, DistanceBucket], list[ProbabilityRule]] = {}
        for r in rules:
            key_exact = (r.stage, r.current_side, r.distance_bucket, r.volatility_bucket, r.pattern)
            key_no_vol = (r.stage, r.current_side, r.distance_bucket, r.pattern)
            key_no_pattern = (r.stage, r.current_side, r.distance_bucket)
            self._exact.setdefault(key_exact, []).append(r)
            self._no_vol.setdefault(key_no_vol, []).append(r)
            self._no_pattern.setdefault(key_no_pattern, []).append(r)

    def lookup(
        self,
        *,
        stage: Stage,
        current_side: CurrentSide,
        distance_bucket: DistanceBucket,
        volatility_bucket: VolatilityBucket,
        pattern: str,
    ) -> tuple[ProbabilityRule | None, RuleMatchType]:
        key: tuple[Stage, CurrentSide, DistanceBucket, VolatilityBucket, str] = (stage, current_side, distance_bucket, volatility_bucket, pattern)
        if key in self._exact:
            return _best(self._exact[key]), RuleMatchType.EXACT
        key_no_vol: tuple[Stage, CurrentSide, DistanceBucket, str] = (stage, current_side, distance_bucket, pattern)
        if key_no_vol in self._no_vol:
            return _best(self._no_vol[key_no_vol]), RuleMatchType.FALLBACK_NO_VOL
        key_no_pattern: tuple[Stage, CurrentSide, DistanceBucket] = (stage, current_side, distance_bucket)
        if key_no_pattern in self._no_pattern:
            return _best(self._no_pattern[key_no_pattern]), RuleMatchType.FALLBACK_NO_PATTERN
        return None, RuleMatchType.NO_MATCH


def _best(rules: list[ProbabilityRule]) -> ProbabilityRule:
    """Pick the rule with the highest samples; ties broken by higher probability."""
    return max(rules, key=lambda r: (r.samples, r.historical_probability))


class ProbabilityRules:
    """High-level rules accessor with no-trade filtering."""

    def __init__(self, rules: list[ProbabilityRule]) -> None:
        self.rules = rules
        self._index = _Index(rules)

    @classmethod
    def from_file(cls, path: Path) -> ProbabilityRules:
        return cls(load_rules(path))

    def lookup(
        self,
        *,
        stage: Stage,
        current_side: CurrentSide,
        distance_bucket: DistanceBucket,
        volatility_bucket: VolatilityBucket,
        pattern: str,
        min_samples: int,
        min_historical_probability: Decimal,
    ) -> RuleLookupResult:
        rule, match_type = self._index.lookup(
            stage=stage,
            current_side=current_side,
            distance_bucket=distance_bucket,
            volatility_bucket=volatility_bucket,
            pattern=pattern,
        )

        no_trade_reasons: list[str] = []
        if rule is None:
            no_trade_reasons.append("no_rule_for_state")
            return RuleLookupResult(
                rule=None,
                match_type=match_type,
                historical_probability=None,
                recommended_side=None,
                samples=0,
                no_trade_reasons=no_trade_reasons,
            )

        if rule.samples < min_samples:
            no_trade_reasons.append(
                f"samples_below_threshold:{rule.samples}<{min_samples}"
            )
        if rule.historical_probability < min_historical_probability:
            no_trade_reasons.append(
                f"probability_below_threshold:{rule.historical_probability}<{min_historical_probability}"
            )
        if not rule.return_aligned:
            no_trade_reasons.append("return_not_aligned")

        return RuleLookupResult(
            rule=rule,
            match_type=match_type,
            historical_probability=rule.historical_probability,
            recommended_side=rule.recommended_side,
            samples=rule.samples,
            no_trade_reasons=no_trade_reasons,
        )


# === Rule ID generation (used by build script) ===

def slugify_for_rule_id(s: str) -> str:
    """Make a string safe for a rule_id: lowercase, no hyphens, no '->'."""
    s = s.lower().strip()
    s = s.replace("->", "_").replace("-", "_").replace(" ", "_")
    s = re.sub(r"[^a-z0-9_]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def build_rule_id(
    *,
    asset: str,
    timeframe: str,
    stage: Stage,
    current_side: CurrentSide,
    distance_bucket: DistanceBucket,
    volatility_bucket: VolatilityBucket,
    pattern: str,
) -> str:
    return "_".join(
        [
            asset.lower(),
            timeframe.lower(),
            slugify_for_rule_id(stage.value),
            slugify_for_rule_id(current_side.value),
            slugify_for_rule_id(distance_bucket.value),
            slugify_for_rule_id(volatility_bucket.value),
            slugify_for_rule_id(pattern),
        ]
    )
