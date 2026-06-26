"""
v11_run_pipeline.py — Run the full v11 pipeline in one shot.

Pipeline:
  1. Build dataset (1m features + microstructure + MTF + labels)
  2. Train models (per symbol × horizon, walk-forward)
  3. Backtest (fixed + adaptive exits, cost sweep)
  4. Compare with v7.5 baseline

USAGE:
    python scripts/v11/v11_run_pipeline.py
    python scripts/v11/v11_run_pipeline.py --quick
    python scripts/v11/v11_run_pipeline.py --adaptive-only
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts" / "v11"

LOG = logging.getLogger("v11_pipeline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def run_step(name: str, cmd: list[str], timeout: int = 600) -> bool:
    """Run a pipeline step."""
    print(f"\n{'='*80}")
    print(f"STEP: {name}")
    print(f"CMD: {' '.join(cmd)}")
    print(f"{'='*80}")
    
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            timeout=timeout,
            capture_output=False,
        )
        elapsed = time.time() - t0
        if result.returncode != 0:
            LOG.error("STEP %s FAILED (exit code %d) in %.1fs", name, result.returncode, elapsed)
            return False
        LOG.info("STEP %s completed in %.1fs", name, elapsed)
        return True
    except subprocess.TimeoutExpired:
        LOG.error("STEP %s TIMED OUT after %ds", name, timeout)
        return False
    except Exception as e:
        LOG.error("STEP %s ERROR: %s", name, e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Run v11 pipeline")
    parser.add_argument("--quick", action="store_true", help="Quick mode: fewer symbols/horizons")
    parser.add_argument("--adaptive-only", action="store_true", help="Only run adaptive backtest")
    parser.add_argument("--skip-build", action="store_true", help="Skip dataset build (use cached)")
    parser.add_argument("--skip-train", action="store_true", help="Skip training (use cached models)")
    args = parser.parse_args()
    
    if args.quick:
        symbols = "SOL,DOGE"
        horizons = "36,72"
    else:
        symbols = "SOL,DOGE,AVAX"
        horizons = "12,36,72,288"
    
    print("=" * 80)
    print("v11 PIPELINE — Low Timeframe Trading")
    print(f"  Symbols: {symbols}")
    print(f"  Horizons: {horizons}")
    print(f"  Quick mode: {args.quick}")
    print("=" * 80)
    
    t0 = time.time()
    
    # Step 1: Build dataset
    if not args.skip_build:
        ok = run_step(
            "Build Dataset",
            [sys.executable, str(SCRIPTS_DIR / "v11_build_dataset.py"),
             "--symbols", symbols, "--horizons", horizons],
            timeout=600,
        )
        if not ok:
            LOG.error("Pipeline aborted at step 1")
            sys.exit(1)
    
    # Step 2: Train models
    if not args.skip_train:
        ok = run_step(
            "Train Models",
            [sys.executable, str(SCRIPTS_DIR / "v11_train.py")],
            timeout=600,
        )
        if not ok:
            LOG.error("Pipeline aborted at step 2")
            sys.exit(1)
    
    # Step 3: Fixed backtest
    if not args.adaptive_only:
        ok = run_step(
            "Backtest (Fixed Exits)",
            [sys.executable, str(SCRIPTS_DIR / "v11_backtest.py"),
             "--cost", "maker"],
            timeout=600,
        )
        if not ok:
            LOG.warning("Fixed backtest failed, continuing...")
    
    # Step 4: Adaptive backtest
    ok = run_step(
        "Backtest (Adaptive Exits)",
        [sys.executable, str(SCRIPTS_DIR / "v11_backtest.py"),
         "--adaptive", "--cost", "maker"],
        timeout=600,
    )
    if not ok:
        LOG.warning("Adaptive backtest failed, continuing...")
    
    elapsed = time.time() - t0
    print(f"\n{'='*80}")
    print(f"PIPELINE COMPLETE in {elapsed:.0f}s")
    print(f"Results in: {PROJECT_ROOT}/data/v11/")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
