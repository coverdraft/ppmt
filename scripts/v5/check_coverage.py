#!/usr/bin/env python3
"""Check current OHLCV coverage in ppmt.db (Coinbase tables)."""
import sqlite3
import os

DB = "/home/z/my-project/data/ppmt.db"

if not os.path.exists(DB):
    print(f"DB not found: {DB}")
    raise SystemExit(1)

conn = sqlite3.connect(DB)
cur = conn.cursor()

# List tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("Tables in DB:", tables)
print()

cb_table = "ohlcv_ext_cb"
# Schema
cur.execute(f"PRAGMA table_info({cb_table})")
cols = [r[1] for r in cur.fetchall()]
print(f"{cb_table} columns:", cols)
print()

# Per window / timeframe coverage
WINDOWS = {
    "BULL_2024":   ("2024-10-01", "2024-12-31"),
    "RANGE_2025":  ("2025-08-01", "2025-10-31"),
    "RECENT_2026": ("2026-03-01", "2026-06-23"),
    "BEAR_2022":   ("2022-05-01", "2022-07-31"),
    "RANGE_2023":  ("2023-03-01", "2023-10-31"),
}

# Distinct timeframes present in the table
cur.execute(f"SELECT DISTINCT timeframe FROM {cb_table} ORDER BY timeframe")
tfs_present = [r[0] for r in cur.fetchall()]
print("TFs present:", tfs_present)
print()

# Distinct symbols
cur.execute(f"SELECT DISTINCT symbol FROM {cb_table} ORDER BY symbol")
syms = [r[0] for r in cur.fetchall()]
print(f"Symbols in {cb_table} ({len(syms)}):", syms)
print()

# Distinct windows
cur.execute(f"SELECT DISTINCT window FROM {cb_table} ORDER BY window")
wins_present = [r[0] for r in cur.fetchall()]
print("Windows present:", wins_present)
print()

# Coverage matrix per (window, timeframe)
print(f"{'Window':<14} {'TF':<6} {'Symbols':<8} {'Candles':<10} {'First':<12} {'Last':<12}")
print("-" * 70)

import datetime
for wname in [w[0] for w in [(k,) for k in WINDOWS.keys()]]:
    cur.execute(
        f"SELECT timeframe, COUNT(*), COUNT(DISTINCT symbol), MIN(timestamp), MAX(timestamp) "
        f"FROM {cb_table} WHERE window=? GROUP BY timeframe ORDER BY timeframe",
        (wname,),
    )
    rows = cur.fetchall()
    if not rows:
        print(f"{wname:<14} {'-':<6} {'0':<8} {'0':<10} {'-':<12} {'-':<12}")
    else:
        for tf, cnt, nsym, mn, mx in rows:
            first = datetime.datetime.utcfromtimestamp(mn).strftime("%Y-%m-%d") if mn else "-"
            last = datetime.datetime.utcfromtimestamp(mx).strftime("%Y-%m-%d") if mx else "-"
            print(f"{wname:<14} {tf:<6} {nsym:<8} {cnt:<10} {first:<12} {last:<12}")
    print()

conn.close()
