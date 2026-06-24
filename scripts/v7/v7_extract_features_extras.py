"""
v7_extract_features_extras.py — Bulk extract F4 features for ALL feature_observations_v6
rows and store in a new SQLite table `feature_observations_v7_extras`.

WHY BULK EXTRACT?
-----------------
The FeaturesExtrasExtractor.extract_batch() does one SQLite query per row (1.4M rows
× 4 queries = 5.6M queries). That's slow (~30 min). Bulk extraction via pandas
merge_asof is 100x faster (~30 sec) and produces a reusable table.

PIPELINE (per symbol):
----------------------
1. Load (symbol, ts) from feature_observations_v6 (1.4M rows total, ~120K per symbol)
2. Load funding_rates for this symbol from cache → asof merge on funding_time <= ts
   → produces funding_rate column. Compute funding_z via rolling 90 settled rates
   (z-score vs last 90 rates ending at each ts).
3. Load oi_history for this symbol from cache → asof merge on timestamp <= ts
   → produces oi_now. Compute oi_change_1h = (oi_now - oi_then_1h) / oi_then_1h * 100,
   similarly oi_change_4h.
4. Sector one-hot: 4 binary columns + 1 categorical int (vectorized from symbol)
5. Day-of-week sin/cos: from ts (vectorized via pandas)
6. Write rows to feature_observations_v7_extras table (UPSERT by symbol+ts)

ANTI-LEAKAGE:
- merge_asof(direction='backward') ensures only PAST funding/OI data is used
- funding_z uses rates with funding_time <= ts only (rolling window over sorted history)
- OI change uses oi_at(ts) - oi_at(ts - lookback), both backward-looking

OUTPUT TABLE SCHEMA:
    feature_observations_v7_extras (
        symbol TEXT NOT NULL,
        ts INTEGER NOT NULL,                   -- epoch seconds (matches feature_observations_v6.ts)
        funding_rate REAL,                     -- last settled rate (or 0.0)
        funding_rate_z REAL,                   -- z-score vs last 90 settled rates (or 0.0)
        oi_change_1h REAL,                     -- % change vs 1h ago (or 0.0)
        oi_change_4h REAL,                     -- % change vs 4h ago (or 0.0)
        sector_blue_chip REAL,                 -- 0.0 or 1.0
        sector_large_cap REAL,
        sector_old_meme REAL,
        sector_new_meme REAL,
        sector_idx INTEGER,                    -- 0=blue_chip, 1=large_cap, 2=old_meme, 3=new_meme
        day_of_week_sin REAL,                  -- sin(2*pi*dow/7)
        day_of_week_cos REAL,                  -- cos(2*pi*dow/7)
        day_of_week INTEGER,                   -- 0=Monday ... 6=Sunday
        PRIMARY KEY (symbol, ts)
    )

USAGE:
    python /home/z/my-project/scripts/v7/v7_extract_features_extras.py
    python /home/z/my-project/scripts/v7/v7_extract_features_extras.py --symbol BTCUSDT
    python /home/z/my-project/scripts/v7/v7_extract_features_extras.py --skip-existing  # only insert new rows
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Make v7 module importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from v7_features_extras import (
    SECTOR_INDEX,
    to_binance_symbol,
)
from v7_ohlcv_encoder import symbol_to_sector

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

DB_PATH = os.environ.get("PPMT_DB_PATH", "/home/z/my-project/data/ppmt.db")
FUNDING_CACHE = "/home/z/my-project/data/v7_cache/funding_cache.db"
OI_CACHE = "/home/z/my-project/data/v7_cache/oi_cache.db"

LOG = logging.getLogger("v7_extract_extras")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Funding z-score window (90 settled rates = 30 days at 8h interval)
FUNDING_Z_WINDOW = 90
FUNDING_Z_MIN_HISTORY = 10  # return 0.0 if fewer than 10 historical rates

# OI lookback windows (seconds)
OI_LOOKBACK_1H = 3600
OI_LOOKBACK_4H = 4 * 3600


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------

def ensure_table(conn: sqlite3.Connection) -> None:
    """Create the feature_observations_v7_extras table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feature_observations_v7_extras (
            symbol TEXT NOT NULL,
            ts INTEGER NOT NULL,
            funding_rate REAL NOT NULL,
            funding_rate_z REAL NOT NULL,
            oi_change_1h REAL NOT NULL,
            oi_change_4h REAL NOT NULL,
            sector_blue_chip REAL NOT NULL,
            sector_large_cap REAL NOT NULL,
            sector_old_meme REAL NOT NULL,
            sector_new_meme NOT NULL,
            sector_idx INTEGER NOT NULL,
            day_of_week_sin REAL NOT NULL,
            day_of_week_cos REAL NOT NULL,
            day_of_week INTEGER NOT NULL,
            PRIMARY KEY (symbol, ts)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_v7_extras_symbol_ts "
        "ON feature_observations_v7_extras(symbol, ts)"
    )
    conn.commit()


# ----------------------------------------------------------------------------
# Per-symbol extraction
# ----------------------------------------------------------------------------

def load_feature_observations(
    conn: sqlite3.Connection,
    symbol: Optional[str] = None,
    skip_existing: bool = False,
) -> pd.DataFrame:
    """Load (symbol, ts) pairs from feature_observations_v6, optionally filtered by symbol."""
    if skip_existing:
        # Only load rows that don't yet exist in v7_extras
        sub_query = "SELECT symbol, ts FROM feature_observations_v6"
        if symbol is not None:
            sub_query += f" WHERE symbol = '{symbol}'"
        sql = f"""
            SELECT symbol, ts FROM ({sub_query}) AS src
            WHERE NOT EXISTS (
                SELECT 1 FROM feature_observations_v7_extras AS dst
                WHERE dst.symbol = src.symbol AND dst.ts = src.ts
            )
        """
        if symbol is not None:
            sql += f" AND src.symbol = '{symbol}'"
    else:
        sql = "SELECT symbol, ts FROM feature_observations_v6"
        if symbol is not None:
            sql += f" WHERE symbol = '{symbol}'"
    df = pd.read_sql_query(sql, conn)
    df["ts"] = df["ts"].astype(np.int64)
    return df


def load_funding_history(symbol: str) -> pd.DataFrame:
    """Load all funding_rates rows for symbol, sorted by funding_time."""
    conn = sqlite3.connect(FUNDING_CACHE)
    try:
        df = pd.read_sql_query(
            "SELECT funding_time, funding_rate FROM funding_rates WHERE symbol = ? ORDER BY funding_time ASC",
            conn,
            params=(symbol,),
        )
    finally:
        conn.close()
    if df.empty:
        return df
    # Convert funding_time from ms to seconds (we'll work in seconds to match feature_obs ts)
    df["funding_time_s"] = (df["funding_time"] // 1000).astype(np.int64)
    df["funding_rate"] = df["funding_rate"].astype(np.float64)
    return df[["funding_time_s", "funding_rate"]]


def load_oi_history(symbol: str) -> pd.DataFrame:
    """Load all oi_history rows for symbol, sorted by timestamp."""
    conn = sqlite3.connect(OI_CACHE)
    try:
        df = pd.read_sql_query(
            "SELECT timestamp, open_interest FROM oi_history WHERE symbol = ? ORDER BY timestamp ASC",
            conn,
            params=(symbol,),
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["timestamp_s"] = (df["timestamp"] // 1000).astype(np.int64)
    df["open_interest"] = df["open_interest"].astype(np.float64)
    return df[["timestamp_s", "open_interest"]]


def compute_funding_features(
    obs_df: pd.DataFrame,
    funding_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute funding_rate (last settled <= ts) and funding_rate_z (z-score vs
    last 90 settled rates ending at ts) for each observation row.

    Uses pandas merge_asof for the backward-looking join, then a vectorized
    rolling z-score computation.
    """
    if funding_df.empty:
        obs_df["funding_rate"] = 0.0
        obs_df["funding_rate_z"] = 0.0
        return obs_df

    # merge_asof requires both sides sorted by the merge key
    obs_sorted = obs_df.sort_values("ts").reset_index(drop=True).copy()
    fund_sorted = funding_df.sort_values("funding_time_s").reset_index(drop=True).copy()

    # Asof merge: for each obs.ts, find the latest funding_time_s <= ts
    merged = pd.merge_asof(
        obs_sorted[["ts"]],
        fund_sorted.rename(columns={"funding_time_s": "ts", "funding_rate": "funding_rate"}),
        on="ts",
        direction="backward",
    )
    # Fill rows before first funding record with 0.0 (no settled rate yet)
    merged["funding_rate"] = merged["funding_rate"].fillna(0.0)

    # Compute funding_z via rolling z-score over the funding history,
    # then map each obs.ts to the funding_time_s just <= ts (the one we just merged)
    # and look up the z-score at that point.

    # Step 1: For each funding_time_s, compute z-score vs the previous FUNDING_Z_WINDOW rates
    fund_with_z = fund_sorted.copy()
    if len(fund_with_z) >= FUNDING_Z_MIN_HISTORY:
        rates = fund_with_z["funding_rate"].values
        # Rolling mean and std over the PREVIOUS window rates (closed='left' to exclude current)
        s = pd.Series(rates)
        # Use min_periods=FUNDING_Z_MIN_HISTORY, window=FUNDING_Z_WINDOW
        # closed='left' means the window ENDS BEFORE the current observation (anti-leakage)
        rolling_mean = s.shift(1).rolling(window=FUNDING_Z_WINDOW, min_periods=FUNDING_Z_MIN_HISTORY).mean()
        rolling_std = s.shift(1).rolling(window=FUNDING_Z_WINDOW, min_periods=FUNDING_Z_MIN_HISTORY).std()
        # z-score = (current - mean) / std
        fund_with_z["funding_z"] = (s - rolling_mean) / rolling_std.replace(0.0, np.nan)
        fund_with_z["funding_z"] = fund_with_z["funding_z"].fillna(0.0)
    else:
        fund_with_z["funding_z"] = 0.0

    # Step 2: Asof merge obs.ts → funding_time_s → funding_z
    z_lookup = fund_with_z[["funding_time_s", "funding_z"]].rename(
        columns={"funding_time_s": "ts", "funding_z": "funding_rate_z"}
    )
    merged = pd.merge_asof(
        merged[["ts", "funding_rate"]],
        z_lookup,
        on="ts",
        direction="backward",
    )
    merged["funding_rate_z"] = merged["funding_rate_z"].fillna(0.0)

    # Merge back to original obs_df (preserving original order)
    obs_df = obs_df.merge(merged, on="ts", how="left")
    obs_df["funding_rate"] = obs_df["funding_rate"].fillna(0.0)
    obs_df["funding_rate_z"] = obs_df["funding_rate_z"].fillna(0.0)
    return obs_df


def compute_oi_features(
    obs_df: pd.DataFrame,
    oi_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute oi_change_1h and oi_change_4h (% change vs lookback ago).
    Uses asof merge to get oi_now at each ts, then again for oi_then.
    """
    if oi_df.empty:
        obs_df["oi_change_1h"] = 0.0
        obs_df["oi_change_4h"] = 0.0
        return obs_df

    obs_sorted = obs_df.sort_values("ts").reset_index(drop=True).copy()
    oi_sorted = oi_df.sort_values("timestamp_s").reset_index(drop=True).copy()
    oi_sorted = oi_sorted.rename(columns={"timestamp_s": "ts", "open_interest": "oi"})

    # Asof merge: oi_now at each obs.ts
    merged = pd.merge_asof(
        obs_sorted[["ts"]],
        oi_sorted,
        on="ts",
        direction="backward",
    )
    merged["oi"] = merged["oi"].fillna(0.0)

    # For oi_then at ts - lookback: shift obs.ts back, asof merge, shift forward
    for label, lookback in [("oi_change_1h", OI_LOOKBACK_1H), ("oi_change_4h", OI_LOOKBACK_4H)]:
        shifted_ts = merged[["ts"]].copy()
        shifted_ts["ts_shifted"] = shifted_ts["ts"] - lookback
        shifted_ts = shifted_ts.sort_values("ts_shifted").reset_index(drop=True)

        oi_then = pd.merge_asof(
            shifted_ts[["ts_shifted"]].rename(columns={"ts_shifted": "ts"}),
            oi_sorted,
            on="ts",
            direction="backward",
        ).rename(columns={"oi": "oi_then"})
        # Bring back to original ts via the index alignment
        shifted_ts["oi_then"] = oi_then["oi_then"].fillna(0.0).values
        # Now align back to merged (same row count, just re-sorted)
        shifted_ts = shifted_ts.sort_values("ts").reset_index(drop=True)
        merged[f"oi_then_{label}"] = shifted_ts["oi_then"].values

        # Compute % change safely
        oi_now = merged["oi"].values
        oi_then_vals = merged[f"oi_then_{label}"].values
        # Avoid div-by-zero: where oi_then <= 0, return 0.0
        with np.errstate(divide="ignore", invalid="ignore"):
            change = np.where(
                (oi_then_vals > 0) & (oi_now > 0),
                (oi_now - oi_then_vals) / oi_then_vals * 100.0,
                0.0,
            )
        merged[label] = change

    obs_df = obs_df.merge(
        merged[["ts", "oi_change_1h", "oi_change_4h"]], on="ts", how="left"
    )
    obs_df["oi_change_1h"] = obs_df["oi_change_1h"].fillna(0.0)
    obs_df["oi_change_4h"] = obs_df["oi_change_4h"].fillna(0.0)
    return obs_df


def compute_sector_features(obs_df: pd.DataFrame) -> pd.DataFrame:
    """Compute sector one-hot (4 binaries + 1 int) from symbol."""
    sectors = obs_df["symbol"].apply(symbol_to_sector)
    obs_df["sector_blue_chip"] = (sectors == "blue_chip").astype(np.float32)
    obs_df["sector_large_cap"] = (sectors == "large_cap").astype(np.float32)
    obs_df["sector_old_meme"] = (sectors == "old_meme").astype(np.float32)
    obs_df["sector_new_meme"] = (sectors == "new_meme").astype(np.float32)
    obs_df["sector_idx"] = sectors.map(SECTOR_INDEX).astype(np.int32)
    return obs_df


def compute_dow_features(obs_df: pd.DataFrame) -> pd.DataFrame:
    """Compute day-of-week sin/cos/int from ts (epoch seconds)."""
    ts_pd = pd.to_datetime(obs_df["ts"], unit="s", utc=True)
    dow = ts_pd.dt.dayofweek.astype(np.int32)  # 0=Monday, 6=Sunday
    obs_df["day_of_week"] = dow
    obs_df["day_of_week_sin"] = np.sin(2 * np.pi * dow / 7.0).astype(np.float32)
    obs_df["day_of_week_cos"] = np.cos(2 * np.pi * dow / 7.0).astype(np.float32)
    return obs_df


def extract_symbol(
    main_conn: sqlite3.Connection,
    symbol: str,
    skip_existing: bool = False,
) -> int:
    """Extract all F4 features for one symbol and insert into feature_observations_v7_extras."""
    t0 = time.time()
    LOG.info("[%s] loading observations from DB...", symbol)
    obs_df = load_feature_observations(main_conn, symbol=symbol, skip_existing=skip_existing)
    if obs_df.empty:
        LOG.info("[%s] no new observations to extract (skip_existing=True)", symbol)
        return 0
    LOG.info("[%s] %d observations to process", symbol, len(obs_df))

    # Load funding + OI history for this symbol
    funding_df = load_funding_history(symbol)
    oi_df = load_oi_history(symbol)
    LOG.info(
        "[%s] funding history: %d rates (range %s -> %s); OI history: %d snapshots",
        symbol,
        len(funding_df),
        (pd.to_datetime(funding_df["funding_time_s"].min(), unit="s", utc=True) if not funding_df.empty else "n/a"),
        (pd.to_datetime(funding_df["funding_time_s"].max(), unit="s", utc=True) if not funding_df.empty else "n/a"),
        len(oi_df),
    )

    # Compute features
    obs_df = compute_funding_features(obs_df, funding_df)
    obs_df = compute_oi_features(obs_df, oi_df)
    obs_df = compute_sector_features(obs_df)
    obs_df = compute_dow_features(obs_df)

    # Insert into DB (chunked for memory safety)
    cols = [
        "symbol", "ts",
        "funding_rate", "funding_rate_z",
        "oi_change_1h", "oi_change_4h",
        "sector_blue_chip", "sector_large_cap", "sector_old_meme", "sector_new_meme",
        "sector_idx",
        "day_of_week_sin", "day_of_week_cos", "day_of_week",
    ]
    rows = obs_df[cols].values.tolist()

    CHUNK_SIZE = 50_000
    n_inserted = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i:i + CHUNK_SIZE]
        main_conn.executemany(
            f"""
            INSERT OR REPLACE INTO feature_observations_v7_extras
            ({", ".join(cols)})
            VALUES ({", ".join(["?"] * len(cols))})
            """,
            chunk,
        )
        main_conn.commit()
        n_inserted += len(chunk)

    LOG.info(
        "[%s] inserted %d rows in %.1fs (%.0f rows/sec)",
        symbol,
        n_inserted,
        time.time() - t0,
        n_inserted / max(time.time() - t0, 0.001),
    )
    return n_inserted


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None, help="Process only this symbol (default: all)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Only process rows not yet in v7_extras table")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)

        # Determine symbol list
        if args.symbol is not None:
            symbols = [args.symbol]
        else:
            cur = conn.execute(
                "SELECT DISTINCT symbol FROM feature_observations_v6 ORDER BY symbol"
            )
            symbols = [r[0] for r in cur.fetchall()]
        LOG.info("Processing %d symbols: %s", len(symbols), ", ".join(symbols))

        total_inserted = 0
        for sym in symbols:
            try:
                n = extract_symbol(conn, sym, skip_existing=args.skip_existing)
                total_inserted += n
            except Exception as e:
                LOG.error("[%s] FAILED: %s", sym, e)
                import traceback
                traceback.print_exc()

        # Final summary
        cur = conn.execute("SELECT COUNT(*) FROM feature_observations_v7_extras")
        total_rows = cur.fetchone()[0]
        LOG.info("=" * 60)
        LOG.info("DONE. Inserted %d new rows. Total table size: %d", total_inserted, total_rows)

        # Per-symbol stats
        cur = conn.execute(
            "SELECT symbol, COUNT(*), "
            "AVG(funding_rate), AVG(funding_rate_z), "
            "AVG(oi_change_1h), AVG(oi_change_4h) "
            "FROM feature_observations_v7_extras GROUP BY symbol ORDER BY symbol"
        )
        LOG.info("Per-symbol stats:")
        LOG.info("  %-12s %8s %12s %12s %12s %12s",
                 "symbol", "rows", "fund_rate", "fund_z", "oi_1h", "oi_4h")
        for r in cur.fetchall():
            LOG.info("  %-12s %8d %12.6f %12.4f %12.4f %12.4f", *r)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
