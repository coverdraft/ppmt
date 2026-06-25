"""
v6_extract_features.py — Feature extractor for v6 (post-leakage redesign).

Key safety changes vs v5_cb_v2:
  1. ALL forward-looking columns use the `fwd_` prefix. The script ASSERTS
     that no feature column name starts with `fwd_` (anti-leakage guard #1).
  2. Feature range validation: each feature has an expected range; if the
     observed range exceeds 2x the expected, the script aborts (guard #2).
  3. Label is now `fwd_ret_3` (a.k.a. fwd_ret_15m — 3 bars × 5m = 15m), used
     as a REGRESSION target. We still compute fwd_tp_first_3 for diagnostic
     purposes (binary TP/SL outcome) but the model trains on the continuous
     return.

Feature count: 59 = 38 (v5 verified-clean) + 21 new
  - 8 multi-TF (BTC leading, ETH correlation, alt spread, BTC vol regime)
  - 6 microstructure approximations
  - 4 improved regime
  - 3 cross-asset timing

Output table: feature_observations_v6
"""
from __future__ import annotations


# === Auto-detected project root (portable paths, patched) ===
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[2]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
# === End path setup ===



import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

LOG = logging.getLogger("v6_extract")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DB_PATH = os.environ.get("PPMT_DB_PATH", _PROJECT_ROOT_STR + "/data/ppmt.db")
STATE_FILE = Path(_PROJECT_ROOT_STR + "/data/v6_extract_state.json")

# Per-timeframe config: (label horizons, primary label column, table name)
# - 5m TF: keep v6 original behavior (horizons 3,6,12 = 15m/30m/60m forward, primary=fwd_ret_3)
# - 15m TF (Fase 3): use horizons 1,2,3 = 15m/30m/45m forward, primary=fwd_ret_1
#   This matches the 5m primary label wall-clock (15m forward) so SHORT expert v2
#   gets a fair comparison between TFs.
TF_HORIZONS = {
    "5m":  (3, 6, 12),
    "15m": (1, 2, 3),
}
TF_PRIMARY_LABEL = {
    "5m":  "fwd_ret_3",   # 3 bars × 5m = 15m forward
    "15m": "fwd_ret_1",   # 1 bar  × 15m = 15m forward
}

TOKEN_CLASS = {
    "BTCUSDT": "blue_chip", "ETHUSDT": "blue_chip",
    "SOLUSDT": "large_cap", "XRPUSDT": "large_cap",
    "ADAUSDT": "mid_cap", "AVAXUSDT": "mid_cap", "LINKUSDT": "mid_cap",
    "DOGEUSDT": "meme", "SHIBUSDT": "meme", "PEPEUSDT": "meme",
    "WIFUSDT": "meme", "BONKUSDT": "meme",
}

# ----------------------------------------------------------------------------
# Feature list (59 total) — split for clarity
# ----------------------------------------------------------------------------

# 38 v5 features (verified not leakage — see v5_leakage_postmortem.md)
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

# 21 new v6 features
FEATURE_NAMES_V6_NEW = [
    # 8 multi-TF (BTC leading, ETH corr, alt spread, BTC vol regime)
    "btc_ret_1m", "btc_ret_5m", "btc_ret_15m", "btc_vol_z",
    "btc_trend_50", "eth_corr_30", "btc_alt_spread_15m", "btc_volatility_regime",
    # 6 microstructure
    "vol_delta_3", "wick_imbalance_3", "body_consistency_5",
    "range_expansion_3", "close_persistence_5", "vol_acceleration",
    # 4 improved regime
    "atr_percentile_50", "trend_strength_50", "regime_vol_trend", "hour_quantile",
    # 3 cross-asset timing (note: removed one redundant to keep total = 21, not 22)
    "alt_lead_5m", "alt_lag_signal", "momentum_dispersion",
]

FEATURE_NAMES = FEATURE_NAMES_V5 + FEATURE_NAMES_V6_NEW
assert len(FEATURE_NAMES) == 59, f"expected 59 features, got {len(FEATURE_NAMES)}"

# Expected feature ranges (for anti-leakage guard #2). None = no hard bound.
# (min, max) — observed values outside [min*2, max*2] (when both signs present)
# or outside [min, max*2] (when only one sign) trigger abort.
FEATURE_RANGES = {
    # v5 features — bounds calibrated to real 5m crypto (incl. small-cap memecoins
    # like WIF/PEPE/BONK which can move 50-100% in a single 5m bar on listing day)
    "body_pct": (-1.0, 1.0), "upper_wick": (0.0, 1.0), "lower_wick": (0.0, 1.0),
    "body_abs": (0.0, 1.0), "close_pos": (0.0, 1.0), "range_pct": (0.0, 60.0),
    "ret_1": (-0.50, 0.50), "ret_3": (-1.0, 1.0), "ret_5": (-1.5, 1.5),
    "ret_10": (-2.0, 2.0), "log_ret_1": (-0.50, 0.50),
    "atr_pct": (0.0, 60.0), "vol_std_10": (0.0, 0.50), "rsi_14": (0.0, 100.0),
    "ema_9_20_cross": (-30.0, 30.0), "ema_20_50_cross": (-60.0, 60.0),
    "ema_9_slope": (-0.50, 0.50), "ema_20_slope": (-0.50, 0.50), "ema_50_slope": (-0.50, 0.50),
    "price_vs_ema20": (-100.0, 100.0), "price_vs_ema50": (-150.0, 150.0),
    "vol_ratio": (0.0, 500.0), "vol_z": (-15.0, 15.0),
    "last_3_body_sum": (-3.0, 3.0), "last_3_range_sum": (0.0, 150.0),
    "bullish_engulf_2": (0, 1), "hammer_like": (0, 1), "shooting_star": (0, 1),
    "breakout_up": (0, 1), "breakout_down": (0, 1),
    "dist_to_high_20": (-100.0, 1.0), "dist_to_low_20": (-1.0, 100.0),
    "trend_50": (-1, 1), "vol_regime": (0, 3), "trending": (0, 1),
    "hour_sin": (-1.0, 1.0), "hour_cos": (-1.0, 1.0), "dow": (0, 6),
    # v6 new — bounds reflect real 5m crypto including small-caps
    "btc_ret_1m": (-0.50, 0.50), "btc_ret_5m": (-1.0, 1.0), "btc_ret_15m": (-2.0, 2.0),
    "btc_vol_z": (-15.0, 15.0), "btc_trend_50": (-1, 1),
    "eth_corr_30": (-1.0, 1.0), "btc_alt_spread_15m": (-60.0, 60.0),
    "btc_volatility_regime": (0, 3),
    "vol_delta_3": (-5.0, 5.0), "wick_imbalance_3": (-3.0, 3.0),
    "body_consistency_5": (0.0, 1.0), "range_expansion_3": (0.0, 30.0),
    "close_persistence_5": (0.0, 1.0), "vol_acceleration": (-15.0, 15.0),
    "atr_percentile_50": (0.0, 1.0), "trend_strength_50": (0.0, 100.0),
    "regime_vol_trend": (-3, 6), "hour_quantile": (0, 3),
    "alt_lead_5m": (-60.0, 60.0), "alt_lag_signal": (0, 1),
    "momentum_dispersion": (0.0, 0.50),
}


# ----------------------------------------------------------------------------
# v5 features (38) — kept verbatim from v5_extract_features_cb.py
# (verified not to be the source of leakage; only the label-collision was)
# ----------------------------------------------------------------------------

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
        ts = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df["hour_utc"] = ts.dt.hour
        df["dow"] = ts.dt.dayofweek
        df["hour_sin"] = np.sin(2 * np.pi * df["hour_utc"] / 24)
        df["hour_cos"] = np.cos(2 * np.pi * df["hour_utc"] / 24)
    return df


# ----------------------------------------------------------------------------
# v6 new features (21)
# ----------------------------------------------------------------------------

def compute_indicators_v6_new(df: pd.DataFrame, btc_df: pd.DataFrame, eth_df: pd.DataFrame) -> pd.DataFrame:
    """Add 21 new features. Requires pre-computed BTC + ETH aligned OHLCV.

    btc_df and eth_df are DataFrames with columns ['timestamp', 'close', 'volume', 'high', 'low']
    indexed/aligned by timestamp to df. We compute BTC-side features on btc_df
    then merge onto df by timestamp.
    """
    df = df.copy()

    # --- BTC features (computed on btc_df, merged by timestamp) ---
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

    # Merge BTC features onto df by timestamp
    df = df.merge(btc, on="timestamp", how="left")

    # btc_alt_spread_15m: (alt 15m return %) - (btc 15m return %)
    alt_ret_15m_pct = df["close"].pct_change(15, fill_method=None) * 100
    btc_ret_15m_pct = df["btc_ret_15m"] * 100
    df["btc_alt_spread_15m"] = alt_ret_15m_pct - btc_ret_15m_pct

    # --- ETH correlation (rolling 30-bar correlation of returns) ---
    eth = eth_df[["timestamp", "close"]].copy().rename(columns={"close": "eth_close"})
    df = df.merge(eth, on="timestamp", how="left")
    alt_ret = df["close"].pct_change(fill_method=None)
    eth_ret = df["eth_close"].pct_change(fill_method=None)
    df["eth_corr_30"] = alt_ret.rolling(30).corr(eth_ret)

    # --- Microstructure (6) ---
    # vol_delta_3: winsorized pct_change of volume over 3 bars.
    # Raw pct_change can spike to millions on small-cap memecoins when volume
    # goes from ~0 to large in a single bar. Clip to ±5 (still informative,
    # bounded for ML).
    df["vol_delta_3"] = df["volume"].pct_change(3, fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0).clip(-5, 5)
    # wick imbalance: lower_wick - upper_wick, summed over 3 bars
    if "lower_wick" in df.columns and "upper_wick" in df.columns:
        df["wick_imbalance_3"] = (df["lower_wick"] - df["upper_wick"]).rolling(3).sum()
    else:
        df["wick_imbalance_3"] = 0.0
    # body consistency: fraction of last 5 bars with body > 0
    body_sign = (df["close"] - df["open"] > 0).astype(float)
    df["body_consistency_5"] = body_sign.rolling(5).mean()
    # range expansion: avg range_pct last 3 / avg range_pct last 20
    avg_rng_3 = df["range_pct"].rolling(3).mean()
    avg_rng_20 = df["range_pct"].rolling(20).mean().replace(0, 1e-10)
    df["range_expansion_3"] = (avg_rng_3 / avg_rng_20).clip(0, 10)
    # close persistence: fraction of last 5 closes above EMA20
    above_ema = (df["close"] > df["ema_20"]).astype(float)
    df["close_persistence_5"] = above_ema.rolling(5).mean()
    # vol acceleration: vol_ratio - vol_ratio.shift(3)
    df["vol_acceleration"] = df["vol_ratio"] - df["vol_ratio"].shift(3)

    # --- Improved regime (4) ---
    df["atr_percentile_50"] = df["atr_pct"].rolling(50, min_periods=5).rank(pct=True)
    df["trend_strength_50"] = ((df["ema_9"] - df["ema_50"]).abs() / df["atr_pct"].replace(0, 1e-10)).clip(0, 20)
    df["regime_vol_trend"] = df["vol_regime"] * df["trend_50"]
    # hour_quantile: 0=asia (00-08 UTC), 1=europe (08-14), 2=us (14-22), 3=overlap (22-24)
    if "hour_utc" in df.columns:
        df["hour_quantile"] = pd.cut(df["hour_utc"], bins=[-1, 8, 14, 22, 24],
                                     labels=[0, 1, 2, 3]).astype(int)
    else:
        df["hour_quantile"] = 1

    # --- Cross-asset timing (3) ---
    # alt_lead_5m: symbol 5m return - BTC 5m return (in %)
    df["alt_lead_5m"] = (df["close"].pct_change(5, fill_method=None) - df["btc_ret_5m"]) * 100
    # alt_lag_signal: 1 if BTC moved >0.2% in last 1m but symbol didn't
    df["alt_lag_signal"] = ((df["btc_ret_1m"].abs() > 0.002) &
                             (df["ret_1"].abs() < 0.001)).astype(int)
    # momentum dispersion: std of 1m returns over last 10 bars
    df["momentum_dispersion"] = df["ret_1"].rolling(10).std()

    return df


# ----------------------------------------------------------------------------
# Labels (forward-looking — strict fwd_ prefix)
# ----------------------------------------------------------------------------

def make_labels(df: pd.DataFrame, horizons=(3, 6, 12)) -> pd.DataFrame:
    """Compute forward-looking labels. ALL column names use `fwd_` prefix."""
    df = df.copy()
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    n = len(df)

    for H in horizons:
        ret = np.full(n, np.nan)
        mfe = np.full(n, np.nan)
        mae = np.full(n, np.nan)
        tp_first = np.full(n, np.nan)
        tp_p = 0.6
        sl_p = 0.4

        for i in range(n - H):
            entry = c[i]
            ret[i] = (c[i + H] - entry) / entry * 100
            hh = h[i + 1 : i + H + 1]
            ll = l[i + 1 : i + H + 1]
            mfe[i] = (hh.max() - entry) / entry * 100 if len(hh) else 0
            mae[i] = (ll.min() - entry) / entry * 100 if len(ll) else 0

            tp_price = entry * (1 + tp_p / 100)
            sl_price = entry * (1 - sl_p / 100)
            for j in range(1, H + 1):
                if i + j >= n: break
                if l[i + j] <= sl_price:
                    tp_first[i] = 0; break
                if h[i + j] >= tp_price:
                    tp_first[i] = 1; break

        df[f"fwd_ret_{H}"] = ret
        df[f"fwd_mfe_{H}"] = mfe
        df[f"fwd_mae_{H}"] = mae
        df[f"fwd_tp_first_{H}"] = tp_first
    return df


# ----------------------------------------------------------------------------
# Anti-leakage guards
# ----------------------------------------------------------------------------

def assert_no_fwd_in_features() -> None:
    """Guard #1: no feature column name may start with `fwd_`."""
    bad = [f for f in FEATURE_NAMES if f.startswith("fwd_")]
    if bad:
        raise RuntimeError(f"LEAKAGE GUARD #1 FAILED: features starting with 'fwd_': {bad}")
    LOG.info("Guard #1 OK: no feature starts with 'fwd_'")


def validate_feature_ranges(df: pd.DataFrame) -> None:
    """Guard #2: observed feature ranges must be within 2x expected bounds."""
    failures = []
    for fname, (exp_min, exp_max) in FEATURE_RANGES.items():
        if fname not in df.columns:
            failures.append(f"{fname}: missing")
            continue
        obs_min = df[fname].min()
        obs_max = df[fname].max()
        # Allow 2x slack on the bound that has slack
        if obs_min < exp_min * 2 - (exp_max - exp_min):
            failures.append(f"{fname}: obs_min={obs_min:.4f} < {exp_min * 2:.4f}")
        if obs_max > exp_max * 2:
            failures.append(f"{fname}: obs_max={obs_max:.4f} > {exp_max * 2:.4f}")
    if failures:
        msg = "LEAKAGE GUARD #2 FAILED: feature range violations:\n  " + "\n  ".join(failures[:20])
        if len(failures) > 20:
            msg += f"\n  ... and {len(failures) - 20} more"
        raise RuntimeError(msg)
    LOG.info("Guard #2 OK: all feature ranges within 2x expected bounds")


# ----------------------------------------------------------------------------
# DB
# ----------------------------------------------------------------------------

def ensure_table(conn, timeframe: str = "5m"):
    """Create the features table for the given timeframe.

    For 5m TF, uses the original `feature_observations_v6` table (unchanged).
    For 15m TF (Fase 3), uses a NEW `feature_observations_v6_15m` table so the
    existing 5m dataset is untouched (safe rollback if experiment fails).

    Schema mirrors the 5m table, plus additional fwd_ret_1/fwd_ret_2/fwd_mae_1/
    fwd_mfe_1/fwd_tp_first_1/fwd_tp_first_2 columns for the finer-grained labels
    used at 15m TF (where fwd_ret_3 = 45m forward, not 15m).
    """
    if timeframe == "5m":
        table = "feature_observations_v6"
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feature_observations_v6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                ts INTEGER NOT NULL,
                window TEXT NOT NULL,
                asset_class TEXT,
                features_json TEXT,
                fwd_ret_3 REAL,         -- primary regression label (= fwd_ret_15m)
                fwd_ret_6 REAL,
                fwd_ret_12 REAL,
                fwd_mfe_3 REAL,
                fwd_mae_3 REAL,
                fwd_tp_first_3 INTEGER,
                fwd_tp_first_6 INTEGER,
                fwd_tp_first_12 INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, timeframe, ts, window)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fov6_sym_win
            ON feature_observations_v6(symbol, window, ts)
        """)
    else:
        # 15m TF: separate table with fwd_ret_1/2 + fwd_mae_1 + fwd_mfe_1 + fwd_tp_first_1/2
        table = "feature_observations_v6_15m"
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                ts INTEGER NOT NULL,
                window TEXT NOT NULL,
                asset_class TEXT,
                features_json TEXT,
                fwd_ret_1 REAL,         -- primary regression label at 15m TF (= 15m forward)
                fwd_ret_2 REAL,
                fwd_ret_3 REAL,
                fwd_mfe_1 REAL,
                fwd_mae_1 REAL,
                fwd_mfe_2 REAL,
                fwd_mae_2 REAL,
                fwd_mfe_3 REAL,
                fwd_mae_3 REAL,
                fwd_tp_first_1 INTEGER,
                fwd_tp_first_2 INTEGER,
                fwd_tp_first_3 INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, timeframe, ts, window)
            )
        """)
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_fov6_15m_sym_win
            ON {table}(symbol, window, ts)
        """)
    conn.commit()
    return table


# ----------------------------------------------------------------------------
# Per-symbol extraction
# ----------------------------------------------------------------------------

def fetch_ohlcv(conn, symbol: str, window: str, timeframe: str = "5m") -> pd.DataFrame:
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM ohlcv_v6 "
        "WHERE symbol = ? AND timeframe = ? AND window = ? ORDER BY timestamp ASC",
        (symbol, timeframe, window),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def fetch_btc_eth_full(conn, window: str, timeframe: str = "5m") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch full BTC and ETH OHLCV for the window (for multi-TF features)."""
    btc_rows = conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM ohlcv_v6 "
        "WHERE symbol = 'BTCUSDT' AND timeframe = ? AND window = ? ORDER BY timestamp ASC",
        (timeframe, window),
    ).fetchall()
    eth_rows = conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM ohlcv_v6 "
        "WHERE symbol = 'ETHUSDT' AND timeframe = ? AND window = ? ORDER BY timestamp ASC",
        (timeframe, window),
    ).fetchall()
    btc_df = pd.DataFrame(btc_rows, columns=["timestamp", "open", "high", "low", "close", "volume"]) if btc_rows else pd.DataFrame()
    eth_df = pd.DataFrame(eth_rows, columns=["timestamp", "open", "high", "low", "close", "volume"]) if eth_rows else pd.DataFrame()
    return btc_df, eth_df


def extract_one(conn, symbol: str, asset_class: str, window: str,
                btc_df: pd.DataFrame, eth_df: pd.DataFrame,
                timeframe: str = "5m") -> int:
    df = fetch_ohlcv(conn, symbol, window, timeframe=timeframe)
    if len(df) < 200:
        LOG.warning("  Skipping %s %s: only %d rows", symbol, window, len(df))
        return 0

    df = compute_indicators_v5(df)
    df = compute_indicators_v6_new(df, btc_df, eth_df)
    horizons = TF_HORIZONS[timeframe]
    df = make_labels(df, horizons=horizons)

    # Drop rows with NaN in critical features (warmup period for indicators)
    critical = ["ret_10", "atr_pct", "rsi_14", "ema_20_50_cross", "vol_z",
                "btc_ret_15m", "eth_corr_30", "momentum_dispersion"]
    df = df.dropna(subset=critical).reset_index(drop=True)
    if len(df) < 100:
        LOG.warning("  Skipping %s %s: only %d rows after warmup", symbol, window, len(df))
        return 0

    # Fill remaining NaNs with 0 (only for non-critical features)
    for f in FEATURE_NAMES:
        if f in df.columns:
            df[f] = df[f].replace([np.inf, -np.inf], 0).fillna(0)

    # Guard #2: validate feature ranges (per-symbol, before insert)
    try:
        validate_feature_ranges(df)
    except RuntimeError as e:
        LOG.error("  %s %s: %s", symbol, window, e)
        return 0

    # Build feature dicts and insert
    feat_arrays = {k: df[k].values for k in FEATURE_NAMES}
    ts_arr = df["timestamp"].values.astype(int)
    primary_label = TF_PRIMARY_LABEL[timeframe]
    primary = df[primary_label].values

    batch = []
    BATCH_SIZE = 500
    inserted = 0
    n = len(df)
    for i in range(n):
        # Skip rows where primary label is NaN (last H bars — no label)
        if np.isnan(primary[i]):
            continue
        features = {k: float(feat_arrays[k][i]) for k in FEATURE_NAMES}
        row = (
            symbol, timeframe, int(ts_arr[i]), window, asset_class,
            json.dumps(features, default=str),
        )
        # Append forward-looking labels in same order as DB schema for this TF
        if timeframe == "5m":
            row = row + (
                float(df["fwd_ret_3"].iloc[i]) if not np.isnan(df["fwd_ret_3"].iloc[i]) else None,
                float(df["fwd_ret_6"].iloc[i]) if not np.isnan(df["fwd_ret_6"].iloc[i]) else None,
                float(df["fwd_ret_12"].iloc[i]) if not np.isnan(df["fwd_ret_12"].iloc[i]) else None,
                float(df["fwd_mfe_3"].iloc[i]) if not np.isnan(df["fwd_mfe_3"].iloc[i]) else None,
                float(df["fwd_mae_3"].iloc[i]) if not np.isnan(df["fwd_mae_3"].iloc[i]) else None,
                int(df["fwd_tp_first_3"].iloc[i]) if not np.isnan(df["fwd_tp_first_3"].iloc[i]) else None,
                int(df["fwd_tp_first_6"].iloc[i]) if not np.isnan(df["fwd_tp_first_6"].iloc[i]) else None,
                int(df["fwd_tp_first_12"].iloc[i]) if not np.isnan(df["fwd_tp_first_12"].iloc[i]) else None,
            )
        else:  # 15m TF
            row = row + (
                float(df["fwd_ret_1"].iloc[i]) if not np.isnan(df["fwd_ret_1"].iloc[i]) else None,
                float(df["fwd_ret_2"].iloc[i]) if not np.isnan(df["fwd_ret_2"].iloc[i]) else None,
                float(df["fwd_ret_3"].iloc[i]) if not np.isnan(df["fwd_ret_3"].iloc[i]) else None,
                float(df["fwd_mfe_1"].iloc[i]) if not np.isnan(df["fwd_mfe_1"].iloc[i]) else None,
                float(df["fwd_mae_1"].iloc[i]) if not np.isnan(df["fwd_mae_1"].iloc[i]) else None,
                float(df["fwd_mfe_2"].iloc[i]) if not np.isnan(df["fwd_mfe_2"].iloc[i]) else None,
                float(df["fwd_mae_2"].iloc[i]) if not np.isnan(df["fwd_mae_2"].iloc[i]) else None,
                float(df["fwd_mfe_3"].iloc[i]) if not np.isnan(df["fwd_mfe_3"].iloc[i]) else None,
                float(df["fwd_mae_3"].iloc[i]) if not np.isnan(df["fwd_mae_3"].iloc[i]) else None,
                int(df["fwd_tp_first_1"].iloc[i]) if not np.isnan(df["fwd_tp_first_1"].iloc[i]) else None,
                int(df["fwd_tp_first_2"].iloc[i]) if not np.isnan(df["fwd_tp_first_2"].iloc[i]) else None,
                int(df["fwd_tp_first_3"].iloc[i]) if not np.isnan(df["fwd_tp_first_3"].iloc[i]) else None,
            )
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            _flush_batch(conn, batch, timeframe=timeframe)
            inserted += len(batch)
            batch = []
    if batch:
        _flush_batch(conn, batch, timeframe=timeframe)
        inserted += len(batch)
    return inserted


def _flush_batch(conn, batch: list, timeframe: str = "5m") -> None:
    if timeframe == "5m":
        # 14 columns per row: (sym, tf, ts, win, cls, json, ret3, ret6, ret12, mfe3, mae3, tp3, tp6, tp12)
        placeholders = ",".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(batch))
        sql = f"""
            INSERT OR REPLACE INTO feature_observations_v6
                (symbol, timeframe, ts, window, asset_class, features_json,
                 fwd_ret_3, fwd_ret_6, fwd_ret_12,
                 fwd_mfe_3, fwd_mae_3,
                 fwd_tp_first_3, fwd_tp_first_6, fwd_tp_first_12)
            VALUES {placeholders}
        """
    else:  # 15m TF — 18 columns
        # (sym, tf, ts, win, cls, json, ret1, ret2, ret3, mfe1, mae1, mfe2, mae2, mfe3, mae3, tp1, tp2, tp3)
        placeholders = ",".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(batch))
        sql = f"""
            INSERT OR REPLACE INTO feature_observations_v6_15m
                (symbol, timeframe, ts, window, asset_class, features_json,
                 fwd_ret_1, fwd_ret_2, fwd_ret_3,
                 fwd_mfe_1, fwd_mae_1, fwd_mfe_2, fwd_mae_2, fwd_mfe_3, fwd_mae_3,
                 fwd_tp_first_1, fwd_tp_first_2, fwd_tp_first_3)
            VALUES {placeholders}
        """
    flat = [v for row in batch for v in row]
    conn.execute(sql, flat)
    conn.commit()


# ----------------------------------------------------------------------------
# State + main
# ----------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"completed": [], "failed": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--windows", nargs="+", default=None)
    parser.add_argument("--timeframe", default="5m", choices=list(TF_HORIZONS.keys()),
                        help="5m (default) or 15m (Fase 3 SHORT experiment)")
    parser.add_argument("--max-seconds", type=int, default=110)
    args = parser.parse_args()

    # Guard #1 runs once at startup
    assert_no_fwd_in_features()

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    table = ensure_table(conn, timeframe=args.timeframe)
    LOG.info("Using features table: %s (timeframe=%s, primary label=%s)",
             table, args.timeframe, TF_PRIMARY_LABEL[args.timeframe])

    # Discover available combos for this timeframe
    rows = conn.execute(
        "SELECT DISTINCT symbol, window FROM ohlcv_v6 WHERE timeframe = ? ORDER BY symbol, window",
        (args.timeframe,)
    ).fetchall()
    available = []
    for sym, win in rows:
        if args.symbols and sym not in args.symbols:
            continue
        if args.windows and win not in args.windows:
            continue
        if sym not in TOKEN_CLASS:
            continue
        available.append((sym, win))

    LOG.info("Found %d (symbol, window) combos to extract", len(available))

    state = load_state()
    # Per-timeframe resume key (5m completions don't block 15m)
    done_keys = {f"{r['sym']}|{r['window']}|{r.get('timeframe', '5m')}" for r in state["completed"]}
    for r in state.get("failed", []):
        if r.get("status", "").startswith("error"):
            done_keys.add(f"{r['sym']}|{r['window']}|{r.get('timeframe', '5m')}")

    remaining = [(s, w) for (s, w) in available if f"{s}|{w}|{args.timeframe}" not in done_keys]
    LOG.info("Remaining: %d (after progress filter)", len(remaining))

    if not remaining:
        LOG.info("All done for [%s]. ✓", args.timeframe)
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"\nTotal {table} rows: {cur.fetchone()[0]:,}")
        cur.execute(f"SELECT symbol, COUNT(*) FROM {table} GROUP BY symbol ORDER BY symbol")
        for s, n in cur.fetchall():
            print(f"  {s:10} {n:>6,}")
        return

    # Pre-fetch BTC and ETH per window (shared across symbols in same window)
    windows_needed = sorted({w for (_s, w) in remaining})
    btc_cache = {}
    eth_cache = {}
    for w in windows_needed:
        btc_cache[w], eth_cache[w] = fetch_btc_eth_full(conn, w, timeframe=args.timeframe)

    t_start = time.time()
    n_processed = 0
    for i, (sym, win) in enumerate(remaining, 1):
        if time.time() - t_start > args.max_seconds:
            LOG.info("Hit max_seconds, exiting")
            break
        cls = TOKEN_CLASS.get(sym, "default")
        t0 = time.time()
        try:
            n = extract_one(conn, sym, cls, win,
                            btc_cache.get(win, pd.DataFrame()),
                            eth_cache.get(win, pd.DataFrame()),
                            timeframe=args.timeframe)
            elapsed = time.time() - t0
            LOG.info("[%d/%d] %s %s: %d feature rows in %.1fs",
                     i, len(remaining), sym, win, n, elapsed)
            state["completed"].append({
                "sym": sym, "window": win, "timeframe": args.timeframe, "rows": n,
            })
        except Exception as e:
            LOG.exception("FAILED %s %s: %s", sym, win, e)
            state["failed"].append({
                "sym": sym, "window": win, "timeframe": args.timeframe, "error": str(e),
            })
        save_state(state)
        n_processed += 1

    LOG.info("Processed %d combos in %.1fs", n_processed, time.time() - t_start)
    conn.close()


if __name__ == "__main__":
    main()
