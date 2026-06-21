#!/usr/bin/env python3
"""
PPMT — DOGE/USDT 1m SAX Parameter Optimizer

PASO 1: OOS Diagnosis (500 candles) — win_rate, historical_count, expected_move per level
PASO 2: Iterative parameter tuning (3 attempts)
PASO 3: Final validation with weighted_confidence >= 0.45

This script:
1. Downloads DOGE/USDT 1m data (and BTC/USDT 1m for N5 context)
2. Builds 4-level tries with the CURRENT sax.py config
3. Runs a 500-candle OOS walk-forward, printing per-level node metadata
4. Computes weighted_confidence across all OOS candles
5. Then allows parameter modification and re-run
"""

import sys
import os
import json
import time
import copy
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone

# Setup project paths
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_PATH = os.path.join(PROJECT_ROOT, 'src')
sys.path.insert(0, SRC_PATH)
os.environ['PYTHONPATH'] = SRC_PATH

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger("doge_optimizer")
logger.setLevel(logging.INFO)

# ============================================================
# STEP 1: Download data
# ============================================================
def download_klines(symbol: str, interval: str = "1m", days: int = 5, exchange: str = "binance"):
    """Download OHLCV data from exchange APIs with fallback chain."""
    from ppmt.data.collector import DataCollector
    collector = DataCollector()
    
    # Use the collector's built-in download with fallback
    try:
        df = collector.fetch_ohlcv(symbol, timeframe=interval, days=days)
        if df is not None and len(df) > 0:
            logger.info(f"Downloaded {len(df)} candles for {symbol} @ {interval}")
            return df
    except Exception as e:
        logger.warning(f"DataCollector fetch failed: {e}")
    
    # Direct Binance API fallback
    return download_binance_direct(symbol, interval, days)


def download_binance_direct(symbol: str, interval: str = "1m", days: int = 5):
    """Direct Binance klines API download with pagination."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
    import json as _json
    
    base_url = "https://api.binance.com/api/v3/klines"
    binance_symbol = symbol.replace("/", "")
    
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)
    
    all_candles = []
    current_start = start_ms
    limit = 1000
    
    while current_start < end_ms:
        url = f"{base_url}?symbol={binance_symbol}&interval={interval}&startTime={current_start}&limit={limit}"
        req = Request(url, headers={"User-Agent": "PPMT/0.50.0"})
        
        try:
            with urlopen(req, timeout=30) as resp:
                data = _json.loads(resp.read().decode())
        except (HTTPError, URLError) as e:
            logger.warning(f"Binance API error: {e}, trying Bybit...")
            return download_bybit_direct(symbol, interval, days)
        
        if not data:
            break
        
        for candle in data:
            all_candles.append({
                "timestamp": candle[0],
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            })
        
        current_start = data[-1][0] + 1
        time.sleep(0.15)  # Rate limit
    
    if not all_candles:
        return None
    
    df = pd.DataFrame(all_candles)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    logger.info(f"Downloaded {len(df)} candles for {symbol} @ {interval} via Binance")
    return df


def download_bybit_direct(symbol: str, interval: str = "1m", days: int = 5):
    """Direct Bybit klines API download."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
    import json as _json
    
    base_url = "https://api.bybit.com/v5/market/kline"
    bybit_symbol = symbol.replace("/", "")
    category = "spot"
    
    # Bybit interval mapping
    interval_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
    bybit_interval = interval_map.get(interval, "1")
    
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)
    
    all_candles = []
    current_end = end_ms
    limit = 200
    
    while current_end > start_ms:
        url = (
            f"{base_url}?category={category}&symbol={bybit_symbol}"
            f"&interval={bybit_interval}&end={current_end}&limit={limit}"
        )
        req = Request(url, headers={"User-Agent": "PPMT/0.50.0"})
        
        try:
            with urlopen(req, timeout=30) as resp:
                resp_data = _json.loads(resp.read().decode())
        except (HTTPError, URLError) as e:
            logger.error(f"Bybit API error: {e}")
            break
        
        if resp_data.get("retCode") != 0 or not resp_data.get("result", {}).get("list"):
            break
        
        candles = resp_data["result"]["list"]
        for candle in candles:
            all_candles.append({
                "timestamp": int(candle[0]),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            })
        
        # Bybit returns newest first; use oldest timestamp as next end
        oldest_ts = min(c[0] for c in candles)
        current_end = oldest_ts - 1
        
        if oldest_ts <= start_ms:
            break
        time.sleep(0.15)
    
    if not all_candles:
        return None
    
    df = pd.DataFrame(all_candles)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    # Remove duplicates
    df = df.drop_duplicates(subset=["timestamp"], keep="first").reset_index(drop=True)
    logger.info(f"Downloaded {len(df)} candles for {symbol} @ {interval} via Bybit")
    return df


# ============================================================
# STEP 2: Build engine + tries
# ============================================================
def build_engine_with_data(
    symbol: str = "DOGE/USDT",
    asset_class: str = "meme",
    timeframe: str = "1m",
    df: pd.DataFrame = None,
    btc_df: pd.DataFrame = None,
):
    """Build a PPMT engine, train on data, and return it."""
    from ppmt.engine.ppmt import PPMT
    from ppmt.data.storage import PPMTStorage
    
    storage = PPMTStorage()
    
    engine = PPMT(
        symbol=symbol,
        asset_class=asset_class,
        dual_sax=True,
        min_confidence=0.08,
        timeframe=timeframe,
    )
    
    # Attach storage so build() contributes to shared pools
    engine.attach_storage(storage)
    
    # Load existing shared pools (N1 universal, N2 class) if available
    from ppmt.data.storage import UNIVERSAL_POOL_KEY, class_pool_key
    
    n1_shared = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
    n2_shared = storage.load_trie(class_pool_key(asset_class), "n2")
    
    if n1_shared is not None:
        engine.trie_n1 = n1_shared
        logger.info(f"Loaded shared N1: {n1_shared.pattern_count} patterns")
    if n2_shared is not None:
        engine.trie_n2 = n2_shared
        logger.info(f"Loaded shared N2 ({asset_class}): {n2_shared.pattern_count} patterns")
    
    # Build from training data
    count = engine.build(df)
    logger.info(f"Built {count} patterns from {len(df)} candles")
    
    # Save updated shared pools and per-symbol tries
    storage.save_trie(UNIVERSAL_POOL_KEY, "n1", engine.trie_n1)
    storage.save_trie(class_pool_key(asset_class), "n2", engine.trie_n2)
    storage.save_trie(symbol, "n3", engine.trie_n3)
    storage.save_trie(symbol, "n4", engine.trie_n4)
    
    return engine, storage


# ============================================================
# STEP 3: OOS Diagnosis
# ============================================================
def oos_diagnosis(engine, oos_df: pd.DataFrame, btc_oos_df: pd.DataFrame = None, label: str = ""):
    """
    Run OOS walk-forward diagnosis on 500 candles.
    
    For each candle, we:
    1. Encode the recent window with each level's encoder
    2. Match against tries
    3. Extract node metadata (win_rate, historical_count, expected_move)
    4. Compute weighted_confidence
    
    Returns the average weighted_confidence and per-level stats.
    """
    from ppmt.core.sax import LEVEL_WINDOW_CONFIG, LEVEL_PATTERN_CONFIG
    
    timeframe = engine.timeframe or "1m"
    w_config = LEVEL_WINDOW_CONFIG.get(timeframe, {})
    
    # Minimum candles needed for encoding
    min_candles = max(
        engine.sax_n1.window_size * engine.pl_n1,
        engine.sax_n2.window_size * engine.pl_n2,
        engine.sax_n3.window_size * engine.pl_n3,
        engine.sax_n4.window_size * engine.pl_n4,
    )
    
    # Step through OOS data
    step_size = 1  # Every candle
    results = []
    level_metadata = {"n1": [], "n2": [], "n3": [], "n4": [], "n5": []}
    match_counts = {"n1": 0, "n2": 0, "n3": 0, "n4": 0, "n5": 0}
    
    total_oos = len(oos_df)
    n_evaluated = 0
    
    for i in range(min_candles, total_oos):
        # Get recent candles for encoding
        recent_df = oos_df.iloc[max(0, i - min_candles - 50):i+1].copy()
        
        if len(recent_df) < min_candles:
            continue
        
        # Get BTC context candles for N5
        btc_recent = None
        if btc_oos_df is not None and len(btc_oos_df) > 20:
            btc_idx = min(i, len(btc_oos_df) - 1)
            btc_recent = btc_oos_df.iloc[max(0, btc_idx - 100):btc_idx+1].copy()
        
        # Run match_raw
        try:
            result = engine.match_raw(
                current_symbols=[],
                current_price=oos_df.iloc[i]["close"],
                recent_candles=recent_df,
                btc_recent_candles=btc_recent,
            )
        except Exception as e:
            continue
        
        n_evaluated += 1
        
        # Extract per-level node metadata
        for level_name, match_result in [
            ("n1", result.n1_match),
            ("n2", result.n2_match),
            ("n3", result.n3_match),
            ("n4", result.n4_match),
        ]:
            if match_result is not None and match_result.node is not None:
                node = match_result.node
                meta = node.metadata
                match_counts[level_name] += 1
                level_metadata[level_name].append({
                    "win_rate": meta.win_rate if hasattr(meta, 'win_rate') else 0.0,
                    "historical_count": meta.historical_count if hasattr(meta, 'historical_count') else 0,
                    "expected_move_pct": meta.expected_move_pct if hasattr(meta, 'expected_move_pct') else 0.0,
                    "confidence": meta.confidence if hasattr(meta, 'confidence') else 0.0,
                })
        
        # N5 check
        if hasattr(engine, 'trie_n5') and engine.trie_n5 is not None:
            # N5 metadata is blended into weighted_confidence
            pass
        
        results.append({
            "weighted_confidence": result.weighted_confidence,
            "n1_confidence": result.n1_confidence,
            "n2_confidence": result.n2_confidence,
            "n3_confidence": result.n3_confidence,
            "n4_confidence": result.n4_confidence,
        })
        
        # Limit to 500 evaluations
        if n_evaluated >= 500:
            break
    
    if not results:
        logger.error("No OOS results produced!")
        return None
    
    # Compute summary statistics
    confidences = [r["weighted_confidence"] for r in results]
    avg_weighted = np.mean(confidences)
    median_weighted = np.median(confidences)
    
    print(f"\n{'='*80}")
    print(f"  OOS DIAGNOSIS — {label}")
    print(f"  Evaluated: {n_evaluated} / {total_oos} candles")
    print(f"{'='*80}")
    
    # Per-level summary
    print(f"\n  {'Level':<6} {'Match%':>8} {'Avg WR':>10} {'Avg Count':>12} {'Avg Move%':>12} {'Avg Conf':>10}")
    print(f"  {'-'*6} {'-'*8} {'-'*10} {'-'*12} {'-'*12} {'-'*10}")
    
    for level_name in ["n1", "n2", "n3", "n4"]:
        meta_list = level_metadata[level_name]
        match_pct = (match_counts[level_name] / n_evaluated * 100) if n_evaluated > 0 else 0
        
        if meta_list:
            avg_wr = np.mean([m["win_rate"] for m in meta_list])
            avg_count = np.mean([m["historical_count"] for m in meta_list])
            avg_move = np.mean([m["expected_move_pct"] for m in meta_list])
            avg_conf = np.mean([m["confidence"] for m in meta_list])
            print(f"  {level_name:<6} {match_pct:>7.1f}% {avg_wr:>10.4f} {avg_count:>12.1f} {avg_move:>11.4f}% {avg_conf:>10.4f}")
        else:
            print(f"  {level_name:<6} {match_pct:>7.1f}% {'N/A':>10} {'N/A':>12} {'N/A':>12} {'N/A':>10}")
    
    print(f"\n  WEIGHTED CONFIDENCE:")
    print(f"    Average:  {avg_weighted:.4f}")
    print(f"    Median:   {median_weighted:.4f}")
    print(f"    Min:      {min(confidences):.4f}")
    print(f"    Max:      {max(confidences):.4f}")
    print(f"    Std:      {np.std(confidences):.4f}")
    
    # Count how many candles hit >= 0.45
    above_45 = sum(1 for c in confidences if c >= 0.45)
    print(f"    >= 0.45:  {above_45} / {len(confidences)} ({above_45/len(confidences)*100:.1f}%)")
    above_30 = sum(1 for c in confidences if c >= 0.30)
    print(f"    >= 0.30:  {above_30} / {len(confidences)} ({above_30/len(confidences)*100:.1f}%)")
    
    # Print detailed N3 stats (the key level for 1m optimization)
    print(f"\n  N3 DETAILED STATS (key level for 1m):")
    n3_metas = level_metadata["n3"]
    if n3_metas:
        counts = [m["historical_count"] for m in n3_metas]
        wrs = [m["win_rate"] for m in n3_metas]
        moves = [m["expected_move_pct"] for m in n3_metas]
        print(f"    historical_count: min={min(counts)}, max={max(counts)}, mean={np.mean(counts):.1f}, median={np.median(counts):.1f}")
        print(f"    win_rate:         min={min(wrs):.4f}, max={max(wrs):.4f}, mean={np.mean(wrs):.4f}")
        print(f"    expected_move:    min={min(moves):.4f}%, max={max(moves):.4f}%, mean={np.mean(moves):.4f}%")
        
        # Distribution of counts
        count_buckets = {"<5": 0, "5-10": 0, "10-20": 0, "20-50": 0, "50-100": 0, ">100": 0}
        for c in counts:
            if c < 5: count_buckets["<5"] += 1
            elif c < 10: count_buckets["5-10"] += 1
            elif c < 20: count_buckets["10-20"] += 1
            elif c < 50: count_buckets["20-50"] += 1
            elif c < 100: count_buckets["50-100"] += 1
            else: count_buckets[">100"] += 1
        print(f"    count distribution: {count_buckets}")
    else:
        print(f"    NO N3 MATCHES FOUND")
    
    # Print N3 trie stats
    print(f"\n  TRIE STATS:")
    print(f"    N1 patterns: {engine.trie_n1.pattern_count}")
    print(f"    N2 patterns: {engine.trie_n2.pattern_count}")
    print(f"    N3 patterns: {engine.trie_n3.pattern_count}")
    n4_count = engine.trie_n4.pattern_count if hasattr(engine.trie_n4, 'pattern_count') else 0
    print(f"    N4 patterns: {n4_count}")
    
    # Sax config
    print(f"\n  CURRENT SAX CONFIG:")
    print(f"    N1: α_price={engine.sax_n1.alphabet_size}, W={engine.sax_n1.window_size}, P={engine.pl_n1}")
    if hasattr(engine.sax_n2, 'price_alphabet_size'):
        print(f"    N2: α_price={engine.sax_n2.price_alphabet_size}, α_vol={engine.sax_n2.volume_alphabet_size}, W={engine.sax_n2.window_size}, P={engine.pl_n2}")
    else:
        print(f"    N2: α={engine.sax_n2.alphabet_size}, W={engine.sax_n2.window_size}, P={engine.pl_n2}")
    if hasattr(engine.sax_n3, 'price_alphabet_size'):
        print(f"    N3: α_price={engine.sax_n3.price_alphabet_size}, α_vol={engine.sax_n3.volume_alphabet_size}, W={engine.sax_n3.window_size}, P={engine.pl_n3}")
    else:
        print(f"    N3: α={engine.sax_n3.alphabet_size}, W={engine.sax_n3.window_size}, P={engine.pl_n3}")
    if hasattr(engine.sax_n4, 'price_alphabet_size'):
        print(f"    N4: α_price={engine.sax_n4.price_alphabet_size}, α_vol={engine.sax_n4.volume_alphabet_size}, W={engine.sax_n4.window_size}, P={engine.pl_n4}")
    else:
        print(f"    N4: α={engine.sax_n4.alphabet_size}, W={engine.sax_n4.window_size}, P={engine.pl_n4}")
    if engine.sax_n5 is not None and hasattr(engine.sax_n5, 'price_alphabet_size'):
        print(f"    N5: α_price={engine.sax_n5.price_alphabet_size}, α_vol={engine.sax_n5.volume_alphabet_size}, W={engine.sax_n5.window_size}, P={engine.pl_n5}")
    
    print(f"{'='*80}\n")
    
    return {
        "avg_weighted_confidence": avg_weighted,
        "median_weighted_confidence": median_weighted,
        "n_evaluated": n_evaluated,
        "match_counts": match_counts,
        "level_metadata": level_metadata,
    }


# ============================================================
# STEP 4: Modify sax.py configuration
# ============================================================
def modify_sax_config(
    pattern_length_n3=None,
    pattern_length_n4=None,
    pattern_length_n5=None,
    window_n3_1m=None,
    window_n4_1m=None,
    window_n5_1m=None,
    alpha_vol_n3=None,
    alpha_vol_n4=None,
):
    """Modify sax.py LEVEL_PATTERN_CONFIG, LEVEL_WINDOW_CONFIG, and LEVEL_DUAL_ALPHA_CONFIG."""
    sax_path = os.path.join(SRC_PATH, "ppmt", "core", "sax.py")
    
    with open(sax_path, 'r') as f:
        content = f.read()
    
    # Modify LEVEL_PATTERN_CONFIG
    if pattern_length_n3 is not None:
        import re
        # Match the n3 line in LEVEL_PATTERN_CONFIG
        content = re.sub(
            r'("n3":\s*)\d+',
            f'\\g<1>{pattern_length_n3}',
            content,
            count=0  # Replace all occurrences
        )
    if pattern_length_n4 is not None:
        import re
        content = re.sub(
            r'("n4":\s*)\d+',
            f'\\g<1>{pattern_length_n4}',
            content,
            count=0
        )
    if pattern_length_n5 is not None:
        import re
        content = re.sub(
            r'("n5":\s*)\d+',
            f'\\g<1>{pattern_length_n5}',
            content,
            count=0
        )
    
    # Modify LEVEL_WINDOW_CONFIG for 1m
    if window_n3_1m is not None:
        import re
        # Find the "1m" line and replace n3 value
        # The line looks like: "1m":  {"n1": 60, "n2": 60, "n3": 20, "n4": 20, "n5": 20},
        old_1m_pattern = r'("1m":\s*\{[^}]*"n3":\s*)\d+'
        content = re.sub(old_1m_pattern, f'\\g<1>{window_n3_1m}', content)
    if window_n4_1m is not None:
        import re
        old_1m_pattern = r'("1m":\s*\{[^}]*"n4":\s*)\d+'
        content = re.sub(old_1m_pattern, f'\\g<1>{window_n4_1m}', content)
    if window_n5_1m is not None:
        import re
        old_1m_pattern = r'("1m":\s*\{[^}]*"n5":\s*)\d+'
        content = re.sub(old_1m_pattern, f'\\g<1>{window_n5_1m}', content)
    
    # Modify LEVEL_DUAL_ALPHA_CONFIG for n3 volume
    if alpha_vol_n3 is not None:
        import re
        # The n3 line: "n3": {"price": 4, "volume": 3},
        old_n3_vol = r'("n3":\s*\{"price":\s*\d+,\s*"volume":\s*)\d+'
        content = re.sub(old_n3_vol, f'\\g<1>{alpha_vol_n3}', content)
    if alpha_vol_n4 is not None:
        import re
        old_n4_vol = r'("n4":\s*\{"price":\s*\d+,\s*"volume":\s*)\d+'
        content = re.sub(old_n4_vol, f'\\g<1>{alpha_vol_n4}', content)
    
    with open(sax_path, 'w') as f:
        f.write(content)
    
    # Clear cached imports so next import picks up changes
    for mod_name in list(sys.modules.keys()):
        if 'ppmt.core.sax' in mod_name:
            del sys.modules[mod_name]
    
    logger.info(f"Modified sax.py with: P_n3={pattern_length_n3}, P_n4={pattern_length_n4}, P_n5={pattern_length_n5}, "
                f"W_n3_1m={window_n3_1m}, W_n4_1m={window_n4_1m}, W_n5_1m={window_n5_1m}, "
                f"α_vol_n3={alpha_vol_n3}, α_vol_n4={alpha_vol_n4}")


# ============================================================
# STEP 5: Rebuild engine with new config
# ============================================================
def rebuild_engine(df, btc_df, symbol="DOGE/USDT", asset_class="meme", timeframe="1m"):
    """Force reimport and rebuild engine with current sax.py config."""
    # Force reimport of all PPMT modules
    mods_to_del = [k for k in sys.modules.keys() if k.startswith('ppmt')]
    for m in mods_to_del:
        del sys.modules[m]
    
    # Re-import
    from ppmt.engine.ppmt import PPMT
    from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
    
    storage = PPMTStorage()
    
    engine = PPMT(
        symbol=symbol,
        asset_class=asset_class,
        dual_sax=True,
        min_confidence=0.08,
        timeframe=timeframe,
    )
    
    engine.attach_storage(storage)
    
    # Load shared pools
    n1_shared = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
    n2_shared = storage.load_trie(class_pool_key(asset_class), "n2")
    
    if n1_shared is not None:
        engine.trie_n1 = n1_shared
        logger.info(f"Loaded shared N1: {n1_shared.pattern_count} patterns")
    if n2_shared is not None:
        engine.trie_n2 = n2_shared
        logger.info(f"Loaded shared N2 ({asset_class}): {n2_shared.pattern_count} patterns")
    
    # Rebuild per-symbol tries (N3, N4) from training data
    count = engine.build(df)
    logger.info(f"Rebuilt {count} patterns from {len(df)} training candles")
    
    # Save updated tries
    storage.save_trie(UNIVERSAL_POOL_KEY, "n1", engine.trie_n1)
    storage.save_trie(class_pool_key(asset_class), "n2", engine.trie_n2)
    storage.save_trie(symbol, "n3", engine.trie_n3)
    storage.save_trie(symbol, "n4", engine.trie_n4)
    
    return engine, storage


# ============================================================
# MAIN: Execute all steps
# ============================================================
def main():
    print("=" * 80)
    print("  PPMT DOGE/USDT 1m — SAX Parameter Optimizer")
    print("  Goal: weighted_confidence >= 0.45")
    print("=" * 80)
    
    # --- Download Data ---
    print("\n[1/5] Downloading DOGE/USDT 1m data (5 days = ~7200 candles)...")
    doge_df = download_klines("DOGE/USDT", "1m", days=5)
    if doge_df is None or len(doge_df) < 1000:
        print("FATAL: Could not download DOGE/USDT 1m data")
        sys.exit(1)
    print(f"  Downloaded {len(doge_df)} DOGE/USDT 1m candles")
    
    print("\n[2/5] Downloading BTC/USDT 1m data (for N5 context)...")
    btc_df = download_klines("BTC/USDT", "1m", days=5)
    if btc_df is not None:
        print(f"  Downloaded {len(btc_df)} BTC/USDT 1m candles")
    else:
        print("  WARNING: Could not download BTC data, N5 will be disabled")
    
    # --- Split: Train (70%) / OOS (30%) ---
    train_size = int(len(doge_df) * 0.70)
    train_df = doge_df.iloc[:train_size].copy().reset_index(drop=True)
    oos_df = doge_df.iloc[train_size:].copy().reset_index(drop=True)
    
    btc_train_df = btc_df.iloc[:train_size].copy().reset_index(drop=True) if btc_df is not None else None
    btc_oos_df = btc_df.iloc[train_size:].copy().reset_index(drop=True) if btc_df is not None else None
    
    print(f"\n  Train: {len(train_df)} candles, OOS: {len(oos_df)} candles")
    
    # --- PASO 1: Baseline Diagnosis ---
    print("\n" + "=" * 80)
    print("  PASO 1: BASELINE DIAGNOSIS (current config)")
    print("=" * 80)
    
    engine, storage = rebuild_engine(train_df, btc_train_df)
    baseline_result = oos_diagnosis(engine, oos_df, btc_oos_df, label="BASELINE (current config)")
    
    if baseline_result is None:
        print("FATAL: Baseline OOS produced no results")
        sys.exit(1)
    
    baseline_conf = baseline_result["avg_weighted_confidence"]
    print(f"\n  >>> BASELINE weighted_confidence: {baseline_conf:.4f}")
    
    # --- PASO 2: Iterative Optimization ---
    attempts = [
        {
            "name": "INTENTO 1: pattern_length=3 for N3/N4/N5 in 1m",
            "changes": {
                "pattern_length_n3": 3,
                "pattern_length_n4": 3,
                "pattern_length_n5": 3,
            },
        },
        {
            "name": "INTENTO 2: P=3 + W_n3=30 for 1m",
            "changes": {
                "pattern_length_n3": 3,
                "pattern_length_n4": 3,
                "pattern_length_n5": 3,
                "window_n3_1m": 30,
            },
        },
        {
            "name": "INTENTO 3: P=3 + W_n3=30 + α_vol_n3=1 for 1m",
            "changes": {
                "pattern_length_n3": 3,
                "pattern_length_n4": 3,
                "pattern_length_n5": 3,
                "window_n3_1m": 30,
                "alpha_vol_n3": 1,
                "alpha_vol_n4": 1,
            },
        },
    ]
    
    results_table = []
    winning_config = None
    winning_conf = baseline_conf
    
    for attempt in attempts:
        print(f"\n{'='*80}")
        print(f"  PASO 2: {attempt['name']}")
        print(f"{'='*80}")
        
        # First reset sax.py to baseline, then apply changes
        # Reset by modifying back to baseline then applying new changes
        # Actually, we should modify from current state incrementally
        
        # Apply changes
        modify_sax_config(**attempt["changes"])
        
        # Rebuild engine
        engine, storage = rebuild_engine(train_df, btc_train_df)
        
        # Run OOS
        result = oos_diagnosis(engine, oos_df, btc_oos_df, label=attempt["name"])
        
        if result is None:
            results_table.append((attempt["name"], "FAILED", "N/A"))
            continue
        
        conf = result["avg_weighted_confidence"]
        results_table.append((attempt["name"], attempt["changes"], f"{conf:.4f}"))
        
        print(f"\n  >>> {attempt['name']}: weighted_confidence = {conf:.4f}")
        
        if conf >= 0.45:
            winning_config = attempt
            winning_conf = conf
            print(f"\n  *** WINNER! weighted_confidence = {conf:.4f} >= 0.45 ***")
            break
        elif conf > winning_conf:
            winning_conf = conf
            winning_config = attempt
            print(f"\n  New best (but < 0.45): {conf:.4f}")
    
    # --- Print Results Table ---
    print(f"\n{'='*80}")
    print(f"  OPTIMIZATION RESULTS TABLE")
    print(f"{'='*80}")
    print(f"  {'Attempt':<55} {'Confidence':>12}")
    print(f"  {'-'*55} {'-'*12}")
    print(f"  {'BASELINE (current config)':<55} {baseline_conf:>12.4f}")
    for name, changes, conf_str in results_table:
        print(f"  {name:<55} {conf_str:>12}")
    
    # --- PASO 3: Final Validation ---
    if winning_config is not None and winning_conf >= 0.45:
        print(f"\n{'='*80}")
        print(f"  PASO 3: FINAL VALIDATION — winning config confirmed")
        print(f"{'='*80}")
        print(f"  Winning attempt: {winning_config['name']}")
        print(f"  weighted_confidence: {winning_conf:.4f}")
    elif winning_conf < 0.45:
        print(f"\n{'='*80}")
        print(f"  WARNING: No attempt achieved >= 0.45 confidence")
        print(f"  Best result: {winning_conf:.4f}")
        if winning_config:
            print(f"  Best attempt: {winning_config['name']}")
        print(f"  The winning config will be the best one found.")
    
    # Print the final sax.py diff
    print(f"\n{'='*80}")
    print(f"  FINAL SAX.PY CONFIGURATION")
    print(f"{'='*80}")
    
    # Read current sax.py and print relevant sections
    sax_path = os.path.join(SRC_PATH, "ppmt", "core", "sax.py")
    with open(sax_path, 'r') as f:
        lines = f.readlines()
    
    # Print LEVEL_PATTERN_CONFIG section
    print("\n  LEVEL_PATTERN_CONFIG:")
    in_section = False
    for line in lines:
        if "LEVEL_PATTERN_CONFIG" in line and "=" in line:
            in_section = True
        if in_section:
            print(f"    {line.rstrip()}")
            if line.strip() == "}":
                in_section = False
                break
    
    # Print LEVEL_WINDOW_CONFIG "1m" section
    print("\n  LEVEL_WINDOW_CONFIG (1m):")
    in_section = False
    for line in lines:
        if "LEVEL_WINDOW_CONFIG" in line and "=" in line:
            in_section = True
        if in_section:
            if '"1m"' in line or 'in_section' in line or line.strip().startswith('"1m"'):
                print(f"    {line.rstrip()}")
            if line.strip() == "}" and in_section:
                in_section = False
                break
    
    # Print LEVEL_DUAL_ALPHA_CONFIG
    print("\n  LEVEL_DUAL_ALPHA_CONFIG:")
    in_section = False
    for line in lines:
        if "LEVEL_DUAL_ALPHA_CONFIG" in line and "=" in line:
            in_section = True
        if in_section:
            print(f"    {line.rstrip()}")
            if line.strip() == "}":
                in_section = False
                break
    
    print(f"\n{'='*80}")
    print(f"  DONE")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
