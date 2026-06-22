#!/usr/bin/env python3
"""
Trace raw signal directions through the replay pipeline.
P1: How many raw signals are LONG vs SHORT vs None?
P4: Where do SHORTs get lost?
"""
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

import numpy as np
import pandas as pd
from ppmt.data.classifier import AssetClassifier
from ppmt.data.storage import PPMTStorage
from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.weights import AdaptiveWeights
from ppmt.core.profiles import SPREAD_ESTIMATES
from ppmt.core.trie import PPMTTrie

storage = PPMTStorage()
classifier = AssetClassifier()

tokens = ["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"]
OOS_DAYS = 7

for symbol in tokens:
    info = classifier.classify(symbol)
    df = storage.load_ohlcv(symbol, "5m")
    if df is None or len(df) < 50:
        continue

    oos_start = df.index[-1] - pd.Timedelta(days=OOS_DAYS)
    oos_df = df[df.index >= oos_start]

    tries = storage.load_all_tries(symbol, info.asset_class, timeframe="5m")
    n1_trie = tries.get("n1")
    n2_trie = tries.get("n2")
    n3_trie = tries.get("n3")
    n4_trie = tries.get("n4")

    engine = PPMT(
        symbol=symbol,
        asset_class=info.asset_class,
        weight_profile=info.weight_profile,
        dual_sax=True,
        min_confidence=0.08,
        timeframe="5m",
    )
    engine.weights = AdaptiveWeights.from_profile(info.weight_profile, timeframe="5m")
    engine.set_tries(
        trie_n1=n1_trie if n1_trie else PPMTTrie(name="empty_n1"),
        trie_n2=n2_trie if n2_trie else PPMTTrie(name="empty_n2"),
        trie_n3=n3_trie if n3_trie else PPMTTrie(name="empty_n3"),
        trie_n4=n4_trie if n4_trie else engine.trie_n4,
    )

    # Count signal directions at each stage
    raw_count = 0
    raw_long = 0
    raw_short = 0
    raw_none_dir = 0
    raw_no_signal = 0
    # Track match results
    match_dirs = {"n3": {"LONG": 0, "SHORT": 0, "None": 0},
                  "n1": {"LONG": 0, "SHORT": 0, "None": 0},
                  "n2": {"LONG": 0, "SHORT": 0, "None": 0},
                  "n4": {"LONG": 0, "SHORT": 0, "None": 0}}
    
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
            candle_df=row,
            current_price=current_price,
            is_in_position=False,
            entry_price=None,
        )

        if result is None:
            continue

        # Check all match results for direction
        for lvl, mr in [("n3", result.n3_match), ("n1", result.n1_match),
                        ("n2", result.n2_match), ("n4", result.n4_match)]:
            if mr and mr.node and mr.node.metadata and mr.node.metadata.historical_count > 0:
                d = mr.node.metadata.best_direction_p7(min_edge_pct=0.10)
                match_dirs[lvl][d if d else "None"] += 1

        sig = result.signal
        if sig is None or not sig.is_entry:
            raw_no_signal += 1
            continue

        raw_count += 1
        d = sig.direction
        if d == "LONG":
            raw_long += 1
        elif d == "SHORT":
            raw_short += 1
        else:
            raw_none_dir += 1

    print(f"\n{'='*70}")
    print(f"  {symbol} (class={info.asset_class}) — Raw Signal Analysis")
    print(f"{'='*70}")
    print(f"  Total raw entry signals: {raw_count}")
    print(f"    LONG:   {raw_long} ({raw_long/raw_count*100:.1f}%)" if raw_count > 0 else "    LONG: 0")
    print(f"    SHORT:  {raw_short} ({raw_short/raw_count*100:.1f}%)" if raw_count > 0 else "    SHORT: 0")
    print(f"    None:   {raw_none_dir}")
    print(f"  No-signal candles: {raw_no_signal}")
    print(f"\n  Match direction (best_direction_p7 of matched nodes):")
    for lvl in ["n3", "n1", "n2", "n4"]:
        total = sum(match_dirs[lvl].values())
        if total > 0:
            print(f"    {lvl}: LONG={match_dirs[lvl]['LONG']} SHORT={match_dirs[lvl]['SHORT']} None={match_dirs[lvl]['None']} (total matches={total})")
