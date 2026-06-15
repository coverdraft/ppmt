#!/usr/bin/env python3
"""
Diagnostic: Trie Node Proliferation Analysis (QUICK)

Only tests 1h timeframe to avoid timeout.
Uses ccxt for data fetching.
"""

import json
import sys
import os
import time
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

import ccxt
import numpy as np
import pandas as pd

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie


def fetch_data_ccxt(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Fetch OHLCV data via ccxt with rate limiting."""
    exchange = ccxt.binance({"enableRateLimit": True})
    
    since = exchange.parse8601(
        (pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    )
    
    all_ohlcv = []
    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        except Exception as e:
            print(f"  ccxt error: {e}, retrying in 5s...")
            time.sleep(5)
            continue
        
        if not ohlcv:
            break
        all_ohlcv.extend(ohlcv)
        since = ohlcv[-1][0] + 1
        if len(ohlcv) < 1000:
            break
        time.sleep(0.3)
    
    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def count_nodes_by_depth(trie: PPMTTrie) -> dict:
    depth_counts = defaultdict(int)
    count_buckets = defaultdict(int)
    
    def walk(node, depth):
        depth_counts[depth] += 1
        hc = node.metadata.historical_count if node.metadata else 0
        if hc == 0:
            count_buckets["0"] += 1
        elif hc == 1:
            count_buckets["1"] += 1
        elif hc <= 5:
            count_buckets["2-5"] += 1
        elif hc <= 10:
            count_buckets["6-10"] += 1
        elif hc <= 50:
            count_buckets["11-50"] += 1
        else:
            count_buckets["50+"] += 1
        for child in node.children.values():
            walk(child, depth + 1)
    
    walk(trie.root, 0)
    return {"by_depth": dict(depth_counts), "by_count": dict(count_buckets)}


def find_near_duplicates(trie: PPMTTrie, alphabet_size: int) -> dict:
    near_dupes = 0
    total_parents = 0
    parents_with_near_dupes = 0
    
    def walk(node):
        nonlocal near_dupes, total_parents, parents_with_near_dupes
        if not node.children:
            return
        total_parents += 1
        children_symbols = list(node.children.keys())
        has_near = False
        for i, sym_a in enumerate(children_symbols):
            for sym_b in children_symbols[i+1:]:
                idx_a = ord(sym_a) - ord('a')
                idx_b = ord(sym_b) - ord('a')
                if abs(idx_a - idx_b) == 1:
                    near_dupes += 1
                    has_near = True
        if has_near:
            parents_with_near_dupes += 1
        for child in node.children.values():
            walk(child)
    
    walk(trie.root)
    return {
        "total_parents": total_parents,
        "parents_with_near_dupes": parents_with_near_dupes,
        "near_duplicate_pairs": near_dupes,
        "pct": round(parents_with_near_dupes / max(total_parents, 1) * 100, 1),
    }


def find_low_confidence_patterns(trie: PPMTTrie) -> dict:
    low_conf = med_conf = high_conf = single_obs = total_terminal = 0
    
    def walk(node):
        nonlocal low_conf, med_conf, high_conf, single_obs, total_terminal
        if not node.children:
            total_terminal += 1
            conf = node.metadata.confidence if node.metadata else 0.0
            hc = node.metadata.historical_count if node.metadata else 0
            if hc <= 1:
                single_obs += 1
            if conf < 0.15:
                low_conf += 1
            elif conf < 0.30:
                med_conf += 1
            else:
                high_conf += 1
        for child in node.children.values():
            walk(child)
    
    walk(trie.root)
    return {
        "total_terminal": total_terminal,
        "single_obs": single_obs,
        "single_obs_pct": round(single_obs / max(total_terminal, 1) * 100, 1),
        "low_conf": low_conf,
        "low_conf_pct": round(low_conf / max(total_terminal, 1) * 100, 1),
        "med_conf": med_conf,
        "high_conf": high_conf,
    }


def measure_read_write_mismatch(trie: PPMTTrie, symbols: list[str], encoder: SAXEncoder) -> dict:
    from ppmt.core.matcher import FuzzyMatcher
    matcher = FuzzyMatcher(encoder, threshold=0.85)
    pattern_length = 5
    
    exact = fuzzy_only = no_match = 0
    total = min(1000, len(symbols) - pattern_length)
    step = max(1, (len(symbols) - pattern_length) // total)
    
    for i in range(0, len(symbols) - pattern_length, step):
        pattern = symbols[i:i + pattern_length]
        
        exact_node = trie.search(pattern)
        if exact_node is not None:
            exact += 1
        else:
            fuzzy_result = matcher.best_match(trie, pattern)
            if fuzzy_result.matched:
                fuzzy_only += 1
            else:
                no_match += 1
    
    total_tested = exact + fuzzy_only + no_match
    return {
        "total_tested": total_tested,
        "exact": exact,
        "fuzzy_only": fuzzy_only,
        "no_match": no_match,
        "fuzzy_only_pct": round(fuzzy_only / max(total_tested, 1) * 100, 1),
        "mismatch_pct": round(fuzzy_only / max(total_tested, 1) * 100, 1),
    }


def run_diagnostic(symbol: str, timeframe: str, alpha: int, window: int, days: int = 365):
    print(f"\n{'='*70}")
    print(f"TRIE DIAGNOSTIC: {symbol} @ {timeframe} (alpha={alpha}, window={window})")
    print(f"{'='*70}")
    
    print(f"Fetching {days} days of {timeframe} data...")
    df = fetch_data_ccxt(symbol, timeframe, days)
    print(f"  Got {len(df)} candles")
    
    encoder = SAXEncoder(alphabet_size=alpha, window_size=window)
    symbols = encoder.encode(df)
    unique_symbols = set(symbols)
    print(f"  {len(symbols)} SAX symbols, {len(unique_symbols)} unique / {alpha} possible")
    
    pattern_length = 5
    unique_patterns = set()
    for i in range(len(symbols) - pattern_length):
        unique_patterns.add(tuple(symbols[i:i + pattern_length]))
    print(f"  {len(unique_patterns)} unique {pattern_length}-symbol patterns")
    
    trie = PPMTTrie(name=f"diag_{symbol.replace('/','_')}_{timeframe}")
    for i in range(len(symbols) - pattern_length):
        pattern = symbols[i:i + pattern_length]
        next_sym = symbols[i + pattern_length] if i + pattern_length < len(symbols) else None
        trie.insert_with_observations(symbols=pattern, move_pct=0.0, won=False, next_symbol=next_sym)
    
    dist = count_nodes_by_depth(trie)
    near_dup = find_near_duplicates(trie, alpha)
    low_conf = find_low_confidence_patterns(trie)
    mismatch = measure_read_write_mismatch(trie, symbols, encoder)
    total_nodes = sum(dist['by_depth'].values()) - 1
    
    print(f"\n  --- Node Distribution ---")
    print(f"  By depth: {dist['by_depth']}")
    print(f"  By count: {dist['by_count']}")
    print(f"  Total nodes: {total_nodes}")
    
    print(f"\n  --- Near-Duplicates ---")
    print(f"  Parents with near-dup children: {near_dup['parents_with_near_dupes']}/{near_dup['total_parents']} ({near_dup['pct']}%)")
    print(f"  Near-dup pairs: {near_dup['near_duplicate_pairs']}")
    
    print(f"\n  --- Low-Confidence ---")
    print(f"  Terminal nodes: {low_conf['total_terminal']}")
    print(f"  Single-obs: {low_conf['single_obs']} ({low_conf['single_obs_pct']}%)")
    print(f"  Low conf (<0.15): {low_conf['low_conf']} ({low_conf['low_conf_pct']}%)")
    print(f"  High conf (>0.30): {low_conf['high_conf']}")
    
    print(f"\n  --- Read/Write Mismatch ---")
    print(f"  Exact matches: {mismatch['exact']}")
    print(f"  Fuzzy-only matches: {mismatch['fuzzy_only']} ({mismatch['fuzzy_only_pct']}%)")
    print(f"  No match: {mismatch['no_match']}")
    print(f"  MISMATCH RATIO: {mismatch['mismatch_pct']}%")
    
    return {
        "symbol": symbol, "timeframe": timeframe, "alpha": alpha, "window": window,
        "candles": len(df), "unique_symbols": len(unique_symbols),
        "unique_patterns": len(unique_patterns), "total_nodes": total_nodes,
        "distribution": dist, "near_duplicates": near_dup,
        "low_confidence": low_conf, "mismatch": mismatch,
        "theoretical_patterns": alpha ** pattern_length,
    }


def main():
    # Only 1h timeframe to avoid timeout
    test_cases = [
        ("BTC/USDT", "1h", 3, 7, 365),
        ("DOGE/USDT", "1h", 3, 7, 365),
        ("BTC/USDT", "5m", 4, 7, 90),  # 90 days only
    ]
    
    results = []
    for symbol, tf, alpha, window, days in test_cases:
        try:
            result = run_diagnostic(symbol, tf, alpha, window, days)
            results.append(result)
        except Exception as e:
            print(f"\nERROR for {symbol}@{tf}: {e}")
            import traceback
            traceback.print_exc()
    
    # Summary
    print(f"\n\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for r in results:
        print(f"\n  {r['symbol']} @ {r['timeframe']} (alpha={r['alpha']}):")
        print(f"    Nodes: {r['total_nodes']}, Terminal: {r['low_confidence']['total_terminal']} / {r['theoretical_patterns']} theoretical")
        print(f"    Unique patterns in data: {r['unique_patterns']}")
        print(f"    Single-obs: {r['low_confidence']['single_obs']} ({r['low_confidence']['single_obs_pct']}%)")
        print(f"    Near-dup pairs: {r['near_duplicates']['near_duplicate_pairs']}")
        print(f"    R/W mismatch: {r['mismatch']['fuzzy_only']} ({r['mismatch']['mismatch_pct']}%)")
    
    output_path = "/home/z/my-project/download/trie_proliferation_diagnostic.json"
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
