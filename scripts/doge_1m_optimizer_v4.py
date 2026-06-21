#!/usr/bin/env python3
"""
PPMT — DOGE/USDT 1m SAX Parameter Optimizer v4

Clean approach: modify config objects AFTER import, not file editing.
This avoids corrupting sax.py.
"""

import sys, os, time, logging
import numpy as np, pandas as pd
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_PATH = os.path.join(PROJECT_ROOT, 'src')
sys.path.insert(0, SRC_PATH)
os.environ['PYTHONPATH'] = SRC_PATH

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("v4")
logger.setLevel(logging.INFO)

# ============================================================
# Data Download
# ============================================================
def download_klines(symbol, interval="1m", days=30):
    from urllib.request import Request, urlopen
    import json as _json
    # Try Binance
    try:
        bsym = symbol.replace("/", "")
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = end_ms - (days * 24 * 60 * 60 * 1000)
        all_c, cur = [], start_ms
        while cur < end_ms:
            url = f"https://api.binance.com/api/v3/klines?symbol={bsym}&interval={interval}&startTime={cur}&limit=1000"
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
        if all_c:
            df = pd.DataFrame(all_c)
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            return df.sort_values("timestamp").reset_index(drop=True)
    except: pass
    return None

# ============================================================
# Runtime Config Patching
# ============================================================
def patch_sax_config(pattern_config=None, window_1m=None, dual_alpha_n3=None, dual_alpha_n4=None):
    """Patch sax.py config objects at runtime (after import)."""
    import ppmt.core.sax as sax_mod
    
    if pattern_config:
        for k, v in pattern_config.items():
            sax_mod.LEVEL_PATTERN_CONFIG[k] = v
    
    if window_1m:
        sax_mod.LEVEL_WINDOW_CONFIG["1m"] = window_1m
    
    if dual_alpha_n3:
        sax_mod.LEVEL_DUAL_ALPHA_CONFIG["n3"] = dual_alpha_n3
    
    if dual_alpha_n4:
        sax_mod.LEVEL_DUAL_ALPHA_CONFIG["n4"] = dual_alpha_n4
    
    # Also update LEVEL_ALPHA_CONFIG for n3/n4 to match price alpha
    if dual_alpha_n3:
        sax_mod.LEVEL_ALPHA_CONFIG["n3"] = dual_alpha_n3["price"]
    if dual_alpha_n4:
        sax_mod.LEVEL_ALPHA_CONFIG["n4"] = dual_alpha_n4["price"]


def force_reimport():
    """Force reimport of all ppmt modules."""
    for m in list(sys.modules.keys()):
        if m.startswith('ppmt'):
            del sys.modules[m]


# ============================================================
# Build & Evaluate
# ============================================================
def build_and_evaluate(train_df, oos_df, btc_train, btc_oos, config, label=""):
    """Build engine with patched config, evaluate on OOS data."""
    force_reimport()
    
    # Import fresh, then patch
    import ppmt.core.sax as sax_mod
    patch_sax_config(**config)
    
    # Now import PPMT (it will read the patched sax config)
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
    
    results = []
    level_meta = {"n1":[], "n2":[], "n3":[], "n4":[]}
    match_counts = {"n1":0, "n2":0, "n3":0, "n4":0}
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
    
    w = engine.weights
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"  Train: {len(train_df)} | OOS: {n_eval} | N3: {engine.trie_n3.pattern_count} | N4: {getattr(engine.trie_n4,'pattern_count',0)}")
    print(f"  Wts: N1={w.n1_universal:.0%} N2={w.n2_asset_class:.0%} N3={w.n3_per_asset:.0%} N4={w.n4_per_asset_regime:.0%}")
    print(f"{'='*80}")
    print(f"  {'Lvl':<5} {'Mch%':>6} {'WR':>7} {'Count':>8} {'Move%':>9} {'Conf':>7}")
    for ln in ["n1","n2","n3","n4"]:
        ml = level_meta[ln]
        mp = match_counts[ln]/n_eval*100 if n_eval else 0
        if ml:
            print(f"  {ln:<5} {mp:>5.0f}% {np.mean([m['wr'] for m in ml]):>7.3f} "
                  f"{np.mean([m['hc'] for m in ml]):>8.1f} {np.mean([m['em'] for m in ml]):>8.4f}% "
                  f"{np.mean([m['cf'] for m in ml]):>7.4f}")
    
    print(f"\n  WC: {avg:.4f} (med={np.median(wcs):.4f}) | >=0.45: {sum(1 for c in wcs if c>=0.45)}/500 | >=0.30: {sum(1 for c in wcs if c>=0.30)}/500")
    
    if hasattr(engine.sax_n3, 'price_alphabet_size'):
        ea = engine.sax_n3.price_alphabet_size * max(1, engine.sax_n3.volume_alphabet_size)
    else:
        ea = engine.sax_n3.alphabet_size
    print(f"  N3: α_eff={ea}, P={engine.pl_n3}, max_pat={ea**engine.pl_n3}")
    if level_meta["n3"]:
        cs = [m["hc"] for m in level_meta["n3"]]
        print(f"  N3 obs/node: avg={np.mean(cs):.1f} min={min(cs)} max={max(cs)}")
    
    return {"avg_wc": avg}


def main():
    print("=" * 80)
    print("  PPMT DOGE/USDT 1m — Optimizer v4 (runtime patching)")
    print("=" * 80)
    
    print("\n[1] Downloading data (30 days)...")
    doge = download_klines("DOGE/USDT", "1m", days=30)
    if doge is None or len(doge) < 5000:
        print("FATAL: No DOGE data"); sys.exit(1)
    print(f"  DOGE: {len(doge)} candles")
    
    btc = download_klines("BTC/USDT", "1m", days=30)
    print(f"  BTC: {len(btc) if btc is not None else 0} candles")
    
    train_sz = int(len(doge) * 0.70)
    train_df = doge.iloc[:train_sz].reset_index(drop=True)
    oos_df = doge.iloc[train_sz:].reset_index(drop=True)
    btc_tr = btc.iloc[:train_sz].reset_index(drop=True) if btc is not None else None
    btc_oos = btc.iloc[train_sz:].reset_index(drop=True) if btc is not None else None
    print(f"  Train: {len(train_df)}, OOS: {len(oos_df)}")
    
    # Configuration attempts
    attempts = [
        {
            "label": "BASELINE (original config, won-fix only)",
            "config": {},
        },
        {
            "label": "A: price-only N3/N4 P=3 W=10",
            "config": {
                "pattern_config": {"n3": 3, "n4": 3, "n5": 3},
                "window_1m": {"n1": 60, "n2": 60, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "B: +N2 P=3 (27 patterns, 8x denser)",
            "config": {
                "pattern_config": {"n2": 3, "n3": 3, "n4": 3, "n5": 3},
                "window_1m": {"n1": 60, "n2": 60, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "C: +N2 P=3 W_n2=10 (max N2 symbols in 1m)",
            "config": {
                "pattern_config": {"n2": 3, "n3": 3, "n4": 3, "n5": 3},
                "window_1m": {"n1": 60, "n2": 10, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "D: N2 P=3 + N3 P=2 W=5 (9 N3 patterns)",
            "config": {
                "pattern_config": {"n2": 3, "n3": 2, "n4": 2, "n5": 2},
                "window_1m": {"n1": 60, "n2": 10, "n3": 5, "n4": 5, "n5": 5},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "E: N2 P=3 + N3 P=3 + N4 P=4 (81 N4 patterns)",
            "config": {
                "pattern_config": {"n2": 3, "n3": 3, "n4": 4, "n5": 3},
                "window_1m": {"n1": 60, "n2": 10, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
        {
            "label": "F: ALL P=3, ALL W=10 (uniform density)",
            "config": {
                "pattern_config": {"n2": 3, "n3": 3, "n4": 3, "n5": 3},
                "window_1m": {"n1": 60, "n2": 10, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
        },
    ]
    
    results_table = []
    best_wc, best_idx = 0.0, -1
    
    for idx, att in enumerate(attempts):
        r = build_and_evaluate(train_df, oos_df, btc_tr, btc_oos, att["config"], att["label"])
        if r:
            results_table.append((att["label"], r["avg_wc"]))
            if r["avg_wc"] > best_wc:
                best_wc = r["avg_wc"]
                best_idx = idx
                print(f"\n  *** NEW BEST: {best_wc:.4f} ***")
            if r["avg_wc"] >= 0.45:
                print(f"\n  *** WINNER! >= 0.45 ***")
                break
        else:
            results_table.append((att["label"], "FAILED"))
    
    print(f"\n{'='*80}")
    print(f"  RESULTS TABLE")
    print(f"{'='*80}")
    for name, wc in results_table:
        marker = " <<<" if isinstance(wc, float) and wc == best_wc else ""
        print(f"  {name:<60} {wc if isinstance(wc,str) else f'{wc:.4f}'}{marker}")
    
    print(f"\n  BEST: WC={best_wc:.4f}")
    if best_idx >= 0:
        print(f"  Config: {attempts[best_idx]['config']}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
