#!/usr/bin/env python3
"""
Trace which SAX pattern is being matched at each level during OOS.
Why does N3 always return the same node?
"""
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

import pandas as pd
from ppmt.data.classifier import AssetClassifier
from ppmt.data.storage import PPMTStorage
from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.weights import AdaptiveWeights
from ppmt.core.trie import PPMTTrie

storage = PPMTStorage()
classifier = AssetClassifier()

symbol = "BTC/USDT"
info = classifier.classify(symbol)
df = storage.load_ohlcv(symbol, "5m")
oos_start = df.index[-1] - pd.Timedelta(days=7)
oos_df = df[df.index >= oos_start]

tries = storage.load_all_tries(symbol, info.asset_class, timeframe="5m")

engine = PPMT(
    symbol=symbol, asset_class=info.asset_class,
    weight_profile=info.weight_profile, dual_sax=True,
    min_confidence=0.08, timeframe="5m",
)
engine.weights = AdaptiveWeights.from_profile(info.weight_profile, timeframe="5m")
engine.set_tries(
    trie_n1=tries["n1"] or PPMTTrie(name="empty_n1"),
    trie_n2=tries["n2"] or PPMTTrie(name="empty_n2"),
    trie_n3=tries["n3"] or PPMTTrie(name="empty_n3"),
    trie_n4=tries["n4"] or engine.trie_n4,
)

# Track SAX symbols produced and matched
n3_patterns_matched = {}
n1_patterns_matched = {}
sax_symbols_seen = {}
entry_signal_count = 0
total_candles_with_result = 0

_last_engine_ts = 0

for idx in range(len(oos_df)):
    row = oos_df.iloc[[idx]]
    current_price = float(row["close"].iloc[0])
    ts = oos_df.index[idx]
    ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)

    if ts_sec <= _last_engine_ts:
        continue
    _last_engine_ts = ts_sec

    result = engine.process_new_candle(
        candle_df=row, current_price=current_price,
        is_in_position=False, entry_price=None,
    )

    if result is None:
        continue

    total_candles_with_result += 1

    # Track SAX symbols
    if result.sax_symbols:
        sym_str = "".join(str(s) for s in result.sax_symbols)
        sax_symbols_seen[sym_str] = sax_symbols_seen.get(sym_str, 0) + 1

    # Track N3 match
    if result.n3_match and result.n3_match.node:
        n3_node = result.n3_match.node
        # Get the path key from the trie
        n3_key = ""
        if hasattr(n3_node, 'symbol') and n3_node.symbol:
            n3_key = n3_node.symbol
        n3_hc = n3_node.metadata.historical_count if n3_node.metadata else 0
        n3_dir = n3_node.metadata.best_direction_p7(min_edge_pct=0.10) if n3_node.metadata and n3_hc > 0 else "N/A"
        key = f"hc={n3_hc}_dir={n3_dir}"
        n3_patterns_matched[key] = n3_patterns_matched.get(key, 0) + 1

    # Track N1 match
    if result.n1_match and result.n1_match.node:
        n1_node = result.n1_match.node
        n1_hc = n1_node.metadata.historical_count if n1_node.metadata else 0
        n1_dir = n1_node.metadata.best_direction_p7(min_edge_pct=0.10) if n1_node.metadata and n1_hc > 0 else "N/A"
        key = f"hc={n1_hc}_dir={n1_dir}"
        n1_patterns_matched[key] = n1_patterns_matched.get(key, 0) + 1

    sig = result.signal
    if sig is None or not sig.is_entry:
        continue
    entry_signal_count += 1

print(f"\n{'='*70}")
print(f"  {symbol} — SAX Pattern Analysis (5m, 7d OOS)")
print(f"{'='*70}")
print(f"  Total candles with result: {total_candles_with_result}")
print(f"  Entry signals: {entry_signal_count}")

print(f"\n  SAX symbols seen (top 20 by frequency):")
sorted_sax = sorted(sax_symbols_seen.items(), key=lambda x: -x[1])[:20]
for sym, count in sorted_sax:
    print(f"    {sym:<20} → {count} times ({count/total_candles_with_result*100:.1f}%)")

print(f"\n  N3 matched nodes (by historical_count + direction):")
sorted_n3 = sorted(n3_patterns_matched.items(), key=lambda x: -x[1])[:10]
for key, count in sorted_n3:
    print(f"    {key:<30} → {count} matches ({count/total_candles_with_result*100:.1f}%)")

print(f"\n  N1 matched nodes (by historical_count + direction):")
sorted_n1 = sorted(n1_patterns_matched.items(), key=lambda x: -x[1])[:10]
for key, count in sorted_n1:
    print(f"    {key:<30} → {count} matches ({count/total_candles_with_result*100:.1f}%)")

# Check: what SAX symbols does the streaming buffer have?
print(f"\n  Current streaming buffer state:")
buf = getattr(engine, '_streaming_buffer', None)
if buf:
    print(f"    Buffer type: {type(buf).__name__}")
    if hasattr(buf, '_pattern_buffer'):
        pb = buf._pattern_buffer
        print(f"    Pattern buffer length: {len(pb)}")
        if pb:
            print(f"    Last 5 symbols: {pb[-5:]}")
