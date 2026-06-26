"""
v12_train_final.py — Train V12 models with cost-aware labels.

KEY DIFFERENCES vs V11:
  1. Cost-aware labels: only "win" if return > 0.08% (2x maker fee)
  2. More selective quantile configs (Q92/8, Q95/5)
  3. Comprehensive evaluation comparing V11 vs V12 labels

USAGE:
    python scripts/v12/v12_train_final.py
    python scripts/v12/v12_train_final.py --symbol DOGE --horizon 12
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
V12_DIR = DATA_DIR / "v12"
OUTPUT_DIR = V12_DIR / "models"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v12_train_final")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Load feature names
with open(V12_DIR / "feature_columns.json") as f:
    FEATURE_NAMES = json.load(f)

DEFAULT_SYMBOLS = ["SOL", "DOGE", "AVAX"]
DEFAULT_HORIZONS = [12, 36]

MAKER_COST_PCT = 0.04

HP_PRESETS = {
    "ltf_ultra_reg": {
        "num_leaves": 7,
        "learning_rate": 0.003,
        "min_data_in_leaf": 150,
        "lambda_l1": 5.0,
        "lambda_l2": 15.0,
    },
    "ltf_reg": {
        "num_leaves": 15,
        "learning_rate": 0.005,
        "min_data_in_leaf": 80,
        "lambda_l1": 2.0,
        "lambda_l2": 8.0,
    },
}

SYMBOL_HP = {
    12: "ltf_ultra_reg",
    36: "ltf_reg",
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

NUM_BOOST_ROUND = 500


def _auc(y_true, y_pred):
    order = np.argsort(-y_pred)
    y_sorted = y_true[order]
    n_pos = y_sorted.sum()
    n_neg = len(y_sorted) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    tp = auc = 0.0
    for y in y_sorted:
        if y == 1:
            tp += 1
        else:
            auc += tp
    return auc / (n_pos * n_neg)


def walk_forward_splits(df, n_windows=4, test_frac=0.07):
    ts = df["timestamp"].values
    ts_first, ts_last = ts[0], ts[-1]
    span_days = (ts_last - ts_first) / (1000 * 86400)
    test_days = max(span_days * test_frac, 0.5)
    
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


def sequential_backtest(preds, fwd, q_long, q_short, hold_bars, cost_pct=MAKER_COST_PCT/100, window_size=200):
    n_trades = 0
    n_win = 0
    pnl = 0.0
    in_trade = False
    exit_bar = 0
    recent_preds = []
    trade_returns = []
    
    for i in range(len(preds)):
        p_val = float(preds[i])
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
        
        direction = 0
        if p_val > q_high:
            direction = 1
        elif p_val < q_low:
            direction = -1
        
        if direction != 0 and not np.isnan(fwd[i]):
            n_trades += 1
            trade_ret = direction * fwd[i] - cost_pct
            pnl += trade_ret
            trade_returns.append(trade_ret)
            in_trade = True
            exit_bar = i + hold_bars
            if trade_ret > 0:
                n_win += 1
    
    wr = n_win / n_trades if n_trades > 0 else 0
    sharpe = (np.mean(trade_returns) / np.std(trade_returns)) if len(trade_returns) > 1 else 0
    gains = sum(r for r in trade_returns if r > 0)
    losses = abs(sum(r for r in trade_returns if r < 0))
    pf = gains / losses if losses > 0 else (99.0 if gains > 0 else 0)
    
    return {
        "n_trades": n_trades,
        "win_rate": round(wr, 4),
        "pnl_pct": round(pnl * 100, 4),
        "sharpe": round(sharpe, 4),
        "profit_factor": round(pf, 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--horizon", type=int, default=None)
    args = parser.parse_args()
    
    symbols = [args.symbol] if args.symbol else DEFAULT_SYMBOLS
    horizons = [args.horizon] if args.horizon else DEFAULT_HORIZONS
    
    print("=" * 100)
    print("V12 TRAIN FINAL — Cost-Aware Labels")
    print(f"  Symbols: {symbols}")
    print(f"  Horizons: {horizons}")
    print(f"  Features: {len(FEATURE_NAMES)}")
    print("=" * 100)
    
    # Load dataset
    dataset_path = V12_DIR / "v12_dataset.parquet"
    LOG.info("Loading dataset: %s", dataset_path)
    df = pd.read_parquet(dataset_path)
    LOG.info("  loaded %d rows", len(df))
    
    all_results = []
    
    for symbol in symbols:
        sym_df = df[df["symbol"] == symbol].copy().reset_index(drop=True)
        if len(sym_df) < 1000:
            continue
        
        for horizon in horizons:
            hp = SYMBOL_HP.get(horizon, "ltf_reg")
            
            # Test both label types
            for label_type in ["standard", "costaware"]:
                label_col = f"label_h{horizon}" if label_type == "standard" else f"label_costaware_h{horizon}"
                fwd_col = f"fwd_ret_h{horizon}"
                
                if label_col not in sym_df.columns:
                    continue
                
                valid_df = sym_df[sym_df[label_col].notna()].reset_index(drop=True)
                if len(valid_df) < 500:
                    continue
                
                splits = walk_forward_splits(valid_df)
                if len(splits) < 2:
                    continue
                
                LOG.info("Training %s H=%d label=%s — %d splits", symbol, horizon, label_type, len(splits))
                
                for split_name, train_df, test_df in splits:
                    train_clean = train_df[train_df[label_col].notna()].reset_index(drop=True)
                    test_clean = test_df[test_df[label_col].notna()].reset_index(drop=True)
                    
                    X_tr = train_clean[FEATURE_NAMES].values.astype(np.float32)
                    X_tr = np.nan_to_num(X_tr, nan=0.0)
                    y_tr = train_clean[label_col].values.astype(np.float32)
                    
                    X_test = test_clean[FEATURE_NAMES].values.astype(np.float32)
                    X_test = np.nan_to_num(X_test, nan=0.0)
                    fwd = test_clean[fwd_col].values
                    
                    if len(X_tr) < 200 or len(X_test) < 20:
                        continue
                    
                    hp_dict = HP_PRESETS.get(hp, HP_PRESETS["ltf_reg"])
                    params = dict(BASE_PARAMS)
                    params.update(hp_dict)
                    
                    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES, free_raw_data=False)
                    bst = lgb.train(params, d_tr, num_boost_round=NUM_BOOST_ROUND,
                                     callbacks=[lgb.log_evaluation(period=0)])
                    
                    preds = bst.predict(X_test)
                    preds_tr = bst.predict(X_tr)
                    auc_train = float(_auc(y_tr, preds_tr))
                    auc_test_val = float(_auc(test_clean[label_col].values, preds))
                    
                    # Backtest
                    q_configs = [(80, 20), (85, 15), (90, 10), (92, 8), (95, 5)]
                    backtest_results = {}
                    for q_long, q_short in q_configs:
                        bt = sequential_backtest(preds, fwd, q_long, q_short, horizon)
                        backtest_results[f"q{q_long}_{q_short}"] = bt
                    
                    # Feature importance
                    imp = bst.feature_importance(importance_type="gain")
                    feat_imp = sorted(zip(FEATURE_NAMES, imp), key=lambda x: -x[1])[:10]
                    
                    result = {
                        "symbol": symbol,
                        "horizon": horizon,
                        "label_type": label_type,
                        "split": split_name,
                        "hp": hp,
                        "auc_train": auc_train,
                        "auc_test": auc_test_val,
                        "backtest": backtest_results,
                        "top_features": [{"name": n, "gain": float(g)} for n, g in feat_imp],
                    }
                    all_results.append(result)
                    
                    # Save model
                    model_name = f"v12_{label_type}_{symbol}_h{horizon}"
                    bst.save_model(str(OUTPUT_DIR / f"{model_name}.txt"))
    
    if not all_results:
        LOG.error("No results")
        sys.exit(1)
    
    # Save
    with open(V12_DIR / "v12_train_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    
    # Print summary
    print(f"\n{'='*120}")
    print("V12 RESULTS — Standard vs Cost-Aware Labels")
    print(f"{'='*120}")
    
    grouped = defaultdict(list)
    for r in all_results:
        grouped[(r["symbol"], r["horizon"], r["label_type"])].append(r)
    
    print(f"\n{'Symbol':<8} {'Horizon':>8} {'Label':>12} {'AUC_te':>8} {'Best Q':>8} {'PnL%':>10} {'WR':>8} {'PF':>8}")
    print("-" * 100)
    
    for (sym, h, lt), results in sorted(grouped.items()):
        auc_tests = [r["auc_test"] for r in results]
        
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
        
        print(f"{sym:<8} {h:>5d}(1h) {lt:>12} {np.mean(auc_tests):>8.3f} {best_q:>8} "
              f"{best_pnl:>+9.2f}% {best_wr:>8.3f} {best_pf:>8.2f}")
    
    # Label comparison
    print(f"\n{'='*120}")
    print("LABEL TYPE COMPARISON")
    print(f"{'='*120}")
    
    for h in horizons:
        print(f"\nHorizon H={h} ({h*5/60:.0f}h):")
        for lt in ["standard", "costaware"]:
            lt_results = [r for r in all_results if r["label_type"] == lt and r["horizon"] == h]
            if not lt_results:
                continue
            
            all_wrs = []
            all_pnls = []
            all_pfs = []
            for r in lt_results:
                for bt in r["backtest"].values():
                    if bt["n_trades"] >= 3:
                        all_wrs.append(bt["win_rate"])
                        all_pnls.append(bt["pnl_pct"])
                        all_pfs.append(bt["profit_factor"])
            
            print(f"  {lt:>12}: avg WR={np.mean(all_wrs):.3f}  avg PnL={np.mean(all_pnls):+.1f}%  avg PF={np.mean(all_pfs):.2f}")
    
    LOG.info("Results saved to %s", V12_DIR / "v12_train_results.json")


if __name__ == "__main__":
    main()
