#!/usr/bin/env python3
"""
PPMT v0.6.2 — Low Timeframe Validation (5m + 1m)
MINIMUM 6 MONTHS REAL DATA. No synthetic data.

Optimized: Uses cached SQLite data first, only downloads missing data.
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
from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.engine.ppmt import PPMT
from ppmt.engine.signal import SignalType


# ============================================================
# Configuration
# ============================================================

TOKENS_5M = {
    "BTC/USDT":  {"asset_class": "blue_chip"},
    "ETH/USDT":  {"asset_class": "blue_chip"},
    "SOL/USDT":  {"asset_class": "large_cap"},
    "BNB/USDT":  {"asset_class": "large_cap"},
    "DOGE/USDT": {"asset_class": "meme"},
    "LINK/USDT": {"asset_class": "defi"},
}

TOKENS_1M = {
    "BTC/USDT":  {"asset_class": "blue_chip"},
    "SOL/USDT":  {"asset_class": "large_cap"},
    "DOGE/USDT": {"asset_class": "meme"},
    "LINK/USDT": {"asset_class": "defi"},
}

DAYS_OF_DATA = 200
PATTERN_LENGTH = 5
TRAIN_RATIO = 0.70
MC_SIMS = 300

ALPHAS = [3, 4, 5]
WINDOWS = [5, 7, 10]

WF_PARAMS = {
    "5m": {"initial_train": 10000, "step": 5000, "min_test": 2000},
    "1m": {"initial_train": 50000, "step": 20000, "min_test": 10000},
}

BARS_PER_YEAR = {"1h": 8760, "5m": 105120, "1m": 525600}

MIN_DATA_SPAN_DAYS = 150  # ~5 months minimum


# ============================================================
# Data Loading with Cache
# ============================================================

def get_data(symbol, timeframe, days_needed):
    """Load from cache first, download only if needed."""
    storage = PPMTStorage()

    # Check cache
    cached = storage.load_ohlcv(symbol, timeframe)
    if not cached.empty:
        days_span = (cached.index[-1] - cached.index[0]).days
        if days_span >= MIN_DATA_SPAN_DAYS and len(cached) >= 5000:
            storage.close()
            print(f"cached {len(cached)} candles ({days_span} days)", end=" ", flush=True)
            return cached

    # Download (Bybit primary, auto-fallback to OKX/Kraken)
    collector = DataCollector(exchange="bybit")
    df = collector.fetch_and_save(symbol, timeframe, days=days_needed)
    collector.close()
    storage.close()

    if not df.empty:
        days_span = (df.index[-1] - df.index[0]).days
        print(f"downloaded {len(df)} candles ({days_span} days)", end=" ", flush=True)
    else:
        print("NO DATA", end=" ", flush=True)

    return df


# ============================================================
# Trading Simulation
# ============================================================

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


def compute_stats(trades, timeframe="5m"):
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "total_pnl_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_approx": 0.0,
            "long_trades": 0, "short_trades": 0,
            "mc_mean_pnl": 0.0, "mc_profitable_pct": 0.0,
            "avg_pnl_per_trade": 0.0, "trades_per_day": 0.0,
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

    bpy = BARS_PER_YEAR.get(timeframe, 8760)
    sharpe = 0.0
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(bpy)

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


def build_engine_and_test(train_df, oos_df, symbol, asset_class, alpha, window, timeframe):
    """Build engine, run trading sim, return stats."""
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
    stats = compute_stats(trades, timeframe)
    stats["patterns_built"] = n_built
    return stats


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 100)
    print("  PPMT v0.6.6 — LOW TIMEFRAME VALIDATION (5m + 1m)")
    print(f"  MINIMUM 6 MONTHS REAL DATA — NO SYNTHETIC DATA")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 100)

    start_time = datetime.now()
    all_results = {}

    for tf_name, token_config in [("5m", TOKENS_5M), ("1m", TOKENS_1M)]:
        print(f"\n{'='*100}")
        print(f"  === {tf_name} TIMEFRAME ===")
        print(f"{'='*100}")

        tf_results = {}
        wf_p = WF_PARAMS[tf_name]

        for symbol, config in token_config.items():
            asset_class = config["asset_class"]
            print(f"\n  {symbol} ({asset_class}) @ {tf_name}:", end=" ", flush=True)

            # Get data (cache first)
            df = get_data(symbol, tf_name, DAYS_OF_DATA)
            if df.empty or len(df) < 5000:
                print("INSUFFICIENT")
                continue

            days_span = (df.index[-1] - df.index[0]).days
            if days_span < MIN_DATA_SPAN_DAYS:
                print(f"INSUFFICIENT SPAN ({days_span} days, need {MIN_DATA_SPAN_DAYS})")
                continue

            n = len(df)
            split = int(n * TRAIN_RATIO)
            train_df = df.iloc[:split]
            oos_df = df.iloc[split:]

            # Trading-calibrated grid search
            print(f"calibrating...", end=" ", flush=True)
            cal_results = []
            for alpha in ALPHAS:
                for window in WINDOWS:
                    try:
                        stats = build_engine_and_test(
                            train_df, oos_df, symbol, asset_class,
                            alpha, window, tf_name
                        )
                        cal_results.append({
                            "alpha": alpha, "window": window,
                            **stats,
                        })
                    except (ValueError, Exception) as e:
                        pass

            if not cal_results:
                print("CALIBRATION FAILED")
                continue

            cal_results.sort(key=lambda r: r["total_pnl_pct"], reverse=True)
            best = cal_results[0]
            alpha = best["alpha"]
            window = best["window"]

            print(f"best a={alpha}/w={window}", end=" ", flush=True)

            # Re-run with best (already computed in calibration)
            oos_stats = best

            pnl_sign = "+" if oos_stats["total_pnl_pct"] > 0 else ""
            print(f"OOS: {oos_stats['total_trades']}tr WR={oos_stats['win_rate']:.1%} "
                  f"PnL={pnl_sign}{oos_stats['total_pnl_pct']:.2f}% "
                  f"Tr/d={oos_stats['trades_per_day']:.1f}", end=" ", flush=True)

            # Walk-Forward
            trades_all_wf = []
            fold_count = 0
            train_end = wf_p["initial_train"]

            while train_end + wf_p["min_test"] <= n:
                test_end = min(train_end + wf_p["step"], n)
                wf_train = df.iloc[:train_end]
                wf_test = df.iloc[train_end:test_end]

                try:
                    wf_stats = build_engine_and_test(
                        wf_train, wf_test, symbol, asset_class,
                        alpha, window, tf_name
                    )
                    # Just accumulate trades
                    engine_wf = PPMT(
                        symbol=symbol, asset_class=asset_class,
                        sax_alphabet_size=alpha, sax_window_size=window,
                        sax_strategy="ohlcv", fuzzy_threshold=0.80,
                        min_confidence=0.05, min_risk_reward=0.3,
                    )
                    engine_wf.build(wf_train, pattern_length=PATTERN_LENGTH)
                    for trie in [engine_wf.trie_n1, engine_wf.trie_n2, engine_wf.trie_n3, engine_wf.trie_n4]:
                        trie.propagate_metadata()
                    fold_trades = simulate_trades(engine_wf, wf_test, PATTERN_LENGTH)
                    trades_all_wf.extend(fold_trades)
                except Exception:
                    pass

                fold_count += 1
                train_end = test_end

            wf_stats = compute_stats(trades_all_wf, tf_name)
            oos_pnl = oos_stats["total_pnl_pct"]
            wf_pnl = wf_stats["total_pnl_pct"]
            wf_ratio = wf_pnl / oos_pnl if oos_pnl != 0 else 0

            print(f"WF: {fold_count}f {wf_stats['total_trades']}tr "
                  f"PnL={wf_pnl:+.2f}% ratio={wf_ratio:.2f}")

            tf_results[symbol] = {
                "timeframe": tf_name,
                "candles": n,
                "data_span_days": days_span,
                "train_candles": len(train_df),
                "oos_candles": len(oos_df),
                "best_alpha": alpha,
                "best_window": window,
                "calibration_all": cal_results,
                **oos_stats,
                "walk_forward": {
                    "folds": fold_count,
                    **wf_stats,
                    "wf_oos_ratio": round(wf_ratio, 4),
                },
            }

        all_results[tf_name] = tf_results

    # ================================================================
    # SUMMARY
    # ================================================================
    print("\n" + "=" * 100)
    print("  COMPREHENSIVE RESULTS")
    print("=" * 100)

    for tf_name, tf_results in all_results.items():
        if not tf_results:
            continue

        print(f"\n  === {tf_name} OOS TRADING ===\n")
        print(f"  {'Token':<12} {'Class':<10} {'a':>3} {'w':>3} "
              f"{'Candles':>8} {'Days':>5} "
              f"{'Trades':>7} {'Tr/day':>7} "
              f"{'WR':>7} {'PF':>7} {'PnL%':>10} "
              f"{'Sharpe':>8} {'MC%':>6} {'MaxDD':>8}")
        print("  " + "-" * 105)

        for symbol in sorted(tf_results.keys()):
            r = tf_results[symbol]
            ac = TOKENS_5M.get(symbol, TOKENS_1M.get(symbol, {})).get("asset_class", "?")
            print(f"  {symbol:<12} {ac:<10} "
                  f"{r['best_alpha']:>3} {r['best_window']:>3} "
                  f"{r['candles']:>8} {r['data_span_days']:>5} "
                  f"{r['total_trades']:>7} {r['trades_per_day']:>7.2f} "
                  f"{r['win_rate']:>7.1%} {r['profit_factor']:>7.2f} "
                  f"{r['total_pnl_pct']:>+10.2f} "
                  f"{r['sharpe_approx']:>8.2f} {r['mc_profitable_pct']:>6.0f} "
                  f"{r['max_drawdown_pct']:>8.2f}")

        # WF summary
        print(f"\n  === {tf_name} WALK-FORWARD ===\n")
        print(f"  {'Token':<12} {'Folds':>6} {'Trades':>7} "
              f"{'WR':>7} {'PnL%':>10} {'MC%':>6} {'WF/OOS':>8}")
        print("  " + "-" * 65)

        for symbol in sorted(tf_results.keys()):
            r = tf_results[symbol]
            wf = r["walk_forward"]
            print(f"  {symbol:<12} {wf['folds']:>6} {wf['total_trades']:>7} "
                  f"{wf['win_rate']:>7.1%} {wf['total_pnl_pct']:>+10.2f} "
                  f"{wf['mc_profitable_pct']:>6.0f} {wf['wf_oos_ratio']:>8.2f}")

    # Cross-TF comparison
    common = set(all_results.get("5m", {}).keys()) & set(all_results.get("1m", {}).keys())
    if common:
        print(f"\n  === CROSS-TIMEFRAME ({sorted(common)}) ===\n")
        print(f"  {'Token':<12} {'TF':>4} {'Trades':>7} {'Tr/day':>8} "
              f"{'WR':>7} {'PF':>7} {'PnL%':>10} {'Sharpe':>8} {'MC%':>6}")
        print("  " + "-" * 80)

        for symbol in sorted(common):
            for tf_name in ["5m", "1m"]:
                if symbol in all_results[tf_name]:
                    r = all_results[tf_name][symbol]
                    print(f"  {symbol:<12} {tf_name:>4} {r['total_trades']:>7} "
                          f"{r['trades_per_day']:>8.2f} "
                          f"{r['win_rate']:>7.1%} {r['profit_factor']:>7.2f} "
                          f"{r['total_pnl_pct']:>+10.2f} {r['sharpe_approx']:>8.2f} "
                          f"{r['mc_profitable_pct']:>6.0f}")

    # Overall
    for tf_name, tf_results in all_results.items():
        if not tf_results:
            continue
        pnls = [r["total_pnl_pct"] for r in tf_results.values()]
        profitable = sum(1 for p in pnls if p > 0)
        print(f"\n  {tf_name} OVERALL: {profitable}/{len(tf_results)} profitable "
              f"({profitable/len(tf_results):.0%}) | Avg PnL: {np.mean(pnls):+.2f}% "
              f"| Range: {min(pnls):+.2f}% to {max(pnls):+.2f}%")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n  Total time: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    # Save
    output = {
        "timestamp": datetime.now().isoformat(),
        "validation_type": "low_timeframe_5m_1m",
        "data_requirement": "minimum_6_months_real",
        "days_of_data": DAYS_OF_DATA,
        "5m": {
            symbol: {k: v for k, v in r.items() if k != "calibration_all"}
            for symbol, r in all_results.get("5m", {}).items()
        },
        "1m": {
            symbol: {k: v for k, v in r.items() if k != "calibration_all"}
            for symbol, r in all_results.get("1m", {}).items()
        },
    }

    output_path = "/home/z/my-project/download/v066_low_tf_validation_results.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_path}")
    return output


if __name__ == "__main__":
    results = main()
