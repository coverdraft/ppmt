"""Download 90d OHLCV for 5 tokens × 2 timeframes via Binance REST.

Avoids ccxt rate-limit issues by using requests directly + paginated klines.
Saves into /home/z/.ppmt/ppmt.db table `ohlcv`.
"""
import sqlite3, time, requests, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

DB_PATH = "/home/z/.ppmt/ppmt.db"
BINANCE = "https://api.binance.com"
KLINES = f"{BINANCE}/api/v3/klines"

TOKENS = {
    "BTC/USDT": "blue_chip",
    "ETH/USDT": "blue_chip",
    "SOL/USDT": "large_cap",
    "DOGE/USDT": "meme",
    "LINK/USDT": "mid_cap",
}
TIMEFRAMES = ["5m", "15m"]
DAYS = 90

# Binance TF mapping
TF_TO_BINANCE = {"5m": "5m", "15m": "15m", "1m": "1m"}

def to_ms_days_ago(days: int) -> int:
    return int((time.time() - days * 86400) * 1000)

def download_klines(symbol_ccxt: str, tf: str, days: int):
    """Download paginated klines. symbol_ccxt like 'BTC/USDT' -> 'BTCUSDT'"""
    bn_symbol = symbol_ccxt.replace("/", "")
    bn_tf = TF_TO_BINANCE[tf]
    now_ms = int(time.time() * 1000)
    start_ms = to_ms_days_ago(days)
    end_ms = now_ms
    all_rows = []
    cur_start = start_ms
    session = requests.Session()
    while cur_start < end_ms:
        params = {
            "symbol": bn_symbol,
            "interval": bn_tf,
            "startTime": cur_start,
            "endTime": end_ms,
            "limit": 1000,
        }
        for attempt in range(4):
            try:
                r = session.get(KLINES, params=params, timeout=20)
                if r.status_code == 429 or r.status_code == 418:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  FAIL {symbol_ccxt} {tf}: {e}")
                    return all_rows
                time.sleep(2 ** attempt)
        else:
            break
        data = r.json()
        if not data:
            break
        for row in data:
            # [open_time, open, high, low, close, volume, close_time, ...]
            all_rows.append({
                "symbol": symbol_ccxt,
                "timeframe": tf,
                "timestamp": row[0] // 1000,  # seconds
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })
        last_ts_ms = data[-1][0]
        if last_ts_ms <= cur_start:
            break
        cur_start = last_ts_ms + 1
        time.sleep(0.15)  # rate-limit friendly
    return all_rows

def save_to_db(rows):
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executemany("""
        INSERT OR REPLACE INTO ohlcv(symbol, timeframe, timestamp, open, high, low, close, volume)
        VALUES (:symbol, :timeframe, :timestamp, :open, :high, :low, :close, :volume)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)

def main():
    Path("/home/z/.ppmt").mkdir(parents=True, exist_ok=True)
    # Ensure table exists
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            UNIQUE(symbol, timeframe, timestamp)
        )
    """)
    conn.commit()
    conn.close()

    jobs = [(sym, tf) for sym in TOKENS for tf in TIMEFRAMES]
    print(f"Downloading {len(jobs)} jobs: 5 tokens × 2 timeframes × {DAYS}d")

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(download_klines, sym, tf, DAYS): (sym, tf) for sym, tf in jobs}
        for fut in as_completed(futures):
            sym, tf = futures[fut]
            try:
                rows = fut.result()
                n = save_to_db(rows)
                first_ts = rows[0]["timestamp"] if rows else 0
                last_ts = rows[-1]["timestamp"] if rows else 0
                print(f"  OK {sym:10s} {tf:4s}: {n:6d} candles  "
                      f"({pd.Timestamp(first_ts, unit='s')} → {pd.Timestamp(last_ts, unit='s')})")
            except Exception as e:
                print(f"  ERROR {sym} {tf}: {e}")

    # Summary
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    print("\n=== SUMMARY ===")
    cur.execute("SELECT symbol, timeframe, COUNT(*) FROM ohlcv GROUP BY symbol, timeframe ORDER BY symbol, timeframe")
    for r in cur.fetchall(): print(" ", r)
    conn.close()

if __name__ == "__main__":
    main()
