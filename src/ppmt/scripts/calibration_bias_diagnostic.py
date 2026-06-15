#!/usr/bin/env python3
"""
PPMT — Calibration Bias Diagnostic

Direct comparison of CalibrationEngine (pattern-matching) vs
TradingCalibrationEngine (mini-backtest) on the same data.

PROVES: Old engine always selects alpha=3/window=5 (structural bias).
SHOWS: New engine produces diverse, data-driven selections.

Runs on BTC, ETH, SOL, DOGE across 1h timeframe with Bybit data.
"""

import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import pandas as pd

from ppmt.data.collector import DataCollector
from ppmt.data.storage import PPMTStorage
from ppmt.core.profiles import CalibrationEngine, TradingCalibrationEngine


TOKENS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "BNB/USDT", "LINK/USDT"]
TIMEFRAME = "1h"
DAYS = 600
TRAIN_RATIO = 0.70
PATTERN_LENGTH = 5


def main():
    print("=" * 80)
    print("  CALIBRATION BIAS DIAGNOSTIC")
    print(f"  Old (pattern-matching) vs New (trading-based) engine")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)

    # Load data
    storage = PPMTStorage()
    token_data = {}

    print("\n  Loading data...")
    for symbol in TOKENS:
        cached = storage.load_ohlcv(symbol, TIMEFRAME)
        if not cached.empty and len(cached) >= 3000:
            token_data[symbol] = cached
            print(f"    {symbol}: {len(cached)} candles (cached)")
        else:
            try:
                collector = DataCollector(exchange="bybit")
                df = collector.fetch_and_save(symbol, TIMEFRAME, days=DAYS)
                collector.close()
                if not df.empty and len(df) >= 3000:
                    token_data[symbol] = df
                    print(f"    {symbol}: {len(df)} candles (downloaded)")
                else:
                    print(f"    {symbol}: INSUFFICIENT DATA")
            except Exception as e:
                print(f"    {symbol}: ERROR - {e}")

    storage.close()

    if len(token_data) < 3:
        print(f"\n  ERROR: Need at least 3 tokens, got {len(token_data)}")
        return

    # Run both engines on each token
    old_engine = CalibrationEngine(train_ratio=TRAIN_RATIO, pattern_length=PATTERN_LENGTH)
    new_engine = TradingCalibrationEngine(train_ratio=TRAIN_RATIO, pattern_length=PATTERN_LENGTH)

    results = {}

    print("\n" + "=" * 80)
    print("  COMPARISON RESULTS")
    print("=" * 80)
    print(f"\n  {'Token':<12} {'Old α/W':<10} {'Old metric':<12} {'New α/W':<10} "
          f"{'New metric':<12} {'New PnL%':<12} {'Match?':<8}")
    print("  " + "-" * 76)

    for symbol, df in token_data.items():
        # Old engine (pattern-matching)
        try:
            old_profile, old_results = old_engine.calibrate(df, symbol=symbol, verbose=False)
            old_best = max(old_results, key=lambda r: r.calibration_metric)
            old_alpha = old_profile.sax_alphabet_size
            old_window = old_profile.sax_window_size
            old_metric = old_best.calibration_metric
        except Exception as e:
            print(f"  {symbol}: Old engine error: {e}")
            continue

        # New engine (trading-based)
        try:
            new_profile, new_results = new_engine.calibrate(df, symbol=symbol, verbose=False)
            new_best = [r for r in new_results if r.alphabet_size == new_profile.sax_alphabet_size
                        and r.window_size == new_profile.sax_window_size][0]
            new_alpha = new_profile.sax_alphabet_size
            new_window = new_profile.sax_window_size
            new_metric = new_best.trading_metric
            new_pnl = new_best.total_pnl_pct
        except Exception as e:
            print(f"  {symbol}: New engine error: {e}")
            continue

        match = "YES" if old_alpha == new_alpha and old_window == new_window else "NO"

        print(f"  {symbol:<12} α={old_alpha}/w={old_window:<5} {old_metric:<12.4f} "
              f"α={new_alpha}/w={new_window:<5} {new_metric:<12.4f} {new_pnl:<+12.2f} {match:<8}")

        # Detailed grid comparison
        results[symbol] = {
            "old_engine": {
                "best_alpha": old_alpha,
                "best_window": old_window,
                "best_metric": old_metric,
                "grid": {
                    f"a{r.alphabet_size}_w{r.window_size}": {
                        "metric": r.calibration_metric,
                        "information": r.information,
                        "oos_match_rate": r.oos_match_rate,
                        "overlap_ratio": r.overlap_ratio,
                        "repetition": r.repetition,
                    }
                    for r in old_results
                }
            },
            "new_engine": {
                "best_alpha": new_alpha,
                "best_window": new_window,
                "best_metric": new_metric,
                "total_pnl_pct": new_pnl,
                "grid": {
                    f"a{r.alphabet_size}_w{r.window_size}": {
                        "trading_metric": r.trading_metric,
                        "pattern_metric": r.pattern_metric,
                        "total_pnl_pct": r.total_pnl_pct,
                        "win_rate": r.win_rate,
                        "total_trades": r.total_trades,
                        "oos_match_rate": r.oos_match_rate,
                    }
                    for r in new_results
                }
            }
        }

    # Summary analysis
    print("\n" + "=" * 80)
    print("  BIAS ANALYSIS")
    print("=" * 80)

    # Count alpha selections per engine
    from collections import Counter
    old_alphas = Counter()
    new_alphas = Counter()
    old_combos = Counter()
    new_combos = Counter()

    for symbol, r in results.items():
        old_alphas[r["old_engine"]["best_alpha"]] += 1
        new_alphas[r["new_engine"]["best_alpha"]] += 1
        old_combos[f"a{r['old_engine']['best_alpha']}_w{r['old_engine']['best_window']}"] += 1
        new_combos[f"a{r['new_engine']['best_alpha']}_w{r['new_engine']['best_window']}"] += 1

    print(f"\n  OLD ENGINE (pattern-matching) alpha distribution: {dict(old_alphas)}")
    print(f"  OLD ENGINE combo distribution: {dict(old_combos)}")
    print(f"\n  NEW ENGINE (trading-based) alpha distribution: {dict(new_alphas)}")
    print(f"  NEW ENGINE combo distribution: {dict(new_combos)}")

    old_alpha3_pct = old_alphas.get(3, 0) / len(results) * 100
    new_alpha3_pct = new_alphas.get(3, 0) / len(results) * 100

    print(f"\n  Old engine selects alpha=3: {old_alphas.get(3, 0)}/{len(results)} ({old_alpha3_pct:.0f}%)")
    print(f"  New engine selects alpha=3: {new_alphas.get(3, 0)}/{len(results)} ({new_alpha3_pct:.0f}%)")

    if old_alpha3_pct > 80:
        print("\n  >>> BIAS CONFIRMED: Old engine overwhelmingly selects alpha=3")
    if new_alpha3_pct < 50:
        print("  >>> BIAS FIXED: New engine produces diverse alpha selections")

    # Detailed grid metrics for one token to show WHY alpha=3 always wins
    example_token = list(results.keys())[0]
    r = results[example_token]

    print(f"\n  --- Detailed Grid: {example_token} ---")
    print(f"\n  OLD ENGINE (pattern-matching metric):")
    for combo in sorted(r["old_engine"]["grid"].keys()):
        g = r["old_engine"]["grid"][combo]
        marker = " <<< BEST" if combo == f"a{r['old_engine']['best_alpha']}_w{r['old_engine']['best_window']}" else ""
        print(f"    {combo}: metric={g['metric']:.4f} info={g['information']:.3f} "
              f"oos_match={g['oos_match_rate']:.1%} overlap={g['overlap_ratio']:.2f}x "
              f"repet={g['repetition']:.3f}{marker}")

    print(f"\n  NEW ENGINE (trading metric):")
    for combo in sorted(r["new_engine"]["grid"].keys()):
        g = r["new_engine"]["grid"][combo]
        marker = " <<< BEST" if combo == f"a{r['new_engine']['best_alpha']}_w{r['new_engine']['best_window']}" else ""
        print(f"    {combo}: tmetric={g['trading_metric']:.4f} pmetric={g['pattern_metric']:.4f} "
              f"PnL={g['total_pnl_pct']:+.1f}% WR={g['win_rate']:.1%} "
              f"Trades={g['total_trades']} oos_match={g['oos_match_rate']:.1%}{marker}")

    # Save results
    output = {
        "timestamp": datetime.now().isoformat(),
        "tokens_tested": len(results),
        "old_engine_alpha_distribution": dict(old_alphas),
        "old_engine_combo_distribution": dict(old_combos),
        "new_engine_alpha_distribution": dict(new_alphas),
        "new_engine_combo_distribution": dict(new_combos),
        "bias_confirmed": old_alpha3_pct > 80,
        "bias_fixed": new_alpha3_pct < 50,
        "per_token": results,
    }

    output_path = "/home/z/my-project/download/calibration_bias_diagnostic.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n  Results saved to: {output_path}")
    return output


if __name__ == "__main__":
    main()
