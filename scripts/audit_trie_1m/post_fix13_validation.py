"""
Post-FIX-13 validation: run PPMT engine with the NEW config (α=4) on real 1m
data and confirm the engine produces more matches and higher confidence
than with the OLD config (α=5).

For each token, builds trie on first 35k candles, tests on last 15k candles.
Measures: matches, signals generated (with confidence >= 0.15), avg confidence.

Compares α=4 (new) vs α=5 (old) head-to-head on REAL data.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
from ppmt.core.matcher import FuzzyMatcher
from ppmt.core.regime import RegimeDetector

DATA = Path("/home/z/my-project/download/real_data_1m")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
           "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]
PL = 5
WINDOW = 7
MIN_CONF = 0.15
MIN_SIM = 0.70
TRAIN_CANDLES = 35_000
TEST_CANDLES = 15_000


def build_trie(df: pd.DataFrame, alpha: int) -> tuple[PPMTTrie, RegimePartitionedTrie]:
    sax = SAXEncoder(alphabet_size=alpha, window_size=WINDOW)
    rd = RegimeDetector()
    syms = sax.encode(df)
    trie_n3 = PPMTTrie(name="n3")
    trie_n4 = RegimePartitionedTrie(name="n4")
    for i in range(len(syms) - PL):
        pattern = syms[i:i+PL]
        next_sym = syms[i+PL] if i+PL < len(syms) else None
        start = i * WINDOW
        end = (i + PL) * WINDOW
        if end > len(df):
            break
        wdf = df.iloc[start:end]
        ep = wdf["close"].iloc[0]
        xp = wdf["close"].iloc[-1]
        move = ((xp - ep) / ep) * 100.0
        dd = ((wdf["low"].min() - ep) / ep) * 100.0
        fav = ((wdf["high"].max() - ep) / ep) * 100.0
        won = move > 0
        regime = rd.detect_simple(wdf)
        trie_n3.insert_with_observations(
            symbols=pattern, move_pct=move, drawdown_pct=dd,
            favorable_pct=fav, duration=len(wdf), won=won,
            next_symbol=next_sym, regime=regime,
        )
        trie_n4.insert_with_observations(
            symbols=pattern, move_pct=move, drawdown_pct=dd,
            favorable_pct=fav, duration=len(wdf), won=won,
            next_symbol=next_sym, regime=regime,
        )
    return trie_n3, trie_n4


def test_match_count(df: pd.DataFrame, trie: PPMTTrie, alpha: int) -> dict:
    """
    Walk through test candles, query trie with each pattern.
    Returns: total queries, exact matches, matches with conf>=0.15, avg conf.
    """
    sax = SAXEncoder(alphabet_size=alpha, window_size=WINDOW)
    rd = RegimeDetector()
    syms = sax.encode(df)
    matcher = FuzzyMatcher(sax_encoder=sax, threshold=0.85,
                           min_similarity=MIN_SIM, min_confidence=MIN_CONF)

    total_queries = 0
    exact_matches = 0
    fuzzy_matches = 0
    confs_above_gate = 0
    confs_list = []
    directions = []  # +1 for LONG bias, -1 for SHORT bias

    # We step 1 candle at a time on the test set
    # Each "query" = a window of PL SAX symbols built from the rolling window
    # Convert to SAX symbols first
    for i in range(0, len(syms) - PL, 1):
        pattern = syms[i:i+PL]
        if len(pattern) < PL:
            continue
        total_queries += 1

        # Try exact match first
        node = trie.search(pattern)
        if node is not None and node.metadata.historical_count > 0:
            exact_matches += 1
            conf = float(node.metadata.confidence)
            confs_list.append(conf)
            if conf >= MIN_CONF:
                confs_above_gate += 1
                # Direction: positive expected_move_pct = LONG bias
                em = node.metadata.expected_move_pct
                directions.append(1 if em > 0 else -1)
            continue

        # Try fuzzy match (prefix + 1-edit)
        result = matcher.best_match(trie, pattern)
        if result is not None and result.node is not None and result.node.metadata.historical_count > 0:
            fuzzy_matches += 1
            conf = float(result.node.metadata.confidence)
            confs_list.append(conf)
            if conf >= MIN_CONF:
                confs_above_gate += 1
                em = result.node.metadata.expected_move_pct
                directions.append(1 if em > 0 else -1)

    return {
        "total_queries": total_queries,
        "exact_matches": exact_matches,
        "fuzzy_matches": fuzzy_matches,
        "total_matches": exact_matches + fuzzy_matches,
        "match_rate_pct": round((exact_matches + fuzzy_matches) / max(1, total_queries) * 100, 2),
        "signals_above_gate": confs_above_gate,
        "avg_confidence": round(float(np.mean(confs_list)) if confs_list else 0.0, 4),
        "max_confidence": round(float(np.max(confs_list)) if confs_list else 0.0, 4),
        "long_signals": sum(1 for d in directions if d > 0),
        "short_signals": sum(1 for d in directions if d < 0),
    }


def main():
    print(f"Post-FIX-13 validation: α=4 (new) vs α=5 (old) on REAL 1m data")
    print(f"  Train: first {TRAIN_CANDLES:,} candles | Test: last {TEST_CANDLES:,} candles")
    print(f"  Symbols: {len(SYMBOLS)}")
    print(f"  Gates: min_sim={MIN_SIM}, min_conf={MIN_CONF}")
    print()

    results = {}
    for sym in SYMBOLS:
        df = pd.read_csv(DATA / f"{sym}_1m.csv")
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)

        train_df = df.head(TRAIN_CANDLES).copy()
        test_df = df.tail(TEST_CANDLES).copy()

        results[sym] = {}
        for alpha in [4, 5]:
            trie_n3, _ = build_trie(train_df, alpha=alpha)
            r = test_match_count(test_df, trie_n3, alpha=alpha)
            results[sym][f"alpha_{alpha}"] = r
        print(f"  {sym}: α=4 matches={results[sym]['alpha_4']['total_matches']:>5} "
              f"signals={results[sym]['alpha_4']['signals_above_gate']:>5} "
              f"avg_conf={results[sym]['alpha_4']['avg_confidence']:.3f} | "
              f"α=5 matches={results[sym]['alpha_5']['total_matches']:>5} "
              f"signals={results[sym]['alpha_5']['signals_above_gate']:>5} "
              f"avg_conf={results[sym]['alpha_5']['avg_confidence']:.3f}")

    # Aggregate
    print()
    print("=" * 110)
    print(f"{'Config':<12} {'AvgMatches':>12} {'AvgSignals':>12} {'AvgConf':>10} "
          f"{'AvgLong':>10} {'AvgShort':>10} {'Long/Short':>12}")
    print("-" * 110)
    for alpha in [4, 5]:
        m = np.mean([results[s][f"alpha_{alpha}"]["total_matches"] for s in SYMBOLS])
        sg = np.mean([results[s][f"alpha_{alpha}"]["signals_above_gate"] for s in SYMBOLS])
        c = np.mean([results[s][f"alpha_{alpha}"]["avg_confidence"] for s in SYMBOLS])
        lg = np.mean([results[s][f"alpha_{alpha}"]["long_signals"] for s in SYMBOLS])
        sh = np.mean([results[s][f"alpha_{alpha}"]["short_signals"] for s in SYMBOLS])
        ratio = "∞" if sh == 0 else f"{lg/sh:.2f}"
        print(f"α={alpha} (new) " if alpha == 4 else f"α={alpha} (old) ",
              f"{m:>12.0f} {sg:>12.0f} {c:>10.4f} {lg:>10.0f} {sh:>10.0f} {ratio:>12}")

    print()
    print("Interpretation:")
    print("  - α=4 should produce MORE signals (higher match rate due to more reps per pattern)")
    print("  - α=4 should have HIGHER avg_confidence (passes 0.15 gate more often)")
    print("  - Long/Short ratio should be balanced (~1.0) — if not, there's a directional bias")

    # Save JSON
    out = Path("/home/z/my-project/download/trie_stats_1m/post_fix13_validation.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    import json
    main()
