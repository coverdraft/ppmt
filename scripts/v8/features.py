"""
features.py — v8 Pattern-Based Features for Low-TF Multi-Token Trading

DESIGN PHILOSOPHY — Based on CORRECTED pattern analysis of 446 matched trades:
  
  DISCOVERED EDGES:
    BREAKOUT long:     230 trades, 73.9% WR, PnL +251.1  ← THE MAIN EDGE
    EMA_BOUNCE short:   14 trades, 85.7% WR, PnL +27.3   ← counter-trend edge
    LEVEL_TEST short:   11 trades, 100%  WR, PnL +33.2   ← support bounce
  
  THE HOLE:
    BREAKOUT short:    165 trades, 68.5% WR, PnL -556.2  ← THE HOLE
  
  KEY INSIGHT: Direction matters more than pattern type.
    Breakout LONG = profitable. Breakout SHORT = catastrophic.
    Counter-trend SHORT (EMA bounce, level test) = profitable.
    
  The `trade_direction` feature is CRITICAL — it lets the model learn
  that the same breakout features predict opposite EV per direction.
  
  RISK MANAGEMENT FINDING:
    Both directions have ~72% WR but 1:3 win/loss size ratio.
    Winners: median 8-9min. Losers: median 21-28min.
    → Time stop is the #1 edge preserver.

Feature groups:
  G1: Price microstructure (10) — bar structure, directional conviction
  G2: Returns + momentum (9)    — multi-scale, acceleration, z-scores
  G3: Volatility regime (5)     — ATR, squeeze, regime
  G4: Volume dynamics (5)       — ratio, skew, conviction
  G5: Breakout context (7)      — continuous breakout quality features
  G6: Trend alignment (5)       — EMA distance, bounce, trend quality
  G7: BTC lead/lag (5)          — cross-asset signals
  G8: Derivatives (4)           — funding, OI
  G9: Multi-timeframe (4)       — 1h context
  G10: Cross-sectional (3)      — sector momentum
  G11: Temporal (2)             — cyclical encoding
  G12: Token identity (2)       — sector encoding

Total: ~61 features (pattern-informed, continuous, model-friendly)
"""
from __future__ import annotations

import logging
import traceback
from typing import Optional

import numpy as np
import pandas as pd

# Force-disable Copy-on-Write — causes "assignment destination is read-only"
# errors on pandas 2.x when arrays pass through boolean indexing / merge
pd.options.mode.copy_on_write = False

LOG = logging.getLogger("v8_features")

# ---------------------------------------------------------------------------
# Feature definitions
# ---------------------------------------------------------------------------

# G1: Price microstructure (10)
FEATURES_G1 = [
    "body_pct",            # (close-open)/range — directional conviction
    "close_pos",           # (close-low)/range — buying pressure
    "range_pct",           # (high-low)/close*100 — bar range
    "wick_imbalance",      # (lower - upper) / range — net wick direction
    "body_consistency_5",  # fraction of last 5 bars with same direction
    "range_expansion_3",   # range_3avg / range_20avg — expansion signal
    "price_acceleration",  # 2nd derivative of close
    "dist_to_high_20",     # distance to 20-bar high (breakout proximity)
    "dist_to_low_20",      # distance to 20-bar low (breakdown proximity)
    "close_position_10",   # close position within 10-bar range [0,1]
]

# G2: Returns + momentum (9)
FEATURES_G2 = [
    "ret_1",               # 1-bar return (5min)
    "ret_3",               # 3-bar return (15min) — key for breakout quality
    "ret_6",               # 6-bar return (30min = our time horizon)
    "ret_12",              # 12-bar return (1h)
    "consecutive_dir",     # consecutive bars same direction (signed)
    "momentum_strength",   # |ret_1| / rolling σ — impulse detection
    "ret_z_6",             # z-score of 6-bar return
    "trend_50",            # EMA(9) vs EMA(50) direction
    "pullback_depth",      # how much retrace within trend
]

# G3: Volatility regime (5)
FEATURES_G3 = [
    "atr_pct",             # ATR(14)/close*100
    "atr_pct_lagged",      # ATR from 3 bars ago — matches label TP/SL
    "atr_percentile_50",   # ATR rank vs last 50 bars
    "squeeze_score",       # Bollinger bandwidth / ATR — contraction
    "vol_regime",          # discrete vol regime (0-3)
]

# G4: Volume dynamics (5)
FEATURES_G4 = [
    "vol_ratio",           # current vol / 20-bar avg — KEY breakout confirm
    "vol_z",               # volume z-score
    "vol_skew",            # directional volume
    "vol_acceleration",    # change in vol_ratio over 3 bars
    "volume_conviction",   # vol_ratio * body_pct
]

# G5: Breakout context (7) — NEW: continuous breakout quality
# These replace the coarse binary breakout flags.
# The model learns: "what breakout quality predicts positive EV?"
FEATURES_G5 = [
    "close_position_20",   # [0,1] where is close in 20-bar range? (>0.95 = breakout)
    "breakout_strength",   # how far beyond 20-bar high/low (normalized)
    "is_at_high_20",       # continuous: close_pos_20 > 0.95 → 1.0
    "is_at_low_20",        # continuous: close_pos_20 < 0.05 → 1.0
    "breakout_volume_score",# vol_ratio * breakout_strength — confirms real break
    "breakout_age",        # bars since first touch of 20-bar high/low (fresh vs stale)
    "range_breakout_pct",  # (close - high_20) / atr → normalized breakout distance
]

# G6: Trend alignment (5) — NEW: distinguishes BREAKOUT_UP from BREAKOUT_DOWN
# From analysis: ema_alignment +0.68 for winners, dist_ema21_atr key differentiator
FEATURES_G6 = [
    "dist_ema9_atr",       # distance to EMA9 in ATR units — proximity
    "dist_ema21_atr",      # distance to EMA21 in ATR units — KEY feature
    "ema_alignment",       # sign(EMA9 - EMA21) — trend direction
    "ema_trend_strength",  # (EMA9 - EMA50) / EMA50 * 100 — trend power
    "ema21_bounce_score",  # continuous: how close did price touch EMA21 recently
]

# G7: BTC lead/lag (5)
FEATURES_G7 = [
    "btc_ret_1m",
    "btc_ret_5m",
    "btc_impulse_score",
    "alt_btc_lag_1",
    "alt_btc_spread_5m",
]

# G8: Derivatives (4)
FEATURES_G8 = [
    "funding_rate_z",
    "oi_change_1h",
    "oi_change_4h",
    "funding_oi_divergence",
]

# G9: Multi-timeframe context (4)
FEATURES_G9 = [
    "ret_1h",
    "ema_trend_1h",
    "vol_regime_1h",
    "atr_pct_1h",
]

# G10: Cross-sectional (3)
FEATURES_G10 = [
    "sector_idx",
    "sector_momentum_5m",
    "token_rel_strength",
]

# G11: Temporal (2)
FEATURES_G11 = [
    "hour_sin",
    "hour_cos",
]

# G12: Token identity (2)
FEATURES_G12 = [
    "sector_idx_float",
    "price_tier",
]

# G13: Pattern signals (4) — discrete flags for pattern-gated trading
# These are the KEY signals from corrected pattern analysis:
#   BREAKOUT long: +251 → signal_breakout_up allows LONG
#   BREAKOUT short: -556 → signal_breakout_down BLOCKS SHORT (THE HOLE)
#   EMA_BOUNCE short: +27 → signal_ema_bounce allows SHORT
#   LEVEL_TEST short: +33 → signal_level_test allows SHORT
FEATURES_G13 = [
    "signal_breakout_up",
    "signal_breakout_down",
    "signal_ema_bounce",
    "signal_level_test",
]

FEATURE_NAMES = (
    FEATURES_G1 + FEATURES_G2 + FEATURES_G3 + FEATURES_G4 + FEATURES_G5 +
    FEATURES_G6 + FEATURES_G7 + FEATURES_G8 + FEATURES_G9 + FEATURES_G10 +
    FEATURES_G11 + FEATURES_G12 + FEATURES_G13 +
    ["trade_direction"]  # +1=LONG, -1=SHORT — added by model.py at expand time
)

N_FEATURES = len(FEATURE_NAMES)
LOG.info("v8 Pattern-based features: %d total (%d groups)", N_FEATURES, 13)

# ---------------------------------------------------------------------------
# Sector definitions
# ---------------------------------------------------------------------------

SECTOR_TOKENS = {
    "blue_chip": ["BTC", "ETH"],
    "large_cap": ["SOL", "ADA", "AVAX", "LINK", "XRP"],
    "old_meme":  ["DOGE", "SHIB", "PEPE"],
    "new_meme":  ["WIF", "BONK", "PIPPIN", "PENGU", "RIVER", "PUMP", "HYPE"],
}

SECTOR_INDEX = {"blue_chip": 0, "large_cap": 1, "old_meme": 2, "new_meme": 3}


def symbol_to_sector(symbol: str) -> str:
    base = symbol.split("/")[0].upper()
    for sector, tokens in SECTOR_TOKENS.items():
        if base in tokens:
            return sector
    return "large_cap"


def symbol_to_sector_idx(symbol: str) -> int:
    return SECTOR_INDEX.get(symbol_to_sector(symbol), 1)


def price_to_tier(price: float) -> float:
    """Map token price to tier based on pattern analysis."""
    if price < 0.05:
        return 0.0   # micro-cap meme
    elif price < 1.0:
        return 1.0   # small-cap
    elif price < 20.0:
        return 2.0   # mid-cap
    else:
        return 3.0   # large-cap


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Compute EMA for numpy array."""
    alpha = 2 / (period + 1)
    result = np.empty_like(arr, dtype=np.float64)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def compute_features(
    ohlcv_df: pd.DataFrame,
    btc_df: pd.DataFrame,
    eth_df: pd.DataFrame,
    symbol: str = "",
    funding_rate_z: float = 0.0,
    oi_change_1h: float = 0.0,
    oi_change_4h: float = 0.0,
    sector_avg_ret_5m: float = 0.0,
) -> pd.DataFrame:
    """Compute all v8 pattern-based features for a symbol's OHLCV data."""
    # Reconstruct DataFrame from fresh numpy arrays — pandas 2.x CoW can make
    # .copy() still return read-only backing arrays after boolean indexing / merge.
    _data = {col: ohlcv_df[col].values.copy() for col in ohlcv_df.columns}
    df = pd.DataFrame(_data)
    o = _data["open"].astype(np.float64)
    h = _data["high"].astype(np.float64)
    l = _data["low"].astype(np.float64)
    c = _data["close"].astype(np.float64)
    v = _data["volume"].astype(np.float64)
    n = len(df)

    rng = np.maximum(h - l, 1e-10)
    body = c - o

    # ── G1: Price microstructure ──────────────────────────────────
    df["body_pct"] = body / rng
    df["close_pos"] = (c - l) / rng
    df["range_pct"] = rng / np.maximum(c, 1e-10) * 100
    upper_wick = (h - np.maximum(o, c)) / rng
    lower_wick = (np.minimum(o, c) - l) / rng
    df["wick_imbalance"] = lower_wick - upper_wick

    body_sign = (c > o).astype(float)
    body_consist = np.zeros(n)
    for i in range(1, n):
        body_consist[i] = body_consist[i-1] + (1 if body_sign[i] == body_sign[i-1] and body_sign[i] != 0 else 0)
        if body_sign[i] != body_sign[i-1] or body_sign[i] == 0:
            body_consist[i] = 1 if body_sign[i] != 0 else 0
    df["body_consistency_5"] = pd.Series(body_consist).rolling(5, min_periods=1).mean().values

    range_pct_series = pd.Series(df["range_pct"].values)
    avg_rng_3 = range_pct_series.rolling(3, min_periods=1).mean()
    avg_rng_20 = range_pct_series.rolling(20, min_periods=1).mean().replace(0, 1e-10)
    df["range_expansion_3"] = (avg_rng_3 / avg_rng_20).clip(0, 10).values

    # Price acceleration
    prev_c = np.append(c[0], c[:-1])  # previous close per bar (length N)
    ret_1_raw = (np.diff(c, prepend=c[0]) / np.maximum(prev_c, 1e-10)).copy()  # prepend to keep length
    ret_1_raw[0] = 0
    accel = np.diff(ret_1_raw, prepend=0)
    df["price_acceleration"] = accel

    # 20-bar high/low distance
    h_20 = pd.Series(h).rolling(20, min_periods=1).max().values
    l_20 = pd.Series(l).rolling(20, min_periods=1).min().values
    df["dist_to_high_20"] = (c - h_20) / np.maximum(h_20, 1e-10) * 100
    df["dist_to_low_20"] = (c - l_20) / np.maximum(l_20, 1e-10) * 100

    high_10 = pd.Series(h).rolling(10, min_periods=1).max().values
    low_10 = pd.Series(l).rolling(10, min_periods=1).min().values
    range_10 = np.maximum(high_10 - low_10, 1e-10)
    df["close_position_10"] = (c - low_10) / range_10

    # ── G2: Returns + momentum ────────────────────────────────────
    df["ret_1"] = pd.Series(c).pct_change(1, fill_method=None).values
    df["ret_3"] = pd.Series(c).pct_change(3, fill_method=None).values
    df["ret_6"] = pd.Series(c).pct_change(6, fill_method=None).values
    df["ret_12"] = pd.Series(c).pct_change(12, fill_method=None).values

    # Consecutive direction
    ret1 = pd.Series(c).pct_change(1, fill_method=None).fillna(0).values
    signs = np.sign(ret1)
    consec = np.zeros(n)
    for i in range(1, n):
        if signs[i] == signs[i-1] and signs[i] != 0:
            consec[i] = consec[i-1] + signs[i]
        else:
            consec[i] = signs[i] if signs[i] != 0 else 0
    df["consecutive_dir"] = consec

    # Momentum strength
    ret1_series = pd.Series(ret1)
    ret1_std = ret1_series.rolling(50, min_periods=5).std().replace(0, 1e-10).values
    df["momentum_strength"] = np.clip(np.abs(ret1) / ret1_std, 0, 10)

    # Z-score of 6-bar return
    ret6_series = pd.Series(c).pct_change(6, fill_method=None)
    ret6_mean = ret6_series.rolling(50, min_periods=5).mean()
    ret6_std = ret6_series.rolling(50, min_periods=5).std().replace(0, 1e-10)
    df["ret_z_6"] = ((ret6_series - ret6_mean) / ret6_std).clip(-5, 5).values

    # Trend direction
    ema_9 = _ema(c, 9)
    ema_50 = _ema(c, 50)
    df["trend_50"] = np.sign(ema_9 - ema_50)

    # Pullback depth
    high_6 = pd.Series(h).rolling(6, min_periods=2).max().values
    low_6 = pd.Series(l).rolling(6, min_periods=2).min().values
    range_6 = np.maximum(high_6 - low_6, 1e-10)
    trend = df["trend_50"].values
    pullback = np.where(
        trend > 0,
        (high_6 - c) / range_6,   # uptrend: distance from recent high
        (c - low_6) / range_6,     # downtrend: distance from recent low
    )
    df["pullback_depth"] = np.clip(pullback, 0, 1)

    # ── G3: Volatility regime ─────────────────────────────────────
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.append(c[0], c[:-1])),
                                       np.abs(l - np.append(c[0], c[:-1]))))
    atr_14 = pd.Series(tr).rolling(14, min_periods=5).mean().values
    df["atr_pct"] = atr_14 / np.maximum(c, 1e-10) * 100
    df["atr_pct_lagged"] = pd.Series(df["atr_pct"].values).shift(3).values

    atr_pct_series = pd.Series(df["atr_pct"].values)
    df["atr_percentile_50"] = atr_pct_series.rolling(50, min_periods=5).rank(pct=True).values

    # Squeeze score
    sma_20 = pd.Series(c).rolling(20, min_periods=5).mean().values
    std_20 = pd.Series(c).rolling(20, min_periods=5).std().values
    bollinger_bw = 2 * std_20 / np.maximum(sma_20, 1e-10) * 100
    df["squeeze_score"] = np.clip(bollinger_bw / np.maximum(df["atr_pct"].values, 1e-10), 0, 20)

    # Vol regime
    df["vol_regime"] = np.digitize(np.nan_to_num(df["atr_pct"].values, nan=0.0), [0.3, 0.8, 2.0]).astype(float)

    # Store ATR_14 in price units for label computation
    df["_atr_14_price"] = atr_14

    # ── G4: Volume dynamics ───────────────────────────────────────
    vol_ma = pd.Series(v).rolling(20, min_periods=5).mean().values
    vol_std = pd.Series(v).rolling(20, min_periods=5).std().values
    vol_ma_safe = np.maximum(vol_ma, 1e-10)
    vol_std_safe = np.maximum(vol_std, 1e-10)
    df["vol_ratio"] = v / vol_ma_safe
    df["vol_z"] = (v - vol_ma_safe) / vol_std_safe

    # Volume skew
    up_vol = pd.Series(np.where(c > o, v, 0.0)).rolling(10, min_periods=2).sum().values
    total_vol = pd.Series(v).rolling(10, min_periods=2).sum().values
    df["vol_skew"] = (up_vol / np.maximum(total_vol, 1e-10) - 0.5) * 2

    df["vol_acceleration"] = pd.Series(df["vol_ratio"].values).diff(3).values

    # Volume conviction
    df["volume_conviction"] = np.clip(df["vol_ratio"].values * np.abs(df["body_pct"].values), 0, 5)

    # ── G5: Breakout context — NEW ────────────────────────────────
    # close_position_20: where is close in the 20-bar range? (already computed as close_pos_10)
    range_20 = np.maximum(h_20 - l_20, 1e-10)
    close_pos_20 = (c - l_20) / range_20
    df["close_position_20"] = close_pos_20

    # breakout_strength: how far beyond the range
    breakout_up_dist = np.maximum(c - h_20, 0)
    breakout_down_dist = np.maximum(l_20 - c, 0)
    breakout_strength = np.maximum(breakout_up_dist, breakout_down_dist) / range_20
    df["breakout_strength"] = breakout_strength

    # is_at_high_20 / is_at_low_20: continuous version
    df["is_at_high_20"] = np.clip((close_pos_20 - 0.9) / 0.1, 0, 1)  # ramps 0.9→1.0
    df["is_at_low_20"] = np.clip((0.1 - close_pos_20) / 0.1, 0, 1)    # ramps 0.0→0.1

    # breakout_volume_score: confirms real breakout with volume
    df["breakout_volume_score"] = df["vol_ratio"].values * breakout_strength

    # breakout_age: bars since first touch of 20-bar high/low
    at_high = c >= h_20 * 0.999
    at_low = c <= l_20 * 1.001
    breakout_age = np.zeros(n)
    age_counter = 999  # "old"
    for i in range(n):
        if at_high[i] or at_low[i]:
            if age_counter == 999:
                age_counter = 0  # fresh touch
            else:
                age_counter += 1
        else:
            age_counter = 999  # reset
        breakout_age[i] = min(age_counter, 50) / 50.0  # normalized 0-1
    df["breakout_age"] = breakout_age

    # range_breakout_pct: normalized breakout distance in ATR units
    df["range_breakout_pct"] = np.maximum(breakout_up_dist, breakout_down_dist) / np.maximum(atr_14, 1e-10)

    # ── G6: Trend alignment — NEW ─────────────────────────────────
    ema21 = _ema(c, 21)

    # dist_ema9_atr, dist_ema21_atr: key differentiators from analysis
    atr_safe = np.maximum(atr_14, 1e-10)
    df["dist_ema9_atr"] = (c - ema_9) / atr_safe
    df["dist_ema21_atr"] = (c - ema21) / atr_safe

    # ema_alignment: sign(EMA9 - EMA21) — from analysis +0.68 for winning breakouts
    df["ema_alignment"] = np.sign(ema_9 - ema21)

    # ema_trend_strength: (EMA9 - EMA50) / EMA50 * 100
    df["ema_trend_strength"] = (ema_9 - ema_50) / np.maximum(np.abs(ema_50), 1e-10) * 100

    # ema21_bounce_score: continuous — how close did price touch EMA21 recently?
    ema21_bounce = np.zeros(n)
    for i in range(1, n):
        for j in range(max(0, i-3), i):
            # Check if low touched EMA21 from below or high touched from above
            touch_dist = min(
                abs(l[j] - ema21[j]) / atr_safe[i],
                abs(h[j] - ema21[j]) / atr_safe[i],
            )
            if touch_dist < 0.5:
                ema21_bounce[i] = max(ema21_bounce[i], 1.0 - touch_dist / 0.5)
    df["ema21_bounce_score"] = ema21_bounce

    # ── G7: BTC lead/lag ─────────────────────────────────────────
    # Compute BTC features on btc_df first, then merge safely
    btc = btc_df[["timestamp", "close", "high", "low", "volume"]].copy()
    btc = btc.rename(columns={
        "close": "btc_close", "volume": "btc_volume",
        "high": "btc_high", "low": "btc_low",
    })
    btc["btc_ret_1m"] = btc["btc_close"].pct_change(1, fill_method=None)
    btc["btc_ret_5m"] = btc["btc_close"].pct_change(5, fill_method=None)

    btc_ret_std = btc["btc_ret_1m"].rolling(50, min_periods=5).std().replace(0, 1e-10)
    btc["btc_impulse_score"] = (btc["btc_ret_1m"].abs() / btc_ret_std).clip(0, 10)

    btc = btc[["timestamp", "btc_ret_1m", "btc_ret_5m", "btc_impulse_score"]]
    # Drop duplicate timestamps in BTC data to prevent row multiplication
    btc = btc.drop_duplicates(subset=["timestamp"], keep="first")
    df = df.merge(btc, on="timestamp", how="left")

    # Reconstruct from fresh arrays after merge — pandas CoW makes merged blocks read-only
    _merged = {col: df[col].values.copy() for col in df.columns}
    df = pd.DataFrame(_merged)

    # After merge, re-extract close array (length may differ if BTC had missing timestamps)
    c_post = _merged["close"].astype(np.float64)

    # Alt-BTC lag
    alt_ret_1 = pd.Series(c_post).pct_change(1, fill_method=None).values
    btc_ret_1 = df["btc_ret_1m"].fillna(0).values
    df["alt_btc_lag_1"] = np.clip(
        np.sign(btc_ret_1) * (np.abs(btc_ret_1) - np.abs(alt_ret_1)), -0.1, 0.1
    )

    # Alt-BTC spread 5m
    alt_ret_5 = pd.Series(c_post).pct_change(5, fill_method=None).values * 100
    btc_ret_5_pct = df["btc_ret_5m"].fillna(0).values * 100
    df["alt_btc_spread_5m"] = alt_ret_5 - btc_ret_5_pct

    # ── G8: Derivatives (from cache) ─────────────────────────────
    df["funding_rate_z"] = funding_rate_z
    df["oi_change_1h"] = oi_change_1h
    df["oi_change_4h"] = oi_change_4h

    df["funding_oi_divergence"] = (
        np.sign(df["funding_rate_z"]) * np.sign(df["oi_change_1h"]) *
        df["funding_rate_z"].abs() * df["oi_change_1h"].abs().clip(0, 20)
    ) / 100

    # ── G9: Multi-timeframe context ───────────────────────────────
    if "timestamp" in df.columns:
        sample = df["timestamp"].iloc[0] if len(df) else 0
        unit = "ms" if sample > 1e12 else "s"
        ts_dt = pd.to_datetime(df["timestamp"], unit=unit, utc=True)
        df["_ts_dt"] = ts_dt

        df["ret_1h"] = pd.Series(c_post).pct_change(12, fill_method=None).values

        ret_12 = pd.Series(c_post).pct_change(12, fill_method=None)
        ret_12_mean = ret_12.rolling(60, min_periods=12).mean()
        df["ema_trend_1h"] = np.sign(ret_12 - ret_12_mean).astype(float).values

        atr_12 = pd.Series(atr_14).rolling(12, min_periods=3).mean().values
        df["atr_pct_1h"] = atr_12 / np.maximum(c_post, 1e-10) * 100
        df["vol_regime_1h"] = np.digitize(
            np.nan_to_num(df["atr_pct_1h"].values, nan=0.0), [0.3, 0.8, 2.0]
        ).astype(float)

    # ── G10: Cross-sectional ───────────────────────────────────────
    sector = symbol_to_sector(symbol) if symbol else "large_cap"
    sector_idx = SECTOR_INDEX.get(sector, 1)
    df["sector_idx"] = sector_idx

    df["sector_momentum_5m"] = sector_avg_ret_5m
    df["token_rel_strength"] = df["ret_6"].values - sector_avg_ret_5m

    # ── G11: Temporal ──────────────────────────────────────────────
    if "_ts_dt" in df.columns:
        hour = df["_ts_dt"].dt.hour
        df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    else:
        df["hour_sin"] = 0.0
        df["hour_cos"] = 0.0

    # ── G12: Token identity ───────────────────────────────────────
    df["sector_idx_float"] = float(sector_idx)
    df["price_tier"] = np.vectorize(price_to_tier)(c_post)

    # ── G13: Pattern signals (discrete) ────────────────────────────
    # Based on CORRECTED pattern analysis:
    #   BREAKOUT_UP + LONG = +251 (THE EDGE)  → allow LONG
    #   BREAKOUT_DOWN + SHORT = -556 (THE HOLE) → BLOCK SHORT
    #   EMA_BOUNCE + SHORT = +27 (counter-trend) → allow SHORT
    #   LEVEL_TEST + SHORT = +33 (support bounce) → allow SHORT
    df["signal_breakout_up"] = (
        (df["close_position_20"].values > 0.95) &
        (df["vol_ratio"].values > 1.3) &
        (df["breakout_strength"].values > 0.01)
    ).astype(float)

    df["signal_breakout_down"] = (
        (df["close_position_20"].values < 0.05) &
        (df["vol_ratio"].values > 1.3) &
        (df["breakout_strength"].values > 0.01)
    ).astype(float)

    df["signal_ema_bounce"] = (
        df["ema21_bounce_score"].values > 0.5
    ).astype(float)

    df["signal_level_test"] = (
        (df["close_position_20"].values < 0.15) &
        (df["vol_ratio"].values < 1.5) &   # low vol = test, not breakdown
        (df["ema_alignment"].values < 0)    # in downtrend / resistance context
    ).astype(float)

    # ── Final cleanup ─────────────────────────────────────────────
    for col in FEATURE_NAMES:
        if col in df.columns:
            df[col] = df[col].replace([np.inf, -np.inf], np.nan)
        else:
            df[col] = np.nan

    return df


def latest_feature_row(
    ohlcv_df: pd.DataFrame,
    btc_df: pd.DataFrame,
    eth_df: pd.DataFrame,
    symbol: str = "",
    funding_rate_z: float = 0.0,
    oi_change_1h: float = 0.0,
    oi_change_4h: float = 0.0,
    sector_avg_ret_5m: float = 0.0,
) -> dict | None:
    """Return the most recent feature row as a dict, or None if insufficient data."""
    if len(ohlcv_df) < 60 or len(btc_df) < 60 or len(eth_df) < 60:
        return None

    feat_df = compute_features(
        ohlcv_df, btc_df, eth_df, symbol,
        funding_rate_z, oi_change_1h, oi_change_4h,
        sector_avg_ret_5m,
    )

    last = feat_df.iloc[-1]
    row = {}
    for f in FEATURE_NAMES:
        val = last.get(f, np.nan)
        row[f] = float(val) if pd.notna(val) else np.nan

    row["_timestamp"] = int(ohlcv_df["timestamp"].iloc[-1])
    row["_close"] = float(ohlcv_df["close"].iloc[-1])
    row["_atr_14_price"] = float(last.get("_atr_14_price", 0)) if pd.notna(last.get("_atr_14_price")) else 0.0

    return row
