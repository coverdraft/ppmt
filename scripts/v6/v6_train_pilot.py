"""
v6_train_pilot.py — Fast pilot training (single split) to validate the pipeline.

Train: all data before 2026-05
Test:  May 2026 data
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_squared_error, mean_absolute_error

DB_PATH = '/home/z/my-project/data/ppmt.db'
OUT_DIR = Path('/home/z/my-project/data/v6_models')
OUT_DIR.mkdir(parents=True, exist_ok=True)

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


def load_dataset():
    """Load and parse features. Use SQL to extract each feature directly from JSON."""
    print(f"[1/5] Loading from DB...", flush=True)
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)

    # Build SQL with json_extract for each feature — much faster than Python parse
    feat_cols = ", ".join([f"json_extract(features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES])
    sql = f"""
        SELECT ts, window, symbol, fwd_ret_3, {feat_cols}
        FROM feature_observations_v6
        WHERE fwd_ret_3 IS NOT NULL
    """
    df = pd.read_sql_query(sql, conn)
    conn.close()
    print(f"  loaded {len(df):,} rows in {time.time()-t0:.1f}s", flush=True)

    # Fill NaNs/inf
    for f in FEATURE_NAMES:
        df[f] = pd.to_numeric(df[f], errors='coerce').replace([np.inf, -np.inf], 0).fillna(0)

    df['ts'] = pd.to_datetime(df['ts'], unit='s', utc=True)
    print(f"  ts range: {df['ts'].min()} to {df['ts'].max()}", flush=True)
    return df


def main():
    df = load_dataset()

    print(f"[2/5] Splitting...", flush=True)
    # Use 2025-06 (last month of RECENT_2026 window) as OOS test
    test_mask = (df['ts'].dt.year == 2025) & (df['ts'].dt.month == 6)
    train_df = df[~test_mask].copy()
    test_df  = df[test_mask].copy()
    print(f"  train: {len(train_df):,}  test: {len(test_df):,}", flush=True)

    X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_df['fwd_ret_3'].values.astype(np.float32)
    X_test  = test_df[FEATURE_NAMES].values.astype(np.float32)
    y_test  = test_df['fwd_ret_3'].values.astype(np.float32)

    # 10% validation for early stopping
    rng = np.random.default_rng(seed=42)
    n_val = int(len(X_train) * 0.1)
    val_idx = rng.choice(len(X_train), size=n_val, replace=False)
    val_mask = np.zeros(len(X_train), dtype=bool)
    val_mask[val_idx] = True
    X_val, y_val = X_train[val_mask], y_train[val_mask]
    X_tr,  y_tr  = X_train[~val_mask], y_train[~val_mask]

    print(f"  tr={len(X_tr):,}  val={len(X_val):,}  test={len(X_test):,}", flush=True)

    print(f"[3/5] Training LightGBM regression...", flush=True)
    dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES)
    dval   = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, reference=dtrain)
    params = {
        'objective': 'regression',
        'metric': ['rmse', 'mae'],
        'num_leaves': 31,
        'learning_rate': 0.05,
        'feature_fraction': 0.85,
        'bagging_fraction': 0.85,
        'bagging_freq': 5,
        'min_data_in_leaf': 200,
        'lambda_l2': 1.0,
        'verbosity': -1,
        'seed': 42,
    }
    t0 = time.time()
    model = lgb.train(
        params, dtrain, num_boost_round=300,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(0)],
    )
    print(f"  trained in {time.time()-t0:.1f}s, best_iter={model.best_iteration}", flush=True)

    print(f"[4/5] Evaluating...", flush=True)
    pred_test  = model.predict(X_test, num_iteration=model.best_iteration)
    pred_train = model.predict(X_tr, num_iteration=model.best_iteration)

    rmse_test  = float(np.sqrt(mean_squared_error(y_test, pred_test)))
    rmse_train = float(np.sqrt(mean_squared_error(y_tr, pred_train)))
    mae_test   = float(mean_absolute_error(y_test, pred_test))
    corr_test  = float(np.corrcoef(y_test, pred_test)[0, 1])
    corr_train = float(np.corrcoef(y_tr, pred_train)[0, 1])
    dir_acc_test  = float(((pred_test  > 0) == (y_test  > 0)).mean())
    dir_acc_train = float(((pred_train > 0) == (y_tr > 0)).mean())
    rmse_mean = float(np.sqrt(mean_squared_error(y_test, np.full_like(y_test, y_tr.mean()))))
    rmse_zero = float(np.sqrt(mean_squared_error(y_test, np.zeros_like(y_test))))
    dir_baseline = float((y_test > 0).mean())

    print()
    print("=" * 60)
    print("v6 PILOT RESULTS (train != 2025-06, test=2025-06)")
    print("=" * 60)
    print(f"RMSE  test: {rmse_test:.4f}  (baseline mean: {rmse_mean:.4f}, zero: {rmse_zero:.4f})")
    print(f"RMSE  train:{rmse_train:.4f}")
    print(f"MAE   test: {mae_test:.4f}")
    print(f"Corr  test: {corr_test:+.4f}  (train: {corr_train:+.4f})")
    print(f"Dir   test: {dir_acc_test:.4f}  (baseline always-up: {dir_baseline:.4f})")
    print(f"Label y_test: mean={y_test.mean():+.4f}% std={y_test.std():.4f}%")

    importance = model.feature_importance(importance_type='gain')
    feat_imp = sorted(zip(FEATURE_NAMES, importance), key=lambda x: -x[1])
    total = max(float(importance.sum()), 1.0)
    print()
    print("Top 20 features by gain:")
    for n, g in feat_imp[:20]:
        pct = g / total * 100
        print(f"  {n:<28} {g:>10.0f}  ({pct:>5.1f}%)")

    # Anti-leakage guards
    print()
    print("=== Anti-leakage guards ===")
    top_feat_pct = feat_imp[0][1] / total * 100
    print(f"Guard #3 (top feature %):  {top_feat_pct:.1f}%  (threshold: 30%)  -> {'PASS' if top_feat_pct < 30 else 'SUSPICIOUS'}")
    print(f"Guard #4 (train corr):     {corr_train:+.4f}  (threshold: 0.85)   -> {'PASS' if corr_train < 0.85 else 'ABORT'}")

    # Save
    model_path = OUT_DIR / 'v6_pilot.txt'
    model.save_model(str(model_path))
    result = {
        'rmse_test': rmse_test, 'rmse_train': rmse_train, 'mae_test': mae_test,
        'corr_test': corr_test, 'corr_train': corr_train,
        'dir_acc_test': dir_acc_test, 'dir_acc_train': dir_acc_train,
        'rmse_mean_baseline': rmse_mean, 'rmse_zero_baseline': rmse_zero,
        'dir_baseline': dir_baseline,
        'best_iteration': int(model.best_iteration) if model.best_iteration else 300,
        'n_train': len(X_tr), 'n_val': len(X_val), 'n_test': len(X_test),
        'top_feat_pct': float(top_feat_pct),
        'top_feat_name': feat_imp[0][0],
        'top_20_features': [{'name': n, 'gain': float(g), 'pct': float(g/total*100)} for n, g in feat_imp[:20]],
        'feature_names': FEATURE_NAMES,
        'model_path': str(model_path),
    }
    with open(OUT_DIR / 'v6_pilot_results.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\n[5/5] Saved: {model_path}")
    print(f"       Saved: {OUT_DIR / 'v6_pilot_results.json'}")


if __name__ == '__main__':
    main()
