#!/usr/bin/env python3
"""
v5_fill_one.py — Process ONE gap job per invocation, with checkpointing.

Reads job list from download/gap_jobs.json (created by v5_fill_gaps.py).
Picks the next unfinished job, runs it, marks it done, exits.

Safe to re-invoke many times. Survives shell kills because each call
is short-lived.

Usage:
    python scripts/v5_fill_one.py              # process next job
    python scripts/v5_fill_one.py --all        # loop until all done (long-running)
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import threading
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v5_download_coinbase import (
    WINDOWS, TOKENS, TF_MAP, EARLIEST_MS, PAGE_CANDLES,
    fetch_candles_paginated,
)

LOG = logging.getLogger("v5_fill_one")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)

DB = "/home/z/my-project/data/ppmt.db"
JOBS_FILE = "/home/z/my-project/download/gap_jobs.json"
PROGRESS_FILE = "/home/z/my-project/download/gap_progress.json"

EXCLUDE_SYMS = {"BNBUSDT"}
CPD = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1h": 24}
TARGET_TFS = ["1m", "5m", "15m"]
COVERAGE_THRESHOLD = 0.95

_DB_WRITE_LOCK = threading.Lock()


def window_days(start_ms: int, end_ms: int) -> int:
    return max(1, (end_ms - start_ms) // 86_400_000)


def is_combo_fetchable(pair: str, start_ms: int, end_ms: int) -> bool:
    earliest = EARLIEST_MS.get(pair, 0)
    if end_ms < earliest:
        return False
    return True


def build_job_list(conn: sqlite3.Connection) -> list[dict]:
    """Find all gaps and return the job list."""
    cur = conn.cursor()
    jobs = []
    for (bsym, pair, cls) in TOKENS:
        if bsym in EXCLUDE_SYMS:
            continue
        for tf in TARGET_TFS:
            for (wn, ws, we, _desc) in WINDOWS:
                # Get current count
                cur.execute(
                    "SELECT COUNT(*) FROM ohlcv_ext_cb "
                    "WHERE symbol=? AND timeframe=? AND window=?",
                    (bsym, tf, wn),
                )
                cnt = cur.fetchone()[0]
                expected = window_days(ws, we) * CPD.get(tf, 0)
                if expected == 0:
                    continue
                cov = cnt / expected if expected else 0
                if cov >= COVERAGE_THRESHOLD:
                    continue
                if not is_combo_fetchable(pair, ws, we):
                    continue
                jobs.append({
                    "sym": bsym, "pair": pair, "tf": tf, "window": wn,
                    "start_ms": ws, "end_ms": we,
                    "current_count": cnt, "expected": expected, "coverage": round(cov, 3),
                })
    return jobs


def load_progress() -> dict:
    if Path(PROGRESS_FILE).exists():
        return json.loads(Path(PROGRESS_FILE).read_text())
    return {"completed": [], "failed": [], "skipped": []}


def save_progress(prog: dict) -> None:
    Path(PROGRESS_FILE).write_text(json.dumps(prog, indent=2))


def store_rows(conn, symbol, timeframe, window, rows):
    if not rows:
        return 0
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


def run_one_job(conn, job: dict) -> dict:
    """Execute one download job. Returns result dict."""
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

    LOG.info("→ Fetching %s %s %s (have %d/%d = %.1f%%)",
             sym, tf, window, job["current_count"], job["expected"], job["coverage"] * 100)

    t0 = time.time()
    rows = fetch_candles_paginated(pair, tf, eff_start, end_ms, max_retries=5)
    inserted = store_rows(conn, sym, tf, window, rows)
    elapsed = time.time() - t0

    # Get new count
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM ohlcv_ext_cb WHERE symbol=? AND timeframe=? AND window=?",
        (sym, tf, window),
    )
    new_cnt = cur.fetchone()[0]
    new_cov = new_cnt / job["expected"] if job["expected"] else 0

    LOG.info("✓ %s %s %s: fetched=%d inserted=%d new_total=%d (%.1f%%) in %.1fs",
             sym, tf, window, len(rows), inserted, new_cnt, new_cov * 100, elapsed)

    return {
        **job,
        "status": "ok" if inserted > 0 else "empty",
        "fetched": len(rows),
        "inserted": inserted,
        "new_count": new_cnt,
        "new_coverage": round(new_cov, 3),
        "elapsed_sec": round(elapsed, 1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true",
                        help="Loop through all jobs (long-running)")
    parser.add_argument("--max-jobs", type=int, default=1,
                        help="Max jobs to process in this invocation (default 1)")
    parser.add_argument("--max-seconds", type=int, default=120,
                        help="Stop after this many seconds (default 120)")
    parser.add_argument("--only-tf", type=str, default=None,
                        help="Only process this TF (1m, 5m, 15m)")
    parser.add_argument("--only-sym", type=str, default=None,
                        help="Only process this symbol")
    args = parser.parse_args()

    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # 1) Build fresh job list (current state of DB)
    LOG.info("Scanning DB for current gaps…")
    jobs = build_job_list(conn)
    LOG.info("Found %d jobs total", len(jobs))

    # Save job list
    Path(JOBS_FILE).write_text(json.dumps(jobs, indent=2))

    # 2) Load progress
    prog = load_progress()
    done_keys = set()
    for r in prog.get("completed", []):
        done_keys.add(f"{r['sym']}|{r['tf']}|{r['window']}")
    for r in prog.get("skipped", []):
        done_keys.add(f"{r['sym']}|{r['tf']}|{r['window']}")
    for r in prog.get("failed", []):
        # Allow retries on failed — but only after 3 attempts
        attempts = sum(1 for f in prog["failed"] if f"{f['sym']}|{f['tf']}|{f['window']}"
                       == f"{r['sym']}|{r['tf']}|{r['window']}")
        if attempts >= 3:
            done_keys.add(f"{r['sym']}|{r['tf']}|{r['window']}")

    # 3) Filter jobs
    remaining = []
    for j in jobs:
        key = f"{j['sym']}|{j['tf']}|{j['window']}"
        if key in done_keys:
            continue
        if args.only_tf and j["tf"] != args.only_tf:
            continue
        if args.only_sym and j["sym"] != args.only_sym:
            continue
        remaining.append(j)

    LOG.info("Remaining: %d jobs (after progress filter)", len(remaining))

    if not remaining:
        LOG.info("All jobs done. ✓")
        # Print final summary
        print("\n=== FINAL SUMMARY ===")
        print(f"  Completed: {len(prog.get('completed', []))}")
        print(f"  Skipped (impossible): {len(prog.get('skipped', []))}")
        print(f"  Failed: {len(prog.get('failed', []))}")
        return

    # 4) Sort jobs by size (smallest first)
    def job_size(j):
        # Estimate work: number of candles to fetch
        gap = max(0, j["expected"] - j["current_count"])
        return gap
    remaining.sort(key=job_size)

    # 5) Process jobs
    t_start = time.time()
    n_processed = 0
    for j in remaining:
        if n_processed >= args.max_jobs and not args.all:
            break
        if time.time() - t_start > args.max_seconds:
            LOG.info("Hit max_seconds=%d, exiting", args.max_seconds)
            break

        try:
            res = run_one_job(conn, j)
            if res["status"] == "ok":
                prog.setdefault("completed", []).append(res)
            elif res["status"] == "skip_not_listed":
                prog.setdefault("skipped", []).append(res)
            else:
                # empty = tried but no data; not really a failure
                prog.setdefault("skipped", []).append(res)
            save_progress(prog)
            n_processed += 1
        except Exception as e:
            LOG.exception("FAILED job %s %s %s: %s", j["sym"], j["tf"], j["window"], e)
            err = {**j, "status": "error", "error": str(e)}
            prog.setdefault("failed", []).append(err)
            save_progress(prog)
            n_processed += 1

    LOG.info("Processed %d jobs in %.1fs", n_processed, time.time() - t_start)
    conn.close()


if __name__ == "__main__":
    main()
