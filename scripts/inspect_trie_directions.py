#!/usr/bin/env python3
"""Inspect trie nodes to count LONG vs SHORT observations."""
import sys, os
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_dir = os.path.join(_repo_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt.data.classifier import AssetClassifier

storage = PPMTStorage()
classifier = AssetClassifier()

for symbol in ['BTC/USDT', 'SOL/USDT', 'DOGE/USDT', 'LINK/USDT']:
    info = classifier.classify(symbol)
    tries = storage.load_all_tries(symbol, info.asset_class, timeframe='5m')

    for trie_name in ['n3', 'n4']:
        trie = tries.get(trie_name)
        if not trie:
            print(f'{symbol} {trie_name}: trie not found')
            continue

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

        walk(trie.root)
        tlo = counts['total_long_obs']
        tso = counts['total_short_obs']
        total_obs = tlo + tso
        if total_obs > 0:
            print(
                f'{symbol} {trie_name.upper()}: nodes={counts["total_nodes"]} '
                f'LONG_only={counts["long_only"]} SHORT_only={counts["short_only"]} both={counts["both_dirs"]} neither={counts["neither"]} '
                f'| total_long_obs={tlo} total_short_obs={tso} '
                f'| LONG%={tlo/total_obs*100:.1f}%'
            )
        else:
            print(f'{symbol} {trie_name}: no obs')
