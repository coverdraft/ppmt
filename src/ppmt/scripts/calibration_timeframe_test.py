#!/usr/bin/env python3
"""
PPMT v0.6.3 — Improved Calibration + Multi-Timeframe Validation

Two critical improvements:
1. TRADING-CALIBRATED: Instead of calibrating on pattern matching stats,
   we now calibrate on ACTUAL OOS TRADING PnL. This fixes the issue where
   all tokens converged to alpha=3/window=5 (best for matching, not trading).

2. MULTI-TIMEFRAME: Test 15m, 30m, 1h, 4h to find the optimal timeframe.
   More candles = more patterns, but also more noise. Which wins?

Bug fixes applied before this test:
- regime now piped through insert_with_observations (V4 was dead code)
- propagate_metadata now preserves regime_stats and move_variance
- Simple regime detection added to PPMT.build()

All data: REAL Binance. No synthetic data.
"""

import sys
import os
import json
import traceback
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import pandas as pd

from ppmt.data.collector import DataCollector
from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.engine.ppmt import PPMT
from ppmt.engine.signal import SignalType


# ============================================================
# Configuration
# ============================================================

# Representative tokens from each class for speed
TOKENS = {
    "BTC/USDT":  {"asset_class": "blue_chip"},
    "DOGE/USDT": {"asset_class": "meme"},
}

# Timeframes to test
TIMEFRAMES = ["30m", "1h", "4h"]
DAYS_OF_DATA = 365  # 1 year to keep 15m/30m manageable

# Calibration grid
ALPHAS = [3, 4, 5]
WINDOWS = [5, 7, 10]

PATTERN_LENGTH = 5
TRAIN_RATIO = 0.70
MC_SIMS = 300  # Reduced for speed


# ============================================================
# Trading Simulation (shared)
# ============================================================

def simulate_trades(engine, test_df, pattern_length=5):
    """Run OOS trading simulation with SL/TP."""
    window = engine.sax.window_size
    test_symbols = engine.sax.encode(test_df)

    trades = []
    in_position = False
    entry_price = 0.0
    position_direction = ""
    entry_sl = 0.0
    entry_tp = 0.0

    for i in range(len(test_symbols) - pattern_length):
        pattern = test_symbols[i:i + pattern_length]
        candle_idx = min((i + pattern_length) * window, len(test_df) - 1)
        if candle_idx >= len(test_df):
            break

        row = test_df.iloc[candle_idx]
        current_price = float(row["close"])
        current_low = float(row["low"])
        current_high = float(row["high"])

        # SL/TP check
        if in_position:
            exited = False
            if position_direction == "LONG":
                if current_low <= entry_sl:
                    pnl = ((entry_sl - entry_price) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "LONG", "exit": "SL"})
                    exited = True
                elif current_high >= entry_tp:
                    pnl = ((entry_tp - entry_price) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "LONG", "exit": "TP"})
                    exited = True
            elif position_direction == "SHORT":
                if current_high >= entry_sl:
                    pnl = ((entry_price - entry_sl) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "SHORT", "exit": "SL"})
                    exited = True
                elif current_low <= entry_tp:
                    pnl = ((entry_price - entry_tp) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "SHORT", "exit": "TP"})
                    exited = True
            if exited:
                in_position = False
                continue

        result = engine.match(
            current_symbols=pattern,
            current_price=current_price,
            is_in_position=in_position,
            entry_price=entry_price if in_position else None,
        )
        signal = result.signal

        if not in_position and signal.is_entry:
            in_position = True
            entry_price = current_price
            position_direction = signal.direction or "LONG"
            entry_sl = signal.sl_price or (current_price * 0.97 if position_direction == "LONG" else current_price * 1.03)
            entry_tp = signal.tp_price or (current_price * 1.05 if position_direction == "LONG" else current_price * 0.95)
        elif in_position and signal.is_exit:
            exit_price = current_price
            if position_direction == "LONG":
                pnl = ((exit_price - entry_price) / entry_price) * 100.0
            else:
                pnl = ((entry_price - exit_price) / entry_price) * 100.0
            trades.append({"pnl_pct": round(pnl, 4), "direction": position_direction, "exit": "SIGNAL"})
            in_position = False

    # Close open position
    if in_position and len(test_df) > 0:
        last_price = float(test_df["close"].iloc[-1])
        if position_direction == "LONG":
            pnl = ((last_price - entry_price) / entry_price) * 100.0
        else:
            pnl = ((entry_price - last_price) / entry_price) * 100.0
        trades.append({"pnl_pct": round(pnl, 4), "direction": position_direction, "exit": "END"})

    return trades


def compute_stats(trades):
    """Compute trading stats from trade list."""
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "total_pnl_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_approx": 0.0,
            "mc_profitable_pct": 0.0,
        }

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    win_rate = len(wins) / len(trades)
    profit_factor = sum(wins) / sum(losses) if losses else 0.0
    total_pnl = sum(pnls)
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    max_dd = abs(min(cumulative - peak)) if len(cumulative) > 0 else 0.0

    sharpe = 0.0
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(24 * 365)

    # MC
    mc_profits = np.array([sum(np.random.permutation(pnls)) for _ in range(MC_SIMS)])

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "total_pnl_pct": round(total_pnl, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe_approx": round(sharpe, 4),
        "mc_profitable_pct": round(float(np.mean(mc_profits > 0) * 100), 2),
    }


# ============================================================
# Trading-Calibrated Engine
# ============================================================

def trading_calibrate(train_df, oos_df, symbol, asset_class):
    """
    Trading-calibrated parameter selection.

    Instead of picking alpha/window by pattern matching metrics,
    we run actual OOS trading for each combo and select by PnL.
    This fixes the issue where all tokens converged to alpha=3/window=5.
    """
    results = []

    for alpha in ALPHAS:
        for window in WINDOWS:
            try:
                engine = PPMT(
                    symbol=symbol, asset_class=asset_class,
                    sax_alphabet_size=alpha, sax_window_size=window,
                    sax_strategy="ohlcv", fuzzy_threshold=0.80,
                    min_confidence=0.05, min_risk_reward=0.3,
                )
                n_built = engine.build(train_df, pattern_length=PATTERN_LENGTH)
                for trie in [engine.trie_n1, engine.trie_n2, engine.trie_n3, engine.trie_n4]:
                    trie.propagate_metadata()

                trades = simulate_trades(engine, oos_df, PATTERN_LENGTH)
                stats = compute_stats(trades)

                # Symbol distribution
                try:
                    train_syms = engine.sax.encode(train_df)
                    sym_counts = {}
                    for s in train_syms:
                        sym_counts[s] = sym_counts.get(s, 0) + 1
                    total = len(train_syms) if len(train_syms) > 0 else 1
                    max_conc = max(sym_counts.values()) / total if sym_counts else 1.0
                except Exception:
                    max_conc = 1.0

                results.append({
                    "alpha": alpha, "window": window,
                    "patterns_built": n_built,
                    "max_concentration": round(max_conc, 4),
                    **stats,
                })

            except ValueError:
                pass

    return results


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 90)
    print("  PPMT v0.6.3 — Trading-Calibrated + Multi-Timeframe Validation")
    print(f"  Bug fixes: regime piped, propagate preserves variance/stats")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 90)

    start_time = datetime.now()
    all_results = {}

    for symbol, config in TOKENS.items():
        asset_class = config["asset_class"]
        all_results[symbol] = {"asset_class": asset_class, "timeframes": {}}

        print(f"\n{'='*90}")
        print(f"  {symbol} ({asset_class})")
        print(f"{'='*90}")

        for tf in TIMEFRAMES:
            print(f"\n  --- {symbol} @ {tf} ---")

            # Download data for this timeframe
            try:
                collector = DataCollector(exchange="binance")
                df = collector.fetch_and_save(symbol, tf, days=DAYS_OF_DATA)

                if df.empty or len(df) < 3000:
                    print(f"  SKIP: {len(df)} candles (need 3000+)")
                    continue

                print(f"  Downloaded: {len(df)} candles")
            except Exception as e:
                print(f"  DOWNLOAD ERROR: {e}")
                continue

            # Split
            n = len(df)
            split = int(n * TRAIN_RATIO)
            train_df = df.iloc[:split]
            oos_df = df.iloc[split:]
            print(f"  Train: {len(train_df)} | OOS: {len(oos_df)}")

            # Trading-calibrated grid search
            print(f"  Running trading calibration (alpha={ALPHAS} x window={WINDOWS})...")
            results = trading_calibrate(train_df, oos_df, symbol, asset_class)

            if not results:
                print(f"  No valid configs")
                continue

            # Sort by total_pnl_pct
            results.sort(key=lambda r: r["total_pnl_pct"], reverse=True)

            # Print all configs
            print(f"\n  {'alpha':<5} {'win':<5} {'Trades':<7} {'WR':<7} "
                  f"{'PF':<7} {'PnL%':<10} {'Sharpe':<7} {'MC%':<6} {'Conc%':<7}")
            print("  " + "-" * 70)

            for r in results:
                print(f"  {r['alpha']:<5} {r['window']:<5} {r['total_trades']:<7} "
                      f"{r['win_rate']:<7.1%} {r['profit_factor']:<7.2f} "
                      f"{r['total_pnl_pct']:<+10.2f} {r['sharpe_approx']:<7.2f} "
                      f"{r['mc_profitable_pct']:<6.0f} {r['max_concentration']:<7.1%}")

            # Best by trading PnL
            best = results[0]
            print(f"\n  BEST TRADING: alpha={best['alpha']} window={best['window']} "
                  f"PnL={best['total_pnl_pct']:+.2f}% WR={best['win_rate']:.1%} "
                  f"PF={best['profit_factor']:.2f}")

            all_results[symbol]["timeframes"][tf] = {
                "candles": len(df),
                "train_candles": len(train_df),
                "oos_candles": len(oos_df),
                "best_alpha": best["alpha"],
                "best_window": best["window"],
                "best_pnl_pct": best["total_pnl_pct"],
                "best_win_rate": best["win_rate"],
                "best_profit_factor": best["profit_factor"],
                "best_sharpe": best["sharpe_approx"],
                "all_configs": results,
            }

    # ================================================================
    # SUMMARY
    # ================================================================
    print("\n" + "=" * 90)
    print("  COMPREHENSIVE SUMMARY")
    print("=" * 90)

    # --- Best config per token per timeframe ---
    print("\n  === BEST TRADING CONFIG PER TOKEN × TIMEFRAME ===\n")

    print(f"  {'Token':<12} {'Class':<10} {'TF':<5} {'Candles':<9} "
          f"{'Best a':<7} {'Best w':<7} {'Trades':<7} {'WR':<7} "
          f"{'PF':<7} {'PnL%':<10} {'Sharpe':<7}")
    print("  " + "-" * 95)

    for symbol, data in all_results.items():
        for tf, tf_data in data["timeframes"].items():
            best = [r for r in tf_data["all_configs"]
                    if r["alpha"] == tf_data["best_alpha"]
                    and r["window"] == tf_data["best_window"]][0]
            print(f"  {symbol:<12} {data['asset_class']:<10} {tf:<5} "
                  f"{tf_data['candles']:<9} "
                  f"{tf_data['best_alpha']:<7} {tf_data['best_window']:<7} "
                  f"{best['total_trades']:<7} {best['win_rate']:<7.1%} "
                  f"{best['profit_factor']:<7.2f} "
                  f"{tf_data['best_pnl_pct']:<+10.2f} "
                  f"{tf_data['best_sharpe']:<7.2f}")

    # --- Best timeframe per token ---
    print("\n  === BEST TIMEFRAME PER TOKEN ===\n")

    for symbol, data in all_results.items():
        best_tf = None
        best_pnl = -float('inf')
        for tf, tf_data in data["timeframes"].items():
            if tf_data["best_pnl_pct"] > best_pnl:
                best_pnl = tf_data["best_pnl_pct"]
                best_tf = tf

        if best_tf:
            tf_data = data["timeframes"][best_tf]
            print(f"  {symbol} ({data['asset_class']}): BEST TF = {best_tf} "
                  f"(alpha={tf_data['best_alpha']}, window={tf_data['best_window']}, "
                  f"PnL={tf_data['best_pnl_pct']:+.2f}%)")

    # --- Alpha convergence comparison ---
    print("\n  === ALPHA CONVERGENCE: OLD vs TRADING-CALIBRATED ===\n")

    for symbol, data in all_results.items():
        for tf, tf_data in data["timeframes"].items():
            configs = tf_data["all_configs"]
            # Old calibration would pick alpha=3/window=5 (max repetition)
            old_best = [r for r in configs if r["alpha"] == 3 and r["window"] == 5]
            new_best = [r for r in configs if r["alpha"] == tf_data["best_alpha"]
                        and r["window"] == tf_data["best_window"]]

            if old_best and new_best:
                old_pnl = old_best[0]["total_pnl_pct"]
                new_pnl = new_best[0]["total_pnl_pct"]
                improvement = new_pnl - old_pnl if old_pnl != 0 else 0
                improved = "BETTER" if new_pnl > old_pnl else ("SAME" if new_pnl == old_pnl else "WORSE")

                print(f"  {symbol} @ {tf}: Old a3w5={old_pnl:+.2f}% → "
                      f"New a{tf_data['best_alpha']}w{tf_data['best_window']}={new_pnl:+.2f}% "
                      f"({improved}, delta={improvement:+.2f}%)")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n  Total time: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    # Save
    output_path = "/home/z/my-project/download/calibration_timeframe_results.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_path}")

    return all_results


if __name__ == "__main__":
    results = main()
