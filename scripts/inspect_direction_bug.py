#!/usr/bin/env python3
"""Inspect trie nodes for direction distribution (LONG vs SHORT stats)."""
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie

storage = PPMTStorage()
classifier = AssetClassifier()

tokens = ["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"]

for symbol in tokens:
    info = classifier.classify(symbol)
    tries = storage.load_all_tries(symbol, info.asset_class, timeframe="5m")

    print(f"\n{'='*70}")
    print(f"  {symbol} (class={info.asset_class}) — 5m tries")
    print(f"{'='*70}")

    for level_name, trie in tries.items():
        if trie is None:
            print(f"  {level_name}: EMPTY")
            continue

        c = {
            "total_nodes": 0, "nodes_with_obs": 0,
            "long_only": 0, "short_only": 0, "both_dirs": 0,
            "total_long_count": 0, "total_short_count": 0,
            "short_preferred": 0,
        }
        sample_nodes = []

        def walk(node, depth=0):
            c["total_nodes"] += 1

            if node.metadata and node.metadata.historical_count > 0:
                c["nodes_with_obs"] += 1
                lc = node.metadata.long_stats.count
                sc = node.metadata.short_stats.count
                c["total_long_count"] += lc
                c["total_short_count"] += sc

                if lc > 0 and sc == 0:
                    c["long_only"] += 1
                elif sc > 0 and lc == 0:
                    c["short_only"] += 1
                elif lc > 0 and sc > 0:
                    c["both_dirs"] += 1

                # Check which direction best_direction_p7 would pick
                best_dir = node.metadata.best_direction_p7(min_edge_pct=0.10)
                if best_dir == "SHORT":
                    c["short_preferred"] += 1
                    if len(sample_nodes) < 3:
                        sample_nodes.append({
                            "lc": lc, "sc": sc,
                            "long_edge": node.metadata.long_edge(),
                            "short_edge": node.metadata.short_edge(),
                            "avg_move_long": node.metadata.avg_move_long,
                            "avg_move_short": node.metadata.avg_move_short,
                            "hist_count": node.metadata.historical_count,
                        })

            for child in node.children.values():
                walk(child, depth + 1)

        # Handle RegimePartitionedTrie (N4)
        if isinstance(trie, RegimePartitionedTrie):
            for regime_name, sub_trie in trie.sub_tries.items():
                walk(sub_trie.root)
        else:
            walk(trie.root)

        tlc = c["total_long_count"]
        tsc = c["total_short_count"]
        nwo = c["nodes_with_obs"]
        print(f"  {level_name}:")
        print(f"    Total nodes:       {c['total_nodes']}")
        print(f"    Nodes with obs:    {nwo}")
        if nwo > 0:
            print(f"    LONG-only nodes:   {c['long_only']} ({c['long_only']/nwo*100:.1f}%)")
            print(f"    SHORT-only nodes:  {c['short_only']} ({c['short_only']/nwo*100:.1f}%)")
            print(f"    Both dirs nodes:   {c['both_dirs']} ({c['both_dirs']/nwo*100:.1f}%)")
        print(f"    Total LONG obs:    {tlc}")
        print(f"    Total SHORT obs:   {tsc}")
        if tsc > 0:
            print(f"    LONG/SHORT ratio:  {tlc/tsc:.2f}")
        else:
            print(f"    LONG/SHORT ratio:  INF (0 SHORT obs!)")
        print(f"    Nodes preferring SHORT (best_direction_p7): {c['short_preferred']}")
        
        if sample_nodes:
            print(f"    Sample SHORT-preferred nodes:")
            for sn in sample_nodes:
                print(f"      lc={sn['lc']} sc={sn['sc']} long_edge={sn['long_edge']:.4f} short_edge={sn['short_edge']:.4f} avg_long={sn['avg_move_long']:.3f}% avg_short={sn['avg_move_short']:.3f}% hist={sn['hist_count']}")
