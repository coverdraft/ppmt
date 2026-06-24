"""
v7_materialize_f6_features.py — F6: Materialize LONG & SHORT parquets with trie features.

Extends F5a/F5b materialization to include the 25 trie features extracted by
v7_extract_trie_features.py (stored in feature_observations_v7_trie).

OUTPUT (96 features per row, was 71):
  - 59 v6 base features
  - 12 F4 extras (funding_rate, oi, sector_one_hot, day_of_week)
  - 25 trie features (n1/n2 × seq_len 3/5 + aggregates)
  - LABEL (fwd_ret_3) + metadata (symbol, ts, window, vol_regime)

PIPELINE:
  1. For each of 12 symbols:
     a. Load v6 features (json_extract from feature_observations_v6)
     b. LEFT JOIN with v7_extras (F4 features) ON ts
     c. LEFT JOIN with v7_trie (25 trie features) ON (symbol, ts)
     d. Filter to LONG (fwd_ret_3 > 0) OR SHORT (fwd_ret_3 < 0)
     e. Convert to float32, write per-symbol parquet
  2. Concat all per-symbol parquets into master parquet

ANTI-LEAKAGE:
  - Trie features at time T contain outcomes from rows with ts < T only
    (enforced by v7_trie_conflict.py INSERT-AFTER-PREDICT)
  - Encoders were fit on a fixed training snapshot (frozen at inference)
  - vol_ma20 uses closed='left' (anti-lookahead)

USAGE:
    python /home/z/my-project/scripts/v7/v7_materialize_f6_features.py
    # Outputs:
    #   data/v7_models/long_expert/long_features_f6.parquet  (96 features, LONG)
    #   data/v7_models/short_expert/short_features_f6.parquet (96 features, SHORT)
"""
from __future__ import annotations

import gc
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Make v7 module importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Reuse feature list from F5a trainer
from v7_train_long_expert import (
    DB_PATH,
    FEATURE_NAMES_V6,
    FEATURE_NAMES_F4,
    LABEL,
)
from v7_trie_conflict import TRIE_FEATURE_NAMES as FEATURE_NAMES_TRIE

# Final F6 feature list (96 features = 59 + 12 + 25)
FEATURE_NAMES = FEATURE_NAMES_V6 + FEATURE_NAMES_F4 + FEATURE_NAMES_TRIE
assert len(FEATURE_NAMES) == 96, f"Expected 96 features, got {len(FEATURE_NAMES)}"

LONG_OUTPUT_DIR = Path("/home/z/my-project/data/v7_models/long_expert")
SHORT_OUTPUT_DIR = Path("/home/z/my-project/data/v7_models/short_expert")
LONG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SHORT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LONG_PARQUET = LONG_OUTPUT_DIR / "long_features_f6.parquet"
SHORT_PARQUET = SHORT_OUTPUT_DIR / "short_features_f6.parquet"
LONG_PER_SYMBOL_DIR = LONG_OUTPUT_DIR / "_per_symbol_f6"
SHORT_PER_SYMBOL_DIR = SHORT_OUTPUT_DIR / "_per_symbol_f6"
LONG_PER_SYMBOL_DIR.mkdir(parents=True, exist_ok=True)
SHORT_PER_SYMBOL_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v7_materialize_f6")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def load_v7_extras_per_symbol() -> Dict[str, pd.DataFrame]:
    """Load v7_extras grouped by symbol (12 small DataFrames)."""
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


def load_v7_trie_per_symbol(symbol: str) -> pd.DataFrame:
    """Load trie features for one symbol (25 columns)."""
    conn = sqlite3.connect(DB_PATH)
    trie_cols_sql = ", ".join(FEATURE_NAMES_TRIE)
    df = pd.read_sql_query(
        f"SELECT ts, {trie_cols_sql} FROM feature_observations_v7_trie WHERE symbol = ?",
        conn,
        params=(symbol,),
    )
    conn.close()
    for f in FEATURE_NAMES_TRIE:
        df[f] = pd.to_numeric(df[f], errors="coerce").replace(
            [np.inf, -np.inf], 0
        ).fillna(0).astype(np.float32)
    df["ts"] = df["ts"].astype(np.int64)
    return df


def process_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    v7_extras_slice: pd.DataFrame,
) -> Tuple[Path, Path, int, int]:
    """Process one symbol: load v6+v7_extras+v7_trie, split LONG/SHORT, write parquets.

    Returns:
        (long_path, short_path, n_long, n_short)
    """
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

    # Merge with v7_extras (F4 features)
    chunk = chunk.merge(v7_extras_slice, on="ts", how="inner")

    # Merge with v7_trie (25 trie features)
    trie_df = load_v7_trie_per_symbol(symbol)
    chunk = chunk.merge(trie_df, on="ts", how="left")
    # Fill missing trie rows with 0 (shouldn't happen — trie table covers all rows)
    for f in FEATURE_NAMES_TRIE:
        if f not in chunk.columns:
            chunk[f] = 0.0
        chunk[f] = chunk[f].fillna(0).astype(np.float32)

    # Convert to float32 + clean numerics
    for f in FEATURE_NAMES + [LABEL]:
        chunk[f] = pd.to_numeric(chunk[f], errors="coerce").replace(
            [np.inf, -np.inf], 0
        ).fillna(0).astype(np.float32)
    chunk["ts"] = chunk["ts"].astype(np.int64)

    # Split LONG / SHORT
    n_total = len(chunk)
    long_chunk = chunk[chunk[LABEL] > 0].copy()
    short_chunk = chunk[chunk[LABEL] < 0].copy()
    n_long = len(long_chunk)
    n_short = len(short_chunk)

    # Write per-symbol parquets
    long_path = LONG_PER_SYMBOL_DIR / f"{symbol}.parquet"
    short_path = SHORT_PER_SYMBOL_DIR / f"{symbol}.parquet"
    long_chunk.to_parquet(long_path, engine="pyarrow", compression="zstd", index=False)
    short_chunk.to_parquet(short_path, engine="pyarrow", compression="zstd", index=False)
    LOG.info(
        "  [%s] total=%d → LONG=%d (%.1f%%), SHORT=%d (%.1f%%) in %.1fs",
        symbol, n_total,
        n_long, 100 * n_long / max(n_total, 1),
        n_short, 100 * n_short / max(n_total, 1),
        time.time() - t0,
    )
    del chunk, long_chunk, short_chunk, trie_df
    gc.collect()
    return long_path, short_path, n_long, n_short


def concat_parquets(per_symbol_paths: List[Path], output_path: Path) -> int:
    """Concat per-symbol parquets into master parquet."""
    LOG.info("Concatenating %d per-symbol parquets → %s ...", len(per_symbol_paths), output_path.name)
    t0 = time.time()

    if output_path.exists():
        output_path.unlink()

    schema = None
    writer = None
    n_total = 0
    for path in per_symbol_paths:
        table = pq.read_table(path)
        if writer is None:
            schema = table.schema
            writer = pq.ParquetWriter(str(output_path), schema, compression="zstd")
        writer.write_table(table)
        n_total += table.num_rows
        del table

    if writer is not None:
        writer.close()

    LOG.info(
        "  wrote %d rows to %s in %.1fs (%.1f MB)",
        n_total, output_path.name, time.time() - t0,
        output_path.stat().st_size / 1e6,
    )
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
    print("v7 MATERIALIZE F6 FEATURES (LONG + SHORT with 25 trie features)")
    print(f"  source: feature_observations_v6 + v7_extras + v7_trie")
    print(f"  filter: LONG = {LABEL} > 0, SHORT = {LABEL} < 0")
    print(f"  output: {LONG_PARQUET}")
    print(f"          {SHORT_PARQUET}")
    print(f"  features: {len(FEATURE_NAMES)} (59 v6 + 12 F4 + 25 trie)")
    if args.symbol:
        print(f"  mode: SINGLE SYMBOL ({args.symbol})")
    elif args.concat_only:
        print(f"  mode: CONCAT ONLY")
    else:
        print(f"  mode: ALL SYMBOLS (sequential)")
    print("=" * 76)

    if args.concat_only:
        long_paths = sorted(LONG_PER_SYMBOL_DIR.glob("*.parquet"))
        short_paths = sorted(SHORT_PER_SYMBOL_DIR.glob("*.parquet"))
        if not long_paths or not short_paths:
            LOG.error("No per-symbol parquets found")
            sys.exit(1)
        n_long = concat_parquets(long_paths, LONG_PARQUET)
        n_short = concat_parquets(short_paths, SHORT_PARQUET)
    elif args.symbol:
        v7_per_symbol = load_v7_extras_per_symbol()
        if args.symbol not in v7_per_symbol:
            LOG.error("Unknown symbol: %s", args.symbol)
            sys.exit(1)
        conn = sqlite3.connect(DB_PATH)
        process_symbol(conn, args.symbol, v7_per_symbol[args.symbol])
        conn.close()
        return
    else:
        if LONG_PARQUET.exists():
            LOG.warning("Master LONG parquet exists. Will be overwritten at concat step.")
        if SHORT_PARQUET.exists():
            LOG.warning("Master SHORT parquet exists. Will be overwritten at concat step.")

        v7_per_symbol = load_v7_extras_per_symbol()
        conn = sqlite3.connect(DB_PATH)

        long_paths: List[Path] = []
        short_paths: List[Path] = []
        for sym, v7_slice in v7_per_symbol.items():
            long_path = LONG_PER_SYMBOL_DIR / f"{sym}.parquet"
            short_path = SHORT_PER_SYMBOL_DIR / f"{sym}.parquet"
            if long_path.exists() and short_path.exists():
                LOG.info("  [%s] already exists, skipping", sym)
                long_paths.append(long_path)
                short_paths.append(short_path)
                continue
            try:
                lp, sp, _, _ = process_symbol(conn, sym, v7_slice)
                long_paths.append(lp)
                short_paths.append(sp)
            except Exception as e:
                LOG.error("[%s] FAILED: %s", sym, e)
                import traceback
                traceback.print_exc()
        conn.close()
        del v7_per_symbol
        gc.collect()

        n_long = concat_parquets(long_paths, LONG_PARQUET)
        n_short = concat_parquets(short_paths, SHORT_PARQUET)

        # Cleanup per-symbol files
        for p in long_paths + short_paths:
            p.unlink()
        try:
            LONG_PER_SYMBOL_DIR.rmdir()
            SHORT_PER_SYMBOL_DIR.rmdir()
        except OSError:
            pass

    # Verify
    if LONG_PARQUET.exists():
        LOG.info("Verifying LONG parquet...")
        df = pd.read_parquet(LONG_PARQUET)
        LOG.info(
            "  LONG: %d rows × %d cols, windows=%s, label mean=%.4f%%",
            len(df), len(df.columns), df["window"].value_counts().to_dict(),
            float(df[LABEL].mean()),
        )
        # Check trie features non-zero
        trie_nz = (df[FEATURE_NAMES_TRIE] != 0).any(axis=1).sum()
        LOG.info(
            "  trie features: %d/%d rows have non-zero trie signal (%.1f%%)",
            trie_nz, len(df), 100 * trie_nz / max(len(df), 1),
        )
        del df

    if SHORT_PARQUET.exists():
        LOG.info("Verifying SHORT parquet...")
        df = pd.read_parquet(SHORT_PARQUET)
        LOG.info(
            "  SHORT: %d rows × %d cols, windows=%s, label mean=%.4f%%",
            len(df), len(df.columns), df["window"].value_counts().to_dict(),
            float(df[LABEL].mean()),
        )
        trie_nz = (df[FEATURE_NAMES_TRIE] != 0).any(axis=1).sum()
        LOG.info(
            "  trie features: %d/%d rows have non-zero trie signal (%.1f%%)",
            trie_nz, len(df), 100 * trie_nz / max(len(df), 1),
        )
        del df

    print()
    print(f"DONE.")
    print(f"  LONG:  {n_long:,} rows, {LONG_PARQUET}")
    print(f"  SHORT: {n_short:,} rows, {SHORT_PARQUET}")


if __name__ == "__main__":
    main()
