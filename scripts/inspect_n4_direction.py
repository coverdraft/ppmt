#!/usr/bin/env python3
"""Inspect N4 RegimePartitionedTrie for direction bias."""
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie

storage = PPMTStorage()
classifier = AssetClassifier()

for symbol in ["BTC/USDT", "DOGE/USDT"]:
    info = classifier.classify(symbol)
    tries = storage.load_all_tries(symbol, info.asset_class, timeframe="5m")
    n4 = tries.get("n4")

    print(f"\n{'='*60}")
    print(f"  {symbol} N4 Direction Analysis")
    print(f"{'='*60}")

    if isinstance(n4, RegimePartitionedTrie):
        print(f"  Type: RegimePartitionedTrie")
        print(f"  Sub-tries: {list(n4.sub_tries.keys())}")
        for regime_name, sub_trie in n4.sub_tries.items():
            c = {"tl": 0, "ts": 0, "lo": 0, "so": 0, "b": 0, "nwo": 0}

            def walk(node):
                if node.metadata and node.metadata.historical_count > 0:
                    c["nwo"] += 1
                    lc = node.metadata.long_stats.count
                    sc = node.metadata.short_stats.count
                    c["tl"] += lc
                    c["ts"] += sc
                    if lc > 0 and sc == 0: c["lo"] += 1
                    elif sc > 0 and lc == 0: c["so"] += 1
                    elif lc > 0 and sc > 0: c["b"] += 1
                for child in node.children.values():
                    walk(child)

            walk(sub_trie.root)
            print(f"  Regime '{regime_name}': nodes={c['nwo']} LONG_obs={c['tl']} SHORT_obs={c['ts']} "
                  f"L_only={c['lo']} S_only={c['so']} Both={c['b']}")

        # Also check: what does best_direction_p7 return for N4 matched nodes?
        print(f"\n  Checking N4 best_direction_p7 per regime:")
        for regime_name, sub_trie in n4.sub_tries.items():
            c2 = {"sp": 0, "to": 0}

            def walk2(node):
                if node.metadata and node.metadata.historical_count > 0:
                    c2["to"] += 1
                    d = node.metadata.best_direction_p7(min_edge_pct=0.10)
                    if d == "SHORT":
                        c2["sp"] += 1
                for child in node.children.values():
                    walk2(child)

            walk2(sub_trie.root)
            print(f"    Regime '{regime_name}': {c2['sp']}/{c2['to']} nodes prefer SHORT")
    else:
        print(f"  Type: plain PPMTTrie")
