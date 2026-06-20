#!/usr/bin/env python3
"""Download 90 days of 1m+5m data for all PPMT tokens.
Runs each token sequentially with progress output.
"""
import sys
import time
import os
sys.path.insert(0, '/home/z/my-project/ppmt/src')

from ppmt.data.bulk_downloader import BulkDownloader
from ppmt.data.storage import PPMTStorage

# All tokens needed, grouped by asset class
TOKENS = {
    "blue_chip": ["BTC/USDT", "ETH/USDT"],
    "large_cap": ["SOL/USDT", "BNB/USDT", "XRP/USDT"],
    "mid_cap": ["LINK/USDT", "AVAX/USDT", "DOT/USDT", "AAVE/USDT", "UNI/USDT"],
    "meme": ["DOGE/USDT", "SHIB/USDT", "WIF/USDT"],
    "oos": ["PEPE/USDT"],
}

TIMEFRAMES = ["1m", "5m"]
DAYS = 90
EXPECTED_1M = DAYS * 1440  # ~129,600
EXPECTED_5M = DAYS * 288   # ~25,920

def main():
    storage = PPMTStorage()
    downloader = BulkDownloader(exchange="binance")
    
    # Check what we already have
    cur = storage.conn.cursor()
    cur.execute('SELECT symbol, timeframe, COUNT(*) FROM ohlcv GROUP BY symbol, timeframe')
    existing = {}
    for row in cur.fetchall():
        existing[(row[0], row[1])] = row[2]
    
    total_downloaded = 0
    
    for asset_class, symbols in TOKENS.items():
        print(f"\n{'='*60}")
        print(f"Asset class: {asset_class}")
        print(f"{'='*60}")
        
        for symbol in symbols:
            for tf in TIMEFRAMES:
                key = (symbol, tf)
                current = existing.get(key, 0)
                expected = EXPECTED_1M if tf == "1m" else EXPECTED_5M
                
                if current >= expected * 0.95:
                    print(f"  {symbol} {tf}: {current:,} candles (already have ~90d) ✅")
                    continue
                
                print(f"  {symbol} {tf}: {current:,} candles (need ~{expected:,}) → downloading...", flush=True)
                
                try:
                    df = downloader.download_token(symbol, tf, days=DAYS)
                    if df is not None and len(df) > 0:
                        # Save to DB
                        for _, row in df.iterrows():
                            storage.save_candle(
                                symbol=symbol,
                                timeframe=tf,
                                timestamp=int(row['timestamp']),
                                open=float(row['open']),
                                high=float(row['high']),
                                low=float(row['low']),
                                close=float(row['close']),
                                volume=float(row['volume']),
                            )
                        storage.conn.commit()
                        print(f"    → Saved {len(df):,} candles (total: {current + len(df):,})", flush=True)
                        total_downloaded += len(df)
                    else:
                        print(f"    → No data returned!", flush=True)
                except Exception as e:
                    print(f"    → ERROR: {e}", flush=True)
    
    # Final verification
    print(f"\n{'='*60}")
    print("FINAL VERIFICATION")
    print(f"{'='*60}")
    cur.execute('SELECT symbol, timeframe, COUNT(*) FROM ohlcv GROUP BY symbol, timeframe ORDER BY symbol, timeframe')
    for r in cur.fetchall():
        expected = EXPECTED_1M if r[1] == "1m" else EXPECTED_5M
        pct = r[2] / expected * 100 if expected else 0
        status = "✅" if pct >= 90 else "⚠️"
        print(f"  {r[0]:15s} {r[1]:5s} {r[2]:7,} / {expected:,} ({pct:.0f}%) {status}")
    
    print(f"\nTotal new candles downloaded: {total_downloaded:,}")

if __name__ == "__main__":
    main()
