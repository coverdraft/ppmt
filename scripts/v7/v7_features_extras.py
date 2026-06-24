"""
PPMT v7 — Features Extras (F4)
================================

Adds 6 new features on top of v6's 59 base features:
  1. funding_rate         — Raw funding rate (8h interval, last known)
  2. funding_rate_z       — Z-score vs 30-day rolling mean
  3. oi_change_1h         — Open Interest % change vs 1h ago (12x 5m bars)
  4. oi_change_4h         — Open Interest % change vs 4h ago (48x 5m bars)
  5. sector_one_hot       — 4 binary features (blue_chip/large_cap/old_meme/new_meme)
                            + 1 categorical int (0-3) for LightGBM native handling
  6. day_of_week_sin/cos  — Cyclical day encoding (sin + cos to capture weekly seasonality)

Total NEW features: 4 (funding/OI) + 5 (sector: 4 binary + 1 int) + 2 (dow sin/cos) = 11
But we count "logical" additions:
  - funding_rate, funding_rate_z, oi_change_1h, oi_change_4h = 4 numeric
  - sector_one_hot (expanded to 4 binaries + 1 categorical) = 5 features
  - day_of_week_sin, day_of_week_cos = 2 features
  TOTAL: 11 features (vs 6 conceptual)

Why these matter (per MASTER_PLAN §3):
  - funding_rate_z: SHORT gate (only SHORT if funding_z > 1.5 — longs overleveraged)
  - oi_change_1h/4h: rising OI + rising price = trend confirmation;
                     rising OI + falling price = bearish positioning
  - sector_one_hot: LightGBM sees sector and learns per-sector adjustments
                    (PEPE/WIF/BONK share microstructure within new_meme)
  - day_of_week_sin/cos: captures weekend vs weekday effect in crypto
                         (Sunday/Monday typically lower volume)

Data sources:
  - Funding rate: Binance Futures public API (no auth required)
    GET https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000
  - Open Interest: Binance Futures public API
    GET https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=5m&limit=1000
  - Both are 8h/5m intervals respectively, we cache locally to avoid API limits

Anti-leakage:
  - Funding rate is forward-looking in nature: the 8h funding rate SETTLES
    at fixed times (00:00, 08:00, 16:00 UTC). We use the LAST SETTLED rate
    (i.e., fundingRate for fundingTime <= current_ts), never the upcoming.
  - OI is real-time market data, no leakage concern (just need to align
    timestamps with candle close).
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Same dir
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from v7_ohlcv_encoder import SECTOR_TOKENS, SECTOR_BINS, symbol_to_sector


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BINANCE_FAPI_BASE = "https://fapi.binance.com"

# Binance Futures uses "1000X" prefix for low-priced tokens (< $0.01)
# to avoid precision issues. Map our internal symbol → Binance API symbol.
BINANCE_SYMBOL_MAP: Dict[str, str] = {
    "SHIBUSDT": "1000SHIBUSDT",
    "PEPEUSDT": "1000PEPEUSDT",
    "BONKUSDT": "1000BONKUSDT",
    # All other tokens (BTCUSDT, ETHUSDT, SOLUSDT, ADAUSDT, AVAXUSDT,
    # LINKUSDT, XRPUSDT, DOGEUSDT, WIFUSDT) use the same name on both sides.
}


def to_binance_symbol(symbol: str) -> str:
    """
    Convert our internal symbol (e.g., 'SHIBUSDT') to the Binance API
    symbol (e.g., '1000SHIBUSDT').

    For symbols without a mapping, returns the input unchanged.
    """
    s = symbol.upper().strip()
    return BINANCE_SYMBOL_MAP.get(s, s)


# Sectors in fixed order (for one-hot encoding)
SECTOR_INDEX: Dict[str, int] = {
    "blue_chip": 0,
    "large_cap": 1,
    "old_meme": 2,
    "new_meme": 3,
}

# Funding rate settles every 8h at 00:00, 08:00, 16:00 UTC
FUNDING_INTERVAL_SECONDS = 8 * 3600  # 28800

# OI history period (must match our candle TF)
OI_PERIOD = "5m"

# Cache config
CACHE_DIR_DEFAULT = "data/v7_cache"
FUNDING_CACHE_DB = "funding_cache.db"      # SQLite: per-symbol funding rates
OI_CACHE_DB = "oi_cache.db"                # SQLite: per-symbol OI snapshots

# Rolling window for funding_rate_z (30 days = 90 funding intervals at 8h)
FUNDING_Z_WINDOW = 90


# ---------------------------------------------------------------------------
# Sector one-hot encoding
# ---------------------------------------------------------------------------

def encode_sector_one_hot(symbol: str) -> Dict[str, float]:
    """
    Encode the sector of a symbol as 4 binary features + 1 categorical.

    Returns dict with keys:
        sector_blue_chip, sector_large_cap, sector_old_meme, sector_new_meme
        sector_idx  (0-3, for LightGBM categorical handling)

    Args:
        symbol: e.g., 'BTCUSDT', 'BTC', 'btc-usd'

    Raises:
        ValueError: if symbol not in any sector.
    """
    sector = symbol_to_sector(symbol)
    idx = SECTOR_INDEX[sector]
    return {
        "sector_blue_chip": 1.0 if sector == "blue_chip" else 0.0,
        "sector_large_cap": 1.0 if sector == "large_cap" else 0.0,
        "sector_old_meme":  1.0 if sector == "old_meme" else 0.0,
        "sector_new_meme":  1.0 if sector == "new_meme" else 0.0,
        "sector_idx": float(idx),
    }


# ---------------------------------------------------------------------------
# Day-of-week cyclical encoding
# ---------------------------------------------------------------------------

def encode_day_of_week(timestamp: float) -> Dict[str, float]:
    """
    Encode day of week as sin/cos cyclical features.

    Args:
        timestamp: epoch seconds (UTC)

    Returns:
        dict with:
            day_of_week_sin: sin(2*pi*dow/7)  in [-1, 1]
            day_of_week_cos: cos(2*pi*dow/7)  in [-1, 1]
            day_of_week:     integer 0-6 (0=Monday, 6=Sunday)
    """
    # Python's time.localtime().tm_wday: 0=Monday, 6=Sunday
    # Use gmtime for UTC consistency
    dow = time.gmtime(timestamp).tm_wday  # 0-6
    angle = 2.0 * math.pi * dow / 7.0
    return {
        "day_of_week_sin": math.sin(angle),
        "day_of_week_cos": math.cos(angle),
        "day_of_week": float(dow),
    }


# ---------------------------------------------------------------------------
# Binance API fetcher (with local SQLite cache)
# ---------------------------------------------------------------------------

@dataclass
class BinanceFundingFetcher:
    """
    Fetches and caches Binance Futures funding rates per symbol.

    Cache schema (SQLite):
        CREATE TABLE funding_rates (
            symbol TEXT,
            funding_time INTEGER,    -- epoch ms (when rate settles)
            funding_rate REAL,       -- e.g., 0.00002154 (0.002154%)
            mark_price REAL,         -- e.g., 62696.80 (USDT)
            fetched_at INTEGER,     -- epoch s (when we cached it)
            PRIMARY KEY (symbol, funding_time)
        )
    """

    cache_dir: str = CACHE_DIR_DEFAULT
    cache_db: str = FUNDING_CACHE_DB
    timeout_seconds: int = 15
    user_agent: str = "ppmt-v7/1.0"

    # Binance returns at most 1000 records per request
    BINANCE_MAX_LIMIT = 1000

    def __post_init__(self) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        # Eagerly create the cache DB so it exists immediately after construction
        self._connect().close()

    @property
    def cache_path(self) -> str:
        return os.path.join(self.cache_dir, self.cache_db)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.cache_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS funding_rates (
                symbol TEXT NOT NULL,
                funding_time INTEGER NOT NULL,
                funding_rate REAL NOT NULL,
                mark_price REAL,
                fetched_at INTEGER NOT NULL,
                PRIMARY KEY (symbol, funding_time)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_time ON funding_rates(symbol, funding_time)")
        conn.commit()
        return conn

    def _fetch_binance(
        self,
        symbol: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Fetch funding rates from Binance API.

        Args:
            symbol: 'BTCUSDT' (must include USDT suffix)
            start_time_ms: optional epoch ms (inclusive)
            end_time_ms: optional epoch ms (exclusive)
            limit: max records (1-1000)
        """
        params = {"symbol": symbol, "limit": min(limit, self.BINANCE_MAX_LIMIT)}
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        url = f"{BINANCE_FAPI_BASE}/fapi/v1/fundingRate?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as r:
            data = json.loads(r.read())
        return data

    def fetch_and_cache(
        self,
        symbol: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        max_pages: int = 50,
    ) -> int:
        """
        Fetch funding rates from Binance and store in local cache.

        Handles pagination: if more than `limit` records are available,
        fetches in batches using startTime/endTime.

        Args:
            symbol: 'BTCUSDT' (internal name; will be mapped to Binance API
                    symbol via to_binance_symbol, e.g., 'SHIBUSDT' → '1000SHIBUSDT')
            start_time_ms: optional epoch ms
            end_time_ms: optional epoch ms
            max_pages: safety cap on number of API calls

        Returns:
            Number of new records cached.
        """
        binance_symbol = to_binance_symbol(symbol)
        conn = self._connect()
        try:
            n_inserted = 0
            cursor_ms = start_time_ms
            for _ in range(max_pages):
                batch = self._fetch_binance(binance_symbol, start_time_ms=cursor_ms, end_time_ms=end_time_ms)
                if not batch:
                    break
                # Insert into cache (under the INTERNAL symbol, not Binance's)
                rows = []
                for r in batch:
                    rows.append((
                        symbol,
                        int(r["fundingTime"]),
                        float(r["fundingRate"]),
                        float(r.get("markPrice", 0.0) or 0.0),
                        int(time.time()),
                    ))
                conn.executemany("""
                    INSERT OR REPLACE INTO funding_rates
                    (symbol, funding_time, funding_rate, mark_price, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                """, rows)
                conn.commit()
                n_inserted += len(rows)

                # Advance cursor: last fundingTime + 1ms
                last_time = int(batch[-1]["fundingTime"])
                cursor_ms = last_time + 1
                if end_time_ms is not None and cursor_ms >= end_time_ms:
                    break
                if len(batch) < self.BINANCE_MAX_LIMIT:
                    break  # no more pages
            return n_inserted
        finally:
            conn.close()

    def get_last_settled_rate(
        self,
        symbol: str,
        current_ts_seconds: float,
    ) -> Tuple[float, float]:
        """
        Get the most recent SETTLED funding rate at or before current_ts.

        Anti-leakage: returns the rate that was already settled before
        current_ts (funding_time <= current_ts). The NEXT upcoming rate
        is NOT used (it would be lookahead).

        Args:
            symbol: 'BTCUSDT'
            current_ts_seconds: epoch seconds of the candle close

        Returns:
            (funding_rate, funding_time_seconds)
            Returns (0.0, 0.0) if no settled rate in cache.
        """
        current_ms = int(current_ts_seconds * 1000)
        conn = self._connect()
        try:
            cur = conn.execute("""
                SELECT funding_rate, funding_time
                FROM funding_rates
                WHERE symbol = ? AND funding_time <= ?
                ORDER BY funding_time DESC
                LIMIT 1
            """, (symbol, current_ms))
            row = cur.fetchone()
            if row is None:
                return (0.0, 0.0)
            return (float(row[0]), int(row[1]) / 1000.0)
        finally:
            conn.close()

    def get_history(
        self,
        symbol: str,
        end_ts_seconds: float,
        lookback_seconds: int = 30 * 24 * 3600,  # 30 days
    ) -> List[Tuple[float, float]]:
        """
        Get historical funding rates for z-score computation.

        Args:
            symbol: 'BTCUSDT'
            end_ts_seconds: epoch seconds (exclusive upper bound)
            lookback_seconds: how far back to fetch (default 30 days)

        Returns:
            List of (funding_rate, funding_time_seconds), oldest first.
        """
        start_ms = int((end_ts_seconds - lookback_seconds) * 1000)
        end_ms = int(end_ts_seconds * 1000)
        conn = self._connect()
        try:
            cur = conn.execute("""
                SELECT funding_rate, funding_time
                FROM funding_rates
                WHERE symbol = ? AND funding_time >= ? AND funding_time <= ?
                ORDER BY funding_time ASC
            """, (symbol, start_ms, end_ms))
            return [(float(r[0]), int(r[1]) / 1000.0) for r in cur.fetchall()]
        finally:
            conn.close()

    def compute_funding_z(
        self,
        symbol: str,
        current_ts_seconds: float,
        window: int = FUNDING_Z_WINDOW,
    ) -> float:
        """
        Compute the z-score of the current funding rate vs the last
        `window` settled rates (default 90 = 30 days at 8h intervals).

        z = (current_rate - mean) / std

        Args:
            symbol: 'BTCUSDT'
            current_ts_seconds: epoch seconds
            window: number of historical settled rates to use

        Returns:
            z-score (float). Returns 0.0 if insufficient history.
        """
        history = self.get_history(
            symbol,
            end_ts_seconds=current_ts_seconds,
            lookback_seconds=window * FUNDING_INTERVAL_SECONDS + 86400,  # +1 day buffer
        )
        if len(history) < max(10, window // 3):
            return 0.0  # not enough data
        # Use the last `window` rates
        rates = [r[0] for r in history[-window:]]
        n = len(rates)
        mean = sum(rates) / n
        var = sum((r - mean) ** 2 for r in rates) / max(1, n - 1)
        std = math.sqrt(var)
        if std < 1e-9:
            return 0.0
        current_rate, _ = self.get_last_settled_rate(symbol, current_ts_seconds)
        return (current_rate - mean) / std


# ---------------------------------------------------------------------------
# Binance Open Interest fetcher
# ---------------------------------------------------------------------------

@dataclass
class BinanceOIFetcher:
    """
    Fetches and caches Binance Futures Open Interest history per symbol.

    Cache schema (SQLite):
        CREATE TABLE oi_history (
            symbol TEXT,
            timestamp INTEGER,        -- epoch ms (start of 5m period)
            open_interest REAL,       -- sumOpenInterest (in base asset units)
            open_interest_value REAL, -- sumOpenInterestValue (in USDT)
            fetched_at INTEGER,
            PRIMARY KEY (symbol, timestamp)
        )
    """

    cache_dir: str = CACHE_DIR_DEFAULT
    cache_db: str = OI_CACHE_DB
    timeout_seconds: int = 15
    user_agent: str = "ppmt-v7/1.0"
    period: str = OI_PERIOD

    BINANCE_MAX_LIMIT = 500  # openInterestHist endpoint limit

    def __post_init__(self) -> "os.makedirs":
        os.makedirs(self.cache_dir, exist_ok=True)
        # Eagerly create the cache DB so it exists immediately after construction
        self._connect().close()

    @property
    def cache_path(self) -> str:
        return os.path.join(self.cache_dir, self.cache_db)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.cache_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oi_history (
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open_interest REAL NOT NULL,
                open_interest_value REAL,
                fetched_at INTEGER NOT NULL,
                PRIMARY KEY (symbol, timestamp)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_oi_symbol_ts ON oi_history(symbol, timestamp)")
        conn.commit()
        return conn

    def _fetch_binance(
        self,
        symbol: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        params = {
            "symbol": symbol,
            "period": self.period,
            "limit": min(limit, self.BINANCE_MAX_LIMIT),
        }
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        url = f"{BINANCE_FAPI_BASE}/futures/data/openInterestHist?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as r:
            data = json.loads(r.read())
        return data

    def fetch_and_cache(
        self,
        symbol: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        max_pages: int = 100,
    ) -> int:
        """
        Fetch OI history from Binance and store in local cache.

        Args:
            symbol: 'BTCUSDT' (internal name; mapped to Binance API symbol
                    via to_binance_symbol, e.g., 'SHIBUSDT' → '1000SHIBUSDT')

        Returns: number of new records cached.
        """
        binance_symbol = to_binance_symbol(symbol)
        conn = self._connect()
        try:
            n_inserted = 0
            cursor_ms = start_time_ms
            for _ in range(max_pages):
                batch = self._fetch_binance(binance_symbol, start_time_ms=cursor_ms, end_time_ms=end_time_ms)
                if not batch:
                    break
                rows = []
                for r in batch:
                    rows.append((
                        symbol,  # store under INTERNAL symbol
                        int(r["timestamp"]),
                        float(r["sumOpenInterest"]),
                        float(r["sumOpenInterestValue"]),
                        int(time.time()),
                    ))
                conn.executemany("""
                    INSERT OR REPLACE INTO oi_history
                    (symbol, timestamp, open_interest, open_interest_value, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                """, rows)
                conn.commit()
                n_inserted += len(rows)

                # Advance cursor
                last_ts = int(batch[-1]["timestamp"])
                cursor_ms = last_ts + 1
                if end_time_ms is not None and cursor_ms >= end_time_ms:
                    break
                if len(batch) < self.BINANCE_MAX_LIMIT:
                    break
            return n_inserted
        finally:
            conn.close()

    def get_oi_at(self, symbol: str, ts_seconds: float) -> Optional[float]:
        """
        Get the OI snapshot whose timestamp is the LARGEST value <= ts_seconds.

        Anti-leakage: only uses OI data that was already observable at ts_seconds.
        Returns None if no data.
        """
        ts_ms = int(ts_seconds * 1000)
        conn = self._connect()
        try:
            cur = conn.execute("""
                SELECT open_interest
                FROM oi_history
                WHERE symbol = ? AND timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (symbol, ts_ms))
            row = cur.fetchone()
            return float(row[0]) if row else None
        finally:
            conn.close()

    def compute_oi_change(
        self,
        symbol: str,
        current_ts_seconds: float,
        lookback_seconds: int,
    ) -> float:
        """
        Compute % change in OI vs `lookback_seconds` ago.

        change = (oi_now - oi_then) / oi_then * 100

        Args:
            symbol: 'BTCUSDT'
            current_ts_seconds: epoch seconds of the candle close
            lookback_seconds: how far back to compare (e.g., 3600 for 1h)

        Returns:
            % change (float). Returns 0.0 if either point missing.
        """
        oi_now = self.get_oi_at(symbol, current_ts_seconds)
        if oi_now is None or oi_now <= 0:
            return 0.0
        oi_then = self.get_oi_at(symbol, current_ts_seconds - lookback_seconds)
        if oi_then is None or oi_then <= 0:
            return 0.0
        return (oi_now - oi_then) / oi_then * 100.0


# ---------------------------------------------------------------------------
# Combined feature extractor
# ---------------------------------------------------------------------------

@dataclass
class FeaturesExtrasExtractor:
    """
    Combines all F4 features into a single dict.

    Usage:
        extractor = FeaturesExtrasExtractor()
        # Pre-fetch funding + OI history (one-time, per symbol)
        extractor.prefetch_symbol("BTCUSDT", start_ts, end_ts)
        # Compute features at inference time
        features = extractor.extract(symbol="BTCUSDT", ts=candle_close_ts)
    """

    cache_dir: str = CACHE_DIR_DEFAULT
    funding_fetcher: BinanceFundingFetcher = field(default_factory=BinanceFundingFetcher)
    oi_fetcher: BinanceOIFetcher = field(default_factory=BinanceOIFetcher)

    # Feature names produced by extract()
    FEATURE_NAMES: List[str] = field(default_factory=lambda: [
        "funding_rate",
        "funding_rate_z",
        "oi_change_1h",
        "oi_change_4h",
        "sector_blue_chip", "sector_large_cap", "sector_old_meme", "sector_new_meme",
        "sector_idx",
        "day_of_week_sin", "day_of_week_cos", "day_of_week",
    ])

    def __post_init__(self) -> None:
        # Ensure fetchers share cache_dir
        self.funding_fetcher.cache_dir = self.cache_dir
        self.oi_fetcher.cache_dir = self.cache_dir

    # ------------- prefetch -------------

    def prefetch_symbol(
        self,
        symbol: str,
        start_ts_seconds: float,
        end_ts_seconds: float,
        fetch_funding: bool = True,
        fetch_oi: bool = True,
    ) -> Dict[str, int]:
        """
        Fetch and cache funding + OI history for a symbol between
        [start_ts, end_ts]. Idempotent: re-running won't duplicate data
        (SQLite INSERT OR REPLACE).

        Returns: dict with counts {funding_new, oi_new}.
        """
        result = {"funding_new": 0, "oi_new": 0}
        start_ms = int(start_ts_seconds * 1000)
        end_ms = int(end_ts_seconds * 1000)

        if fetch_funding:
            try:
                result["funding_new"] = self.funding_fetcher.fetch_and_cache(
                    symbol, start_time_ms=start_ms, end_time_ms=end_ms,
                )
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                print(f"  ! funding fetch failed for {symbol}: {e}")
        if fetch_oi:
            try:
                result["oi_new"] = self.oi_fetcher.fetch_and_cache(
                    symbol, start_time_ms=start_ms, end_time_ms=end_ms,
                )
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                print(f"  ! OI fetch failed for {symbol}: {e}")
        return result

    # ------------- extract -------------

    def extract(self, symbol: str, ts_seconds: float) -> Dict[str, float]:
        """
        Extract all F4 features for a (symbol, timestamp) pair.

        Args:
            symbol: 'BTCUSDT' (must include USDT suffix for API)
            ts_seconds: epoch seconds of the candle close

        Returns:
            Dict with all F4 feature names (12 features).
            Missing data → 0.0 (safe defaults).
        """
        features: Dict[str, float] = {}

        # Funding rate + z-score
        funding_rate, _ = self.funding_fetcher.get_last_settled_rate(symbol, ts_seconds)
        features["funding_rate"] = funding_rate
        features["funding_rate_z"] = self.funding_fetcher.compute_funding_z(symbol, ts_seconds)

        # OI changes
        features["oi_change_1h"] = self.oi_fetcher.compute_oi_change(
            symbol, ts_seconds, lookback_seconds=3600,
        )
        features["oi_change_4h"] = self.oi_fetcher.compute_oi_change(
            symbol, ts_seconds, lookback_seconds=4 * 3600,
        )

        # Sector one-hot
        features.update(encode_sector_one_hot(symbol))

        # Day of week
        features.update(encode_day_of_week(ts_seconds))

        return features

    def extract_batch(
        self,
        symbol: str,
        timestamps: List[float],
    ) -> List[Dict[str, float]]:
        """
        Extract features for a list of timestamps (same symbol).
        Useful for backtest: avoids redundant DB connections per row.
        """
        # Single connection batch would be faster, but the simple loop
        # is good enough for now — each query is indexed.
        return [self.extract(symbol, ts) for ts in timestamps]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def get_feature_names() -> List[str]:
    """Return the list of F4 feature names (12 total)."""
    return [
        "funding_rate",
        "funding_rate_z",
        "oi_change_1h",
        "oi_change_4h",
        "sector_blue_chip", "sector_large_cap", "sector_old_meme", "sector_new_meme",
        "sector_idx",
        "day_of_week_sin", "day_of_week_cos", "day_of_week",
    ]


def is_funding_data_available(symbol: str, ts_seconds: float, cache_dir: str = CACHE_DIR_DEFAULT) -> bool:
    """Quick check: is there at least one funding rate <= ts_seconds in cache?"""
    ff = BinanceFundingFetcher(cache_dir=cache_dir)
    rate, _ = ff.get_last_settled_rate(symbol, ts_seconds)
    return rate != 0.0


def is_oi_data_available(symbol: str, ts_seconds: float, cache_dir: str = CACHE_DIR_DEFAULT) -> bool:
    """Quick check: is there OI data <= ts_seconds in cache?"""
    of = BinanceOIFetcher(cache_dir=cache_dir)
    oi = of.get_oi_at(symbol, ts_seconds)
    return oi is not None
