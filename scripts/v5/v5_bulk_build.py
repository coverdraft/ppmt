"""
Track B4: V5 Bulk build — populate tries from ohlcv_ext with historical_regime.

Iterates over every (symbol, timeframe, window) in ohlcv_ext, builds the
PPMT 4-level Trie for each, and propagates the window tag into
BlockLifecycleMetadata.historical_regime.

Usage:
    python /home/z/my-project/scripts/v5_bulk_build.py --timeframes 5m 15m
    python /home/z/my-project/scripts/v5_bulk_build.py --timeframes 1m --symbols BTCUSDT ETHUSDT
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))

from ppmt.data.storage import PPMTStorage  # noqa: E402
from ppmt.engine.ppmt import PPMT  # noqa: E402

LOG = logging.getLogger("v5_bulk_build")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# Token -> asset_class mapping (must match v5_download_massive.py)
TOKEN_CLASS = {
    "BTCUSDT":  "blue_chip",
    "ETHUSDT":  "blue_chip",
    "BNBUSDT":  "blue_chip",
    "SOLUSDT":  "large_cap",
    "XRPUSDT":  "large_cap",
    "ADAUSDT":  "mid_cap",
    "AVAXUSDT": "mid_cap",
    "LINKUSDT": "mid_cap",
    "DOGEUSDT": "meme",
    "SHIBUSDT": "meme",
    "PEPEUSDT": "meme",
    "WIFUSDT":  "meme",
    "BONKUSDT": "meme",
    # Legacy formats with slash
    "BTC/USDT":  "blue_chip",
    "ETH/USDT":  "blue_chip",
    "BNB/USDT":  "blue_chip",
    "SOL/USDT":  "large_cap",
    "XRP/USDT":  "large_cap",
    "ADA/USDT":  "mid_cap",
    "AVAX/USDT": "mid_cap",
    "LINK/USDT": "mid_cap",
    "DOGE/USDT": "meme",
    "SHIB/USDT": "meme",
    "PEPE/USDT": "meme",
    "WIF/USDT":  "meme",
    "BONK/USDT": "meme",
}

WINDOWS = ["BULL_2024", "RANGE_2025", "RECENT_2026", "BEAR_2022", "RANGE_2023"]


def load_window_df(storage: PPMTStorage, symbol: str, timeframe: str, window: str) -> pd.DataFrame:
    """Load OHLCV rows for (symbol, tf, window) from ohlcv_ext as a DataFrame."""
    conn = storage._ensure_conn()
    rows = conn.execute(
        """
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_ext
        WHERE symbol = ? AND timeframe = ? AND window = ?
        ORDER BY timestamp ASC
        """,
        (symbol, timeframe, window),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.set_index("timestamp").sort_index()
    return df


def build_one(
    storage: PPMTStorage,
    symbol: str,
    asset_class: str,
    timeframe: str,
    window: str,
) -> int:
    """Build a single (symbol, tf, window) trie with historical_regime=window."""
    df = load_window_df(storage, symbol, timeframe, window)
    if len(df) < 100:
        LOG.warning("Skipping %s %s %s: only %d rows", symbol, timeframe, window, len(df))
        return 0

    LOG.info("Building %s %s %s: %d candles", symbol, timeframe, window, len(df))

    engine = PPMT(
        symbol=symbol,
        asset_class=asset_class,
        timeframe=timeframe,
    )
    engine.attach_storage(storage)

    n_patterns = engine.build(df, historical_regime=window)
    LOG.info("  -> %d patterns inserted", n_patterns)
    return n_patterns


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframes", nargs="+", default=["5m", "15m"])
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--windows", nargs="+", default=None)
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()

    storage = PPMTStorage(args.db_path) if args.db_path else PPMTStorage()

    # Discover available (symbol, tf, window) combos in ohlcv_ext
    conn = storage._ensure_conn()
    rows = conn.execute(
        "SELECT DISTINCT symbol, timeframe, window FROM ohlcv_ext"
    ).fetchall()

    available = []
    for sym, tf, win in rows:
        if args.symbols and sym not in args.symbols:
            continue
        if args.timeframes and tf not in args.timeframes:
            continue
        if args.windows and win not in args.windows:
            continue
        available.append((sym, tf, win))

    LOG.info("Found %d (symbol, tf, window) combos to build", len(available))

    total_patterns = 0
    t0 = time.time()
    for i, (sym, tf, win) in enumerate(available, 1):
        cls = TOKEN_CLASS.get(sym, "default")
        try:
            n = build_one(storage, sym, cls, tf, win)
            total_patterns += n
        except Exception as e:
            LOG.exception("FAILED %s %s %s: %s", sym, tf, win, e)

        if i % 5 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed
            remaining = (len(available) - i) / rate if rate > 0 else 0
            LOG.info("Progress: %d/%d (%.1f/s, ETA %.0fs)", i, len(available), rate, remaining)

    elapsed = time.time() - t0
    LOG.info("=" * 60)
    LOG.info("Build complete: %d patterns across %d combos in %.1fs",
             total_patterns, len(available), elapsed)

    # Final trie count
    n_tries = conn.execute("SELECT COUNT(*) FROM tries").fetchone()[0]
    LOG.info("Total tries in DB: %d", n_tries)


if __name__ == "__main__":
    main()
