"""Small scheduled Telegram reports for paper trading results."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .models import DecisionKind, PositionStatus, Settlement, Side
from .storage import Storage

KYIV_TZ = ZoneInfo("Europe/Kyiv")
DEFAULT_REPORT_HOURS: tuple[int, ...] = (8, 20)


@dataclass(frozen=True)
class ReportWindow:
    key: str
    start_utc: datetime
    end_utc: datetime
    start_local: datetime
    end_local: datetime


def parse_report_hours(raw: str) -> tuple[int, ...]:
    """Parse comma-separated local hours, e.g. ``"8,20"``."""
    hours: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        hour = int(part)
        if hour < 0 or hour > 23:
            raise ValueError(f"telegram report hour out of range: {hour}")
        hours.append(hour)
    return tuple(sorted(set(hours))) or DEFAULT_REPORT_HOURS


def kyiv_report_window(
    now_utc: datetime,
    *,
    report_hours: tuple[int, ...] = DEFAULT_REPORT_HOURS,
) -> ReportWindow | None:
    """Return the due Kyiv report window for ``now_utc``, if any.

    A report is due during the configured local hour. This gives the bot
    a full hour to send the message if it restarts a few minutes late,
    while the persisted window key prevents duplicate sends.
    """
    now = _as_utc(now_utc)
    now_local = now.astimezone(KYIV_TZ)
    if now_local.hour not in report_hours:
        return None

    end_local = now_local.replace(minute=0, second=0, microsecond=0)
    previous_hours = [h for h in report_hours if h < end_local.hour]
    if previous_hours:
        start_local = end_local.replace(hour=previous_hours[-1])
    else:
        start_local = (end_local - timedelta(days=1)).replace(hour=report_hours[-1])

    return ReportWindow(
        key=f"{end_local:%Y-%m-%d-%H}",
        start_utc=end_local_to_utc(start_local),
        end_utc=end_local_to_utc(end_local),
        start_local=start_local,
        end_local=end_local,
    )


def end_local_to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def build_telegram_report_summary(
    storage: Storage,
    window: ReportWindow,
    *,
    now_utc: datetime,
) -> dict[str, Any]:
    decisions = [
        d
        for d in storage.list_decisions(since_iso=_iso_z(window.start_utc))
        if _in_window(_parse_dt(d["timestamp_utc"]), window.start_utc, window.end_utc)
    ]
    settlements = [
        s
        for s in storage.list_settlements(since_iso=_iso_z(window.start_utc))
        if _in_window(s.resolved_at_utc, window.start_utc, window.end_utc)
    ]
    open_positions = [
        p for p in storage.list_all_positions() if p.status == PositionStatus.OPEN
    ]

    day_start_local = _as_utc(now_utc).astimezone(KYIV_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    day_start_utc = day_start_local.astimezone(UTC)
    day_settlements = [
        s
        for s in storage.list_settlements(since_iso=_iso_z(day_start_utc))
        if _in_window(s.resolved_at_utc, day_start_utc, _as_utc(now_utc))
    ]

    return {
        "title": "BTC UP/DOWN paper",
        "period_label": _period_label(window),
        "trades": sum(
            1 for d in decisions if d.get("decision") == DecisionKind.TRADE.value
        ),
        "settled": len(settlements),
        "wins": sum(1 for s in settlements if s.won),
        "losses": sum(1 for s in settlements if not s.won),
        "win_rate": _win_rate(settlements),
        "pnl": _sum_pnl(settlements),
        "open_positions": len(open_positions),
        "up_trades": sum(1 for s in settlements if s.selected_side == Side.UP),
        "up_pnl": _sum_pnl([s for s in settlements if s.selected_side == Side.UP]),
        "down_trades": sum(1 for s in settlements if s.selected_side == Side.DOWN),
        "down_pnl": _sum_pnl([s for s in settlements if s.selected_side == Side.DOWN]),
        "day_trades": len(day_settlements),
        "day_pnl": _sum_pnl(day_settlements),
    }


def format_telegram_report(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"📊 {summary['title']}",
            f"Period: {summary['period_label']}",
            "",
            (
                f"Trades: {summary['trades']} | Settled: {summary['settled']} | "
                f"WR: {_pct(summary['win_rate'])}"
            ),
            f"PnL: {_money(summary['pnl'])} | Open: {summary['open_positions']}",
            (
                "Side: "
                f"UP {summary['up_trades']} / {_money(summary['up_pnl'])}, "
                f"DOWN {summary['down_trades']} / {_money(summary['down_pnl'])}"
            ),
            f"Day: {summary['day_trades']} settled / {_money(summary['day_pnl'])}",
        ]
    )


class TelegramReportService:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        *,
        send_func: Callable[[str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._state_path = settings.telegram_report_state_file
        self._send_func = send_func or (
            lambda text: send_telegram_message(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                text=text,
                timeout_seconds=settings.http_timeout_seconds,
            )
        )

    def maybe_send(self, *, now_utc: datetime | None = None) -> bool:
        if not self._settings.telegram_reports_enabled:
            return False
        if not self._settings.telegram_bot_token or not self._settings.telegram_chat_id:
            return False

        now = now_utc or datetime.now(UTC)
        window = kyiv_report_window(
            now,
            report_hours=parse_report_hours(self._settings.telegram_report_hours_kyiv),
        )
        if window is None or self._already_sent(window.key):
            return False

        summary = build_telegram_report_summary(self._storage, window, now_utc=now)
        self._send_func(format_telegram_report(summary))
        self._mark_sent(window.key)
        return True

    def _already_sent(self, key: str) -> bool:
        if not self._state_path.exists():
            return False
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return str(data.get("last_sent_key", "")) == key

    def _mark_sent(self, key: str) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        tmp.write_text(json.dumps({"last_sent_key": key}), encoding="utf-8")
        tmp.replace(self._state_path)


def send_telegram_message(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    timeout_seconds: int,
) -> None:
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"telegram send failed with HTTP {resp.status}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"telegram send failed: {e}") from e


def _period_label(window: ReportWindow) -> str:
    if window.start_local.date() == window.end_local.date():
        return f"{window.end_local:%Y-%m-%d} {window.start_local:%H:%M}–{window.end_local:%H:%M} Kyiv"
    return f"{window.start_local:%Y-%m-%d %H:%M}–{window.end_local:%H:%M} Kyiv"


def _sum_pnl(settlements: list[Settlement]) -> float:
    return float(sum((s.realized_pnl_usd for s in settlements), Decimal("0")))


def _win_rate(settlements: list[Settlement]) -> float:
    if not settlements:
        return 0.0
    return sum(1 for s in settlements if s.won) / len(settlements)


def _money(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):.2f}"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _iso_z(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _in_window(value: datetime, start: datetime, end: datetime) -> bool:
    current = _as_utc(value)
    return _as_utc(start) <= current < _as_utc(end)
