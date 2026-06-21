#!/usr/bin/env python3
"""OOS Validation: DOGE/USDT 1m + SOL/USDT 5m — Post nuclear rebuild.

v0.53.0: Uses timeframe-aware trie loading.
"""
import sys, time, requests, pandas as pd
sys.path.insert(0, '/home/z/my-project/ppmt/src')

from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier

def run_oos(symbol: str, timeframe: str, btc_tf: str = None):
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

    # Download BTC candles for N5 context (1m only)
    btc_df = None
    if btc_tf:
        print(f"Downloading BTC/USDT {btc_tf} candles for N5 context...", flush=True)
        try:
            btc_resp = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": btc_tf, "limit": 500},
                timeout=30,
            )
            btc_data = btc_resp.json()
            btc_df = pd.DataFrame(btc_data, columns=["ts", "open", "high", "low", "close", "volume",
                                                       "ct", "qv", "n", "tbb", "tbq", "ignore"])
            btc_df = btc_df[["ts", "open", "high", "low", "close", "volume"]].astype(float)
            btc_df["timestamp"] = pd.to_datetime(btc_df["ts"], unit="ms")
            btc_df = btc_df.drop(columns=["ts"]).set_index("timestamp")
            btc_df = btc_df.sort_index()
            print(f"Downloaded {len(btc_df)} BTC candles", flush=True)
        except Exception as e:
            print(f"WARNING: Could not download BTC data: {e}")

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
    print(f"Engine timeframe: {engine.weights.timeframe}")
    print(f"  N1 weight: {engine.weights.n1_universal:.2%}")
    print(f"  N2 weight: {engine.weights.n2_asset_class:.2%}")
    print(f"  N3 weight: {engine.weights.n3_per_asset:.2%}")
    print(f"  N4 weight: {engine.weights.n4_per_asset_regime:.2%}")
    print(f"  N5 weight: {engine.weights.n5_btc_context:.2%}")

    # Run match_raw
    recent_df = df
    btc_recent = btc_df if btc_df is not None else None
    print(f"\nRunning match_raw on last {len(recent_df)} candles...", flush=True)

    result = engine.match_raw(
        current_symbols=[],
        current_price=float(df["close"].iloc[-1]),
        recent_candles=recent_df,
        btc_recent_candles=btc_recent,
    )

    print(f"\n{'='*60}")
    print(f"{symbol} {timeframe} — OOS VALIDATION RESULT (v0.53.0)")
    print(f"{'='*60}")
    print(f"  N1 confidence:    {result.n1_confidence:.4f}")
    print(f"  N2 confidence:    {result.n2_confidence:.4f}")
    print(f"  N3 confidence:    {result.n3_confidence:.4f}")
    print(f"  N4 confidence:    {result.n4_confidence:.4f}")
    print(f"  N5 confidence:    {result.n5_confidence:.4f}")
    print(f"  Weighted conf:    {result.weighted_confidence:.4f}")
    print(f"  Search time:      {result.search_time_ms:.1f}ms")

    # Per-level node metadata
    print(f"\n  PER-LEVEL METADATA:")
    for level_name, match_result in [
        ("N1", result.n1_match), ("N2", result.n2_match),
        ("N3", result.n3_match), ("N4", result.n4_match),
    ]:
        if match_result and match_result.node:
            meta = match_result.node.metadata
            print(f"    {level_name}: win_rate={getattr(meta, 'win_rate', 'N/A'):.2%}, "
                  f"obs={getattr(meta, 'historical_count', 'N/A')}, "
                  f"conf={getattr(meta, 'confidence', 'N/A'):.4f}")
        else:
            print(f"    {level_name}: NO MATCH")

    # Target check
    if result.weighted_confidence >= 0.45:
        print(f"\n  ✅ Weighted confidence {result.weighted_confidence:.4f} >= 0.45 (TARGET MET)")
    elif result.weighted_confidence >= 0.40:
        print(f"\n  ⚠️ Weighted confidence {result.weighted_confidence:.4f} >= 0.40 (MINIMUM MET)")
    elif result.weighted_confidence > 0.30:
        print(f"\n  ⚠️ Weighted confidence {result.weighted_confidence:.4f} > 0.30 (above filter)")
    else:
        print(f"\n  ❌ Weighted confidence {result.weighted_confidence:.4f} <= 0.30 (BELOW FILTER)")

    storage.close()
    return result


# === Main ===
classifier = AssetClassifier()
storage_ref = PPMTStorage()

print("=" * 70)
print("TAREA 13 — OOS VALIDATION (POST NUCLEAR REBUILD)")
print("=" * 70)

# DOGE/USDT 1m
doge_result = run_oos("DOGE/USDT", "1m", btc_tf="1m")

# SOL/USDT 5m
sol_result = run_oos("SOL/USDT", "5m", btc_tf="5m")

print(f"\n{'='*70}")
print("SUMMARY")
print(f"{'='*70}")
print(f"  DOGE 1m: N1_WR=N/A, N3_WR=N/A, WC={doge_result.weighted_confidence:.4f}")
print(f"  SOL 5m:  N1_WR=N/A, N3_WR=N/A, WC={sol_result.weighted_confidence:.4f}")

storage_ref.close()
print("\nDone.")
