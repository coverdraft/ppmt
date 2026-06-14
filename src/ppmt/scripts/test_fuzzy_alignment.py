#!/usr/bin/env python3
"""
Validation test for v0.6.6 Read/Write Path Alignment fix.

Tests across alpha=3,4,5 to verify the fix works at all scales.
Uses generated realistic OHLCV data (no Binance API needed).
"""

import sys
import os
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.core.matcher import FuzzyMatcher
from ppmt.engine.paper_trader import _record_observation, PaperTrade


def generate_realistic_ohlcv(n_candles: int = 5000, base_price: float = 50000.0, volatility: float = 0.02) -> pd.DataFrame:
    """Generate realistic OHLCV data using geometric Brownian motion."""
    np.random.seed(42)
    returns = np.random.normal(0.0001, volatility / np.sqrt(24), n_candles)
    close = base_price * np.exp(np.cumsum(returns))
    high = close * (1 + np.abs(np.random.normal(0, volatility * 0.3, n_candles)))
    low = close * (1 - np.abs(np.random.normal(0, volatility * 0.3, n_candles)))
    open_price = close * (1 + np.random.normal(0, volatility * 0.1, n_candles))
    volume = np.random.lognormal(15, 1, n_candles)
    
    df = pd.DataFrame({
        'timestamp': pd.date_range('2024-01-01', periods=n_candles, freq='1h'),
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume,
    })
    df['high'] = df[['open', 'high', 'low', 'close']].max(axis=1)
    df['low'] = df[['open', 'high', 'low', 'close']].min(axis=1)
    return df


def test_single_config(alpha: int, window: int, df: pd.DataFrame, pattern_length: int = 5) -> dict:
    """Test one alpha/window combination."""
    encoder = SAXEncoder(alphabet_size=alpha, window_size=window)
    symbols = encoder.encode(df)
    unique = set(symbols)
    
    matcher = FuzzyMatcher(sax_encoder=encoder, threshold=0.85, max_edit_distance=2)
    
    split_idx = int(len(symbols) * 0.7)
    
    # Count fuzzy-only matches in OOS data
    trie_check = PPMTTrie(name="check")
    for i in range(split_idx - pattern_length):
        pattern = symbols[i:i + pattern_length]
        next_sym = symbols[i + pattern_length] if i + pattern_length < len(symbols) else None
        trie_check.insert_with_observations(symbols=pattern, move_pct=0.0, won=False, next_symbol=next_sym)
    trie_check.propagate_metadata()
    
    fuzzy_only_count = 0
    for i in range(split_idx, len(symbols) - pattern_length, 3):
        pattern = symbols[i:i + pattern_length]
        exact_node = trie_check.search(pattern)
        if exact_node is None:
            fuzzy_result = matcher.best_match(trie_check, pattern)
            if fuzzy_result.matched:
                fuzzy_only_count += 1
    
    # Test WITHOUT fuzzy_matcher
    trie_old = PPMTTrie(name="old")
    for i in range(split_idx - pattern_length):
        pattern = symbols[i:i + pattern_length]
        next_sym = symbols[i + pattern_length] if i + pattern_length < len(symbols) else None
        trie_old.insert_with_observations(symbols=pattern, move_pct=0.0, won=False, next_symbol=next_sym)
    trie_old.propagate_metadata()
    initial_patterns = trie_old.pattern_count
    
    total_obs_old = 0
    total_new_nodes_old = 0
    
    for i in range(split_idx, len(symbols) - pattern_length, 2):
        pattern = symbols[i:i + pattern_length]
        next_sym = symbols[i + pattern_length] if i + pattern_length < len(symbols) else None
        trade = PaperTrade(
            trade_id=i, symbol="BTC/USDT", direction="LONG",
            entry_price=50000, exit_price=50500, entry_time="2024-01-01",
            size=0.1, confidence=0.3, quality_score=0.5, sizing_multiplier=1.0,
            win_rate=0.6, risk_reward_ratio=2.0, expected_move_pct=1.0,
            matched_pattern=pattern, entry_sym_idx=i, pnl_pct=1.0, actual_move_pct=1.0,
        )
        result = _record_observation(trie_old, trade, i + 5, next_sym)
        total_obs_old += result["observations"]
        total_new_nodes_old += result["new_nodes"]
    
    # Test WITH fuzzy_matcher
    trie_new = PPMTTrie(name="new")
    for i in range(split_idx - pattern_length):
        pattern = symbols[i:i + pattern_length]
        next_sym = symbols[i + pattern_length] if i + pattern_length < len(symbols) else None
        trie_new.insert_with_observations(symbols=pattern, move_pct=0.0, won=False, next_symbol=next_sym)
    trie_new.propagate_metadata()
    
    total_obs_new = 0
    total_new_nodes_new = 0
    
    for i in range(split_idx, len(symbols) - pattern_length, 2):
        pattern = symbols[i:i + pattern_length]
        next_sym = symbols[i + pattern_length] if i + pattern_length < len(symbols) else None
        trade = PaperTrade(
            trade_id=i, symbol="BTC/USDT", direction="LONG",
            entry_price=50000, exit_price=50500, entry_time="2024-01-01",
            size=0.1, confidence=0.3, quality_score=0.5, sizing_multiplier=1.0,
            win_rate=0.6, risk_reward_ratio=2.0, expected_move_pct=1.0,
            matched_pattern=pattern, entry_sym_idx=i, pnl_pct=1.0, actual_move_pct=1.0,
        )
        result = _record_observation(trie_new, trade, i + 5, next_sym, fuzzy_matcher=matcher)
        total_obs_new += result["observations"]
        total_new_nodes_new += result["new_nodes"]
    
    reduction = total_new_nodes_old - total_new_nodes_new
    reduction_pct = round(reduction / max(total_new_nodes_old, 1) * 100, 1)
    
    return {
        "alpha": alpha,
        "window": window,
        "candles": len(df),
        "sax_symbols": len(symbols),
        "unique_symbols": len(unique),
        "initial_patterns": initial_patterns,
        "fuzzy_only_count": fuzzy_only_count,
        "old_new_nodes": total_new_nodes_old,
        "old_total_patterns": trie_old.pattern_count,
        "old_observations": total_obs_old,
        "new_new_nodes": total_new_nodes_new,
        "new_total_patterns": trie_new.pattern_count,
        "new_observations": total_obs_new,
        "node_reduction": reduction,
        "reduction_pct": reduction_pct,
        "theoretical_patterns": alpha ** pattern_length,
        "pass": total_new_nodes_new <= total_new_nodes_old and total_obs_new == total_obs_old,
    }


def main():
    print("=" * 60)
    print("TEST: v0.6.6 Read/Write Path Alignment")
    print("=" * 60)
    
    # Generate realistic data
    df = generate_realistic_ohlcv(n_candles=5000)
    print(f"Generated {len(df)} candles\n")
    
    # Test across different alpha values
    configs = [
        (3, 7),  # 1h
        (4, 7),  # 5m
        (5, 7),  # 1m
    ]
    
    results = []
    for alpha, window in configs:
        print(f"\n--- Alpha={alpha}, Window={window} ---")
        result = test_single_config(alpha, window, df)
        results.append(result)
        
        print(f"  Theoretical patterns: {result['theoretical_patterns']}")
        print(f"  Initial patterns (train): {result['initial_patterns']}")
        print(f"  Fuzzy-only matches (OOS): {result['fuzzy_only_count']}")
        print(f"  Old: {result['old_new_nodes']} new nodes → {result['old_total_patterns']} total")
        print(f"  New: {result['new_new_nodes']} new nodes → {result['new_total_patterns']} total")
        print(f"  Reduction: {result['node_reduction']} nodes ({result['reduction_pct']}%)")
        print(f"  {'✅ PASS' if result['pass'] else '❌ FAIL'}")
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    
    all_pass = all(r['pass'] for r in results)
    for r in results:
        status = "✅" if r['pass'] else "❌"
        print(f"  {status} alpha={r['alpha']}: {r['reduction_pct']}% reduction, {r['fuzzy_only_count']} fuzzy-only matches")
    
    if all_pass:
        print("\n✅ ALL TESTS PASSED — v0.6.6 fix is backward compatible")
    else:
        print("\n❌ SOME TESTS FAILED — review the fix")
    
    # Save
    output_path = "/home/z/my-project/download/fuzzy_alignment_test.json"
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
