#!/usr/bin/env python3
"""TAREA 16: Rebuild 1m tries with volume + body_anatomy.

Only rebuilds 1m timeframe (N3/N4/N5) for all 10 tokens.
N1/N2 are NOT affected (their parameters don't change).
5m/15m are NOT affected.
"""
import sys, time, os, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pandas as pd
from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier

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

DAYS_1M = 120
BINANCE_BASE = "https://api.binance.com"
RATE_LIMIT_SLEEP = 0.1


def symbol_to_binance(symbol: str) -> str:
    return symbol.replace("/", "")


def download_klines(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    api_symbol = symbol_to_binance(symbol)
    ms_per_candle = 60_000  # 1m
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - (days * 86_400_000)

    all_data = []
    current_start = start_ts
    request_count = 0

    while current_start < end_ts:
        url = f"{BINANCE_BASE}/api/v3/klines"
        params = {"symbol": api_symbol, "interval": timeframe, "limit": 1000, "startTime": current_start}
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    [WARN] Request failed: {e}")
            time.sleep(1)
            continue

        if not data:
            break

        for candle in data:
            all_data.append({
                "timestamp": candle[0],
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            })

        current_start = data[-1][0] + ms_per_candle
        request_count += 1
        time.sleep(RATE_LIMIT_SLEEP)

        if request_count % 10 == 0:
            print(f"    ... {len(all_data):,} candles ({request_count} requests)", flush=True)

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def main():
    print("=" * 70)
    print("TAREA 16: REBUILD 1m WITH VOLUME + BODY_ANATOMY")
    print("=" * 70)
    print(f"Tokens: {len(TOKENS)} | Timeframe: 1m only | Days: {DAYS_1M}")
    print()

    storage = PPMTStorage()
    total_inserted = 0

    for idx, token in enumerate(TOKENS):
        symbol = token["symbol"]
        asset_class = token["class"]
        print(f"\n{'─' * 60}")
        print(f"[{idx + 1}/{len(TOKENS)}] {symbol} ({asset_class}) @ 1m")
        print(f"{'─' * 60}")

        # Download
        print(f"  Downloading {DAYS_1M}d of 1m data...", flush=True)
        try:
            df = download_klines(symbol, "1m", DAYS_1M)
        except Exception as e:
            print(f"  [ERROR] Download failed: {e}")
            continue

        if df is None or len(df) == 0:
            print(f"  [ERROR] No data for {symbol} 1m")
            continue

        print(f"  Downloaded: {len(df):,} candles", flush=True)

        # Build
        try:
            engine = PPMT(
                symbol=symbol,
                asset_class=asset_class,
                dual_sax=True,
                min_confidence=0.08,
                timeframe="1m",
            )
            engine.attach_storage(storage)
            n_inserted = engine.build(df)
            total_inserted += n_inserted

            n3_count = engine.trie_n3.pattern_count
            n4_count = engine.trie_n4.pattern_count if hasattr(engine.trie_n4, 'pattern_count') else 0
            n1_count = engine.trie_n1.pattern_count

            # N3 aggregate stats
            n3_total_obs = 0
            n3_total_wins = 0
            for pattern_key, node in engine.trie_n3.root.children.items():
                meta = node.metadata
                n3_total_obs += meta.historical_count
                n3_total_wins += int(meta.historical_count * meta.win_rate)

            n3_wr = (n3_total_wins / n3_total_obs * 100) if n3_total_obs > 0 else 0

            print(f"  Built {symbol} 1m -> N3: {n3_count} patterns, {n3_total_obs} obs, WR={n3_wr:.1f}%")
            print(f"  N1: {n1_count} patterns | N4: {n4_count} patterns | Inserted: {n_inserted:,}")

        except Exception as e:
            print(f"  [ERROR] Build failed: {e}")
            import traceback
            traceback.print_exc()
            continue

    print(f"\n{'=' * 70}")
    print("BUILD COMPLETE")
    print(f"{'=' * 70}")
    print(f"Total inserted: {total_inserted:,}")

    # Verify DOGE specifically
    print(f"\nDOGE/USDT 1m verification:")
    n3_trie = storage.load_trie("DOGE/USDT", "n3", timeframe="1m")
    if n3_trie:
        total_obs = 0
        total_wins = 0
        for pk, node in n3_trie.root.children.items():
            meta = node.metadata
            total_obs += meta.historical_count
            total_wins += int(meta.historical_count * meta.win_rate)
        wr = (total_wins / total_obs * 100) if total_obs > 0 else 0
        print(f"  N3 patterns: {n3_trie.pattern_count}")
        print(f"  N3 total obs: {total_obs}")
        print(f"  N3 aggregate WR: {wr:.1f}%")

    storage.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
