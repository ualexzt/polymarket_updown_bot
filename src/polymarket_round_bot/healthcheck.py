"""Runtime health checks for the paper bot."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class DatabaseHealth:
    ok: bool
    reason: str
    last_decision_at: datetime | None = None
    last_decision_age_seconds: int | None = None
    error: str | None = None


def _parse_utc_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def check_database_health(
    database_file: str | Path,
    *,
    max_decision_age_seconds: int,
    now_utc: datetime | None = None,
) -> DatabaseHealth:
    """Return health based on SQLite integrity and latest decision freshness."""
    path = Path(database_file)
    if not path.exists():
        return DatabaseHealth(ok=False, reason="database_missing")

    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    try:
        with sqlite3.connect(path) as con:
            quick_check = con.execute("pragma quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                return DatabaseHealth(
                    ok=False,
                    reason="integrity_check_failed",
                    error=str(quick_check[0] if quick_check else "no quick_check result"),
                )
            row = con.execute("select max(timestamp_utc) from decisions").fetchone()
    except sqlite3.DatabaseError as exc:
        return DatabaseHealth(ok=False, reason="database_error", error=str(exc))

    last_value = row[0] if row else None
    if last_value is None:
        return DatabaseHealth(ok=False, reason="no_decisions")

    try:
        last_decision_at = _parse_utc_timestamp(str(last_value))
    except ValueError as exc:
        return DatabaseHealth(ok=False, reason="invalid_last_decision_timestamp", error=str(exc))

    age = max(0, int((now - last_decision_at).total_seconds()))
    if age > max_decision_age_seconds:
        return DatabaseHealth(
            ok=False,
            reason="last_decision_stale",
            last_decision_at=last_decision_at,
            last_decision_age_seconds=age,
        )

    return DatabaseHealth(
        ok=True,
        reason="ok",
        last_decision_at=last_decision_at,
        last_decision_age_seconds=age,
    )
