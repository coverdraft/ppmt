#!/usr/bin/env python3
"""
PPMT Initial Portfolio Builder — FASE 3 TAREA 10

Downloads historical OHLCV data from Binance and builds PPMT tries
for 10 tokens across 3 timeframes. The first build populates the
shared N1 (universal) and N2 (class) pools so that subsequent
tokens benefit from cross-asset pattern matching.

Usage:
    python scripts/build_initial_portfolio.py

Output:
    Sequential build progress + final verification of shared pool counts.
"""

import sys
import time
import os
import sqlite3

# Ensure ppmt is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import requests
import pandas as pd
import numpy as np

from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, CLASS_POOL_PREFIX, class_pool_key
from ppmt.data.classifier import AssetClassifier

# ─── Configuration (PASO 10A) ────────────────────────────────────
TOKENS = [
    {"symbol": "BTC/USDT", "class": "blue_chip"},
    {"symbol": "ETH/USDT", "class": "blue_chip"},
    {"symbol": "SOL/USDT", "class": "large_cap"},
    {"symbol": "XRP/USDT", "class": "large_cap"},
    {"symbol": "AVAX/USDT", "class": "large_cap"},
    {"symbol": "DOGE/USDT", "class": "meme"},
    {"symbol": "PEPE/USDT", "class": "meme"},
    {"symbol": "WIF/USDT", "class": "meme"},
    {"symbol": "LINK/USDT", "class": "alt"},
    {"symbol": "UNI/USDT", "class": "alt"},
]

TIMEFRAMES = ["1m", "5m", "15m"]

# Days of data per timeframe
DAYS = {"1m": 120, "5m": 180, "15m": 180}

# Binance API
BINANCE_BASE = "https://api.binance.com"
RATE_LIMIT_SLEEP = 0.1  # 100ms between requests

# Binance interval mapping
TF_TO_BINANCE = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


def symbol_to_binance(symbol: str) -> str:
    """Convert 'BTC/USDT' → 'BTCUSDT' for Binance API."""
    return symbol.replace("/", "")


def download_klines(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Download paginated klines from Binance API (PASO 10B).

    Binance returns max 1000 candles per request. We paginate by
    advancing startTime until we have all required days.

    Args:
        symbol: Trading pair in CCXT format (e.g., 'BTC/USDT')
        timeframe: Candle interval (e.g., '1m', '5m')
        days: Number of days to download

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume
    """
    api_symbol = symbol_to_binance(symbol)
    interval = TF_TO_BINANCE.get(timeframe, timeframe)

    # Calculate start time
    ms_per_candle = {
        "1m": 60_000, "5m": 300_000, "15m": 900_000,
        "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
    }
    candle_ms = ms_per_candle.get(timeframe, 60_000)
    total_candles = days * 86_400_000 // candle_ms
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - (days * 86_400_000)

    all_data = []
    current_start = start_ts
    request_count = 0

    while current_start < end_ts:
        url = f"{BINANCE_BASE}/api/v3/klines"
        params = {
            "symbol": api_symbol,
            "interval": interval,
            "limit": 1000,
            "startTime": current_start,
        }

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    [WARN] Request failed at {current_start}: {e}")
            time.sleep(1)
            continue

        if not data:
            break

        for candle in data:
            all_data.append({
                "timestamp": candle[0],
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            })

        # Advance start time to after the last candle
        current_start = data[-1][0] + candle_ms
        request_count += 1

        # Rate limiting
        time.sleep(RATE_LIMIT_SLEEP)

        # Progress indicator every 10 requests
        if request_count % 10 == 0:
            print(f"    ... {len(all_data):,} candles downloaded ({request_count} requests)", flush=True)

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def main():
    print("=" * 70)
    print("PPMT INITIAL PORTFOLIO BUILDER — FASE 3 TAREA 10")
    print("=" * 70)
    print(f"Tokens: {len(TOKENS)} | Timeframes: {TIMEFRAMES}")
    print(f"Days: {DAYS}")
    print()

    # Open shared storage (PASO 10C)
    storage = PPMTStorage()
    print(f"Storage: {storage.db_path}")
    print()

    total_patterns = 0
    build_results = []

    # Sequential build: BTC first (builds universal N1 + blue_chip N2)
    for token_idx, token in enumerate(TOKENS):
        symbol = token["symbol"]
        asset_class = token["class"]

        print(f"\n{'─' * 60}")
        print(f"[{token_idx + 1}/{len(TOKENS)}] {symbol} ({asset_class})")
        print(f"{'─' * 60}")

        for tf in TIMEFRAMES:
            days = DAYS[tf]
            print(f"\n  {symbol} @ {tf} — downloading {days}d...", flush=True)

            # Download
            try:
                df = download_klines(symbol, tf, days)
            except Exception as e:
                print(f"  [ERROR] Download failed: {e}")
                continue

            if df is None or len(df) == 0:
                print(f"  [ERROR] No data returned for {symbol} {tf}")
                continue

            print(f"  Downloaded: {len(df):,} candles", flush=True)

            # Build PPMT engine
            try:
                engine = PPMT(
                    symbol=symbol,
                    asset_class=asset_class,
                    dual_sax=True,
                    min_confidence=0.08,
                    timeframe=tf,
                )
                engine.attach_storage(storage)
                n_inserted = engine.build(df)
                total_patterns += n_inserted

                n3_count = engine.trie_n3.pattern_count
                n1_count = engine.trie_n1.pattern_count
                n4_count = engine.trie_n4.pattern_count if hasattr(engine.trie_n4, 'pattern_count') else 0

                print(f"  Built {symbol} {tf} -> N3 patterns: {n3_count}, N1: {n1_count}, N4: {n4_count}")
                build_results.append({
                    "symbol": symbol, "timeframe": tf,
                    "n3": n3_count, "n1": n1_count, "n4": n4_count,
                    "candles": len(df), "inserted": n_inserted,
                })

            except Exception as e:
                print(f"  [ERROR] Build failed: {e}")
                import traceback
                traceback.print_exc()
                continue

    # ─── Final Verification (PASO 10C.3) ────────────────────────
    print(f"\n{'=' * 70}")
    print("BUILD COMPLETE — VERIFICATION")
    print(f"{'=' * 70}")

    # Check shared pools
    n1_universal = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
    n1_count = n1_universal.pattern_count if n1_universal else 0

    class_keys = set()
    for token in TOKENS:
        class_keys.add(token["class"])

    class_counts = {}
    for cls in sorted(class_keys):
        n2_trie = storage.load_trie(class_pool_key(cls), "n2")
        class_counts[cls] = n2_trie.pattern_count if n2_trie else 0

    print(f"\n  N1 Universal Pool (__UNIVERSAL__): {n1_count:,} patterns")
    for cls, count in class_counts.items():
        status = "✅" if count >= 1000 else "⚠️"
        print(f"  N2 Class Pool ({cls}): {count:,} patterns {status}")

    # Per-symbol N3 counts
    print(f"\n  Per-symbol N3:")
    for token in TOKENS:
        symbol = token["symbol"]
        n3_trie = storage.load_trie(symbol, "n3")
        n3c = n3_trie.pattern_count if n3_trie else 0
        print(f"    {symbol:15s} N3: {n3c:,} patterns")

    # Summary
    print(f"\n  Total patterns inserted: {total_patterns:,}")
    print(f"  Total tokens built: {len(TOKENS)}")
    print(f"  Total timeframes: {len(TIMEFRAMES)}")
    print()

    # Verification checks
    n1_ok = n1_count >= 5000
    print(f"  N1 > 5000? {'✅ PASS' if n1_ok else '❌ FAIL'} ({n1_count:,})")
    for cls, count in class_counts.items():
        cls_ok = count >= 1000
        print(f"  N2 ({cls}) > 1000? {'✅ PASS' if cls_ok else '❌ FAIL'} ({count:,})")

    storage.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
