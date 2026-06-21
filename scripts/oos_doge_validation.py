#!/usr/bin/env python3
"""OOS Validation: DOGE/USDT 1m — Simulated live flow.

Loads built tries, downloads 100 real candles from Binance,
runs match_raw(), and prints weighted_confidence.
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
print(f"Downloaded {len(df)} candles", flush=True)

# 2. Load tries from storage
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

# 3. Create engine and inject tries
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

# 4. Run match_raw on the last 100 candles
recent_df = df.iloc[-100:]
print(f"\nRunning match_raw on last {len(recent_df)} candles...")

result = engine.match_raw(
    current_symbols=[],
    current_price=float(df["close"].iloc[-1]),
    recent_candles=recent_df,
)

print(f"\n{'='*50}")
print(f"DOGE/USDT 1m — OOS VALIDATION RESULT")
print(f"{'='*50}")
print(f"  N1 confidence:    {result.n1_confidence:.4f}")
print(f"  N2 confidence:    {result.n2_confidence:.4f}")
print(f"  N3 confidence:    {result.n3_confidence:.4f}")
print(f"  N4 confidence:    {result.n4_confidence:.4f}")
print(f"  Weighted conf:    {result.weighted_confidence:.4f}")
print(f"  Search time:      {result.search_time_ms:.1f}ms")

# Check against the 0.13 dead value
if result.weighted_confidence > 0.13:
    print(f"\n  ✅ Weighted confidence {result.weighted_confidence:.4f} > 0.13 (dead value)")
else:
    print(f"\n  ⚠️ Weighted confidence {result.weighted_confidence:.4f} <= 0.13 (below dead value)")

# 5. Also run multiple windows to get a range
print(f"\n--- Sliding window validation (last 5 windows) ---")
for i in range(5):
    offset = 100 + i * 20
    window = df.iloc[-offset:-offset+100] if offset < len(df) else df.iloc[-100:]
    r = engine.match_raw(current_symbols=[], current_price=float(window["close"].iloc[-1]), recent_candles=window)
    print(f"  Window -{offset}: N1={r.n1_confidence:.3f} N2={r.n2_confidence:.3f} N3={r.n3_confidence:.3f} N4={r.n4_confidence:.3f} WC={r.weighted_confidence:.4f}")

storage.close()
print("\nDone.")
