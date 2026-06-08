#!/usr/bin/env python3
"""
PPMT Bulk Ingest Pipeline

Ingests historical OHLCV data for multiple trading pairs across
multiple timeframes from Binance, then automatically builds PPMT
tries for all assets that received data.

Usage:
    # Default: 20 pairs × 3 timeframes (1h, 4h, 1d) × 365 days
    cd ~/ppmt && python -m ppmt.scripts.bulk_ingest

    # Custom pairs and timeframes
    python ppmt/scripts/bulk_ingest.py --pairs BTC/USDT,ETH/USDT --timeframes 1h,4h --days 180

    # Use defaults with custom days
    python ppmt/scripts/bulk_ingest.py --days 730
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field

# Ensure ppmt modules can be imported from this script location
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ppmt.data.collector import DataCollector
from ppmt.data.classifier import AssetClassifier
from ppmt.data.storage import PPMTStorage
from ppmt.engine.ppmt import PPMT


# ── Default Configuration ──────────────────────────────────────────────

DEFAULT_PAIRS: dict[str, list[str]] = {
    "blue_chip": [
        "BTC/USDT", "ETH/USDT",
    ],
    "large_cap": [
        "BNB/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
        "AVAX/USDT", "DOT/USDT",
    ],
    "mid_cap": [
        "LINK/USDT", "UNI/USDT", "NEAR/USDT", "APT/USDT",
        "ARB/USDT", "OP/USDT",
    ],
    "defi": [
        "AAVE/USDT", "CRV/USDT",
    ],
    "meme": [
        "DOGE/USDT", "SHIB/USDT", "PEPE/USDT",
    ],
}

DEFAULT_TIMEFRAMES = ["1h", "4h", "1d"]

# Rate limit delay between Binance requests (seconds)
RATE_LIMIT_DELAY = 0.3


# ── Data Structures ────────────────────────────────────────────────────

@dataclass
class IngestResult:
    """Result for a single pair+timeframe ingestion."""
    symbol: str
    timeframe: str
    success: bool
    candle_count: int = 0
    error: str = ""


@dataclass
class BuildResult:
    """Result for a single asset trie build."""
    symbol: str
    timeframe: str
    success: bool
    pattern_count: int = 0
    error: str = ""


@dataclass
class BulkIngestSummary:
    """Summary of the entire bulk ingest run."""
    total_pairs: int = 0
    total_timeframes: int = 0
    total_requests: int = 0
    successful_ingests: int = 0
    failed_ingests: int = 0
    total_candles: int = 0
    total_patterns: int = 0
    successful_builds: int = 0
    failed_builds: int = 0
    failed_pairs: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    results: list[IngestResult] = field(default_factory=list)
    build_results: list[BuildResult] = field(default_factory=list)


# ── Core Logic ─────────────────────────────────────────────────────────

def run_bulk_ingest(
    pairs: list[str] | None = None,
    timeframes: list[str] | None = None,
    days: int = 365,
) -> BulkIngestSummary:
    """
    Run the bulk ingestion pipeline.

    1. Fetch OHLCV data from Binance for each pair+timeframe
    2. Register each asset with the classifier
    3. Build PPMT tries for all assets that got data

    Args:
        pairs: List of trading pairs (e.g., ['BTC/USDT', 'ETH/USDT']).
               If None, uses the DEFAULT_PAIRS list (20 pairs).
        timeframes: List of timeframes (e.g., ['1h', '4h', '1d']).
                    If None, uses DEFAULT_TIMEFRAMES.
        days: Number of days of historical data to fetch.

    Returns:
        BulkIngestSummary with detailed results.
    """
    start_time = time.time()

    # Resolve pairs
    if pairs is None:
        all_pairs = []
        for asset_class, symbols in DEFAULT_PAIRS.items():
            all_pairs.extend(symbols)
        pairs = all_pairs

    # Resolve timeframes
    if timeframes is None:
        timeframes = DEFAULT_TIMEFRAMES

    summary = BulkIngestSummary(
        total_pairs=len(pairs),
        total_timeframes=len(timeframes),
        total_requests=len(pairs) * len(timeframes),
    )

    # Initialize components
    storage = PPMTStorage()
    collector = DataCollector(storage=storage)
    classifier = AssetClassifier()

    # Track which assets got data and which timeframes per asset
    asset_data: dict[str, dict[str, int]] = {}  # symbol -> {timeframe: candle_count}

    # ── Phase 1: Ingest OHLCV Data ────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  PPMT Bulk Ingest Pipeline")
    print(f"  {len(pairs)} pairs × {len(timeframes)} timeframes × {days} days")
    print(f"  Total requests: {summary.total_requests}")
    print(f"{'='*60}\n")

    request_num = 0
    for symbol in pairs:
        for tf in timeframes:
            request_num += 1
            print(f"  [{request_num}/{summary.total_requests}] {symbol} {tf} ... ", end="", flush=True)

            try:
                df = collector.fetch_and_save(symbol, tf, days)

                if df is not None and not df.empty:
                    candle_count = len(df)
                    summary.successful_ingests += 1
                    summary.total_candles += candle_count

                    # Register asset with classifier
                    info = classifier.classify(symbol)
                    storage.register_asset(symbol, info.asset_class)

                    # Track data
                    if symbol not in asset_data:
                        asset_data[symbol] = {}
                    asset_data[symbol][tf] = candle_count

                    result = IngestResult(
                        symbol=symbol,
                        timeframe=tf,
                        success=True,
                        candle_count=candle_count,
                    )
                    print(f"✓ {candle_count} candles")
                else:
                    summary.failed_ingests += 1
                    result = IngestResult(
                        symbol=symbol,
                        timeframe=tf,
                        success=False,
                        error="No data returned",
                    )
                    if symbol not in summary.failed_pairs:
                        summary.failed_pairs.append(symbol)
                    print(f"✗ No data")

            except Exception as e:
                summary.failed_ingests += 1
                result = IngestResult(
                    symbol=symbol,
                    timeframe=tf,
                    success=False,
                    error=str(e),
                )
                if symbol not in summary.failed_pairs:
                    summary.failed_pairs.append(symbol)
                print(f"✗ Error: {e}")

            summary.results.append(result)

            # Rate limit: be nice to Binance
            if request_num < summary.total_requests:
                time.sleep(RATE_LIMIT_DELAY)

    # ── Phase 2: Build Tries ──────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Building PPMT Tries for {len(asset_data)} assets")
    print(f"{'='*60}\n")

    for symbol, tf_data in asset_data.items():
        info = classifier.classify(symbol)
        print(f"  Building {symbol} ({info.asset_class}/{info.weight_profile}) ... ", end="", flush=True)

        for tf, candle_count in tf_data.items():
            try:
                # Load the OHLCV data we just ingested
                df = storage.load_ohlcv(symbol, tf)

                if df is None or df.empty:
                    summary.failed_builds += 1
                    build_result = BuildResult(
                        symbol=symbol,
                        timeframe=tf,
                        success=False,
                        error="No OHLCV data to build from",
                    )
                    summary.build_results.append(build_result)
                    continue

                # Create PPMT engine and build tries
                engine = PPMT(
                    symbol=symbol,
                    asset_class=info.asset_class,
                    weight_profile=info.weight_profile,
                )
                pattern_count = engine.build(df)

                # Save tries to storage
                for level_name, trie in [
                    ("N1", engine.trie_n1),
                    ("N2", engine.trie_n2),
                    ("N3", engine.trie_n3),
                    ("N4", engine.trie_n4),
                ]:
                    storage.save_trie(symbol, level_name, trie)

                # Save engine state
                engine.adapt_weights()
                state = engine.get_stats()
                storage.save_engine_state(symbol, state)

                summary.successful_builds += 1
                summary.total_patterns += pattern_count
                build_result = BuildResult(
                    symbol=symbol,
                    timeframe=tf,
                    success=True,
                    pattern_count=pattern_count,
                )
                summary.build_results.append(build_result)

            except Exception as e:
                summary.failed_builds += 1
                build_result = BuildResult(
                    symbol=symbol,
                    timeframe=tf,
                    success=False,
                    error=str(e),
                )
                summary.build_results.append(build_result)
                print(f"\n    ✗ Build error for {symbol} {tf}: {e}")

        # Print summary for this asset
        asset_builds = [b for b in summary.build_results if b.symbol == symbol]
        total_pat = sum(b.pattern_count for b in asset_builds)
        tfs_ok = sum(1 for b in asset_builds if b.success)
        tfs_total = len(asset_builds)
        print(f"{tfs_ok}/{tfs_total} TFs, {total_pat} patterns")

    # Clean up
    collector.close()

    # ── Final Summary ──────────────────────────────────────────────────
    summary.elapsed_seconds = time.time() - start_time

    print(f"\n{'='*60}")
    print(f"  BULK INGEST COMPLETE")
    print(f"{'='*60}")
    print(f"  Total pairs:       {summary.total_pairs}")
    print(f"  Total requests:    {summary.total_requests}")
    print(f"  Successful:        {summary.successful_ingests}")
    print(f"  Failed:            {summary.failed_ingests}")
    print(f"  Total candles:     {summary.total_candles:,}")
    print(f"  Total patterns:    {summary.total_patterns:,}")
    print(f"  Tries built:       {summary.successful_builds}")
    print(f"  Build failures:    {summary.failed_builds}")
    if summary.failed_pairs:
        print(f"  Failed pairs:      {', '.join(summary.failed_pairs)}")
    print(f"  Elapsed:           {summary.elapsed_seconds:.1f}s")
    print(f"{'='*60}\n")

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="PPMT Bulk Ingest Pipeline - Fetch historical data and build tries for multiple pairs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default: 20 pairs × 3 timeframes × 365 days
  python -m ppmt.scripts.bulk_ingest

  # Custom pairs
  python -m ppmt.scripts.bulk_ingest --pairs BTC/USDT,ETH/USDT,SOL/USDT

  # Custom timeframes and days
  python -m ppmt.scripts.bulk_ingest --timeframes 1h,4h --days 180

  # Full pipeline with custom params
  python -m ppmt.scripts.bulk_ingest --pairs BTC/USDT,ETH/USDT --timeframes 1h,4h,1d --days 730
        """,
    )
    parser.add_argument(
        "--pairs",
        type=str,
        default=None,
        help="Comma-separated list of trading pairs (e.g., BTC/USDT,ETH/USDT). Default: 20 pairs across all asset classes.",
    )
    parser.add_argument(
        "--timeframes",
        type=str,
        default=None,
        help="Comma-separated list of timeframes (e.g., 1h,4h,1d). Default: 1h,4h,1d",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Number of days of historical data to fetch. Default: 365",
    )

    args = parser.parse_args()

    # Parse pairs
    pairs = None
    if args.pairs:
        pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]

    # Parse timeframes
    timeframes = None
    if args.timeframes:
        timeframes = [tf.strip() for tf in args.timeframes.split(",") if tf.strip()]

    # Run
    summary = run_bulk_ingest(
        pairs=pairs,
        timeframes=timeframes,
        days=args.days,
    )

    # Exit with non-zero code if there were failures
    if summary.failed_ingests > 0 and summary.successful_ingests == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
