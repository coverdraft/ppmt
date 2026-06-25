"""
v6_train_short_expert_v2_15m.py — SHORT expert on 15m TF (Fase 3 experiment).

CONTEXT:
  - SHORT-expert v2 at 5m FAILED: -$5,061 across 5 walk-forward windows.
  - Fase 1 (more bear data) made it WORSE: -$6,510.
  - Root cause: 5m timeframe is too noisy for SHORT signals. Crypto drops
    are fast (minutes) and 5m bars can't separate signal from noise.

HYPOTHESIS (Fase 3):
  - At 15m TF, noise is reduced by ~sqrt(3) = 1.7x (3x more data per bar).
  - The model's edge on drops should be clearer at 15m.
  - Already proven for LONG: STEP 8 showed 15m beats 30m/60m for LONG signal.
  - Same wall-clock forward horizon (15m): fwd_ret_1 at 15m TF = fwd_ret_3 at 5m TF.

DESIGN (mirrors v6_train_short_expert_v2.py with TF swap):
  - Table: feature_observations_v6_15m (NEW, separate from 5m)
  - Label: fwd_ret_1 (= 15m forward, matches 5m's fwd_ret_3 wall-clock)
  - Sample weights: same as v2 — 2x drops, 2x BEAR_2022 (compound 4x)
  - LGBM params: same as v2 (num_leaves=31, lr=0.05, n_est=300, early_stop=30)
  - Anti-leakage guards: top_feat < 30% of gain

USAGE:
    python scripts/v6/v6_train_short_expert_v2_15m.py --window 2025-04
    for w in 2025-04 2025-05 2025-06 2025-09 2025-10; do
        python scripts/v6/v6_train_short_expert_v2_15m.py --window $w
    done
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
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_squared_error

LOG = logging.getLogger("v6_short_expert_v2_15m")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH = '/home/z/my-project/data/ppmt.db'
OUT_DIR = Path('/home/z/my-project/data/v6_models/short_expert_v2_15m')
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

# At 15m TF, primary label is fwd_ret_1 (= 15m forward, matches 5m's fwd_ret_3)
LABEL = "fwd_ret_1"
TABLE = "feature_observations_v6_15m"

DROP_WEIGHT = 2.0
BEAR_WEIGHT = 2.0

# Walk-forward windows (same as 5m SHORT expert v2)
WF_WINDOWS = ["2025-04", "2025-05", "2025-06", "2025-09", "2025-10"]


def load_dataset():
    print(f"Loading all 15m features from DB (table={TABLE})...", flush=True)
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    feat_cols = ", ".join([f"json_extract(features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES])
    sql = f"""
        SELECT ts, window, symbol, {LABEL}, {feat_cols}
        FROM {TABLE}
        WHERE {LABEL} IS NOT NULL
    """
    df = pd.read_sql_query(sql, conn)
    conn.close()
    for f in FEATURE_NAMES + [LABEL]:
        df[f] = pd.to_numeric(df[f], errors='coerce').replace([np.inf, -np.inf], 0).fillna(0)
    df['ts'] = pd.to_datetime(df['ts'], unit='s', utc=True)
    print(f"  loaded {len(df):,} rows in {time.time()-t0:.1f}s", flush=True)

    # Sanity: print label distribution
    n = len(df)
    n_drop = int((df[LABEL] < 0).sum())
    n_bear = int((df['window'] == 'BEAR_2022').sum())
    n_bear_drop = int(((df[LABEL] < 0) & (df['window'] == 'BEAR_2022')).sum())
    print(f"  15m label stats: n={n:,}  drops={n_drop:,} ({100*n_drop/n:.1f}%)  "
          f"BEAR_2022={n_bear:,}  BEAR+drops={n_bear_drop:,}", flush=True)
    return df


def train_window_short_expert_v2_15m(df, window_str: str):
    yr, mo = window_str.split('-')
    yr, mo = int(yr), int(mo)
    cutoff = pd.Timestamp(year=yr, month=mo, day=1, tz='UTC')

    train_df = df[df['ts'] < cutoff].copy()
    test_df  = df[(df['ts'].dt.year == yr) & (df['ts'].dt.month == mo)].copy()

    # Sample weights: 2x for drops, 2x for BEAR_2022 (compound: BEAR drop = 4x)
    w = np.ones(len(train_df), dtype=np.float32)
    w[train_df[LABEL].values < 0] *= DROP_WEIGHT
    w[train_df['window'].values == 'BEAR_2022'] *= BEAR_WEIGHT

    n_drop = int((train_df[LABEL] < 0).sum())
    n_bear = int((train_df['window'] == 'BEAR_2022').sum())
    n_bear_drop = int(((train_df[LABEL] < 0) & (train_df['window'] == 'BEAR_2022')).sum())
    print(f"\n--- SHORT-expert-v2-15m window {window_str} ---")
    print(f"  train: {len(train_df):,}  test: {len(test_df):,}")
    print(f"  drops: {n_drop:,} (weight x{DROP_WEIGHT})  BEAR_2022: {n_bear:,} (weight x{BEAR_WEIGHT})")
    print(f"  BEAR+drops (weight x{DROP_WEIGHT*BEAR_WEIGHT}): {n_bear_drop:,}")
    print(f"  effective sample size: {w.sum():,.0f}")

    X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_df[LABEL].values.astype(np.float32)
    X_test  = test_df[FEATURE_NAMES].values.astype(np.float32)
    y_test  = test_df[LABEL].values.astype(np.float32)

    # 10% val with same weighting scheme
    rng = np.random.default_rng(seed=42)
    n_val = int(len(X_train) * 0.1)
    val_idx = rng.choice(len(X_train), size=n_val, replace=False)
    val_mask = np.zeros(len(X_train), dtype=bool)
    val_mask[val_idx] = True
    X_val, y_val, w_val = X_train[val_mask], y_train[val_mask], w[val_mask]
    X_tr,  y_tr,  w_tr  = X_train[~val_mask], y_train[~val_mask], w[~val_mask]

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

    pred_test = model.predict(X_test, num_iteration=model.best_iteration)
    rmse_test = float(np.sqrt(mean_squared_error(y_test, pred_test)))
    try: corr_test = float(np.corrcoef(y_test, pred_test)[0, 1])
    except Exception: corr_test = 0.0
    dir_acc = float(((pred_test > 0) == (y_test > 0)).mean())

    # SHORT signals: pred < -threshold
    # At 15m TF, std is ~0.58% (vs 0.34% at 5m). Scale threshold proportionally
    # so we get a comparable signal density. 5m used 0.30% — at 15m we use 0.50%
    # (still captures meaningful moves, just at the 15m-noise scale).
    # Also test the original 0.30% for direct apples-to-apples comparison.
    results_by_thr = {}
    for threshold in [0.30, 0.50]:
        short_mask = pred_test < -threshold
        n_short = int(short_mask.sum())
        if n_short > 0:
            actuals_short = y_test[short_mask]
            # SHORT PnL: -actuals - 0.14% cost (round-trip)
            net_short = -actuals_short - 0.14
            wins_s = float(net_short[net_short > 0].sum())
            losses_s = float(-net_short[net_short < 0].sum())
            pf_short = wins_s / losses_s if losses_s > 0 else 99.0
            wr_short = float((net_short > 0).mean())
            avg_pnl_short = float(net_short.mean())
            tot_dollars_short = float(net_short.sum() / 100 * 700)
        else:
            pf_short = wr_short = avg_pnl_short = tot_dollars_short = 0.0
        results_by_thr[f"thr_{threshold:.2f}"] = {
            'n_signals': n_short, 'wr': float(wr_short), 'pf': float(pf_short),
            'avg_pnl_pct': float(avg_pnl_short), 'tot_dollars': float(tot_dollars_short),
        }

    # Also check LONG side (sanity: weighting shouldn't break long)
    for threshold in [0.30, 0.50]:
        long_mask = pred_test > threshold
        n_long = int(long_mask.sum())
        if n_long > 0:
            net_long = y_test[long_mask] - 0.14
            wr_long = float((net_long > 0).mean())
            tot_dollars_long = float(net_long.sum() / 100 * 700)
            pf_long = float(net_long[net_long > 0].sum() / max(-net_long[net_long < 0].sum(), 1e-9))
        else:
            wr_long = tot_dollars_long = pf_long = 0.0
        results_by_thr[f"long_thr_{threshold:.2f}"] = {
            'n_signals': n_long, 'wr': float(wr_long), 'pf': float(pf_long),
            'tot_dollars': float(tot_dollars_long),
        }

    importance = model.feature_importance(importance_type='gain')
    feat_imp = sorted(zip(FEATURE_NAMES, importance), key=lambda x: -x[1])
    total = max(float(importance.sum()), 1.0)
    top_feat_pct = feat_imp[0][1] / total

    result = {
        'window': window_str, 'model_type': 'short_expert_v2_15m',
        'timeframe': '15m', 'label': LABEL,
        'n_train': len(X_tr), 'n_val': len(X_val), 'n_test': len(X_test),
        'n_drops_train': n_drop, 'n_bear_train': n_bear, 'n_bear_drops_train': n_bear_drop,
        'effective_sample_size': float(w.sum()),
        'best_iteration': int(model.best_iteration) if model.best_iteration else 300,
        'rmse_test': rmse_test, 'corr_test': corr_test, 'dir_acc_test': dir_acc,
        'short': results_by_thr,
        'top_feat_pct': float(top_feat_pct), 'top_feat_name': feat_imp[0][0],
        'top_20_features': [{'name': n, 'gain': float(g), 'pct': float(g/total*100)} for n, g in feat_imp[:20]],
        'guards': {'top_feat_under_30pct': bool(top_feat_pct < 0.30)},
    }
    model_path = OUT_DIR / f'v6_short_expert_v2_15m_{window_str}.txt'
    model.save_model(str(model_path))
    result['model_path'] = str(model_path)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', required=True, choices=WF_WINDOWS)
    args = parser.parse_args()

    print("=" * 110)
    print(f"v6 SHORT EXPERT v2 — 15m TF — sample_weight (drops x{DROP_WEIGHT}, BEAR_2022 x{BEAR_WEIGHT})")
    print(f"  label={LABEL}  table={TABLE}")
    print("=" * 110)

    df = load_dataset()
    result = train_window_short_expert_v2_15m(df, args.window)

    print()
    print("=" * 60)
    print(f"v6 SHORT-EXPERT-V2-15m WINDOW {args.window}")
    print("=" * 60)
    print(f"Train: {result['n_train']:,}  Test: {result['n_test']:,}")
    print(f"  drops: {result['n_drops_train']:,}  BEAR_2022: {result['n_bear_train']:,}  BEAR+drops: {result['n_bear_drops_train']:,}")
    print(f"RMSE test: {result['rmse_test']:.4f}  Corr: {result['corr_test']:+.4f}  Dir: {result['dir_acc_test']:.4f}")
    for thr_key, s in result['short'].items():
        if thr_key.startswith('long'):
            continue
        print(f"  SHORT {thr_key}: {s['n_signals']:>5} signals, WR={s['wr']:.3f}, PF={s['pf']:.2f}, tot=${s['tot_dollars']:+.2f}")
    for thr_key, l in result['short'].items():
        if not thr_key.startswith('long'):
            continue
        print(f"  LONG  {thr_key}: {l['n_signals']:>5} signals, WR={l['wr']:.3f}, PF={l['pf']:.2f}, tot=${l['tot_dollars']:+.2f} (sanity)")
    print(f"Top feat: {result['top_feat_name']} ({result['top_feat_pct']*100:.1f}%)  guard: {result['guards']['top_feat_under_30pct']}")

    results_path = OUT_DIR / f'v6_short_expert_v2_15m_{args.window}_results.json'
    with open(results_path, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved: {result['model_path']}")
    print(f"Saved: {results_path}")


if __name__ == '__main__':
    main()
