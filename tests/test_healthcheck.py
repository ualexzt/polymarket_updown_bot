from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from polymarket_round_bot.healthcheck import check_database_health


def _make_db(path, last_decision: datetime | None) -> None:
    con = sqlite3.connect(path)
    con.execute("create table decisions (timestamp_utc text not null)")
    if last_decision is not None:
        con.execute("insert into decisions (timestamp_utc) values (?)", (last_decision.isoformat().replace("+00:00", "Z"),))
    con.commit()
    con.close()


def test_database_health_accepts_recent_decision(tmp_path):
    now = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)
    db = tmp_path / "paper.sqlite"
    _make_db(db, now - timedelta(seconds=42))

    result = check_database_health(db, max_decision_age_seconds=300, now_utc=now)

    assert result.ok is True
    assert result.last_decision_age_seconds == 42
    assert result.reason == "ok"


def test_database_health_rejects_stale_decision(tmp_path):
    now = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)
    db = tmp_path / "paper.sqlite"
    _make_db(db, now - timedelta(seconds=301))

    result = check_database_health(db, max_decision_age_seconds=300, now_utc=now)

    assert result.ok is False
    assert result.reason == "last_decision_stale"
    assert result.last_decision_age_seconds == 301


def test_database_health_rejects_missing_decisions(tmp_path):
    now = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)
    db = tmp_path / "paper.sqlite"
    _make_db(db, None)

    result = check_database_health(db, max_decision_age_seconds=300, now_utc=now)

    assert result.ok is False
    assert result.reason == "no_decisions"


def test_database_health_rejects_malformed_database(tmp_path):
    db = tmp_path / "paper.sqlite"
    db.write_bytes(b"not a sqlite database")

    result = check_database_health(db, max_decision_age_seconds=300)

    assert result.ok is False
    assert result.reason == "database_error"
    assert result.error is not None
