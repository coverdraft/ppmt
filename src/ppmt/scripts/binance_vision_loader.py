#!/usr/bin/env python3
"""
PPMT v0.6.6 — Binance Data Vision Bulk Loader

Downloads historical klines from Binance Data Vision (S3 public bucket).
This is MUCH faster than paginated API calls:
  - Monthly zips: ~470KB each for 5m, ~50KB for 1h
  - Daily zips: ~15KB for 5m, ~2KB for 1h
  - No rate limits, no pagination, no API keys needed

Format: https://data.binance.vision/data/spot/{frequency}/klines/{symbol}/{timeframe}/{symbol}-{timeframe}-{date}.zip
"""

import sys
import os
import json
import time
import zipfile
import io
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import pandas as pd
import numpy as np

from ppmt.data.storage import PPMTStorage


# Binance kline columns
KLINES_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore"
]


def download_binance_vision_klines(
    symbol: str,
    timeframe: str,
    months_back: int = 7,
    include_current_month: bool = True,
) -> pd.DataFrame:
    """
    Download klines from Binance Data Vision.

    Downloads monthly zips for the last N months, then daily zips for
    the current month.

    Args:
        symbol: Trading pair in Binance format (e.g., 'BTCUSDT')
        timeframe: Candle interval (e.g., '1h', '5m', '1m')
        months_back: Number of monthly zips to download
        include_current_month: Whether to include current month's daily zips
    """
    base_url = "https://data.binance.vision/data/spot"
    all_dfs = []
    now = datetime.utcnow()

    # Download monthly zips
    for month_offset in range(months_back):
        target = now - timedelta(days=30 * month_offset)
        date_str = target.strftime("%Y-%m")
        filename = f"{symbol}-{timeframe}-{date_str}.zip"
        url = f"{base_url}/monthly/klines/{symbol}/{timeframe}/{filename}"

        try:
            req = Request(url)
            req.add_header("User-Agent", "PPMT/0.6.6")
            with urlopen(req, timeout=30) as response:
                zip_data = response.read()
        except HTTPError as e:
            if e.code == 404:
                continue  # Month not available yet
            print(f"  HTTP {e.code} for {filename}")
            continue
        except Exception as e:
            print(f"  Error for {filename}: {str(e)[:60]}")
            continue

        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                csv_name = zf.namelist()[0]
                with zf.open(csv_name) as csv_file:
                    df = pd.read_csv(csv_file, header=None, names=KLINES_COLUMNS)
                    all_dfs.append(df)
        except Exception as e:
            print(f"  Parse error for {filename}: {str(e)[:60]}")
            continue

    # Download daily zips for current month
    if include_current_month:
        current_month_start = now.replace(day=1)
        for day_offset in range(now.day):
            target = current_month_start + timedelta(days=day_offset)
            date_str = target.strftime("%Y-%m-%d")
            filename = f"{symbol}-{timeframe}-{date_str}.zip"
            url = f"{base_url}/daily/klines/{symbol}/{timeframe}/{filename}"

            try:
                req = Request(url)
                req.add_header("User-Agent", "PPMT/0.6.6")
                with urlopen(req, timeout=15) as response:
                    zip_data = response.read()
                with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                    csv_name = zf.namelist()[0]
                    with zf.open(csv_name) as csv_file:
                        df = pd.read_csv(csv_file, header=None, names=KLINES_COLUMNS)
                        all_dfs.append(df)
            except HTTPError:
                continue
            except Exception:
                continue

    if not all_dfs:
        return pd.DataFrame()

    # Combine all data
    df = pd.concat(all_dfs, ignore_index=True)

    # Keep only needed columns
    df = df[["open_time", "open", "high", "low", "close", "volume"]]

    # Convert types
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    # Set datetime index
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.set_index(pd.to_datetime(df["open_time"].values.astype("datetime64[ms]")))
    df = df.drop(columns=["open_time"])

    # Dedup and sort
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    return df


def load_all_timeframes():
    """Load data for all tokens and timeframes using Binance Data Vision."""

    print("=" * 90)
    print("  PPMT v0.6.6 — BINANCE DATA VISION BULK LOADER")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 90)

    storage = PPMTStorage()

    # Token configs per timeframe
    CONFIGS = {
        "5m": {
            "tokens": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT"],
            "months_back": 7,  # ~7 months of 5m data
            "symbol_format": lambda s: f"{s[:3]}/USDT" if len(s) == 7 else f"{s[:4]}/USDT",
        },
        "1m": {
            "tokens": ["BTCUSDT", "SOLUSDT", "DOGEUSDT", "LINKUSDT"],
            "months_back": 2,  # 1m data is huge, only 2 months
            "symbol_format": lambda s: f"{s[:3]}/USDT" if len(s) == 7 else f"{s[:4]}/USDT",
        },
    }

    for tf_name, config in CONFIGS.items():
        print(f"\n  === {tf_name} TIMEFRAME ===")

        for raw_symbol in config["tokens"]:
            # Convert to PPMT format
            if raw_symbol == "BTCUSDT":
                ppmt_symbol = "BTC/USDT"
            elif raw_symbol == "ETHUSDT":
                ppmt_symbol = "ETH/USDT"
            elif raw_symbol == "SOLUSDT":
                ppmt_symbol = "SOL/USDT"
            elif raw_symbol == "BNBUSDT":
                ppmt_symbol = "BNB/USDT"
            elif raw_symbol == "DOGEUSDT":
                ppmt_symbol = "DOGE/USDT"
            elif raw_symbol == "LINKUSDT":
                ppmt_symbol = "LINK/USDT"
            else:
                ppmt_symbol = raw_symbol.replace("USDT", "/USDT")

            # Check cache first
            cached = storage.load_ohlcv(ppmt_symbol, tf_name)
            min_candles = 5000 if tf_name == "5m" else 40000
            if not cached.empty and len(cached) >= min_candles:
                days_span = (cached.index[-1] - cached.index[0]).days
                print(f"  {ppmt_symbol:12} {tf_name}: CACHED ({len(cached)} candles, {days_span} days)")
                continue

            print(f"  {ppmt_symbol:12} {tf_name}: Downloading from Binance Data Vision...", end=" ", flush=True)
            start = time.time()

            try:
                df = download_binance_vision_klines(
                    raw_symbol, tf_name,
                    months_back=config["months_back"],
                    include_current_month=True,
                )
                elapsed = time.time() - start

                if df.empty:
                    print("NO DATA")
                    continue

                days_span = (df.index[-1] - df.index[0]).days
                storage.save_ohlcv(ppmt_symbol, tf_name, df)
                print(f"{len(df)} candles, {days_span} days ({elapsed:.0f}s)")

            except Exception as e:
                print(f"ERROR: {str(e)[:80]}")

    storage.close()

    # Final summary
    print("\n" + "=" * 90)
    print("  DATA SUMMARY")
    print("=" * 90)

    storage = PPMTStorage()
    import sqlite3
    conn = sqlite3.connect(storage.db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, timeframe, COUNT(*) as cnt
        FROM ohlcv
        GROUP BY symbol, timeframe
        ORDER BY timeframe, symbol
    """)
    for r in cursor.fetchall():
        print(f"  {r[0]:12} {r[1]:4}  {r[2]:>7} candles")
    conn.close()
    storage.close()


if __name__ == "__main__":
    load_all_timeframes()
