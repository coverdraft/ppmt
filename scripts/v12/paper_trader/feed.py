"""
feed.py — 1m OHLCV data feed with 5m aggregation for V12 paper trading.

Extends the v7 Feed to fetch 1m candles from Bybit and aggregate them into
5m bars for the V12 feature pipeline. Also fetches BTC and ETH 1m data
for correlation features.

Design:
- Fetch 1m candles (Bybit public API, no key needed)
- Aggregate to 5m OHLCV using PURE INTEGER groupby (no pd.to_datetime)
- Return 5m DataFrames ready for feature computation

CRITICAL: NEVER use pd.to_datetime on timestamp columns. It produces
incorrect datetimes on some pandas versions/platforms (shifted ~15h).
All aggregation uses integer division on ms timestamps.
"""
from __future__ import annotations

import time
import logging
import datetime as dt

import ccxt
import numpy as np
import pandas as pd

LOG = logging.getLogger("v12_feed")


class Feed:
    def __init__(self, exchange_id: str = "bybit"):
        ex_cls = getattr(ccxt, exchange_id, None)
        if ex_cls is None:
            raise ValueError(f"unknown exchange: {exchange_id}")
        self.ex = ex_cls({"enableRateLimit": True})
        try:
            self.ex.load_markets()
            LOG.info("v12_feed: exchange=%s markets=%d loaded", exchange_id, len(self.ex.markets))
        except Exception as e:
            LOG.warning("v12_feed: load_markets failed (will retry): %s", e)

    def fetch_1m_history(self, symbol: str, limit: int = 1000) -> list[list]:
        """Fetch up to `limit` most recent 1m candles. Returns oldest-first list."""
        out: list[list] = []
        raw = self.ex.fetch_ohlcv(symbol, "1m", limit=min(limit, 1000))
        out.extend(raw)
        # Paginate backward if needed
        while len(out) < limit:
            oldest_ts = out[0][0]
            since = oldest_ts - 60 * 1000 * 1000  # go back 1000 candles
            batch = self.ex.fetch_ohlcv(symbol, "1m", since=since, limit=1000)
            if not batch:
                break
            new_batch = [c for c in batch if c[0] < out[0][0]]
            if not new_batch:
                break
            out = new_batch + out
            if len(new_batch) < 1000:
                break
        return out[:limit]

    def fetch_5m_history(self, symbol: str, limit: int = 500) -> list[list]:
        """Fetch 5m candles directly. Used for quick checks."""
        raw = self.ex.fetch_ohlcv(symbol, "5m", limit=min(limit, 1000))
        return raw

    def aggregate_1m_to_5m(self, candles_1m: list[list]) -> pd.DataFrame:
        """Aggregate 1m candles into 5m OHLCV DataFrame.

        Uses PURE INTEGER groupby on ms timestamps. NEVER touches pd.to_datetime.
        This is the ONLY reliable way to aggregate across all pandas versions.
        """
        if not candles_1m:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(candles_1m, columns=["timestamp", "open", "high", "low", "close", "volume"])

        # Group by 5-minute interval using integer division on ms timestamps
        interval_ms = 5 * 60 * 1000  # 300,000 ms
        df["_group"] = df["timestamp"] // interval_ms

        agg = df.groupby("_group").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })

        # Convert group key back to ms timestamp (start of each 5m interval)
        agg["timestamp"] = (agg.index.astype(np.int64) * interval_ms)
        agg = agg[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
        return agg

    def fetch_5m_window(self, symbol: str, n_5m_bars: int = 400) -> pd.DataFrame:
        """Fetch enough 1m data to produce n_5m_bars of 5m candles.

        We need ~5x 1m candles per 5m bar, plus buffer for partial bars.
        """
        n_1m_needed = n_5m_bars * 5 + 50  # extra buffer
        candles_1m = self.fetch_1m_history(symbol, limit=n_1m_needed)
        df_5m = self.aggregate_1m_to_5m(candles_1m)
        # Return only the last n_5m_bars
        if len(df_5m) > n_5m_bars:
            df_5m = df_5m.iloc[-n_5m_bars:].reset_index(drop=True)
        return df_5m

    def wait_for_next_5m_close(self, symbol: str, last_seen_ts: int | None = None,
                                poll_secs: int = 15, max_wait_secs: int = 600) -> int | None:
        """Wait until a new 5m candle closes on the given symbol.

        Returns the timestamp (ms) of the new closed 5m candle, or None on timeout.
        """
        start = time.time()
        while time.time() - start < max_wait_secs:
            try:
                raw = self.fetch_5m_history(symbol, limit=5)
            except Exception as e:
                LOG.warning("v12_feed: fetch failed: %s — retry in %ds", e, poll_secs)
                time.sleep(poll_secs)
                continue

            if len(raw) < 2:
                time.sleep(poll_secs)
                continue

            # Last candle in raw is currently forming (not yet closed).
            # The one before is the most recent CLOSED candle.
            closed_ts = raw[-2][0]
            if last_seen_ts is None or closed_ts > last_seen_ts:
                LOG.info("v12_feed: new 5m candle closed at ts=%s (%s)",
                         closed_ts, dt.datetime.utcfromtimestamp(closed_ts / 1000).isoformat())
                return closed_ts

            # Not yet — calculate wait time
            now_ms = int(time.time() * 1000)
            five_min_ms = 5 * 60 * 1000
            secs_to_next = (five_min_ms - (now_ms % five_min_ms)) / 1000 + 5
            sleep_secs = min(max(secs_to_next, 5), poll_secs * 2)
            LOG.debug("v12_feed: waiting %.0fs for next 5m close", sleep_secs)
            time.sleep(sleep_secs)

        LOG.warning("v12_feed: wait_for_next_5m_close timed out after %ds", max_wait_secs)
        return None
