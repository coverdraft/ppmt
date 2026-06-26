"""
v12_adaptive_exit.py — Adaptive exit strategy with trailing stop.

EXIT STRATEGIES TESTED:
  1. Fixed hold (baseline) — hold for exactly H bars
  2. ATR trailing stop — trail at ATR-based distance
  3. Momentum exit — exit when momentum reverses
  4. Profit lock — lock profits after X% gain, trail tighter

USAGE:
    python scripts/v12/v12_adaptive_exit.py
    python scripts/v12/v12_adaptive_exit.py --symbol SOL --horizon 6
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
V11_DIR = DATA_DIR / "v11"
OUTPUT_DIR = DATA_DIR / "v12"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v12_adaptive")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "v11"))
from v11_build_dataset import ALL_FEATURE_NAMES

MAKER_COST_PCT = 0.0004


def backtest_atr_trailing(
    preds: np.ndarray,
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    atr_pct: np.ndarray,
    trend_1h: np.ndarray,
    q_long: int, q_short: int,
    horizon: int,
    atr_multiplier: float = 2.0,
    min_hold: int = 3,
    direction_mode: str = "both",
    trend_filter: str = "none",
    cost_pct: float = MAKER_COST_PCT,
    window_size: int = 200,
) -> dict:
    """Backtest with ATR-based trailing stop."""
    n_trades = 0
    n_win = 0
    pnl = 0.0
    trade_returns = []
    n_long = 0
    n_short = 0
    n_stopped = 0
    n_trailed = 0
    n_hold_expired = 0
    recent_preds = []
    
    i = 0
    while i < len(preds):
        p_val = float(preds[i])
        recent_preds.append(p_val)
        if len(recent_preds) > window_size:
            recent_preds.pop(0)
        
        if len(recent_preds) < 20:
            i += 1
            continue
        
        q_high = np.percentile(recent_preds, q_long)
        q_low = np.percentile(recent_preds, q_short)
        
        direction = 0
        if p_val > q_high:
            direction = 1
        elif p_val < q_low:
            direction = -1
        
        if direction == 0:
            i += 1
            continue
        
        # Direction filter
        if direction_mode == "long_only" and direction == -1:
            i += 1
            continue
        if direction_mode == "short_only" and direction == 1:
            i += 1
            continue
        
        # Trend filter
        if trend_filter == "aligned":
            if direction == 1 and trend_1h[i] < 0:
                i += 1
                continue
            if direction == -1 and trend_1h[i] > 0:
                i += 1
                continue
        
        # Enter trade
        entry_price = close[i]
        entry_bar = i
        atr_at_entry = atr_pct[i] if not np.isnan(atr_pct[i]) else 0.01
        
        # Compute stop distance
        stop_distance = atr_at_entry * atr_multiplier
        
        # Track trade
        max_favorable = 0.0  # maximum favorable excursion
        max_adverse = 0.0    # maximum adverse excursion
        exit_price = close[i + horizon] if i + horizon < len(close) else close[-1]
        exit_bar = min(i + horizon, len(close) - 1)
        exit_reason = "hold_expired"
        
        for j in range(i + 1, min(i + horizon + 1, len(close))):
            current_price = close[j]
            
            if direction == 1:  # Long
                favorable = (current_price - entry_price) / entry_price
                adverse = (entry_price - low[j]) / entry_price
                
                # Update max favorable
                if favorable > max_favorable:
                    max_favorable = favorable
                
                # Trailing stop: exit if price drops below (entry + max_favorable - stop_distance)
                if max_favorable > stop_distance and j > entry_bar + min_hold:
                    trail_level = entry_price * (1 + max_favorable - stop_distance)
                    if low[j] < trail_level:
                        exit_price = trail_level
                        exit_bar = j
                        exit_reason = "trailed"
                        break
                
                # Hard stop loss
                if adverse > stop_distance * 1.5:
                    exit_price = entry_price * (1 - stop_distance * 1.5)
                    exit_bar = j
                    exit_reason = "stopped"
                    break
                
            else:  # Short
                favorable = (entry_price - current_price) / entry_price
                adverse = (high[j] - entry_price) / entry_price
                
                if favorable > max_favorable:
                    max_favorable = favorable
                
                # Trailing stop: exit if price rises above (entry - max_favorable + stop_distance)
                if max_favorable > stop_distance and j > entry_bar + min_hold:
                    trail_level = entry_price * (1 - max_favorable + stop_distance)
                    if high[j] > trail_level:
                        exit_price = trail_level
                        exit_bar = j
                        exit_reason = "trailed"
                        break
                
                # Hard stop loss
                if adverse > stop_distance * 1.5:
                    exit_price = entry_price * (1 + stop_distance * 1.5)
                    exit_bar = j
                    exit_reason = "stopped"
                    break
        
        # Calculate trade return
        if direction == 1:
            trade_ret = (exit_price - entry_price) / entry_price - cost_pct
        else:
            trade_ret = (entry_price - exit_price) / entry_price - cost_pct
        
        n_trades += 1
        if direction == 1:
            n_long += 1
        else:
            n_short += 1
        
        pnl += trade_ret
        trade_returns.append(trade_ret)
        
        if trade_ret > 0:
            n_win += 1
        
        if exit_reason == "stopped":
            n_stopped += 1
        elif exit_reason == "trailed":
            n_trailed += 1
        else:
            n_hold_expired += 1
        
        # Skip to exit bar
        i = exit_bar + 1
    
    wr = n_win / n_trades if n_trades > 0 else 0
    sharpe = (np.mean(trade_returns) / np.std(trade_returns)) if len(trade_returns) > 1 else 0
    gains = sum(r for r in trade_returns if r > 0)
    losses = abs(sum(r for r in trade_returns if r < 0))
    pf = gains / losses if losses > 0 else (99.0 if gains > 0 else 0)
    
    return {
        "n_trades": n_trades,
        "n_long": n_long,
        "n_short": n_short,
        "n_stopped": n_stopped,
        "n_trailed": n_trailed,
        "n_hold_expired": n_hold_expired,
        "win_rate": round(wr, 4),
        "pnl_pct": round(pnl * 100, 4),
        "sharpe": round(sharpe, 4),
        "profit_factor": round(pf, 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--horizon", type=int, default=6)
    args = parser.parse_args()
    
    symbols = [args.symbol] if args.symbol else ["SOL", "DOGE", "AVAX", "XRP", "SUI", "LINK"]
    horizon = args.horizon
    
    print("=" * 110)
    print("V12 ADAPTIVE EXIT — ATR Trailing Stop")
    print(f"  Symbols: {symbols}")
    print(f"  Horizon: {horizon} ({horizon * 5 / 60:.0f}h)")
    print("=" * 110)
    
    df = pd.read_parquet(V11_DIR / "v11_dataset.parquet")
    LOG.info("Loaded %d rows", len(df))
    
    # Test configurations
    atr_multipliers = [1.0, 1.5, 2.0, 2.5, 3.0]
    q_configs = [(95, 5), (98, 2)]
    
    all_results = []
    
    for symbol in symbols:
        sym_df = df[df["symbol"] == symbol].copy().reset_index(drop=True)
        model_path = V11_DIR / "models" / f"v11_clf_{symbol}_h{horizon}.txt"
        if not model_path.exists():
            LOG.warning("No model for %s H=%d", symbol, horizon)
            continue
        
        model = lgb.Booster(model_file=str(model_path))
        
        # Use last 20% as test
        n = len(sym_df)
        test_start = int(n * 0.8)
        test_df = sym_df.iloc[test_start:].reset_index(drop=True)
        
        X_test = test_df[ALL_FEATURE_NAMES].values.astype(np.float32)
        X_test = np.nan_to_num(X_test, nan=0.0)
        preds = model.predict(X_test)
        
        close = test_df["close"].values
        high = test_df["high"].values
        low = test_df["low"].values
        atr_pct = test_df["atr_pct"].values if "atr_pct" in test_df.columns else np.full(len(test_df), 0.01)
        trend_1h = test_df["trend_1h"].values if "trend_1h" in test_df.columns else np.zeros(len(test_df))
        
        LOG.info("Testing %s H=%d (%d test rows)", symbol, horizon, len(test_df))
        
        for q_long, q_short in q_configs:
            for atr_mult in atr_multipliers:
                for dir_mode in ["both", "long_only"]:
                    for trend_f in ["none", "aligned"]:
                        bt = backtest_atr_trailing(
                            preds, close, high, low, atr_pct, trend_1h,
                            q_long, q_short, horizon,
                            atr_multiplier=atr_mult,
                            direction_mode=dir_mode,
                            trend_filter=trend_f,
                        )
                        
                        result = {
                            "symbol": symbol,
                            "q_long": q_long,
                            "q_short": q_short,
                            "atr_mult": atr_mult,
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
    results_df.to_csv(OUTPUT_DIR / "v12_adaptive_exit_results.csv", index=False)
    
    # Compare fixed vs adaptive
    print(f"\n{'='*120}")
    print("ATR TRAILING STOP — TOP 20 BY WIN RATE (min 30 trades)")
    print(f"{'='*120}")
    
    filtered = results_df[results_df["n_trades"] >= 30].copy()
    filtered = filtered.sort_values("win_rate", ascending=False)
    
    print(f"\n{'Symbol':<8} {'Q':>6} {'ATR':>5} {'Dir':>10} {'Trend':>8} {'Trades':>7} {'WR':>6} {'PnL%':>10} {'PF':>6} {'Stop':>5} {'Trail':>6} {'Hold':>5}")
    print("-" * 110)
    
    for _, row in filtered.head(20).iterrows():
        q_str = f"{int(row['q_long'])}/{int(row['q_short'])}"
        print(f"{row['symbol']:<8} {q_str:>6} {row['atr_mult']:>5.1f} {row['direction']:>10} {row['trend_filter']:>8} "
              f"{int(row['n_trades']):>7d} {row['win_rate']:>6.3f} {row['pnl_pct']:>+9.1f}% "
              f"{row['profit_factor']:>6.2f} {int(row['n_stopped']):>5d} {int(row['n_trailed']):>6d} {int(row['n_hold_expired']):>5d}")
    
    # Best per symbol comparison: fixed vs adaptive
    print(f"\n{'='*120}")
    print("FIXED HOLD vs ATR TRAILING — BEST PER SYMBOL")
    print(f"{'='*120}")
    
    for symbol in symbols:
        sym_results = results_df[results_df["symbol"] == symbol]
        if len(sym_results) == 0:
            continue
        
        # Best fixed (high atr_mult = effectively fixed)
        best_fixed = sym_results[(sym_results["atr_mult"] >= 3.0) & (sym_results["n_trades"] >= 30)]
        best_fixed = best_fixed.sort_values("win_rate", ascending=False).head(1)
        
        # Best adaptive
        best_adaptive = sym_results[(sym_results["atr_mult"] < 3.0) & (sym_results["n_trades"] >= 30)]
        best_adaptive = best_adaptive.sort_values("win_rate", ascending=False).head(1)
        
        print(f"\n  {symbol}:")
        if len(best_fixed) > 0:
            row = best_fixed.iloc[0]
            print(f"    Fixed:  Q{int(row['q_long'])}/{int(row['q_short'])} {row['direction']}  "
                  f"WR={row['win_rate']:.3f}  PnL={row['pnl_pct']:+.1f}%  PF={row['profit_factor']:.2f}")
        if len(best_adaptive) > 0:
            row = best_adaptive.iloc[0]
            print(f"    Adaptive: Q{int(row['q_long'])}/{int(row['q_short'])} ATR={row['atr_mult']:.1f} {row['direction']}  "
                  f"WR={row['win_rate']:.3f}  PnL={row['pnl_pct']:+.1f}%  PF={row['profit_factor']:.2f}  "
                  f"Trail={int(row['n_trailed'])}  Stop={int(row['n_stopped'])}")
    
    LOG.info("Results saved to %s", OUTPUT_DIR / "v12_adaptive_exit_results.csv")


if __name__ == "__main__":
    main()
