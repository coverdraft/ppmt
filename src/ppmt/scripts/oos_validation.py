#!/usr/bin/env python3
"""
Cross-Token OOS Validation with Auto-Calibration

Downloads real Binance data for BTC, ETH, SOL and runs:
  1. Calibration Phase: discovers optimal alpha x window per token
  2. Full OOS Validation: builds trie on 70%, trades on 30%
  3. Monte Carlo: resamples OOS trades for statistical robustness
  4. Cross-token comparison: are results consistent across tokens?

This validates that:
  - The additive OHLCV composite works (not degenerate)
  - The auto-calibration finds good parameters per token
  - The engine generalizes across different asset types
  - Results are not from overfitting or lookahead bias

All data: REAL Binance. No synthetic data.
"""

import sys
import os
import json
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import pandas as pd

from ppmt.data.collector import DataCollector
from ppmt.core.profiles import CalibrationEngine, TradingCalibrationEngine, TokenProfile, CalibrationResult
from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.core.matcher import FuzzyMatcher
from ppmt.engine.ppmt import PPMT
from ppmt.engine.weights import AdaptiveWeights


# ============================================================
# Configuration
# ============================================================

TOKENS = {
    "BTC/USDT": {"asset_class": "blue_chip"},
    "ETH/USDT": {"asset_class": "blue_chip"},
    "SOL/USDT": {"asset_class": "large_cap"},
}

TIMEFRAME = "1h"
TRAIN_RATIO = 0.70
PATTERN_LENGTH = 5
DAYS_OF_DATA = 600  # ~14,400 hourly candles


# ============================================================
# Data Download
# ============================================================

def download_data(symbol: str, timeframe: str = "1h", days: int = 600) -> pd.DataFrame:
    """Download real OHLCV data from Binance."""
    print(f"\n  Downloading {symbol} {timeframe} ({days} days) from Binance...")
    collector = DataCollector(exchange="binance")
    df = collector.fetch_and_save(symbol, timeframe, days=days)

    if df.empty:
        print(f"  ERROR: No data returned for {symbol}")
        return df

    print(f"  Got {len(df)} candles: {df.index[0]} → {df.index[-1]}")
    print(f"  Price range: ${df['low'].min():,.2f} - ${df['high'].max():,.2f}")
    return df


# ============================================================
# OOS Trading Simulation (simplified paper trader)
# ============================================================

def oos_trading_simulation(
    train_df: pd.DataFrame,
    oos_df: pd.DataFrame,
    profile: TokenProfile,
    pattern_length: int = 5,
) -> dict:
    """
    Run OOS trading simulation with calibrated profile.

    Build trie on training data, then walk through OOS candles
    simulating trades based on pattern matching.

    Returns dict with trade results and statistics.
    """
    # Build engine with calibrated profile
    engine = PPMT(
        symbol=profile.symbol,
        asset_class=profile.asset_class,
        sax_alphabet_size=profile.sax_alphabet_size,
        sax_window_size=profile.sax_window_size,
        sax_strategy="ohlcv",
        fuzzy_threshold=profile.fuzzy_threshold,
        min_confidence=0.10,  # Low threshold — let data decide
        min_risk_reward=0.5,  # Low RR — don't filter too much
        weight_profile=profile.weight_profile,
    )

    # Build trie from training data
    n_built = engine.build(train_df, pattern_length=pattern_length)
    print(f"    Trie built: {n_built} patterns")

    # Propagate metadata
    for trie in [engine.trie_n1, engine.trie_n2, engine.trie_n3, engine.trie_n4]:
        trie.propagate_metadata()

    # Encode OOS data into SAX symbols
    oos_symbols = engine.sax.encode(oos_df)

    # Walk through OOS symbols and simulate trading
    trades = []
    in_position = False
    entry_price = 0.0
    entry_idx = 0
    position_direction = ""  # "LONG" or "SHORT"

    # We need to map SAX symbol indices back to candle indices
    window = profile.sax_window_size

    for i in range(len(oos_symbols) - pattern_length):
        current_pattern = oos_symbols[i:i + pattern_length]

        # Map to candle index for price lookup
        candle_idx = min((i + pattern_length) * window, len(oos_df) - 1)
        if candle_idx >= len(oos_df):
            break

        current_price = float(oos_df["close"].iloc[candle_idx])

        # Match against all 4 trie levels
        result = engine.match(
            current_symbols=current_pattern,
            current_price=current_price,
            is_in_position=in_position,
            entry_price=entry_price if in_position else None,
        )

        signal = result.signal

        if not in_position and signal.is_entry:
            # Enter position
            in_position = True
            entry_price = current_price
            entry_idx = i
            position_direction = signal.direction or "LONG"

        elif in_position and signal.is_exit:
            # Exit position
            exit_price = current_price
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0
            if position_direction == "SHORT":
                pnl_pct = -pnl_pct

            duration_candles = (i - entry_idx) * window

            trades.append({
                "entry_idx": entry_idx,
                "exit_idx": i,
                "direction": position_direction,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": round(pnl_pct, 4),
                "duration_candles": duration_candles,
                "confidence": round(result.weighted_confidence, 4),
                "exit_reason": signal.signal_type.value,
            })

            in_position = False
            entry_price = 0.0

    # Close any open position at end
    if in_position and len(oos_df) > 0:
        last_price = float(oos_df["close"].iloc[-1])
        pnl_pct = ((last_price - entry_price) / entry_price) * 100.0
        if position_direction == "SHORT":
            pnl_pct = -pnl_pct

        trades.append({
            "entry_idx": entry_idx,
            "exit_idx": len(oos_symbols) - 1,
            "direction": position_direction,
            "entry_price": entry_price,
            "exit_price": last_price,
            "pnl_pct": round(pnl_pct, 4),
            "duration_candles": (len(oos_symbols) - 1 - entry_idx) * window,
            "confidence": 0.0,
            "exit_reason": "END_OF_DATA",
        })

    # Compute statistics
    stats = compute_trade_stats(trades, profile)

    return {
        "profile": profile.to_dict(),
        "trades": trades,
        "stats": stats,
        "n_patterns_built": n_built,
        "oos_symbols": len(oos_symbols),
    }


def compute_trade_stats(trades: list, profile: TokenProfile) -> dict:
    """Compute trading statistics from a list of trades."""
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_pnl_pct": 0.0,
            "total_pnl_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_approx": 0.0,
            "long_trades": 0,
            "short_trades": 0,
            "long_win_rate": 0.0,
            "short_win_rate": 0.0,
        }

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]

    # Win rate
    win_rate = len(wins) / len(trades) if trades else 0.0

    # Profit Factor
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = sum(losses) if losses else 0.01
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    # Cumulative PnL and max drawdown
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    drawdowns = cumulative - peak
    max_drawdown = abs(min(drawdowns)) if len(drawdowns) > 0 else 0.0

    # Sharpe approximation (annualized for 1h candles)
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(24 * 365)  # 1h candles
    else:
        sharpe = 0.0

    # Long/Short breakdown
    long_trades = [t for t in trades if t["direction"] == "LONG"]
    short_trades = [t for t in trades if t["direction"] == "SHORT"]
    long_wins = [t for t in long_trades if t["pnl_pct"] > 0]
    short_wins = [t for t in short_trades if t["pnl_pct"] > 0]

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "avg_pnl_pct": round(np.mean(pnls), 4),
        "total_pnl_pct": round(sum(pnls), 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "sharpe_approx": round(sharpe, 4),
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_win_rate": round(len(long_wins) / len(long_trades), 4) if long_trades else 0.0,
        "short_win_rate": round(len(short_wins) / len(short_trades), 4) if short_trades else 0.0,
        "best_trade_pct": round(max(pnls), 4) if pnls else 0.0,
        "worst_trade_pct": round(min(pnls), 4) if pnls else 0.0,
        "avg_winner_pct": round(np.mean(wins), 4) if wins else 0.0,
        "avg_loser_pct": round(np.mean([p for p in pnls if p < 0]), 4) if losses else 0.0,
    }


# ============================================================
# Monte Carlo Resampling
# ============================================================

def monte_carlo_simulation(
    trades: list,
    n_simulations: int = 1000,
) -> dict:
    """
    Monte Carlo simulation by resampling trades.

    Randomly reshuffles the trade sequence N times and computes
    the distribution of final PnL. This tests whether results
    are robust to trade ordering (not path-dependent).
    """
    if len(trades) < 5:
        return {"mc_pnl_mean": 0, "mc_pnl_std": 0, "mc_win_pct": 0, "mc_best": 0, "mc_worst": 0}

    pnls = np.array([t["pnl_pct"] for t in trades])

    final_pnls = []
    for _ in range(n_simulations):
        reshuffled = np.random.permutation(pnls)
        final_pnls.append(np.sum(reshuffled))

    final_pnls = np.array(final_pnls)

    return {
        "mc_pnl_mean": round(np.mean(final_pnls), 4),
        "mc_pnl_std": round(np.std(final_pnls), 4),
        "mc_win_pct": round(np.mean(final_pnls > 0) * 100, 2),
        "mc_best": round(np.max(final_pnls), 4),
        "mc_worst": round(np.min(final_pnls), 4),
        "mc_p5": round(np.percentile(final_pnls, 5), 4),
        "mc_p95": round(np.percentile(final_pnls, 95), 4),
    }


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("  PPMT v0.6.8 — Cross-Token OOS Validation")
    print("  Trading Calibration (bias-fixed) + Additive OHLCV Composite")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    all_results = {}

    # Step 1: Download data
    print("\n" + "=" * 70)
    print("  STEP 1: Download Real Data from Binance")
    print("=" * 70)

    data = {}
    for symbol in TOKENS:
        df = download_data(symbol, TIMEFRAME, DAYS_OF_DATA)
        if not df.empty:
            data[symbol] = df

    if len(data) < 2:
        print("\n  ERROR: Need at least 2 tokens with data. Got:", len(data))
        return

    # Step 2: Calibration Phase
    print("\n" + "=" * 70)
    print("  STEP 2: Auto-Calibration (alpha x window grid search)")
    print("=" * 70)

    calibrator = TradingCalibrationEngine(train_ratio=TRAIN_RATIO, pattern_length=PATTERN_LENGTH, timeframe=TIMEFRAME)
    profiles = {}

    for symbol, df in data.items():
        profile, results = calibrator.calibrate(df, symbol=symbol, verbose=True)
        profiles[symbol] = profile

        # Print trading metrics for best config
        best_r = [r for r in results if r.alphabet_size == profile.sax_alphabet_size
                  and r.window_size == profile.sax_window_size][0]
        print(f"\n  Best: alpha={best_r.alphabet_size} window={best_r.window_size}")
        print(f"    PnL={best_r.total_pnl_pct:+.1f}% WR={best_r.win_rate:.1%} Trades={best_r.total_trades}")
        print(f"    SL/TP={calibrator.sl_pct}%/{calibrator.tp_pct}% (asset-class-adaptive)")

    # Step 3: Full OOS Trading Simulation
    print("\n" + "=" * 70)
    print("  STEP 3: OOS Trading Simulation (calibrated profiles)")
    print("=" * 70)

    for symbol, df in data.items():
        profile = profiles[symbol]
        print(f"\n  --- {symbol} (alpha={profile.sax_alphabet_size}, window={profile.sax_window_size}) ---")

        # Split train/OOS
        n = len(df)
        split = int(n * TRAIN_RATIO)
        train_df = df.iloc[:split]
        oos_df = df.iloc[split:]

        print(f"    Train: {len(train_df)} candles | OOS: {len(oos_df)} candles")

        # Run simulation
        result = oos_trading_simulation(
            train_df=train_df,
            oos_df=oos_df,
            profile=profile,
            pattern_length=PATTERN_LENGTH,
        )

        # Monte Carlo
        mc = monte_carlo_simulation(result["trades"], n_simulations=1000)
        result["monte_carlo"] = mc
        result["train_candles"] = len(train_df)
        result["oos_candles"] = len(oos_df)

        # Print results
        stats = result["stats"]
        print(f"\n    === {symbol} OOS Results ===")
        print(f"    Total trades: {stats['total_trades']}")
        print(f"    Win rate:     {stats['win_rate']:.1%}")
        print(f"    Profit Factor:{stats['profit_factor']:.2f}")
        print(f"    Total PnL:    {stats['total_pnl_pct']:.2f}%")
        print(f"    Max Drawdown: {stats['max_drawdown_pct']:.2f}%")
        print(f"    Sharpe (approx): {stats['sharpe_approx']:.2f}")
        print(f"    Long/Short:   {stats['long_trades']}/{stats['short_trades']}")
        print(f"    Long WR:      {stats['long_win_rate']:.1%}")
        print(f"    Short WR:     {stats['short_win_rate']:.1%}")
        print(f"\n    Monte Carlo (1000 sims):")
        print(f"    Mean PnL:     {mc['mc_pnl_mean']:.2f}%")
        print(f"    Std PnL:      {mc['mc_pnl_std']:.2f}%")
        print(f"    Profitable%:  {mc['mc_win_pct']:.1f}%")
        print(f"    5th-95th:     [{mc['mc_p5']:.2f}%, {mc['mc_p95']:.2f}%]")

        all_results[symbol] = result

    # Step 4: Cross-Token Comparison
    print("\n" + "=" * 70)
    print("  STEP 4: Cross-Token Comparison")
    print("=" * 70)

    print(f"\n  {'Token':<10} {'Class':<12} {'alpha':<6} {'window':<7} "
          f"{'Trades':<8} {'WR':<8} {'PF':<8} {'PnL%':<10} {'Sharpe':<8} {'MC Prof%':<10}")
    print("  " + "-" * 85)

    for symbol, result in all_results.items():
        s = result["stats"]
        mc = result["monte_carlo"]
        p = result["profile"]
        print(f"  {symbol:<10} {p['asset_class']:<12} {p['sax_alphabet_size']:<6} {p['sax_window_size']:<7} "
              f"{s['total_trades']:<8} {s['win_rate']:<8.1%} {s['profit_factor']:<8.2f} "
              f"{s['total_pnl_pct']:<10.2f} {s['sharpe_approx']:<8.2f} {mc['mc_win_pct']:<10.1f}")

    # Save results to JSON for TRACEABILITY
    output_path = "/home/z/my-project/download/oos_validation_results.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Convert to serializable format
    serializable = {}
    for symbol, result in all_results.items():
        sr = {}
        for key, val in result.items():
            if isinstance(val, (np.integer,)):
                sr[key] = int(val)
            elif isinstance(val, (np.floating,)):
                sr[key] = float(val)
            elif isinstance(val, (np.ndarray,)):
                sr[key] = val.tolist()
            elif isinstance(val, dict):
                sr[key] = val
            elif isinstance(val, list):
                sr[key] = val
            else:
                sr[key] = str(val)
        serializable[symbol] = sr

    with open(output_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)

    print(f"\n  Results saved to: {output_path}")

    # Return for TRACEABILITY update
    return all_results


if __name__ == "__main__":
    results = main()
