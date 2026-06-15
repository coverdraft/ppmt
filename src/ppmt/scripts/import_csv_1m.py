#!/usr/bin/env python3
"""
PPMT v0.12.0 — CSV Import for 1-Minute Historical Data

Imports 1-minute OHLCV data from CSV files into PPMT's SQLite storage.
Supports multiple CSV formats commonly used for crypto historical data:

  1. Generic CSV: timestamp, open, high, low, close, volume
  2. Binance export: open_time, open, high, low, close, volume, ...
  3. CryptoDataDownload: Unix, Date, Symbol, Open, High, Low, Close, ...
  4. CCXT format: timestamp, open, high, low, close, volume
  5. Custom: auto-detect column names with flexible mapping

Usage:
    # Single CSV file
    python -m ppmt.scripts.import_csv_1m \\
        --csv ./data/BTCUSDT_1m.csv \\
        --symbol BTC/USDT

    # Directory of CSVs (one per token)
    python -m ppmt.scripts.import_csv_1m \\
        --dir ./data/1m_csvs/ \\
        --timeframe 1m

    # With validation and stats
    python -m ppmt.scripts.import_csv_1m \\
        --csv ./data/BTCUSDT_1m.csv \\
        --symbol BTC/USDT \\
        --validate \\
        --stats

    # Specify format explicitly
    python -m ppmt.scripts.import_csv_1m \\
        --csv ./data/BTCUSDT_1m.csv \\
        --symbol BTC/USDT \\
        --format binance

CSV Format Detection:
    The script auto-detects the CSV format by examining column names.
    If auto-detection fails, use --format to specify:
      - generic: timestamp/datetime/date, open, high, low, close, volume
      - binance: open_time, open, high, low, close, volume, close_time, ...
      - cdd: Unix, Date, Symbol, Open, High, Low, Close, Volume USDT, ...
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent src to path so we can import ppmt when running as module
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd

from ppmt.data.collector import DataCollector
from ppmt.data.storage import PPMTStorage


# ============================================================
# CSV Format Detection & Parsing
# ============================================================

# Column name mappings for each format
FORMAT_COLUMNS = {
    "generic": {
        "timestamp": "timestamp",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
    },
    "binance": {
        "timestamp": "open_time",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
    },
    "cdd": {
        "timestamp": "Unix",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume USDT",
    },
}


def detect_csv_format(df: pd.DataFrame) -> str:
    """
    Auto-detect CSV format from column names.

    Examines the DataFrame columns to determine which format the CSV uses.
    Returns one of: 'generic', 'binance', 'cdd', or 'unknown'.
    """
    cols = set(df.columns.str.strip())
    cols_lower = set(c.lower() for c in cols)

    # Binance format: has open_time and close_time
    if "open_time" in cols_lower and "close_time" in cols_lower:
        return "binance"

    # CDD format: has Unix, Symbol, Date columns
    if "unix" in cols_lower and "symbol" in cols_lower:
        return "cdd"

    # Generic format: has timestamp or datetime + OHLCV
    if ("timestamp" in cols_lower or "datetime" in cols_lower or "date" in cols_lower):
        if all(c in cols_lower for c in ["open", "high", "low", "close"]):
            return "generic"

    # Try by position: if first column looks like a timestamp and we have 6+ columns
    if len(df.columns) >= 6:
        first_col = df.columns[0]
        first_val = df.iloc[0, 0] if len(df) > 0 else None
        if first_val is not None:
            try:
                val = float(first_val)
                # Unix millisecond timestamp (typical range: 2017-2026)
                if 1.5e12 > val > 1e12:
                    return "binance"
                # Unix seconds
                if 1.5e9 > val > 1e9:
                    return "generic"
            except (ValueError, TypeError):
                pass

    return "unknown"


def parse_csv(csv_path: str, fmt: str = "auto") -> pd.DataFrame:
    """
    Parse a CSV file into a standard OHLCV DataFrame.

    Args:
        csv_path: Path to the CSV file
        fmt: Format hint ('auto', 'generic', 'binance', 'cdd')

    Returns:
        DataFrame with columns [open, high, low, close, volume] and DatetimeIndex
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    # Read CSV
    df = pd.read_csv(csv_path)

    if df.empty:
        raise ValueError(f"CSV file is empty: {csv_path}")

    # Auto-detect format if not specified
    if fmt == "auto":
        fmt = detect_csv_format(df)
        if fmt == "unknown":
            # Fall back to generic parsing with column position heuristics
            fmt = "generic"
            print(f"  [yellow]Could not auto-detect CSV format, using 'generic'. "
                  f"Use --format to specify explicitly.[/yellow]")

    print(f"  Detected format: {fmt}")

    # Parse based on format
    if fmt == "binance":
        df = _parse_binance_csv(df)
    elif fmt == "cdd":
        df = _parse_cdd_csv(df)
    else:
        df = _parse_generic_csv(df)

    # Standard post-processing
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    return df


def _parse_generic_csv(df: pd.DataFrame) -> pd.DataFrame:
    """Parse generic CSV with timestamp/datetime/date column."""
    df.columns = [c.strip().lower() for c in df.columns]

    # Find timestamp column
    ts_col = None
    for candidate in ["timestamp", "datetime", "date", "time"]:
        if candidate in df.columns:
            ts_col = candidate
            break

    if ts_col is None:
        # Try first column
        ts_col = df.columns[0]

    # Parse timestamps
    try:
        ts_values = df[ts_col].astype(float)
        # Check if unix milliseconds (value > 1e12)
        if ts_values.iloc[0] > 1e12:
            df.index = pd.to_datetime(ts_values, unit="ms")
        elif ts_values.iloc[0] > 1e9:
            df.index = pd.to_datetime(ts_values, unit="s")
        else:
            df.index = pd.to_datetime(df[ts_col])
    except (ValueError, OverflowError):
        df.index = pd.to_datetime(df[ts_col])

    # Handle volume column variants
    if "volume" not in df.columns:
        for vol_candidate in ["vol", "volume_usdt", "volume_btc", "quote_volume"]:
            if vol_candidate in df.columns:
                df["volume"] = df[vol_candidate]
                break
        else:
            df["volume"] = 0.0  # Default if no volume column

    return df


def _parse_binance_csv(df: pd.DataFrame) -> pd.DataFrame:
    """Parse Binance-format CSV (open_time, open, high, low, close, volume, ...)."""
    # Binance klines format: open_time, open, high, low, close, volume,
    # close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, ignore

    # If columns are numeric (no header), assign names
    if all(isinstance(c, int) or c.isdigit() for c in df.columns[:3]):
        kline_cols = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ]
        # Only rename if column count matches
        if len(df.columns) <= len(kline_cols):
            df.columns = kline_cols[:len(df.columns)]

    df.columns = [c.strip().lower() for c in df.columns]

    # Parse open_time as timestamp
    df.index = pd.to_datetime(df["open_time"].astype(float).astype(int), unit="ms")

    # Use quote_volume as volume if volume is base currency
    if "quote_volume" in df.columns:
        df["volume"] = df["quote_volume"]

    return df


def _parse_cdd_csv(df: pd.DataFrame) -> pd.DataFrame:
    """Parse CryptoDataDownload CSV format."""
    # CDD has a URL row first — skip it
    first_row = df.iloc[0]
    try:
        float(first_row.iloc[0])  # If first column is numeric, it's data
    except (ValueError, TypeError):
        df = df.iloc[1:]  # Skip URL header row

    col_map = {
        "Unix": "timestamp",
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume USDT": "volume",
        "Volume BTC": "volume_btc",
        "Symbol": "symbol",
        "tradecount": "trades",
    }

    # Rename known columns
    for old, new in col_map.items():
        if old in df.columns:
            df = df.rename(columns={old: new})

    # Parse timestamp
    if "timestamp" in df.columns:
        df.index = pd.to_datetime(df["timestamp"].astype(float).astype(int), unit="ms")
    elif "date" in df.columns:
        df.index = pd.to_datetime(df["date"])

    return df


# ============================================================
# Validation & Statistics
# ============================================================

def validate_1m_data(df: pd.DataFrame, symbol: str = "") -> dict:
    """
    Validate 1-minute OHLCV data quality.

    Checks:
    - Time gaps (missing minutes)
    - Duplicate timestamps
    - Price consistency (high >= low, open/close within high-low range)
    - Zero/negative prices
    - Volume anomalies

    Returns dict with validation results.
    """
    issues = []
    total_rows = len(df)

    # 1. Duplicate timestamps
    dupes = df.index.duplicated().sum()
    if dupes > 0:
        issues.append(f"Duplicate timestamps: {dupes}")

    # 2. Time gaps (expected: exactly 1 minute between rows)
    if total_rows > 1:
        time_diffs = pd.Series(df.index).diff().dropna()
        expected_delta = pd.Timedelta(minutes=1)
        gaps = time_diffs[time_diffs > expected_delta * 1.5]
        if len(gaps) > 0:
            total_gap_minutes = sum(
                (g - expected_delta).total_seconds() / 60 for g in gaps
            )
            issues.append(f"Time gaps: {len(gaps)} gaps, "
                          f"{total_gap_minutes:.0f} minutes of missing data")

            # Report largest gap
            largest_gap = max(gaps)
            largest_gap_mins = (largest_gap - expected_delta).total_seconds() / 60
            issues.append(f"Largest gap: {largest_gap_mins:.0f} minutes "
                          f"({largest_gap})")

    # 3. Price consistency
    bad_hl = (df["high"] < df["low"]).sum()
    if bad_hl > 0:
        issues.append(f"High < Low: {bad_hl} rows")

    # Check open/close within high-low range
    bad_open = ((df["open"] > df["high"]) | (df["open"] < df["low"])).sum()
    bad_close = ((df["close"] > df["high"]) | (df["close"] < df["low"])).sum()
    if bad_open > 0:
        issues.append(f"Open outside H-L range: {bad_open} rows")
    if bad_close > 0:
        issues.append(f"Close outside H-L range: {bad_close} rows")

    # 4. Zero/negative prices
    for col in ["open", "high", "low", "close"]:
        bad = (df[col] <= 0).sum()
        if bad > 0:
            issues.append(f"Zero/negative {col}: {bad} rows")

    # 5. Volume anomalies
    zero_vol = (df["volume"] == 0).sum()
    if zero_vol > total_rows * 0.1:  # More than 10% zero volume
        issues.append(f"Zero volume: {zero_vol}/{total_rows} rows "
                      f"({zero_vol/total_rows*100:.1f}%)")

    return {
        "valid": len(issues) == 0,
        "total_rows": total_rows,
        "issues": issues,
        "symbol": symbol,
    }


def print_data_stats(df: pd.DataFrame, symbol: str = "") -> None:
    """Print detailed statistics about the imported data."""
    print(f"\n  Data Statistics: {symbol}")
    print(f"  {'='*50}")
    print(f"  Rows:      {len(df):,}")
    print(f"  Date range: {df.index[0]} → {df.index[-1]}")

    days_span = (df.index[-1] - df.index[0]).total_seconds() / 86400
    print(f"  Span:      {days_span:.1f} days")

    # Expected vs actual candles
    expected = int(days_span * 1440)  # 1440 minutes per day
    coverage = len(df) / max(expected, 1) * 100
    print(f"  Expected:  {expected:,} candles (at 1m)")
    print(f"  Coverage:  {coverage:.1f}%")

    # Price stats
    for col in ["close", "high", "low"]:
        vals = df[col].values
        print(f"  {col:>6}: min={np.min(vals):.4f}, "
              f"max={np.max(vals):.4f}, "
              f"mean={np.mean(vals):.4f}")

    # Volume stats
    vol = df["volume"].values
    print(f"  Volume:    min={np.min(vol):.2f}, "
          f"max={np.max(vol):.2f}, "
          f"mean={np.mean(vol):.2f}")

    # ATR-like volatility measure
    closes = df["close"].values
    if len(closes) > 14:
        returns = np.diff(closes) / closes[:-1] * 100
        volatility = np.std(returns) * np.sqrt(1440)  # Annualized
        print(f"  Volatility (annualized): {volatility:.1f}%")


# ============================================================
# Main Import Logic
# ============================================================

def import_csv_1m(
    csv_path: str,
    symbol: str,
    timeframe: str = "1m",
    fmt: str = "auto",
    validate: bool = False,
    stats: bool = False,
    dry_run: bool = False,
    merge: bool = False,
) -> pd.DataFrame:
    """
    Import a single CSV file of 1-minute data.

    Args:
        csv_path: Path to CSV file
        symbol: Trading pair (e.g. 'BTC/USDT')
        timeframe: Timeframe string (default '1m')
        fmt: CSV format ('auto', 'generic', 'binance', 'cdd')
        validate: Run data quality validation
        stats: Print detailed statistics
        dry_run: Parse and validate without saving
        merge: Merge with existing data instead of replacing

    Returns:
        DataFrame with imported data
    """
    print(f"\n  Importing: {csv_path}")
    print(f"  Symbol:    {symbol}")
    print(f"  Timeframe: {timeframe}")

    start_time = time.time()

    # Parse CSV
    df = parse_csv(csv_path, fmt=fmt)

    if df.empty:
        print(f"  [red]No data found in CSV[/red]")
        return df

    parse_time = time.time() - start_time
    print(f"  Parsed:    {len(df):,} rows in {parse_time:.1f}s")

    # Validate if requested
    if validate:
        val_result = validate_1m_data(df, symbol)
        if val_result["valid"]:
            print(f"  [green]Validation: PASSED ({val_result['total_rows']:,} rows)[/green]")
        else:
            print(f"  [yellow]Validation: ISSUES FOUND[/yellow]")
            for issue in val_result["issues"]:
                print(f"    - {issue}")

    # Print stats if requested
    if stats:
        print_data_stats(df, symbol)

    # Save to storage
    if not dry_run:
        storage = PPMTStorage()

        if merge:
            # Merge with existing data
            existing = storage.load_ohlcv(symbol, timeframe)
            if not existing.empty:
                df = pd.concat([existing, df])
                df = df[~df.index.duplicated(keep="last")]
                df = df.sort_index()
                print(f"  Merged:    {len(existing):,} existing + new = {len(df):,} total")

        storage.save_ohlcv(symbol, timeframe, df)
        storage.close()
        print(f"  [green]Saved to PPMT storage ({len(df):,} candles)[/green]")
    else:
        print(f"  [dim]DRY RUN — data not saved[/dim]")

    elapsed = time.time() - start_time
    print(f"  Completed in {elapsed:.1f}s")

    return df


def import_csv_directory(
    dir_path: str,
    timeframe: str = "1m",
    fmt: str = "auto",
    validate: bool = False,
    stats: bool = False,
    dry_run: bool = False,
    merge: bool = False,
) -> dict:
    """
    Import all CSV files from a directory.

    Expects filenames to contain the symbol name, e.g.:
    - BTCUSDT_1m.csv
    - BTC-USDT_1m.csv
    - BTC_USDT-1m.csv
    - btcusdt_1m.csv
    """
    if not os.path.isdir(dir_path):
        raise NotADirectoryError(f"Directory not found: {dir_path}")

    # Find all CSV files
    csv_files = sorted(Path(dir_path).glob("*.csv"))
    if not csv_files:
        print(f"  [yellow]No CSV files found in {dir_path}[/yellow]")
        return {}

    print(f"\n  Found {len(csv_files)} CSV files in {dir_path}")

    results = {}

    for csv_file in csv_files:
        filename = csv_file.stem  # filename without extension

        # Try to extract symbol from filename
        symbol = _extract_symbol_from_filename(filename)

        if not symbol:
            print(f"\n  [yellow]Skipping {filename}: could not determine symbol. "
                  f"Use --symbol to specify manually.[/yellow]")
            continue

        try:
            df = import_csv_1m(
                csv_path=str(csv_file),
                symbol=symbol,
                timeframe=timeframe,
                fmt=fmt,
                validate=validate,
                stats=stats,
                dry_run=dry_run,
                merge=merge,
            )
            results[symbol] = df
        except Exception as e:
            print(f"  [red]Error importing {filename}: {e}[/red]")

    # Summary
    print(f"\n  {'='*50}")
    print(f"  Import Summary: {len(results)}/{len(csv_files)} files imported")
    for sym, df in results.items():
        if not df.empty:
            days_span = (df.index[-1] - df.index[0]).total_seconds() / 86400
            print(f"    {sym:12} {len(df):>8,} candles  {days_span:>6.1f} days")

    return results


# ============================================================
# Symbol Extraction from Filename
# ============================================================

# Common quote currencies
QUOTE_CURRENCIES = ["USDT", "BUSD", "USD", "BTC", "ETH", "BNB"]

# Common base currencies for auto-detection
KNOWN_TOKENS = [
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC",
    "LINK", "UNI", "ATOM", "LTC", "FIL", "APT", "ARB", "OP", "NEAR", "SUI",
    "SHIB", "PEPE", "AAVE", "MKR", "COMP", "CRV", "SUSHI", "1INCH", "YFI", "SNX",
    "ALGO", "FTM", "IMX", "IMX", "SKL", "LRC", "CELO", "BOBA", "STRK", "WIF",
    "FLOKI", "BONK", "RENDER", "INJ", "TIA", "SEI", "JUP", "W", "ENA", "PENDLE",
]


def _extract_symbol_from_filename(filename: str) -> Optional[str]:
    """
    Extract trading pair symbol from a filename.

    Tries patterns like:
    - BTCUSDT_1m → BTC/USDT
    - BTC-USDT-1m → BTC/USDT
    - BTC_USDT_1m → BTC/USDT
    - btcusdt-1m → BTC/USDT
    - Binance_BTCUSDT_1m → BTC/USDT
    """
    # Remove common prefixes
    name = filename
    for prefix in ["Binance_", "binance_", "Bybit_", "bybit_", "OKX_", "okx_"]:
        if name.startswith(prefix):
            name = name[len(prefix):]

    # Remove timeframe suffixes
    import re
    name = re.sub(r'[_\-]?(1m|5m|15m|1h|4h|1d|1w)$', '', name, flags=re.IGNORECASE)

    # Try to split on common separators
    parts = re.split(r'[_\-]', name)

    if len(parts) >= 2:
        # Check if first part is a known token
        base = parts[0].upper()
        quote = parts[1].upper()

        if base in KNOWN_TOKENS and quote in QUOTE_CURRENCIES:
            return f"{base}/{quote}"

    # Try to find token in the full name
    upper_name = name.upper().replace("-", "").replace("_", "")

    for token in sorted(KNOWN_TOKENS, key=len, reverse=True):  # Longest first
        if upper_name.startswith(token):
            quote = upper_name[len(token):]
            if quote in QUOTE_CURRENCIES:
                return f"{token}/{quote}"

    return None


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="PPMT CSV Import for 1-Minute Historical Data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single CSV file
  python -m ppmt.scripts.import_csv_1m --csv BTCUSDT_1m.csv --symbol BTC/USDT

  # Directory of CSVs
  python -m ppmt.scripts.import_csv_1m --dir ./data/1m_csvs/

  # With validation and statistics
  python -m ppmt.scripts.import_csv_1m --csv data.csv --symbol BTC/USDT --validate --stats

  # Dry run (parse only, don't save)
  python -m ppmt.scripts.import_csv_1m --csv data.csv --symbol BTC/USDT --dry-run

  # Specify format explicitly
  python -m ppmt.scripts.import_csv_1m --csv data.csv --symbol BTC/USDT --format binance
        """,
    )

    # Input source (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--csv", type=str,
        help="Path to a single CSV file to import",
    )
    input_group.add_argument(
        "--dir", type=str,
        help="Path to directory containing CSV files to import",
    )

    # Options
    parser.add_argument(
        "--symbol", type=str, default="",
        help="Trading pair symbol (e.g. BTC/USDT). Required with --csv. "
             "Auto-detected from filename with --dir.",
    )
    parser.add_argument(
        "--timeframe", type=str, default="1m",
        help="Timeframe to store as (default: 1m)",
    )
    parser.add_argument(
        "--format", type=str, default="auto",
        choices=["auto", "generic", "binance", "cdd"],
        help="CSV format. 'auto' detects from column names (default: auto)",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate data quality after import",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print detailed data statistics",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and validate without saving to storage",
    )
    parser.add_argument(
        "--merge", action="store_true",
        help="Merge with existing data instead of replacing",
    )

    args = parser.parse_args()

    if args.csv:
        if not args.symbol:
            # Try auto-detect from filename
            symbol = _extract_symbol_from_filename(Path(args.csv).stem)
            if symbol:
                print(f"  Auto-detected symbol: {symbol}")
                args.symbol = symbol
            else:
                parser.error("--symbol is required when using --csv (could not auto-detect)")

        import_csv_1m(
            csv_path=args.csv,
            symbol=args.symbol,
            timeframe=args.timeframe,
            fmt=args.format,
            validate=args.validate,
            stats=args.stats,
            dry_run=args.dry_run,
            merge=args.merge,
        )
    else:
        import_csv_directory(
            dir_path=args.dir,
            timeframe=args.timeframe,
            fmt=args.format,
            validate=args.validate,
            stats=args.stats,
            dry_run=args.dry_run,
            merge=args.merge,
        )


if __name__ == "__main__":
    main()
