"""Download a single (symbol, timeframe, window) combo with chunked progress.
For very long windows (e.g. RANGE_2023 = 8 months), we split the time
range into smaller chunks (e.g. 20 days each) and download each chunk
separately. This way each chunk completes in <90s and we can resume
across multiple tool calls.

Usage:
    python v5_download_chunked.py --symbol BTCUSDT --timeframe 1m --window RANGE_2023 --chunk-days 20
"""
from __future__ import annotations
import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))
from ppmt.data.storage import PPMTStorage as Storage  # noqa: E402

# Import the helpers from the main Coinbase downloader
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v5_download_coinbase as cb  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("chunk_dl")


def ensure_db():
    storage = Storage()
    if cb._DB_CONN is None:
        cb._DB_CONN = sqlite3.connect(storage.db_path, check_same_thread=False)
        cb._DB_CONN.execute("PRAGMA journal_mode=WAL")
        cb._DB_CONN.execute("PRAGMA synchronous=NORMAL")
        conn = cb._DB_CONN
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv_ext_cb (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                timestamp INTEGER NOT NULL, window TEXT NOT NULL DEFAULT '',
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                close REAL NOT NULL, volume REAL NOT NULL,
                UNIQUE(symbol, timeframe, timestamp, window)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ohlcv_ext_cb_sym_tf_win
            ON ohlcv_ext_cb(symbol, timeframe, window, timestamp)
        """)
        conn.commit()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--timeframe", required=True)
    p.add_argument("--window", required=True)
    p.add_argument("--chunk-days", type=int, default=15)
    p.add_argument("--max-chunks", type=int, default=0,
                   help="If >0, only download this many chunks then exit (for resuming)")
    args = p.parse_args()

    pair = None
    for b, p_, c in cb.TOKENS:
        if b == args.symbol:
            pair = p_
            break
    if pair is None:
        LOG.error(f"Unknown symbol: {args.symbol}")
        sys.exit(1)

    win = None
    for n, s, e, d in cb.WINDOWS:
        if n == args.window:
            win = (n, s, e, d)
            break
    if win is None:
        LOG.error(f"Unknown window: {args.window}")
        sys.exit(1)

    ensure_db()

    wn, ws, we, _ = win
    chunk_ms = args.chunk_days * 86400 * 1000

    total = 0
    cursor = ws
    chunk_idx = 1
    n_chunks = (we - ws + chunk_ms - 1) // chunk_ms
    LOG.info(f"Start {args.symbol} {args.timeframe} {wn}: {n_chunks} chunks of {args.chunk_days}d")
    while cursor < we:
        if args.max_chunks > 0 and chunk_idx > args.max_chunks:
            LOG.info(f"Reached max_chunks={args.max_chunks}, stopping (will resume next call)")
            break
        chunk_end = min(cursor + chunk_ms, we)
        rows = cb.fetch_candles_paginated(pair, args.timeframe, cursor, chunk_end)
        n = cb.store_rows(args.symbol, args.timeframe, wn, rows)
        total += n
        from datetime import datetime, timezone
        cs = datetime.fromtimestamp(cursor//1000, tz=timezone.utc).strftime("%Y-%m-%d")
        ce = datetime.fromtimestamp(chunk_end//1000, tz=timezone.utc).strftime("%Y-%m-%d")
        LOG.info(f"chunk {chunk_idx}/{n_chunks} {cs}→{ce}: +{n:,} (total {total:,})")
        cursor = chunk_end
        chunk_idx += 1

    LOG.info(f"DONE {args.symbol} {args.timeframe} {wn}: {total:,} candles")


if __name__ == "__main__":
    main()
