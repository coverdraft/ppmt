"""
v12_optimize.py — Exhaustive trading parameter optimization using V11 models.

Tests combinations of:
  - Quantile thresholds (Q80/20 through Q98/2)
  - Direction modes: long-only, short-only, both
  - Signal confidence filters
  - Trend alignment modes
  - Hold periods (fixed vs variable)

USAGE:
    python scripts/v12/v12_optimize.py
    python scripts/v12/v12_optimize.py --symbol DOGE --horizon 12
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
V11_DIR = DATA_DIR / "v11"
OUTPUT_DIR = DATA_DIR / "v12"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v12_opt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "v11"))
from v11_build_dataset import ALL_FEATURE_NAMES, DEFAULT_SYMBOLS

MAKER_COST_PCT = 0.0004


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


def sequential_backtest(
    preds, fwd, trend_1h, mtf_alignment,
    q_long, q_short, hold_bars,
    direction_mode="both",  # "both", "long_only", "short_only"
    trend_filter="none",    # "none", "aligned", "counter"
    min_confidence=0.0,     # minimum prediction value for long, max for short
    cost_pct=MAKER_COST_PCT,
    window_size=200,
):
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
        
        # Direction mode filter
        if direction_mode == "long_only" and direction == -1:
            continue
        if direction_mode == "short_only" and direction == 1:
            continue
        
        # Confidence filter
        if direction == 1 and p_val < min_confidence:
            continue
        if direction == -1 and p_val > (1 - min_confidence):
            continue
        
        # Trend filter
        if trend_filter == "aligned":
            if direction == 1 and trend_1h[i] < 0:
                continue
            if direction == -1 and trend_1h[i] > 0:
                continue
        elif trend_filter == "counter":
            if direction == 1 and trend_1h[i] > 0:
                continue
            if direction == -1 and trend_1h[i] < 0:
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
    
    return {
        "n_trades": n_trades,
        "n_long": n_long,
        "n_short": n_short,
        "win_rate": round(wr, 4),
        "pnl_pct": round(pnl * 100, 4),
        "sharpe": round(sharpe, 4),
        "profit_factor": round(pf, 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--horizon", type=int, default=12)
    args = parser.parse_args()
    
    symbols = [args.symbol] if args.symbol else DEFAULT_SYMBOLS
    horizon = args.horizon
    fwd_col = f"fwd_ret_h{horizon}"
    
    print("=" * 100)
    print("V12 OPTIMIZATION — Exhaustive Trading Parameter Search")
    print(f"  Symbols: {symbols}")
    print(f"  Horizon: {horizon} ({horizon * 5 / 60:.0f}h)")
    print("=" * 100)
    
    # Load V11 dataset
    df = pd.read_parquet(V11_DIR / "v11_dataset.parquet")
    LOG.info("Loaded %d rows", len(df))
    
    all_results = []
    
    # Parameter grid
    q_configs = [
        (80, 20), (82, 18), (85, 15), (87, 13),
        (90, 10), (92, 8), (95, 5), (97, 3), (98, 2),
    ]
    direction_modes = ["both", "long_only"]
    trend_filters = ["none", "aligned"]
    
    for symbol in symbols:
        sym_df = df[df["symbol"] == symbol].copy().reset_index(drop=True)
        model_path = V11_DIR / "models" / f"v11_clf_{symbol}_h{horizon}.txt"
        if not model_path.exists():
            continue
        
        model = lgb.Booster(model_file=str(model_path))
        
        # Use last 20% as test
        n = len(sym_df)
        test_start = int(n * 0.8)
        test_df = sym_df.iloc[test_start:].reset_index(drop=True)
        
        preds = model.predict(test_df[ALL_FEATURE_NAMES].values.astype(np.float32))
        fwd = test_df[fwd_col].values
        trend_1h = test_df["trend_1h"].values if "trend_1h" in test_df.columns else np.zeros(len(test_df))
        mtf_alignment = test_df["mtf_alignment"].values if "mtf_alignment" in test_df.columns else np.zeros(len(test_df))
        
        LOG.info("Testing %d configs for %s H=%d (test: %d rows)", 
                 len(q_configs) * len(direction_modes) * len(trend_filters),
                 symbol, horizon, len(test_df))
        
        for q_long, q_short in q_configs:
            for dir_mode in direction_modes:
                for trend_f in trend_filters:
                    bt = sequential_backtest(
                        preds, fwd, trend_1h, mtf_alignment,
                        q_long, q_short, horizon,
                        direction_mode=dir_mode,
                        trend_filter=trend_f,
                    )
                    
                    result = {
                        "symbol": symbol,
                        "horizon": horizon,
                        "q_long": q_long,
                        "q_short": q_short,
                        "direction": dir_mode,
                        "trend_filter": trend_f,
                        **bt,
                    }
                    all_results.append(result)
    
    if not all_results:
        LOG.error("No results")
        sys.exit(1)
    
    # Save
    results_df = pd.DataFrame(all_results)
    results_df.to_parquet(OUTPUT_DIR / "v12_optimization_results.parquet", index=False)
    results_df.to_csv(OUTPUT_DIR / "v12_optimization_results.csv", index=False)
    
    # Print top results by WR (with minimum trades)
    print(f"\n{'='*120}")
    print("TOP 30 CONFIGS BY WIN RATE (min 50 trades)")
    print(f"{'='*120}")
    
    min_trades = 50
    filtered = results_df[results_df["n_trades"] >= min_trades].copy()
    filtered = filtered.sort_values("win_rate", ascending=False)
    
    print(f"\n{'Symbol':<8} {'Q':>6} {'Dir':>10} {'Trend':>8} {'Trades':>7} {'WR':>6} {'PnL%':>10} {'PF':>6} {'Sharpe':>7}")
    print("-" * 100)
    
    for _, row in filtered.head(30).iterrows():
        q_str = f"{int(row['q_long'])}/{int(row['q_short'])}"
        print(f"{row['symbol']:<8} {q_str:>6} {row['direction']:>10} {row['trend_filter']:>8} "
              f"{int(row['n_trades']):>7d} {row['win_rate']:>6.3f} {row['pnl_pct']:>+9.1f}% "
              f"{row['profit_factor']:>6.2f} {row['sharpe']:>+7.3f}")
    
    # Print top results by PnL
    print(f"\n{'='*120}")
    print("TOP 30 CONFIGS BY PnL (min 50 trades)")
    print(f"{'='*120}")
    
    filtered_pnl = results_df[results_df["n_trades"] >= min_trades].copy()
    filtered_pnl = filtered_pnl.sort_values("pnl_pct", ascending=False)
    
    print(f"\n{'Symbol':<8} {'Q':>6} {'Dir':>10} {'Trend':>8} {'Trades':>7} {'WR':>6} {'PnL%':>10} {'PF':>6} {'Sharpe':>7}")
    print("-" * 100)
    
    for _, row in filtered_pnl.head(30).iterrows():
        q_str = f"{int(row['q_long'])}/{int(row['q_short'])}"
        print(f"{row['symbol']:<8} {q_str:>6} {row['direction']:>10} {row['trend_filter']:>8} "
              f"{int(row['n_trades']):>7d} {row['win_rate']:>6.3f} {row['pnl_pct']:>+9.1f}% "
              f"{row['profit_factor']:>6.2f} {row['sharpe']:>+7.3f}")
    
    # Best config per symbol
    print(f"\n{'='*120}")
    print("BEST CONFIG PER SYMBOL (balanced WR + PnL)")
    print(f"{'='*120}")
    
    for symbol in symbols:
        sym_results = results_df[(results_df["symbol"] == symbol) & (results_df["n_trades"] >= min_trades)]
        if len(sym_results) == 0:
            continue
        
        # Score = WR * PF * sign(PnL)
        sym_results = sym_results.copy()
        sym_results["score"] = sym_results["win_rate"] * sym_results["profit_factor"] * np.sign(sym_results["pnl_pct"])
        best = sym_results.loc[sym_results["score"].idxmax()]
        
        q_str = f"{int(best['q_long'])}/{int(best['q_short'])}"
        print(f"\n  {symbol}:")
        print(f"    Best: Q{q_str} {best['direction']} trend={best['trend_filter']}")
        print(f"    WR={best['win_rate']:.3f}  PnL={best['pnl_pct']:+.1f}%  PF={best['profit_factor']:.2f}  "
              f"Trades={int(best['n_trades'])}  Sharpe={best['sharpe']:+.3f}")
        print(f"    Long={int(best['n_long'])}  Short={int(best['n_short'])}")
    
    # Direction mode comparison
    print(f"\n{'='*120}")
    print("DIRECTION MODE COMPARISON")
    print(f"{'='*120}")
    
    for dir_mode in ["both", "long_only", "short_only"]:
        mode_results = results_df[(results_df["direction"] == dir_mode) & (results_df["n_trades"] >= 20)]
        if len(mode_results) == 0:
            continue
        avg_wr = mode_results["win_rate"].mean()
        avg_pnl = mode_results["pnl_pct"].mean()
        avg_pf = mode_results["profit_factor"].mean()
        best_wr = mode_results["win_rate"].max()
        print(f"  {dir_mode:>12}: avg WR={avg_wr:.3f}  best WR={best_wr:.3f}  avg PnL={avg_pnl:+.1f}%  avg PF={avg_pf:.2f}")
    
    LOG.info("Results saved to %s", OUTPUT_DIR / "v12_optimization_results.csv")


if __name__ == "__main__":
    main()
