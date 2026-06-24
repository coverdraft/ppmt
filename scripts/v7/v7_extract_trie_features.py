"""
v7_extract_trie_features.py — F6: Bulk-extract 25 trie features per row.

For each row in feature_observations_v6 (1.41M rows × 12 symbols):
  1. Query trie (built incrementally from prior rows of the same sector)
  2. Insert this row's outcome (fwd_ret_3) into trie
  3. Store 25 trie features in new SQLite table feature_observations_v7_trie

PIPELINE:
  - For each of 12 symbols (chronological order):
    - For each row (sorted by ts):
      - Load last 15 candles from ohlcv_v6 ending at row.ts
      - Encode with sector encoder → key per seq_len
      - Query trie → 25 features (or 15 for new_meme which only has seq_len=5)
      - Insert (key, fwd_ret_3, vol_regime, ts) into trie (INSERT-AFTER-PREDICT)
  - Concat all per-symbol results → write to SQLite table

CRITICAL (ANTI-LEAKAGE):
  - Trie at time T contains outcomes from rows with ts < T only
  - Encoders were fit on a fixed training snapshot (saved to disk, frozen)
  - vol_ma20 uses closed='left' (compute_vol_ma20 in v7_ohlcv_encoder.py)
  - vol_regime computed from atr_percentile_50 (already in feature_observations_v6)
    with frozen quartile breakpoints (25/50/75)

OUTPUT:
  - SQLite table feature_observations_v7_trie:
    schema (symbol TEXT, ts INTEGER, vol_regime INTEGER,
            trie_n1_pred_5 REAL, trie_n1_conf_5 REAL, ...,
            trie_any_signal REAL,
            PRIMARY KEY (symbol, ts))

USAGE:
    python /home/z/my-project/scripts/v7/v7_extract_trie_features.py
    python /home/z/my-project/scripts/v7/v7_extract_trie_features.py --symbol BTCUSDT
    python /home/z/my-project/scripts/v7/v7_extract_trie_features.py --resume
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Set

import numpy as np
import pandas as pd

# Make v7 module importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from v7_ohlcv_encoder import SECTOR_TOKENS, symbol_to_sector
from v7_trie_conflict import TrieFeatureExtractor, SECTOR_TRIE_FEATURES, TRIE_FEATURE_NAMES

DB_PATH = os.environ.get("PPMT_DB_PATH", "/home/z/my-project/data/ppmt.db")
TABLE_NAME = "feature_observations_v7_trie"

LOG = logging.getLogger("v7_extract_trie")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def get_all_symbols() -> List[str]:
    """Get all 12 symbols (as {TOKEN}USDT)."""
    return [f"{tok}USDT" for sector_tokens in SECTOR_TOKENS.values() for tok in sector_tokens]


def get_completed_symbols() -> Set[str]:
    """Check which symbols already have rows in the trie table (for --resume)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            f"SELECT DISTINCT symbol FROM {TABLE_NAME}"
        )
        done = {r[0] for r in cur.fetchall()}
        conn.close()
        return done
    except sqlite3.OperationalError:
        return set()


def create_table_if_not_exists(conn: sqlite3.Connection, sector_features: Dict[str, List[str]]) -> None:
    """Create the trie features table with all union features (35 columns)."""
    # Use the UNION of all sector features so the table schema is uniform
    all_features = list(TRIE_FEATURE_NAMES)
    cols_sql = ",\n            ".join([f"{f} REAL" for f in all_features])
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            symbol TEXT NOT NULL,
            ts INTEGER NOT NULL,
            vol_regime INTEGER NOT NULL,
            {cols_sql},
            PRIMARY KEY (symbol, ts)
        )
    """)
    # Index for fast JOIN with feature_observations_v6
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_fov7trie_sym_ts ON {TABLE_NAME}(symbol, ts)"
    )
    conn.commit()
    LOG.info("Table %s ready (%d feature columns)", TABLE_NAME, len(all_features))


def insert_symbol_rows(conn: sqlite3.Connection, df: pd.DataFrame, all_features: List[str]) -> int:
    """Insert one symbol's trie features into the SQLite table."""
    # Build column list dynamically (sector may not produce all 35 features)
    cols_present = [c for c in all_features if c in df.columns]
    n_cols = len(cols_present) + 3  # +3 for symbol, ts, vol_regime
    placeholders = ", ".join(["?"] * n_cols)
    col_names = ", ".join(["symbol", "ts", "vol_regime"] + cols_present)

    rows = []
    for _, r in df.iterrows():
        row = [r["symbol"], int(r["ts"]), int(r["vol_regime"])]
        for c in cols_present:
            v = r[c]
            if pd.isna(v) or not np.isfinite(v):
                v = 0.0
            row.append(float(v))
        rows.append(tuple(row))

    conn.executemany(
        f"INSERT OR REPLACE INTO {TABLE_NAME} ({col_names}) VALUES ({placeholders})",
        rows,
    )
    conn.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None,
                        help="Process only this symbol (default: all 12)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip symbols already in the trie table")
    args = parser.parse_args()

    print("=" * 76)
    print("v7 EXTRACT TRIE FEATURES (F6)")
    print(f"  source: ohlcv_v6 + feature_observations_v6 + encoders/")
    print(f"  output: SQLite table {TABLE_NAME} in {DB_PATH}")
    print(f"  features: up to 35 (per-sector subset varies)")
    print(f"  anti-leakage: INSERT-AFTER-PREDICT, trie grows incrementally")
    if args.symbol:
        print(f"  mode: SINGLE SYMBOL ({args.symbol})")
    elif args.resume:
        print(f"  mode: RESUME (skip already-processed symbols)")
    else:
        print(f"  mode: ALL SYMBOLS (sequential)")
    print("=" * 76)

    # Open DB and prepare table
    conn = sqlite3.connect(DB_PATH)
    create_table_if_not_exists(conn, SECTOR_TRIE_FEATURES)

    # Determine which symbols to process
    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = get_all_symbols()

    if args.resume and not args.symbol:
        done = get_completed_symbols()
        symbols = [s for s in symbols if s not in done]
        LOG.info("Resume mode: %d symbols already done, %d remaining",
                 len(done), len(symbols))

    if not symbols:
        print("Nothing to do.")
        conn.close()
        return

    # Build one extractor per sector group — but the SectorTrieContainer
    # is shared across all symbols of the same sector (intentional per §4.5).
    # The TrieFeatureExtractor wraps the container, so we create ONE extractor
    # and use it for all symbols — the trie accumulates observations across
    # all symbols of the same sector.
    extractor = TrieFeatureExtractor(encoders_dir="/home/z/my-project/data/v7_models/encoders")
    extractor.load_encoders()

    t_total = time.time()
    total_rows = 0
    for i, sym in enumerate(symbols, 1):
        t0 = time.time()
        try:
            df = extractor.process_symbol(sym)
            n = insert_symbol_rows(conn, df, TRIE_FEATURE_NAMES)
            total_rows += n
            LOG.info("[%d/%d] %s: %d rows inserted in %.1fs (total: %d)",
                     i, len(symbols), sym, n, time.time() - t0, total_rows)
        except Exception as e:
            LOG.error("[%d/%d] %s FAILED: %s", i, len(symbols), sym, e)
            import traceback
            traceback.print_exc()

    # Print trie stats (per sector)
    print()
    print("=" * 76)
    print("TRIE STATS (after processing all symbols)")
    print("=" * 76)
    stats = extractor.stats()
    for sector, sector_stats in stats.items():
        for seq_len, s in sector_stats.items():
            print(f"  {sector:<12} seq_len={seq_len}:  "
                  f"nodes={s['global_nodes']:>6,}  "
                  f"obs={s['total_observations']:>8,}  "
                  f"avg={s['avg_obs_per_node']:>5.1f}  "
                  f"inserts={s['insert_count']:>8,}")

    print()
    print(f"DONE. {total_rows:,} rows in {TABLE_NAME} ({time.time() - t_total:.1f}s total)")
    conn.close()


if __name__ == "__main__":
    main()
