"""
engine.py — V12 paper trading engine.

On each 5m candle close:
1. Fetch 1m data for target symbol + BTC + ETH
2. Aggregate to 5m bars and compute 80 features
3. Predict P(UP in 1h) with V11 LightGBM model
4. Apply V12 quantile-based signal generation with direction+trend filters
5. Manage position: hold for H=12 bars (1h), close on reverse or expiry
6. Log to CSV and persist state

Key differences from v7 engine:
- HORIZON=12 (1h) instead of 288 (24h)
- direction_mode: "both", "long_only", "short_only"
- trend_filter: "none" or "aligned"
- Uses 1m OHLCV data aggregated to 5m (not native 5m)
- 80 features including microstructure
"""
from __future__ import annotations

import csv
import json
import logging
import time
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from .feed import Feed
from .features import latest_feature_row, ALL_FEATURE_NAMES
from .model import (
    load_model, load_metadata, predict, predict_raw, is_trained,
    get_symbol_config, HORIZON, COST_PCT, PROB_LONG, PROB_SHORT,
    V12_SYMBOL_CONFIG, DEFAULT_PROFILE,
)

LOG = logging.getLogger("v12_engine")

STATE_DIR = Path(__file__).resolve().parents[3] / "data" / "paper_trading" / "v12_state"
LOGS_DIR = Path(__file__).resolve().parents[3] / "data" / "paper_trading" / "v12_logs"
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SIGNAL_CSV_HEADER = [
    "ts_utc", "ts_iso", "symbol", "close",
    "pred", "decision", "q_high", "q_low",
    "direction_mode", "trend_filter",
    "action",  # OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT, HOLD, NO_ACTION
    "position_side", "position_bars_held",
    "entry_price", "exit_price", "pnl_pct", "cost_pct", "pnl_net_pct",
    "equity_pct",
]

EQUITY_CSV_HEADER = ["ts_utc", "ts_iso", "equity_pct", "n_trades", "n_wins", "win_rate"]


class Engine:
    def __init__(self, symbol: str, profile: str = DEFAULT_PROFILE,
                 warmup_5m_bars: int = 400, exchange: str = "bybit"):
        """Initialize V12 paper trading engine.

        Args:
            symbol: Token symbol (e.g. "SOL", "DOGE", "AVAX" or "SOL/USDT")
            profile: Trading profile ("balanced" or "conservative")
            warmup_5m_bars: Number of 5m bars for feature warm-up
            exchange: Exchange ID for ccxt
        """
        # Normalize symbol
        self.symbol_raw = symbol
        self.symbol = symbol.replace("/USDT", "").replace("/usdt", "")
        self.symbol_pair = f"{self.symbol}/USDT"
        self.profile = profile
        self.warmup_5m_bars = warmup_5m_bars

        # Load V12 config
        self.cfg = get_symbol_config(self.symbol, profile)
        self.q_long = self.cfg["q_long"]
        self.q_short = self.cfg["q_short"]
        self.direction_mode = self.cfg.get("direction", "both")
        self.trend_filter = self.cfg.get("trend_filter", "none")
        self.window_size = self.cfg.get("window_size", 200)
        self.trade_cost = self.cfg.get("cost_pct", COST_PCT)

        LOG.info("v12_engine: %s profile=%s config: Q%d/%d dir=%s trend=%s Win=%d Cost=%.2f%%",
                 self.symbol, profile, self.q_long, self.q_short,
                 self.direction_mode, self.trend_filter, self.window_size, self.trade_cost)

        self.feed = Feed(exchange_id=exchange)
        self.state_path = STATE_DIR / f"engine_v12_{self.symbol}.json"
        self.signal_log_path = LOGS_DIR / f"signals_v12_{self.symbol}.csv"
        self.equity_log_path = LOGS_DIR / f"equity_v12_{self.symbol}.csv"

        self._ensure_csv_header(self.signal_log_path, SIGNAL_CSV_HEADER)
        self._ensure_csv_header(self.equity_log_path, EQUITY_CSV_HEADER)

        self.state = self._load_state()
        self.bst = None
        self.meta = None

    def _load_state(self) -> dict:
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text())
            # Validate last_closed_candle_ts — if corrupted, reset it
            last_ts = state.get("last_closed_candle_ts")
            if last_ts is not None and isinstance(last_ts, (int, float)):
                if last_ts < 1e12:
                    LOG.warning("v12_engine: corrupted last_closed_candle_ts=%d — resetting", last_ts)
                    state["last_closed_candle_ts"] = None
                    # Also reset recent_preds since they were computed from corrupted data
                    state["recent_preds"] = []
            return state
        return {
            "symbol": self.symbol,
            "profile": self.profile,
            "started_at": int(time.time()),
            "last_closed_candle_ts": None,
            "position": None,
            "equity_pct": 0.0,
            "n_trades": 0,
            "n_wins": 0,
            "recent_preds": [],
        }

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def _ensure_csv_header(self, path: Path, header: list[str]) -> None:
        if not path.exists() or path.stat().st_size == 0:
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(header)

    def ensure_model(self) -> None:
        if is_trained(self.symbol):
            self.bst = load_model(self.symbol)
            self.meta = load_metadata(self.symbol)
            LOG.info("v12_engine: loaded V11 model for %s", self.symbol)
            return

        # Check common naming
        raise FileNotFoundError(
            f"no V11 model for {self.symbol}; train first:\n"
            f"  python scripts/v11/v11_train.py --symbol {self.symbol} --horizon 12"
        )

    def _quantile_decision(self, pred: float, trend_1h: float = 0.0) -> tuple[str, float, float]:
        """Compute quantile-based decision with V12 direction+trend filters."""
        recent = self.state.get("recent_preds", [])
        if len(recent) < 20:
            # Fallback to fixed thresholds
            if pred > PROB_LONG:
                decision = "LONG"
            elif pred < PROB_SHORT:
                decision = "SHORT"
            else:
                decision = "WAIT"
        else:
            q_high = float(np.percentile(recent, self.q_long))
            q_low = float(np.percentile(recent, self.q_short))

            if pred > q_high:
                decision = "LONG"
            elif pred < q_low:
                decision = "SHORT"
            else:
                decision = "WAIT"

        # Apply direction mode filter
        if decision == "SHORT" and self.direction_mode == "long_only":
            decision = "WAIT"
        if decision == "LONG" and self.direction_mode == "short_only":
            decision = "WAIT"

        # Apply trend alignment filter
        if self.trend_filter == "aligned":
            if decision == "LONG" and trend_1h < 0:
                decision = "WAIT"
            if decision == "SHORT" and trend_1h > 0:
                decision = "WAIT"

        # Recompute q_high/q_low for logging (even in fallback mode)
        if len(recent) >= 20:
            q_high = float(np.percentile(recent, self.q_long))
            q_low = float(np.percentile(recent, self.q_short))
        else:
            q_high = q_low = 0.0

        return decision, q_high, q_low

    def run_once(self) -> dict | None:
        """Process one 5m candle cycle. Returns result dict or None."""
        # 1. Wait for new 5m candle close
        last_ts = self.state.get("last_closed_candle_ts")
        new_ts = self.feed.wait_for_next_5m_close(self.symbol_pair, last_seen_ts=last_ts)
        if new_ts is None:
            LOG.warning("v12_engine: no new 5m candle (timeout)")
            return None

        # 2. Fetch 5m windows for symbol + BTC + ETH
        try:
            sym_5m = self.feed.fetch_5m_window(self.symbol_pair, self.warmup_5m_bars)
            btc_5m = self.feed.fetch_5m_window("BTC/USDT", self.warmup_5m_bars)
            eth_5m = self.feed.fetch_5m_window("ETH/USDT", self.warmup_5m_bars)
        except Exception as e:
            LOG.error("v12_engine: fetch failed: %s", e)
            return None

        if len(sym_5m) < 60 or len(btc_5m) < 60 or len(eth_5m) < 60:
            LOG.warning("v12_engine: insufficient data (sym=%d btc=%d eth=%d)",
                        len(sym_5m), len(btc_5m), len(eth_5m))
            return None

        latest_close = float(sym_5m["close"].iloc[-1])

        # Get timestamp from DataFrame — validate against feed's confirmed new_ts
        df_ts = int(sym_5m["timestamp"].iloc[-1])
        if df_ts < 1e12:
            # DataFrame timestamp is corrupted — use feed's confirmed timestamp
            LOG.warning("v12_engine: DataFrame ts corrupted (%d) — using feed ts=%d", df_ts, new_ts)
            latest_ts = new_ts
        else:
            latest_ts = df_ts

        # CRITICAL: always use feed's confirmed timestamp for dedup tracking.
        # This prevents infinite loops if DataFrame timestamps are unreliable.
        self.state["last_closed_candle_ts"] = new_ts

        LOG.info("v12_engine: ts=%s close=%.4f",
                 dt.datetime.utcfromtimestamp(latest_ts / 1000).isoformat(), latest_close)

        # 3. Compute features
        feat_row = latest_feature_row(sym_5m, btc_5m, eth_5m)
        if feat_row is None:
            LOG.warning("v12_engine: feature computation failed")
            return None

        # 4. Predict
        pred = predict_raw(self.bst, feat_row)

        # 5. Update rolling prediction window
        recent_preds = self.state.get("recent_preds", [])
        recent_preds.append(float(pred))
        if len(recent_preds) > self.window_size:
            recent_preds = recent_preds[-self.window_size:]
        self.state["recent_preds"] = recent_preds

        # 6. Quantile-based decision with V12 filters
        trend_1h = feat_row.get("_trend_1h", 0.0)
        decision, q_high, q_low = self._quantile_decision(pred, trend_1h)

        LOG.info("v12_engine: pred=%.4f decision=%s q_high=%.4f q_low=%.4f trend_1h=%.1f (window=%d)",
                 pred, decision, q_high, q_low, trend_1h, len(recent_preds))

        # 7. Act on decision
        result = self._act_on_decision(decision, pred, q_high, q_low, trend_1h,
                                        latest_ts, latest_close)

        # 8. Persist state
        self._save_state()
        return result

    def _act_on_decision(self, decision: str, pred: float,
                         q_high: float, q_low: float, trend_1h: float,
                         ts: int, close: float) -> dict:
        pos = self.state["position"]
        action = "NO_ACTION"
        entry_price = exit_price = pnl_pct = pnl_net_pct = 0.0

        if pos is not None:
            pos["bars_held"] = pos.get("bars_held", 0) + 1
            side = pos["side"]
            # Force-close after HORIZON bars (H=12 = 1h)
            time_up = pos["bars_held"] >= HORIZON
            # Disagreement: position direction vs signal
            disagree = (side == "LONG" and decision == "SHORT") or (side == "SHORT" and decision == "LONG")

            if time_up or disagree:
                action = f"CLOSE_{side}"
                entry_price = pos["entry_price"]
                exit_price = close
                if side == "LONG":
                    pnl_pct = (exit_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - exit_price) / entry_price * 100
                pnl_net_pct = pnl_pct - self.trade_cost
                self.state["equity_pct"] += pnl_net_pct
                self.state["n_trades"] += 1
                if pnl_net_pct > 0:
                    self.state["n_wins"] += 1
                LOG.info("CLOSE %s entry=%.4f exit=%.4f pnl=%.3f%% net=%.3f%% equity=%.3f%%",
                         side, entry_price, exit_price, pnl_pct, pnl_net_pct, self.state["equity_pct"])
                self.state["position"] = None
                # If disagree, immediately open opposite
                if disagree and decision in ("LONG", "SHORT"):
                    self._open_position(decision, ts, close)
                    action = f"REVERSE_TO_{decision}"
            else:
                action = "HOLD"
        else:
            # No position — open if decision is LONG or SHORT
            if decision in ("LONG", "SHORT"):
                self._open_position(decision, ts, close)
                action = f"OPEN_{decision}"

        # Log signal row
        row = {
            "ts_utc": ts,
            "ts_iso": dt.datetime.utcfromtimestamp(ts / 1000).isoformat(),
            "symbol": self.symbol,
            "close": close,
            "pred": pred,
            "decision": decision,
            "q_high": q_high,
            "q_low": q_low,
            "direction_mode": self.direction_mode,
            "trend_filter": self.trend_filter,
            "action": action,
            "position_side": pos["side"] if pos else "",
            "position_bars_held": pos.get("bars_held", 0) if pos else 0,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "cost_pct": self.trade_cost if action.startswith("CLOSE") or action.startswith("REVERSE") else 0.0,
            "pnl_net_pct": pnl_net_pct,
            "equity_pct": self.state["equity_pct"],
        }
        with open(self.signal_log_path, "a", newline="") as f:
            csv.writer(f).writerow([row[h] for h in SIGNAL_CSV_HEADER])

        # Log equity snapshot
        eq_row = {
            "ts_utc": ts,
            "ts_iso": row["ts_iso"],
            "equity_pct": self.state["equity_pct"],
            "n_trades": self.state["n_trades"],
            "n_wins": self.state["n_wins"],
            "win_rate": (self.state["n_wins"] / self.state["n_trades"]) if self.state["n_trades"] > 0 else 0.0,
        }
        with open(self.equity_log_path, "a", newline="") as f:
            csv.writer(f).writerow([eq_row[h] for h in EQUITY_CSV_HEADER])

        return row

    def _open_position(self, side: str, ts: int, close: float) -> None:
        self.state["position"] = {
            "side": side,
            "entry_ts": ts,
            "entry_price": close,
            "bars_held": 0,
        }
        LOG.info("OPEN %s @ %.4f ts=%s", side, close, ts)

    def run_forever(self, max_cycles: int | None = None) -> None:
        cycles = 0
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                LOG.info("v12_engine: interrupted by user — exiting")
                break
            except Exception as e:
                LOG.exception("v12_engine: error in run_once: %s — will retry in 60s", e)
                time.sleep(60)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                LOG.info("v12_engine: reached max_cycles=%d — stopping", max_cycles)
                break

    def status(self) -> dict:
        return {
            "symbol": self.symbol,
            "profile": self.profile,
            "config": self.cfg,
            "model_loaded": self.bst is not None,
            "model_meta": self.meta,
            "state": self.state,
            "signal_log": str(self.signal_log_path),
            "equity_log": str(self.equity_log_path),
        }
