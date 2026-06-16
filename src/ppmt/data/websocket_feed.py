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


def _mexc_subscribe_msg(symbol: str, timeframe: str) -> dict:
    """Build MEXC spot WebSocket subscription message."""
    interval = MEXC_WS_INTERVALS.get(timeframe, "Min5")
    # MEXC uses lowercase symbol without slash for spot WS
    mexc_symbol = symbol.replace("/", "").lower()
    return {
        "method": "SUBSCRIPTION",
        "params": [f"spot@public.kline.v3.api+{interval}+{mexc_symbol}"],
    }


def _parse_mexc_kline(data: dict, symbol: str, timeframe: str) -> Optional[Candle]:
    """Parse a MEXC spot kline WebSocket message into a Candle."""
    try:
        # MEXC v3 kline format: {"d": {"e": "spot@public.kline.v3.api", "k": {...}}}
        # Or directly: {"k": {...}}
        k = data.get("k", data.get("d", {}).get("k", {}))
        if not k:
            return None

        return Candle(
            timestamp=int(k.get("t", 0)),
            open=float(k.get("o", 0)),
            high=float(k.get("h", 0)),
            low=float(k.get("l", 0)),
            close=float(k.get("c", 0)),
            volume=float(k.get("v", 0)),
            closed=k.get("x", False),
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

        async with websockets.connect(
            url,
            ping_interval=15,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws

            # Subscribe to kline channel
            sub_msg = _mexc_subscribe_msg(self.symbol, self.timeframe)
            await ws.send(json.dumps(sub_msg))
            logger.info(f"Subscribed to MEXC kline: {sub_msg}")

            if self.on_status:
                self.on_status("streaming", f"Streaming {self.symbol} {self.timeframe}")

            self._reconnect_count = 0

            async for raw_msg in ws:
                if not self._running:
                    break

                try:
                    msg = json.loads(raw_msg)

                    # MEXC subscription confirmation
                    if msg.get("method") == "SUBSCRIPTION" or "id" in msg:
                        logger.info(f"MEXC subscription confirmed: {msg}")
                        continue

                    # MEXC ping — respond with pong
                    if msg.get("method") == "ping" or msg.get("ping"):
                        pong = msg.get("id", 0)
                        await ws.send(json.dumps({"method": "pong", "id": pong}))
                        continue

                    # MEXC kline data
                    # MEXC format: {"c": "spot@public.kline.v3.api+Min5+btcusdt", "d": {...}, "s": "BTCUSDT", "t": 1234567890}
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
                    logger.warning("Invalid JSON from MEXC WS")
                except Exception as e:
                    logger.error(f"Error processing MEXC message: {e}")

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
