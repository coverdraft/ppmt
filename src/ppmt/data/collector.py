"""
PPMT Data Collector - Market Data Fetching

Supports:
  1. Bybit API (free, no account needed — PRIMARY since Binance geo-blocked)
  2. Binance API (free, no account needed — may be geo-blocked 418)
  3. OKX API (free, no account needed — backup)
  4. Kraken API (free, no account needed — backup)
  5. ccxt library (optional, for any other exchange)
  6. CSV import (offline, works without any exchange connection)

Automatic fallback chain: primary → OKX → Kraken → Binance → ccxt
If the primary exchange fails (geo-block, rate-limit, etc.), the system
automatically tries the next available source.

All exchanges are free public APIs — no account or API key needed.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import pandas as pd

from ppmt.data.storage import PPMTStorage

logger = logging.getLogger(__name__)

# ============================================================
# API Endpoints (all free, public, no account needed)
# ============================================================
BINANCE_API_BASE = "https://api.binance.com"
BINANCE_KLINES_URL = f"{BINANCE_API_BASE}/api/v3/klines"

BYBIT_API_BASE = "https://api.bybit.com"
BYBIT_KLINES_URL = f"{BYBIT_API_BASE}/v5/market/kline"

OKX_API_BASE = "https://www.okx.com"
OKX_CANDLES_URL = f"{OKX_API_BASE}/api/v5/market/candles"

KRAKEN_API_BASE = "https://api.kraken.com"
KRAKEN_OHLC_URL = f"{KRAKEN_API_BASE}/0/public/OHLC"

# Fallback chain: try these exchanges in order if primary fails
DEFAULT_FALLBACK_CHAIN = ["bybit", "okx", "kraken", "binance"]

# Rate limits (seconds between requests)
EXCHANGE_RATE_LIMITS = {
    "bybit": 0.15,
    "okx": 0.20,
    "kraken": 1.0,  # Kraken is stricter
    "binance": 0.20,
}

# ============================================================
# Timeframe mapping per exchange
# ============================================================
BYBIT_INTERVALS = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W", "1M": "M",
}

OKX_INTERVALS = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H",
    "1d": "1D", "1w": "1W", "1M": "1M",
}

KRAKEN_INTERVALS = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
}


class DataCollector:
    """
    OHLCV data collector for PPMT with multi-exchange support.

    Supports Bybit (primary), Binance, OKX, Kraken as direct API sources,
    plus ccxt for any other exchange, and CSV import.

    Features:
      - Automatic fallback chain if primary exchange fails
      - Paginated historical data fetching
      - Local caching via PPMTStorage (SQLite)

    Usage:
        collector = DataCollector(exchange="bybit", storage=storage)
        df = collector.fetch_and_save("BTC/USDT", "1h", days=365)
        collector.close()
    """

    def __init__(
        self,
        exchange: str = "bybit",
        storage: Optional[PPMTStorage] = None,
        fallback_chain: Optional[List[str]] = None,
    ):
        self.exchange = exchange
        self.storage = storage or PPMTStorage()
        self._ccxt_exchange = None
        self.fallback_chain = fallback_chain or DEFAULT_FALLBACK_CHAIN

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

        Tries the configured exchange first, then falls back through
        the fallback chain (Bybit → OKX → Kraken → Binance → ccxt).

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            timeframe: Candle interval (e.g., '1h', '4h', '1d')
            days: Number of days of history to fetch

        Returns:
            DataFrame with OHLCV data
        """
        # Build the list of exchanges to try
        exchanges_to_try = [self.exchange]
        for ex in self.fallback_chain:
            if ex != self.exchange:
                exchanges_to_try.append(ex)

        last_error = None
        for exchange in exchanges_to_try:
            try:
                df = self._fetch_from_exchange(exchange, symbol, timeframe, days)
                if not df.empty:
                    if self.storage:
                        self.storage.save_ohlcv(symbol, timeframe, df)
                    return df
            except Exception as e:
                last_error = e
                logger.warning(f"  {exchange} failed for {symbol}: {e}")
                continue

        # Last resort: try ccxt with the original exchange
        if self._init_ccxt():
            try:
                df = self._fetch_ccxt(symbol, timeframe, days)
                if not df.empty:
                    if self.storage:
                        self.storage.save_ohlcv(symbol, timeframe, df)
                    return df
            except Exception as e:
                last_error = e
                logger.warning(f"  ccxt/{self.exchange} failed for {symbol}: {e}")

        if last_error:
            raise RuntimeError(
                f"All exchanges failed for {symbol}/{timeframe}. "
                f"Last error: {last_error}"
            ) from last_error

        return pd.DataFrame()

    def _fetch_from_exchange(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        days: int,
    ) -> pd.DataFrame:
        """Route to the appropriate fetch method based on exchange."""
        if exchange == "bybit":
            return self._fetch_bybit(symbol, timeframe, days)
        elif exchange == "okx":
            return self._fetch_okx(symbol, timeframe, days)
        elif exchange == "kraken":
            return self._fetch_kraken(symbol, timeframe, days)
        elif exchange == "binance":
            return self._fetch_binance(symbol, timeframe, days)
        else:
            raise ValueError(f"Unknown exchange: {exchange}")

    # ================================================================
    # BYBIT V5 API (Primary — free, public, no account needed)
    # ================================================================
    def _fetch_bybit(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 365,
    ) -> pd.DataFrame:
        """
        Fetch historical klines from Bybit V5 API (free, public).

        Bybit V5 kline format: [startTime, open, high, low, close, volume, turnover]
        Returns candles in REVERSE chronological order (newest first).
        Max 200 candles per request.
        """
        bybit_symbol = symbol.replace("/", "")
        interval = BYBIT_INTERVALS.get(timeframe)
        if interval is None:
            raise ValueError(f"Bybit: unsupported timeframe '{timeframe}'")

        tf_ms = self._timeframe_to_ms(timeframe)
        end_ts = int(time.time() * 1000)
        start_ts = end_ts - (days * 24 * 60 * 60 * 1000)

        all_klines = []
        current_end = end_ts
        rate_limit = EXCHANGE_RATE_LIMITS["bybit"]

        while current_end > start_ts:
            url = (
                f"{BYBIT_KLINES_URL}?"
                f"category=spot&"
                f"symbol={bybit_symbol}&"
                f"interval={interval}&"
                f"start={start_ts}&"
                f"end={current_end}&"
                f"limit=200"
            )

            try:
                req = Request(url)
                req.add_header("User-Agent", "PPMT/0.6.6")
                with urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode())
            except (HTTPError, URLError) as e:
                if "400" in str(e) or "404" in str(e):
                    return pd.DataFrame()
                raise RuntimeError(f"Bybit API error: {e}") from e

            ret_code = data.get("retCode", -1)
            if ret_code != 0:
                ret_msg = data.get("retMsg", "unknown")
                if ret_code == 10001 or "invalid symbol" in ret_msg.lower():
                    return pd.DataFrame()
                raise RuntimeError(f"Bybit API error: {ret_msg} (code={ret_code})")

            candles = data.get("result", {}).get("list", [])
            if not candles:
                break

            all_klines.extend(candles)

            # Bybit returns newest first; oldest candle is last in list
            oldest_ts = int(candles[-1][0])
            current_end = oldest_ts - 1  # Move end before oldest candle

            time.sleep(rate_limit)

            # If we got less than 200, we've reached the beginning
            if len(candles) < 200:
                break

        if not all_klines:
            return pd.DataFrame()

        # Parse: [startTime, open, high, low, close, volume, turnover]
        df = pd.DataFrame(all_klines, columns=[
            "open_time", "open", "high", "low", "close", "volume", "turnover"
        ])

        # Keep needed columns only
        df = df[["open_time", "open", "high", "low", "close", "volume"]]

        # Convert types
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        # Set datetime index
        df = df.set_index(pd.to_datetime(df["open_time"].astype(int), unit="ms"))
        df = df.drop(columns=["open_time"])

        # Dedup and sort (Bybit returns newest first, we need chronological)
        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()

        return df

    # ================================================================
    # OKX V5 API (Backup — free, public, no account needed)
    # ================================================================
    def _fetch_okx(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 365,
    ) -> pd.DataFrame:
        """
        Fetch historical candles from OKX V5 API (free, public).

        OKX candle format: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        Returns candles in REVERSE chronological order (newest first).
        Max 100 candles per request.
        """
        # OKX instrument format: BTC-USDT (dash instead of slash)
        okx_inst = symbol.replace("/", "-")
        bar = OKX_INTERVALS.get(timeframe)
        if bar is None:
            raise ValueError(f"OKX: unsupported timeframe '{timeframe}'")

        tf_ms = self._timeframe_to_ms(timeframe)
        end_ts = int(time.time() * 1000)
        start_ts = end_ts - (days * 24 * 60 * 60 * 1000)

        all_klines = []
        current_end = end_ts
        rate_limit = EXCHANGE_RATE_LIMITS["okx"]

        while current_end > start_ts:
            url = (
                f"{OKX_CANDLES_URL}?"
                f"instId={okx_inst}&"
                f"bar={bar}&"
                f"after={current_end}&"
                f"limit=100"
            )

            try:
                req = Request(url)
                req.add_header("User-Agent", "PPMT/0.6.6")
                with urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode())
            except (HTTPError, URLError) as e:
                if "400" in str(e) or "404" in str(e):
                    return pd.DataFrame()
                raise RuntimeError(f"OKX API error: {e}") from e

            code = data.get("code", "-1")
            if code != "0":
                msg = data.get("msg", "unknown")
                if "Invalid" in msg or "instrument" in msg.lower():
                    return pd.DataFrame()
                raise RuntimeError(f"OKX API error: {msg} (code={code})")

            candles = data.get("data", [])
            if not candles:
                break

            all_klines.extend(candles)

            # OKX returns newest first; oldest is last
            oldest_ts = int(candles[-1][0])
            current_end = oldest_ts - 1

            time.sleep(rate_limit)

            if len(candles) < 100:
                break

        if not all_klines:
            return pd.DataFrame()

        # Parse: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        df = pd.DataFrame(all_klines, columns=[
            "open_time", "open", "high", "low", "close",
            "volume", "vol_ccy", "vol_ccy_quote", "confirm"
        ])

        df = df[["open_time", "open", "high", "low", "close", "volume"]]

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        df = df.set_index(pd.to_datetime(df["open_time"].astype(int), unit="ms"))
        df = df.drop(columns=["open_time"])

        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()

        return df

    # ================================================================
    # KRAKEN API (Backup — free, public, no account needed)
    # ================================================================
    def _fetch_kraken(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 365,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLC from Kraken API (free, public).

        Kraken returns: {pair_name: [[ts, o, h, l, c, vwap, volume, count], ...], last: ts}
        Candles are in chronological order.
        Max 720 candles per request for most pairs.
        Note: Kraken uses XBT instead of BTC.
        """
        # Kraken symbol format: XBTUSDT (XBT not BTC), no slash
        kraken_symbol = symbol.replace("/", "").replace("BTC", "XBT")
        interval = KRAKEN_INTERVALS.get(timeframe)
        if interval is None:
            raise ValueError(f"Kraken: unsupported timeframe '{timeframe}'")

        tf_ms = self._timeframe_to_ms(timeframe)
        now = int(time.time())
        since = now - (days * 24 * 60 * 60)

        all_klines = []
        current_since = since
        rate_limit = EXCHANGE_RATE_LIMITS["kraken"]

        while current_since < now:
            url = (
                f"{KRAKEN_OHLC_URL}?"
                f"pair={kraken_symbol}&"
                f"interval={interval}&"
                f"since={current_since}"
            )

            try:
                req = Request(url)
                req.add_header("User-Agent", "PPMT/0.6.6")
                with urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode())
            except (HTTPError, URLError) as e:
                if "400" in str(e) or "404" in str(e):
                    return pd.DataFrame()
                raise RuntimeError(f"Kraken API error: {e}") from e

            errors = data.get("error", [])
            if errors:
                if any("EQuery:Unknown asset pair" in e for e in errors):
                    return pd.DataFrame()
                raise RuntimeError(f"Kraken API error: {errors}")

            result = data.get("result", {})
            last_ts = result.get("last", 0)
            pair_key = [k for k in result.keys() if k != "last"]
            if not pair_key:
                break

            candles = result[pair_key[0]]
            if not candles:
                break

            all_klines.extend(candles)

            # Move forward using the 'last' cursor
            if last_ts <= current_since:
                break
            current_since = last_ts

            time.sleep(rate_limit)

            # If fewer than expected candles, we've reached current time
            if len(candles) < 720:
                break

        if not all_klines:
            return pd.DataFrame()

        # Kraken format: [ts, o, h, l, c, vwap, volume, count]
        df = pd.DataFrame(all_klines, columns=[
            "open_time", "open", "high", "low", "close",
            "vwap", "volume", "count"
        ])

        df = df[["open_time", "open", "high", "low", "close", "volume"]]

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        # Kraken open_time is in seconds, convert to ms for consistency
        df["open_time_ms"] = df["open_time"].astype(int) * 1000
        df = df.set_index(pd.to_datetime(df["open_time_ms"], unit="ms"))
        df = df.drop(columns=["open_time", "open_time_ms"])

        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()

        return df

    # ================================================================
    # BINANCE API (Original — may be geo-blocked 418)
    # ================================================================
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
        binance_symbol = symbol.replace("/", "")

        end_time = int(time.time() * 1000)
        start_time = end_time - (days * 24 * 60 * 60 * 1000)
        tf_ms = self._timeframe_to_ms(timeframe)

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
                req.add_header("User-Agent", "PPMT/0.6.6")
                with urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode())
            except (HTTPError, URLError) as e:
                if "400" in str(e) or "404" in str(e) or "418" in str(e):
                    return pd.DataFrame()
                raise RuntimeError(f"Binance API error: {e}") from e

            if not data:
                break

            all_klines.extend(data)
            last_open_time = data[-1][0]
            current_start = last_open_time + tf_ms

            time.sleep(EXCHANGE_RATE_LIMITS["binance"])

            if len(data) < 1000:
                break

        if not all_klines:
            return pd.DataFrame()

        df = pd.DataFrame(
            all_klines,
            columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore",
            ],
        )

        df = df[["open_time", "open", "high", "low", "close", "volume"]]

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        df["timestamp"] = df["open_time"]
        df = df.set_index(pd.to_datetime(df["open_time"], unit="ms"))
        df = df.drop(columns=["open_time"])

        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()

        return df

    # ================================================================
    # CCXT (Universal fallback — requires ccxt package)
    # ================================================================
    def _fetch_ccxt(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 365,
    ) -> pd.DataFrame:
        """Fetch data using ccxt library (optional, for any exchange)."""
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

    # ================================================================
    # CSV Import (offline)
    # ================================================================
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
        """
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"CSV not found: {csv_path}")

        df = pd.read_csv(csv_path)
        df.columns = [c.strip().lower() for c in df.columns]

        if "timestamp" in df.columns:
            try:
                df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
            except (ValueError, OverflowError):
                df["ts"] = pd.to_datetime(df["timestamp"])
        elif "date" in df.columns:
            df["ts"] = pd.to_datetime(df["date"])
        elif "datetime" in df.columns:
            df["ts"] = pd.to_datetime(df["datetime"])
        else:
            df["ts"] = pd.to_datetime(df.iloc[:, 0])

        df = df.set_index("ts")

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

        if self.storage:
            self.storage.save_ohlcv(symbol, timeframe, df)

        return df

    # ================================================================
    # Utilities
    # ================================================================
    @staticmethod
    def _timeframe_to_ms(timeframe: str) -> int:
        """Convert timeframe string to milliseconds."""
        units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
        for suffix, ms in units.items():
            if timeframe.endswith(suffix):
                try:
                    value = int(timeframe[:-1])
                    return value * ms
                except ValueError:
                    break
        return 3_600_000  # Default: 1h

    # ================================================================
    # MARKET DISCOVERY (required by ppmt scan)
    # ================================================================

    def get_markets(self) -> list[str]:
        """
        Get list of available trading pairs from the exchange.

        Uses ccxt to fetch market info. Returns a list of symbol strings
        in CCXT format (e.g., 'BTC/USDT', 'ETH/USDT').

        v0.21.0: Filters out derivatives (symbols with ':') to return
        only spot pairs, which are what PPMT uses for pattern matching.
        """
        if not self._init_ccxt():
            raise RuntimeError(
                f"ccxt is required for market scanning. "
                f"Install with: pip install ccxt>=4.0.0"
            )

        try:
            markets = self._ccxt_exchange.load_markets()
            # v0.21.0: Filter out derivatives (symbols with ':SETTLE' suffix)
            # and only return spot pairs
            spot_symbols = [s for s in markets.keys() if ':' not in s]
            logger.info(f"Loaded {len(spot_symbols)} spot markets from {self.exchange} (total: {len(markets)})")
            return spot_symbols
        except Exception as e:
            raise RuntimeError(f"Failed to load markets from {self.exchange}: {e}") from e

    def get_tickers(self, symbols: list[str]) -> dict:
        """
        Get 24h ticker data for a list of symbols.

        Returns a dict mapping symbol → ticker dict with keys:
          quoteVolume, percentage, high, low, last, bid, ask, etc.

        v0.21.0: Fixed to convert ccxt Ticker objects to plain dicts.
        """
        if not self._init_ccxt():
            raise RuntimeError(
                f"ccxt is required for ticker data. "
                f"Install with: pip install ccxt>=4.0.0"
            )

        def _ticker_to_dict(t):
            """Convert ccxt Ticker object to plain dict."""
            if isinstance(t, dict):
                return t
            # ccxt Ticker objects have a .to_dict() method or we extract attrs
            try:
                return t.to_dict()
            except AttributeError:
                pass
            # Fallback: extract common attributes
            return {
                "symbol": getattr(t, "symbol", ""),
                "last": getattr(t, "last", 0),
                "bid": getattr(t, "bid", 0),
                "ask": getattr(t, "ask", 0),
                "high": getattr(t, "high", 0),
                "low": getattr(t, "low", 0),
                "quoteVolume": getattr(t, "quoteVolume", 0) or 0,
                "percentage": getattr(t, "percentage", 0) or 0,
                "change": getattr(t, "change", 0) or 0,
                "baseVolume": getattr(t, "baseVolume", 0) or 0,
                "vwap": getattr(t, "vwap", 0),
                "open": getattr(t, "open", 0),
                "close": getattr(t, "close", 0) or getattr(t, "last", 0),
                "previousClose": getattr(t, "previousClose", 0),
                "info": getattr(t, "info", {}),
            }

        try:
            # ccxt fetch_tickers() returns ALL tickers at once (1 API call)
            # Then we filter to requested symbols
            all_tickers = self._ccxt_exchange.fetch_tickers()

            # v0.21.0: Build a lookup that strips the :SETTLE suffix
            # Bybit returns keys like 'BTC/USDT:USDT' but we search for 'BTC/USDT'
            ticker_lookup = {}
            for k, v in all_tickers.items():
                base_key = k.split(':')[0]  # 'BTC/USDT:USDT' → 'BTC/USDT'
                ticker_lookup[base_key] = v
                ticker_lookup[k] = v  # Also keep full key

            result = {}
            for sym in symbols:
                if sym in ticker_lookup:
                    result[sym] = _ticker_to_dict(ticker_lookup[sym])
            logger.info(f"Fetched tickers for {len(result)}/{len(symbols)} symbols from {self.exchange}")
            return result
        except Exception as e:
            # Fallback: try fetching tickers one by one (slower but more reliable)
            logger.warning(f"Bulk ticker fetch failed: {e}. Trying one-by-one...")
            result = {}
            for sym in symbols:
                try:
                    ticker = self._ccxt_exchange.fetch_ticker(sym)
                    result[sym] = _ticker_to_dict(ticker)
                    time.sleep(self._ccxt_exchange.rateLimit / 1000.0)
                except Exception:
                    pass  # Skip symbols that fail
            return result

    def close(self) -> None:
        """Clean up resources."""
        if self._ccxt_exchange:
            try:
                self._ccxt_exchange.close()
            except Exception:
                pass
            self._ccxt_exchange = None
        if self.storage:
            self.storage.close()
