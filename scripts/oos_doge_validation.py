#!/usr/bin/env python3
"""OOS Validation: DOGE/USDT 1m — Simulated live flow.

Loads built tries, downloads 500 real candles from Binance,
runs match_raw(), and prints weighted_confidence with per-level metadata.
v0.52.0: Includes BTC 1m candles for N5 context and per-level node metadata.
"""
import sys, time, requests, pandas as pd
sys.path.insert(0, '/home/z/my-project/ppmt/src')

from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier

# 1. Download 500 recent 1m candles (last ~8h)
print("Downloading DOGE/USDT 1m candles from Binance...", flush=True)
resp = requests.get(
    "https://api.binance.com/api/v3/klines",
    params={"symbol": "DOGEUSDT", "interval": "1m", "limit": 500},
    timeout=30,
)
data = resp.json()
df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume",
                                   "ct", "qv", "n", "tbb", "tbq", "ignore"])
df = df[["ts", "open", "high", "low", "close", "volume"]].astype(float)
df["timestamp"] = pd.to_datetime(df["ts"], unit="ms")
df = df.drop(columns=["ts"]).set_index("timestamp")
df = df.sort_index()
print(f"Downloaded {len(df)} DOGE candles", flush=True)

# 2. Download BTC 1m candles for N5 context
print("Downloading BTC/USDT 1m candles for N5 context...", flush=True)
try:
    btc_resp = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "1m", "limit": 500},
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
    btc_df = None

# 3. Load tries from storage
storage = PPMTStorage()
classifier = AssetClassifier()
info = classifier.classify("DOGE/USDT")

all_tries = storage.load_all_tries("DOGE/USDT", asset_class=info.asset_class)
n1 = all_tries.get("n1")
n2 = all_tries.get("n2")
n3 = all_tries.get("n3")
n4 = all_tries.get("n4")

print(f"\nN1 loaded: {n1.pattern_count if n1 else 0} patterns")
print(f"N2 loaded: {n2.pattern_count if n2 else 0} patterns")
print(f"N3 loaded: {n3.pattern_count if n3 else 0} patterns")
n4c = n4.pattern_count if hasattr(n4, 'pattern_count') else 0
print(f"N4 loaded: {n4c} patterns")

# 4. Create engine and inject tries
engine = PPMT(
    symbol="DOGE/USDT",
    asset_class=info.asset_class,
    weight_profile=info.weight_profile,
    dual_sax=True,
    min_confidence=0.08,
    timeframe="1m",
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

# 5. Print engine weight profile
print(f"\nEngine weight profile: {engine.weights.profile}")
print(f"Engine timeframe: {engine.weights.timeframe}")
print(f"  N1 weight: {engine.weights.n1_universal:.2%}")
print(f"  N2 weight: {engine.weights.n2_asset_class:.2%}")
print(f"  N3 weight: {engine.weights.n3_per_asset:.2%}")
print(f"  N4 weight: {engine.weights.n4_per_asset_regime:.2%}")
print(f"  N5 weight: {engine.weights.n5_btc_context:.2%}")

# 6. Run match_raw with FULL 500 candles for proper N1/N2 encoding.
# N1/N2 use W=60, P=5 → need 300+ candles for a full 5-symbol pattern.
# With only 100 candles, N1/N2 produce just 1 symbol → trivial match → 0 confidence.
# N3/N4 use W=10, P=3 → need only 30 candles → 100 is more than enough.
recent_df = df  # Pass ALL 500 candles for proper multi-window encoding
btc_recent = btc_df if btc_df is not None else None
print(f"\nRunning match_raw on last {len(recent_df)} candles (with BTC context)...")

result = engine.match_raw(
    current_symbols=[],
    current_price=float(df["close"].iloc[-1]),
    recent_candles=recent_df,
    btc_recent_candles=btc_recent,
)

print(f"\n{'='*60}")
print(f"DOGE/USDT 1m — OOS VALIDATION RESULT (v0.52.0 weight rebalance)")
print(f"{'='*60}")
print(f"  N1 confidence:    {result.n1_confidence:.4f}")
print(f"  N2 confidence:    {result.n2_confidence:.4f}")
print(f"  N3 confidence:    {result.n3_confidence:.4f}")
print(f"  N4 confidence:    {result.n4_confidence:.4f}")
print(f"  N5 confidence:    {result.n5_confidence:.4f}")
print(f"  Weighted conf:    {result.weighted_confidence:.4f}")
print(f"  Search time:      {result.search_time_ms:.1f}ms")

# 7. Print per-level node metadata (PASO 1 diagnostic)
print(f"\n{'='*60}")
print(f"PER-LEVEL NODE METADATA (Diagnostic)")
print(f"{'='*60}")

for level_name, match_result in [
    ("N1", result.n1_match),
    ("N2", result.n2_match),
    ("N3", result.n3_match),
    ("N4", result.n4_match),
]:
    if match_result and match_result.node:
        meta = match_result.node.metadata
        print(f"\n  {level_name}:")
        print(f"    win_rate:         {getattr(meta, 'win_rate', 'N/A')}")
        print(f"    historical_count: {getattr(meta, 'historical_count', 'N/A')}")
        print(f"    expected_move:    {getattr(meta, 'expected_move', 'N/A')}")
        print(f"    confidence:       {getattr(meta, 'confidence', 'N/A')}")
        print(f"    last_seen:        {getattr(meta, 'last_seen_timestamp', 'N/A')}")
    else:
        print(f"\n  {level_name}: NO MATCH")

# Check against targets
if result.weighted_confidence >= 0.45:
    print(f"\n  ✅ Weighted confidence {result.weighted_confidence:.4f} >= 0.45 (TARGET MET)")
elif result.weighted_confidence >= 0.40:
    print(f"\n  ⚠️ Weighted confidence {result.weighted_confidence:.4f} >= 0.40 (MINIMUM MET, target 0.45)")
elif result.weighted_confidence > 0.30:
    print(f"\n  ⚠️ Weighted confidence {result.weighted_confidence:.4f} > 0.30 (above filter, below target)")
else:
    print(f"\n  ❌ Weighted confidence {result.weighted_confidence:.4f} <= 0.30 (below system filter)")

# 8. Sliding window validation (use 300+ candles per window for N1/N2 encoding)
print(f"\n--- Sliding window validation (5 windows, 300 candles each) ---")
WINDOW_SIZE = 300  # N1/N2 need W=60*P=5=300 candles minimum
for i in range(5):
    # Slide the end point back by 20 candles each time
    end_offset = i * 20
    end_idx = len(df) - end_offset
    start_idx = max(0, end_idx - WINDOW_SIZE)
    if end_idx <= start_idx or end_idx > len(df):
        continue
    window = df.iloc[start_idx:end_idx]
    btc_end_idx = len(btc_df) - end_offset if btc_df is not None else 0
    btc_start_idx = max(0, btc_end_idx - WINDOW_SIZE) if btc_df is not None else 0
    btc_window = btc_df.iloc[btc_start_idx:btc_end_idx] if btc_df is not None and btc_end_idx > btc_start_idx else None
    if len(window) < WINDOW_SIZE:
        print(f"  Window {i}: Skipped (only {len(window)} candles, need {WINDOW_SIZE})")
        continue
    r = engine.match_raw(
        current_symbols=[],
        current_price=float(window["close"].iloc[-1]),
        recent_candles=window,
        btc_recent_candles=btc_window,
    )
    print(f"  Window {i} (last price={float(window['close'].iloc[-1]):.6f}): N1={r.n1_confidence:.3f} N2={r.n2_confidence:.3f} N3={r.n3_confidence:.3f} N4={r.n4_confidence:.3f} N5={r.n5_confidence:.3f} WC={r.weighted_confidence:.4f}")

storage.close()
print("\nDone.")
