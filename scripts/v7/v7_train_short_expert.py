"""
v7_train_short_expert.py — F5b: Train LightGBM SHORT expert on v7 features.

WHAT THIS DOES
--------------
Per PPMT_v7_MASTER_PLAN.md §5 (LightGBM dual experts):
  - Filter training data to fwd_ret_3 < 0 (SHORT-only observations = drops)
  - Train LightGBM regression on the magnitude of negative returns (always < 0)
  - At inference, |pred_short| > thr_short (default 0.40%) → SHORT signal candidate
  - Sample weights: 2x for big drops (bottom 25% of negative returns) +
                    2x for BEAR_2022 (drops in bear market = rare-but-critical)
                    Compound: BEAR_2022 big drop = 4x weight

FEATURES (71 total = 59 v6 + 12 F4):
  - 59 v6 base features (from feature_observations_v6.features_json)
  - 12 F4 features (from feature_observations_v7_extras):
    funding_rate, funding_rate_z, oi_change_1h, oi_change_4h,
    sector_blue_chip, sector_large_cap, sector_old_meme, sector_new_meme,
    sector_idx, day_of_week_sin, day_of_week_cos, day_of_week

ANTI-LEAKAGE GUARDS (master plan §11.2):
  #3: top_feat_gain < 30% of total gain (no single feature dominates)
  #4: train_corr(pred, y) < 0.85 (model not overfit)
  #5: test_corr std across walk-forward windows < 0.05 (model stable over time)

SHORT-SPECIFIC SAFETY (master plan §11.4):
  - Higher default threshold: thr_short = 0.40% (vs thr_long = 0.30%)
  - We evaluate at multiple thresholds: 0.20, 0.30, 0.40, 0.50, 0.75, 1.00
  - PLUS a "gated" evaluation: filter to funding_rate_z > 1.5 (longs overleveraged).
    This gate is enforced at inference time by the decision layer (F7). Here we
    pre-compute gated metrics so we know SHORT WR with the gate applied.

WALK-FORWARD SPLITS (5 windows, mirrors F5a):
  Test months: 2025-04, 2025-05, 2025-06, 2025-09, 2025-10
  Train = all data BEFORE test month (across all symbols/windows)
  Test  = data DURING test month

OUTPUTS:
  - data/v7_models/short_expert/v7_short_expert_{window}.txt  (LGBM model file)
  - data/v7_models/short_expert/v7_short_expert_summary.json

USAGE:
    python /home/z/my-project/scripts/v7/v7_train_short_expert.py
    python /home/z/my-project/scripts/v7/v7_train_short_expert.py --windows 2025-06,2025-10
    python /home/z/my-project/scripts/v7/v7_train_short_expert.py --max-seconds 240
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_squared_error, mean_absolute_error

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

DB_PATH = os.environ.get("PPMT_DB_PATH", "/home/z/my-project/data/ppmt.db")
OUTPUT_DIR = Path("/home/z/my-project/data/v7_models/short_expert")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v7_short_expert")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# 59 v6 features (must match v6_extract_features.py)
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
assert len(FEATURE_NAMES_V6) == 59

# 12 F4 features (must match v7_extract_features_extras.py)
FEATURE_NAMES_F4 = [
    "funding_rate", "funding_rate_z",
    "oi_change_1h", "oi_change_4h",
    "sector_blue_chip", "sector_large_cap", "sector_old_meme", "sector_new_meme",
    "sector_idx",
    "day_of_week_sin", "day_of_week_cos", "day_of_week",
]
assert len(FEATURE_NAMES_F4) == 12

# Final feature list (71 total)
FEATURE_NAMES = FEATURE_NAMES_V6 + FEATURE_NAMES_F4
assert len(FEATURE_NAMES) == 71

LABEL = "fwd_ret_3"  # 15-minute forward return in % (3 bars × 5m)

# Sample weights
DROP_WEIGHT = 2.0       # bottom 25% of negative returns (largest drops)
BEAR_WEIGHT = 2.0       # BEAR_2022 window
DROP_PERCENTILE = 25    # bottom 25% (= most negative)

# Walk-forward test months (same as F5a — apples-to-apples comparison)
WF_WINDOWS = ["2025-04", "2025-05", "2025-06", "2025-09", "2025-10"]

# Frozen LGBM hyperparams (master plan §5.3)
LGB_PARAMS = {
    "objective": "regression",
    "metric": ["rmse", "mae"],
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.85,
    "bagging_fraction": 0.85,
    "bagging_freq": 5,
    "min_data_in_leaf": 200,
    "lambda_l2": 1.0,
    "verbosity": -1,
    "seed": 42,
}
N_BOOST_ROUND = 200  # cap to limit memory; best_iter usually hits early stopping
EARLY_STOPPING = 30

# SHORT-specific safety (master plan §11.4)
THR_SHORT_DEFAULT = 0.40  # more selective than LONG (0.30)
FUNDING_Z_GATE = 1.5      # SHORT only if funding_rate_z > 1.5 (longs overleveraged)

# Round-trip cost: 0.14% (taker fee + slippage on both sides)
# Same as LONG expert (F5a) — keeps cost model consistent for fair comparison
ROUND_TRIP_COST_PCT = 0.14
DOLLARS_PER_TRADE = 700.0  # notional position size for PnL reporting


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------

PARQUET_PATH = OUTPUT_DIR / "short_features.parquet"


def load_dataset() -> pd.DataFrame:
    """Load SHORT-filtered features from materialized parquet file.

    The parquet file is created by v7_materialize_short_features.py (one-time cost).
    It contains ~684K rows filtered to fwd_ret_3 < 0, with all 71 features as
    float32 columns. Loading takes ~1s vs ~5min from JSON.

    Falls back to in-DB loading if parquet is missing (with a warning).
    """
    if not PARQUET_PATH.exists():
        LOG.warning("Parquet file %s not found. Falling back to slow in-DB load.",
                    PARQUET_PATH)
        LOG.warning("Run: python scripts/v7/v7_materialize_short_features.py")
        return load_dataset_from_db()

    LOG.info("Loading parquet: %s", PARQUET_PATH)
    t0 = time.time()
    df = pd.read_parquet(PARQUET_PATH)
    LOG.info("  loaded %d rows × %d cols in %.1fs (%.1f MB)",
             len(df), len(df.columns), time.time() - t0,
             PARQUET_PATH.stat().st_size / 1e6)
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    LOG.info("  label stats: mean=%.4f%% std=%.4f%% n=%d",
             float(df[LABEL].mean()), float(df[LABEL].std()), len(df))
    LOG.info("  windows: %s", df["window"].value_counts().to_dict())
    return df


def load_dataset_from_db() -> pd.DataFrame:
    """Fallback: load directly from DB (slow, memory-heavy). Use only if parquet missing."""
    LOG.warning("Using SLOW in-DB loader. This may take ~5min and use ~5GB RAM.")
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)

    v6_feat_cols = ", ".join(
        [f"json_extract(features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES_V6]
    )
    f4_cols_sql = ", ".join(FEATURE_NAMES_F4)
    sql = f"""
        SELECT v6.symbol, v6.ts, v6.window, v6.{LABEL},
               {v6_feat_cols},
               e.{f4_cols_sql.replace(', ', ', e.')}
        FROM feature_observations_v6 AS v6
        INNER JOIN feature_observations_v7_extras AS e
          ON v6.symbol = e.symbol AND v6.ts = e.ts
        WHERE v6.{LABEL} < 0
    """
    df = pd.read_sql_query(sql, conn)
    conn.close()
    for f in FEATURE_NAMES + [LABEL]:
        df[f] = pd.to_numeric(df[f], errors="coerce").replace([np.inf, -np.inf], 0).fillna(0).astype(np.float32)
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    LOG.info("  loaded %d SHORT rows in %.1fs", len(df), time.time() - t0)
    return df


def filter_short(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to fwd_ret_3 < 0 (SHORT-only observations)."""
    n_before = len(df)
    out = df[df[LABEL] < 0].copy()
    LOG.info(
        "SHORT filter: %d -> %d rows (%.1f%% kept). Mean label: %.4f%% -> %.4f%%",
        n_before, len(out), 100 * len(out) / max(n_before, 1),
        df[LABEL].mean(), out[LABEL].mean(),
    )
    return out


# ----------------------------------------------------------------------------
# Walk-forward splits + training
# ----------------------------------------------------------------------------

def walk_forward_splits(df: pd.DataFrame) -> List[Tuple[str, pd.DataFrame, pd.DataFrame]]:
    """5 monthly walk-forward windows. Train = all data BEFORE test month."""
    splits = []
    for window_str in WF_WINDOWS:
        yr, mo = window_str.split("-")
        yr, mo = int(yr), int(mo)
        test_mask = (df["ts"].dt.year == yr) & (df["ts"].dt.month == mo)
        test_df = df[test_mask].copy()
        cutoff = pd.Timestamp(year=yr, month=mo, day=1, tz="UTC")
        train_df = df[df["ts"] < cutoff].copy()
        if len(train_df) > 1000 and len(test_df) > 500:
            splits.append((window_str, train_df, test_df))
            # Use np.percentile to identify bottom-25% drops (most negative)
            drop_threshold = np.percentile(train_df[LABEL].values, DROP_PERCENTILE)
            LOG.info(
                "split %s: train=%d  test=%d  (train drops<%.4f%%=%d, bear=%d)",
                window_str, len(train_df), len(test_df),
                drop_threshold,
                (train_df[LABEL] <= drop_threshold).sum(),
                (train_df["window"] == "BEAR_2022").sum(),
            )
        else:
            LOG.warning("split %s: SKIPPED (train=%d test=%d)", window_str, len(train_df), len(test_df))
    return splits


def compute_sample_weights(y_train: np.ndarray, window_train: np.ndarray) -> np.ndarray:
    """SHORT expert: 2x for big drops (bottom 25%) + 2x for BEAR_2022.

    Bottom 25% = 25th percentile of y_train (most negative values).
    Rows with y_train <= percentile get DROP_WEIGHT (2x).
    """
    w = np.ones(len(y_train), dtype=np.float32)
    drop_threshold = np.percentile(y_train, DROP_PERCENTILE)
    w[y_train <= drop_threshold] *= DROP_WEIGHT
    w[window_train == "BEAR_2022"] *= BEAR_WEIGHT
    return w


def _short_metrics(y_test: np.ndarray, pred_test: np.ndarray,
                   funding_z_test: Optional[np.ndarray],
                   thr: float,
                   apply_funding_gate: bool = False) -> Dict:
    """Compute SHORT signal metrics at one threshold.

    SHORT signal: pred_test < -thr  (model predicts a drop of magnitude > thr%)
    SHORT PnL: -actuals - cost   (we profit when actual is negative)
      - If actual = -1.0% and we shorted: profit = +1.0% - 0.14% = +0.86%
      - If actual = +0.5% and we shorted: profit = -0.5% - 0.14% = -0.64%

    If apply_funding_gate=True: only count signals where funding_z > FUNDING_Z_GATE.
    This simulates the master plan §11.4 inference-time gate.

    If apply_funding_gate=True but funding_z_test is None: returns 0 signals
    (safer to skip than to emit un-gated signals marked as gated).
    """
    short_mask = pred_test < -thr
    if apply_funding_gate:
        if funding_z_test is None:
            # Cannot apply gate without funding_z data → emit no signals
            return {
                "n_signals": 0, "wr": 0.0, "pf": 0.0,
                "avg_pnl_pct": 0.0, "tot_dollars": 0.0,
            }
        short_mask = short_mask & (funding_z_test > FUNDING_Z_GATE)
    n_short = int(short_mask.sum())
    if n_short > 0:
        actuals_short = y_test[short_mask]
        # SHORT PnL = -actuals - cost (round-trip)
        net_short = -actuals_short - ROUND_TRIP_COST_PCT
        wins = float(net_short[net_short > 0].sum())
        losses = float(-net_short[net_short < 0].sum())
        pf = wins / losses if losses > 0 else 99.0
        wr = float((net_short > 0).mean())
        avg_pnl = float(net_short.mean())
        tot_dollars = float(net_short.sum() / 100 * DOLLARS_PER_TRADE)
    else:
        pf = wr = avg_pnl = tot_dollars = 0.0
    return {
        "n_signals": n_short,
        "wr": float(wr),
        "pf": float(pf),
        "avg_pnl_pct": float(avg_pnl),
        "tot_dollars": float(tot_dollars),
    }


def train_one_window(name: str, train_df: pd.DataFrame, test_df: pd.DataFrame) -> Dict:
    """Train LightGBM-SHORT expert on one walk-forward window."""
    t0 = time.time()

    X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_df[LABEL].values.astype(np.float32)
    w_train = compute_sample_weights(y_train, train_df["window"].values)
    X_test = test_df[FEATURE_NAMES].values.astype(np.float32)
    y_test = test_df[LABEL].values.astype(np.float32)
    funding_z_test = test_df["funding_rate_z"].values.astype(np.float32) \
        if "funding_rate_z" in test_df.columns else None

    # Validation split (15% of train, with same weighting)
    rng = np.random.default_rng(seed=42)
    n_val = max(int(len(X_train) * 0.15), 100)  # floor 100 for small datasets
    n_val = min(n_val, len(X_train) - 1)  # ensure n_val < train size
    val_idx = rng.choice(len(X_train), size=n_val, replace=False)
    val_mask = np.zeros(len(X_train), dtype=bool)
    val_mask[val_idx] = True
    X_val, y_val, w_val = X_train[val_mask], y_train[val_mask], w_train[val_mask]
    X_tr,  y_tr,  w_tr  = X_train[~val_mask], y_train[~val_mask], w_train[~val_mask]

    drop_thr_train = float(np.percentile(y_tr, DROP_PERCENTILE))
    LOG.info("[%s] train=%d val=%d test=%d  (effective_sample=%.0f, drop_thr=%.4f%%)",
             name, len(X_tr), len(X_val), len(X_test), w_tr.sum(), drop_thr_train)

    dtrain = lgb.Dataset(X_tr, label=y_tr, weight=w_tr, feature_name=FEATURE_NAMES)
    dval   = lgb.Dataset(X_val, label=y_val, weight=w_val, feature_name=FEATURE_NAMES, reference=dtrain)

    model = lgb.train(
        LGB_PARAMS,
        dtrain,
        num_boost_round=N_BOOST_ROUND,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False), lgb.log_evaluation(0)],
    )
    best_iter = int(model.best_iteration) if model.best_iteration else N_BOOST_ROUND

    # Predictions
    pred_train = model.predict(X_tr, num_iteration=best_iter)
    pred_val   = model.predict(X_val, num_iteration=best_iter)
    pred_test  = model.predict(X_test, num_iteration=best_iter)

    # Metrics
    rmse_test  = float(np.sqrt(mean_squared_error(y_test, pred_test)))
    mae_test   = float(mean_absolute_error(y_test, pred_test))
    rmse_train = float(np.sqrt(mean_squared_error(y_tr, pred_train)))
    try: corr_test  = float(np.corrcoef(y_test,  pred_test)[0, 1])
    except Exception: corr_test = 0.0
    try: corr_train = float(np.corrcoef(y_tr, pred_train)[0, 1])
    except Exception: corr_train = 0.0
    try: corr_val   = float(np.corrcoef(y_val, pred_val)[0, 1])
    except Exception: corr_val = 0.0

    # Direction accuracy (sign match) — note: all y < 0 here (SHORT filter)
    # so "direction correct" means pred < 0
    dir_acc_test  = float((pred_test < 0).mean())
    dir_acc_train = float((pred_train < 0).mean())

    # SHORT signal performance: pred < -thr_short
    # Test multiple thresholds to find best operating point
    thresholds = [0.20, 0.30, 0.40, 0.50, 0.75, 1.00]
    results_by_thr = {}
    gated_results_by_thr = {}
    for thr in thresholds:
        results_by_thr[f"thr_{thr:.2f}"] = _short_metrics(
            y_test, pred_test, funding_z_test, thr, apply_funding_gate=False
        )
        gated_results_by_thr[f"thr_{thr:.2f}"] = _short_metrics(
            y_test, pred_test, funding_z_test, thr, apply_funding_gate=True
        )

    # Feature importance
    importance = model.feature_importance(importance_type="gain")
    feat_imp = sorted(zip(FEATURE_NAMES, importance), key=lambda x: -x[1])
    total = max(float(importance.sum()), 1.0)
    top_feat_pct = feat_imp[0][1] / total

    # Save model
    model_path = OUTPUT_DIR / f"v7_short_expert_{name}.txt"
    model.save_model(str(model_path))

    result = {
        "window": name,
        "model_type": "v7_short_expert",
        "label": LABEL,
        "n_features": len(FEATURE_NAMES),
        "n_train": len(X_tr),
        "n_val": len(X_val),
        "n_test": len(X_test),
        "effective_sample_size": float(w_tr.sum()),
        "n_drops_train": int((y_tr <= np.percentile(y_tr, DROP_PERCENTILE)).sum()),
        "n_bear_train": int((train_df["window"].values == "BEAR_2022").sum()),
        "best_iteration": best_iter,
        "rmse_train": rmse_train,
        "rmse_test": rmse_test,
        "mae_test": mae_test,
        "corr_train": corr_train,
        "corr_val": corr_val,
        "corr_test": corr_test,
        "dir_acc_test": dir_acc_test,
        "dir_acc_train": dir_acc_train,
        "short_thresholds": results_by_thr,
        "short_thresholds_gated_fundingz": gated_results_by_thr,
        "funding_z_gate": FUNDING_Z_GATE,
        "top_feat_pct": float(top_feat_pct),
        "top_feat_name": feat_imp[0][0],
        "top_20_features": [
            {"name": n, "gain": float(g), "pct": float(g / total * 100)}
            for n, g in feat_imp[:20]
        ],
        "guards": {
            "top_feat_under_30pct": bool(top_feat_pct < 0.30),
            "train_corr_under_085": bool(corr_train < 0.85),
        },
        "model_path": str(model_path),
        "train_time_seconds": float(time.time() - t0),
    }
    LOG.info(
        "[%s] done in %.1fs  best_iter=%d  rmse_test=%.4f  corr_test=%+.4f  "
        "thr_0.40: n=%d wr=%.3f pf=%.2f tot=$%+.0f  | gated: n=%d wr=%.3f  top=%s(%.1f%%)",
        name, result["train_time_seconds"], best_iter,
        rmse_test, corr_test,
        results_by_thr["thr_0.40"]["n_signals"],
        results_by_thr["thr_0.40"]["wr"],
        results_by_thr["thr_0.40"]["pf"],
        results_by_thr["thr_0.40"]["tot_dollars"],
        gated_results_by_thr["thr_0.40"]["n_signals"],
        gated_results_by_thr["thr_0.40"]["wr"],
        feat_imp[0][0], top_feat_pct * 100,
    )
    return result


# ----------------------------------------------------------------------------
# Anti-leakage summary
# ----------------------------------------------------------------------------

def run_anti_leakage_checks(results: List[Dict]) -> Dict:
    """Aggregate guards #3, #4, #5 across windows."""
    alerts = []

    # Guard #3: top_feat_gain < 30% in every window
    for r in results:
        if not r["guards"]["top_feat_under_30pct"]:
            alerts.append(
                f"GUARD #3 SUSPICIOUS ({r['window']}): top feature '{r['top_feat_name']}' "
                f"accounts for {r['top_feat_pct']*100:.1f}% of gain (threshold 30%)"
            )

    # Guard #4: train_corr < 0.85 in every window
    for r in results:
        if not r["guards"]["train_corr_under_085"]:
            alerts.append(
                f"GUARD #4 ABORT ({r['window']}): train corr {r['corr_train']:.4f} > 0.85"
            )

    # Guard #5: test_corr std across windows < 0.05
    corrs = [r["corr_test"] for r in results if r["corr_test"] is not None]
    corr_std = float(np.std(corrs)) if len(corrs) >= 2 else 0.0
    if len(corrs) >= 3 and corr_std > 0.05:
        alerts.append(
            f"GUARD #5 UNSTABLE: test corr std across {len(corrs)} windows = "
            f"{corr_std:.4f} > 0.05"
        )

    return {
        "alerts": alerts,
        "guard_3_max_top_feat_pct": float(max(r["top_feat_pct"] for r in results)),
        "guard_4_max_train_corr":   float(max(r["corr_train"] for r in results)),
        "guard_5_corr_std":         corr_std,
        "test_corr_mean":           float(np.mean(corrs)) if corrs else 0.0,
        "n_windows_trained":        len(results),
    }


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", default=None,
                        help="Comma-separated list of YYYY-MM windows (default: all in WF_WINDOWS)")
    parser.add_argument("--max-seconds", type=int, default=300,
                        help="Safety cap on total runtime (seconds)")
    args = parser.parse_args()

    if args.windows:
        global WF_WINDOWS
        WF_WINDOWS = args.windows.split(",")

    print("=" * 76)
    print("v7 SHORT EXPERT — F5b")
    print(f"  features: {len(FEATURE_NAMES)} (59 v6 + 12 F4)")
    print(f"  label: {LABEL} (15m forward return %, filtered < 0)")
    print(f"  sample weights: 2x bottom-{DROP_PERCENTILE}% drops + 2x BEAR_2022")
    print(f"  LGB params: {LGB_PARAMS}")
    print(f"  walk-forward windows: {WF_WINDOWS}")
    print(f"  default thr_short: {THR_SHORT_DEFAULT}%  (vs thr_long 0.30%)")
    print(f"  funding_rate_z gate: > {FUNDING_Z_GATE}  (master plan §11.4)")
    print("=" * 76)

    df = load_dataset()  # parquet already filtered to LABEL < 0
    splits = walk_forward_splits(df)
    del df  # free memory; we only need the splits
    import gc
    gc.collect()

    if not splits:
        LOG.error("No valid walk-forward splits found.")
        sys.exit(1)

    results = []
    t0 = time.time()
    import gc
    for i, (name, train_df, test_df) in enumerate(splits, 1):
        if time.time() - t0 > args.max_seconds:
            LOG.warning("Hit max_seconds=%d before window %s", args.max_seconds, name)
            break
        LOG.info("=== Window %s (%d/%d) ===", name, i, len(splits))
        r = train_one_window(name, train_df, test_df)
        results.append(r)
        # Free per-window memory before next iteration
        del train_df, test_df
        gc.collect()

    checks = run_anti_leakage_checks(results)

    # Print summary table
    print("\n" + "=" * 110)
    print("v7 SHORT EXPERT — F5b RESULTS (walk-forward)")
    print("=" * 110)
    print(f"{'window':<10} {'n_tr':>7} {'n_te':>6} {'rmse_t':>8} {'corr_t':>7} {'dir_t':>7} "
          f"{'thr0.40_n':>9} {'thr0.40_wr':>10} {'thr0.40_pf':>10} "
          f"{'gated_n':>8} {'gated_wr':>9} {'top_feat%':>10}")
    for r in results:
        thr40 = r["short_thresholds"]["thr_0.40"]
        gated40 = r["short_thresholds_gated_fundingz"]["thr_0.40"]
        print(f"{r['window']:<10} {r['n_train']:>7,} {r['n_test']:>6,} "
              f"{r['rmse_test']:>8.4f} {r['corr_test']:>+7.4f} {r['dir_acc_test']:>7.4f} "
              f"{thr40['n_signals']:>9,} {thr40['wr']:>10.3f} {thr40['pf']:>10.2f} "
              f"{gated40['n_signals']:>8,} {gated40['wr']:>9.3f} "
              f"{r['top_feat_pct']*100:>9.1f}%")
    print()
    print(f"Mean test corr:     {checks['test_corr_mean']:+.4f}")
    print(f"Test corr std:      {checks['guard_5_corr_std']:.4f}  (guard #5 threshold: 0.05)")
    print(f"Max train corr:     {checks['guard_4_max_train_corr']:+.4f}  (guard #4 threshold: 0.85)")
    print(f"Max top-feat pct:   {checks['guard_3_max_top_feat_pct']*100:.1f}%  (guard #3 threshold: 30%)")
    print()
    if checks["alerts"]:
        print("=== ANTI-LEAKAGE ALERTS ===")
        for a in checks["alerts"]:
            print(f"  WARNING: {a}")
    else:
        print("OK: All anti-leakage guards passed")
    print()

    # Top features from first window
    if results:
        print("=== Top 20 features (first window) ===")
        for f in results[0]["top_20_features"]:
            print(f"  {f['name']:<28} {f['gain']:>12.0f}  ({f['pct']:.2f}%)")

    # Save summary
    summary_path = OUTPUT_DIR / "v7_short_expert_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "results": results,
            "anti_leakage_checks": checks,
            "feature_names": FEATURE_NAMES,
            "label": LABEL,
            "sample_weights": {
                "drop_weight": DROP_WEIGHT,
                "bear_weight": BEAR_WEIGHT,
                "drop_percentile": DROP_PERCENTILE,
            },
            "lgb_params": LGB_PARAMS,
            "n_boost_round": N_BOOST_ROUND,
            "early_stopping": EARLY_STOPPING,
            "short_specific_safety": {
                "thr_short_default": THR_SHORT_DEFAULT,
                "funding_z_gate": FUNDING_Z_GATE,
                "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            },
        }, f, indent=2)
    print(f"\nSummary saved: {summary_path}")


if __name__ == "__main__":
    main()
