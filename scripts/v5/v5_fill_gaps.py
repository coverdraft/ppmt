#!/usr/bin/env python3
"""
v5_fill_gaps.py — Targeted Coinbase download to fill known gaps.

Strategy:
  1. Query ohlcv_ext_cb to find (window, tf, sym) combos with <95% coverage
  2. Skip combos that are impossible per Coinbase listing dates
     (BNB never had a stable BNB-USD pair on Coinbase; BONK/PEPE/WIF/ShIB
      were listed later than some windows; XRP was delisted 2020-07 → 2023-07)
  3. Re-download only those combos, using INSERT OR IGNORE to dedupe
  4. Print coverage before and after

This script is safe to re-run; it only adds data, never deletes.
"""
from __future__ import annotations

import datetime
import json
import logging
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# Reuse the proven download logic from v5_download_coinbase.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v5_download_coinbase import (
    WINDOWS, TOKENS, TF_MAP, EARLIEST_MS, PAGE_CANDLES,
    fetch_candles_paginated,
)

LOG = logging.getLogger("v5_fill_gaps")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)

DB = "/home/z/my-project/data/ppmt.db"

# Tokens that should be excluded entirely (never available on Coinbase)
EXCLUDE_SYMS = {"BNBUSDT"}

# Expected candles per day per TF
CPD = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1h": 24}
TARGET_TFS = ["1m", "5m", "15m"]

# Threshold below which we re-fetch a combo
COVERAGE_THRESHOLD = 0.95

_DB_WRITE_LOCK = threading.Lock()
_DB_CONN: sqlite3.Connection | None = None


def window_days(start_ms: int, end_ms: int) -> int:
    """Number of days in the window."""
    return max(1, (end_ms - start_ms) // 86_400_000)


def is_combo_fetchable(sym: str, pair: str, start_ms: int, end_ms: int) -> bool:
    """Return True iff the token was listed on Coinbase during the window."""
    earliest = EARLIEST_MS.get(pair, 0)
    # If window ends before listing, no data is available
    if end_ms < earliest:
        return False
    # If window starts before listing, only the part after listing is fetchable
    # (still worth fetching)
    return True


def get_current_coverage(cur, sym: str, tf: str, window: str) -> tuple[int, int]:
    """Return (current_count, expected_count) for the (sym, tf, window)."""
    cur.execute(
        "SELECT COUNT(*) FROM ohlcv_ext_cb "
        "WHERE symbol=? AND timeframe=? AND window=?",
        (sym, tf, window),
    )
    cnt = cur.fetchone()[0]
    # Find window bounds
    win_dict = {w[0]: (w[1], w[2]) for w in WINDOWS}
    if window not in win_dict:
        return cnt, 0
    start_ms, end_ms = win_dict[window]
    expected = window_days(start_ms, end_ms) * CPD.get(tf, 0)
    return cnt, expected


def store_rows(symbol: str, timeframe: str, window: str, rows: list[list]) -> int:
    if not rows:
        return 0
    conn = _DB_CONN
    inserted = 0
    batch = []
    for r in rows:
        time_sec = r[0]
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
                    f"INSERT OR IGNORE INTO ohlcv_ext_cb "
                    f"(symbol, timeframe, timestamp, window, open, high, low, close, volume) "
                    f"VALUES {placeholders}",
                    flat,
                )
                conn.commit()
            inserted += len(chunk)
        except Exception as e:
            LOG.error("DB insert failed for %s %s %s: %s", symbol, timeframe, window, e)
    return inserted


def fetch_one(job: dict) -> dict:
    """Download + store one combo. Returns result dict."""
    sym = job["sym"]
    pair = job["pair"]
    tf = job["tf"]
    window = job["window"]
    start_ms = job["start_ms"]
    end_ms = job["end_ms"]

    # Clip start to listing date
    earliest = EARLIEST_MS.get(pair, 0)
    eff_start = max(start_ms, earliest)
    if eff_start >= end_ms:
        return {**job, "status": "skip_not_listed", "fetched": 0, "inserted": 0}

    t0 = time.time()
    rows = fetch_candles_paginated(pair, tf, eff_start, end_ms, max_retries=5)
    inserted = store_rows(sym, tf, window, rows)
    elapsed = time.time() - t0
    return {
        **job,
        "status": "ok" if inserted > 0 else "empty",
        "fetched": len(rows),
        "inserted": inserted,
        "elapsed_sec": round(elapsed, 1),
    }


def main():
    global _DB_CONN
    _DB_CONN = sqlite3.connect(DB, check_same_thread=False)
    _DB_CONN.execute("PRAGMA journal_mode=WAL")
    _DB_CONN.execute("PRAGMA synchronous=NORMAL")
    cur = _DB_CONN.cursor()

    # 1) Find all gaps
    LOG.info("Scanning current DB for gaps…")
    jobs = []
    skip_impossible = 0
    skip_complete = 0

    for (bsym, pair, cls) in TOKENS:
        if bsym in EXCLUDE_SYMS:
            continue
        for tf in TARGET_TFS:
            for (wn, ws, we, _desc) in WINDOWS:
                cnt, expected = get_current_coverage(cur, bsym, tf, wn)
                if expected == 0:
                    continue
                cov = cnt / expected if expected else 0
                if cov >= COVERAGE_THRESHOLD:
                    skip_complete += 1
                    continue
                # Check if combo is fetchable at all
                if not is_combo_fetchable(bsym, pair, ws, we):
                    skip_impossible += 1
                    continue
                jobs.append({
                    "sym": bsym, "pair": pair, "tf": tf, "window": wn,
                    "start_ms": ws, "end_ms": we,
                    "current_count": cnt, "expected": expected, "coverage": round(cov, 3),
                })

    LOG.info("Found %d jobs to run", len(jobs))
    LOG.info("  Skipped (already ≥95%%): %d", skip_complete)
    LOG.info("  Skipped (impossible — pre-listing): %d", skip_impossible)

    if not jobs:
        LOG.info("Nothing to do.")
        return

    # Print job list
    print("\nJobs to fetch:")
    print(f"{'Window':<14} {'TF':<5} {'Sym':<10} {'Have':<10} {'Expected':<10} {'Cov':<6}")
    for j in jobs:
        print(f"{j['window']:<14} {j['tf']:<5} {j['sym']:<10} "
              f"{j['current_count']:<10,} {j['expected']:<10,} {j['coverage']*100:.1f}%")
    print()

    # Save job list
    Path("/home/z/my-project/download/gap_jobs.json").write_text(
        json.dumps(jobs, indent=2)
    )

    # 2) Run in parallel
    n_workers = min(8, len(jobs))
    LOG.info("Launching %d workers for %d jobs", n_workers, len(jobs))

    results = []
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="gap") as ex:
        futures = {ex.submit(fetch_one, j): j for j in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            meta = futures[fut]
            try:
                res = fut.result()
                results.append(res)
                LOG.info(
                    "[%d/%d] %s %s %s: fetched=%d inserted=%d (%.1fs, status=%s)",
                    i, len(jobs), res["sym"], res["tf"], res["window"],
                    res.get("fetched", 0), res.get("inserted", 0),
                    res.get("elapsed_sec", 0), res["status"],
                )
            except Exception as e:
                LOG.exception("FAILED %s: %s", meta, e)
                results.append({**meta, "status": "error", "error": str(e)})

    elapsed = time.time() - t_start
    LOG.info("=" * 60)
    LOG.info("Done in %.1fs", elapsed)

    # 3) Final summary
    total_inserted = sum(r.get("inserted", 0) for r in results)
    ok = sum(1 for r in results if r["status"] == "ok")
    empty = sum(1 for r in results if r["status"] == "empty")
    err = sum(1 for r in results if r["status"] == "error")
    skip = sum(1 for r in results if r["status"] == "skip_not_listed")

    print("\n" + "=" * 60)
    print(f"RESULTS — {len(results)} jobs in {elapsed:.1f}s")
    print(f"  OK:       {ok}")
    print(f"  Empty:    {empty}")
    print(f"  Skipped:  {skip}")
    print(f"  Errors:   {err}")
    print(f"  Total new candles inserted: {total_inserted:,}")
    print("=" * 60)

    # 4) Verify new coverage
    print("\nNew coverage (only jobs that ran):")
    print(f"{'Window':<14} {'TF':<5} {'Sym':<10} {'Before':<10} {'After':<10} {'Expected':<10} {'Cov%':<6}")
    for r in results:
        if r["status"] not in ("ok", "empty"):
            continue
        cur.execute(
            "SELECT COUNT(*) FROM ohlcv_ext_cb "
            "WHERE symbol=? AND timeframe=? AND window=?",
            (r["sym"], r["tf"], r["window"]),
        )
        new_cnt = cur.fetchone()[0]
        cov = 100 * new_cnt / r["expected"] if r["expected"] else 0
        print(f"{r['window']:<14} {r['tf']:<5} {r['sym']:<10} "
              f"{r['current_count']:<10,} {new_cnt:<10,} {r['expected']:<10,} {cov:.1f}%")

    # Save results
    Path("/home/z/my-project/download/gap_results.json").write_text(
        json.dumps(results, indent=2, default=str)
    )
    LOG.info("Results saved to /home/z/my-project/download/gap_results.json")

    _DB_CONN.close()


if __name__ == "__main__":
    main()
