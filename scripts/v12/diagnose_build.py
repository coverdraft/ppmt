"""
diagnose_build.py — Diagnose why SOL/AVAX fail in v11_build_dataset.py

Checks:
  1. Do 1m data files exist for all symbols?
  2. What format are the timestamps? (ms, seconds, datetime?)
  3. How many rows and what date range?
  4. Try building 5m aggregation for each symbol
  5. Try the full build pipeline for one symbol (SOL) with verbose output

Usage:
    python scripts/v12/diagnose_build.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "v10" / "ohlcv_cache"
DATA_DIR = PROJECT_ROOT / "data"

SYMBOLS = ["SOL", "DOGE", "AVAX", "BTC", "ETH"]


def check_1m_data():
    """Check 1m data files existence and format."""
    print("=" * 70)
    print("STEP 1: Check 1m data files")
    print("=" * 70)

    for sym in SYMBOLS:
        path = CACHE_DIR / f"{sym}_1m.parquet"
        if not path.exists():
            print(f"  {sym}: FILE NOT FOUND at {path}")
            continue

        try:
            df = pd.read_parquet(path)
            ts = df["timestamp"]

            # Detect format
            dtype = ts.dtype
            median_val = ts.median()
            min_val = ts.min()
            max_val = ts.max()

            if pd.api.types.is_datetime64_any_dtype(ts):
                fmt = "datetime64"
                span_days = (max_val - min_val).total_seconds() / 86400
            elif median_val > 1e12:
                fmt = "int64 ms"
                span_days = (max_val - min_val) / (1000 * 86400)
            elif median_val > 1e9:
                fmt = "int64 seconds"
                span_days = (max_val - min_val) / 86400
            else:
                fmt = f"UNKNOWN (median={median_val:.0f})"
                span_days = 0

            # Show columns
            cols = df.columns.tolist()

            print(f"  {sym}: {len(df):,} rows, format={fmt}, dtype={dtype}")
            print(f"    timestamp range: {min_val} to {max_val}")
            print(f"    span: {span_days:.1f} days")
            print(f"    columns: {cols}")

            # Sample values
            print(f"    first 3 rows:")
            for i in range(min(3, len(df))):
                print(f"      ts={ts.iloc[i]}, close={df['close'].iloc[i]:.4f}")

        except Exception as e:
            print(f"  {sym}: ERROR reading file: {e}")
            traceback.print_exc()

    print()


def check_5m_aggregation():
    """Try aggregating 1m → 5m for each symbol."""
    print("=" * 70)
    print("STEP 2: Test 1m → 5m aggregation")
    print("=" * 70)

    for sym in SYMBOLS:
        path = CACHE_DIR / f"{sym}_1m.parquet"
        if not path.exists():
            print(f"  {sym}: SKIP (no 1m data)")
            continue

        try:
            df = pd.read_parquet(path)
            ts = df["timestamp"]

            # Normalize timestamps
            if pd.api.types.is_datetime64_any_dtype(ts):
                df["timestamp"] = df["timestamp"].astype(np.int64) // 10**6
            elif ts.median() > 1e12:
                df["timestamp"] = df["timestamp"].astype(np.int64)
            elif ts.median() > 1e9:
                df["timestamp"] = (df["timestamp"] * 1000).astype(np.int64)
            else:
                print(f"  {sym}: Cannot normalize timestamps (median={ts.median():.0f})")
                continue

            # Convert to datetime for resample
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.set_index("timestamp")

            agg = df.resample("5min").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna()

            print(f"  {sym}: {len(agg):,} 5m bars from {len(df):,} 1m bars")

            # Check for duplicate timestamps after aggregation
            agg = agg.reset_index()
            dupes = agg["timestamp"].duplicated().sum()
            if dupes > 0:
                print(f"    WARNING: {dupes} duplicate 5m timestamps!")
            else:
                print(f"    No duplicate timestamps ✓")

        except Exception as e:
            print(f"  {sym}: ERROR in 5m aggregation: {e}")
            traceback.print_exc()

    print()


def check_timestamp_overlap():
    """Check if SOL/DOGE/AVAX 5m timestamps overlap with BTC."""
    print("=" * 70)
    print("STEP 3: Check timestamp overlap between symbols and BTC")
    print("=" * 70)

    # Load and aggregate BTC 5m
    btc_path = CACHE_DIR / "BTC_1m.parquet"
    if not btc_path.exists():
        print("  BTC data not found — cannot check overlap")
        return

    btc_df = pd.read_parquet(btc_path)
    btc_ts = btc_df["timestamp"]
    if btc_ts.median() > 1e12:
        btc_df["timestamp"] = btc_ts.astype(np.int64)
    elif btc_ts.median() > 1e9:
        btc_df["timestamp"] = (btc_ts * 1000).astype(np.int64)

    btc_df["timestamp"] = pd.to_datetime(btc_df["timestamp"], unit="ms", utc=True)
    btc_df = btc_df.set_index("timestamp")
    btc_5m = btc_df.resample("5min").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna().reset_index()

    btc_5m_ts = set(btc_5m["timestamp"])
    print(f"  BTC: {len(btc_5m):,} 5m bars")

    for sym in ["SOL", "DOGE", "AVAX"]:
        path = CACHE_DIR / f"{sym}_1m.parquet"
        if not path.exists():
            print(f"  {sym}: SKIP (no data)")
            continue

        df = pd.read_parquet(path)
        ts = df["timestamp"]
        if ts.median() > 1e12:
            df["timestamp"] = ts.astype(np.int64)
        elif ts.median() > 1e9:
            df["timestamp"] = (ts * 1000).astype(np.int64)

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        sym_5m = df.resample("5min").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna().reset_index()

        sym_5m_ts = set(sym_5m["timestamp"])
        overlap = sym_5m_ts & btc_5m_ts
        sym_only = sym_5m_ts - btc_5m_ts
        btc_only = btc_5m_ts - sym_5m_ts

        print(f"  {sym}: {len(sym_5m):,} 5m bars")
        print(f"    Overlap with BTC: {len(overlap):,} timestamps ({len(overlap)/max(len(sym_5m_ts),1)*100:.1f}%)")
        print(f"    {sym}-only timestamps: {len(sym_only):,}")
        print(f"    BTC-only timestamps: {len(btc_only):,}")

        if len(sym_only) > 0:
            # Show some sym-only timestamps
            sorted_only = sorted(sym_only)[:5]
            print(f"    First sym-only timestamps: {sorted_only}")

    print()


def try_full_build():
    """Try the full build pipeline for SOL with verbose error reporting."""
    print("=" * 70)
    print("STEP 4: Try full build for SOL (verbose)")
    print("=" * 70)

    # Import the v2 build script
    sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "v11"))
    try:
        from v11_build_dataset import (
            load_1m_data,
            compute_microstructure_features,
            aggregate_1m_to_5m,
            compute_5m_features,
            compute_higher_tf_features,
            merge_micro_into_5m,
            compute_labels,
            normalize_timestamps_ms,
            ALL_FEATURE_NAMES,
        )
        print("  Imported v11_build_dataset ✓")
    except ImportError as e:
        print(f"  Cannot import v11_build_dataset: {e}")
        return

    # Load BTC
    try:
        print("\n  Loading BTC 1m data...")
        btc_1m = load_1m_data("BTC")
        print(f"  BTC loaded: {len(btc_1m):,} rows")
    except Exception as e:
        print(f"  ERROR loading BTC: {e}")
        traceback.print_exc()
        return

    # Try SOL step by step
    for sym in ["SOL", "AVAX"]:
        print(f"\n  --- Building {sym} step by step ---")
        try:
            print(f"  1. Loading {sym} 1m data...")
            sym_1m = load_1m_data(sym)
            print(f"     {sym} loaded: {len(sym_1m):,} rows")
        except Exception as e:
            print(f"     ERROR: {e}")
            traceback.print_exc()
            continue

        try:
            print(f"  2. Computing microstructure features...")
            sym_1m_micro = compute_microstructure_features(sym_1m)
            print(f"     {len(sym_1m_micro):,} rows with micro features")
        except Exception as e:
            print(f"     ERROR: {e}")
            traceback.print_exc()
            continue

        try:
            print(f"  3. Aggregating to 5m...")
            sym_5m = aggregate_1m_to_5m(sym_1m)
            btc_5m = aggregate_1m_to_5m(btc_1m)
            print(f"     {sym}: {len(sym_5m):,} 5m bars, BTC: {len(btc_5m):,} 5m bars")
        except Exception as e:
            print(f"     ERROR: {e}")
            traceback.print_exc()
            continue

        try:
            print(f"  4. ETH proxy setup...")
            eth_5m = btc_5m[["timestamp", "close"]].copy()
            eth_5m = normalize_timestamps_ms(eth_5m, "timestamp")
            sym_5m = normalize_timestamps_ms(sym_5m, "timestamp")
            btc_5m = normalize_timestamps_ms(btc_5m, "timestamp")
            print(f"     {sym}: {len(sym_5m):,} rows, ETH proxy: {len(eth_5m):,} rows")
        except Exception as e:
            print(f"     ERROR: {e}")
            traceback.print_exc()
            continue

        try:
            print(f"  5. Computing 5m features (BTC/ETH merge)...")
            sym_5m_feat = compute_5m_features(sym_5m, btc_5m, eth_5m)
            print(f"     After 5m features: {len(sym_5m_feat):,} rows")
        except Exception as e:
            print(f"     ERROR: {e}")
            traceback.print_exc()
            continue

        try:
            print(f"  6. Computing higher TF features...")
            sym_5m_feat = compute_higher_tf_features(sym_5m_feat, btc_5m)
            print(f"     After HTF features: {len(sym_5m_feat):,} rows")
        except Exception as e:
            print(f"     ERROR: {e}")
            traceback.print_exc()
            continue

        try:
            print(f"  7. Merging micro features...")
            sym_5m_full = merge_micro_into_5m(sym_5m_feat, sym_1m_micro)
            print(f"     After micro merge: {len(sym_5m_full):,} rows")
        except Exception as e:
            print(f"     ERROR: {e}")
            traceback.print_exc()
            continue

        try:
            print(f"  8. Computing labels...")
            sym_5m_full = compute_labels(sym_5m_full, [12, 36, 72, 288])
            print(f"     After labels: {len(sym_5m_full):,} rows")
            print(f"     Label h12 valid: {sym_5m_full['label_h12'].notna().sum()}")
        except Exception as e:
            print(f"     ERROR: {e}")
            traceback.print_exc()
            continue

        # Check for NaN features
        nan_counts = sym_5m_full[ALL_FEATURE_NAMES].isna().sum()
        bad_features = nan_counts[nan_counts > 0]
        if len(bad_features) > 0:
            print(f"  WARNING: NaN features found:")
            for col, cnt in bad_features.items():
                print(f"    {col}: {cnt} NaN values")
        else:
            print(f"  All features are valid ✓")

        # Final row count
        valid_mask = sym_5m_full[[f"fwd_ret_h{h}" for h in [12, 36, 72, 288]]].notna().any(axis=1)
        feat_mask = sym_5m_full[ALL_FEATURE_NAMES].notna().all(axis=1)
        valid_rows = (valid_mask & feat_mask).sum()
        print(f"  Valid rows after filtering: {valid_rows:,} / {len(sym_5m_full):,}")

    print()


if __name__ == "__main__":
    check_1m_data()
    check_5m_aggregation()
    check_timestamp_overlap()
    try_full_build()
