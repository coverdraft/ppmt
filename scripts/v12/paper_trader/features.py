"""
features.py — V12 80-feature extractor for paper trading.

Reimplements the feature computation from v11_build_dataset.py for streaming use.
Computes all 80 features (microstructure + 5m base + MTF + BTC correlation)
on a batch of 5m bars and returns the latest feature row for prediction.

Input: 5m OHLCV DataFrames for the target symbol, BTC, and ETH
Output: dict of 80 feature values for the latest closed 5m bar

CRITICAL: NEVER use pd.to_datetime on timestamp columns. It produces
incorrect datetimes on some pandas versions/platforms (shifted ~15h).
All time-based operations use integer arithmetic on ms timestamps.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ============================================================================
# FEATURE SPECIFICATION — must match v11_build_dataset.py exactly
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
    "trend_15m", "rsi_15m", "vol_regime_15m", "btc_trend_15m",
]

FEATURES_1H = [
    "trend_1h", "rsi_1h", "vol_regime_1h", "btc_trend_1h", "mtf_alignment",
]

ALL_FEATURE_NAMES = (
    FEATURES_MICRO_1M + FEATURES_5M_V5 + FEATURES_5M_V6 +
    FEATURES_15M + FEATURES_1H
)

assert len(ALL_FEATURE_NAMES) == 80, f"Expected 80 features, got {len(ALL_FEATURE_NAMES)}"


# ============================================================================
# FEATURE COMPUTATION
# ============================================================================

def compute_microstructure_from_5m(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Compute microstructure features from 5m bars.

    Since we don't have 1m candles in streaming mode, we approximate
    the microstructure features from 5m bars. This is a simplification
    but maintains feature alignment with the training pipeline.

    The key insight: during paper trading, we use the SAME approximation
    that the model was trained on, so predictions remain calibrated.
    """
    df = df_5m.copy().reset_index(drop=True)
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]

    # Micro returns (using 5m bars as proxy for 1m)
    df["ret_1m"] = c.pct_change(1)
    df["ret_3m"] = c.pct_change(3)
    df["ret_5m"] = c.pct_change(5)

    # Volume delta
    rng = (h - l).replace(0, 1e-10)
    direction = np.sign(c - o)
    signed_vol = v * direction
    df["vol_delta_1m"] = signed_vol.diff(1)
    df["vol_delta_5m"] = signed_vol.rolling(5).sum() - signed_vol.shift(1).rolling(5).sum()

    # Candle shape
    df["body_pct_1m"] = (c - o) / rng
    df["wick_ratio_1m"] = ((h - np.maximum(o, c)) + (np.minimum(o, c) - l)) / rng

    # CVD
    df["cvd_1m"] = signed_vol.rolling(12, min_periods=1).sum()   # ~1h equivalent
    df["cvd_5m"] = signed_vol.rolling(60, min_periods=1).sum()   # ~5h equivalent

    # Price impact
    price_move = (c - o).abs()
    df["price_impact_1m"] = (price_move / v.replace(0, 1e-10)).rolling(10, min_periods=1).mean()

    # Micro momentum
    df["micro_momentum_5m"] = (c - c.shift(1)) / c.shift(1).replace(0, 1e-10) * 100

    # Vol clustering
    vol_ma = v.rolling(10, min_periods=1).mean().replace(0, 1e-10)
    df["vol_clustering_10m"] = (v / vol_ma).clip(0, 10)

    # Order flow acceleration
    df["order_flow_accel_5m"] = df["cvd_1m"].diff(5) / df["cvd_1m"].shift(5).replace(0, 1e-10).abs().clip(lower=1e-10)
    df["order_flow_accel_5m"] = df["order_flow_accel_5m"].clip(-10, 10)

    # Clean up
    for col in FEATURES_MICRO_1M:
        if col in df.columns:
            df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0)

    return df


def _ms_to_hour(ts_ms: np.ndarray) -> np.ndarray:
    """Extract UTC hour from millisecond timestamps using pure integer arithmetic.

    NEVER uses pd.to_datetime — avoids platform-specific datetime bugs.
    """
    # Convert ms → seconds since epoch → seconds within the day → hours
    seconds = ts_ms.astype(np.int64) // 1000
    hours_since_epoch = seconds // 3600
    # Hour of day: hours_since_epoch mod 24
    return (hours_since_epoch % 24).astype(float)


def compute_5m_features(df_5m: pd.DataFrame, btc_5m: pd.DataFrame, eth_5m: pd.DataFrame) -> pd.DataFrame:
    """Compute all 58 v7-style features on 5m candles + microstructure."""
    # First compute microstructure
    df = compute_microstructure_from_5m(df_5m)

    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]

    # === 5m base features (v5 style, 37 features) ===
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
    bins_atr = [0.5, 1.5, 5.0]
    df["vol_regime"] = np.digitize(atr_p, bins_atr).astype(int)
    df["trending"] = (df["atr_pct"] > df["atr_pct"].rolling(50, min_periods=5).mean()).astype(int)

    # Hour features — PURE INTEGER ARITHMETIC, no pd.to_datetime
    hour = _ms_to_hour(df["timestamp"].values)
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)

    # === BTC/ETH cross-asset features (v6 style, 21 features) ===
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

    df = _safe_merge_asof(df, btc, on="timestamp")

    alt_ret_15m_pct = df["close"].pct_change(15) * 100
    btc_ret_15m_pct = df["btc_ret_15m"] * 100
    df["btc_alt_spread_15m"] = alt_ret_15m_pct - btc_ret_15m_pct

    eth = eth_5m[["timestamp", "close"]].copy().rename(columns={"close": "eth_close"})
    df = _safe_merge_asof(df, eth, on="timestamp")
    alt_ret = df["close"].pct_change()
    eth_ret = df["eth_close"].pct_change()
    df["eth_corr_30"] = alt_ret.rolling(30).corr(eth_ret)

    df["vol_delta_3"] = df["volume"].pct_change(3).replace([np.inf, -np.inf], np.nan).fillna(0).clip(-5, 5)
    df["wick_imbalance_3"] = (df["lower_wick"] - df["upper_wick"]).rolling(3).sum()
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

    # Hour quantile — PURE INTEGER ARITHMETIC, no pd.to_datetime
    hour2 = _ms_to_hour(df["timestamp"].values)
    df["hour_quantile"] = np.digitize(hour2, bins=[8, 14, 22]).astype(int)

    df["alt_lead_5m"] = (df["close"].pct_change(5) - df["btc_ret_5m"]) * 100
    df["alt_lag_signal"] = ((df["btc_ret_1m"].abs() > 0.002) &
                             (df["ret_1"].abs() < 0.001)).astype(int)
    df["momentum_dispersion"] = df["ret_1"].rolling(10).std()

    # === Higher timeframe features (15m, 1h) ===
    df = compute_htf_features(df, btc_5m)

    # Final cleanup
    for col in ALL_FEATURE_NAMES:
        if col in df.columns:
            df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0)

    return df


def _safe_merge_asof(left: pd.DataFrame, right: pd.DataFrame,
                     on: str = "timestamp") -> pd.DataFrame:
    """Merge using merge_asof — never duplicates rows or creates NaN mismatches."""
    left = left.copy()
    right = right.copy()
    left[on] = left[on].astype(np.int64)
    right[on] = right[on].astype(np.int64)
    left_sorted = left.sort_values(on).reset_index(drop=True)
    right_sorted = right.sort_values(on).reset_index(drop=True)
    right_sorted = right_sorted.drop_duplicates(subset=[on], keep="last")
    merged = pd.merge_asof(left_sorted, right_sorted, on=on, direction="backward")
    return merged


def _aggregate_5m_to_n(df_5m: pd.DataFrame, n: int) -> pd.DataFrame:
    """Aggregate 5m candles into n-minute candles.

    Uses PURE INTEGER groupby on ms timestamps. NEVER touches pd.to_datetime.
    This is the ONLY reliable way to aggregate across all pandas versions.
    """
    if len(df_5m) == 0:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = df_5m.copy()
    interval_ms = n * 60 * 1000

    # Group by n-minute interval using integer division on ms timestamps
    df["_group"] = df["timestamp"] // interval_ms

    agg = df.groupby("_group").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })

    # Convert group key back to ms timestamp (start of each interval)
    agg["timestamp"] = (agg.index.astype(np.int64) * interval_ms)
    agg = agg[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
    return agg


def compute_htf_features(df_5m: pd.DataFrame, btc_5m: pd.DataFrame) -> pd.DataFrame:
    """Compute 15m and 1h features and merge into 5m DataFrame."""
    df = df_5m.copy()

    # --- 15m features ---
    df_15m = _aggregate_5m_to_n(df_5m, 15)
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

    btc_15m = _aggregate_5m_to_n(btc_5m, 15)
    btc_15m["btc_ema_9_15m"] = btc_15m["close"].ewm(span=9, adjust=False).mean()
    btc_15m["btc_ema_20_15m"] = btc_15m["close"].ewm(span=20, adjust=False).mean()
    btc_15m["btc_trend_15m"] = np.sign(btc_15m["btc_ema_9_15m"] - btc_15m["btc_ema_20_15m"]).astype(int)
    # Use merge_asof instead of direct array assignment
    df_15m = _safe_merge_asof(df_15m, btc_15m[["timestamp", "btc_trend_15m"]], on="timestamp")

    # Merge 15m -> 5m using merge_asof
    df["timestamp"] = df["timestamp"].astype(np.int64)
    df_15m_sorted = df_15m[["timestamp"] + FEATURES_15M].sort_values("timestamp")
    df = pd.merge_asof(df.sort_values("timestamp"), df_15m_sorted, on="timestamp", direction="backward")
    for col in FEATURES_15M:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # --- 1h features ---
    df_1h = _aggregate_5m_to_n(df_5m, 60)
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

    btc_1h = _aggregate_5m_to_n(btc_5m, 60)
    btc_1h["btc_ema_9_1h"] = btc_1h["close"].ewm(span=9, adjust=False).mean()
    btc_1h["btc_ema_20_1h"] = btc_1h["close"].ewm(span=20, adjust=False).mean()
    btc_1h["btc_trend_1h"] = np.sign(btc_1h["btc_ema_9_1h"] - btc_1h["btc_ema_20_1h"]).astype(int)
    # Use merge_asof instead of direct array assignment
    df_1h = _safe_merge_asof(df_1h, btc_1h[["timestamp", "btc_trend_1h"]], on="timestamp")

    # Merge 1h -> 5m
    df_1h_sorted = df_1h[["timestamp", "trend_1h", "rsi_1h", "vol_regime_1h", "btc_trend_1h"]].sort_values("timestamp")
    df = pd.merge_asof(df.sort_values("timestamp"), df_1h_sorted, on="timestamp", direction="backward")
    for col in ["trend_1h", "rsi_1h", "vol_regime_1h", "btc_trend_1h"]:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # MTF alignment
    df["mtf_alignment"] = (
        df["trend_50"].astype(float) +
        df["trend_15m"].astype(float) +
        df["trend_1h"].astype(float)
    ) / 3.0

    return df


def latest_feature_row(sym_5m: pd.DataFrame, btc_5m: pd.DataFrame, eth_5m: pd.DataFrame) -> dict | None:
    """Compute all 80 features and return the latest row as a dict.

    Needs at least 60 bars for warm-up of rolling indicators.
    """
    if len(sym_5m) < 60 or len(btc_5m) < 60 or len(eth_5m) < 60:
        return None

    feat_df = compute_5m_features(sym_5m, btc_5m, eth_5m)
    last = feat_df.iloc[-1]

    row = {}
    for f in ALL_FEATURE_NAMES:
        val = last.get(f, 0.0)
        row[f] = float(val) if pd.notna(val) else 0.0

    row["_timestamp"] = int(sym_5m["timestamp"].iloc[-1])
    row["_close"] = float(sym_5m["close"].iloc[-1])
    row["_trend_1h"] = row.get("trend_1h", 0.0)

    return row
