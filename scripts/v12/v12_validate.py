"""
v12_validate.py — Walk-forward validation of optimal trading configs.

Validates the best configs found by v12_optimize.py across multiple
time windows to ensure robustness.

USAGE:
    python scripts/v12/v12_validate.py
    python scripts/v12/v12_validate.py --symbol SOL --horizon 12
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
V11_DIR = DATA_DIR / "v11"
OUTPUT_DIR = DATA_DIR / "v12"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v12_val")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "v11"))
from v11_build_dataset import ALL_FEATURE_NAMES

MAKER_COST_PCT = 0.0004

# Best configs from optimization
BEST_CONFIGS = {
    "SOL": {
        "conservative": {"q_long": 95, "q_short": 5, "direction": "long_only", "trend_filter": "aligned"},
        "balanced": {"q_long": 95, "q_short": 5, "direction": "both", "trend_filter": "none"},
        "aggressive": {"q_long": 90, "q_short": 10, "direction": "both", "trend_filter": "none"},
    },
    "DOGE": {
        "conservative": {"q_long": 98, "q_short": 2, "direction": "both", "trend_filter": "none"},
        "balanced": {"q_long": 95, "q_short": 5, "direction": "both", "trend_filter": "none"},
        "aggressive": {"q_long": 85, "q_short": 15, "direction": "both", "trend_filter": "none"},
    },
    "AVAX": {
        "conservative": {"q_long": 97, "q_short": 3, "direction": "long_only", "trend_filter": "aligned"},
        "balanced": {"q_long": 95, "q_short": 5, "direction": "both", "trend_filter": "aligned"},
        "aggressive": {"q_long": 90, "q_short": 10, "direction": "both", "trend_filter": "none"},
    },
}


def walk_forward_splits(df, n_windows=6, test_frac=0.07):
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


def sequential_backtest(preds, fwd, trend_1h, q_long, q_short, hold_bars,
                         direction_mode="both", trend_filter="none",
                         cost_pct=MAKER_COST_PCT, window_size=200):
    n_trades = 0
    n_win = 0
    pnl = 0.0
    in_trade = False
    exit_bar = 0
    recent_preds = []
    trade_returns = []
    n_long = 0
    n_short = 0
    
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
        
        if direction == 0:
            continue
        if direction_mode == "long_only" and direction == -1:
            continue
        if direction_mode == "short_only" and direction == 1:
            continue
        if trend_filter == "aligned":
            if direction == 1 and trend_1h[i] < 0:
                continue
            if direction == -1 and trend_1h[i] > 0:
                continue
        
        if not np.isnan(fwd[i]):
            n_trades += 1
            trade_ret = direction * fwd[i] - cost_pct
            pnl += trade_ret
            trade_returns.append(trade_ret)
            in_trade = True
            exit_bar = i + hold_bars
            if direction == 1:
                n_long += 1
            else:
                n_short += 1
            if trade_ret > 0:
                n_win += 1
    
    wr = n_win / n_trades if n_trades > 0 else 0
    sharpe = (np.mean(trade_returns) / np.std(trade_returns)) if len(trade_returns) > 1 else 0
    gains = sum(r for r in trade_returns if r > 0)
    losses = abs(sum(r for r in trade_returns if r < 0))
    pf = gains / losses if losses > 0 else (99.0 if gains > 0 else 0)
    
    # Max drawdown
    if trade_returns:
        cum = np.cumsum(trade_returns)
        running_max = np.maximum.accumulate(cum)
        dd = cum - running_max
        max_dd = float(dd.min())
    else:
        max_dd = 0
    
    return {
        "n_trades": n_trades,
        "n_long": n_long,
        "n_short": n_short,
        "win_rate": round(wr, 4),
        "pnl_pct": round(pnl * 100, 4),
        "sharpe": round(sharpe, 4),
        "profit_factor": round(pf, 4),
        "max_dd_pct": round(max_dd * 100, 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--horizon", type=int, default=12)
    args = parser.parse_args()
    
    symbols = [args.symbol] if args.symbol else ["SOL", "DOGE", "AVAX"]
    horizon = args.horizon
    fwd_col = f"fwd_ret_h{horizon}"
    
    print("=" * 110)
    print("V12 WALK-FORWARD VALIDATION — Best Configs from Optimization")
    print(f"  Symbols: {symbols}")
    print(f"  Horizon: {horizon} ({horizon * 5 / 60:.0f}h)")
    print(f"  Walk-forward windows: 6")
    print("=" * 110)
    
    df = pd.read_parquet(V11_DIR / "v11_dataset.parquet")
    LOG.info("Loaded %d rows", len(df))
    
    all_results = []
    
    for symbol in symbols:
        if symbol not in BEST_CONFIGS:
            continue
        
        sym_df = df[df["symbol"] == symbol].copy().reset_index(drop=True)
        model_path = V11_DIR / "models" / f"v11_clf_{symbol}_h{horizon}.txt"
        if not model_path.exists():
            continue
        
        model = lgb.Booster(model_file=str(model_path))
        splits = walk_forward_splits(sym_df, n_windows=6)
        
        LOG.info("Validating %s — %d windows", symbol, len(splits))
        
        for profile_name, config in BEST_CONFIGS[symbol].items():
            for split_name, train_df, test_df in splits:
                # Re-train model on training data
                label_col = f"label_h{horizon}"
                train_clean = train_df[train_df[label_col].notna()].reset_index(drop=True)
                test_clean = test_df[test_df[label_col].notna()].reset_index(drop=True)
                
                if len(train_clean) < 200 or len(test_clean) < 20:
                    continue
                
                X_tr = train_clean[ALL_FEATURE_NAMES].values.astype(np.float32)
                X_tr = np.nan_to_num(X_tr, nan=0.0)
                y_tr = train_clean[label_col].values.astype(np.float32)
                
                import lightgbm as lgbm
                params = {
                    "objective": "binary",
                    "metric": "auc",
                    "num_leaves": 7,
                    "learning_rate": 0.003,
                    "feature_fraction": 0.7,
                    "bagging_fraction": 0.7,
                    "bagging_freq": 3,
                    "min_data_in_leaf": 150,
                    "lambda_l1": 5.0,
                    "lambda_l2": 15.0,
                    "verbosity": -1,
                    "seed": 42,
                }
                d_tr = lgbm.Dataset(X_tr, label=y_tr, feature_name=ALL_FEATURE_NAMES, free_raw_data=False)
                bst = lgbm.train(params, d_tr, num_boost_round=500,
                                  callbacks=[lgbm.log_evaluation(period=0)])
                
                X_test = test_clean[ALL_FEATURE_NAMES].values.astype(np.float32)
                X_test = np.nan_to_num(X_test, nan=0.0)
                preds = bst.predict(X_test)
                fwd = test_clean[fwd_col].values
                trend_1h = test_clean["trend_1h"].values if "trend_1h" in test_clean.columns else np.zeros(len(test_clean))
                
                bt = sequential_backtest(
                    preds, fwd, trend_1h,
                    config["q_long"], config["q_short"],
                    horizon,
                    direction_mode=config["direction"],
                    trend_filter=config["trend_filter"],
                )
                
                result = {
                    "symbol": symbol,
                    "profile": profile_name,
                    "config": config,
                    "split": split_name,
                    **bt,
                }
                all_results.append(result)
    
    if not all_results:
        LOG.error("No results")
        sys.exit(1)
    
    # Save
    with open(OUTPUT_DIR / "v12_validation_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    
    # Print summary
    print(f"\n{'='*120}")
    print("WALK-FORWARD VALIDATION RESULTS")
    print(f"{'='*120}")
    
    grouped = defaultdict(list)
    for r in all_results:
        grouped[(r["symbol"], r["profile"])].append(r)
    
    print(f"\n{'Symbol':<8} {'Profile':<15} {'Windows':>8} {'Avg WR':>8} {'Avg PnL%':>10} {'Avg PF':>8} {'Avg Sharpe':>11} {'Consistency':>12}")
    print("-" * 110)
    
    for (sym, profile), results in sorted(grouped.items()):
        wrs = [r["win_rate"] for r in results]
        pnls = [r["pnl_pct"] for r in results]
        pfs = [r["profit_factor"] for r in results]
        sharpes = [r["sharpe"] for r in results]
        
        # Consistency: how many windows are profitable
        n_positive = sum(1 for p in pnls if p > 0)
        consistency = f"{n_positive}/{len(pnls)}"
        
        print(f"{sym:<8} {profile:<15} {len(results):>8d} {np.mean(wrs):>8.3f} "
              f"{np.mean(pnls):>+9.1f}% {np.mean(pfs):>8.2f} {np.mean(sharpes):>+10.3f} {consistency:>12}")
    
    # Per-window detail for best profiles
    print(f"\n{'='*120}")
    print("PER-WINDOW DETAIL (balanced profile)")
    print(f"{'='*120}")
    
    for symbol in symbols:
        balanced_results = [r for r in all_results if r["symbol"] == symbol and r["profile"] == "balanced"]
        if not balanced_results:
            continue
        
        print(f"\n  {symbol} — Balanced Profile:")
        for r in balanced_results:
            q_str = f"Q{r['config']['q_long']}/{r['config']['q_short']}"
            print(f"    {r['split']}: trades={r['n_trades']:>4d}  WR={r['win_rate']:.3f}  "
                  f"PnL={r['pnl_pct']:+.1f}%  PF={r['profit_factor']:.2f}  "
                  f"Sharpe={r['sharpe']:+.3f}  MaxDD={r['max_dd_pct']:.1f}%")
    
    # Final verdict
    print(f"\n{'='*120}")
    print("FINAL VERDICT")
    print(f"{'='*120}")
    
    for symbol in symbols:
        balanced_results = [r for r in all_results if r["symbol"] == symbol and r["profile"] == "balanced"]
        if not balanced_results:
            continue
        
        wrs = [r["win_rate"] for r in balanced_results]
        pnls = [r["pnl_pct"] for r in balanced_results]
        n_positive = sum(1 for p in pnls if p > 0)
        
        status = "ROBUST" if n_positive >= len(pnls) * 0.75 and np.mean(wrs) > 0.55 else "FRAGILE"
        
        print(f"  {symbol}: {status} — avg WR={np.mean(wrs):.3f}, "
              f"{n_positive}/{len(pnls)} windows profitable, "
              f"avg PnL={np.mean(pnls):+.1f}%")
    
    LOG.info("Validation results saved to %s", OUTPUT_DIR / "v12_validation_results.json")


if __name__ == "__main__":
    main()
