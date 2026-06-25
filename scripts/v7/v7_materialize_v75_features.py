"""
v7_materialize_v75_features.py — One-time materialization of v7.5 dataset to parquet.

WHY
---
Loading 1.44M rows × 71 features via SQL JOIN + json_extract takes ~5min and
~5GB RAM. By materializing to parquet once, subsequent loads (training, backtest)
take ~1-2s and ~500MB RAM.

OUTPUT
------
data/v7_models/v75/v75_features.parquet  (~250MB)
  columns: symbol, ts, window, fwd_ret_3, <71 features>
  filtered to fwd_ret_3 IS NOT NULL (all labels, no sign filter)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

DB_PATH = os.environ.get("PPMT_DB_PATH", "/home/z/my-project/data/ppmt.db")
OUTPUT_DIR = Path("/home/z/my-project/data/v7_models/v75")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PARQUET_PATH = OUTPUT_DIR / "v75_features.parquet"

FEATURE_NAMES_V5 = [
    "body_pct", "upper_wick", "lower_wick", "body_abs", "close_pos", "range_pct",
    "ret_1", "ret_3", "ret_5", "ret_10", "log_ret_1",
    "atr_pct", "vol_std_10", "rsi_14",
    "ema_9_20_cross", "ema_20_50_cross", "ema_9_slope", "ema_20_slope", "ema_50_slope",
    "price_vs_ema20", "price_vs_ema50", "vol_ratio", "vol_z",
    "last_3_body_sum", "last_3_range_sum",
    "bullish_engulf_2", "hammer_like", "shooting_star",
    "breakout_up", "breakout_down", "dist_to_high_20", "dist_to_low_20",
    "trend_50", "vol_regime", "trending",
    "hour_sin", "hour_cos", "dow",
]
FEATURE_NAMES_V6_NEW = [
    "btc_ret_1m", "btc_ret_5m", "btc_ret_15m", "btc_vol_z",
    "btc_trend_50", "eth_corr_30", "btc_alt_spread_15m", "btc_volatility_regime",
    "vol_delta_3", "wick_imbalance_3", "body_consistency_5",
    "range_expansion_3", "close_persistence_5", "vol_acceleration",
    "atr_percentile_50", "trend_strength_50", "regime_vol_trend", "hour_quantile",
    "alt_lead_5m", "alt_lag_signal", "momentum_dispersion",
]
FEATURE_NAMES_V6 = FEATURE_NAMES_V5 + FEATURE_NAMES_V6_NEW
FEATURE_NAMES_F4 = [
    "funding_rate", "funding_rate_z",
    "oi_change_1h", "oi_change_4h",
    "sector_blue_chip", "sector_large_cap", "sector_old_meme", "sector_new_meme",
    "sector_idx",
    "day_of_week_sin", "day_of_week_cos", "day_of_week",
]
FEATURE_NAMES = FEATURE_NAMES_V6 + FEATURE_NAMES_F4
LABEL = "fwd_ret_3"


def main():
    if PARQUET_PATH.exists():
        print(f"Parquet already exists: {PARQUET_PATH} ({PARQUET_PATH.stat().st_size/1e6:.1f} MB)")
        print("Delete it to force re-materialization.")
        return

    print(f"Materializing v7.5 dataset to {PARQUET_PATH}...")
    t0 = time.time()

    # Get all symbols
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM feature_observations_v6 ORDER BY symbol")
    symbols = [r[0] for r in cur.fetchall()]
    print(f"Found {len(symbols)} symbols: {symbols}")

    v6_feat_cols = ", ".join(
        [f"json_extract(v6.features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES_V6]
    )
    f4_cols_sql = ", ".join([f"e.{f}" for f in FEATURE_NAMES_F4])

    all_dfs = []
    for i, sym in enumerate(symbols, 1):
        t_sym = time.time()
        sql = f"""
            SELECT v6.symbol, v6.ts, v6.window, v6.{LABEL},
                   {v6_feat_cols},
                   {f4_cols_sql}
            FROM feature_observations_v6 AS v6
            INNER JOIN feature_observations_v7_extras AS e
              ON v6.symbol = e.symbol AND v6.ts = e.ts
            WHERE v6.{LABEL} IS NOT NULL
              AND v6.symbol = ?
        """
        df_sym = pd.read_sql_query(sql, conn, params=(sym,))
        for f in FEATURE_NAMES + [LABEL]:
            df_sym[f] = pd.to_numeric(df_sym[f], errors="coerce").replace([np.inf, -np.inf], 0).fillna(0).astype(np.float32)
        all_dfs.append(df_sym)
        print(f"  [{i}/{len(symbols)}] {sym}: {len(df_sym):,} rows in {time.time()-t_sym:.1f}s")

    conn.close()

    print("Concatenating...")
    df = pd.concat(all_dfs, ignore_index=True)
    del all_dfs
    print(f"Total: {len(df):,} rows × {len(df.columns)} cols")

    print(f"Writing parquet...")
    df.to_parquet(PARQUET_PATH, index=False)
    print(f"Done in {time.time()-t0:.1f}s. Size: {PARQUET_PATH.stat().st_size/1e6:.1f} MB")
    print(f"Label stats: mean={df[LABEL].mean():.4f}% std={df[LABEL].std():.4f}%")
    print(f"Windows: {df['window'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
