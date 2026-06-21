#!/usr/bin/env python3
"""
PPMT — DOGE/USDT 1m — Final Optimizer v5

The math: with meme weights (N2=60%), N2 at 0.32 conf is a hard ceiling.
Solution: Rebuild N2 from scratch with P=3 (27 dense patterns, cross-token data).
Also try larger windows and dual-encoder variants for better directionality.
"""

import sys, os, time, logging
import numpy as np, pandas as pd
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_PATH = os.path.join(PROJECT_ROOT, 'src')
sys.path.insert(0, SRC_PATH)
os.environ['PYTHONPATH'] = SRC_PATH

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("v5")
logger.setLevel(logging.INFO)

def download_klines(symbol, interval="1m", days=30):
    from urllib.request import Request, urlopen
    import json as _json
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
    if not all_c: return None
    df = pd.DataFrame(all_c)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)

def force_reimport():
    for m in list(sys.modules.keys()):
        if m.startswith('ppmt'): del sys.modules[m]

def patch_and_build(train_df, oos_df, btc_train, btc_oos, config, 
                    rebuild_n2=False, meme_tokens_data=None, label=""):
    """Build with patched config. Optionally rebuild N2 from scratch."""
    force_reimport()
    
    import ppmt.core.sax as sax_mod
    if config.get("pattern_config"):
        for k, v in config["pattern_config"].items():
            sax_mod.LEVEL_PATTERN_CONFIG[k] = v
    if config.get("window_1m"):
        sax_mod.LEVEL_WINDOW_CONFIG["1m"] = config["window_1m"]
    if config.get("dual_alpha_n3"):
        sax_mod.LEVEL_DUAL_ALPHA_CONFIG["n3"] = config["dual_alpha_n3"]
        sax_mod.LEVEL_ALPHA_CONFIG["n3"] = config["dual_alpha_n3"]["price"]
    if config.get("dual_alpha_n4"):
        sax_mod.LEVEL_DUAL_ALPHA_CONFIG["n4"] = config["dual_alpha_n4"]
        sax_mod.LEVEL_ALPHA_CONFIG["n4"] = config["dual_alpha_n4"]["price"]
    
    from ppmt.engine.ppmt import PPMT
    from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
    
    storage = PPMTStorage()
    engine = PPMT(symbol="DOGE/USDT", asset_class="meme", dual_sax=True,
                  min_confidence=0.08, timeframe="1m")
    engine.attach_storage(storage)
    
    # Load N1 from storage (universal, P=5, compatible)
    n1 = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
    if n1: engine.trie_n1 = n1
    
    if rebuild_n2 and meme_tokens_data:
        # Rebuild N2 from scratch with new P for all meme tokens
        # Don't load stored N2 - build fresh
        logger.info("Rebuilding N2 from scratch with all meme tokens...")
        for token_sym, token_df in meme_tokens_data.items():
            token_engine = PPMT(symbol=token_sym, asset_class="meme", dual_sax=True,
                               min_confidence=0.08, timeframe="1m")
            token_engine.attach_storage(storage)
            # Load existing N1/N2 for this token
            tn1 = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
            if tn1: token_engine.trie_n1 = tn1
            tn2 = storage.load_trie(class_pool_key("meme"), "n2")
            if tn2: token_engine.trie_n2 = tn2
            token_engine.build(token_df)
            # Save back
            storage.save_trie(UNIVERSAL_POOL_KEY, "n1", token_engine.trie_n1)
            storage.save_trie(class_pool_key("meme"), "n2", token_engine.trie_n2)
        
        # Now load the rebuilt N2
        n2 = storage.load_trie(class_pool_key("meme"), "n2")
        if n2: engine.trie_n2 = n2
        logger.info(f"Rebuilt N2: {engine.trie_n2.pattern_count} patterns")
    else:
        n2 = storage.load_trie(class_pool_key("meme"), "n2")
        if n2: engine.trie_n2 = n2
    
    # Build DOGE
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
        results.append(r.weighted_confidence)
        if n_eval >= 500: break
    
    if not results: return None
    avg = np.mean(results)
    w = engine.weights
    
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"  N3: {engine.trie_n3.pattern_count} | N4: {getattr(engine.trie_n4,'pattern_count',0)}")
    print(f"  Wts: N1={w.n1_universal:.0%} N2={w.n2_asset_class:.0%} N3={w.n3_per_asset:.0%} N4={w.n4_per_asset_regime:.0%}")
    print(f"{'='*80}")
    print(f"  {'Lvl':<5} {'Mch%':>6} {'WR':>7} {'Count':>8} {'Conf':>7}")
    for ln in ["n1","n2","n3","n4"]:
        ml = level_meta[ln]
        mp = match_counts[ln]/n_eval*100 if n_eval else 0
        if ml:
            print(f"  {ln:<5} {mp:>5.0f}% {np.mean([m['wr'] for m in ml]):>7.3f} "
                  f"{np.mean([m['hc'] for m in ml]):>8.1f} {np.mean([m['cf'] for m in ml]):>7.4f}")
    
    above45 = sum(1 for c in results if c >= 0.45)
    above30 = sum(1 for c in results if c >= 0.30)
    print(f"\n  WC: {avg:.4f} | >=0.45: {above45}/500 | >=0.30: {above30}/500")
    
    return {"avg_wc": avg}

def main():
    print("=" * 80)
    print("  PPMT DOGE/USDT 1m — Final Optimizer v5")
    print("  Strategy: Rebuild N2 from all meme tokens + tune N3/N4")
    print("=" * 80)
    
    # Download all meme tokens
    print("\n[1] Downloading meme token data (30 days)...")
    doge = download_klines("DOGE/USDT", "1m", days=30)
    pepe = download_klines("PEPE/USDT", "1m", days=30)
    wif = download_klines("WIF/USDT", "1m", days=30)
    btc = download_klines("BTC/USDT", "1m", days=30)
    
    print(f"  DOGE: {len(doge) if doge is not None else 0}")
    print(f"  PEPE: {len(pepe) if pepe is not None else 0}")
    print(f"  WIF:  {len(wif) if wif is not None else 0}")
    print(f"  BTC:  {len(btc) if btc is not None else 0}")
    
    # Split DOGE
    train_sz = int(len(doge) * 0.70)
    train_df = doge.iloc[:train_sz].reset_index(drop=True)
    oos_df = doge.iloc[train_sz:].reset_index(drop=True)
    btc_tr = btc.iloc[:train_sz].reset_index(drop=True) if btc is not None else None
    btc_oos = btc.iloc[train_sz:].reset_index(drop=True) if btc is not None else None
    
    # Prepare meme tokens data for N2 rebuild (use 70% of each)
    meme_data = {}
    if pepe is not None:
        tsz = int(len(pepe) * 0.70)
        meme_data["PEPE/USDT"] = pepe.iloc[:tsz].reset_index(drop=True)
    if wif is not None:
        tsz = int(len(wif) * 0.70)
        meme_data["WIF/USDT"] = wif.iloc[:tsz].reset_index(drop=True)
    meme_data["DOGE/USDT"] = train_df.copy()
    
    print(f"  DOGE Train: {len(train_df)}, OOS: {len(oos_df)}")
    print(f"  Meme tokens for N2 rebuild: {list(meme_data.keys())}")
    
    # Attempts — focus on N2 rebuild + best N3/N4 configs
    attempts = [
        {
            "label": "A: N3 price-only P=3 W=10 + N2 rebuild P=3 (27 dense N2)",
            "config": {
                "pattern_config": {"n2": 3, "n3": 3, "n4": 3, "n5": 3},
                "window_1m": {"n1": 60, "n2": 60, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
            "rebuild_n2": True,
        },
        {
            "label": "B: +N2 W=10 (more N2 symbols, smaller window)",
            "config": {
                "pattern_config": {"n2": 3, "n3": 3, "n4": 3, "n5": 3},
                "window_1m": {"n1": 60, "n2": 10, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
            "rebuild_n2": True,
        },
        {
            "label": "C: N3 dual (p=3,vol=2) 6^3=216 + N2 rebuild P=3",
            "config": {
                "pattern_config": {"n2": 3, "n3": 3, "n4": 3, "n5": 3},
                "window_1m": {"n1": 60, "n2": 10, "n3": 10, "n4": 10, "n5": 10},
                "dual_alpha_n3": {"price": 3, "volume": 2},
                "dual_alpha_n4": {"price": 3, "volume": 2},
            },
            "rebuild_n2": True,
        },
        {
            "label": "D: N3 price-only P=3 W=30 (longer trend, 90min) + N2 rebuild",
            "config": {
                "pattern_config": {"n2": 3, "n3": 3, "n4": 3, "n5": 3},
                "window_1m": {"n1": 60, "n2": 10, "n3": 30, "n4": 30, "n5": 30},
                "dual_alpha_n3": {"price": 3, "volume": 0},
                "dual_alpha_n4": {"price": 3, "volume": 0},
            },
            "rebuild_n2": True,
        },
        {
            "label": "E: N3 dual (p=3,vol=2) W=30 + N2 rebuild P=3 W=10",
            "config": {
                "pattern_config": {"n2": 3, "n3": 3, "n4": 3, "n5": 3},
                "window_1m": {"n1": 60, "n2": 10, "n3": 30, "n4": 30, "n5": 30},
                "dual_alpha_n3": {"price": 3, "volume": 2},
                "dual_alpha_n4": {"price": 3, "volume": 2},
            },
            "rebuild_n2": True,
        },
    ]
    
    results_table = []
    best_wc, best_idx = 0.0, -1
    
    for idx, att in enumerate(attempts):
        try:
            r = patch_and_build(
                train_df, oos_df, btc_tr, btc_oos,
                config=att["config"],
                rebuild_n2=att.get("rebuild_n2", False),
                meme_tokens_data=meme_data if att.get("rebuild_n2") else None,
                label=att["label"]
            )
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
        except Exception as e:
            print(f"\n  ERROR: {e}")
            results_table.append((att["label"], f"ERROR: {str(e)[:50]}"))
    
    print(f"\n{'='*80}")
    print(f"  FINAL RESULTS")
    print(f"{'='*80}")
    for name, wc in results_table:
        marker = " <<<" if isinstance(wc, float) and wc == best_wc else ""
        print(f"  {name:<65} {wc if isinstance(wc,str) else f'{wc:.4f}'}{marker}")
    
    print(f"\n  BEST: WC={best_wc:.4f}")
    if best_idx >= 0:
        print(f"  Config: {attempts[best_idx]['config']}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
