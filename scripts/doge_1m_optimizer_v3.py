#!/usr/bin/env python3
"""
PPMT — DOGE/USDT 1m SAX Parameter Optimizer v3

After won-fix: baseline 0.259 → best 0.317. 
Bottleneck: N2 at 60% weight (meme profile) with 0.32 confidence.
Strategy: 
1. Try N2 P=3 (27 patterns, denser, global change that helps all TFs)
2. Try 30 days of training data
3. Try combinations that maximize N3/N4 confidence while keeping N2 high
"""

import sys, os, json, time, logging
import numpy as np, pandas as pd
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_PATH = os.path.join(PROJECT_ROOT, 'src')
sys.path.insert(0, SRC_PATH)
os.environ['PYTHONPATH'] = SRC_PATH

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("v3")
logger.setLevel(logging.INFO)

# Data download functions (same as v2)
def download_klines(symbol, interval="1m", days=30):
    from urllib.request import Request, urlopen
    import json as _json
    try:
        df = _binance(symbol, interval, days)
        if df is not None and len(df) > 100: return df
    except: pass
    try:
        df = _bybit(symbol, interval, days)
        if df is not None and len(df) > 100: return df
    except: pass
    return None

def _binance(symbol, interval, days):
    from urllib.request import Request, urlopen
    import json as _json
    url_base = "https://api.binance.com/api/v3/klines"
    bsym = symbol.replace("/", "")
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)
    all_c, cur = [], start_ms
    while cur < end_ms:
        url = f"{url_base}?symbol={bsym}&interval={interval}&startTime={cur}&limit=1000"
        try:
            with urlopen(Request(url, headers={"User-Agent": "PPMT/0.51.0"}), timeout=30) as r:
                data = _json.loads(r.read().decode())
        except: break
        if not data: break
        for c in data:
            all_c.append({"timestamp": c[0], "open": float(c[1]), "high": float(c[2]),
                          "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])})
        cur = data[-1][0] + 1
        time.sleep(0.12)
    if not all_c: return None
    df = pd.DataFrame(all_c)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)

def _bybit(symbol, interval, days):
    from urllib.request import Request, urlopen
    import json as _json
    url_base = "https://api.bybit.com/v5/market/kline"
    bsym = symbol.replace("/", "")
    imap = {"1m": "1", "5m": "5", "15m": "15"}
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)
    all_c, cur_end = [], end_ms
    while cur_end > start_ms:
        url = f"{url_base}?category=spot&symbol={bsym}&interval={imap.get(interval,'1')}&end={cur_end}&limit=200"
        try:
            with urlopen(Request(url, headers={"User-Agent": "PPMT/0.51.0"}), timeout=30) as r:
                rd = _json.loads(r.read().decode())
        except: break
        if rd.get("retCode") != 0 or not rd.get("result", {}).get("list"): break
        for c in rd["result"]["list"]:
            all_c.append({"timestamp": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                          "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])})
        oldest = min(c[0] for c in rd["result"]["list"])
        cur_end = oldest - 1
        if oldest <= start_ms: break
        time.sleep(0.15)
    if not all_c: return None
    df = pd.DataFrame(all_c)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["timestamp"], keep="first")
    return df.sort_values("timestamp").reset_index(drop=True)

# SAX config management
SAX_PATH = os.path.join(SRC_PATH, "ppmt", "core", "sax.py")
BASELINE = {
    "pattern_n1": 5, "pattern_n2": 5, "pattern_n3": 4, "pattern_n4": 4, "pattern_n5": 4,
    "window_1m": {"n1": 60, "n2": 60, "n3": 20, "n4": 20, "n5": 20},
    "dual_alpha_n3": {"price": 4, "volume": 3},
    "dual_alpha_n4": {"price": 4, "volume": 3},
}

def write_sax_config(config):
    with open(SAX_PATH, 'r') as f: lines = f.readlines()
    new_lines = []
    for line in lines:
        s = line.strip()
        # Pattern config
        if '"n1":' in s and 'LEVEL_PATTERN' not in s and 'window' not in s.lower():
            v = config.get("pattern_n1", BASELINE["pattern_n1"])
            line = line.replace(s, f'"n1": {v},')
        elif '"n2":' in s and 'LEVEL_PATTERN' not in s and 'window' not in s.lower():
            v = config.get("pattern_n2", BASELINE["pattern_n2"])
            line = line.replace(s, f'"n2": {v},')
        elif '"n3":' in s and '"price"' not in s and 'window' not in s.lower():
            v = config.get("pattern_n3", BASELINE["pattern_n3"])
            line = line.replace(s, f'"n3": {v},')
        elif '"n4":' in s and '"price"' not in s and 'window' not in s.lower():
            v = config.get("pattern_n4", BASELINE["pattern_n4"])
            line = line.replace(s, f'"n4": {v},')
        elif '"n5":' in s and '"price"' not in s and 'window' not in s.lower():
            v = config.get("pattern_n5", BASELINE["pattern_n5"])
            line = line.replace(s, f'"n5": {v},')
        # 1m window
        if '"1m"' in s and "n1" in s:
            w = config.get("window_1m", BASELINE["window_1m"])
            line = f'    "1m":  {{"n1": {w["n1"]}, "n2": {w["n2"]}, "n3": {w["n3"]}, "n4": {w["n4"]}, "n5": {w["n5"]}}},  # v0.51.0: tuned for 1m density\n'
        # Dual alpha
        if '"n3":' in s and '"price"' in s:
            a = config.get("dual_alpha_n3", BASELINE["dual_alpha_n3"])
            line = f'    "n3": {{"price": {a["price"]}, "volume": {a["volume"]}}},\n'
        if '"n4":' in s and '"price"' in s:
            a = config.get("dual_alpha_n4", BASELINE["dual_alpha_n4"])
            line = f'    "n4": {{"price": {a["price"]}, "volume": {a["volume"]}}},\n'
        new_lines.append(line)
    with open(SAX_PATH, 'w') as f: f.writelines(new_lines)
    for m in list(sys.modules.keys()):
        if 'ppmt' in m: del sys.modules[m]

# Build & Evaluate
def build_and_evaluate(train_df, oos_df, btc_train, btc_oos, label=""):
    for m in list(sys.modules.keys()):
        if m.startswith('ppmt'): del sys.modules[m]
    from ppmt.engine.ppmt import PPMT
    from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
    
    storage = PPMTStorage()
    engine = PPMT(symbol="DOGE/USDT", asset_class="meme", dual_sax=True,
                  min_confidence=0.08, timeframe="1m")
    engine.attach_storage(storage)
    
    n1 = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
    n2 = storage.load_trie(class_pool_key("meme"), "n2")
    if n1: engine.trie_n1 = n1
    if n2: engine.trie_n2 = n2
    
    count = engine.build(train_df)
    storage.save_trie(UNIVERSAL_POOL_KEY, "n1", engine.trie_n1)
    storage.save_trie(class_pool_key("meme"), "n2", engine.trie_n2)
    storage.save_trie("DOGE/USDT", "n3", engine.trie_n3)
    storage.save_trie("DOGE/USDT", "n4", engine.trie_n4)
    
    min_candles = max(engine.sax_n1.window_size * engine.pl_n1,
                      engine.sax_n2.window_size * engine.pl_n2,
                      engine.sax_n3.window_size * engine.pl_n3,
                      engine.sax_n4.window_size * engine.pl_n4)
    
    results, level_meta, match_counts = [], {"n1":[],"n2":[],"n3":[],"n4":[]}, {"n1":0,"n2":0,"n3":0,"n4":0}
    n_eval = 0
    
    for i in range(min_candles, len(oos_df)):
        recent_df = oos_df.iloc[max(0, i-min_candles-50):i+1].copy()
        if len(recent_df) < min_candles: continue
        btc_recent = None
        if btc_oos is not None and len(btc_oos) > 20:
            bi = min(i, len(btc_oos)-1)
            btc_recent = btc_oos.iloc[max(0, bi-100):bi+1].copy()
        try:
            r = engine.match_raw(current_symbols=[], current_price=oos_df.iloc[i]["close"],
                                 recent_candles=recent_df, btc_recent_candles=btc_recent)
        except: continue
        n_eval += 1
        for ln, mr in [("n1",r.n1_match),("n2",r.n2_match),("n3",r.n3_match),("n4",r.n4_match)]:
            if mr and mr.node:
                m = mr.node.metadata
                match_counts[ln] += 1
                level_meta[ln].append({"wr": getattr(m,'win_rate',0), "hc": getattr(m,'historical_count',0),
                                       "em": getattr(m,'expected_move_pct',0), "cf": getattr(m,'confidence',0)})
        results.append({"wc": r.weighted_confidence, "n1": r.n1_confidence, "n2": r.n2_confidence,
                         "n3": r.n3_confidence, "n4": r.n4_confidence})
        if n_eval >= 500: break
    
    if not results: return None
    wcs = [r["wc"] for r in results]
    avg = np.mean(wcs)
    
    # Print weights used
    w = engine.weights
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"  OOS: {n_eval} | N3: {engine.trie_n3.pattern_count} pat | N4: {engine.trie_n4.pattern_count if hasattr(engine.trie_n4,'pattern_count') else 0} pat")
    print(f"  Weights: N1={w.n1_universal:.0%} N2={w.n2_asset_class:.0%} N3={w.n3_per_asset:.0%} N4={w.n4_per_asset_regime:.0%}")
    print(f"{'='*80}")
    print(f"  {'Lvl':<5} {'Mch%':>6} {'WR':>7} {'Count':>8} {'Move%':>9} {'Conf':>7}")
    for ln in ["n1","n2","n3","n4"]:
        ml = level_meta[ln]
        mp = match_counts[ln]/n_eval*100 if n_eval else 0
        if ml:
            print(f"  {ln:<5} {mp:>5.0f}% {np.mean([m['wr'] for m in ml]):>7.3f} "
                  f"{np.mean([m['hc'] for m in ml]):>8.1f} {np.mean([m['em'] for m in ml]):>8.4f}% "
                  f"{np.mean([m['cf'] for m in ml]):>7.4f}")
        else:
            print(f"  {ln:<5} {mp:>5.0f}% {'N/A':>7} {'N/A':>8} {'N/A':>9} {'N/A':>7}")
    
    print(f"\n  WC: {avg:.4f} (med={np.median(wcs):.4f}) | >=0.45: {sum(1 for c in wcs if c>=0.45)}/500 | >=0.30: {sum(1 for c in wcs if c>=0.30)}/500")
    
    if hasattr(engine.sax_n3, 'price_alphabet_size'):
        ea = engine.sax_n3.price_alphabet_size * max(1, engine.sax_n3.volume_alphabet_size)
    else:
        ea = engine.sax_n3.alphabet_size
    print(f"  N3: α_eff={ea}, P={engine.pl_n3}, max_pat={ea**engine.pl_n3}")
    if level_meta["n3"]:
        cs = [m["hc"] for m in level_meta["n3"]]
        print(f"  N3 obs/node: avg={np.mean(cs):.1f} min={min(cs)} max={max(cs)}")
    
    return {"avg_wc": avg, "n3_meta": level_meta["n3"]}

def main():
    print("=" * 80)
    print("  PPMT DOGE/USDT 1m — Optimizer v3 (won-fix + aggressive N2/N3/N4)")
    print("=" * 80)
    
    print("\n[1] Downloading DOGE/USDT 1m (30 days)...")
    doge = download_klines("DOGE/USDT", "1m", days=30)
    if doge is None or len(doge) < 5000:
        print("FATAL: No DOGE data"); sys.exit(1)
    print(f"  Got {len(doge)} DOGE candles")
    
    print("[2] Downloading BTC/USDT 1m (30 days)...")
    btc = download_klines("BTC/USDT", "1m", days=30)
    if btc is not None and len(btc) > 0:
        print(f"  Got {len(btc)} BTC candles")
    else:
        btc = None
    
    train_sz = int(len(doge) * 0.70)
    train_df = doge.iloc[:train_sz].reset_index(drop=True)
    oos_df = doge.iloc[train_sz:].reset_index(drop=True)
    btc_tr = btc.iloc[:train_sz].reset_index(drop=True) if btc is not None else None
    btc_oos = btc.iloc[train_sz:].reset_index(drop=True) if btc is not None else None
    print(f"  Train: {len(train_df)}, OOS: {len(oos_df)}")
    
    # Attempts
    attempts = [
        {
            "label": "A: price-only N3/N4 P=3 W=10 (won-fix baseline)",
            "config": {
                "pattern_n3": 3, "pattern_n4": 3, "pattern_n5": 3,
                "window_1m": {"n1": 60, "n2": 60, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "B: +N2 P=3 (27 pat, 8x denser N2)",
            "config": {
                "pattern_n2": 3, "pattern_n3": 3, "pattern_n4": 3, "pattern_n5": 3,
                "window_1m": {"n1": 60, "n2": 60, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "C: +N2 P=3 + W_n2=30 for 1m",
            "config": {
                "pattern_n2": 3, "pattern_n3": 3, "pattern_n4": 3, "pattern_n5": 3,
                "window_1m": {"n1": 60, "n2": 30, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "D: +N2 P=3 + W_n2=10 for 1m (max N2 symbols)",
            "config": {
                "pattern_n2": 3, "pattern_n3": 3, "pattern_n4": 3, "pattern_n5": 3,
                "window_1m": {"n1": 60, "n2": 10, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "E: N2 P=3 + N3 P=2 (9 ultra-dense N3) + W_n3=5",
            "config": {
                "pattern_n2": 3, "pattern_n3": 2, "pattern_n4": 2, "pattern_n5": 2,
                "window_1m": {"n1": 60, "n2": 10, "n3": 5, "n4": 5, "n5": 5},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "F: N2 P=3 + N3 P=3 + N4 P=4 (3^4=81 N4, denser per regime)",
            "config": {
                "pattern_n2": 3, "pattern_n3": 3, "pattern_n4": 4, "pattern_n5": 3,
                "window_1m": {"n1": 60, "n2": 10, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
    ]
    
    results_table = []
    best_wc, best_idx = 0.0, -1
    
    for idx, att in enumerate(attempts):
        # Reset to baseline first
        write_sax_config({})
        write_sax_config(att["config"])
        r = build_and_evaluate(train_df, oos_df, btc_tr, btc_oos, att["label"])
        if r:
            results_table.append((att["label"], r["avg_wc"]))
            if r["avg_wc"] > best_wc:
                best_wc = r["avg_wc"]
                best_idx = idx
                print(f"  *** NEW BEST: {best_wc:.4f} ***")
            if r["avg_wc"] >= 0.45:
                print(f"  *** WINNER! >= 0.45 ***")
                break
        else:
            results_table.append((att["label"], "FAILED"))
    
    print(f"\n{'='*80}")
    print(f"  RESULTS TABLE")
    print(f"{'='*80}")
    for name, wc in results_table:
        marker = " <<<" if isinstance(wc, float) and wc == best_wc else ""
        print(f"  {name:<65} {wc if isinstance(wc,str) else f'{wc:.4f}'}{marker}")
    
    if best_idx >= 0:
        print(f"\n  BEST: {attempts[best_idx]['label']} → WC={best_wc:.4f}")
        # Apply winning config
        write_sax_config({})
        write_sax_config(attempts[best_idx]["config"])
    
    print(f"\n{'='*80}")
    print(f"  DONE. Best WC: {best_wc:.4f}")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
