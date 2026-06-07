"""Tests for Storage helpers and audit queries."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_round_bot.models import (
    CurrentSide,
    DecisionKind,
    DecisionSnapshot,
    DistanceBucket,
    PaperPosition,
    PositionStatus,
    RuleMatchType,
    Side,
    Stage,
    VolatilityBucket,
)
from polymarket_round_bot.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "audit.sqlite")


def _mk_position(
    slug: str,
    *,
    status: PositionStatus = PositionStatus.OPEN,
    side: Side = Side.UP,
    entry_price: Decimal = Decimal("0.65"),
) -> PaperPosition:
    return PaperPosition(
        position_id=f"pos_{slug}_{side.value}_{status.value}",
        decision_id=f"dec_{slug}_{side.value}",
        market_slug=slug,
        event_url=None,
        selected_side=side,
        token_id="0xtok",
        entry_timestamp_utc=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        entry_price=entry_price,
        entry_best_ask=entry_price,
        entry_best_bid=entry_price - Decimal("0.01"),
        entry_spread=Decimal("0.01"),
        entry_size_usd=Decimal("1"),
        shares=Decimal("1.5"),
        fair_price_at_entry=Decimal("0.85"),
        max_buy_price_at_entry=Decimal("0.80"),
        edge_at_entry=Decimal("0.20"),
        round_open_price=Decimal("60000"),
        btc_price_at_entry=Decimal("60100"),
        distance_bucket_at_entry=DistanceBucket.D_010_020pct,
        volatility_bucket_at_entry=VolatilityBucket.VOL_LOW,
        pattern_at_entry="normal_bull",
        stage_at_entry=Stage.AFTER_5M,
        seconds_to_expiry_at_entry=600,
        current_side_at_entry=CurrentSide.ABOVE_OPEN,
        status=status,
        rule_id="r1",
        rule_match_type=RuleMatchType.EXACT,
        historical_probability_at_entry=Decimal("0.85"),
        samples_at_entry=200,
    )


def test_audit_duplicates_empty_on_clean_db(storage: Storage) -> None:
    audit = storage.audit_duplicates()
    assert audit == {
        "open_by_market": [],
        "lifetime_by_market": [],
        "rapid_trade_decisions": [],
    }


def test_audit_duplicates_detects_multiple_open(storage: Storage) -> None:
    """The partial unique index uq_open_position_market prevents a second
    OPEN row on the same slug. We verify by raw INSERT (bypassing the
    application's INSERT OR REPLACE, which would silently swap rows).
    Audit must return no duplicates when the index holds.
    """
    slug = "btc-updown-15m-1780767000"
    storage.upsert_position(_mk_position(slug, status=PositionStatus.OPEN, side=Side.UP))
    import sqlite3
    # Raw INSERT bypassing upsert's OR REPLACE: must fail.
    pos2 = _mk_position(slug, status=PositionStatus.OPEN, side=Side.DOWN)
    with pytest.raises(sqlite3.IntegrityError), storage._conn() as conn:  # type: ignore[attr-defined]
        from polymarket_round_bot.storage import _position_row  # type: ignore[attr-defined]
        conn.execute(
            """
            INSERT INTO paper_positions VALUES (
              :position_id, :decision_id, :market_slug, :event_url, :selected_side, :token_id,
              :entry_timestamp_utc, :entry_price, :entry_best_ask, :entry_best_bid, :entry_spread,
              :entry_size_usd, :shares, :fair_price_at_entry, :max_buy_price_at_entry, :edge_at_entry,
              :round_open_price, :btc_price_at_entry,
              :distance_bucket_at_entry, :volatility_bucket_at_entry,
              :pattern_at_entry, :stage_at_entry, :seconds_to_expiry_at_entry,
              :current_side_at_entry,
              :status, :rule_id, :rule_match_type,
              :historical_probability_at_entry, :samples_at_entry
            )
            """,
            _position_row(pos2),
        )

    audit = storage.audit_duplicates()
    assert audit["open_by_market"] == []
    assert audit["lifetime_by_market"] == []


def test_audit_duplicates_detects_legacy_duplicates_via_direct_insert(
    storage: Storage,
) -> None:
    """Simulate a legacy DB: bypass upsert and force two OPEN rows via
    raw SQL, then drop+recreate the index to allow the bypass. Audit
    must surface the duplicates.
    """
    slug = "btc-updown-15m-1780767000"
    # Drop the index so we can insert duplicates.
    with storage._conn() as conn:  # type: ignore[attr-defined]
        conn.execute("DROP INDEX IF EXISTS uq_open_position_market")
        conn.execute(
            "DELETE FROM paper_positions WHERE market_slug = ?", (slug,)
        )
    storage.upsert_position(_mk_position(slug, status=PositionStatus.OPEN, side=Side.UP))
    storage.upsert_position(_mk_position(slug, status=PositionStatus.OPEN, side=Side.DOWN))

    audit = storage.audit_duplicates()
    obm = audit["open_by_market"]
    assert len(obm) == 1
    assert obm[0]["market_slug"] == slug
    assert obm[0]["open_count"] == 2
    lbm = audit["lifetime_by_market"]
    assert any(d["market_slug"] == slug and d["total_count"] == 2 for d in lbm)


def test_audit_duplicates_lifetime_ignores_settled(storage: Storage) -> None:
    """If a slug has 1 OPEN + 1 SETTLED position, lifetime surfaces it
    but the OPEN count is 1, which is healthy (the SETTLED is from an
    earlier, completed round)."""
    slug = "btc-updown-15m-1780767000"
    storage.upsert_position(_mk_position(slug, status=PositionStatus.SETTLED))
    storage.upsert_position(_mk_position(slug, status=PositionStatus.OPEN, side=Side.DOWN))

    audit = storage.audit_duplicates()
    obm = audit["open_by_market"]
    assert obm == []  # only 1 OPEN
    lbm = audit["lifetime_by_market"]
    matching = [d for d in lbm if d["market_slug"] == slug]
    assert len(matching) == 1
    assert matching[0]["total_count"] == 2
    assert matching[0]["open_count"] == 1


def test_audit_duplicates_rapid_trade_decisions(storage: Storage) -> None:
    """Two TRADE decisions on the same slug <5s apart signal a near-race.
    Note: the audit considers both decisions regardless of whether they
    are on the same (slug, side) pair, so it surfaces both legitimate
    bursts and true duplicates.
    """
    slug = "btc-updown-15m-1780767000"
    base_ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

    for ts_offset, dec_id in [(0, "dec_a"), (2, "dec_b"), (10, "dec_c")]:
        snap = _build_snap(
            base_ts.replace(second=ts_offset), dec_id, slug
        )
        storage.insert_decision(snap)

    audit = storage.audit_duplicates()
    rtd = audit["rapid_trade_decisions"]
    # dec_a and dec_b are 2s apart, dec_c is 8s after dec_b. Only the
    # first pair should be flagged.
    flagged = [d for d in rtd if d["market_slug"] == slug]
    assert len(flagged) == 1
    assert flagged[0]["previous_decision_id"] == "dec_a"
    assert flagged[0]["seconds_apart"] < 5


def _build_snap(ts: datetime, dec_id: str, slug: str) -> DecisionSnapshot:
    """Construct a minimal-but-valid DecisionSnapshot for audit tests."""
    from polymarket_round_bot.models import (
        BinanceState,
        Candle,
        OrderbookSnapshot,
    )

    c0 = Candle(
        open_time_utc=ts,
        open=Decimal("100"),
        high=Decimal("100.2"),
        low=Decimal("99.9"),
        close=Decimal("100.10"),
        volume=Decimal("100"),
        is_closed=True,
    )
    BinanceState(
        symbol="BTCUSDT",
        candles=[c0],
        current_price=Decimal("100.10"),
        received_at_utc=ts,
    )
    # OrderbookSnapshot isn't needed for the snapshot (it has its own
    # flat book fields). The intermediate variables are constructed
    # only to obtain valid Decimal values for the snapshot fields.
    _up = OrderbookSnapshot(
        token_id="up",
        best_bid=Decimal("0.62"),
        best_ask=Decimal("0.65"),
        spread=Decimal("0.03"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        received_at_utc=ts,
    )
    _down = OrderbookSnapshot(
        token_id="down",
        best_bid=Decimal("0.32"),
        best_ask=Decimal("0.35"),
        spread=Decimal("0.03"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        received_at_utc=ts,
    )
    _ = (_up, _down)  # silence linter; values copied into snapshot below
    return DecisionSnapshot(
        decision_id=dec_id,
        timestamp_utc=ts,
        market_slug=slug,
        event_url=None,
        timeframe=__import__("polymarket_round_bot.models", fromlist=["Timeframe"]).Timeframe.M15,
        round_start_ts=ts,
        round_end_ts=ts,
        seconds_to_expiry=600,
        stage=Stage.AFTER_5M,
        side_checked=Side.UP,
        selected_side=Side.UP,
        outcome_token_id="up",
        opposite_token_id="down",
        decision=DecisionKind.TRADE,
        skip_reason=None,
        round_open_price=Decimal("100"),
        current_btc_price=Decimal("100.10"),
        current_side=CurrentSide.ABOVE_OPEN,
        distance_from_round_open=Decimal("0.001"),
        distance_bucket=DistanceBucket.D_010_020pct,
        volatility_bucket=VolatilityBucket.VOL_LOW,
        candle_pattern="normal_bull",
        pattern_combo=None,
        c0_open=Decimal("100"),
        c0_high=Decimal("100.2"),
        c0_low=Decimal("99.9"),
        c0_close=Decimal("100.10"),
        c0_volume=Decimal("100"),
        c1_open=None,
        c1_high=None,
        c1_low=None,
        c1_close=None,
        c1_volume=None,
        source_exchange="binance",
        source_symbol="BTCUSDT",
        binance_data_received_at_utc=ts,
        binance_data_age_seconds=Decimal("1"),
        up_best_bid=Decimal("0.62"),
        up_best_ask=Decimal("0.65"),
        down_best_bid=Decimal("0.32"),
        down_best_ask=Decimal("0.35"),
        up_spread=Decimal("0.03"),
        down_spread=Decimal("0.03"),
        selected_best_bid=Decimal("0.62"),
        selected_best_ask=Decimal("0.65"),
        selected_spread=Decimal("0.03"),
        selected_ask_size=Decimal("100"),
        selected_bid_size=Decimal("100"),
        orderbook_depth_top_5_json="[]",
        liquidity_usd_estimate=Decimal("500"),
        market_active=True,
        market_closed=False,
        market_accepting_orders=True,
        orderbook_received_at_utc=ts,
        orderbook_age_seconds=Decimal("1"),
        metadata_received_at_utc=ts,
        metadata_age_seconds=Decimal("1"),
        rule_id="r1",
        rule_match_type=RuleMatchType.EXACT,
        samples=200,
        historical_probability=Decimal("0.85"),
        fair_price=Decimal("0.85"),
        safety_buffer=Decimal("0.05"),
        max_buy_price=Decimal("0.80"),
        market_ask=Decimal("0.65"),
        edge_vs_ask=Decimal("0.20"),
        min_edge_required=Decimal("0.05"),
        recommended_side=Side.UP,
        return_aligned=True,
        requested_size_usd=Decimal("1"),
        max_position_usd=Decimal("1"),
        open_positions_count=0,
        max_open_positions=1,
        daily_realized_pnl=Decimal("0"),
        max_daily_loss_usd=Decimal("10"),
        risk_allowed=True,
        risk_reject_reason=None,
    )
