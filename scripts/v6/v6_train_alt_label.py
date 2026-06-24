"""
v6_train_alt_label.py — Test alternative regression labels fwd_ret_6 (30m) and fwd_ret_12 (60m).

Hypothesis: 15m forward (fwd_ret_3) is noisy. Longer horizons may carry
more signal because:
  - 30m / 60m smooths out 1-bar noise
  - Trends persist longer than 15m
  - Microstructure features (vol_delta_3, wick_imbalance_3) have more time
    to play out

Tradeoff:
  - Longer hold = more capital committed per trade
  - Longer hold = more risk per trade (vol scales with sqrt(time))
  - Exit at +30m / +60m instead of +15m

Method:
  - Pilot split: train on all data before 2025-10, test on 2025-10
  - Train 3 models: fwd_ret_3 (baseline), fwd_ret_6, fwd_ret_12
  - Compare: RMSE, corr, dir_acc, top_feat_pct, anti-leakage guards
  - If alt label shows higher corr/dir_acc → retrain full walk-forward
  - If not → keep fwd_ret_3

For each label, we ALSO compute the backtest at thr=0.30% to see if the
directional edge translates to $ PnL.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_squared_error, mean_absolute_error

LOG = logging.getLogger("v6_altlabel")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH = '/home/z/my-project/data/ppmt.db'
OUT_DIR = Path('/home/z/my-project/data/v6_models/alt_label')
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

LABELS = ['fwd_ret_3', 'fwd_ret_6', 'fwd_ret_12']
LABEL_TO_HORIZON_MIN = {'fwd_ret_3': 15, 'fwd_ret_6': 30, 'fwd_ret_12': 60}

# Pilot split: test on 2025-10, train on everything before
TEST_WINDOW = '2025-10'

ROUND_TRIP_COST_PCT = 0.14
THRESHOLD = 0.30
POSITION_NOTIONAL = 700.0


def load_dataset():
    print(f"Loading all features from DB (one-shot)...", flush=True)
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    feat_cols = ", ".join([f"json_extract(features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES])
    label_cols = ", ".join(LABELS)
    sql = f"""
        SELECT ts, symbol, {label_cols}, {feat_cols}
        FROM feature_observations_v6
        WHERE fwd_ret_3 IS NOT NULL AND fwd_ret_6 IS NOT NULL AND fwd_ret_12 IS NOT NULL
    """
    df = pd.read_sql_query(sql, conn)
    conn.close()
    for f in FEATURE_NAMES + LABELS:
        df[f] = pd.to_numeric(df[f], errors='coerce').replace([np.inf, -np.inf], 0).fillna(0)
    df['ts'] = pd.to_datetime(df['ts'], unit='s', utc=True)
    print(f"  loaded {len(df):,} rows in {time.time()-t0:.1f}s", flush=True)
    return df


def train_pilot(df, label: str):
    yr, mo = int(TEST_WINDOW[:4]), int(TEST_WINDOW[5:7])
    cutoff = pd.Timestamp(year=yr, month=mo, day=1, tz='UTC')
    train_df = df[df['ts'] < cutoff].copy()
    test_df  = df[(df['ts'].dt.year == yr) & (df['ts'].dt.month == mo)].copy()
    print(f"\n--- Training pilot for label={label} (horizon={LABEL_TO_HORIZON_MIN[label]}m) ---")
    print(f"  train: {len(train_df):,}  test: {len(test_df):,}", flush=True)

    X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_df[label].values.astype(np.float32)
    X_test  = test_df[FEATURE_NAMES].values.astype(np.float32)
    y_test  = test_df[label].values.astype(np.float32)

    # 10% val
    rng = np.random.default_rng(seed=42)
    n_val = int(len(X_train) * 0.1)
    val_idx = rng.choice(len(X_train), size=n_val, replace=False)
    val_mask = np.zeros(len(X_train), dtype=bool)
    val_mask[val_idx] = True
    X_val, y_val = X_train[val_mask], y_train[val_mask]
    X_tr,  y_tr  = X_train[~val_mask], y_train[~val_mask]

    dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES)
    dval   = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, reference=dtrain)
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

    pred_test  = model.predict(X_test, num_iteration=model.best_iteration)
    pred_train = model.predict(X_tr, num_iteration=model.best_iteration)

    rmse_test  = float(np.sqrt(mean_squared_error(y_test, pred_test)))
    rmse_train = float(np.sqrt(mean_squared_error(y_tr, pred_train)))
    mae_test   = float(mean_absolute_error(y_test, pred_test))
    try: corr_test  = float(np.corrcoef(y_test, pred_test)[0, 1])
    except Exception: corr_test = 0.0
    try: corr_train = float(np.corrcoef(y_tr, pred_train)[0, 1])
    except Exception: corr_train = 0.0
    dir_acc_test = float(((pred_test > 0) == (y_test > 0)).mean())
    rmse_mean = float(np.sqrt(mean_squared_error(y_test, np.full_like(y_test, y_tr.mean()))))
    rmse_zero = float(np.sqrt(mean_squared_error(y_test, np.zeros_like(y_test))))
    dir_baseline = float((y_test > 0).mean())

    importance = model.feature_importance(importance_type='gain')
    feat_imp = sorted(zip(FEATURE_NAMES, importance), key=lambda x: -x[1])
    total = max(float(importance.sum()), 1.0)
    top_feat_pct = feat_imp[0][1] / total

    # Backtest on test window at thr=0.30%
    entered_mask = pred_test > THRESHOLD
    n_trades = int(entered_mask.sum())
    if n_trades > 0:
        net = y_test[entered_mask] - ROUND_TRIP_COST_PCT
        wins = float(net[net > 0].sum())
        losses = float(-net[net < 0].sum())
        pf = wins / losses if losses > 0 else 99.0
        wr = float((net > 0).mean())
        avg_pnl = float(net.mean())
        tot_pnl_pct = float(net.sum())
        tot_dollars = tot_pnl_pct / 100 * POSITION_NOTIONAL
    else:
        pf = wr = avg_pnl = tot_pnl_pct = tot_dollars = 0.0

    # Anti-leakage guards
    guard_top_feat = top_feat_pct < 0.30  # < 30% of gain
    guard_train_corr = abs(corr_train) < 0.85

    result = {
        'label': label,
        'horizon_min': LABEL_TO_HORIZON_MIN[label],
        'test_window': TEST_WINDOW,
        'n_train': len(X_tr), 'n_val': len(X_val), 'n_test': len(X_test),
        'best_iteration': int(model.best_iteration) if model.best_iteration else 300,
        'rmse_test': rmse_test, 'rmse_train': rmse_train, 'mae_test': mae_test,
        'corr_test': corr_test, 'corr_train': corr_train,
        'dir_acc_test': dir_acc_test, 'dir_baseline': dir_baseline,
        'rmse_mean_baseline': rmse_mean, 'rmse_zero_baseline': rmse_zero,
        'label_mean': float(y_test.mean()), 'label_std': float(y_test.std()),
        'top_feat_pct': float(top_feat_pct), 'top_feat_name': feat_imp[0][0],
        'top_20_features': [{'name': n, 'gain': float(g), 'pct': float(g/total*100)} for n, g in feat_imp[:20]],
        'backtest_thr_030': {
            'n_trades': n_trades, 'wr': float(wr), 'pf': float(pf),
            'avg_pnl_pct': float(avg_pnl), 'tot_pnl_pct': float(tot_pnl_pct),
            'tot_dollars': float(tot_dollars),
        },
        'guards': {
            'top_feat_under_30pct': bool(guard_top_feat),
            'train_corr_under_0_85': bool(guard_train_corr),
        },
    }
    # Save model
    model_path = OUT_DIR / f'pilot_{label}.txt'
    model.save_model(str(model_path))
    result['model_path'] = str(model_path)
    return result, model


def main():
    print("=" * 110)
    print("v6 ALT-LABEL PILOT — fwd_ret_3 vs fwd_ret_6 vs fwd_ret_12")
    print("=" * 110)
    print(f"Test window: {TEST_WINDOW} (most recent)")
    print(f"Labels: {LABELS} (15m / 30m / 60m forward)")
    print(f"Backtest: thr={THRESHOLD}%, cost={ROUND_TRIP_COST_PCT}%, notional=${POSITION_NOTIONAL}")
    print()

    df = load_dataset()
    all_results = []
    for label in LABELS:
        r, _ = train_pilot(df, label)
        all_results.append(r)
        # Print summary
        print()
        print(f"  Label: {r['label']} ({r['horizon_min']}m horizon)")
        print(f"  RMSE test: {r['rmse_test']:.4f}  (mean baseline: {r['rmse_mean_baseline']:.4f})")
        print(f"  Corr  test: {r['corr_test']:+.4f}  (train: {r['corr_train']:+.4f})")
        print(f"  Dir   test: {r['dir_acc_test']:.4f}  (baseline always-up: {r['dir_baseline']:.4f})")
        print(f"  Top feat:  {r['top_feat_name']} ({r['top_feat_pct']*100:.1f}% of gain)")
        print(f"  Backtest @ thr=0.30%: trades={r['backtest_thr_030']['n_trades']}, "
              f"WR={r['backtest_thr_030']['wr']:.3f}, PF={r['backtest_thr_030']['pf']:.2f}, "
              f"tot=${r['backtest_thr_030']['tot_dollars']:+.2f}")
        print(f"  Guards: top_feat<30%={r['guards']['top_feat_under_30pct']}, "
              f"train_corr<0.85={r['guards']['train_corr_under_0_85']}")

    # Comparison
    print()
    print("=" * 110)
    print("COMPARISON TABLE")
    print("=" * 110)
    print(f"  {'label':12s} {'horizon':>8s} {'RMSE':>8s} {'Corr':>8s} {'Dir':>7s} {'DirBase':>8s} "
          f"{'top_feat%':>10s} {'trades':>7s} {'WR':>6s} {'PF':>6s} {'tot_$':>10s}")
    for r in all_results:
        bt = r['backtest_thr_030']
        print(f"  {r['label']:12s} {r['horizon_min']:>7d}m {r['rmse_test']:>8.4f} {r['corr_test']:>+8.4f} "
              f"{r['dir_acc_test']:>7.4f} {r['dir_baseline']:>8.4f} {r['top_feat_pct']*100:>9.1f}% "
              f"{bt['n_trades']:>7d} {bt['wr']:>6.3f} {bt['pf']:>6.2f} {bt['tot_dollars']:>+10.2f}")

    # Verdict
    print()
    print("=" * 110)
    print("VERDICT")
    print("=" * 110)
    best = max(all_results, key=lambda r: r['backtest_thr_030']['tot_dollars'])
    print(f"  Best label by $ PnL: {best['label']} ({best['horizon_min']}m horizon)")
    print(f"    tot_${best['backtest_thr_030']['tot_dollars']:+.2f}, "
          f"WR={best['backtest_thr_030']['wr']:.3f}, PF={best['backtest_thr_030']['pf']:.2f}")
    print(f"    corr_test={best['corr_test']:+.4f}, dir_acc={best['dir_acc_test']:.4f}")

    baseline = next(r for r in all_results if r['label'] == 'fwd_ret_3')
    if best['label'] != 'fwd_ret_3':
        delta_dollars = best['backtest_thr_030']['tot_dollars'] - baseline['backtest_thr_030']['tot_dollars']
        delta_corr = best['corr_test'] - baseline['corr_test']
        print(f"  vs fwd_ret_3 baseline: Δ${delta_dollars:+.2f}, Δcorr={delta_corr:+.4f}")
        print(f"  → RECOMMENDATION: switch to {best['label']} and re-run full walk-forward + filtered backtest")
    else:
        print(f"  → RECOMMENDATION: keep fwd_ret_3 (longer horizons do not help)")

    # Save
    out_path = OUT_DIR / 'pilot_alt_label_results.json'
    with open(out_path, 'w') as f:
        json.dump({
            'config': {
                'test_window': TEST_WINDOW,
                'labels': LABELS,
                'threshold': THRESHOLD,
                'round_trip_cost_pct': ROUND_TRIP_COST_PCT,
                'position_notional': POSITION_NOTIONAL,
            },
            'results': all_results,
        }, f, indent=2)
    print(f"\nSaved: {out_path}")


if __name__ == '__main__':
    main()
