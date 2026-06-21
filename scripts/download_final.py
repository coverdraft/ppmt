#!/usr/bin/env python3
"""Download all missing 90d data for PPMT. Single process with flush."""
import sys; sys.path.insert(0, '/home/z/my-project/ppmt/src')
import time
from ppmt.data.bulk_downloader import BulkDownloader
from ppmt.data.storage import PPMTStorage

storage = PPMTStorage()
downloader = BulkDownloader(exchange="binance")

# Tokens needing both 1m and 5m
tokens_1m_5m = ['LINK/USDT', 'AVAX/USDT', 'DOT/USDT', 'AAVE/USDT', 'UNI/USDT', 'PEPE/USDT']
# Blue chips needing only 5m
bluechip_5m = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT']

for sym in tokens_1m_5m:
    for tf in ['1m', '5m']:
        print(f'{sym} {tf}...', end=' ', flush=True)
        try:
            df = downloader.download_token(sym, tf, days=90)
            if df is not None and len(df) > 0:
                cur = storage.conn.cursor()
                rows = [(sym, tf, int(r.timestamp), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume)) for r in df.itertuples()]
                cur.executemany('INSERT OR IGNORE INTO ohlcv (symbol, timeframe, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', rows)
                storage.conn.commit()
                print(f'{len(rows):,} candles', flush=True)
            else:
                print('NO DATA', flush=True)
        except Exception as e:
            print(f'ERROR: {e}', flush=True)
            time.sleep(5)

for sym in bluechip_5m:
    print(f'{sym} 5m...', end=' ', flush=True)
    try:
        df = downloader.download_token(sym, '5m', days=90)
        if df is not None and len(df) > 0:
            cur = storage.conn.cursor()
            rows = [(sym, '5m', int(r.timestamp), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume)) for r in df.itertuples()]
            cur.executemany('INSERT OR IGNORE INTO ohlcv (symbol, timeframe, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', rows)
            storage.conn.commit()
            print(f'{len(rows):,} candles', flush=True)
        else:
            print('NO DATA', flush=True)
    except Exception as e:
        print(f'ERROR: {e}', flush=True)
        time.sleep(5)

print('\nALL DOWNLOADS COMPLETE', flush=True)

# Verify
cur = storage.conn.cursor()
cur.execute('SELECT symbol, timeframe, COUNT(*) FROM ohlcv GROUP BY symbol, timeframe ORDER BY symbol, timeframe')
print('\nFINAL DATA INVENTORY:', flush=True)
for r in cur.fetchall():
    expected = 129600 if r[1] == "1m" else 25920
    pct = r[2] / expected * 100
    print(f'  {r[0]:15s} {r[1]:5s} {r[2]:7,} / {expected:,} ({pct:.0f}%)', flush=True)
