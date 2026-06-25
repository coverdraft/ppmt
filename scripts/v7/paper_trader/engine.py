"""
engine.py — paper trading engine main loop.

On each 5m candle close:
1. Fetch latest closed OHLCV for the target symbol + BTC + ETH (200 bars each)
2. Compute 59 features for the latest closed candle
3. Predict fwd_ret_3 with the v6-LONG model
4. Decision rule: LONG if pred > thr_long, SHORT if pred < -thr_short, else WAIT
5. Manage any open position:
   - If a position is open and a new decision disagrees (LONG→SHORT or SHORT→LONG),
     close the open position at the current close, record PnL, open the new one.
   - If a position has been open for >= HORIZON candles, close it.
6. Log every decision (signal, fill, exit) to CSV
7. Update equity curve state file

State (persisted between runs):
- position: {symbol, side, entry_ts, entry_price, bars_held}
- equity: total PnL since start
- last_signal_ts: ts of the last signal we acted on (to avoid duplicate fills)
"""
from __future__ import annotations

import csv
import json
import logging
import time
import datetime as dt
from pathlib import Path
from typing import Optional

import pandas as pd

from .feed import Feed
from .features import latest_feature_row, FEATURE_NAMES
from .model import (
    load_model, load_metadata, predict, train, is_trained,
    THR_LONG, THR_SHORT, COST_PCT, HORIZON,
)

LOG = logging.getLogger("pt_engine")

STATE_DIR = Path(__file__).resolve().parents[3] / "data" / "paper_trading" / "state"
LOGS_DIR = Path(__file__).resolve().parents[3] / "data" / "paper_trading" / "logs"
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SIGNAL_CSV_HEADER = [
    "ts_utc", "ts_iso", "symbol", "close",
    "pred", "decision", "thr_long", "thr_short",
    "action",  # OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT, HOLD, NO_ACTION
    "position_side", "position_bars_held",
    "entry_price", "exit_price", "pnl_pct", "cost_pct", "pnl_net_pct",
    "equity_pct",
]

EQUITY_CSV_HEADER = ["ts_utc", "ts_iso", "equity_pct", "n_trades", "n_wins", "win_rate"]


class Engine:
    def __init__(self, symbol: str, timeframe: str = "5m",
                 warmup_bars: int = 200, exchange: str = "bybit",
                 bootstrap_bars: int = 4000,
                 auto_train: bool = True):
        self.symbol = symbol
        self.timeframe = timeframe
        self.warmup_bars = warmup_bars
        self.bootstrap_bars = bootstrap_bars
        self.auto_train = auto_train

        self.feed = Feed(exchange_id=exchange)
        self.state_path = STATE_DIR / f"engine_{symbol.replace('/', '_')}.json"
        self.signal_log_path = LOGS_DIR / f"signals_{symbol.replace('/', '_')}.csv"
        self.equity_log_path = LOGS_DIR / f"equity_{symbol.replace('/', '_')}.csv"

        # Ensure CSV logs have headers
        self._ensure_csv_header(self.signal_log_path, SIGNAL_CSV_HEADER)
        self._ensure_csv_header(self.equity_log_path, EQUITY_CSV_HEADER)

        self.state = self._load_state()
        self.bst = None
        self.meta = None

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------
    def _load_state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {
            "symbol": self.symbol,
            "started_at": int(time.time()),
            "last_signal_ts": None,
            "last_closed_candle_ts": None,
            "position": None,  # {side, entry_ts, entry_price, bars_held}
            "equity_pct": 0.0,
            "n_trades": 0,
            "n_wins": 0,
        }

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def _ensure_csv_header(self, path: Path, header: list[str]) -> None:
        if not path.exists() or path.stat().st_size == 0:
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(header)

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------
    def ensure_model(self) -> None:
        if is_trained(self.symbol):
            LOG.info("engine: loading existing model for %s", self.symbol)
            self.bst = load_model(self.symbol)
            self.meta = load_metadata(self.symbol)
            return

        if not self.auto_train:
            raise FileNotFoundError(
                f"no model for {self.symbol} and auto_train=False; "
                "run `python -m scripts.v7.paper_trader.runner --train` first"
            )

        LOG.info("engine: no model found — bootstrapping training on %d bars", self.bootstrap_bars)
        self._bootstrap_train()

    def _bootstrap_train(self) -> None:
        """Pull bootstrap_bars of historical data and train a fresh model."""
        LOG.info("bootstrap: fetching %d bars for %s + BTC + ETH ...",
                 self.bootstrap_bars, self.symbol)
        ohlcv = self.feed.fetch_history(self.symbol, self.timeframe, limit=self.bootstrap_bars)
        btc = self.feed.fetch_history("BTC/USDT", self.timeframe, limit=self.bootstrap_bars)
        eth = self.feed.fetch_history("ETH/USDT", self.timeframe, limit=self.bootstrap_bars)
        ohlcv_df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        btc_df = pd.DataFrame(btc, columns=["timestamp", "open", "high", "low", "close", "volume"])
        eth_df = pd.DataFrame(eth, columns=["timestamp", "open", "high", "low", "close", "volume"])
        # Align timestamps — intersection
        common_ts = set(ohlcv_df["timestamp"]) & set(btc_df["timestamp"]) & set(eth_df["timestamp"])
        ohlcv_df = ohlcv_df[ohlcv_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
        btc_df = btc_df[btc_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
        eth_df = eth_df[eth_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
        LOG.info("bootstrap: aligned rows=%d", len(ohlcv_df))
        self.meta = train(self.symbol, ohlcv_df, btc_df, eth_df)
        self.bst = load_model(self.symbol)

    # ------------------------------------------------------------------
    # Main signal loop
    # ------------------------------------------------------------------
    def run_once(self) -> dict | None:
        """Process one candle: fetch features, predict, maybe trade. Returns
        a dict describing what happened (or None if no new candle)."""
        # 1. Wait for a new closed candle on the target symbol
        last_ts = self.state.get("last_closed_candle_ts")
        new_window = self.feed.wait_for_next_close(self.symbol, self.timeframe, last_seen_ts=last_ts)
        if new_window is None:
            LOG.warning("engine.run_once: no new candle (timeout)")
            return None

        # 2. Fetch matching BTC + ETH windows
        btc_window = self.feed.fetch_recent_window("BTC/USDT", self.timeframe, window=self.warmup_bars)
        eth_window = self.feed.fetch_recent_window("ETH/USDT", self.timeframe, window=self.warmup_bars)
        if not btc_window or not eth_window:
            LOG.warning("engine.run_once: failed fetching BTC/ETH window")
            return None

        # 3. Build DataFrames (truncate to warmup length)
        ohlcv_df = pd.DataFrame(new_window[-self.warmup_bars:],
                                columns=["timestamp", "open", "high", "low", "close", "volume"])
        btc_df = pd.DataFrame(btc_window[-self.warmup_bars:],
                              columns=["timestamp", "open", "high", "low", "close", "volume"])
        eth_df = pd.DataFrame(eth_window[-self.warmup_bars:],
                              columns=["timestamp", "open", "high", "low", "close", "volume"])

        latest_ts = int(ohlcv_df["timestamp"].iloc[-1])
        latest_close = float(ohlcv_df["close"].iloc[-1])
        self.state["last_closed_candle_ts"] = latest_ts
        LOG.info("engine.run_once: ts=%s (%s) close=%.4f",
                 latest_ts, dt.datetime.utcfromtimestamp(latest_ts / 1000).isoformat(), latest_close)

        # 4. Compute features
        feat_row = latest_feature_row(ohlcv_df, btc_df, eth_df)
        if feat_row is None:
            LOG.warning("engine.run_once: insufficient history for features")
            return None

        # 5. Predict
        pred, decision = predict(self.bst, feat_row)
        LOG.info("engine.run_once: pred=%.4f decision=%s", pred, decision)

        # 6. Manage position + take action
        result = self._act_on_decision(decision, pred, latest_ts, latest_close)

        # 7. Persist state
        self._save_state()
        return result

    def _act_on_decision(self, decision: str, pred: float, ts: int, close: float) -> dict:
        pos = self.state["position"]
        action = "NO_ACTION"
        entry_price = exit_price = pnl_pct = pnl_net_pct = 0.0

        # If a position is open, decide: hold / close / reverse
        if pos is not None:
            pos["bars_held"] = pos.get("bars_held", 0) + 1
            side = pos["side"]
            # Force-close after HORIZON bars (matches training label horizon)
            time_up = pos["bars_held"] >= HORIZON
            # Disagreement: pos is LONG and decision is SHORT, or vice versa
            disagree = (side == "LONG" and decision == "SHORT") or (side == "SHORT" and decision == "LONG")

            if time_up or disagree:
                # Close
                action = f"CLOSE_{side}"
                entry_price = pos["entry_price"]
                exit_price = close
                if side == "LONG":
                    pnl_pct = (exit_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - exit_price) / entry_price * 100
                pnl_net_pct = pnl_pct - COST_PCT
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

        # 8. Log signal row
        row = {
            "ts_utc": ts,
            "ts_iso": dt.datetime.utcfromtimestamp(ts / 1000).isoformat(),
            "symbol": self.symbol,
            "close": close,
            "pred": pred,
            "decision": decision,
            "thr_long": THR_LONG,
            "thr_short": THR_SHORT,
            "action": action,
            "position_side": pos["side"] if pos else "",
            "position_bars_held": pos.get("bars_held", 0) if pos else 0,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "cost_pct": COST_PCT if action.startswith("CLOSE") or action.startswith("REVERSE") else 0.0,
            "pnl_net_pct": pnl_net_pct,
            "equity_pct": self.state["equity_pct"],
        }
        with open(self.signal_log_path, "a", newline="") as f:
            csv.writer(f).writerow([row[h] for h in SIGNAL_CSV_HEADER])

        # 9. Log equity snapshot
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

    # ------------------------------------------------------------------
    # Continuous loop
    # ------------------------------------------------------------------
    def run_forever(self, max_cycles: int | None = None) -> None:
        cycles = 0
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                LOG.info("engine: interrupted by user — exiting")
                break
            except Exception as e:
                LOG.exception("engine: error in run_once: %s — will retry in 60s", e)
                time.sleep(60)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                LOG.info("engine: reached max_cycles=%d — stopping", max_cycles)
                break

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "model_loaded": self.bst is not None,
            "model_meta": self.meta,
            "state": self.state,
            "signal_log": str(self.signal_log_path),
            "equity_log": str(self.equity_log_path),
        }
