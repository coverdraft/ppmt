"""
features.py — v6 59-feature extractor for paper trading.

Exact copy of v6_extract_features.compute_indicators_v5 + compute_indicators_v6_new,
trimmed for streaming use (operates on a single symbol's DataFrame plus a BTC and
ETH reference frame aligned by timestamp).

All 59 features are backward-looking (no forward returns used at inference time).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Feature spec — must match v6_extract_features exactly
# ---------------------------------------------------------------------------

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
    "hour_sin", "hour_cos",
    # NOTE: "dow" REMOVED — with HORIZON=288 (24h forward), only ~4-5 samples per
    # day-of-week in 30d window. Correlation is spurious (0.52 on BTC) and does not
    # generalize. hour_sin/hour_cos capture intraday seasonality without overfitting.
]
FEATURE_NAMES_V6_NEW = [
    "btc_ret_1m", "btc_ret_5m", "btc_ret_15m", "btc_vol_z",
    "btc_trend_50", "eth_corr_30", "btc_alt_spread_15m", "btc_volatility_regime",
    "vol_delta_3", "wick_imbalance_3", "body_consistency_5",
    "range_expansion_3", "close_persistence_5", "vol_acceleration",
    "atr_percentile_50", "trend_strength_50", "regime_vol_trend", "hour_quantile",
    "alt_lead_5m", "alt_lag_signal", "momentum_dispersion",
]
FEATURE_NAMES = FEATURE_NAMES_V5 + FEATURE_NAMES_V6_NEW
assert len(FEATURE_NAMES) == 58  # was 59, removed 'dow'


def compute_indicators_v5(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)
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

    if "timestamp" in df.columns:
        # ccxt returns ts in ms; v6 SQLite stored ts in s. Detect unit by magnitude.
        sample = df["timestamp"].iloc[0] if len(df) else 0
        unit = "ms" if sample > 1e12 else "s"
        ts = pd.to_datetime(df["timestamp"], unit=unit, utc=True)
        df["hour_utc"] = ts.dt.hour
        df["dow"] = ts.dt.dayofweek
        df["hour_sin"] = np.sin(2 * np.pi * df["hour_utc"] / 24)
        df["hour_cos"] = np.cos(2 * np.pi * df["hour_utc"] / 24)
    return df


def compute_indicators_v6_new(df: pd.DataFrame, btc_df: pd.DataFrame, eth_df: pd.DataFrame) -> pd.DataFrame:
    """Add 21 new features. btc_df and eth_df must be aligned-by-timestamp to df."""
    df = df.copy()

    btc = btc_df[["timestamp", "close", "volume", "high", "low"]].copy()
    btc = btc.rename(columns={
        "close": "btc_close", "volume": "btc_volume",
        "high": "btc_high", "low": "btc_low",
    })
    btc["btc_ret_1m"]  = btc["btc_close"].pct_change(1, fill_method=None)
    btc["btc_ret_5m"]  = btc["btc_close"].pct_change(5, fill_method=None)
    btc["btc_ret_15m"] = btc["btc_close"].pct_change(15, fill_method=None)
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

    alt_ret_15m_pct = df["close"].pct_change(15, fill_method=None) * 100
    btc_ret_15m_pct = df["btc_ret_15m"] * 100
    df["btc_alt_spread_15m"] = alt_ret_15m_pct - btc_ret_15m_pct

    eth = eth_df[["timestamp", "close"]].copy().rename(columns={"close": "eth_close"})
    df = df.merge(eth, on="timestamp", how="left")
    alt_ret = df["close"].pct_change(fill_method=None)
    eth_ret = df["eth_close"].pct_change(fill_method=None)
    df["eth_corr_30"] = alt_ret.rolling(30).corr(eth_ret)

    df["vol_delta_3"] = df["volume"].pct_change(3, fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0).clip(-5, 5)
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
    if "hour_utc" in df.columns:
        df["hour_quantile"] = pd.cut(df["hour_utc"], bins=[-1, 8, 14, 22, 24],
                                     labels=[0, 1, 2, 3]).astype(int)
    else:
        df["hour_quantile"] = 1

    df["alt_lead_5m"] = (df["close"].pct_change(5, fill_method=None) - df["btc_ret_5m"]) * 100
    df["alt_lag_signal"] = ((df["btc_ret_1m"].abs() > 0.002) &
                             (df["ret_1"].abs() < 0.001)).astype(int)
    df["momentum_dispersion"] = df["ret_1"].rolling(10).std()

    return df


def extract_features(ohlcv_df: pd.DataFrame, btc_df: pd.DataFrame, eth_df: pd.DataFrame) -> pd.DataFrame:
    """Compute all 59 features on a single symbol OHLCV.

    Inputs:
      ohlcv_df : columns [timestamp, open, high, low, close, volume]
      btc_df   : same columns, BTC/USDT 5m, must span the same timestamps
      eth_df   : same columns, ETH/USDT 5m, must span the same timestamps

    Returns:
      DataFrame with all 59 features. The last row corresponds to the most
      recent closed candle.
    """
    df = compute_indicators_v5(ohlcv_df)
    df = compute_indicators_v6_new(df, btc_df, eth_df)
    return df


def latest_feature_row(ohlcv_df: pd.DataFrame, btc_df: pd.DataFrame, eth_df: pd.DataFrame) -> dict | None:
    """Return the most recent feature row as a dict, or None if insufficient history.

    Needs at least 50 prior bars for atr_percentile_50 / ema_50 / etc.
    """
    if len(ohlcv_df) < 60 or len(btc_df) < 60 or len(eth_df) < 60:
        return None
    feat_df = extract_features(ohlcv_df, btc_df, eth_df)
    last = feat_df.iloc[-1]
    row = {f: float(last[f]) if pd.notna(last[f]) else 0.0 for f in FEATURE_NAMES}
    row["_timestamp"] = int(ohlcv_df["timestamp"].iloc[-1])
    row["_close"] = float(ohlcv_df["close"].iloc[-1])
    return row


def make_label(ohlcv_df: pd.DataFrame, horizon: int = 3) -> float | None:
    """Compute fwd_ret_3 label for the LAST row (used at training time only).

    Returns (close[i+H] - close[i]) / close[i] * 100, or None if insufficient
    forward bars.
    """
    c = ohlcv_df["close"].values
    i = len(c) - 1 - horizon
    if i < 0:
        return None
    return float((c[i + horizon] - c[i]) / c[i] * 100)
