#!/usr/bin/env python3
"""
v5_fill_zeros.py — Download full data for known 0%-coverage combos.

For each (sym, tf, window) where the DB has 0 rows, do a fresh full
download of the window from Coinbase, no gap detection.

Each combo is one self-contained call; safe to run sequentially.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v5_download_coinbase import WINDOWS, TOKENS, TF_MAP, EARLIEST_MS
from v5_fill_smart import fetch_range_chunked, store_rows  # reuse

LOG = logging.getLogger("v5_fill_zeros")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)

DB = "/home/z/my-project/data/ppmt.db"
PROGRESS_FILE = "/home/z/my-project/download/gap_zeros_progress.json"


def main():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cur = conn.cursor()

    # Find all 0%-coverage combos (or very low < 50%)
    LOG.info("Scanning for 0% or very-low coverage combos…")
    targets = []
    for (bsym, pair, cls) in TOKENS:
        if bsym == "BNBUSDT":
            continue
        for tf in ["1m", "5m", "15m"]:
            for (wn, ws, we, _desc) in WINDOWS:
                earliest = EARLIEST_MS.get(pair, 0)
                if we < earliest:
                    continue
                cur.execute(
                    "SELECT COUNT(*) FROM ohlcv_ext_cb "
                    "WHERE symbol=? AND timeframe=? AND window=?",
                    (bsym, tf, wn),
                )
                cnt = cur.fetchone()[0]
                if cnt == 0:
                    targets.append({
                        "sym": bsym, "pair": pair, "tf": tf, "window": wn,
                        "start_ms": ws, "end_ms": we,
                    })

    LOG.info("Found %d 0-coverage combos to fetch", len(targets))
    for t in targets:
        print(f"  {t['sym']} {t['tf']} {t['window']}")

    # Load progress
    prog = {}
    if Path(PROGRESS_FILE).exists():
        prog = json.loads(Path(PROGRESS_FILE).read_text())
    done = set(prog.get("completed", []))
    failed = set(prog.get("failed", []))

    # Process targets
    for t in targets:
        key = f"{t['sym']}|{t['tf']}|{t['window']}"
        if key in done or key in failed:
            LOG.info("SKIP %s (already done/failed)", key)
            continue

        LOG.info("→ %s %s %s — full download", t['sym'], t['tf'], t['window'])
        t0 = time.time()
        try:
            # Fetch the full window
            rows = fetch_range_chunked(t['pair'], t['tf'], t['start_ms'], t['end_ms'])
            n = store_rows(conn, t['sym'], t['tf'], t['window'], rows)
            elapsed = time.time() - t0
            LOG.info("✓ %s: fetched=%d stored=%d in %.1fs", key, len(rows), n, elapsed)
            done.add(key)
            prog["completed"] = sorted(list(done))
            Path(PROGRESS_FILE).write_text(json.dumps(prog, indent=2))
        except Exception as e:
            LOG.exception("FAILED %s: %s", key, e)
            failed.add(key)
            prog["failed"] = sorted(list(failed))
            Path(PROGRESS_FILE).write_text(json.dumps(prog, indent=2))

    print(f"\nDone. Completed: {len(done)}, Failed: {len(failed)}")
    conn.close()


if __name__ == "__main__":
    main()
