#!/usr/bin/env python3
"""Download all missing 90d data for PPMT. Runs as a single long process.
Writes progress to a status file for monitoring.
"""
import sys
import time
import os
sys.path.insert(0, '/home/z/my-project/ppmt/src')

from ppmt.data.bulk_downloader import BulkDownloader
from ppmt.data.storage import PPMTStorage

STATUS_FILE = "/home/z/my-project/download_status.txt"
DAYS = 90

# What we need: (symbol, timeframe) pairs
DOWNLOADS = []

# Tokens needing both 1m and 5m
for sym in ["XRP/USDT", "LINK/USDT", "AVAX/USDT", "DOT/USDT", 
            "AAVE/USDT", "UNI/USDT", "PEPE/USDT"]:
    DOWNLOADS.append((sym, "1m"))
    DOWNLOADS.append((sym, "5m"))

# Blue chips needing 5m only
for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]:
    DOWNLOADS.append((sym, "5m"))

def write_status(msg):
    with open(STATUS_FILE, "a") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

def main():
    # Clear status file
    with open(STATUS_FILE, "w") as f:
        f.write(f"Starting download of {len(DOWNLOADS)} token/timeframe pairs\n")
    
    storage = PPMTStorage()
    downloader = BulkDownloader(exchange="binance")
    
    # Check what's already done
    cur = storage.conn.cursor()
    cur.execute('SELECT symbol, timeframe, COUNT(*) FROM ohlcv GROUP BY symbol, timeframe')
    existing = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    
    write_status(f"Existing data: {len(existing)} symbol/tf combos")
    
    for i, (symbol, tf) in enumerate(DOWNLOADS):
        key = (symbol, tf)
        current = existing.get(key, 0)
        expected = 129600 if tf == "1m" else 25920
        
        if current >= expected * 0.9:
            write_status(f"[{i+1}/{len(DOWNLOADS)}] {symbol} {tf}: SKIP ({current:,} already)")
            continue
        
        write_status(f"[{i+1}/{len(DOWNLOADS)}] {symbol} {tf}: DOWNLOADING (have {current:,})...")
        
        try:
            df = downloader.download_token(symbol, tf, days=DAYS)
            if df is not None and len(df) > 0:
                rows = []
                for _, row in df.iterrows():
                    rows.append((
                        symbol, tf, int(row['timestamp']),
                        float(row['open']), float(row['high']),
                        float(row['low']), float(row['close']),
                        float(row['volume'])
                    ))
                cur = storage.conn.cursor()
                cur.executemany(
                    """INSERT OR IGNORE INTO ohlcv 
                       (symbol, timeframe, timestamp, open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows
                )
                storage.conn.commit()
                write_status(f"  → SAVED {len(rows):,} candles (total now: {current + len(rows):,})")
                existing[key] = current + len(rows)
            else:
                write_status(f"  → NO DATA RETURNED")
        except Exception as e:
            write_status(f"  → ERROR: {e}")
            time.sleep(5)
    
    # Final verification
    write_status("=" * 60)
    write_status("FINAL VERIFICATION")
    write_status("=" * 60)
    cur = storage.conn.cursor()
    cur.execute('SELECT symbol, timeframe, COUNT(*) FROM ohlcv GROUP BY symbol, timeframe ORDER BY symbol, timeframe')
    for r in cur.fetchall():
        expected = 129600 if r[1] == "1m" else 25920
        pct = r[2] / expected * 100 if expected else 0
        status = "OK" if pct >= 90 else "LOW"
        write_status(f"  {r[0]:15s} {r[1]:5s} {r[2]:7,} / {expected:,} ({pct:.0f}%) {status}")
    
    write_status("DONE")

if __name__ == "__main__":
    main()
