#!/usr/bin/env python3
"""
PPMT Multi-Timeframe Validation — 5m, 1m vs 1h Baseline

Tests the PPMT system on lower timeframes to determine if
shorter candles produce better (or worse) signal quality.

Tokens tested: BTC, SOL, DOGE (blue_chip, large_cap, meme)
Timeframes: 1h (baseline), 5m, 1m
Data source: Binance real data ONLY (no synthetic)

Key questions:
1. Does PPMT work on lower timeframes?
2. How many trades per timeframe?
3. Is win rate / PnL better or worse at 5m / 1m?
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

# Add project to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "ppmt" / "src"
sys.path.insert(0, str(SRC_PATH))

from ppmt.data.collector import DataCollector
from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.core.matcher import FuzzyMatcher


# ============================================================
# CONFIGURATION
# ============================================================

TOKEN_CONFIG = {
    "BTC/USDT":  {"asset_class": "blue_chip"},
    "SOL/USDT":  {"asset_class": "large_cap"},
    "DOGE/USDT": {"asset_class": "meme"},
}

TIMEFRAMES = {
    "1h":  {"days": 600, "train_pct": 0.80},   # ~14,400 candles
    "5m":  {"days": 40,  "train_pct": 0.80},   # ~11,520 candles
    "1m":  {"days": 8,   "train_pct": 0.80},   # ~11,520 candles
}

SAX_ALPHA = 3
SAX_WINDOW = 5
PATTERN_LENGTH = 5
FORWARD_WINDOW = 5

OUTPUT_PATH = PROJECT_ROOT.parent / "download" / "multi_timeframe_results.json"


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trades(symbols, close_prices, trie, min_confidence=0.10):
    """
    Simulate trades using trie pattern matching on OOS data.

    Walks through OOS SAX symbols, looks up each pattern in the trie,
    and enters trades based on expected_move direction with metadata SL/TP.

    Returns list of trade dicts.
    """
    trades = []
    in_position = False
    entry_price = 0.0
    entry_idx = 0
    direction = 0  # +1 long, -1 short
    sl_price = 0.0
    tp_price = 0.0
    max_favorable_pct = 0.0
    max_adverse_pct = 0.0

    i = PATTERN_LENGTH
    while i < len(symbols):
        current_pattern = symbols[i - PATTERN_LENGTH:i]

        if not in_position:
            # Look for entry signal
            node = trie.search(current_pattern)
            if node is None:
                node, depth = trie.search_prefix(current_pattern)
                if node is None or depth < 2:
                    i += 1
                    continue

            meta = node.metadata
            if meta.historical_count < 3:
                i += 1
                continue

            confidence = meta.confidence
            if confidence < min_confidence:
                i += 1
                continue

            expected_move = meta.expected_move_pct
            if abs(expected_move) < 0.1:
                i += 1
                continue

            # Enter trade
            price_idx = i * SAX_WINDOW
            if price_idx >= len(close_prices):
                break

            entry_price = close_prices[price_idx]
            entry_idx = i
            direction = 1 if expected_move > 0 else -1

            # Set SL/TP from metadata
            meta.compute_sl_tp(entry_price)
            sl_price = meta.sl_price if meta.sl_price else entry_price * (0.97 if direction == 1 else 1.03)
            tp_price = meta.tp_price if meta.tp_price else entry_price * (1.0 + expected_move / 100.0 * 0.8)

            max_favorable_pct = 0.0
            max_adverse_pct = 0.0
            in_position = True

        else:
            # Check exit conditions
            price_idx = i * SAX_WINDOW
            if price_idx >= len(close_prices):
                last_idx = min(price_idx, len(close_prices) - 1)
                exit_price = close_prices[last_idx]
                pnl_pct = direction * ((exit_price - entry_price) / entry_price) * 100.0
                trades.append({
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": pnl_pct,
                    "direction": "LONG" if direction == 1 else "SHORT",
                    "bars_held": i - entry_idx,
                    "exit_reason": "end_of_data",
                    "max_favorable_pct": max_favorable_pct,
                    "max_adverse_pct": max_adverse_pct,
                })
                break

            current_price = close_prices[price_idx]

            # Track excursion
            if direction == 1:
                favorable = ((current_price - entry_price) / entry_price) * 100.0
                adverse = ((entry_price - current_price) / entry_price) * 100.0
            else:
                favorable = ((entry_price - current_price) / entry_price) * 100.0
                adverse = ((current_price - entry_price) / entry_price) * 100.0

            max_favorable_pct = max(max_favorable_pct, favorable)
            max_adverse_pct = max(max_adverse_pct, adverse)

            # Check SL hit
            sl_hit = False
            tp_hit = False
            if direction == 1:
                if current_price <= sl_price:
                    sl_hit = True
                if tp_price and current_price >= tp_price:
                    tp_hit = True
            else:
                if current_price >= sl_price:
                    sl_hit = True
                if tp_price and current_price <= tp_price:
                    tp_hit = True

            # Check pattern break (only exit if in profit)
            pattern_break = False
            node = trie.search(current_pattern)
            if node is not None and i + 1 < len(symbols):
                next_sym = symbols[i]
                if not node.has_child(next_sym):
                    pnl_pct = direction * ((current_price - entry_price) / entry_price) * 100.0
                    if pnl_pct > 0.5:
                        pattern_break = True

            # Exit conditions
            exit_reason = None
            if sl_hit:
                exit_reason = "stop_loss"
            elif tp_hit:
                exit_reason = "take_profit"
            elif pattern_break:
                exit_reason = "pattern_break"
            elif max_adverse_pct > 8.0:
                exit_reason = "catastrophic"
            elif i - entry_idx > 200:
                exit_reason = "max_hold"

            if exit_reason:
                pnl_pct = direction * ((current_price - entry_price) / entry_price) * 100.0
                trades.append({
                    "entry_price": entry_price,
                    "exit_price": current_price,
                    "pnl_pct": pnl_pct,
                    "direction": "LONG" if direction == 1 else "SHORT",
                    "bars_held": i - entry_idx,
                    "exit_reason": exit_reason,
                    "max_favorable_pct": max_favorable_pct,
                    "max_adverse_pct": max_adverse_pct,
                })
                in_position = False
                i += 1
                continue

        i += 1

    return trades


def compute_stats(trades):
    """Compute trading statistics from list of trade dicts."""
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "total_pnl_pct": 0.0,
            "avg_pnl_per_trade": 0.0, "profit_factor": 0.0,
            "max_drawdown_pct": 0.0, "long_trades": 0, "short_trades": 0,
            "avg_bars_held": 0.0, "exit_reasons": {},
        }

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    total_pnl = sum(t["pnl_pct"] for t in trades)
    gross_profit = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0))

    # Max drawdown from cumulative PnL
    cum_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum_pnl += t["pnl_pct"]
        peak = max(peak, cum_pnl)
        dd = peak - cum_pnl
        max_dd = max(max_dd, dd)

    longs = sum(1 for t in trades if t["direction"] == "LONG")
    shorts = sum(1 for t in trades if t["direction"] == "SHORT")

    exit_reasons = {}
    for t in trades:
        r = t["exit_reason"]
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    return {
        "total_trades": len(trades),
        "win_rate": wins / len(trades) if trades else 0.0,
        "total_pnl_pct": total_pnl,
        "avg_pnl_per_trade": total_pnl / len(trades),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "max_drawdown_pct": max_dd,
        "long_trades": longs,
        "short_trades": shorts,
        "avg_bars_held": sum(t["bars_held"] for t in trades) / len(trades),
        "exit_reasons": exit_reasons,
    }


def run_monte_carlo(trades, n_sims=300):
    """Run Monte Carlo simulation by resampling trades."""
    if not trades:
        return {"mc_mean_pnl": 0.0, "mc_profitable_pct": 0.0, "mc_5th_pct": 0.0, "mc_95th_pct": 0.0}

    pnls = [t["pnl_pct"] for t in trades]
    sim_results = []

    for _ in range(n_sims):
        resampled = np.random.choice(pnls, size=len(pnls), replace=True)
        sim_results.append(sum(resampled))

    sim_results = np.array(sim_results)

    return {
        "mc_mean_pnl": float(np.mean(sim_results)),
        "mc_profitable_pct": float(np.mean(sim_results > 0) * 100),
        "mc_5th_pct": float(np.percentile(sim_results, 5)),
        "mc_95th_pct": float(np.percentile(sim_results, 95)),
    }


# ============================================================
# MAIN VALIDATION LOOP
# ============================================================

def validate_token_timeframe(symbol, asset_class, timeframe, days, train_pct):
    """
    Run full OOS validation for one token on one timeframe.
    Returns dict with all results including raw trades for MC.
    """
    print(f"\n{'='*60}")
    print(f"  {symbol} @ {timeframe} ({asset_class})")
    print(f"{'='*60}")

    # Fetch data
    print(f"  Fetching {days} days of {timeframe} data from Binance...")
    collector = DataCollector(exchange="binance")
    df = collector.fetch_and_save(symbol, timeframe, days=days)

    if df.empty:
        print(f"  ERROR: No data returned for {symbol} @ {timeframe}")
        return None, []

    n_candles = len(df)
    print(f"  Got {n_candles} candles ({df.index[0]} to {df.index[-1]})")

    if n_candles < 1000:
        print(f"  WARNING: Only {n_candles} candles — may be insufficient")

    # Split train/test
    train_size = int(n_candles * train_pct)
    train_df = df.iloc[:train_size]
    test_df = df.iloc[train_size:]

    print(f"  Train: {len(train_df)} candles, Test: {len(test_df)} candles")

    # Encode with consistent normalization (V7.9 fix)
    encoder = SAXEncoder(alphabet_size=SAX_ALPHA, window_size=SAX_WINDOW, strategy="ohlcv")

    train_symbols, train_mean, train_std = encoder.encode_with_normalization(train_df)
    test_symbols, _, _ = encoder.encode_with_normalization(test_df, paa_mean=train_mean, paa_std=train_std)

    print(f"  SAX symbols: {len(train_symbols)} train, {len(test_symbols)} test")

    # Build trie from training data
    print(f"  Building trie (alpha={SAX_ALPHA}, window={SAX_WINDOW})...")
    trie = PPMTTrie(name=f"{symbol}_{timeframe}")

    max_i = len(train_symbols) - PATTERN_LENGTH - FORWARD_WINDOW
    patterns_built = 0
    for i in range(max(0, max_i) + 1):
        pattern = train_symbols[i:i + PATTERN_LENGTH]
        next_sym = train_symbols[i + PATTERN_LENGTH] if i + PATTERN_LENGTH < len(train_symbols) else None

        start_candle = i * SAX_WINDOW
        end_candle = (i + PATTERN_LENGTH) * SAX_WINDOW
        forward_candle = (i + PATTERN_LENGTH + FORWARD_WINDOW) * SAX_WINDOW

        if forward_candle > len(train_df):
            break

        pattern_df = train_df.iloc[start_candle:end_candle]
        forward_df = train_df.iloc[end_candle:forward_candle]
        combined_df = train_df.iloc[start_candle:forward_candle]

        if len(pattern_df) == 0 or len(combined_df) == 0:
            continue

        entry_price = pattern_df["close"].iloc[0]
        exit_price = combined_df["close"].iloc[-1]
        move_pct = ((exit_price - entry_price) / entry_price) * 100.0

        high = combined_df["high"].max()
        low = combined_df["low"].min()
        drawdown_pct = ((low - entry_price) / entry_price) * 100.0
        favorable_pct = ((high - entry_price) / entry_price) * 100.0
        duration = len(combined_df)
        won = move_pct > 0

        trie.insert_with_observations(
            symbols=pattern,
            move_pct=move_pct,
            drawdown_pct=drawdown_pct,
            favorable_pct=favorable_pct,
            duration=duration,
            won=won,
            next_symbol=next_sym,
        )
        patterns_built += 1

    print(f"  Trie built: {patterns_built} patterns, {trie.pattern_count} unique")

    # Simulate trades on test data
    test_close = test_df["close"].values
    print(f"  Simulating trades on {len(test_symbols)} OOS symbols...")

    trades = simulate_trades(test_symbols, test_close, trie)
    stats = compute_stats(trades)

    # Monte Carlo
    mc = run_monte_carlo(trades, n_sims=300)

    # Print results
    print(f"\n  RESULTS:")
    print(f"    Total trades:     {stats['total_trades']}")
    print(f"    Long/Short:       {stats['long_trades']}/{stats['short_trades']}")
    print(f"    Win rate:         {stats['win_rate']:.1%}")
    print(f"    Total PnL:        {stats['total_pnl_pct']:+.2f}%")
    print(f"    Avg PnL/trade:    {stats['avg_pnl_per_trade']:+.4f}%")
    print(f"    Profit factor:    {stats['profit_factor']:.2f}")
    print(f"    Max drawdown:     {stats['max_drawdown_pct']:.2f}%")
    print(f"    Avg bars held:    {stats['avg_bars_held']:.1f}")
    print(f"    MC profitable:    {mc['mc_profitable_pct']:.0f}%")
    print(f"    MC mean PnL:      {mc['mc_mean_pnl']:+.2f}%")
    print(f"    Exit reasons:     {stats['exit_reasons']}")

    result = {
        "symbol": symbol,
        "asset_class": asset_class,
        "timeframe": timeframe,
        "candles_fetched": n_candles,
        "train_candles": len(train_df),
        "test_candles": len(test_df),
        "train_sax_symbols": len(train_symbols),
        "test_sax_symbols": len(test_symbols),
        "patterns_built": patterns_built,
        "unique_patterns": trie.pattern_count,
        "sax_alpha": SAX_ALPHA,
        "sax_window": SAX_WINDOW,
        **stats,
        **mc,
    }

    return result, trades


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("  PPMT MULTI-TIMEFRAME VALIDATION")
    print(f"  Timeframes: {list(TIMEFRAMES.keys())}")
    print(f"  Tokens: {list(TOKEN_CONFIG.keys())}")
    print(f"  SAX: alpha={SAX_ALPHA}, window={SAX_WINDOW}")
    print(f"  Composite: ADDITIVE (0.4*body + 0.35*dir + 0.25*vol)")
    print(f"  Date: {datetime.now().isoformat()}")
    print("=" * 70)

    all_results = {}
    summary = {}

    for tf_name, tf_config in TIMEFRAMES.items():
        print(f"\n\n{'#'*70}")
        print(f"  TIMEFRAME: {tf_name} (fetching {tf_config['days']} days)")
        print(f"{'#'*70}")

        tf_results = {}
        tf_summary = {
            "timeframe": tf_name,
            "tokens_tested": 0,
            "tokens_profitable": 0,
            "avg_pnl_pct": 0.0,
            "avg_win_rate": 0.0,
            "avg_trades": 0.0,
        }

        for symbol, config in TOKEN_CONFIG.items():
            result, trades = validate_token_timeframe(
                symbol=symbol,
                asset_class=config["asset_class"],
                timeframe=tf_name,
                days=tf_config["days"],
                train_pct=tf_config["train_pct"],
            )

            if result is not None:
                tf_results[symbol] = result
                tf_summary["tokens_tested"] += 1
                if result["total_pnl_pct"] > 0:
                    tf_summary["tokens_profitable"] += 1

        # Compute TF averages
        if tf_results:
            pnls = [r["total_pnl_pct"] for r in tf_results.values()]
            wrs = [r["win_rate"] for r in tf_results.values()]
            n_trades = [r["total_trades"] for r in tf_results.values()]
            tf_summary["avg_pnl_pct"] = float(np.mean(pnls))
            tf_summary["avg_win_rate"] = float(np.mean(wrs))
            tf_summary["avg_trades"] = float(np.mean(n_trades))

        all_results[tf_name] = tf_results
        summary[tf_name] = tf_summary

    # Save results
    output = {
        "timestamp": datetime.now().isoformat(),
        "sax_config": {"alpha": SAX_ALPHA, "window": SAX_WINDOW, "strategy": "ohlcv"},
        "composite_formula": "additive (0.4*body_position + 0.35*direction + 0.25*vol_signal)",
        "timeframe_summary": summary,
        "detailed_results": {
            tf: {sym: r for sym, r in results.items()}
            for tf, results in all_results.items()
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n\n{'='*70}")
    print(f"  RESULTS SAVED TO: {OUTPUT_PATH}")
    print(f"{'='*70}")

    # Print comparison table
    print(f"\n\n{'='*70}")
    print(f"  TIMEFRAME COMPARISON TABLE")
    print(f"{'='*70}")
    print(f"  {'Token':<12} {'TF':<5} {'Trades':>7} {'WinRate':>8} {'PnL%':>10} {'PF':>7} {'MaxDD%':>8} {'MC%':>5}")
    print(f"  {'-'*12} {'-'*5} {'-'*7} {'-'*8} {'-'*10} {'-'*7} {'-'*8} {'-'*5}")

    for tf_name in TIMEFRAMES:
        for symbol in TOKEN_CONFIG:
            if symbol in all_results[tf_name]:
                r = all_results[tf_name][symbol]
                mc_prof = r.get("mc_profitable_pct", 0)
                print(f"  {symbol:<12} {tf_name:<5} {r['total_trades']:>7} "
                      f"{r['win_rate']:>7.1%} {r['total_pnl_pct']:>+9.2f}% "
                      f"{r['profit_factor']:>7.2f} {r['max_drawdown_pct']:>7.2f}% "
                      f"{mc_prof:>4.0f}%")
        print()

    # Summary comparison
    print(f"\n  TIMEFRAME AVERAGES:")
    print(f"  {'TF':<5} {'AvgTrades':>10} {'AvgWinRate':>12} {'AvgPnL%':>10} {'Profitable':>12}")
    print(f"  {'-'*5} {'-'*10} {'-'*12} {'-'*10} {'-'*12}")
    for tf_name, tf_sum in summary.items():
        print(f"  {tf_name:<5} {tf_sum['avg_trades']:>10.1f} {tf_sum['avg_win_rate']:>11.1%} "
              f"{tf_sum['avg_pnl_pct']:>+9.2f}% {tf_sum['tokens_profitable']}/{tf_sum['tokens_tested']:>9}")

    return output


if __name__ == "__main__":
    main()
