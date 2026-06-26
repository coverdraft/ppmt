"""
v11_build_dataset.py — Build dataset for low-timeframe trading (1h-6h horizons).

KEY INNOVATIONS vs v7.5:
  1. 1m candle base — microstructure features (volume delta, CVD, price impact)
  2. Multi-timeframe aggregation from 1m (5m/15m/1h all from 1m data)
  3. Intermediate horizons: H=12 (1h), H=36 (3h), H=72 (6h) — the "marginal" zone
  4. Microstructure features: CVD, volume delta, order flow acceleration
  5. Maker fees (0.04%) — essential for short horizons

DATA SOURCES:
  - 1m OHLCV from v10 cache (data/v10/ohlcv_cache/{SYMBOL}_1m.parquet)
  - BTC 1m as reference
  - All features computed from 1m candles, aggregated into multi-timeframe

OUTPUT:
  - data/v11/v11_dataset.parquet — full dataset with all features + labels

USAGE:
    python scripts/v11/v11_build_dataset.py
    python scripts/v11/v11_build_dataset.py --symbols SOL,DOGE,AVAX
    python scripts/v11/v11_build_dataset.py --horizons 12,36,72
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
# H=12 means 12 × 5min = 1h forward, H=36 = 3h, H=72 = 6h
DEFAULT_HORIZONS = [12, 36, 72, 288]  # 1h, 3h, 6h, 24h

# ============================================================================
# FEATURE SPECIFICATION
# ============================================================================

# Group 1: 1m microstructure features (computed on 1m candles)
FEATURES_MICRO_1M = [
    "ret_1m", "ret_3m", "ret_5m",           # micro momentum
    "vol_delta_1m", "vol_delta_5m",          # volume change acceleration
    "body_pct_1m", "wick_ratio_1m",          # 1m candle shape
    "cvd_1m", "cvd_5m",                      # cumulative volume delta
    "price_impact_1m",                        # move per unit volume
    "micro_momentum_5m",                      # 5m directional strength
    "vol_clustering_10m",                     # vol regime shift
    "order_flow_accel_5m",                    # CVD change rate
]

# Group 2: 5m OHLCV features (from v7 paper trader, 58 features)
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

# Group 3: 15m aggregate features (trend context)
FEATURES_15M = [
    "trend_15m",          # 15m EMA direction
    "rsi_15m",            # 15m RSI
    "vol_regime_15m",     # 15m volatility regime
    "btc_trend_15m",      # BTC 15m trend alignment
]

# Group 4: 1h aggregate features (higher timeframe filter)
FEATURES_1H = [
    "trend_1h",           # 1h EMA direction
    "rsi_1h",             # 1h RSI
    "vol_regime_1h",      # 1h volatility regime
    "btc_trend_1h",       # BTC 1h trend
    "mtf_alignment",      # 5m/15m/1h trend alignment score
]

# All feature groups
ALL_FEATURE_GROUPS = {
    "micro_1m": FEATURES_MICRO_1M,
    "5m_v5": FEATURES_5M_V5,
    "5m_v6": FEATURES_5M_V6,
    "15m": FEATURES_15M,
    "1h": FEATURES_1H,
}

ALL_FEATURE_NAMES = (
    FEATURES_MICRO_1M + FEATURES_5M_V5 + FEATURES_5M_V6 +
    FEATURES_15M + FEATURES_1H
)

LABELS = ["fwd_ret_h12", "fwd_ret_h36", "fwd_ret_h72", "fwd_ret_h288"]


# ============================================================================
# 1m MICROSTRUCTURE FEATURES
# ============================================================================

def compute_microstructure_features(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Compute microstructure features on 1m candles.
    
    These capture short-term order flow dynamics that are invisible on 5m+ candles.
    Key insight: on 1m, we can detect:
    - Buying vs selling pressure (volume delta proxy from candle shape)
    - Order flow acceleration (CVD changes)
    - Price impact (how much price moves per unit volume)
    """
    df = df_1m.copy().reset_index(drop=True)
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    
    # 1m returns
    df["ret_1m"] = c.pct_change(1)
    df["ret_3m"] = c.pct_change(3)
    df["ret_5m"] = c.pct_change(5)
    
    # 1m candle shape
    rng = (h - l).replace(0, 1e-10)
    df["body_pct_1m"] = (c - o) / rng
    df["wick_ratio_1m"] = ((h - np.maximum(o, c)) + (np.minimum(o, c) - l)) / rng
    
    # Volume delta proxy: if close > open, assume buy volume; else sell volume
    # This is a rough proxy since we don't have actual tick data
    direction = np.sign(c - o)
    df["vol_delta_1m"] = (v * direction).diff(1)
    df["vol_delta_5m"] = (v * direction).rolling(5).sum() - (v * direction).shift(1).rolling(5).sum()
    
    # Cumulative Volume Delta (CVD) — running sum of signed volume
    signed_vol = v * direction
    df["cvd_1m"] = signed_vol.rolling(60, min_periods=1).sum()  # last 60 min
    df["cvd_5m"] = signed_vol.rolling(300, min_periods=1).sum()  # last 5h
    
    # Price impact: how much price moves per unit volume
    price_move = (c - o).abs()
    df["price_impact_1m"] = (price_move / v.replace(0, 1e-10)).rolling(10, min_periods=1).mean()
    
    # Micro momentum: directional strength over 5m
    df["micro_momentum_5m"] = (c - c.shift(5)) / c.shift(5).replace(0, 1e-10) * 100
    
    # Vol clustering: ratio of current vol to recent average
    vol_ma = v.rolling(10, min_periods=1).mean().replace(0, 1e-10)
    df["vol_clustering_10m"] = (v / vol_ma).clip(0, 10)
    
    # Order flow acceleration: rate of change of CVD
    df["order_flow_accel_5m"] = df["cvd_1m"].diff(5) / df["cvd_1m"].shift(5).replace(0, 1e-10).abs().clip(lower=1e-10)
    df["order_flow_accel_5m"] = df["order_flow_accel_5m"].clip(-10, 10)
    
    # Replace inf/nan
    for col in FEATURES_MICRO_1M:
        if col in df.columns:
            df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0)
    
    return df


# ============================================================================
# 5m OHLCV FEATURES (from v7 paper trader)
# ============================================================================

def aggregate_1m_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1m candles into 5m candles."""
    df = df_1m.copy()
    # Ensure timestamp is datetime
    if df["timestamp"].dtype in [np.float64, np.int64, float, int]:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    elif df["timestamp"].dtype == object:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    
    df = df.set_index("timestamp")
    
    # Resample to 5min
    agg = df.resample("5min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    
    # Reset index and convert back to ms timestamp
    agg = agg.reset_index()
    agg["timestamp"] = agg["timestamp"].astype(np.int64) // 10**6
    
    return agg


def compute_5m_features(df_5m: pd.DataFrame, btc_5m: pd.DataFrame, eth_5m: pd.DataFrame) -> pd.DataFrame:
    """Compute all 58 v7 features on 5m candles."""
    df = df_5m.copy().reset_index(drop=True)
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

    # Hour features
    ts_dt = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["hour_sin"] = np.sin(2 * np.pi * ts_dt.dt.hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * ts_dt.dt.hour / 24)
    
    # V6 new features with BTC/ETH
    btc = btc_5m[["timestamp", "close", "volume", "high", "low"]].copy()
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
    btc = btc[["timestamp", "btc_ret_1m", "btc_ret_5m", "btc_ret_15m",
               "btc_vol_z", "btc_trend_50", "btc_volatility_regime"]]
    
    df = df.merge(btc, on="timestamp", how="left")
    
    alt_ret_15m_pct = df["close"].pct_change(15) * 100
    btc_ret_15m_pct = df["btc_ret_15m"] * 100
    df["btc_alt_spread_15m"] = alt_ret_15m_pct - btc_ret_15m_pct
    
    eth = eth_5m[["timestamp", "close"]].copy().rename(columns={"close": "eth_close"})
    df = df.merge(eth, on="timestamp", how="left")
    alt_ret = df["close"].pct_change()
    eth_ret = df["eth_close"].pct_change()
    df["eth_corr_30"] = alt_ret.rolling(30).corr(eth_ret)
    
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
    
    ts_dt = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    hour = ts_dt.dt.hour
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

def aggregate_5m_to_n(df_5m: pd.DataFrame, n: int) -> pd.DataFrame:
    """Aggregate 5m candles into n-minute candles."""
    df = df_5m.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    
    agg = df.resample(f"{n}min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    
    agg = agg.reset_index()
    agg["timestamp"] = agg["timestamp"].astype(np.int64) // 10**6
    return agg


def _merge_htf_to_5m(df_5m: pd.DataFrame, htf_df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """Merge higher-timeframe features into 5m dataframe using merge_asof.
    
    Uses timestamps (int64 ms) for matching — no datetime index needed.
    """
    # Ensure timestamps are int64 ms
    df_5m = df_5m.copy()
    htf_df = htf_df.copy()
    df_5m["timestamp"] = df_5m["timestamp"].astype(np.int64)
    htf_df["timestamp"] = htf_df["timestamp"].astype(np.int64)
    
    # Sort by timestamp for merge_asof
    df_5m_sorted = df_5m.sort_values("timestamp").reset_index(drop=True)
    htf_sorted = htf_df[["timestamp"] + feature_cols].sort_values("timestamp").reset_index(drop=True)
    
    # Forward-fill higher TF features: each 5m bar gets the last completed HTF bar
    merged = pd.merge_asof(
        df_5m_sorted,
        htf_sorted,
        on="timestamp",
        direction="backward",  # use last completed HTF bar
    )
    
    # Fill any missing with 0
    for col in feature_cols:
        if col in merged.columns:
            merged[col] = merged[col].fillna(0).astype(np.float32)
        else:
            merged[col] = 0.0
    
    return merged


def compute_higher_tf_features(df_5m: pd.DataFrame, btc_5m: pd.DataFrame) -> pd.DataFrame:
    """Compute 15m and 1h features, merge back to 5m index."""
    df = df_5m.copy()
    
    # --- 15m features ---
    df_15m = aggregate_5m_to_n(df_5m, 15)
    df_15m["ema_9_15m"] = df_15m["close"].ewm(span=9, adjust=False).mean()
    df_15m["ema_20_15m"] = df_15m["close"].ewm(span=20, adjust=False).mean()
    df_15m["trend_15m"] = np.sign(df_15m["ema_9_15m"] - df_15m["ema_20_15m"]).astype(int)
    
    # 15m RSI
    delta_15 = df_15m["close"].diff()
    gain_15 = delta_15.where(delta_15 > 0, 0).rolling(14).mean()
    loss_15 = (-delta_15.where(delta_15 < 0, 0)).rolling(14).mean()
    rs_15 = gain_15 / loss_15.replace(0, 1e-10)
    df_15m["rsi_15m"] = 100 - (100 / (1 + rs_15))
    
    # 15m vol regime
    atr_15 = (df_15m["high"] - df_15m["low"]).rolling(14).mean()
    atr_pct_15 = atr_15 / df_15m["close"] * 100
    df_15m["vol_regime_15m"] = np.digitize(atr_pct_15.fillna(0).values, [0.5, 1.5, 5.0]).astype(int)
    
    # BTC 15m trend
    btc_15m = aggregate_5m_to_n(btc_5m, 15)
    btc_15m["btc_ema_9_15m"] = btc_15m["close"].ewm(span=9, adjust=False).mean()
    btc_15m["btc_ema_20_15m"] = btc_15m["close"].ewm(span=20, adjust=False).mean()
    btc_15m["btc_trend_15m"] = np.sign(btc_15m["btc_ema_9_15m"] - btc_15m["btc_ema_20_15m"]).astype(int)
    
    # Merge BTC trend into 15m
    df_15m["btc_trend_15m"] = btc_15m["btc_trend_15m"].values[:len(df_15m)]
    
    # Merge 15m features into 5m
    df = _merge_htf_to_5m(df, df_15m, FEATURES_15M)
    
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
    
    # BTC 1h trend
    btc_1h = aggregate_5m_to_n(btc_5m, 60)
    btc_1h["btc_ema_9_1h"] = btc_1h["close"].ewm(span=9, adjust=False).mean()
    btc_1h["btc_ema_20_1h"] = btc_1h["close"].ewm(span=20, adjust=False).mean()
    btc_1h["btc_trend_1h"] = np.sign(btc_1h["btc_ema_9_1h"] - btc_1h["btc_ema_20_1h"]).astype(int)
    
    df_1h["btc_trend_1h"] = btc_1h["btc_trend_1h"].values[:len(df_1h)]
    
    # Merge 1h features into 5m
    df = _merge_htf_to_5m(df, df_1h, ["trend_1h", "rsi_1h", "vol_regime_1h", "btc_trend_1h"])
    
    # MTF alignment score: how many timeframes agree on direction
    df["mtf_alignment"] = (
        df["trend_50"].astype(float) +       # 5m trend
        df["trend_15m"].astype(float) +       # 15m trend
        df["trend_1h"].astype(float)          # 1h trend
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
    """Compute forward returns for multiple horizons.
    
    Horizons are in number of 5m bars:
    - H=12 → 1h forward
    - H=36 → 3h forward
    - H=72 → 6h forward
    - H=288 → 24h forward
    """
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
    """Merge 1m microstructure features into 5m dataframe.
    
    For each 5m bar, we take the LAST 1m candle's micro features
    (they represent the most recent microstructure state).
    """
    # Align by timestamp: for each 5m bar, find the last 1m bar within it
    df_5m_ts = pd.to_datetime(df_5m["timestamp"], unit="ms", utc=True)
    df_1m_ts = pd.to_datetime(df_1m_micro["timestamp"], unit="ms", utc=True)
    
    # Create a lookup: for each 5m timestamp, find the closest 1m timestamp <= it
    df_1m_micro = df_1m_micro.copy()
    df_1m_micro["dt"] = df_1m_ts
    
    # We need to merge: for each 5m bar, get the micro features from the 
    # 1m bar that falls at the end of that 5m period
    df_5m_copy = df_5m.copy()
    df_5m_copy["dt"] = df_5m_ts
    
    # Use merge_asof to find nearest 1m timestamp <= 5m timestamp
    df_1m_sorted = df_1m_micro[["dt"] + FEATURES_MICRO_1M].sort_values("dt")
    df_5m_sorted = df_5m_copy.sort_values("dt")
    
    merged = pd.merge_asof(
        df_5m_sorted,
        df_1m_sorted,
        on="dt",
        direction="backward",  # use the last 1m bar before/at 5m bar
    )
    
    # Drop the datetime helper
    merged = merged.drop(columns=["dt"])
    
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
    LOG.info("Loaded %s_1m: %d rows", symbol, len(df))
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
    
    # Create a dummy ETH 5m if not available (use BTC as proxy)
    if eth_5m is None:
        LOG.warning("No ETH data — using BTC as ETH proxy for correlation features")
        eth_5m = btc_5m.copy()
        eth_5m = eth_5m.rename(columns={
            "btc_close": "close" if "close" not in eth_5m.columns else "eth_close"
        })
        if "close" not in eth_5m.columns and "eth_close" in eth_5m.columns:
            eth_5m["close"] = eth_5m["eth_close"]
    
    # Ensure timestamp types match
    sym_5m["timestamp"] = sym_5m["timestamp"].astype(np.int64)
    btc_5m["timestamp"] = btc_5m["timestamp"].astype(np.int64)
    eth_5m["timestamp"] = eth_5m["timestamp"].astype(np.int64)
    
    # 3. Compute 5m features (v7 58 features)
    sym_5m_feat = compute_5m_features(sym_5m, btc_5m, eth_5m)
    
    # 4. Compute higher timeframe features (15m, 1h)
    sym_5m_feat = compute_higher_tf_features(sym_5m_feat, btc_5m)
    
    # 5. Merge 1m microstructure into 5m
    sym_5m_full = merge_micro_into_5m(sym_5m_feat, sym_1m_micro)
    
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
        try:
            df = build_symbol_dataset(sym, horizons, btc_1m)
            all_dfs.append(df)
        except Exception as e:
            LOG.error("Failed for %s: %s", sym, e)
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
    
    # Save
    output_path = Path(args.output)
    combined.to_parquet(output_path, index=False)
    
    print(f"\n{'='*80}")
    print(f"DATASET SAVED: {output_path}")
    print(f"  Total rows: {len(combined):,}")
    print(f"  Total features: {len(ALL_FEATURE_NAMES)}")
    print(f"  Symbols: {combined['symbol'].unique().tolist()}")
    for h in horizons:
        col = f"fwd_ret_h{h}"
        valid = combined[col].notna().sum()
        print(f"  Label {col}: {valid:,} valid rows, "
              f"mean={combined[col].mean():+.4f}%, std={combined[col].std():.4f}%")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
