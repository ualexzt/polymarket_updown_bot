"""SQLite storage layer.

Tables:
  markets, decisions, paper_positions, mark_to_market_snapshots,
  settlements, bot_runs.

All money/price values stored as TEXT (Decimal string) for safety.
Timestamps stored as ISO 8601 UTC.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import (
    DecisionSnapshot,
    MarkToMarket,
    PaperPosition,
    PositionStatus,
    Settlement,
)

log = logging.getLogger("polymarket_round_bot.storage")

SCHEMA: str = """
CREATE TABLE IF NOT EXISTS markets (
    market_id          TEXT PRIMARY KEY,
    condition_id       TEXT NOT NULL,
    slug               TEXT NOT NULL,
    event_slug         TEXT,
    question           TEXT NOT NULL,
    up_token_id        TEXT NOT NULL,
    down_token_id      TEXT NOT NULL,
    start_ts           TEXT NOT NULL,
    end_ts             TEXT NOT NULL,
    active             INTEGER NOT NULL,
    closed             INTEGER NOT NULL,
    accepting_orders   INTEGER NOT NULL,
    resolved_outcome   TEXT,
    liquidity_usd      TEXT,
    fee_rate           TEXT,
    discovered_at_utc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_markets_slug ON markets(slug);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id            TEXT PRIMARY KEY,
    timestamp_utc          TEXT NOT NULL,
    market_slug            TEXT NOT NULL,
    event_url              TEXT,
    timeframe              TEXT NOT NULL,
    round_start_ts         TEXT NOT NULL,
    round_end_ts           TEXT NOT NULL,
    seconds_to_expiry      INTEGER NOT NULL,
    stage                  TEXT NOT NULL,
    side_checked           TEXT NOT NULL,
    selected_side          TEXT,
    outcome_token_id       TEXT,
    opposite_token_id      TEXT,
    decision               TEXT NOT NULL,
    skip_reason            TEXT,
    round_open_price       TEXT NOT NULL,
    current_btc_price      TEXT NOT NULL,
    current_side           TEXT NOT NULL,
    distance_from_round_open TEXT NOT NULL,
    distance_bucket        TEXT NOT NULL,
    volatility_bucket      TEXT NOT NULL,
    candle_pattern         TEXT NOT NULL,
    pattern_combo          TEXT,
    c0_open                TEXT, c0_high TEXT, c0_low TEXT, c0_close TEXT, c0_volume TEXT,
    c1_open                TEXT, c1_high TEXT, c1_low TEXT, c1_close TEXT, c1_volume TEXT,
    source_exchange        TEXT NOT NULL,
    source_symbol          TEXT NOT NULL,
    binance_data_received_at_utc TEXT NOT NULL,
    binance_data_age_seconds     TEXT NOT NULL,
    up_best_bid TEXT, up_best_ask TEXT, down_best_bid TEXT, down_best_ask TEXT,
    up_spread TEXT, down_spread TEXT,
    selected_best_bid TEXT, selected_best_ask TEXT, selected_spread TEXT,
    selected_ask_size TEXT, selected_bid_size TEXT,
    orderbook_depth_top_5_json TEXT NOT NULL,
    liquidity_usd_estimate TEXT,
    market_active INTEGER NOT NULL,
    market_closed INTEGER NOT NULL,
    market_accepting_orders INTEGER NOT NULL,
    orderbook_received_at_utc TEXT NOT NULL,
    orderbook_age_seconds   TEXT NOT NULL,
    metadata_received_at_utc TEXT NOT NULL,
    metadata_age_seconds   TEXT NOT NULL,
    rule_id TEXT,
    rule_match_type TEXT NOT NULL,
    samples INTEGER NOT NULL,
    historical_probability TEXT,
    fair_price TEXT,
    safety_buffer TEXT NOT NULL,
    max_buy_price TEXT,
    market_ask TEXT,
    edge_vs_ask TEXT,
    min_edge_required TEXT NOT NULL,
    recommended_side TEXT,
    return_aligned INTEGER NOT NULL,
    requested_size_usd TEXT NOT NULL,
    max_position_usd TEXT NOT NULL,
    open_positions_count INTEGER NOT NULL,
    max_open_positions INTEGER NOT NULL,
    daily_realized_pnl TEXT NOT NULL,
    max_daily_loss_usd TEXT NOT NULL,
    risk_allowed INTEGER NOT NULL,
    risk_reject_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_slug ON decisions(market_slug);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(timestamp_utc);

CREATE TABLE IF NOT EXISTS paper_positions (
    position_id          TEXT PRIMARY KEY,
    decision_id          TEXT NOT NULL,
    market_slug          TEXT NOT NULL,
    event_url            TEXT,
    selected_side        TEXT NOT NULL,
    token_id             TEXT NOT NULL,
    entry_timestamp_utc  TEXT NOT NULL,
    entry_price          TEXT NOT NULL,
    entry_best_ask       TEXT NOT NULL,
    entry_best_bid       TEXT NOT NULL,
    entry_spread         TEXT NOT NULL,
    entry_size_usd       TEXT NOT NULL,
    shares               TEXT NOT NULL,
    fair_price_at_entry  TEXT NOT NULL,
    max_buy_price_at_entry TEXT NOT NULL,
    edge_at_entry        TEXT NOT NULL,
    round_open_price     TEXT NOT NULL,
    btc_price_at_entry   TEXT NOT NULL,
    distance_bucket_at_entry TEXT NOT NULL,
    volatility_bucket_at_entry TEXT NOT NULL,
    pattern_at_entry     TEXT NOT NULL,
    stage_at_entry       TEXT NOT NULL,
    seconds_to_expiry_at_entry INTEGER NOT NULL,
    current_side_at_entry TEXT NOT NULL,
    status               TEXT NOT NULL,
    rule_id              TEXT,
    rule_match_type      TEXT NOT NULL,
    historical_probability_at_entry TEXT NOT NULL,
    samples_at_entry     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_slug ON paper_positions(market_slug);

CREATE TABLE IF NOT EXISTS mark_to_market_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    best_bid TEXT, best_ask TEXT, mid_price TEXT,
    estimated_exit_value_bid TEXT, unrealized_pnl_bid TEXT,
    btc_price TEXT, distance_from_round_open TEXT, seconds_to_expiry INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mtm_position ON mark_to_market_snapshots(position_id);

CREATE TABLE IF NOT EXISTS settlements (
    settlement_id        TEXT PRIMARY KEY,
    position_id          TEXT NOT NULL,
    market_slug          TEXT NOT NULL,
    resolved_outcome     TEXT NOT NULL,
    selected_side        TEXT NOT NULL,
    won                  INTEGER NOT NULL,
    entry_price          TEXT NOT NULL,
    shares               TEXT NOT NULL,
    cost_usd             TEXT NOT NULL,
    payout_usd           TEXT NOT NULL,
    realized_pnl_usd     TEXT NOT NULL,
    realized_roi_pct     TEXT NOT NULL,
    settlement_source    TEXT NOT NULL,
    round_open_price     TEXT NOT NULL,
    round_close_price    TEXT NOT NULL,
    final_btc_price      TEXT NOT NULL,
    resolved_at_utc      TEXT NOT NULL,
    trade_quality        TEXT NOT NULL,
    edge_at_entry        TEXT NOT NULL,
    spread_at_entry      TEXT NOT NULL,
    rule_id              TEXT,
    historical_probability_at_entry TEXT NOT NULL,
    seconds_to_expiry_at_entry INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_settlements_slug ON settlements(market_slug);
CREATE INDEX IF NOT EXISTS idx_settlements_ts ON settlements(resolved_at_utc);

CREATE TABLE IF NOT EXISTS bot_runs (
    run_id           TEXT PRIMARY KEY,
    started_at_utc   TEXT NOT NULL,
    ended_at_utc     TEXT,
    bot_mode         TEXT NOT NULL,
    settings_json    TEXT NOT NULL,
    notes            TEXT
);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _dec(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


class Storage:
    def __init__(
        self,
        db_path: Path,
        *,
        telemetry_writer: object | None = None,
        strategy_id: str | None = None,
    ) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._telemetry_writer = telemetry_writer
        self._strategy_id = strategy_id
        self._init_schema()

    def _emit(self, method_name: str, **kwargs: object) -> None:
        """Mirror a write to the control plane telemetry writer if configured.

        Failures are logged and swallowed: telemetry is best-effort and
        must never break the bot's local persistence path.
        """
        if self._telemetry_writer is None or self._strategy_id is None:
            return
        method = getattr(self._telemetry_writer, method_name, None)
        if method is None:
            return
        try:
            method(**kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "telemetry_emit_failed method=%s err=%s", method_name, exc
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            # Defense-in-depth: partial unique index ensures no two
            # rows with status='OPEN' share the same market_slug.
            # If pre-existing duplicates are in the DB (e.g. created
            # by the pre-fix bot 2026-06-06), the CREATE will fail;
            # we log and continue. A future audit/cleanup pass can
            # remove the duplicates and re-create the index.
            try:
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                        uq_open_position_market
                    ON paper_positions(market_slug)
                    WHERE status = 'OPEN'
                    """
                )
            except Exception as e:  # IntegrityError on duplicate data
                # Non-fatal: risk-manager + storage-list checks
                # already prevent new duplicates.
                log.warning(
                    "uq_open_position_market_index_skipped err=%s", e
                )

    # === Bot runs ===

    def start_run(self, run_id: str, *, bot_mode: str, settings_json: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO bot_runs(run_id, started_at_utc, bot_mode, settings_json) VALUES(?,?,?,?)",
                (run_id, _now_iso(), bot_mode, settings_json),
            )
        if self._strategy_id is not None:
            self._emit(
                "write_strategy_identity",
                strategy_id=self._strategy_id,
                name=f"Polymarket Up/Down Bot ({bot_mode})",
                kind="updown-15m",
                mode=bot_mode,
                asset="BTC",
                status="running",
                config_version=f"run:{run_id}",
                last_seen_at=datetime.now(UTC),
            )

    def end_run(self, run_id: str, *, notes: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE bot_runs SET ended_at_utc=?, notes=? WHERE run_id=?",
                (_now_iso(), notes, run_id),
            )

    # === Decisions ===

    def insert_decision(self, snap: DecisionSnapshot) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO decisions VALUES (
                  :decision_id, :timestamp_utc, :market_slug, :event_url, :timeframe,
                  :round_start_ts, :round_end_ts, :seconds_to_expiry, :stage, :side_checked,
                  :selected_side, :outcome_token_id, :opposite_token_id, :decision, :skip_reason,
                  :round_open_price, :current_btc_price, :current_side, :distance_from_round_open,
                  :distance_bucket, :volatility_bucket, :candle_pattern, :pattern_combo,
                  :c0_open, :c0_high, :c0_low, :c0_close, :c0_volume,
                  :c1_open, :c1_high, :c1_low, :c1_close, :c1_volume,
                  :source_exchange, :source_symbol, :binance_data_received_at_utc, :binance_data_age_seconds,
                  :up_best_bid, :up_best_ask, :down_best_bid, :down_best_ask, :up_spread, :down_spread,
                  :selected_best_bid, :selected_best_ask, :selected_spread,
                  :selected_ask_size, :selected_bid_size, :orderbook_depth_top_5_json, :liquidity_usd_estimate,
                  :market_active, :market_closed, :market_accepting_orders,
                  :orderbook_received_at_utc, :orderbook_age_seconds,
                  :metadata_received_at_utc, :metadata_age_seconds,
                  :rule_id, :rule_match_type, :samples, :historical_probability, :fair_price,
                  :safety_buffer, :max_buy_price, :market_ask, :edge_vs_ask, :min_edge_required,
                  :recommended_side, :return_aligned,
                  :requested_size_usd, :max_position_usd, :open_positions_count, :max_open_positions,
                  :daily_realized_pnl, :max_daily_loss_usd, :risk_allowed, :risk_reject_reason
                )
                """,
                _decision_row(snap),
            )
        if self._strategy_id is not None:
            self._emit(
                "write_snapshot_if_changed",
                strategy_id=self._strategy_id,
                captured_at=snap.timestamp_utc,
                operational_status="healthy" if snap.risk_allowed else "degraded",
                risk_severity=("high" if not snap.risk_allowed else "low"),
                activity_state=("active" if snap.decision.value == "TRADE" else "pending"),
                freshness="live",
                current_market=snap.market_slug,
                open_positions=snap.open_positions_count,
                pnl_amount=_dec(snap.daily_realized_pnl),
                open_exposure_amount=(
                    _dec(snap.requested_size_usd) if snap.decision.value == "TRADE" else Decimal("0")
                ),
                current_market_count=1,
            )

    # === Positions ===

    def upsert_position(self, pos: PaperPosition) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO paper_positions VALUES (
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
                _position_row(pos),
            )
        if self._strategy_id is not None:
            self._emit(
                "write_position",
                external_position_id=pos.position_id,
                strategy_id=self._strategy_id,
                market_slug=pos.market_slug,
                side=pos.selected_side.value,
                status=pos.status.value,
                size=pos.shares,
                average_price=_dec(pos.entry_price),
                opened_at=pos.entry_timestamp_utc,
                closed_at=None,
                updated_at=datetime.now(UTC),
                raw_payload=_position_row(pos),
            )
            # Mirror the paper order itself so the control plane
            # /api/orders table can show it. Paper-broker logic
            # collapses order → position (one decision = one
            # paper_position = one paper_order), so the natural key
            # is the decision_id. This emit is best-effort and
            # never affects trading: it is gated on the same
            # telemetry_writer + strategy_id check as positions and
            # failures are swallowed by _emit().
            self._emit(
                "write_order",
                external_order_id=pos.decision_id,
                strategy_id=self._strategy_id,
                market_slug=pos.market_slug,
                side=pos.selected_side.value,
                status=pos.status.value,
                price=pos.entry_price,
                size=pos.shares,
                created_at=pos.entry_timestamp_utc,
                source_created_at=pos.entry_timestamp_utc,
                updated_at=datetime.now(UTC),
                raw_payload=_position_row(pos),
            )

    def get_position(self, position_id: str) -> PaperPosition | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM paper_positions WHERE position_id=?", (position_id,)
            ).fetchone()
        if not row:
            return None
        return PaperPosition.model_validate(dict(row))

    def list_open_positions(self) -> list[PaperPosition]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE status=?",
                (PositionStatus.OPEN.value,),
            ).fetchall()
        return [PaperPosition.model_validate(dict(r)) for r in rows]

    def list_all_positions(self) -> list[PaperPosition]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM paper_positions").fetchall()
        return [PaperPosition.model_validate(dict(r)) for r in rows]

    # === Audit queries (defence-in-depth, 2026-06-07) ===

    def audit_duplicates(self) -> dict[str, list[dict[str, Any]]]:
        """Find potential duplicate positions.

        Returns three lists:
        - open_by_market: market_slug with >1 OPEN positions (should be empty
          after the 2026-06-07 fix; surfaces lingering duplicates from earlier
          runs).
        - lifetime_by_market: market_slug with >1 paper_positions rows across
          all statuses (lifetime duplicates — these are the rows that would
          have prevented the partial unique index from being created on a
          legacy DB).
        - rapid_trade_decisions: pairs of TRADE decisions on the same slug
          whose timestamps differ by <5 seconds (signals a near-race).
        """
        result: dict[str, list[dict[str, Any]]] = {
            "open_by_market": [],
            "lifetime_by_market": [],
            "rapid_trade_decisions": [],
        }
        with self._conn() as conn:
            for row in conn.execute(
                """
                SELECT market_slug, COUNT(*) AS n
                FROM paper_positions
                WHERE status = 'OPEN'
                GROUP BY market_slug
                HAVING n > 1
                ORDER BY n DESC
                """
            ).fetchall():
                result["open_by_market"].append(
                    {"market_slug": row["market_slug"], "open_count": row["n"]}
                )
            for row in conn.execute(
                """
                SELECT market_slug, COUNT(*) AS n,
                       SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) AS open_n
                FROM paper_positions
                GROUP BY market_slug
                HAVING n > 1
                ORDER BY n DESC
                """
            ).fetchall():
                result["lifetime_by_market"].append(
                    {
                        "market_slug": row["market_slug"],
                        "total_count": row["n"],
                        "open_count": row["open_n"],
                    }
                )
            # Rapid TRADE decisions: <5 seconds apart on the same slug.
            for row in conn.execute(
                """
                WITH ranked AS (
                  SELECT decision_id, market_slug, timestamp_utc,
                         LAG(timestamp_utc) OVER (
                           PARTITION BY market_slug ORDER BY timestamp_utc
                         ) AS prev_ts,
                         LAG(decision_id) OVER (
                           PARTITION BY market_slug ORDER BY timestamp_utc
                         ) AS prev_id
                  FROM decisions
                  WHERE decision = 'TRADE'
                )
                SELECT decision_id, market_slug, timestamp_utc,
                       prev_id, prev_ts,
                       (julianday(timestamp_utc) - julianday(prev_ts)) * 86400.0
                         AS seconds_apart
                FROM ranked
                WHERE prev_ts IS NOT NULL
                  AND (julianday(timestamp_utc) - julianday(prev_ts)) * 86400.0 < 5
                ORDER BY timestamp_utc
                """
            ).fetchall():
                result["rapid_trade_decisions"].append(
                    {
                        "decision_id": row["decision_id"],
                        "market_slug": row["market_slug"],
                        "timestamp_utc": row["timestamp_utc"],
                        "previous_decision_id": row["prev_id"],
                        "previous_timestamp_utc": row["prev_ts"],
                        "seconds_apart": row["seconds_apart"],
                    }
                )
        return result

    # === Mark-to-market ===

    def insert_mtm(self, mtm: MarkToMarket) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO mark_to_market_snapshots
                  (position_id, timestamp_utc, best_bid, best_ask, mid_price,
                   estimated_exit_value_bid, unrealized_pnl_bid, btc_price,
                   distance_from_round_open, seconds_to_expiry)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    mtm.position_id,
                    mtm.timestamp_utc.isoformat(),
                    _dec(mtm.best_bid),
                    _dec(mtm.best_ask),
                    _dec(mtm.mid_price),
                    _dec(mtm.estimated_exit_value_bid),
                    _dec(mtm.unrealized_pnl_bid),
                    _dec(mtm.btc_price),
                    _dec(mtm.distance_from_round_open),
                    mtm.seconds_to_expiry,
                ),
            )

    def list_mtm(self, position_id: str) -> list[MarkToMarket]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM mark_to_market_snapshots WHERE position_id=? ORDER BY timestamp_utc",
                (position_id,),
            ).fetchall()
        return [MarkToMarket.model_validate(dict(r)) for r in rows]

    # === Settlements ===

    def insert_settlement(self, s: Settlement) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO settlements VALUES (
                  :settlement_id, :position_id, :market_slug,
                  :resolved_outcome, :selected_side, :won,
                  :entry_price, :shares, :cost_usd, :payout_usd,
                  :realized_pnl_usd, :realized_roi_pct, :settlement_source,
                  :round_open_price, :round_close_price, :final_btc_price,
                  :resolved_at_utc, :trade_quality,
                  :edge_at_entry, :spread_at_entry, :rule_id,
                  :historical_probability_at_entry, :seconds_to_expiry_at_entry
                )
                """,
                _settlement_row(s),
            )
        if self._strategy_id is not None:
            self._emit(
                "write_settlement",
                external_settlement_id=s.settlement_id,
                strategy_id=self._strategy_id,
                market_slug=s.market_slug,
                side=s.selected_side.value,
                winner=s.resolved_outcome.value,
                entry_price=_dec(s.entry_price),
                size=s.shares,
                realized_pnl=_dec(s.realized_pnl_usd),
                result=("win" if s.won else "loss"),
                settled_at=s.resolved_at_utc,
                source=s.settlement_source.value,
                raw_payload=_settlement_row(s),
            )

    def list_settlements(self, since_iso: str | None = None) -> list[Settlement]:
        sql = "SELECT * FROM settlements"
        params: tuple[str, ...] = ()
        if since_iso:
            sql += " WHERE resolved_at_utc >= ?"
            params = (since_iso,)
        sql += " ORDER BY resolved_at_utc"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Settlement.model_validate({**dict(r), "won": bool(r["won"])}) for r in rows]

    def list_decisions(self, since_iso: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM decisions"
        params: tuple[str, ...] = ()
        if since_iso:
            sql += " WHERE timestamp_utc >= ?"
            params = (since_iso,)
        sql += " ORDER BY timestamp_utc"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def _decision_row(s: DecisionSnapshot) -> dict[str, Any]:
    d = s.to_dict()
    for k in (
        "return_aligned",
        "market_active",
        "market_closed",
        "market_accepting_orders",
        "risk_allowed",
    ):
        d[k] = 1 if d.get(k) else 0
    return d


def _position_row(p: PaperPosition) -> dict[str, Any]:
    return p.model_dump(mode="json")


def _settlement_row(s: Settlement) -> dict[str, Any]:
    d = s.model_dump(mode="json")
    d["won"] = 1 if s.won else 0
    return d
