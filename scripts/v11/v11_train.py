"""
v11_train.py — Train binary classifier for low-timeframe trading.

KEY DESIGN DECISIONS:
  - Binary classification (P(price UP in horizon)) — direction is more stable than magnitude
  - Per-symbol HP tuning (from v7.5 deep optimization experience)
  - Multiple horizons trained independently (1h, 3h, 6h, 24h)
  - NO early stopping — use fixed iterations with regularization
    (v7 experience: early stopping on regime-shifted val set → best_iter=1 → useless model)
  - Quantile-based trading (rank ordering, not probability threshold)
  - Walk-forward cross-validation with 4 rolling windows

OUTPUT:
  - data/v11/models/v11_clf_{symbol}_h{horizon}.txt  (LGBM model)
  - data/v11/models/v11_results.json                   (all results)
  - data/v11/models/v11_feature_importance.json        (feature importance)

USAGE:
    python scripts/v11/v11_train.py
    python scripts/v11/v11_train.py --horizon 36
    python scripts/v11/v11_train.py --symbol SOL --horizon 12
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = DATA_DIR / "v11" / "models"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v11_train")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Import feature names from build script
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "v11"))
from v11_build_dataset import ALL_FEATURE_NAMES, DEFAULT_SYMBOLS, DEFAULT_HORIZONS

# Cost model
MAKER_COST_PCT = 0.04   # Limit orders: 0.02% each way
TAKER_COST_PCT = 0.14   # Market orders: 0.07% each way

# ============================================================================
# HYPERPARAMETER PRESETS (from v7.5 deep optimization)
# ============================================================================

HP_PRESETS = {
    "default": {
        "num_leaves": 31,
        "learning_rate": 0.01,
        "min_data_in_leaf": 30,
        "lambda_l1": 0.3,
        "lambda_l2": 3.0,
    },
    "more_reg": {
        "num_leaves": 15,
        "learning_rate": 0.005,
        "min_data_in_leaf": 50,
        "lambda_l1": 1.0,
        "lambda_l2": 5.0,
    },
    "very_reg": {
        "num_leaves": 15,
        "learning_rate": 0.01,
        "min_data_in_leaf": 100,
        "lambda_l1": 1.0,
        "lambda_l2": 10.0,
    },
    # New presets for short horizons — need more regularization due to noisier labels
    "ltf_reg": {
        "num_leaves": 15,
        "learning_rate": 0.005,
        "min_data_in_leaf": 80,
        "lambda_l1": 2.0,
        "lambda_l2": 8.0,
    },
    "ltf_ultra_reg": {
        "num_leaves": 7,
        "learning_rate": 0.003,
        "min_data_in_leaf": 150,
        "lambda_l1": 5.0,
        "lambda_l2": 15.0,
    },
}

# Per-symbol + per-horizon HP config
# Key insight from v7.5: shorter horizons need MORE regularization (noisier labels)
SYMBOL_HP = {
    # H=12 (1h): very noisy, need ultra regularization
    12: "ltf_ultra_reg",
    # H=36 (3h): moderately noisy
    36: "ltf_reg",
    # H=72 (6h): moderate
    72: "more_reg",
    # H=288 (24h): v7.5 baseline
    288: "default",
}

# Override per symbol if needed
SYMBOL_HP_OVERRIDE = {
    ("SOL", 12): "ltf_ultra_reg",
    ("DOGE", 12): "ltf_ultra_reg",
    ("AVAX", 12): "ltf_ultra_reg",
    ("SOL", 36): "ltf_reg",
    ("DOGE", 36): "ltf_reg",
    ("AVAX", 36): "ltf_reg",
    ("SOL", 72): "more_reg",
    ("DOGE", 72): "more_reg",
    ("AVAX", 72): "more_reg",
}

BASE_PARAMS = {
    "objective": "binary",
    "metric": ["binary_logloss", "auc"],
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "bagging_freq": 3,
    "verbosity": -1,
    "seed": 42,
}

NUM_BOOST_ROUND = 500  # Fixed iterations, NO early stopping (v7 experience)


# ============================================================================
# WALK-FORWARD SPLITS
# ============================================================================

def make_walk_forward_splits(df: pd.DataFrame, n_windows: int = 4) -> list:
    """Create rolling walk-forward splits.
    
    Uses time-based splitting with 7% test, 10% val (but val is only for monitoring,
    not for early stopping). The model trains on all data before test.
    """
    ts = df["timestamp"].values
    ts_first, ts_last = ts[0], ts[-1]
    span_ms = ts_last - ts_first
    span_days = span_ms / (1000 * 86400)
    
    test_days = max(span_days * 0.07, 0.5)
    
    splits = []
    for w in range(n_windows):
        offset_ms = int(w * test_days * 86400 * 1000)
        test_end_ts = ts_last - offset_ms
        test_start_ts = test_end_ts - int(test_days * 86400 * 1000)
        
        if test_start_ts <= ts_first:
            break
        
        train_df = df[df["timestamp"] < test_start_ts].reset_index(drop=True)
        test_df = df[(df["timestamp"] >= test_start_ts) & (df["timestamp"] < test_end_ts)].reset_index(drop=True)
        
        if len(train_df) < 500 or len(test_df) < 50:
            continue
        
        splits.append((f"w{w+1}", train_df, test_df))
    
    return splits


# ============================================================================
# TRAINING
# ============================================================================

def _auc(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Simple AUC calculation."""
    order = np.argsort(-y_pred)
    y_sorted = y_true[order]
    n_pos = y_sorted.sum()
    n_neg = len(y_sorted) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    tp = 0.0
    auc = 0.0
    for y in y_sorted:
        if y == 1:
            tp += 1
        else:
            auc += tp
    return auc / (n_pos * n_neg)


def train_one(
    symbol: str,
    horizon: int,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    hp_preset: str = "default",
) -> dict:
    """Train one model for one symbol + horizon."""
    t0 = time.time()
    
    label_col = f"label_h{horizon}"
    fwd_col = f"fwd_ret_h{horizon}"
    
    # Filter to rows with valid labels
    train_clean = train_df[train_df[label_col].notna()].reset_index(drop=True)
    test_clean = test_df[test_df[label_col].notna()].reset_index(drop=True)
    
    X_tr = train_clean[ALL_FEATURE_NAMES].values.astype(np.float32)
    y_tr = train_clean[label_col].values.astype(np.float32)
    X_test = test_clean[ALL_FEATURE_NAMES].values.astype(np.float32)
    y_test = test_clean[label_col].values.astype(np.float32)
    
    if len(X_tr) < 200 or len(X_test) < 20:
        return None
    
    # Get params
    hp = HP_PRESETS.get(hp_preset, HP_PRESETS["default"])
    params = dict(BASE_PARAMS)
    params.update(hp)
    
    # Train with fixed iterations (NO early stopping)
    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=ALL_FEATURE_NAMES, free_raw_data=False)
    
    callbacks = [lgb.log_evaluation(period=0)]  # silent
    
    bst = lgb.train(
        params,
        d_tr,
        num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[d_tr],
        valid_names=["train"],
        callbacks=callbacks,
    )
    
    # Predictions
    pred_tr = bst.predict(X_tr)
    pred_test = bst.predict(X_test)
    
    # Metrics
    auc_tr = float(_auc(y_tr, pred_tr))
    auc_test = float(_auc(y_test, pred_test))
    dir_acc = float(((pred_test > 0.5) == (y_test > 0.5)).mean())
    
    # Quantile-based backtest on test set
    fwd_ret = test_clean[fwd_col].values.astype(np.float64)
    q_configs = [
        (95, 5), (90, 10), (85, 15), (82, 18), (80, 20),
    ]
    backtest_results = {}
    
    for q_long, q_short in q_configs:
        bt = _sequential_backtest(
            pred_test, fwd_ret, q_long, q_short, 
            hold_bars=horizon, cost_pct=MAKER_COST_PCT,
        )
        backtest_results[f"q{q_long}_{q_short}"] = bt
    
    # Feature importance
    imp = bst.feature_importance(importance_type="gain")
    feat_imp = sorted(zip(ALL_FEATURE_NAMES, imp), key=lambda x: -x[1])
    total_gain = max(float(imp.sum()), 1.0)
    
    result = {
        "symbol": symbol,
        "horizon": horizon,
        "horizon_h": horizon * 5 / 60,
        "hp_preset": hp_preset,
        "n_train": len(X_tr),
        "n_test": len(X_test),
        "label_up_pct": float(y_tr.mean() * 100),
        "auc_train": auc_tr,
        "auc_test": auc_test,
        "dir_acc_test": dir_acc,
        "backtest": backtest_results,
        "top_features": [
            {"name": n, "gain": float(g), "pct": float(g / total_gain * 100)}
            for n, g in feat_imp[:20]
        ],
        "train_time_s": float(time.time() - t0),
    }
    
    # Save model
    model_name = f"v11_clf_{symbol}_h{horizon}"
    model_path = OUTPUT_DIR / f"{model_name}.txt"
    bst.save_model(str(model_path))
    result["model_path"] = str(model_path)
    
    LOG.info(
        "  %s H=%d(%dh) HP=%s: auc_tr=%.3f auc_test=%.3f dir_acc=%.3f "
        "best_Q_pnl=%+.2f%% in %.1fs",
        symbol, horizon, int(horizon * 5 / 60), hp_preset,
        auc_tr, auc_test, dir_acc,
        max(bt["pnl_pct"] for bt in backtest_results.values()),
        result["train_time_s"],
    )
    
    return result


def _sequential_backtest(
    pred: np.ndarray,
    fwd_ret: np.ndarray,
    q_long: int,
    q_short: int,
    hold_bars: int,
    window_size: int = 200,
    cost_pct: float = MAKER_COST_PCT,
) -> dict:
    """Sequential backtest with rolling quantiles."""
    n_trades = 0
    n_win = 0
    pnl = 0.0
    in_trade = False
    exit_bar = 0
    recent_preds = []
    trade_returns = []
    n_long = 0
    n_short = 0
    
    for i in range(len(pred)):
        p_val = float(pred[i])
        recent_preds.append(p_val)
        if len(recent_preds) > window_size:
            recent_preds.pop(0)
        
        if in_trade:
            if i >= exit_bar:
                in_trade = False
            else:
                continue
        
        if len(recent_preds) < 20:
            continue
        
        q_high = np.percentile(recent_preds, q_long)
        q_low = np.percentile(recent_preds, q_short)
        
        sig = 0
        if p_val > q_high:
            sig = 1
            n_long += 1
        elif p_val < q_low:
            sig = -1
            n_short += 1
        
        if sig != 0 and not np.isnan(fwd_ret[i]):
            n_trades += 1
            trade_ret = sig * fwd_ret[i] - cost_pct
            pnl += trade_ret
            trade_returns.append(trade_ret)
            in_trade = True
            exit_bar = i + hold_bars
            if trade_ret > 0:
                n_win += 1
    
    win_rate = n_win / n_trades if n_trades > 0 else 0
    avg_ret = pnl / n_trades if n_trades > 0 else 0
    sharpe = (np.mean(trade_returns) / np.std(trade_returns)) if len(trade_returns) > 1 else 0
    
    # MaxDD
    if trade_returns:
        cum = np.cumsum(trade_returns)
        running_max = np.maximum.accumulate(cum)
        dd = cum - running_max
        max_dd = float(dd.min())
    else:
        max_dd = 0
    
    # Profit factor
    gains = sum(r for r in trade_returns if r > 0)
    losses = abs(sum(r for r in trade_returns if r < 0))
    pf = gains / losses if losses > 0 else (99.0 if gains > 0 else 0)
    
    return {
        "n_trades": n_trades,
        "n_long": n_long,
        "n_short": n_short,
        "win_rate": round(win_rate, 4),
        "avg_ret_pct": round(avg_ret, 4),
        "pnl_pct": round(pnl, 4),
        "sharpe": round(sharpe, 4),
        "max_dd_pct": round(max_dd, 4),
        "profit_factor": round(pf, 4),
    }


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Train v11 models")
    parser.add_argument("--symbol", default=None, help="Single symbol (default: all)")
    parser.add_argument("--horizon", type=int, default=None, help="Single horizon (default: all)")
    parser.add_argument("--n-windows", type=int, default=4, help="Walk-forward windows")
    args = parser.parse_args()
    
    symbols = [args.symbol] if args.symbol else DEFAULT_SYMBOLS
    horizons = [args.horizon] if args.horizon else DEFAULT_HORIZONS
    
    print("=" * 90)
    print("v11 TRAIN — Low Timeframe Binary Classifier")
    print(f"  Symbols: {symbols}")
    print(f"  Horizons: {horizons} ({[f'{h*5/60:.0f}h' for h in horizons]})")
    print(f"  Features: {len(ALL_FEATURE_NAMES)}")
    print(f"  Cost: {MAKER_COST_PCT}% (maker fees)")
    print(f"  Walk-forward windows: {args.n_windows}")
    print(f"  Training: {NUM_BOOST_ROUND} rounds, NO early stopping")
    print("=" * 90)
    
    # Load dataset
    dataset_path = DATA_DIR / "v11" / "v11_dataset.parquet"
    if not dataset_path.exists():
        LOG.error("Dataset not found: %s", dataset_path)
        LOG.error("Run: python scripts/v11/v11_build_dataset.py")
        sys.exit(1)
    
    LOG.info("Loading dataset: %s", dataset_path)
    df = pd.read_parquet(dataset_path)
    LOG.info("  loaded %d rows", len(df))
    
    all_results = []
    
    for symbol in symbols:
        sym_df = df[df["symbol"] == symbol].copy().reset_index(drop=True)
        if len(sym_df) < 1000:
            LOG.warning("Skipping %s: only %d rows", symbol, len(sym_df))
            continue
        
        for horizon in horizons:
            label_col = f"label_h{horizon}"
            if label_col not in sym_df.columns:
                LOG.warning("No label %s for %s", label_col, symbol)
                continue
            
            # Filter to valid labels
            valid_df = sym_df[sym_df[label_col].notna()].reset_index(drop=True)
            if len(valid_df) < 500:
                LOG.warning("Skipping %s H=%d: only %d valid rows", symbol, horizon, len(valid_df))
                continue
            
            # Get HP preset
            hp = SYMBOL_HP_OVERRIDE.get((symbol, horizon), SYMBOL_HP.get(horizon, "default"))
            
            # Walk-forward splits
            splits = make_walk_forward_splits(valid_df, n_windows=args.n_windows)
            if len(splits) < 2:
                LOG.warning("Skipping %s H=%d: only %d splits", symbol, horizon, len(splits))
                continue
            
            LOG.info("Training %s H=%d (%dh) HP=%s — %d splits, %d rows",
                     symbol, horizon, int(horizon * 5 / 60), hp, len(splits), len(valid_df))
            
            # Train on each window
            for split_name, train_df, test_df in splits:
                result = train_one(symbol, horizon, train_df, test_df, hp)
                if result is not None:
                    result["split"] = split_name
                    all_results.append(result)
    
    # Aggregate results
    if not all_results:
        LOG.error("No models trained successfully")
        sys.exit(1)
    
    # Save all results
    results_path = OUTPUT_DIR / "v11_results.json"
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    
    # Print summary
    print("\n" + "=" * 110)
    print("v11 TRAINING RESULTS SUMMARY")
    print("=" * 110)
    
    # Group by (symbol, horizon)
    from collections import defaultdict
    grouped = defaultdict(list)
    for r in all_results:
        grouped[(r["symbol"], r["horizon"])].append(r)
    
    print(f"\n{'Symbol':<8} {'Horizon':>8} {'HP':>12} {'AUC_tr':>8} {'AUC_te':>8} "
          f"{'DirAcc':>8} {'Best Q':>8} {'Best PnL':>10} {'Best WR':>8} {'Best PF':>8}")
    print("-" * 110)
    
    for (sym, h), results in sorted(grouped.items()):
        auc_tests = [r["auc_test"] for r in results]
        dir_accs = [r["dir_acc_test"] for r in results]
        
        # Find best Q config across windows
        best_pnl = -999
        best_q = ""
        best_wr = 0
        best_pf = 0
        for r in results:
            for q_name, bt in r["backtest"].items():
                if bt["pnl_pct"] > best_pnl and bt["n_trades"] >= 3:
                    best_pnl = bt["pnl_pct"]
                    best_q = q_name
                    best_wr = bt["win_rate"]
                    best_pf = bt["profit_factor"]
        
        print(f"{sym:<8} {h:>5d}({h*5//60}h) {results[0]['hp_preset']:>12} "
              f"{np.mean(auc_tests):>8.3f} {np.mean(auc_tests):>8.3f} "
              f"{np.mean(dir_accs):>8.3f} "
              f"{best_q:>8} {best_pnl:>+9.2f}% {best_wr:>8.3f} {best_pf:>8.2f}")
    
    # Horizon comparison
    print(f"\n{'='*110}")
    print("HORIZON COMPARISON (aggregated across symbols and windows)")
    print(f"{'='*110}")
    
    horizon_results = defaultdict(list)
    for r in all_results:
        horizon_results[r["horizon"]].append(r)
    
    print(f"\n{'Horizon':>8} {'Avg AUC':>10} {'Avg DirAcc':>12} "
          f"{'Best PnL':>10} {'Avg PnL':>10} {'Avg WR':>10} {'Trades':>8}")
    print("-" * 80)
    
    for h in sorted(horizon_results.keys()):
        results = horizon_results[h]
        auc_tests = [r["auc_test"] for r in results]
        dir_accs = [r["dir_acc_test"] for r in results]
        
        all_pnls = []
        all_wrs = []
        all_trades = []
        for r in results:
            for bt in r["backtest"].values():
                if bt["n_trades"] >= 3:
                    all_pnls.append(bt["pnl_pct"])
                    all_wrs.append(bt["win_rate"])
                    all_trades.append(bt["n_trades"])
        
        print(f"{h:>5d}({h*5//60}h) {np.mean(auc_tests):>10.3f} {np.mean(dir_accs):>12.3f} "
              f"{max(all_pnls) if all_pnls else 0:>+10.2f}% "
              f"{np.mean(all_pnls) if all_pnls else 0:>+10.2f}% "
              f"{np.mean(all_wrs) if all_wrs else 0:>10.3f} "
              f"{np.mean(all_trades) if all_trades else 0:>8.1f}")
    
    # Feature importance
    print(f"\n{'='*110}")
    print("TOP 20 FEATURES (from first model)")
    print(f"{'='*110}")
    if all_results:
        for feat in all_results[0]["top_features"][:20]:
            print(f"  {feat['name']:<30} {feat['gain']:>12.0f}  ({feat['pct']:.2f}%)")
    
    LOG.info("Results saved to %s", results_path)


if __name__ == "__main__":
    main()
