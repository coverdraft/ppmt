#!/usr/bin/env python3
"""
PPMT Alpha Diagnostic — Re-encode SAX with α=5 and α=7 to predict
whether higher alpha would produce nodes with sufficient observations
and higher confidence.

NO CODE CHANGES — pure analysis script.

The confidence formula (from metadata.py):
    adjusted_wr = (win_rate * count + 0.5 * 10) / (count + 10)
    count_bonus = min(1.0, sqrt(log(count+1) / log(1000)))
    confidence = adjusted_wr * count_bonus

This means confidence depends on:
  1. win_rate (Bayesian shrunk toward 0.5)
  2. historical_count (count_bonus via sqrt(log(N)/log(1000)))

With α=3, P=3 → 27 patterns. 864 symbols → ~32 obs/node avg.
But win_rate ≈ 0.50 → confidence ≈ 0.20. The shrinkage dominates.

With α=5, P=3 → 125 patterns. 864 symbols → ~6.9 obs/node avg.
FEWER obs per node, but if win_rate is higher (patterns more specific),
confidence could still be higher.

With α=7, P=3 → 343 patterns. 864 symbols → ~2.5 obs/node avg.
Very sparse — most nodes get < 5 obs, shrinkage kills confidence.

The question is: does the specificity gain from higher α outweigh
the sparsity loss? This script answers with REAL data.

Usage:
    python scripts/alpha_diagnostic.py
    python scripts/alpha_diagnostic.py --tokens BTC/USDT DOGE/USDT
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from typing import Optional

# Ensure ppmt is importable
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_dir = os.path.join(_repo_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import numpy as np
import pandas as pd

from ppmt.core.sax import SAXEncoder, SAX_BREAKPOINTS, SAX_ALPHABET
from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
logger = logging.getLogger("alpha_diag")
logger.setLevel(logging.INFO)

TIMEFRAME = "5m"
PATTERN_LENGTH = 3  # N3/N4 P=3
WINDOW_N3 = 10      # N3 W=10 for 5m
OOS_DAYS = 7


def compute_confidence(win_rate: float, count: int, prior_strength: float = 10.0) -> float:
    """
    Exact confidence formula from BlockLifecycleMetadata.confidence.
    
    adjusted_wr = (wr * count + 0.5 * 10) / (count + 10)
    count_bonus = min(1.0, sqrt(log(count+1) / log(1000)))
    confidence = adjusted_wr * count_bonus
    """
    if count == 0:
        return 0.0
    adjusted_wr = (win_rate * count + 0.5 * prior_strength) / (count + prior_strength)
    count_bonus = min(1.0, np.sqrt(np.log1p(count) / np.log(1000)))
    return adjusted_wr * count_bonus


def encode_and_count_patterns(
    df: pd.DataFrame,
    alpha: int,
    window: int,
    pattern_length: int,
    strategy: str = "ohlcv",
) -> dict:
    """
    Encode OHLCV with given alpha, extract P-length patterns, count.
    Returns dict with:
      - symbols: list of SAX symbols
      - pattern_counts: Counter of pattern → frequency
      - total_patterns_possible: alpha^pattern_length
    """
    encoder = SAXEncoder(alphabet_size=alpha, window_size=window, strategy=strategy)
    symbols = encoder.encode(df)

    # Extract patterns of length P
    pattern_counts: Counter = Counter()
    for i in range(len(symbols) - pattern_length + 1):
        pat = tuple(symbols[i:i + pattern_length])
        pattern_counts[pat] += 1

    total_possible = alpha ** pattern_length

    return {
        "symbols": symbols,
        "pattern_counts": pattern_counts,
        "total_patterns_possible": total_possible,
        "num_symbols": len(symbols),
        "num_unique_patterns": len(pattern_counts),
        "coverage": len(pattern_counts) / total_possible * 100,
    }


def estimate_pattern_win_rates(
    df: pd.DataFrame,
    symbols: list[str],
    pattern_length: int,
    window: int,
) -> dict[tuple, float]:
    """
    For each pattern, estimate the win_rate by looking at what happens
    AFTER the pattern completes.

    "Win" = price moves favorably (up for LONG patterns, down for SHORT).
    Since we don't know direction a priori, we measure:
    - What fraction of the time does the next P symbols show the SAME
      pattern continuing (directional consistency)?
    
    Actually, let's use a simpler metric that the trie actually uses:
    For each occurrence of a pattern, look at the price move from the
    end of the pattern window to N candles later. If the move is in the
    direction of the pattern's average tendency, it's a "win".

    We'll compute a simpler metric:
    - For each pattern occurrence, look at the close price at the END
      of the pattern vs close price P*window candles later.
    - If the pattern has a directional tendency (e.g., price tends to go
      UP after this pattern), count how often it does.
    
    Return: dict of pattern → estimated win_rate
    """
    # Price at each symbol boundary
    closes = df["close"].values
    # Each symbol represents `window` candles
    # Symbol i corresponds to candles [i*window, (i+1)*window)
    # The "entry" after seeing pattern ending at symbol i is at candle (i+1)*window
    
    pattern_outcomes: dict[tuple, list[float]] = {}
    lookahead = pattern_length  # Look P symbols ahead = P*window candles
    
    for i in range(len(symbols) - pattern_length - lookahead + 1):
        pat = tuple(symbols[i:i + pattern_length])
        # Entry candle index (end of pattern)
        entry_idx = (i + pattern_length) * window
        # Exit candle index (P symbols later)
        exit_idx = entry_idx + lookahead * window
        
        if exit_idx >= len(closes):
            break
        
        entry_price = closes[entry_idx]
        exit_price = closes[exit_idx]
        
        move_pct = (exit_price - entry_price) / entry_price * 100
        
        if pat not in pattern_outcomes:
            pattern_outcomes[pat] = []
        pattern_outcomes[pat].append(move_pct)
    
    # Compute win_rate per pattern
    # "Win" = move in the pattern's dominant direction
    # If average move is positive → LONG wins when move > 0
    # If average move is negative → SHORT wins when move < 0
    pattern_win_rates: dict[tuple, float] = {}
    for pat, moves in pattern_outcomes.items():
        if not moves:
            continue
        avg_move = np.mean(moves)
        if avg_move >= 0:
            # LONG bias: win if move > 0
            wins = sum(1 for m in moves if m > 0)
        else:
            # SHORT bias: win if move < 0
            wins = sum(1 for m in moves if m < 0)
        pattern_win_rates[pat] = wins / len(moves) if moves else 0.5
    
    return pattern_win_rates


def analyze_alpha(
    symbol: str,
    df: pd.DataFrame,
    alpha: int,
    is_df: pd.DataFrame,
) -> None:
    """Full analysis for one symbol + alpha combination."""
    
    # Encode IS data
    result = encode_and_count_patterns(is_df, alpha, WINDOW_N3, PATTERN_LENGTH)
    pattern_counts = result["pattern_counts"]
    n_symbols = result["num_symbols"]
    n_patterns = result["num_unique_patterns"]
    total_possible = result["total_patterns_possible"]
    coverage = result["coverage"]

    print(f"\n  α={alpha}: {n_symbols} SAX symbols → {n_patterns} unique patterns "
          f"(of {total_possible} possible = {coverage:.1f}% coverage)")

    # Observation distribution
    obs_values = list(pattern_counts.values())
    if not obs_values:
        print(f"    No patterns produced!")
        return

    mean_obs = np.mean(obs_values)
    median_obs = np.median(obs_values)

    # Bucket counts
    gt1 = sum(1 for v in obs_values if v >= 1)
    gt5 = sum(1 for v in obs_values if v >= 5)
    gt10 = sum(1 for v in obs_values if v >= 10)
    gt20 = sum(1 for v in obs_values if v >= 20)
    gt50 = sum(1 for v in obs_values if v >= 50)
    gt100 = sum(1 for v in obs_values if v >= 100)

    print(f"    Observations per node:")
    print(f"      Mean: {mean_obs:.1f} | Median: {median_obs:.1f} | "
          f"Min: {min(obs_values)} | Max: {max(obs_values)}")
    print(f"      Nodes with ≥ 1 obs:   {gt1:>4} ({gt1/n_patterns*100:.1f}% of {n_patterns} patterns)")
    print(f"      Nodes with ≥ 5 obs:   {gt5:>4} ({gt5/n_patterns*100:.1f}%)")
    print(f"      Nodes with ≥ 10 obs:  {gt10:>4} ({gt10/n_patterns*100:.1f}%)")
    print(f"      Nodes with ≥ 20 obs:  {gt20:>4} ({gt20/n_patterns*100:.1f}%)")
    print(f"      Nodes with ≥ 50 obs:  {gt50:>4} ({gt50/n_patterns*100:.1f}%)")
    print(f"      Nodes with ≥ 100 obs: {gt100:>4} ({gt100/n_patterns*100:.1f}%)")

    # Observation histogram
    print(f"    Distribution of obs/node:")
    buckets = [(1, 2), (2, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 9999)]
    for lo, hi in buckets:
        cnt = sum(1 for v in obs_values if lo <= v < hi)
        bar = "█" * max(1, int(cnt / max(n_patterns * 0.03, 1)))
        label = f"[{lo:>3}, {hi:>4})"
        print(f"      {label}: {cnt:>4} ({cnt/n_patterns*100:>5.1f}%) {bar}")

    # ─── Estimate confidence with real win_rates ─────────────
    print(f"\n    ─── Confidence estimation (real win-rates from data) ───")
    
    # Compute actual win_rates from price data
    win_rates = estimate_pattern_win_rates(is_df, result["symbols"], PATTERN_LENGTH, WINDOW_N3)
    
    if win_rates:
        wr_values = list(win_rates.values())
        avg_wr = np.mean(wr_values)
        print(f"    Average pattern win-rate: {avg_wr:.3f} (across {len(win_rates)} patterns)")
        print(f"    Win-rate range: [{min(wr_values):.3f} — {max(wr_values):.3f}]")
        
        # WR distribution
        wr_buckets = [(0.0, 0.4), (0.4, 0.5), (0.5, 0.55), (0.55, 0.6), (0.6, 0.7), (0.7, 1.01)]
        print(f"    Win-rate distribution:")
        for lo, hi in wr_buckets:
            cnt = sum(1 for w in wr_values if lo <= w < hi)
            print(f"      [{lo:.1f}, {hi:.1f}): {cnt:>4} ({cnt/len(win_rates)*100:.1f}%)")

        # Compute confidence for each pattern using real wr + obs count
        confidences = []
        for pat, count in pattern_counts.items():
            wr = win_rates.get(pat, 0.5)
            conf = compute_confidence(wr, count)
            confidences.append((pat, count, wr, conf))

        # Sort by confidence
        confidences.sort(key=lambda x: -x[3])
        
        # Top patterns
        print(f"\n    Top 10 patterns by confidence:")
        print(f"      {'Pattern':<15} {'Obs':>5} {'WR':>6} {'Conf':>7}")
        for pat, cnt, wr, conf in confidences[:10]:
            pat_str = "".join(pat)
            print(f"      {pat_str:<15} {cnt:>5} {wr:>5.3f} {conf:>7.4f}")

        # Confidence distribution
        all_confs = [c[3] for c in confidences]
        conf_buckets = [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4),
                       (0.4, 0.5), (0.5, 0.6), (0.6, 0.8), (0.8, 1.01)]
        print(f"\n    Confidence distribution (α={alpha}, ALL nodes):")
        for lo, hi in conf_buckets:
            cnt = sum(1 for c in all_confs if lo <= c < hi)
            bar = "█" * max(1, int(cnt / max(len(all_confs) * 0.03, 1)))
            print(f"      [{lo:.1f}, {hi:.1f}): {cnt:>4} ({cnt/len(all_confs)*100:>5.1f}%) {bar}")

        # ─── EV Gate simulation ────────────────────────────────
        # For nodes with real win-rate, compute expected EV
        # net_rr_capped comes from the metadata, which we don't have exactly.
        # But from the previous diagnostic, we know:
        # - Average net_rr_capped for DOGE = 2.76, BTC = 3.0, LINK = 3.0
        # So EV ≈ confidence × 2.5-3.0
        print(f"\n    ─── EV Gate pass rate estimate ───")
        print(f"    (Using net_rr_capped = 2.75 as observed for DOGE)")
        net_rr_est = 2.75  # from diagnostic results
        
        for ev_thresh in [0.40, 0.60, 0.80]:
            passing = sum(1 for _, _, _, conf in confidences if conf * net_rr_est >= ev_thresh)
            passing_obs = sum(cnt for pat, cnt, _, conf in confidences if conf * net_rr_est >= ev_thresh)
            print(f"      EV >= {ev_thresh:.2f}: {passing:>4} patterns "
                  f"({passing/len(confidences)*100:.1f}%), "
                  f"covering {passing_obs} of {n_symbols} obs ({passing_obs/n_symbols*100:.1f}%)")

        # Key metric: what confidence is needed for EV ≥ 0.80?
        req_conf = 0.80 / net_rr_est
        print(f"\n    Required confidence for EV ≥ 0.80: {req_conf:.3f}")
        nodes_above = sum(1 for _, _, _, conf in confidences if conf >= req_conf)
        print(f"    Nodes with conf ≥ {req_conf:.3f}: {nodes_above} ({nodes_above/len(confidences)*100:.1f}%)")
    
    else:
        # Fallback: estimate with theoretical win-rates
        print(f"    (Using assumed win-rates for estimation)")
        for assumed_wr in [0.50, 0.55, 0.60, 0.65]:
            print(f"\n    Assuming win_rate = {assumed_wr:.2f}:")
            for obs_count in [5, 10, 20, 50, 100]:
                conf = compute_confidence(assumed_wr, obs_count)
                ev = conf * 2.75
                pass_mark = "✓ PASSES" if ev >= 0.80 else ""
                print(f"      {obs_count:>3} obs → conf={conf:.4f} → EV={ev:.3f} {pass_mark}")


def main():
    parser = argparse.ArgumentParser(description="PPMT Alpha Diagnostic")
    parser.add_argument("--tokens", nargs="+", default=["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"])
    args = parser.parse_args()

    storage = PPMTStorage()
    classifier = AssetClassifier()

    for symbol in args.tokens:
        info = classifier.classify(symbol)
        print(f"\n{'═'*90}")
        print(f"  {symbol} ({info.asset_class}, {info.weight_profile}) — 5m")
        print(f"{'═'*90}")

        # Load data
        df = storage.load_ohlcv(symbol, TIMEFRAME)
        if df is None or len(df) < 200:
            print(f"  SKIP: insufficient data ({len(df) if df is not None else 0} candles)")
            continue

        print(f"  Total candles loaded: {len(df):,}")

        # Split IS/OOS
        oos_start = df.index[-1] - pd.Timedelta(days=OOS_DAYS)
        is_df = df[df.index < oos_start]
        oos_df = df[df.index >= oos_start]

        print(f"  IS: {len(is_df):,} candles | OOS: {len(oos_df):,} candles")
        
        # How many SAX symbols does IS produce at W=10?
        n_symbols_is = len(is_df) // WINDOW_N3
        print(f"  IS SAX symbols (W={WINDOW_N3}): ~{n_symbols_is}")

        # ─── Analyze each alpha ────────────────────────────────
        for alpha in [3, 5, 7]:
            # Check if alpha is supported
            if alpha not in SAX_BREAKPOINTS:
                print(f"\n  α={alpha}: NOT SUPPORTED (breakpoints not defined)")
                continue

            analyze_alpha(symbol, df, alpha, is_df)

    # ─── Cross-alpha comparison table ───────────────────────────
    print(f"\n\n{'═'*90}")
    print(f"  CROSS-ALPHA COMPARISON TABLE")
    print(f"{'═'*90}")
    print(f"  (All values for N3 level, P=3, W=10, 5m timeframe, IS data only)")
    print(f"\n  {'Token':<12} {'α':>3} {'Symbs':>6} {'Pats':>6} {'MaxPat':>6} {'Cov%':>6} "
          f"{'≥10obs':>7} {'≥20obs':>7} {'≥50obs':>7} {'AvgConf':>8} {'EV≥0.80':>8}")
    print(f"  {'─'*12} {'─'*3} {'─'*6} {'─'*6} {'─'*6} {'─'*6} {'─'*7} {'─'*7} {'─'*7} {'─'*8} {'─'*8}")

    for symbol in args.tokens:
        info = classifier.classify(symbol)
        df = storage.load_ohlcv(symbol, TIMEFRAME)
        if df is None or len(df) < 200:
            continue

        oos_start = df.index[-1] - pd.Timedelta(days=OOS_DAYS)
        is_df = df[df.index < oos_start]

        for alpha in [3, 5, 7]:
            if alpha not in SAX_BREAKPOINTS:
                continue

            result = encode_and_count_patterns(is_df, alpha, WINDOW_N3, PATTERN_LENGTH)
            pc = result["pattern_counts"]
            obs_values = list(pc.values())
            n_patterns = result["num_unique_patterns"]
            total_possible = result["total_patterns_possible"]
            coverage = result["coverage"]

            gt10 = sum(1 for v in obs_values if v >= 10)
            gt20 = sum(1 for v in obs_values if v >= 20)
            gt50 = sum(1 for v in obs_values if v >= 50)

            # Compute real win-rates and confidence
            win_rates = estimate_pattern_win_rates(is_df, result["symbols"], PATTERN_LENGTH, WINDOW_N3)
            confidences = []
            for pat, count in pc.items():
                wr = win_rates.get(pat, 0.5)
                conf = compute_confidence(wr, count)
                confidences.append(conf)
            
            avg_conf = np.mean(confidences) if confidences else 0.0
            ev_pass = sum(1 for c in confidences if c * 2.75 >= 0.80)

            print(f"  {symbol:<12} {alpha:>3} {result['num_symbols']:>6} {n_patterns:>6} "
                  f"{total_possible:>6} {coverage:>5.1f}% "
                  f"{gt10:>7} {gt20:>7} {gt50:>7} {avg_conf:>8.4f} {ev_pass:>8}")

        print(f"  {'─'*12} {'─'*3} {'─'*6} {'─'*6} {'─'*6} {'─'*6} {'─'*7} {'─'*7} {'─'*7} {'─'*8} {'─'*8}")

    # ─── Theoretical vs Actual ──────────────────────────────────
    print(f"\n\n{'═'*90}")
    print(f"  THEORETICAL ANALYSIS — What the numbers mean")
    print(f"{'═'*90}")

    # Average IS data across tokens
    print(f"\n  With ~8,640 IS candles and W=10, we get ~864 SAX symbols.")
    print(f"  With P=3, each consecutive triple of symbols is a pattern.")
    print(f"  Total pattern occurrences = 864 - 3 + 1 = ~862")
    print(f"")
    print(f"  Pattern space vs. density:")
    print(f"  {'α':>3} {'α^P':>6} {'Avg obs/node':>14} {'Obs for WR=0.55':>18} {'Conf at 20 obs':>16}")
    print(f"  {'─'*3} {'─'*6} {'─'*14} {'─'*18} {'─'*16}")
    
    for alpha in [3, 5, 7]:
        total_patterns = alpha ** 3
        avg_obs = 862 / total_patterns
        # What win_rate is needed for confidence > 0.27 (to match α=3)?
        # conf = (wr * 20 + 5) / 30 * sqrt(log(21)/log(1000))
        # conf = (wr * 20 + 5) / 30 * 0.438
        # For conf = 0.27: wr = (0.27/0.438 * 30 - 5) / 20 = 0.74
        conf_20 = compute_confidence(0.55, 20)
        print(f"  {alpha:>3} {total_patterns:>6} {avg_obs:>14.1f} {'862 obs ÷ ' + str(total_patterns) + ' nodes':>18} {conf_20:>16.4f}")

    print(f"\n  Key insight: α=3 spreads 862 observations across 27 nodes = 32 obs/node average.")
    print(f"  α=5 spreads 862 observations across 125 nodes = 6.9 obs/node average.")
    print(f"  α=7 spreads 862 observations across 343 nodes = 2.5 obs/node average.")
    print(f"")
    print(f"  The count_bonus formula penalizes low counts HEAVILY:")
    for n in [3, 5, 10, 20, 50, 100]:
        cb = min(1.0, np.sqrt(np.log1p(n) / np.log(1000)))
        print(f"    {n:>3} obs → count_bonus = {cb:.4f}")

    print(f"\n  With α=5 and ~7 obs/node, count_bonus ≈ {compute_confidence(1.0, 7) / ((7 + 5) / (7 + 10)):.4f}")
    print(f"  This means even with win_rate = 0.60, confidence ≈ {compute_confidence(0.60, 7):.4f}")
    print(f"  And EV = {compute_confidence(0.60, 7) * 2.75:.3f} (needs ≥ 0.80)")
    print(f"")
    print(f"  CRITICAL: α=5 needs nodes with >20 obs for confidence > 0.27.")
    print(f"  But with only 7 obs/node on average, most nodes will have <10 obs.")
    print(f"  Only the most common patterns will have >20 obs.")
    print(f"  → α=5 works ONLY IF those common patterns have higher win_rates.")
    print(f"")
    print(f"  The diagnostic below shows whether this is actually the case.")


if __name__ == "__main__":
    main()
