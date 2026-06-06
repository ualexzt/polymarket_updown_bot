"""Domain models for Polymarket round-pricing bot.

All money/price values use Decimal. All timestamps are timezone-aware UTC.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# === Enums (match research CSV exactly) ===

class Timeframe(str, Enum):
    M5 = "5m"
    M15 = "15m"


class MarketType(str, Enum):
    UPDOWN = "UPDOWN"


class Asset(str, Enum):
    BTC = "BTC"


class Stage(str, Enum):
    """Round progress marker.

    - AFTER_5M  — 15m round, ~5 minutes elapsed, only c0 finished
    - AFTER_10M — 15m round, ~10 minutes elapsed, c0 and c1 finished
    - CUSTOM_5M_STATE — 5m round, no internal candle segmentation
    """
    AFTER_5M = "AFTER_5M"
    AFTER_10M = "AFTER_10M"
    CUSTOM_5M_STATE = "CUSTOM_5M_STATE"


class CurrentSide(str, Enum):
    ABOVE_OPEN = "ABOVE_OPEN"
    BELOW_OPEN = "BELOW_OPEN"
    AT_OPEN = "AT_OPEN"


class DistanceBucket(str, Enum):
    D_0_005pct = "D_0_005pct"
    D_005_010pct = "D_005_010pct"
    D_010_020pct = "D_010_020pct"
    D_020_035pct = "D_020_035pct"
    D_035_050pct = "D_035_050pct"
    D_GT_050pct = "D_GT_050pct"


class VolatilityBucket(str, Enum):
    VOL_LOW = "VOL_LOW"
    VOL_NORMAL = "VOL_NORMAL"
    VOL_HIGH = "VOL_HIGH"
    VOL_UNKNOWN = "VOL_UNKNOWN"


class PatternName(str, Enum):
    """Single-candle patterns. Combos are 'name1 -> name2' (string)."""
    STRONG_BULL_CLOSE_NEAR_HIGH = "strong_bull_close_near_high"
    STRONG_BEAR_CLOSE_NEAR_LOW = "strong_bear_close_near_low"
    NORMAL_BULL = "normal_bull"
    NORMAL_BEAR = "normal_bear"
    BULL_LONG_UPPER_WICK = "bull_long_upper_wick"
    BULL_LONG_LOWER_WICK = "bull_long_lower_wick"
    BEAR_LONG_UPPER_WICK = "bear_long_upper_wick"
    BEAR_LONG_LOWER_WICK = "bear_long_lower_wick"
    DOJI_LONG_UPPER_WICK = "doji_long_upper_wick"
    DOJI_LONG_LOWER_WICK = "doji_long_lower_wick"
    DOJI_TWO_LONG_WICKS = "doji_two_long_wicks"
    WEAK_BULL = "weak_bull"
    WEAK_BEAR = "weak_bear"
    FLAT = "flat"


class Side(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class DecisionKind(str, Enum):
    TRADE = "TRADE"
    SKIP = "SKIP"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class SettlementSource(str, Enum):
    POLYMARKET_API = "POLYMARKET_API"
    BINANCE_FALLBACK = "BINANCE_FALLBACK"


class TradeQuality(str, Enum):
    GOOD_WIN = "GOOD_WIN"
    BAD_WIN = "BAD_WIN"
    GOOD_LOSS = "GOOD_LOSS"
    BAD_LOSS = "BAD_LOSS"
    EXECUTION_ERROR = "EXECUTION_ERROR"


class RuleMatchType(str, Enum):
    EXACT = "exact"
    FALLBACK_NO_VOL = "fallback_no_volatility"
    FALLBACK_NO_PATTERN = "fallback_no_pattern"
    NO_MATCH = "no_match"


# === Base ===

class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# === URL parser result ===

class ParsedSlug(_Base):
    asset: Asset
    market_type: MarketType
    timeframe: Timeframe
    timestamp: int = Field(ge=0)
    slug: str

    @field_validator("slug")
    @classmethod
    def _slug_nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("slug must be non-empty")
        return v


# === Market discovery ===

class MarketMetadata(_Base):
    market_id: str
    condition_id: str
    question: str
    slug: str
    event_slug: str | None = None
    up_token_id: str
    down_token_id: str
    outcomes: list[str]
    start_ts: datetime
    end_ts: datetime
    active: bool
    closed: bool
    accepting_orders: bool
    resolved_outcome: Side | None = None
    liquidity_usd: Decimal | None = None
    fee_rate: Decimal | None = None

    @field_validator("start_ts", "end_ts")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)


class TimestampAlignment(_Base):
    slug_timestamp: int
    market_start_ts: datetime
    market_end_ts: datetime
    alignment: str  # MATCHES_START | MATCHES_END | OFFSET | UNKNOWN


# === Orderbook ===

class OrderbookLevel(_Base):
    price: Decimal
    size: Decimal


class OrderbookSnapshot(_Base):
    token_id: str
    best_bid: Decimal | None
    best_ask: Decimal | None
    spread: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    top_5_bids: list[OrderbookLevel] = Field(default_factory=list)
    top_5_asks: list[OrderbookLevel] = Field(default_factory=list)
    liquidity_usd_estimate: Decimal | None = None
    received_at_utc: datetime

    @field_validator("received_at_utc")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)


class PairOrderbook(_Base):
    up: OrderbookSnapshot
    down: OrderbookSnapshot
    received_at_utc: datetime

    @field_validator("received_at_utc")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)


# === Binance candles ===

class Candle(_Base):
    open_time_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool = True

    @field_validator("open_time_utc")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)


class BinanceState(_Base):
    symbol: str
    candles: list[Candle]  # most recent N closed candles
    current_price: Decimal  # last seen price (close of last closed candle)
    received_at_utc: datetime

    @field_validator("received_at_utc")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)


# === Candle features ===

class CandleFeatures(_Base):
    pattern: str  # PatternName.value or "name1 -> name2" combo or "no_internal_candles"
    body: Decimal
    body_abs: Decimal
    range_: Decimal = Field(alias="range")
    upper_wick: Decimal
    lower_wick: Decimal
    body_to_range: Decimal
    upper_wick_to_range: Decimal
    lower_wick_to_range: Decimal
    close_position_in_range: Decimal
    is_doji: bool
    is_small_body: bool
    is_strong_body: bool
    has_long_upper_wick: bool
    has_long_lower_wick: bool

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# === Round state ===

class RoundState(_Base):
    """Computed state of the round for decision-making.

    - 15m rounds produce AFTER_5M (uses c0) and AFTER_10M (uses c0+c1).
    - 5m rounds produce CUSTOM_5M_STATE (no internal pattern).
    """
    timeframe: Timeframe
    stage: Stage
    round_open_price: Decimal
    round_close_price: Decimal | None  # None while in progress
    current_btc_price: Decimal
    current_side: CurrentSide
    distance_pct: Decimal  # signed: positive ABOVE, negative BELOW
    distance_bucket: DistanceBucket
    volatility_bucket: VolatilityBucket
    prev_16_abs_return_mean: Decimal | None
    candle_pattern: str  # PatternName.value or "c0_pattern -> c1_pattern" or "no_internal_candles"
    pattern_combo: str | None  # "c0 -> c1" for 15m AFTER_10M, else None
    seconds_to_expiry: int

    # 15m round only: closed 5m candles within this round
    c0: Candle | None = None
    c1: Candle | None = None
    c2: Candle | None = None  # last in-round candle; closed only at expiry


# === Probability rule ===

class ProbabilityRule(_Base):
    rule_id: str
    stage: Stage
    current_side: CurrentSide
    distance_bucket: DistanceBucket
    volatility_bucket: VolatilityBucket
    pattern: str  # "single" or "c0 -> c1" combo
    recommended_side: Side
    historical_probability: Decimal
    samples: int = Field(ge=1)
    median_round_return: Decimal
    return_aligned: bool
    usable_signal: bool


# === Rule lookup result ===

class RuleLookupResult(_Base):
    rule: ProbabilityRule | None
    match_type: RuleMatchType
    historical_probability: Decimal | None
    recommended_side: Side | None
    samples: int
    no_trade_reasons: list[str] = Field(default_factory=list)


# === Signal / decision ===

class SignalInputs(_Base):
    """Snapshot of all inputs used to produce a decision."""
    state: RoundState
    selected_side: Side
    selected_token_id: str
    selected_best_bid: Decimal | None
    selected_best_ask: Decimal | None
    selected_spread: Decimal | None
    selected_ask_size: Decimal | None
    selected_bid_size: Decimal | None
    liquidity_usd_estimate: Decimal | None
    market: MarketMetadata
    orderbook_received_at_utc: datetime
    binance_state: BinanceState
    rule_lookup: RuleLookupResult


class SignalDecision(_Base):
    decision: DecisionKind
    side: Side | None
    market_slug: str
    event_url: str | None
    token_id: str | None
    stage: Stage
    current_side: CurrentSide
    distance_bucket: DistanceBucket
    volatility_bucket: VolatilityBucket
    pattern: str
    rule_id: str | None
    rule_match_type: RuleMatchType
    samples: int
    historical_probability: Decimal | None
    fair_price: Decimal | None = None
    safety_buffer: Decimal
    max_buy_price: Decimal | None
    market_ask: Decimal | None
    edge_vs_ask: Decimal | None
    spread: Decimal | None
    size_usd: Decimal
    reason: str


# === Risk ===

class RiskDecision(_Base):
    allowed: bool
    reject_reason: str | None
    requested_size_usd: Decimal
    max_position_usd: Decimal
    open_positions_count: int
    max_open_positions: int
    daily_realized_pnl: Decimal
    max_daily_loss_usd: Decimal


# === Decision snapshot (persisted) ===

class DecisionSnapshot(_Base):
    decision_id: str
    timestamp_utc: datetime
    market_slug: str
    event_url: str | None
    timeframe: Timeframe
    round_start_ts: datetime
    round_end_ts: datetime
    seconds_to_expiry: int
    stage: Stage
    side_checked: Side
    selected_side: Side | None
    outcome_token_id: str | None
    opposite_token_id: str | None
    decision: DecisionKind
    skip_reason: str | None

    # BTC state
    round_open_price: Decimal
    current_btc_price: Decimal
    current_side: CurrentSide
    distance_from_round_open: Decimal
    distance_bucket: DistanceBucket
    volatility_bucket: VolatilityBucket
    candle_pattern: str
    pattern_combo: str | None
    c0_open: Decimal | None
    c0_high: Decimal | None
    c0_low: Decimal | None
    c0_close: Decimal | None
    c0_volume: Decimal | None
    c1_open: Decimal | None
    c1_high: Decimal | None
    c1_low: Decimal | None
    c1_close: Decimal | None
    c1_volume: Decimal | None
    source_exchange: str
    source_symbol: str
    binance_data_received_at_utc: datetime
    binance_data_age_seconds: Decimal

    # Polymarket snapshot
    up_best_bid: Decimal | None
    up_best_ask: Decimal | None
    down_best_bid: Decimal | None
    down_best_ask: Decimal | None
    up_spread: Decimal | None
    down_spread: Decimal | None
    selected_best_bid: Decimal | None
    selected_best_ask: Decimal | None
    selected_spread: Decimal | None
    selected_ask_size: Decimal | None
    selected_bid_size: Decimal | None
    orderbook_depth_top_5_json: str
    liquidity_usd_estimate: Decimal | None
    market_active: bool
    market_closed: bool
    market_accepting_orders: bool
    orderbook_received_at_utc: datetime
    orderbook_age_seconds: Decimal
    metadata_received_at_utc: datetime
    metadata_age_seconds: Decimal

    # Signal
    rule_id: str | None
    rule_match_type: RuleMatchType
    samples: int
    historical_probability: Decimal | None
    fair_price: Decimal | None
    safety_buffer: Decimal
    max_buy_price: Decimal | None
    market_ask: Decimal | None
    edge_vs_ask: Decimal | None
    min_edge_required: Decimal
    recommended_side: Side | None
    return_aligned: bool

    # Risk
    requested_size_usd: Decimal
    max_position_usd: Decimal
    open_positions_count: int
    max_open_positions: int
    daily_realized_pnl: Decimal
    max_daily_loss_usd: Decimal
    risk_allowed: bool
    risk_reject_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# === Paper position ===

class PaperPosition(_Base):
    position_id: str
    decision_id: str
    market_slug: str
    event_url: str | None
    selected_side: Side
    token_id: str
    entry_timestamp_utc: datetime
    entry_price: Decimal
    entry_best_ask: Decimal
    entry_best_bid: Decimal
    entry_spread: Decimal
    entry_size_usd: Decimal
    shares: Decimal
    fair_price_at_entry: Decimal
    max_buy_price_at_entry: Decimal
    edge_at_entry: Decimal
    round_open_price: Decimal
    btc_price_at_entry: Decimal
    distance_bucket_at_entry: DistanceBucket
    volatility_bucket_at_entry: VolatilityBucket
    pattern_at_entry: str
    stage_at_entry: Stage
    seconds_to_expiry_at_entry: int
    current_side_at_entry: CurrentSide
    status: PositionStatus
    rule_id: str | None
    rule_match_type: RuleMatchType
    historical_probability_at_entry: Decimal
    samples_at_entry: int


class MarkToMarket(_Base):
    position_id: str
    timestamp_utc: datetime
    best_bid: Decimal | None
    best_ask: Decimal | None
    mid_price: Decimal | None
    estimated_exit_value_bid: Decimal | None
    unrealized_pnl_bid: Decimal | None
    btc_price: Decimal | None
    distance_from_round_open: Decimal | None
    seconds_to_expiry: int | None


# === Settlement ===

class Settlement(_Base):
    settlement_id: str
    position_id: str
    market_slug: str
    resolved_outcome: Side
    selected_side: Side
    won: bool
    entry_price: Decimal
    shares: Decimal
    cost_usd: Decimal
    payout_usd: Decimal
    realized_pnl_usd: Decimal
    realized_roi_pct: Decimal
    settlement_source: SettlementSource
    round_open_price: Decimal
    round_close_price: Decimal
    final_btc_price: Decimal
    resolved_at_utc: datetime
    trade_quality: TradeQuality
    edge_at_entry: Decimal
    spread_at_entry: Decimal
    rule_id: str | None
    historical_probability_at_entry: Decimal
    seconds_to_expiry_at_entry: int
