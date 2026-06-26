"""
download_1m_data.py — Download 1m OHLCV data from Bybit for V11/V12 pipeline.

Fetches 1m candles for the required symbols and saves them as parquet files
in data/v10/ohlcv_cache/ — the same format used by v11_build_dataset.py.

USAGE:
    python scripts/v12/download_1m_data.py
    python scripts/v12/download_1m_data.py --symbols SOL,DOGE,AVAX
    python scripts/v12/download_1m_data.py --days 365

Bybit public API (no key needed):
  - 1000 candles per request
  - Rate limit: ~10 req/s
  - Symbols: SOL/USDT, DOGE/USDT, AVAX/USDT, BTC/USDT, ETH/USDT
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
LOG = logging.getLogger("download_1m")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "v10" / "ohlcv_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SYMBOLS = ["SOL", "DOGE", "AVAX", "BTC", "ETH"]
DAYS_DEFAULT = 365  # 1 year of 1m data


def download_1m(symbol: str, days: int = 365, exchange_id: str = "bybit") -> pd.DataFrame:
    """Download 1m OHLCV data for a symbol from Bybit.

    Returns DataFrame with columns: timestamp, open, high, low, close, volume
    """
    ex_cls = getattr(ccxt, exchange_id)
    ex = ex_cls({"enableRateLimit": True})
    ex.load_markets()

    pair = f"{symbol}/USDT"
    if pair not in ex.markets:
        raise ValueError(f"Symbol {pair} not found on {exchange_id}")

    # Calculate time range
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400 * 1000

    all_candles = []
    since = start_ms

    LOG.info("Downloading %s 1m data: %d days from %s...",
             symbol, days, exchange_id)

    while since < now_ms:
        try:
            batch = ex.fetch_ohlcv(pair, "1m", since=since, limit=1000)
        except Exception as e:
            LOG.warning("Fetch error: %s — retrying in 5s", e)
            time.sleep(5)
            continue

        if not batch:
            break

        all_candles.extend(batch)
        # Move past the last candle we got
        since = batch[-1][0] + 60_000  # +1 minute

        if len(batch) < 1000:
            break  # No more data

        # Progress
        pct = min(100, (since - start_ms) / (now_ms - start_ms) * 100)
        n_bars = len(all_candles)
        LOG.info("  %s: %d bars fetched (%.0f%%)", symbol, n_bars, pct)

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])

    # Remove duplicates
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    LOG.info("Downloaded %s: %d 1m candles (%s to %s)",
             symbol, len(df),
             pd.to_datetime(df["timestamp"].iloc[0], unit="ms").strftime("%Y-%m-%d"),
             pd.to_datetime(df["timestamp"].iloc[-1], unit="ms").strftime("%Y-%m-%d"))

    return df


def save_parquet(df: pd.DataFrame, symbol: str) -> Path:
    """Save DataFrame to parquet cache."""
    path = CACHE_DIR / f"{symbol}_1m.parquet"
    df.to_parquet(path, index=False)
    LOG.info("Saved %s → %s (%.1f MB)", symbol, path, path.stat().st_size / 1e6)
    return path


def main():
    parser = argparse.ArgumentParser(description="Download 1m OHLCV data from Bybit")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                        help="Comma-separated symbol list (default: SOL,DOGE,AVAX,BTC,ETH)")
    parser.add_argument("--days", type=int, default=DAYS_DEFAULT,
                        help="Number of days to download (default: 365)")
    parser.add_argument("--exchange", default="bybit",
                        choices=["bybit", "okx", "binance"],
                        help="Exchange to use")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]

    LOG.info("=" * 50)
    LOG.info("Downloading 1m data for %d symbols × %d days", len(symbols), args.days)
    LOG.info("Cache dir: %s", CACHE_DIR)
    LOG.info("=" * 50)

    for symbol in symbols:
        pair = f"{symbol}_1m.parquet"
        existing = CACHE_DIR / pair
        if existing.exists():
            existing_df = pd.read_parquet(existing)
            LOG.info("%s: cache exists (%d bars) — use --force to overwrite", symbol, len(existing_df))
            continue

        try:
            df = download_1m(symbol, days=args.days, exchange_id=args.exchange)
            save_parquet(df, symbol)
        except Exception as e:
            LOG.error("Failed to download %s: %s", symbol, e)
            continue

    LOG.info("=" * 50)
    LOG.info("Download complete. Cache contents:")
    for p in sorted(CACHE_DIR.glob("*_1m.parquet")):
        size_mb = p.stat().st_size / 1e6
        LOG.info("  %s (%.1f MB)", p.name, size_mb)


if __name__ == "__main__":
    main()
