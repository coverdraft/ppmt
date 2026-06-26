"""
engine.py — v8 Pattern-Informed Live Trading Engine

HARD RULES from corrected pattern analysis (446 entries, long+short):
1. TIME STOP at 30min — winners 8-9min, losers 21-28min → #1 edge preserver
2. NO averaging down — both directions have 1:3 win/loss ratio
3. Max 3 entries per trade (with EV confirmation only)
4. Max concurrent positions = 5
5. Position sizing proportional to model confidence (Kelly)
6. Direction-aware: model learns breakout longs win, breakout shorts lose
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import lightgbm as lgb

from .features import FEATURE_NAMES, latest_feature_row, symbol_to_sector
from .model import (
    predict_ev, EV_THRESHOLD_LONG, EV_THRESHOLD_SHORT,
    TP_ATR_MULT, SL_ATR_MULT, LOOKAHEAD, ATR_LAG_OFFSET, DEFAULT_COST,
    MAX_HOLD_BARS, MAX_ENTRIES_PER_TRADE, MAX_CONCURRENT_POSITIONS,
)

LOG = logging.getLogger("v8_engine")

STATE_DIR = Path(__file__).resolve().parents[2] / "data" / "v8_live" / "state"
LOGS_DIR = Path(__file__).resolve().parents[2] / "data" / "v8_live" / "logs"
MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "v8_models"

STATE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


class Position:
    """An open position with TP/SL + TIME STOP management."""
    def __init__(self, symbol: str, direction: str, entry_price: float,
                 entry_ts: int, atr_at_entry: float, ev_pred: float,
                 size_pct: float = 100.0, cost_pct: float = DEFAULT_COST,
                 n_entries: int = 1):
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry_price
        self.entry_ts = entry_ts
        self.atr_at_entry = atr_at_entry
        self.ev_pred = ev_pred
        self.size_pct = size_pct
        self.cost_pct = cost_pct
        self.bars_held = 0
        self.n_entries = n_entries

        if direction == "LONG":
            self.tp_price = entry_price + TP_ATR_MULT * atr_at_entry
            self.sl_price = entry_price - SL_ATR_MULT * atr_at_entry
        else:
            self.tp_price = entry_price - TP_ATR_MULT * atr_at_entry
            self.sl_price = entry_price + SL_ATR_MULT * atr_at_entry

    def check_exit(self, high: float, low: float, close: float) -> Optional[dict]:
        """Check if position should be closed this bar."""
        self.bars_held += 1

        tp_hit = False
        sl_hit = False

        if self.direction == "LONG":
            tp_hit = high >= self.tp_price
            sl_hit = low <= self.sl_price
        else:
            tp_hit = low <= self.tp_price
            sl_hit = high >= self.sl_price

        exit_reason = None
        exit_price = close

        if tp_hit and sl_hit:
            exit_reason = "SL"
            exit_price = self.sl_price
        elif tp_hit:
            exit_reason = "TP"
            exit_price = self.tp_price
        elif sl_hit:
            exit_reason = "SL"
            exit_price = self.sl_price

        # TIME STOP — the most powerful filter
        if exit_reason is None and self.bars_held >= MAX_HOLD_BARS:
            exit_reason = "TIME_STOP"
            exit_price = close

        if exit_reason is None:
            return None

        if self.direction == "LONG":
            pnl_pct = (exit_price - self.entry_price) / self.entry_price * 100
        else:
            pnl_pct = (self.entry_price - exit_price) / self.entry_price * 100

        size_mult = self.size_pct / 100.0
        pnl_net = (pnl_pct - self.cost_pct) * size_mult

        return {
            "exit_reason": exit_reason,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct * size_mult,
            "cost_pct": self.cost_pct * size_mult,
            "pnl_net_pct": pnl_net,
            "bars_held": self.bars_held,
        }

    def can_add_entry(self) -> bool:
        """Check if we can add another entry (DCA with EV confirmation only)."""
        return self.n_entries < MAX_ENTRIES_PER_TRADE

    def add_entry(self, price: float, atr: float, ev: float, size_pct: float) -> None:
        """Add an entry (average up only — never down)."""
        # Only add if price moved in our direction (averaging UP)
        if self.direction == "LONG" and price >= self.entry_price:
            # Weighted average entry
            old_size = self.size_pct
            self.entry_price = (self.entry_price * old_size + price * size_pct) / (old_size + size_pct)
            self.size_pct += size_pct
            self.n_entries += 1
            # Update TP/SL with new ATR
            self.atr_at_entry = atr
            self.tp_price = self.entry_price + TP_ATR_MULT * atr
            self.sl_price = self.entry_price - SL_ATR_MULT * atr
        elif self.direction == "SHORT" and price <= self.entry_price:
            old_size = self.size_pct
            self.entry_price = (self.entry_price * old_size + price * size_pct) / (old_size + size_pct)
            self.size_pct += size_pct
            self.n_entries += 1
            self.atr_at_entry = atr
            self.tp_price = self.entry_price - TP_ATR_MULT * atr
            self.sl_price = self.entry_price + SL_ATR_MULT * atr
        # If price moved against us, DO NOT add (no averaging down)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "entry_ts": self.entry_ts,
            "tp_price": self.tp_price,
            "sl_price": self.sl_price,
            "atr_at_entry": self.atr_at_entry,
            "ev_pred": self.ev_pred,
            "size_pct": self.size_pct,
            "bars_held": self.bars_held,
            "n_entries": self.n_entries,
        }


class ScalpelEngine:
    """Multi-token live trading engine with pattern-informed rules."""

    def __init__(self, model_path: Optional[Path] = None, exchange: str = "bybit",
                 max_concurrent: int = MAX_CONCURRENT_POSITIONS,
                 cost_pct: float = DEFAULT_COST):
        from scripts.v7.paper_trader.feed import Feed
        self.feed = Feed(exchange_id=exchange)
        self.max_concurrent = max_concurrent
        self.cost_pct = cost_pct

        if model_path is None:
            candidates = list(MODEL_DIR.glob("v8_pattern_*.txt"))
            if candidates:
                model_path = sorted(candidates)[-1]
            else:
                raise FileNotFoundError("No v8 model found. Train first with runner.py --mode train")

        self.model = lgb.Booster(model_file=str(model_path))
        LOG.info("Loaded model from %s", model_path)

        self.positions: dict[str, Position] = {}
        self.equity_pct = 0.0
        self.n_trades = 0
        self.n_wins = 0
        self.trade_log = []

    def process_candle(self, symbol: str, ohlcv_df: pd.DataFrame,
                       btc_df: pd.DataFrame, eth_df: pd.DataFrame,
                       funding_rate_z: float = 0.0,
                       oi_change_1h: float = 0.0,
                       oi_change_4h: float = 0.0,
                       sector_avg_ret: float = 0.0) -> Optional[dict]:
        """Process one closed candle for a symbol."""
        feat_row = latest_feature_row(
            ohlcv_df, btc_df, eth_df, symbol,
            funding_rate_z, oi_change_1h, oi_change_4h, sector_avg_ret,
        )
        if feat_row is None:
            return None

        ev, direction, size_signal = predict_ev(self.model, feat_row)

        latest_close = feat_row["_close"]
        atr_14_price = feat_row.get("_atr_14_price", 0.0)
        latest_ts = feat_row["_timestamp"]

        result = {
            "ts": latest_ts,
            "symbol": symbol,
            "close": latest_close,
            "ev": ev,
            "direction": direction,
            "size_signal": size_signal,
            "atr": atr_14_price,
            "action": "NO_ACTION",
        }

        # Check existing position for exit
        if symbol in self.positions:
            pos = self.positions[symbol]
            high = ohlcv_df["high"].iloc[-1]
            low = ohlcv_df["low"].iloc[-1]
            close = ohlcv_df["close"].iloc[-1]

            exit_info = pos.check_exit(high, low, close)
            if exit_info is not None:
                self.equity_pct += exit_info["pnl_net_pct"]
                self.n_trades += 1
                if exit_info["pnl_net_pct"] > 0:
                    self.n_wins += 1

                result["action"] = f"CLOSE_{pos.direction}"
                result["exit_reason"] = exit_info["exit_reason"]
                result["pnl_net"] = exit_info["pnl_net_pct"]
                result["equity"] = self.equity_pct

                LOG.info("CLOSE %s %s @ %.4f -> %.4f (%s) pnl=%.3f%% equity=%.3f%%",
                         symbol, pos.direction, pos.entry_price, close,
                         exit_info["exit_reason"], exit_info["pnl_net_pct"],
                         self.equity_pct)

                del self.positions[symbol]

                # Reverse signal
                if direction in ("LONG", "SHORT") and symbol not in self.positions:
                    if len(self.positions) < self.max_concurrent:
                        self._open_position(symbol, direction, close, atr_14_price, ev, latest_ts, size_signal)
                        result["action"] = f"REVERSE_TO_{direction}"
            else:
                # Check for adding to winner (averaging UP only)
                if pos.can_add_entry() and direction == pos.direction and size_signal > 1.5:
                    add_size = min(size_signal * 30, 80)
                    pos.add_entry(latest_close, atr_14_price, ev, add_size)
                    result["action"] = f"ADD_{pos.direction}"
                    LOG.info("ADD %s %s @ %.4f (entry #%d) ev=%.3f%%",
                             symbol, pos.direction, latest_close, pos.n_entries, ev)
                else:
                    result["action"] = "HOLD"
                return result

        # Open new position if signal
        elif direction in ("LONG", "SHORT"):
            if len(self.positions) < self.max_concurrent and symbol not in self.positions:
                self._open_position(symbol, direction, latest_close, atr_14_price, ev, latest_ts, size_signal)
                result["action"] = f"OPEN_{direction}"

        return result

    def _open_position(self, symbol: str, direction: str, price: float,
                       atr: float, ev: float, ts: int, size_signal: float) -> None:
        """Open a new position."""
        size_pct = min(size_signal * 50, 150)

        pos = Position(
            symbol=symbol,
            direction=direction,
            entry_price=price,
            entry_ts=ts,
            atr_at_entry=max(atr, 1e-10),
            ev_pred=ev,
            size_pct=size_pct,
            cost_pct=self.cost_pct,
        )
        self.positions[symbol] = pos

        LOG.info("OPEN %s %s @ %.4f TP=%.4f SL=%.4f size=%.0f%% ev=%.3f%% time_stop=%dbars",
                 symbol, direction, price, pos.tp_price, pos.sl_price,
                 size_pct, ev, MAX_HOLD_BARS)

    def status(self) -> dict:
        """Return current engine status."""
        return {
            "equity_pct": self.equity_pct,
            "n_trades": self.n_trades,
            "n_wins": self.n_wins,
            "win_rate": self.n_wins / max(self.n_trades, 1),
            "open_positions": {sym: pos.to_dict() for sym, pos in self.positions.items()},
            "hard_rules": {
                "time_stop": f"{MAX_HOLD_BARS} bars ({MAX_HOLD_BARS * 5} min)",
                "no_averaging_down": True,
                "max_entries": MAX_ENTRIES_PER_TRADE,
                "max_concurrent": self.max_concurrent,
            },
        }
