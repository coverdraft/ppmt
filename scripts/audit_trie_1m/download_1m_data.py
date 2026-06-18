"""
Download real 1m candles from Binance for BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX.
Saves ~50,000 candles per token (~35 days) to CSV.

Usage:
    python scripts/audit_trie_1m/download_1m_data.py

Output:
    /home/z/my-project/download/real_data_1m/<SYMBOL>_1m.csv
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

import pandas as pd

# Output dir is outside the repo (it's bulk data, not source code)
OUT_DIR = Path("/home/z/my-project/download/real_data_1m")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
           "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]

TARGET_CANDLES = 50_000      # ~35 days at 1m
BINANCE_LIMIT = 1000         # max per request
BASE_URL = "https://api.binance.com/api/v3/klines"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_window(symbol: str, start_ms: int, end_ms: int) -> list[list]:
    """Fetch up to 1000 candles in [start_ms, end_ms]."""
    url = (f"{BASE_URL}?symbol={symbol}&interval=1m"
           f"&startTime={start_ms}&endTime={end_ms}&limit={BINANCE_LIMIT}")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def download_symbol(symbol: str, target_candles: int) -> Path:
    """Page backwards from now until we have target_candles or hit limit."""
    out_csv = OUT_DIR / f"{symbol}_1m.csv"
    if out_csv.exists():
        df_existing = pd.read_csv(out_csv)
        if len(df_existing) >= target_candles:
            print(f"  [{symbol}] already have {len(df_existing)} candles — skip")
            return out_csv

    end_ms = int(time.time() * 1000)
    # Start ~ target_candles minutes ago + small buffer
    start_ms = end_ms - (target_candles + 100) * 60_000
    all_candles: list[list] = []
    cursor = start_ms
    page = 0
    while cursor < end_ms and len(all_candles) < target_candles:
        try:
            batch = fetch_window(symbol, cursor, end_ms)
        except Exception as e:
            print(f"  [{symbol}] error page {page}: {e}; retry after 2s")
            time.sleep(2)
            continue
        if not batch:
            break
        all_candles.extend(batch)
        last_open = int(batch[-1][0])
        cursor = last_open + 60_000
        page += 1
        if page % 10 == 0:
            print(f"  [{symbol}] page {page}: {len(all_candles)} candles so far")
        # Polite rate limit: ~5 req/s is fine for Binance public
        time.sleep(0.15)
        if len(batch) < BINANCE_LIMIT:
            break

    if not all_candles:
        print(f"  [{symbol}] NO DATA — skip")
        return out_csv

    # Binance kline fields:
    # 0 open_time, 1 open, 2 high, 3 low, 4 close, 5 volume,
    # 6 close_time, 7 quote_volume, 8 trades, 9 taker_buy_base,
    # 10 taker_buy_quote, 11 ignore
    df = pd.DataFrame(all_candles, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "_ignore",
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume",
              "quote_volume", "taker_buy_base", "taker_buy_quote"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["trades"] = pd.to_numeric(df["trades"], errors="coerce").astype("Int64")
    df = df.drop(columns=["_ignore"])
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    df.to_csv(out_csv, index=False)
    print(f"  [{symbol}] saved {len(df)} candles to {out_csv}")
    return out_csv


def main():
    print(f"Downloading up to {TARGET_CANDLES} 1m candles per token "
          f"from Binance...")
    summary = {}
    for sym in SYMBOLS:
        print(f"\n=== {sym} ===")
        try:
            path = download_symbol(sym, TARGET_CANDLES)
            df = pd.read_csv(path)
            summary[sym] = {
                "candles": len(df),
                "start": str(df["open_time"].iloc[0]),
                "end": str(df["open_time"].iloc[-1]),
                "file": str(path),
            }
        except Exception as e:
            print(f"  [{sym}] FATAL: {e}")
            summary[sym] = {"error": str(e)}

    summary_path = OUT_DIR / "_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved to {summary_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
