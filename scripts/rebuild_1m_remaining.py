#!/usr/bin/env python3
"""Build remaining 5 tokens for 1m with volume + body_anatomy."""
import sys, time, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import requests, pandas as pd
from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage

TOKENS = [
    {"symbol": "DOGE/USDT", "class": "meme"},
    {"symbol": "PEPE/USDT", "class": "meme"},
    {"symbol": "WIF/USDT", "class": "meme"},
    {"symbol": "LINK/USDT", "class": "alt"},
    {"symbol": "UNI/USDT", "class": "alt"},
]

DAYS = 120
LOG = "/home/z/my-project/rebuild_remaining.log"

def log(msg):
    with open(LOG, "a") as f:
        f.write(msg + "\n")
    print(msg, flush=True)

def download_1m(symbol, days):
    api_sym = symbol.replace("/", "")
    ms_per_candle = 60_000
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - days * 86_400_000
    all_data = []
    cur = start_ts
    while cur < end_ts:
        try:
            resp = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": api_sym, "interval": "1m", "limit": 1000, "startTime": cur},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log(f"    [WARN] {e}")
            time.sleep(1)
            continue
        if not data:
            break
        for c in data:
            all_data.append([c[0], float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
        cur = data[-1][0] + ms_per_candle
        time.sleep(0.1)
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df

def main():
    open(LOG, "w").close()  # clear log
    log("TAREA 16: Building remaining 5 tokens for 1m")
    storage = PPMTStorage()
    total = 0

    for idx, tok in enumerate(TOKENS):
        sym, cls = tok["symbol"], tok["class"]
        log(f"[{idx+1}/5] {sym} ({cls}) @ 1m — downloading {DAYS}d...")
        df = download_1m(sym, DAYS)
        if len(df) == 0:
            log(f"  [ERROR] No data")
            continue
        log(f"  {len(df):,} candles")

        try:
            engine = PPMT(symbol=sym, asset_class=cls, dual_sax=True, min_confidence=0.08, timeframe="1m")
            engine.attach_storage(storage)
            n = engine.build(df)
            total += n

            n3c = engine.trie_n3.pattern_count
            n4c = engine.trie_n4.pattern_count if hasattr(engine.trie_n4, 'pattern_count') else 0

            def get_leaves(node, depth=0, max_depth=3):
                if depth == max_depth or not node.children:
                    return [node]
                leaves = []
                for k, child in node.children.items():
                    leaves.extend(get_leaves(child, depth+1, max_depth))
                return leaves

            leaves = get_leaves(engine.trie_n3.root, 0, 3)
            n3_obs = sum(l.metadata.historical_count for l in leaves)
            n3_wins = sum(int(l.metadata.historical_count * l.metadata.win_rate) for l in leaves)
            n3_wr = (n3_wins / n3_obs * 100) if n3_obs > 0 else 0

            log(f"  N3: {n3c} pat, {n3_obs} obs, WR={n3_wr:.1f}% | N4: {n4c} pat | Ins: {n:,}")
        except Exception as e:
            log(f"  [ERROR] Build failed: {e}")
            import traceback
            traceback.print_exc()

    log(f"DONE. Total inserted: {total:,}")
    storage.close()

if __name__ == "__main__":
    main()
