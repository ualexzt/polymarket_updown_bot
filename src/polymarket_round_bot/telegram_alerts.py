"""Telegram incident alerts for bot liveness failures."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .healthcheck import DatabaseHealth, check_database_health
from .telegram_reports import send_telegram_message


class TelegramHealthAlertService:
    """Send one down alert per incident and one recovery alert after it clears."""

    def __init__(
        self,
        settings: Settings,
        *,
        send_func: Callable[[str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._state_path = settings.telegram_alert_state_file
        self._send_func = send_func or (
            lambda text: send_telegram_message(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                text=text,
                timeout_seconds=settings.http_timeout_seconds,
            )
        )

    def check_and_alert(self, *, now_utc: datetime | None = None) -> bool:
        """Run health check and send an alert if incident state changed."""
        if not self._settings.telegram_alerts_enabled:
            return False
        if not self._settings.telegram_bot_token or not self._settings.telegram_chat_id:
            return False

        now = (now_utc or datetime.now(UTC)).astimezone(UTC)
        health = check_database_health(
            self._settings.database_file,
            max_decision_age_seconds=self._settings.telegram_alert_max_decision_age_seconds,
            now_utc=now,
        )
        state = self._read_state()
        previous_status = str(state.get("status", "ok"))

        if health.ok:
            if previous_status == "unhealthy":
                self._send_func(_format_recovery_message(health, now))
                self._write_state({"status": "ok", "updated_at": _iso_z(now)})
                return True
            self._write_state({"status": "ok", "updated_at": _iso_z(now)})
            return False

        if previous_status != "unhealthy":
            self._send_func(_format_down_message(health, now, self._settings.database_file))
            self._write_state(
                {
                    "status": "unhealthy",
                    "reason": health.reason,
                    "updated_at": _iso_z(now),
                    "last_decision_at": _iso_z(health.last_decision_at)
                    if health.last_decision_at
                    else None,
                    "last_decision_age_seconds": health.last_decision_age_seconds,
                    "error": health.error,
                }
            )
            return True

        self._write_state(
            {
                **state,
                "status": "unhealthy",
                "reason": health.reason,
                "updated_at": _iso_z(now),
                "last_decision_at": _iso_z(health.last_decision_at)
                if health.last_decision_at
                else None,
                "last_decision_age_seconds": health.last_decision_age_seconds,
                "error": health.error,
            }
        )
        return False

    def _read_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"status": "ok"}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "ok"}
        return data if isinstance(data, dict) else {"status": "ok"}

    def _write_state(self, data: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._state_path)


def _format_down_message(health: DatabaseHealth, now: datetime, database_file: Path) -> str:
    lines = [
        "🔴 BOT ALERT: Polymarket UP/DOWN bot unhealthy",
        f"Time UTC: {_iso_z(now)}",
        f"Reason: {health.reason}",
        f"Database: {database_file}",
    ]
    if health.last_decision_at is not None:
        lines.append(f"Last decision: {_iso_z(health.last_decision_at)}")
    if health.last_decision_age_seconds is not None:
        lines.append(f"Decision age: {health.last_decision_age_seconds}s")
    if health.error:
        lines.append(f"Error: {health.error}")
    return "\n".join(lines)


def _format_recovery_message(health: DatabaseHealth, now: datetime) -> str:
    lines = [
        "🟢 BOT RECOVERED: Polymarket UP/DOWN bot healthy",
        f"Time UTC: {_iso_z(now)}",
    ]
    if health.last_decision_at is not None:
        lines.append(f"Last decision: {_iso_z(health.last_decision_at)}")
    if health.last_decision_age_seconds is not None:
        lines.append(f"Decision age: {health.last_decision_age_seconds}s")
    return "\n".join(lines)


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
