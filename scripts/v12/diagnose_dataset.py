"""
diagnose_dataset.py — Quick diagnostic for V11 dataset issues.

Checks:
1. Dataset shape and columns
2. Timestamp range and type
3. Duplicate timestamps
4. Per-symbol row counts
5. Walk-forward split feasibility
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_ROOT / "data" / "v11" / "v11_dataset.parquet"

def main():
    if not DATASET_PATH.exists():
        print(f"ERROR: Dataset not found at {DATASET_PATH}")
        print("Run: python scripts/v11/v11_build_dataset.py")
        return

    print("Loading dataset...")
    df = pd.read_parquet(DATASET_PATH)
    print(f"Shape: {df.shape}")
    print(f"Columns ({len(df.columns)}): {list(df.columns[:20])}...")

    # Check timestamp
    print(f"\n--- TIMESTAMP ---")
    print(f"  dtype: {df['timestamp'].dtype}")
    print(f"  first: {df['timestamp'].iloc[0]} (type: {type(df['timestamp'].iloc[0])})")
    print(f"  last:  {df['timestamp'].iloc[-1]} (type: {type(df['timestamp'].iloc[-1])})")

    ts = df["timestamp"].values
    ts_first, ts_last = ts[0], ts[-1]

    # Check if timestamp is int64 ms
    if isinstance(ts_first, (int, np.integer)):
        span_ms = ts_last - ts_first
        span_days = span_ms / (1000 * 86400)
        print(f"  span: {span_days:.1f} days")
    elif isinstance(ts_first, pd.Timestamp):
        span = ts_last - ts_first
        span_days = span.total_seconds() / 86400
        print(f"  span: {span_days:.1f} days (Timestamp type)")
    else:
        print(f"  UNEXPECTED type: {type(ts_first)}")

    # Duplicates
    n_dup = df.duplicated(subset=["timestamp", "symbol"]).sum()
    n_dup_ts = df.duplicated(subset=["timestamp"]).sum()
    print(f"\n--- DUPLICATES ---")
    print(f"  Duplicate (timestamp, symbol): {n_dup}")
    print(f"  Duplicate timestamp (any symbol): {n_dup_ts}")
    print(f"  Unique timestamps: {df['timestamp'].nunique()}")

    # Per symbol
    print(f"\n--- PER SYMBOL ---")
    for sym in df["symbol"].unique():
        sym_df = df[df["symbol"] == sym]
        n_rows = len(sym_df)
        n_unique_ts = sym_df["timestamp"].nunique()
        print(f"  {sym}: {n_rows:,} rows, {n_unique_ts:,} unique timestamps, "
              f"ratio={n_rows/max(n_unique_ts,1):.2f}")

        # Check timestamp range
        sym_ts = sym_df["timestamp"].values
        if isinstance(sym_ts[0], (int, np.integer)):
            first_dt = pd.to_datetime(sym_ts[0], unit="ms")
            last_dt = pd.to_datetime(sym_ts[-1], unit="ms")
            span_d = (sym_ts[-1] - sym_ts[0]) / (1000 * 86400)
        else:
            first_dt = sym_ts[0]
            last_dt = sym_ts[-1]
            span_d = (sym_ts[-1] - sym_ts[0]).total_seconds() / 86400

        print(f"       range: {first_dt} to {last_dt} ({span_d:.1f} days)")

    # Check label columns
    print(f"\n--- LABELS ---")
    for col in df.columns:
        if col.startswith("label_h"):
            n_valid = df[col].notna().sum()
            n_pos = (df[col] == 1).sum()
            print(f"  {col}: {n_valid:,} valid, {n_pos:,} positive ({n_pos/max(n_valid,1)*100:.1f}%)")
        elif col.startswith("fwd_ret_h"):
            n_valid = df[col].notna().sum()
            n_nan = df[col].isna().sum()
            print(f"  {col}: {n_valid:,} valid, {n_nan:,} NaN")

    # Walk-forward split simulation
    print(f"\n--- WALK-FORWARD FEASIBILITY ---")
    for sym in df["symbol"].unique():
        sym_df = df[df["symbol"] == sym].copy()
        label_col = "label_h12"
        if label_col not in sym_df.columns:
            print(f"  {sym}: label_h12 not found!")
            continue

        valid_df = sym_df[sym_df[label_col].notna()].reset_index(drop=True)
        print(f"  {sym}: {len(valid_df):,} valid rows for label_h12")

        if len(valid_df) < 500:
            print(f"    NOT ENOUGH DATA (< 500)")
            continue

        ts = valid_df["timestamp"].values
        ts_first, ts_last = ts[0], ts[-1]

        if isinstance(ts_first, (int, np.integer)):
            span_ms = ts_last - ts_first
            span_days = span_ms / (1000 * 86400)
        elif isinstance(ts_first, pd.Timestamp):
            span_days = (ts_last - ts_first).total_seconds() / 86400
        else:
            print(f"    Cannot compute span — unexpected type: {type(ts_first)}")
            continue

        test_days = max(span_days * 0.07, 0.5)
        print(f"    span={span_days:.1f} days, test_window={test_days:.1f} days")

        for w in range(4):
            if isinstance(ts_first, (int, np.integer)):
                offset_ms = int(w * test_days * 86400 * 1000)
                test_end_ts = ts_last - offset_ms
                test_start_ts = test_end_ts - int(test_days * 86400 * 1000)
            else:
                offset = pd.Timedelta(days=w * test_days)
                test_end_ts = ts_last - offset
                test_start_ts = test_end_ts - pd.Timedelta(days=test_days)

            if isinstance(ts_first, (int, np.integer)):
                if test_start_ts <= ts_first:
                    print(f"    Window {w+1}: test_start <= data_start, BREAK")
                    break
            else:
                if test_start_ts <= ts_first:
                    print(f"    Window {w+1}: test_start <= data_start, BREAK")
                    break

            train_df = valid_df[valid_df["timestamp"] < test_start_ts]
            test_df = valid_df[(valid_df["timestamp"] >= test_start_ts) & (valid_df["timestamp"] < test_end_ts)]

            print(f"    Window {w+1}: train={len(train_df):,}, test={len(test_df):,}, "
                  f"train_ok={len(train_df) >= 500}, test_ok={len(test_df) >= 50}")


if __name__ == "__main__":
    main()
