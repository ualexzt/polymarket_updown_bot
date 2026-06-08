from __future__ import annotations

from datetime import UTC, datetime

from polymarket_round_bot.config import Settings
from polymarket_round_bot.storage import Storage
from polymarket_round_bot.telegram_reports import (
    TelegramReportService,
    format_telegram_report,
    kyiv_report_window,
)


def test_kyiv_report_window_at_08_covers_previous_20_to_08() -> None:
    window = kyiv_report_window(datetime(2026, 6, 8, 5, 3, tzinfo=UTC))

    assert window is not None
    assert window.key == "2026-06-08-08"
    assert window.start_utc == datetime(2026, 6, 7, 17, 0, tzinfo=UTC)
    assert window.end_utc == datetime(2026, 6, 8, 5, 0, tzinfo=UTC)


def test_kyiv_report_window_outside_report_hour_is_not_due() -> None:
    assert kyiv_report_window(datetime(2026, 6, 8, 4, 59, tzinfo=UTC)) is None
    assert kyiv_report_window(datetime(2026, 6, 8, 6, 0, tzinfo=UTC)) is None


def test_format_telegram_report_keeps_summary_small() -> None:
    text = format_telegram_report(
        {
            "title": "BTC UP/DOWN paper",
            "period_label": "2026-06-08 08:00–20:00 Kyiv",
            "trades": 4,
            "settled": 4,
            "wins": 3,
            "losses": 1,
            "win_rate": 0.75,
            "pnl": 1.23456,
            "open_positions": 1,
            "up_trades": 2,
            "up_pnl": 1.5,
            "down_trades": 2,
            "down_pnl": -0.26544,
            "day_trades": 10,
            "day_pnl": 2.0,
        }
    )

    assert "BTC UP/DOWN paper" in text
    assert "2026-06-08 08:00–20:00 Kyiv" in text
    assert "Trades: 4" in text
    assert "WR: 75.0%" in text
    assert "PnL: +$1.23" in text
    assert "Open: 1" in text
    assert "UP 2 / +$1.50" in text
    assert "DOWN 2 / -$0.27" in text
    assert len(text) < 600


def test_telegram_report_service_sends_once_per_window(tmp_path) -> None:
    sent: list[str] = []
    db = tmp_path / "paper.sqlite"
    storage = Storage(db)
    settings = Settings(
        database_path=str(db),
        telegram_bot_token="token",
        telegram_chat_id="chat",
        telegram_report_state_path=str(tmp_path / "telegram_report_state.json"),
    )
    service = TelegramReportService(settings, storage, send_func=lambda text: sent.append(text))
    now = datetime(2026, 6, 8, 5, 3, tzinfo=UTC)  # 08:03 Kyiv

    assert service.maybe_send(now_utc=now) is True
    assert service.maybe_send(now_utc=now) is False
    assert len(sent) == 1
    assert "20:00–08:00 Kyiv" in sent[0]
