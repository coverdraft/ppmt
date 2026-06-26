"""
run_pipeline.py — Run the full v9 pipeline in one command

Step 1: Parse trades from MEXC XLSX (filter losses > $5)
Step 2: Build labeled dataset (download 1m data + features)
Step 3: Train binary classifier
Step 4: Backtest with mechanical exits

Usage:
  python3 -m scripts.v9.run_pipeline
  python3 -m scripts.v9.run_pipeline --days 30 --symbols SOL/USDT,AVAX/USDT,XRP/USDT
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LOG = logging.getLogger("v9_pipeline")
DATA_DIR = PROJECT_ROOT / "data" / "v9"


def main():
    parser = argparse.ArgumentParser(description="v9 Full Pipeline")
    parser.add_argument("--big-loss", type=float, default=5.0)
    parser.add_argument("--neg-ratio", type=float, default=3.0)
    parser.add_argument("--max-symbols", type=int, default=15)
    parser.add_argument("--days", type=int, default=30,
                        help="Days of 1m data for backtest")
    parser.add_argument("--symbols", default="SOL/USDT,AVAX/USDT,XRP/USDT")
    parser.add_argument("--skip-parse", action="store_true",
                        help="Skip step 1 if filtered_trades.json exists")
    parser.add_argument("--skip-dataset", action="store_true",
                        help="Skip step 2 if dataset.parquet exists")
    parser.add_argument("--skip-train", action="store_true",
                        help="Skip step 3 if model exists")
    parser.add_argument("--skip-backtest", action="store_true",
                        help="Skip step 4")
    parser.add_argument("--exchange", default="bybit")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Delete cached OHLCV data and re-download")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")

    print("\n" + "=" * 70)
    print("V9 SUPERVISED TRADER CLONE — Full Pipeline")
    print("=" * 70)

    # Step 1: Parse trades
    trades_path = DATA_DIR / "filtered_trades.json"
    if args.skip_parse and trades_path.exists():
        LOG.info("Step 1: SKIPPED (filtered_trades.json exists)")
    else:
        LOG.info("Step 1: Parsing trades from XLSX...")
        from scripts.v9.parse_trades import parse_mexc_orders, match_trades, filter_trades

        xlsx_name = "MEXC - Historial de Ordenes de Futuros-20250624-20260623_1782174256031.xlsx"
        candidates = [
            SCRIPT_DIR.parent / "v8" / "pattern_analysis" / xlsx_name,
            PROJECT_ROOT / "scripts" / "v8" / "pattern_analysis" / xlsx_name,
        ]
        xlsx_path = None
        for c in candidates:
            if c.exists():
                xlsx_path = c
                break

        if xlsx_path is None:
            LOG.error("XLSX not found! Place it in scripts/v8/pattern_analysis/")
            sys.exit(1)

        orders = parse_mexc_orders(xlsx_path)
        trades = match_trades(orders)
        filtered = filter_trades(trades, big_loss=args.big_loss)

        import json
        import numpy as np
        records = filtered.to_dict(orient="records")
        for rec in records:
            for k, v in rec.items():
                if isinstance(v, (np.integer, np.floating)):
                    rec[k] = float(v)
                elif isinstance(v, np.bool_):
                    rec[k] = bool(v)

        with open(trades_path, "w") as f:
            json.dump(records, f, indent=2, default=str)
        LOG.info("Step 1: DONE — %d filtered trades saved", len(records))

    # Step 2: Build dataset
    dataset_path = DATA_DIR / "dataset.parquet"
    if args.skip_dataset and dataset_path.exists():
        LOG.info("Step 2: SKIPPED (dataset.parquet exists)")
    else:
        LOG.info("Step 2: Building dataset (1m features)...")
        from scripts.v9.build_dataset import main as build_main
        # Override args
        old_argv = sys.argv
        build_args = ["build_dataset", "--neg-ratio", str(args.neg_ratio),
                      "--max-symbols", str(args.max_symbols)]
        if args.clear_cache:
            build_args.append("--clear-cache")
        sys.argv = build_args
        try:
            build_main()
        finally:
            sys.argv = old_argv
        LOG.info("Step 2: DONE")

    # Step 3: Train model
    model_path = DATA_DIR / "models" / "v9_trader_classifier.lgb"
    if args.skip_train and model_path.exists():
        LOG.info("Step 3: SKIPPED (model exists)")
    else:
        if not dataset_path.exists():
            LOG.error("dataset.parquet not found. Step 2 must have failed.")
            sys.exit(1)

        LOG.info("Step 3: Training classifier...")
        from scripts.v9.train import train_model
        bst, meta = train_model(dataset_path)
        LOG.info("Step 3: DONE — AUC_test=%.4f", meta.get("auc_test", 0))

    # Step 4: Backtest
    if args.skip_backtest:
        LOG.info("Step 4: SKIPPED")
    else:
        LOG.info("Step 4: Backtesting...")
        from scripts.v9.backtest import main as backtest_main
        old_argv = sys.argv
        sys.argv = ["backtest", "--symbols", args.symbols,
                    "--days", str(args.days), "--exchange", args.exchange]
        try:
            backtest_main()
        finally:
            sys.argv = old_argv

    print("\n" + "=" * 70)
    print("V9 PIPELINE COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
