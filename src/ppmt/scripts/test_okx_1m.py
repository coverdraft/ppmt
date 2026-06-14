#!/usr/bin/env python3
"""
Test OKX 1m data availability for SOL/DOGE/LINK
Bybit only has 43 days for these tokens at 1m.
OKX may have more historical data.
"""

import sys
sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.data.collector import DataCollector
from ppmt.data.storage import PPMTStorage
import time

def test_okx_1m():
    storage = PPMTStorage()
    
    # Test OKX directly for tokens that Bybit only has 43d
    tokens = ["SOL/USDT", "DOGE/USDT", "LINK/USDT"]
    target_days = 200
    
    for symbol in tokens:
        print(f"\n{'='*60}")
        print(f"Testing OKX 1m data for {symbol} ({target_days} days)")
        print(f"{'='*60}")
        
        collector = DataCollector(exchange="okx", storage=storage)
        try:
            df = collector.fetch_and_save(symbol, "1m", days=target_days)
            if not df.empty:
                span = (df.index[-1] - df.index[0]).days
                print(f"  ✅ OKX: Got {len(df)} candles, {span} days span")
                print(f"  Date range: {df.index[0]} → {df.index[-1]}")
            else:
                print(f"  ❌ OKX: Empty DataFrame")
        except Exception as e:
            print(f"  ❌ OKX failed: {e}")
        
        # Also try Kraken as backup
        print(f"\n  Trying Kraken 1m for {symbol}...")
        collector2 = DataCollector(exchange="kraken", storage=storage)
        try:
            df2 = collector2.fetch_and_save(symbol, "1m", days=target_days)
            if not df2.empty:
                span2 = (df2.index[-1] - df2.index[0]).days
                print(f"  ✅ Kraken: Got {len(df2)} candles, {span2} days span")
                print(f"  Date range: {df2.index[0]} → {df2.index[-1]}")
            else:
                print(f"  ❌ Kraken: Empty DataFrame")
        except Exception as e:
            print(f"  ❌ Kraken failed: {e}")
        
        time.sleep(2)  # Be nice to APIs
    
    storage.close()
    print("\n\nDone testing alternative exchanges for 1m data")

if __name__ == "__main__":
    test_okx_1m()
