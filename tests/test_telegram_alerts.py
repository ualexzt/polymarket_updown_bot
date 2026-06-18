from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from polymarket_round_bot.config import Settings
from polymarket_round_bot.telegram_alerts import TelegramHealthAlertService


def _make_db(path, last_decision: datetime) -> None:
    con = sqlite3.connect(path)
    con.execute("create table decisions (timestamp_utc text not null)")
    con.execute(
        "insert into decisions (timestamp_utc) values (?)",
        (last_decision.isoformat().replace("+00:00", "Z"),),
    )
    con.commit()
    con.close()


def _settings(tmp_path, db_path) -> Settings:
    return Settings(
        database_path=str(db_path),
        telegram_alerts_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        telegram_alert_state_path=str(tmp_path / "telegram_alert_state.json"),
        telegram_alert_max_decision_age_seconds=300,
    )


def test_alert_service_sends_one_down_alert_until_recovery(tmp_path):
    now = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)
    db = tmp_path / "paper.sqlite"
    _make_db(db, now - timedelta(seconds=301))
    sent: list[str] = []
    service = TelegramHealthAlertService(_settings(tmp_path, db), send_func=sent.append)

    assert service.check_and_alert(now_utc=now) is True
    assert service.check_and_alert(now_utc=now + timedelta(seconds=60)) is False

    assert len(sent) == 1
    assert "🔴" in sent[0]
    assert "last_decision_stale" in sent[0]


def test_alert_service_sends_recovery_after_unhealthy_state(tmp_path):
    now = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)
    db = tmp_path / "paper.sqlite"
    _make_db(db, now - timedelta(seconds=301))
    sent: list[str] = []
    service = TelegramHealthAlertService(_settings(tmp_path, db), send_func=sent.append)

    assert service.check_and_alert(now_utc=now) is True

    con = sqlite3.connect(db)
    con.execute("delete from decisions")
    con.execute(
        "insert into decisions (timestamp_utc) values (?)",
        ((now + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),),
    )
    con.commit()
    con.close()

    assert service.check_and_alert(now_utc=now + timedelta(seconds=60)) is True

    assert len(sent) == 2
    assert "🟢" in sent[1]
    assert "RECOVERED" in sent[1]


def test_alert_service_is_disabled_without_token(tmp_path):
    now = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)
    db = tmp_path / "paper.sqlite"
    _make_db(db, now - timedelta(seconds=301))
    settings = _settings(tmp_path, db).model_copy(update={"telegram_bot_token": ""})
    sent: list[str] = []
    service = TelegramHealthAlertService(settings, send_func=sent.append)

    assert service.check_and_alert(now_utc=now) is False
    assert sent == []
