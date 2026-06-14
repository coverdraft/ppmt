#!/usr/bin/env python3
"""
Multi-Alpha OOS Trading Validation

Tests ALL alpha x window combos for actual trading performance,
not just the calibration metric. This finds the sweet spot where
both overlap AND signal differentiation are good enough.

Key insight: calibration optimizes for information x repetition,
but trading needs signal QUALITY — enough granularity to differentiate
winning from losing patterns.
"""

import sys
import os
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import pandas as pd

from ppmt.data.collector import DataCollector
from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.core.matcher import FuzzyMatcher
from ppmt.engine.ppmt import PPMT
from ppmt.engine.signal import SignalType


TOKENS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAME = "1h"
TRAIN_RATIO = 0.70
PATTERN_LENGTH = 5
DAYS_OF_DATA = 600


def download_all():
    """Download data for all tokens."""
    data = {}
    for symbol in TOKENS:
        collector = DataCollector(exchange="binance")
        df = collector.fetch_and_save(symbol, TIMEFRAME, days=DAYS_OF_DATA)
        if not df.empty:
            data[symbol] = df
            print(f"  {symbol}: {len(df)} candles, ${df['low'].min():,.0f}-${df['high'].max():,.0f}")
    return data


def run_trading_sim(train_df, oos_df, alpha, window, pattern_length=5):
    """
    Run OOS trading simulation for one alpha x window combo.
    Returns comprehensive stats.
    """
    try:
        engine = PPMT(
            symbol="TEST",
            asset_class="default",
            sax_alphabet_size=alpha,
            sax_window_size=window,
            sax_strategy="ohlcv",
            fuzzy_threshold=0.80,
            min_confidence=0.05,  # Very low — let data speak
            min_risk_reward=0.3,  # Very low
            weight_profile="default",
        )
    except ValueError:
        return None

    # Build trie
    n_built = engine.build(train_df, pattern_length=pattern_length)
    for trie in [engine.trie_n1, engine.trie_n2, engine.trie_n3, engine.trie_n4]:
        trie.propagate_metadata()

    # Encode OOS
    oos_symbols = engine.sax.encode(oos_df)

    # Collect ALL pattern match results for OOS analysis
    match_results = []
    for i in range(len(oos_symbols) - pattern_length):
        pattern = oos_symbols[i:i + pattern_length]
        candle_idx = min((i + pattern_length) * window, len(oos_df) - 1)
        if candle_idx >= len(oos_df):
            break
        current_price = float(oos_df["close"].iloc[candle_idx])

        result = engine.match(
            current_symbols=pattern,
            current_price=current_price,
        )

        # Collect match info regardless of whether signal generated
        if result.n3_match and result.n3_match.node:
            node = result.n3_match.node
            match_results.append({
                "weighted_confidence": result.weighted_confidence,
                "n3_confidence": result.n3_confidence,
                "n3_hist_count": node.metadata.historical_count,
                "n3_win_rate": node.metadata.win_rate,
                "n3_expected_move": node.metadata.expected_move_pct,
                "n3_risk_reward": node.metadata.risk_reward_ratio,
                "signal_type": result.signal.signal_type.value,
                "is_entry": result.signal.is_entry,
            })
        else:
            match_results.append({
                "weighted_confidence": result.weighted_confidence,
                "signal_type": result.signal.signal_type.value,
                "is_entry": False,
            })

    # Now do actual trading simulation
    trades = []
    in_position = False
    entry_price = 0.0
    entry_idx = 0
    position_direction = ""
    entry_sl = 0.0
    entry_tp = 0.0

    for i in range(len(oos_symbols) - pattern_length):
        pattern = oos_symbols[i:i + pattern_length]
        candle_idx = min((i + pattern_length) * window, len(oos_df) - 1)
        if candle_idx >= len(oos_df):
            break

        row = oos_df.iloc[candle_idx]
        current_price = float(row["close"])
        current_low = float(row["low"])
        current_high = float(row["high"])

        # Check SL/TP hit first (if in position)
        if in_position:
            if position_direction == "LONG":
                if current_low <= entry_sl:
                    # SL hit
                    pnl = ((entry_sl - entry_price) / entry_price) * 100.0
                    trades.append({
                        "direction": "LONG", "entry": entry_price,
                        "exit": entry_sl, "pnl_pct": round(pnl, 4),
                        "exit_reason": "SL_HIT",
                    })
                    in_position = False
                    continue
                if current_high >= entry_tp:
                    # TP hit
                    pnl = ((entry_tp - entry_price) / entry_price) * 100.0
                    trades.append({
                        "direction": "LONG", "entry": entry_price,
                        "exit": entry_tp, "pnl_pct": round(pnl, 4),
                        "exit_reason": "TP_HIT",
                    })
                    in_position = False
                    continue
            elif position_direction == "SHORT":
                if current_high >= entry_sl:
                    pnl = ((entry_price - entry_sl) / entry_price) * 100.0
                    trades.append({
                        "direction": "SHORT", "entry": entry_price,
                        "exit": entry_sl, "pnl_pct": round(pnl, 4),
                        "exit_reason": "SL_HIT",
                    })
                    in_position = False
                    continue
                if current_low <= entry_tp:
                    pnl = ((entry_price - entry_tp) / entry_price) * 100.0
                    trades.append({
                        "direction": "SHORT", "entry": entry_price,
                        "exit": entry_tp, "pnl_pct": round(pnl, 4),
                        "exit_reason": "TP_HIT",
                    })
                    in_position = False
                    continue

        # Get signal
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
            entry_idx = i
            position_direction = signal.direction or "LONG"
            entry_sl = signal.sl_price or (current_price * 0.97 if position_direction == "LONG" else current_price * 1.03)
            entry_tp = signal.tp_price or (current_price * 1.05 if position_direction == "LONG" else current_price * 0.95)

        elif in_position and signal.is_exit:
            exit_price = current_price
            if position_direction == "LONG":
                pnl = ((exit_price - entry_price) / entry_price) * 100.0
            else:
                pnl = ((entry_price - exit_price) / entry_price) * 100.0

            trades.append({
                "direction": position_direction, "entry": entry_price,
                "exit": exit_price, "pnl_pct": round(pnl, 4),
                "exit_reason": signal.signal_type.value,
            })
            in_position = False

    # Close open position at end
    if in_position:
        last_price = float(oos_df["close"].iloc[-1])
        if position_direction == "LONG":
            pnl = ((last_price - entry_price) / entry_price) * 100.0
        else:
            pnl = ((entry_price - last_price) / entry_price) * 100.0
        trades.append({
            "direction": position_direction, "entry": entry_price,
            "exit": last_price, "pnl_pct": round(pnl, 4),
            "exit_reason": "END_OF_DATA",
        })

    # Compute stats
    total_matches = len(match_results)
    entries_generated = sum(1 for m in match_results if m["is_entry"])
    avg_confidence = np.mean([m["weighted_confidence"] for m in match_results]) if match_results else 0.0

    if trades:
        pnls = [t["pnl_pct"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        win_rate = len(wins) / len(trades)
        profit_factor = sum(wins) / sum(losses) if losses else 0.0
        total_pnl = sum(pnls)
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        max_dd = abs(min(cumulative - peak))
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(24 * 365) if len(pnls) > 1 and np.std(pnls) > 0 else 0.0

        # Monte Carlo
        mc_profits = []
        for _ in range(500):
            reshuffled = np.random.permutation(pnls)
            mc_profits.append(np.sum(reshuffled))
        mc_profits = np.array(mc_profits)
    else:
        win_rate = profit_factor = total_pnl = max_dd = sharpe = 0.0
        mc_profits = np.array([0])

    # Symbol distribution
    encoder = SAXEncoder(alphabet_size=alpha, window_size=window, strategy="ohlcv")
    train_syms = encoder.encode(train_df)
    sym_counts = {}
    for s in train_syms:
        sym_counts[s] = sym_counts.get(s, 0) + 1
    total = len(train_syms) if train_syms else 1
    sym_dist = {s: round(c / total, 4) for s, c in sorted(sym_counts.items())}
    max_conc = max(sym_counts.values()) / total if sym_counts else 1.0

    # Overlap
    overlap = max(len(train_syms) - pattern_length, 1) / max(n_built, 1)

    # OOS match rate (from match_results)
    matched = sum(1 for m in match_results if m.get("n3_hist_count", 0) > 0)
    oos_match_rate = matched / total_matches if total_matches > 0 else 0.0

    return {
        "alpha": alpha,
        "window": window,
        "patterns_built": n_built,
        "overlap_ratio": round(overlap, 2),
        "oos_match_rate": round(oos_match_rate, 4),
        "symbol_distribution": sym_dist,
        "max_concentration": round(max_conc, 4),
        "information": round(1 - max_conc, 4),
        "total_trades": len(trades),
        "entries_available": entries_generated,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "total_pnl_pct": round(total_pnl, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe_approx": round(sharpe, 4),
        "avg_confidence": round(avg_confidence, 4),
        "long_trades": sum(1 for t in trades if t["direction"] == "LONG"),
        "short_trades": sum(1 for t in trades if t["direction"] == "SHORT"),
        "sl_hits": sum(1 for t in trades if t["exit_reason"] == "SL_HIT"),
        "tp_hits": sum(1 for t in trades if t["exit_reason"] == "TP_HIT"),
        "mc_mean_pnl": round(float(np.mean(mc_profits)), 4),
        "mc_std_pnl": round(float(np.std(mc_profits)), 4),
        "mc_profitable_pct": round(float(np.mean(mc_profits > 0) * 100), 2),
    }


def main():
    print("=" * 80)
    print("  PPMT v0.6.2 — Multi-Alpha OOS Trading Validation")
    print("  Additive OHLCV Composite + Alpha×Window Grid for Trading")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)

    # Download
    print("\n  STEP 1: Download Data")
    data = download_all()

    # Test grid
    ALPHAS = [3, 4, 5]
    WINDOWS = [5, 7, 10]

    all_results = {}

    for symbol, df in data.items():
        print(f"\n{'='*80}")
        print(f"  {symbol} — Multi-Alpha Trading Comparison")
        print(f"  Data: {len(df)} candles | Train: {int(len(df)*TRAIN_RATIO)} | OOS: {len(df) - int(len(df)*TRAIN_RATIO)}")
        print(f"{'='*80}")

        n = len(df)
        split = int(n * TRAIN_RATIO)
        train_df = df.iloc[:split]
        oos_df = df.iloc[split:]

        results = []
        for alpha in ALPHAS:
            for window in WINDOWS:
                print(f"  Testing alpha={alpha} window={window}...", end="", flush=True)
                r = run_trading_sim(train_df, oos_df, alpha, window, PATTERN_LENGTH)
                if r:
                    results.append(r)
                    pnl_str = f"{r['total_pnl_pct']:+.2f}%" if r['total_trades'] > 0 else "NO TRADES"
                    print(f" trades={r['total_trades']} WR={r['win_rate']:.1%} "
                          f"PF={r['profit_factor']:.2f} PnL={pnl_str} "
                          f"overlap={r['overlap_ratio']:.1f}x match={r['oos_match_rate']:.1%}")
                else:
                    print(" SKIP")

        all_results[symbol] = results

        # Print summary table
        print(f"\n  {symbol} Summary:")
        print(f"  {'alpha':<6} {'win':<6} {'overlap':<9} {'match%':<9} {'conc%':<8} "
              f"{'trades':<8} {'WR':<8} {'PF':<7} {'PnL%':<10} {'Sharpe':<8} {'MC prof%':<9}")
        print("  " + "-" * 85)
        for r in sorted(results, key=lambda x: x["total_pnl_pct"], reverse=True):
            print(f"  {r['alpha']:<6} {r['window']:<6} {r['overlap_ratio']:<9.1f} "
                  f"{r['oos_match_rate']:<9.1%} {r['max_concentration']:<8.1%} "
                  f"{r['total_trades']:<8} {r['win_rate']:<8.1%} {r['profit_factor']:<7.2f} "
                  f"{r['total_pnl_pct']:<+10.2f} {r['sharpe_approx']:<8.2f} {r['mc_profitable_pct']:<9.1f}")

    # Cross-token comparison for best alpha per token
    print(f"\n{'='*80}")
    print("  CROSS-TOKEN BEST ALPHA COMPARISON")
    print(f"{'='*80}")

    for symbol, results in all_results.items():
        # Find best by total_pnl (with at least 5 trades)
        valid = [r for r in results if r["total_trades"] >= 5]
        if valid:
            best = max(valid, key=lambda r: r["total_pnl_pct"])
            print(f"\n  {symbol}: Best trading alpha={best['alpha']} window={best['window']}")
            print(f"    PnL={best['total_pnl_pct']:+.2f}%  WR={best['win_rate']:.1%}  "
                  f"PF={best['profit_factor']:.2f}  Trades={best['total_trades']}  "
                  f"MC Prof={best['mc_profitable_pct']:.1f}%")

    # Save results
    output_path = "/home/z/my-project/download/multi_alpha_oos_results.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_path}")

    return all_results


if __name__ == "__main__":
    main()
