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

    enabled_raw = data.get("enabled", False)
    if not isinstance(enabled_raw, bool):
        raise RuleWhitelistError("enabled must be a boolean")
    enabled = enabled_raw
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
    try:
        side = Side(side_raw) if side_raw is not None else None
    except ValueError as e:
        raise RuleWhitelistError(f"allowed_rules.{rule_id}.side must be UP or DOWN") from e
    max_entry_ask = _optional_decimal(raw.get("max_entry_ask"), rule_id, "max_entry_ask")
    min_edge = _optional_decimal(raw.get("min_edge"), rule_id, "min_edge")
    return RuleGate(side=side, max_entry_ask=max_entry_ask, min_edge=min_edge)


def _optional_decimal(value: object, rule_id: str, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except Exception as e:
        raise RuleWhitelistError(f"allowed_rules.{rule_id}.{field} must be decimal-compatible") from e
    if not d.is_finite():
        raise RuleWhitelistError(
            f"allowed_rules.{rule_id}.{field} must be a finite decimal, got {d}"
        )
    return d
