"""
PPMT V2 Terminal — FastAPI WebSocket Bridge.

This server provides the real-time bridge between the PPMT Python engine
and the React terminal frontend. It exposes a single WebSocket endpoint
per token that streams candle, brain, and position updates.

v0.50.0: ENTREGABLE 11 — Proxy/VPN URL support + Mock Live Test mode.

Usage:
    uvicorn ppmt.terminal.v2_server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ppmt.data.storage import PPMTStorage
from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.terminal.paper_executor import PaperExecutor
from ppmt.execution.models import PositionState, PositionStatus
from ppmt.execution.interfaces import IExecutor
from ppmt.execution.mexc_futures import MexcFuturesExecutor
from ppmt.execution.crypto import decrypt_auth_payload

logger = logging.getLogger("ppmt.v2")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

# ─── v0.57.0 (TAREA 19): Global Anti-Overlap tracker ──────────
# Tracks which symbols currently have an ACTIVE position across
# ALL WebSocket sessions (5m + 15m combined). If SOL/USDT has
# a position open in 5m, a 15m signal for SOL/USDT is REJECTED.
_ACTIVE_SYMBOLS: dict[str, str] = {}  # {"SOL/USDT": "5m", "DOGE/USDT": "15m", ...}

# ─── v0.57.0 (TAREA 19): Net EV Gate statistics ──────────────
_NET_EV_STATS: dict[str, int] = {
    "total_raw_signals": 0,
    "passed_net_ev": 0,
    "rejected_spread": 0,   # Net_Move <= 0 (spread devoured the move)
    "rejected_ev_score": 0,  # Net_EV < 0.80
    "rejected_overlap": 0,   # Same symbol already has position in another TF
}

# ─── v0.58.0 (TAREA 21): Session-wide open positions tracker ──
_OPEN_POSITIONS: dict[str, dict] = {}  # {"SOL/USDT": {direction, entry, pnl_pct, ev_score, ...}}

# ─── v0.58.0 (TAREA 21): Last Net EV score per symbol ─────────
_LAST_NET_EV: dict[str, dict] = {}  # {"SOL/USDT": {ev_score, passed, net_rr, conf, ...}}

# ─── v0.58.0 (TAREA 21): Log emitter helper ──────────────────
# v2.1: Only emit logs with these tags to the Learning Feed.
# Prevents noise from spurious tags flooding the frontend.
_FEED_ALLOWED_TAGS = frozenset({
    "LEARN", "PATTERN BROKEN", "EV GATE", "SIGNAL", "WALK-FORWARD",
    "DIVERGE", "ERROR",
})

async def _emit_log(websocket, tag: str, message: str, level: str = "info"):
    """Emit a structured log message through the WebSocket for the terminal feed.
    
    v2.1 FIX: Only emit if tag is in the allowlist. This prevents the Learning
    Feed from filling with irrelevant entries (e.g. raw candle data).
    """
    if tag.upper() not in _FEED_ALLOWED_TAGS:
        return
    try:
        await websocket.send_json({
            "type": "log",
            "data": {
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "tag": tag,
                "message": message,
                "level": level,
            },
        })
    except Exception:
        pass  # Don't break the poll loop if WS send fails


# ─── Helpers ──────────────────────────────────────────────────

def _sax_symbol_to_str(sym) -> str:
    """Convert a SAX symbol (str or tuple) to a pure string.
    
    Single: 'a' → 'a'
    Dual:   ('a', 'x') → 'a'  (price symbol only, for path IDs)
    """
    if isinstance(sym, tuple):
        return str(sym[0])  # price symbol
    return str(sym)


def _sax_symbol_to_json(sym) -> list[str] | str:
    """Convert a SAX symbol to JSON-safe format.
    
    Single: 'a' → 'a'
    Dual:   ('a', 'x') → ['a', 'x']  (pure JSON string array)
    """
    if isinstance(sym, tuple):
        return [str(s) for s in sym]
    return str(sym)


def _build_active_path_ids(backward_path: list) -> list[str]:
    """Build cumulative path IDs from a backward path.
    
    backward_path: ['a', 'b', 'c'] or [('a','x'), ('b','y'), ('c','z')]
    Result: ["root", "a", "a-b", "a-b-c"]
    """
    ids = ["root"]
    cumulative = []
    for sym in backward_path:
        key = _sax_symbol_to_str(sym)
        cumulative.append(key)
        ids.append("-".join(cumulative))
    return ids


# ─── App ──────────────────────────────────────────────────────
app = FastAPI(title="PPMT V2 Terminal", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Global state ─────────────────────────────────────────────
_storage: Optional[PPMTStorage] = None

# v0.50.0: ENTREGABLE 11 — Mock Live Test mode.
# When PPMT_MOCK_LIVE=1, the live-trading endpoint uses PaperExecutor
# instead of MexcFuturesExecutor, and injects fake signals to test the
# frontend chart + position card lifecycle without real money.
MOCK_LIVE = os.environ.get("PPMT_MOCK_LIVE", "0") == "1"
if MOCK_LIVE:
    logger.warning("⚠️  PPMT_MOCK_LIVE=1 — Live trading uses PaperExecutor (NO REAL MONEY)")

# ─── v0.58.0: TAREA 20 — Risk Config & Live Session Tracker ──────
# In-memory risk configuration adjustable via /api/risk/config.
# Live session registry: each active WS connection registers its
# position state so /api/portfolio/live can report across sessions.

_RISK_CONFIG: dict = {
    "risk_per_trade": 0.01,      # 1% por defecto
    "max_positions": 3,
    "total_capital": 1000.0,     # USDT
    "current_drawdown": 0.0,     # % drawdown actual
}

# _LIVE_SESSIONS tracks active positions across all WS connections.
# Key: "SOL/USDT:5m" → value: dict with position summary.
# Populated by WebSocket handlers when positions open/close.
_LIVE_SESSIONS: dict[str, dict] = {}

# v0.60.0 (TERMINAL-v2.1): Trade history — in-memory list of closed trades.
_TRADE_HISTORY: list[dict] = []


def get_storage() -> PPMTStorage:
    global _storage
    if _storage is None:
        _storage = PPMTStorage()
    return _storage


# ─── REST: Health check ───────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


# ─── REST: Available symbols ─────────────────────────────────
@app.get("/api/symbols")
async def list_symbols():
    storage = get_storage()
    assets = storage.get_assets()
    return {"symbols": [a["symbol"] for a in assets]}


# ─── v0.57.0 (TAREA 19): Net EV Gate Statistics ──────────────
@app.get("/api/net-ev-stats")
async def net_ev_stats():
    """Return cumulative Net EV Gate filtering statistics."""
    return {
        "stats": dict(_NET_EV_STATS),
        "active_symbols": dict(_ACTIVE_SYMBOLS),
    }


# ─── REST: Risk Control Endpoints (TAREA 20) ─────────────────────

class RiskConfigPayload(BaseModel):
    """Payload for POST /api/risk/config — partial update of risk parameters."""
    risk_per_trade: Optional[float] = None
    max_positions: Optional[int] = None
    total_capital: Optional[float] = None
    current_drawdown: Optional[float] = None


@app.get("/api/risk/status")
async def risk_status():
    """Return the current risk configuration."""
    return dict(_RISK_CONFIG)


@app.post("/api/risk/config")
async def risk_config(payload: RiskConfigPayload):
    """Update risk configuration parameters in memory.

    Accepts a partial JSON body with any of:
      risk_per_trade (0.001–0.10), max_positions (1–20),
      total_capital (>0), current_drawdown (0.0–1.0)
    """
    updated = {}

    if payload.risk_per_trade is not None:
        val = payload.risk_per_trade
        if not (0.001 <= val <= 0.10):
            return {"error": "risk_per_trade must be between 0.001 and 0.10"}
        _RISK_CONFIG["risk_per_trade"] = val
        updated["risk_per_trade"] = val

    if payload.max_positions is not None:
        val = payload.max_positions
        if not (1 <= val <= 20):
            return {"error": "max_positions must be between 1 and 20"}
        _RISK_CONFIG["max_positions"] = val
        updated["max_positions"] = val

    if payload.total_capital is not None:
        val = payload.total_capital
        if val <= 0:
            return {"error": "total_capital must be > 0"}
        _RISK_CONFIG["total_capital"] = val
        updated["total_capital"] = val

    if payload.current_drawdown is not None:
        val = payload.current_drawdown
        if not (0.0 <= val <= 1.0):
            return {"error": "current_drawdown must be between 0.0 and 1.0"}
        _RISK_CONFIG["current_drawdown"] = val
        updated["current_drawdown"] = val

    logger.info(f"[RISK] Config updated: {updated}")
    return {"status": "ok", "updated": updated, "config": dict(_RISK_CONFIG)}


@app.get("/api/portfolio/live")
async def portfolio_live():
    """Return currently open positions with aggregate stats.

    Aggregates from both _LIVE_SESSIONS (TAREA 20) and _OPEN_POSITIONS (TAREA 21).
    """
    positions = []
    total_exposure = 0.0
    session_ev = 0.0

    # From _LIVE_SESSIONS (populated by live-trading WS)
    for session_key, data in _LIVE_SESSIONS.items():
        if data.get("status") not in ("ACTIVE", "BREAK_EVEN_SECURED", "TP_EXTENDED"):
            continue
        pos_info = {
            "symbol": data.get("symbol", session_key),
            "direction": data.get("direction", "LONG"),
            "entry": data.get("entry_price", 0.0),
            "pnl": data.get("pnl_pct", "0.0%"),
            "ev_score": data.get("ev_score", 0.0),
            "status": data.get("status", "UNKNOWN"),
            "timeframe": data.get("timeframe", ""),
        }
        positions.append(pos_info)
        total_exposure += data.get("size_usdt", 0.0)
        session_ev += data.get("ev_r", 0.0)

    # From _OPEN_POSITIONS (populated by paper-live WS)
    for sym, pos_data in _OPEN_POSITIONS.items():
        positions.append({"symbol": sym, **pos_data})
        total_exposure += pos_data.get("size_usdt", 0.0)
        session_ev += pos_data.get("ev_score", 0.0)

    return {
        "positions": positions,
        "total_exposure": round(total_exposure, 2),
        "active_count": len(positions),
        "session_ev": round(session_ev, 3),
    }


# ─── REST: Trade History ──────────────────────────────────────
# v0.60.0 (TERMINAL-v2.1): In-memory trade history endpoint.
@app.get("/api/trades/history")
async def get_trade_history():
    """Return all closed trades from the current server session."""
    return {"trades": _TRADE_HISTORY, "count": len(_TRADE_HISTORY)}


# ─── REST: Backtest ──────────────────────────────────────────────
# v2.1: Run a quick OOS backtest on historical data using the real
# PPMT engine with Config F. Results stream via WebSocket IN REAL-TIME.
#
# v2.1-fix: Rewritten to use EXACTLY the same logic as
# full_replay_v21.py — builds tries from IS data, uses the same
# replay loop, and streams results via WS.

class BacktestPayload(BaseModel):
    symbol: str = "SOL/USDT"
    timeframe: str = "5m"
    days: int = 7


# Track active backtest WebSocket connections so the endpoint can
# stream results to the calling client.
_BACKTEST_WS: dict[str, WebSocket] = {}  # key: "SOL/USDT:5m"

# Track running backtests to prevent duplicates
_BACKTEST_RUNNING: set[str] = set()  # keys like "SOL/USDT:5m"


@app.post("/api/backtest")
async def run_backtest(payload: BacktestPayload):
    """Start a backtest for the given symbol/timeframe/days.

    Runs in a background thread. Results are streamed to the client
    in real-time via the same WebSocket connection the client has open.
    """
    symbol = payload.symbol
    timeframe = payload.timeframe
    days = payload.days

    logger.info(f"[BACKTEST] POST received: {symbol} {timeframe} {days}d")

    if timeframe not in ("5m", "15m"):
        return {"status": "error", "message": f"Timeframe {timeframe} not supported for backtest. Use 5m or 15m."}

    if days < 1 or days > 30:
        return {"status": "error", "message": f"Days must be 1-30, got {days}"}

    # Prevent duplicate backtests for the same symbol:tf
    bt_key = f"{symbol}:{timeframe}"
    if bt_key in _BACKTEST_RUNNING:
        return {"status": "error", "message": f"Backtest already running for {symbol} {timeframe}"}

    # Check if WS client is registered
    ws = _BACKTEST_WS.get(bt_key)
    if not ws:
        logger.warning(f"[BACKTEST] No WS client registered for {bt_key}")
        return {"status": "error", "message": f"No WebSocket connection for {symbol} {timeframe}. Connect to the terminal first."}

    logger.info(f"[BACKTEST] Starting: {symbol} {timeframe} {days}d (WS client found)")

    asyncio.create_task(_run_backtest_async(symbol, timeframe, days))

    return {"status": "started", "symbol": symbol, "timeframe": timeframe, "days": days}


async def _run_backtest_async(symbol: str, timeframe: str, days: int):
    """Run the backtest in a thread and stream results via WebSocket in real-time."""
    bt_key = f"{symbol}:{timeframe}"
    _BACKTEST_RUNNING.add(bt_key)
    msg_queue: queue.Queue = queue.Queue()

    bt_thread = threading.Thread(
        target=_backtest_sync,
        args=(symbol, timeframe, days, msg_queue),
        daemon=True,
    )
    bt_thread.start()
    logger.info(f"[BACKTEST] Thread started for {bt_key}")

    try:
        # Drain the queue and send messages in real-time via WS
        got_complete = False
        while True:
            # Thread finished AND queue empty → done
            if not bt_thread.is_alive() and msg_queue.empty():
                break

            try:
                msg = msg_queue.get(timeout=0.3)
            except queue.Empty:
                await asyncio.sleep(0.05)  # Yield to event loop
                continue

            # Send the message via WS
            ws = _BACKTEST_WS.get(bt_key)
            if ws:
                try:
                    await ws.send_json(msg)
                except Exception as e:
                    logger.error(f"[BACKTEST] WS send failed: {e}")
                    _BACKTEST_WS.pop(bt_key, None)
                    break
            else:
                logger.warning(f"[BACKTEST] WS client gone for {bt_key}, stopping stream")
                break

            # If this was the completion message, we're done
            if msg.get("type") == "backtest_complete":
                got_complete = True
                break

        # If thread died without sending backtest_complete, send error
        if not got_complete:
            logger.error(f"[BACKTEST] Thread ended without sending complete for {bt_key}")
            ws = _BACKTEST_WS.get(bt_key)
            if ws:
                try:
                    await ws.send_json({"type": "backtest_complete", "data": {
                        "error": "Backtest thread crashed (no completion message)",
                        "trades": 0, "wins": 0, "losses": 0,
                        "wr": 0, "pnl_pct": 0, "profit_factor": 0, "max_drawdown": 0,
                    }})
                except Exception:
                    _BACKTEST_WS.pop(bt_key, None)

    except Exception as e:
        logger.error(f"[BACKTEST] Async streamer failed: {e}", exc_info=True)
        ws = _BACKTEST_WS.get(bt_key)
        if ws:
            try:
                await ws.send_json({"type": "backtest_complete", "data": {
                    "error": str(e), "trades": 0, "wins": 0, "losses": 0,
                    "wr": 0, "pnl_pct": 0, "profit_factor": 0, "max_drawdown": 0,
                }})
            except Exception:
                _BACKTEST_WS.pop(bt_key, None)
    finally:
        _BACKTEST_RUNNING.discard(bt_key)
        logger.info(f"[BACKTEST] Finished: {bt_key}")


def _backtest_sync(symbol: str, timeframe: str, days: int, msg_queue: queue.Queue) -> None:
    """Synchronous backtest logic — runs in a thread.

    This function replicates EXACTLY the same logic as full_replay_v21.py:
    - Loads IS + OOS data (DB first, Binance REST fallback)
    - Builds tries from IS data with Config F alpha=3
    - Runs OOS replay with Config F parameters
    - Streams results via msg_queue

    CRITICAL: ALL SQLite connections must be created INSIDE this function
    because SQLite objects can only be used in the thread that created them.
    """
    import copy
    import requests as _requests
    from ppmt.data.classifier import AssetClassifier
    from ppmt.data.storage import PPMTStorage as _PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
    from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
    from ppmt.core.regime import RegimeDetector
    from ppmt.core.profiles import SPREAD_ESTIMATES
    from ppmt.core.thresholds import TIMEFRAME_HARD_MOVE_FLOOR
    from ppmt.engine.weights import AdaptiveWeights
    from ppmt.core.sax import LEVEL_DUAL_ALPHA_CONFIG, LEVEL_DUAL_ALPHA_TF_OVERRIDES

    def _send(msg_type: str, data: dict):
        msg_queue.put({"type": msg_type, "data": data})

    # Config F parameters (EXACTLY matching full_replay_v21.py Config F)
    EV_THRESHOLD = 0.40
    SL_MULT = 2.0
    ALPHA_N3_N4 = 3  # Config F uses alpha=3
    CONFIG_F_WEIGHTS = {"n1": 0.10, "n2": 0.00, "n3": 0.90, "n4": 0.00, "n5": 0.00}
    HARD_MOVE_FLOOR = 0.10  # Config F: 0.10% for 5m
    CAPITAL_USDT = 1000.0
    RISK_PCT = 0.01
    IS_DAYS = 60  # In-sample period for trie building
    REGIME_WINDOW_SIZE = 10

    try:
        logger.info(f"[BACKTEST] _backtest_sync started: {symbol} {timeframe} {days}d")

        # 1. Classify asset
        classifier = AssetClassifier()
        info = classifier.classify(symbol)
        asset_class = info.asset_class
        weight_profile = info.weight_profile
        logger.info(f"[BACKTEST] Classified: {symbol} → {asset_class}/{weight_profile}")

        # 2. Load data — try DB first, fall back to Binance download
        storage = _PPMTStorage()  # NEW instance in this thread (SQLite safe)
        total_days = IS_DAYS + days
        df = None

        # Try loading from DB
        try:
            db_df = storage.load_ohlcv(symbol, timeframe)
            if db_df is not None and len(db_df) > 1000:
                logger.info(f"[BACKTEST] DB has {len(db_df)} candles for {symbol} {timeframe}")
                df = db_df
        except Exception as e:
            logger.warning(f"[BACKTEST] DB load failed: {e}")

        # Fall back to Binance REST API (direct, like full_replay_v21.py)
        if df is None:
            logger.info(f"[BACKTEST] Fetching {total_days}d of {timeframe} data from Binance REST API...")
            api_symbol = symbol.replace("/", "")
            tf_ms = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000}
            candle_ms = tf_ms.get(timeframe, 300000)
            target_candles = total_days * (86400000 // candle_ms)

            all_data = []
            end_ms = int(time.time() * 1000)
            current_end = end_ms
            total_fetched = 0

            while total_fetched < target_candles:
                batch_size = 1000
                start_ms = current_end - (batch_size * candle_ms)
                url = "https://api.binance.com/api/v3/klines"
                params = {
                    "symbol": api_symbol,
                    "interval": timeframe,
                    "limit": batch_size,
                    "startTime": start_ms,
                    "endTime": current_end,
                }
                try:
                    resp = _requests.get(url, params=params, timeout=15)
                    if resp.status_code != 200:
                        logger.warning(f"[BACKTEST] Binance HTTP {resp.status_code}")
                        break
                    data = resp.json()
                    if not data:
                        break
                    parsed = [
                        [int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])]
                        for c in data
                    ]
                    all_data.extend(parsed)
                    total_fetched += len(parsed)
                    current_end = int(data[0][0]) - 1
                    if len(parsed) < batch_size:
                        break
                except Exception as e:
                    logger.warning(f"[BACKTEST] Binance download error: {e}")
                    break

            if len(all_data) < 500:
                raise ValueError(f"Not enough data for {symbol} {timeframe}: {len(all_data)} candles (need 500+)")
            logger.info(f"[BACKTEST] Downloaded {len(all_data)} candles from Binance")

            df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
            df = df[~df.index.duplicated(keep="first")]
            df.sort_index(inplace=True)

        logger.info(f"[BACKTEST] Data: {len(df)} candles, {df.index[0]} → {df.index[-1]}")

        # 3. IS/OOS split (EXACTLY like full_replay_v21.py)
        total_candles = len(df)
        is_cutoff = int(total_candles * IS_DAYS / (IS_DAYS + days))
        is_df = df.iloc[:is_cutoff]
        oos_df = df.iloc[is_cutoff:]
        logger.info(f"[BACKTEST] IS/OOS split: {len(is_df)} IS + {len(oos_df)} OOS candles")

        # 4. Load or build tries with Config F alpha
        tf_key = f"{timeframe}_a{ALPHA_N3_N4}"  # e.g. "5m_a3"
        tries = storage.load_all_tries(symbol, asset_class, timeframe=tf_key)

        trie_counts = {}
        for lvl in ("n1", "n2", "n3", "n4"):
            t = tries.get(lvl)
            trie_counts[lvl] = t.pattern_count if t else 0
        logger.info(f"[BACKTEST] Tries from '{tf_key}': N1={trie_counts['n1']} N2={trie_counts['n2']} N3={trie_counts['n3']} N4={trie_counts['n4']}")

        # If no tries under the alpha key, build them from IS data
        if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
            logger.info(f"[BACKTEST] No tries under '{tf_key}', building from {len(is_df)} IS candles...")

            saved_n3_b = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
            saved_n4_b = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
            saved_tf_b = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)

            LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": ALPHA_N3_N4, "volume": 0}
            LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": ALPHA_N3_N4, "volume": 0}
            for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
                for lvl in ["n3", "n4"]:
                    LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)

            try:
                build_engine = PPMT(
                    symbol=symbol,
                    asset_class=asset_class,
                    weight_profile=weight_profile,
                    dual_sax=True,
                    min_confidence=0.08,
                    timeframe=timeframe,
                )
                build_count = build_engine.build(is_df)
                logger.info(f"[BACKTEST] Built {build_count} patterns from {len(is_df)} IS candles (α={ALPHA_N3_N4})")

                # Save tries to storage for future use
                if build_engine.trie_n1 and build_engine.trie_n1.pattern_count > 0:
                    storage.save_trie(UNIVERSAL_POOL_KEY, "n1", build_engine.trie_n1, timeframe=tf_key)
                if build_engine.trie_n2 and build_engine.trie_n2.pattern_count > 0:
                    storage.save_trie(class_pool_key(asset_class), "n2", build_engine.trie_n2, timeframe=tf_key)
                if build_engine.trie_n3 and build_engine.trie_n3.pattern_count > 0:
                    storage.save_trie(symbol, "n3", build_engine.trie_n3, timeframe=tf_key)
                if build_engine.trie_n4 and build_engine.trie_n4.pattern_count > 0:
                    storage.save_trie(symbol, "n4", build_engine.trie_n4, timeframe=tf_key)
            finally:
                LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3_b
                LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4_b
                LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
                LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_b)

            # Reload tries
            tries = storage.load_all_tries(symbol, asset_class, timeframe=tf_key)
            trie_counts = {}
            for lvl in ("n1", "n2", "n3", "n4"):
                t = tries.get(lvl)
                trie_counts[lvl] = t.pattern_count if t else 0
            logger.info(f"[BACKTEST] Rebuilt tries: N1={trie_counts['n1']} N2={trie_counts['n2']} N3={trie_counts['n3']} N4={trie_counts['n4']}")

        # Final check — still no tries?
        if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
            raise ValueError(f"No tries for {symbol} {timeframe}! Backtest cannot run.")

        # 5. Override alpha + hard_move_floor for Config F
        saved_n3 = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
        saved_n4 = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
        saved_tf_overrides = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)
        saved_hmf = TIMEFRAME_HARD_MOVE_FLOOR.get(timeframe, 0.15)

        LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": ALPHA_N3_N4, "volume": 0}
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": ALPHA_N3_N4, "volume": 0}
        for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
            for lvl in ["n3", "n4"]:
                LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)
        TIMEFRAME_HARD_MOVE_FLOOR[timeframe] = HARD_MOVE_FLOOR

        try:
            # 6. Create PPMT engine for replay
            engine = PPMT(
                symbol=symbol,
                asset_class=asset_class,
                weight_profile=weight_profile,
                dual_sax=True,
                min_confidence=0.08,
                timeframe=timeframe,
            )
            logger.info(f"[BACKTEST] PPMT engine created: symbol={symbol} asset_class={asset_class}")

            # 7. Apply Config F weights: N1=10%, N2=0%, N3=90%, N4=0%
            engine.weights = AdaptiveWeights(
                n1_universal=CONFIG_F_WEIGHTS["n1"],
                n2_asset_class=CONFIG_F_WEIGHTS["n2"],
                n3_per_asset=CONFIG_F_WEIGHTS["n3"],
                n4_per_asset_regime=CONFIG_F_WEIGHTS["n4"],
                n5_btc_context=CONFIG_F_WEIGHTS["n5"],
            )
            logger.info(f"[BACKTEST] Config F applied: EV>={EV_THRESHOLD} SL={SL_MULT}xDD N3=90% floor={HARD_MOVE_FLOOR}%")

            engine.set_tries(
                trie_n1=tries["n1"] if tries["n1"] else PPMTTrie(name="empty_n1"),
                trie_n2=tries["n2"] if tries["n2"] else PPMTTrie(name="empty_n2"),
                trie_n3=tries["n3"] if tries["n3"] else PPMTTrie(name="empty_n3"),
                trie_n4=tries["n4"] if tries["n4"] else engine.trie_n4,
            )
            logger.info(f"[BACKTEST] Tries set on engine")
        finally:
            # Restore global state IMMEDIATELY after engine creation
            LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3
            LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4
            LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
            LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_overrides)
            TIMEFRAME_HARD_MOVE_FLOOR[timeframe] = saved_hmf

        # 8. OOS replay — EXACTLY matching full_replay_v21.py run_replay()
        # No separate warmup phase: the engine processes OOS candles directly.
        # The first ~10-20 candles will not produce signals (SAX buffer filling),
        # which is expected and matches the script behavior.
        executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
        executor._position = None
        regime_detector = RegimeDetector()
        regime_window: list[dict] = []
        _last_engine_ts = 0

        trades = []
        wins = 0
        losses = 0
        total_pnl = 0.0
        gross_profit = 0.0
        gross_loss = 0.0
        max_drawdown = 0.0
        peak_pnl = 0.0
        long_count = 0
        short_count = 0
        total_signals_raw = 0
        signals_rejected_spread = 0
        signals_rejected_ev = 0
        regime_counts = {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0}

        spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)
        logger.info(f"[BACKTEST] Starting OOS replay: {len(oos_df)} candles, spread={spread_pct}%")

        for idx in range(len(oos_df)):
            row = oos_df.iloc[[idx]]
            current_price = float(row["close"].iloc[0])
            candle_high = float(row["high"].iloc[0])
            candle_low = float(row["low"].iloc[0])
            ts = oos_df.index[idx]
            ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)

            # ── Check SL/TP (script style: NO continue after close) ──
            if executor.is_in_position:
                pos = executor.position
                closed = None
                if pos.direction == "LONG":
                    if candle_low <= pos.catastrophic_sl:
                        closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif candle_low <= pos.current_sl:
                        closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif candle_high >= pos.current_tp:
                        closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
                else:
                    if candle_high >= pos.catastrophic_sl:
                        closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif candle_high >= pos.current_sl:
                        closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif candle_low <= pos.current_tp:
                        closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")

                if closed:
                    pnl = closed.pnl_pct or 0.0
                    if pnl > 0:
                        wins += 1
                        gross_profit += pnl
                    else:
                        losses += 1
                        gross_loss += abs(pnl)
                    total_pnl += pnl
                    peak_pnl = max(peak_pnl, total_pnl)
                    dd = peak_pnl - total_pnl
                    max_drawdown = max(max_drawdown, dd)

                    _send("backtest_trade", {
                        "direction": pos.direction, "entry": round(pos.entry_price, 6),
                        "exit": round(closed.close_price, 6), "pnl_pct": round(pnl, 2),
                        "close_reason": closed.close_reason or "UNKNOWN",
                        "timestamp": int(ts.timestamp()),
                    })
                    executor._position = None
                    # Script does NOT continue here — falls through to feed candle

            # ── Feed candle to engine (script style: uses iloc[[idx]] row) ──
            result: Optional[PPMTResult] = None
            if ts_sec > _last_engine_ts:
                _last_engine_ts = ts_sec

                # Regime detection (on every candle, script style)
                regime_window.append({
                    "open": float(row["open"].iloc[0]),
                    "high": candle_high,
                    "low": candle_low,
                    "close": current_price,
                    "volume": float(row["volume"].iloc[0]),
                })
                if len(regime_window) > REGIME_WINDOW_SIZE:
                    regime_window = regime_window[-REGIME_WINDOW_SIZE:]
                if len(regime_window) >= 2:
                    try:
                        rw_df = pd.DataFrame(regime_window)
                        detected = regime_detector.detect_simple(rw_df, timeframe=timeframe)
                        regime_counts[detected] += 1
                        engine.set_regime(detected)
                    except Exception:
                        regime_counts["ranging"] += 1
                        engine.set_regime("ranging")

                result = engine.process_new_candle(
                    candle_df=row,
                    current_price=current_price,
                    is_in_position=executor.is_in_position,
                    entry_price=executor.position.entry_price if executor.position else None,
                )

            if result is None:
                continue

            sig = result.signal if result and result.signal else None
            if sig is None or not sig.is_entry:
                continue
            if executor.is_in_position:
                continue

            total_signals_raw += 1

            # ── Net EV Gate (Config F) — same as full_replay_v21.py ──
            best_node = None
            for _mr in [result.n3_match, result.n1_match, result.n2_match, result.n4_match]:
                if _mr and _mr.node and _mr.node.metadata and _mr.node.metadata.historical_count > 0:
                    best_node = _mr.node
                    break

            favorable_pct = abs(best_node.metadata.max_favorable_pct) if best_node else 0.0
            drawdown_pct = abs(best_node.metadata.max_drawdown_pct) if best_node else 0.5

            if favorable_pct < 0.001:
                favorable_pct = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.1
            if drawdown_pct < 0.001:
                drawdown_pct = 0.5

            net_favorable = favorable_pct - spread_pct
            if net_favorable <= 0:
                signals_rejected_spread += 1
                continue

            net_rr = min(net_favorable / drawdown_pct, 3.0)
            net_ev = sig.confidence * net_rr

            if net_ev < EV_THRESHOLD:
                signals_rejected_ev += 1
                continue

            # ── Signal passed EV gate → open position ──
            direction = sig.direction or "LONG"
            expected_move_pct = sig.expected_move_pct or 1.0
            size_usdt = CAPITAL_USDT * RISK_PCT / (abs(expected_move_pct) * 0.012)
            size_usdt = min(size_usdt, CAPITAL_USDT)

            try:
                pos = executor.open_position_sync(
                    symbol=symbol,
                    direction=direction,
                    entry_price=current_price,
                    expected_move_pct=expected_move_pct,
                    predicted_path_symbols=sig.predicted_path_symbols if sig.predicted_path else None,
                    size_usdt=size_usdt,
                )
            except RuntimeError:
                continue

            # Config F: SL = max(default 1.2xEM, drawdown_pct x SL_MULT)
            sl_dist_pct = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
            dd_sl_pct = drawdown_pct * SL_MULT
            if dd_sl_pct > sl_dist_pct:
                extra = dd_sl_pct - sl_dist_pct
                if pos.direction == "LONG":
                    pos.current_sl -= pos.entry_price * (extra / 100.0)
                    pos.catastrophic_sl -= pos.entry_price * (extra / 100.0)
                else:
                    pos.current_sl += pos.entry_price * (extra / 100.0)
                    pos.catastrophic_sl += pos.entry_price * (extra / 100.0)

            if direction == "LONG":
                long_count += 1
            else:
                short_count += 1

            _send("backtest_signal", {
                "symbol": symbol, "direction": direction,
                "entry": round(current_price, 6), "confidence": round(sig.confidence, 3),
                "ev_score": round(net_ev, 2), "ev_passed": True,
                "timestamp": int(ts.timestamp()),
            })

            # ── Check entry candle for immediate SL/TP (script style) ──
            if executor.is_in_position:
                entry_closed = None
                if pos.direction == "LONG":
                    if candle_low <= pos.catastrophic_sl:
                        entry_closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif candle_low <= pos.current_sl:
                        entry_closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif candle_high >= pos.current_tp:
                        entry_closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
                else:
                    if candle_high >= pos.catastrophic_sl:
                        entry_closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif candle_high >= pos.current_sl:
                        entry_closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif candle_low <= pos.current_tp:
                        entry_closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")

                if entry_closed:
                    pnl = entry_closed.pnl_pct or 0.0
                    if pnl > 0:
                        wins += 1
                        gross_profit += pnl
                    else:
                        losses += 1
                        gross_loss += abs(pnl)
                    total_pnl += pnl
                    peak_pnl = max(peak_pnl, total_pnl)
                    dd = peak_pnl - total_pnl
                    max_drawdown = max(max_drawdown, dd)

                    _send("backtest_trade", {
                        "direction": pos.direction, "entry": round(pos.entry_price, 6),
                        "exit": round(entry_closed.close_price, 6), "pnl_pct": round(pnl, 2),
                        "close_reason": entry_closed.close_reason or "ENTRY_CANDLE",
                        "timestamp": int(ts.timestamp()),
                    })
                    executor._position = None
                    continue  # Script continues after entry candle close

            # Walk-Forward check (script style)
            if result and executor.is_in_position:
                current_sax = []
                buf = getattr(engine, '_streaming_buffer', None)
                if buf and buf._pattern_buffer:
                    last_sym = buf._pattern_buffer[-1]
                    if isinstance(last_sym, (tuple, list)):
                        current_sax = [str(s) for s in last_sym]
                    else:
                        current_sax = [str(last_sym)]
                if current_sax:
                    executor.check_walk_forward(current_sax, current_price)

        # ── Force-close remaining position at end ──
        if executor.is_in_position and executor._position:
            last_price = float(oos_df["close"].iloc[-1])
            closed = executor.force_close(last_price, "REPLAY_END")
            pnl = closed.pnl_pct or 0.0
            if pnl > 0:
                wins += 1
                gross_profit += pnl
            else:
                losses += 1
                gross_loss += abs(pnl)
            total_pnl += pnl

            _send("backtest_trade", {
                "direction": closed.direction, "entry": round(closed.entry_price, 6),
                "exit": round(last_price, 6), "pnl_pct": round(pnl, 2),
                "close_reason": "REPLAY_END",
                "timestamp": int(oos_df.index[-1].timestamp()) if isinstance(oos_df.index[-1], pd.Timestamp) else 0,
            })
            executor._position = None

        # ── Summary ──
        total_trades = wins + losses
        wr = round((wins / total_trades * 100), 1) if total_trades > 0 else 0
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.99 if gross_profit > 0 else 0.0)

        logger.info(
            f"[BACKTEST] Replay complete: {symbol} {timeframe} {days}d | "
            f"OOS={len(oos_df)} raw_signals={total_signals_raw} "
            f"rej_spread={signals_rejected_spread} rej_ev={signals_rejected_ev} | "
            f"trades={total_trades} WR={wr}% PnL={round(total_pnl,2)}% PF={pf} | "
            f"regimes={regime_counts}"
        )

        summary = {
            "trades": total_trades, "longs": long_count, "shorts": short_count,
            "wins": wins, "losses": losses, "wr": wr,
            "pnl_pct": round(total_pnl, 2), "profit_factor": pf,
            "max_drawdown": round(max_drawdown, 2),
            "signals_total": total_signals_raw,
            "signals_rejected_spread": signals_rejected_spread,
            "signals_rejected_ev": signals_rejected_ev,
        }

        _send("backtest_complete", summary)

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"[BACKTEST] _backtest_sync FAILED: {e}\n{tb}")
        _send("backtest_complete", {
            "error": str(e), "trades": 0, "wins": 0, "losses": 0,
            "wr": 0, "pnl_pct": 0, "profit_factor": 0, "max_drawdown": 0,
        })


# ─── WebSocket: Paper Live ────────────────────────────────────
@app.websocket("/ws/paper-live/{symbol}/{timeframe}")
async def paper_live_websocket(websocket: WebSocket, symbol: str, timeframe: str):
    """
    Real-time paper trading WebSocket.

    Flow:
    1. Accept connection
    2. Load tries from SQLite, warmup engine with historical data
    3. Poll loop: fetch candle from exchange → feed PPMT → emit messages
    """

    await websocket.accept()
    
    # Normalize symbol: URL path uses "DOGE-USDT" (no slashes in URL segments)
    # Convert to internal format "DOGE/USDT" and API format "DOGEUSDT"
    internal_symbol = symbol.replace("-", "/")  # "DOGE-USDT" → "DOGE/USDT"
    api_symbol = internal_symbol.replace("/", "")  # "DOGE/USDT" → "DOGEUSDT"
    symbol = internal_symbol
    
    logger.info(f"[WS] Connected: {symbol}/{timeframe}")

    # v2.1: Register this WS for backtest streaming
    _ws_session_id = f"{symbol}:{timeframe}"
    _BACKTEST_WS[_ws_session_id] = websocket
    storage = get_storage()

    # ─── 1. Initialize PPMT Engine ────────────────────────────
    try:
        # FIX #1: AssetClassifier.classify() returns AssetInfo object, NOT dict
        asset_class = "meme"  # Default fallback
        weight_profile = "meme"
        try:
            from ppmt.data.classifier import AssetClassifier
            classifier = AssetClassifier()
            info = classifier.classify(symbol)
            asset_class = info.asset_class       # NOT info.get("asset_class")
            weight_profile = info.weight_profile  # Also extract weight_profile
            logger.info(f"[WS] Classified: {symbol} → asset_class={asset_class}, weight_profile={weight_profile}")
        except Exception as e:
            logger.warning(f"[WS] AssetClassifier unavailable ({e}), defaulting to 'meme'")

        engine = PPMT(
            symbol=symbol,
            asset_class=asset_class,
            weight_profile=weight_profile,   # Pass correct weight_profile
            dual_sax=True,
            min_confidence=0.08,
            timeframe=timeframe,             # Pass timeframe for correct SAX params
        )

        # Load pre-built tries from SQLite — MANDATORY for PPMT
        # v0.57.0 (TAREA 19): Pass timeframe to load_all_tries so it loads
        # the correct per-timeframe tries (5m, 15m, etc.)
        tries = storage.load_all_tries(symbol, asset_class, timeframe=timeframe)
        
        # Log what we loaded with pattern counts
        n1 = tries.get("n1")
        n2 = tries.get("n2")
        n3 = tries.get("n3")
        n4 = tries.get("n4")
        
        n1_count = n1.pattern_count if n1 else 0
        n2_count = n2.pattern_count if n2 else 0
        n3_count = n3.pattern_count if n3 else 0
        n4_count = n4.pattern_count if hasattr(n4, 'pattern_count') and n4 else 0
        
        logger.info(
            f"[WS] Loaded N1: {n1_count} patterns, "
            f"N2: {n2_count} patterns, "
            f"N3: {n3_count} patterns, "
            f"N4: {n4_count} patterns"
        )
        
        if n1 or n2 or n3:
            # N4 may be None — keep the default RegimePartitionedTrie instead
            from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
            # v0.49.0 (FASE 3 BUG 3): DO NOT fall back to engine.trie_n1/n2 —
            # those are empty in storage mode and would mask a missing shared pool.
            # Use empty tries + WARNING instead (same as realtime.py FIX-1B).
            _n1 = n1 if n1 is not None else None
            _n2 = n2 if n2 is not None else None
            if _n1 is None:
                _n1 = PPMTTrie(name="universal_empty")
                logger.warning(f"[WS] N1 universal pool not found for {symbol}. Using empty trie — confidence will be 0.")
            if _n2 is None:
                _n2 = PPMTTrie(name=f"class_empty:{asset_class}")
                logger.warning(f"[WS] N2 class pool not found for {asset_class}. Using empty trie — confidence will be 0.")
            engine.set_tries(
                trie_n1=_n1,
                trie_n2=_n2,
                trie_n3=n3 or PPMTTrie(name="n3_empty"),
                trie_n4=n4 if n4 is not None else engine.trie_n4,
            )
            logger.info(f"[WS] Tries injected into engine — Transfer Learning ACTIVE")
        else:
            logger.warning(f"[WS] No tries found for {symbol}/{asset_class}, engine runs without tries")

    except Exception as e:
        import traceback
        logger.error(f"[WS] Engine init failed: {e}")
        traceback.print_exc()
        await websocket.send_json({"type": "error", "data": {"message": f"Engine init failed: {e}"}})
        await websocket.close()
        return

    # ─── 2. Initialize PaperExecutor (via IExecutor interface) ──
    # v0.49.0 (FASE 3 BUG 2): Ensure clean state on new WS session.
    # Previously, if a session restarted, stale position data from
    # a prior session could leak through. Now we explicitly reset.
    executor: IExecutor = PaperExecutor(capital_usdt=100.0)
    executor._position = None  # Force FLAT state

    # ─── 3. Initialize Exchange Poller ────────────────────────
    from ppmt.engine.realtime import _DirectPollExchange
    exchange = _DirectPollExchange("binance")

    # ─── 4. Warmup: Load historical candles into engine ───────
    warmup_count = 0
    try:
        ohlcv_raw = await exchange.fetch_ohlcv(api_symbol, timeframe, limit=500)
        if ohlcv_raw:
            # Convert to DataFrame
            df = pd.DataFrame(ohlcv_raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)

            # Feed all but the last candle for warmup
            for i in range(len(df) - 1):
                row = df.iloc[[i]]
                result = engine.process_new_candle(
                    candle_df=row,
                    current_price=float(row["close"].iloc[0]),
                )
                if result is not None:
                    warmup_count += 1

            # Send the last 50 candles to the frontend for chart display
            for i in range(max(0, len(df) - 50), len(df)):
                r = df.iloc[i]
                await websocket.send_json({
                    "type": "candle",
                    "data": {
                        "time": int(df.index[i].timestamp()) if isinstance(df.index[i], pd.Timestamp) else int(df.index[i]),
                        "open": float(r["open"]),
                        "high": float(r["high"]),
                        "low": float(r["low"]),
                        "close": float(r["close"]),
                    },
                })

            logger.info(f"[WS] Warmup complete: {len(df)} candles, {warmup_count} SAX outputs")
            # Log SAX buffer state after warmup to verify engine is alive
            _w_buf = getattr(engine, '_streaming_buffer', None)
            _w_n1 = getattr(engine, '_streaming_buffer_n1', None)
            _w_len = len(_w_buf._pattern_buffer) if _w_buf else 0
            _w_n1_len = len(_w_n1._pattern_buffer) if _w_n1 else 0
            logger.info(
                f"[WS] Post-warmup SAX: buf_n3={_w_len} symbols, buf_n1={_w_n1_len} symbols"
            )
    except Exception as e:
        logger.warning(f"[WS] Warmup fetch failed: {e}. Continuing without historical data.")

    # ─── 5. Polling interval ─────────────────────────────────
    tf_seconds = {"1m": 5, "5m": 10, "15m": 15, "1h": 30}
    poll_interval = tf_seconds.get(timeframe, 5)

    # ─── v2.1 FIX: Regime detection for N4 ─────────────────────
    # BUG: Without set_regime(), N4's RegimePartitionedTrie defaults
    # to _current_regime="trending_up" → 100% LONG bias.
    # Fix: maintain a rolling window of recent candles and call
    # detect_simple() before each process_new_candle().
    from ppmt.core.regime import RegimeDetector
    _regime_detector = RegimeDetector()
    _regime_window: list[dict] = []  # rolling OHLCV window for regime
    _REGIME_WINDOW_SIZE = 10  # same as trie build uses
    _last_regime = "ranging"  # default until we have data

    last_candle_ts = 0

    # ─── 6. Main poll loop ────────────────────────────────────
    # v2.1 FIX: Separate UI price updates from engine candle processing.
    # - UI gets the LATEST price on EVERY poll (forming candle updates)
    # - Engine only gets CLOSED candles (new timestamp = new candle)
    # This ensures the displayed price matches Binance in real-time.
    _last_engine_ts = 0  # Last candle timestamp fed to engine
    _last_ui_update = 0.0  # Timestamp of last UI update (monotonic)
    _ticker_price = 0.0  # v2.1: Real-time price from /api/v3/ticker/price
    # v2.1 FIX: Sticky N3/N4 confidence — preserve last known good values
    # between full matches (result=YES). Without this, the quick match
    # (result=no) overwrites N3/N4 with 0.0 every 10s, causing the UI
    # to show 0.00 between candles.
    _sticky_n3_conf = 0.0
    _sticky_n4_conf = 0.0
    _sticky_direction = "FLAT"
    _sticky_direction_score = 0.0
    try:
        while True:
            try:
                # Fetch latest candles (last 2: closed + forming)
                ohlcv_raw = await exchange.fetch_ohlcv(api_symbol, timeframe, limit=2)

                if not ohlcv_raw:
                    await asyncio.sleep(poll_interval)
                    continue

                # ─── Fetch REAL-TIME price from Binance ticker ───
                # klines close price can lag (forming candle, cache).
                # /api/v3/ticker/price returns the LAST TRADE price —
                # the most accurate real-time price for display.
                try:
                    ticker = await exchange.fetch_ticker(api_symbol)
                    _ticker_price = float(ticker.get("last", 0))
                except Exception as _te:
                    logger.debug(f"[WS] Ticker fetch failed (using kline close): {_te}")
                    _ticker_price = 0.0

                latest = ohlcv_raw[-1]
                ts_ms, o, h, l, c, v = latest
                ts_sec = int(ts_ms / 1000)
                kline_close = float(c)
                # Use ticker price for display/engine if available, fallback to kline close
                current_price = _ticker_price if _ticker_price > 0 else kline_close

                # ─── Diagnostic logging ─────────────────────────
                # Log the exact timestamps so we can verify data freshness
                logger.info(
                    f"[WS] Binance fetch: kline_ts={ts_sec} kline_C={kline_close:.6f} "
                    f"ticker_C={_ticker_price:.6f} delta={abs(_ticker_price - kline_close):.6f} "
                    f"({symbol})"
                )

                # Emit candle to frontend on EVERY poll.
                # candleSeries.update() handles overwriting the forming candle.
                # ticker_price is the real-time price for display; chart uses OHLC.
                candle_msg = {
                    "type": "candle",
                    "data": {
                        "time": ts_sec,
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "ticker_price": round(_ticker_price, 8) if _ticker_price > 0 else None,
                    },
                }
                await websocket.send_json(candle_msg)
                _last_ui_update = time.monotonic()

                # ─── Feed CLOSED candles to PPMT engine ──────────
                # Only process when a NEW candle closes (timestamp changes).
                # The forming candle would corrupt the SAX buffer.
                result = None  # Reset on every tick
                if ts_sec > _last_engine_ts:
                    _last_engine_ts = ts_sec
                    logger.info(
                        f"[WS] Candle: ts={ts_sec} C={current_price:.6f} ({symbol})"
                    )

                    # ── v2.1 FIX: Update regime for N4 ────────────
                    # Maintain rolling window of recent candles and
                    # detect regime before feeding the candle to engine.
                    _regime_window.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
                    if len(_regime_window) > _REGIME_WINDOW_SIZE:
                        _regime_window = _regime_window[-_REGIME_WINDOW_SIZE:]
                    if len(_regime_window) >= 2:
                        try:
                            _rw_df = pd.DataFrame(_regime_window)
                            _detected_regime = _regime_detector.detect_simple(_rw_df, timeframe=timeframe)
                            if _detected_regime != _last_regime:
                                logger.info(f"[WS] Regime changed: {_last_regime} → {_detected_regime} ({symbol})")
                                _last_regime = _detected_regime
                            engine.set_regime(_detected_regime)
                        except Exception as _re:
                            # Don't block trading if regime detection fails
                            logger.debug(f"[WS] Regime detection failed: {_re}")

                    candle_df = pd.DataFrame(
                        {"open": [o], "high": [h], "low": [l], "close": [c], "volume": [v]},
                        index=pd.DatetimeIndex([datetime.fromtimestamp(ts_sec, tz=timezone.utc)]),
                    )

                    result: Optional[PPMTResult] = engine.process_new_candle(
                        candle_df=candle_df,
                        current_price=current_price,
                        is_in_position=executor.is_in_position,
                        entry_price=executor.position.entry_price if executor.position else None,
                    )

                # ─── SAX output log (ENTREGABLE 13 FIX) ───────
                # Log current SAX buffer state on EVERY candle so we can
                # verify the engine keeps thinking after warmup.
                _sax_buf = getattr(engine, '_streaming_buffer', None)
                _sax_n1_buf = getattr(engine, '_streaming_buffer_n1', None)
                _buf_len = len(_sax_buf._pattern_buffer) if _sax_buf else 0
                _buf_n1_len = len(_sax_n1_buf._pattern_buffer) if _sax_n1_buf else 0
                _sax_sym = str(_sax_buf._pattern_buffer[-1]) if _sax_buf and _sax_buf._pattern_buffer else "—"
                logger.info(
                    f"[WS] SAX output: [{_sax_sym}] buf_n3={_buf_len} buf_n1={_buf_n1_len}"
                    f" result={'YES' if result else 'no'} C={current_price:.6f}"
                )

                # ─── Emit brain_update ────────────────────────
                # FIX #2: SAX serialization — decompose tuples into pure JSON string arrays
                # FIX #3: Build active_path_ids from matched Trie nodes
                #
                # IMPORTANT: process_new_candle() only returns a PPMTResult when
                # a SAX window completes (every W=45 candles for 1m). We must
                # extract the current SAX state from the engine's streaming buffers
                # on EVERY candle so the frontend gets continuous updates.
                current_sax = []
                active_path_ids = ["root"]
                n1_conf = 0.0
                n2_conf = 0.0
                n3_conf = _sticky_n3_conf  # v2.1 FIX: Start with sticky values
                n4_conf = _sticky_n4_conf  # v2.1 FIX: Start with sticky values
                weighted_conf = 0.0
                signal_type = "NO_SIGNAL"
                direction = _sticky_direction  # v2.1 FIX: Start with sticky direction
                direction_score = _sticky_direction_score  # v2.1 FIX
                _last_best_level = None  # Track last known active path level

                if result is not None:
                    # A SAX window completed — extract full match data
                    # Current SAX symbol(s) — extract last symbol, decompose tuples
                    if result.sax_symbols:
                        last_sym = result.sax_symbols[-1]
                        if isinstance(last_sym, tuple):
                            current_sax = [str(s) for s in last_sym]
                        elif isinstance(last_sym, list):
                            current_sax = [str(s) for s in last_sym]
                        else:
                            current_sax = [str(last_sym)]

                    # Active path from match results — build cumulative path IDs
                    best_match = None
                    best_level = None
                    for level, match in [
                        ("n3", result.n3_match),
                        ("n2", result.n2_match),
                        ("n4", result.n4_match),
                        ("n1", result.n1_match),
                    ]:
                        if match and match.node:
                            conf = match.node.metadata.confidence if match.node.metadata else 0.0
                            if best_match is None or conf > (best_match.node.metadata.confidence if best_match.node and best_match.node.metadata else 0.0):
                                best_match = match
                                best_level = level
                    
                    if best_match and best_match.node:
                        backward_path = best_match.node.get_backward_path()
                        active_path_ids = _build_active_path_ids(backward_path)
                        _last_best_level = best_level
                        logger.info(
                            f"[WS] Active path ({best_level}): {active_path_ids} "
                            f"conf={f'{best_match.node.metadata.confidence:.3f}' if best_match.node.metadata else '0.000'}"
                        )

                    n1_conf = result.n1_confidence
                    n2_conf = result.n2_confidence
                    weighted_conf = result.weighted_confidence
                    signal_type = result.signal.signal_type.value if result.signal else "NO_SIGNAL"
                    # v2.1 FIX: Only update n3/n4 from full match if > 0.
                    # A full match (result is not None) can still have n3_confidence=0
                    # if N3 didn't match. Overwriting sticky with 0 destroys the
                    # last-known-good value that the UI should display.
                    if result.n3_confidence > 0:
                        n3_conf = result.n3_confidence
                        _sticky_n3_conf = n3_conf
                    # else: keep n3_conf = _sticky_n3_conf (set at line ~626)
                    if result.n4_confidence > 0:
                        n4_conf = result.n4_confidence
                        _sticky_n4_conf = n4_conf
                    # else: keep n4_conf = _sticky_n4_conf
                    # v2.1 FIX: Only update direction sticky when non-FLAT
                    if hasattr(result, 'direction') and result.direction and result.direction != "FLAT":
                        direction = result.direction
                        _sticky_direction = direction
                    if hasattr(result, 'direction_score') and result.direction_score != 0:
                        direction_score = result.direction_score
                        _sticky_direction_score = direction_score
                else:
                    # No SAX window completed this candle — still extract
                    # current buffer state so the frontend shows partial progress
                    buf = getattr(engine, '_streaming_buffer', None)
                    buf_n1 = getattr(engine, '_streaming_buffer_n1', None)
                    buf_n2 = getattr(engine, '_streaming_buffer_n2', None)
                    
                    if buf and buf._pattern_buffer:
                        last_sym = buf._pattern_buffer[-1]
                        if isinstance(last_sym, tuple):
                            current_sax = [str(s) for s in last_sym]
                        else:
                            current_sax = [str(last_sym)]
                    
                    # Re-run a quick match on current buffer state to get active_path
                    if buf and buf.has_pattern():
                        try:
                            current_pattern_n3 = buf.get_pattern()
                            current_pattern_n1 = buf_n1.get_pattern() if buf_n1 else None
                            current_pattern_n2 = buf_n2.get_pattern() if buf_n2 else None
                            
                            quick_result = engine.match(
                                current_symbols=current_pattern_n3,
                                current_price=current_price,
                                is_in_position=executor.is_in_position,
                                entry_price=executor.position.entry_price if executor.position else None,
                                current_symbols_n1=current_pattern_n1 or None,
                                current_symbols_n2=current_pattern_n2 or None,
                                current_symbols_n3=current_pattern_n3,
                            )
                            # v2.1 FIX: Also extract N4 buffer symbols
                            buf_n4 = getattr(engine, '_streaming_buffer_n4', None)
                            current_pattern_n4 = buf_n4.get_pattern() if buf_n4 and buf_n4.has_pattern() else None

                            if quick_result:
                                n1_conf = quick_result.n1_confidence
                                n2_conf = quick_result.n2_confidence
                                # v2.1 FIX: Only update N3/N4 if quick match found them,
                                # otherwise keep sticky values from last full match.
                                if quick_result.n3_confidence > 0:
                                    n3_conf = quick_result.n3_confidence
                                    _sticky_n3_conf = n3_conf
                                if quick_result.n4_confidence > 0:
                                    n4_conf = quick_result.n4_confidence
                                    _sticky_n4_conf = n4_conf
                                weighted_conf = quick_result.weighted_confidence
                                if quick_result.direction and quick_result.direction != "FLAT":
                                    direction = quick_result.direction
                                    _sticky_direction = direction
                                if quick_result.direction_score != 0:
                                    direction_score = quick_result.direction_score
                                    _sticky_direction_score = direction_score
                                
                                # Find best match for active path
                                best_match = None
                                best_level = None
                                for level, match in [
                                    ("n3", quick_result.n3_match),
                                    ("n2", quick_result.n2_match),
                                    ("n4", quick_result.n4_match),
                                    ("n1", quick_result.n1_match),
                                ]:
                                    if match and match.node:
                                        conf = match.node.metadata.confidence if match.node.metadata else 0.0
                                        if best_match is None or conf > (best_match.node.metadata.confidence if best_match.node and best_match.node.metadata else 0.0):
                                            best_match = match
                                            best_level = level
                                
                                if best_match and best_match.node:
                                    backward_path = best_match.node.get_backward_path()
                                    active_path_ids = _build_active_path_ids(backward_path)
                                    _last_best_level = best_level
                                    logger.info(
                                        f"[WS] Quick match ({best_level}): path={active_path_ids} "
                                        f"n1={n1_conf:.3f} n2={n2_conf:.3f} n3={n3_conf:.3f} n4={n4_conf:.3f} wconf={weighted_conf:.3f}"
                                    )
                        except Exception as e:
                            logger.debug(f"[WS] Quick match failed: {e}")
                    else:
                        logger.debug(f"[WS] No pattern in buffer yet (buf={buf is not None}, symbols={len(buf._pattern_buffer) if buf else 0})")

                # ─── Extract full current SAX pattern buffer ───────
                current_pattern = []
                _pat_buf = getattr(engine, '_streaming_buffer', None)
                if _pat_buf and _pat_buf._pattern_buffer:
                    for _sym in _pat_buf._pattern_buffer:
                        if isinstance(_sym, tuple):
                            current_pattern.append([str(s) for s in _sym])
                        else:
                            current_pattern.append(str(_sym))

                # ─── Get last known EV score for this symbol ────────
                _ev_info = _LAST_NET_EV.get(symbol, {})

                # v2.1 FIX: Build brain_update using STICKY values for n3/n4.
                # If sticky is 0.0 (never initialized), send None so the frontend
                # keeps its last display instead of resetting to 0.00.
                _brain_n3 = round(_sticky_n3_conf, 4) if _sticky_n3_conf > 0 else None
                _brain_n4 = round(_sticky_n4_conf, 4) if _sticky_n4_conf > 0 else None
                _brain_dir = _sticky_direction if _sticky_direction != "FLAT" else direction
                _brain_dir_score = round(_sticky_direction_score, 4) if _sticky_direction_score != 0 else round(direction_score, 4)

                brain_msg = {
                    "type": "brain_update",
                    "data": {
                        "current_sax_symbol": current_sax,
                        "active_path_ids": active_path_ids,
                        "n1_confidence": round(n1_conf if n1_conf and n1_conf == n1_conf else 0.0, 4),  # NaN guard
                        "n2_confidence": round(n2_conf if n2_conf and n2_conf == n2_conf else 0.0, 4),
                        "n3_confidence": _brain_n3,  # v2.1 FIX: null when no sticky value
                        "n4_confidence": _brain_n4,  # v2.1 FIX: null when no sticky value
                        "weighted_confidence": round(weighted_conf if weighted_conf and weighted_conf == weighted_conf else 0.0, 4),
                        "signal_type": signal_type,
                        "current_pattern": current_pattern,
                        "direction": _brain_dir,
                        "direction_score": _brain_dir_score,
                        "ev_score": round(_ev_info.get("ev_score", 0.0) or 0.0, 3),
                        "ev_passed": _ev_info.get("passed", False),
                        "net_rr": round(_ev_info.get("net_rr", 0.0) or 0.0, 2),
                        "ticker_price": round(_ticker_price, 8) if _ticker_price > 0 else None,  # v2.1 FIX: Price in brain_update too
                    },
                }
                # v2.1 TEMP LOG: Verify brain_update values before sending
                logger.info(
                    f"[BRAIN-JSON] {symbol} n1={n1_conf:.3f} n2={n2_conf:.3f} "
                    f"n3={_brain_n3} n4={_brain_n4} dir={_brain_dir} "
                    f"sticky_n3={_sticky_n3_conf:.3f} sticky_n4={_sticky_n4_conf:.3f} "
                    f"result={'YES' if result else 'no'}"
                )
                await websocket.send_json(brain_msg)

                # ─── Signal → Net EV Gate → Routed execution ─────
                # v0.57.0 (TAREA 19): Net EV Gate with friction-aware filtering.
                # Before opening any position, we discount the expected_move
                # by the real spread+slippage for this asset class. If the
                # net move doesn't cover costs, the signal is REJECTED.
                if result and result.signal and result.signal.is_entry and not (isinstance(executor, PaperExecutor) and executor.is_in_position):
                    sig = result.signal
                    _NET_EV_STATS["total_raw_signals"] += 1

                    # ─── Anti-overlap check ──────────────────────
                    # If this symbol already has a position open in
                    # another TF, REJECT to avoid correlated exposure.
                    if symbol in _ACTIVE_SYMBOLS:
                        existing_tf = _ACTIVE_SYMBOLS[symbol]
                        _NET_EV_STATS["rejected_overlap"] += 1
                        _LAST_NET_EV[symbol] = {"ev_score": 0.0, "passed": False, "net_rr": 0.0, "conf": sig.confidence, "reason": "overlap"}
                        logger.info(
                            f"[NET EV GATE] OVERLAP REJECTED: {symbol} already has "
                            f"position in {existing_tf}, ignoring {timeframe} signal"
                        )
                        await _emit_log(websocket, "EV GATE", f"OVERLAP REJECTED: {symbol} already in {existing_tf}", "warn")
                    else:
                        # ─── Net EV Gate calculation ─────────────
                        # The signal's expected_move_pct is the AVERAGE move (small).
                        # For R:R, we use the historical BEST outcome (max_favorable)
                        # and worst drawdown from the matched pattern node.
                        # Net favorable = max_favorable - spread (friction-aware).

                        # 1. Get favorable/drawdown from matched node metadata
                        # Walk through match results to find best node with metadata
                        _best_node = None
                        for _lvl, _mr in [("n3", result.n3_match), ("n1", result.n1_match),
                                          ("n2", result.n2_match), ("n4", result.n4_match)]:
                            if _mr and _mr.node and _mr.node.metadata.historical_count > 0:
                                _best_node = _mr.node
                                break

                        favorable_pct = abs(_best_node.metadata.max_favorable_pct) if _best_node else 0.0
                        drawdown_pct = abs(_best_node.metadata.max_drawdown_pct) if _best_node else 0.5
                        if favorable_pct < 0.001:
                            favorable_pct = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.1
                        if drawdown_pct < 0.001:
                            drawdown_pct = 0.5

                        # 2. Get spread for this asset class
                        from ppmt.core.profiles import SPREAD_ESTIMATES
                        spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)  # default 0.05%

                        # 3. Net Favorable = max_favorable - spread
                        net_favorable = favorable_pct - spread_pct

                        # 4. If Net_Favorable <= 0: REJECT (signal doesn't cover costs)
                        if net_favorable <= 0:
                            _NET_EV_STATS["rejected_spread"] += 1
                            _LAST_NET_EV[symbol] = {"ev_score": 0.0, "passed": False, "net_rr": 0.0, "conf": sig.confidence, "reason": "spread"}
                            logger.info(
                                f"[NET EV GATE] SPREAD REJECTED: {symbol} {timeframe} "
                                f"favorable={favorable_pct:.3f}% "
                                f"spread={spread_pct:.3f}% "
                                f"net_favorable={net_favorable:.3f}% (≤0)"
                            )
                            await _emit_log(websocket, "EV GATE", f"SPREAD REJECTED: {symbol} favorable={favorable_pct:.3f}% spread={spread_pct:.3f}%", "warn")
                        else:
                            # 5. Net R:R = net_favorable / drawdown
                            net_rr = net_favorable / drawdown_pct
                            net_rr_capped = min(net_rr, 3.0)

                            # 6. Net EV = confidence × min(Net_R:R, 3.0)
                            net_ev = sig.confidence * net_rr_capped

                            # 7. Gate: Net EV must be >= 0.40 (v2.1 Config F)
                            # v2.1: Lowered from 0.80 to 0.40 — validated 30-day OOS
                            # Config F: N3=90%, N4=0%, SL=2.0×drawdown, hard_move_floor=0.10%
                            _EV_THRESHOLD = 0.40
                            if net_ev < _EV_THRESHOLD:
                                _NET_EV_STATS["rejected_ev_score"] += 1
                                _LAST_NET_EV[symbol] = {"ev_score": net_ev, "passed": False, "net_rr": net_rr_capped, "conf": sig.confidence, "reason": "ev_score"}
                                logger.info(
                                    f"[NET EV GATE] EV REJECTED: {symbol} {timeframe} "
                                    f"conf={sig.confidence:.3f} net_R:R={net_rr_capped:.2f} "
                                    f"Net_EV={net_ev:.3f} (min {_EV_THRESHOLD})"
                                )
                                await _emit_log(websocket, "EV GATE", f"EV REJECTED: conf={sig.confidence:.3f} R:R={net_rr_capped:.2f} EV={net_ev:.3f}", "warn")
                            else:
                                # ─── PASSED: Open position ─────────
                                _NET_EV_STATS["passed_net_ev"] += 1
                                _ACTIVE_SYMBOLS[symbol] = timeframe  # Register in anti-overlap
                                _LAST_NET_EV[symbol] = {"ev_score": net_ev, "passed": True, "net_rr": net_rr_capped, "conf": sig.confidence}
                                logger.info(
                                    f"[NET EV GATE] PASSED: {symbol} {timeframe} "
                                    f"conf={sig.confidence:.3f} net_R:R={net_rr_capped:.2f} "
                                    f"Net_EV={net_ev:.3f} spread={spread_pct:.3f}% "
                                    f"favorable={favorable_pct:.3f}% drawdown={drawdown_pct:.3f}%"
                                )
                                await _emit_log(websocket, "EV GATE", f"PASSED: conf={sig.confidence:.3f} R:R={net_rr_capped:.2f} EV={net_ev:.3f}", "info")
                                try:
                                    pos = await executor.open_position(
                                        symbol=symbol,
                                        direction=sig.direction or "LONG",
                                        size_usdt=100.0,
                                        metadata={
                                            "entry_price": current_price,
                                            "expected_move_pct": sig.expected_move_pct or 1.0,
                                            "predicted_path_symbols": sig.predicted_path_symbols if sig.predicted_path else None,
                                            "net_ev_score": net_ev,
                                            "net_rr": net_rr_capped,
                                            "spread_pct": spread_pct,
                                        },
                                    )
                                    # v2.1 Config F: SL = max(default 1.2×EM, drawdown_pct × 2.0)
                                    _SL_MULT = 2.0
                                    current_sl_distance_pct = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
                                    drawdown_sl_pct = drawdown_pct * _SL_MULT
                                    if drawdown_sl_pct > current_sl_distance_pct:
                                        extra_distance = drawdown_sl_pct - current_sl_distance_pct
                                        if pos.direction == "LONG":
                                            pos.current_sl -= pos.entry_price * (extra_distance / 100.0)
                                            pos.catastrophic_sl -= pos.entry_price * (extra_distance / 100.0)
                                        else:
                                            pos.current_sl += pos.entry_price * (extra_distance / 100.0)
                                            pos.catastrophic_sl += pos.entry_price * (extra_distance / 100.0)
                                    # Track in session-wide positions
                                    _OPEN_POSITIONS[symbol] = {
                                        "direction": pos.direction,
                                        "entry_price": pos.entry_price,
                                        "size_usdt": pos.size_usdt,
                                        "pnl_pct": 0.0,
                                        "ev_score": net_ev,
                                        "status": pos.status,
                                        "timeframe": timeframe,
                                    }
                                    logger.info(
                                        f"[WS] SIGNAL {sig.signal_type.value} @ {current_price:.6f} "
                                        f"conf={sig.confidence:.3f} Net_EV={net_ev:.3f} "
                                        f"SL={pos.current_sl:.6f} TP={pos.current_tp:.6f}"
                                    )

                                    # v0.58.0: TAREA 20 — Register position in live session tracker
                                    _session_key = f"{symbol}:{timeframe}"
                                    _LIVE_SESSIONS[_session_key] = {
                                        "symbol": symbol,
                                        "direction": sig.direction or "LONG",
                                        "entry_price": current_price,
                                        "size_usdt": 100.0,
                                        "pnl_pct": "0.0%",
                                        "ev_score": round(net_ev, 4),
                                        "ev_r": round(net_ev, 4),
                                        "status": "ACTIVE",
                                        "timeframe": timeframe,
                                    }
                                    logger.info(f"[WS] Session tracker: registered {_session_key}")

                                    # v0.58.0: TAREA 21 — Track in _OPEN_POSITIONS + emit log
                                    _OPEN_POSITIONS[symbol] = {
                                        "direction": pos.direction,
                                        "entry_price": pos.entry_price,
                                        "size_usdt": pos.size_usdt,
                                        "pnl_pct": 0.0,
                                        "ev_score": net_ev,
                                        "status": pos.status,
                                        "timeframe": timeframe,
                                    }
                                    await _emit_log(websocket, "SIGNAL", f"{sig.signal_type.value} {pos.direction} @ {current_price:.6f} EV={net_ev:.3f}", "info")
                                    await websocket.send_json({
                                        "type": "position_update",
                                        "data": pos.to_dict(),
                                    })
                                except Exception as e:
                                    # Position failed — remove from active symbols
                                    _ACTIVE_SYMBOLS.pop(symbol, None)
                                    _OPEN_POSITIONS.pop(symbol, None)
                                    logger.error(f"[WS] Failed to open position: {e}")

                # ─── Walk-Forward check ───────────────────────
                if result and executor.is_in_position and current_sax:
                    updated = executor.check_walk_forward(current_sax, current_price)
                    if updated:
                        # Check if this was a MATCH (sequence_index advanced)
                        _old_idx = updated.sequence_index
                        logger.info(
                            f"[WS] Walk-Forward: seq_idx={updated.sequence_index} "
                            f"status={updated.status} SL={updated.current_sl:.6f} TP={updated.current_tp:.6f}"
                        )
                        await _emit_log(websocket, "WALK-FORWARD", f"MATCH #{updated.sequence_index} → {updated.status} SL={updated.current_sl:.6f}", "info")
                        await websocket.send_json({
                            "type": "position_update",
                            "data": updated.to_dict(),
                        })

                # ─── Pattern Divergence check ───────────────────
                # v0.60.0 (TERMINAL-v2.1): Extract pattern_break_score from
                # the best match result and pass to executor.
                if executor.is_in_position and result is not None:
                    _break_score = 1.0  # Default: assume continuation
                    for _mr in [result.n3_match, result.n1_match, result.n2_match, result.n4_match]:
                        if _mr is not None:
                            _break_score = getattr(_mr, 'pattern_break_score', 1.0)
                            break  # Use first available match

                    divergence_closed = executor.check_divergence(_break_score, current_price)
                    if divergence_closed:
                        closed = executor._position  # Already closed by check_divergence
                        if closed:
                            _ACTIVE_SYMBOLS.pop(symbol, None)
                            _OPEN_POSITIONS.pop(symbol, None)
                            # v0.60.0 (TERMINAL-v2.1): Append to trade history
                            _TRADE_HISTORY.append({
                                "timestamp": int(time.time() * 1000),
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "direction": closed.direction,
                                "entry_price": closed.entry_price,
                                "exit_price": closed.close_price,
                                "pnl_pct": round(closed.pnl_pct or 0, 4),
                                "close_reason": closed.close_reason or "DIVERGENCE",
                            })
                            logger.info(
                                f"[WS] DIVERGENCE EXIT: score={_break_score:.2f} @ {current_price:.6f} "
                                f"PnL={closed.pnl_pct:+.2f}%"
                            )
                            await websocket.send_json({
                                "type": "position_update",
                                "data": closed.to_dict(),
                            })
                            # Skip check_price() this candle — already closed
                            continue

                # ─── Check SL/TP hit ──────────────────────────
                if executor.is_in_position:
                    closed = executor.check_price(current_price)
                    if closed:
                        # v0.57.0 (TAREA 19): Remove from anti-overlap when position closes
                        _ACTIVE_SYMBOLS.pop(symbol, None)
                        _OPEN_POSITIONS.pop(symbol, None)
                        logger.info(
                            f"[WS] CLOSED: {closed.status} @ {closed.close_price:.6f} "
                            f"PnL={closed.pnl_pct:+.2f}% (${closed.pnl_usdt:+.2f})"
                        )

                        # v0.58.0: TAREA 20 — Update session tracker on close
                        _session_key = f"{symbol}:{timeframe}"
                        if _session_key in _LIVE_SESSIONS:
                            _LIVE_SESSIONS[_session_key]["status"] = closed.status
                            _LIVE_SESSIONS[_session_key]["pnl_pct"] = f"{closed.pnl_pct:+.2f}%"
                            logger.info(f"[WS] Session tracker: closed {_session_key} → {closed.status}")

                        # v0.60.0 (TERMINAL-v2.1): Append to trade history
                        _TRADE_HISTORY.append({
                            "timestamp": int(time.time() * 1000),
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "direction": closed.direction,
                            "entry_price": closed.entry_price,
                            "exit_price": closed.close_price,
                            "pnl_pct": round(closed.pnl_pct or 0, 4),
                            "close_reason": closed.close_reason or closed.status,
                        })

                        # v0.58.0: TAREA 21 — Determine log tag + emit learning feed
                        _close_tag = "LEARN"
                        if "DIVERGENCE" in (closed.close_reason or ""):
                            _close_tag = "PATTERN BROKEN"
                        elif "SL" in (closed.close_reason or "") or "CATASTROPHIC" in (closed.close_reason or ""):
                            _close_tag = "LEARN"
                        elif "TP" in (closed.close_reason or ""):
                            _close_tag = "LEARN"
                        _won = closed.pnl_pct is not None and closed.pnl_pct > 0
                        await _emit_log(
                            websocket, _close_tag,
                            f"{'Won' if _won else 'Lost'}: {closed.status} @ {closed.close_price:.6f} PnL={closed.pnl_pct:+.2f}% pattern=closed",
                            "info" if _won else "warn"
                        )
                        await websocket.send_json({
                            "type": "position_update",
                            "data": closed.to_dict(),
                        })

            except WebSocketDisconnect:
                logger.info(f"[WS] Client disconnected: {symbol}/{timeframe}")
                break
            except Exception as e:
                logger.error(f"[WS] Poll loop error: {e}", exc_info=True)
                try:
                    await websocket.send_json({"type": "error", "data": {"message": str(e)}})
                except Exception:
                    break

            await asyncio.sleep(poll_interval)

    except WebSocketDisconnect:
        pass
    finally:
        # v0.57.0 (TAREA 19): Clean up anti-overlap on disconnect
        if symbol in _ACTIVE_SYMBOLS and _ACTIVE_SYMBOLS.get(symbol) == timeframe:
            _ACTIVE_SYMBOLS.pop(symbol, None)
        try:
            await exchange.close()
        except Exception:
            pass
        # v0.58.0: TAREA 20 — Clean up session tracker on disconnect
        _session_key = f"{symbol}:{timeframe}"
        _LIVE_SESSIONS.pop(_session_key, None)
        # v2.1: Clean up backtest WS registration
        _BACKTEST_WS.pop(_session_key, None)
        logger.info(f"[WS] Session closed: {symbol}/{timeframe}")


# ─── WebSocket: Live Trading (MEXC Futures) ───────────────────
# v0.45.0: ENTREGABLE 6 — Encrypted credentials + Walk-Forward Loop

# Helper: normalize a SAX symbol (str, list, or tuple) to a flat string
# for safe comparison across trie levels (N1/N2 produce str, N3/N4 produce tuple).
def _norm_sax(sym) -> str:
    """Normalize a SAX symbol to a flat string for comparison.
    
    'a'       → 'a'
    ['d','x'] → 'd'   (price dimension only)
    ('a','y') → 'a'
    """
    if isinstance(sym, (tuple, list)):
        return str(sym[0])  # price symbol only
    return str(sym)


# Helper: check if any IExecutor has an active position
def _executor_in_position(executor: IExecutor) -> bool:
    """Check if executor has an active (open) position — works for all IExecutor impls."""
    if isinstance(executor, PaperExecutor):
        return executor.is_in_position
    # MexcFuturesExecutor and future impls store current position
    pos = getattr(executor, '_position', None) or getattr(executor, 'position', None)
    return pos is not None and pos.status in ("ACTIVE", "BREAK_EVEN_SECURED", "TP_EXTENDED")


def _executor_position(executor: IExecutor) -> Optional[PositionState]:
    """Get current position from any IExecutor — works for all impls."""
    if isinstance(executor, PaperExecutor):
        return executor.position
    return getattr(executor, '_position', None) or getattr(executor, 'position', None)


@app.websocket("/ws/live-trading/{symbol}/{timeframe}")
async def live_trading_websocket(websocket: WebSocket, symbol: str, timeframe: str):
    """
    Real-money trading WebSocket via MEXC Futures.

    v0.45.0: ENTREGABLE 6
    - Credentials are Fernet-encrypted in transit.
    - Full Walk-Forward Loop monitors open positions on every candle.
    - Divergence → close. Match → advance SL/TP along expected sequence.

    Auth flow:
      1. Frontend encrypts api_key + api_secret with Fernet using a key
         derived from the user's session password (PBKDF2-SHA256).
      2. Frontend sends: {"type":"auth", "api_key":"<enc>", "api_secret":"<enc>",
         "session_password_hash":"<sha256_hex>"}
      3. Backend decrypts, instantiates MexcFuturesExecutor, zeroes plaintext.
    """

    await websocket.accept()

    # ─── 1. Instantiate executor ────────────────────────────────
    # v0.50.0: ENTREGABLE 11 — If PPMT_MOCK_LIVE=1, use PaperExecutor
    # instead of MexcFuturesExecutor to test the frontend without real money.
    # Injected signals: entry at 10s, MATCH at 20s, DIVERGE at 30s.
    allocated_usdt: float = 100.0
    custom_base_url: str = ""

    if MOCK_LIVE:
        # ── MOCK MODE: Skip auth entirely, use PaperExecutor ──
        logger.info("[WS-LIVE-MOCK] Using PaperExecutor (Mock Live Test mode)")
        executor: IExecutor = PaperExecutor(capital_usdt=allocated_usdt)
        executor._position = None  # v0.49.0 (FASE 3 BUG 2): Force clean state

        # Still send auth_ok so the frontend proceeds to config
        await websocket.send_json({"type": "auth_ok"})

        # Read config message (allocated_usdt + optional custom_base_url)
        try:
            config_raw = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
            if config_raw.get("type") == "config":
                allocated_usdt = float(config_raw.get("allocated_usdt", 100.0))
                if allocated_usdt <= 0:
                    allocated_usdt = 100.0
                custom_base_url = str(config_raw.get("custom_base_url", "")).strip()
                logger.info(f"[WS-LIVE-MOCK] Config: allocated_usdt={allocated_usdt}, custom_base_url={'<set>' if custom_base_url else '<default>'}")
        except asyncio.TimeoutError:
            logger.warning("[WS-LIVE-MOCK] No config message received")

        # Update PaperExecutor capital with the user's allocated amount
        if isinstance(executor, PaperExecutor):
            executor.capital_usdt = allocated_usdt

    else:
        # ── PRODUCTION MODE: Real MEXC Futures ──
        # Wait for encrypted credentials
        try:
            auth_raw = await asyncio.wait_for(websocket.receive_json(), timeout=15.0)
        except asyncio.TimeoutError:
            await websocket.send_json({"type": "error", "data": {"message": "Auth timeout — send encrypted auth within 15s"}})
            await websocket.close()
            return

        if auth_raw.get("type") != "auth":
            await websocket.send_json({"type": "error", "data": {"message": "First message must be {type:auth, ...}"}})
            await websocket.close()
            return

        # Decrypt credentials
        api_key, api_secret = decrypt_auth_payload(auth_raw)

        if api_key is None or api_secret is None:
            await websocket.send_json({"type": "error", "data": {"message": "Decryption failed — wrong session password or tampered payload"}})
            await websocket.close()
            return

        # Defer executor creation until AFTER config (need custom_base_url)
        _api_key = api_key
        _api_secret = api_secret
        del api_key
        del api_secret

        # Read config message (allocated_usdt + custom_base_url)
        try:
            config_raw = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
            if config_raw.get("type") == "config":
                allocated_usdt = float(config_raw.get("allocated_usdt", 100.0))
                if allocated_usdt <= 0:
                    allocated_usdt = 100.0
                custom_base_url = str(config_raw.get("custom_base_url", "")).strip()
                logger.info(f"[WS-LIVE] Config received: allocated_usdt={allocated_usdt}, custom_base_url={'<set>' if custom_base_url else '<default>'}")
        except asyncio.TimeoutError:
            logger.warning("[WS-LIVE] No config message received, defaulting to 100 USDT")

        # Create MexcFuturesExecutor with optional custom_base_url
        executor = MexcFuturesExecutor(
            api_key=_api_key,
            secret=_api_secret,
            base_url=custom_base_url,
        )
        del _api_key
        del _api_secret

        logger.info(f"[WS-LIVE] Auth OK — MexcFuturesExecutor created (base_url={'custom' if custom_base_url else 'default'}, credentials zeroed)")
        await websocket.send_json({"type": "auth_ok"})

    # ─── 2. Same engine init as paper-live ─────────────────────
    internal_symbol = symbol.replace("-", "/")
    api_symbol = internal_symbol.replace("/", "")
    symbol = internal_symbol

    logger.info(f"[WS-LIVE] Connected: {symbol}/{timeframe}")
    storage = get_storage()

    try:
        asset_class = "meme"
        weight_profile = "meme"
        try:
            from ppmt.data.classifier import AssetClassifier
            classifier = AssetClassifier()
            info = classifier.classify(symbol)
            asset_class = info.asset_class
            weight_profile = info.weight_profile
        except Exception as e:
            logger.warning(f"[WS-LIVE] AssetClassifier unavailable ({e}), defaulting to 'meme'")

        engine = PPMT(
            symbol=symbol, asset_class=asset_class,
            weight_profile=weight_profile, dual_sax=True,
            min_confidence=0.08, timeframe=timeframe,
        )

        tries = storage.load_all_tries(symbol, asset_class, timeframe=timeframe)
        n1, n2, n3, n4 = tries.get("n1"), tries.get("n2"), tries.get("n3"), tries.get("n4")
        n1c = n1.pattern_count if n1 else 0
        n2c = n2.pattern_count if n2 else 0
        n3c = n3.pattern_count if n3 else 0
        n4c = n4.pattern_count if hasattr(n4, 'pattern_count') and n4 else 0
        logger.info(f"[WS-LIVE] Loaded N1: {n1c} N2: {n2c} N3: {n3c} N4: {n4c}")

        if n1 or n2 or n3:
            from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
            # v0.49.0 (FASE 3 BUG 3): Same fix as paper_live_websocket.
            _n1 = n1 if n1 is not None else None
            _n2 = n2 if n2 is not None else None
            if _n1 is None:
                _n1 = PPMTTrie(name="universal_empty")
                logger.warning(f"[WS-LIVE] N1 universal pool not found for {symbol}. Using empty trie.")
            if _n2 is None:
                _n2 = PPMTTrie(name=f"class_empty:{asset_class}")
                logger.warning(f"[WS-LIVE] N2 class pool not found for {asset_class}. Using empty trie.")
            engine.set_tries(
                trie_n1=_n1,
                trie_n2=_n2,
                trie_n3=n3 or PPMTTrie(name="n3_empty"),
                trie_n4=n4 if n4 is not None else engine.trie_n4,
            )
    except Exception as e:
        logger.error(f"[WS-LIVE] Engine init failed: {e}")
        await websocket.send_json({"type": "error", "data": {"message": f"Engine init failed: {e}"}})
        await websocket.close()
        return

    # ─── 3. Exchange poller + warmup (same as paper) ───────────
    from ppmt.engine.realtime import _DirectPollExchange
    exchange = _DirectPollExchange("binance")

    try:
        ohlcv_raw = await exchange.fetch_ohlcv(api_symbol, timeframe, limit=500)
        if ohlcv_raw:
            df = pd.DataFrame(ohlcv_raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            for i in range(len(df) - 1):
                engine.process_new_candle(candle_df=df.iloc[[i]], current_price=float(df["close"].iloc[i]))
            for i in range(max(0, len(df) - 50), len(df)):
                r = df.iloc[i]
                await websocket.send_json({"type": "candle", "data": {"time": int(df.index[i].timestamp()) if isinstance(df.index[i], pd.Timestamp) else int(df.index[i]), "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"])}})
    except Exception as e:
        logger.warning(f"[WS-LIVE] Warmup failed: {e}")

    tf_seconds = {"1m": 5, "5m": 10, "15m": 15, "1h": 30}
    poll_interval = tf_seconds.get(timeframe, 5)
    last_candle_ts = 0
    heartbeat_counter = 0  # ticks every loop iteration; used to throttle heartbeat

    # v2.1 FIX: Sticky N3/N4 confidence for live path (same as paper)
    _sticky_n3_conf_live = 0.0
    _sticky_n4_conf_live = 0.0
    _sticky_direction_live = "FLAT"
    _sticky_direction_score_live = 0.0

    # ── v2.1 FIX: Regime detection for N4 (same as paper path) ──
    from ppmt.core.regime import RegimeDetector
    _regime_detector_live = RegimeDetector()
    _regime_window_live: list[dict] = []
    _REGIME_WINDOW_SIZE = 10
    _last_regime_live = "ranging"

    # ─── v0.50.0: ENTREGABLE 11 — Mock Live Test state ────────
    # The mock injection runs as a separate asyncio task so it fires
    # independently of the candle poll loop (which may skip iterations
    # when the same candle timestamp repeats on 1m+ timeframes).
    mock_task: asyncio.Task | None = None
    mock_t0 = time.monotonic()

    async def _mock_inject_signals():
        """Inject fake signals at 10s (entry), 20s (MATCH→BE), 30s (DIVERGE→close)."""
        nonlocal mock_t0
        mock_injected_entry = False
        mock_injected_match = False
        mock_injected_diverge = False

        while True:
            await asyncio.sleep(1)  # check every second
            elapsed = time.monotonic() - mock_t0

            # +10s: Inject ENTRY signal
            if not mock_injected_entry and elapsed >= 10.0:
                _ip = _executor_in_position(executor)
                if not _ip:
                    mock_injected_entry = True
                    # Use latest known price from exchange, or a fallback
                    mock_entry_price = 0.08350  # fallback DOGE price
                    # Try to get the real current price from the last candle
                    if last_candle_ts > 0:
                        pass  # We already have current_price from the poll loop

                    try:
                        pos = await executor.open_position(
                            symbol=symbol,
                            direction="LONG",
                            size_usdt=allocated_usdt,
                            metadata={
                                "entry_price": mock_entry_price,
                                "expected_move_pct": 1.5,
                                "predicted_path_symbols": ["a", "b", "c"],
                            },
                        )
                        logger.info(
                            f"[WS-LIVE-MOCK] INJECTED ENTRY @ {mock_entry_price:.6f} "
                            f"SL={pos.current_sl:.6f} TP={pos.current_tp:.6f}"
                        )
                        await websocket.send_json({"type": "position_update", "data": pos.to_dict()})
                    except Exception as e:
                        logger.error(f"[WS-LIVE-MOCK] Failed to inject entry: {e}")

            # +20s: Inject MATCH → SL moves to break-even
            if mock_injected_entry and not mock_injected_match and elapsed >= 20.0:
                _ip = _executor_in_position(executor)
                if _ip:
                    mock_injected_match = True
                    pos = _executor_position(executor)
                    if pos and pos.status == "ACTIVE":
                        new_sl = pos.entry_price
                        await executor.update_position(pos, new_sl=new_sl, new_tp=None)
                        pos.status = "BREAK_EVEN_SECURED"
                        pos.sequence_index = 1
                        logger.info(
                            f"[WS-LIVE-MOCK] INJECTED MATCH → SL→BE "
                            f"SL={new_sl:.6f} status=BREAK_EVEN_SECURED"
                        )
                        await websocket.send_json({"type": "position_update", "data": pos.to_dict()})

            # +30s: Inject DIVERGE → position closes
            if mock_injected_match and not mock_injected_diverge and elapsed >= 30.0:
                _ip = _executor_in_position(executor)
                if _ip:
                    mock_injected_diverge = True
                    pos = _executor_position(executor)
                    if pos and pos.status in ("ACTIVE", "BREAK_EVEN_SECURED", "TP_EXTENDED"):
                        close_price = pos.current_tp  # close at TP for a positive PnL
                        logger.warning(
                            f"[WS-LIVE-MOCK] INJECTED DIVERGENCE → CLOSING @ {close_price:.6f}"
                        )
                        closed = await executor.close_position(pos, "CLOSED_BY_DIVERGENCE")
                        await websocket.send_json({"type": "position_update", "data": closed.to_dict()})

            # Stop after all injections
            if mock_injected_diverge:
                logger.info("[WS-LIVE-MOCK] All mock signals injected, task complete")
                break

    if MOCK_LIVE:
        mock_task = asyncio.create_task(_mock_inject_signals())
        logger.info("[WS-LIVE-MOCK] Mock signal injection task started (10s/20s/30s)")

    # ─── 4. Main poll loop — routed execution + Walk-Forward ───
    # v2.1 FIX: Same real-time price separation as paper-live path.
    _last_engine_ts_live = 0
    _last_ui_update_live = 0.0
    _ticker_price_live = 0.0  # v2.1: Real-time price from /api/v3/ticker/price
    try:
        while True:
            try:
                ohlcv_raw = await exchange.fetch_ohlcv(api_symbol, timeframe, limit=2)
                if not ohlcv_raw:
                    await asyncio.sleep(poll_interval)
                    continue

                # ─── Fetch REAL-TIME price from Binance ticker ───
                try:
                    ticker = await exchange.fetch_ticker(api_symbol)
                    _ticker_price_live = float(ticker.get("last", 0))
                except Exception as _te:
                    logger.debug(f"[WS-LIVE] Ticker fetch failed (using kline close): {_te}")
                    _ticker_price_live = 0.0

                latest = ohlcv_raw[-1]
                ts_ms, o, h, l, c, v = latest
                ts_sec = int(ts_ms / 1000)
                kline_close = float(c)
                # Use ticker price for display/engine if available, fallback to kline close
                current_price = _ticker_price_live if _ticker_price_live > 0 else kline_close

                # ─── Diagnostic logging ─────────────────────────
                logger.info(
                    f"[WS-LIVE] Binance fetch: kline_ts={ts_sec} kline_C={kline_close:.6f} "
                    f"ticker_C={_ticker_price_live:.6f} delta={abs(_ticker_price_live - kline_close):.6f} "
                    f"({symbol})"
                )

                # Always send current price to UI (forming candle updates)
                # ticker_price is the real-time price for display; chart uses OHLC.
                await websocket.send_json({"type": "candle", "data": {"time": ts_sec, "open": float(o), "high": float(h), "low": float(l), "close": float(c), "ticker_price": round(_ticker_price_live, 8) if _ticker_price_live > 0 else None}})
                _last_ui_update_live = time.monotonic()

                # Only feed closed candles to engine
                result = None
                _in_pos = _executor_in_position(executor)
                _entry = _executor_position(executor).entry_price if _in_pos else None
                if ts_sec > _last_engine_ts_live:
                    _last_engine_ts_live = ts_sec

                    # ── v2.1 FIX: Update regime for N4 ────────────
                    _regime_window_live.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
                    if len(_regime_window_live) > _REGIME_WINDOW_SIZE:
                        _regime_window_live = _regime_window_live[-_REGIME_WINDOW_SIZE:]
                    if len(_regime_window_live) >= 2:
                        try:
                            _rw_df = pd.DataFrame(_regime_window_live)
                            _detected_regime = _regime_detector_live.detect_simple(_rw_df, timeframe=timeframe)
                            if _detected_regime != _last_regime_live:
                                logger.info(f"[WS-LIVE] Regime changed: {_last_regime_live} → {_detected_regime} ({symbol})")
                                _last_regime_live = _detected_regime
                            engine.set_regime(_detected_regime)
                        except Exception as _re:
                            logger.debug(f"[WS-LIVE] Regime detection failed: {_re}")

                    candle_df = pd.DataFrame(
                        {"open": [o], "high": [h], "low": [l], "close": [c], "volume": [v]},
                        index=pd.DatetimeIndex([datetime.fromtimestamp(ts_sec, tz=timezone.utc)]),
                    )

                    result = engine.process_new_candle(
                        candle_df=candle_df, current_price=current_price,
                        is_in_position=_in_pos, entry_price=_entry,
                    )

                # ─── SAX output log (ENTREGABLE 13 FIX) ───────
                _sax_buf = getattr(engine, '_streaming_buffer', None)
                _sax_n1_buf = getattr(engine, '_streaming_buffer_n1', None)
                _buf_len = len(_sax_buf._pattern_buffer) if _sax_buf else 0
                _buf_n1_len = len(_sax_n1_buf._pattern_buffer) if _sax_n1_buf else 0
                _sax_sym = str(_sax_buf._pattern_buffer[-1]) if _sax_buf and _sax_buf._pattern_buffer else "—"
                logger.info(
                    f"[WS-LIVE] SAX output: [{_sax_sym}] buf_n3={_buf_len} buf_n1={_buf_n1_len}"
                    f" result={'YES' if result else 'no'} C={current_price:.6f}"
                )

                # brain_update (identical logic — now includes N3/N4/direction)
                current_sax, active_path_ids = [], ["root"]
                n1_conf, n2_conf = 0.0, 0.0
                n3_conf = _sticky_n3_conf_live  # v2.1 FIX: Start with sticky values
                n4_conf = _sticky_n4_conf_live  # v2.1 FIX: Start with sticky values
                weighted_conf, signal_type = 0.0, "NO_SIGNAL"
                direction = _sticky_direction_live  # v2.1 FIX: Sticky
                direction_score = _sticky_direction_score_live  # v2.1 FIX: Sticky

                if result is not None:
                    if result.sax_symbols:
                        last_sym = result.sax_symbols[-1]
                        current_sax = [str(s) for s in last_sym] if isinstance(last_sym, (tuple, list)) else [str(last_sym)]
                    best_match, best_level = None, None
                    for level, match in [("n3", result.n3_match), ("n2", result.n2_match), ("n4", result.n4_match), ("n1", result.n1_match)]:
                        if match and match.node:
                            conf = match.node.metadata.confidence if match.node.metadata else 0.0
                            if best_match is None or conf > (best_match.node.metadata.confidence if best_match.node and best_match.node.metadata else 0.0):
                                best_match, best_level = match, level
                    if best_match and best_match.node:
                        active_path_ids = _build_active_path_ids(best_match.node.get_backward_path())
                    n1_conf, n2_conf = result.n1_confidence, result.n2_confidence
                    weighted_conf = result.weighted_confidence
                    signal_type = result.signal.signal_type.value if result.signal else "NO_SIGNAL"
                    # v2.1 FIX: Only update n3/n4 from full match if > 0
                    if result.n3_confidence > 0:
                        n3_conf = result.n3_confidence
                        _sticky_n3_conf_live = n3_conf
                    if result.n4_confidence > 0:
                        n4_conf = result.n4_confidence
                        _sticky_n4_conf_live = n4_conf
                    # v2.1 FIX: Only update direction sticky when non-FLAT
                    if hasattr(result, 'direction') and result.direction and result.direction != "FLAT":
                        direction = result.direction
                        _sticky_direction_live = direction
                    if hasattr(result, 'direction_score') and result.direction_score != 0:
                        direction_score = result.direction_score
                        _sticky_direction_score_live = direction_score
                else:
                    buf = getattr(engine, '_streaming_buffer', None)
                    if buf and buf._pattern_buffer:
                        last_sym = buf._pattern_buffer[-1]
                        current_sax = [str(s) for s in last_sym] if isinstance(last_sym, (tuple, list)) else [str(last_sym)]
                    if buf and buf.has_pattern():
                        try:
                            qr = engine.match(current_symbols=buf.get_pattern(), current_price=current_price, is_in_position=_in_pos, entry_price=_entry)
                            if qr:
                                n1_conf, n2_conf = qr.n1_confidence, qr.n2_confidence
                                # v2.1 FIX: Only update N3/N4 if quick match found them
                                if qr.n3_confidence > 0:
                                    n3_conf = qr.n3_confidence
                                    _sticky_n3_conf_live = n3_conf
                                if qr.n4_confidence > 0:
                                    n4_conf = qr.n4_confidence
                                    _sticky_n4_conf_live = n4_conf
                                weighted_conf = qr.weighted_confidence
                                if qr.direction and qr.direction != "FLAT":
                                    direction = qr.direction
                                    _sticky_direction_live = direction
                                if qr.direction_score != 0:
                                    direction_score = qr.direction_score
                                    _sticky_direction_score_live = direction_score
                                best_match = None
                                for level, match in [("n3", qr.n3_match), ("n2", qr.n2_match), ("n4", qr.n4_match), ("n1", qr.n1_match)]:
                                    if match and match.node:
                                        conf = match.node.metadata.confidence if match.node.metadata else 0.0
                                        if best_match is None or conf > (best_match.node.metadata.confidence if best_match.node and best_match.node.metadata else 0.0):
                                            best_match, best_level = match, level
                                if best_match and best_match.node:
                                    active_path_ids = _build_active_path_ids(best_match.node.get_backward_path())
                        except Exception:
                            pass

                # Get last known EV score
                _ev_info_live = _LAST_NET_EV.get(symbol, {})

                # v2.1 FIX: Build brain_update using STICKY values for n3/n4
                _brain_n3_l = round(_sticky_n3_conf_live, 4) if _sticky_n3_conf_live > 0 else None
                _brain_n4_l = round(_sticky_n4_conf_live, 4) if _sticky_n4_conf_live > 0 else None
                _brain_dir_l = _sticky_direction_live if _sticky_direction_live != "FLAT" else direction
                _brain_dir_score_l = round(_sticky_direction_score_live, 4) if _sticky_direction_score_live != 0 else round(direction_score, 4)

                await websocket.send_json({
                    "type": "brain_update",
                    "data": {
                        "current_sax_symbol": current_sax,
                        "active_path_ids": active_path_ids,
                        "n1_confidence": round(n1_conf if n1_conf and n1_conf == n1_conf else 0.0, 4),
                        "n2_confidence": round(n2_conf if n2_conf and n2_conf == n2_conf else 0.0, 4),
                        "n3_confidence": _brain_n3_l,  # v2.1 FIX: null when no sticky
                        "n4_confidence": _brain_n4_l,  # v2.1 FIX: null when no sticky
                        "weighted_confidence": round(weighted_conf if weighted_conf and weighted_conf == weighted_conf else 0.0, 4),
                        "signal_type": signal_type,
                        "direction": _brain_dir_l,
                        "direction_score": _brain_dir_score_l,
                        "ev_score": round(_ev_info_live.get("ev_score", 0.0) or 0.0, 3),
                        "ev_passed": _ev_info_live.get("passed", False),
                        "net_rr": round(_ev_info_live.get("net_rr", 0.0) or 0.0, 2),
                        "ticker_price": round(current_price, 8) if current_price > 0 else None,  # v2.1 FIX: Price in brain_update
                    },
                })
                # v2.1 TEMP LOG: Verify brain_update values
                logger.info(
                    f"[BRAIN-JSON-LIVE] {symbol} n1={n1_conf:.3f} n2={n2_conf:.3f} "
                    f"n3={_brain_n3_l} n4={_brain_n4_l} dir={_brain_dir_l} "
                    f"sticky_n3={_sticky_n3_conf_live:.3f} sticky_n4={_sticky_n4_conf_live:.3f} "
                    f"result={'YES' if result else 'no'}"
                )

                # ── ROUTED EXECUTION: paper vs live ────────────
                if result and result.signal and result.signal.is_entry and not _in_pos:
                    sig = result.signal
                    # v2.1 Config F: Apply same EV gate to live path
                    _live_best_node = None
                    for _lvl, _mr in [("n3", result.n3_match), ("n1", result.n1_match),
                                      ("n2", result.n2_match), ("n4", result.n4_match)]:
                        if _mr and _mr.node and _mr.node.metadata.historical_count > 0:
                            _live_best_node = _mr.node
                            break
                    _live_fav = abs(_live_best_node.metadata.max_favorable_pct) if _live_best_node else 0.0
                    _live_dd = abs(_live_best_node.metadata.max_drawdown_pct) if _live_best_node else 0.5
                    if _live_fav < 0.001:
                        _live_fav = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.1
                    if _live_dd < 0.001:
                        _live_dd = 0.5
                    from ppmt.core.profiles import SPREAD_ESTIMATES as _SPREAD_LIVE
                    _live_spread = _SPREAD_LIVE.get(asset_class, 0.050)
                    _live_net_fav = _live_fav - _live_spread
                    _live_ev_passed = True
                    if _live_net_fav <= 0:
                        _live_ev_passed = False
                        logger.info(f"[WS-LIVE] EV GATE REJECTED (spread): {symbol}")
                    else:
                        _live_net_rr = min(_live_net_fav / _live_dd, 3.0)
                        _live_net_ev = sig.confidence * _live_net_rr
                        _LAST_NET_EV[symbol] = {"ev_score": _live_net_ev, "passed": _live_net_ev >= 0.40, "net_rr": _live_net_rr, "conf": sig.confidence}
                        if _live_net_ev < 0.40:
                            _live_ev_passed = False
                            logger.info(f"[WS-LIVE] EV GATE REJECTED (ev): {symbol} EV={_live_net_ev:.3f}")
                    if not _live_ev_passed:
                        continue
                    try:
                        pos = await executor.open_position(
                            symbol=symbol,
                            direction=sig.direction or "LONG",
                            size_usdt=allocated_usdt,
                            metadata={
                                "entry_price": current_price,
                                "expected_move_pct": sig.expected_move_pct or 1.0,
                                "predicted_path_symbols": sig.predicted_path_symbols if sig.predicted_path else None,
                            },
                        )
                        # v2.1 Config F: SL = max(default 1.2×EM, drawdown_pct × 2.0)
                        _SL_MULT_LIVE = 2.0
                        _live_sl_dist = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
                        _live_dd_sl = _live_dd * _SL_MULT_LIVE
                        if _live_dd_sl > _live_sl_dist:
                            _live_extra = _live_dd_sl - _live_sl_dist
                            if pos.direction == "LONG":
                                pos.current_sl -= pos.entry_price * (_live_extra / 100.0)
                                pos.catastrophic_sl -= pos.entry_price * (_live_extra / 100.0)
                            else:
                                pos.current_sl += pos.entry_price * (_live_extra / 100.0)
                                pos.catastrophic_sl += pos.entry_price * (_live_extra / 100.0)
                        logger.info(
                            f"[WS-LIVE] SIGNAL {sig.signal_type.value} @ {current_price:.6f} "
                            f"conf={sig.confidence:.3f} SL={pos.current_sl:.6f} TP={pos.current_tp:.6f}"
                        )

                        # v0.58.0: TAREA 20 — Register position in live session tracker
                        _live_session_key = f"{symbol}:{timeframe}"
                        _LIVE_SESSIONS[_live_session_key] = {
                            "symbol": symbol,
                            "direction": sig.direction or "LONG",
                            "entry_price": current_price,
                            "size_usdt": allocated_usdt,
                            "pnl_pct": "0.0%",
                            "ev_score": round(sig.confidence, 4),
                            "ev_r": round(sig.confidence * min((sig.expected_move_pct or 1.0) / max(abs(current_price - pos.current_sl) / current_price * 100, 0.01), 3.0), 4),
                            "status": "ACTIVE",
                            "timeframe": timeframe,
                        }
                        logger.info(f"[WS-LIVE] Session tracker: registered {_live_session_key}")

                        await websocket.send_json({"type": "position_update", "data": pos.to_dict()})
                    except Exception as e:
                        logger.error(f"[WS-LIVE] Failed to open position: {e}")
                        await websocket.send_json({"type": "error", "data": {"message": str(e)}})

                # ── WALK-FORWARD LOOP ──────────────────────────
                # ENTREGABLE 6: Full position lifecycle monitoring.
                # On every candle while in position:
                #   1. Compare current SAX symbol vs expected_sequence[index]
                #   2. DIVERGE → close position
                #   3. MATCH  → advance index, move SL/TP
                #
                # v0.46.0 FIX: Normalize both sides with _norm_sax() before
                # comparison to prevent false divergences when one side is a
                # string ('a') and the other is a list/tuple (['a','x']).
                if _in_pos and current_sax:
                    pos = _executor_position(executor)
                    if pos and pos.sequence_index < len(pos.expected_sequence):
                        expected = pos.expected_sequence[pos.sequence_index]

                        # Normalize: extract price dimension from both sides
                        cur_norm = _norm_sax(current_sax[0]) if current_sax else ""
                        exp_norm = _norm_sax(expected[0]) if expected else ""

                        if cur_norm == exp_norm:
                            # ── MATCH: Advance sequence ──────────
                            pos.sequence_index += 1
                            idx = pos.sequence_index  # 1-based after increment

                            if idx == 1:
                                # First match → SL to break-even
                                new_sl = pos.entry_price
                                await executor.update_position(pos, new_sl=new_sl, new_tp=None)
                                pos.status = "BREAK_EVEN_SECURED"
                                logger.info(
                                    f"[WS-LIVE] Walk-Forward: MATCH #{idx} → SL→BE "
                                    f"SL={new_sl:.6f} status=BREAK_EVEN_SECURED"
                                )
                            else:
                                # Subsequent match → Walk the Trie, extend TP
                                # Read expected_move from next trie node if available
                                # Default extension: 0.5% per matched step
                                move_pct = 0.5
                                # If the result has node metadata with expected_move, use it
                                if result and result.signal and result.signal.expected_move_pct:
                                    move_pct = result.signal.expected_move_pct
                                extension = pos.entry_price * (move_pct / 100.0)

                                if pos.direction == "LONG":
                                    new_tp = pos.current_tp + extension
                                    if new_tp > pos.current_tp:
                                        await executor.update_position(pos, new_sl=None, new_tp=new_tp)
                                        pos.status = "TP_EXTENDED"
                                else:
                                    new_tp = pos.current_tp - extension
                                    if new_tp < pos.current_tp:
                                        await executor.update_position(pos, new_sl=None, new_tp=new_tp)
                                        pos.status = "TP_EXTENDED"

                                logger.info(
                                    f"[WS-LIVE] Walk-Forward: MATCH #{idx} → TP extended "
                                    f"TP={pos.current_tp:.6f} status={pos.status}"
                                )

                            # Send updated position to frontend (React chart lines move)
                            await websocket.send_json({"type": "position_update", "data": pos.to_dict()})

                        else:
                            # ── DIVERGE: Close position immediately ──
                            logger.warning(
                                f"[WS-LIVE] Walk-Forward: DIVERGENCE at idx={pos.sequence_index} "
                                f"expected={expected} got={current_sax} → CLOSING"
                            )
                            closed = await executor.close_position(pos, "CLOSED_BY_DIVERGENCE")
                            await websocket.send_json({"type": "position_update", "data": closed.to_dict()})

                # ── HEARTBEAT: Check if position is still alive on MEXC ──
                # v0.47.0: ENTREGABLE 8
                # Every 3 iterations (~15-90s depending on timeframe), verify the
                # position still exists on MEXC. If MEXC closed it (SL/TP hit
                # physically), update local state and notify the frontend.
                heartbeat_counter += 1
                if _in_pos and heartbeat_counter % 3 == 0:
                    pos = _executor_position(executor)
                    if pos and isinstance(executor, MexcFuturesExecutor):
                        alive = await executor.check_position_alive(symbol)
                        if alive is None:
                            # MEXC closed the position — determine why
                            close_reason = "CLOSED_BY_SL"  # default assumption
                            if pos.direction == "LONG":
                                if current_price >= pos.current_tp:
                                    close_reason = "CLOSED_BY_TP"
                                elif current_price <= pos.catastrophic_sl:
                                    close_reason = "CLOSED_CATASTROPHIC"
                            else:
                                if current_price <= pos.current_tp:
                                    close_reason = "CLOSED_BY_TP"
                                elif current_price >= pos.catastrophic_sl:
                                    close_reason = "CLOSED_CATASTROPHIC"

                            pos.close_price = current_price
                            pos.close_reason = close_reason
                            pos.status = close_reason
                            if pos.direction == "LONG":
                                pos.pnl_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100.0
                            else:
                                pos.pnl_pct = ((pos.entry_price - current_price) / pos.entry_price) * 100.0
                            pos.pnl_usdt = pos.size_usdt * (pos.pnl_pct / 100.0)

                            # Remove from executor's internal tracking
                            executor._positions.pop(symbol, None)

                            logger.warning(
                                f"[WS-LIVE] HEARTBEAT: MEXC closed position! "
                                f"reason={close_reason} @ {current_price:.6f} "
                                f"PnL={pos.pnl_pct:+.2f}%"
                            )
                            await websocket.send_json({"type": "position_update", "data": pos.to_dict()})
                        elif isinstance(alive, dict) and alive.get("_error"):
                            logger.debug("[WS-LIVE] Heartbeat API failed — skipping this cycle")

                # ── Check SL/TP hit (price-based) ──────────────
                if _in_pos:
                    pos = _executor_position(executor)
                    if pos and pos.status in ("ACTIVE", "BREAK_EVEN_SECURED", "TP_EXTENDED"):
                        hit = False
                        close_reason = None
                        if pos.direction == "LONG":
                            if current_price <= pos.catastrophic_sl:
                                hit, close_reason = True, "CLOSED_CATASTROPHIC"
                            elif current_price <= pos.current_sl:
                                hit, close_reason = True, "CLOSED_BY_SL"
                            elif current_price >= pos.current_tp:
                                hit, close_reason = True, "CLOSED_BY_TP"
                        else:
                            if current_price >= pos.catastrophic_sl:
                                hit, close_reason = True, "CLOSED_CATASTROPHIC"
                            elif current_price >= pos.current_sl:
                                hit, close_reason = True, "CLOSED_BY_SL"
                            elif current_price <= pos.current_tp:
                                hit, close_reason = True, "CLOSED_BY_TP"

                        if hit and close_reason:
                            closed = await executor.close_position(pos, close_reason)
                            logger.info(
                                f"[WS-LIVE] CLOSED: {closed.status} @ {current_price:.6f} "
                                f"PnL={closed.pnl_pct:+.2f}% (${closed.pnl_usdt:+.2f})"
                            )

                            # v0.58.0: TAREA 20 — Update session tracker on close
                            _live_session_key = f"{symbol}:{timeframe}"
                            if _live_session_key in _LIVE_SESSIONS:
                                _LIVE_SESSIONS[_live_session_key]["status"] = closed.status
                                _LIVE_SESSIONS[_live_session_key]["pnl_pct"] = f"{closed.pnl_pct:+.2f}%"
                                logger.info(f"[WS-LIVE] Session tracker: closed {_live_session_key} → {closed.status}")

                            await websocket.send_json({"type": "position_update", "data": closed.to_dict()})

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"[WS-LIVE] Poll loop error: {e}", exc_info=True)

            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        pass
    finally:
        # Cancel mock injection task if running
        if mock_task and not mock_task.done():
            mock_task.cancel()
            try:
                await mock_task
            except asyncio.CancelledError:
                pass
        try:
            await exchange.close()
        except Exception:
            pass
        if isinstance(executor, MexcFuturesExecutor):
            await executor.close()
        # v0.58.0: TAREA 20 — Clean up session tracker on disconnect
        _live_session_key = f"{symbol}:{timeframe}"
        _LIVE_SESSIONS.pop(_live_session_key, None)
        logger.info(f"[WS-LIVE] Session closed: {symbol}/{timeframe}")


# ─── Static files: serve new terminal UI ─────────────────────
# v0.59.0 (TAREA 22): Mount static/ so index.html is served at /,
# and assets (if any) at /static/. This replaces the old React
# terminal that required npm + Vite on port 5173.

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/")
async def serve_terminal():
    """Serve the PPMT terminal HTML at the root URL."""
    index_path = os.path.join(_STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"error": "Terminal not found — index.html missing"}


# Mount static dir AFTER all API routes so /api/* takes precedence
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ─── Run directly ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
