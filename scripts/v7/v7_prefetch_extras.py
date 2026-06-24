"""
F4 — Prefetch funding rate + OI history for all 12 symbols.

Fetches the maximum history Binance allows (1000 funding rates = ~333 days
at 8h intervals, 500 OI snapshots = ~1.7 days at 5m intervals).

Output:
- data/v7_cache/funding_cache.db  (per-symbol funding rates)
- data/v7_cache/oi_cache.db       (per-symbol OI snapshots)

This is a one-time fetch. Subsequent calls will only fetch NEW data
(SQLite INSERT OR REPLACE).
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "v7"))

from v7_features_extras import (
    BinanceFundingFetcher,
    BinanceOIFetcher,
    FeaturesExtrasExtractor,
    SECTOR_TOKENS,
)

CACHE_DIR = "data/v7_cache"


def main():
    print(f"Cache dir: {CACHE_DIR}")
    extractor = FeaturesExtrasExtractor(cache_dir=CACHE_DIR)

    # Get all 12 symbols (add USDT suffix for Binance API)
    all_symbols = []
    for tokens in SECTOR_TOKENS.values():
        for tok in tokens:
            all_symbols.append(f"{tok}USDT")

    print(f"\nFetching data for {len(all_symbols)} symbols: {all_symbols}\n")

    # Time range: from 365 days ago to now
    end_ts = time.time()
    start_ts_funding = end_ts - 365 * 24 * 3600  # 1 year for funding (8h interval)
    start_ts_oi = end_ts - 7 * 24 * 3600           # 7 days for OI (5m interval, 500/req)

    total_funding = 0
    total_oi = 0
    failed = []

    for i, symbol in enumerate(all_symbols, 1):
        print(f"[{i}/{len(all_symbols)}] {symbol}")
        # Funding
        try:
            n_fund = extractor.funding_fetcher.fetch_and_cache(
                symbol,
                start_time_ms=int(start_ts_funding * 1000),
                end_time_ms=int(end_ts * 1000),
                max_pages=5,  # 5 * 1000 = 5000 records max (~555 days at 8h)
            )
            total_funding += n_fund
            print(f"  funding: +{n_fund} rates")
        except Exception as e:
            print(f"  ! funding FAILED: {e}")
            failed.append((symbol, "funding", str(e)))

        # OI (5m period, Binance limit 500/req)
        try:
            n_oi = extractor.oi_fetcher.fetch_and_cache(
                symbol,
                start_time_ms=int(start_ts_oi * 1000),
                end_time_ms=int(end_ts * 1000),
                max_pages=30,  # 30 * 500 = 15000 records (~52 days at 5m)
            )
            total_oi += n_oi
            print(f"  OI: +{n_oi} snapshots")
        except Exception as e:
            print(f"  ! OI FAILED: {e}")
            failed.append((symbol, "oi", str(e)))

        # Be polite to Binance API (avoid rate limit)
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"F4 PREFETCH SUMMARY")
    print(f"{'='*60}")
    print(f"  Symbols processed: {len(all_symbols)}")
    print(f"  Funding rates cached: {total_funding}")
    print(f"  OI snapshots cached: {total_oi}")
    print(f"  Failures: {len(failed)}")
    if failed:
        for sym, kind, err in failed:
            print(f"    - {sym} {kind}: {err}")

    print(f"\nCache files:")
    print(f"  {CACHE_DIR}/funding_cache.db")
    print(f"  {CACHE_DIR}/oi_cache.db")

    # Verify: extract a sample feature vector for BTCUSDT now
    print(f"\nSample feature extraction (BTCUSDT, ts=now):")
    features = extractor.extract("BTCUSDT", time.time())
    for k, v in features.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
