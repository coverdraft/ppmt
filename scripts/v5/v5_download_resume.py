"""Resume-aware chunked downloader.

For each (symbol, timeframe, window), checks the DB for the highest
timestamp already stored and only downloads chunks AFTER that timestamp.
This allows resuming across multiple tool calls.
"""
from __future__ import annotations
import argparse
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))
from ppmt.data.storage import PPMTStorage as Storage  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v5_download_coinbase as cb  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("resume_dl")


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


def get_resume_cursor(symbol, timeframe, window):
    """Return the highest timestamp (in ms) already stored, or None."""
    conn = cb._DB_CONN
    row = conn.execute("""
        SELECT MAX(timestamp) FROM ohlcv_ext_cb
        WHERE symbol=? AND timeframe=? AND window=?
    """, (symbol, timeframe, window)).fetchone()
    if row and row[0]:
        # timestamp is in seconds in DB; convert to ms
        return int(row[0]) * 1000
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--timeframe", required=True)
    p.add_argument("--window", required=True)
    p.add_argument("--chunk-days", type=int, default=15)
    p.add_argument("--time-budget-sec", type=int, default=80,
                   help="Stop after this many seconds (use to fit tool timeout)")
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

    import time
    start_time = time.time()

    wn, ws, we, _ = win
    chunk_ms = args.chunk_days * 86400 * 1000

    # Resume from highest timestamp if any
    resume_at = get_resume_cursor(args.symbol, args.timeframe, wn)
    if resume_at and resume_at > ws:
        LOG.info(f"Resuming from {resume_at} (already had data up to here)")
        cursor = resume_at + 1  # avoid re-fetching last second
    else:
        cursor = ws

    total = 0
    chunk_idx = 1
    n_chunks = (we - cursor + chunk_ms - 1) // chunk_ms
    LOG.info(f"Start {args.symbol} {args.timeframe} {wn}: {n_chunks} chunks remaining of {args.chunk_days}d each")

    from datetime import datetime, timezone
    while cursor < we:
        elapsed = time.time() - start_time
        if elapsed > args.time_budget_sec:
            LOG.info(f"Time budget {args.time_budget_sec}s reached, stopping (will resume next call). Total this run: {total:,}")
            break
        chunk_end = min(cursor + chunk_ms, we)
        rows = cb.fetch_candles_paginated(pair, args.timeframe, cursor, chunk_end)
        n = cb.store_rows(args.symbol, args.timeframe, wn, rows)
        total += n
        cs = datetime.fromtimestamp(cursor//1000, tz=timezone.utc).strftime("%Y-%m-%d")
        ce = datetime.fromtimestamp(chunk_end//1000, tz=timezone.utc).strftime("%Y-%m-%d")
        LOG.info(f"chunk {chunk_idx}/{n_chunks} {cs}→{ce}: +{n:,} (run total {total:,}, elapsed {elapsed:.0f}s)")
        cursor = chunk_end
        chunk_idx += 1

    LOG.info(f"Run done {args.symbol} {args.timeframe} {wn}: +{total:,} candles this run")


if __name__ == "__main__":
    main()
