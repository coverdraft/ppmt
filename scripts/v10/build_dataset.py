"""
build_dataset.py — V10: Exit-Aware Dataset + Enhanced Multi-Timeframe

Pipeline:
  1. Load filtered trades from parse_trades.py (reuse v9)
  2. Download 1m OHLCV for each symbol + BTC (for correlation)
  3. Compute MFE/MAE for each trade entry (max favorable/adverse excursion)
  4. Compute features at each trade entry bar, including:
     - All V9 features (48 base features)
     - BTC correlation features (5 new)
     - 1h MTF features (4 new)
     - Enhanced MTF agreement (1m/5m/15m/1h)
  5. Label strategy (v3 — EXIT-AWARE):
     - Binary: 1.0=winner, -1.0=loser, 0.0=random (same as v2)
     - Regression: mfe_pct, mae_pct, mfe_mae_ratio, time_to_mfe (NEW)
  6. Save dataset for training

The v3 approach adds EXIT-AWARE labels on top of v2:
  - v2 taught the model "what does a WINNING entry look like"
  - v3 teaches "how MUCH potential does this entry have" (MFE/MAE)
  - A trade that hits +3% then reverses to -0.5% has a DIFFERENT entry
    quality than one that goes straight to -0.5%
  - MFE/MAE captures what binary labels miss
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

pd.options.mode.copy_on_write = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LOG = logging.getLogger("v10_build")

DATA_DIR = PROJECT_ROOT / "data" / "v10"
CACHE_DIR = DATA_DIR / "ohlcv_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── EMA / ATR helpers ──

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    result = np.empty_like(arr, dtype=np.float64)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def _atr(h, l, c, period=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.append(c[0], c[:-1])),
                                       np.abs(l - np.append(c[0], c[:-1]))))
    atr = pd.Series(tr).rolling(period, min_periods=5).mean().values
    return atr


# ── Feature names ──

FEATURE_NAMES = [
    # G1: Price microstructure
    "body_pct", "close_pos", "range_pct", "wick_imbalance",
    "body_consistency_5", "range_expansion_3",

    # G2: Returns + momentum
    "ret_1", "ret_3", "ret_6", "ret_12", "ret_30",
    "consecutive_dir", "momentum_strength", "ret_z_12",

    # G3: Volatility
    "atr_pct", "atr_percentile_50", "squeeze_score", "vol_regime",

    # G4: Volume
    "vol_ratio", "vol_z", "vol_skew", "vol_acceleration", "volume_conviction",

    # G5: Breakout context
    "close_position_20", "breakout_strength", "is_at_high_20", "is_at_low_20",
    "breakout_volume_score", "breakout_age",

    # G6: Trend alignment
    "dist_ema9_atr", "dist_ema21_atr", "ema_alignment",
    "ema_trend_strength", "ema21_bounce_score",

    # G7: Candle patterns
    "is_doji", "is_hammer", "is_shooting_star",
    "is_bullish_engulf", "is_bearish_engulf",
    "is_bull_pin", "is_bear_pin",

    # G8: Reversal
    "v_reversal", "pullback_depth",

    # G9: Temporal
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",

    # G10: Direction (label-related)
    "trade_direction",  # +1=LONG, -1=SHORT

    # G11: Multi-timeframe context (5m / 15m / 1h)
    "mtf5_ema_align", "mtf5_trend_str", "mtf5_ret_3", "mtf5_vol_ratio", "mtf5_atr_pct",
    "mtf15_ema_align", "mtf15_trend_str", "mtf15_ret_3", "mtf15_atr_pct",
    "mtf1h_ema_align", "mtf1h_trend_str", "mtf1h_ret_3", "mtf1h_atr_pct",
    "mtf_trend_agree",  # 1m/5m/15m/1h all aligned

    # G12: BTC correlation (restored from V8)
    "btc_ret_1m", "btc_ret_5m", "btc_impulse_score",
    "alt_btc_lag_1", "alt_btc_corr_30",

    # LABELS (binary + regression)
    "label",  # 1.0=winner entry, -1.0=loser entry, 0.0=random bar
    "mfe_pct",  # Max favorable excursion (% from entry price)
    "mae_pct",  # Max adverse excursion (% from entry price)
    "mfe_mae_ratio",  # MFE / max(MAE, 0.01) — entry efficiency
    "time_to_mfe",  # Minutes to peak favorable price
]

N_FEATURES = len([f for f in FEATURE_NAMES if f not in
                  ("label", "mfe_pct", "mae_pct", "mfe_mae_ratio", "time_to_mfe")])


# ── MFE/MAE computation ──

def compute_mfe_mae(
    entry_price: float,
    entry_bar: int,
    exit_bar: int,
    direction: str,
    highs: np.ndarray,
    lows: np.ndarray,
) -> dict:
    """Compute Maximum Favorable/Adverse Excursion for a trade.

    Returns dict with mfe_pct, mae_pct, mfe_mae_ratio, time_to_mfe.
    """
    if exit_bar <= entry_bar or entry_bar < 0:
        return {"mfe_pct": 0.0, "mae_pct": 0.0, "mfe_mae_ratio": 0.0, "time_to_mfe": 0}

    hold_highs = highs[entry_bar:exit_bar + 1]
    hold_lows = lows[entry_bar:exit_bar + 1]
    n_bars = len(hold_highs)

    if direction == "long":
        # Favorable = price goes up, Adverse = price goes down
        mfe_pct = float(np.max(hold_highs) / entry_price - 1.0) * 100
        mae_pct = float(1.0 - np.min(hold_lows) / entry_price) * 100
        # Time to MFE: which bar hit the max high?
        peak_bar = int(np.argmax(hold_highs))
    else:  # short
        # Favorable = price goes down, Adverse = price goes up
        mfe_pct = float(1.0 - np.min(hold_lows) / entry_price) * 100
        mae_pct = float(np.max(hold_highs) / entry_price - 1.0) * 100
        peak_bar = int(np.argmin(hold_lows))

    mfe_mae_ratio = mfe_pct / max(mae_pct, 0.01)  # avoid div by zero

    return {
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "mfe_mae_ratio": round(mfe_mae_ratio, 4),
        "time_to_mfe": peak_bar,  # in bars (= minutes for 1m data)
    }


# ── Feature computation (1m bars) ──

def compute_features_1m(
    df: pd.DataFrame,
    symbol: str = "",
    btc_df: pd.DataFrame = None,
) -> pd.DataFrame:
    """Compute all features for 1m OHLCV data. Returns DataFrame with FEATURE_NAMES columns."""
    o = df["open"].values.astype(np.float64)
    h = df["high"].values.astype(np.float64)
    l = df["low"].values.astype(np.float64)
    c = df["close"].values.astype(np.float64)
    v = df["volume"].values.astype(np.float64)
    n = len(df)

    if n < 60:
        LOG.warning("  %s: too few bars (%d) for features, need >=60", symbol, n)
        empty = pd.DataFrame(columns=FEATURE_NAMES + ["timestamp", "_atr_14_price", "symbol"])
        return empty

    rng = np.maximum(h - l, 1e-10)
    body = c - o

    feat = pd.DataFrame(index=df.index)
    feat["timestamp"] = df["timestamp"].values

    # G1: Price microstructure
    feat["body_pct"] = body / rng
    feat["close_pos"] = (c - l) / rng
    feat["range_pct"] = rng / np.maximum(c, 1e-10) * 100

    upper_wick = (h - np.maximum(o, c)) / rng
    lower_wick = (np.minimum(o, c) - l) / rng
    feat["wick_imbalance"] = lower_wick - upper_wick

    body_sign = (c > o).astype(float)
    body_consist = np.zeros(n)
    for i in range(1, n):
        if body_sign[i] == body_sign[i - 1] and body_sign[i] != 0:
            body_consist[i] = body_consist[i - 1] + 1
        else:
            body_consist[i] = 1 if body_sign[i] != 0 else 0
    feat["body_consistency_5"] = pd.Series(body_consist).rolling(5, min_periods=1).mean().values

    range_pct_s = pd.Series(feat["range_pct"].values.copy())
    avg_rng_3 = range_pct_s.rolling(3, min_periods=1).mean()
    avg_rng_20 = range_pct_s.rolling(20, min_periods=1).mean().replace(0, 1e-10)
    feat["range_expansion_3"] = (avg_rng_3 / avg_rng_20).clip(0, 10).values

    # G2: Returns + momentum
    for p, name in [(1, "ret_1"), (3, "ret_3"), (6, "ret_6"), (12, "ret_12"), (30, "ret_30")]:
        feat[name] = pd.Series(c).pct_change(p, fill_method=None).values

    ret1 = pd.Series(c).pct_change(1, fill_method=None).fillna(0).values
    signs = np.sign(ret1)
    consec = np.zeros(n)
    for i in range(1, n):
        if signs[i] == signs[i - 1] and signs[i] != 0:
            consec[i] = consec[i - 1] + signs[i]
        else:
            consec[i] = signs[i] if signs[i] != 0 else 0
    feat["consecutive_dir"] = consec

    ret1_std = pd.Series(ret1).rolling(50, min_periods=5).std().replace(0, 1e-10).values
    feat["momentum_strength"] = np.clip(np.abs(ret1) / ret1_std, 0, 10)

    ret12 = pd.Series(c).pct_change(12, fill_method=None)
    ret12_mean = ret12.rolling(50, min_periods=5).mean()
    ret12_std = ret12.rolling(50, min_periods=5).std().replace(0, 1e-10)
    feat["ret_z_12"] = ((ret12 - ret12_mean) / ret12_std).clip(-5, 5).values

    # G3: Volatility
    atr_14 = _atr(h, l, c, 14)
    feat["atr_pct"] = atr_14 / np.maximum(c, 1e-10) * 100
    feat["atr_percentile_50"] = pd.Series(feat["atr_pct"].values.copy()).rolling(50, min_periods=5).rank(pct=True).values

    sma_20 = pd.Series(c).rolling(20, min_periods=5).mean().values
    std_20 = pd.Series(c).rolling(20, min_periods=5).std().values
    bollinger_bw = 2 * std_20 / np.maximum(sma_20, 1e-10) * 100
    feat["squeeze_score"] = np.clip(bollinger_bw / np.maximum(feat["atr_pct"].values, 1e-10), 0, 20)
    feat["vol_regime"] = np.digitize(np.nan_to_num(feat["atr_pct"].values, nan=0.0), [0.3, 0.8, 2.0]).astype(float)

    # G4: Volume
    vol_ma = pd.Series(v).rolling(20, min_periods=5).mean().values
    vol_std = pd.Series(v).rolling(20, min_periods=5).std().values
    vol_ma_safe = np.maximum(vol_ma, 1e-10)
    vol_std_safe = np.maximum(vol_std, 1e-10)
    feat["vol_ratio"] = v / vol_ma_safe
    feat["vol_z"] = (v - vol_ma_safe) / vol_std_safe

    up_vol = pd.Series(np.where(c > o, v, 0.0)).rolling(10, min_periods=2).sum().values
    total_vol = pd.Series(v).rolling(10, min_periods=2).sum().values
    feat["vol_skew"] = (up_vol / np.maximum(total_vol, 1e-10) - 0.5) * 2
    feat["vol_acceleration"] = pd.Series(feat["vol_ratio"].values.copy()).diff(3).values
    feat["volume_conviction"] = np.clip(feat["vol_ratio"].values * np.abs(feat["body_pct"].values), 0, 5)

    # G5: Breakout context — shifted by 1 bar
    h_20 = pd.Series(h).rolling(20, min_periods=1).max().shift(1).values.copy()
    l_20 = pd.Series(l).rolling(20, min_periods=1).min().shift(1).values.copy()
    h_20[:20] = np.nanmax(h_20[20:40]) if len(h_20) > 40 else h_20[len(h_20) // 2] if len(h_20) > 0 else 0
    l_20[:20] = np.nanmin(l_20[20:40]) if len(l_20) > 40 else l_20[len(l_20) // 2] if len(l_20) > 0 else 0
    h_20 = np.nan_to_num(h_20, nan=h_20[~np.isnan(h_20)][0] if np.any(~np.isnan(h_20)) else 0)
    l_20 = np.nan_to_num(l_20, nan=l_20[~np.isnan(l_20)][0] if np.any(~np.isnan(l_20)) else 0)

    range_20 = np.maximum(h_20 - l_20, 1e-10)
    close_pos_20 = (c - l_20) / range_20
    feat["close_position_20"] = close_pos_20

    breakout_up_dist = np.maximum(c - h_20, 0)
    breakout_down_dist = np.maximum(l_20 - c, 0)
    breakout_strength = np.maximum(breakout_up_dist, breakout_down_dist) / range_20
    feat["breakout_strength"] = breakout_strength

    feat["is_at_high_20"] = np.clip((close_pos_20 - 0.9) / 0.1, 0, 1)
    feat["is_at_low_20"] = np.clip((0.1 - close_pos_20) / 0.1, 0, 1)
    feat["breakout_volume_score"] = feat["vol_ratio"].values * breakout_strength

    at_high = c >= h_20 * 0.999
    at_low = c <= l_20 * 1.001
    breakout_age = np.zeros(n)
    age_counter = 999
    for i in range(n):
        if at_high[i] or at_low[i]:
            age_counter = 0 if age_counter == 999 else age_counter + 1
        else:
            age_counter = 999
        breakout_age[i] = min(age_counter, 50) / 50.0
    feat["breakout_age"] = breakout_age

    # G6: Trend alignment
    ema_9 = _ema(c, 9)
    ema_21 = _ema(c, 21)
    ema_50 = _ema(c, 50)
    atr_safe = np.maximum(atr_14, 1e-10)

    feat["dist_ema9_atr"] = (c - ema_9) / atr_safe
    feat["dist_ema21_atr"] = (c - ema_21) / atr_safe
    feat["ema_alignment"] = np.sign(ema_9 - ema_21)
    feat["ema_trend_strength"] = (ema_9 - ema_50) / np.maximum(np.abs(ema_50), 1e-10) * 100

    # EMA21 bounce score
    ema21_bounce = np.zeros(n)
    for i in range(1, n):
        for j in range(max(0, i - 3), i):
            touch_dist = min(
                abs(l[j] - ema_21[j]) / atr_safe[i],
                abs(h[j] - ema_21[j]) / atr_safe[i],
            )
            if touch_dist < 0.5:
                ema21_bounce[i] = max(ema21_bounce[i], 1.0 - touch_dist / 0.5)
    feat["ema21_bounce_score"] = ema21_bounce

    # G7: Candle patterns
    feat["is_doji"] = (feat["body_pct"].abs() < 0.1).astype(float)
    feat["is_hammer"] = (lower_wick > 0.4).astype(float) * (upper_wick < 0.15).astype(float)
    feat["is_shooting_star"] = (upper_wick > 0.4).astype(float) * (lower_wick < 0.15).astype(float)
    feat["is_bull_pin"] = ((lower_wick > 0.6) & (feat["body_pct"].abs() < 0.3)).astype(float)
    feat["is_bear_pin"] = ((upper_wick > 0.6) & (feat["body_pct"].abs() < 0.3)).astype(float)

    prev_body = np.append(0, c[:-1] - o[:-1])
    feat["is_bullish_engulf"] = ((body > 0) & (prev_body < 0) & (c > np.append(c[0], o[:-1])) &
                                  (o < np.append(o[0], c[:-1]))).astype(float)
    feat["is_bearish_engulf"] = ((body < 0) & (prev_body > 0) & (c < np.append(c[0], o[:-1])) &
                                  (o > np.append(o[0], c[:-1]))).astype(float)

    # G8: Reversal + pullback
    if n >= 7:
        r1 = np.zeros(n)
        r2 = np.zeros(n)
        for i in range(6, n):
            r1[i] = (c[i - 3] - c[i - 6]) / max(c[i - 6], 1e-10)
            r2[i] = (c[i] - c[i - 3]) / max(c[i - 3], 1e-10)
        feat["v_reversal"] = ((np.sign(r1) != np.sign(r2)) & (np.abs(r2) > np.abs(r1) * 0.5)).astype(float)
    else:
        feat["v_reversal"] = 0.0

    trend = feat["ema_alignment"].values
    high_6 = pd.Series(h).rolling(6, min_periods=2).max().values
    low_6 = pd.Series(l).rolling(6, min_periods=2).min().values
    range_6 = np.maximum(high_6 - low_6, 1e-10)
    pullback = np.where(
        trend > 0, (high_6 - c) / range_6, (c - low_6) / range_6
    )
    feat["pullback_depth"] = np.clip(pullback, 0, 1)

    # G9: Temporal
    ts_col = df["timestamp"] if "timestamp" in df.columns else df.index
    try:
        ts_dt = pd.to_datetime(ts_col, unit="ms", utc=True)
        hour = ts_dt.dt.hour
        dow = ts_dt.dt.dayofweek
        feat["hour_sin"] = np.sin(2 * np.pi * hour / 24).values
        feat["hour_cos"] = np.cos(2 * np.pi * hour / 24).values
        feat["dow_sin"] = np.sin(2 * np.pi * dow / 7).values
        feat["dow_cos"] = np.cos(2 * np.pi * dow / 7).values
    except Exception:
        feat["hour_sin"] = 0.0
        feat["hour_cos"] = 0.0
        feat["dow_sin"] = 0.0
        feat["dow_cos"] = 0.0

    # ── G11: Multi-timeframe features (5m / 15m / 1h from 1m data) ──
    try:
        df_5m_raw = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v})

        # 5-minute aggregation (every 5 bars)
        group5 = np.arange(n) // 5
        agg5 = df_5m_raw.groupby(group5).agg(
            open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"), volume=("volume", "sum")
        )
        c5 = agg5["close"].values
        h5 = agg5["high"].values
        l5 = agg5["low"].values
        v5 = agg5["volume"].values
        n5 = len(agg5)

        if n5 >= 50:
            ema9_5 = _ema(c5, 9)
            ema21_5 = _ema(c5, 21)
            atr5_14 = _atr(h5, l5, c5, 14)
            vol_ma5_20 = pd.Series(v5).rolling(20, min_periods=5).mean().values

            mtf5_ema_align = np.sign(ema9_5 - ema21_5)
            mtf5_trend_str = (ema9_5 - ema21_5) / np.maximum(np.abs(ema21_5), 1e-10) * 100
            mtf5_ret3 = pd.Series(c5).pct_change(3, fill_method=None).values
            mtf5_vol_ratio = v5 / np.maximum(vol_ma5_20, 1e-10)
            mtf5_atr_pct = atr5_14 / np.maximum(c5, 1e-10) * 100

            bar_to_group5 = np.clip(np.arange(n) // 5, 0, n5 - 1)

            feat["mtf5_ema_align"] = mtf5_ema_align[bar_to_group5]
            feat["mtf5_trend_str"] = mtf5_trend_str[bar_to_group5]
            feat["mtf5_ret_3"] = mtf5_ret3[bar_to_group5]
            feat["mtf5_vol_ratio"] = mtf5_vol_ratio[bar_to_group5]
            feat["mtf5_atr_pct"] = mtf5_atr_pct[bar_to_group5]
        else:
            for col in ["mtf5_ema_align", "mtf5_trend_str", "mtf5_ret_3",
                        "mtf5_vol_ratio", "mtf5_atr_pct"]:
                feat[col] = 0.0

        # 15-minute aggregation (every 15 bars)
        group15 = np.arange(n) // 15
        agg15 = df_5m_raw.groupby(group15).agg(
            open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"), volume=("volume", "sum")
        )
        c15 = agg15["close"].values
        h15 = agg15["high"].values
        l15 = agg15["low"].values
        n15 = len(agg15)

        if n15 >= 50:
            ema9_15 = _ema(c15, 9)
            ema21_15 = _ema(c15, 21)
            atr15_14 = _atr(h15, l15, c15, 14)

            mtf15_ema_align = np.sign(ema9_15 - ema21_15)
            mtf15_trend_str = (ema9_15 - ema21_15) / np.maximum(np.abs(ema21_15), 1e-10) * 100
            mtf15_ret3 = pd.Series(c15).pct_change(3, fill_method=None).values
            mtf15_atr_pct = atr15_14 / np.maximum(c15, 1e-10) * 100

            bar_to_group15 = np.clip(np.arange(n) // 15, 0, n15 - 1)

            feat["mtf15_ema_align"] = mtf15_ema_align[bar_to_group15]
            feat["mtf15_trend_str"] = mtf15_trend_str[bar_to_group15]
            feat["mtf15_ret_3"] = mtf15_ret3[bar_to_group15]
            feat["mtf15_atr_pct"] = mtf15_atr_pct[bar_to_group15]
        else:
            for col in ["mtf15_ema_align", "mtf15_trend_str", "mtf15_ret_3", "mtf15_atr_pct"]:
                feat[col] = 0.0

        # 1-hour aggregation (every 60 bars)
        group60 = np.arange(n) // 60
        agg60 = df_5m_raw.groupby(group60).agg(
            open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"), volume=("volume", "sum")
        )
        c60 = agg60["close"].values
        h60 = agg60["high"].values
        l60 = agg60["low"].values
        n60 = len(agg60)

        if n60 >= 30:
            ema9_60 = _ema(c60, 9)
            ema21_60 = _ema(c60, 21)
            atr60_14 = _atr(h60, l60, c60, 14)

            mtf1h_ema_align = np.sign(ema9_60 - ema21_60)
            mtf1h_trend_str = (ema9_60 - ema21_60) / np.maximum(np.abs(ema21_60), 1e-10) * 100
            mtf1h_ret3 = pd.Series(c60).pct_change(3, fill_method=None).values
            mtf1h_atr_pct = atr60_14 / np.maximum(c60, 1e-10) * 100

            bar_to_group60 = np.clip(np.arange(n) // 60, 0, n60 - 1)

            feat["mtf1h_ema_align"] = mtf1h_ema_align[bar_to_group60]
            feat["mtf1h_trend_str"] = mtf1h_trend_str[bar_to_group60]
            feat["mtf1h_ret_3"] = mtf1h_ret3[bar_to_group60]
            feat["mtf1h_atr_pct"] = mtf1h_atr_pct[bar_to_group60]
        else:
            for col in ["mtf1h_ema_align", "mtf1h_trend_str", "mtf1h_ret_3", "mtf1h_atr_pct"]:
                feat[col] = 0.0

        # Trend agreement: 1m, 5m, 15m, 1h all aligned
        ema1_align = feat["ema_alignment"].values
        mtf5_align = feat["mtf5_ema_align"].values
        mtf15_align = feat["mtf15_ema_align"].values
        mtf1h_align = feat["mtf1h_ema_align"].values

        agree_count = (
            (ema1_align * mtf5_align > 0).astype(float) +
            (ema1_align * mtf15_align > 0).astype(float) +
            (ema1_align * mtf1h_align > 0).astype(float)
        )
        # 0 = no agreement, 1 = one other TF, 2 = two, 3 = all three
        feat["mtf_trend_agree"] = agree_count / 3.0  # normalized to [0, 1]

    except Exception as e:
        LOG.warning("  %s: MTF feature computation failed: %s", symbol, str(e)[:80])
        for col in ["mtf5_ema_align", "mtf5_trend_str", "mtf5_ret_3",
                    "mtf5_vol_ratio", "mtf5_atr_pct",
                    "mtf15_ema_align", "mtf15_trend_str", "mtf15_ret_3",
                    "mtf15_atr_pct",
                    "mtf1h_ema_align", "mtf1h_trend_str", "mtf1h_ret_3",
                    "mtf1h_atr_pct",
                    "mtf_trend_agree"]:
            feat[col] = 0.0

    # ── G12: BTC correlation features ──
    if btc_df is not None and len(btc_df) > 50:
        try:
            # Align BTC data to the same timestamps as the symbol
            btc_ts = btc_df["timestamp"].values.astype(np.int64)
            sym_ts = df["timestamp"].values.astype(np.int64)

            btc_close = btc_df["close"].values.astype(np.float64)

            # Create a mapping: sym timestamp → btc close
            btc_close_map = dict(zip(btc_ts, btc_close))

            # Map BTC close to symbol bars (nearest timestamp)
            btc_c = np.array([btc_close_map.get(int(ts), np.nan) for ts in sym_ts], dtype=np.float64)

            # Forward-fill any missing BTC bars
            btc_s = pd.Series(btc_c)
            btc_c = btc_s.ffill().bfill().values

            if len(btc_c) > 50:
                # BTC 1-minute return
                btc_ret1 = pd.Series(btc_c).pct_change(1, fill_method=None).values
                feat["btc_ret_1m"] = np.clip(btc_ret1 * 100, -5, 5)  # in %

                # BTC 5-minute return
                btc_ret5 = pd.Series(btc_c).pct_change(5, fill_method=None).values
                feat["btc_ret_5m"] = np.clip(btc_ret5 * 100, -10, 10)  # in %

                # BTC impulse score: sudden large BTC move
                btc_ret1_abs = np.abs(btc_ret1)
                btc_ret1_std = pd.Series(btc_ret1_abs).rolling(50, min_periods=5).std().replace(0, 1e-10).values
                feat["btc_impulse_score"] = np.clip(btc_ret1_abs / btc_ret1_std, 0, 10)

                # Alt-BTC lag: does the altcoin lag BTC?
                sym_ret1 = pd.Series(c).pct_change(1, fill_method=None).values
                # Correlation of current alt return with PREVIOUS bar BTC return
                btc_ret1_shifted = pd.Series(btc_ret1).shift(1).values
                valid = ~np.isnan(btc_ret1_shifted) & ~np.isnan(sym_ret1)
                if valid.sum() > 10:
                    # Rolling correlation of alt vs lagged BTC
                    alt_ret_s = pd.Series(sym_ret1)
                    btc_lag_s = pd.Series(btc_ret1_shifted)
                    corr30 = alt_ret_s.rolling(30, min_periods=10).corr(btc_lag_s)
                    feat["alt_btc_lag_1"] = corr30.fillna(0).values
                else:
                    feat["alt_btc_lag_1"] = 0.0

                # Rolling 30-bar correlation of alt vs BTC returns
                alt_ret_s = pd.Series(sym_ret1)
                btc_ret_s = pd.Series(btc_ret1)
                corr30 = alt_ret_s.rolling(30, min_periods=10).corr(btc_ret_s)
                feat["alt_btc_corr_30"] = corr30.fillna(0).values
            else:
                for col in ["btc_ret_1m", "btc_ret_5m", "btc_impulse_score",
                            "alt_btc_lag_1", "alt_btc_corr_30"]:
                    feat[col] = 0.0
        except Exception as e:
            LOG.warning("  %s: BTC features failed: %s", symbol, str(e)[:80])
            for col in ["btc_ret_1m", "btc_ret_5m", "btc_impulse_score",
                        "alt_btc_lag_1", "alt_btc_corr_30"]:
                feat[col] = 0.0
    else:
        for col in ["btc_ret_1m", "btc_ret_5m", "btc_impulse_score",
                    "alt_btc_lag_1", "alt_btc_corr_30"]:
            feat[col] = 0.0

    # Placeholders for direction + labels (filled later)
    feat["trade_direction"] = np.nan
    feat["label"] = np.nan
    feat["mfe_pct"] = np.nan
    feat["mae_pct"] = np.nan
    feat["mfe_mae_ratio"] = np.nan
    feat["time_to_mfe"] = np.nan
    feat["_atr_14_price"] = atr_14
    feat["symbol"] = symbol

    # Clean inf/nan
    for col in FEATURE_NAMES:
        if col in feat.columns:
            feat[col] = feat[col].replace([np.inf, -np.inf], np.nan)

    return feat


# ── Robust timestamp-to-milliseconds conversion ──

def _ts_to_ms(series: pd.Series) -> pd.Series:
    """Convert tz-aware datetime Series to milliseconds (int64)."""
    try:
        ms_series = series.dt.as_unit("ms")
        return ms_series.view("int64").astype(np.int64)
    except (TypeError, ValueError, AttributeError):
        pass
    try:
        return series.apply(lambda ts: int(ts.timestamp() * 1000)).astype(np.int64)
    except (TypeError, ValueError, AttributeError):
        pass
    try:
        naive = series.dt.tz_convert(None)
        ms_series = naive.as_unit("ms")
        return ms_series.view("int64").astype(np.int64)
    except (TypeError, ValueError, AttributeError):
        pass
    try:
        val = series.astype(np.int64)
        sample = int(val.iloc[0]) if len(val) > 0 else 0
        if 1e9 < sample < 2e9:
            return (val * 1000).astype(np.int64)
        elif 1e18 < sample < 2e18:
            return (val // 1_000_000).astype(np.int64)
        else:
            raise ValueError(f"Unexpected timestamp value: {sample}")
    except (TypeError, ValueError):
        pass
    raise ValueError("Cannot convert tz-aware datetime to milliseconds!")


# ── Download 1m OHLCV with proper pagination ──

def download_1m(symbol: str, start_ts_ms: int, end_ts_ms: int) -> pd.DataFrame:
    """Download 1m OHLCV with caching. Paginates correctly across the full date range."""
    cache_file = CACHE_DIR / f"{symbol}_1m.parquet"

    # ── Check cache with overlap validation ──
    if cache_file.exists():
        cached = pd.read_parquet(cache_file)
        if len(cached) > 0:
            cache_start = int(cached["timestamp"].min())
            cache_end = int(cached["timestamp"].max())
            if cache_start <= start_ts_ms + 86400000 and cache_end >= end_ts_ms - 86400000:
                mask = (cached["timestamp"] >= start_ts_ms) & (cached["timestamp"] <= end_ts_ms)
                result = cached[mask].copy()
                if len(result) > 0:
                    LOG.info("  %s: %d bars from cache (1m)  [%s -> %s]",
                             symbol, len(result),
                             pd.to_datetime(result["timestamp"].min(), unit="ms").strftime("%Y-%m-%d"),
                             pd.to_datetime(result["timestamp"].max(), unit="ms").strftime("%Y-%m-%d"))
                    return result
            else:
                LOG.info("  %s: cache exists but doesn't cover needed range", symbol)

    import ccxt

    for exchange_id in ["binance", "bybit", "mexc"]:
        try:
            exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
            exchange.load_markets()

            candidates = [
                f"{symbol}/USDT",
                f"{symbol}/USDT:USDT",
                f"{symbol}USDT",
            ]
            ccxt_sym = None
            for cand in candidates:
                if cand in exchange.markets:
                    ccxt_sym = cand
                    break

            if ccxt_sym is None:
                for market_id in exchange.markets:
                    base = exchange.markets[market_id].get("base", "")
                    if base.upper() == symbol and "USDT" in market_id.upper():
                        ccxt_sym = market_id
                        break

            if ccxt_sym is None:
                continue

            limit = 1000
            all_ohlcv = []
            since = start_ts_ms
            max_iterations = 2000
            iteration = 0

            total_days = (end_ts_ms - start_ts_ms) / 86400000
            LOG.info("  %s: fetching from %s (%.0f days)...",
                     symbol, exchange_id, total_days)

            while since < end_ts_ms and iteration < max_iterations:
                iteration += 1
                try:
                    ohlcv = exchange.fetch_ohlcv(ccxt_sym, "1m", since=since, limit=limit)
                except Exception as e:
                    LOG.warning("  %s on %s: fetch error: %s", symbol, exchange_id, str(e)[:80])
                    time.sleep(3)
                    try:
                        ohlcv = exchange.fetch_ohlcv(ccxt_sym, "1m", since=since, limit=limit)
                    except Exception:
                        break

                if not ohlcv or len(ohlcv) == 0:
                    break

                all_ohlcv.extend(ohlcv)
                last_ts = ohlcv[-1][0]

                if last_ts >= end_ts_ms:
                    break

                if len(ohlcv) < 5:
                    break

                since = last_ts + 60000
                time.sleep(exchange.rateLimit / 1000)

            if all_ohlcv:
                df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

                if cache_file.exists():
                    try:
                        old = pd.read_parquet(cache_file)
                        df = pd.concat([old, df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
                    except Exception:
                        pass

                df.to_parquet(cache_file, index=False)

                result = df[(df["timestamp"] >= start_ts_ms) & (df["timestamp"] <= end_ts_ms)].copy()
                LOG.info("  %s: %d bars from %s (1m)  [%s -> %s]",
                         symbol, len(result), exchange_id,
                         pd.to_datetime(result["timestamp"].min(), unit="ms").strftime("%Y-%m-%d"),
                         pd.to_datetime(result["timestamp"].max(), unit="ms").strftime("%Y-%m-%d"))
                return result

        except Exception as e:
            LOG.warning("  %s on %s: %s", symbol, exchange_id, str(e)[:80])
            time.sleep(2)

    LOG.warning("  %s: no 1m data available on any exchange", symbol)
    return pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description="V10 Build Dataset")
    parser.add_argument("--neg-ratio", type=float, default=3.0,
                        help="Ratio of negative samples per positive (default: 3)")
    parser.add_argument("--big-loss", type=float, default=5.0,
                        help="Re-filter with this threshold (default: $5)")
    parser.add_argument("--max-symbols", type=int, default=50,
                        help="Max symbols to process (default: 50 = all)")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Delete all cached OHLCV data and re-download")
    parser.add_argument("--skip-btc", action="store_true",
                        help="Skip BTC correlation features (faster)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")

    if args.clear_cache:
        LOG.info("Clearing OHLCV cache...")
        for f in CACHE_DIR.glob("*.parquet"):
            f.unlink()
        LOG.info("Cache cleared")

    # Load filtered trades (reuse v9 output)
    trades_path = PROJECT_ROOT / "data" / "v9" / "filtered_trades.json"
    if not trades_path.exists():
        LOG.error("No filtered_trades.json. Run v9/parse_trades.py first!")
        sys.exit(1)

    with open(trades_path) as f:
        trades = json.load(f)

    LOG.info("Loaded %d filtered trades", len(trades))
    tdf = pd.DataFrame(trades)

    # Timestamp conversion
    tdf["entry_ts"] = pd.to_datetime(tdf["entry_time"], utc=True)
    tdf["exit_ts"] = pd.to_datetime(tdf["exit_time"], utc=True)
    tdf["entry_ts_ms"] = _ts_to_ms(tdf["entry_ts"])
    tdf["exit_ts_ms"] = _ts_to_ms(tdf["exit_ts"])

    sample_ts = int(tdf["entry_ts_ms"].iloc[0]) if len(tdf) > 0 else 0
    sample_date = pd.to_datetime(sample_ts, unit="ms").strftime("%Y-%m-%d %H:%M")
    LOG.info("Sample entry_ts_ms: %d (%s)", sample_ts, sample_date)

    if sample_ts < 1e12 or sample_ts > 2e12:
        LOG.error("Timestamp conversion looks wrong! Got %d", sample_ts)
        sys.exit(1)

    trade_min = int(tdf["entry_ts_ms"].min())
    trade_max = int(tdf["entry_ts_ms"].max())
    LOG.info("Trade dates: %s -> %s (%.0f days)",
             pd.to_datetime(trade_min, unit="ms").strftime("%Y-%m-%d"),
             pd.to_datetime(trade_max, unit="ms").strftime("%Y-%m-%d"),
             (trade_max - trade_min) / 86400000)

    sym_counts = tdf["symbol"].value_counts()
    top_syms = sym_counts.head(args.max_symbols).index.tolist()
    LOG.info("Processing %d symbols: %s", len(top_syms), top_syms[:10])

    # ── Download BTC data for correlation features ──
    btc_df = None
    if not args.skip_btc:
        LOG.info("Downloading BTC 1m data for correlation features...")
        btc_start = trade_min - 3 * 86400000
        btc_end = trade_max + 86400000
        btc_df = download_1m("BTC", btc_start, btc_end)
        if len(btc_df) < 100:
            LOG.warning("BTC data insufficient (%d bars), correlation features will be 0", len(btc_df))
            btc_df = None
        else:
            LOG.info("BTC data: %d bars", len(btc_df))

    # Build lookup: symbol -> list of trade info
    entry_lookup = {}
    for sym in top_syms:
        sym_trades = tdf[tdf["symbol"] == sym]
        lookup = []
        for _, row in sym_trades.iterrows():
            exact_ms = int(row["entry_ts_ms"])
            rounded_ms = (exact_ms // 60000) * 60000
            direction = row["direction"]
            is_win = bool(row.get("is_win", True))
            lookup.append({
                "rounded_ms": rounded_ms,
                "exact_ms": exact_ms,
                "direction": direction,
                "is_win": is_win,
                "exit_ts_ms": int(row["exit_ts_ms"]),
            })
        entry_lookup[sym] = lookup

    # Download 1m data per symbol
    all_features = []
    mfe_stats = {"winners": [], "losers": []}

    for i_sym, symbol in enumerate(top_syms):
        sym_trades = tdf[tdf["symbol"] == symbol]
        LOG.info("[%d/%d] %s: %d trades", i_sym + 1, len(top_syms), symbol, len(sym_trades))

        start_ts = int(sym_trades["entry_ts_ms"].min()) - 3 * 86400000
        end_ts = int(sym_trades["exit_ts_ms"].max()) + 86400000

        ohlcv = download_1m(symbol, start_ts, end_ts)
        if len(ohlcv) < 100:
            LOG.warning("  %s: insufficient data (%d bars), skipping", symbol, len(ohlcv))
            continue

        # Verify overlap
        ohlcv_min = int(ohlcv["timestamp"].min())
        ohlcv_max = int(ohlcv["timestamp"].max())
        trade_min_sym = int(sym_trades["entry_ts_ms"].min())
        trade_max_sym = int(sym_trades["exit_ts_ms"].max())
        overlap = ohlcv_min <= trade_max_sym and ohlcv_max >= trade_min_sym

        if not overlap:
            LOG.warning("  %s: OHLCV data doesn't overlap with trades!", symbol)
            continue

        # Compute features
        feat_df = compute_features_1m(ohlcv, symbol=symbol, btc_df=btc_df)
        if len(feat_df) == 0:
            LOG.warning("  %s: no features computed, skipping", symbol)
            continue

        # ── Mark POSITIVE samples (trader entry bars) + compute MFE/MAE ──
        feat_ts = feat_df["timestamp"].values.astype(np.int64)
        feat_ts_index = {int(ts): i for i, ts in enumerate(feat_ts)}

        highs = ohlcv["high"].values.astype(np.float64)
        lows = ohlcv["low"].values.astype(np.float64)

        sym_lookup = entry_lookup.get(symbol, [])
        n_matched = 0
        n_exact = 0
        n_closest = 0
        n_winners = 0
        n_losers = 0

        for trade_info in sym_lookup:
            rounded_ms = trade_info["rounded_ms"]
            exact_ms = trade_info["exact_ms"]
            direction = trade_info["direction"]
            is_win = trade_info["is_win"]
            exit_ts_ms = trade_info["exit_ts_ms"]

            matched = False
            bar_idx = None

            # Try 1: Exact match on rounded timestamp
            if rounded_ms in feat_ts_index:
                bar_idx = feat_ts_index[rounded_ms]
                n_exact += 1
                matched = True

            if not matched and exact_ms in feat_ts_index:
                bar_idx = feat_ts_index[exact_ms]
                n_exact += 1
                matched = True

            if not matched:
                diffs = np.abs(feat_ts - exact_ms)
                bar_idx = int(np.argmin(diffs))
                if diffs[bar_idx] <= 120000:
                    n_closest += 1
                    matched = True
                else:
                    bar_idx = None

            if matched and bar_idx is not None:
                # Set binary label + direction
                feat_df.iloc[bar_idx, feat_df.columns.get_loc("trade_direction")] = 1.0 if direction == "long" else -1.0
                feat_df.iloc[bar_idx, feat_df.columns.get_loc("label")] = 1.0 if is_win else -1.0

                # ── Compute MFE/MAE ──
                # Find exit bar index
                exit_rounded = (exit_ts_ms // 60000) * 60000
                exit_bar = feat_ts_index.get(exit_rounded)
                if exit_bar is None:
                    # Find closest
                    exit_diffs = np.abs(feat_ts - exit_ts_ms)
                    exit_bar = int(np.argmin(exit_diffs))

                entry_price = float(ohlcv["close"].iloc[bar_idx]) if bar_idx < len(ohlcv) else 0
                if entry_price > 0 and exit_bar > bar_idx:
                    mfe_data = compute_mfe_mae(
                        entry_price, bar_idx, exit_bar,
                        direction, highs, lows
                    )
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("mfe_pct")] = mfe_data["mfe_pct"]
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("mae_pct")] = mfe_data["mae_pct"]
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("mfe_mae_ratio")] = mfe_data["mfe_mae_ratio"]
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("time_to_mfe")] = mfe_data["time_to_mfe"]

                    if is_win:
                        mfe_stats["winners"].append(mfe_data)
                    else:
                        mfe_stats["losers"].append(mfe_data)

                n_matched += 1
                n_winners += int(is_win)
                n_losers += int(not is_win)

        LOG.info("  %s: matched %d / %d trades (exact=%d, closest=%d)  winners=%d losers=%d",
                 symbol, n_matched, len(sym_trades), n_exact, n_closest, n_winners, n_losers)

        # ── Mark NEGATIVE samples ──
        entry_ms_set = set()
        for trade_info in sym_lookup:
            entry_ms_set.add(int(trade_info["rounded_ms"]))
            entry_ms_set.add(int(trade_info["exact_ms"]))

        entry_windows = set()
        for ems in entry_ms_set:
            low_ms = ems - 900000
            high_ms = ems + 900000
            window_mask = (feat_ts >= low_ms) & (feat_ts <= high_ms)
            window_ts = feat_ts[window_mask]
            entry_windows.update(window_ts.tolist())

        non_entry_mask = ~feat_df["timestamp"].astype(np.int64).isin(entry_windows)
        non_entry_bars = feat_df[non_entry_mask]

        n_winners = int((feat_df["label"] == 1.0).sum())
        n_random_neg = min(int(n_winners * args.neg_ratio), len(non_entry_bars))

        if n_random_neg > 0 and n_winners > 0:
            neg_sample = non_entry_bars.sample(n=n_random_neg, random_state=42)
            for idx in neg_sample.index:
                feat_df.loc[idx, "trade_direction"] = np.random.choice([1.0, -1.0])
                feat_df.loc[idx, "label"] = 0.0
                # Random negatives get MFE/MAE = 0 (no trade happened)
                feat_df.loc[idx, "mfe_pct"] = 0.0
                feat_df.loc[idx, "mae_pct"] = 0.0
                feat_df.loc[idx, "mfe_mae_ratio"] = 0.0
                feat_df.loc[idx, "time_to_mfe"] = 0.0

        labeled = feat_df[feat_df["label"].notna() & feat_df["trade_direction"].notna()].copy()
        n_win_sym = int((labeled["label"] == 1.0).sum())
        n_lose_sym = int((labeled["label"] == -1.0).sum())
        n_rand_sym = int((labeled["label"] == 0.0).sum())
        LOG.info("  %s: %d winners + %d losers + %d random = %d total",
                 symbol, n_win_sym, n_lose_sym, n_rand_sym, len(labeled))

        all_features.append(labeled)

    if not all_features:
        LOG.error("No features computed!")
        sys.exit(1)

    combined = pd.concat(all_features, ignore_index=True)
    n_total = len(combined)
    n_win_total = int((combined["label"] == 1.0).sum())
    n_lose_total = int((combined["label"] == -1.0).sum())
    n_rand_total = int((combined["label"] == 0.0).sum())

    LOG.info("Combined dataset: %d rows (winners=%d / losers=%d / random=%d)",
             n_total, n_win_total, n_lose_total, n_rand_total)

    # ── MFE/MAE summary statistics ──
    if mfe_stats["winners"]:
        w_mfe = np.mean([m["mfe_pct"] for m in mfe_stats["winners"]])
        w_mae = np.mean([m["mae_pct"] for m in mfe_stats["winners"]])
        w_ratio = np.mean([m["mfe_mae_ratio"] for m in mfe_stats["winners"]])
        w_time = np.mean([m["time_to_mfe"] for m in mfe_stats["winners"]])
        LOG.info("WINNERS MFE/MAE: MFE=%.3f%% MAE=%.3f%% Ratio=%.2f TimeToMFE=%.1f bars",
                 w_mfe, w_mae, w_ratio, w_time)

    if mfe_stats["losers"]:
        l_mfe = np.mean([m["mfe_pct"] for m in mfe_stats["losers"]])
        l_mae = np.mean([m["mae_pct"] for m in mfe_stats["losers"]])
        l_ratio = np.mean([m["mfe_mae_ratio"] for m in mfe_stats["losers"]])
        l_time = np.mean([m["time_to_mfe"] for m in mfe_stats["losers"]])
        LOG.info("LOSERS  MFE/MAE: MFE=%.3f%% MAE=%.3f%% Ratio=%.2f TimeToMFE=%.1f bars",
                 l_mfe, l_mae, l_ratio, l_time)

    # Save
    output_path = DATA_DIR / "dataset.parquet"
    combined.to_parquet(output_path, index=False)
    LOG.info("Saved to %s", output_path)

    # Feature columns for training (exclude label + regression targets)
    feat_cols_path = DATA_DIR / "feature_columns.json"
    feat_cols = [c for c in FEATURE_NAMES if c not in
                 ("label", "mfe_pct", "mae_pct", "mfe_mae_ratio", "time_to_mfe")]
    with open(feat_cols_path, "w") as f:
        json.dump(feat_cols, f, indent=2)

    # Regression target columns
    reg_targets_path = DATA_DIR / "regression_targets.json"
    with open(reg_targets_path, "w") as f:
        json.dump(["mfe_pct", "mae_pct", "mfe_mae_ratio", "time_to_mfe"], f, indent=2)

    print(f"\n{'='*70}")
    print(f"V10 DATASET BUILT (v3 — Exit-Aware Labels + Enhanced MTF)")
    print(f"{'='*70}")
    print(f"  Total: {n_total} rows")
    print(f"  Winners (label=1.0):  {n_win_total}")
    print(f"  Losers  (label=-1.0): {n_lose_total} (hard negatives)")
    print(f"  Random  (label=0.0):  {n_rand_total} (easy negatives)")
    print(f"  Features: {N_FEATURES}")
    if mfe_stats["winners"]:
        print(f"  Winner MFE: {w_mfe:.3f}%  MAE: {w_mae:.3f}%  Ratio: {w_ratio:.2f}")
    if mfe_stats["losers"]:
        print(f"  Loser  MFE: {l_mfe:.3f}%  MAE: {l_mae:.3f}%  Ratio: {l_ratio:.2f}")
    print(f"  Regression targets: mfe_pct, mae_pct, mfe_mae_ratio, time_to_mfe")
    print(f"  Output: {output_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
