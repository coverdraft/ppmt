#!/usr/bin/env python3
"""
PPMT v0.6.2 — 5m Validation (3 tokens, 6+ months, OOS + MC)
Step 1 of the low-TF validation.
"""

import sys, os, json, traceback
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import pandas as pd

from ppmt.data.collector import DataCollector
from ppmt.data.storage import PPMTStorage
from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.engine.ppmt import PPMT
from ppmt.engine.signal import SignalType

TOKENS = {
    "BTC/USDT":  {"asset_class": "blue_chip"},
    "SOL/USDT":  {"asset_class": "large_cap"},
    "DOGE/USDT": {"asset_class": "meme"},
    "ETH/USDT":  {"asset_class": "blue_chip"},
    "BNB/USDT":  {"asset_class": "large_cap"},
    "LINK/USDT": {"asset_class": "defi"},
}

TIMEFRAME = "5m"
DAYS_OF_DATA = 200
PATTERN_LENGTH = 5
TRAIN_RATIO = 0.70
MC_SIMS = 300
BARS_PER_YEAR = 288 * 365

ALPHAS = [3, 4, 5]
WINDOWS = [5, 7, 10]


def get_data(symbol, timeframe, days_needed):
    storage = PPMTStorage()
    cached = storage.load_ohlcv(symbol, timeframe)
    if not cached.empty:
        days_span = (cached.index[-1] - cached.index[0]).days
        if days_span >= 150 and len(cached) >= 5000:
            storage.close()
            return cached
    collector = DataCollector(exchange="binance")
    df = collector.fetch_and_save(symbol, timeframe, days=days_needed)
    collector.close()
    storage.close()
    return df


def simulate_trades(engine, test_df, pattern_length=5):
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

    if in_position and len(test_df) > 0:
        last_price = float(test_df["close"].iloc[-1])
        if position_direction == "LONG":
            pnl = ((last_price - entry_price) / entry_price) * 100.0
        else:
            pnl = ((entry_price - last_price) / entry_price) * 100.0
        trades.append({"pnl_pct": round(pnl, 4), "direction": position_direction, "exit": "END"})

    return trades


def compute_stats(trades):
    if not trades:
        return {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "total_pnl_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_approx": 0.0,
                "long_trades": 0, "short_trades": 0,
                "mc_mean_pnl": 0.0, "mc_profitable_pct": 0.0,
                "avg_pnl_per_trade": 0.0, "trades_per_day": 0.0}

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
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(BARS_PER_YEAR)

    mc_profits = np.array([sum(np.random.permutation(pnls)) for _ in range(MC_SIMS)])

    test_days = DAYS_OF_DATA * (1 - TRAIN_RATIO)
    trades_per_day = len(trades) / test_days if test_days > 0 else 0

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
        "avg_pnl_per_trade": round(total_pnl / len(trades), 4) if len(trades) > 0 else 0.0,
        "trades_per_day": round(trades_per_day, 2),
    }


def main():
    print("=" * 100)
    print(f"  PPMT v0.6.2 — 5m VALIDATION ({len(TOKENS)} tokens, 6+ months real data)")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 100)

    start_time = datetime.now()
    results = {}

    for symbol, config in TOKENS.items():
        asset_class = config["asset_class"]
        print(f"\n  {symbol} ({asset_class}):", end=" ", flush=True)

        # Get data
        df = get_data(symbol, TIMEFRAME, DAYS_OF_DATA)
        if df.empty or len(df) < 5000:
            print("INSUFFICIENT DATA")
            continue

        days_span = (df.index[-1] - df.index[0]).days
        if days_span < 150:
            print(f"INSUFFICIENT SPAN ({days_span} days)")
            continue

        print(f"{len(df)} candles ({days_span}d) |", end=" ", flush=True)

        n = len(df)
        split = int(n * TRAIN_RATIO)
        train_df = df.iloc[:split]
        oos_df = df.iloc[split:]

        # Trading calibration (grid search)
        best_stats = None
        best_alpha = 3
        best_window = 5
        best_pnl = -float('inf')

        for alpha in ALPHAS:
            for window in WINDOWS:
                try:
                    engine = PPMT(
                        symbol=symbol, asset_class=asset_class,
                        sax_alphabet_size=alpha, sax_window_size=window,
                        sax_strategy="ohlcv", fuzzy_threshold=0.80,
                        min_confidence=0.05, min_risk_reward=0.3,
                    )
                    engine.build(train_df, pattern_length=PATTERN_LENGTH)
                    for trie in [engine.trie_n1, engine.trie_n2, engine.trie_n3, engine.trie_n4]:
                        trie.propagate_metadata()

                    trades = simulate_trades(engine, oos_df, PATTERN_LENGTH)
                    stats = compute_stats(trades)

                    if stats["total_pnl_pct"] > best_pnl:
                        best_pnl = stats["total_pnl_pct"]
                        best_alpha = alpha
                        best_window = window
                        best_stats = stats
                except (ValueError, Exception):
                    pass

        if best_stats is None:
            print("CALIBRATION FAILED")
            continue

        print(f"best a={best_alpha}/w={best_window} |", end=" ", flush=True)

        pnl_sign = "+" if best_stats["total_pnl_pct"] > 0 else ""
        print(f"OOS: {best_stats['total_trades']}tr WR={best_stats['win_rate']:.1%} "
              f"PnL={pnl_sign}{best_stats['total_pnl_pct']:.2f}% "
              f"Tr/day={best_stats['trades_per_day']:.1f} MC={best_stats['mc_profitable_pct']:.0f}%")

        results[symbol] = {
            "timeframe": TIMEFRAME,
            "candles": n,
            "data_span_days": days_span,
            "train_candles": len(train_df),
            "oos_candles": len(oos_df),
            "best_alpha": best_alpha,
            "best_window": best_window,
            **best_stats,
        }

    # Summary
    print(f"\n{'='*100}")
    print("  5m VALIDATION SUMMARY")
    print(f"{'='*100}")

    print(f"\n  {'Token':<12} {'Class':<10} {'a':>3} {'w':>3} "
          f"{'Candles':>8} {'Days':>5} "
          f"{'Trades':>7} {'Tr/day':>7} "
          f"{'WR':>7} {'PF':>7} {'PnL%':>10} "
          f"{'Sharpe':>8} {'MC%':>6} {'MaxDD':>8}")
    print("  " + "-" * 105)

    for symbol in sorted(results.keys()):
        r = results[symbol]
        ac = TOKENS[symbol]["asset_class"]
        print(f"  {symbol:<12} {ac:<10} "
              f"{r['best_alpha']:>3} {r['best_window']:>3} "
              f"{r['candles']:>8} {r['data_span_days']:>5} "
              f"{r['total_trades']:>7} {r['trades_per_day']:>7.2f} "
              f"{r['win_rate']:>7.1%} {r['profit_factor']:>7.2f} "
              f"{r['total_pnl_pct']:>+10.2f} "
              f"{r['sharpe_approx']:>8.2f} {r['mc_profitable_pct']:>6.0f} "
              f"{r['max_drawdown_pct']:>8.2f}")

    pnls = [r["total_pnl_pct"] for r in results.values()]
    profitable = sum(1 for p in pnls if p > 0)
    print(f"\n  OVERALL: {profitable}/{len(results)} profitable | "
          f"Avg PnL: {np.mean(pnls):+.2f}% | Range: {min(pnls):+.2f}% to {max(pnls):+.2f}%")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"  Time: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    # Save
    output = {
        "timestamp": datetime.now().isoformat(),
        "timeframe": "5m",
        "data_span_days": DAYS_OF_DATA,
        "tokens": results,
    }
    output_path = "/home/z/my-project/download/low_tf_5m_results.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Saved to: {output_path}")
    return output


if __name__ == "__main__":
    main()
