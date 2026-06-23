"""
Track A: Massive multi-regime OHLCV download for PPMT V5.

Downloads 1m + 5m + 15m candles for 9 base tokens + 5 memes across
4 historical windows:
  - BULL_2024   (2024-10-01 → 2024-12-30): BTC pump to 100k
  - RANGE_2025  (2025-08-01 → 2025-10-30): post-ebb consolidation
  - RECENT_2026 (2026-03-24 → 2026-06-22): recent live regime
  - BEAR_2022   (2022-05-01 → 2022-07-30): LUNA / 3AC crash
  - RANGE_2023  (2023-03-01 → 2023-10-30): post-FTM accumulation

Stores into ohlcv_ext table (preserving existing rows). For windows
where a token did not exist on Binance, silently skips that token-window
combination and logs the skip.

Usage:
    python /home/z/my-project/scripts/v5_download_massive.py [--timeframes 1m 5m 15m] [--workers 4]
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# Make ppmt importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))

from ppmt.data.storage import PPMTStorage as Storage  # noqa: E402

LOG = logging.getLogger("v5_download")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

# Process-wide lock for SQLite writes (SQLite connections are not thread-safe
# even with check_same_thread=False — concurrent writes corrupt the DB).
_DB_WRITE_LOCK = threading.Lock()
# Shared SQLite connection (opened with check_same_thread=False).
_DB_CONN: Optional[sqlite3.Connection] = None

# Window definitions: (name, start_ms, end_ms, description)
WINDOWS = [
    ("BULL_2024",   1727740800000, 1735516800000, "Oct-Dec 2024 BTC pump to 100k"),
    ("RANGE_2025",  1753990400000, 1761817200000, "Aug-Oct 2025 consolidation"),
    ("RECENT_2026", 1742774400000, 1750636800000, "Mar-Jun 2026 recent"),
    ("BEAR_2022",   1651363200000, 1659139200000, "May-Jul 2022 LUNA/3AC crash"),
    ("RANGE_2023",  1677628800000, 1698710400000, "Mar-Oct 2023 accumulation"),
]

# Token list: (symbol_binance, asset_class)
TOKENS = [
    # Blue chips (always available across all windows)
    ("BTCUSDT",  "blue_chip"),
    ("ETHUSDT",  "blue_chip"),
    ("BNBUSDT",  "blue_chip"),
    # Large caps
    ("SOLUSDT",  "large_cap"),
    ("XRPUSDT",  "large_cap"),
    # Mid caps
    ("ADAUSDT",  "mid_cap"),
    ("AVAXUSDT", "mid_cap"),
    ("LINKUSDT", "mid_cap"),
    # Memes — availability varies by window
    ("DOGEUSDT", "meme"),
    ("SHIBUSDT", "meme"),
    ("PEPEUSDT", "meme"),
    ("WIFUSDT",  "meme"),
    ("BONKUSDT", "meme"),
]

# Tokens that did NOT exist (or had negligible liquidity) on Binance
# during certain windows. We skip these to avoid fake data or sparse data.
# Format: { window_name: set(symbols_to_skip) }
SKIP = {
    "BEAR_2022": {
        # PEPE/WIF/BONK did not exist until 2023-2024
        "PEPEUSDT", "WIFUSDT", "BONKUSDT",
        # SHIB existed but had thin futures liquidity; keep spot only
        # We'll attempt the fetch anyway — Binance will return 400 if missing.
    },
    "BULL_2024": {
        # All listed tokens existed by Oct 2024 except BONK (Dec 2023 listing, OK)
        # WIF listed on Binance futures in Mar 2024, OK for Oct-Dec window
    },
    "RANGE_2023": {
        # PEPE listed May 2023 — partial coverage Mar-Oct. Skip Mar-Apr.
        # For simplicity, skip PEPE entirely in this window (only ~5 months data).
        "PEPEUSDT",
        # WIF / BONK did not exist until late 2023 / Dec 2023
        "WIFUSDT", "BONKUSDT",
    },
}

TF_MS = {
    "1m":  60_000,
    "5m":  300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h":  3_600_000,
}


def fetch_klines_paginated(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    max_retries: int = 4,
) -> list[list]:
    """Fetch all klines between [start_ms, end_ms] for symbol+interval.

    Returns a list of Binance kline rows:
        [open_time, open, high, low, close, volume, close_time,
         quote_volume, count, taker_buy_base, taker_buy_quote, ignore]
    """
    all_rows: list[list] = []
    cursor = start_ms
    page_size = 1000  # Binance max

    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": page_size,
        }

        for attempt in range(max_retries):
            try:
                r = requests.get(BINANCE_KLINES, params=params, timeout=20)
                if r.status_code in (429, 418):
                    # Rate limit — exponential backoff
                    wait = 2 ** (attempt + 1)
                    LOG.warning("Rate limited on %s %s, waiting %ds", symbol, interval, wait)
                    time.sleep(wait)
                    continue
                if r.status_code == 400:
                    # Symbol likely doesn't exist for this window — abort silently
                    return all_rows
                r.raise_for_status()
                rows = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                wait = 2 ** attempt
                LOG.warning("Error on %s %s attempt %d: %s — wait %ds",
                            symbol, interval, attempt + 1, e, wait)
                time.sleep(wait)
        else:
            LOG.error("Failed after %d retries: %s %s @ %d",
                      max_retries, symbol, interval, cursor)
            return all_rows

        if not rows:
            break

        all_rows.extend(rows)

        # Advance cursor: use last close_time + 1ms
        last_close = rows[-1][6]
        cursor = last_close + 1

        # If we got fewer than a full page, we're done
        if len(rows) < page_size:
            break

        # Be polite to Binance
        time.sleep(0.12)

    return all_rows


def store_rows(
    storage: PPMTStorage,
    symbol: str,
    timeframe: str,
    window: str,
    rows: list[list],
) -> int:
    """Insert klines into ohlcv_ext. Returns number of rows inserted.

    Uses the shared _DB_CONN opened with check_same_thread=False and
    serializes writes with _DB_WRITE_LOCK.
    """
    if not rows:
        return 0

    conn = _DB_CONN
    inserted = 0
    batch = []

    for r in rows:
        open_time = r[0]  # ms
        ts_sec = open_time // 1000
        batch.append((
            symbol, timeframe, ts_sec, window,
            float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
        ))

    CHUNK = 500
    for i in range(0, len(batch), CHUNK):
        chunk = batch[i:i + CHUNK]
        placeholders = ",".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(chunk))
        flat = [v for row in chunk for v in row]
        try:
            with _DB_WRITE_LOCK:
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO ohlcv_ext
                        (symbol, timeframe, timestamp, window,
                         open, high, low, close, volume)
                    VALUES {placeholders}
                    """,
                    flat,
                )
                conn.commit()
            inserted += len(chunk)
        except Exception as e:
            LOG.error("DB insert failed for %s %s %s: %s", symbol, timeframe, window, e)

    return inserted


def download_one(
    symbol: str,
    asset_class: str,
    timeframe: str,
    window_name: str,
    start_ms: int,
    end_ms: int,
    storage: Storage,
) -> tuple[str, str, str, str, int, str]:
    """Download + store one (symbol, tf, window) combo. Returns a result tuple."""
    if symbol in SKIP.get(window_name, set()):
        return (symbol, timeframe, window_name, asset_class, 0, "skipped_not_listed")

    rows = fetch_klines_paginated(symbol, timeframe, start_ms, end_ms)
    n = store_rows(storage, symbol, timeframe, window_name, rows)

    status = "ok" if n > 0 else "empty"
    return (symbol, timeframe, window_name, asset_class, n, status)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframes", nargs="+", default=["1m", "5m", "15m"])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tokens", nargs="+", default=None,
                        help="Subset of tokens (e.g. BTCUSDT PEPEUSDT)")
    parser.add_argument("--windows", nargs="+", default=None,
                        help="Subset of windows (e.g. BULL_2024 BEAR_2022)")
    args = parser.parse_args()

    storage = Storage()

    # Open a separate thread-safe connection that workers can share.
    # The original PPMTStorage.conn uses check_same_thread=True (default),
    # which makes it impossible to use from ThreadPoolExecutor workers.
    global _DB_CONN
    _DB_CONN = sqlite3.connect(storage.db_path, check_same_thread=False)
    _DB_CONN.execute("PRAGMA journal_mode=WAL")
    _DB_CONN.execute("PRAGMA synchronous=NORMAL")

    # Ensure ohlcv_ext has the window column (idempotent)
    conn = _DB_CONN
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_ext (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            window TEXT NOT NULL DEFAULT '',
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            UNIQUE(symbol, timeframe, timestamp, window)
        )
    """)
    # Make sure window column exists on legacy installations
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ohlcv_ext)").fetchall()]
    if "window" not in cols:
        conn.execute("ALTER TABLE ohlcv_ext ADD COLUMN window TEXT NOT NULL DEFAULT ''")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_ext_sym_tf_win
        ON ohlcv_ext(symbol, timeframe, window, timestamp)
    """)
    conn.commit()

    tokens = [(s, c) for (s, c) in TOKENS if args.tokens is None or s in args.tokens]
    windows = [(n, s, e, d) for (n, s, e, d) in WINDOWS
               if args.windows is None or n in args.windows]

    jobs = []
    for tf in args.timeframes:
        for (sym, cls) in tokens:
            for (wn, ws, we, _) in windows:
                jobs.append((sym, cls, tf, wn, ws, we))

    LOG.info("Starting download: %d jobs (%d tokens × %d TFs × %d windows)",
             len(jobs), len(tokens), len(args.timeframes), len(windows))

    results = []
    ok = 0
    skipped = 0
    empty = 0
    failed = 0
    total_candles = 0

    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="dl") as ex:
        futures = {
            ex.submit(download_one, sym, cls, tf, wn, ws, we, storage): (sym, cls, tf, wn)
            for (sym, cls, tf, wn, ws, we) in jobs
        }
        for i, fut in enumerate(as_completed(futures), 1):
            meta = futures[fut]
            try:
                res = fut.result()
                results.append(res)
                sym, tf, wn, cls, n, status = res
                if status == "ok":
                    ok += 1
                    total_candles += n
                    LOG.info("[%d/%d] %s %s %s: %d candles", i, len(jobs), sym, tf, wn, n)
                elif status == "skipped_not_listed":
                    skipped += 1
                    LOG.info("[%d/%d] %s %s %s: skipped (not listed)", i, len(jobs), sym, tf, wn)
                elif status == "empty":
                    empty += 1
                    LOG.warning("[%d/%d] %s %s %s: empty", i, len(jobs), sym, tf, wn)
            except Exception as e:
                failed += 1
                LOG.exception("[%d/%d] FAILED %s: %s", i, len(jobs), meta, e)

    LOG.info("=" * 60)
    LOG.info("Download complete:")
    LOG.info("  OK:       %d jobs (%d candles total)", ok, total_candles)
    LOG.info("  Skipped:  %d (token not listed in window)", skipped)
    LOG.info("  Empty:    %d", empty)
    LOG.info("  Failed:   %d", failed)

    # Summary by window
    print("\n=== Summary by window ===")
    by_window: dict[str, int] = {}
    for r in results:
        if r[5] == "ok":
            by_window[r[2]] = by_window.get(r[2], 0) + r[4]
    for w, n in sorted(by_window.items()):
        print(f"  {w:14s}  {n:>10,d} candles")

    # Summary by token
    print("\n=== Summary by token ===")
    by_token: dict[str, int] = {}
    for r in results:
        if r[5] == "ok":
            by_token[r[0]] = by_token.get(r[0], 0) + r[4]
    for t, n in sorted(by_token.items(), key=lambda x: -x[1]):
        print(f"  {t:12s}  {n:>10,d} candles")

    # Summary by TF
    print("\n=== Summary by timeframe ===")
    by_tf: dict[str, int] = {}
    for r in results:
        if r[5] == "ok":
            by_tf[r[1]] = by_tf.get(r[1], 0) + r[4]
    for tf, n in sorted(by_tf.items()):
        print(f"  {tf:4s}  {n:>10,d} candles")


if __name__ == "__main__":
    main()
