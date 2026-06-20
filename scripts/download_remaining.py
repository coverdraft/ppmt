#!/usr/bin/env python3
"""Download missing 90d data for PPMT tokens. Uses bulk INSERT for speed."""
import sys
import time
import sqlite3
sys.path.insert(0, '/home/z/my-project/ppmt/src')

from ppmt.data.bulk_downloader import BulkDownloader
from ppmt.data.storage import PPMTStorage

DAYS = 90
TIMEFRAMES = ["1m", "5m"]

# Tokens that need full 1m+5m download
FULL_TOKENS = ["XRP/USDT", "LINK/USDT", "AVAX/USDT", "DOT/USDT", 
               "AAVE/USDT", "UNI/USDT", "PEPE/USDT"]
# Blue chips that need only 5m
BLUECHIP_5M = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]

def main():
    storage = PPMTStorage()
    downloader = BulkDownloader(exchange="binance")
    
    # Build download list: (symbol, timeframe)
    downloads = []
    for sym in FULL_TOKENS:
        for tf in TIMEFRAMES:
            downloads.append((sym, tf))
    for sym in BLUECHIP_5M:
        downloads.append((sym, "5m"))
    
    print(f"Total downloads needed: {len(downloads)}", flush=True)
    print(f"Estimated time: ~{len(downloads) * 3} minutes", flush=True)
    
    for i, (symbol, tf) in enumerate(downloads):
        print(f"\n[{i+1}/{len(downloads)}] {symbol} {tf}...", flush=True)
        try:
            df = downloader.download_token(symbol, tf, days=DAYS)
            if df is not None and len(df) > 0:
                # Bulk insert using executemany for speed
                cur = storage.conn.cursor()
                rows = []
                for _, row in df.iterrows():
                    rows.append((
                        symbol, tf, int(row['timestamp']),
                        float(row['open']), float(row['high']),
                        float(row['low']), float(row['close']),
                        float(row['volume'])
                    ))
                # Use INSERT OR IGNORE to handle duplicates
                cur.executemany(
                    """INSERT OR IGNORE INTO ohlcv 
                       (symbol, timeframe, timestamp, open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows
                )
                storage.conn.commit()
                print(f"  → {len(rows):,} candles saved", flush=True)
            else:
                print(f"  → No data returned!", flush=True)
        except Exception as e:
            print(f"  → ERROR: {e}", flush=True)
            time.sleep(5)
    
    # Final verification
    print(f"\n{'='*60}", flush=True)
    print("FINAL VERIFICATION", flush=True)
    print(f"{'='*60}", flush=True)
    cur = storage.conn.cursor()
    cur.execute('SELECT symbol, timeframe, COUNT(*) FROM ohlcv GROUP BY symbol, timeframe ORDER BY symbol, timeframe')
    for r in cur.fetchall():
        expected = 129600 if r[1] == "1m" else 25920
        pct = r[2] / expected * 100 if expected else 0
        status = "✅" if pct >= 90 else "⚠️"
        print(f"  {r[0]:15s} {r[1]:5s} {r[2]:7,} / {expected:,} ({pct:.0f}%) {status}", flush=True)

if __name__ == "__main__":
    main()
