#!/usr/bin/env python3
"""
Walk-Forward OOS Validation + Weight Sensitivity Analysis

Two critical validations in one script:

1. WALK-FORWARD TEST
   Instead of a single 70/30 split, uses rolling expanding windows:
   - Train on [0..T], test on [T..T+step]
   - Then train on [0..T+step], test on [T+step..T+2*step]
   - Each step, the trie is REBUILT from scratch (no lookahead)
   - If single-split results are from lookahead, walk-forward will expose it

2. WEIGHT SENSITIVITY
   Tests different composite weight combinations:
   - Current: 0.40/0.35/0.25
   - Equal: 0.33/0.33/0.33
   - Body-heavy: 0.50/0.30/0.20
   - Direction-heavy: 0.30/0.50/0.20
   - Volume-heavy: 0.30/0.20/0.50
   - Extreme: 0.60/0.25/0.15
   If all produce similar results, the weights don't matter much.
   If one dominates, we know what drives performance.

All data: REAL Binance. No synthetic data.
"""

import sys
import os
import json
import copy
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

TOKENS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAME = "1h"
DAYS_OF_DATA = 600

# Walk-forward params
WF_INITIAL_TRAIN = 5000   # First training window (candles)
WF_STEP = 1000            # Step forward each iteration
WF_MIN_TEST = 500         # Minimum test window

# Weight sensitivity configs
# (body_position_weight, direction_weight, vol_signal_weight)
WEIGHT_CONFIGS = {
    "current_0.40_0.35_0.25": (0.40, 0.35, 0.25),
    "equal_0.33_0.33_0.33": (0.333, 0.333, 0.334),
    "body_heavy_0.50_0.30_0.20": (0.50, 0.30, 0.20),
    "direction_heavy_0.30_0.50_0.20": (0.30, 0.50, 0.20),
    "volume_heavy_0.30_0.20_0.50": (0.30, 0.20, 0.50),
    "extreme_body_0.60_0.25_0.15": (0.60, 0.25, 0.15),
}


# ============================================================
# Patchable SAX encoder for weight testing
# ============================================================

def make_patched_encoder(alpha, window, body_w, dir_w, vol_w):
    """Create a SAXEncoder with custom composite weights."""
    encoder = SAXEncoder(alphabet_size=alpha, window_size=window, strategy="ohlcv")

    # Monkey-patch _extract_series to use custom weights
    original_extract = encoder._extract_series

    def patched_extract(df):
        if len(df) == 0:
            return np.array([])
        if encoder.strategy == "close":
            return df["close"].values.astype(float)
        elif encoder.strategy == "typical_price":
            return ((df["high"] + df["low"] + df["close"]) / 3.0).values.astype(float)
        elif encoder.strategy == "ohlcv":
            o = df["open"].values.astype(float)
            h = df["high"].values.astype(float)
            l = df["low"].values.astype(float)
            c = df["close"].values.astype(float)
            v = df["volume"].values.astype(float) if "volume" in df.columns else np.ones_like(c)
            rng = h - l
            rng = np.where(rng == 0, 1e-10, rng)
            body_position = ((c + o) / 2.0 - l) / rng
            direction = (c - o) / rng
            vol_window = min(20, len(v))
            if vol_window > 0 and len(v) > 0:
                vol_mean = np.convolve(v, np.ones(vol_window) / vol_window, mode="same")
                vol_mean = np.where(vol_mean == 0, 1.0, vol_mean)
                vol_ratio = np.clip(v / vol_mean, 0.5, 2.0)
            else:
                vol_ratio = np.ones_like(v)
            vol_signal = np.clip((vol_ratio - 0.5) / 1.5, 0.0, 1.0)
            composite = body_position * body_w + direction * dir_w + vol_signal * vol_w
            return composite
        else:
            raise ValueError(f"Unknown strategy: {encoder.strategy}")

    encoder._extract_series = patched_extract
    return encoder


# ============================================================
# Walk-Forward Validation
# ============================================================

def walk_forward_test(df, symbol, alpha=4, window=7, pattern_length=5):
    """
    Walk-forward validation with expanding window.

    Each fold:
      1. Train on all data up to T
      2. Test on [T..T+step]
      3. T += step (expanding window, NOT rolling — keeps all history)

    This is the gold standard for detecting lookahead bias:
    - If single-split was biased, walk-forward PnL will be much lower
    - If single-split was honest, walk-forward should be similar
    """
    n = len(df)
    trades_all = []
    fold_results = []

    train_end = WF_INITIAL_TRAIN

    fold = 0
    while train_end + WF_MIN_TEST <= n:
        test_end = min(train_end + WF_STEP, n)
        train_df = df.iloc[:train_end]
        test_df = df.iloc[train_end:test_end]

        fold += 1

        # Build engine fresh each fold (NO lookahead)
        engine = PPMT(
            symbol=symbol,
            asset_class="default",
            sax_alphabet_size=alpha,
            sax_window_size=window,
            sax_strategy="ohlcv",
            fuzzy_threshold=0.80,
            min_confidence=0.05,
            min_risk_reward=0.3,
        )

        n_built = engine.build(train_df, pattern_length=pattern_length)
        for trie in [engine.trie_n1, engine.trie_n2, engine.trie_n3, engine.trie_n4]:
            trie.propagate_metadata()

        # Encode test data
        test_symbols = engine.sax.encode(test_df)

        # Trade on test data
        trades = []
        in_position = False
        entry_price = 0.0
        position_direction = ""

        for i in range(len(test_symbols) - pattern_length):
            pattern = test_symbols[i:i + pattern_length]
            candle_idx = min((i + pattern_length) * window, len(test_df) - 1)
            if candle_idx >= len(test_df):
                break

            current_price = float(test_df["close"].iloc[candle_idx])

            # Check SL/TP
            if in_position:
                low = float(test_df["low"].iloc[candle_idx])
                high = float(test_df["high"].iloc[candle_idx])
                if position_direction == "LONG" and low <= entry_sl:
                    pnl = ((entry_sl - entry_price) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "LONG", "exit_reason": "SL"})
                    in_position = False
                    continue
                elif position_direction == "SHORT" and high >= entry_sl:
                    pnl = ((entry_price - entry_sl) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "SHORT", "exit_reason": "SL"})
                    in_position = False
                    continue
                elif position_direction == "LONG" and high >= entry_tp:
                    pnl = ((entry_tp - entry_price) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "LONG", "exit_reason": "TP"})
                    in_position = False
                    continue
                elif position_direction == "SHORT" and low <= entry_tp:
                    pnl = ((entry_price - entry_tp) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "SHORT", "exit_reason": "TP"})
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
                trades.append({"pnl_pct": round(pnl, 4), "direction": position_direction, "exit_reason": signal.signal_type.value})
                in_position = False

        # Close open position at end of fold
        if in_position:
            last_price = float(test_df["close"].iloc[-1])
            if position_direction == "LONG":
                pnl = ((last_price - entry_price) / entry_price) * 100.0
            else:
                pnl = ((entry_price - last_price) / entry_price) * 100.0
            trades.append({"pnl_pct": round(pnl, 4), "direction": position_direction, "exit_reason": "END_FOLD"})

        # Fold stats
        pnls = [t["pnl_pct"] for t in trades]
        fold_pnl = sum(pnls) if pnls else 0.0
        fold_wr = len([p for p in pnls if p > 0]) / len(pnls) if pnls else 0.0

        fold_results.append({
            "fold": fold,
            "train_end": train_end,
            "test_end": test_end,
            "train_candles": train_end,
            "test_candles": test_end - train_end,
            "patterns_built": n_built,
            "trades": len(trades),
            "fold_pnl": round(fold_pnl, 4),
            "fold_wr": round(fold_wr, 4),
        })

        trades_all.extend(trades)

        # Expand training window
        train_end = test_end

    # Aggregate stats
    pnls = [t["pnl_pct"] for t in trades_all]
    if pnls:
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        total_pnl = sum(pnls)
        win_rate = len(wins) / len(pnls)
        profit_factor = sum(wins) / sum(losses) if losses else 0.0

        # Cumulative and drawdown
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        max_dd = abs(min(cumulative - peak))

        # Sharpe
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(24 * 365) if len(pnls) > 1 and np.std(pnls) > 0 else 0.0

        # Monte Carlo
        mc_profits = [sum(np.random.permutation(pnls)) for _ in range(500)]
        mc_profits = np.array(mc_profits)
    else:
        total_pnl = win_rate = profit_factor = max_dd = sharpe = 0.0
        mc_profits = np.array([0])

    return {
        "symbol": symbol,
        "alpha": alpha,
        "window": window,
        "total_folds": len(fold_results),
        "total_trades": len(trades_all),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "total_pnl_pct": round(total_pnl, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe_approx": round(sharpe, 4),
        "mc_mean_pnl": round(float(np.mean(mc_profits)), 4),
        "mc_profitable_pct": round(float(np.mean(mc_profits > 0) * 100), 2),
        "folds": fold_results,
    }


# ============================================================
# Weight Sensitivity Test
# ============================================================

def weight_sensitivity_test(df, symbol, alpha=4, window=7, pattern_length=5):
    """
    Test different OHLCV composite weight combinations.

    If all produce similar results → weights don't matter much → robust
    If one dominates → we know what drives performance → fragile
    """
    n = len(df)
    split = int(n * 0.70)
    train_df = df.iloc[:split]
    test_df = df.iloc[split:]

    results = {}

    for name, (body_w, dir_w, vol_w) in WEIGHT_CONFIGS.items():
        # Create engine with patched encoder
        engine = PPMT(
            symbol=symbol,
            asset_class="default",
            sax_alphabet_size=alpha,
            sax_window_size=window,
            sax_strategy="ohlcv",
            fuzzy_threshold=0.80,
            min_confidence=0.05,
            min_risk_reward=0.3,
        )

        # Replace SAX encoder with weighted version
        engine.sax = make_patched_encoder(alpha, window, body_w, dir_w, vol_w)

        # Rebuild with custom weights
        n_built = engine.build(train_df, pattern_length=pattern_length)
        for trie in [engine.trie_n1, engine.trie_n2, engine.trie_n3, engine.trie_n4]:
            trie.propagate_metadata()

        # Encode and trade on OOS
        oos_symbols = engine.sax.encode(test_df)

        trades = []
        in_position = False
        entry_price = 0.0
        position_direction = ""

        for i in range(len(oos_symbols) - pattern_length):
            pattern = oos_symbols[i:i + pattern_length]
            candle_idx = min((i + pattern_length) * window, len(test_df) - 1)
            if candle_idx >= len(test_df):
                break

            current_price = float(test_df["close"].iloc[candle_idx])

            # SL/TP check
            if in_position:
                low = float(test_df["low"].iloc[candle_idx])
                high = float(test_df["high"].iloc[candle_idx])
                if position_direction == "LONG" and low <= entry_sl:
                    pnl = ((entry_sl - entry_price) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "LONG"})
                    in_position = False
                    continue
                elif position_direction == "SHORT" and high >= entry_sl:
                    pnl = ((entry_price - entry_sl) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "SHORT"})
                    in_position = False
                    continue
                elif position_direction == "LONG" and high >= entry_tp:
                    pnl = ((entry_tp - entry_price) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "LONG"})
                    in_position = False
                    continue
                elif position_direction == "SHORT" and low <= entry_tp:
                    pnl = ((entry_price - entry_tp) / entry_price) * 100.0
                    trades.append({"pnl_pct": round(pnl, 4), "direction": "SHORT"})
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
                trades.append({"pnl_pct": round(pnl, 4), "direction": position_direction})
                in_position = False

        # Close open
        if in_position:
            last_price = float(test_df["close"].iloc[-1])
            if position_direction == "LONG":
                pnl = ((last_price - entry_price) / entry_price) * 100.0
            else:
                pnl = ((entry_price - last_price) / entry_price) * 100.0
            trades.append({"pnl_pct": round(pnl, 4), "direction": position_direction})

        # Stats
        pnls = [t["pnl_pct"] for t in trades]
        if pnls:
            wins = [p for p in pnls if p > 0]
            losses = [abs(p) for p in pnls if p < 0]
            total_pnl = sum(pnls)
            win_rate = len(wins) / len(pnls)
            profit_factor = sum(wins) / sum(losses) if losses else 0.0
            cumulative = np.cumsum(pnls)
            peak = np.maximum.accumulate(cumulative)
            max_dd = abs(min(cumulative - peak)) if len(cumulative) > 0 else 0
            sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(24 * 365) if len(pnls) > 1 and np.std(pnls) > 0 else 0.0
        else:
            total_pnl = win_rate = profit_factor = max_dd = sharpe = 0.0

        # Symbol distribution for this weight config
        syms = engine.sax.encode(train_df)
        sym_counts = {}
        for s in syms:
            sym_counts[s] = sym_counts.get(s, 0) + 1
        total = len(syms) if syms else 1
        max_conc = max(sym_counts.values()) / total if sym_counts else 1.0

        results[name] = {
            "weights": (body_w, dir_w, vol_w),
            "max_concentration": round(max_conc, 4),
            "total_trades": len(trades),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
            "total_pnl_pct": round(total_pnl, 4),
            "max_drawdown_pct": round(max_dd, 4),
            "sharpe_approx": round(sharpe, 4),
        }

    return results


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 80)
    print("  PPMT v0.6.2 — Walk-Forward + Weight Sensitivity Validation")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)

    # Download data
    print("\n  STEP 0: Download Data")
    data = {}
    for symbol in TOKENS:
        collector = DataCollector(exchange="binance")
        df = collector.fetch_and_save(symbol, TIMEFRAME, days=DAYS_OF_DATA)
        if not df.empty:
            data[symbol] = df
            print(f"  {symbol}: {len(df)} candles")

    # Best configs from multi-alpha test
    BEST_CONFIGS = {
        "BTC/USDT": {"alpha": 4, "window": 7},
        "ETH/USDT": {"alpha": 3, "window": 7},
        "SOL/USDT": {"alpha": 3, "window": 7},
    }

    # ========================================
    # PART 1: WALK-FORWARD
    # ========================================
    print("\n" + "=" * 80)
    print("  PART 1: Walk-Forward Validation (expanding window)")
    print(f"  Initial train: {WF_INITIAL_TRAIN} | Step: {WF_STEP} | Min test: {WF_MIN_TEST}")
    print("=" * 80)

    wf_results = {}
    for symbol, df in data.items():
        config = BEST_CONFIGS[symbol]
        print(f"\n  --- {symbol} (alpha={config['alpha']}, window={config['window']}) ---")

        result = walk_forward_test(df, symbol, alpha=config["alpha"], window=config["window"])
        wf_results[symbol] = result

        print(f"  Folds: {result['total_folds']} | Trades: {result['total_trades']}")
        print(f"  WR: {result['win_rate']:.1%} | PF: {result['profit_factor']:.2f} | PnL: {result['total_pnl_pct']:+.2f}%")
        print(f"  Sharpe: {result['sharpe_approx']:.2f} | MC Prof: {result['mc_profitable_pct']:.1f}%")

        # Per-fold breakdown
        print(f"\n  Per-fold breakdown:")
        for f in result["folds"]:
            print(f"    Fold {f['fold']:2d}: train={f['train_candles']:5d} test={f['test_candles']:4d} "
                  f"patterns={f['patterns_built']:4d} trades={f['trades']:3d} "
                  f"PnL={f['fold_pnl']:+7.2f}% WR={f['fold_wr']:.1%}")

    # Compare with single-split
    print("\n" + "=" * 80)
    print("  WALK-FORWARD vs SINGLE-SPLIT COMPARISON")
    print("=" * 80)

    single_split_pnl = {"BTC/USDT": 237.85, "ETH/USDT": 470.17, "SOL/USDT": 679.73}

    print(f"\n  {'Token':<12} {'Single-Split':<15} {'Walk-Forward':<15} {'Ratio':<10} {'Verdict':<15}")
    print("  " + "-" * 65)

    for symbol in data:
        wf_pnl = wf_results[symbol]["total_pnl_pct"]
        ss_pnl = single_split_pnl[symbol]
        ratio = wf_pnl / ss_pnl if ss_pnl != 0 else 0
        if ratio > 0.5:
            verdict = "✅ CONSISTENT"
        elif ratio > 0.2:
            verdict = "⚠️ DEGRADED"
        else:
            verdict = "❌ LOOKAHEAD?"

        print(f"  {symbol:<12} {ss_pnl:>+.2f}%{'':<6} {wf_pnl:>+.2f}%{'':<6} {ratio:.2f}{'':<5} {verdict}")

    # ========================================
    # PART 2: WEIGHT SENSITIVITY
    # ========================================
    print("\n" + "=" * 80)
    print("  PART 2: Weight Sensitivity Analysis")
    print(f"  Testing {len(WEIGHT_CONFIGS)} weight configurations")
    print("=" * 80)

    all_sensitivity = {}
    for symbol, df in data.items():
        config = BEST_CONFIGS[symbol]
        print(f"\n  --- {symbol} (alpha={config['alpha']}, window={config['window']}) ---")

        results = weight_sensitivity_test(df, symbol, alpha=config["alpha"], window=config["window"])
        all_sensitivity[symbol] = results

        print(f"\n  {'Config':<30} {'Conc%':<8} {'Trades':<8} {'WR':<8} {'PF':<8} {'PnL%':<10} {'Sharpe':<8}")
        print("  " + "-" * 80)

        for name, r in sorted(results.items(), key=lambda x: x[1]["total_pnl_pct"], reverse=True):
            print(f"  {name:<30} {r['max_concentration']:<8.1%} {r['total_trades']:<8} "
                  f"{r['win_rate']:<8.1%} {r['profit_factor']:<8.2f} "
                  f"{r['total_pnl_pct']:<+10.2f} {r['sharpe_approx']:<8.2f}")

    # Weight sensitivity summary
    print("\n" + "=" * 80)
    print("  WEIGHT SENSITIVITY SUMMARY")
    print("=" * 80)

    for symbol in data:
        results = all_sensitivity[symbol]
        pnls = [r["total_pnl_pct"] for r in results.values()]
        all_profitable = all(p > 0 for p in pnls)
        pnl_range = max(pnls) - min(pnls)
        pnl_std = np.std(pnls)
        pnl_mean = np.mean(pnls)
        coeff_var = pnl_std / abs(pnl_mean) if pnl_mean != 0 else float('inf')

        print(f"\n  {symbol}:")
        print(f"    All configs profitable: {'YES ✅' if all_profitable else 'NO ❌'}")
        print(f"    PnL range: {min(pnls):+.2f}% to {max(pnls):+.2f}% (spread: {pnl_range:.2f}%)")
        print(f"    Coefficient of variation: {coeff_var:.2%}")
        if coeff_var < 0.3:
            print(f"    → ROBUST: Weights have moderate impact")
        elif coeff_var < 0.6:
            print(f"    → SENSITIVE: Weights matter somewhat")
        else:
            print(f"    → FRAGILE: Results depend heavily on weight choice")

    # Save results
    output = {
        "walk_forward": {s: {k: v for k, v in r.items() if k != "folds"} for s, r in wf_results.items()},
        "weight_sensitivity": {s: {n: r for n, r in rs.items()} for s, rs in all_sensitivity.items()},
        "timestamp": datetime.now().isoformat(),
    }
    output_path = "/home/z/my-project/download/walkforward_sensitivity_results.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_path}")

    return wf_results, all_sensitivity


if __name__ == "__main__":
    main()
