"""
Trade Journal — Phase 9: Persistent trade analytics with SQLite backend.

Records every trade with full context (entry/exit reasons, indicators,
confidence scores, regime) for adaptive learning and performance analysis.

Usage:
    from core.trade_journal import trade_journal
    trade_journal.record(journal_entry)
    report = trade_journal.performance_summary()
    df = trade_journal.query(strategy="ema_supertrend", regime="TRENDING_UP")
"""
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from config.settings import db_config
from core.models import TradeJournalEntry
from utils.logger import get_logger

logger = get_logger("trade_journal")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        TEXT,
    symbol          TEXT,
    direction       TEXT,
    strategy_name   TEXT,
    entry_price     REAL,
    entry_time      TEXT,
    entry_reason    TEXT,
    confidence_score REAL,
    trade_score     INTEGER,
    market_regime   TEXT,
    higher_tf_alignment TEXT,
    confirmations   TEXT,
    quantity        INTEGER,
    stop_loss       REAL,
    take_profit     REAL,
    risk_amount     REAL,
    risk_reward_ratio REAL,
    exit_price      REAL,
    exit_time       TEXT,
    exit_reason     TEXT,
    pnl             REAL,
    pnl_pct         REAL,
    holding_minutes REAL,
    max_favorable_excursion REAL,
    max_adverse_excursion   REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_strategy ON trades(strategy_name);
CREATE INDEX IF NOT EXISTS idx_regime ON trades(market_regime);
CREATE INDEX IF NOT EXISTS idx_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_entry_time ON trades(entry_time);
"""


class TradeJournal:
    """
    Thread-safe SQLite-backed trade journal.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = Path(db_path or db_config.journal_db_path)
        self._lock = threading.RLock()
        self._enabled = db_config.enabled
        if self._enabled:
            self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Returns a new connection to the database."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        try:
            with self._lock:
                conn = self._get_conn()
                conn.executescript(_SCHEMA)
                conn.commit()
                conn.close()
            logger.info(f"Trade journal initialized: {self._db_path}")
        except Exception as e:
            logger.error(f"Trade journal init failed: {e}")
            self._enabled = False

    def record(self, entry: TradeJournalEntry) -> None:
        """Record a completed trade entry."""
        if not self._enabled:
            return
        try:
            import json
            with self._lock:
                conn = self._get_conn()
                conn.execute("""
                    INSERT INTO trades (
                        trade_id, symbol, direction, strategy_name,
                        entry_price, entry_time, entry_reason, confidence_score,
                        trade_score, market_regime, higher_tf_alignment,
                        confirmations, quantity, stop_loss, take_profit,
                        risk_amount, risk_reward_ratio,
                        exit_price, exit_time, exit_reason,
                        pnl, pnl_pct, holding_minutes,
                        max_favorable_excursion, max_adverse_excursion
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    entry.trade_id, entry.symbol, entry.direction, entry.strategy_name,
                    entry.entry_price, entry.entry_time, entry.entry_reason,
                    entry.confidence_score, entry.trade_score, entry.market_regime,
                    entry.higher_tf_alignment,
                    json.dumps(entry.confirmations),
                    entry.quantity, entry.stop_loss, entry.take_profit,
                    entry.risk_amount, entry.risk_reward_ratio,
                    entry.exit_price, entry.exit_time, entry.exit_reason,
                    entry.pnl, entry.pnl_pct, entry.holding_minutes,
                    entry.max_favorable_excursion, entry.max_adverse_excursion,
                ))
                conn.commit()
                conn.close()
        except Exception as e:
            logger.error(f"Trade journal record failed: {e}")

    def query(
        self,
        strategy: Optional[str] = None,
        regime: Optional[str] = None,
        symbol: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict]:
        """Query trades with optional filters. Returns list of dicts."""
        if not self._enabled:
            return []
        try:
            conditions = []
            params = []
            if strategy:
                conditions.append("strategy_name = ?")
                params.append(strategy)
            if regime:
                conditions.append("market_regime = ?")
                params.append(regime)
            if symbol:
                conditions.append("symbol = ?")
                params.append(symbol)
            if date_from:
                conditions.append("entry_time >= ?")
                params.append(date_from)
            if date_to:
                conditions.append("entry_time <= ?")
                params.append(date_to)

            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            sql = f"SELECT * FROM trades {where} ORDER BY entry_time DESC LIMIT ?"
            params.append(limit)

            with self._lock:
                conn = self._get_conn()
                rows = conn.execute(sql, params).fetchall()
                conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Trade journal query failed: {e}")
            return []

    def performance_summary(self, strategy: Optional[str] = None) -> dict:
        """
        Returns aggregated performance metrics.
        If strategy is specified, filters to that strategy only.
        """
        if not self._enabled:
            return {}
        try:
            where = "WHERE strategy_name = ?" if strategy else ""
            params = [strategy] if strategy else []
            sql = f"""
                SELECT
                    strategy_name,
                    market_regime,
                    COUNT(*) AS total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) AS losses,
                    ROUND(SUM(pnl), 2) AS total_pnl,
                    ROUND(AVG(pnl), 2) AS avg_pnl,
                    ROUND(AVG(confidence_score), 3) AS avg_confidence,
                    ROUND(AVG(trade_score), 1) AS avg_trade_score,
                    ROUND(AVG(holding_minutes), 1) AS avg_holding_min
                FROM trades {where}
                GROUP BY strategy_name, market_regime
                ORDER BY strategy_name, total_trades DESC
            """
            with self._lock:
                conn = self._get_conn()
                rows = conn.execute(sql, params).fetchall()
                conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Trade journal summary failed: {e}")
            return {}

    def export_csv(self, path: str) -> bool:
        """Export all trades to a CSV file."""
        if not self._enabled:
            return False
        try:
            records = self.query(limit=100000)
            df = pd.DataFrame(records)
            df.to_csv(path, index=False)
            logger.info(f"Trade journal exported to {path}")
            return True
        except Exception as e:
            logger.error(f"Trade journal export failed: {e}")
            return False

    def get_today_trades(self) -> List[Dict]:
        """Returns all trades from today."""
        from datetime import date
        today = date.today().isoformat()
        return self.query(date_from=today, limit=100)

    def get_stats_by_time_of_day(self) -> List[Dict]:
        """Returns win rate broken down by hour of day."""
        if not self._enabled:
            return []
        try:
            sql = """
                SELECT
                    SUBSTR(entry_time, 12, 2) AS hour_of_day,
                    COUNT(*) AS total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl), 2) AS avg_pnl
                FROM trades
                GROUP BY hour_of_day
                ORDER BY hour_of_day
            """
            with self._lock:
                conn = self._get_conn()
                rows = conn.execute(sql).fetchall()
                conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Time-of-day stats failed: {e}")
            return []


# Module-level singleton
trade_journal = TradeJournal()
