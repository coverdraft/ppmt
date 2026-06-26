"""
v11_train_fast.py — Fast training with single split (not walk-forward).

For quick experimentation — trains one model per (symbol, horizon) on a single
train/test split instead of 4 rolling windows.

USAGE:
    python scripts/v11/v11_train_fast.py
"""
from __future__ import annotations

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
OUTPUT_DIR = DATA_DIR / "v11" / "models"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v11_fast")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "v11"))
from v11_build_dataset import ALL_FEATURE_NAMES

SYMBOLS = ["SOL", "DOGE", "AVAX"]
HORIZONS = [12, 36, 72, 288]  # 1h, 3h, 6h, 24h

MAKER_COST = 0.04

HP_PRESETS = {
    "default": {"num_leaves": 31, "learning_rate": 0.01, "min_data_in_leaf": 30, "lambda_l1": 0.3, "lambda_l2": 3.0},
    "more_reg": {"num_leaves": 15, "learning_rate": 0.005, "min_data_in_leaf": 50, "lambda_l1": 1.0, "lambda_l2": 5.0},
    "ltf_reg": {"num_leaves": 15, "learning_rate": 0.005, "min_data_in_leaf": 80, "lambda_l1": 2.0, "lambda_l2": 8.0},
    "ltf_ultra_reg": {"num_leaves": 7, "learning_rate": 0.003, "min_data_in_leaf": 150, "lambda_l1": 5.0, "lambda_l2": 15.0},
}

HORIZON_HP = {12: "ltf_ultra_reg", 36: "ltf_reg", 72: "more_reg", 288: "default"}

BASE_PARAMS = {
    "objective": "binary",
    "metric": ["binary_logloss", "auc"],
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "bagging_freq": 3,
    "verbosity": -1,
    "seed": 42,
}

NUM_BOOST_ROUND = 300  # Fewer rounds for speed


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


def sequential_backtest(pred, fwd_ret, q_long, q_short, hold_bars, window_size=200, cost_pct=MAKER_COST):
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
    
    if not trade_returns:
        return {"n_trades": 0, "win_rate": 0, "pnl_pct": 0, "sharpe": 0, "profit_factor": 0}
    
    trade_returns = np.array(trade_returns)
    wr = float((trade_returns > 0).mean())
    sharpe = float(np.mean(trade_returns) / np.std(trade_returns)) if len(trade_returns) > 1 and np.std(trade_returns) > 0 else 0
    gains = float(trade_returns[trade_returns > 0].sum())
    losses = float(-trade_returns[trade_returns < 0].sum())
    pf = gains / losses if losses > 0 else (99.0 if gains > 0 else 0)
    
    return {
        "n_trades": n_trades,
        "n_long": n_long,
        "n_short": n_short,
        "win_rate": round(wr, 4),
        "pnl_pct": round(float(pnl), 4),
        "sharpe": round(sharpe, 4),
        "profit_factor": round(pf, 4),
    }


def main():
    dataset_path = DATA_DIR / "v11" / "v11_dataset.parquet"
    if not dataset_path.exists():
        LOG.error("Dataset not found: %s", dataset_path)
        sys.exit(1)
    
    LOG.info("Loading dataset...")
    df = pd.read_parquet(dataset_path)
    LOG.info("  %d rows", len(df))
    
    print("=" * 110)
    print("v11 FAST TRAIN — Low Timeframe Binary Classifier")
    print(f"  Features: {len(ALL_FEATURE_NAMES)}")
    print(f"  Cost: {MAKER_COST}% (maker)")
    print(f"  Rounds: {NUM_BOOST_ROUND}")
    print("=" * 110)
    
    all_results = []
    
    for symbol in SYMBOLS:
        sym_df = df[df["symbol"] == symbol].copy().reset_index(drop=True)
        if len(sym_df) < 1000:
            continue
        
        for horizon in HORIZONS:
            label_col = f"label_h{horizon}"
            fwd_col = f"fwd_ret_h{horizon}"
            if label_col not in sym_df.columns:
                continue
            
            valid_df = sym_df[sym_df[label_col].notna()].reset_index(drop=True)
            if len(valid_df) < 500:
                continue
            
            # Single split: 80% train, 20% test (time-ordered)
            split_idx = int(len(valid_df) * 0.8)
            train_df = valid_df.iloc[:split_idx]
            test_df = valid_df.iloc[split_idx:]
            
            X_tr = train_df[ALL_FEATURE_NAMES].values.astype(np.float32)
            y_tr = train_df[label_col].values.astype(np.float32)
            X_test = test_df[ALL_FEATURE_NAMES].values.astype(np.float32)
            y_test = test_df[label_col].values.astype(np.float32)
            fwd_ret = test_df[fwd_col].values.astype(np.float64)
            
            # Train
            hp_name = HORIZON_HP.get(horizon, "default")
            hp = HP_PRESETS.get(hp_name, HP_PRESETS["default"])
            params = dict(BASE_PARAMS)
            params.update(hp)
            
            t0 = time.time()
            d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=ALL_FEATURE_NAMES, free_raw_data=True)
            bst = lgb.train(params, d_tr, num_boost_round=NUM_BOOST_ROUND,
                           callbacks=[lgb.log_evaluation(period=0)])
            
            pred = bst.predict(X_test)
            auc_test = float(_auc(y_test, pred))
            dir_acc = float(((pred > 0.5) == (y_test > 0.5)).mean())
            
            elapsed = time.time() - t0
            
            # Backtest sweep
            q_configs = [(95, 5), (90, 10), (85, 15), (82, 18), (80, 20)]
            best_pnl = -999
            best_q = ""
            best_bt = None
            
            for q_long, q_short in q_configs:
                bt = sequential_backtest(pred, fwd_ret, q_long, q_short, horizon)
                if bt["n_trades"] >= 3 and bt["pnl_pct"] > best_pnl:
                    best_pnl = bt["pnl_pct"]
                    best_q = f"Q{q_long}/{q_short}"
                    best_bt = bt
            
            # Feature importance
            imp = bst.feature_importance(importance_type="gain")
            feat_imp = sorted(zip(ALL_FEATURE_NAMES, imp), key=lambda x: -x[1])
            total_gain = max(float(imp.sum()), 1.0)
            
            result = {
                "symbol": symbol,
                "horizon": horizon,
                "horizon_h": horizon * 5 / 60,
                "hp": hp_name,
                "auc_test": round(auc_test, 4),
                "dir_acc": round(dir_acc, 4),
                "best_q": best_q,
                "best_pnl": best_pnl,
                "best_wr": best_bt["win_rate"] if best_bt else 0,
                "best_sharpe": best_bt["sharpe"] if best_bt else 0,
                "best_pf": best_bt["profit_factor"] if best_bt else 0,
                "best_trades": best_bt["n_trades"] if best_bt else 0,
                "train_time_s": round(elapsed, 1),
                "top_features": [{"name": n, "pct": round(g / total_gain * 100, 2)} for n, g in feat_imp[:15]],
            }
            all_results.append(result)
            
            # Save model
            model_path = OUTPUT_DIR / f"v11_clf_{symbol}_h{horizon}.txt"
            bst.save_model(str(model_path))
            
            LOG.info("  %s H=%d(%dh) HP=%s: auc=%.3f dir=%.3f best=%s PnL=%+.2f%% WR=%.3f PF=%.2f in %.1fs",
                     symbol, horizon, int(horizon * 5 / 60), hp_name,
                     auc_test, dir_acc, best_q, best_pnl,
                     result["best_wr"], result["best_pf"], elapsed)
    
    # Print results
    print("\n" + "=" * 120)
    print("v11 RESULTS — LOW TIMEFRAME TRADING")
    print("=" * 120)
    print(f"\n{'Symbol':<8} {'Horizon':>8} {'HP':>14} {'AUC':>7} {'DirAcc':>7} "
          f"{'Best Q':>8} {'PnL%':>9} {'WR':>6} {'Sharpe':>7} {'PF':>6} {'Trades':>7} {'Time':>5}")
    print("-" * 120)
    
    for r in all_results:
        print(f"{r['symbol']:<8} {r['horizon']:>5d}({r['horizon_h']:.0f}h) {r['hp']:>14} "
              f"{r['auc_test']:>7.3f} {r['dir_acc']:>7.3f} "
              f"{r['best_q']:>8} {r['best_pnl']:>+8.2f}% "
              f"{r['best_wr']:>5.3f} {r['best_sharpe']:>+6.3f} "
              f"{r['best_pf']:>5.2f} {r['best_trades']:>7} {r['train_time_s']:>4.0f}s")
    
    # Horizon comparison
    print(f"\n--- HORIZON COMPARISON ---")
    for h in HORIZONS:
        h_results = [r for r in all_results if r["horizon"] == h]
        if not h_results:
            continue
        avg_pnl = np.mean([r["best_pnl"] for r in h_results])
        avg_wr = np.mean([r["best_wr"] for r in h_results])
        avg_auc = np.mean([r["auc_test"] for r in h_results])
        avg_sharpe = np.mean([r["best_sharpe"] for r in h_results])
        positive = sum(1 for r in h_results if r["best_pnl"] > 0)
        print(f"  H={h:>3d} ({h*5//60}h): Avg PnL={avg_pnl:+.2f}% WR={avg_wr:.3f} "
              f"AUC={avg_auc:.3f} Sharpe={avg_sharpe:+.3f} — {positive}/{len(h_results)} positive")
    
    # Feature importance across all models
    print(f"\n--- TOP FEATURES (most frequently important) ---")
    feat_scores = defaultdict(float)
    for r in all_results:
        for f in r["top_features"][:10]:
            feat_scores[f["name"]] += f["pct"]
    
    for name, score in sorted(feat_scores.items(), key=lambda x: -x[1])[:20]:
        print(f"  {name:<30} {score:>6.1f}%")
    
    # Save results
    with open(OUTPUT_DIR / "v11_fast_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    
    LOG.info("Results saved to %s", OUTPUT_DIR / "v11_fast_results.json")


if __name__ == "__main__":
    main()
