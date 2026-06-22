#!/usr/bin/env python3
"""
Trace the regime during OOS replay to understand why N4 always matches LONG.
Also trace which level is selected as best_match and what direction it produces.
"""
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

import pandas as pd
from ppmt.data.classifier import AssetClassifier
from ppmt.data.storage import PPMTStorage
from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.weights import AdaptiveWeights
from ppmt.core.trie import PPMTTrie

storage = PPMTStorage()
classifier = AssetClassifier()

symbol = "BTC/USDT"
info = classifier.classify(symbol)
df = storage.load_ohlcv(symbol, "5m")
oos_start = df.index[-1] - pd.Timedelta(days=7)
oos_df = df[df.index >= oos_start]

tries = storage.load_all_tries(symbol, info.asset_class, timeframe="5m")

engine = PPMT(
    symbol=symbol, asset_class=info.asset_class,
    weight_profile=info.weight_profile, dual_sax=True,
    min_confidence=0.08, timeframe="5m",
)
engine.weights = AdaptiveWeights.from_profile(info.weight_profile, timeframe="5m")
engine.set_tries(
    trie_n1=tries["n1"] or PPMTTrie(name="empty_n1"),
    trie_n2=tries["n2"] or PPMTTrie(name="empty_n2"),
    trie_n3=tries["n3"] or PPMTTrie(name="empty_n3"),
    trie_n4=tries["n4"] or engine.trie_n4,
)

# Track regimes and best_match selection
regime_counts = {}
best_level_counts = {}
best_match_dirs = {}
n4_regime_at_signal = []

_last_engine_ts = 0

for idx in range(len(oos_df)):
    row = oos_df.iloc[[idx]]
    current_price = float(row["close"].iloc[0])
    ts = oos_df.index[idx]
    ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)

    if ts_sec <= _last_engine_ts:
        continue
    _last_engine_ts = ts_sec

    result = engine.process_new_candle(
        candle_df=row, current_price=current_price,
        is_in_position=False, entry_price=None,
    )

    if result is None:
        continue

    sig = result.signal
    if sig is None or not sig.is_entry:
        continue

    # Get the current regime from N4
    n4_trie = tries.get("n4")
    current_regime = "unknown"
    if hasattr(n4_trie, 'current_regime'):
        current_regime = n4_trie.current_regime or "unknown"
    
    # Determine which level won best_match
    # We need to re-examine: which level had highest confidence?
    n1_conf = result.n1_confidence
    n2_conf = result.n2_confidence
    n3_conf = result.n3_confidence
    n4_conf = result.n4_confidence
    
    confs = {"n1": n1_conf, "n2": n2_conf, "n3": n3_conf, "n4": n4_conf}
    best_lvl = max(confs, key=lambda k: confs[k])
    
    regime_counts[current_regime] = regime_counts.get(current_regime, 0) + 1
    best_level_counts[best_lvl] = best_level_counts.get(best_lvl, 0) + 1
    
    d = sig.direction
    best_match_dirs[d] = best_match_dirs.get(d, 0) + 1
    
    n4_regime_at_signal.append({
        "regime": current_regime,
        "best_level": best_lvl,
        "direction": d,
        "n1_conf": n1_conf, "n2_conf": n2_conf,
        "n3_conf": n3_conf, "n4_conf": n4_conf,
        "weighted_conf": result.weighted_confidence,
    })

print(f"\n{'='*70}")
print(f"  {symbol} — Signal Generation Analysis (5m, 7d OOS)")
print(f"{'='*70}")

print(f"\n  Signal directions: {best_match_dirs}")
print(f"  Best level selection: {best_level_counts}")
print(f"  N4 regime at signal time: {regime_counts}")

print(f"\n  Per-signal detail (first 20):")
for i, s in enumerate(n4_regime_at_signal[:20]):
    print(f"    #{i+1} regime={s['regime']:<15} best={s['best_level']} dir={s['direction']} "
          f"conf: N1={s['n1_conf']:.3f} N2={s['n2_conf']:.3f} N3={s['n3_conf']:.3f} N4={s['n4_conf']:.3f} "
          f"weighted={s['weighted_conf']:.3f}")

# Now check: for signals where N3 was best, what direction did N3 want?
print(f"\n  Direction by best level:")
for lvl in ["n1", "n2", "n3", "n4"]:
    lvl_signals = [s for s in n4_regime_at_signal if s["best_level"] == lvl]
    if lvl_signals:
        dirs = {}
        for s in lvl_signals:
            dirs[s["direction"]] = dirs.get(s["direction"], 0) + 1
        print(f"    {lvl}: {dirs} (from {len(lvl_signals)} signals)")
