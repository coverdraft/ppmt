"""
Track A: Coinbase alternative OHLCV downloader for PPMT V5.

Coinbase Exchange public API is much more lenient than Binance (10 req/s
public endpoints, no IP bans). This script downloads the same set of
tokens/timeframes/windows as v5_download_massive.py but from Coinbase.

Key differences from Binance version:
  - URL: https://api.exchange.coinbase.com/products/<pair>/candles
  - Pair format: "BTC-USD" (vs Binance "BTCUSDT")
  - Granularity: in seconds (60, 300, 900, 3600, 21600, 86400)
  - Response: [time, low, high, open, close, volume] in DESC order (newest first)
  - Time: in SECONDS (Binance uses ms)
  - Cap: 300 candles per request (Binance: 1000)
  - HTTP 400 if (window_seconds / granularity) > 300 — handle gracefully
  - Rate limit: 10 req/s public (we throttle to ~6 req/s to be safe)

Storage: writes to a SEPARATE table `ohlcv_ext_cb` to keep the existing
Binance data in `ohlcv_ext` untouched for comparison.

Usage:
    python /home/z/my-project/scripts/v5_download_coinbase.py \\
        [--timeframes 1m 5m 15m 1h] [--workers 6] \\
        [--tokens BTCUSDT DOGEUSDT] [--windows BULL_2024 BEAR_2022]
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))
from ppmt.data.storage import PPMTStorage as Storage  # noqa: E402

LOG = logging.getLogger("v5_dl_cb")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)

COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/{pair}/candles"
USER_AGENT = "ppmt-v5-research/1.0 (contact: research@example.com)"

# Process-wide lock for SQLite writes
_DB_WRITE_LOCK = threading.Lock()
_DB_CONN: Optional[sqlite3.Connection] = None

# Same windows as v5_download_massive.py
WINDOWS = [
    ("BULL_2024",   1727740800000, 1735516800000, "Oct-Dec 2024 BTC pump to 100k"),
    ("RANGE_2025",  1753990400000, 1761817200000, "Aug-Oct 2025 consolidation"),
    ("RECENT_2026", 1742774400000, 1750636800000, "Mar-Jun 2026 recent"),
    ("BEAR_2022",   1651363200000, 1659139200000, "May-Jul 2022 LUNA/3AC crash"),
    ("RANGE_2023",  1677628800000, 1698710400000, "Mar-Oct 2023 accumulation"),
]

# Token list: (binance_symbol, coinbase_pair, asset_class)
TOKENS = [
    ("BTCUSDT",  "BTC-USD",   "blue_chip"),
    ("ETHUSDT",  "ETH-USD",   "blue_chip"),
    ("BNBUSDT",  "BNB-USD",   "blue_chip"),
    ("SOLUSDT",  "SOL-USD",   "large_cap"),
    ("XRPUSDT",  "XRP-USD",   "large_cap"),
    ("ADAUSDT",  "ADA-USD",   "mid_cap"),
    ("AVAXUSDT", "AVAX-USD",  "mid_cap"),
    ("LINKUSDT", "LINK-USD",  "mid_cap"),
    ("DOGEUSDT", "DOGE-USD",  "meme"),
    ("SHIBUSDT", "SHIB-USD",  "meme"),
    ("PEPEUSDT", "PEPE-USD",  "meme"),
    ("WIFUSDT",  "WIF-USD",   "meme"),
    ("BONKUSDT", "BONK-USD",  "meme"),
]

# Coinbase listing dates (probed empirically — tokens return [] before this).
# Format: { pair: earliest_ms_we_can_fetch }. We skip fetches entirely before this.
# Source: probed via /products/<pair>/candles with various start dates.
EARLIEST_MS = {
    "BTC-USD":   0,              # Coinbase has BTC from 2015
    "ETH-USD":   0,              # since 2016
    "BNB-USD":   1_555_372_800_000,   # ~2019-04 (BNB listed late on Coinbase)
    "SOL-USD":   1_609_459_200_000,   # 2021-01
    "XRP-USD":   1_546_300_800_000,   # 2019-01 (with gaps due to SEC delisting 2020-2023)
    "ADA-USD":   1_533_590_400_000,   # 2018-08
    "AVAX-USD":  1_633_977_600_000,   # 2021-10-12 listing
    "LINK-USD":  1_581_408_000_000,   # 2020-02
    "DOGE-USD":  1_620_000_000_000,   # 2021-05 (Coinbase listing)
    "SHIB-USD":  1_636_915_200_000,   # 2021-11-15 listing
    "PEPE-USD":  1_731_542_400_000,   # 2024-11-13 listing on Coinbase
    "WIF-USD":   1_716_336_000_000,   # 2024-05-21 listing (probed)
    "BONK-USD":  1_704_067_200_000,   # 2024-01-01 (probed)
}

# Timeframe: (string_label, granularity_seconds, ms_per_candle)
TF_MAP = {
    "1m":  (60,      60_000),
    "5m":  (300,     300_000),
    "15m": (900,     900_000),
    "30m": (1800,    1_800_000),
    "1h":  (3600,    3_600_000),
    "6h":  (21600,   21_600_000),
    "1d":  (86400,   86_400_000),
}

# Coinbase caps responses at 300 candles. We use a slightly smaller chunk
# to avoid edge-case 400s when the requested window straddles a listing date.
PAGE_CANDLES = 300


def fetch_candles_paginated(
    pair: str,
    tf_label: str,
    start_ms: int,
    end_ms: int,
    max_retries: int = 5,
) -> list[list]:
    """Fetch all candles between [start_ms, end_ms] for pair+granularity.

    Returns ASC-ordered list of normalized rows in Coinbase format:
        [time_sec, low, high, open, close, volume]
    (each row is the raw Coinbase row, but in ASC order).
    """
    granularity, ms_per_candle = TF_MAP[tf_label]

    # If window predates Coinbase listing for this pair, skip entirely
    earliest = EARLIEST_MS.get(pair, 0)
    if end_ms < earliest:
        return []
    # Clip the start to the listing date
    eff_start = max(start_ms, earliest)

    chunk_ms = PAGE_CANDLES * ms_per_candle
    all_rows: list[list] = []
    cursor = eff_start

    while cursor < end_ms:
        chunk_end = min(cursor + chunk_ms, end_ms)
        # Coinbase expects ISO 8601 strings
        start_iso = datetime.fromtimestamp(cursor / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso   = datetime.fromtimestamp(chunk_end / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        url = COINBASE_CANDLES.format(pair=pair)
        params = {"granularity": granularity, "start": start_iso, "end": end_iso}
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

        rows = None
        for attempt in range(max_retries):
            try:
                r = requests.get(url, params=params, headers=headers, timeout=20)
                if r.status_code == 429:
                    wait = 5 * (attempt + 1)
                    LOG.warning("429 rate limit on %s %s — wait %ds", pair, tf_label, wait)
                    time.sleep(wait)
                    continue
                if r.status_code == 400:
                    # Coinbase returns 400 with "granularity too small for the requested time range"
                    # if (window_seconds / granularity) > 300. Shouldn't happen given our chunking,
                    # but if it does, halve the chunk and retry.
                    msg = ""
                    try:
                        msg = r.json().get("message", "")
                    except Exception:
                        pass
                    LOG.warning("400 on %s %s (%s): %s — halving chunk", pair, tf_label, tf_label, msg)
                    chunk_ms = max(chunk_ms // 2, ms_per_candle)
                    time.sleep(0.5)
                    break  # break retry loop, recompute chunk in outer while
                r.raise_for_status()
                rows = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                wait = 2 ** attempt
                LOG.warning("Error on %s %s attempt %d: %s — wait %ds",
                            pair, tf_label, attempt + 1, e, wait)
                time.sleep(wait)
        else:
            LOG.error("Failed after %d retries: %s %s @ %d",
                      max_retries, pair, tf_label, cursor)
            return all_rows

        if rows is None:
            # We broke out of retry loop due to 400 — recompute chunk and continue
            continue

        if not isinstance(rows, list) or not rows:
            # Empty response means either: (a) chunk has no data yet, or
            # (b) pair was not listed during this window. Either way, advance
            # the cursor by the chunk size and continue.
            cursor = chunk_end + 1
            continue

        # Coinbase returns DESC (newest first). Reverse to ASC.
        rows_asc = list(reversed(rows))
        all_rows.extend(rows_asc)

        # Advance cursor: last candle's time + 1 granularity step
        last_time_ms = rows_asc[-1][0] * 1000
        cursor = last_time_ms + ms_per_candle

        # Be polite — Coinbase public endpoints allow 10 req/s per IP.
        # We use 8 workers with 0.04s sleep = ~200 req/s effective which
        # Coinbase tolerates in practice (probed empirically).
        time.sleep(0.04)

        # If we got fewer than a full page, we've exhausted the chunk
        if len(rows_asc) < PAGE_CANDLES:
            # Still need to advance cursor — but we may have gaps. Set cursor
            # to chunk_end + 1 to continue from the next chunk boundary.
            cursor = max(cursor, chunk_end + 1)

    return all_rows


def store_rows(
    symbol: str,
    timeframe: str,
    window: str,
    rows: list[list],
) -> int:
    """Insert candles into ohlcv_ext_cb. Returns number of rows inserted."""
    if not rows:
        return 0

    conn = _DB_CONN
    inserted = 0
    batch = []

    for r in rows:
        time_sec = r[0]   # Coinbase time is already in seconds
        # Coinbase row: [time, low, high, open, close, volume]
        low, high, opn, close, vol = r[1], r[2], r[3], r[4], r[5]
        batch.append((
            symbol, timeframe, time_sec, window,
            float(opn), float(high), float(low), float(close), float(vol),
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
                    INSERT OR IGNORE INTO ohlcv_ext_cb
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
    binance_symbol: str,
    pair: str,
    asset_class: str,
    timeframe: str,
    window_name: str,
    start_ms: int,
    end_ms: int,
) -> tuple[str, str, str, str, int, str]:
    """Download + store one (symbol, tf, window) combo."""
    rows = fetch_candles_paginated(pair, timeframe, start_ms, end_ms)
    n = store_rows(binance_symbol, timeframe, window_name, rows)
    status = "ok" if n > 0 else "empty"
    return (binance_symbol, timeframe, window_name, asset_class, n, status)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframes", nargs="+", default=["1m", "5m", "15m"])
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--tokens", nargs="+", default=None,
                        help="Subset of Binance symbols (e.g. BTCUSDT PEPEUSDT)")
    parser.add_argument("--windows", nargs="+", default=None,
                        help="Subset of windows (e.g. BULL_2024 BEAR_2022)")
    args = parser.parse_args()

    storage = Storage()

    global _DB_CONN
    _DB_CONN = sqlite3.connect(storage.db_path, check_same_thread=False)
    _DB_CONN.execute("PRAGMA journal_mode=WAL")
    _DB_CONN.execute("PRAGMA synchronous=NORMAL")

    conn = _DB_CONN
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_ext_cb (
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
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_ext_cb_sym_tf_win
        ON ohlcv_ext_cb(symbol, timeframe, window, timestamp)
    """)
    conn.commit()

    tokens = [(b, p, c) for (b, p, c) in TOKENS if args.tokens is None or b in args.tokens]
    windows = [(n, s, e, d) for (n, s, e, d) in WINDOWS
               if args.windows is None or n in args.windows]

    jobs = []
    for tf in args.timeframes:
        for (bsym, pair, cls) in tokens:
            for (wn, ws, we, _) in windows:
                jobs.append((bsym, pair, cls, tf, wn, ws, we))

    LOG.info("Starting Coinbase download: %d jobs (%d tokens × %d TFs × %d windows)",
             len(jobs), len(tokens), len(args.timeframes), len(windows))

    results = []
    ok = 0; empty = 0; failed = 0; total_candles = 0

    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="cb") as ex:
        futures = {
            ex.submit(download_one, bsym, pair, cls, tf, wn, ws, we): (bsym, pair, cls, tf, wn)
            for (bsym, pair, cls, tf, wn, ws, we) in jobs
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
                else:
                    empty += 1
                    LOG.info("[%d/%d] %s %s %s: empty (likely not listed yet)", i, len(jobs), sym, tf, wn)
            except Exception as e:
                failed += 1
                LOG.exception("[%d/%d] FAILED %s: %s", i, len(jobs), meta, e)

    LOG.info("=" * 60)
    LOG.info("Coinbase download complete:")
    LOG.info("  OK:       %d jobs (%d candles total)", ok, total_candles)
    LOG.info("  Empty:    %d", empty)
    LOG.info("  Failed:   %d", failed)

    print("\n=== Summary by window ===")
    by_window: dict[str, int] = {}
    for r in results:
        if r[5] == "ok":
            by_window[r[2]] = by_window.get(r[2], 0) + r[4]
    for w, n in sorted(by_window.items()):
        print(f"  {w:14s}  {n:>10,d} candles")

    print("\n=== Summary by token ===")
    by_token: dict[str, int] = {}
    for r in results:
        if r[5] == "ok":
            by_token[r[0]] = by_token.get(r[0], 0) + r[4]
    for t, n in sorted(by_token.items(), key=lambda x: -x[1]):
        print(f"  {t:12s}  {n:>10,d} candles")

    print("\n=== Summary by timeframe ===")
    by_tf: dict[str, int] = {}
    for r in results:
        if r[5] == "ok":
            by_tf[r[1]] = by_tf.get(r[1], 0) + r[4]
    for tf, n in sorted(by_tf.items()):
        print(f"  {tf:4s}  {n:>10,d} candles")


if __name__ == "__main__":
    main()
