"""
feed.py — OHLCV data feed for V12 paper trading.

Fetches 5m candles directly from Bybit (no 1m→5m aggregation needed).
Also fetches BTC and ETH 5m data for correlation features.

Design:
- Fetch 5m candles directly from Bybit's 5m API (same source as wait_for_next_5m_close)
- Paginate backward for warmup windows (up to 1000 candles per request)
- NEVER aggregates 1m→5m — eliminates all timestamp corruption issues
- Returns 5m DataFrames ready for feature computation

The 1m→5m aggregation was removed because Bybit's 1m API + pandas aggregation
produced timestamps shifted ~15h on some platforms. Using the 5m API directly
gives timestamps consistent with wait_for_next_5m_close.
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

    def fetch_5m_candles(self, symbol: str, limit: int = 1000) -> list[list]:
        """Fetch 5m candles directly from Bybit. Returns oldest-first list.

        Paginates backward if needed to get `limit` candles.
        Bybit returns up to 1000 candles per request.
        """
        out: list[list] = []
        raw = self.ex.fetch_ohlcv(symbol, "5m", limit=min(limit, 1000))
        out.extend(raw)
        # Paginate backward if needed
        while len(out) < limit:
            oldest_ts = out[0][0]
            since = oldest_ts - 5 * 60 * 1000 * 1000  # go back 1000 5m candles
            batch = self.ex.fetch_ohlcv(symbol, "5m", since=since, limit=1000)
            if not batch:
                break
            new_batch = [c for c in batch if c[0] < out[0][0]]
            if not new_batch:
                break
            out = new_batch + out
            if len(new_batch) < 1000:
                break
        return out[:limit]

    def fetch_5m_window(self, symbol: str, n_5m_bars: int = 400) -> pd.DataFrame:
        """Fetch n_5m_bars of 5m candles as a DataFrame.

        Uses Bybit's 5m API directly — same data source as wait_for_next_5m_close.
        No 1m→5m aggregation, so timestamps are always correct.
        """
        candles = self.fetch_5m_candles(symbol, limit=n_5m_bars + 10)  # small buffer
        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])

        # Drop the last candle (it may still be forming)
        # The last closed candle is the one before the current one
        if len(df) > 1:
            df = df.iloc[:-1]

        if len(df) > n_5m_bars:
            df = df.iloc[-n_5m_bars:]

        return df.reset_index(drop=True)

    def wait_for_next_5m_close(self, symbol: str, last_seen_ts: int | None = None,
                                poll_secs: int = 15, max_wait_secs: int = 600) -> int | None:
        """Wait until a new 5m candle closes on the given symbol.

        Returns the timestamp (ms) of the new closed 5m candle, or None on timeout.
        """
        start = time.time()
        while time.time() - start < max_wait_secs:
            try:
                raw = self.ex.fetch_ohlcv(symbol, "5m", limit=5)
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
