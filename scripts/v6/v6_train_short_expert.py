"""
v6_train_short_expert.py — Train a SHORT-only expert model.

Hypothesis: the joint model (predict fwd_ret_3 on ALL rows) is bull-biased
because 2025 is a bull market. When it predicts < -0.30%, those are weak
bearish signals that get reverted by the bull regime.

Solution: train a SEPARATE model that ONLY sees rows where fwd_ret_3 < 0.
This model learns specifically what causes drops, not "what causes moves".

Method:
  - Filter training set to rows where fwd_ret_3 < 0 (drops only)
  - Apply sample_weight = 2.0 for BEAR_2022 rows (so bear regime has more influence)
  - Label is still fwd_ret_3, but now all labels are negative
  - Walk-forward: same 5 windows as LONG model (2025-04 to 2025-10)
  - At inference: predict on ALL rows; if pred < -threshold → SHORT signal

Why this should work:
  - The SHORT model only needs to RANK drops (which drop is bigger?)
  - It doesn't need to learn "is this a drop or a pump?" — the joint model
    already does that badly because of bull bias
  - Sample weighting on BEAR_2022 gives it real bear examples

Tradeoff:
  - Train set shrinks (~50% of rows have fwd_ret_3 < 0)
  - But it's still ~500K rows per window, plenty for LightGBM
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_squared_error, mean_absolute_error

LOG = logging.getLogger("v6_short_expert")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH = '/home/z/my-project/data/ppmt.db'
OUT_DIR = Path('/home/z/my-project/data/v6_models/short_expert')
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
LABEL = "fwd_ret_3"

BEAR_WINDOW_WEIGHT = 2.0  # 2x weight on BEAR_2022 rows


def load_dataset():
    print(f"Loading all features from DB...", flush=True)
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    feat_cols = ", ".join([f"json_extract(features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES])
    sql = f"""
        SELECT ts, window, symbol, {LABEL}, {feat_cols}
        FROM feature_observations_v6
        WHERE {LABEL} IS NOT NULL
    """
    df = pd.read_sql_query(sql, conn)
    conn.close()
    for f in FEATURE_NAMES + [LABEL]:
        df[f] = pd.to_numeric(df[f], errors='coerce').replace([np.inf, -np.inf], 0).fillna(0)
    df['ts'] = pd.to_datetime(df['ts'], unit='s', utc=True)
    print(f"  loaded {len(df):,} rows in {time.time()-t0:.1f}s", flush=True)
    return df


def train_window_short_expert(df, window_str: str):
    """Train SHORT-expert model for one walk-forward window.
    Train: all data before window_str, FILTERED to rows where fwd_ret_3 < 0.
    Test:  ALL rows in window_str (we'll filter at inference time).
    Sample weight: 2x for BEAR_2022 rows.
    """
    yr, mo = window_str.split('-')
    yr, mo = int(yr), int(mo)
    cutoff = pd.Timestamp(year=yr, month=mo, day=1, tz='UTC')

    train_full = df[df['ts'] < cutoff].copy()
    test_df    = df[(df['ts'].dt.year == yr) & (df['ts'].dt.month == mo)].copy()

    # FILTER: SHORT expert only sees drops in training
    train_drops_only = train_full[train_full[LABEL] < 0].copy()
    print(f"\n--- SHORT-expert window {window_str} ---")
    print(f"  full train rows: {len(train_full):,}")
    print(f"  drops-only train rows: {len(train_drops_only):,} ({len(train_drops_only)/len(train_full)*100:.1f}%)")
    print(f"  test rows: {len(test_df):,}")

    # Sample weights: 2x for BEAR_2022
    sample_weight = np.where(
        train_drops_only['window'] == 'BEAR_2022',
        BEAR_WINDOW_WEIGHT, 1.0
    )
    n_bear = int((train_drops_only['window'] == 'BEAR_2022').sum())
    print(f"  BEAR_2022 rows in drops-only train: {n_bear:,} (weighted as {n_bear*BEAR_WINDOW_WEIGHT:,})")

    X_train = train_drops_only[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_drops_only[LABEL].values.astype(np.float32)
    X_test  = test_df[FEATURE_NAMES].values.astype(np.float32)
    y_test  = test_df[LABEL].values.astype(np.float32)

    # 10% val (also drops-only, weighted)
    rng = np.random.default_rng(seed=42)
    n_val = int(len(X_train) * 0.1)
    val_idx = rng.choice(len(X_train), size=n_val, replace=False)
    val_mask = np.zeros(len(X_train), dtype=bool)
    val_mask[val_idx] = True
    X_val, y_val, w_val = X_train[val_mask], y_train[val_mask], sample_weight[val_mask]
    X_tr,  y_tr,  w_tr  = X_train[~val_mask], y_train[~val_mask], sample_weight[~val_mask]

    dtrain = lgb.Dataset(X_tr, label=y_tr, weight=w_tr, feature_name=FEATURE_NAMES)
    dval   = lgb.Dataset(X_val, label=y_val, weight=w_val, feature_name=FEATURE_NAMES, reference=dtrain)
    params = {
        'objective': 'regression', 'metric': ['rmse', 'mae'],
        'num_leaves': 31, 'learning_rate': 0.05,
        'feature_fraction': 0.85, 'bagging_fraction': 0.85, 'bagging_freq': 5,
        'min_data_in_leaf': 200, 'lambda_l2': 1.0, 'verbosity': -1, 'seed': 42,
    }
    t0 = time.time()
    model = lgb.train(
        params, dtrain, num_boost_round=300,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(0)],
    )
    print(f"  trained in {time.time()-t0:.1f}s, best_iter={model.best_iteration}", flush=True)

    # Predict on test (ALL rows — we filter at inference)
    pred_test = model.predict(X_test, num_iteration=model.best_iteration)

    # Metrics on ALL test rows (the model will be used to rank for SHORT signals)
    rmse_test = float(np.sqrt(mean_squared_error(y_test, pred_test)))
    try: corr_test = float(np.corrcoef(y_test, pred_test)[0, 1])
    except Exception: corr_test = 0.0

    # SHORT-specific metrics: only rows where pred < -threshold (the SHORT signals we'll actually trade)
    threshold = 0.30
    short_mask = pred_test < -threshold
    n_short = int(short_mask.sum())
    if n_short > 0:
        actuals_short = y_test[short_mask]
        # PnL = -actuals - cost (we profit when actual is negative)
        net_short = -actuals_short - 0.14
        wins_s = float(net_short[net_short > 0].sum())
        losses_s = float(-net_short[net_short < 0].sum())
        pf_short = wins_s / losses_s if losses_s > 0 else 99.0
        wr_short = float((net_short > 0).mean())
        avg_pnl_short = float(net_short.mean())
        tot_dollars_short = float(net_short.sum() / 100 * 700)
    else:
        pf_short = wr_short = avg_pnl_short = tot_dollars_short = 0.0

    # Anti-leakage: feature importance
    importance = model.feature_importance(importance_type='gain')
    feat_imp = sorted(zip(FEATURE_NAMES, importance), key=lambda x: -x[1])
    total = max(float(importance.sum()), 1.0)
    top_feat_pct = feat_imp[0][1] / total

    result = {
        'window': window_str,
        'model_type': 'short_expert',
        'n_train_full': len(train_full),
        'n_train_drops_only': len(train_drops_only),
        'n_train_bear_2022': n_bear,
        'n_test': len(X_test),
        'best_iteration': int(model.best_iteration) if model.best_iteration else 300,
        'rmse_test_all': rmse_test,
        'corr_test_all': corr_test,
        'short_thr_030': {
            'n_signals': n_short, 'wr': float(wr_short), 'pf': float(pf_short),
            'avg_pnl_pct': float(avg_pnl_short), 'tot_dollars': float(tot_dollars_short),
        },
        'top_feat_pct': float(top_feat_pct), 'top_feat_name': feat_imp[0][0],
        'top_20_features': [{'name': n, 'gain': float(g), 'pct': float(g/total*100)} for n, g in feat_imp[:20]],
        'guards': {
            'top_feat_under_30pct': bool(top_feat_pct < 0.30),
        },
    }
    model_path = OUT_DIR / f'v6_short_expert_{window_str}.txt'
    model.save_model(str(model_path))
    result['model_path'] = str(model_path)
    return result, model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', required=True, help='Test window, e.g. 2025-06')
    args = parser.parse_args()

    print("=" * 110)
    print(f"v6 SHORT EXPERT — train on drops only (fwd_ret_3 < 0), weight BEAR_2022 x{BEAR_WINDOW_WEIGHT}")
    print("=" * 110)

    df = load_dataset()
    result, _ = train_window_short_expert(df, args.window)

    print()
    print("=" * 60)
    print(f"v6 SHORT-EXPERT WINDOW {args.window}")
    print("=" * 60)
    print(f"Train drops-only: {result['n_train_drops_only']:,} (of {result['n_train_full']:,} full)")
    print(f"  BEAR_2022 in train: {result['n_train_bear_2022']:,} (weighted x{BEAR_WINDOW_WEIGHT})")
    print(f"Test rows: {result['n_test']:,}")
    print(f"RMSE test (all rows): {result['rmse_test_all']:.4f}")
    print(f"Corr test (all rows): {result['corr_test_all']:+.4f}")
    s = result['short_thr_030']
    print(f"SHORT @ thr=-0.30%: {s['n_signals']} signals, WR={s['wr']:.3f}, PF={s['pf']:.2f}, "
          f"tot=${s['tot_dollars']:+.2f}")
    print(f"Top feat: {result['top_feat_name']} ({result['top_feat_pct']*100:.1f}% of gain)")
    print(f"Guard top_feat<30%: {result['guards']['top_feat_under_30pct']}")

    results_path = OUT_DIR / f'v6_short_expert_{args.window}_results.json'
    with open(results_path, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved: {result['model_path']}")
    print(f"Saved: {results_path}")


if __name__ == '__main__':
    main()
