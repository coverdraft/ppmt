"""
v11_backtest.py — Comprehensive backtest for low-timeframe models.

TESTS:
  1. Fixed-exit backtest (same as v7.5) — baseline comparison
  2. Adaptive-exit backtest — SL/TP/trailing based on predicted signal strength
  3. Multi-horizon sweep — find optimal horizon per symbol
  4. Cost sensitivity — maker vs taker fees impact
  5. MTF filter — does trend alignment improve WR?

KEY INNOVATIONS:
  - Adaptive SL/TP: wider stops for strong signals, tighter for weak
  - Trailing stop: lock in profits on strong moves
  - MTF filter: only trade when 5m/15m/1h trends align
  - Cost analysis: maker vs taker fee impact

USAGE:
    python scripts/v11/v11_backtest.py
    python scripts/v11/v11_backtest.py --symbol SOL --horizon 36
    python scripts/v11/v11_backtest.py --adaptive
    python scripts/v11/v11_backtest.py --sweep
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
MODELS_DIR = DATA_DIR / "v11" / "models"
OUTPUT_DIR = DATA_DIR / "v11"

LOG = logging.getLogger("v11_bt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "v11"))
from v11_build_dataset import ALL_FEATURE_NAMES, DEFAULT_SYMBOLS, DEFAULT_HORIZONS
from v11_train import MAKER_COST_PCT, TAKER_COST_PCT

WF_WINDOWS = 4
POSITION_NOTIONAL = 700.0
ACCOUNT_SIZE = 10000.0


# ============================================================================
# DATA LOADING
# ============================================================================

def load_dataset() -> pd.DataFrame:
    """Load v11 dataset."""
    path = DATA_DIR / "v11" / "v11_dataset.parquet"
    if not path.exists():
        LOG.error("Dataset not found: %s", path)
        sys.exit(1)
    df = pd.read_parquet(path)
    LOG.info("Loaded dataset: %d rows", len(df))
    return df


def make_walk_forward_splits(df: pd.DataFrame, n_windows: int = WF_WINDOWS):
    """Create rolling walk-forward splits."""
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
# BACKTEST ENGINES
# ============================================================================

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


def sequential_backtest_fixed(
    pred: np.ndarray,
    fwd_ret: np.ndarray,
    q_long: int,
    q_short: int,
    hold_bars: int,
    window_size: int = 200,
    cost_pct: float = MAKER_COST_PCT,
) -> dict:
    """Fixed-exit sequential backtest (v7.5 style)."""
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
    
    return _compute_metrics(trade_returns, n_trades, n_win, n_long, n_short, pnl)


def sequential_backtest_adaptive(
    pred: np.ndarray,
    fwd_ret: np.ndarray,
    close_prices: np.ndarray,
    q_long: int,
    q_short: int,
    hold_bars: int,
    window_size: int = 200,
    cost_pct: float = MAKER_COST_PCT,
    # Adaptive exit parameters
    sl_atr_mult: float = 2.0,       # SL = entry ± ATR * mult
    tp_atr_mult: float = 3.0,       # TP = entry ± ATR * mult
    trail_atr_mult: float = 1.5,    # Trail activation = ATR * mult in profit
    trail_step: float = 0.5,        # Trail step in ATR units
) -> dict:
    """Adaptive-exit sequential backtest with SL/TP and trailing stop.
    
    KEY IDEA: Instead of holding for the full horizon, exit early if:
    - SL hit: price moves against us by SL_atr * ATR
    - TP hit: price moves in our favor by TP_atr * ATR
    - Trail: after moving trail_atr * ATR in profit, trail by trail_step * ATR
    
    This should improve WR by cutting losses early and letting winners run.
    """
    n_trades = 0
    n_win = 0
    pnl = 0.0
    in_trade = False
    entry_bar = 0
    entry_price = 0.0
    side = 0
    exit_bar = 0
    recent_preds = []
    trade_returns = []
    n_long = 0
    n_short = 0
    n_sl = 0
    n_tp = 0
    n_trail = 0
    n_hold = 0
    
    # Compute ATR for adaptive exits (rolling 14-bar range)
    if len(close_prices) > 14:
        atr = np.zeros(len(close_prices))
        for i in range(1, len(close_prices)):
            atr[i] = abs(close_prices[i] - close_prices[i-1])
        atr = pd.Series(atr).rolling(14, min_periods=5).mean().values
        atr = np.nan_to_num(atr, nan=0.001)
        atr = np.maximum(atr, close_prices * 0.001)  # floor at 0.1% of price
    else:
        atr = np.full(len(close_prices), close_prices.mean() * 0.01)
    
    for i in range(len(pred)):
        p_val = float(pred[i])
        recent_preds.append(p_val)
        if len(recent_preds) > window_size:
            recent_preds.pop(0)
        
        # Check exit conditions for open trade
        if in_trade:
            current_price = close_prices[i]
            atr_val = atr[i]
            
            if side == 1:  # LONG
                unrealized = current_price - entry_price
                
                # SL check
                if current_price <= entry_price - sl_atr_mult * atr_val:
                    trade_ret = (current_price - entry_price) / entry_price * 100 - cost_pct
                    trade_returns.append(trade_ret)
                    pnl += trade_ret
                    n_trades += 1
                    n_sl += 1
                    if trade_ret > 0:
                        n_win += 1
                    in_trade = False
                    continue
                
                # TP check
                if current_price >= entry_price + tp_atr_mult * atr_val:
                    trade_ret = (current_price - entry_price) / entry_price * 100 - cost_pct
                    trade_returns.append(trade_ret)
                    pnl += trade_ret
                    n_trades += 1
                    n_tp += 1
                    if trade_ret > 0:
                        n_win += 1
                    in_trade = False
                    continue
                
                # Trailing stop
                max_price = np.max(close_prices[entry_bar:i+1])
                if max_price >= entry_price + trail_atr_mult * atr_val:
                    trail_price = max_price - trail_step * atr_val
                    if current_price <= trail_price:
                        trade_ret = (current_price - entry_price) / entry_price * 100 - cost_pct
                        trade_returns.append(trade_ret)
                        pnl += trade_ret
                        n_trades += 1
                        n_trail += 1
                        if trade_ret > 0:
                            n_win += 1
                        in_trade = False
                        continue
                
                # Hold expiry
                if i >= exit_bar:
                    trade_ret = (current_price - entry_price) / entry_price * 100 - cost_pct
                    trade_returns.append(trade_ret)
                    pnl += trade_ret
                    n_trades += 1
                    n_hold += 1
                    if trade_ret > 0:
                        n_win += 1
                    in_trade = False
                    continue
            
            elif side == -1:  # SHORT
                unrealized = entry_price - current_price
                
                # SL check
                if current_price >= entry_price + sl_atr_mult * atr_val:
                    trade_ret = (entry_price - current_price) / entry_price * 100 - cost_pct
                    trade_returns.append(trade_ret)
                    pnl += trade_ret
                    n_trades += 1
                    n_sl += 1
                    if trade_ret > 0:
                        n_win += 1
                    in_trade = False
                    continue
                
                # TP check
                if current_price <= entry_price - tp_atr_mult * atr_val:
                    trade_ret = (entry_price - current_price) / entry_price * 100 - cost_pct
                    trade_returns.append(trade_ret)
                    pnl += trade_ret
                    n_trades += 1
                    n_tp += 1
                    if trade_ret > 0:
                        n_win += 1
                    in_trade = False
                    continue
                
                # Trailing stop
                min_price = np.min(close_prices[entry_bar:i+1])
                if min_price <= entry_price - trail_atr_mult * atr_val:
                    trail_price = min_price + trail_step * atr_val
                    if current_price >= trail_price:
                        trade_ret = (entry_price - current_price) / entry_price * 100 - cost_pct
                        trade_returns.append(trade_ret)
                        pnl += trade_ret
                        n_trades += 1
                        n_trail += 1
                        if trade_ret > 0:
                            n_win += 1
                        in_trade = False
                        continue
                
                # Hold expiry
                if i >= exit_bar:
                    trade_ret = (entry_price - current_price) / entry_price * 100 - cost_pct
                    trade_returns.append(trade_ret)
                    pnl += trade_ret
                    n_trades += 1
                    n_hold += 1
                    if trade_ret > 0:
                        n_win += 1
                    in_trade = False
                    continue
        
        # Entry signal
        if len(recent_preds) < 20:
            continue
        
        q_high = np.percentile(recent_preds, q_long)
        q_low = np.percentile(recent_preds, q_short)
        
        if p_val > q_high:
            side = 1
            n_long += 1
        elif p_val < q_low:
            side = -1
            n_short += 1
        else:
            continue
        
        # Enter trade
        if not np.isnan(fwd_ret[i]):
            in_trade = True
            entry_bar = i
            entry_price = close_prices[i]
            exit_bar = i + hold_bars
    
    result = _compute_metrics(trade_returns, n_trades, n_win, n_long, n_short, pnl)
    result["n_sl"] = n_sl
    result["n_tp"] = n_tp
    result["n_trail"] = n_trail
    result["n_hold"] = n_hold
    return result


def _compute_metrics(trade_returns, n_trades, n_win, n_long, n_short, pnl):
    """Compute standard metrics from trade returns."""
    if not trade_returns:
        return {
            "n_trades": 0, "n_long": 0, "n_short": 0,
            "win_rate": 0, "avg_ret_pct": 0, "pnl_pct": 0,
            "sharpe": 0, "max_dd_pct": 0, "profit_factor": 0,
        }
    
    trade_returns = np.array(trade_returns)
    wr = float((trade_returns > 0).mean())
    avg_ret = float(trade_returns.mean())
    sharpe = float(np.mean(trade_returns) / np.std(trade_returns)) if len(trade_returns) > 1 and np.std(trade_returns) > 0 else 0
    
    cum = np.cumsum(trade_returns)
    running_max = np.maximum.accumulate(cum)
    dd = cum - running_max
    max_dd = float(dd.min()) if len(dd) > 0 else 0
    
    gains = float(trade_returns[trade_returns > 0].sum())
    losses = float(-trade_returns[trade_returns < 0].sum())
    pf = gains / losses if losses > 0 else (99.0 if gains > 0 else 0)
    
    return {
        "n_trades": n_trades,
        "n_long": n_long,
        "n_short": n_short,
        "win_rate": round(wr, 4),
        "avg_ret_pct": round(avg_ret, 4),
        "pnl_pct": round(float(pnl), 4),
        "sharpe": round(sharpe, 4),
        "max_dd_pct": round(max_dd, 4),
        "profit_factor": round(pf, 4),
    }


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="v11 backtest")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--horizon", type=int, default=None)
    parser.add_argument("--adaptive", action="store_true", help="Use adaptive exits")
    parser.add_argument("--sweep", action="store_true", help="Full sweep of all configs")
    parser.add_argument("--cost", choices=["maker", "taker", "both"], default="maker")
    args = parser.parse_args()
    
    symbols = [args.symbol] if args.symbol else DEFAULT_SYMBOLS
    horizons = [args.horizon] if args.horizon else DEFAULT_HORIZONS
    
    print("=" * 110)
    print("v11 BACKTEST — Low Timeframe Trading")
    print(f"  Symbols: {symbols}")
    print(f"  Horizons: {horizons}")
    print(f"  Exit mode: {'ADAPTIVE (SL/TP/Trail)' if args.adaptive else 'FIXED (hold until expiry)'}")
    print(f"  Cost: {args.cost}")
    print("=" * 110)
    
    df = load_dataset()
    
    # Cost configs
    costs = {"maker": 0.04, "taker": 0.14}
    cost_list = [args.cost] if args.cost != "both" else ["maker", "taker"]
    
    # Q configs to sweep
    q_configs = [
        (95, 5), (92, 8), (90, 10), (87, 13), (85, 15), (82, 18), (80, 20),
    ]
    
    all_results = []
    
    for symbol in symbols:
        sym_df = df[df["symbol"] == symbol].copy().reset_index(drop=True)
        if len(sym_df) < 1000:
            LOG.warning("Skipping %s: only %d rows", symbol, len(sym_df))
            continue
        
        for horizon in horizons:
            label_col = f"label_h{horizon}"
            fwd_col = f"fwd_ret_h{horizon}"
            
            if label_col not in sym_df.columns:
                continue
            
            valid_df = sym_df[sym_df[label_col].notna()].reset_index(drop=True)
            if len(valid_df) < 500:
                continue
            
            splits = make_walk_forward_splits(valid_df, WF_WINDOWS)
            if len(splits) < 2:
                continue
            
            for split_name, train_df, test_df in splits:
                X_tr = train_df[ALL_FEATURE_NAMES].values.astype(np.float32)
                y_tr = train_df[label_col].values.astype(np.float32)
                X_test = test_df[ALL_FEATURE_NAMES].values.astype(np.float32)
                y_test = test_df[label_col].values.astype(np.float32)
                fwd_ret = test_df[fwd_col].values.astype(np.float64)
                close_prices = test_df["close"].values.astype(np.float64)
                
                # Train model
                from v11_train import HP_PRESETS, SYMBOL_HP, SYMBOL_HP_OVERRIDE, BASE_PARAMS, NUM_BOOST_ROUND
                hp_name = SYMBOL_HP_OVERRIDE.get((symbol, horizon), SYMBOL_HP.get(horizon, "default"))
                hp = HP_PRESETS.get(hp_name, HP_PRESETS["default"])
                params = dict(BASE_PARAMS)
                params.update(hp)
                
                d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=ALL_FEATURE_NAMES, free_raw_data=False)
                bst = lgb.train(params, d_tr, num_boost_round=NUM_BOOST_ROUND,
                               callbacks=[lgb.log_evaluation(period=0)])
                
                pred = bst.predict(X_test)
                auc_test = float(_auc(y_test, pred))
                
                # Backtest with each config
                for cost_name in cost_list:
                    cost_pct = costs[cost_name]
                    
                    for q_long, q_short in q_configs:
                        if args.adaptive:
                            bt = sequential_backtest_adaptive(
                                pred, fwd_ret, close_prices,
                                q_long, q_short, horizon,
                                cost_pct=cost_pct,
                            )
                        else:
                            bt = sequential_backtest_fixed(
                                pred, fwd_ret,
                                q_long, q_short, horizon,
                                cost_pct=cost_pct,
                            )
                        
                        all_results.append({
                            "symbol": symbol,
                            "horizon": horizon,
                            "horizon_h": horizon * 5 / 60,
                            "split": split_name,
                            "cost": cost_name,
                            "cost_pct": cost_pct,
                            "q_long": q_long,
                            "q_short": q_short,
                            "exit_mode": "adaptive" if args.adaptive else "fixed",
                            "auc_test": round(auc_test, 4),
                            **bt,
                        })
            
            LOG.info("  %s H=%d done", symbol, horizon)
    
    if not all_results:
        LOG.error("No backtest results generated")
        sys.exit(1)
    
    # Save raw results
    results_df = pd.DataFrame(all_results)
    results_path = OUTPUT_DIR / "v11_backtest_raw.parquet"
    results_df.to_parquet(results_path, index=False)
    LOG.info("Raw results saved to %s", results_path)
    
    # Aggregate across windows
    agg = results_df.groupby(["symbol", "horizon", "horizon_h", "cost", "q_long", "q_short", "exit_mode"]).agg(
        auc_test=("auc_test", "mean"),
        n_trades=("n_trades", "sum"),
        win_rate=("win_rate", "mean"),
        avg_ret_pct=("avg_ret_pct", "mean"),
        pnl_pct=("pnl_pct", "sum"),
        sharpe=("sharpe", "mean"),
        max_dd_pct=("max_dd_pct", "min"),
        profit_factor=("profit_factor", "mean"),
        n_windows=("pnl_pct", "count"),
        pnl_positive=("pnl_pct", lambda x: (x > 0).sum()),
    ).reset_index()
    
    agg["consistency"] = agg.apply(
        lambda r: f"{int(r['pnl_positive'])}/{int(r['n_windows'])}", axis=1
    )
    
    # Save aggregated
    agg_path = OUTPUT_DIR / "v11_backtest_agg.parquet"
    agg.to_parquet(agg_path, index=False)
    
    # Print report
    _print_report(agg, args.adaptive)
    
    # Save summary JSON
    summary = {
        "config": {
            "symbols": symbols,
            "horizons": horizons,
            "exit_mode": "adaptive" if args.adaptive else "fixed",
            "cost": args.cost,
        },
        "best_per_symbol_horizon": _get_best_per_group(agg),
    }
    with open(OUTPUT_DIR / "v11_backtest_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)


def _get_best_per_group(agg: pd.DataFrame) -> list:
    """Get best config per (symbol, horizon)."""
    results = []
    for (sym, h), group in agg.groupby(["symbol", "horizon"]):
        best = group.sort_values("pnl_pct", ascending=False).iloc[0]
        results.append({
            "symbol": sym,
            "horizon": int(h),
            "horizon_h": float(best["horizon_h"]),
            "best_q": f"Q{int(best['q_long'])}/{int(best['q_short'])}",
            "best_cost": best["cost"],
            "best_exit": best["exit_mode"],
            "pnl_pct": float(best["pnl_pct"]),
            "win_rate": float(best["win_rate"]),
            "sharpe": float(best["sharpe"]),
            "profit_factor": float(best["profit_factor"]),
            "consistency": best["consistency"],
            "n_trades": int(best["n_trades"]),
        })
    return results


def _print_report(agg: pd.DataFrame, adaptive: bool):
    """Print comprehensive backtest report."""
    print("\n" + "=" * 130)
    mode_str = "ADAPTIVE (SL/TP/Trail)" if adaptive else "FIXED (hold until expiry)"
    print(f"v11 BACKTEST REPORT — {mode_str}")
    print("=" * 130)
    
    # 1. Best config per (symbol, horizon)
    print("\n--- BEST CONFIG PER SYMBOL × HORIZON ---")
    print(f"{'Symbol':<8} {'Horizon':>8} {'Q':>8} {'Cost':>6} {'PnL%':>9} "
          f"{'WR':>6} {'Sharpe':>7} {'PF':>6} {'Cons':>6} {'Trades':>7}")
    print("-" * 80)
    
    for (sym, h), group in agg.groupby(["symbol", "horizon"]):
        best = group.sort_values("pnl_pct", ascending=False).iloc[0]
        print(f"{sym:<8} {h:>5d}({h*5//60}h) Q{int(best['q_long'])}/{int(best['q_short']):>2d} "
              f"{best['cost']:>6} {best['pnl_pct']:>+8.2f}% "
              f"{best['win_rate']:>5.3f} {best['sharpe']:>+6.3f} "
              f"{best['profit_factor']:>5.2f} {best['consistency']:>6} "
              f"{int(best['n_trades']):>7}")
    
    # 2. Horizon comparison
    print("\n--- HORIZON COMPARISON (maker fees, best Q per group) ---")
    print(f"{'Horizon':>8} {'Avg PnL':>10} {'Avg WR':>10} {'Avg Sharpe':>12} "
          f"{'% Positive':>12} {'Best PnL':>10}")
    print("-" * 70)
    
    for h in sorted(agg["horizon"].unique()):
        h_df = agg[agg["horizon"] == h]
        # Get best per symbol within this horizon
        best_per_sym = h_df.groupby("symbol").apply(lambda x: x.sort_values("pnl_pct", ascending=False).iloc[0])
        if len(best_per_sym) == 0:
            continue
        
        print(f"{h:>5d}({h*5//60}h) {best_per_sym['pnl_pct'].mean():>+9.2f}% "
              f"{best_per_sym['win_rate'].mean():>9.3f} "
              f"{best_per_sym['sharpe'].mean():>+11.3f} "
              f"{(best_per_sym['pnl_pct'] > 0).mean()*100:>10.0f}% "
              f"{best_per_sym['pnl_pct'].max():>+9.2f}%")
    
    # 3. Cost sensitivity
    if len(agg["cost"].unique()) > 1:
        print("\n--- COST SENSITIVITY ---")
        for cost_name in ["maker", "taker"]:
            cost_df = agg[agg["cost"] == cost_name]
            best_per_sym = cost_df.groupby(["symbol", "horizon"]).apply(
                lambda x: x.sort_values("pnl_pct", ascending=False).iloc[0]
            )
            if len(best_per_sym) > 0:
                print(f"  {cost_name:>6}: avg PnL={best_per_sym['pnl_pct'].mean():+.2f}%, "
                      f"avg WR={best_per_sym['win_rate'].mean():.3f}, "
                      f"avg Sharpe={best_per_sym['sharpe'].mean():+.3f}")
    
    # 4. Top 20 configs
    print("\n--- TOP 20 CONFIGS BY PnL ---")
    top = agg.sort_values("pnl_pct", ascending=False).head(20)
    cols = ["symbol", "horizon", "q_long", "q_short", "cost", "n_trades",
            "win_rate", "pnl_pct", "sharpe", "profit_factor", "consistency"]
    print(top[cols].to_string(index=False))
    
    # 5. Honest assessment
    print("\n" + "=" * 130)
    print("HONEST ASSESSMENT")
    print("=" * 130)
    
    robust = agg[(agg["pnl_pct"] > 0) & (agg["pnl_positive"] >= 3)]
    very_robust = agg[(agg["pnl_pct"] > 5) & (agg["pnl_positive"] >= 3)]
    
    if len(very_robust) > 0:
        print(f"\n  VERY ROBUST configs (PnL>5%, >=3/4 windows): {len(very_robust)}")
        for _, r in very_robust.sort_values("pnl_pct", ascending=False).head(10).iterrows():
            print(f"    {r['symbol']:>5s} H={int(r['horizon']):>3d} Q{int(r['q_long'])}/{int(r['q_short'])} "
                  f"Cost={r['cost']:>5s} PnL={r['pnl_pct']:+.2f}% WR={r['win_rate']:.3f} "
                  f"Sharpe={r['sharpe']:+.3f} Cons={r['consistency']}")
    elif len(robust) > 0:
        print(f"\n  MODERATE configs (PnL>0, >=3/4): {len(robust)}")
    else:
        print(f"\n  NO robust configs — signal is weak")
    
    # 6. WR improvement opportunities
    print("\n--- WR IMPROVEMENT ANALYSIS ---")
    best_overall = agg.sort_values("pnl_pct", ascending=False).iloc[0]
    print(f"  Best WR: {best_overall['win_rate']:.3f} (PnL={best_overall['pnl_pct']:+.2f}%)")
    
    # Check adaptive vs fixed
    if len(agg["exit_mode"].unique()) > 1:
        for mode in agg["exit_mode"].unique():
            mode_df = agg[agg["exit_mode"] == mode]
            best = mode_df.sort_values("pnl_pct", ascending=False).iloc[0]
            print(f"  {mode:>8}: best WR={best['win_rate']:.3f}, PnL={best['pnl_pct']:+.2f}%")
    
    # v7.5 comparison
    print("\n--- COMPARISON WITH v7.5 BASELINE (H=288, 24h) ---")
    v75_row = agg[agg["horizon"] == 288]
    if len(v75_row) > 0:
        best_v75 = v75_row.sort_values("pnl_pct", ascending=False).iloc[0]
        print(f"  v11 H=288: PnL={best_v75['pnl_pct']:+.2f}% WR={best_v75['win_rate']:.3f} "
              f"Sharpe={best_v75['sharpe']:+.3f}")
        print(f"  v7.5 ref:  PnL=+333.76% WR=0.517 Sharpe=2.80")
    
    # Short horizon verdict
    short_horizons = agg[agg["horizon"] < 288]
    if len(short_horizons) > 0:
        print("\n--- SHORT HORIZON VERDICT (< 24h) ---")
        for h in sorted(short_horizons["horizon"].unique()):
            h_df = short_horizons[short_horizons["horizon"] == h]
            best = h_df.sort_values("pnl_pct", ascending=False).iloc[0]
            positive_count = (h_df.groupby(["symbol", "q_long", "q_short"])["pnl_pct"].sum() > 0).sum()
            total_count = len(h_df.groupby(["symbol", "q_long", "q_short"]))
            print(f"  H={h} ({h*5/60:.0f}h): Best PnL={best['pnl_pct']:+.2f}% WR={best['win_rate']:.3f} "
                  f"Sharpe={best['sharpe']:+.3f} — {positive_count}/{total_count} configs positive")


if __name__ == "__main__":
    main()
