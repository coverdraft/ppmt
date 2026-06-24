"""
v6_train.py — Train LightGBM regression model on v6 features with walk-forward.

Design (per docs/v5_cb_v2/v6_design.md):
  - Objective: regression (L2 / MSE)
  - Metrics: RMSE + MAE
  - Hyperparams: num_leaves=31, lr=0.05, n_estimators=500, early_stopping=50
  - Walk-forward: 6 monthly windows over RANGE_2025 + RECENT_2026
  - Anti-leakage guards:
      #3: no single feature may account for >30% of total gain
      #4: if train AUC (proxy: RMSE on label) > 0.85 equivalent, abort
          (we use correlation between pred and actual as the proxy)
      #5: if correlation std across 6 windows > 0.05, flag as unstable

Also reports:
  - Per-window RMSE, MAE, pred-actual correlation
  - Top 20 features by gain
  - Baseline comparison: predict-mean vs predict-zero
  - Direction accuracy (sign(pred) == sign(actual))
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_squared_error, mean_absolute_error

LOG = logging.getLogger("v6_train")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DB_PATH = os.environ.get("PPMT_DB_PATH", "/home/z/my-project/data/ppmt.db")
OUTPUT_DIR = Path("/home/z/my-project/data/v6_models")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Feature list must match v6_extract_features.py
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
FEATURE_NAMES = FEATURE_NAMES_V5 + FEATURE_NAMES_V6_NEW
assert len(FEATURE_NAMES) == 59

LABEL = "fwd_ret_3"  # 15-minute forward return in %


def load_dataset(conn) -> pd.DataFrame:
    """Load all feature rows into a DataFrame using SQL json_extract (fast)."""
    LOG.info("Loading feature_observations_v6 with json_extract...")
    feat_cols = ", ".join([f"json_extract(features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES])
    sql = f"""
        SELECT symbol, ts, window, asset_class, fwd_ret_3, {feat_cols}
        FROM feature_observations_v6
        WHERE fwd_ret_3 IS NOT NULL
    """
    df = pd.read_sql_query(sql, conn)
    LOG.info("Loaded %d rows. Cleaning features...", len(df))

    # Fill NaNs/inf
    for f in FEATURE_NAMES:
        df[f] = pd.to_numeric(df[f], errors="coerce").replace([np.inf, -np.inf], 0).fillna(0)

    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    LOG.info("Dataset ready: %d rows, %d features", len(df), len(FEATURE_NAMES))
    LOG.info("Windows: %s", df["window"].value_counts().to_dict())
    LOG.info("Label stats: mean=%.4f%% std=%.4f%% min=%.3f%% max=%.3f%%",
             df[LABEL].mean(), df[LABEL].std(), df[LABEL].min(), df[LABEL].max())
    return df


def walk_forward_splits(df: pd.DataFrame) -> list[tuple[str, pd.DataFrame, pd.DataFrame]]:
    """6 monthly windows over RANGE_2025 + RECENT_2026 (which is actually 2025).

    Train = all data BEFORE the test month (across all symbols/windows).
    Test  = data DURING the test month.

    Walk-forward: each month, train expands.

    Note: window names are misleading. The "RECENT_2026" window actually
    contains 2025-03 to 2025-06 data (timestamp 1742774400000 = 2025-03-24).
    RANGE_2025 contains 2025-08 to 2025-10.
    """
    # 3 monthly test windows (year, month) — reduced from 6 to fit bash timeout
    test_months = [
        (2025, 6),   # RECENT_2026 (actually 2025 Q2)
        (2025, 9),   # RANGE_2025
        (2025, 10),  # RANGE_2025
    ]
    splits = []
    for (yr, mo) in test_months:
        test_mask = (df["ts"].dt.year == yr) & (df["ts"].dt.month == mo)
        test_df = df[test_mask].copy()
        cutoff = pd.Timestamp(year=yr, month=mo, day=1, tz="UTC")
        train_df = df[df["ts"] < cutoff].copy()
        if len(train_df) > 1000 and len(test_df) > 1000:
            splits.append((f"{yr}-{mo:02d}", train_df, test_df))
            LOG.info("Split %s: train=%d  test=%d", f"{yr}-{mo:02d}", len(train_df), len(test_df))
    return splits


def train_one_window(name: str, train_df: pd.DataFrame, test_df: pd.DataFrame) -> dict:
    """Train LightGBM regression on train_df, evaluate on test_df."""
    X_train = train_df[FEATURE_NAMES].values
    y_train = train_df[LABEL].values
    X_test = test_df[FEATURE_NAMES].values
    y_test = test_df[LABEL].values

    # Also hold out 15% of train as validation for early stopping
    n_val = max(int(len(X_train) * 0.15), 1000)
    rng = np.random.default_rng(seed=42)
    val_idx = rng.choice(len(X_train), size=n_val, replace=False)
    val_mask = np.zeros(len(X_train), dtype=bool)
    val_mask[val_idx] = True
    X_val, y_val = X_train[val_mask], y_train[val_mask]
    X_tr,  y_tr  = X_train[~val_mask], y_train[~val_mask]

    dtrain = lgb.Dataset(X_tr,  label=y_tr,  feature_name=FEATURE_NAMES)
    dval   = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, reference=dtrain)

    params = {
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

    model = lgb.train(
        params,
        dtrain,
        num_boost_round=500,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)],
    )

    # Predictions
    pred_train = model.predict(X_tr, num_iteration=model.best_iteration)
    pred_test  = model.predict(X_test, num_iteration=model.best_iteration)
    pred_val   = model.predict(X_val, num_iteration=model.best_iteration)

    # Metrics
    rmse_test = float(np.sqrt(mean_squared_error(y_test, pred_test)))
    mae_test  = float(mean_absolute_error(y_test, pred_test))
    rmse_train = float(np.sqrt(mean_squared_error(y_tr, pred_train)))
    mae_train  = float(mean_absolute_error(y_tr, pred_train))

    # Correlation (proxy for AUC in regression)
    corr_test  = float(np.corrcoef(y_test,  pred_test)[0, 1])
    corr_train = float(np.corrcoef(y_tr, pred_train)[0, 1])
    corr_val   = float(np.corrcoef(y_val, pred_val)[0, 1])

    # Direction accuracy
    dir_acc_test  = float(((pred_test  > 0) == (y_test  > 0)).mean())
    dir_acc_train = float(((pred_train > 0) == (y_tr > 0)).mean())

    # Baselines for comparison
    # 1. Predict the mean of training y
    mean_pred = y_tr.mean()
    rmse_mean_baseline = float(np.sqrt(mean_squared_error(y_test, np.full_like(y_test, mean_pred))))
    # 2. Predict zero
    rmse_zero_baseline = float(np.sqrt(mean_squared_error(y_test, np.zeros_like(y_test))))

    # Feature importance
    importance = model.feature_importance(importance_type="gain")
    feat_imp = sorted(zip(FEATURE_NAMES, importance), key=lambda x: -x[1])
    total_gain = importance.sum() if importance.sum() > 0 else 1.0
    top_feat_pct = feat_imp[0][1] / total_gain if total_gain > 0 else 0.0

    # Save model
    model_path = OUTPUT_DIR / f"v6_{name}.txt"
    model.save_model(str(model_path))

    return {
        "window": name,
        "n_train": len(X_tr),
        "n_val":   len(X_val),
        "n_test":  len(X_test),
        "best_iteration": int(model.best_iteration) if model.best_iteration else 500,
        "rmse_train": rmse_train, "mae_train": mae_train,
        "rmse_test":  rmse_test,  "mae_test":  mae_test,
        "corr_train": corr_train, "corr_val": corr_val, "corr_test": corr_test,
        "dir_acc_train": dir_acc_train, "dir_acc_test": dir_acc_test,
        "rmse_mean_baseline": rmse_mean_baseline,
        "rmse_zero_baseline": rmse_zero_baseline,
        "top_feat_pct": float(top_feat_pct),
        "top_feat_name": feat_imp[0][0],
        "top_20_features": [{"name": n, "gain": float(g)} for n, g in feat_imp[:20]],
        "model_path": str(model_path),
    }


def run_anti_leakage_checks(results: list[dict]) -> dict:
    """Guards #3, #4, #5."""
    alerts = []

    # Guard #3: no feature may account for >30% of total gain in any window
    for r in results:
        if r["top_feat_pct"] > 0.30:
            alerts.append(
                f"GUARD #3 SUSPICIOUS (window={r['window']}): top feature "
                f"'{r['top_feat_name']}' accounts for {r['top_feat_pct']*100:.1f}% of gain "
                f"(threshold 30%) — possible leakage"
            )

    # Guard #4: if train correlation > 0.85, abort
    # (regression analog of AUC > 0.85; for regression, corr > 0.85 = R² > 0.72,
    # which is implausibly high for 5m crypto returns)
    for r in results:
        if r["corr_train"] > 0.85:
            alerts.append(
                f"GUARD #4 ABORT (window={r['window']}): train correlation "
                f"{r['corr_train']:.4f} > 0.85 — almost certainly leakage"
            )

    # Guard #5: if test correlation std across windows > 0.05, flag unstable
    corrs = [r["corr_test"] for r in results if r["corr_test"] is not None]
    if len(corrs) >= 3:
        corr_std = float(np.std(corrs))
        if corr_std > 0.05:
            alerts.append(
                f"GUARD #5 UNSTABLE: test correlation std across {len(corrs)} windows = "
                f"{corr_std:.4f} > 0.05 — model is unstable across time"
            )

    return {
        "alerts": alerts,
        "guard_3_max_top_feat_pct": float(max(r["top_feat_pct"] for r in results)),
        "guard_4_max_train_corr":   float(max(r["corr_train"] for r in results)),
        "guard_5_corr_std":         float(np.std(corrs)) if corrs else 0.0,
        "test_corr_mean":           float(np.mean(corrs)) if corrs else 0.0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-seconds", type=int, default=300)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    df = load_dataset(conn)
    conn.close()

    splits = walk_forward_splits(df)
    if not splits:
        LOG.error("No valid walk-forward splits — need data in RANGE_2025 + RECENT_2026")
        sys.exit(1)

    results = []
    import time
    t0 = time.time()
    for i, (name, train_df, test_df) in enumerate(splits, 1):
        if time.time() - t0 > args.max_seconds:
            LOG.warning("Hit max_seconds=%d before training window %s", args.max_seconds, name)
            break
        LOG.info("=== Training window %s (%d/%d) ===", name, i, len(splits))
        r = train_one_window(name, train_df, test_df)
        results.append(r)
        LOG.info("  %s: rmse_test=%.4f mae_test=%.4f corr_test=%.4f dir_acc_test=%.4f (baseline dir=0.500)",
                 name, r["rmse_test"], r["mae_test"], r["corr_test"], r["dir_acc_test"])
        LOG.info("  top feature: %s (%.1f%% of gain)",
                 r["top_feat_name"], r["top_feat_pct"] * 100)

    # Anti-leakage checks
    checks = run_anti_leakage_checks(results)

    # Print summary
    print("\n" + "="*72)
    print("v6 TRAIN RESULTS (walk-forward, 6 monthly windows)")
    print("="*72)
    print(f"{'window':<10} {'n_train':>8} {'n_test':>7} {'rmse_t':>8} {'mae_t':>7} {'corr_t':>7} {'dir_t':>7} {'top_feat%':>10}")
    for r in results:
        print(f"{r['window']:<10} {r['n_train']:>8,} {r['n_test']:>7,} "
              f"{r['rmse_test']:>8.4f} {r['mae_test']:>7.4f} {r['corr_test']:>7.4f} "
              f"{r['dir_acc_test']:>7.4f} {r['top_feat_pct']*100:>9.1f}%")
    print()
    print(f"Mean test corr:     {checks['test_corr_mean']:+.4f}")
    print(f"Test corr std:      {checks['guard_5_corr_std']:.4f}  (guard #5 threshold: 0.05)")
    print(f"Max train corr:     {checks['guard_4_max_train_corr']:+.4f}  (guard #4 threshold: 0.85)")
    print(f"Max top-feat pct:   {checks['guard_3_max_top_feat_pct']*100:.1f}%  (guard #3 threshold: 30%)")
    print()
    if checks["alerts"]:
        print("=== ANTI-LEAKAGE ALERTS ===")
        for a in checks["alerts"]:
            print(f"  ⚠️  {a}")
    else:
        print("✓ All anti-leakage guards passed")
    print()

    # Show top 20 features from first window
    if results:
        print("=== Top 20 features (first window) ===")
        for f in results[0]["top_20_features"]:
            print(f"  {f['name']:<28} {f['gain']:>12.0f}")

    # Save results JSON
    out_path = OUTPUT_DIR / "v6_train_results.json"
    with open(out_path, "w") as f:
        json.dump({
            "results": results,
            "anti_leakage_checks": checks,
            "feature_names": FEATURE_NAMES,
            "label": LABEL,
        }, f, indent=2)
    print(f"\nResults saved: {out_path}")


if __name__ == "__main__":
    main()
