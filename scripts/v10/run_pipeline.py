"""
run_pipeline.py — V10: Run the full pipeline

Steps:
  1. parse_trades  (reuse v9 — same XLSX, same logic)
  2. build_dataset (v10: MFE/MAE + BTC + 1h MTF)
  3. train          (v10: dual models)
  4. backtest       (v10: adaptive exits)

Usage:
  python3 -m scripts.v10.run_pipeline --days 30 --symbols SOL/USDT,AVAX/USDT,XRP/USDT
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

LOG = logging.getLogger("v10_pipeline")


def run_step(name: str, cmd: list[str]) -> bool:
    """Run a pipeline step, return True if successful."""
    LOG.info("Running: %s", " ".join(cmd))
    t0 = time.time()
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    elapsed = time.time() - t0
    ok = result.returncode == 0
    if ok:
        LOG.info("%s completed (%.1fs)", name, elapsed)
    else:
        LOG.error("%s FAILED (exit code %d)", name, result.returncode)
    return ok


def main():
    parser = argparse.ArgumentParser(description="V10 Pipeline")
    parser.add_argument("--symbols", default="SOL/USDT,AVAX/USDT,XRP/USDT")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--big-loss", type=float, default=5.0)
    parser.add_argument("--skip-parse", action="store_true",
                        help="Skip parse_trades (reuse v9 output)")
    parser.add_argument("--skip-build", action="store_true",
                        help="Skip build_dataset (reuse existing)")
    parser.add_argument("--skip-btc", action="store_true",
                        help="Skip BTC download (faster but no correlation features)")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Clear OHLCV cache before building")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")

    print(f"\n{'='*70}")
    print(f"V10 PIPELINE — Exit-Aware Classifier + Enhanced MTF")
    print(f"{'='*70}")
    print(f"  Symbols: {args.symbols}")
    print(f"  Days: {args.days}")
    print(f"  BTC features: {'SKIP' if args.skip_btc else 'YES'}")
    print(f"{'='*70}")

    # Step 1: Parse trades (reuse v9)
    if not args.skip_parse:
        ok = run_step(
            "parse_trades",
            [sys.executable, "-m", "scripts.v9.parse_trades",
             "--big-loss", str(args.big_loss)]
        )
        if not ok:
            LOG.error("Pipeline aborted at step 1")
            sys.exit(1)

    # Step 2: Build dataset (v10)
    if not args.skip_build:
        build_cmd = [sys.executable, "-m", "scripts.v10.build_dataset",
                     "--neg-ratio", "3.0",
                     "--big-loss", str(args.big_loss)]
        if args.skip_btc:
            build_cmd.append("--skip-btc")
        if args.clear_cache:
            build_cmd.append("--clear-cache")

        ok = run_step("build_dataset_v10", build_cmd)
        if not ok:
            LOG.error("Pipeline aborted at step 2")
            sys.exit(1)

    # Step 3: Train models
    ok = run_step(
        "train_v10",
        [sys.executable, "-m", "scripts.v10.train"]
    )
    if not ok:
        LOG.error("Pipeline aborted at step 3")
        sys.exit(1)

    # Step 4: Backtest
    ok = run_step(
        "backtest_v10",
        [sys.executable, "-m", "scripts.v10.backtest",
         "--symbols", args.symbols,
         "--days", str(args.days)]
    )
    if not ok:
        LOG.error("Pipeline aborted at step 4")
        sys.exit(1)

    print(f"\n{'='*70}")
    print(f"V10 PIPELINE COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
