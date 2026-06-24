"""
v6_download_ohlcv.py — Download OHLCV from Coinbase Exchange for v6.

v6 design choices:
  - 5m timeframe ONLY (cleaner than mixing TFs)
  - 12 tokens (drop BNB, no PPMT use case)
  - 4 windows: BEAR_2022, BULL_2024, RANGE_2025, RECENT_2026
  - Single new table `ohlcv_v6` (do not reuse ohlcv_ext_cb — fresh start
    after the leakage postmortem)

API: https://api.exchange.coinbase.com/products/<pair>/candles
  - 300 candles/page, DESC order, time in SECONDS
  - 10 req/s public, we throttle to ~6 req/s
  - HTTP 400 if window_seconds/granularity > 300 (handled by chunking)

Storage: SQLite at /home/z/my-project/data/ppmt.db (env override PPMT_DB_PATH)
State: /home/z/my-project/data/v6_download_state.json (per-pair resume)

Usage:
    python /home/z/my-project/scripts/v6/v6_download_ohlcv.py
    python /home/z/my-project/scripts/v6/v6_download_ohlcv.py --tokens BTCUSDT ETHUSDT
    python /home/z/my-project/scripts/v6/v6_download_ohlcv.py --windows BULL_2024
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

LOG = logging.getLogger("v6_dl")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

DB_PATH = os.environ.get("PPMT_DB_PATH", "/home/z/my-project/data/ppmt.db")
STATE_FILE = Path("/home/z/my-project/data/v6_download_state.json")

COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/{pair}/candles"
USER_AGENT = "ppmt-v6-research/1.0 (contact: research@example.com)"

# (binance_symbol, coinbase_pair, asset_class)
TOKENS = [
    ("BTCUSDT",  "BTC-USD",   "blue_chip"),
    ("ETHUSDT",  "ETH-USD",   "blue_chip"),
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

# Same windows as v5_cb_v2 used (consistency).
# (name, start_ms, end_ms, description)
WINDOWS = [
    # Historical bears (added for SHORT expert — needs more bear data)
    ("BEAR_2018_Q2_Q4", 1522540800000, 1546300800000, "Apr-Dec 2018 crypto bear (post-Dec 2017 peak)"),
    ("BEAR_2019_Q1",    1546300800000, 1554076800000, "Jan-Mar 2019 continuation of bear"),
    ("BEAR_2020_COVID", 1580515200000, 1588291200000, "Feb-Apr 2020 COVID crash"),
    # Original v6 windows
    ("BEAR_2022",   1651363200000, 1659139200000, "May-Jul 2022 LUNA/3AC crash"),
    ("BULL_2024",   1727740800000, 1735516800000, "Oct-Dec 2024 BTC pump to 100k"),
    ("RANGE_2025",  1753990400000, 1761817200000, "Aug-Oct 2025 consolidation"),
    ("RECENT_2026", 1742774400000, 1750636800000, "Mar-Jun 2026 recent"),
]

# Coinbase listing dates (probed empirically). Skip fetches before this.
EARLIEST_MS = {
    "BTC-USD":   0,
    "ETH-USD":   0,
    "SOL-USD":   1_609_459_200_000,   # 2021-01
    "XRP-USD":   1_519_776_000_000,   # 2018-02-28 (Coinbase Pro listing; gaps during SEC delisting Dec 2020)
    "ADA-USD":   1_616_015_200_000,   # 2021-03-18 (ADA-USD pair launch on Coinbase Pro)
    "AVAX-USD":  1_633_977_600_000,   # 2021-10
    "LINK-USD":  1_581_408_000_000,   # 2020-02
    "DOGE-USD":  1_620_000_000_000,   # 2021-05
    "SHIB-USD":  1_636_915_200_000,   # 2021-11
    "PEPE-USD":  1_731_542_400_000,   # 2024-11
    "WIF-USD":   1_716_336_000_000,   # 2024-05
    "BONK-USD":  1_704_067_200_000,   # 2024-01
}

# v6 design: 5m only. (granularity_seconds, ms_per_candle)
TF_5M = (300, 300_000)
PAGE_CANDLES = 300  # Coinbase hard cap

# ----------------------------------------------------------------------------
# DB
# ----------------------------------------------------------------------------

_DB_WRITE_LOCK = threading.Lock()
_DB_CONN: Optional[sqlite3.Connection] = None


def ensure_db():
    global _DB_CONN
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    _DB_CONN = sqlite3.connect(DB_PATH, check_same_thread=False)
    _DB_CONN.execute("PRAGMA journal_mode=WAL")
    _DB_CONN.execute("PRAGMA synchronous=NORMAL")
    _DB_CONN.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_v6 (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            window TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            UNIQUE(symbol, timeframe, timestamp, window)
        )
    """)
    _DB_CONN.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_v6_sym_tf_win
        ON ohlcv_v6(symbol, timeframe, window, timestamp)
    """)
    _DB_CONN.commit()


# ----------------------------------------------------------------------------
# Coinbase fetch
# ----------------------------------------------------------------------------

def fetch_candles_paginated(
    pair: str,
    start_ms: int,
    end_ms: int,
    max_retries: int = 5,
) -> list[list]:
    """Fetch all 5m candles between [start_ms, end_ms] for pair.

    Returns ASC-ordered list of normalized rows: [time_sec, low, high, open, close, volume]
    """
    granularity, ms_per_candle = TF_5M

    earliest = EARLIEST_MS.get(pair, 0)
    if end_ms < earliest:
        return []
    eff_start = max(start_ms, earliest)

    chunk_ms = PAGE_CANDLES * ms_per_candle
    all_rows: list[list] = []
    cursor = eff_start

    while cursor < end_ms:
        chunk_end = min(cursor + chunk_ms, end_ms)
        start_iso = datetime.fromtimestamp(cursor / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = datetime.fromtimestamp(chunk_end / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        url = COINBASE_CANDLES.format(pair=pair)
        params = {"granularity": granularity, "start": start_iso, "end": end_iso}
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

        rows = None
        for attempt in range(max_retries):
            try:
                r = requests.get(url, params=params, headers=headers, timeout=20)
                if r.status_code == 429:
                    wait = 5 * (attempt + 1)
                    LOG.warning("429 rate limit on %s — wait %ds", pair, wait)
                    time.sleep(wait)
                    continue
                if r.status_code == 400:
                    msg = ""
                    try:
                        msg = r.json().get("message", "")
                    except Exception:
                        pass
                    LOG.warning("400 on %s: %s — halving chunk", pair, msg)
                    chunk_ms = max(chunk_ms // 2, ms_per_candle)
                    time.sleep(0.5)
                    break
                r.raise_for_status()
                rows = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                wait = 2 ** attempt
                LOG.warning("Error on %s attempt %d: %s — wait %ds", pair, attempt + 1, e, wait)
                time.sleep(wait)
        else:
            LOG.error("Failed after %d retries: %s @ %d", max_retries, pair, cursor)
            return all_rows

        if rows is None:
            continue

        if not isinstance(rows, list) or not rows:
            cursor = chunk_end + 1
            continue

        # Coinbase returns DESC (newest first). Reverse to ASC.
        rows_asc = list(reversed(rows))
        all_rows.extend(rows_asc)

        last_time_ms = rows_asc[-1][0] * 1000
        cursor = last_time_ms + ms_per_candle
        time.sleep(0.04)  # ~6 req/s sustained

        if len(rows_asc) < PAGE_CANDLES:
            cursor = max(cursor, chunk_end + 1)

    return all_rows


def store_rows(symbol: str, window: str, rows: list[list]) -> int:
    if not rows:
        return 0
    conn = _DB_CONN
    inserted = 0
    batch = []
    for r in rows:
        time_sec = r[0]
        low, high, opn, close, vol = r[1], r[2], r[3], r[4], r[5]
        batch.append((symbol, "5m", time_sec, window,
                      float(opn), float(high), float(low), float(close), float(vol)))
    CHUNK = 500
    for i in range(0, len(batch), CHUNK):
        chunk = batch[i:i + CHUNK]
        placeholders = ",".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(chunk))
        flat = [v for row in chunk for v in row]
        try:
            with _DB_WRITE_LOCK:
                conn.execute(
                    f"INSERT OR IGNORE INTO ohlcv_v6 "
                    f"(symbol, timeframe, timestamp, window, open, high, low, close, volume) "
                    f"VALUES {placeholders}",
                    flat,
                )
                conn.commit()
            inserted += len(chunk)
        except Exception as e:
            LOG.error("DB insert failed for %s %s: %s", symbol, window, e)
    return inserted


# ----------------------------------------------------------------------------
# State (for resume across short-lived invocations)
# ----------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"completed": [], "failed": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def download_one(binance_symbol: str, pair: str, window_name: str, start_ms: int, end_ms: int) -> dict:
    t0 = time.time()
    try:
        rows = fetch_candles_paginated(pair, start_ms, end_ms)
        n = store_rows(binance_symbol, window_name, rows)
        return {
            "symbol": binance_symbol, "pair": pair, "window": window_name,
            "rows": n, "elapsed_s": round(time.time() - t0, 1), "status": "ok" if n else "empty",
        }
    except Exception as e:
        return {
            "symbol": binance_symbol, "pair": pair, "window": window_name,
            "rows": 0, "elapsed_s": round(time.time() - t0, 1), "status": f"error: {e}",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokens", nargs="+", default=None,
                        help="Subset of binance symbols (e.g. BTCUSDT PEPEUSDT)")
    parser.add_argument("--windows", nargs="+", default=None,
                        help="Subset of windows (e.g. BULL_2024 BEAR_2022)")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--max-seconds", type=int, default=110,
                        help="Soft time budget for this invocation (env rotation aware)")
    args = parser.parse_args()

    ensure_db()

    # Filter tokens
    tokens = [t for t in TOKENS if args.tokens is None or t[0] in args.tokens]
    windows = [w for w in WINDOWS if args.windows is None or w[0] in args.windows]

    LOG.info("v6 OHLCV downloader: %d tokens x %d windows = %d jobs",
             len(tokens), len(windows), len(tokens) * len(windows))

    state = load_state()
    done_keys = {f"{r['symbol']}|{r['window']}" for r in state["completed"]}
    # Failed jobs are retried (not permanently marked failed)
    done_keys |= {f"{r['symbol']}|{r['window']}" for r in state.get("failed", []) if r.get("status", "").startswith("error")}

    jobs = []
    for sym, pair, _cls in tokens:
        for w_name, w_start, w_end, _desc in windows:
            key = f"{sym}|{w_name}"
            if key in done_keys:
                continue
            jobs.append((sym, pair, w_name, w_start, w_end))

    LOG.info("Remaining jobs: %d (after resume filter)", len(jobs))
    if not jobs:
        # Final summary
        cur = _DB_CONN.cursor()
        cur.execute("SELECT COUNT(*) FROM ohlcv_v6")
        total = cur.fetchone()[0]
        cur.execute("SELECT symbol, window, COUNT(*) FROM ohlcv_v6 GROUP BY symbol, window ORDER BY symbol, window")
        per = cur.fetchall()
        print(f"\n=== v6 OHLCV FINAL ===")
        print(f"Total rows: {total:,}")
        for sym, win, n in per:
            print(f"  {sym:10} {win:12} {n:>6,}")
        return 0

    t_start = time.time()
    n_done = 0
    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="w") as ex:
        futs = {ex.submit(download_one, *job): job for job in jobs}
        for fut in as_completed(futs):
            if time.time() - t_start > args.max_seconds:
                LOG.info("Hit max_seconds=%d, exiting cleanly", args.max_seconds)
                # Cancel remaining
                for f in futs:
                    f.cancel()
                break
            res = fut.result()
            n_done += 1
            LOG.info("[%d/%d] %s %s: %d rows in %.1fs (%s)",
                     n_done, len(jobs), res["symbol"], res["window"],
                     res["rows"], res["elapsed_s"], res["status"])
            if res["status"] == "ok" or res["status"] == "empty":
                state["completed"].append({
                    "symbol": res["symbol"], "window": res["window"], "rows": res["rows"],
                })
            else:
                state["failed"].append({
                    "symbol": res["symbol"], "window": res["window"], "status": res["status"],
                })
            save_state(state)

    LOG.info("Processed %d/%d jobs in %.1fs", n_done, len(jobs), time.time() - t_start)

    cur = _DB_CONN.cursor()
    cur.execute("SELECT COUNT(*) FROM ohlcv_v6")
    print(f"\n=== v6 OHLCV progress ===")
    print(f"Total rows so far: {cur.fetchone()[0]:,}")
    cur.execute("SELECT window, COUNT(*) FROM ohlcv_v6 GROUP BY window ORDER BY window")
    for w, n in cur.fetchall():
        print(f"  {w:12} {n:>8,}")
    return 0 if n_done == len(jobs) else 2  # 2 = partial


if __name__ == "__main__":
    sys.exit(main())
