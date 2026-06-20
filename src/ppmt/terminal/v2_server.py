"""
PPMT V2 Terminal — FastAPI WebSocket Bridge.

This server provides the real-time bridge between the PPMT Python engine
and the React terminal frontend. It exposes a single WebSocket endpoint
per token that streams candle, brain, and position updates.

v0.45.0: ENTREGABLE 6 — Credential encryption (Fernet) + Walk-Forward Loop.

Usage:
    uvicorn ppmt.terminal.v2_server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ppmt.data.storage import PPMTStorage
from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.terminal.paper_executor import PaperExecutor
from ppmt.execution.models import PositionState
from ppmt.execution.interfaces import IExecutor
from ppmt.execution.mexc_futures import MexcFuturesExecutor
from ppmt.execution.crypto import decrypt_auth_payload

logger = logging.getLogger("ppmt.v2")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

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
        tries = storage.load_all_tries(symbol, asset_class)
        
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
            engine.set_tries(
                trie_n1=n1 or engine.trie_n1,
                trie_n2=n2 or engine.trie_n2,
                trie_n3=n3 or engine.trie_n3,
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
    executor: IExecutor = PaperExecutor(capital_usdt=100.0)

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
                        "time": int(df.index[i].timestamp()),
                        "open": float(r["open"]),
                        "high": float(r["high"]),
                        "low": float(r["low"]),
                        "close": float(r["close"]),
                    },
                })

            logger.info(f"[WS] Warmup complete: {len(df)} candles, {warmup_count} SAX outputs")
    except Exception as e:
        logger.warning(f"[WS] Warmup fetch failed: {e}. Continuing without historical data.")

    # ─── 5. Polling interval ─────────────────────────────────
    tf_seconds = {"1m": 5, "5m": 10, "15m": 15, "1h": 30}
    poll_interval = tf_seconds.get(timeframe, 5)

    last_candle_ts = 0

    # ─── 6. Main poll loop ────────────────────────────────────
    try:
        while True:
            try:
                # Fetch latest candle
                ohlcv_raw = await exchange.fetch_ohlcv(api_symbol, timeframe, limit=2)

                if not ohlcv_raw:
                    await asyncio.sleep(poll_interval)
                    continue

                # Get the most recent closed candle
                latest = ohlcv_raw[-1]
                ts_ms, o, h, l, c, v = latest
                ts_sec = int(ts_ms / 1000)

                # Skip if we've already processed this candle
                if ts_sec <= last_candle_ts:
                    await asyncio.sleep(poll_interval)
                    continue

                last_candle_ts = ts_sec
                current_price = float(c)

                # ─── Emit candle to frontend ──────────────────
                candle_msg = {
                    "type": "candle",
                    "data": {
                        "time": ts_sec,
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                    },
                }
                await websocket.send_json(candle_msg)
                logger.info(
                    f"[WS] Candle: ts={ts_sec} C={c:.6f}"
                )

                # ─── Feed to PPMT engine ──────────────────────
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
                weighted_conf = 0.0
                signal_type = "NO_SIGNAL"
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
                            f"conf={best_match.node.metadata.confidence:.3f if best_match.node.metadata else 0.0}"
                        )

                    n1_conf = result.n1_confidence
                    n2_conf = result.n2_confidence
                    weighted_conf = result.weighted_confidence
                    signal_type = result.signal.signal_type.value if result.signal else "NO_SIGNAL"
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
                            if quick_result:
                                n1_conf = quick_result.n1_confidence
                                n2_conf = quick_result.n2_confidence
                                weighted_conf = quick_result.weighted_confidence
                                
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
                                        f"n1={n1_conf:.3f} n2={n2_conf:.3f} wconf={weighted_conf:.3f}"
                                    )
                        except Exception as e:
                            logger.debug(f"[WS] Quick match failed: {e}")
                    else:
                        logger.debug(f"[WS] No pattern in buffer yet (buf={buf is not None}, symbols={len(buf._pattern_buffer) if buf else 0})")

                brain_msg = {
                    "type": "brain_update",
                    "data": {
                        "current_sax_symbol": current_sax,
                        "active_path_ids": active_path_ids,
                        "n1_confidence": round(n1_conf, 4),
                        "n2_confidence": round(n2_conf, 4),
                        "weighted_confidence": round(weighted_conf, 4),
                        "signal_type": signal_type,
                    },
                }
                await websocket.send_json(brain_msg)

                # ─── Signal → Routed execution ────────────────
                # PAPER: await executor.open_position() → in-memory
                # LIVE:  await executor.open_position() → MEXC API
                # Same IExecutor interface, different backend.
                if result and result.signal and result.signal.is_entry and not (isinstance(executor, PaperExecutor) and executor.is_in_position):
                    sig = result.signal
                    try:
                        pos = await executor.open_position(
                            symbol=symbol,
                            direction=sig.direction or "LONG",
                            size_usdt=100.0,
                            metadata={
                                "entry_price": current_price,
                                "expected_move_pct": sig.expected_move_pct or 1.0,
                                "predicted_path_symbols": sig.predicted_path_symbols if sig.predicted_path else None,
                            },
                        )
                        logger.info(
                            f"[WS] SIGNAL {sig.signal_type.value} @ {current_price:.6f} "
                            f"conf={sig.confidence:.3f} SL={pos.current_sl:.6f} TP={pos.current_tp:.6f}"
                        )
                        await websocket.send_json({
                            "type": "position_update",
                            "data": pos.to_dict(),
                        })
                    except Exception as e:
                        logger.error(f"[WS] Failed to open position: {e}")

                # ─── Walk-Forward check ───────────────────────
                if result and executor.is_in_position and current_sax:
                    updated = executor.check_walk_forward(current_sax, current_price)
                    if updated:
                        logger.info(
                            f"[WS] Walk-Forward: seq_idx={updated.sequence_index} "
                            f"status={updated.status} SL={updated.current_sl:.6f} TP={updated.current_tp:.6f}"
                        )
                        await websocket.send_json({
                            "type": "position_update",
                            "data": updated.to_dict(),
                        })

                # ─── Check SL/TP hit ──────────────────────────
                if executor.is_in_position:
                    closed = executor.check_price(current_price)
                    if closed:
                        logger.info(
                            f"[WS] CLOSED: {closed.status} @ {closed.close_price:.6f} "
                            f"PnL={closed.pnl_pct:+.2f}% (${closed.pnl_usdt:+.2f})"
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
        try:
            await exchange.close()
        except Exception:
            pass
        logger.info(f"[WS] Session closed: {symbol}/{timeframe}")


# ─── WebSocket: Live Trading (MEXC Futures) ───────────────────
# v0.45.0: ENTREGABLE 6 — Encrypted credentials + Walk-Forward Loop

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

    # ─── 0. Auth: wait for ENCRYPTED credentials ──────────────
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

    # ─── Decrypt credentials with Fernet ──────────────────────
    api_key, api_secret = decrypt_auth_payload(auth_raw)

    if api_key is None or api_secret is None:
        await websocket.send_json({"type": "error", "data": {"message": "Decryption failed — wrong session password or tampered payload"}})
        await websocket.close()
        return

    # ─── 1. Instantiate MexcFuturesExecutor with decrypted keys ─
    executor: IExecutor = MexcFuturesExecutor(
        api_key=api_key,
        secret=api_secret,
    )

    # Zero plaintext from local scope immediately
    del api_key
    del api_secret

    logger.info("[WS-LIVE] Auth OK — MexcFuturesExecutor created (credentials zeroed)")
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

        tries = storage.load_all_tries(symbol, asset_class)
        n1, n2, n3, n4 = tries.get("n1"), tries.get("n2"), tries.get("n3"), tries.get("n4")
        n1c = n1.pattern_count if n1 else 0
        n2c = n2.pattern_count if n2 else 0
        n3c = n3.pattern_count if n3 else 0
        n4c = n4.pattern_count if hasattr(n4, 'pattern_count') and n4 else 0
        logger.info(f"[WS-LIVE] Loaded N1: {n1c} N2: {n2c} N3: {n3c} N4: {n4c}")

        if n1 or n2 or n3:
            from ppmt.core.trie import RegimePartitionedTrie
            engine.set_tries(
                trie_n1=n1 or engine.trie_n1,
                trie_n2=n2 or engine.trie_n2,
                trie_n3=n3 or engine.trie_n3,
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
                await websocket.send_json({"type": "candle", "data": {"time": int(df.index[i].timestamp()), "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"])}})
    except Exception as e:
        logger.warning(f"[WS-LIVE] Warmup failed: {e}")

    tf_seconds = {"1m": 5, "5m": 10, "15m": 15, "1h": 30}
    poll_interval = tf_seconds.get(timeframe, 5)
    last_candle_ts = 0

    # ─── 4. Main poll loop — routed execution + Walk-Forward ───
    try:
        while True:
            try:
                ohlcv_raw = await exchange.fetch_ohlcv(api_symbol, timeframe, limit=2)
                if not ohlcv_raw:
                    await asyncio.sleep(poll_interval)
                    continue

                latest = ohlcv_raw[-1]
                ts_ms, o, h, l, c, v = latest
                ts_sec = int(ts_ms / 1000)
                if ts_sec <= last_candle_ts:
                    await asyncio.sleep(poll_interval)
                    continue
                last_candle_ts = ts_sec
                current_price = float(c)

                await websocket.send_json({"type": "candle", "data": {"time": ts_sec, "open": float(o), "high": float(h), "low": float(l), "close": float(c)}})

                candle_df = pd.DataFrame(
                    {"open": [o], "high": [h], "low": [l], "close": [c], "volume": [v]},
                    index=pd.DatetimeIndex([datetime.fromtimestamp(ts_sec, tz=timezone.utc)]),
                )

                # Position state — works for any IExecutor
                _in_pos = _executor_in_position(executor)
                _entry = _executor_position(executor).entry_price if _in_pos else None

                result = engine.process_new_candle(
                    candle_df=candle_df, current_price=current_price,
                    is_in_position=_in_pos, entry_price=_entry,
                )

                # brain_update (identical logic)
                current_sax, active_path_ids = [], ["root"]
                n1_conf, n2_conf, weighted_conf, signal_type = 0.0, 0.0, 0.0, "NO_SIGNAL"

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
                    n1_conf, n2_conf, weighted_conf = result.n1_confidence, result.n2_confidence, result.weighted_confidence
                    signal_type = result.signal.signal_type.value if result.signal else "NO_SIGNAL"
                else:
                    buf = getattr(engine, '_streaming_buffer', None)
                    if buf and buf._pattern_buffer:
                        last_sym = buf._pattern_buffer[-1]
                        current_sax = [str(s) for s in last_sym] if isinstance(last_sym, (tuple, list)) else [str(last_sym)]
                    if buf and buf.has_pattern():
                        try:
                            qr = engine.match(current_symbols=buf.get_pattern(), current_price=current_price, is_in_position=_in_pos, entry_price=_entry)
                            if qr:
                                n1_conf, n2_conf, weighted_conf = qr.n1_confidence, qr.n2_confidence, qr.weighted_confidence
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

                await websocket.send_json({"type": "brain_update", "data": {"current_sax_symbol": current_sax, "active_path_ids": active_path_ids, "n1_confidence": round(n1_conf, 4), "n2_confidence": round(n2_conf, 4), "weighted_confidence": round(weighted_conf, 4), "signal_type": signal_type}})

                # ── ROUTED EXECUTION: paper vs live ────────────
                if result and result.signal and result.signal.is_entry and not _in_pos:
                    sig = result.signal
                    try:
                        pos = await executor.open_position(
                            symbol=symbol,
                            direction=sig.direction or "LONG",
                            size_usdt=100.0,
                            metadata={
                                "entry_price": current_price,
                                "expected_move_pct": sig.expected_move_pct or 1.0,
                                "predicted_path_symbols": sig.predicted_path_symbols if sig.predicted_path else None,
                            },
                        )
                        logger.info(
                            f"[WS-LIVE] SIGNAL {sig.signal_type.value} @ {current_price:.6f} "
                            f"conf={sig.confidence:.3f} SL={pos.current_sl:.6f} TP={pos.current_tp:.6f}"
                        )
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
                if _in_pos and current_sax:
                    pos = _executor_position(executor)
                    if pos and pos.sequence_index < len(pos.expected_sequence):
                        expected = pos.expected_sequence[pos.sequence_index]

                        if current_sax == expected or (len(current_sax) == 1 and len(expected) == 1 and current_sax[0] == expected[0]):
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
                            await websocket.send_json({"type": "position_update", "data": closed.to_dict()})

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"[WS-LIVE] Poll loop error: {e}", exc_info=True)

            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await exchange.close()
        except Exception:
            pass
        if isinstance(executor, MexcFuturesExecutor):
            await executor.close()
        logger.info(f"[WS-LIVE] Session closed: {symbol}/{timeframe}")


# ─── Run directly ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
