#!/usr/bin/env python3
"""
PPMT — DOGE/USDT 1m SAX Parameter Optimizer v2

Diagnosis showed N3 avg historical_count=1.0 (catastrophically sparse).
Root cause: dual encoder (price=4, vol=3) = 12 effective symbols.
12^4=20,736 possible patterns with only ~5000 training candles → ~1 obs/pattern.

Strategy: Reduce effective alphabet to increase pattern density.
Key insight from N1's success: price-only α=3 → 243 max patterns → 45 obs/node → conf>0.4

This script tests configurations that make N3/N4 denser by:
1. Reducing pattern_length (3 instead of 4)
2. Reducing volume alpha or making N3/N4 price-only (volume=0)
3. Using smaller windows for 1m N3/N4 (more symbols from same data)
"""

import sys
import os
import json
import time
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_PATH = os.path.join(PROJECT_ROOT, 'src')
sys.path.insert(0, SRC_PATH)
os.environ['PYTHONPATH'] = SRC_PATH

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger("doge_opt_v2")
logger.setLevel(logging.INFO)

# ============================================================
# Data Download (direct Binance API with Bybit fallback)
# ============================================================
def download_klines(symbol: str, interval: str = "1m", days: int = 15):
    """Download OHLCV data from Binance with Bybit fallback."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
    import json as _json
    
    # Try Binance first
    try:
        df = _download_binance(symbol, interval, days)
        if df is not None and len(df) > 100:
            return df
    except Exception as e:
        logger.warning(f"Binance failed: {e}")
    
    # Fallback to Bybit
    try:
        df = _download_bybit(symbol, interval, days)
        if df is not None and len(df) > 100:
            return df
    except Exception as e:
        logger.warning(f"Bybit failed: {e}")
    
    return None


def _download_binance(symbol, interval, days):
    from urllib.request import Request, urlopen
    import json as _json
    
    base_url = "https://api.binance.com/api/v3/klines"
    binance_symbol = symbol.replace("/", "")
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)
    
    all_candles = []
    current_start = start_ms
    
    while current_start < end_ms:
        url = f"{base_url}?symbol={binance_symbol}&interval={interval}&startTime={current_start}&limit=1000"
        req = Request(url, headers={"User-Agent": "PPMT/0.50.0"})
        try:
            with urlopen(req, timeout=30) as resp:
                data = _json.loads(resp.read().decode())
        except Exception:
            break
        
        if not data:
            break
        
        for c in data:
            all_candles.append({
                "timestamp": c[0], "open": float(c[1]), "high": float(c[2]),
                "low": float(c[3]), "close": float(c[4]), "volume": float(c[5]),
            })
        current_start = data[-1][0] + 1
        time.sleep(0.12)
    
    if not all_candles:
        return None
    df = pd.DataFrame(all_candles)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def _download_bybit(symbol, interval, days):
    from urllib.request import Request, urlopen
    import json as _json
    
    base_url = "https://api.bybit.com/v5/market/kline"
    bybit_symbol = symbol.replace("/", "")
    interval_map = {"1m": "1", "5m": "5", "15m": "15"}
    
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)
    
    all_candles = []
    current_end = end_ms
    
    while current_end > start_ms:
        url = f"{base_url}?category=spot&symbol={bybit_symbol}&interval={interval_map.get(interval, '1')}&end={current_end}&limit=200"
        req = Request(url, headers={"User-Agent": "PPMT/0.50.0"})
        try:
            with urlopen(req, timeout=30) as resp:
                resp_data = _json.loads(resp.read().decode())
        except Exception:
            break
        
        if resp_data.get("retCode") != 0 or not resp_data.get("result", {}).get("list"):
            break
        
        for c in resp_data["result"]["list"]:
            all_candles.append({
                "timestamp": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                "low": float(c[3]), "close": float(c[4]), "volume": float(c[5]),
            })
        oldest_ts = min(c[0] for c in resp_data["result"]["list"])
        current_end = oldest_ts - 1
        if oldest_ts <= start_ms:
            break
        time.sleep(0.15)
    
    if not all_candles:
        return None
    df = pd.DataFrame(all_candles)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["timestamp"], keep="first")
    return df.sort_values("timestamp").reset_index(drop=True)


# ============================================================
# SAX Config Management — Direct file editing
# ============================================================
SAX_PATH = os.path.join(SRC_PATH, "ppmt", "core", "sax.py")

# Baseline config (original values before any modifications)
BASELINE_CONFIG = {
    "LEVEL_PATTERN_CONFIG": {"n1": 5, "n2": 5, "n3": 4, "n4": 4, "n5": 4},
    "LEVEL_WINDOW_1M": {"n1": 60, "n2": 60, "n3": 20, "n4": 20, "n5": 20},
    "LEVEL_DUAL_ALPHA_N3": {"price": 4, "volume": 3},
    "LEVEL_DUAL_ALPHA_N4": {"price": 4, "volume": 3},
}


def write_sax_config(config: dict):
    """Write specific config values to sax.py by direct line replacement."""
    with open(SAX_PATH, 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    in_pattern_config = False
    in_window_config = False
    in_dual_alpha_config = False
    in_1m_line = False
    
    for line in lines:
        stripped = line.strip()
        
        # LEVEL_PATTERN_CONFIG
        if stripped.startswith("LEVEL_PATTERN_CONFIG"):
            in_pattern_config = True
        if in_pattern_config:
            if '"n3":' in stripped:
                val = config.get("pattern_n3", BASELINE_CONFIG["LEVEL_PATTERN_CONFIG"]["n3"])
                line = line.replace(stripped, f'"n3": {val},')
            elif '"n4":' in stripped:
                val = config.get("pattern_n4", BASELINE_CONFIG["LEVEL_PATTERN_CONFIG"]["n4"])
                line = line.replace(stripped, f'"n4": {val},')
            elif '"n5":' in stripped:
                val = config.get("pattern_n5", BASELINE_CONFIG["LEVEL_PATTERN_CONFIG"]["n5"])
                line = line.replace(stripped, f'"n5": {val},')
            if stripped == "}":
                in_pattern_config = False
        
        # LEVEL_WINDOW_CONFIG — "1m" line
        if '"1m"' in stripped and "n1" in stripped:
            in_1m_line = True
            w = config.get("window_1m", BASELINE_CONFIG["LEVEL_WINDOW_1M"])
            line = f'    "1m":  {{"n1": {w["n1"]}, "n2": {w["n2"]}, "n3": {w["n3"]}, "n4": {w["n4"]}, "n5": {w["n5"]}}},  # v0.48.0 (FASE 2B): N5=BTC context, 1m only\n'
            in_1m_line = False
        
        # LEVEL_DUAL_ALPHA_CONFIG — n3/n4 lines
        if '"n3":' in stripped and '"price"' in stripped:
            alpha = config.get("dual_alpha_n3", BASELINE_CONFIG["LEVEL_DUAL_ALPHA_N3"])
            line = f'    "n3": {{"price": {alpha["price"]}, "volume": {alpha["volume"]}}},\n'
        if '"n4":' in stripped and '"price"' in stripped:
            alpha = config.get("dual_alpha_n4", BASELINE_CONFIG["LEVEL_DUAL_ALPHA_N4"])
            line = f'    "n4": {{"price": {alpha["price"]}, "volume": {alpha["volume"]}}},\n'
        
        new_lines.append(line)
    
    with open(SAX_PATH, 'w') as f:
        f.writelines(new_lines)
    
    # Clear cached imports
    for mod_name in list(sys.modules.keys()):
        if 'ppmt' in mod_name:
            del sys.modules[mod_name]
    
    logger.info(f"Written sax.py config: {config}")


def reset_sax_to_baseline():
    """Reset sax.py to baseline configuration."""
    write_sax_config({})


# ============================================================
# Engine Build & OOS Evaluation
# ============================================================
def build_and_evaluate(train_df, oos_df, btc_train_df, btc_oos_df, label=""):
    """Build PPMT engine from training data, evaluate on OOS data."""
    # Force reimport
    for m in list(sys.modules.keys()):
        if m.startswith('ppmt'):
            del sys.modules[m]
    
    from ppmt.engine.ppmt import PPMT
    from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
    
    storage = PPMTStorage()
    
    engine = PPMT(
        symbol="DOGE/USDT",
        asset_class="meme",
        dual_sax=True,
        min_confidence=0.08,
        timeframe="1m",
    )
    engine.attach_storage(storage)
    
    # Load shared pools
    n1 = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
    n2 = storage.load_trie(class_pool_key("meme"), "n2")
    if n1: engine.trie_n1 = n1
    if n2: engine.trie_n2 = n2
    
    # Build
    count = engine.build(train_df)
    
    # Save
    storage.save_trie(UNIVERSAL_POOL_KEY, "n1", engine.trie_n1)
    storage.save_trie(class_pool_key("meme"), "n2", engine.trie_n2)
    storage.save_trie("DOGE/USDT", "n3", engine.trie_n3)
    storage.save_trie("DOGE/USDT", "n4", engine.trie_n4)
    
    # Compute min candles needed
    min_candles = max(
        engine.sax_n1.window_size * engine.pl_n1,
        engine.sax_n2.window_size * engine.pl_n2,
        engine.sax_n3.window_size * engine.pl_n3,
        engine.sax_n4.window_size * engine.pl_n4,
    )
    
    # OOS walk-forward
    results = []
    level_meta = {"n1": [], "n2": [], "n3": [], "n4": []}
    match_counts = {"n1": 0, "n2": 0, "n3": 0, "n4": 0}
    n_eval = 0
    
    for i in range(min_candles, len(oos_df)):
        recent_df = oos_df.iloc[max(0, i - min_candles - 50):i+1].copy()
        if len(recent_df) < min_candles:
            continue
        
        btc_recent = None
        if btc_oos_df is not None and len(btc_oos_df) > 20:
            btc_idx = min(i, len(btc_oos_df) - 1)
            btc_recent = btc_oos_df.iloc[max(0, btc_idx - 100):btc_idx+1].copy()
        
        try:
            result = engine.match_raw(
                current_symbols=[],
                current_price=oos_df.iloc[i]["close"],
                recent_candles=recent_df,
                btc_recent_candles=btc_recent,
            )
        except Exception:
            continue
        
        n_eval += 1
        
        for lname, mresult in [("n1", result.n1_match), ("n2", result.n2_match),
                                ("n3", result.n3_match), ("n4", result.n4_match)]:
            if mresult and mresult.node:
                meta = mresult.node.metadata
                match_counts[lname] += 1
                level_meta[lname].append({
                    "win_rate": getattr(meta, 'win_rate', 0.0),
                    "historical_count": getattr(meta, 'historical_count', 0),
                    "expected_move_pct": getattr(meta, 'expected_move_pct', 0.0),
                    "confidence": getattr(meta, 'confidence', 0.0),
                })
        
        results.append({
            "weighted_confidence": result.weighted_confidence,
            "n1": result.n1_confidence, "n2": result.n2_confidence,
            "n3": result.n3_confidence, "n4": result.n4_confidence,
        })
        
        if n_eval >= 500:
            break
    
    if not results:
        return None
    
    confs = [r["weighted_confidence"] for r in results]
    avg_wc = np.mean(confs)
    
    # Print summary
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"  OOS: {n_eval} candles | N3 patterns: {engine.trie_n3.pattern_count} | N4 patterns: {engine.trie_n4.pattern_count if hasattr(engine.trie_n4, 'pattern_count') else 0}")
    print(f"{'='*80}")
    
    print(f"  {'Level':<6} {'Match%':>8} {'Avg WR':>10} {'Avg Count':>12} {'Avg Move%':>12} {'Avg Conf':>10}")
    print(f"  {'-'*58}")
    for ln in ["n1", "n2", "n3", "n4"]:
        ml = level_meta[ln]
        mp = match_counts[ln] / n_eval * 100 if n_eval > 0 else 0
        if ml:
            print(f"  {ln:<6} {mp:>7.1f}% {np.mean([m['win_rate'] for m in ml]):>10.4f} "
                  f"{np.mean([m['historical_count'] for m in ml]):>12.1f} "
                  f"{np.mean([m['expected_move_pct'] for m in ml]):>11.4f}% "
                  f"{np.mean([m['confidence'] for m in ml]):>10.4f}")
        else:
            print(f"  {ln:<6} {mp:>7.1f}% {'N/A':>10} {'N/A':>12} {'N/A':>12} {'N/A':>10}")
    
    print(f"\n  WEIGHTED CONFIDENCE: {avg_wc:.4f}  (median={np.median(confs):.4f})")
    print(f"    >=0.45: {sum(1 for c in confs if c >= 0.45)}/{len(confs)} | >=0.30: {sum(1 for c in confs if c >= 0.30)}/{len(confs)}")
    
    # SAX config summary
    if hasattr(engine.sax_n3, 'price_alphabet_size'):
        eff_alpha = engine.sax_n3.price_alphabet_size * max(1, engine.sax_n3.volume_alphabet_size)
    else:
        eff_alpha = engine.sax_n3.alphabet_size
    pl_n3 = engine.pl_n3
    max_patterns = eff_alpha ** pl_n3
    print(f"  N3 effective: α={eff_alpha}, P={pl_n3}, max_patterns={max_patterns}")
    
    # N3 detailed
    n3m = level_meta["n3"]
    if n3m:
        counts = [m["historical_count"] for m in n3m]
        print(f"  N3 avg obs/node: {np.mean(counts):.1f} (min={min(counts)}, max={max(counts)})")
    
    return {"avg_wc": avg_wc, "median_wc": np.median(confs), "n3_meta": level_meta["n3"]}


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 80)
    print("  PPMT DOGE/USDT 1m — Aggressive SAX Parameter Optimizer v2")
    print("  Goal: weighted_confidence >= 0.45")
    print("=" * 80)
    
    # Download data (15 days for more training data)
    print("\n[1] Downloading DOGE/USDT 1m (15 days)...")
    doge_df = download_klines("DOGE/USDT", "1m", days=15)
    if doge_df is None or len(doge_df) < 2000:
        print("FATAL: Could not download DOGE data")
        sys.exit(1)
    print(f"  Got {len(doge_df)} DOGE candles")
    
    print("[2] Downloading BTC/USDT 1m (15 days)...")
    btc_df = download_klines("BTC/USDT", "1m", days=15)
    if btc_df is not None and len(btc_df) > 0:
        print(f"  Got {len(btc_df)} BTC candles")
    else:
        btc_df = None
        print("  WARNING: No BTC data, N5 disabled")
    
    # Train/test split (70/30)
    train_size = int(len(doge_df) * 0.70)
    train_df = doge_df.iloc[:train_size].reset_index(drop=True)
    oos_df = doge_df.iloc[train_size:].reset_index(drop=True)
    btc_train = btc_df.iloc[:train_size].reset_index(drop=True) if btc_df is not None else None
    btc_oos = btc_df.iloc[train_size:].reset_index(drop=True) if btc_df is not None else None
    print(f"  Train: {len(train_df)}, OOS: {len(oos_df)}")
    
    # ============================================================
    # PASO 1: BASELINE DIAGNOSIS
    # ============================================================
    print(f"\n{'#'*80}")
    print(f"  PASO 1: BASELINE DIAGNOSIS")
    print(f"{'#'*80}")
    reset_sax_to_baseline()
    baseline = build_and_evaluate(train_df, oos_df, btc_train, btc_oos, "BASELINE")
    
    if baseline is None:
        print("FATAL: Baseline failed")
        sys.exit(1)
    
    # ============================================================
    # PASO 2: OPTIMIZATION ATTEMPTS
    # ============================================================
    
    # Define attempts — each is a config dict + description
    attempts = [
        {
            "label": "INTENTO 1: N3/N4/N5 P=3 (12^3=1728, denser)",
            "config": {
                "pattern_n3": 3, "pattern_n4": 3, "pattern_n5": 3,
            },
        },
        {
            "label": "INTENTO 2: N3/N4/N5 P=3 + W_n3=30",
            "config": {
                "pattern_n3": 3, "pattern_n4": 3, "pattern_n5": 3,
                "window_1m": {"n1": 60, "n2": 60, "n3": 30, "n4": 20, "n5": 20},
            },
        },
        {
            "label": "INTENTO 3: N3/N4 price-only α_p=3 vol=0 P=3 (3^3=27 ultra-dense)",
            "config": {
                "pattern_n3": 3, "pattern_n4": 3, "pattern_n5": 3,
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "INTENTO 4: price-only α=3 vol=0 P=3 W_n3=5 (max density)",
            "config": {
                "pattern_n3": 3, "pattern_n4": 3, "pattern_n5": 3,
                "window_1m": {"n1": 60, "n2": 60, "n3": 5, "n4": 5, "n5": 5},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "INTENTO 5: price-only α=3 vol=0 P=3 W_n3=10",
            "config": {
                "pattern_n3": 3, "pattern_n4": 3, "pattern_n5": 3,
                "window_1m": {"n1": 60, "n2": 60, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "INTENTO 6: price-only α=3 vol=0 P=3 W_n3=10 W_n2=20 (boost N2 too)",
            "config": {
                "pattern_n3": 3, "pattern_n4": 3, "pattern_n5": 3,
                "window_1m": {"n1": 60, "n2": 20, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "INTENTO 7: price-only α=3 vol=0 P=2 W=5 (9 patterns, extreme density)",
            "config": {
                "pattern_n3": 2, "pattern_n4": 2, "pattern_n5": 2,
                "window_1m": {"n1": 60, "n2": 60, "n3": 5, "n4": 5, "n5": 5},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
    ]
    
    results_table = [("BASELINE", baseline["avg_wc"])]
    best_conf = baseline["avg_wc"]
    best_attempt_idx = -1
    
    for idx, attempt in enumerate(attempts):
        print(f"\n{'#'*80}")
        print(f"  PASO 2: {attempt['label']}")
        print(f"{'#'*80}")
        
        # Reset to baseline first, then apply changes
        reset_sax_to_baseline()
        write_sax_config(attempt["config"])
        
        result = build_and_evaluate(train_df, oos_df, btc_train, btc_oos, attempt["label"])
        
        if result is None:
            results_table.append((attempt["label"], "FAILED"))
            continue
        
        wc = result["avg_wc"]
        results_table.append((attempt["label"], wc))
        
        if wc > best_conf:
            best_conf = wc
            best_attempt_idx = idx
            print(f"\n  *** NEW BEST: {wc:.4f} ***")
        
        if wc >= 0.45:
            print(f"\n  *** WINNER! weighted_confidence = {wc:.4f} >= 0.45 ***")
            break
    
    # ============================================================
    # RESULTS TABLE
    # ============================================================
    print(f"\n{'='*80}")
    print(f"  OPTIMIZATION RESULTS TABLE")
    print(f"{'='*80}")
    print(f"  {'Attempt':<70} {'WC':>8}")
    print(f"  {'-'*70} {'-'*8}")
    for name, wc in results_table:
        if isinstance(wc, float):
            marker = " <<<" if wc == best_conf else ""
            print(f"  {name:<70} {wc:>8.4f}{marker}")
        else:
            print(f"  {name:<70} {'FAILED':>8}")
    
    # ============================================================
    # PASO 3: Apply winning config
    # ============================================================
    if best_attempt_idx >= 0:
        winning = attempts[best_attempt_idx]
        print(f"\n{'='*80}")
        print(f"  PASO 3: WINNING CONFIG")
        print(f"{'='*80}")
        print(f"  {winning['label']}")
        print(f"  weighted_confidence: {best_conf:.4f}")
        
        # Reset and apply winning config
        reset_sax_to_baseline()
        write_sax_config(winning["config"])
        
        # Print final sax.py sections
        print(f"\n  Final sax.py configuration:")
        with open(SAX_PATH, 'r') as f:
            lines = f.readlines()
        
        for section_name in ["LEVEL_PATTERN_CONFIG", "LEVEL_WINDOW_CONFIG", "LEVEL_DUAL_ALPHA_CONFIG"]:
            print(f"\n  {section_name}:")
            in_section = False
            for line in lines:
                if section_name in line and "=" in line and not line.strip().startswith("#"):
                    in_section = True
                if in_section:
                    print(f"    {line.rstrip()}")
                    if line.strip() == "}":
                        in_section = False
                        break
    
    print(f"\n{'='*80}")
    print(f"  DONE. Best weighted_confidence: {best_conf:.4f}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
