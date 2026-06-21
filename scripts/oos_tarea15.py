#!/usr/bin/env python3
"""OOS Validation: DOGE/USDT 1m — Post directional outcome fix.

v0.54.0 (TAREA 15): Validates the new compute_outcome_directional()
build by running OOS on the latest 500 candles.
"""
import sys, os, time, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pandas as pd
from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier

classifier = AssetClassifier()


def run_oos(symbol: str, timeframe: str):
    """Run OOS validation for a symbol/timeframe combination."""
    api_symbol = symbol.replace("/", "")
    info = classifier.classify(symbol)

    # Download 500 recent candles
    print(f"\nDownloading {symbol} {timeframe} candles from Binance...", flush=True)
    resp = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": api_symbol, "interval": timeframe, "limit": 500},
        timeout=30,
    )
    data = resp.json()
    df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume",
                                       "ct", "qv", "n", "tbb", "tbq", "ignore"])
    df = df[["ts", "open", "high", "low", "close", "volume"]].astype(float)
    df["timestamp"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.drop(columns=["ts"]).set_index("timestamp")
    df = df.sort_index()
    print(f"Downloaded {len(df)} {symbol} candles", flush=True)

    # Load tries from storage with timeframe
    storage = PPMTStorage()
    all_tries = storage.load_all_tries(symbol, asset_class=info.asset_class, timeframe=timeframe)
    n1 = all_tries.get("n1")
    n2 = all_tries.get("n2")
    n3 = all_tries.get("n3")
    n4 = all_tries.get("n4")

    print(f"\nN1 loaded: {n1.pattern_count if n1 else 0} patterns")
    print(f"N2 loaded: {n2.pattern_count if n2 else 0} patterns")
    print(f"N3 loaded: {n3.pattern_count if n3 else 0} patterns")
    n4c = n4.pattern_count if hasattr(n4, 'pattern_count') else 0
    print(f"N4 loaded: {n4c} patterns")

    # Create engine and inject tries
    engine = PPMT(
        symbol=symbol,
        asset_class=info.asset_class,
        weight_profile=info.weight_profile,
        dual_sax=True,
        min_confidence=0.08,
        timeframe=timeframe,
    )

    if n1 or n2 or n3:
        from ppmt.core.trie import PPMTTrie
        _n1 = n1 if n1 is not None else PPMTTrie(name="universal_empty")
        _n2 = n2 if n2 is not None else PPMTTrie(name="class_empty")
        engine.set_tries(
            trie_n1=_n1,
            trie_n2=_n2,
            trie_n3=n3 or PPMTTrie(name="n3_empty"),
            trie_n4=n4 if n4 is not None else engine.trie_n4,
        )

    # Print engine weight profile
    print(f"\nEngine weight profile: {engine.weights.profile}")
    print(f"  N1 weight: {engine.weights.n1_universal:.2%}")
    print(f"  N2 weight: {engine.weights.n2_asset_class:.2%}")
    print(f"  N3 weight: {engine.weights.n3_per_asset:.2%}")
    print(f"  N4 weight: {engine.weights.n4_per_asset_regime:.2%}")

    # Run match_raw
    recent_df = df
    print(f"\nRunning match_raw on last {len(recent_df)} candles...", flush=True)

    result = engine.match_raw(
        current_symbols=[],
        current_price=float(df["close"].iloc[-1]),
        recent_candles=recent_df,
    )

    print(f"\n{'='*60}")
    print(f"{symbol} {timeframe} — OOS VALIDATION RESULT (v0.54.0)")
    print(f"{'='*60}")
    print(f"  N1 confidence:    {result.n1_confidence:.4f}")
    print(f"  N2 confidence:    {result.n2_confidence:.4f}")
    print(f"  N3 confidence:    {result.n3_confidence:.4f}")
    print(f"  N4 confidence:    {result.n4_confidence:.4f}")
    print(f"  Weighted conf:    {result.weighted_confidence:.4f}")

    # Per-level node metadata
    print(f"\n  PER-LEVEL METADATA:")
    for level_name, match_result in [
        ("N1", result.n1_match), ("N2", result.n2_match),
        ("N3", result.n3_match), ("N4", result.n4_match),
    ]:
        if match_result and match_result.node:
            meta = match_result.node.metadata
            wr = getattr(meta, 'win_rate', 0)
            obs = getattr(meta, 'historical_count', 0)
            conf = getattr(meta, 'confidence', 0)
            print(f"    {level_name}: WR={wr:.2%}, obs={obs}, conf={conf:.4f}")
        else:
            print(f"    {level_name}: NO MATCH")

    # Target check
    if result.weighted_confidence >= 0.50:
        print(f"\n  ✅ Weighted confidence {result.weighted_confidence:.4f} >= 0.50 (ABOVE RANDOM)")
    elif result.weighted_confidence >= 0.45:
        print(f"\n  ✅ Weighted confidence {result.weighted_confidence:.4f} >= 0.45 (TARGET MET)")
    elif result.weighted_confidence >= 0.40:
        print(f"\n  ⚠️ Weighted confidence {result.weighted_confidence:.4f} >= 0.40 (MINIMUM MET)")
    else:
        print(f"\n  ❌ Weighted confidence {result.weighted_confidence:.4f} < 0.40 (BELOW MINIMUM)")

    storage.close()
    return result


# === Main ===
print("=" * 70)
print("TAREA 15 — OOS VALIDATION (DIRECTIONAL OUTCOME FIX)")
print("=" * 70)

doge_result = run_oos("DOGE/USDT", "1m")

print(f"\n{'='*70}")
print("SUMMARY")
print(f"{'='*70}")
print(f"  DOGE 1m: Weighted confidence = {doge_result.weighted_confidence:.4f}")
print(f"  DOGE 1m: N1_conf={doge_result.n1_confidence:.4f}, N2_conf={doge_result.n2_confidence:.4f}")
print(f"  DOGE 1m: N3_conf={doge_result.n3_confidence:.4f}, N4_conf={doge_result.n4_confidence:.4f}")
print("\nDone.")
