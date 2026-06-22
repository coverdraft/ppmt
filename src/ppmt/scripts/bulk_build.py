#!/usr/bin/env python3
"""
PPMT Bulk Trie Builder — Build N1/N2/N3/N4 tries for ALL symbols+timeframes
that have OHLCV data in the database.

Usage (from repo root):
    # Build everything that has OHLCV data
    python3 scripts/bulk_build.py

    # Build only a specific symbol/timeframe
    python3 scripts/bulk_build.py --symbol SOL/USDT --timeframe 5m

    # Build only a specific timeframe for all symbols
    python3 scripts/bulk_build.py --timeframe 5m

    # Force rebuild (overwrite existing tries)
    python3 scripts/bulk_build.py --force

    # Clean all tries and rebuild from scratch
    python3 scripts/bulk_build.py --clean --timeframe 5m

    # Dry run (show what would be built without building)
    python3 scripts/bulk_build.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

# Ensure ppmt is importable when running as standalone script
# from the repo root: python3 scripts/bulk_build.py
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_dir = os.path.join(_repo_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from rich.console import Console
from rich.table import Table

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.ppmt import PPMT

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bulk_build")


def get_available_data(storage: PPMTStorage) -> list[dict]:
    """Get all symbol/timeframe combos that have OHLCV data."""
    cursor = storage._ensure_conn().cursor()
    cursor.execute(
        """SELECT symbol, timeframe, COUNT(*) as candle_count,
                  MIN(timestamp) as first_ts, MAX(timestamp) as last_ts
           FROM ohlcv
           GROUP BY symbol, timeframe
           ORDER BY symbol, timeframe"""
    )
    rows = cursor.fetchall()
    return [
        {
            "symbol": r[0],
            "timeframe": r[1],
            "candles": r[2],
            "first_ts": r[3],
            "last_ts": r[4],
        }
        for r in rows
    ]


def get_existing_tries(storage: PPMTStorage) -> dict[tuple[str, str, str], int]:
    """Get existing trie node counts: (symbol, timeframe, level) -> node_count."""
    cursor = storage._ensure_conn().cursor()
    try:
        cursor.execute(
            "SELECT symbol, timeframe, level FROM tries"
        )
        rows = cursor.fetchall()
        counts: dict[tuple[str, str, str], int] = {}
        for r in rows:
            key = (r[0], r[1], r[2])
            counts[key] = counts.get(key, 0) + 1
        return counts
    except Exception:
        return {}


def build_one(
    storage: PPMTStorage,
    symbol: str,
    timeframe: str,
    force: bool = False,
) -> dict:
    """Build tries for a single symbol/timeframe. Returns stats dict."""
    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "status": "skipped",
        "n1": 0,
        "n2": 0,
        "n3": 0,
        "n4": 0,
        "patterns_built": 0,
        "elapsed_s": 0.0,
    }

    # Load OHLCV data
    df = storage.load_ohlcv(symbol, timeframe)
    if df.empty:
        result["status"] = "no_data"
        return result

    # Check if tries already exist
    if not force:
        existing_n3 = storage.load_trie(symbol, "n3", timeframe=timeframe)
        if existing_n3 is not None and existing_n3.pattern_count > 0:
            result["status"] = "exists"
            result["n3"] = existing_n3.pattern_count
            return result

    # Classify asset
    classifier = AssetClassifier()
    info = classifier.classify(symbol)

    console.print(f"  [cyan]Building {symbol} {timeframe}[/cyan] "
                  f"({len(df)} candles, class={info.asset_class})")

    start = time.perf_counter()

    # Create engine WITH timeframe — this is the critical fix
    # that the CLI `ppmt build` was missing
    engine = PPMT(
        symbol=symbol,
        asset_class=info.asset_class,
        dual_sax=True,
        timeframe=timeframe,  # ← THIS IS THE KEY: enables per-TF windows
    )

    # Attach storage so N1/N2 get flushed to shared pools
    engine.attach_storage(storage)

    # Build
    count = engine.build(df)

    elapsed = time.perf_counter() - start

    # Save per-symbol tries (N3/N4) — N1/N2 already saved by build()
    # via the storage flush mechanism. But we also save N3/N4 explicitly
    # in case the storage flush didn't happen (no storage attached).
    # With storage attached, build() already calls save_trie for all levels.
    # So this is a safety net.
    result["patterns_built"] = count
    result["n1"] = engine.trie_n1.pattern_count
    result["n2"] = engine.trie_n2.pattern_count
    result["n3"] = engine.trie_n3.pattern_count
    result["n4"] = engine.trie_n4.pattern_count if hasattr(engine.trie_n4, 'pattern_count') else 0
    result["elapsed_s"] = round(elapsed, 2)
    result["status"] = "built"

    console.print(
        f"  [green]  Done: {count} patterns in {elapsed:.1f}s — "
        f"N1={result['n1']} N2={result['n2']} "
        f"N3={result['n3']} N4={result['n4']}[/green]"
    )

    return result


def main():
    parser = argparse.ArgumentParser(
        description="PPMT Bulk Trie Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--symbol", "-s", default=None, help="Build only this symbol")
    parser.add_argument("--timeframe", "-t", default=None, help="Build only this timeframe")
    parser.add_argument("--force", "-f", action="store_true", help="Rebuild even if tries exist")
    parser.add_argument("--clean", "-c", action="store_true", help="DELETE all tries before building (fresh start)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be built without building")
    args = parser.parse_args()

    storage = PPMTStorage()

    # --clean: wipe all tries before building
    if args.clean:
        cursor = storage._ensure_conn().cursor()
        cursor.execute("SELECT COUNT(*) FROM tries")
        old_count = cursor.fetchone()[0]
        if old_count > 0:
            cursor.execute("DELETE FROM tries")
            storage._ensure_conn().commit()
            console.print(f"[yellow]Cleaned {old_count} trie rows from database[/yellow]")
        else:
            console.print("[dim]No tries to clean[/dim]")

    # Discover available data
    available = get_available_data(storage)
    if not available:
        console.print("[red]No OHLCV data found in database. Run 'ppmt ingest' first.[/red]")
        storage.close()
        sys.exit(1)

    # Filter by CLI args
    if args.symbol:
        available = [d for d in available if d["symbol"] == args.symbol]
    if args.timeframe:
        available = [d for d in available if d["timeframe"] == args.timeframe]

    if not available:
        console.print("[red]No matching symbol/timeframe combinations found.[/red]")
        storage.close()
        sys.exit(1)

    # Show what we found
    console.print(f"\n[bold]PPMT Bulk Builder — {len(available)} symbol/timeframe combos found[/bold]\n")

    table = Table(title="Available OHLCV Data")
    table.add_column("Symbol", style="cyan")
    table.add_column("TF", style="yellow")
    table.add_column("Candles", justify="right")
    for d in available:
        table.add_row(d["symbol"], d["timeframe"], str(d["candles"]))
    console.print(table)

    if args.dry_run:
        console.print("\n[yellow]Dry run — no changes made.[/yellow]")
        storage.close()
        return

    # Build each combo
    results = []
    total_start = time.perf_counter()

    for d in available:
        r = build_one(
            storage,
            d["symbol"],
            d["timeframe"],
            force=args.force,
        )
        results.append(r)

    total_elapsed = time.perf_counter() - total_start

    # Summary
    console.print(f"\n[bold]Build Complete — {total_elapsed:.1f}s total[/bold]\n")

    summary = Table(title="Build Results")
    summary.add_column("Symbol", style="cyan")
    summary.add_column("TF", style="yellow")
    summary.add_column("Status")
    summary.add_column("N1", justify="right")
    summary.add_column("N2", justify="right")
    summary.add_column("N3", justify="right")
    summary.add_column("N4", justify="right")
    summary.add_column("Time", justify="right")

    for r in results:
        status_style = "green" if r["status"] == "built" else "yellow" if r["status"] == "exists" else "red"
        summary.add_row(
            r["symbol"],
            r["timeframe"],
            f"[{status_style}]{r['status']}[/{status_style}]",
            str(r["n1"]),
            str(r["n2"]),
            str(r["n3"]),
            str(r["n4"]),
            f"{r['elapsed_s']}s",
        )

    console.print(summary)

    built = sum(1 for r in results if r["status"] == "built")
    skipped = sum(1 for r in results if r["status"] == "exists")
    failed = sum(1 for r in results if r["status"] == "no_data")
    console.print(
        f"\n  Built: {built}  Skipped (existing): {skipped}  No data: {failed}"
    )

    storage.close()


if __name__ == "__main__":
    main()
