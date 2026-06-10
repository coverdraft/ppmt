"""
PPMT Bulk Ingest Script

Ingests historical data for multiple trading pairs across multiple timeframes,
then automatically builds the 4-level PPMT Trie for each.

Usage:
    python -m ppmt.scripts.bulk_ingest --days 365
    python -m ppmt.scripts.bulk_ingest --days 90 --pairs btc_eth_only
    python -m ppmt.scripts.bulk_ingest --days 365 --timeframes 1h,4h,1d
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Add parent src to path so we can import ppmt when running as module
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ppmt.data.storage import PPMTStorage
from ppmt.data.collector import DataCollector
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.ppmt import PPMT
from ppmt.core.trie import PPMTTrie

# ============================================================
# PAIR PRESETS
# ============================================================

PAIR_PRESETS = {
    "all": [
        "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
        "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "MATIC/USDT",
        "LINK/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "FIL/USDT",
        "APT/USDT", "ARB/USDT", "OP/USDT", "NEAR/USDT", "SUI/USDT",
    ],
    "btc_eth_only": [
        "BTC/USDT", "ETH/USDT",
    ],
    "top10": [
        "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
        "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "MATIC/USDT",
    ],
    "defi": [
        "UNI/USDT", "LINK/USDT", "AAVE/USDT", "MKR/USDT", "COMP/USDT",
        "CRV/USDT", "SUSHI/USDT", "1INCH/USDT", "YFI/USDT", "SNX/USDT",
    ],
    "layer1": [
        "SOL/USDT", "AVAX/USDT", "DOT/USDT", "ATOM/USDT", "NEAR/USDT",
        "APT/USDT", "SUI/USDT", "ADA/USDT", "ALGO/USDT", "FTM/USDT",
    ],
    "layer2": [
        "ARB/USDT", "OP/USDT", "MATIC/USDT", "IMX/USDT", "METIS/USDT",
        "SKL/USDT", "LRC/USDT", "CELO/USDT", "BOBA/USDT", "STRK/USDT",
    ],
}

DEFAULT_TIMEFRAMES = ["1h", "4h", "1d"]


def run_ingest(
    symbol: str,
    timeframe: str,
    days: int,
    exchange: str = "binance",
    storage: PPMTStorage | None = None,
) -> dict:
    """Ingest data for a single symbol/timeframe combination."""
    close_storage = False
    if storage is None:
        storage = PPMTStorage()
        close_storage = True

    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "status": "pending",
        "candles": 0,
        "error": None,
    }

    try:
        collector = DataCollector(exchange=exchange, storage=storage)
        df = collector.fetch_and_save(symbol, timeframe, days)

        if df.empty:
            result["status"] = "no_data"
            result["error"] = "No data returned from exchange"
        else:
            # Classify and register asset
            classifier = AssetClassifier()
            info = classifier.classify(symbol)
            storage.register_asset(symbol, info.asset_class)

            result["status"] = "ok"
            result["candles"] = len(df)
            result["asset_class"] = info.asset_class
            result["weight_profile"] = info.weight_profile

        collector.close()

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    if close_storage:
        storage.close()

    return result


def run_build(
    symbol: str,
    timeframe: str = "1h",
    pattern_length: int = 5,
    storage: PPMTStorage | None = None,
) -> dict:
    """Build PPMT Trie for a single symbol."""
    close_storage = False
    if storage is None:
        storage = PPMTStorage()
        close_storage = True

    result = {
        "symbol": symbol,
        "status": "pending",
        "patterns": 0,
        "error": None,
    }

    try:
        # Load data
        df = storage.load_ohlcv(symbol, timeframe)
        if df.empty:
            result["status"] = "no_data"
            result["error"] = f"No data found for {symbol} ({timeframe}). Run ingest first."
        else:
            # Classify
            classifier = AssetClassifier()
            info = classifier.classify(symbol)

            # Load config
            import yaml
            config_path = os.path.expanduser("~/.ppmt/config.yaml")
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = yaml.safe_load(f) or {}

            # Create engine
            sax_config = config.get("sax", {})
            engine = PPMT(
                symbol=symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=sax_config.get("alphabet_size", 10),
                sax_window_size=sax_config.get("window_size", 5),
                sax_strategy=sax_config.get("strategy", "ohlcv"),
                weight_profile=info.weight_profile,
            )

            # Build Trie
            count = engine.build(df, pattern_length=pattern_length)

            # Save Tries
            for level, trie in [
                ("n1", engine.trie_n1),
                ("n2", engine.trie_n2),
                ("n3", engine.trie_n3),
                ("n4", engine.trie_n4),
            ]:
                storage.save_trie(symbol, level, trie)

            # Save engine state
            storage.save_engine_state(symbol, engine.get_stats())

            result["status"] = "ok"
            result["patterns"] = count
            result["asset_class"] = info.asset_class
            result["weight_profile"] = info.weight_profile

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    if close_storage:
        storage.close()

    return result


def main():
    parser = argparse.ArgumentParser(description="PPMT Bulk Data Ingestion")
    parser.add_argument(
        "--days", type=int, default=365,
        help="Days of historical data to fetch (default: 365)"
    )
    parser.add_argument(
        "--pairs", type=str, default="all",
        help=f"Pair preset name: {', '.join(PAIR_PRESETS.keys())} (default: all)"
    )
    parser.add_argument(
        "--timeframes", type=str, default="1h,4h,1d",
        help="Comma-separated timeframes (default: 1h,4h,1d)"
    )
    parser.add_argument(
        "--exchange", type=str, default="binance",
        help="Exchange name (default: binance)"
    )
    parser.add_argument(
        "--pattern-length", type=int, default=5,
        help="SAX blocks per pattern (default: 5)"
    )
    parser.add_argument(
        "--ingest-only", action="store_true",
        help="Only ingest data, skip building tries"
    )
    parser.add_argument(
        "--build-only", action="store_true",
        help="Only build tries from existing data"
    )

    args = parser.parse_args()

    # Resolve pairs
    pairs = PAIR_PRESETS.get(args.pairs)
    if pairs is None:
        # Check if it's a comma-separated list
        if "/" in args.pairs:
            pairs = [p.strip() for p in args.pairs.split(",")]
        else:
            print(f"ERROR: Unknown pair preset '{args.pairs}'. Choose from: {', '.join(PAIR_PRESETS.keys())}")
            sys.exit(1)

    # Resolve timeframes
    timeframes = [tf.strip() for tf in args.timeframes.split(",")]

    print(f"=" * 60)
    print(f"PPMT Bulk Ingest")
    print(f"  Pairs: {len(pairs)} ({args.pairs})")
    print(f"  Timeframes: {timeframes}")
    print(f"  Days: {args.days}")
    print(f"  Exchange: {args.exchange}")
    print(f"=" * 60)

    # Initialize PPMT if needed
    config_dir = os.path.expanduser("~/.ppmt")
    if not os.path.exists(os.path.join(config_dir, "ppmt.db")):
        print("\nInitializing PPMT database...")
        storage = PPMTStorage()
        storage.close()
        print("Database created.")

    storage = PPMTStorage()
    results = {
        "started_at": time.time(),
        "pairs": pairs,
        "timeframes": timeframes,
        "days": args.days,
        "ingest_results": [],
        "build_results": [],
        "summary": {
            "total_tasks": len(pairs) * len(timeframes),
            "ingest_ok": 0,
            "ingest_errors": 0,
            "build_ok": 0,
            "build_errors": 0,
            "total_candles": 0,
            "total_patterns": 0,
        },
    }

    # Phase 1: Ingest
    if not args.build_only:
        print(f"\n--- Phase 1: Ingesting {len(pairs)} x {len(timeframes)} = {len(pairs) * len(timeframes)} combinations ---")

        for i, symbol in enumerate(pairs):
            for tf in timeframes:
                task_num = i * len(timeframes) + timeframes.index(tf) + 1
                total = len(pairs) * len(timeframes)
                print(f"\n[{task_num}/{total}] Ingesting {symbol} ({tf})...")

                r = run_ingest(
                    symbol=symbol,
                    timeframe=tf,
                    days=args.days,
                    exchange=args.exchange,
                    storage=storage,
                )
                results["ingest_results"].append(r)

                if r["status"] == "ok":
                    results["summary"]["ingest_ok"] += 1
                    results["summary"]["total_candles"] += r["candles"]
                    print(f"  OK: {r['candles']} candles ({r.get('asset_class', '?')})")
                else:
                    results["summary"]["ingest_errors"] += 1
                    print(f"  ERROR: {r.get('error', 'Unknown error')}")

                # Small delay to respect rate limits
                time.sleep(0.5)

    # Phase 2: Build
    if not args.ingest_only:
        print(f"\n--- Phase 2: Building Tries for {len(pairs)} assets ---")

        for i, symbol in enumerate(pairs):
            print(f"\n[{i + 1}/{len(pairs)}] Building {symbol}...")

            # Use the first available timeframe for building
            r = run_build(
                symbol=symbol,
                timeframe=timeframes[0],
                pattern_length=args.pattern_length,
                storage=storage,
            )
            results["build_results"].append(r)

            if r["status"] == "ok":
                results["summary"]["build_ok"] += 1
                results["summary"]["total_patterns"] += r["patterns"]
                print(f"  OK: {r['patterns']} patterns")
            else:
                results["summary"]["build_errors"] += 1
                print(f"  ERROR: {r.get('error', 'Unknown error')}")

    storage.close()

    results["completed_at"] = time.time()
    results["duration_seconds"] = round(results["completed_at"] - results["started_at"], 1)

    # Print summary
    s = results["summary"]
    print(f"\n{'=' * 60}")
    print(f"BULK INGEST COMPLETE")
    print(f"  Duration: {results['duration_seconds']}s")
    print(f"  Ingest: {s['ingest_ok']} OK, {s['ingest_errors']} errors, {s['total_candles']} total candles")
    print(f"  Build: {s['build_ok']} OK, {s['build_errors']} errors, {s['total_patterns']} total patterns")
    print(f"{'=' * 60}")

    # Output JSON for the API route to parse
    json_output = json.dumps(results)
    print(f"\n__JSON_OUTPUT__")
    print(json_output)

    return results


if __name__ == "__main__":
    main()
