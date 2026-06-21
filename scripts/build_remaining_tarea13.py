#!/usr/bin/env python3
"""Build remaining tokens that were missed in the main build run."""
import sys, time, os, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ppmt', 'src'))

import requests
import pandas as pd
from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage

TOKENS_TO_BUILD = [
    {"symbol": "LINK/USDT", "class": "alt",     "timeframes": ["5m", "15m"]},
    {"symbol": "UNI/USDT",  "class": "alt",      "timeframes": ["1m", "5m", "15m"]},
]

DAYS = {"1m": 120, "5m": 180, "15m": 180}
BINANCE_BASE = "https://api.binance.com"
TF_TO_BINANCE = {"1m": "1m", "5m": "5m", "15m": "15m"}

def symbol_to_binance(symbol):
    return symbol.replace("/", "")

def download_klines(symbol, timeframe, days):
    api_symbol = symbol_to_binance(symbol)
    interval = TF_TO_BINANCE.get(timeframe, timeframe)
    ms_per_candle = {"1m": 60_000, "5m": 300_000, "15m": 900_000}
    candle_ms = ms_per_candle.get(timeframe, 60_000)
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - (days * 86_400_000)
    all_data = []
    current_start = start_ts
    while current_start < end_ts:
        url = f"{BINANCE_BASE}/api/v3/klines"
        params = {"symbol": api_symbol, "interval": interval, "limit": 1000, "startTime": current_start}
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    [WARN] Request failed: {e}")
            time.sleep(1)
            continue
        if not data:
            break
        for candle in data:
            all_data.append({
                "timestamp": candle[0], "open": float(candle[1]),
                "high": float(candle[2]), "low": float(candle[3]),
                "close": float(candle[4]), "volume": float(candle[5]),
            })
        current_start = data[-1][0] + candle_ms
        time.sleep(0.1)
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df

def main():
    print("=" * 70)
    print("REMAINING BUILD — LINK 5m/15m + UNI 1m/5m/15m")
    print("=" * 70)
    storage = PPMTStorage()
    for token in TOKENS_TO_BUILD:
        symbol = token["symbol"]
        asset_class = token["class"]
        for tf in token["timeframes"]:
            days = DAYS[tf]
            print(f"\n  {symbol} @ {tf} — downloading {days}d...", flush=True)
            try:
                df = download_klines(symbol, tf, days)
            except Exception as e:
                print(f"  [ERROR] Download failed: {e}")
                continue
            if df is None or len(df) == 0:
                print(f"  [ERROR] No data for {symbol} {tf}")
                continue
            print(f"  Downloaded: {len(df):,} candles", flush=True)
            try:
                engine = PPMT(symbol=symbol, asset_class=asset_class, dual_sax=True,
                              min_confidence=0.08, timeframe=tf)
                engine.attach_storage(storage)
                n_inserted = engine.build(df)
                n3 = engine.trie_n3.pattern_count
                n4 = engine.trie_n4.pattern_count if hasattr(engine.trie_n4, 'pattern_count') else 0
                print(f"  Built {symbol} {tf} -> N3: {n3}, N4: {n4}, inserted: {n_inserted}")
            except Exception as e:
                print(f"  [ERROR] Build failed: {e}")
                import traceback
                traceback.print_exc()
                continue
    storage.close()
    print("\nDone.")

if __name__ == "__main__":
    main()
