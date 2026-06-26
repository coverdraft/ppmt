"""
feed.py — OHLCV data feed for paper trading.

- Bybit public API (no key required) for spot pairs BTC/USDT, ETH/USDT, SOL/USDT.
- fetch_history: pull N 5m candles going back from now.
- wait_for_close: block until next 5m candle closes, then return latest closed candle.
- fetch_recent_window: get the last W candles (for feature warm-up).

Design notes:
- We use ccxt's `fetch_ohlcv` which returns [ts, o, h, l, c, v].
- Binance IP-banned us (418) so we default to Bybit.
- We poll every 30s — Bybit rate limit is generous (1200 weight/min).
- The 5m candle that just closed at minute M (M % 5 == 0) is the candle we
  want to act on. We poll until we see a new candle in the API response,
  then we know the previous one closed.
"""
from __future__ import annotations

import time
import logging
import datetime as dt
from typing import Optional

import ccxt

LOG = logging.getLogger("pt_feed")


class Feed:
    def __init__(self, exchange_id: str = "bybit"):
        ex_cls = getattr(ccxt, exchange_id, None)
        if ex_cls is None:
            raise ValueError(f"unknown exchange: {exchange_id}")
        self.ex = ex_cls({"enableRateLimit": True})
        # Warm up markets so we don't trigger lazy load in the hot loop
        try:
            self.ex.load_markets()
            LOG.info("feed: exchange=%s markets=%d loaded", exchange_id, len(self.ex.markets))
        except Exception as e:
            LOG.warning("feed: load_markets failed (will retry on first fetch): %s", e)

    def fetch_history(self, symbol: str, timeframe: str, limit: int = 500) -> list[list]:
        """Fetch up to `limit` most recent candles. Returns oldest-first list."""
        # Bybit allows max 1000 per call; we paginate if needed.
        out: list[list] = []
        # fetch_ohlcv returns newest-last when given no `since`.
        raw = self.ex.fetch_ohlcv(symbol, timeframe, limit=min(limit, 1000))
        out.extend(raw)
        # If user wants more than 1000, paginate backward
        while len(out) < limit:
            oldest_ts = out[0][0]
            # Go back in time by `limit` candles
            tf_ms = _tf_to_ms(timeframe)
            since = oldest_ts - tf_ms * 1000
            batch = self.ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not batch:
                break
            # Drop potential overlap with `out[0]`
            new_batch = [c for c in batch if c[0] < out[0][0]]
            if not new_batch:
                break
            out = new_batch + out
            if len(new_batch) < 1000:
                break
        return out[:limit]

    def fetch_recent_window(self, symbol: str, timeframe: str, window: int) -> list[list]:
        """Return exactly `window` most recent CLOSED candles (oldest-first)."""
        # We fetch window+1 and drop the last (incomplete) candle.
        raw = self.fetch_history(symbol, timeframe, limit=window + 2)
        # Drop last (currently forming) candle
        closed = raw[:-1]
        return closed[-window:]

    def wait_for_next_close(self, symbol: str, timeframe: str, last_seen_ts: int | None = None,
                            poll_secs: int = 30, max_wait_secs: int = 600) -> list[list] | None:
        """Block until a new candle closes (after last_seen_ts) and return the
        last `window` candles including the new one.

        Returns None on timeout or error.
        """
        tf_ms = _tf_to_ms(timeframe)
        start = time.time()
        while time.time() - start < max_wait_secs:
            try:
                raw = self.fetch_history(symbol, timeframe, limit=200)
            except Exception as e:
                LOG.warning("feed: fetch failed: %s — retry in %ds", e, poll_secs)
                time.sleep(poll_secs)
                continue
            # Last candle in raw is currently forming (not yet closed).
            # The one before is the most recent CLOSED candle.
            if len(raw) < 2:
                time.sleep(poll_secs)
                continue
            closed = raw[:-1]
            latest_closed_ts = closed[-1][0]
            if last_seen_ts is None or latest_closed_ts > last_seen_ts:
                LOG.info("feed: new closed candle ts=%s (%s)",
                         latest_closed_ts,
                         dt.datetime.utcfromtimestamp(latest_closed_ts / 1000).isoformat())
                return closed
            # Not yet — wait
            now_ms = int(time.time() * 1000)
            # Sleep until ~next boundary + 5s buffer
            secs_to_next_boundary = (tf_ms - (now_ms % tf_ms)) / 1000 + 5
            sleep_secs = min(max(secs_to_next_boundary, 10), poll_secs * 2)
            LOG.debug("feed: waiting %.0fs for next %s close (last_seen=%s, latest=%s)",
                      sleep_secs, timeframe, last_seen_ts, latest_closed_ts)
            time.sleep(sleep_secs)
        LOG.warning("feed: wait_for_next_close timed out after %ds", max_wait_secs)
        return None


def _tf_to_ms(timeframe: str) -> int:
    """Convert timeframe string like '5m' / '1h' / '1d' to milliseconds."""
    unit = timeframe[-1]
    n = int(timeframe[:-1])
    if unit == "m":
        return n * 60 * 1000
    if unit == "h":
        return n * 3600 * 1000
    if unit == "d":
        return n * 86400 * 1000
    raise ValueError(f"unknown timeframe {timeframe!r}")
