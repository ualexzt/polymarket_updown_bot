"""Settings via pydantic-settings v2.

Loads .env from project root. extra='ignore' so non-model env vars
(like POLY_*) don't cause validation errors.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import Timeframe

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # === Mode ===
    bot_mode: str = Field(default="paper")

    # === Asset / timeframe ===
    btc_symbol: str = Field(default="BTCUSDT")
    default_timeframe: Timeframe = Field(default=Timeframe.M15)

    # === Polymarket event input (optional) ===
    polymarket_event_url: str = Field(default="")
    polymarket_event_slug: str = Field(default="")

    # === Value entry thresholds ===
    # strict v1 (aligned with polymarket_round_research_v2.py defaults):
    # min_edge == safety_buffer == 0.05 so the two checks collapse into
    # one: trade only if ask <= historical_probability - 0.05.
    safety_buffer: Decimal = Field(default=Decimal("0.05"))
    min_edge: Decimal = Field(default=Decimal("0.05"))
    # Cap on absolute ask price. Rules with hist_prob >= 0.90 require
    # WR > 90% to break even at entry 0.85+, which backtest showed
    # overfit. Forbid asks above this cap in v1.
    max_entry_ask: Decimal = Field(default=Decimal("0.80"))
    max_spread: Decimal = Field(default=Decimal("0.03"))
    min_liquidity_usd: Decimal = Field(default=Decimal("25"))
    min_historical_probability: Decimal = Field(default=Decimal("0.60"))
    min_samples: int = Field(default=60)

    # === Position / risk caps ===
    # Strict paper v1: $1 per trade so a 24-trade day cannot exceed
    # $24 of theoretical max loss, and the daily_loss cap of $10
    # actually constrains behaviour instead of being the floor.
    max_position_usd: Decimal = Field(default=Decimal("1"))
    max_daily_loss_usd: Decimal = Field(default=Decimal("10"))
    max_open_positions: int = Field(default=1)

    # === Stage gates ===
    allow_after_5m: bool = Field(default=True)
    allow_after_10m: bool = Field(default=True)

    # === Timeframe gates ===
    # 5m markets have no calibrated rules in v1; explicit gate to
    # surface this in skip_reason (default off).
    allow_5m_trading: bool = Field(default=False)

    # === Rule trading restriction (v1) ===
    # v1 trades only on EXACT match. Fallback matches are logged
    # but never traded.
    allow_fallback_trading: bool = Field(default=False)

    # === Seconds-to-expiry windows ===
    # 15m round = 900s total. AFTER_5M stage starts ~5min in (sec_to_expiry=600),
    # AFTER_10M stage starts ~10min in (sec_to_expiry=300).
    min_seconds_to_expiry_15m_after_5m: int = Field(default=300)
    max_seconds_to_expiry_15m_after_5m: int = Field(default=600)
    min_seconds_to_expiry_15m_after_10m: int = Field(default=60)
    max_seconds_to_expiry_15m_after_10m: int = Field(default=300)

    # === Paper bankroll ===
    paper_starting_balance_usd: Decimal = Field(default=Decimal("100"))

    # === Freshness windows ===
    binance_price_max_age_seconds: int = Field(default=10)
    poly_orderbook_max_age_seconds: int = Field(default=5)
    poly_market_metadata_max_age_seconds: int = Field(default=60)

    # === Stale-position settlement ===
    # After a 15m market ends, Gamma keeps the market for ~24h. After
    # that, we fall back to Binance close price to settle stuck
    # positions. Grace period starts from window end.
    binance_fallback_grace_seconds: int = Field(default=300)

    # === Mark intervals ===
    paper_mark_interval_seconds_5m: int = Field(default=10)
    paper_mark_interval_seconds_15m: int = Field(default=15)

    # === Paths (relative to project root) ===
    state_rules_path: str = Field(
        default="config/btc_updown_state_rules_15m.json"
    )
    database_path: str = Field(default="data/polymarket_round_paper.sqlite")

    # === Telegram reports ===
    telegram_reports_enabled: bool = Field(default=True)
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    # Local Kyiv hours when a small trading report should be sent.
    telegram_report_hours_kyiv: str = Field(default="8,20")
    telegram_report_state_path: str = Field(default="data/telegram_report_state.json")

    # === HTTP ===
    http_timeout_seconds: int = Field(default=15)
    http_user_agent: str = Field(default="polymarket-round-bot/0.1")

    def resolve(self, key: str) -> Path:
        """Resolve a configured path (relative or absolute) against project root."""
        value = getattr(self, key)
        p = Path(value)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def state_rules_file(self) -> Path:
        return self.resolve("state_rules_path")

    @property
    def database_file(self) -> Path:
        return self.resolve("database_path")

    @property
    def telegram_report_state_file(self) -> Path:
        return self.resolve("telegram_report_state_path")
