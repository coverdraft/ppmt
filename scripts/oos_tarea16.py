#!/usr/bin/env python3
"""TAREA 16 OOS Validation: DOGE/USDT 1m (500 candles).

Tests the new volume + body_anatomy encoding for 1m N3/N4/N5.
"""
import sys, os, requests, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier

classifier = AssetClassifier()

def run_oos(symbol, timeframe, btc_tf=None):
    api_symbol = symbol.replace("/", "")
    info = classifier.classify(symbol)

    # Download 500 recent candles
    print(f"Downloading {symbol} {timeframe}...", flush=True)
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
    df = df.drop(columns=["ts"]).set_index("timestamp").sort_index()
    print(f"Downloaded {len(df)} candles", flush=True)

    # Download BTC candles for N5 context (1m only)
    btc_df = None
    if btc_tf:
        try:
            print(f"Downloading BTC {btc_tf}...", flush=True)
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
            btc_df = btc_df.drop(columns=["ts"]).set_index("timestamp").sort_index()
            print(f"Downloaded {len(btc_df)} BTC candles", flush=True)
        except Exception as e:
            print(f"WARNING: BTC download failed: {e}", flush=True)

    # Load tries from storage
    storage = PPMTStorage()
    all_tries = storage.load_all_tries(symbol, asset_class=info.asset_class, timeframe=timeframe)
    n1 = all_tries.get("n1")
    n2 = all_tries.get("n2")
    n3 = all_tries.get("n3")
    n4 = all_tries.get("n4")

    print(f"N1: {n1.pattern_count if n1 else 0} | N2: {n2.pattern_count if n2 else 0} | "
          f"N3: {n3.pattern_count if n3 else 0} | N4: {n4.pattern_count if n4 else 0}", flush=True)

    # N3 detailed stats
    if n3:
        def get_leaves(node, depth=0, max_depth=3):
            if depth == max_depth or not node.children:
                return [node]
            leaves = []
            for k, child in node.children.items():
                leaves.extend(get_leaves(child, depth+1, max_depth))
            return leaves
        leaves = get_leaves(n3.root, 0, 3)
        n3_obs = sum(l.metadata.historical_count for l in leaves)
        n3_wins = sum(int(l.metadata.historical_count * l.metadata.win_rate) for l in leaves)
        n3_wr = (n3_wins / n3_obs * 100) if n3_obs > 0 else 0
        print(f"N3 aggregate: {n3_obs} obs, WR={n3_wr:.1f}%", flush=True)

    # Create engine and inject tries
    engine = PPMT(
        symbol=symbol,
        asset_class=info.asset_class,
        weight_profile=info.weight_profile,
        dual_sax=True,
        min_confidence=0.08,
        timeframe=timeframe,
    )

    from ppmt.core.trie import PPMTTrie
    if n1 or n2 or n3:
        engine.set_tries(
            trie_n1=n1 if n1 is not None else PPMTTrie(name="universal_empty"),
            trie_n2=n2 if n2 is not None else PPMTTrie(name="class_empty"),
            trie_n3=n3 or PPMTTrie(name="n3_empty"),
            trie_n4=n4 if n4 is not None else engine.trie_n4,
        )

    # Print weight profile
    print(f"\nWeight profile: {engine.weights.profile}, TF: {engine.weights.timeframe}", flush=True)
    print(f"  N1: {engine.weights.n1_universal:.2%} | N2: {engine.weights.n2_asset_class:.2%} | "
          f"N3: {engine.weights.n3_per_asset:.2%} | N4: {engine.weights.n4_per_asset_regime:.2%} | "
          f"N5: {engine.weights.n5_btc_context:.2%}", flush=True)

    # Run match_raw
    print(f"\nRunning match_raw on {len(df)} candles...", flush=True)
    result = engine.match_raw(
        current_symbols=[],
        current_price=float(df["close"].iloc[-1]),
        recent_candles=df,
        btc_recent_candles=btc_df,
    )

    print(f"\n{'='*60}", flush=True)
    print(f"OOS RESULT: {symbol} {timeframe} (v0.55.0 TAREA 16)", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  N1 confidence:    {result.n1_confidence:.4f}", flush=True)
    print(f"  N2 confidence:    {result.n2_confidence:.4f}", flush=True)
    print(f"  N3 confidence:    {result.n3_confidence:.4f}", flush=True)
    print(f"  N4 confidence:    {result.n4_confidence:.4f}", flush=True)
    print(f"  N5 confidence:    {result.n5_confidence:.4f}", flush=True)
    print(f"  Weighted conf:    {result.weighted_confidence:.4f}", flush=True)
    print(f"  Search time:      {result.search_time_ms:.1f}ms", flush=True)

    # Per-level node metadata
    print(f"\n  PER-LEVEL METADATA:", flush=True)
    for level_name, match_result in [
        ("N1", result.n1_match), ("N2", result.n2_match),
        ("N3", result.n3_match), ("N4", result.n4_match),
    ]:
        if match_result and match_result.node:
            meta = match_result.node.metadata
            print(f"    {level_name}: win_rate={meta.win_rate:.2%}, "
                  f"obs={meta.historical_count}, "
                  f"conf={meta.confidence:.4f}, "
                  f"em={meta.expected_move_pct:.4f}%", flush=True)
        else:
            print(f"    {level_name}: NO MATCH", flush=True)

    # Target check
    if result.weighted_confidence >= 0.50:
        print(f"\n  ✅ Weighted confidence {result.weighted_confidence:.4f} >= 0.50 (TARGET MET)", flush=True)
    elif result.weighted_confidence >= 0.45:
        print(f"\n  ⚠️ Weighted confidence {result.weighted_confidence:.4f} >= 0.45 (NEAR TARGET)", flush=True)
    elif result.weighted_confidence >= 0.40:
        print(f"\n  ⚠️ Weighted confidence {result.weighted_confidence:.4f} >= 0.40 (MINIMUM)", flush=True)
    else:
        print(f"\n  ❌ Weighted confidence {result.weighted_confidence:.4f} < 0.40", flush=True)

    storage.close()
    return result


# === Main ===
print("=" * 70, flush=True)
print("TAREA 16 — OOS VALIDATION (VOLUME + BODY_ANATOMY)", flush=True)
print("=" * 70, flush=True)

doge_result = run_oos("DOGE/USDT", "1m", btc_tf="1m")

print(f"\n{'='*70}", flush=True)
print("SUMMARY", flush=True)
print(f"{'='*70}", flush=True)

# Extract key metrics
n3_wr = "N/A"
n3_conf = f"{doge_result.n3_confidence:.4f}"
wc = f"{doge_result.weighted_confidence:.4f}"

if doge_result.n3_match and doge_result.n3_match.node:
    n3_wr = f"{doge_result.n3_match.node.metadata.win_rate:.2%}"

print(f"  DOGE 1m: N3_WR={n3_wr}, N3_conf={n3_conf}, Weighted_confidence={wc}", flush=True)
print("\nDone.", flush=True)
