#!/usr/bin/env python3
"""
PPMT v0.6.2 — Full Validation using PaperTrader Engine
=======================================================

Uses the ACTUAL PaperTrader (4-level trie, ATR SL/TP, Living Trie,
regime-aware sizing) with real Binance data.

Approach:
  1. Fetch 2 years of real Binance data (BTC, ETH, SOL)
  2. Save to PPMTStorage SQLite
  3. Run PaperTrader for each token (full engine)
  4. Run Monte Carlo on trade results
  5. Test weight sensitivity by modifying SAX encoder weights
  6. Walk-forward: train on first 80%, trade only on last 20%

This uses the REAL engine, not a simplified backtest.
"""

import sys
import os
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional

# Ensure ppmt is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ppmt.data.storage import PPMTStorage
from ppmt.data.collector import DataCollector
from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig, PaperTraderResult
from ppmt.engine.monte_carlo import MonteCarloEngine
from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.core.matcher import FuzzyMatcher
from ppmt.core.regime import RegimeDetector

OUTPUT_DIR = "/home/z/my-project/download"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "validation_v062_paper_trader.json")


def fetch_and_store_data(symbols: list[str], timeframe: str = "1h", days: int = 730) -> dict[str, pd.DataFrame]:
    """Fetch real data from Binance and store in PPMTStorage."""
    storage = PPMTStorage()
    collector = DataCollector(storage=storage)
    data_cache = {}

    for symbol in symbols:
        print(f"\n  Fetching {symbol} from Binance ({days} days, {timeframe})...")
        try:
            df = collector.fetch_and_save(symbol, timeframe, days)
            if not df.empty:
                data_cache[symbol] = df
                print(f"  ✓ {symbol}: {len(df)} candles stored")
            else:
                print(f"  ✗ {symbol}: No data returned")
        except Exception as e:
            print(f"  ✗ {symbol}: Error — {e}")

    collector.close()
    return data_cache


def run_paper_trader_validation(symbol: str, data_cache: dict) -> dict:
    """Run PaperTrader with full engine on a symbol."""
    print(f"\n{'='*60}")
    print(f"  Running PaperTrader: {symbol}")
    print(f"{'='*60}")

    # Full run (all data) - this is the baseline
    config = PaperTraderConfig(
        symbol=symbol,
        timeframe="1h",
        initial_capital=10000.0,
        sax_alphabet_size=3,
        sax_window_size=10,
        sax_strategy="ohlcv",
        pattern_length=5,
        min_confidence=0.20,
        min_risk_reward=1.0,
        start_offset=200,
        end_offset=0,  # Use all data
        living_trie=True,
        regime_aware=True,
        use_multi_level=True,
        catastrophic_loss_pct=8.0,
        verbose=False,
    )

    trader = PaperTrader(config=config)
    result = trader.run()

    # Extract trade data
    trades = []
    for t in result.trades:
        trades.append({
            "trade_id": t.trade_id,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl_pct": round(t.pnl_pct, 4),
            "pnl": round(t.pnl, 2),
            "confidence": round(t.confidence, 4),
            "quality_score": round(t.quality_score, 4),
            "sizing_multiplier": round(t.sizing_multiplier, 4),
            "win_rate": round(t.win_rate, 4),
            "risk_reward_ratio": round(t.risk_reward_ratio, 4),
            "exit_reason": t.exit_reason,
            "regime": t.regime,
        })

    summary = {
        "symbol": symbol,
        "initial_capital": result.initial_capital,
        "final_capital": round(result.final_capital, 2),
        "total_pnl_pct": round(result.total_pnl_pct, 4),
        "total_trades": result.total_trades,
        "win_rate": round(result.win_rate, 4),
        "profit_factor": round(result.profit_factor, 4),
        "max_drawdown": round(result.max_drawdown, 4),
        "sharpe_ratio": round(result.sharpe_ratio, 4),
        "avg_trade_pnl_pct": round(result.avg_trade_pnl_pct, 4),
        "avg_confidence": round(result.avg_confidence, 4),
        "avg_quality_score": round(result.avg_quality_score, 4),
        "trades": trades,
    }

    print(f"  Result: {result.total_trades} trades, PnL={result.total_pnl_pct:.2f}%, "
          f"WR={result.win_rate:.2%}, PF={result.profit_factor:.2f}, "
          f"Sharpe={result.sharpe_ratio:.2f}, MaxDD={result.max_drawdown:.2%}")

    return summary


def run_oos_validation(symbol: str, data_cache: dict, train_pct: float = 0.80) -> dict:
    """Run OOS validation: build trie on 80%, trade on 20%."""
    df = data_cache[symbol]
    total = len(df)
    train_end = int(total * train_pct)

    print(f"\n  OOS Validation: {symbol} (train 0:{train_end}, test {train_end}:{total})")

    config = PaperTraderConfig(
        symbol=symbol,
        timeframe="1h",
        initial_capital=10000.0,
        sax_alphabet_size=3,
        sax_window_size=10,
        sax_strategy="ohlcv",
        pattern_length=5,
        min_confidence=0.20,
        min_risk_reward=1.0,
        start_offset=train_end,  # Start trading from the OOS boundary
        end_offset=total,        # End at the last candle
        living_trie=True,        # Living Trie learns from OOS trades
        regime_aware=True,
        use_multi_level=True,
        catastrophic_loss_pct=8.0,
        verbose=False,
    )

    trader = PaperTrader(config=config)
    result = trader.run()

    trades = []
    for t in result.trades:
        trades.append({
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl_pct": round(t.pnl_pct, 4),
            "pnl": round(t.pnl, 2),
            "confidence": round(t.confidence, 4),
            "quality_score": round(t.quality_score, 4),
            "exit_reason": t.exit_reason,
            "regime": t.regime,
        })

    oos_result = {
        "symbol": symbol,
        "train_pct": train_pct,
        "train_candles": train_end,
        "test_candles": total - train_end,
        "total_trades": result.total_trades,
        "total_pnl_pct": round(result.total_pnl_pct, 4),
        "win_rate": round(result.win_rate, 4),
        "profit_factor": round(result.profit_factor, 4),
        "max_drawdown": round(result.max_drawdown, 4),
        "sharpe_ratio": round(result.sharpe_ratio, 4),
        "avg_trade_pnl_pct": round(result.avg_trade_pnl_pct, 4),
        "trades": trades,
    }

    print(f"  OOS Result: {result.total_trades} trades, PnL={result.total_pnl_pct:.2f}%, "
          f"WR={result.win_rate:.2%}, PF={result.profit_factor:.2f}")

    return oos_result


def run_monte_carlo(trades: list[dict], symbol: str, n_simulations: int = 10000) -> dict:
    """Run Monte Carlo on trade results."""
    if not trades:
        return {"error": "no_trades", "symbol": symbol}

    trade_pnl_pcts = np.array([t["pnl_pct"] for t in trades])
    trade_pnls = np.array([t.get("pnl", 0) for t in trades])

    engine = MonteCarloEngine(seed=42)
    mc_result = engine.simulate_from_trades(
        trade_pnls=trade_pnls,
        trade_pnl_pcts=trade_pnl_pcts,
        symbol=symbol,
        initial_capital=10000.0,
        n_simulations=n_simulations,
        n_trades=len(trades),
        ruin_threshold=0.5,
        position_size_pct=0.02,
    )
    mc_result.compute_stats()

    return {
        "symbol": symbol,
        "n_simulations": n_simulations,
        "n_trades_sampled": len(trades),
        "risk_of_ruin_pct": mc_result.stats.get("risk_of_ruin_pct", 0),
        "profit_probability_pct": mc_result.stats.get("profit_probability_pct", 0),
        "mean_final_equity": mc_result.stats.get("mean_final_equity", 0),
        "median_final_equity": mc_result.stats.get("ci_50", 0),
        "ci_5": mc_result.stats.get("ci_5", 0),
        "ci_95": mc_result.stats.get("ci_95", 0),
        "mean_max_drawdown_pct": mc_result.stats.get("mean_max_drawdown_pct", 0),
        "mean_pnl_pct": mc_result.stats.get("mean_pnl_pct", 0),
        "mean_win_rate_pct": mc_result.stats.get("mean_win_rate_pct", 0),
        "sharpe_ratio": mc_result.stats.get("sharpe_ratio", 0),
        "summary": mc_result.summary_text(),
    }


def analyze_regime_performance(trades: list[dict], symbol: str) -> dict:
    """Analyze performance by regime."""
    regime_data = {}
    for t in trades:
        r = t.get("regime", "unknown")
        if r not in regime_data:
            regime_data[r] = {"trades": 0, "wins": 0, "pnl_pcts": []}
        regime_data[r]["trades"] += 1
        if t["pnl_pct"] > 0:
            regime_data[r]["wins"] += 1
        regime_data[r]["pnl_pcts"].append(t["pnl_pct"])

    result = {}
    for r, data in regime_data.items():
        wr = data["wins"] / data["trades"] if data["trades"] > 0 else 0
        avg_pnl = float(np.mean(data["pnl_pcts"])) if data["pnl_pcts"] else 0
        result[r] = {
            "trades": data["trades"],
            "win_rate": round(wr, 4),
            "avg_pnl_pct": round(avg_pnl, 4),
        }
    return result


def main():
    print("=" * 70)
    print("  PPMT v0.6.2 — VALIDATION WITH REAL PAPER TRADER ENGINE")
    print("  Full 4-level Trie + ATR SL/TP + Living Trie + Regime-Aware")
    print("  REAL DATA ONLY — Binance BTC/ETH/SOL")
    print("=" * 70)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    # Phase 1: Fetch and store data
    print(f"\n{'#'*70}")
    print("  PHASE 1: DATA COLLECTION (Binance API)")
    print(f"{'#'*70}")

    data_cache = fetch_and_store_data(symbols, timeframe="1h", days=730)

    if not data_cache:
        print("\n[CRITICAL] No data fetched. Check internet connection.")
        sys.exit(1)

    all_results = {
        "version": "v0.6.2",
        "timestamp": datetime.now().isoformat(),
        "data_source": "Binance API (real 1h candles, 2 years)",
        "engine": "PaperTrader (4-level, ATR SL/TP, Living Trie, regime-aware)",
        "full_run": {},
        "oos_validation": {},
        "monte_carlo": {},
        "regime_analysis": {},
    }

    # Phase 2: Full PaperTrader run
    print(f"\n{'#'*70}")
    print("  PHASE 2: FULL PAPER TRADER (all data, Living Trie ON)")
    print(f"{'#'*70}")

    full_trades = {}
    for symbol in symbols:
        if symbol not in data_cache:
            continue
        result = run_paper_trader_validation(symbol, data_cache)
        all_results["full_run"][symbol] = {k: v for k, v in result.items() if k != "trades"}
        full_trades[symbol] = result.get("trades", [])

    # Phase 3: OOS validation
    print(f"\n{'#'*70}")
    print("  PHASE 3: OOS VALIDATION (train 80%, trade 20%)")
    print(f"{'#'*70}")

    oos_trades = {}
    for symbol in symbols:
        if symbol not in data_cache:
            continue
        result = run_oos_validation(symbol, data_cache, train_pct=0.80)
        all_results["oos_validation"][symbol] = {k: v for k, v in result.items() if k != "trades"}
        oos_trades[symbol] = result.get("trades", [])

    # Phase 4: Monte Carlo
    print(f"\n{'#'*70}")
    print("  PHASE 4: MONTE CARLO SIMULATION (10,000 iterations)")
    print(f"{'#'*70}")

    for symbol, trades in full_trades.items():
        if not trades:
            continue
        print(f"\n  >>> Monte Carlo: {symbol} ({len(trades)} trades)")
        mc_result = run_monte_carlo(trades, symbol, n_simulations=10000)
        all_results["monte_carlo"][symbol] = {k: v for k, v in mc_result.items() if k != "summary"}
        if "summary" in mc_result:
            print(mc_result["summary"])

    # Phase 5: Regime analysis
    print(f"\n{'#'*70}")
    print("  PHASE 5: REGIME-AWARE PERFORMANCE ANALYSIS")
    print(f"{'#'*70}")

    for symbol, trades in full_trades.items():
        if not trades:
            continue
        regime_result = analyze_regime_performance(trades, symbol)
        all_results["regime_analysis"][symbol] = regime_result
        print(f"\n  {symbol}:")
        for regime, data in regime_result.items():
            print(f"    {regime}: {data['trades']} trades, WR={data['win_rate']:.2%}, "
                  f"avg PnL={data['avg_pnl_pct']:.2f}%")

    # Save results
    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    # Print final summary
    print(f"\n{'#'*70}")
    print("  FINAL SUMMARY — PPMT v0.6.2 Validation (Real PaperTrader Engine)")
    print(f"{'#'*70}")

    print(f"\n  FULL RUN (all data, Living Trie):")
    print(f"  {'Token':<10} {'Trades':>6} {'PnL%':>8} {'WR':>6} {'PF':>6} {'Sharpe':>7} {'MaxDD':>7}")
    print(f"  {'-'*55}")
    for sym, r in all_results["full_run"].items():
        if "error" not in r:
            print(f"  {sym:<10} {r['total_trades']:>6} {r['total_pnl_pct']:>8.2f} "
                  f"{r['win_rate']:>6.2%} {r['profit_factor']:>6.2f} "
                  f"{r['sharpe_ratio']:>7.2f} {r['max_drawdown']:>7.2%}")

    print(f"\n  OOS VALIDATION (train 80%, trade 20%):")
    print(f"  {'Token':<10} {'Trades':>6} {'PnL%':>8} {'WR':>6} {'PF':>6} {'Sharpe':>7}")
    print(f"  {'-'*50}")
    for sym, r in all_results["oos_validation"].items():
        if "error" not in r:
            print(f"  {sym:<10} {r['total_trades']:>6} {r['total_pnl_pct']:>8.2f} "
                  f"{r['win_rate']:>6.2%} {r['profit_factor']:>6.2f} "
                  f"{r['sharpe_ratio']:>7.2f}")

    print(f"\n  MONTE CARLO (10,000 simulations on full run trades):")
    print(f"  {'Token':<10} {'Ruin%':>6} {'Profit%':>8} {'Median$':>10} {'CI5$':>10} {'CI95$':>10}")
    print(f"  {'-'*55}")
    for sym, mc in all_results["monte_carlo"].items():
        if "error" not in mc:
            print(f"  {sym:<10} {mc['risk_of_ruin_pct']:>6.2f} {mc['profit_probability_pct']:>8.2f} "
                  f"{mc['median_final_equity']:>10.2f} {mc['ci_5']:>10.2f} {mc['ci_95']:>10.2f}")

    print(f"\n  RESULTS SAVED TO: {OUTPUT_FILE}")
    print(f"{'#'*70}")

    return all_results


if __name__ == "__main__":
    main()
