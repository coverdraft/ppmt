#!/usr/bin/env python3
"""
PPMT v0.6.7 — Massive Multi-Token Validation

Tests 12+ tokens across ALL asset classes with:
  1. Trading-Calibration per token (mini-backtest selects best α/W)
  2. OOS Trading simulation (single split)
  3. Walk-Forward validation (expanding window)
  4. Monte Carlo resampling (500 sims)
  5. Asset class comparison

Optimized for speed:
  - MC simulations reduced to 500
  - Walk-forward folds reduced by larger step
  - Runs in <8 min for 12 tokens

All data: REAL (Bybit). No synthetic data.
v0.6.7: TradingCalibrationEngine replaces CalibrationEngine to fix alpha=3 bias.
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
from ppmt.data.storage import PPMTStorage
from ppmt.core.profiles import CalibrationEngine, TradingCalibrationEngine, TokenProfile, ASSET_CLASS_DEFAULTS
from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.engine.ppmt import PPMT
from ppmt.engine.signal import SignalType


# ============================================================
# Configuration — 12 tokens across all asset classes
# ============================================================

TOKEN_CONFIG = {
    # === BLUE CHIP (2) ===
    "BTC/USDT":  {"asset_class": "blue_chip"},
    "ETH/USDT":  {"asset_class": "blue_chip"},

    # === LARGE CAP (4) ===
    "SOL/USDT":  {"asset_class": "large_cap"},
    "BNB/USDT":  {"asset_class": "large_cap"},
    "XRP/USDT":  {"asset_class": "large_cap"},
    "ADA/USDT":  {"asset_class": "large_cap"},

    # === DEFI (3) ===
    "LINK/USDT": {"asset_class": "defi"},
    "UNI/USDT":  {"asset_class": "defi"},
    "ATOM/USDT": {"asset_class": "defi"},

    # === MEME (3) ===
    "DOGE/USDT": {"asset_class": "meme"},
    "SHIB/USDT": {"asset_class": "meme"},
    "PEPE/USDT": {"asset_class": "meme"},
}

TIMEFRAME = "1h"
DAYS_OF_DATA = 600
PATTERN_LENGTH = 5
TRAIN_RATIO = 0.70
MC_SIMS = 500  # Reduced for speed

# Walk-forward params (larger step for speed)
WF_INITIAL_TRAIN = 5000
WF_STEP = 2000    # Larger step = fewer folds = faster
WF_MIN_TEST = 500


# ============================================================
# Data Download (with caching)
# ============================================================

def download_all_tokens() -> dict:
    """Download data for all configured tokens (with SQLite cache)."""
    data = {}
    failed = []

    storage = PPMTStorage()

    for symbol, config in TOKEN_CONFIG.items():
        try:
            print(f"  {symbol} ({config['asset_class']})...", end=" ", flush=True)

            # Check SQLite cache first (from bulk data loader)
            cached = storage.load_ohlcv(symbol, TIMEFRAME)
            if not cached.empty and len(cached) >= 3000:
                days_span = (cached.index[-1] - cached.index[0]).days
                print(f"CACHED ({len(cached)} candles, {days_span} days)")
                data[symbol] = {"df": cached, "asset_class": config["asset_class"]}
                continue

            # Fallback to API (Bybit primary, auto-fallback to OKX/Kraken)
            collector = DataCollector(exchange="bybit")
            df = collector.fetch_and_save(symbol, TIMEFRAME, days=DAYS_OF_DATA)
            collector.close()

            if df.empty:
                print("NO DATA")
                failed.append(symbol)
                continue

            if len(df) < 3000:
                print(f"TOO FEW ({len(df)})")
                failed.append(symbol)
                continue

            print(f"OK ({len(df)} candles)")
            data[symbol] = {"df": df, "asset_class": config["asset_class"]}

        except Exception as e:
            print(f"ERROR: {e}")
            failed.append(symbol)

    storage.close()
    print(f"\n  Downloaded: {len(data)} | Failed: {len(failed)} {failed}")
    return data


# ============================================================
# Core Trading Simulation (shared by OOS and WF)
# ============================================================

def simulate_trades(engine, test_df, pattern_length=5):
    """
    Run trading simulation on test data with an already-built engine.
    Returns list of trade dicts.
    """
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
    """Compute trading statistics from trade list."""
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "total_pnl_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_approx": 0.0,
            "long_trades": 0, "short_trades": 0,
            "mc_mean_pnl": 0.0, "mc_profitable_pct": 0.0,
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

    # Monte Carlo
    mc_profits = np.array([sum(np.random.permutation(pnls)) for _ in range(MC_SIMS)])

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "total_pnl_pct": round(total_pnl, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe_approx": round(sharpe, 4),
        "long_trades": sum(1 for t in trades if t["direction"] == "LONG"),
        "short_trades": sum(1 for t in trades if t["direction"] == "SHORT"),
        "mc_mean_pnl": round(float(np.mean(mc_profits)), 4),
        "mc_profitable_pct": round(float(np.mean(mc_profits > 0) * 100), 2),
    }


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 90)
    print("  PPMT v0.6.8 — MASSIVE Multi-Token Validation (Trading Calibration)")
    print(f"  Tokens: {len(TOKEN_CONFIG)} | Classes: blue_chip, large_cap, defi, meme")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 90)

    start_time = datetime.now()

    # ================================================================
    # STEP 1: Download all data
    # ================================================================
    print("\n  STEP 1: Load Real Data (CDD/BV/Bybit → SQLite cache)")
    print("-" * 90)

    token_data = download_all_tokens()

    if len(token_data) < 5:
        print(f"\n  ERROR: Need at least 5 tokens. Got {len(token_data)}.")
        return None

    # ================================================================
    # STEP 2: Auto-Calibration per token
    # ================================================================
    print("\n  STEP 2: Auto-Calibration")
    print("-" * 90)

    calibrator = TradingCalibrationEngine(train_ratio=TRAIN_RATIO, pattern_length=PATTERN_LENGTH, timeframe=TIMEFRAME)
    profiles = {}

    for symbol, info in token_data.items():
        df = info["df"]
        asset_class = info["asset_class"]
        print(f"\n  {symbol} ({asset_class}):", end=" ", flush=True)

        try:
            profile, results = calibrator.calibrate(df, symbol=symbol, verbose=False)
            profiles[symbol] = profile
            best = [r for r in results if r.alphabet_size == profile.sax_alphabet_size
                    and r.window_size == profile.sax_window_size][0]
            print(f"best alpha={profile.sax_alphabet_size} window={profile.sax_window_size} "
                  f"metric={profile.calibration_metric:.4f} "
                  f"overlap={best.overlap_ratio:.1f}x oos_match={best.oos_match_rate:.1%}")
        except Exception as e:
            print(f"CALIBRATION ERROR: {e}")
            profile = TokenProfile.from_asset_class(symbol, asset_class)
            profiles[symbol] = profile

    # ================================================================
    # STEP 3: OOS Trading (single split) with calibrated profiles
    # ================================================================
    print("\n  STEP 3: OOS Trading (single 70/30 split)")
    print("-" * 90)

    oos_results = {}

    for symbol, info in token_data.items():
        df = info["df"]
        profile = profiles[symbol]
        asset_class = info["asset_class"]
        alpha = profile.sax_alphabet_size
        window = profile.sax_window_size

        n = len(df)
        split = int(n * TRAIN_RATIO)
        train_df = df.iloc[:split]
        oos_df = df.iloc[split:]

        print(f"  {symbol} ({asset_class}, a={alpha}, w={window}):", end=" ", flush=True)

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

            oos_results[symbol] = {
                "asset_class": asset_class,
                "alpha": alpha, "window": window,
                "patterns_built": n_built,
                **stats,
            }

            pnl_sign = "+" if stats["total_pnl_pct"] > 0 else ""
            print(f"Trades={stats['total_trades']} WR={stats['win_rate']:.1%} "
                  f"PF={stats['profit_factor']:.2f} PnL={pnl_sign}{stats['total_pnl_pct']:.2f}% "
                  f"MC={stats['mc_profitable_pct']:.0f}%")

        except Exception as e:
            print(f"ERROR: {e}")
            traceback.print_exc()

    # ================================================================
    # STEP 4: Walk-Forward Validation
    # ================================================================
    print("\n  STEP 4: Walk-Forward Validation (expanding window)")
    print("-" * 90)

    wf_results = {}

    for symbol, info in token_data.items():
        df = info["df"]
        profile = profiles[symbol]
        asset_class = info["asset_class"]
        alpha = profile.sax_alphabet_size
        window = profile.sax_window_size

        print(f"  {symbol} ({asset_class}, a={alpha}, w={window}):", end=" ", flush=True)

        try:
            n = len(df)
            trades_all = []
            fold_count = 0
            train_end = WF_INITIAL_TRAIN

            while train_end + WF_MIN_TEST <= n:
                test_end = min(train_end + WF_STEP, n)
                train_df = df.iloc[:train_end]
                test_df = df.iloc[train_end:test_end]

                engine = PPMT(
                    symbol=symbol, asset_class=asset_class,
                    sax_alphabet_size=alpha, sax_window_size=window,
                    sax_strategy="ohlcv", fuzzy_threshold=0.80,
                    min_confidence=0.05, min_risk_reward=0.3,
                )
                engine.build(train_df, pattern_length=PATTERN_LENGTH)
                for trie in [engine.trie_n1, engine.trie_n2, engine.trie_n3, engine.trie_n4]:
                    trie.propagate_metadata()

                fold_trades = simulate_trades(engine, test_df, PATTERN_LENGTH)
                trades_all.extend(fold_trades)

                fold_count += 1
                train_end = test_end

            stats = compute_stats(trades_all)

            wf_results[symbol] = {
                "asset_class": asset_class,
                "alpha": alpha, "window": window,
                "total_folds": fold_count,
                **stats,
            }

            oos_pnl = oos_results.get(symbol, {}).get("total_pnl_pct", 0)
            wf_pnl = stats["total_pnl_pct"]
            ratio = wf_pnl / oos_pnl if oos_pnl != 0 else 0

            print(f"Folds={fold_count} Trades={stats['total_trades']} "
                  f"WR={stats['win_rate']:.1%} PnL={stats['total_pnl_pct']:+.2f}% "
                  f"WF/OOS={ratio:.2f}")

        except Exception as e:
            print(f"ERROR: {e}")
            traceback.print_exc()

    # ================================================================
    # STEP 5: Summary Tables
    # ================================================================
    print("\n" + "=" * 90)
    print("  COMPREHENSIVE SUMMARY")
    print("=" * 90)

    # --- OOS Results ---
    print("\n  === OOS TRADING (Single Split 70/30) ===\n")

    print(f"  {'Token':<12} {'Class':<10} {'a':<3} {'w':<3} "
          f"{'Trades':<7} {'WR':<7} {'PF':<7} {'PnL%':<10} "
          f"{'Sharpe':<7} {'MC%':<6}")
    print("  " + "-" * 80)

    for symbol in sorted(oos_results.keys()):
        r = oos_results[symbol]
        print(f"  {symbol:<12} {r['asset_class']:<10} {r['alpha']:<3} {r['window']:<3} "
              f"{r['total_trades']:<7} {r['win_rate']:<7.1%} {r['profit_factor']:<7.2f} "
              f"{r['total_pnl_pct']:<+10.2f} {r['sharpe_approx']:<7.2f} "
              f"{r['mc_profitable_pct']:<6.0f}")

    # --- Walk-Forward Results ---
    print("\n  === WALK-FORWARD (Expanding Window) ===\n")

    print(f"  {'Token':<12} {'Class':<10} {'Folds':<6} {'Trades':<7} "
          f"{'WR':<7} {'PF':<7} {'PnL%':<10} {'MC%':<6} {'OOS→WF':<8}")
    print("  " + "-" * 80)

    for symbol in sorted(wf_results.keys()):
        wf = wf_results[symbol]
        oos_pnl = oos_results.get(symbol, {}).get("total_pnl_pct", 0)
        ratio = wf["total_pnl_pct"] / oos_pnl if oos_pnl != 0 else 0
        verdict = "✅" if ratio > 0.5 else ("⚠️" if ratio > 0.2 else "❌")

        print(f"  {symbol:<12} {wf['asset_class']:<10} {wf['total_folds']:<6} "
              f"{wf['total_trades']:<7} {wf['win_rate']:<7.1%} {wf['profit_factor']:<7.2f} "
              f"{wf['total_pnl_pct']:<+10.2f} {wf['mc_profitable_pct']:<6.0f} "
              f"{ratio:.2f} {verdict}")

    # --- Asset Class Aggregation ---
    print("\n  === ASSET CLASS AGGREGATION ===\n")

    class_stats = {}
    for symbol, r in oos_results.items():
        ac = r["asset_class"]
        if ac not in class_stats:
            class_stats[ac] = {"pnls": [], "win_rates": [], "profit_factors": [], "mc_pcts": [], "tokens": []}
        class_stats[ac]["pnls"].append(r["total_pnl_pct"])
        class_stats[ac]["win_rates"].append(r["win_rate"])
        class_stats[ac]["profit_factors"].append(r["profit_factor"])
        class_stats[ac]["mc_pcts"].append(r["mc_profitable_pct"])
        class_stats[ac]["tokens"].append(symbol)

    print(f"  {'Class':<12} {'N':<4} {'All Prof?':<10} {'Avg PnL%':<10} "
          f"{'Avg WR':<8} {'Avg PF':<8} {'Avg MC%':<8} {'Range':<25}")
    print("  " + "-" * 95)

    for ac in ["blue_chip", "large_cap", "defi", "meme"]:
        if ac not in class_stats:
            continue
        s = class_stats[ac]
        all_profit = all(p > 0 for p in s["pnls"])
        print(f"  {ac:<12} {len(s['tokens']):<4} "
              f"{'YES' if all_profit else 'NO':<10} "
              f"{np.mean(s['pnls']):<+10.2f} {np.mean(s['win_rates']):<8.1%} "
              f"{np.mean(s['profit_factors']):<8.2f} {np.mean(s['mc_pcts']):<8.0f} "
              f"{min(s['pnls']):+.1f} to {max(s['pnls']):+.1f}")

    # --- Overall Summary ---
    print("\n  === OVERALL ===\n")

    all_pnls = [r["total_pnl_pct"] for r in oos_results.values()]
    all_wr = [r["win_rate"] for r in oos_results.values()]
    all_mc = [r["mc_profitable_pct"] for r in oos_results.values()]
    all_profitable = sum(1 for p in all_pnls if p > 0)
    total_tokens = len(all_pnls)

    print(f"  Tokens tested:       {total_tokens}")
    print(f"  Profitable:          {all_profitable}/{total_tokens} ({all_profitable/total_tokens:.0%})")
    print(f"  Avg PnL:             {np.mean(all_pnls):+.2f}%")
    print(f"  Median PnL:          {np.median(all_pnls):+.2f}%")
    print(f"  PnL range:           {min(all_pnls):+.2f}% to {max(all_pnls):+.2f}%")
    print(f"  Avg Win Rate:        {np.mean(all_wr):.1%}")
    print(f"  Avg MC Profitable:   {np.mean(all_mc):.0f}%")

    # WF/OOS ratios
    wf_oos_ratios = []
    for symbol in wf_results:
        oos_pnl = oos_results.get(symbol, {}).get("total_pnl_pct", 0)
        wf_pnl = wf_results[symbol]["total_pnl_pct"]
        if oos_pnl != 0:
            wf_oos_ratios.append(wf_pnl / oos_pnl)

    if wf_oos_ratios:
        consistent = sum(1 for r in wf_oos_ratios if r > 0.5)
        print(f"\n  WF/OOS Ratio:        avg={np.mean(wf_oos_ratios):.2f} "
              f"min={min(wf_oos_ratios):.2f} max={max(wf_oos_ratios):.2f}")
        print(f"  WF Consistent:       {consistent}/{len(wf_oos_ratios)}")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n  Total time: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    # ================================================================
    # STEP 6: Save results
    # ================================================================

    output = {
        "timestamp": datetime.now().isoformat(),
        "tokens_tested": total_tokens,
        "oos_trading": oos_results,
        "walk_forward": wf_results,
        "asset_class_summary": {
            ac: {
                "tokens": s["tokens"],
                "all_profitable": all(p > 0 for p in s["pnls"]),
                "avg_pnl_pct": round(float(np.mean(s["pnls"])), 4),
                "avg_win_rate": round(float(np.mean(s["win_rates"])), 4),
                "avg_profit_factor": round(float(np.mean(s["profit_factors"])), 4),
                "avg_mc_profitable_pct": round(float(np.mean(s["mc_pcts"])), 2),
                "pnl_range": [round(min(s["pnls"]), 4), round(max(s["pnls"]), 4)],
            }
            for ac, s in class_stats.items()
        },
        "overall": {
            "profitable_count": all_profitable,
            "total_tokens": total_tokens,
            "avg_pnl_pct": round(float(np.mean(all_pnls)), 4),
            "median_pnl_pct": round(float(np.median(all_pnls)), 4),
            "avg_win_rate": round(float(np.mean(all_wr)), 4),
            "avg_mc_profitable_pct": round(float(np.mean(all_mc)), 2),
        },
    }

    output_path = "/home/z/my-project/download/v066_massive_validation_results.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n  Results saved to: {output_path}")
    return output


if __name__ == "__main__":
    results = main()
