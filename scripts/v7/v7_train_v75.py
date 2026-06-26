"""
v7_train_v75.py — Option D ("v7.5"): v7 features (71) + v6 architecture (single regression, ALL labels).

WHY THIS EXISTS
---------------
F5a/F5b dual-expert design FAILED (F7 backtest: -37,571% PnL, Sharpe -55).
Root cause: training on `LABEL > 0` only (LONG) or `LABEL < 0` only (SHORT) makes
the model learn MAGNITUDE (|fwd_ret_3|), not DIRECTION. Both experts converge to
predicting E[|fwd_ret_3| | features], so at inference both predict positive values
and the sign-based decision rule breaks down.

v6 architecture (single LightGBM regression on ALL labels) learns E[fwd_ret_3 | features]
which DOES include the sign of expected return — directional by construction.

v7.5 = v7's richer feature set (71 = 59 v6 + 12 F4) + v6's directional learning architecture.
Expected: Sharpe 1.5-2.0, WR 58-62%, +150-200% PnL (beats v6 baseline Sharpe 1.22).

WHAT THIS DOES
--------------
Per Option D (PPMT_v7_MASTER_PLAN.md §12.1):
  - Train on ALL labels (no sign filter) — directional learning
  - LightGBM regression on fwd_ret_3 (%)
  - NO sample weights (v6 doesn't use them; weights were a dual-expert trick)
  - 5 walk-forward windows: 2025-04, 05, 06, 09, 10
  - Anti-leakage guards #3 (top_feat<30%), #4 (train_corr<0.85), #5 (test_corr std<0.05)

FEATURES (71 total = 59 v6 + 12 F4):
  - 59 v6 base features (from feature_observations_v6.features_json via json_extract)
  - 12 F4 features (from feature_observations_v7_extras as plain columns):
    funding_rate, funding_rate_z, oi_change_1h, oi_change_4h,
    sector_blue_chip, sector_large_cap, sector_old_meme, sector_new_meme,
    sector_idx, day_of_week_sin, day_of_week_cos, day_of_week

INFERENCE DECISION RULE (used by v7_backtest_v75.py):
  - pred >  +thr  → LONG signal (expected fwd_ret_3 > +thr%)
  - pred < -thr  → SHORT signal (expected fwd_ret_3 < -thr%)
  - |pred| <= thr → WAIT (no signal)

OUTPUTS:
  - data/v7_models/v75/v75_{window}.txt  (LGBM model file)
  - data/v7_models/v75/v75_{window}_results.json
  - data/v7_models/v75/v75_summary.json

USAGE:
    python /home/z/my-project/scripts/v7/v7_train_v75.py
    python /home/z/my-project/scripts/v7/v7_train_v75.py --windows 2025-06,2025-10
    python /home/z/my-project/scripts/v7/v7_train_v75.py --max-seconds 240
"""
from __future__ import annotations


# === Auto-detected project root (portable paths, patched) ===
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[2]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
# === End path setup ===



import argparse
import gc
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_squared_error, mean_absolute_error

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

DB_PATH = os.environ.get("PPMT_DB_PATH", _PROJECT_ROOT_STR + "/data/ppmt.db")
OUTPUT_DIR = Path(_PROJECT_ROOT_STR + "/data/v7_models/v75")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v7_5")
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

# Walk-forward test months (same as v6)
WF_WINDOWS = ["2025-04", "2025-05", "2025-06", "2025-09", "2025-10"]

# Frozen LGBM hyperparams (matches v6_train_wf.py exactly — NO sample weights)
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
N_BOOST_ROUND = 200
EARLY_STOPPING = 30

# Cost model (matches v6_backtest_filtered.py)
ROUND_TRIP_COST_PCT = 0.14


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------

PARQUET_PATH = OUTPUT_DIR / "v75_features.parquet"


def load_dataset() -> pd.DataFrame:
    """Load ALL feature observations (no sign filter) from materialized parquet.

    Falls back to in-DB loading if parquet is missing.

    Returns DataFrame with columns: symbol, ts, window, fwd_ret_3, <71 features>.
    """
    if PARQUET_PATH.exists():
        LOG.info("Loading parquet: %s", PARQUET_PATH)
        t0 = time.time()
        df = pd.read_parquet(PARQUET_PATH)
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        LOG.info(
            "  loaded %d rows × %d cols in %.1fs  label mean=%.4f%% std=%.4f%%",
            len(df), len(df.columns), time.time() - t0,
            float(df[LABEL].mean()), float(df[LABEL].std()),
        )
        LOG.info("  windows: %s", df["window"].value_counts().to_dict())
        return df

    LOG.warning("Parquet missing. Falling back to slow in-DB load.")
    LOG.warning("Run: python scripts/v7/v7_materialize_v75_features.py")
    return load_dataset_from_db()


def load_dataset_from_db() -> pd.DataFrame:
    """Fallback: load directly from DB (slow, memory-heavy)."""
    LOG.info("Loading dataset from DB (ALL labels, no filter)...")
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)

    v6_feat_cols = ", ".join(
        [f"json_extract(v6.features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES_V6]
    )
    f4_cols_sql = ", ".join([f"e.{f}" for f in FEATURE_NAMES_F4])
    sql = f"""
        SELECT v6.symbol, v6.ts, v6.window, v6.{LABEL},
               {v6_feat_cols},
               {f4_cols_sql}
        FROM feature_observations_v6 AS v6
        INNER JOIN feature_observations_v7_extras AS e
          ON v6.symbol = e.symbol AND v6.ts = e.ts
        WHERE v6.{LABEL} IS NOT NULL
    """
    df = pd.read_sql_query(sql, conn)
    conn.close()

    for f in FEATURE_NAMES + [LABEL]:
        df[f] = pd.to_numeric(df[f], errors="coerce").replace([np.inf, -np.inf], 0).fillna(0).astype(np.float32)
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    LOG.info(
        "  loaded %d rows × %d cols in %.1fs  label mean=%.4f%% std=%.4f%%",
        len(df), len(df.columns), time.time() - t0,
        float(df[LABEL].mean()), float(df[LABEL].std()),
    )
    LOG.info("  windows: %s", df["window"].value_counts().to_dict())
    return df


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
            n_long_train = int((train_df[LABEL] > 0).sum())
            n_short_train = int((train_df[LABEL] < 0).sum())
            LOG.info(
                "split %s: train=%d  test=%d  (train longs=%d shorts=%d)",
                window_str, len(train_df), len(test_df), n_long_train, n_short_train,
            )
        else:
            LOG.warning("split %s: SKIPPED (train=%d test=%d)", window_str, len(train_df), len(test_df))
    return splits


def train_one_window(name: str, train_df: pd.DataFrame, test_df: pd.DataFrame) -> Dict:
    """Train v7.5 LightGBM regression on one walk-forward window (ALL labels)."""
    t0 = time.time()

    X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_df[LABEL].values.astype(np.float32)
    X_test = test_df[FEATURE_NAMES].values.astype(np.float32)
    y_test = test_df[LABEL].values.astype(np.float32)

    # Validation split (10% of train, matches v6_train_wf.py)
    rng = np.random.default_rng(seed=42)
    n_val = int(len(X_train) * 0.1)
    val_idx = rng.choice(len(X_train), size=n_val, replace=False)
    val_mask = np.zeros(len(X_train), dtype=bool)
    val_mask[val_idx] = True
    X_val, y_val = X_train[val_mask], y_train[val_mask]
    X_tr, y_tr = X_train[~val_mask], y_train[~val_mask]

    LOG.info("[%s] train=%d val=%d test=%d", name, len(X_tr), len(X_val), len(X_test))

    dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES)
    dval = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, reference=dtrain)

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
    pred_val = model.predict(X_val, num_iteration=best_iter)
    pred_test = model.predict(X_test, num_iteration=best_iter)

    # Metrics
    rmse_test = float(np.sqrt(mean_squared_error(y_test, pred_test)))
    mae_test = float(mean_absolute_error(y_test, pred_test))
    rmse_train = float(np.sqrt(mean_squared_error(y_tr, pred_train)))
    try:
        corr_test = float(np.corrcoef(y_test, pred_test)[0, 1])
    except Exception:
        corr_test = 0.0
    try:
        corr_train = float(np.corrcoef(y_tr, pred_train)[0, 1])
    except Exception:
        corr_train = 0.0
    try:
        corr_val = float(np.corrcoef(y_val, pred_val)[0, 1])
    except Exception:
        corr_val = 0.0

    # Direction accuracy (sign match) — this is the KEY metric for v7.5
    # v6 baseline: ~50% (random); v7.5 should be 52-58% if directional edge exists
    dir_acc_test = float(((pred_test > 0) == (y_test > 0)).mean())
    dir_acc_train = float(((pred_train > 0) == (y_tr > 0)).mean())

    # Threshold sweep for LONG and SHORT signals
    # LONG: pred > +thr → pay fwd_ret_3 - cost
    # SHORT: pred < -thr → pay -fwd_ret_3 - cost
    thresholds = [0.20, 0.30, 0.40, 0.50, 0.75, 1.00]
    results_by_thr = {}
    for thr in thresholds:
        long_mask = pred_test > thr
        short_mask = pred_test < -thr
        n_long = int(long_mask.sum())
        n_short = int(short_mask.sum())

        if n_long > 0:
            actuals_long = y_test[long_mask]
            net_long = actuals_long - ROUND_TRIP_COST_PCT
            wins_l = float(net_long[net_long > 0].sum())
            losses_l = float(-net_long[net_long < 0].sum())
            pf_l = wins_l / losses_l if losses_l > 0 else 99.0
            wr_l = float((net_long > 0).mean())
            pnl_l = float(net_long.sum())
        else:
            pf_l = wr_l = pnl_l = 0.0

        if n_short > 0:
            actuals_short = y_test[short_mask]
            # SHORT PnL = -fwd_ret_3 - cost (if fwd_ret_3 is negative, we profit)
            net_short = -actuals_short - ROUND_TRIP_COST_PCT
            wins_s = float(net_short[net_short > 0].sum())
            losses_s = float(-net_short[net_short < 0].sum())
            pf_s = wins_s / losses_s if losses_s > 0 else 99.0
            wr_s = float((net_short > 0).mean())
            pnl_s = float(net_short.sum())
        else:
            pf_s = wr_s = pnl_s = 0.0

        n_total = n_long + n_short
        pnl_total = pnl_l + pnl_s
        # $700 per trade, PnL is in % so divide by 100
        dollars_total = float(pnl_total / 100 * 700)

        results_by_thr[f"thr_{thr:.2f}"] = {
            "n_long": n_long,
            "n_short": n_short,
            "n_total": n_total,
            "long_wr": float(wr_l),
            "long_pf": float(pf_l),
            "long_pnl_pct": float(pnl_l),
            "short_wr": float(wr_s),
            "short_pf": float(pf_s),
            "short_pnl_pct": float(pnl_s),
            "total_pnl_pct": float(pnl_total),
            "total_dollars": dollars_total,
        }

    # Feature importance
    importance = model.feature_importance(importance_type="gain")
    feat_imp = sorted(zip(FEATURE_NAMES, importance), key=lambda x: -x[1])
    total = max(float(importance.sum()), 1.0)
    top_feat_pct = feat_imp[0][1] / total

    # Save model
    model_path = OUTPUT_DIR / f"v75_{name}.txt"
    model.save_model(str(model_path))

    result = {
        "window": name,
        "model_type": "v75_single_regression",
        "label": LABEL,
        "n_features": len(FEATURE_NAMES),
        "n_train": len(X_tr),
        "n_val": len(X_val),
        "n_test": len(X_test),
        "best_iteration": best_iter,
        "rmse_train": rmse_train,
        "rmse_test": rmse_test,
        "mae_test": mae_test,
        "corr_train": corr_train,
        "corr_val": corr_val,
        "corr_test": corr_test,
        "dir_acc_test": dir_acc_test,
        "dir_acc_train": dir_acc_train,
        "thresholds": results_by_thr,
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
    thr30 = results_by_thr["thr_0.30"]
    LOG.info(
        "[%s] done in %.1fs  best_iter=%d  rmse_test=%.4f  corr_test=%+.4f  "
        "dir_acc=%.3f  thr0.30: L=%d S=%d pnl=%+.2f%% $%+.0f  top=%s(%.1f%%)",
        name, result["train_time_seconds"], best_iter,
        rmse_test, corr_test, dir_acc_test,
        thr30["n_long"], thr30["n_short"], thr30["total_pnl_pct"],
        thr30["total_dollars"],
        feat_imp[0][0], top_feat_pct * 100,
    )
    return result


# ----------------------------------------------------------------------------
# Anti-leakage summary
# ----------------------------------------------------------------------------

def run_anti_leakage_checks(results: List[Dict]) -> Dict:
    """Aggregate guards #3, #4, #5 across windows."""
    alerts = []

    for r in results:
        if not r["guards"]["top_feat_under_30pct"]:
            alerts.append(
                f"GUARD #3 SUSPICIOUS ({r['window']}): top feature '{r['top_feat_name']}' "
                f"accounts for {r['top_feat_pct']*100:.1f}% of gain (threshold 30%)"
            )

    for r in results:
        if not r["guards"]["train_corr_under_085"]:
            alerts.append(
                f"GUARD #4 ABORT ({r['window']}): train corr {r['corr_train']:.4f} > 0.85"
            )

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
        "guard_4_max_train_corr": float(max(r["corr_train"] for r in results)),
        "guard_5_corr_std": corr_std,
        "test_corr_mean": float(np.mean(corrs)) if corrs else 0.0,
        "dir_acc_mean": float(np.mean([r["dir_acc_test"] for r in results])),
        "n_windows_trained": len(results),
    }


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", default=None,
                        help="Comma-separated list of YYYY-MM windows (default: all in WF_WINDOWS)")
    parser.add_argument("--max-seconds", type=int, default=600,
                        help="Safety cap on total runtime (seconds)")
    args = parser.parse_args()

    if args.windows:
        global WF_WINDOWS
        WF_WINDOWS = args.windows.split(",")

    print("=" * 76)
    print("v7.5 — Option D: v7 features + v6 architecture (single regression, ALL labels)")
    print(f"  features: {len(FEATURE_NAMES)} (59 v6 + 12 F4)")
    print(f"  label: {LABEL} (15m forward return %, ALL labels — directional)")
    print(f"  NO sample weights (matches v6_train_wf.py)")
    print(f"  LGB params: {LGB_PARAMS}")
    print(f"  walk-forward windows: {WF_WINDOWS}")
    print("=" * 76)

    df = load_dataset()
    splits = walk_forward_splits(df)
    del df
    gc.collect()

    if not splits:
        LOG.error("No valid walk-forward splits found.")
        sys.exit(1)

    results = []
    t0 = time.time()
    for i, (name, train_df, test_df) in enumerate(splits, 1):
        if time.time() - t0 > args.max_seconds:
            LOG.warning("Hit max_seconds=%d before window %s", args.max_seconds, name)
            break
        LOG.info("=== Window %s (%d/%d) ===", name, i, len(splits))
        r = train_one_window(name, train_df, test_df)
        results.append(r)
        del train_df, test_df
        gc.collect()

    checks = run_anti_leakage_checks(results)

    # Print summary table
    print("\n" + "=" * 110)
    print("v7.5 RESULTS (walk-forward, ALL labels, 71 features)")
    print("=" * 110)
    print(f"{'window':<10} {'n_tr':>7} {'n_te':>6} {'rmse_t':>8} {'corr_t':>7} "
          f"{'dir_t':>7} {'L0.30':>6} {'S0.30':>6} {'pnl0.30':>9} {'$0.30':>8} {'top%':>6}")
    print("-" * 110)
    for r in results:
        thr30 = r["thresholds"]["thr_0.30"]
        print(f"{r['window']:<10} {r['n_train']:>7,} {r['n_test']:>6,} "
              f"{r['rmse_test']:>8.4f} {r['corr_test']:>+7.4f} {r['dir_acc_test']:>7.4f} "
              f"{thr30['n_long']:>6,} {thr30['n_short']:>6,} "
              f"{thr30['total_pnl_pct']:>+8.2f}% {thr30['total_dollars']:>+7.0f} "
              f"{r['top_feat_pct']*100:>5.1f}%")
    print()
    print(f"Mean test corr:     {checks['test_corr_mean']:+.4f}")
    print(f"Test corr std:      {checks['guard_5_corr_std']:.4f}  (guard #5 threshold: 0.05)")
    print(f"Mean dir accuracy:  {checks['dir_acc_mean']:.4f}  (v6 baseline ~0.50, target >0.52)")
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
    summary_path = OUTPUT_DIR / "v75_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "results": results,
            "anti_leakage_checks": checks,
            "feature_names": FEATURE_NAMES,
            "label": LABEL,
            "lgb_params": LGB_PARAMS,
            "wf_windows": WF_WINDOWS,
            "description": "v7.5 — v7 features (71) + v6 architecture (single regression, ALL labels, no weights)",
        }, f, indent=2, default=str)
    LOG.info("Summary saved to %s", summary_path)


if __name__ == "__main__":
    main()
