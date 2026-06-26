"""
diagnose_build.py — Diagnose why build_dataset.py produces 0 positive labels

Run this BEFORE the fix to understand the root cause:
  python3 -m scripts.v9.diagnose_build

It checks:
  1. Trade timestamp conversion (entry_ts_ms values)
  2. Cached OHLCV data date ranges
  3. Whether trade dates overlap with OHLCV data
  4. Manual matching attempt
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

pd.options.mode.copy_on_write = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data" / "v9"
CACHE_DIR = DATA_DIR / "ohlcv_cache"


def main():
    # ── 1. Load filtered trades ──
    trades_path = DATA_DIR / "filtered_trades.json"
    if not trades_path.exists():
        print("ERROR: No filtered_trades.json. Run parse_trades.py first!")
        return

    with open(trades_path) as f:
        trades = json.load(f)

    print(f"\n{'='*70}")
    print(f"V9 BUILD DIAGNOSTIC")
    print(f"{'='*70}")
    print(f"  Loaded {len(trades)} filtered trades")

    tdf = pd.DataFrame(trades)

    # ── 2. Check timestamp conversion ──
    print(f"\n── STEP 1: Timestamp Conversion ──")

    # Show raw entry_time samples
    print(f"  Raw entry_time samples (first 3):")
    for i in range(min(3, len(tdf))):
        print(f"    [{i}] {tdf['entry_time'].iloc[i]}")

    # Try the EXISTING conversion method
    tdf["entry_ts"] = pd.to_datetime(tdf["entry_time"], utc=True)
    print(f"  Parsed entry_ts dtype: {tdf['entry_ts'].dtype}")

    try:
        tdf["entry_ts_ms"] = (tdf["entry_ts"].astype(np.int64) // 1_000_000).astype(np.int64)
        print(f"  astype(np.int64) method: OK")
    except Exception as e:
        print(f"  astype(np.int64) method: FAILED — {e}")
        tdf["entry_ts_ms"] = None

    # Try alternative method
    try:
        entry_ts_ns = tdf["entry_ts"].view("int64")
        entry_ts_ms_alt = (entry_ts_ns // 1_000_000).astype(np.int64)
        print(f"  view('int64') method: OK")
    except Exception as e:
        print(f"  view('int64') method: FAILED — {e}")
        entry_ts_ms_alt = None

    # Compare methods
    if tdf["entry_ts_ms"] is not None and entry_ts_ms_alt is not None:
        diff = np.abs(tdf["entry_ts_ms"].values - entry_ts_ms_alt.values)
        print(f"  Difference between methods: max={diff.max()}, mean={diff.mean():.1f}")
        if diff.max() > 0:
            print(f"  ⚠️  METHODS DISAGREE! This is the bug!")
        else:
            print(f"  ✅ Both methods agree")

    # Show converted values
    if tdf["entry_ts_ms"] is not None:
        print(f"\n  Sample entry_ts_ms values:")
        for i in range(min(5, len(tdf))):
            sym = tdf["symbol"].iloc[i]
            ts_ms = tdf["entry_ts_ms"].iloc[i]
            ts_date = pd.to_datetime(ts_ms, unit="ms").strftime("%Y-%m-%d %H:%M:%S")
            print(f"    {sym}: ts_ms={ts_ms} → {ts_date}")

        # Date range
        min_ts = tdf["entry_ts_ms"].min()
        max_ts = tdf["entry_ts_ms"].max()
        print(f"\n  Trade date range:")
        print(f"    Min: {pd.to_datetime(min_ts, unit='ms')} ({min_ts})")
        print(f"    Max: {pd.to_datetime(max_ts, unit='ms')} ({max_ts})")
        print(f"    Span: {(max_ts - min_ts) / 86400000:.1f} days")

        # Sanity check
        if min_ts < 1e12 or max_ts > 2e12:
            print(f"  ⚠️  TIMESTAMP VALUES LOOK WRONG! Expected ~1.7-1.8e12 for 2024-2026")
        else:
            print(f"  ✅ Timestamp values look reasonable")
    else:
        print(f"  ❌ Cannot convert timestamps — both methods failed!")
        return

    # ── 3. Per-symbol breakdown ──
    print(f"\n── STEP 2: Per-Symbol Trade Date Ranges ──")
    for sym in tdf["symbol"].value_counts().head(15).index:
        sym_trades = tdf[tdf["symbol"] == sym]
        min_ts = sym_trades["entry_ts_ms"].min()
        max_ts = sym_trades["exit_ts_ms"].max() if "exit_ts_ms" in tdf.columns else sym_trades["entry_ts_ms"].max()
        min_date = pd.to_datetime(min_ts, unit="ms").strftime("%Y-%m-%d")
        max_date = pd.to_datetime(max_ts, unit="ms").strftime("%Y-%m-%d")
        print(f"  {sym:<12} {len(sym_trades):>3} trades  {min_date} → {max_date}  ({(max_ts-min_ts)/86400000:.0f} days)")

    # ── 4. Check cached OHLCV data ──
    print(f"\n── STEP 3: Cached OHLCV Data ──")
    if not CACHE_DIR.exists():
        print(f"  No cache directory: {CACHE_DIR}")
    else:
        cache_files = list(CACHE_DIR.glob("*.parquet"))
        if not cache_files:
            print(f"  No cached OHLCV files")
        else:
            for cf in sorted(cache_files):
                try:
                    cached = pd.read_parquet(cf)
                    if len(cached) == 0:
                        print(f"  {cf.stem}: EMPTY")
                        continue
                    ts_min = cached["timestamp"].min()
                    ts_max = cached["timestamp"].max()
                    date_min = pd.to_datetime(ts_min, unit="ms").strftime("%Y-%m-%d %H:%M")
                    date_max = pd.to_datetime(ts_max, unit="ms").strftime("%Y-%m-%d %H:%M")
                    span_days = (ts_max - ts_min) / 86400000

                    # Check overlap with trades for this symbol
                    sym = cf.stem.replace("_1m", "")
                    sym_trades = tdf[tdf["symbol"] == sym]
                    overlap = "N/A"
                    if len(sym_trades) > 0:
                        trade_min = sym_trades["entry_ts_ms"].min()
                        trade_max = sym_trades["entry_ts_ms"].max()
                        has_overlap = ts_min <= trade_max and ts_max >= trade_min
                        overlap = "✅ YES" if has_overlap else "❌ NO — THIS IS THE PROBLEM"

                    print(f"  {cf.stem:<15} {len(cached):>7} bars  {date_min} → {date_max}  ({span_days:.1f} days)  Overlap: {overlap}")
                except Exception as e:
                    print(f"  {cf.stem}: ERROR reading — {e}")

    # ── 5. Manual matching test ──
    print(f"\n── STEP 4: Manual Matching Test ──")
    # Try to match a single trade to OHLCV data
    for sym in tdf["symbol"].value_counts().head(5).index:
        sym_trades = tdf[tdf["symbol"] == sym]
        cache_file = CACHE_DIR / f"{sym}_1m.parquet"

        if not cache_file.exists():
            print(f"  {sym}: No cached OHLCV data")
            continue

        cached = pd.read_parquet(cache_file)
        if len(cached) == 0:
            print(f"  {sym}: Cached OHLCV is empty")
            continue

        ohlcv_ts = cached["timestamp"].values.astype(np.int64)

        # Try matching first 3 trades
        print(f"  {sym} — {len(cached)} OHLCV bars, {len(sym_trades)} trades:")
        for i, (_, trade) in enumerate(sym_trades.head(3).iterrows()):
            entry_ms = int(trade["entry_ts_ms"])
            entry_date = pd.to_datetime(entry_ms, unit="ms").strftime("%Y-%m-%d %H:%M:%S")

            # Closest bar
            diffs = np.abs(ohlcv_ts - entry_ms)
            closest_idx = int(np.argmin(diffs))
            closest_ts = int(ohlcv_ts[closest_idx])
            closest_date = pd.to_datetime(closest_ts, unit="ms").strftime("%Y-%m-%d %H:%M:%S")
            diff_ms = diffs[closest_idx]
            diff_min = diff_ms / 60000

            # Exact match
            exact = entry_ms in set(ohlcv_ts.tolist())

            print(f"    Trade {i}: entry_ms={entry_ms} ({entry_date})")
            print(f"      Closest bar: ts={closest_ts} ({closest_date})  diff={diff_ms}ms ({diff_min:.1f}min)  exact={exact}")

            if diff_min > 2:
                print(f"      ⚠️  Closest bar is {diff_min:.1f} minutes away — trade is OUTSIDE OHLCV range!")

    # ── 6. Summary ──
    print(f"\n{'='*70}")
    print(f"DIAGNOSTIC SUMMARY")
    print(f"{'='*70}")

    # Check if the main issue is data coverage
    issues = []
    if tdf["entry_ts_ms"] is not None:
        min_trade = tdf["entry_ts_ms"].min()
        max_trade = tdf["entry_ts_ms"].max()
        trade_span_days = (max_trade - min_trade) / 86400000

        if trade_span_days > 30:
            issues.append(f"Trades span {trade_span_days:.0f} days — Bybit's 999 bars only covers ~0.7 days")
            issues.append("  → FIX: Download pagination must continue when len(ohlcv) < 1000")

    if CACHE_DIR.exists():
        for cf in CACHE_DIR.glob("*.parquet"):
            try:
                cached = pd.read_parquet(cf)
                if len(cached) > 0:
                    sym = cf.stem.replace("_1m", "")
                    sym_trades = tdf[tdf["symbol"] == sym]
                    if len(sym_trades) > 0:
                        ts_min = cached["timestamp"].min()
                        ts_max = cached["timestamp"].max()
                        trade_min = sym_trades["entry_ts_ms"].min()
                        trade_max = sym_trades["entry_ts_ms"].max()
                        if not (ts_min <= trade_max and ts_max >= trade_min):
                            issues.append(f"{sym}: Cached OHLCV ({pd.to_datetime(ts_min, unit='ms').strftime('%Y-%m-%d')} → {pd.to_datetime(ts_max, unit='ms').strftime('%Y-%m-%d')}) doesn't overlap with trades ({pd.to_datetime(trade_min, unit='ms').strftime('%Y-%m-%d')} → {pd.to_datetime(trade_max, unit='ms').strftime('%Y-%m-%d')})")
                            issues.append(f"  → FIX: Delete stale cache and re-download")
            except:
                pass

    if issues:
        print(f"  ISSUES FOUND:")
        for issue in issues:
            print(f"    {issue}")
    else:
        print(f"  No obvious issues found — check manual matching results above")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
