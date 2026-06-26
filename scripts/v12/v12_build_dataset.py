"""
v12_build_dataset.py — Enhanced dataset with cost-aware labels + new features.

IMPROVEMENTS vs V11:
  1. Cost-aware labels: label=1 only if fwd_ret > 2x maker fee (0.08%)
     This teaches the model to only predict "win" when the move is tradeable
  2. CVD divergence features: price up but CVD down = bearish divergence
  3. Volume profile features: relative volume at different timeframes
  4. Momentum acceleration: rate of change of momentum
  5. Microstructure quality: how "clean" is the price action

USAGE:
    python scripts/v12/v12_build_dataset.py
    python scripts/v12/v12_build_dataset.py --symbols SOL,DOGE
"""
from __future__ import annotations

import argparse
import json
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
OUTPUT_DIR = DATA_DIR / "v12"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v12_build")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_SYMBOLS = ["SOL", "DOGE", "AVAX"]
DEFAULT_HORIZONS = [12, 36, 72]  # 1h, 3h, 6h in 5m bars

# Cost threshold: only label as "win" if return > 2x maker fee roundtrip
COST_THRESHOLD = 0.0008  # 0.08% (2x 0.04% maker fee)


def load_1m_data(symbol: str) -> pd.DataFrame:
    """Load 1m OHLCV data from V10 cache."""
    path = CACHE_DIR / f"{symbol}_1m.parquet"
    if not path.exists():
        LOG.warning("No 1m data for %s", symbol)
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["timestamp"] = df["timestamp"].astype(np.int64)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def aggregate_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1m to 5m bars."""
    df = df_1m.copy()
    df["bar_5m"] = df["timestamp"] // (5 * 60 * 1000) * (5 * 60 * 1000)
    
    agg = df.groupby("bar_5m").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "timestamp": "first",
    }).reset_index(drop=True)
    
    return agg.sort_values("timestamp").reset_index(drop=True)


def compute_base_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute base features from 5m bars (same as V11)."""
    # Price features
    df["body_pct"] = (df["close"] - df["open"]) / (df["high"] - df["low"] + 1e-10)
    df["close_pos"] = (df["close"] - df["low"]) / (df["high"] - df["low"] + 1e-10)
    df["range_pct"] = (df["high"] - df["low"]) / (df["close"] + 1e-10)
    df["upper_wick"] = (df["high"] - df[["open", "close"]].max(axis=1)) / (df["high"] - df["low"] + 1e-10)
    df["lower_wick"] = (df[["open", "close"]].min(axis=1) - df["low"]) / (df["high"] - df["low"] + 1e-10)
    df["wick_imbalance_3"] = df["upper_wick"].rolling(3).sum() - df["lower_wick"].rolling(3).sum()
    
    # Returns
    for lag in [1, 3, 5, 10]:
        df[f"ret_{lag}"] = df["close"].pct_change(lag)
    df["log_ret_1"] = np.log(df["close"] / df["close"].shift(1))
    
    # Volume features
    df["vol_ratio"] = df["volume"] / (df["volume"].rolling(20).mean() + 1e-10)
    df["vol_z"] = (df["volume"] - df["volume"].rolling(50).mean()) / (df["volume"].rolling(50).std() + 1e-10)
    df["vol_std_10"] = df["volume"].rolling(10).std()
    
    # ATR
    df["atr_14"] = df["range_pct"].rolling(14).mean()
    df["atr_pct"] = df["atr_14"]
    df["atr_percentile_50"] = df["atr_pct"].rolling(50).rank(pct=True)
    
    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    df["rsi_14"] = 100 - (100 / (1 + rs))
    
    # EMAs
    for span in [9, 20, 50]:
        df[f"ema_{span}"] = df["close"].ewm(span=span).mean()
    
    df["ema_9_slope"] = df["ema_9"].pct_change(3)
    df["ema_20_slope"] = df["ema_20"].pct_change(3)
    df["ema_50_slope"] = df["ema_50"].pct_change(5)
    df["ema_9_20_cross"] = (df["ema_9"] > df["ema_20"]).astype(float)
    df["ema_20_50_cross"] = (df["ema_20"] > df["ema_50"]).astype(float)
    
    # Price vs EMAs
    df["price_vs_ema20"] = (df["close"] - df["ema_20"]) / (df["atr_pct"] + 1e-10)
    df["price_vs_ema50"] = (df["close"] - df["ema_50"]) / (df["atr_pct"] + 1e-10)
    
    # High/Low distance
    df["high_20"] = df["high"].rolling(20).max()
    df["low_20"] = df["low"].rolling(20).min()
    df["dist_to_high_20"] = (df["close"] - df["high_20"]) / (df["atr_pct"] + 1e-10)
    df["dist_to_low_20"] = (df["close"] - df["low_20"]) / (df["atr_pct"] + 1e-10)
    
    # Momentum
    df["momentum_dispersion"] = df["ret_1"].rolling(10).std()
    df["close_persistence_5"] = (df["ret_1"].rolling(5).apply(lambda x: (x > 0).sum()) / 5)
    
    # Trend
    df["trend_50"] = np.sign(df["close"] - df["ema_50"])
    df["trend_strength_50"] = (df["close"] - df["ema_50"]) / (df["atr_pct"] * 10 + 1e-10)
    df["trending"] = (df["trend_strength_50"].abs() > 0.5).astype(float)
    
    # Volume acceleration
    df["vol_acceleration"] = df["vol_ratio"].diff(3)
    
    # Vol regime
    df["vol_regime"] = pd.cut(df["vol_z"], bins=[-99, -1, 1, 3, 99], labels=[0, 1, 2, 3]).astype(float)
    
    # Time features
    ts_dt = pd.to_datetime(df["timestamp"], unit="ms")
    df["hour_sin"] = np.sin(2 * np.pi * ts_dt.dt.hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * ts_dt.dt.hour / 24)
    df["hour_quantile"] = ts_dt.dt.hour / 24
    
    return df


def compute_mtf_features(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Compute multi-timeframe features (15m, 1h) from 5m data."""
    # 15m features (every 3 bars)
    for col in ["close", "volume", "high", "low"]:
        df_5m[f"{col}_15m"] = df_5m[col].rolling(3).mean() if col in ["close", "volume"] else \
                               df_5m[col].rolling(3).max() if col == "high" else \
                               df_5m[col].rolling(3).min()
    
    df_5m["ret_15m"] = df_5m["close_15m"].pct_change(3)
    df_5m["rsi_15m"] = compute_rsi(df_5m["close_15m"], 14)
    df_5m["trend_15m"] = np.sign(df_5m["close"] - df_5m["close"].rolling(12).mean())
    df_5m["vol_regime_15m"] = pd.cut(
        df_5m["vol_z"].rolling(3).mean(),
        bins=[-99, -1, 1, 3, 99],
        labels=[0, 1, 2, 3]
    ).astype(float)
    
    # 1h features (every 12 bars)
    df_5m["close_1h"] = df_5m["close"].rolling(12).mean()
    df_5m["rsi_1h"] = compute_rsi(df_5m["close"].rolling(12).mean(), 14)
    df_5m["trend_1h"] = np.sign(df_5m["close"] - df_5m["close"].rolling(12).mean())
    df_5m["vol_regime_1h"] = pd.cut(
        df_5m["vol_z"].rolling(12).mean(),
        bins=[-99, -1, 1, 3, 99],
        labels=[0, 1, 2, 3]
    ).astype(float)
    
    # MTF alignment
    df_5m["mtf_alignment"] = (
        np.sign(df_5m["trend_15m"]).astype(float) + 
        np.sign(df_5m["trend_1h"]).astype(float) + 
        np.sign(df_5m["trend_50"]).astype(float)
    ) / 3.0
    
    return df_5m


def compute_rsi(series, period=14):
    """Compute RSI."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))


def compute_microstructure_features(df_5m: pd.DataFrame, df_1m: pd.DataFrame) -> pd.DataFrame:
    """Compute microstructure features from 1m data aggregated to 5m."""
    # CVD (Cumulative Volume Delta) - proxy using intra-bar price movement
    df_5m["cvd_1m"] = ((df_5m["close"] - df_5m["open"]) * df_5m["volume"]).rolling(5).sum()
    df_5m["cvd_5m"] = ((df_5m["close"] - df_5m["open"]) * df_5m["volume"]).rolling(12).sum()
    
    # Volume delta
    df_5m["vol_delta_1m"] = df_5m["volume"].diff(1)
    df_5m["vol_delta_5m"] = df_5m["volume"].rolling(5).sum() - df_5m["volume"].rolling(5).sum().shift(5)
    
    # Price impact
    df_5m["price_impact_1m"] = abs(df_5m["close"].pct_change(1)) / (df_5m["volume"].rolling(5).mean() + 1e-10) * 1e6
    
    # Order flow acceleration
    df_5m["order_flow_accel_5m"] = df_5m["cvd_5m"].diff(5)
    
    # Micro momentum
    df_5m["micro_momentum_5m"] = df_5m["close"].pct_change(1).rolling(5).mean()
    
    # Vol clustering
    df_5m["vol_clustering_10m"] = (df_5m["volume"].rolling(10).std() / (df_5m["volume"].rolling(50).std() + 1e-10))
    
    return df_5m


def compute_btc_features(df_5m: pd.DataFrame, btc_1m: pd.DataFrame) -> pd.DataFrame:
    """Compute BTC correlation features."""
    btc_5m = aggregate_to_5m(btc_1m)
    
    # Merge BTC close
    btc_close = btc_5m[["timestamp", "close"]].rename(columns={"close": "btc_close"})
    df_5m = df_5m.merge(btc_close, on="timestamp", how="left")
    df_5m["btc_close"] = df_5m["btc_close"].ffill()
    
    # BTC returns
    df_5m["btc_ret_1m"] = df_5m["btc_close"].pct_change(1)
    df_5m["btc_ret_5m"] = df_5m["btc_close"].pct_change(5)
    df_5m["btc_ret_15m"] = df_5m["btc_close"].pct_change(15)
    
    # BTC trend
    df_5m["btc_trend_50"] = np.sign(df_5m["btc_close"] - df_5m["btc_close"].rolling(50).mean())
    df_5m["btc_trend_1h"] = np.sign(df_5m["btc_close"] - df_5m["btc_close"].rolling(12).mean())
    
    # BTC volatility
    df_5m["btc_vol_z"] = (df_5m["btc_ret_5m"].rolling(50).std() - df_5m["btc_ret_5m"].rolling(200).std()) / \
                          (df_5m["btc_ret_5m"].rolling(200).std() + 1e-10)
    df_5m["btc_volatility_regime"] = pd.cut(
        df_5m["btc_vol_z"], bins=[-99, -1, 1, 3, 99], labels=[0, 1, 2, 3]
    ).astype(float)
    
    # ETH correlation (from 5m data if available)
    if "eth_close" not in df_5m.columns:
        df_5m["eth_corr_30"] = df_5m["ret_1"].rolling(30).corr(df_5m["btc_ret_1m"].rolling(30).mean())
    
    # BTC-ALT spread
    df_5m["btc_alt_spread_15m"] = (df_5m["btc_ret_15m"] - df_5m["ret_15m"]).rolling(3).std()
    
    return df_5m


def compute_new_v12_features(df_5m: pd.DataFrame) -> pd.DataFrame:
    """New features for V12 — designed to improve WR."""
    
    # 1. CVD Divergence: price going up but CVD going down = bearish divergence
    df_5m["cvd_price_divergence"] = (
        np.sign(df_5m["close"].pct_change(5)) != np.sign(df_5m["cvd_5m"].diff(5))
    ).astype(float)
    
    # 2. Volume surge: sudden volume spike
    df_5m["vol_surge"] = (df_5m["vol_ratio"] > 2.0).astype(float)
    
    # 3. Momentum acceleration: is momentum increasing or decreasing?
    df_5m["momentum_accel"] = df_5m["ret_1"].diff(3)
    
    # 4. Range compression: is the range getting tighter? (potential breakout)
    df_5m["range_compression"] = df_5m["range_pct"].rolling(10).mean() / (df_5m["range_pct"].rolling(50).mean() + 1e-10)
    
    # 5. Close position consistency: is close consistently near high/low?
    df_5m["close_consistency"] = df_5m["close_pos"].rolling(5).mean()
    
    # 6. Intraday regime: is the market trending or ranging in the last few hours?
    df_5m["hourly_trend_strength"] = abs(df_5m["close"].pct_change(12)) / (df_5m["range_pct"].rolling(12).sum() + 1e-10)
    
    # 7. Volatility squeeze: ATR contracting
    df_5m["vol_squeeze"] = (df_5m["atr_percentile_50"] < 0.3).astype(float)
    
    # 8. EMA bounce score: how close is price to EMA20?
    df_5m["ema20_bounce_score"] = 1.0 / (1.0 + abs(df_5m["price_vs_ema20"]))
    
    # 9. Breakout strength
    df_5m["breakout_up"] = (df_5m["close"] > df_5m["high_20"]).astype(float)
    df_5m["breakout_down"] = (df_5m["close"] < df_5m["low_20"]).astype(float)
    
    # 10. Last 3 bars pattern
    df_5m["last_3_body_sum"] = df_5m["body_pct"].rolling(3).sum()
    df_5m["last_3_range_sum"] = df_5m["range_pct"].rolling(3).sum()
    
    # 11. BTC impulse: large BTC move
    if "btc_ret_5m" in df_5m.columns:
        df_5m["btc_impulse"] = (abs(df_5m["btc_ret_5m"]) > df_5m["btc_ret_5m"].rolling(50).std() * 2).astype(float)
        df_5m["alt_lag_signal"] = np.sign(df_5m["btc_ret_5m"]) * np.sign(-df_5m["ret_1"])
        df_5m["alt_lead_5m"] = np.sign(df_5m["ret_1"]) * np.sign(df_5m["btc_ret_5m"].shift(-1))
    else:
        df_5m["btc_impulse"] = 0.0
        df_5m["alt_lag_signal"] = 0.0
        df_5m["alt_lead_5m"] = 0.0
    
    return df_5m


def compute_labels(df_5m: pd.DataFrame, horizons: list) -> pd.DataFrame:
    """Compute forward return labels with cost-awareness."""
    for h in horizons:
        fwd_ret = df_5m["close"].shift(-h) / df_5m["close"] - 1
        
        # Standard label
        df_5m[f"fwd_ret_h{h}"] = fwd_ret
        df_5m[f"label_h{h}"] = (fwd_ret > 0).astype(float)
        
        # Cost-aware label: only label as "win" if return > 2x maker fee
        # This teaches the model to distinguish between "barely positive" and "tradeable"
        df_5m[f"label_costaware_h{h}"] = (fwd_ret > COST_THRESHOLD).astype(float)
        
        # Three-way label: lose / neutral / win
        df_5m[f"label_3way_h{h}"] = pd.cut(
            fwd_ret, 
            bins=[-99, -COST_THRESHOLD, COST_THRESHOLD, 99],
            labels=[-1, 0, 1]
        ).astype(float)
    
    return df_5m


# Define feature columns
V12_FEATURE_NAMES = [
    # Base price features
    "body_pct", "close_pos", "range_pct", "wick_imbalance_3",
    "ret_1", "ret_3", "ret_5", "ret_10", "log_ret_1",
    "vol_ratio", "vol_z", "vol_std_10", "vol_acceleration",
    "atr_pct", "atr_percentile_50",
    "rsi_14",
    "ema_9_slope", "ema_20_slope", "ema_50_slope",
    "ema_9_20_cross", "ema_20_50_cross",
    "price_vs_ema20", "price_vs_ema50",
    "dist_to_high_20", "dist_to_low_20",
    "momentum_dispersion", "close_persistence_5",
    "trend_50", "trend_strength_50", "trending",
    "vol_regime",
    "hour_sin", "hour_cos", "hour_quantile",
    # MTF features
    "rsi_15m", "trend_15m", "vol_regime_15m", "ret_15m",
    "rsi_1h", "trend_1h", "vol_regime_1h",
    "mtf_alignment",
    # Microstructure
    "cvd_1m", "cvd_5m", "vol_delta_1m", "vol_delta_5m",
    "price_impact_1m", "order_flow_accel_5m", "micro_momentum_5m",
    "vol_clustering_10m",
    # BTC features
    "btc_ret_1m", "btc_ret_5m", "btc_ret_15m",
    "btc_trend_50", "btc_trend_1h", "btc_vol_z", "btc_volatility_regime",
    "btc_impulse", "alt_lag_signal", "alt_lead_5m",
    "btc_alt_spread_15m", "eth_corr_30",
    # V12 NEW features
    "cvd_price_divergence", "vol_surge", "momentum_accel",
    "range_compression", "close_consistency",
    "hourly_trend_strength", "vol_squeeze",
    "ema20_bounce_score", "breakout_up", "breakout_down",
    "last_3_body_sum", "last_3_range_sum",
]

ALL_FEATURE_NAMES = [f for f in V12_FEATURE_NAMES]  # Alias for compatibility


def build_symbol(symbol: str, horizons: list) -> pd.DataFrame:
    """Build dataset for one symbol."""
    LOG.info("Building %s", symbol)
    
    # Load 1m data
    df_1m = load_1m_data(symbol)
    if len(df_1m) < 10000:
        LOG.warning("Insufficient 1m data for %s: %d rows", symbol, len(df_1m))
        return pd.DataFrame()
    
    LOG.info("  %s: %d 1m bars", symbol, len(df_1m))
    
    # Aggregate to 5m
    df_5m = aggregate_to_5m(df_1m)
    LOG.info("  %s: %d 5m bars", symbol, len(df_5m))
    
    # Compute features
    df_5m = compute_base_features(df_5m)
    df_5m = compute_mtf_features(df_5m)
    df_5m = compute_microstructure_features(df_5m, df_1m)
    
    # BTC features (must be before v12 features that reference BTC columns)
    btc_1m = load_1m_data("BTC")
    if len(btc_1m) > 0:
        df_5m = compute_btc_features(df_5m, btc_1m)
    
    # V12 new features (may reference BTC columns)
    df_5m = compute_new_v12_features(df_5m)
    
    # Labels
    df_5m = compute_labels(df_5m, horizons)
    
    # Add symbol column
    df_5m["symbol"] = symbol
    
    return df_5m


def main():
    parser = argparse.ArgumentParser(description="V12 Build Dataset")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols")
    parser.add_argument("--horizons", default=None, help="Comma-separated horizons")
    args = parser.parse_args()
    
    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS
    horizons = [int(h) for h in args.horizons.split(",")] if args.horizons else DEFAULT_HORIZONS
    
    print("=" * 80)
    print("V12 BUILD DATASET — Cost-Aware Labels + Enhanced Features")
    print(f"  Symbols: {symbols}")
    print(f"  Horizons: {horizons}")
    print(f"  Cost threshold: {COST_THRESHOLD*100:.2f}%")
    print(f"  Features: {len(V12_FEATURE_NAMES)}")
    print("=" * 80)
    
    all_dfs = []
    for symbol in symbols:
        df = build_symbol(symbol, horizons)
        if len(df) > 0:
            all_dfs.append(df)
    
    if not all_dfs:
        LOG.error("No data built!")
        sys.exit(1)
    
    combined = pd.concat(all_dfs, ignore_index=True)
    
    # Clean up NaN in features
    for col in V12_FEATURE_NAMES:
        if col in combined.columns:
            combined[col] = combined[col].replace([np.inf, -np.inf], np.nan)
    
    # Save
    output_path = OUTPUT_DIR / "v12_dataset.parquet"
    combined.to_parquet(output_path, index=False)
    
    # Save feature names
    valid_features = [f for f in V12_FEATURE_NAMES if f in combined.columns]
    with open(OUTPUT_DIR / "feature_columns.json", "w") as f:
        json.dump(valid_features, f, indent=2)
    
    # Summary
    print(f"\n{'='*80}")
    print("DATASET BUILT")
    print(f"  Rows: {len(combined)}")
    print(f"  Features: {len(valid_features)}")
    for col in combined.columns:
        if col.startswith("label_") and not col.startswith("label_3way"):
            valid = combined[col].notna().sum()
            up = combined[col].sum()
            print(f"  {col}: {valid} valid, {up:.0f} up ({up/valid*100:.1f}%)" if valid > 0 else f"  {col}: 0 valid")
    print(f"  Saved to: {output_path}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
