"""
v11_build_dataset.py — Build dataset for low-timeframe trading (1h-6h horizons).

KEY INNOVATIONS vs v7.5:
  1. 1m candle base — microstructure features (volume delta, CVD, price impact)
  2. Multi-timeframe aggregation from 1m (5m/15m/1h all from 1m data)
  3. Intermediate horizons: H=12 (1h), H=36 (3h), H=72 (6h) — the "marginal" zone
  4. Microstructure features: CVD, volume delta, order flow acceleration
  5. Maker fees (0.04%) — essential for short horizons

FIXED (v2):
  - Robust timestamp handling: auto-detect ms vs seconds vs datetime
  - All cross-DataFrame merges use merge_asof (no exact merge on timestamp)
  - Deduplication after every step
  - Validation of timestamp range before saving

DATA SOURCES:
  - 1m OHLCV from v10 cache (data/v10/ohlcv_cache/{SYMBOL}_1m.parquet)
  - BTC 1m as reference
  - All features computed from 1m candles, aggregated into multi-timeframe

OUTPUT:
  - data/v11/v11_dataset.parquet — full dataset with all features + labels

USAGE:
    python scripts/v11/v11_build_dataset.py
    python scripts/v11/v11_build_dataset.py --symbols SOL,DOGE,AVAX
    python scripts/v11/v11_build_dataset.py --horizons 12
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "v10" / "ohlcv_cache"
OUTPUT_DIR = DATA_DIR / "v11"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v11_build")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Symbols with 1m data available
DEFAULT_SYMBOLS = ["SOL", "DOGE", "AVAX"]  # BTC excluded (dead end), ETH not in cache

# Horizons on 5m bars (we aggregate 1m→5m for trading)
DEFAULT_HORIZONS = [12, 36, 72, 288]  # 1h, 3h, 6h, 24h


# ============================================================================
# FEATURE SPECIFICATION
# ============================================================================

FEATURES_MICRO_1M = [
    "ret_1m", "ret_3m", "ret_5m",
    "vol_delta_1m", "vol_delta_5m",
    "body_pct_1m", "wick_ratio_1m",
    "cvd_1m", "cvd_5m",
    "price_impact_1m",
    "micro_momentum_5m",
    "vol_clustering_10m",
    "order_flow_accel_5m",
]

FEATURES_5M_V5 = [
    "body_pct", "upper_wick", "lower_wick", "body_abs", "close_pos", "range_pct",
    "ret_1", "ret_3", "ret_5", "ret_10", "log_ret_1",
    "atr_pct", "vol_std_10", "rsi_14",
    "ema_9_20_cross", "ema_20_50_cross", "ema_9_slope", "ema_20_slope", "ema_50_slope",
    "price_vs_ema20", "price_vs_ema50", "vol_ratio", "vol_z",
    "last_3_body_sum", "last_3_range_sum",
    "bullish_engulf_2", "hammer_like", "shooting_star",
    "breakout_up", "breakout_down", "dist_to_high_20", "dist_to_low_20",
    "trend_50", "vol_regime", "trending",
    "hour_sin", "hour_cos",
]

FEATURES_5M_V6 = [
    "btc_ret_1m", "btc_ret_5m", "btc_ret_15m", "btc_vol_z",
    "btc_trend_50", "eth_corr_30", "btc_alt_spread_15m", "btc_volatility_regime",
    "vol_delta_3", "wick_imbalance_3", "body_consistency_5",
    "range_expansion_3", "close_persistence_5", "vol_acceleration",
    "atr_percentile_50", "trend_strength_50", "regime_vol_trend", "hour_quantile",
    "alt_lead_5m", "alt_lag_signal", "momentum_dispersion",
]

FEATURES_15M = [
    "trend_15m",
    "rsi_15m",
    "vol_regime_15m",
    "btc_trend_15m",
]

FEATURES_1H = [
    "trend_1h",
    "rsi_1h",
    "vol_regime_1h",
    "btc_trend_1h",
    "mtf_alignment",
]

ALL_FEATURE_NAMES = (
    FEATURES_MICRO_1M + FEATURES_5M_V5 + FEATURES_5M_V6 +
    FEATURES_15M + FEATURES_1H
)


# ============================================================================
# TIMESTAMP UTILITIES (ROBUST)
# ============================================================================

def normalize_timestamps_ms(df: pd.DataFrame, col: str = "timestamp") -> pd.DataFrame:
    """Ensure timestamp column is int64 milliseconds since epoch.
    
    Handles all common formats:
    - int64 milliseconds (expected: values > 1e12 for recent dates)
    - int64 seconds (values ~1.7e9 for 2024-2026)
    - datetime64[ns] (pandas Timestamp type)
    - datetime64 with timezone
    """
    df = df.copy()
    ts = df[col]
    
    if pd.api.types.is_datetime64_any_dtype(ts):
        # Already datetime — convert to ms
        df[col] = ts.astype(np.int64) // 10**6
        LOG.debug("  normalize_timestamps: datetime64 → int64 ms")
    elif pd.api.types.is_numeric_dtype(ts):
        # Numeric — check if seconds or milliseconds
        median_val = ts.median()
        if median_val > 1e12:
            # Already in milliseconds (e.g., 1750975200000)
            df[col] = ts.astype(np.int64)
            LOG.debug("  normalize_timestamps: already ms int64")
        elif median_val > 1e9:
            # In seconds (e.g., 1750975200) — convert to ms
            df[col] = (ts * 1000).astype(np.int64)
            LOG.info("  normalize_timestamps: seconds → ms (multiplied by 1000)")
        else:
            # Values too small — probably corrupted, but try to handle
            LOG.warning("  normalize_timestamps: unexpected values (median=%.0f) — attempting x1000000",
                        median_val)
            df[col] = (ts * 1_000_000).astype(np.int64)
    else:
        # String or object type
        try:
            df[col] = pd.to_datetime(ts, utc=True).astype(np.int64) // 10**6
            LOG.info("  normalize_timestamps: string/object → ms")
        except Exception as e:
            raise ValueError(f"Cannot normalize timestamps: {e}")
    
    # Validate: timestamps should be in a reasonable range (2020-2030)
    ts_ms = df[col].values
    ts_min = ts_ms.min()
    ts_max = ts_ms.max()
    # 2020-01-01 = 1577836800000 ms, 2035-01-01 = 2051222400000 ms
    if ts_min < 1577836800000 or ts_max > 2051222400000:
        LOG.warning("  Timestamps out of expected range: min=%d max=%d", ts_min, ts_max)
    
    return df


def ts_to_datetime(ts_ms: pd.Series | np.ndarray) -> pd.DatetimeIndex:
    """Convert millisecond timestamps to datetime (UTC)."""
    return pd.to_datetime(ts_ms, unit="ms", utc=True)


# ============================================================================
# 1m MICROSTRUCTURE FEATURES
# ============================================================================

def compute_microstructure_features(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Compute microstructure features on 1m candles."""
    df = df_1m.copy().reset_index(drop=True)
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    
    df["ret_1m"] = c.pct_change(1)
    df["ret_3m"] = c.pct_change(3)
    df["ret_5m"] = c.pct_change(5)
    
    rng = (h - l).replace(0, 1e-10)
    df["body_pct_1m"] = (c - o) / rng
    df["wick_ratio_1m"] = ((h - np.maximum(o, c)) + (np.minimum(o, c) - l)) / rng
    
    direction = np.sign(c - o)
    df["vol_delta_1m"] = (v * direction).diff(1)
    df["vol_delta_5m"] = (v * direction).rolling(5).sum() - (v * direction).shift(1).rolling(5).sum()
    
    signed_vol = v * direction
    df["cvd_1m"] = signed_vol.rolling(60, min_periods=1).sum()
    df["cvd_5m"] = signed_vol.rolling(300, min_periods=1).sum()
    
    price_move = (c - o).abs()
    df["price_impact_1m"] = (price_move / v.replace(0, 1e-10)).rolling(10, min_periods=1).mean()
    
    df["micro_momentum_5m"] = (c - c.shift(5)) / c.shift(5).replace(0, 1e-10) * 100
    
    vol_ma = v.rolling(10, min_periods=1).mean().replace(0, 1e-10)
    df["vol_clustering_10m"] = (v / vol_ma).clip(0, 10)
    
    df["order_flow_accel_5m"] = df["cvd_1m"].diff(5) / df["cvd_1m"].shift(5).replace(0, 1e-10).abs().clip(lower=1e-10)
    df["order_flow_accel_5m"] = df["order_flow_accel_5m"].clip(-10, 10)
    
    for col in FEATURES_MICRO_1M:
        if col in df.columns:
            df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0)
    
    return df


# ============================================================================
# AGGREGATION (ROBUST)
# ============================================================================

def _datetime_to_ms(series: pd.Series) -> np.ndarray:
    """Convert a datetime Series to int64 milliseconds since epoch.
    
    Handles both tz-aware and tz-naive datetime in all pandas versions.
    Uses .values.astype(np.int64) which always gives nanoseconds from numpy,
    bypassing pandas timezone quirks.
    """
    arr = series.values  # numpy datetime64[ns] array
    ns = arr.astype(np.int64)  # nanoseconds since epoch
    return (ns // 10**6).astype(np.int64)  # milliseconds


def aggregate_1m_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1m candles into 5m candles. Timestamps always in int64 ms."""
    df = df_1m.copy()
    
    # Ensure timestamp is int64 ms first
    df = normalize_timestamps_ms(df, "timestamp")
    
    # Convert to datetime for resample
    df["timestamp"] = ts_to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    
    agg = df.resample("5min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    
    agg = agg.reset_index()
    # Convert datetime back to int64 ms (robust for all pandas versions)
    agg["timestamp"] = _datetime_to_ms(agg["timestamp"])
    
    return agg


def aggregate_5m_to_n(df_5m: pd.DataFrame, n: int) -> pd.DataFrame:
    """Aggregate 5m candles into n-minute candles."""
    df = df_5m.copy()
    df = normalize_timestamps_ms(df, "timestamp")
    
    df["timestamp"] = ts_to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    
    agg = df.resample(f"{n}min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    
    agg = agg.reset_index()
    # Convert datetime back to int64 ms (robust for all pandas versions)
    agg["timestamp"] = _datetime_to_ms(agg["timestamp"])
    
    return agg


# ============================================================================
# MERGE UTILITY (NO ROW DUPLICATION)
# ============================================================================

def safe_merge_asof(left: pd.DataFrame, right: pd.DataFrame,
                    on: str = "timestamp") -> pd.DataFrame:
    """Merge using merge_asof — never duplicates rows.
    
    Unlike df.merge(), merge_asof does a fuzzy time-based join
    that cannot create row multiplication.
    """
    # Ensure timestamps are int64 ms on both sides
    left = normalize_timestamps_ms(left, on)
    right = normalize_timestamps_ms(right, on)
    
    # Sort both by timestamp (required for merge_asof)
    left_sorted = left.sort_values(on).reset_index(drop=True)
    right_sorted = right.sort_values(on).reset_index(drop=True)
    
    # Remove duplicate timestamps from right (keep last)
    right_sorted = right_sorted.drop_duplicates(subset=[on], keep="last")
    
    merged = pd.merge_asof(
        left_sorted,
        right_sorted,
        on=on,
        direction="backward",  # use last completed value
    )
    
    return merged


# ============================================================================
# 5m OHLCV FEATURES
# ============================================================================

def compute_5m_features(df_5m: pd.DataFrame, btc_5m: pd.DataFrame, eth_5m: pd.DataFrame) -> pd.DataFrame:
    """Compute all 5m features + BTC/ETH cross-asset features.
    
    All merges use merge_asof to prevent row duplication.
    """
    df = df_5m.copy().reset_index(drop=True)
    df = normalize_timestamps_ms(df, "timestamp")
    
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    
    rng = (h - l).replace(0, 1e-10)
    body = (c - o)
    df["body_pct"]     = body / rng
    df["upper_wick"]   = (h - np.maximum(o, c)) / rng
    df["lower_wick"]   = (np.minimum(o, c) - l) / rng
    df["body_abs"]     = body.abs() / rng
    df["close_pos"]    = (c - l) / rng
    df["range_pct"]    = rng / c * 100

    df["ret_1"]  = c.pct_change(1)
    df["ret_3"]  = c.pct_change(3)
    df["ret_5"]  = c.pct_change(5)
    df["ret_10"] = c.pct_change(10)
    df["log_ret_1"] = np.log(c / c.shift(1))

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr_14"]   = tr.rolling(14).mean()
    df["atr_pct"]  = df["atr_14"] / c * 100
    df["vol_std_10"] = df["log_ret_1"].rolling(10).std()

    delta = c.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-10)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    for p in [9, 20, 50]:
        df[f"ema_{p}"] = c.ewm(span=p, adjust=False).mean()
        df[f"ema_{p}_slope"] = df[f"ema_{p}"].pct_change(3)
    df["ema_9_20_cross"] = (df["ema_9"] - df["ema_20"]) / c * 100
    df["ema_20_50_cross"] = (df["ema_20"] - df["ema_50"]) / c * 100
    df["price_vs_ema20"] = (c - df["ema_20"]) / c * 100
    df["price_vs_ema50"] = (c - df["ema_50"]) / c * 100

    vol_ma = v.rolling(20).mean().replace(0, 1e-10)
    df["vol_ratio"] = v / vol_ma
    df["vol_z"] = (v - vol_ma) / v.rolling(20).std().replace(0, 1e-10)

    df["last_3_body_sum"] = df["body_pct"].rolling(3).sum()
    df["last_3_range_sum"] = df["range_pct"].rolling(3).sum()

    df["bullish_engulf_2"] = ((df["body_pct"].shift(1) < 0) & (df["body_pct"] > 0) &
                              (df["close"] > df["open"].shift(1)) &
                              (df["open"] < df["close"].shift(1))).astype(int)
    df["hammer_like"] = ((df["lower_wick"] > 2 * df["body_abs"]) & (df["body_abs"] > 0)).astype(int)
    df["shooting_star"] = ((df["upper_wick"] > 2 * df["body_abs"]) & (df["body_abs"] > 0)).astype(int)

    df["high_20"] = h.rolling(20).max()
    df["low_20"]  = l.rolling(20).min()
    df["breakout_up"]   = (h > df["high_20"].shift(1)).astype(int)
    df["breakout_down"] = (l < df["low_20"].shift(1)).astype(int)
    df["dist_to_high_20"] = (c - df["high_20"]) / df["high_20"] * 100
    df["dist_to_low_20"]  = (c - df["low_20"])  / df["low_20"]  * 100

    df["trend_50"] = np.sign(df["ema_9"] - df["ema_50"]).astype(int)
    atr_p = df["atr_pct"].fillna(0).values
    bins = [0.5, 1.5, 5.0]
    df["vol_regime"] = np.digitize(atr_p, bins).astype(int)
    df["trending"] = (df["atr_pct"] > df["atr_pct"].rolling(50, min_periods=5).mean()).astype(int)

    # Hour features (from timestamp)
    ts_dt = ts_to_datetime(df["timestamp"])
    df["hour_sin"] = np.sin(2 * np.pi * ts_dt.dt.hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * ts_dt.dt.hour / 24)
    
    # === BTC/ETH cross-asset features (using merge_asof) ===
    btc = btc_5m.copy()
    btc = normalize_timestamps_ms(btc, "timestamp")
    btc = btc.rename(columns={
        "close": "btc_close", "volume": "btc_volume",
        "high": "btc_high", "low": "btc_low",
    })
    btc["btc_ret_1m"]  = btc["btc_close"].pct_change(1)
    btc["btc_ret_5m"]  = btc["btc_close"].pct_change(5)
    btc["btc_ret_15m"] = btc["btc_close"].pct_change(15)
    btc_vol_ma = btc["btc_volume"].rolling(20).mean().replace(0, 1e-10)
    btc["btc_vol_z"] = (btc["btc_volume"] - btc_vol_ma) / btc["btc_volume"].rolling(20).std().replace(0, 1e-10)
    btc["btc_ema_9"]  = btc["btc_close"].ewm(span=9,  adjust=False).mean()
    btc["btc_ema_50"] = btc["btc_close"].ewm(span=50, adjust=False).mean()
    btc["btc_trend_50"] = np.sign(btc["btc_ema_9"] - btc["btc_ema_50"]).astype(int)
    btc_atr = (btc["btc_high"] - btc["btc_low"]).rolling(14).mean()
    btc_atr_pct = btc_atr / btc["btc_close"] * 100
    btc["btc_volatility_regime"] = np.digitize(btc_atr_pct.fillna(0).values, [0.5, 1.5, 5.0]).astype(int)
    btc_cols = ["timestamp", "btc_ret_1m", "btc_ret_5m", "btc_ret_15m",
                "btc_vol_z", "btc_trend_50", "btc_volatility_regime"]
    btc = btc[btc_cols]
    
    # Use merge_asof instead of merge (prevents row duplication)
    n_before = len(df)
    df = safe_merge_asof(df, btc, on="timestamp")
    assert len(df) == n_before, f"Row count changed after BTC merge: {n_before} → {len(df)}"
    
    alt_ret_15m_pct = df["close"].pct_change(15) * 100
    btc_ret_15m_pct = df["btc_ret_15m"] * 100
    df["btc_alt_spread_15m"] = alt_ret_15m_pct - btc_ret_15m_pct
    
    # ETH correlation
    eth = eth_5m[["timestamp", "close"]].copy()
    eth = normalize_timestamps_ms(eth, "timestamp")
    eth = eth.rename(columns={"close": "eth_close"})
    
    n_before = len(df)
    df = safe_merge_asof(df, eth, on="timestamp")
    assert len(df) == n_before, f"Row count changed after ETH merge: {n_before} → {len(df)}"
    
    alt_ret = df["close"].pct_change()
    eth_ret = df["eth_close"].pct_change()
    df["eth_corr_30"] = alt_ret.rolling(30).corr(eth_ret)
    
    # Remaining v6 features
    df["vol_delta_3"] = df["volume"].pct_change(3).replace([np.inf, -np.inf], np.nan).fillna(0).clip(-5, 5)
    if "lower_wick" in df.columns and "upper_wick" in df.columns:
        df["wick_imbalance_3"] = (df["lower_wick"] - df["upper_wick"]).rolling(3).sum()
    else:
        df["wick_imbalance_3"] = 0.0
    body_sign = (df["close"] - df["open"] > 0).astype(float)
    df["body_consistency_5"] = body_sign.rolling(5).mean()
    avg_rng_3 = df["range_pct"].rolling(3).mean()
    avg_rng_20 = df["range_pct"].rolling(20).mean().replace(0, 1e-10)
    df["range_expansion_3"] = (avg_rng_3 / avg_rng_20).clip(0, 10)
    above_ema = (df["close"] > df["ema_20"]).astype(float)
    df["close_persistence_5"] = above_ema.rolling(5).mean()
    df["vol_acceleration"] = df["vol_ratio"] - df["vol_ratio"].shift(3)
    
    df["atr_percentile_50"] = df["atr_pct"].rolling(50, min_periods=5).rank(pct=True)
    df["trend_strength_50"] = ((df["ema_9"] - df["ema_50"]).abs() / df["atr_pct"].replace(0, 1e-10)).clip(0, 20)
    df["regime_vol_trend"] = df["vol_regime"] * df["trend_50"]
    
    ts_dt2 = ts_to_datetime(df["timestamp"])
    hour = ts_dt2.dt.hour
    df["hour_quantile"] = pd.cut(hour, bins=[-1, 8, 14, 22, 24], labels=[0, 1, 2, 3]).astype(int)
    
    df["alt_lead_5m"] = (df["close"].pct_change(5) - df["btc_ret_5m"]) * 100
    df["alt_lag_signal"] = ((df["btc_ret_1m"].abs() > 0.002) &
                             (df["ret_1"].abs() < 0.001)).astype(int)
    df["momentum_dispersion"] = df["ret_1"].rolling(10).std()
    
    # Clean up
    for col in FEATURES_5M_V5 + FEATURES_5M_V6:
        if col in df.columns:
            df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0)
    
    return df


# ============================================================================
# HIGHER TIMEFRAME FEATURES (15m, 1h)
# ============================================================================

def compute_higher_tf_features(df_5m: pd.DataFrame, btc_5m: pd.DataFrame) -> pd.DataFrame:
    """Compute 15m and 1h features, merge back to 5m index.
    
    All merges use merge_asof to prevent row duplication.
    """
    df = df_5m.copy()
    df = normalize_timestamps_ms(df, "timestamp")
    btc_5m = normalize_timestamps_ms(btc_5m, "timestamp")
    
    # --- 15m features ---
    df_15m = aggregate_5m_to_n(df_5m, 15)
    df_15m["ema_9_15m"] = df_15m["close"].ewm(span=9, adjust=False).mean()
    df_15m["ema_20_15m"] = df_15m["close"].ewm(span=20, adjust=False).mean()
    df_15m["trend_15m"] = np.sign(df_15m["ema_9_15m"] - df_15m["ema_20_15m"]).astype(int)
    
    delta_15 = df_15m["close"].diff()
    gain_15 = delta_15.where(delta_15 > 0, 0).rolling(14).mean()
    loss_15 = (-delta_15.where(delta_15 < 0, 0)).rolling(14).mean()
    rs_15 = gain_15 / loss_15.replace(0, 1e-10)
    df_15m["rsi_15m"] = 100 - (100 / (1 + rs_15))
    
    atr_15 = (df_15m["high"] - df_15m["low"]).rolling(14).mean()
    atr_pct_15 = atr_15 / df_15m["close"] * 100
    df_15m["vol_regime_15m"] = np.digitize(atr_pct_15.fillna(0).values, [0.5, 1.5, 5.0]).astype(int)
    
    btc_15m = aggregate_5m_to_n(btc_5m, 15)
    btc_15m["btc_ema_9_15m"] = btc_15m["close"].ewm(span=9, adjust=False).mean()
    btc_15m["btc_ema_20_15m"] = btc_15m["close"].ewm(span=20, adjust=False).mean()
    btc_15m["btc_trend_15m"] = np.sign(btc_15m["btc_ema_9_15m"] - btc_15m["btc_ema_20_15m"]).astype(int)
    # Use merge_asof instead of direct array assignment (avoids length mismatch)
    df_15m = safe_merge_asof(df_15m, btc_15m[["timestamp", "btc_trend_15m"]], on="timestamp")
    
    # Merge 15m features into 5m (using merge_asof)
    htf_15m_cols = ["timestamp"] + FEATURES_15M
    df_15m_subset = df_15m[htf_15m_cols].copy()
    
    n_before = len(df)
    df = safe_merge_asof(df, df_15m_subset, on="timestamp")
    assert len(df) == n_before, f"Row count changed after 15m merge: {n_before} → {len(df)}"
    
    # --- 1h features ---
    df_1h = aggregate_5m_to_n(df_5m, 60)
    df_1h["ema_9_1h"] = df_1h["close"].ewm(span=9, adjust=False).mean()
    df_1h["ema_20_1h"] = df_1h["close"].ewm(span=20, adjust=False).mean()
    df_1h["trend_1h"] = np.sign(df_1h["ema_9_1h"] - df_1h["ema_20_1h"]).astype(int)
    
    delta_1h = df_1h["close"].diff()
    gain_1h = delta_1h.where(delta_1h > 0, 0).rolling(14).mean()
    loss_1h = (-delta_1h.where(delta_1h < 0, 0)).rolling(14).mean()
    rs_1h = gain_1h / loss_1h.replace(0, 1e-10)
    df_1h["rsi_1h"] = 100 - (100 / (1 + rs_1h))
    
    atr_1h = (df_1h["high"] - df_1h["low"]).rolling(14).mean()
    atr_pct_1h = atr_1h / df_1h["close"] * 100
    df_1h["vol_regime_1h"] = np.digitize(atr_pct_1h.fillna(0).values, [0.5, 1.5, 5.0]).astype(int)
    
    btc_1h = aggregate_5m_to_n(btc_5m, 60)
    btc_1h["btc_ema_9_1h"] = btc_1h["close"].ewm(span=9, adjust=False).mean()
    btc_1h["btc_ema_20_1h"] = btc_1h["close"].ewm(span=20, adjust=False).mean()
    btc_1h["btc_trend_1h"] = np.sign(btc_1h["btc_ema_9_1h"] - btc_1h["btc_ema_20_1h"]).astype(int)
    # Use merge_asof instead of direct array assignment (avoids length mismatch)
    df_1h = safe_merge_asof(df_1h, btc_1h[["timestamp", "btc_trend_1h"]], on="timestamp")
    
    # Merge 1h features into 5m
    htf_1h_cols = ["timestamp", "trend_1h", "rsi_1h", "vol_regime_1h", "btc_trend_1h"]
    df_1h_subset = df_1h[htf_1h_cols].copy()
    
    n_before = len(df)
    df = safe_merge_asof(df, df_1h_subset, on="timestamp")
    assert len(df) == n_before, f"Row count changed after 1h merge: {n_before} → {len(df)}"
    
    # MTF alignment
    df["mtf_alignment"] = (
        df["trend_50"].astype(float) +
        df["trend_15m"].astype(float) +
        df["trend_1h"].astype(float)
    ) / 3.0
    
    # Clean up
    for col in FEATURES_15M + FEATURES_1H:
        if col in df.columns:
            df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0).astype(np.float32)
    
    return df


# ============================================================================
# FORWARD RETURN LABELS
# ============================================================================

def compute_labels(df_5m: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """Compute forward returns for multiple horizons."""
    df = df_5m.copy()
    c = df["close"].values
    n = len(df)
    
    for h in horizons:
        fwd = np.full(n, np.nan)
        for i in range(n - h):
            fwd[i] = (c[i + h] - c[i]) / c[i] * 100
        df[f"fwd_ret_h{h}"] = fwd
        df[f"label_h{h}"] = (fwd > 0).astype(int)
    
    return df


# ============================================================================
# MERGE 1m MICRO INTO 5m
# ============================================================================

def merge_micro_into_5m(df_5m: pd.DataFrame, df_1m_micro: pd.DataFrame) -> pd.DataFrame:
    """Merge 1m microstructure features into 5m dataframe using merge_asof.
    
    For each 5m bar, we take the LAST 1m candle's micro features.
    """
    # Normalize timestamps
    df_5m = normalize_timestamps_ms(df_5m, "timestamp")
    df_1m_micro = normalize_timestamps_ms(df_1m_micro, "timestamp")
    
    # Keep only needed columns from 1m
    micro_cols = ["timestamp"] + FEATURES_MICRO_1M
    df_1m_subset = df_1m_micro[micro_cols].copy()
    
    # Remove duplicate timestamps (keep last = most recent 1m bar)
    df_1m_subset = df_1m_subset.drop_duplicates(subset=["timestamp"], keep="last")
    
    # Use merge_asof
    n_before = len(df_5m)
    merged = safe_merge_asof(df_5m, df_1m_subset, on="timestamp")
    assert len(merged) == n_before, f"Row count changed after micro merge: {n_before} → {len(merged)}"
    
    # Ensure all micro features are present
    for col in FEATURES_MICRO_1M:
        if col not in merged.columns:
            LOG.warning("Missing micro feature: %s", col)
            merged[col] = 0.0
        else:
            merged[col] = merged[col].replace([np.inf, -np.inf], np.nan).fillna(0).astype(np.float32)
    
    return merged


# ============================================================================
# MAIN
# ============================================================================

def load_1m_data(symbol: str) -> pd.DataFrame:
    """Load 1m OHLCV from v10 cache."""
    path = CACHE_DIR / f"{symbol}_1m.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No 1m data for {symbol} at {path}")
    df = pd.read_parquet(path)
    
    # Normalize timestamps to int64 ms
    df = normalize_timestamps_ms(df, "timestamp")
    
    LOG.info("Loaded %s_1m: %d rows (ts: %d to %d)",
             symbol, len(df), df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
    return df


def build_symbol_dataset(
    symbol: str,
    horizons: list[int],
    btc_1m: pd.DataFrame,
    eth_5m: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build complete dataset for one symbol."""
    LOG.info("Building dataset for %s", symbol)
    t0 = time.time()
    
    # Load 1m data
    sym_1m = load_1m_data(symbol)
    
    # 1. Compute microstructure features on 1m
    sym_1m_micro = compute_microstructure_features(sym_1m)
    
    # 2. Aggregate to 5m
    sym_5m = aggregate_1m_to_5m(sym_1m)
    btc_5m = aggregate_1m_to_5m(btc_1m)
    
    LOG.info("  %s: %d 5m bars, BTC: %d 5m bars", symbol, len(sym_5m), len(btc_5m))
    
    # Create a dummy ETH 5m if not available (use BTC as proxy)
    if eth_5m is None:
        LOG.warning("No ETH data — using BTC as ETH proxy for correlation features")
        eth_5m = btc_5m[["timestamp", "close"]].copy()
    
    # Ensure timestamp types match
    sym_5m = normalize_timestamps_ms(sym_5m, "timestamp")
    btc_5m = normalize_timestamps_ms(btc_5m, "timestamp")
    eth_5m = normalize_timestamps_ms(eth_5m, "timestamp")
    
    # 3. Compute 5m features (v7 58 features + BTC/ETH cross-asset)
    sym_5m_feat = compute_5m_features(sym_5m, btc_5m, eth_5m)
    LOG.info("  After compute_5m_features: %d rows", len(sym_5m_feat))
    
    # 4. Compute higher timeframe features (15m, 1h)
    sym_5m_feat = compute_higher_tf_features(sym_5m_feat, btc_5m)
    LOG.info("  After compute_higher_tf_features: %d rows", len(sym_5m_feat))
    
    # 5. Merge 1m microstructure into 5m
    sym_1m_micro = normalize_timestamps_ms(sym_1m_micro, "timestamp")
    sym_5m_full = merge_micro_into_5m(sym_5m_feat, sym_1m_micro)
    LOG.info("  After merge_micro_into_5m: %d rows", len(sym_5m_full))
    
    # 6. Compute forward return labels
    sym_5m_full = compute_labels(sym_5m_full, horizons)
    
    # 7. Add symbol column
    sym_5m_full["symbol"] = symbol
    
    # 8. Clean and validate
    for col in ALL_FEATURE_NAMES:
        if col not in sym_5m_full.columns:
            LOG.warning("Missing feature: %s — filling with 0", col)
            sym_5m_full[col] = 0.0
        else:
            sym_5m_full[col] = sym_5m_full[col].replace([np.inf, -np.inf], np.nan).fillna(0).astype(np.float32)
    
    elapsed = time.time() - t0
    LOG.info("  %s: %d rows × %d features in %.1fs", symbol, len(sym_5m_full), len(ALL_FEATURE_NAMES), elapsed)
    
    # Validate timestamps
    ts = sym_5m_full["timestamp"].values
    ts_min, ts_max = ts.min(), ts.max()
    span_days = (ts_max - ts_min) / (1000 * 86400)
    if span_days < 30:
        LOG.error("  %s: timestamp span only %.1f days — DATA MAY BE CORRUPT!", symbol, span_days)
    else:
        LOG.info("  %s: timestamp span %.1f days ✓", symbol, span_days)
    
    return sym_5m_full


def main():
    parser = argparse.ArgumentParser(description="Build v11 dataset for low-timeframe trading")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS))
    parser.add_argument("--output", default=str(OUTPUT_DIR / "v11_dataset.parquet"))
    args = parser.parse_args()
    
    symbols = args.symbols.split(",")
    horizons = [int(h) for h in args.horizons.split(",")]
    
    print("=" * 80)
    print("v11 BUILD DATASET — Low Timeframe Trading")
    print(f"  Symbols: {symbols}")
    print(f"  Horizons: {horizons} (5m bars → {', '.join(f'{h*5/60:.0f}h' for h in horizons)})")
    print(f"  Features: {len(ALL_FEATURE_NAMES)} ({len(FEATURES_MICRO_1M)} micro + "
          f"{len(FEATURES_5M_V5) + len(FEATURES_5M_V6)} 5m + "
          f"{len(FEATURES_15M)} 15m + {len(FEATURES_1H)} 1h)")
    print("=" * 80)
    
    # Load BTC 1m (reference for all symbols)
    btc_1m = load_1m_data("BTC")
    
    # Build per-symbol datasets
    all_dfs = []
    for sym in symbols:
        LOG.info(">>> Building dataset for %s...", sym)
        try:
            df = build_symbol_dataset(sym, horizons, btc_1m)
            if len(df) == 0:
                LOG.error(">>> %s: build returned 0 rows!", sym)
            else:
                LOG.info(">>> %s: SUCCESS — %d rows", sym, len(df))
            all_dfs.append(df)
        except Exception as e:
            LOG.error(">>> %s: FAILED with %s: %s", sym, type(e).__name__, e)
            import traceback
            traceback.print_exc()
    
    if not all_dfs:
        LOG.error("No datasets built — exiting")
        sys.exit(1)
    
    # Combine
    combined = pd.concat(all_dfs, ignore_index=True)
    
    # Drop rows with NaN labels (forward returns near the end of data)
    label_cols = [f"fwd_ret_h{h}" for h in horizons]
    # Keep rows that have at least one valid label
    valid_mask = combined[label_cols].notna().any(axis=1)
    # Also require all features to be valid
    feat_mask = combined[ALL_FEATURE_NAMES].notna().all(axis=1)
    combined = combined[valid_mask & feat_mask].reset_index(drop=True)
    
    # Final timestamp validation
    ts = combined["timestamp"].values
    ts_min, ts_max = ts.min(), ts.max()
    span_days = (ts_max - ts_min) / (1000 * 86400)
    if span_days < 30:
        LOG.error("FINAL: timestamp span only %.1f days — DATASET MAY BE CORRUPT!", span_days)
    else:
        LOG.info("FINAL: timestamp span %.1f days ✓", span_days)
    
    # Deduplicate by (timestamp, symbol) just in case
    n_before = len(combined)
    combined = combined.drop_duplicates(subset=["timestamp", "symbol"], keep="last").reset_index(drop=True)
    if len(combined) < n_before:
        LOG.warning("Removed %d duplicate (timestamp, symbol) rows", n_before - len(combined))
    
    # Save
    output_path = Path(args.output)
    combined.to_parquet(output_path, index=False)
    
    print(f"\n{'='*80}")
    print(f"DATASET SAVED: {output_path}")
    print(f"  Total rows: {len(combined):,}")
    print(f"  Total features: {len(ALL_FEATURE_NAMES)}")
    print(f"  Symbols: {combined['symbol'].unique().tolist()}")
    print(f"  Timestamp span: {span_days:.1f} days")
    print(f"  Per-symbol row counts:")
    for sym in combined["symbol"].unique():
        n = len(combined[combined["symbol"] == sym])
        print(f"    {sym}: {n:,} rows")
    for h in horizons:
        col = f"fwd_ret_h{h}"
        valid = combined[col].notna().sum()
        print(f"  Label {col}: {valid:,} valid rows, "
              f"mean={combined[col].mean():+.4f}%, std={combined[col].std():.4f}%")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
