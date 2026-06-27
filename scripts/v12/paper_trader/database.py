"""
database.py — SQLite persistence layer for V12 paper trading.

Tables:
  - signals:    Every 5m candle prediction + decision
  - trades:     Open/close events with PnL
  - equity:     Equity snapshots over time
  - predictions: Raw model predictions for drift detection
  - model_versions: Model deployment history with acceptance gates

Design principles:
  - All timestamps in UTC milliseconds (consistent with Bybit API)
  - No pd.to_datetime — use integer arithmetic only
  - Single DB file per symbol for concurrency safety
  - Atomic operations via SQLite transactions
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import datetime as dt
from pathlib import Path
from typing import Any

LOG = logging.getLogger("v12_db")

DB_DIR = Path(__file__).resolve().parents[3] / "data" / "paper_trading" / "v12_db"
DB_DIR.mkdir(parents=True, exist_ok=True)


def _ts_to_iso(ts_ms: int) -> str:
    """Convert ms timestamp to ISO string without pd.to_datetime."""
    return dt.datetime.utcfromtimestamp(ts_ms / 1000).isoformat()


# ============================================================================
# Schema
# ============================================================================

SCHEMA_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          INTEGER NOT NULL,         -- UTC ms timestamp
    ts_iso          TEXT NOT NULL,             -- ISO string for readability
    symbol          TEXT NOT NULL,             -- SOL, DOGE, AVAX
    close           REAL NOT NULL,             -- Close price
    pred            REAL NOT NULL,             -- P(UP in 1h)
    decision        TEXT NOT NULL,             -- LONG, SHORT, WAIT
    q_high          REAL NOT NULL DEFAULT 0,   -- Rolling quantile high
    q_low           REAL NOT NULL DEFAULT 0,   -- Rolling quantile low
    direction_mode  TEXT NOT NULL DEFAULT 'both',
    trend_filter    TEXT NOT NULL DEFAULT 'none',
    action          TEXT NOT NULL,             -- OPEN_LONG, CLOSE_SHORT, HOLD, etc.
    position_side   TEXT NOT NULL DEFAULT '',
    position_bars_held INTEGER NOT NULL DEFAULT 0,
    entry_price     REAL NOT NULL DEFAULT 0,
    exit_price      REAL NOT NULL DEFAULT 0,
    pnl_pct         REAL NOT NULL DEFAULT 0,
    cost_pct        REAL NOT NULL DEFAULT 0,
    pnl_net_pct     REAL NOT NULL DEFAULT 0,
    equity_pct      REAL NOT NULL DEFAULT 0,
    model_version   TEXT NOT NULL DEFAULT '',  -- Which model version produced this
    UNIQUE(ts_utc, symbol)                     -- One signal per candle per symbol
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts_utc);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
"""

SCHEMA_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,             -- LONG or SHORT
    entry_ts        INTEGER NOT NULL,          -- Entry timestamp ms
    entry_ts_iso    TEXT NOT NULL,
    entry_price     REAL NOT NULL,             -- Entry price
    exit_ts         INTEGER,                   -- Exit timestamp ms (NULL if open)
    exit_ts_iso     TEXT,                       -- Exit ISO string
    exit_price      REAL,                       -- Exit price (NULL if open)
    pnl_pct         REAL,                       -- Gross PnL %
    cost_pct        REAL NOT NULL DEFAULT 0.04, -- Trading cost %
    pnl_net_pct     REAL,                       -- Net PnL %
    bars_held       INTEGER,                    -- How many 5m bars held
    exit_reason     TEXT,                        -- 'horizon', 'reverse', 'manual'
    model_version   TEXT NOT NULL DEFAULT '',
    equity_after    REAL NOT NULL DEFAULT 0     -- Equity after this trade
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_entry_ts ON trades(entry_ts);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(exit_ts);  -- open trades have NULL exit_ts
"""

SCHEMA_EQUITY = """
CREATE TABLE IF NOT EXISTS equity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          INTEGER NOT NULL,
    ts_iso          TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    equity_pct      REAL NOT NULL DEFAULT 0,
    n_trades        INTEGER NOT NULL DEFAULT 0,
    n_wins          INTEGER NOT NULL DEFAULT 0,
    win_rate        REAL NOT NULL DEFAULT 0,
    drawdown_pct    REAL NOT NULL DEFAULT 0,     -- Current drawdown from peak
    position_side   TEXT NOT NULL DEFAULT '',
    unrealized_pnl  REAL NOT NULL DEFAULT 0,     -- If position open
    model_version   TEXT NOT NULL DEFAULT '',
    UNIQUE(ts_utc, symbol)
);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(ts_utc);
CREATE INDEX IF NOT EXISTS idx_equity_symbol ON equity(symbol);
"""

SCHEMA_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          INTEGER NOT NULL,
    ts_iso          TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    pred            REAL NOT NULL,             -- Raw P(UP)
    trend_1h        REAL NOT NULL DEFAULT 0,
    vol_regime_1h   REAL NOT NULL DEFAULT 0,   -- Volatility regime
    rsi_14          REAL NOT NULL DEFAULT 0,   -- RSI at prediction time
    actual_outcome  INTEGER,                    -- 1=UP, 0=DOWN (filled after horizon)
    actual_return   REAL,                        -- Actual forward return %
    outcome_ts      INTEGER,                     -- When outcome was determined
    model_version   TEXT NOT NULL DEFAULT '',
    UNIQUE(ts_utc, symbol)
);
CREATE INDEX IF NOT EXISTS idx_predictions_ts ON predictions(ts_utc);
CREATE INDEX IF NOT EXISTS idx_predictions_outcome ON predictions(actual_outcome);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol ON predictions(symbol);
"""

SCHEMA_MODEL_VERSIONS = """
CREATE TABLE IF NOT EXISTS model_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version         TEXT NOT NULL UNIQUE,       -- e.g. "v11_SOL_h12_v1", "v12_SOL_h12_20260627"
    symbol          TEXT NOT NULL,
    model_path      TEXT NOT NULL,              -- Path to .txt model file
    deployed_at     INTEGER NOT NULL,           -- Deploy timestamp ms
    deployed_at_iso TEXT NOT NULL,
    auc_val         REAL,                       -- Validation AUC
    dir_acc_val     REAL,                       -- Directional accuracy on val
    logloss_val     REAL,                       -- Log loss on val
    n_train         INTEGER,                    -- Training rows
    n_val           INTEGER,                    -- Validation rows
    acceptance_decision TEXT NOT NULL,           -- FIRST_DEPLOY, ACCEPT, REJECT, ACCEPT_WITH_WARNING
    delta_auc       REAL,                       -- AUC change vs previous
    training_window_days INTEGER,               -- How many days of data used
    wf_win_rate     REAL,                       -- Walk-forward win rate
    wf_sharpe       REAL,                       -- Walk-forward Sharpe
    wf_pnl_pct      REAL,                       -- Walk-forward PnL
    is_active       INTEGER NOT NULL DEFAULT 1, -- Currently deployed?
    retired_at      INTEGER                     -- When retired (ms)
);
CREATE INDEX IF NOT EXISTS idx_model_versions_symbol ON model_versions(symbol);
CREATE INDEX IF NOT EXISTS idx_model_versions_active ON model_versions(is_active);
"""

SCHEMA_DRIFT_EVENTS = """
CREATE TABLE IF NOT EXISTS drift_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          INTEGER NOT NULL,
    ts_iso          TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    drift_type      TEXT NOT NULL,              -- 'wr_decline', 'pred_shift', 'sharpe_decline', 'regime_change'
    severity        TEXT NOT NULL,              -- 'warning', 'critical'
    metric_name     TEXT NOT NULL,              -- e.g. 'win_rate_24h'
    current_value   REAL NOT NULL,
    baseline_value  REAL NOT NULL,
    delta           REAL NOT NULL,              -- current - baseline
    threshold       REAL NOT NULL,              -- Threshold that was crossed
    recommendation  TEXT NOT NULL DEFAULT '',    -- e.g. 'retrain_recommended'
    model_version   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_drift_ts ON drift_events(ts_utc);
CREATE INDEX IF NOT EXISTS idx_drift_symbol ON drift_events(symbol);
"""

ALL_SCHEMAS = [
    SCHEMA_SIGNALS, SCHEMA_TRADES, SCHEMA_EQUITY,
    SCHEMA_PREDICTIONS, SCHEMA_MODEL_VERSIONS, SCHEMA_DRIFT_EVENTS,
]


# ============================================================================
# TradeDB class
# ============================================================================

class TradeDB:
    """SQLite database for V12 paper trading data."""

    def __init__(self, symbol: str):
        """Initialize database for a specific symbol.

        Args:
            symbol: Token symbol without /USDT (e.g. "SOL")
        """
        self.symbol = symbol.replace("/USDT", "").replace("/usdt", "")
        db_path = DB_DIR / f"v12_{self.symbol}.db"
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=30)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_schema(self) -> None:
        """Create all tables if they don't exist."""
        conn = self._connect()
        for schema in ALL_SCHEMAS:
            conn.executescript(schema)
        conn.commit()
        LOG.debug("TradeDB: schema ensured for %s at %s", self.symbol, self.db_path)

    def close(self) -> None:
        """Close database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ----------------------------------------------------------------
    # Signal logging
    # ----------------------------------------------------------------

    def insert_signal(self, row: dict) -> None:
        """Insert a signal row (one per 5m candle)."""
        conn = self._connect()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO signals (
                    ts_utc, ts_iso, symbol, close, pred, decision,
                    q_high, q_low, direction_mode, trend_filter, action,
                    position_side, position_bars_held, entry_price, exit_price,
                    pnl_pct, cost_pct, pnl_net_pct, equity_pct, model_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["ts_utc"], row.get("ts_iso", _ts_to_iso(row["ts_utc"])),
                self.symbol, row["close"], row["pred"], row["decision"],
                row.get("q_high", 0), row.get("q_low", 0),
                row.get("direction_mode", "both"), row.get("trend_filter", "none"),
                row["action"], row.get("position_side", ""),
                row.get("position_bars_held", 0), row.get("entry_price", 0),
                row.get("exit_price", 0), row.get("pnl_pct", 0),
                row.get("cost_pct", 0), row.get("pnl_net_pct", 0),
                row.get("equity_pct", 0), row.get("model_version", ""),
            ))
            conn.commit()
        except Exception as e:
            LOG.error("TradeDB: insert_signal failed: %s", e)
            conn.rollback()

    # ----------------------------------------------------------------
    # Trade logging
    # ----------------------------------------------------------------

    def open_trade(self, side: str, entry_ts: int, entry_price: float,
                   model_version: str = "") -> int:
        """Record trade opening. Returns trade ID."""
        conn = self._connect()
        try:
            cursor = conn.execute("""
                INSERT INTO trades (symbol, side, entry_ts, entry_ts_iso, entry_price,
                                    model_version)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                self.symbol, side, entry_ts, _ts_to_iso(entry_ts),
                entry_price, model_version,
            ))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            LOG.error("TradeDB: open_trade failed: %s", e)
            conn.rollback()
            return -1

    def close_trade(self, trade_id: int, exit_ts: int, exit_price: float,
                    pnl_pct: float, cost_pct: float, pnl_net_pct: float,
                    bars_held: int, exit_reason: str, equity_after: float = 0) -> None:
        """Record trade closing."""
        conn = self._connect()
        try:
            conn.execute("""
                UPDATE trades SET
                    exit_ts = ?, exit_ts_iso = ?, exit_price = ?,
                    pnl_pct = ?, cost_pct = ?, pnl_net_pct = ?,
                    bars_held = ?, exit_reason = ?, equity_after = ?
                WHERE id = ?
            """, (
                exit_ts, _ts_to_iso(exit_ts), exit_price,
                pnl_pct, cost_pct, pnl_net_pct,
                bars_held, exit_reason, equity_after,
                trade_id,
            ))
            conn.commit()
        except Exception as e:
            LOG.error("TradeDB: close_trade failed: %s", e)
            conn.rollback()

    def get_open_trade(self) -> dict | None:
        """Get the current open trade (if any)."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM trades WHERE symbol = ? AND exit_ts IS NULL ORDER BY id DESC LIMIT 1",
            (self.symbol,)
        ).fetchone()
        return dict(row) if row else None

    def get_trades(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Get closed trades, most recent first."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM trades WHERE symbol = ? AND exit_ts IS NOT NULL "
            "ORDER BY exit_ts DESC LIMIT ? OFFSET ?",
            (self.symbol, limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------
    # Equity snapshots
    # ----------------------------------------------------------------

    def insert_equity(self, row: dict) -> None:
        """Insert equity snapshot."""
        conn = self._connect()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO equity (
                    ts_utc, ts_iso, symbol, equity_pct, n_trades, n_wins,
                    win_rate, drawdown_pct, position_side, unrealized_pnl,
                    model_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["ts_utc"], row.get("ts_iso", _ts_to_iso(row["ts_utc"])),
                self.symbol, row.get("equity_pct", 0),
                row.get("n_trades", 0), row.get("n_wins", 0),
                row.get("win_rate", 0), row.get("drawdown_pct", 0),
                row.get("position_side", ""), row.get("unrealized_pnl", 0),
                row.get("model_version", ""),
            ))
            conn.commit()
        except Exception as e:
            LOG.error("TradeDB: insert_equity failed: %s", e)
            conn.rollback()

    # ----------------------------------------------------------------
    # Predictions (for drift detection)
    # ----------------------------------------------------------------

    def insert_prediction(self, ts_utc: int, pred: float,
                          trend_1h: float = 0, vol_regime_1h: float = 0,
                          rsi_14: float = 0, model_version: str = "") -> None:
        """Insert raw prediction for drift detection."""
        conn = self._connect()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO predictions (
                    ts_utc, ts_iso, symbol, pred, trend_1h, vol_regime_1h,
                    rsi_14, model_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ts_utc, _ts_to_iso(ts_utc), self.symbol,
                pred, trend_1h, vol_regime_1h, rsi_14, model_version,
            ))
            conn.commit()
        except Exception as e:
            LOG.error("TradeDB: insert_prediction failed: %s", e)
            conn.rollback()

    def backfill_outcomes(self, horizon_bars: int = 12) -> int:
        """Fill actual_outcome for predictions where enough time has passed.

        For each prediction without an outcome, check if the candle
        `horizon_bars * 5min` later exists in signals, and set outcome.

        Returns number of outcomes filled.
        """
        horizon_ms = horizon_bars * 5 * 60 * 1000  # 12 * 5min = 1h in ms
        conn = self._connect()
        now_ms = int(time.time() * 1000)

        # Find predictions without outcomes that are old enough
        rows = conn.execute("""
            SELECT p.id, p.ts_utc, s.close as entry_close
            FROM predictions p
            LEFT JOIN signals s ON p.ts_utc = s.ts_utc AND p.symbol = s.symbol
            WHERE p.symbol = ? AND p.actual_outcome IS NULL
              AND p.ts_utc < ? - ?
            ORDER BY p.ts_utc
        """, (self.symbol, now_ms, horizon_ms)).fetchall()

        filled = 0
        for r in rows:
            future_ts = r["ts_utc"] + horizon_ms
            # Find the signal at the future timestamp (or closest)
            future = conn.execute("""
                SELECT close FROM signals
                WHERE symbol = ? AND ts_utc >= ? AND ts_utc <= ? + 600000
                ORDER BY ts_utc LIMIT 1
            """, (self.symbol, future_ts, future_ts)).fetchone()

            if future is None:
                continue

            entry_close = r["entry_close"] if r["entry_close"] else 0
            if entry_close == 0:
                continue

            future_close = future["close"]
            actual_return = (future_close - entry_close) / entry_close * 100
            actual_outcome = 1 if future_close > entry_close else 0

            conn.execute("""
                UPDATE predictions SET
                    actual_outcome = ?, actual_return = ?, outcome_ts = ?
                WHERE id = ?
            """, (actual_outcome, actual_return, future_ts, r["id"]))
            filled += 1

        if filled > 0:
            conn.commit()
            LOG.info("TradeDB: backfilled %d prediction outcomes for %s", filled, self.symbol)

        return filled

    def get_recent_predictions(self, n: int = 200) -> list[dict]:
        """Get N most recent predictions."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM predictions WHERE symbol = ? ORDER BY ts_utc DESC LIMIT ?",
            (self.symbol, n)
        ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------
    # Model versions
    # ----------------------------------------------------------------

    def register_model_version(self, version: str, model_path: str,
                               metrics: dict) -> None:
        """Register a new model version deployment."""
        conn = self._connect()
        now_ms = int(time.time() * 1000)

        # Retire previous active version
        conn.execute("""
            UPDATE model_versions SET is_active = 0, retired_at = ?
            WHERE symbol = ? AND is_active = 1
        """, (now_ms, self.symbol))

        conn.execute("""
            INSERT INTO model_versions (
                version, symbol, model_path, deployed_at, deployed_at_iso,
                auc_val, dir_acc_val, logloss_val, n_train, n_val,
                acceptance_decision, delta_auc, training_window_days,
                wf_win_rate, wf_sharpe, wf_pnl_pct, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            version, self.symbol, model_path,
            now_ms, _ts_to_iso(now_ms),
            metrics.get("auc_val"), metrics.get("dir_acc_val"),
            metrics.get("logloss_val"), metrics.get("n_train"),
            metrics.get("n_val"), metrics.get("acceptance_decision", "FIRST_DEPLOY"),
            metrics.get("delta_auc", 0), metrics.get("training_window_days", 0),
            metrics.get("wf_win_rate"), metrics.get("wf_sharpe"),
            metrics.get("wf_pnl_pct"),
        ))
        conn.commit()
        LOG.info("TradeDB: registered model version %s for %s", version, self.symbol)

    def get_active_model_version(self) -> str:
        """Get the currently active model version string."""
        conn = self._connect()
        row = conn.execute(
            "SELECT version FROM model_versions WHERE symbol = ? AND is_active = 1 "
            "ORDER BY deployed_at DESC LIMIT 1",
            (self.symbol,)
        ).fetchone()
        return row["version"] if row else ""

    def get_model_history(self, limit: int = 10) -> list[dict]:
        """Get model version history."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM model_versions WHERE symbol = ? "
            "ORDER BY deployed_at DESC LIMIT ?",
            (self.symbol, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------
    # Drift events
    # ----------------------------------------------------------------

    def insert_drift_event(self, event: dict) -> None:
        """Insert a drift detection event."""
        conn = self._connect()
        ts = event.get("ts_utc", int(time.time() * 1000))
        conn.execute("""
            INSERT INTO drift_events (
                ts_utc, ts_iso, symbol, drift_type, severity,
                metric_name, current_value, baseline_value, delta,
                threshold, recommendation, model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts, _ts_to_iso(ts), self.symbol,
            event["drift_type"], event["severity"],
            event["metric_name"], event["current_value"],
            event["baseline_value"], event["delta"],
            event["threshold"], event.get("recommendation", ""),
            event.get("model_version", ""),
        ))
        conn.commit()
        LOG.warning("TradeDB: drift event — %s/%s %s: current=%.4f baseline=%.4f delta=%.4f",
                    event["drift_type"], event["severity"], event["metric_name"],
                    event["current_value"], event["baseline_value"], event["delta"])

    def get_drift_events(self, limit: int = 20) -> list[dict]:
        """Get recent drift events."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM drift_events WHERE symbol = ? "
            "ORDER BY ts_utc DESC LIMIT ?",
            (self.symbol, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------
    # Query helpers (for metrics.py)
    # ----------------------------------------------------------------

    def get_trade_stats(self, last_n: int | None = None) -> dict:
        """Get aggregate trade statistics.

        Returns dict with: n_trades, n_wins, win_rate, avg_pnl, total_pnl,
        max_win, max_loss, profit_factor, sharpe, max_drawdown, avg_bars_held,
        n_long, n_short, wr_long, wr_short.
        """
        conn = self._connect()
        if last_n:
            rows = conn.execute(
                "SELECT * FROM trades WHERE symbol = ? AND exit_ts IS NOT NULL "
                "ORDER BY exit_ts DESC LIMIT ?",
                (self.symbol, last_n)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades WHERE symbol = ? AND exit_ts IS NOT NULL "
                "ORDER BY exit_ts",
                (self.symbol,)
            ).fetchall()

        if not rows:
            return {
                "n_trades": 0, "n_wins": 0, "win_rate": 0,
                "avg_pnl": 0, "total_pnl": 0, "max_win": 0, "max_loss": 0,
                "profit_factor": 0, "sharpe": 0, "max_drawdown": 0,
                "avg_bars_held": 0, "n_long": 0, "n_short": 0,
                "wr_long": 0, "wr_short": 0,
            }

        pnls = [r["pnl_net_pct"] for r in rows if r["pnl_net_pct"] is not None]
        n_trades = len(pnls)
        n_wins = sum(1 for p in pnls if p > 0)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        # Long/short breakdown
        longs = [r for r in rows if r["side"] == "LONG"]
        shorts = [r for r in rows if r["side"] == "SHORT"]
        long_wins = sum(1 for r in longs if (r["pnl_net_pct"] or 0) > 0)
        short_wins = sum(1 for r in shorts if (r["pnl_net_pct"] or 0) > 0)

        # Max drawdown from cumulative PnL
        if pnls:
            cum = []
            s = 0
            for p in pnls:
                s += p
                cum.append(s)
            running_max = []
            m = cum[0]
            for c in cum:
                m = max(m, c)
                running_max.append(m)
            dd = [c - rm for c, rm in zip(cum, running_max)]
            max_dd = min(dd) if dd else 0
        else:
            max_dd = 0

        # Sharpe-like ratio (annualized assuming ~6 trades/day from 5m signals)
        if len(pnls) > 1:
            import numpy as np
            mean_pnl = np.mean(pnls)
            std_pnl = np.std(pnls)
            sharpe = (mean_pnl / std_pnl * (6 * 365) ** 0.5) if std_pnl > 0 else 0
        else:
            sharpe = 0

        return {
            "n_trades": n_trades,
            "n_wins": n_wins,
            "win_rate": n_wins / n_trades if n_trades > 0 else 0,
            "avg_pnl": sum(pnls) / n_trades if n_trades > 0 else 0,
            "total_pnl": sum(pnls),
            "max_win": max(pnls) if pnls else 0,
            "max_loss": min(pnls) if pnls else 0,
            "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (99 if wins else 0),
            "sharpe": float(sharpe),
            "max_drawdown": max_dd,
            "avg_bars_held": sum(r["bars_held"] or 0 for r in rows) / n_trades if n_trades > 0 else 0,
            "n_long": len(longs),
            "n_short": len(shorts),
            "wr_long": long_wins / len(longs) if longs else 0,
            "wr_short": short_wins / len(shorts) if shorts else 0,
        }

    def get_equity_curve(self, limit: int | None = None) -> list[dict]:
        """Get equity curve for plotting."""
        conn = self._connect()
        if limit:
            rows = conn.execute(
                "SELECT * FROM equity WHERE symbol = ? ORDER BY ts_utc DESC LIMIT ?",
                (self.symbol, limit)
            ).fetchall()
            return list(reversed([dict(r) for r in rows]))
        rows = conn.execute(
            "SELECT * FROM equity WHERE symbol = ? ORDER BY ts_utc",
            (self.symbol,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_prediction_accuracy(self, hours: int = 24) -> dict:
        """Get prediction accuracy over the last N hours.

        Returns dict with: n_predicted, n_with_outcome, accuracy,
        avg_pred_up, actual_up_pct, calibration_error.
        """
        conn = self._connect()
        since_ms = int(time.time() * 1000) - hours * 3600 * 1000

        rows = conn.execute("""
            SELECT * FROM predictions
            WHERE symbol = ? AND ts_utc >= ?
            ORDER BY ts_utc
        """, (self.symbol, since_ms)).fetchall()

        if not rows:
            return {"n_predicted": 0, "n_with_outcome": 0, "accuracy": 0}

        n_predicted = len(rows)
        with_outcome = [r for r in rows if r["actual_outcome"] is not None]
        n_with_outcome = len(with_outcome)

        if n_with_outcome == 0:
            return {
                "n_predicted": n_predicted,
                "n_with_outcome": 0,
                "accuracy": 0,
                "avg_pred_up": sum(r["pred"] for r in rows) / n_predicted,
                "actual_up_pct": 0,
                "calibration_error": 0,
            }

        correct = sum(1 for r in with_outcome
                      if (r["pred"] > 0.5 and r["actual_outcome"] == 1) or
                         (r["pred"] <= 0.5 and r["actual_outcome"] == 0))

        avg_pred_up = sum(r["pred"] for r in with_outcome) / n_with_outcome
        actual_up_pct = sum(r["actual_outcome"] for r in with_outcome) / n_with_outcome

        return {
            "n_predicted": n_predicted,
            "n_with_outcome": n_with_outcome,
            "accuracy": correct / n_with_outcome if n_with_outcome > 0 else 0,
            "avg_pred_up": avg_pred_up,
            "actual_up_pct": actual_up_pct,
            "calibration_error": abs(avg_pred_up - actual_up_pct),
        }
