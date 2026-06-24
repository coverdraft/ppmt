"""
v7_materialize_short_features.py — One-time materialization of SHORT-filtered
features to disk (parquet format).

PROBLEM:
  - feature_observations_v6 stores features as JSON in `features_json` column
  - json_extract on 1.4M rows × 59 features = slow (~5min) and uses ~5GB RAM
  - Loading all data + v7_extras merge exceeds 8GB container memory limit
  - Solution: pay the migration cost ONCE, write to parquet, subsequent loads are fast

PIPELINE:
  1. Pre-load v7_extras into a per-symbol dict for fast lookup (150MB)
  2. Stream v6 features per-symbol (12 symbols × ~120K rows)
  3. For each symbol:
     - Load v6 features with json_extract
     - Merge with v7_extras slice for this symbol (small)
     - Filter to fwd_ret_3 < 0 (SHORT only — drops)
     - Convert numeric to float32 (halves memory vs float64)
     - Write to per-symbol parquet file
  4. At end: concat all per-symbol parquets into one master parquet (lazy, low memory)

USAGE:
    python /home/z/my-project/scripts/v7/v7_materialize_short_features.py
    # Output: data/v7_models/short_expert/short_features.parquet

After materialization, v7_train_short_expert.py loads the parquet file in ~1s.
"""
from __future__ import annotations

import gc
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Make v7 module importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Reuse feature list from SHORT trainer (imports below to avoid circular dep)
from v7_train_short_expert import (
    DB_PATH,
    FEATURE_NAMES_V6,
    FEATURE_NAMES_F4,
    FEATURE_NAMES,
    LABEL,
)

OUTPUT_DIR = Path("/home/z/my-project/data/v7_models/short_expert")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PARQUET_PATH = OUTPUT_DIR / "short_features.parquet"
PER_SYMBOL_DIR = OUTPUT_DIR / "_per_symbol"
PER_SYMBOL_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v7_materialize_short")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def load_v7_extras_per_symbol() -> Dict[str, pd.DataFrame]:
    """Load v7_extras grouped by symbol (12 small DataFrames, ~13MB each)."""
    LOG.info("Loading v7_extras per symbol...")
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT DISTINCT symbol FROM feature_observations_v7_extras ORDER BY symbol")
    symbols = [r[0] for r in cur.fetchall()]

    f4_cols_sql = ", ".join(FEATURE_NAMES_F4)
    result: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = pd.read_sql_query(
            f"SELECT ts, {f4_cols_sql} FROM feature_observations_v7_extras WHERE symbol = ?",
            conn,
            params=(sym,),
        )
        for f in FEATURE_NAMES_F4:
            df[f] = pd.to_numeric(df[f], errors="coerce").replace(
                [np.inf, -np.inf], 0
            ).fillna(0).astype(np.float32)
        df["ts"] = df["ts"].astype(np.int64)
        result[sym] = df
    conn.close()
    LOG.info("  loaded %d symbols in %.1fs", len(result), time.time() - t0)
    return result


def process_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    v7_slice: pd.DataFrame,
) -> Path:
    """Process one symbol: load v6 features, merge with v7, filter SHORT, write parquet."""
    t0 = time.time()
    v6_feat_cols = ", ".join(
        [f"json_extract(features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES_V6]
    )
    sql = f"""
        SELECT symbol, ts, window, {LABEL},
               {v6_feat_cols}
        FROM feature_observations_v6
        WHERE symbol = ? AND {LABEL} IS NOT NULL
    """
    chunk = pd.read_sql_query(sql, conn, params=(symbol,))

    # Merge with v7_extras slice (small, ~100K rows)
    chunk = chunk.merge(v7_slice, on="ts", how="inner")

    # Convert to float32 + clean numerics
    for f in FEATURE_NAMES + [LABEL]:
        chunk[f] = pd.to_numeric(chunk[f], errors="coerce").replace(
            [np.inf, -np.inf], 0
        ).fillna(0).astype(np.float32)
    chunk["ts"] = chunk["ts"].astype(np.int64)

    # Filter to SHORT (fwd_ret_3 < 0)
    n_before = len(chunk)
    chunk = chunk[chunk[LABEL] < 0].copy()
    n_short = len(chunk)

    # Write to per-symbol parquet
    out_path = PER_SYMBOL_DIR / f"{symbol}.parquet"
    chunk.to_parquet(out_path, engine="pyarrow", compression="zstd", index=False)
    LOG.info("  [%s] v6=%d → SHORT=%d (%.1f%%), wrote %s in %.1fs (%.1fMB)",
             symbol, n_before, n_short, 100 * n_short / max(n_before, 1),
             out_path.name, time.time() - t0,
             out_path.stat().st_size / 1e6)
    del chunk
    gc.collect()
    return out_path


def concat_parquets(per_symbol_paths: List[Path]) -> int:
    """Concat all per-symbol parquets into one master parquet."""
    LOG.info("Concatenating %d per-symbol parquets into master...", len(per_symbol_paths))
    t0 = time.time()

    # Use pyarrow ParquetWriter for efficient append
    schema = None
    writer = None
    n_total = 0
    for path in per_symbol_paths:
        table = pq.read_table(path)
        if writer is None:
            schema = table.schema
            writer = pq.ParquetWriter(str(PARQUET_PATH), schema, compression="zstd")
        writer.write_table(table)
        n_total += table.num_rows
        del table

    if writer is not None:
        writer.close()

    LOG.info("  wrote %d rows to %s in %.1fs (%.1f MB)",
             n_total, PARQUET_PATH.name, time.time() - t0,
             PARQUET_PATH.stat().st_size / 1e6)
    return n_total


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None,
                        help="Process only this symbol (default: all 12 sequentially)")
    parser.add_argument("--concat-only", action="store_true",
                        help="Skip per-symbol processing, just concat existing files")
    args = parser.parse_args()

    print("=" * 76)
    print("v7 MATERIALIZE SHORT FEATURES")
    print(f"  source: feature_observations_v6 + feature_observations_v7_extras")
    print(f"  filter: {LABEL} < 0 (SHORT only — drops)")
    print(f"  output: {PARQUET_PATH}")
    print(f"  features: {len(FEATURE_NAMES)} (59 v6 + 12 F4)")
    if args.symbol:
        print(f"  mode: SINGLE SYMBOL ({args.symbol})")
    elif args.concat_only:
        print(f"  mode: CONCAT ONLY (skip per-symbol processing)")
    else:
        print(f"  mode: ALL SYMBOLS (sequential)")
    print("=" * 76)

    if args.concat_only:
        # Just concat existing per-symbol files
        per_symbol_paths = sorted(PER_SYMBOL_DIR.glob("*.parquet"))
        if not per_symbol_paths:
            LOG.error("No per-symbol parquets found in %s", PER_SYMBOL_DIR)
            sys.exit(1)
        LOG.info("Concatenating %d existing per-symbol parquets...", len(per_symbol_paths))
        if PARQUET_PATH.exists():
            PARQUET_PATH.unlink()
        n_total = concat_parquets(per_symbol_paths)
    elif args.symbol:
        # Process one symbol
        v7_per_symbol = load_v7_extras_per_symbol()
        if args.symbol not in v7_per_symbol:
            LOG.error("Unknown symbol: %s", args.symbol)
            sys.exit(1)
        conn = sqlite3.connect(DB_PATH)
        process_symbol(conn, args.symbol, v7_per_symbol[args.symbol])
        conn.close()
        return
    else:
        # Process all symbols sequentially
        if PARQUET_PATH.exists():
            LOG.warning("Master parquet exists. Will be overwritten at concat step.")
        # Don't clear per-symbol dir — we want to be resumable

        v7_per_symbol = load_v7_extras_per_symbol()
        conn = sqlite3.connect(DB_PATH)

        per_symbol_paths: List[Path] = []
        for sym, v7_slice in v7_per_symbol.items():
            # Skip if already done (resumable)
            out_path = PER_SYMBOL_DIR / f"{sym}.parquet"
            if out_path.exists():
                LOG.info("  [%s] already exists, skipping (%.1fMB)",
                         sym, out_path.stat().st_size / 1e6)
                per_symbol_paths.append(out_path)
                continue
            try:
                path = process_symbol(conn, sym, v7_slice)
                per_symbol_paths.append(path)
            except Exception as e:
                LOG.error("[%s] FAILED: %s", sym, e)
                import traceback
                traceback.print_exc()
        conn.close()
        del v7_per_symbol
        gc.collect()

        n_total = concat_parquets(per_symbol_paths)

        # Cleanup per-symbol files
        for p in per_symbol_paths:
            p.unlink()
        try:
            PER_SYMBOL_DIR.rmdir()
        except OSError:
            pass

    # Verify (only when we have master parquet)
    if PARQUET_PATH.exists():
        LOG.info("Verifying master parquet...")
        t0 = time.time()
        df_check = pd.read_parquet(PARQUET_PATH)
        LOG.info("  loaded %d rows × %d cols in %.1fs",
                 len(df_check), len(df_check.columns), time.time() - t0)
        LOG.info("  windows: %s", df_check["window"].value_counts().to_dict())
        LOG.info("  label stats: mean=%.4f%% std=%.4f%% n_negative=%d",
                 float(df_check[LABEL].mean()), float(df_check[LABEL].std()),
                 int((df_check[LABEL] < 0).sum()))
        LOG.info("  symbol counts: %s", df_check["symbol"].value_counts().to_dict())
        del df_check

        print()
        print(f"DONE. Parquet file: {PARQUET_PATH}")
        print(f"  {n_total:,} SHORT rows")
        print(f"  {PARQUET_PATH.stat().st_size / 1e6:.1f} MB on disk")
        print(f"  Subsequent training runs load this in ~1s (vs ~5min from JSON)")


if __name__ == "__main__":
    main()
