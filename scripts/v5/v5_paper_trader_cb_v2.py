#!/usr/bin/env python3
"""
v5_paper_trader_cb_v2.py — Live paper-trading harness for the cb_v2 LGBM model.

This is the STEP-7 deliverable: deploy the model in paper mode against live
Coinbase market data, with $100/trade, max 3 concurrent positions, 7x leverage,
and full instrumentation (latency, slippage, fills, rejections) to validate
real-world performance vs the Paso-5 backtest expectations.

==============================================================================
  HOW IT WORKS
==============================================================================

1. Feed: Coinbase Exchange public candles endpoint (no API key required).
   - URL: https://api.exchange.coinbase.com/products/{pair}/candles
   - Pair: BTC-USD, ETH-USD, ... 12 tokens
   - Granularity: 300s (5m candles — best TF per Paso 5 backtest)
   - Polling: every 5s per token, round-robin across the 12 tokens.

2. Candle buffer: rolling window of the last 60 closed candles per token.
   - 60 candles = 5h of history — enough for EMA-50, RSI-14, vol-20, etc.

3. Feature extraction: same compute_indicators() as v5_extract_features_cb.py.
   - Produces the 38 features the LGBM was trained on.
   - We compute features on the WHOLE buffer; the last row's features are
     the "current" signal features.

4. Inference: LightGBM model from download/v5_lgbm_model_cb_v2.txt.
   - proba = model.predict_proba(features_last_row)[1]

5. Risk gate: evaluate_signal_cb_v2 from ppmt.risk.v5_risk_gate_cb_v2.
   - Applies confidence threshold (0.70 by default)
   - Applies asset-class boost/damp multipliers
   - Caps leverage at 7x

6. Position manager:
   - account_usd (starts at $10,000 paper)
   - open_positions: list of {symbol, entry_ts, entry_price, fill_price,
     tp_price, sl_price, leverage, margin_usd, max_hold_bars, bars_held}
   - On each NEW candle close, before checking for new signals:
       a. For each open position on this symbol, increment bars_held.
       b. Check if the candle's high >= tp_price → exit at TP (win).
       c. Else if candle's low <= sl_price → exit at SL (loss).
       d. Else if bars_held >= max_hold_bars → exit at close (timeout).
   - Exit fill: simulated with realistic slippage (0.02% adverse).
   - Fees: 0.05% × 2 sides × leverage on margin.

7. Decision logging (JSONL): every decision is logged with:
   - signal_ts (candle close time)
   - decision_ts (wall clock when inference completes)
   - fill_ts (wall clock + 200ms simulated market-order latency)
   - candle_to_decision_ms, decision_to_fill_ms
   - proba, gate_approved, action (OPEN / SKIP_CAPACITY / SKIP_BLOCKED / SKIP_LOW_PROBA)
   - For fills: slippage_bps, fee_bps, net_pnl_pct, net_pnl_usd, outcome

8. Resume: state saved to state/v5_cb_v2/paper_trader_state.json every 30s
   and after every trade event. On restart, loads account, open positions,
   and continues.

==============================================================================
  USAGE
==============================================================================

  # Smoke test (5 minutes, verbose stdout, no state writes)
  python ppmt/scripts/v5/v5_paper_trader_cb_v2.py --mode smoke

  # Live paper trading (1 week, logs to logs/, state to state/)
  python ppmt/scripts/v5/v5_paper_trader_cb_v2.py --mode live --days 7

  # Custom config
  python ppmt/scripts/v5/v5_paper_trader_cb_v2.py \\
      --mode live \\
      --threshold 0.75 \\
      --position-usd 100 \\
      --max-concurrent 3 \\
      --leverage 7 \\
      --account 10000

==============================================================================
  METRICS TO COMPARE vs BACKTEST (Paso 5 best config: thr=0.80, gate=OFF, mc=3)
==============================================================================

  | Metric             | Backtest (Paso 5) | Paper (this run)  |
  |--------------------|------------------:|------------------:|
  | Win rate           |            88.0%  |            <live> |
  | Profit factor      |             6.23  |            <live> |
  | Avg PnL/trade      |         +2.378%   |            <live> |
  | Trades per day     |  15,479 / 90 = 172|            <live> |
  | Slippage per side  |        0.020%     |     <simulated>   |
  | Decision latency   |        n/a        |            <live> |

  After the run, the JSONL log can be analyzed with the companion script
  `v5_paper_vs_backtest.py` (TBD) to produce the comparison table.

==============================================================================
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

# Make ppmt package importable
_HERE = Path(__file__).resolve().parent
for candidate in [_HERE.parent / "src", _HERE.parent.parent / "src", _HERE.parent / "ppmt" / "src"]:
    if (candidate / "ppmt" / "risk" / "v5_risk_gate_cb_v2.py").exists():
        sys.path.insert(0, str(candidate))
        break

import lightgbm as lgb  # noqa: E402
from ppmt.risk.v5_risk_gate_cb_v2 import SignalV5Cb, evaluate_signal_cb_v2  # type: ignore # noqa: E402

LOG = logging.getLogger("v5_paper_trader")

# ── CONSTANTS ───────────────────────────────────────────────────────────────

# Model + paths
MODEL_PATH = Path("/home/z/my-project/download/v5_lgbm_model_cb_v2.txt")
STATE_PATH = Path("/home/z/my-project/state/v5_cb_v2/paper_trader_state.json")
LOG_DIR = Path("/home/z/my-project/logs")
TRADES_LOG = LOG_DIR / "v5_paper_trader_trades.jsonl"
MAIN_LOG = LOG_DIR / "v5_paper_trader.log"

# Coinbase public API
COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/{pair}/candles"
USER_AGENT = "ppmt-v5-paper-trader/1.0 (research)"

# Token universe — same 12 as backtest
# (binance_symbol, coinbase_pair, asset_class)
TOKENS = [
    ("BTCUSDT",  "BTC-USD",   "blue_chip"),
    ("ETHUSDT",  "ETH-USD",   "blue_chip"),
    ("SOLUSDT",  "SOL-USD",   "large_cap"),
    ("XRPUSDT",  "XRP-USD",   "large_cap"),
    ("ADAUSDT",  "ADA-USD",   "mid_cap"),
    ("AVAXUSDT", "AVAX-USD",  "mid_cap"),
    ("LINKUSDT", "LINK-USD",  "mid_cap"),
    ("DOGEUSDT", "DOGE-USD",  "meme"),
    ("SHIBUSDT", "SHIB-USD",  "meme"),
    ("PEPEUSDT", "PEPE-USD",  "meme"),
    ("WIFUSDT",  "WIF-USD",   "meme"),
    ("BONKUSDT", "BONK-USD",  "meme"),
]

TF_LABEL = "5m"
TF_GRANULARITY = 300      # seconds
TF_MS = 300_000
POLL_INTERVAL_SEC = 5     # poll each token every 5s
BUFFER_BARS = 60          # rolling window per token

# Trade parameters (defaults — overridable via CLI)
DEFAULT_THRESHOLD = 0.70
DEFAULT_POSITION_USD = 100.0
DEFAULT_MAX_CONCURRENT = 3
DEFAULT_LEVERAGE = 7
DEFAULT_ACCOUNT = 10_000.0

# Bar-level TP/SL on PRICE (matches label_hit_tp_first label semantics)
TP_PRICE_PCT = 0.6        # +0.6% from entry → take profit
SL_MARGIN_PCT = -5.0      # -5% on MARGIN → on price = -5/leverage
MAX_HOLD_BARS_5M = 3      # 3 × 5m = 15 minutes

# Costs
TAKER_FEE_PCT = 0.05      # per side, % of notional
SLIPPAGE_PCT = 0.02       # per side, % of price
FILL_LATENCY_MS = 200     # simulated market-order fill latency

# Feature names — must match training exactly (40 features, includes edge_strong/edge_marginal)
FEATURE_NAMES = [
    "body_pct", "upper_wick", "lower_wick", "body_abs", "close_pos", "range_pct",
    "ret_1", "ret_3", "ret_5", "ret_10", "log_ret_1",
    "atr_pct", "vol_std_10", "rsi_14",
    "ema_9_20_cross", "ema_20_50_cross", "ema_9_slope", "ema_20_slope", "ema_50_slope",
    "price_vs_ema20", "price_vs_ema50", "vol_ratio", "vol_z",
    "last_3_body_sum", "last_3_range_sum",
    "bullish_engulf_2", "hammer_like", "shooting_star",
    "breakout_up", "breakout_down", "dist_to_high_20", "dist_to_low_20",
    "trend_50", "vol_regime", "trending",
    "hour_sin", "hour_cos", "dow",
    "edge_strong", "edge_marginal",
]

# ── DATA CLASSES ────────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    symbol: str
    pair: str
    asset_class: str
    entry_ts: int          # unix seconds (candle close that triggered entry)
    entry_price: float     # candle close price
    fill_price: float      # simulated fill price (with slippage)
    tp_price: float
    sl_price: float
    leverage: int
    margin_usd: float
    max_hold_bars: int
    bars_held: int = 0
    confidence: float = 0.0
    decision_latency_ms: int = 0
    fill_latency_ms: int = 0


@dataclass
class ClosedTrade:
    symbol: str
    pair: str
    asset_class: str
    entry_ts: int
    exit_ts: int
    entry_price: float
    fill_price: float
    exit_price: float
    exit_fill_price: float
    tp_price: float
    sl_price: float
    leverage: int
    margin_usd: float
    confidence: float
    outcome: str           # win | loss | timeout
    gross_pnl_pct: float   # on margin
    fee_pct: float
    slippage_pct: float
    net_pnl_pct: float
    net_pnl_usd: float
    bars_held: int
    decision_latency_ms: int
    fill_latency_ms: int


@dataclass
class PaperTraderState:
    account_usd: float
    open_positions: list[dict]    # serialized OpenPosition
    closed_trades: list[dict]     # serialized ClosedTrade
    decisions_log: list[dict]     # every decision event
    last_candle_ts: dict          # {symbol: last_candle_ts_seen}
    started_at: str
    last_saved_at: str
    config: dict


# ── FEATURE EXTRACTOR (re-implementation of compute_indicators) ─────────────

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the 38 features on a OHLCV df. Same logic as v5_extract_features_cb.py.

    df must have columns: timestamp, open, high, low, close, volume
    """
    df = df.copy().reset_index(drop=True)
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]

    rng = (h - l).replace(0, 1e-10)
    body = (c - o)
    df["body_pct"]     = body / rng
    df["upper_wick"]   = (h - np.maximum(o, c)) / rng
    df["lower_wick"]   = (np.minimum(o, c) - l) / rng
    df["body_abs"]     = body.abs() / rng
    df["close_pos"]    = (c - l) / rng
    df["range_pct"]    = rng / c * 100

    df["ret_1"]  = c.pct_change(1)
    df["ret_3"]  = c.pct_change(3)
    df["ret_5"]  = c.pct_change(5)
    df["ret_10"] = c.pct_change(10)
    df["log_ret_1"] = np.log(c / c.shift(1))

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr_14"]   = tr.rolling(14).mean()
    df["atr_pct"]  = df["atr_14"] / c * 100
    df["vol_std_10"] = df["log_ret_1"].rolling(10).std()

    delta = c.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-10)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    for p in [9, 20, 50]:
        df[f"ema_{p}"] = c.ewm(span=p, adjust=False).mean()
        df[f"ema_{p}_slope"] = df[f"ema_{p}"].pct_change(3)
    df["ema_9_20_cross"] = (df["ema_9"] - df["ema_20"]) / c * 100
    df["ema_20_50_cross"] = (df["ema_20"] - df["ema_50"]) / c * 100
    df["price_vs_ema20"] = (c - df["ema_20"]) / c * 100
    df["price_vs_ema50"] = (c - df["ema_50"]) / c * 100

    vol_ma = v.rolling(20).mean().replace(0, 1e-10)
    df["vol_ratio"] = v / vol_ma
    df["vol_z"] = (v - vol_ma) / v.rolling(20).std().replace(0, 1e-10)

    df["last_3_body_sum"] = df["body_pct"].rolling(3).sum()
    df["last_3_range_sum"] = df["range_pct"].rolling(3).sum()

    df["bullish_engulf_2"] = ((df["body_pct"].shift(1) < 0) & (df["body_pct"] > 0) &
                              (df["close"] > df["open"].shift(1)) &
                              (df["open"] < df["close"].shift(1))).astype(int)
    df["hammer_like"] = ((df["lower_wick"] > 2 * df["body_abs"]) & (df["body_abs"] > 0)).astype(int)
    df["shooting_star"] = ((df["upper_wick"] > 2 * df["body_abs"]) & (df["body_abs"] > 0)).astype(int)

    df["high_20"] = h.rolling(20).max()
    df["low_20"]  = l.rolling(20).min()
    df["breakout_up"]   = (h > df["high_20"].shift(1)).astype(int)
    df["breakout_down"] = (l < df["low_20"].shift(1)).astype(int)
    df["dist_to_high_20"] = (c - df["high_20"]) / df["high_20"] * 100
    df["dist_to_low_20"]  = (c - df["low_20"])  / df["low_20"]  * 100

    df["trend_50"] = np.sign(df["ema_9"] - df["ema_50"]).astype(int)
    atr_p = df["atr_pct"].fillna(0).values
    bins = [0.5, 1.5, 5.0]
    df["vol_regime"] = np.digitize(atr_p, bins).astype(int)
    df["trending"] = (df["atr_pct"] > df["atr_pct"].rolling(50, min_periods=5).mean()).astype(int)

    ts = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df["hour_utc"] = ts.dt.hour
    df["dow"] = ts.dt.dayofweek
    df["hour_sin"] = np.sin(2 * np.pi * df["hour_utc"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour_utc"] / 24)
    return df


# ── COINBASE FEED ───────────────────────────────────────────────────────────

class CoinbaseFeed:
    """Polls Coinbase public candles API for a single pair."""

    def __init__(self, pair: str, session: requests.Session):
        self.pair = pair
        self.session = session
        self.url = COINBASE_CANDLES.format(pair=pair)
        self.headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    def fetch_recent(self, n_candles: int = BUFFER_BARS) -> list[dict]:
        """Fetch the most recent n_candles closed 5m candles.

        Returns list of dicts: {timestamp_sec, open, high, low, close, volume}
        in ASC order (oldest first, newest last).
        """
        # Coinbase returns DESC (newest first) and caps at 300.
        # We request n_candles+1 (the +1 is the in-progress candle which we discard).
        params = {"granularity": TF_GRANULARITY}
        try:
            r = self.session.get(self.url, params=params, headers=self.headers, timeout=10)
            if r.status_code == 429:
                LOG.warning("429 rate limit on %s — backing off", self.pair)
                time.sleep(2)
                return []
            r.raise_for_status()
            rows = r.json()
        except (requests.RequestException, ValueError) as e:
            LOG.warning("Fetch error on %s: %s", self.pair, e)
            return []

        if not isinstance(rows, list) or not rows:
            return []

        # Coinbase: [time_sec, low, high, open, close, volume] DESC
        rows_asc = list(reversed(rows))

        # Discard the in-progress candle (last in ASC order if its close time is in the future)
        now_sec = int(time.time())
        closed = []
        for r in rows_asc:
            t = int(r[0])
            # 5m candle that closes at t+300 is "closed" if now >= t+300
            if now_sec >= t + TF_GRANULARITY:
                closed.append({
                    "timestamp": t,
                    "open": float(r[3]),
                    "high": float(r[2]),
                    "low": float(r[1]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                })
        # Keep only the last n_candles
        return closed[-n_candles:]


# ── PAPER TRADER ────────────────────────────────────────────────────────────

class PaperTrader:
    def __init__(
        self,
        model: lgb.Booster,
        threshold: float,
        position_usd: float,
        max_concurrent: int,
        leverage: int,
        account: float,
        state_path: Optional[Path] = None,
    ):
        self.model = model
        self.threshold = threshold
        self.position_usd = position_usd
        self.max_concurrent = max_concurrent
        self.leverage = leverage
        self.account = account
        self.state_path = state_path

        # State
        self.open_positions: list[OpenPosition] = []
        self.closed_trades: list[ClosedTrade] = []
        self.decisions_log: list[dict] = []
        self.last_candle_ts: dict[str, int] = {}
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.shutdown_requested = False

        # Stats
        self.n_signals_seen = 0
        self.n_signals_approved = 0
        self.n_skipped_capacity = 0
        self.n_skipped_low_proba = 0
        self.n_skipped_blocked = 0
        self.n_inference_calls = 0
        self.n_inference_errors = 0

        # Buffers: {binance_symbol: list[dict]} — last BUFFER_BARS closed candles
        self.buffers: dict[str, list[dict]] = {sym: [] for sym, _, _ in TOKENS}

    # ── STATE PERSISTENCE ──

    def save_state(self) -> None:
        if self.state_path is None:
            return
        state = {
            "account_usd": self.account,
            "open_positions": [asdict(p) for p in self.open_positions],
            "closed_trades": [asdict(t) for t in self.closed_trades],
            "decisions_log": self.decisions_log[-2000:],  # cap to last 2000
            "last_candle_ts": self.last_candle_ts,
            "started_at": self.started_at,
            "last_saved_at": datetime.now(timezone.utc).isoformat(),
            "config": {
                "threshold": self.threshold,
                "position_usd": self.position_usd,
                "max_concurrent": self.max_concurrent,
                "leverage": self.leverage,
            },
            "stats": {
                "n_signals_seen": self.n_signals_seen,
                "n_signals_approved": self.n_signals_approved,
                "n_skipped_capacity": self.n_skipped_capacity,
                "n_skipped_low_proba": self.n_skipped_low_proba,
                "n_skipped_blocked": self.n_skipped_blocked,
                "n_inference_calls": self.n_inference_calls,
                "n_inference_errors": self.n_inference_errors,
                "n_open": len(self.open_positions),
                "n_closed": len(self.closed_trades),
            },
        }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str))
        tmp.replace(self.state_path)

    def load_state(self) -> bool:
        if self.state_path is None or not self.state_path.exists():
            return False
        try:
            state = json.loads(self.state_path.read_text())
            self.account = state.get("account_usd", self.account)
            self.open_positions = [OpenPosition(**p) for p in state.get("open_positions", [])]
            self.closed_trades = [ClosedTrade(**t) for t in state.get("closed_trades", [])]
            self.decisions_log = state.get("decisions_log", [])
            self.last_candle_ts = {k: int(v) for k, v in state.get("last_candle_ts", {}).items()}
            self.started_at = state.get("started_at", self.started_at)
            LOG.info("Resumed state: account=$%.2f, open=%d, closed=%d",
                     self.account, len(self.open_positions), len(self.closed_trades))
            return True
        except Exception as e:
            LOG.warning("Failed to load state: %s — starting fresh", e)
            return False

    # ── SIGNAL HANDLING ──

    def request_shutdown(self, *_):
        LOG.info("Shutdown requested — finishing current cycle then exiting")
        self.shutdown_requested = True

    # ── INFERENCE ──

    def predict_proba(self, features_row: dict) -> Optional[float]:
        """Run LGBM inference on a single feature row. Returns proba or None on error."""
        try:
            X = np.array([[features_row.get(f, 0.0) for f in FEATURE_NAMES]], dtype=np.float64)
            # Replace NaN/inf with 0
            X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
            proba = self.model.predict(X)[0]
            self.n_inference_calls += 1
            return float(proba)
        except Exception as e:
            self.n_inference_errors += 1
            LOG.warning("Inference error: %s", e)
            return None

    # ── POSITION MANAGEMENT ──

    def check_exits(self, symbol: str, candle: dict) -> None:
        """On a new closed candle for `symbol`, check all open positions on that symbol.

        Updates bars_held, exits at TP/SL/timeout, records closed trades.
        """
        still_open = []
        for pos in self.open_positions:
            if pos.symbol != symbol:
                still_open.append(pos)
                continue

            pos.bars_held += 1
            candle_high = candle["high"]
            candle_low = candle["low"]
            candle_close = candle["close"]

            exit_reason = None
            exit_price = None
            outcome = None

            # Priority: SL first (worst case first — conservative)
            if candle_low <= pos.sl_price:
                exit_reason = "SL"
                exit_price = pos.sl_price
                outcome = "loss"
            elif candle_high >= pos.tp_price:
                exit_reason = "TP"
                exit_price = pos.tp_price
                outcome = "win"
            elif pos.bars_held >= pos.max_hold_bars:
                exit_reason = "TIMEOUT"
                exit_price = candle_close
                outcome = "timeout"

            if exit_reason is None:
                still_open.append(pos)
                continue

            # Simulate exit fill with slippage (adverse to us)
            if outcome == "win":
                # Selling to close LONG → we get the bid (slightly lower)
                exit_fill = exit_price * (1 - SLIPPAGE_PCT / 100.0)
            else:
                # SL hit or timeout → also selling to close, also pay the bid
                exit_fill = exit_price * (1 - SLIPPAGE_PCT / 100.0)

            # Compute PnL on margin
            # gross_pnl_pct = (exit_fill - fill_price) / fill_price * 100 * leverage
            gross_pct = (exit_fill - pos.fill_price) / pos.fill_price * 100 * pos.leverage

            # Fees: taker fee per side, on notional (= margin * leverage)
            # fee_pct_on_margin = TAKER_FEE_PCT * leverage * 2  (entry + exit)
            fee_pct = TAKER_FEE_PCT * pos.leverage * 2

            # Slippage already accounted for in fill prices, but we also model
            # a slippage_pct on margin for reporting parity with backtest
            slip_pct = SLIPPAGE_PCT * pos.leverage * 2

            net_pct = gross_pct - fee_pct - slip_pct
            net_usd = pos.margin_usd * net_pct / 100.0
            self.account += net_usd

            trade = ClosedTrade(
                symbol=pos.symbol,
                pair=pos.pair,
                asset_class=pos.asset_class,
                entry_ts=pos.entry_ts,
                exit_ts=candle["timestamp"],
                entry_price=pos.entry_price,
                fill_price=pos.fill_price,
                exit_price=exit_price,
                exit_fill_price=exit_fill,
                tp_price=pos.tp_price,
                sl_price=pos.sl_price,
                leverage=pos.leverage,
                margin_usd=pos.margin_usd,
                confidence=pos.confidence,
                outcome=outcome,
                gross_pnl_pct=gross_pct,
                fee_pct=fee_pct,
                slippage_pct=slip_pct,
                net_pnl_pct=net_pct,
                net_pnl_usd=net_usd,
                bars_held=pos.bars_held,
                decision_latency_ms=pos.decision_latency_ms,
                fill_latency_ms=pos.fill_latency_ms,
            )
            self.closed_trades.append(trade)
            LOG.info("EXIT %s %s entry=%.6f fill=%.6f exit=%.6f fill=%.6f "
                     "outcome=%s gross=%.3f%% net=%.3f%% pnl=$%.2f account=$%.2f",
                     pos.symbol, exit_reason, pos.entry_price, pos.fill_price,
                     exit_price, exit_fill, outcome, gross_pct, net_pct, net_usd, self.account)

        self.open_positions = still_open

    # ── SIGNAL PROCESSING ──

    def prime_buffer(self, binance_symbol: str, candles: list[dict]) -> None:
        """On the first fetch for a symbol, populate the buffer with all closed
        candles returned and set last_candle_ts to the most recent closed one.

        This avoids the backfill-everything-as-signals problem: we want the
        buffer pre-populated for feature computation, but we only want to
        fire signals on candles that close AFTER the trader started.
        """
        if not candles:
            return
        buf = self.buffers[binance_symbol]
        # If buffer is empty, take all candles. Otherwise only take candles
        # newer than what we already have.
        if not buf:
            buf.extend(candles)
            if len(buf) > BUFFER_BARS:
                del buf[0:len(buf) - BUFFER_BARS]
        else:
            last_ts_in_buf = buf[-1]["timestamp"]
            for c in candles:
                if c["timestamp"] > last_ts_in_buf:
                    buf.append(c)
            if len(buf) > BUFFER_BARS:
                del buf[0:len(buf) - BUFFER_BARS]
        # Set last_candle_ts to the newest closed candle so we don't fire signals
        # on historical data during the first cycle.
        self.last_candle_ts[binance_symbol] = buf[-1]["timestamp"]

    def process_new_candle(
        self,
        binance_symbol: str,
        pair: str,
        asset_class: str,
        candle: dict,
    ) -> None:
        """Process a freshly-closed candle: check exits, run inference, maybe open."""
        ts = candle["timestamp"]

        # 0) Update buffer
        buf = self.buffers[binance_symbol]
        # Only append if newer than what we have
        if not buf or ts > buf[-1]["timestamp"]:
            buf.append(candle)
            if len(buf) > BUFFER_BARS:
                del buf[0:len(buf) - BUFFER_BARS]

        # 1) Check exits for any open positions on this symbol
        self.check_exits(binance_symbol, candle)

        # 2) Need enough history to compute features (EMA-50 needs >= 50 bars)
        if len(buf) < 55:
            return

        # 3) Compute features on the whole buffer; take the last row (= this candle)
        df = pd.DataFrame(buf)
        try:
            df_feat = compute_features(df)
        except Exception as e:
            LOG.warning("Feature compute failed on %s: %s", binance_symbol, e)
            return

        last_row = df_feat.iloc[-1]
        # Skip if RSI is NaN (insufficient history)
        if pd.isna(last_row["rsi_14"]):
            return

        features = {f: float(last_row[f]) if not pd.isna(last_row[f]) else 0.0
                    for f in FEATURE_NAMES if f not in ("edge_strong", "edge_marginal")}

        # Compute the 2 derived edge features (matches v5_backtest_concurrent_cb_v2 logic)
        # edge_strong = (alt & scalp & asia)
        # edge_marginal = (exactly 2 of {alt, scalp, asia}) and not edge_strong
        hour_utc = int(last_row["hour_utc"])
        asia = hour_utc in {0, 1, 2, 18, 19, 20, 21, 22, 23}
        alt = asset_class != "blue_chip"   # NOT blue_chip
        scalp = TF_LABEL in {"1m", "5m", "15m"}   # always True for 5m
        edge_strong = 1 if (alt and scalp and asia) else 0
        score = int(alt) + int(scalp) + int(asia)
        edge_marginal = 1 if (score == 2 and not edge_strong) else 0
        features["edge_strong"] = float(edge_strong)
        features["edge_marginal"] = float(edge_marginal)

        # 4) Inference
        signal_ts_wall = time.time()
        proba = self.predict_proba(features)
        if proba is None:
            return

        decision_ts_wall = time.time()
        candle_to_decision_ms = int((decision_ts_wall - ts - TF_GRANULARITY) * 1000)
        # ^ ts is the candle OPEN time; candle close = ts + 300
        # We approximate decision_ts_wall - candle_close ≈ decision_ts_wall - (ts + 300)

        self.n_signals_seen += 1
        hour_utc = int(last_row["hour_utc"])

        # 5) Risk gate
        sig = SignalV5Cb(
            symbol=binance_symbol,
            asset_class=asset_class,
            timeframe=TF_LABEL,
            direction="LONG",
            entry_price=float(last_row["close"]),
            expected_move_pct=0.0,
            win_rate=0.0,
            confidence=proba,
            hour_utc=hour_utc,
            leverage=self.leverage,
            size_usd=self.position_usd,
        )
        decision = evaluate_signal_cb_v2(sig)

        # 6) Decide action
        action = None
        reason = ""
        if proba < self.threshold:
            action = "SKIP_LOW_PROBA"
            reason = f"proba={proba:.3f} < thr={self.threshold:.3f}"
            self.n_skipped_low_proba += 1
        elif not decision.approved:
            action = "SKIP_BLOCKED"
            reason = decision.reason
            self.n_skipped_blocked += 1
        elif len(self.open_positions) >= self.max_concurrent:
            action = "SKIP_CAPACITY"
            reason = f"open={len(self.open_positions)} >= max={self.max_concurrent}"
            self.n_skipped_capacity += 1
        elif any(p.symbol == binance_symbol for p in self.open_positions):
            action = "SKIP_DUP_SYMBOL"
            reason = f"already open on {binance_symbol}"
        else:
            action = "OPEN"
            reason = decision.reason
            self.n_signals_approved += 1

        # 7) Log the decision (always)
        decision_event = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "signal_ts": ts,
            "symbol": binance_symbol,
            "pair": pair,
            "asset_class": asset_class,
            "candle_close": ts + TF_GRANULARITY,
            "candle_close_iso": datetime.fromtimestamp(ts + TF_GRANULARITY, tz=timezone.utc).isoformat(),
            "close_price": float(last_row["close"]),
            "proba": proba,
            "threshold": self.threshold,
            "gate_approved": decision.approved,
            "gate_reason": decision.reason,
            "final_confidence": decision.final_confidence,
            "action": action,
            "reason": reason,
            "decision_latency_ms": candle_to_decision_ms,
            "account_usd": self.account,
            "n_open": len(self.open_positions),
        }
        self.decisions_log.append(decision_event)

        if action == "OPEN":
            # 8) Open paper position with simulated fill latency + slippage
            entry_price = float(last_row["close"])
            # Buying to open LONG → we pay the ask (slightly higher)
            fill_price = entry_price * (1 + SLIPPAGE_PCT / 100.0)
            fill_ts_wall = time.time() + (FILL_LATENCY_MS / 1000.0)

            # TP at +0.6% from fill_price (conservative — based on actual fill, not signal price)
            tp_price = fill_price * (1 + TP_PRICE_PCT / 100.0)
            # SL at -5% margin / leverage from fill_price
            sl_pct_on_price = abs(SL_MARGIN_PCT) / self.leverage
            sl_price = fill_price * (1 - sl_pct_on_price / 100.0)

            pos = OpenPosition(
                symbol=binance_symbol,
                pair=pair,
                asset_class=asset_class,
                entry_ts=ts + TF_GRANULARITY,  # candle close = entry signal time
                entry_price=entry_price,
                fill_price=fill_price,
                tp_price=tp_price,
                sl_price=sl_price,
                leverage=self.leverage,
                margin_usd=self.position_usd,
                max_hold_bars=MAX_HOLD_BARS_5M,
                bars_held=0,
                confidence=proba,
                decision_latency_ms=candle_to_decision_ms,
                fill_latency_ms=FILL_LATENCY_MS,
            )
            self.open_positions.append(pos)
            LOG.info("OPEN %s entry=%.6f fill=%.6f tp=%.6f sl=%.6f "
                     "conf=%.3f lev=%dx margin=$%.0f n_open=%d",
                     binance_symbol, entry_price, fill_price, tp_price, sl_price,
                     proba, self.leverage, self.position_usd, len(self.open_positions))

    # ── MAIN LOOP ──

    def run(self, duration_sec: int, poll_interval: float = POLL_INTERVAL_SEC) -> None:
        """Run the main polling loop for `duration_sec` seconds."""
        LOG.info("Starting paper trader for %d seconds (%.1f minutes)",
                 duration_sec, duration_sec / 60.0)
        LOG.info("Config: thr=%.2f pos=$%.0f mc=%d lev=%dx account=$%.2f",
                 self.threshold, self.position_usd, self.max_concurrent,
                 self.leverage, self.account)

        session = requests.Session()
        feeds = {sym: CoinbaseFeed(pair, session) for sym, pair, _ in TOKENS}

        start_ts = time.time()
        last_save_ts = start_ts
        last_progress_ts = start_ts
        primed: set[str] = set()
        cycle_n = 0

        while not self.shutdown_requested:
            cycle_n += 1
            now = time.time()
            if now - start_ts > duration_sec:
                LOG.info("Duration reached — exiting main loop")
                break

            # Poll each token round-robin
            for binance_symbol, pair, asset_class in TOKENS:
                if self.shutdown_requested:
                    break
                try:
                    candles = feeds[binance_symbol].fetch_recent(BUFFER_BARS)
                    if not candles:
                        continue

                    # On first fetch for this symbol, prime the buffer with all
                    # historical candles returned (so features can be computed),
                    # and set last_candle_ts to the newest closed candle — we
                    # will NOT fire signals on these historical candles.
                    if binance_symbol not in primed:
                        self.prime_buffer(binance_symbol, candles)
                        primed.add(binance_symbol)
                        if len(primed) == len(TOKENS):
                            LOG.info("All %d tokens primed with historical candles — "
                                     "now listening for new closes only", len(TOKENS))
                        continue

                    # From the second fetch onwards, only process NEW closed candles
                    latest_candle = candles[-1]
                    latest_ts = latest_candle["timestamp"]
                    last_seen = self.last_candle_ts.get(binance_symbol, 0)

                    if latest_ts > last_seen:
                        # Process this new candle (and any other candles between
                        # last_seen and latest_ts in case we missed some)
                        new_candles = [c for c in candles
                                       if c["timestamp"] > last_seen]
                        for c in new_candles:
                            self.process_new_candle(binance_symbol, pair, asset_class, c)
                        self.last_candle_ts[binance_symbol] = latest_ts

                except Exception as e:
                    LOG.warning("Error processing %s: %s", binance_symbol, e)

            # Periodic state save + progress
            elapsed = time.time() - start_ts
            if time.time() - last_save_ts > 30:
                self.save_state()
                last_save_ts = time.time()

            if time.time() - last_progress_ts > 30:
                self._log_progress()
                last_progress_ts = time.time()

            # Debug: log every cycle for first 5 cycles, then every 10
            if cycle_n <= 5 or cycle_n % 10 == 0:
                LOG.info("Cycle %d: elapsed=%.1fs, primed=%d, open=%d",
                         cycle_n, elapsed, len(primed), len(self.open_positions))

            time.sleep(poll_interval)

        # Final save
        self.save_state()
        LOG.info("Paper trader stopped. Final stats:")
        self._log_progress()

    def _log_progress(self) -> None:
        n_open = len(self.open_positions)
        n_closed = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.net_pnl_usd > 0)
        wr = wins / n_closed * 100 if n_closed else 0.0
        total_pnl = sum(t.net_pnl_usd for t in self.closed_trades)
        LOG.info("Stats: signals=%d approved=%d closed=%d open=%d WR=%.1f%% "
                 "PnL=$%.2f account=$%.2f",
                 self.n_signals_seen, self.n_signals_approved, n_closed, n_open,
                 wr, total_pnl, self.account)


# ── MAIN ────────────────────────────────────────────────────────────────────

def setup_logging(mode: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout)]
    if mode == "live":
        handlers.append(logging.FileHandler(MAIN_LOG, mode="a"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
        force=True,
    )


def load_model() -> lgb.Booster:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model file not found: {MODEL_PATH}")
    LOG.info("Loading LGBM model from %s", MODEL_PATH)
    return lgb.Booster(model_file=str(MODEL_PATH))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", choices=["smoke", "live"], default="smoke",
                        help="smoke=5min verbose, live=long-running with state+logs")
    parser.add_argument("--duration-sec", type=int, default=None,
                        help="Override run duration in seconds")
    parser.add_argument("--days", type=float, default=7.0,
                        help="Live mode duration in days (default 7)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--position-usd", type=float, default=DEFAULT_POSITION_USD)
    parser.add_argument("--max-concurrent", type=int, default=DEFAULT_MAX_CONCURRENT)
    parser.add_argument("--leverage", type=int, default=DEFAULT_LEVERAGE)
    parser.add_argument("--account", type=float, default=DEFAULT_ACCOUNT)
    parser.add_argument("--fresh-state", action="store_true",
                        help="Ignore any saved state and start fresh")
    args = parser.parse_args()

    setup_logging(args.mode)

    if args.mode == "smoke":
        duration = args.duration_sec or 300  # 5 minutes
    else:
        duration = args.duration_sec or int(args.days * 86400)

    LOG.info("=" * 70)
    LOG.info("v5_paper_trader_cb_v2 starting")
    LOG.info("Mode: %s  Duration: %ds (%.1fh)", args.mode, duration, duration / 3600.0)
    LOG.info("=" * 70)

    model = load_model()

    state_path = None if args.mode == "smoke" else STATE_PATH
    trader = PaperTrader(
        model=model,
        threshold=args.threshold,
        position_usd=args.position_usd,
        max_concurrent=args.max_concurrent,
        leverage=args.leverage,
        account=args.account,
        state_path=state_path,
    )

    if state_path and not args.fresh_state:
        trader.load_state()

    # Handle SIGINT/SIGTERM cleanly
    signal.signal(signal.SIGINT, trader.request_shutdown)
    signal.signal(signal.SIGTERM, trader.request_shutdown)

    trader.run(duration_sec=duration)

    # Print final summary
    print("\n" + "=" * 70)
    print("PAPER TRADING SESSION SUMMARY")
    print("=" * 70)
    print(f"Duration:           {duration}s ({duration/3600.0:.1f}h)")
    print(f"Account start:      ${args.account:.2f}")
    print(f"Account end:        ${trader.account:.2f}")
    print(f"Return:             ${(trader.account - args.account):+.2f} "
          f"({(trader.account/args.account - 1)*100:+.2f}%)")
    print(f"Signals seen:       {trader.n_signals_seen}")
    print(f"Approved & opened:  {trader.n_signals_approved}")
    print(f"Skipped (capacity): {trader.n_skipped_capacity}")
    print(f"Skipped (low proba):{trader.n_skipped_low_proba}")
    print(f"Skipped (blocked):  {trader.n_skipped_blocked}")
    print(f"Open positions:     {len(trader.open_positions)}")
    print(f"Closed trades:      {len(trader.closed_trades)}")

    if trader.closed_trades:
        wins = [t for t in trader.closed_trades if t.net_pnl_usd > 0]
        losses = [t for t in trader.closed_trades if t.net_pnl_usd <= 0]
        wr = len(wins) / len(trader.closed_trades) * 100
        total_pnl = sum(t.net_pnl_usd for t in trader.closed_trades)
        avg_pnl_pct = sum(t.net_pnl_pct for t in trader.closed_trades) / len(trader.closed_trades)
        gw = sum(t.net_pnl_pct for t in wins)
        gl = -sum(t.net_pnl_pct for t in losses)
        pf = gw / gl if gl > 0 else float("inf")
        print(f"Win rate:           {wr:.1f}%")
        print(f"Profit factor:      {pf:.2f}")
        print(f"Avg PnL/trade:      {avg_pnl_pct:+.3f}% of margin")
        print(f"Total PnL:          ${total_pnl:+.2f}")
        outcomes = defaultdict(int)
        for t in trader.closed_trades:
            outcomes[t.outcome] += 1
        print(f"Outcomes:           {dict(outcomes)}")

    if trader.decisions_log:
        latencies = [d["decision_latency_ms"] for d in trader.decisions_log
                     if d.get("decision_latency_ms") is not None and d["decision_latency_ms"] > 0]
        if latencies:
            print(f"Decision latency:   p50={sorted(latencies)[len(latencies)//2]}ms  "
                  f"p95={sorted(latencies)[int(len(latencies)*0.95)]}ms  "
                  f"max={max(latencies)}ms")

    print("=" * 70)
    if args.mode == "live":
        print(f"State saved to:     {STATE_PATH}")
        print(f"Log file:           {MAIN_LOG}")
        print(f"Decisions logged:   {len(trader.decisions_log)} events in state file")

    return 0


if __name__ == "__main__":
    sys.exit(main())
