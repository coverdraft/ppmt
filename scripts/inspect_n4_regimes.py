#!/usr/bin/env python3
"""Inspect N4 regime sub-tries for LONG vs SHORT observations."""
import sys, os
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_dir = os.path.join(_repo_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt.data.classifier import AssetClassifier
from ppmt.core.trie import RegimePartitionedTrie

storage = PPMTStorage()
classifier = AssetClassifier()

for symbol in ['BTC/USDT', 'SOL/USDT', 'DOGE/USDT', 'LINK/USDT']:
    info = classifier.classify(symbol)
    tries = storage.load_all_tries(symbol, info.asset_class, timeframe='5m')
    n4 = tries.get('n4')
    if not n4:
        print(f'{symbol}: N4 trie not found')
        continue

    if isinstance(n4, RegimePartitionedTrie):
        print(f'\n{symbol} N4 (RegimePartitionedTrie):')
        for regime_name, sub_trie in n4.sub_tries.items():
            counts = {
                'total_nodes': 0, 'long_only': 0, 'short_only': 0,
                'both_dirs': 0, 'neither': 0, 'total_long_obs': 0, 'total_short_obs': 0,
            }

            def walk(node, depth=0):
                if node.metadata and node.metadata.historical_count > 0:
                    counts['total_nodes'] += 1
                    lc = node.metadata.long_stats.count
                    sc = node.metadata.short_stats.count
                    counts['total_long_obs'] += lc
                    counts['total_short_obs'] += sc
                    if lc > 0 and sc > 0:
                        counts['both_dirs'] += 1
                    elif lc > 0:
                        counts['long_only'] += 1
                    elif sc > 0:
                        counts['short_only'] += 1
                    else:
                        counts['neither'] += 1
                for child in node.children.values():
                    walk(child, depth + 1)

            walk(sub_trie.root)
            tlo = counts['total_long_obs']
            tso = counts['total_short_obs']
            total_obs = tlo + tso
            pct = f'{tlo/total_obs*100:.1f}%' if total_obs > 0 else 'N/A'
            print(
                f'  {regime_name:>15}: nodes={counts["total_nodes"]:3d} '
                f'LO={counts["long_only"]:3d} SO={counts["short_only"]:3d} both={counts["both_dirs"]:3d} '
                f'| long_obs={tlo:5d} short_obs={tso:5d} LONG%={pct}'
            )
    else:
        print(f'{symbol} N4: NOT a RegimePartitionedTrie (type={type(n4).__name__})')
