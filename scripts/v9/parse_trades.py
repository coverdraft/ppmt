"""
parse_trades.py — Parse MEXC futures XLSX into filtered trades

Steps:
  1. Load XLSX
  2. Match open/close orders into trades (FIFO)
  3. Filter: remove trades with loss > big_loss_threshold
  4. Save to JSON for the next step

Usage:
  python3 -m scripts.v9.parse_trades [--big-loss 5]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd

pd.options.mode.copy_on_write = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LOG = logging.getLogger("v9_parse")

XLSX_NAME = "MEXC - Historial de Ordenes de Futuros-20250624-20260623_1782174256031.xlsx"
DATA_DIR = PROJECT_ROOT / "data" / "v9"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def parse_mexc_orders(xlsx_path: Path) -> pd.DataFrame:
    """Parse MEXC futures order history."""
    LOG.info("Loading MEXC orders from %s", xlsx_path.name)
    df = pd.read_excel(xlsx_path)
    LOG.info("Loaded %d orders", len(df))

    df["_ts"] = pd.to_datetime(df["Tiempo(UTC+02:00)"].astype(str)) - pd.Timedelta(hours=2)
    df["_ts"] = df["_ts"].dt.tz_localize("UTC")

    df["_symbol"] = (df["Par de Trading de Futuros"]
                     .str.strip().str.upper()
                     .str.replace("USDT", "", regex=False).str.strip())

    df["_action"] = df["Dirección"].str.strip().str.lower().map({
        "buy long": "open_long", "sell long": "close_long",
        "buy short": "open_short", "sell short": "close_short",
    })

    df["_price"] = pd.to_numeric(df["Precio promedio completo"], errors="coerce")
    df["_pnl"] = pd.to_numeric(df["PNL de Cierre"], errors="coerce").fillna(0)
    df["_fee"] = pd.to_numeric(df["Comisión de Trading"], errors="coerce").fillna(0).abs()
    df["_qty"] = pd.to_numeric(df["Cant. Completada (Cripto)"], errors="coerce").fillna(0)
    df["_qty_usdt"] = pd.to_numeric(df["Cant. Completada (Monto)"], errors="coerce").fillna(0)
    df["_leverage"] = pd.to_numeric(df["Apalancamiento"], errors="coerce").fillna(1)

    df = df.sort_values("_ts").reset_index(drop=True)
    LOG.info("Actions: %s", df["_action"].value_counts().to_dict())
    return df


def match_trades(orders: pd.DataFrame) -> pd.DataFrame:
    """Match open/close orders into trades (FIFO by symbol+direction)."""
    trades = []
    opens = defaultdict(lambda: defaultdict(list))

    for _, row in orders.iterrows():
        action = row.get("_action")
        if not action or pd.isna(action):
            continue

        symbol = row["_symbol"]
        ts = row["_ts"]
        price = row["_price"]
        pnl = row["_pnl"]
        fee = row["_fee"]
        qty = row["_qty"]
        qty_usdt = row["_qty_usdt"]
        leverage = row["_leverage"]

        if "open" in action:
            direction = "long" if "long" in action else "short"
            opens[symbol][direction].append({
                "ts": ts, "price": price, "fee": fee, "qty": qty,
                "qty_usdt": qty_usdt, "leverage": leverage,
            })

        elif "close" in action:
            direction = "long" if "long" in action else "short"
            open_list = opens[symbol][direction]

            if not open_list:
                continue

            matched = []
            remaining = qty
            while open_list and remaining > 0:
                o = open_list.pop(0)
                matched.append(o)
                remaining -= o["qty"]

            if remaining < 0 and matched:
                partial = matched.pop()
                partial["qty"] = -remaining
                open_list.insert(0, partial)

            if matched:
                first = matched[0]
                total_fee = sum(o["fee"] for o in matched) + fee
                total_qty_usdt = sum(o["qty_usdt"] for o in matched)
                avg_entry = np.average(
                    [o["price"] for o in matched],
                    weights=[o["qty"] for o in matched]
                )
                duration_min = (ts - first["ts"]).total_seconds() / 60
                n_entries = len(matched)
                max_leverage = max(o["leverage"] for o in matched)

                trades.append({
                    "symbol": symbol,
                    "direction": direction,
                    "entry_time": first["ts"].isoformat(),
                    "entry_price": float(avg_entry),
                    "exit_time": ts.isoformat(),
                    "exit_price": float(price),
                    "pnl": float(pnl),
                    "fee": float(total_fee),
                    "pnl_net": float(pnl - total_fee),
                    "qty_usdt": float(total_qty_usdt),
                    "n_entries": n_entries,
                    "leverage": float(max_leverage),
                    "duration_min": float(duration_min),
                    "is_win": bool(pnl > 0),
                })

    result = pd.DataFrame(trades)
    closed = result[result["pnl"] != 0].copy()
    LOG.info("Matched %d total, %d closed trades", len(result), len(closed))
    return closed


def filter_trades(trades: pd.DataFrame, big_loss: float = 5.0) -> pd.DataFrame:
    """Remove trades with loss > big_loss_threshold (bad exits, not bad entries)."""
    before = len(trades)
    # Keep all wins + losses <= threshold
    filtered = trades[~((trades["pnl"] < 0) & (trades["pnl"] <= -big_loss))].copy()
    after = len(filtered)
    removed = before - after

    wr_before = trades["is_win"].mean() * 100
    pnl_before = trades["pnl"].sum()
    wr_after = filtered["is_win"].mean() * 100
    pnl_after = filtered["pnl"].sum()

    LOG.info("Filter: removed %d trades with loss > $%.1f (%.1f%%)",
             removed, big_loss, removed / before * 100)
    LOG.info("  Before: N=%d WR=%.1f%% PnL=$%.1f", before, wr_before, pnl_before)
    LOG.info("  After:  N=%d WR=%.1f%% PnL=$%.1f PF=%.2f",
             after, wr_after, pnl_after,
             filtered[filtered["pnl"] > 0]["pnl"].sum() /
             abs(filtered[filtered["pnl"] < 0]["pnl"].sum()) if filtered[filtered["pnl"] < 0]["pnl"].sum() != 0 else 0)

    return filtered


def main():
    parser = argparse.ArgumentParser(description="v9 Parse & Filter Trades")
    parser.add_argument("--big-loss", type=float, default=5.0,
                        help="Remove trades with loss greater than this (default: $5)")
    parser.add_argument("--xlsx", type=str, default=None,
                        help="Path to XLSX (default: auto-find)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")

    # Resolve XLSX path
    if args.xlsx:
        xlsx_path = Path(args.xlsx)
    else:
        # Try multiple locations
        candidates = [
            SCRIPT_DIR.parent / "v8" / "pattern_analysis" / XLSX_NAME,
            PROJECT_ROOT / "scripts" / "v8" / "pattern_analysis" / XLSX_NAME,
            Path.cwd() / XLSX_NAME,
        ]
        xlsx_path = None
        for c in candidates:
            if c.exists():
                xlsx_path = c
                break
        if xlsx_path is None:
            LOG.error("XLSX not found! Pass --xlsx path")
            sys.exit(1)

    LOG.info("XLSX: %s", xlsx_path)

    # Parse
    orders = parse_mexc_orders(xlsx_path)

    # Match trades
    trades = match_trades(orders)

    # Filter
    filtered = filter_trades(trades, big_loss=args.big_loss)

    # Save
    output_path = DATA_DIR / "filtered_trades.json"
    records = filtered.to_dict(orient="records")
    # Convert any numpy types
    for rec in records:
        for k, v in rec.items():
            if isinstance(v, (np.integer, np.floating)):
                rec[k] = float(v)
            elif isinstance(v, np.bool_):
                rec[k] = bool(v)

    with open(output_path, "w") as f:
        json.dump(records, f, indent=2, default=str)

    LOG.info("Saved %d filtered trades to %s", len(records), output_path)

    # Summary
    print(f"\n{'='*70}")
    print(f"V9 TRADE PARSING COMPLETE")
    print(f"{'='*70}")
    print(f"  Raw closed trades: {len(trades)}")
    print(f"  Filtered (loss <= ${args.big_loss}): {len(filtered)}")
    print(f"  Removed: {len(trades) - len(filtered)} ({(len(trades)-len(filtered))/len(trades)*100:.1f}%)")
    print(f"  WR: {filtered['is_win'].mean()*100:.1f}%")
    print(f"  PnL: ${filtered['pnl'].sum():+.1f}")
    print(f"  Net: ${filtered['pnl_net'].sum():+.1f}")
    print(f"  Long: {len(filtered[filtered['direction']=='long'])}  Short: {len(filtered[filtered['direction']=='short'])}")
    print(f"  Symbols: {filtered['symbol'].nunique()}")
    print(f"  Median duration: {filtered['duration_min'].median():.1f}min")
    print(f"  Output: {output_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
