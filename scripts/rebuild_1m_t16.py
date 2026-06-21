#!/usr/bin/env python3
"""TAREA 16: Rebuild 1m tries with volume + body_anatomy.

Only rebuilds 1m timeframe (N3/N4) for all 10 tokens.
Uses paginated download for 120 days of 1m data.
"""
import sys, time, os

# Force stdout/stderr to be unbuffered
sys.stdout = open(sys.stdout.fileno(), mode='w', buffering=1)
sys.stderr = open(sys.stderr.fileno(), mode='w', buffering=1)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import requests, pandas as pd
from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage

TOKENS = [
    {"symbol": "BTC/USDT", "class": "blue_chip"},
    {"symbol": "ETH/USDT", "class": "blue_chip"},
    {"symbol": "SOL/USDT", "class": "large_cap"},
    {"symbol": "XRP/USDT", "class": "large_cap"},
    {"symbol": "AVAX/USDT", "class": "large_cap"},
    {"symbol": "DOGE/USDT", "class": "meme"},
    {"symbol": "PEPE/USDT", "class": "meme"},
    {"symbol": "WIF/USDT", "class": "meme"},
    {"symbol": "LINK/USDT", "class": "alt"},
    {"symbol": "UNI/USDT", "class": "alt"},
]

DAYS = 120

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
            print(f"    [WARN] {e}", flush=True)
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
    log_file = open("/home/z/my-project/rebuild_t16.log", "w", buffering=1)

    def log(msg):
        print(msg, flush=True)
        log_file.write(msg + "\n")
        log_file.flush()

    log("=" * 70)
    log("TAREA 16: REBUILD 1m — VOLUME + BODY_ANATOMY")

    storage = PPMTStorage()
    total = 0

    for idx, tok in enumerate(TOKENS):
        sym, cls = tok["symbol"], tok["class"]
        log(f"[{idx+1}/10] {sym} ({cls}) @ 1m — downloading {DAYS}d...")
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

            # N3 aggregate WR
            n3_obs = 0
            n3_wins = 0
            for pk, node in engine.trie_n3.root.children.items():
                m = node.metadata
                n3_obs += m.historical_count
                n3_wins += int(m.historical_count * m.win_rate)
            n3_wr = (n3_wins / n3_obs * 100) if n3_obs > 0 else 0

            log(f"  N3: {n3c} pat, {n3_obs} obs, WR={n3_wr:.1f}% | N4: {n4c} pat | Ins: {n:,}")
        except Exception as e:
            log(f"  [ERROR] Build failed: {e}")
            import traceback
            traceback.print_exc()

    log(f"DONE. Total inserted: {total:,}")

    # DOGE verification
    n3t = storage.load_trie("DOGE/USDT", "n3", timeframe="1m")
    if n3t:
        obs = sum(n.metadata.historical_count for n in n3t.root.children.values())
        wins = sum(int(n.metadata.historical_count * n.metadata.win_rate) for n in n3t.root.children.values())
        wr = (wins / obs * 100) if obs > 0 else 0
        log(f"DOGE 1m N3: {n3t.pattern_count} patterns, {obs} obs, WR={wr:.1f}%")

    storage.close()
    log_file.close()

if __name__ == "__main__":
    main()
