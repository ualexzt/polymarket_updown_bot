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


def test_load_rule_whitelist_rejects_non_boolean_enabled(tmp_path):
    path = tmp_path / "bad-enabled.json"
    path.write_text(json.dumps({"enabled": "false", "allowed_rules": {}, "quarantined_rules": {}}))

    with pytest.raises(RuleWhitelistError, match="enabled must be a boolean"):
        load_rule_whitelist(path)


def test_load_rule_whitelist_rejects_invalid_side(tmp_path):
    path = tmp_path / "bad-side.json"
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "allowed_rules": {"rule_bad": {"side": "MAYBE"}},
                "quarantined_rules": {},
            }
        )
    )

    with pytest.raises(RuleWhitelistError, match="allowed_rules.rule_bad.side"):
        load_rule_whitelist(path)


def test_load_rule_whitelist_rejects_non_finite_min_edge(tmp_path):
    path = tmp_path / "nan-min-edge.json"
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "allowed_rules": {"rule_a": {"min_edge": "NaN"}},
                "quarantined_rules": {},
            }
        )
    )

    with pytest.raises(RuleWhitelistError, match="finite"):
        load_rule_whitelist(path)


def test_load_rule_whitelist_rejects_infinity_min_edge(tmp_path):
    path = tmp_path / "inf-min-edge.json"
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "allowed_rules": {"rule_a": {"min_edge": "Infinity"}},
                "quarantined_rules": {},
            }
        )
    )

    with pytest.raises(RuleWhitelistError, match="finite"):
        load_rule_whitelist(path)


def test_load_rule_whitelist_rejects_negative_infinity_max_entry_ask(tmp_path):
    path = tmp_path / "neginf-max-ask.json"
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "allowed_rules": {"rule_a": {"max_entry_ask": "-Infinity"}},
                "quarantined_rules": {},
            }
        )
    )

    with pytest.raises(RuleWhitelistError, match="finite"):
        load_rule_whitelist(path)


def test_load_rule_whitelist_rejects_overflow_to_infinity(tmp_path):
    path = tmp_path / "overflow.json"
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "allowed_rules": {"rule_a": {"min_edge": 1e1000}},
                "quarantined_rules": {},
            }
        )
    )

    with pytest.raises(RuleWhitelistError, match="finite"):
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
