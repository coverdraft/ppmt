"""
Measure pattern statistics on REAL 1m data for 8 tokens.

For each token, builds the trie at 4 scales (5k, 10k, 20k, 50k candles) and reports:
  - Total unique patterns
  - Distribution of historical_count (count=1, 2, 5, 10+)
  - Mean, median, percentiles of historical_count
  - Mean and max confidence
  - Estimated signals generated (count >= 1 + conf >= 0.15)

Uses the EXACT same engine config as production (SAX α=5, W=7, pattern_length=5 for 1m).

Usage:
    python /home/z/my-project/scripts/measure_trie_stats_1m.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Ensure ppmt is importable
sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
from ppmt.core.metadata import BlockLifecycleMetadata  # noqa: F401
from ppmt.core.regime import RegimeDetector

# --- Config (mirrors production for 1m) ---
TF = "1m"
ALPHA = 5        # TIMEFRAME_ALPHA_DEFAULTS["1m"]["sax_alphabet_size"]
WINDOW = 7       # TIMEFRAME_ALPHA_DEFAULTS["1m"]["sax_window_size"]
PATTERN_LEN = 5  # default in PPMT.build()

# Scales to measure (in candles)
SCALES = [5_000, 10_000, 20_000, 50_000]

# Tokens to measure
DATA_DIR = Path("/home/z/my-project/download/real_data_1m")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
           "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]

# Thresholds (mirror production matcher + signal gates)
MIN_CONFIDENCE = 0.15
MIN_SIMILARITY = 0.70

OUT_DIR = Path("/home/z/my-project/download/trie_stats_1m")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_df(symbol: str, n_candles: int) -> pd.DataFrame:
    """Load last n_candles candles from CSV (chronological order)."""
    csv_path = DATA_DIR / f"{symbol}_1m.csv"
    df = pd.read_csv(csv_path)
    # CSV is chronological ascending already (we sorted on download)
    df = df.tail(n_candles).reset_index(drop=True)
    # Sanity: ensure float dtypes
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return df


def build_trie(df: pd.DataFrame) -> tuple[PPMTTrie, RegimePartitionedTrie, int, RegimeDetector]:
    """
    Build N3 (per-asset) and N4 (per-asset+regime) tries from df.
    Mirrors PPMT.build() logic at lines 288-422 of engine/ppmt.py.

    Returns (trie_n3, trie_n4, n_patterns_inserted, regime_detector)
    """
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    regime_detector = RegimeDetector()
    symbols = sax.encode(df)

    trie_n3 = PPMTTrie(name=f"per_asset:{df.attrs.get('symbol', 'unknown')}")
    trie_n4 = RegimePartitionedTrie(name=f"per_asset_regime:{df.attrs.get('symbol', 'unknown')}")

    count = 0
    for i in range(len(symbols) - PATTERN_LEN):
        pattern = symbols[i:i + PATTERN_LEN]
        next_sym = symbols[i + PATTERN_LEN] if i + PATTERN_LEN < len(symbols) else None

        start_candle = i * WINDOW
        end_candle = (i + PATTERN_LEN) * WINDOW
        if end_candle > len(df):
            break

        window_df = df.iloc[start_candle:end_candle]
        entry_price = window_df["close"].iloc[0]
        exit_price = window_df["close"].iloc[-1]
        move_pct = ((exit_price - entry_price) / entry_price) * 100.0

        high = window_df["high"].max()
        low = window_df["low"].min()
        drawdown_pct = ((low - entry_price) / entry_price) * 100.0
        favorable_pct = ((high - entry_price) / entry_price) * 100.0

        duration = len(window_df)
        won = move_pct > 0

        regime = regime_detector.detect_simple(window_df)

        trie_n3.insert_with_observations(
            symbols=pattern,
            move_pct=move_pct,
            drawdown_pct=drawdown_pct,
            favorable_pct=favorable_pct,
            duration=duration,
            won=won,
            next_symbol=next_sym,
            regime=regime,
        )
        trie_n4.insert_with_observations(
            symbols=pattern,
            move_pct=move_pct,
            drawdown_pct=drawdown_pct,
            favorable_pct=favorable_pct,
            duration=duration,
            won=won,
            next_symbol=next_sym,
            regime=regime,
        )
        count += 1

    return trie_n3, trie_n4, count, regime_detector


def collect_node_stats(trie: PPMTTrie) -> dict[str, Any]:
    """
    Walk the trie DFS and collect metadata from all terminal nodes
    (nodes at depth == pattern_length, i.e. leaves representing full patterns).
    """
    counts = []
    confidences = []
    win_rates = []

    # DFS from root. Track depth.
    pattern_length = PATTERN_LEN
    stack = [(trie.root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth == pattern_length:
            # This is a terminal node (full pattern)
            meta = node.metadata
            if meta.historical_count > 0:
                counts.append(meta.historical_count)
                confidences.append(float(meta.confidence))
                win_rates.append(meta.win_rate)
            continue
        for child in node.children.values():
            stack.append((child, depth + 1))

    if not counts:
        return {
            "n_unique_patterns": 0,
            "count_mean": 0, "count_median": 0,
            "count_p10": 0, "count_p25": 0, "count_p75": 0, "count_p90": 0, "count_p99": 0,
            "count_max": 0,
            "n_count_eq_1": 0, "n_count_eq_2": 0, "n_count_3_4": 0,
            "n_count_5_9": 0, "n_count_10_plus": 0,
            "pct_count_eq_1": 0, "pct_count_eq_2": 0, "pct_count_3_4": 0,
            "pct_count_5_9": 0, "pct_count_10_plus": 0,
            "confidence_mean": 0, "confidence_median": 0, "confidence_max": 0,
            "win_rate_mean": 0,
            "signals_generated": 0,  # nodes with count>=1 AND conf>=0.15
        }

    counts_arr = np.array(counts)
    confs_arr = np.array(confidences)
    wr_arr = np.array(win_rates)

    n = len(counts)
    n_eq_1 = int((counts_arr == 1).sum())
    n_eq_2 = int((counts_arr == 2).sum())
    n_3_4 = int(((counts_arr >= 3) & (counts_arr <= 4)).sum())
    n_5_9 = int(((counts_arr >= 5) & (counts_arr <= 9)).sum())
    n_10p = int((counts_arr >= 10).sum())

    # Signals generated: nodes that pass the production gates
    # (count >= 1 AND confidence >= 0.15)
    signals_mask = (counts_arr >= 1) & (confs_arr >= MIN_CONFIDENCE)
    n_signals = int(signals_mask.sum())

    return {
        "n_unique_patterns": n,
        "count_mean": float(counts_arr.mean()),
        "count_median": float(np.median(counts_arr)),
        "count_p10": float(np.percentile(counts_arr, 10)),
        "count_p25": float(np.percentile(counts_arr, 25)),
        "count_p75": float(np.percentile(counts_arr, 75)),
        "count_p90": float(np.percentile(counts_arr, 90)),
        "count_p99": float(np.percentile(counts_arr, 99)),
        "count_max": int(counts_arr.max()),
        "n_count_eq_1": n_eq_1, "n_count_eq_2": n_eq_2,
        "n_count_3_4": n_3_4, "n_count_5_9": n_5_9, "n_count_10_plus": n_10p,
        "pct_count_eq_1": round(n_eq_1 / n * 100, 1),
        "pct_count_eq_2": round(n_eq_2 / n * 100, 1),
        "pct_count_3_4": round(n_3_4 / n * 100, 1),
        "pct_count_5_9": round(n_5_9 / n * 100, 1),
        "pct_count_10_plus": round(n_10p / n * 100, 1),
        "confidence_mean": float(confs_arr.mean()),
        "confidence_median": float(np.median(confs_arr)),
        "confidence_max": float(confs_arr.max()),
        "win_rate_mean": float(wr_arr.mean()),
        "signals_generated": n_signals,
    }


def collect_regime_partitioned_stats(trie: RegimePartitionedTrie) -> dict[str, Any]:
    """Aggregate stats across all regime sub-tries of an N4 RegimePartitionedTrie."""
    all_stats = {}
    aggregated_counts = []
    aggregated_confs = []
    aggregated_wrs = []
    signals_total = 0
    patterns_total = 0
    per_regime_summary = {}

    # RegimePartitionedTrie stores sub-tries keyed by regime name
    sub_tries = getattr(trie, "sub_tries", None) or getattr(trie, "_sub_tries", None) or {}
    if not sub_tries:
        # Try the public attribute name used in source
        # If still empty, return zeros
        return {"n_regimes": 0, "per_regime": {}, "aggregated": None}

    for regime_name, sub_trie in sub_tries.items():
        if sub_trie is None:
            continue
        stats = collect_node_stats(sub_trie)
        per_regime_summary[regime_name] = {
            "n_unique_patterns": stats["n_unique_patterns"],
            "count_mean": round(stats["count_mean"], 2),
            "count_median": stats["count_median"],
            "count_max": stats["count_max"],
            "signals_generated": stats["signals_generated"],
        }
        # Aggregate raw counts/confs across regimes
        # We have to re-walk to gather them
        stack = [(sub_trie.root, 0)]
        while stack:
            node, depth = stack.pop()
            if depth == PATTERN_LEN:
                meta = node.metadata
                if meta.historical_count > 0:
                    aggregated_counts.append(meta.historical_count)
                    aggregated_confs.append(float(meta.confidence))
                    aggregated_wrs.append(meta.win_rate)
                continue
            for child in node.children.values():
                stack.append((child, depth + 1))

        patterns_total += stats["n_unique_patterns"]
        signals_total += stats["signals_generated"]

    if aggregated_counts:
        c_arr = np.array(aggregated_counts)
        cf_arr = np.array(aggregated_confs)
        signals_mask = (c_arr >= 1) & (cf_arr >= MIN_CONFIDENCE)
        aggregated = {
            "n_unique_patterns_total": int(len(c_arr)),
            "count_mean": float(c_arr.mean()),
            "count_median": float(np.median(c_arr)),
            "count_p10": float(np.percentile(c_arr, 10)),
            "count_p90": float(np.percentile(c_arr, 90)),
            "count_p99": float(np.percentile(c_arr, 99)),
            "count_max": int(c_arr.max()),
            "n_count_eq_1": int((c_arr == 1).sum()),
            "n_count_eq_2": int((c_arr == 2).sum()),
            "n_count_3_4": int(((c_arr >= 3) & (c_arr <= 4)).sum()),
            "n_count_5_9": int(((c_arr >= 5) & (c_arr <= 9)).sum()),
            "n_count_10_plus": int((c_arr >= 10).sum()),
            "confidence_mean": float(cf_arr.mean()),
            "confidence_max": float(cf_arr.max()),
            "signals_generated": int(signals_mask.sum()),
        }
    else:
        aggregated = None

    return {
        "n_regimes": len(sub_tries),
        "per_regime": per_regime_summary,
        "aggregated": aggregated,
    }


def measure_symbol(symbol: str) -> dict[str, Any]:
    """Measure trie stats for one symbol across all scales."""
    print(f"\n{'='*70}\n  {symbol}\n{'='*70}")
    out = {"symbol": symbol, "scales": {}}

    for n_candles in SCALES:
        try:
            df = load_df(symbol, n_candles)
            df.attrs["symbol"] = symbol
            if len(df) < n_candles:
                print(f"  [{symbol} @ {n_candles}] only {len(df)} candles available, skipping")
                continue
            print(f"  [{symbol}] Building trie for {n_candles} candles ({len(df)} loaded)...")
            trie_n3, trie_n4, n_ins, _ = build_trie(df)
            n3_stats = collect_node_stats(trie_n3)
            n4_stats = collect_regime_partitioned_stats(trie_n4)
            out["scales"][str(n_candles)] = {
                "n_candles_requested": n_candles,
                "n_candles_loaded": len(df),
                "n_patterns_inserted": n_ins,
                "n3_per_asset": n3_stats,
                "n4_per_asset_regime": n4_stats,
            }
            print(f"    -> N3: {n3_stats['n_unique_patterns']:>6} unique patterns | "
                  f"mean_count={n3_stats['count_mean']:.2f} "
                  f"median={n3_stats['count_median']:.1f} "
                  f"max={n3_stats['count_max']} | "
                  f"conf_mean={n3_stats['confidence_mean']:.3f} "
                  f"max={n3_stats['confidence_max']:.3f} | "
                  f"signals={n3_stats['signals_generated']}")
            print(f"       count dist: 1={n3_stats['pct_count_eq_1']}% "
                  f"2={n3_stats['pct_count_eq_2']}% "
                  f"3-4={n3_stats['pct_count_3_4']}% "
                  f"5-9={n3_stats['pct_count_5_9']}% "
                  f"10+={n3_stats['pct_count_10_plus']}%")
            if n4_stats["aggregated"]:
                agg = n4_stats["aggregated"]
                print(f"    -> N4: {agg['n_unique_patterns_total']:>6} unique patterns | "
                      f"mean_count={agg['count_mean']:.2f} "
                      f"max={agg['count_max']} | "
                      f"conf_mean={agg['confidence_mean']:.3f} | "
                      f"signals={agg['signals_generated']} "
                      f"({n4_stats['n_regimes']} regimes)")
        except Exception as e:
            import traceback
            print(f"  [{symbol} @ {n_candles}] ERROR: {e}")
            traceback.print_exc()
            out["scales"][str(n_candles)] = {"error": str(e)}

    return out


def main():
    print(f"PPMT Trie Statistics — TF=1m, SAX α={ALPHA} W={WINDOW} "
          f"pattern_length={PATTERN_LEN}")
    print(f"Scales: {SCALES}")
    print(f"Symbols: {SYMBOLS}")
    print(f"Thresholds: min_sim={MIN_SIMILARITY}, min_conf={MIN_CONFIDENCE}")

    all_results = {}
    for sym in SYMBOLS:
        all_results[sym] = measure_symbol(sym)

    out_path = OUT_DIR / "trie_stats_1m_real.json"
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n\nResults saved to {out_path}")

    # Print compact summary table for the 50k scale
    print("\n\n" + "="*100)
    print("COMPACT SUMMARY @ 50,000 candles (1m)")
    print("="*100)
    header = (f"{'Symbol':<10} {'Patterns':>10} {'MeanCount':>10} "
              f"{'MedCount':>10} {'MaxCount':>10} "
              f"{'MeanConf':>10} {'MaxConf':>10} {'Signals':>10}")
    print(header)
    print("-"*100)
    for sym in SYMBOLS:
        s = all_results[sym]["scales"].get("50000", {})
        if "n3_per_asset" not in s:
            continue
        n3 = s["n3_per_asset"]
        print(f"{sym:<10} {n3['n_unique_patterns']:>10} "
              f"{n3['count_mean']:>10.2f} {n3['count_median']:>10.1f} "
              f"{n3['count_max']:>10} "
              f"{n3['confidence_mean']:>10.3f} {n3['confidence_max']:>10.3f} "
              f"{n3['signals_generated']:>10}")


if __name__ == "__main__":
    main()
