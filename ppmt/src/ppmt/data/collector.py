"""
PPMT Data Collector - Market Data Fetching

Supports:
  1. Binance API (free, no account needed for historical klines)
  2. CSV import (offline, works without any exchange connection)
  3. ccxt library (optional, for multi-exchange support)

Binance API is used directly (free, public endpoints) to avoid
requiring ccxt or any account. The ccxt dependency is optional
and only needed for non-Binance exchanges.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import pandas as pd

from ppmt.data.storage import PPMTStorage


# Binance API endpoints (free, public, no account needed)
BINANCE_API_BASE = "https://api.binance.com"
BINANCE_KLINES_URL = f"{BINANCE_API_BASE}/api/v3/klines"


class DataCollector:
    """
    OHLCV data collector for PPMT.

    Fetches candle data from Binance (free API) or imports from CSV.
    All data is stored locally via PPMTStorage (SQLite).

    Usage:
        collector = DataCollector(exchange="binance", storage=storage)
        df = collector.fetch_and_save("BTC/USDT", "1h", days=365)
        collector.close()
    """

    def __init__(
        self,
        exchange: str = "binance",
        storage: Optional[PPMTStorage] = None,
    ):
        self.exchange = exchange
        self.storage = storage or PPMTStorage()
        self._ccxt_exchange = None

    def _init_ccxt(self):
        """Initialize ccxt exchange if available (optional)."""
        if self._ccxt_exchange is not None:
            return True

        try:
            import ccxt
            exchange_class = getattr(ccxt, self.exchange, None)
            if exchange_class is None:
                return False
            self._ccxt_exchange = exchange_class({"enableRateLimit": True})
            return True
        except ImportError:
            return False

    def fetch_and_save(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 365,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data from exchange and save to storage.

        Tries Binance API first (free, no account needed).
        Falls back to ccxt if available for other exchanges.

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            timeframe: Candle interval (e.g., '1h', '4h', '1d')
            days: Number of days of history to fetch

        Returns:
            DataFrame with OHLCV data
        """
        if self.exchange == "binance":
            df = self._fetch_binance(symbol, timeframe, days)
        else:
            # Try ccxt for non-Binance exchanges
            if self._init_ccxt():
                df = self._fetch_ccxt(symbol, timeframe, days)
            else:
                raise RuntimeError(
                    f"Cannot fetch from {self.exchange}: ccxt not installed. "
                    f"Install with: pip install 'ppmt[exchange]'"
                )

        if not df.empty and self.storage:
            self.storage.save_ohlcv(symbol, timeframe, df)

        return df

    def _fetch_binance(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 365,
    ) -> pd.DataFrame:
        """
        Fetch historical klines from Binance API (free, public).

        Binance API returns klines as:
        [open_time, open, high, low, close, volume, close_time,
         quote_volume, trades, taker_buy_base, taker_buy_quote, ignore]
        """
        # Convert symbol format: BTC/USDT → BTCUSDT
        binance_symbol = symbol.replace("/", "")

        # Time parameters
        end_time = int(time.time() * 1000)
        start_time = end_time - (days * 24 * 60 * 60 * 1000)

        # Binance timeframe to milliseconds
        tf_ms = self._timeframe_to_ms(timeframe)

        # Fetch in batches (Binance returns max 1000 candles per request)
        all_klines = []
        current_start = start_time

        while current_start < end_time:
            url = (
                f"{BINANCE_KLINES_URL}?"
                f"symbol={binance_symbol}&"
                f"interval={timeframe}&"
                f"startTime={current_start}&"
                f"endTime={end_time}&"
                f"limit=1000"
            )

            try:
                req = Request(url)
                req.add_header("User-Agent", "PPMT/0.1.0")
                with urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode())
            except (HTTPError, URLError) as e:
                if "400" in str(e) or "404" in str(e):
                    # Symbol might not exist on Binance
                    return pd.DataFrame()
                raise RuntimeError(f"Binance API error: {e}") from e

            if not data:
                break

            all_klines.extend(data)

            # Move start time to after the last candle
            last_open_time = data[-1][0]
            current_start = last_open_time + tf_ms

            # Rate limit: be nice to the API
            time.sleep(0.2)

            # If we got less than 1000, we've reached the end
            if len(data) < 1000:
                break

        if not all_klines:
            return pd.DataFrame()

        # Parse klines into DataFrame
        df = pd.DataFrame(
            all_klines,
            columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore",
            ],
        )

        # Keep only needed columns
        df = df[["open_time", "open", "high", "low", "close", "volume"]]

        # Convert types
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)

        # Convert timestamp to DatetimeIndex
        df["timestamp"] = df["open_time"]
        df = df.set_index(pd.to_datetime(df["open_time"], unit="ms"))
        df = df.drop(columns=["open_time"])

        # Remove duplicates
        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()

        return df

    def _fetch_ccxt(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 365,
    ) -> pd.DataFrame:
        """Fetch data using ccxt library (optional, for non-Binance exchanges)."""
        if self._ccxt_exchange is None:
            raise RuntimeError("ccxt exchange not initialized")

        since = self._ccxt_exchange.parse8601(
            (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).isoformat()
        )

        all_ohlcv = []
        while since < self._ccxt_exchange.milliseconds():
            ohlcv = self._ccxt_exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + 1
            time.sleep(self._ccxt_exchange.rateLimit / 1000.0)

        if not all_ohlcv:
            return pd.DataFrame()

        df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.set_index(pd.to_datetime(df["timestamp"], unit="ms"))
        df = df.drop(columns=["timestamp"])
        df = df[~df.index.duplicated(keep="first")]
        return df.sort_index()

    def import_csv(
        self,
        symbol: str,
        timeframe: str = "1h",
        csv_path: str = "",
    ) -> pd.DataFrame:
        """
        Import OHLCV data from a CSV file.

        The CSV must have columns: timestamp (or date), open, high, low, close, volume.
        Timestamp can be unix milliseconds or ISO 8601 format.

        Args:
            symbol: Trading pair to assign this data to
            timeframe: Candle timeframe
            csv_path: Path to the CSV file
        """
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"CSV not found: {csv_path}")

        df = pd.read_csv(csv_path)

        # Standardize column names (case-insensitive)
        df.columns = [c.strip().lower() for c in df.columns]

        # Handle timestamp column
        if "timestamp" in df.columns:
            # Try unix ms first
            try:
                df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
            except (ValueError, OverflowError):
                df["ts"] = pd.to_datetime(df["timestamp"])
        elif "date" in df.columns:
            df["ts"] = pd.to_datetime(df["date"])
        elif "datetime" in df.columns:
            df["ts"] = pd.to_datetime(df["datetime"])
        else:
            # Use first column as timestamp
            df["ts"] = pd.to_datetime(df.iloc[:, 0])

        df = df.set_index("ts")

        # Ensure required columns exist
        required = ["open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                if col == "volume" and "vol" in df.columns:
                    df["volume"] = df["vol"]
                else:
                    raise ValueError(f"CSV missing required column: {col}")

        df = df[required].astype(float)
        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()

        # Save to storage
        if self.storage:
            self.storage.save_ohlcv(symbol, timeframe, df)

        return df

    @staticmethod
    def _timeframe_to_ms(timeframe: str) -> int:
        """Convert Binance timeframe string to milliseconds."""
        units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
        for suffix, ms in units.items():
            if timeframe.endswith(suffix):
                try:
                    value = int(timeframe[:-1])
                    return value * ms
                except ValueError:
                    break
        return 3_600_000  # Default: 1h

    def close(self) -> None:
        """Clean up resources."""
        if self._ccxt_exchange:
            self._ccxt_exchange.close()
            self._ccxt_exchange = None
        if self.storage:
            self.storage.close()
