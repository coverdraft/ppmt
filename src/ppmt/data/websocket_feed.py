"""
WebSocket Market Data Feed - Real-Time Candle Streaming

Provides real-time OHLCV candle data from exchanges via WebSocket,
replacing the slow REST polling approach with true streaming.

Supported Exchanges:
  1. Binance WebSocket (primary) — wss://stream.binance.com:9443
  2. Bybit WebSocket (backup) — wss://stream.bybit.com/v5/public/spot

Architecture:
  WebSocketFeed maintains a persistent connection and fires callbacks
  when a new candle closes. The PPMT engine consumes candles via the
  on_candle callback, maintaining the incremental SAX pipeline.

Usage:
    from ppmt.data.websocket_feed import WebSocketFeed

    feed = WebSocketFeed(
        symbol="BTC/USDT",
        timeframe="1h",
        exchange="binance",
        on_candle=my_candle_handler,
    )
    await feed.start()  # Runs until stopped

v0.12.0: New module — replaces REST polling in RealtimeTrader.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncIterator, Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class ExchangeWS(Enum):
    """Supported WebSocket exchanges."""
    BINANCE = "binance"
    BYBIT = "bybit"
    MEXC = "mexc"


@dataclass
class Candle:
    """A single OHLCV candle from WebSocket feed."""
    timestamp: int          # Open time in milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = False    # True if candle is complete (final tick)
    exchange: str = ""
    symbol: str = ""
    timeframe: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "closed": self.closed,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
        }

    def to_dataframe_row(self) -> pd.DataFrame:
        """Convert to a single-row DataFrame for SAX encoder."""
        df = pd.DataFrame([{
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }], index=pd.DatetimeIndex([pd.Timestamp(self.timestamp, unit="ms", tz="UTC")]))
        return df


# ============================================================
# BINANCE WEBSOCKET
# ============================================================

BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"
BINANCE_WS_TESTNET = "wss://testnet.binance.vision/ws"

# Binance interval mapping for WebSocket streams
BINANCE_WS_INTERVALS = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h",
    "12h": "12h", "1d": "1d", "3d": "3d", "1w": "1w", "1M": "1M",
}


def _binance_stream_name(symbol: str, timeframe: str) -> str:
    """Build Binance stream name: btcusdt@kline_1h"""
    pair = symbol.replace("/", "").lower()
    interval = BINANCE_WS_INTERVALS.get(timeframe, timeframe)
    return f"{pair}@kline_{interval}"


def _parse_binance_kline(data: dict) -> Optional[Candle]:
    """Parse a Binance kline WebSocket message into a Candle."""
    try:
        k = data["k"]
        return Candle(
            timestamp=k["t"],
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            closed=k["x"],  # Is this kline closed?
            exchange="binance",
            symbol=k["s"],  # e.g., "BTCUSDT"
            timeframe=k["i"],  # e.g., "1h"
        )
    except (KeyError, ValueError) as e:
        logger.warning(f"Failed to parse Binance kline: {e}")
        return None


# ============================================================
# BYBIT WEBSOCKET
# ============================================================

BYBIT_WS_PUBLIC = "wss://stream.bybit.com/v5/public/spot"
BYBIT_WS_PUBLIC_LINEAR = "wss://stream.bybit.com/v5/public/linear"

# ============================================================
# MEXC WEBSOCKET
# ============================================================

MEXC_WS_SPOT = "wss://wbs.mexc.com/ws"

MEXC_WS_INTERVALS = {
    "1m": "Min1", "5m": "Min5", "15m": "Min15", "30m": "Min30",
    "1h": "Min60", "4h": "Hour4", "1d": "Day1", "1w": "Week1",
}


def _mexc_subscribe_msg(symbol: str, timeframe: str, msg_id: int = 1) -> dict:
    """Build MEXC spot WebSocket subscription message.

    v0.32.4: MEXC v3 API REQUIRES an `id` field on the SUBSCRIPTION request.
    Without it, MEXC responds with:
      {"id":0,"code":0,"msg":"Not Subscribed successfully! ... Reason: Blocked!"}
    and silently refuses to send any kline data.
    """
    interval = MEXC_WS_INTERVALS.get(timeframe, "Min5")
    mexc_symbol = symbol.replace("/", "").lower()
    return {
        "method": "SUBSCRIPTION",
        "params": [f"spot@public.kline.v3.api+{interval}+{mexc_symbol}"],
        "id": msg_id,
    }


def _parse_mexc_kline(data: dict, symbol: str, timeframe: str) -> Optional[Candle]:
    """Parse a MEXC spot kline WebSocket message into a Candle.

    v0.32.4: MEXC v3 kline messages do NOT include an `x` (is_closed) field.
    The kline object structure is:
        {"t": <start_ms>, "T": <end_ms>, "s": <SYM>, "i": <interval>,
         "o": <open>, "c": <close>, "h": <high>, "l": <low>,
         "v": <volume>, "a": <quote_volume>}

    MEXC sends continuous updates throughout the candle period. To get a
    "closed" candle, we compare the current wall-clock time to k["T"]
    (the candle's scheduled end time). If current_time >= end_time, the
    candle is closed.

    Additionally, the caller (WebSocketFeed._listen_mexc) uses a buffering
    strategy: when a new timestamp arrives, the PREVIOUS buffered candle is
    emitted as closed. This avoids depending on the wall-clock check alone,
    which can be flaky near the candle boundary.
    """
    try:
        k = data.get("k", data.get("d", {}).get("k", {}))
        if not k:
            return None

        ts = int(k.get("t", 0))
        end_ts = int(k.get("T", 0))
        now_ms = int(time.time() * 1000)
        # If MEXC ever DOES include an `x`, trust it. Otherwise infer from T.
        explicit_closed = k.get("x")
        if explicit_closed is not None:
            closed = bool(explicit_closed)
        elif end_ts > 0:
            closed = now_ms >= end_ts
        else:
            closed = False

        return Candle(
            timestamp=ts,
            open=float(k.get("o", 0)),
            high=float(k.get("h", 0)),
            low=float(k.get("l", 0)),
            close=float(k.get("c", 0)),
            volume=float(k.get("v", 0)),
            closed=closed,
            exchange="mexc",
            symbol=symbol,
            timeframe=timeframe,
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(f"Failed to parse MEXC kline: {e}")
        return None

BYBIT_WS_INTERVALS = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W", "1M": "M",
}


def _bybit_subscribe_msg(symbol: str, timeframe: str) -> dict:
    """Build Bybit V5 subscription message."""
    interval = BYBIT_WS_INTERVALS.get(timeframe, timeframe)
    bybit_symbol = symbol.replace("/", "")
    return {
        "op": "subscribe",
        "args": [f"kline.{interval}.{bybit_symbol}"],
    }


def _parse_bybit_kline(data: dict, symbol: str, timeframe: str) -> Optional[Candle]:
    """Parse a Bybit V5 kline WebSocket message into a Candle."""
    try:
        topic = data.get("topic", "")
        if "kline" not in topic:
            return None

        k = data["data"]
        return Candle(
            timestamp=int(k["start"]),
            open=float(k["open"]),
            high=float(k["high"]),
            low=float(k["low"]),
            close=float(k["close"]),
            volume=float(k["volume"]),
            closed=k.get("confirm", False),
            exchange="bybit",
            symbol=symbol,
            timeframe=timeframe,
        )
    except (KeyError, ValueError) as e:
        logger.warning(f"Failed to parse Bybit kline: {e}")
        return None


# ============================================================
# MAIN WEBSOCKET FEED CLASS
# ============================================================

class WebSocketFeed:
    """
    Real-time WebSocket feed for OHLCV candle data.

    Connects to Binance or Bybit WebSocket and fires the on_candle
    callback each time a new candle tick arrives. Only processes
    closed candles (k["x"] == True) to ensure complete OHLCV data.

    Features:
      - Automatic reconnection with exponential backoff
      - Ping/pong keepalive
      - Graceful shutdown via stop()
      - Warm-up: fetch last N closed candles via REST before streaming
      - Works without ccxt (uses raw websockets)

    Usage:
        async def handle_candle(candle: Candle):
            print(f"New candle: {candle.close}")

        feed = WebSocketFeed(
            symbol="BTC/USDT",
            timeframe="1h",
            exchange="binance",
            on_candle=handle_candle,
        )
        await feed.start()
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        exchange: str = "binance",
        on_candle: Optional[Callable] = None,
        on_tick: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
        on_status: Optional[Callable] = None,
        testnet: bool = False,
        warmup_candles: int = 0,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        # v0.21.0: Handle both str and ExchangeWS enum
        if isinstance(exchange, ExchangeWS):
            self.exchange = exchange.value.lower()
        else:
            self.exchange = exchange.lower()
        self.on_candle = on_candle
        self.on_tick = on_tick
        self.on_error = on_error
        self.on_status = on_status
        self.testnet = testnet
        self.warmup_candles = warmup_candles

        self._ws = None
        self._running = False
        self._reconnect_count = 0
        self._max_reconnects = 50
        self._last_candle_ts = 0
        self._candles_received = 0
        self._ticks_received = 0
        self._started_at: Optional[float] = None

    @property
    def stats(self) -> dict:
        """Current feed statistics."""
        uptime = time.time() - self._started_at if self._started_at else 0
        return {
            "running": self._running,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candles_received": self._candles_received,
            "ticks_received": self._ticks_received,
            "reconnects": self._reconnect_count,
            "uptime_seconds": uptime,
        }

    async def start(self) -> None:
        """
        Start the WebSocket feed. Runs until stop() is called.

        Performs warm-up (if configured) then enters the main
        WebSocket loop with automatic reconnection.
        """
        self._running = True
        self._started_at = time.time()

        if self.on_status:
            self.on_status("connecting", f"Connecting to {self.exchange}...")

        # Warm-up: fetch recent candles via REST
        if self.warmup_candles > 0:
            await self._warmup()

        # Main WebSocket loop with reconnection
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                logger.info("WebSocket feed cancelled")
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                if self.on_error:
                    self.on_error(e)

                self._reconnect_count += 1
                if self._reconnect_count >= self._max_reconnects:
                    logger.error(f"Max reconnection attempts ({self._max_reconnects}) reached")
                    if self.on_status:
                        self.on_status("failed", "Max reconnection attempts reached")
                    break

                # Exponential backoff: 2, 4, 8, 16, 32, 60, 60, 60...
                delay = min(2 ** self._reconnect_count, 60)
                if self.on_status:
                    self.on_status("reconnecting",
                                   f"Reconnecting in {delay}s (attempt {self._reconnect_count})")
                await asyncio.sleep(delay)

        if self.on_status:
            self.on_status("stopped", "Feed stopped")

    def stop(self) -> None:
        """Stop the WebSocket feed gracefully."""
        self._running = False
        if self._ws:
            asyncio.create_task(self._close_ws())

    async def _close_ws(self) -> None:
        """Close the WebSocket connection."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _connect_and_listen(self) -> None:
        """Connect to exchange WebSocket and process messages."""
        try:
            import websockets
        except ImportError:
            raise ImportError(
                "websockets is required for live streaming. "
                "Install with: pip install websockets>=12.0"
            )

        if self.exchange == "binance":
            url = await self._binance_url()
            await self._listen_binance(websockets, url)
        elif self.exchange == "bybit":
            await self._listen_bybit(websockets)
        elif self.exchange == "mexc":
            await self._listen_mexc(websockets)
        else:
            # Fallback: try ccxt REST polling for unsupported WS exchanges
            logger.warning(f"WebSocket not supported for {self.exchange}. Use REST polling via ccxt.")
            raise ValueError(
                f"WebSocket not supported for exchange: {self.exchange}. "
                f"Supported: binance, bybit, mexc. "
                f"For other exchanges, use REST polling (ccxt)."
            )

    # ============================================================
    # BINANCE WEBSOCKET
    # ============================================================

    async def _binance_url(self) -> str:
        """Build the Binance WebSocket URL."""
        if self.testnet:
            base = BINANCE_WS_TESTNET
        else:
            base = BINANCE_WS_BASE

        stream = _binance_stream_name(self.symbol, self.timeframe)
        return f"{base}/{stream}"

    async def _listen_binance(self, websockets, url: str) -> None:
        """Connect and listen to Binance kline WebSocket."""
        logger.info(f"Connecting to Binance WS: {url}")

        if self.on_status:
            self.on_status("connected", f"Connected to Binance ({self.symbol})")

        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._reconnect_count = 0  # Reset on successful connection

            if self.on_status:
                self.on_status("streaming", f"Streaming {self.symbol} {self.timeframe}")

            async for raw_msg in ws:
                if not self._running:
                    break

                try:
                    msg = json.loads(raw_msg)

                    # Binance kline message
                    if "k" in msg:
                        candle = _parse_binance_kline(msg)
                        if candle is None:
                            continue

                        self._ticks_received += 1

                        # Fire tick callback (every update)
                        if self.on_tick:
                            try:
                                self.on_tick(candle)
                            except Exception:
                                pass

                        # Only process closed candles
                        if candle.closed and candle.timestamp != self._last_candle_ts:
                            self._last_candle_ts = candle.timestamp
                            self._candles_received += 1

                            if self.on_candle:
                                try:
                                    result = self.on_candle(candle)
                                    if asyncio.iscoroutine(result):
                                        await result
                                except Exception as e:
                                    logger.error(f"on_candle callback error: {e}")

                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from Binance WS")
                except Exception as e:
                    logger.error(f"Error processing Binance message: {e}")

    # ============================================================
    # BYBIT WEBSOCKET
    # ============================================================

    async def _listen_bybit(self, websockets) -> None:
        """Connect and listen to Bybit V5 kline WebSocket."""
        # Determine public endpoint (spot vs linear)
        symbol_upper = self.symbol.upper()
        if "USDT" in symbol_upper or "PERP" in symbol_upper:
            url = BYBIT_WS_PUBLIC_LINEAR
        else:
            url = BYBIT_WS_PUBLIC

        logger.info(f"Connecting to Bybit WS: {url}")

        if self.on_status:
            self.on_status("connected", f"Connecting to Bybit ({self.symbol})")

        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws

            # Subscribe to kline channel
            sub_msg = _bybit_subscribe_msg(self.symbol, self.timeframe)
            await ws.send(json.dumps(sub_msg))
            logger.info(f"Subscribed to Bybit kline: {sub_msg}")

            if self.on_status:
                self.on_status("streaming", f"Streaming {self.symbol} {self.timeframe}")

            self._reconnect_count = 0

            async for raw_msg in ws:
                if not self._running:
                    break

                try:
                    msg = json.loads(raw_msg)

                    # Bybit subscription confirmation
                    if "op" in msg and msg["op"] == "subscribe":
                        logger.info(f"Bybit subscription confirmed: {msg}")
                        continue

                    # Bybit ping
                    if "op" in msg and msg["op"] == "ping":
                        await ws.send(json.dumps({"op": "pong"}))
                        continue

                    # Bybit kline data
                    if "topic" in msg and "kline" in msg.get("topic", ""):
                        candle = _parse_bybit_kline(msg, self.symbol, self.timeframe)
                        if candle is None:
                            continue

                        self._ticks_received += 1

                        if self.on_tick:
                            try:
                                self.on_tick(candle)
                            except Exception:
                                pass

                        if candle.closed and candle.timestamp != self._last_candle_ts:
                            self._last_candle_ts = candle.timestamp
                            self._candles_received += 1

                            if self.on_candle:
                                try:
                                    result = self.on_candle(candle)
                                    if asyncio.iscoroutine(result):
                                        await result
                                except Exception as e:
                                    logger.error(f"on_candle callback error: {e}")

                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from Bybit WS")
                except Exception as e:
                    logger.error(f"Error processing Bybit message: {e}")

    # ============================================================
    # MEXC WEBSOCKET (v0.20.0)
    # ============================================================

    async def _listen_mexc(self, websockets) -> None:
        """Connect and listen to MEXC spot kline WebSocket."""
        url = MEXC_WS_SPOT
        logger.info(f"Connecting to MEXC WS: {url}")

        if self.on_status:
            self.on_status("connected", f"Connecting to MEXC ({self.symbol})")

        # v0.32.4: Disable websockets protocol-level pings — MEXC uses
        # APPLICATION-level pings ({"method": "ping"}). If both are enabled,
        # websockets expects a pong control frame that MEXC never sends, and
        # closes the connection after ping_timeout seconds. This was the #1
        # cause of "Connecting to MEXC..." repeating every ~30s in the
        # terminal: the WS dropped, the feed reconnected, warmup did NOT
        # re-run (it's once-only in start()), and the cycle repeated.
        async with websockets.connect(
            url,
            ping_interval=None,        # v0.32.4: MEXC uses app-level pings
            ping_timeout=None,
            close_timeout=5,
        ) as ws:
            self._ws = ws

            # Subscribe to kline channel (v0.32.4: must include `id`)
            sub_msg = _mexc_subscribe_msg(self.symbol, self.timeframe, msg_id=1)
            await ws.send(json.dumps(sub_msg))
            logger.info(f"Subscribed to MEXC kline: {sub_msg}")

            if self.on_status:
                self.on_status("streaming", f"Streaming {self.symbol} {self.timeframe}")

            self._reconnect_count = 0

            # v0.32.4: Background task to send client-side application pings
            # every 10 seconds. MEXC REQUIRES the client to initiate pings;
            # if no ping is sent within ~30s, the server closes the connection.
            # Server responds with {"id":0,"code":0,"msg":"PONG"}.
            async def _mexc_ping_loop():
                ping_id = 1000
                while self._running:
                    try:
                        await ws.send(json.dumps({"method": "ping", "id": ping_id}))
                        ping_id += 1
                    except Exception as e:
                        logger.debug(f"MEXC ping send error: {e}")
                        return
                    await asyncio.sleep(10)

            ping_task = asyncio.create_task(_mexc_ping_loop())

            # v0.32.4: Buffered candle strategy.
            # MEXC v3 kline messages do NOT include a reliable "is_closed"
            # flag, so we cannot rely on `candle.closed` alone. Instead, we
            # buffer the latest kline for the current timestamp, and when a
            # new timestamp arrives we emit the PREVIOUS buffered candle as
            # a fully-closed candle. This guarantees:
            #   1. Each candle period fires on_candle exactly once.
            #   2. The emitted candle has the FINAL ohlcv values for that
            #      period (because MEXC sends continuous updates).
            #   3. We don't miss candles or fire duplicates.
            buffered_candle: Optional[Candle] = None

            try:
                async for raw_msg in ws:
                    if not self._running:
                        break

                    try:
                        msg = json.loads(raw_msg)

                        # v0.32.4: MEXC subscription confirmation.
                        # Format: {"id": <sub_id>, "code": 0, "msg": "..."}
                        # A failed subscription also has this shape but
                        # msg contains "Not Subscribed successfully".
                        if "code" in msg and "msg" in msg and "d" not in msg and "k" not in msg:
                            if "Not Subscribed" in str(msg.get("msg", "")):
                                logger.error(
                                    f"MEXC subscription REJECTED: {msg}. "
                                    f"Check symbol/timeframe/exchange reachability."
                                )
                                if self.on_error:
                                    self.on_error(
                                        RuntimeError(f"MEXC subscription rejected: {msg.get('msg')}")
                                    )
                            else:
                                logger.info(f"MEXC subscription confirmed: {msg}")
                            continue

                        # v0.32.4: PONG response to our client ping.
                        # Format: {"id": <pong_id>, "code": 0, "msg": "PONG"}
                        # Note: MEXC returns id=0 regardless of what we sent.
                        if msg.get("msg") == "PONG":
                            continue

                        # v0.32.4: Server-initiated ping (rare in MEXC v3,
                        # but handle it for robustness). Respond with pong.
                        if msg.get("method") == "ping":
                            try:
                                await ws.send(json.dumps({"method": "pong"}))
                            except Exception:
                                pass
                            continue

                        # MEXC subscription response echo (no body)
                        if msg.get("method") == "SUBSCRIPTION":
                            continue

                        # MEXC kline data
                        # Format: {"c": "spot@public.kline.v3.api+Min60+ethusdt",
                        #          "d": {"e": "...", "k": {...}}},
                        #          "s": "ETHUSDT", "t": 1234567890}
                        if "d" in msg or "k" in msg:
                            candle = _parse_mexc_kline(msg, self.symbol, self.timeframe)
                            if candle is None:
                                continue

                            self._ticks_received += 1

                            if self.on_tick:
                                try:
                                    self.on_tick(candle)
                                except Exception:
                                    pass

                            # v0.32.4: Buffered candle approach.
                            if buffered_candle is None:
                                # First ever kline — just buffer it.
                                buffered_candle = candle
                            elif candle.timestamp != buffered_candle.timestamp:
                                # New candle period → previous one is closed.
                                # Emit the buffered (previous) candle.
                                prev = buffered_candle
                                # Mark as closed — we have its final values.
                                prev.closed = True
                                self._last_candle_ts = prev.timestamp
                                self._candles_received += 1

                                if self.on_candle:
                                    try:
                                        result = self.on_candle(prev)
                                        if asyncio.iscoroutine(result):
                                            await result
                                    except Exception as e:
                                        logger.error(f"on_candle callback error: {e}")

                                # Buffer the new (just-started) candle.
                                buffered_candle = candle
                            else:
                                # Same candle period — update buffer with
                                # the latest ohlcv values (especially close,
                                # high, low, volume which keep changing).
                                buffered_candle = candle

                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON from MEXC WS")
                    except Exception as e:
                        logger.error(f"Error processing MEXC message: {e}")
            finally:
                # v0.32.4: Cancel the ping task when we exit the loop.
                ping_task.cancel()
                try:
                    await ping_task
                except (asyncio.CancelledError, Exception):
                    pass

                # Flush the final buffered candle so the last period isn't lost.
                if buffered_candle is not None and self.on_candle is not None:
                    buffered_candle.closed = True
                    if buffered_candle.timestamp != self._last_candle_ts:
                        self._last_candle_ts = buffered_candle.timestamp
                        self._candles_received += 1
                        try:
                            result = self.on_candle(buffered_candle)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.error(f"on_candle flush error: {e}")

    # ============================================================
    # WARMUP: Fetch recent candles via REST before streaming
    # ============================================================

    async def _warmup(self) -> None:
        """
        Fetch recent closed candles via REST API before starting WebSocket.

        This ensures the SAX encoder has enough data to produce at least
        one symbol before the first live candle arrives.
        """
        if self.on_status:
            self.on_status("warming_up",
                           f"Fetching {self.warmup_candles} warmup candles...")

        try:
            from ppmt.data.collector import DataCollector
            from ppmt.data.storage import PPMTStorage

            storage = PPMTStorage()
            collector = DataCollector(exchange=self.exchange, storage=storage)

            # Fetch recent data
            days = self._candles_to_days(self.warmup_candles)
            df = collector.fetch_and_save(self.symbol, self.timeframe, days=days)

            if not df.empty and self.on_candle:
                # Feed the last N candles to the callback
                warmup_df = df.tail(self.warmup_candles)
                for idx, row in warmup_df.iterrows():
                    ts_ms = int(idx.timestamp() * 1000) if hasattr(idx, 'timestamp') else 0
                    candle = Candle(
                        timestamp=ts_ms,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        closed=True,
                        exchange=self.exchange,
                        symbol=self.symbol,
                        timeframe=self.timeframe,
                    )
                    try:
                        result = self.on_candle(candle)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"Warmup candle callback error: {e}")

                self._candles_received += len(warmup_df)
                logger.info(f"Warmup complete: {len(warmup_df)} candles fed")

            collector.close()
            storage.close()

        except Exception as e:
            logger.warning(f"Warmup failed (non-critical): {e}")

    def _candles_to_days(self, n_candles: int) -> int:
        """Convert number of candles to approximate days."""
        candles_per_day = {
            "1m": 1440, "3m": 480, "5m": 288, "15m": 96, "30m": 48,
            "1h": 24, "2h": 12, "4h": 6, "6h": 4, "8h": 3,
            "12h": 2, "1d": 1, "1w": 1 / 7, "1M": 1 / 30,
        }
        cpd = candles_per_day.get(self.timeframe, 24)
        return max(int(n_candles / cpd) + 1, 1)


# ============================================================
# CONVENIENCE: Async Iterator
# ============================================================

class CandleStream:
    """
    Async iterator wrapper for WebSocketFeed.

    Usage:
        stream = CandleStream("BTC/USDT", "1h", "binance")
        async for candle in stream:
            print(f"Close: {candle.close}")
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        exchange: str = "binance",
        warmup_candles: int = 0,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.exchange = exchange
        self.warmup_candles = warmup_candles
        self._queue: asyncio.Queue = asyncio.Queue()
        self._feed: Optional[WebSocketFeed] = None

    def __aiter__(self):
        return self

    async def __anext__(self) -> Candle:
        candle = await self._queue.get()
        if candle is None:
            raise StopAsyncIteration
        return candle

    async def start(self) -> None:
        """Start the feed and begin producing candles."""
        self._feed = WebSocketFeed(
            symbol=self.symbol,
            timeframe=self.timeframe,
            exchange=self.exchange,
            on_candle=self._on_candle,
            warmup_candles=self.warmup_candles,
        )
        # Run feed in background
        asyncio.create_task(self._feed.start())

    async def _on_candle(self, candle: Candle) -> None:
        await self._queue.put(candle)

    def stop(self) -> None:
        if self._feed:
            self._feed.stop()
        # Signal end of stream
        asyncio.create_task(self._queue.put(None))
