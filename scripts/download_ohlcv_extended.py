"""Download OHLCV for 9 tokens × 2 timeframes × multiple historical periods (bull/range/bear).

The user requested:
- Use other tokens (not just SOL/USDT)
- Sample from different historical moments (alcistas, en rango, bajistas)
- Provides variety so the trie learns patterns from all market regimes

We download 3 windows per token:
  Window A (RECENT):  2026-03-24 → 2026-06-22  (90d, current regime — already have)
  Window B (RANGE):   2025-08-01 → 2025-10-30  (90d, range-bound market)
  Window C (BULL):    2024-10-01 → 2024-12-30  (90d, late-2024 BTC rally to ~100k)

Each window tagged with `window` column in DB so we can query separately.
Combined IS for trie build can sample from all 3 windows.
"""
import sqlite3, time, requests, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from datetime import datetime, timedelta

DB_PATH = "/home/z/.ppmt/ppmt.db"
BINANCE = "https://api.binance.com"
KLINES = f"{BINANCE}/api/v3/klines"

# 9 tokens covering blue/mid/meme — diverse asset classes
TOKENS = {
    "BTC/USDT":   "blue_chip",
    "ETH/USDT":   "blue_chip",
    "SOL/USDT":   "large_cap",
    "BNB/USDT":   "large_cap",
    "XRP/USDT":   "large_cap",
    "ADA/USDT":   "mid_cap",
    "AVAX/USDT":  "mid_cap",
    "DOGE/USDT":  "meme",
    "LINK/USDT":  "mid_cap",
}

TIMEFRAMES = ["5m", "15m"]

# 3 windows of 90 days each — diverse market regimes
# Use ISO date strings; converted to ms below
WINDOWS = [
    ("RECENT_2026", "2026-03-24", "2026-06-22"),  # current — already have, will overwrite
    ("RANGE_2025",  "2025-08-01", "2025-10-30"),  # mid-2025 range
    ("BULL_2024",   "2024-10-01", "2024-12-30"),  # late-2024 BTC pump
]


def iso_to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return int(dt.timestamp() * 1000)


def download_klines_window(symbol_ccxt: str, tf: str, start_ms: int, end_ms: int,
                           window_tag: str):
    """Download paginated klines for [start_ms, end_ms]. Returns list of dicts."""
    bn_symbol = symbol_ccxt.replace("/", "")
    all_rows = []
    cur_start = start_ms
    session = requests.Session()
    n_calls = 0
    while cur_start < end_ms:
        params = {
            "symbol": bn_symbol,
            "interval": tf,
            "startTime": cur_start,
            "endTime": end_ms,
            "limit": 1000,
        }
        data = None
        for attempt in range(5):
            try:
                r = session.get(KLINES, params=params, timeout=20)
                if r.status_code in (429, 418):
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                if attempt == 4:
                    print(f"  FAIL {symbol_ccxt} {tf} {window_tag}: {e}")
                    return all_rows
                time.sleep(2 ** attempt)
        if not data:
            break
        n_calls += 1
        for row in data:
            all_rows.append({
                "symbol": symbol_ccxt,
                "timeframe": tf,
                "timestamp": row[0] // 1000,  # seconds
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "window": window_tag,
            })
        last_ts_ms = data[-1][0]
        if last_ts_ms <= cur_start:
            break
        cur_start = last_ts_ms + 1
        time.sleep(0.10)  # rate-limit friendly
    return all_rows


def save_to_db(rows):
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Use INSERT OR REPLACE so windows can be re-downloaded
    cur.executemany("""
        INSERT OR REPLACE INTO ohlcv_ext(symbol, timeframe, timestamp, open, high, low, close, volume, window)
        VALUES (:symbol, :timeframe, :timestamp, :open, :high, :low, :close, :volume, :window)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def main():
    Path("/home/z/.ppmt").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_ext (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            window TEXT NOT NULL,
            UNIQUE(symbol, timeframe, timestamp)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_ext_sym_tf ON ohlcv_ext(symbol, timeframe)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_ext_window ON ohlcv_ext(window)")
    conn.commit()
    conn.close()

    # Build job list: (symbol, tf, start_ms, end_ms, window_tag)
    jobs = []
    for sym in TOKENS:
        for tf in TIMEFRAMES:
            for w_tag, start_iso, end_iso in WINDOWS:
                jobs.append((sym, tf, iso_to_ms(start_iso), iso_to_ms(end_iso), w_tag))

    print(f"Downloading {len(jobs)} jobs: 9 tokens × 2 TFs × 3 windows = {len(jobs)} requests batches")
    print(f"Windows: {[w[0] for w in WINDOWS]}\n")

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(download_klines_window, sym, tf, s, e, w): (sym, tf, w)
                   for sym, tf, s, e, w in jobs}
        for fut in as_completed(futures):
            sym, tf, w = futures[fut]
            try:
                rows = fut.result()
                n = save_to_db(rows)
                if rows:
                    first_ts = rows[0]["timestamp"]
                    last_ts = rows[-1]["timestamp"]
                    print(f"  OK {sym:10s} {tf:5s} {w:13s}: {n:6d} candles  "
                          f"({pd.Timestamp(first_ts, unit='s').date()} → "
                          f"{pd.Timestamp(last_ts, unit='s').date()})")
                else:
                    print(f"  EMPTY {sym:10s} {tf:5s} {w:13s}")
            except Exception as e:
                print(f"  ERROR {sym} {tf} {w}: {e}")

    # Summary
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    print("\n=== SUMMARY by symbol × window ===")
    cur.execute("""
        SELECT symbol, window, timeframe, COUNT(*),
               MIN(timestamp), MAX(timestamp)
        FROM ohlcv_ext
        GROUP BY symbol, window, timeframe
        ORDER BY symbol, window, timeframe
    """)
    for r in cur.fetchall():
        print(f"  {r[0]:12s} {r[1]:13s} {r[2]:5s} n={r[3]:6d}  "
              f"{pd.Timestamp(r[4], unit='s').date()} → {pd.Timestamp(r[5], unit='s').date()}")
    conn.close()


if __name__ == "__main__":
    main()
