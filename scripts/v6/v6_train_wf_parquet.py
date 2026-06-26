"""
v6_train_wf_parquet.py — v6 walk-forward training using parquet (fast).

Same as v6_train_wf.py but loads from v7.5 parquet (which has all 71 features)
and selects only the 59 v6 features. Avoids slow json_extract on 1.44M rows.

Trains one window per invocation, same interface as v6_train_wf.py:
    python v6_train_wf_parquet.py --window 2025-06
"""

# === Auto-detected project root (portable paths, patched) ===
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[2]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
# === End path setup ===



import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_squared_error, mean_absolute_error

OUT_DIR = Path('/home/z/my-project/data/v6_models')
OUT_DIR.mkdir(parents=True, exist_ok=True)
PARQUET_PATH = Path('/home/z/my-project/data/v7_models/v75/v75_features.parquet')

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
LABEL = "fwd_ret_3"


def load_dataset():
    print(f"[1/4] Loading parquet...", flush=True)
    t0 = time.time()
    df = pd.read_parquet(PARQUET_PATH)
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    print(f"  loaded {len(df):,} rows × {len(df.columns)} cols in {time.time()-t0:.1f}s", flush=True)
    return df


def train_window(df, window_str):
    yr, mo = window_str.split('-')
    yr, mo = int(yr), int(mo)
    test_mask = (df['ts'].dt.year == yr) & (df['ts'].dt.month == mo)
    cutoff = pd.Timestamp(year=yr, month=mo, day=1, tz='UTC')
    train_df = df[df['ts'] < cutoff].copy()
    test_df  = df[test_mask].copy()
    print(f"  train: {len(train_df):,}  test: {len(test_df):,}", flush=True)

    X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_df[LABEL].values.astype(np.float32)
    X_test  = test_df[FEATURE_NAMES].values.astype(np.float32)
    y_test  = test_df[LABEL].values.astype(np.float32)

    rng = np.random.default_rng(seed=42)
    n_val = int(len(X_train) * 0.1)
    val_idx = rng.choice(len(X_train), size=n_val, replace=False)
    val_mask = np.zeros(len(X_train), dtype=bool)
    val_mask[val_idx] = True
    X_val, y_val = X_train[val_mask], y_train[val_mask]
    X_tr,  y_tr  = X_train[~val_mask], y_train[~val_mask]
    print(f"  tr={len(X_tr):,} val={len(X_val):,} test={len(X_test):,}", flush=True)

    print(f"[3/4] Training...", flush=True)
    dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES)
    dval   = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, reference=dtrain)
    params = {
        'objective': 'regression', 'metric': ['rmse', 'mae'],
        'num_leaves': 31, 'learning_rate': 0.05,
        'feature_fraction': 0.85, 'bagging_fraction': 0.85, 'bagging_freq': 5,
        'min_data_in_leaf': 200, 'lambda_l2': 1.0,
        'verbosity': -1, 'seed': 42,
    }
    t0 = time.time()
    model = lgb.train(
        params, dtrain, num_boost_round=300,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(0)],
    )
    print(f"  trained in {time.time()-t0:.1f}s, best_iter={model.best_iteration}", flush=True)

    print(f"[4/4] Evaluating...", flush=True)
    pred_test  = model.predict(X_test, num_iteration=model.best_iteration)
    pred_train = model.predict(X_tr, num_iteration=model.best_iteration)

    rmse_test  = float(np.sqrt(mean_squared_error(y_test, pred_test)))
    rmse_train = float(np.sqrt(mean_squared_error(y_tr, pred_train)))
    mae_test   = float(mean_absolute_error(y_test, pred_test))
    try: corr_test  = float(np.corrcoef(y_test, pred_test)[0, 1])
    except Exception: corr_test = 0.0
    try: corr_train = float(np.corrcoef(y_tr, pred_train)[0, 1])
    except Exception: corr_train = 0.0
    dir_acc_test  = float(((pred_test  > 0) == (y_test  > 0)).mean())
    rmse_mean = float(np.sqrt(mean_squared_error(y_test, np.full_like(y_test, y_tr.mean()))))
    rmse_zero = float(np.sqrt(mean_squared_error(y_test, np.zeros_like(y_test))))
    dir_baseline = float((y_test > 0).mean())

    importance = model.feature_importance(importance_type='gain')
    feat_imp = sorted(zip(FEATURE_NAMES, importance), key=lambda x: -x[1])
    total = max(float(importance.sum()), 1.0)
    top_feat_pct = feat_imp[0][1] / total

    model_path = OUT_DIR / f'v6_{window_str}.txt'
    model.save_model(str(model_path))
    result = {
        'window': window_str,
        'n_train': len(X_tr), 'n_val': len(X_val), 'n_test': len(X_test),
        'best_iteration': int(model.best_iteration) if model.best_iteration else 300,
        'rmse_test': rmse_test, 'rmse_train': rmse_train, 'mae_test': mae_test,
        'corr_test': corr_test, 'corr_train': corr_train,
        'dir_acc_test': dir_acc_test,
        'rmse_mean_baseline': rmse_mean, 'rmse_zero_baseline': rmse_zero,
        'dir_baseline': dir_baseline,
        'label_mean': float(y_test.mean()),
        'label_std':  float(y_test.std()),
        'top_feat_pct': float(top_feat_pct),
        'top_feat_name': feat_imp[0][0],
        'top_20_features': [{'name': n, 'gain': float(g), 'pct': float(g/total*100)} for n, g in feat_imp[:20]],
        'model_path': str(model_path),
    }
    results_path = OUT_DIR / f'v6_{window_str}_results.json'
    with open(results_path, 'w') as f:
        json.dump(result, f, indent=2)

    print()
    print("=" * 60)
    print(f"v6 WALK-FORWARD WINDOW {window_str}")
    print("=" * 60)
    print(f"RMSE  test: {rmse_test:.4f}  (baseline mean: {rmse_mean:.4f}, zero: {rmse_zero:.4f})")
    print(f"Corr  test: {corr_test:+.4f}  (train: {corr_train:+.4f})")
    print(f"Dir   test: {dir_acc_test:.4f}  (baseline always-up: {dir_baseline:.4f})")
    print(f"Top feat:  {feat_imp[0][0]}  ({top_feat_pct*100:.1f}% of gain)")
    print(f"\nSaved: {model_path}")
    print(f"Saved: {results_path}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', required=True, help='Test window, e.g. 2025-06')
    args = parser.parse_args()
    df = load_dataset()
    train_window(df, args.window)


if __name__ == "__main__":
    main()
