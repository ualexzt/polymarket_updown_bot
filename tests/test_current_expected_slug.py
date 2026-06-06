"""Tests for scripts/run_polymarket_round_paper.py.

The script's _current_expected_slug helper is imported and tested
directly. This catches a regression discovered on 2026-06-06: the
function crashed with `AttributeError: 'datetime.timezone' object has
no attribute 'localize'` when UTC was the stdlib
`datetime.timezone.utc` (Python 3.11+), not pytz.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Load the script as a module (it's a script, not a package).
_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "run_polymarket_round_paper.py"
)
_spec = importlib.util.spec_from_file_location("run_polymarket_round_paper", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["run_polymarket_round_paper"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]


def test_current_expected_slug_15m_is_floor_to_15m_boundary():
    """The returned timestamp must be floor(now / 900) * 900."""
    from polymarket_round_bot.models import Timeframe

    before = int(datetime.now(UTC).timestamp())
    slug = _mod._current_expected_slug(Timeframe.M15)
    after = int(datetime.now(UTC).timestamp())

    m = re.fullmatch(r"btc-updown-15m-(\d+)", slug)
    assert m, f"unexpected slug format: {slug!r}"
    ts = int(m.group(1))
    assert ts % (15 * 60) == 0, f"slug ts {ts} is not a 15m boundary"
    # The boundary must be <= now and > now - 15min.
    assert before - 15 * 60 <= ts <= after


def test_current_expected_slug_5m_is_floor_to_5m_boundary():
    """Same for 5m markets."""
    from polymarket_round_bot.models import Timeframe

    before = int(datetime.now(UTC).timestamp())
    slug = _mod._current_expected_slug(Timeframe.M5)
    after = int(datetime.now(UTC).timestamp())

    m = re.fullmatch(r"btc-updown-5m-(\d+)", slug)
    assert m, f"unexpected slug format: {slug!r}"
    ts = int(m.group(1))
    assert ts % (5 * 60) == 0, f"slug ts {ts} is not a 5m boundary"
    assert before - 5 * 60 <= ts <= after


def test_current_expected_slug_does_not_use_pytz_localize():
    """Regression guard: the function must not call UTC.localize(...).

    `datetime.timezone.utc` (the stdlib UTC since Python 3.11) has no
    `localize` method. This was a real production bug: continuous
    mode crashed on startup with AttributeError before this fix.
    """
    from polymarket_round_bot.models import Timeframe

    # If .localize were still in the code, this call would raise
    # AttributeError. With the fix, it returns a valid slug.
    slug = _mod._current_expected_slug(Timeframe.M15)
    assert slug.startswith("btc-updown-15m-")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
