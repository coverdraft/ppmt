#!/usr/bin/env python3
"""
PPMT v0.6.6 — Bulk Data Loader

Loads historical OHLCV data from multiple sources:
  1. CryptoDataDownload CSV files (Binance data, free)
  2. Bybit API (for missing tokens/timeframes)
  3. Binance Data Vision (daily zip files, for PEPE etc.)

Produces a unified SQLite cache for validation scripts.
"""

import sys
import os
import json
import time
import zipfile
import io
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import pandas as pd

from ppmt.data.collector import DataCollector
from ppmt.data.storage import PPMTStorage


# ============================================================
# Configuration
# ============================================================

TOKEN_CONFIG = {
    "BTC/USDT":  {"asset_class": "blue_chip"},
    "ETH/USDT":  {"asset_class": "blue_chip"},
    "SOL/USDT":  {"asset_class": "large_cap"},
    "BNB/USDT":  {"asset_class": "large_cap"},
    "XRP/USDT":  {"asset_class": "large_cap"},
    "ADA/USDT":  {"asset_class": "large_cap"},
    "LINK/USDT": {"asset_class": "defi"},
    "UNI/USDT":  {"asset_class": "defi"},
    "ATOM/USDT": {"asset_class": "defi"},
    "DOGE/USDT": {"asset_class": "meme"},
    "SHIB/USDT": {"asset_class": "meme"},
    "PEPE/USDT": {"asset_class": "meme"},
}

# Timeframes we need
TIMEFRAMES = {
    "1h": {"days": 600, "min_candles": 3000},
    "5m": {"days": 200, "min_candles": 5000},
    "1m": {"days": 30, "min_candles": 40000},
}

# Tokens per timeframe
TOKENS_PER_TF = {
    "1h": list(TOKEN_CONFIG.keys()),  # all 12
    "5m": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "DOGE/USDT", "LINK/USDT"],
    "1m": ["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"],
}

# Path to CryptoDataDownload CSVs
RAW_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "raw")


def load_cdd_csv(csv_path: str) -> pd.DataFrame:
    """Load a CryptoDataDownload CSV into standard OHLCV DataFrame."""
    df = pd.read_csv(csv_path)

    # CDD format: Unix,Date,Symbol,Open,High,Low,Close,Volume BTC,Volume USDT,tradecount
    # Data is in reverse chronological order
    df = df.iloc[1:]  # Skip the URL header row

    # Rename columns
    col_map = {
        "Unix": "open_time",
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume USDT": "volume",
    }
    df = df.rename(columns=col_map)

    # Keep only needed columns
    df = df[["open_time", "open", "high", "low", "close", "volume"]]

    # Convert types
    df["open_time"] = df["open_time"].astype(float).astype(int)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    # Set datetime index
    df = df.set_index(pd.to_datetime(df["open_time"], unit="ms"))
    df = df.drop(columns=["open_time"])

    # Dedup and sort chronologically
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    return df


def load_all_data():
    """Load data for all tokens and timeframes using best available source."""

    print("=" * 90)
    print("  PPMT v0.6.6 — BULK DATA LOADER")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 90)

    storage = PPMTStorage()
    collector = DataCollector(exchange="bybit")
    results = {}

    for tf_name, tokens in TOKENS_PER_TF.items():
        tf_config = TIMEFRAMES[tf_name]
        print(f"\n  === {tf_name} TIMEFRAME (need {tf_config['days']} days, min {tf_config['min_candles']} candles) ===")

        for symbol in tokens:
            asset_class = TOKEN_CONFIG[symbol]["asset_class"]
            raw_symbol = symbol.replace("/", "")

            # Check cache first
            cached = storage.load_ohlcv(symbol, tf_name)
            if not cached.empty:
                days_span = (cached.index[-1] - cached.index[0]).days
                if len(cached) >= tf_config["min_candles"] and days_span >= tf_config["days"] * 0.8:
                    print(f"  {symbol} @ {tf_name}: CACHED ({len(cached)} candles, {days_span} days)")
                    results[f"{symbol}_{tf_name}"] = cached
                    continue

            # Try CryptoDataDownload CSV (only 1h available)
            df = pd.DataFrame()
            if tf_name == "1h":
                csv_path = os.path.join(RAW_DATA_DIR, f"Binance_{raw_symbol}_1h.csv")
                if os.path.exists(csv_path):
                    try:
                        df = load_cdd_csv(csv_path)
                        # Take only the most recent N days
                        cutoff = df.index[-1] - pd.Timedelta(days=tf_config["days"])
                        df = df[df.index >= cutoff]
                        print(f"  {symbol} @ {tf_name}: CDD CSV loaded ({len(df)} candles)", end="")
                    except Exception as e:
                        print(f"  {symbol} @ {tf_name}: CDD CSV FAILED ({e})", end="")
                        df = pd.DataFrame()

            # Fallback to Bybit API
            if df.empty or len(df) < tf_config["min_candles"]:
                if not df.empty:
                    print(f" → too few ({len(df)}), trying Bybit API...", end="")
                else:
                    print(f"  {symbol} @ {tf_name}: Downloading from Bybit API...", end="")

                try:
                    start = time.time()
                    df_api = collector.fetch_and_save(symbol, tf_name, days=tf_config["days"])
                    elapsed = time.time() - start

                    if not df_api.empty:
                        # Merge with existing data if any
                        if not df.empty:
                            df = pd.concat([df, df_api])
                            df = df[~df.index.duplicated(keep="last")]
                            df = df.sort_index()
                        else:
                            df = df_api
                        print(f" OK ({len(df_api)} candles, {elapsed:.0f}s)", end="")
                    else:
                        print(f" NO DATA from Bybit", end="")
                except Exception as e:
                    print(f" BYBIT ERROR: {str(e)[:60]}", end="")

            # Final check
            if df.empty:
                print(" → FAILED")
                continue

            # Save to storage
            if len(df) >= tf_config["min_candles"]:
                storage.save_ohlcv(symbol, tf_name, df)
                days_span = (df.index[-1] - df.index[0]).days
                print(f" → SAVED ({len(df)} candles, {days_span} days)")
                results[f"{symbol}_{tf_name}"] = df
            else:
                print(f" → INSUFFICIENT ({len(df)} candles, need {tf_config['min_candles']})")

    collector.close()
    storage.close()

    # Summary
    print("\n" + "=" * 90)
    print("  DATA LOADING SUMMARY")
    print("=" * 90)

    for tf_name in TIMEFRAMES:
        tokens = TOKENS_PER_TF[tf_name]
        print(f"\n  {tf_name}:")
        for symbol in tokens:
            key = f"{symbol}_{tf_name}"
            if key in results:
                df = results[key]
                days_span = (df.index[-1] - df.index[0]).days
                print(f"    {symbol:12} {len(df):>7} candles  {days_span:>4} days  "
                      f"{df.index[0].strftime('%Y-%m-%d')} → {df.index[-1].strftime('%Y-%m-%d')}")
            else:
                print(f"    {symbol:12} MISSING")

    return results


if __name__ == "__main__":
    data = load_all_data()
