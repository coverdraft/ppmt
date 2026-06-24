#!/usr/bin/env python3
"""
v5_fill_smart.py — Smarter gap-filler that skips already-downloaded chunks.

For each (sym, tf, window) gap job:
  1. Compute which time ranges are missing from the DB
  2. Only fetch those missing time ranges
  3. Much faster than re-scanning the whole window

Each invocation processes as many jobs as fit in --max-seconds.
Progress is checkpointed to download/gap_progress_smart.json.
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
)

LOG = logging.getLogger("v5_fill_smart")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)

DB = "/home/z/my-project/data/ppmt.db"
PROGRESS_FILE = "/home/z/my-project/download/gap_progress_smart.json"

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


def find_missing_ranges(cur, sym, tf, window, start_ms, end_ms, ms_per_candle):
    """
    Find time ranges [start_ms, end_ms] that are missing from DB.
    Returns list of (range_start_ms, range_end_ms) tuples.

    Strategy:
      - Get all timestamps present in DB for this (sym, tf, window)
      - Build a sorted list
      - Find gaps between consecutive timestamps > 2 * ms_per_candle
      - Also identify gap at start (before first timestamp) and end (after last)
    """
    cur.execute(
        "SELECT timestamp FROM ohlcv_ext_cb "
        "WHERE symbol=? AND timeframe=? AND window=? "
        "ORDER BY timestamp",
        (sym, tf, window),
    )
    ts_list = [r[0] * 1000 for r in cur.fetchall()]  # convert sec→ms

    # If DB is empty, the entire window is missing
    if not ts_list:
        return [(start_ms, end_ms)]

    missing = []
    # Gap at start
    if ts_list[0] > start_ms + ms_per_candle:
        missing.append((start_ms, ts_list[0]))

    # Gaps in middle
    for i in range(1, len(ts_list)):
        prev = ts_list[i - 1]
        curr = ts_list[i]
        if curr - prev > 2 * ms_per_candle:
            missing.append((prev + ms_per_candle, curr))

    # Gap at end
    if ts_list[-1] < end_ms - ms_per_candle:
        missing.append((ts_list[-1] + ms_per_candle, end_ms))

    return missing


def fetch_range_chunked(pair, tf_label, range_start_ms, range_end_ms, max_retries=5):
    """Fetch candles in [range_start_ms, range_end_ms] chunked by PAGE_CANDLES.
    Reuses the proven chunking logic from v5_download_coinbase.py."""
    granularity, ms_per_candle = TF_MAP[tf_label]
    chunk_ms = PAGE_CANDLES * ms_per_candle
    all_rows = []
    cursor = range_start_ms

    while cursor < range_end_ms:
        chunk_end = min(cursor + chunk_ms, range_end_ms)
        start_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cursor / 1000))
        end_iso   = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(chunk_end / 1000))

        url = f"https://api.exchange.coinbase.com/products/{pair}/candles"
        params = {"granularity": granularity, "start": start_iso, "end": end_iso}
        headers = {"User-Agent": "ppmt-v5-research/1.0", "Accept": "application/json"}

        rows = None
        for attempt in range(max_retries):
            try:
                r = requests.get(url, params=params, headers=headers, timeout=20)
                if r.status_code == 429:
                    wait = 5 * (attempt + 1)
                    LOG.warning("429 on %s %s — wait %ds", pair, tf_label, wait)
                    time.sleep(wait)
                    continue
                if r.status_code == 400:
                    chunk_ms = max(chunk_ms // 2, ms_per_candle)
                    time.sleep(0.5)
                    break
                r.raise_for_status()
                rows = r.json()
                break
            except Exception as e:
                wait = 2 ** attempt
                LOG.warning("Error on %s %s attempt %d: %s — wait %ds",
                            pair, tf_label, attempt + 1, e, wait)
                time.sleep(wait)
        else:
            LOG.error("Failed after %d retries: %s %s @ %d",
                      max_retries, pair, tf_label, cursor)
            return all_rows

        if rows is None:
            continue

        if not isinstance(rows, list) or not rows:
            cursor = chunk_end + 1
            continue

        rows_asc = list(reversed(rows))
        all_rows.extend(rows_asc)
        last_time_ms = rows_asc[-1][0] * 1000
        cursor = max(last_time_ms + ms_per_candle, chunk_end + 1)
        time.sleep(0.04)

    return all_rows


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


def build_job_list(conn):
    cur = conn.cursor()
    jobs = []
    for (bsym, pair, cls) in TOKENS:
        if bsym in EXCLUDE_SYMS:
            continue
        for tf in TARGET_TFS:
            for (wn, ws, we, _desc) in WINDOWS:
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


def run_one_job(conn, job):
    sym = job["sym"]
    pair = job["pair"]
    tf = job["tf"]
    window = job["window"]
    start_ms = job["start_ms"]
    end_ms = job["end_ms"]

    earliest = EARLIEST_MS.get(pair, 0)
    eff_start = max(start_ms, earliest)
    if eff_start >= end_ms:
        return {**job, "status": "skip_not_listed", "fetched": 0, "inserted": 0, "new_count": 0, "new_coverage": 0}

    ms_per_candle = TF_MAP[tf][1]
    cur = conn.cursor()

    LOG.info("→ %s %s %s (have %d/%d = %.1f%%) — finding missing ranges…",
             sym, tf, window, job["current_count"], job["expected"], job["coverage"] * 100)

    missing_ranges = find_missing_ranges(cur, sym, tf, window, eff_start, end_ms, ms_per_candle)
    total_missing_ms = sum(e - s for s, e in missing_ranges)
    est_missing_candles = total_missing_ms // ms_per_candle

    LOG.info("  Found %d missing ranges, ~%d candles to fetch",
             len(missing_ranges), est_missing_candles)

    if not missing_ranges:
        return {**job, "status": "no_gaps", "fetched": 0, "inserted": 0,
                "new_count": job["current_count"], "new_coverage": job["coverage"]}

    t0 = time.time()
    total_fetched = 0
    total_inserted = 0
    for i, (rs, re) in enumerate(missing_ranges):
        if time.time() - t0 > 90:
            LOG.info("  Stopping after 90s in this job, partial progress saved")
            break
        rs_iso = time.strftime("%Y-%m-%d", time.gmtime(rs / 1000))
        re_iso = time.strftime("%Y-%m-%d", time.gmtime(re / 1000))
        LOG.info("  Range %d/%d: %s → %s (%d days)",
                 i + 1, len(missing_ranges), rs_iso, re_iso, (re - rs) // 86_400_000)
        rows = fetch_range_chunked(pair, tf, rs, re)
        ins = store_rows(conn, sym, tf, window, rows)
        total_fetched += len(rows)
        total_inserted += ins

    elapsed = time.time() - t0
    cur.execute(
        "SELECT COUNT(*) FROM ohlcv_ext_cb WHERE symbol=? AND timeframe=? AND window=?",
        (sym, tf, window),
    )
    new_cnt = cur.fetchone()[0]
    new_cov = new_cnt / job["expected"] if job["expected"] else 0

    LOG.info("✓ %s %s %s: fetched=%d inserted=%d new_total=%d (%.1f%%) in %.1fs",
             sym, tf, window, total_fetched, total_inserted, new_cnt, new_cov * 100, elapsed)

    return {
        **job,
        "status": "ok" if total_inserted > 0 else "no_new_data",
        "fetched": total_fetched,
        "inserted": total_inserted,
        "new_count": new_cnt,
        "new_coverage": round(new_cov, 3),
        "elapsed_sec": round(elapsed, 1),
    }


def load_progress():
    if Path(PROGRESS_FILE).exists():
        return json.loads(Path(PROGRESS_FILE).read_text())
    return {"completed": [], "skipped": [], "failed": []}


def save_progress(prog):
    Path(PROGRESS_FILE).write_text(json.dumps(prog, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-seconds", type=int, default=110)
    parser.add_argument("--only-tf", type=str, default=None)
    parser.add_argument("--only-sym", type=str, default=None)
    parser.add_argument("--retry-no-new", action="store_true",
                        help="Retry jobs that previously returned no_new_data")
    args = parser.parse_args()

    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    LOG.info("Scanning DB for current gaps…")
    jobs = build_job_list(conn)
    LOG.info("Found %d jobs", len(jobs))

    prog = load_progress()
    done_keys = set()
    for r in prog.get("completed", []):
        done_keys.add(f"{r['sym']}|{r['tf']}|{r['window']}")
    for r in prog.get("skipped", []):
        done_keys.add(f"{r['sym']}|{r['tf']}|{r['window']}")
    # Don't retry "no_new_data" by default
    for r in prog.get("completed", []):
        if r.get("status") == "no_new_data" and not args.retry_no_new:
            done_keys.add(f"{r['sym']}|{r['tf']}|{r['window']}")

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

    LOG.info("Remaining: %d", len(remaining))

    if not remaining:
        LOG.info("All done!")
        print("\n=== FINAL ===")
        print(f"  Completed: {len(prog.get('completed', []))}")
        print(f"  Skipped: {len(prog.get('skipped', []))}")
        print(f"  Failed: {len(prog.get('failed', []))}")
        return

    # Sort by gap size (smallest first)
    remaining.sort(key=lambda j: max(0, j["expected"] - j["current_count"]))

    t_start = time.time()
    n = 0
    for j in remaining:
        if time.time() - t_start > args.max_seconds:
            LOG.info("Hit max_seconds, exiting")
            break
        try:
            res = run_one_job(conn, j)
            if res["status"] in ("ok", "no_new_data", "no_gaps"):
                prog.setdefault("completed", []).append(res)
            elif res["status"] == "skip_not_listed":
                prog.setdefault("skipped", []).append(res)
            else:
                prog.setdefault("skipped", []).append(res)
            save_progress(prog)
            n += 1
        except Exception as e:
            LOG.exception("FAILED %s: %s", j, e)
            prog.setdefault("failed", []).append({**j, "status": "error", "error": str(e)})
            save_progress(prog)
            n += 1

    LOG.info("Processed %d jobs in %.1fs", n, time.time() - t_start)
    conn.close()


if __name__ == "__main__":
    main()
