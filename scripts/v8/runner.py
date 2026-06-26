"""
runner.py — v8 Pattern-Informed Trading System Entry Point

Usage:
    # Quick validation: train + purged CV + backtest
    python -m scripts.v8.runner --mode validate --symbols DOGE/USDT,SOL/USDT,AVAX/USDT --days 90

    # Full training (all tokens, 180d)
    python -m scripts.v8.runner --mode train --days 180

    # Backtest existing model
    python -m scripts.v8.runner --mode backtest --symbols DOGE/USDT --days 30

Based on CORRECTED pattern analysis (446 entries, long+short):
  BREAKOUT long:     230 trades, 73.9% WR, PnL +251.1  → THE EDGE
  BREAKOUT short:    165 trades, 68.5% WR, PnL -556.2  → THE HOLE
  EMA_BOUNCE short:   14 trades, 85.7% WR, PnL +27.3   → counter-trend edge
  LEVEL_TEST short:   11 trades, 100%  WR, PnL +33.2   → support bounce
  
  Risk finding: 72% WR but 1:3 win/loss ratio → time stop is key
  Hard rules: 30min time stop, no averaging down, max 3 entries
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# Force-disable Copy-on-Write — MUST be set before any pandas operation
pd.options.mode.copy_on_write = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.v7.paper_trader.feed import Feed
from scripts.v8.features import FEATURE_NAMES, compute_features
from scripts.v8.labels import compute_ev_labels_fast, label_stats
from scripts.v8.model import (
    train_model, train_with_purged_cv, build_dataset,
    save_model, load_model, predict_ev,
    TP_ATR_MULT, SL_ATR_MULT, LOOKAHEAD, ATR_LAG_OFFSET,
    EV_THRESHOLD_LONG, EV_THRESHOLD_SHORT, DEFAULT_COST,
    MAX_HOLD_BARS, MAX_ENTRIES_PER_TRADE, MAX_CONCURRENT_POSITIONS,
    MODEL_DIR,
)
from scripts.v8.backtest import run_backtest, print_backtest_report
from scripts.v8.validation import PurgedKFold

LOG = logging.getLogger("v8_runner")

# Default tokens — available on Bybit (MEXC-only tokens like RIVER/PIPPIN/ZEC excluded)
DEFAULT_SYMBOLS = [
    "SOL/USDT", "AVAX/USDT", "XRP/USDT", "SUI/USDT",
    "ADA/USDT", "DOGE/USDT", "PEPE/USDT", "LINK/USDT",
    "WIF/USDT", "SHIB/USDT",
]


def run_validate(args):
    """Quick validation: train model + purged CV + backtest."""
    LOG.info("=" * 60)
    LOG.info("V8 PATTERN-BASED — VALIDATION MODE")
    LOG.info("=" * 60)

    feed = Feed(exchange_id=args.exchange)
    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS

    # 1. Build dataset
    LOG.info("Step 1: Building dataset for %d symbols, %d days...", len(symbols), args.days)
    t0 = time.time()
    dataset = build_dataset(feed, symbols, days=args.days)
    LOG.info("Dataset built in %.1fs: %d rows", time.time() - t0, len(dataset))

    # 2. Label analysis
    clean = dataset.dropna(subset=["ev_label"])
    LOG.info("\n--- LABEL ANALYSIS ---")
    stats = label_stats(clean["ev_label"].values)
    for k, v in stats.items():
        LOG.info("  %s: %s", k, f"{v:.4f}" if isinstance(v, float) else v)

    # 3. Train with purged CV
    LOG.info("\nStep 2: Training with Purged K-Fold CV...")
    model, metrics = train_with_purged_cv(dataset, n_splits=args.n_folds)

    # 4. Print CV results
    cv = metrics.get("cv", {})
    if cv.get("n_folds", 0) > 0:
        LOG.info("\n--- PURGED CV RESULTS ---")
        LOG.info("  Folds: %d", cv["n_folds"])
        LOG.info("  Sharpe: %.3f +/- %.3f", cv["sharpe_mean"], cv["sharpe_std"])
        LOG.info("  Correlation: %.4f", cv["corr_mean"])
    else:
        LOG.warning("No valid CV folds")

    # 5. Backtest on holdout (last 15%)
    LOG.info("\nStep 3: Backtest on holdout...")
    n = len(dataset)
    holdout_start = int(n * 0.85)
    holdout = dataset.iloc[holdout_start:].dropna(subset=["ev_label"])

    if len(holdout) > 50:
        X_hold = holdout[FEATURE_NAMES].values.astype(np.float32)
        preds = model.predict(X_hold)

        result = run_backtest(
            predictions=preds,
            closes=holdout["close"].values,
            highs=holdout["high"].values,
            lows=holdout["low"].values,
            atr_14=holdout["_atr_14_price"].values,
            symbols=holdout.get("symbol", pd.Series([""] * len(holdout))).values,
        )
        print_backtest_report(result)
    else:
        LOG.warning("Holdout too small")

    # 6. Save model
    model_path = MODEL_DIR / f"v8_pattern_{args.days}d.txt"
    save_model(model, metrics, model_path)

    # 7. Feature importance
    imp = model.feature_importance(importance_type="gain")
    total_imp = max(imp.sum(), 1)
    sorted_idx = np.argsort(imp)[::-1]
    LOG.info("\n--- TOP 15 FEATURES ---")
    for rank, idx in enumerate(sorted_idx[:15]):
        LOG.info("  #%2d: %-30s gain=%8d  (%.1f%%)",
                 rank + 1, FEATURE_NAMES[idx], int(imp[idx]),
                 imp[idx] / total_imp * 100)

    return metrics


def run_train(args):
    """Full training for production."""
    LOG.info("=" * 60)
    LOG.info("V8 PATTERN-BASED — PRODUCTION TRAINING")
    LOG.info("=" * 60)

    feed = Feed(exchange_id=args.exchange)
    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS

    dataset = build_dataset(feed, symbols, days=args.days)
    model, metrics = train_with_purged_cv(dataset, n_splits=args.n_folds)

    model_path = MODEL_DIR / "v8_pattern_production.txt"
    save_model(model, metrics, model_path)
    LOG.info("Production model saved to %s", model_path)

    return metrics


def run_backtest_cmd(args):
    """Backtest with existing model."""
    LOG.info("=" * 60)
    LOG.info("V8 PATTERN-BASED — BACKTEST MODE")
    LOG.info("=" * 60)

    feed = Feed(exchange_id=args.exchange)
    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS[:3]

    model_path = MODEL_DIR / f"v8_pattern_{args.days}d.txt"
    if not model_path.exists():
        model_path = MODEL_DIR / "v8_pattern_production.txt"
    if not model_path.exists():
        LOG.error("No model found. Run --mode train first.")
        return

    model = load_model(model_path)
    dataset = build_dataset(feed, symbols, days=args.days)

    clean = dataset.dropna(subset=["ev_label"])
    X = clean[FEATURE_NAMES].values.astype(np.float32)
    preds = model.predict(X)

    result = run_backtest(
        predictions=preds,
        closes=clean["close"].values,
        highs=clean["high"].values,
        lows=clean["low"].values,
        atr_14=clean["_atr_14_price"].values,
        symbols=clean.get("symbol", pd.Series([""] * len(clean))).values,
    )
    print_backtest_report(result)


def main():
    parser = argparse.ArgumentParser(description="v8 Pattern-Based Trading System")
    parser.add_argument("--mode", default="validate", choices=["validate", "train", "backtest"])
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols")
    parser.add_argument("--days", type=int, default=90, help="Data window in days")
    parser.add_argument("--exchange", default="bybit", help="Exchange")
    parser.add_argument("--n-folds", type=int, default=5, help="Purged K-Fold splits")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\n" + "=" * 60)
    print("V8 PATTERN-BASED — Low-TF Multi-Token Trading System")
    print("=" * 60)
    print(f"  Mode:     {args.mode}")
    print(f"  Symbols:  {args.symbols or 'DEFAULT'}")
    print(f"  Days:     {args.days}")
    print(f"  Exchange: {args.exchange}")
    print()
    print("  Architecture (from corrected pattern analysis):")
    print(f"    Label:     EV regression (TP={TP_ATR_MULT}xATR, SL={SL_ATR_MULT}xATR)")
    print(f"    Lookahead: {LOOKAHEAD} bars ({LOOKAHEAD * 5} min)")
    print(f"    Time stop: {MAX_HOLD_BARS} bars ({MAX_HOLD_BARS * 5} min)")
    print(f"    Features:  {len(FEATURE_NAMES)} (pattern-informed: G5 Breakout + G6 Trend)")
    print(f"    Model:     Multi-token LightGBM regression")
    print(f"    CV:        Purged K-Fold (purge={LOOKAHEAD}, embargo=3)")
    print()
    print("  Pattern Analysis Results (446 entries, long+short):")
    print(f"    BREAKOUT long:     73.9% WR, PnL +251 = THE EDGE")
    print(f"    BREAKOUT short:    68.5% WR, PnL -556 = THE HOLE")
    print(f"    EMA_BOUNCE short:  85.7% WR, PnL +27  = counter-trend edge")
    print(f"    LEVEL_TEST short:  100%  WR, PnL +33  = support bounce")
    print()
    print("  Hard Rules:")
    print(f"    + Time stop at {MAX_HOLD_BARS * 5} min (winners 8-9m, losers 21-28m)")
    print(f"    + No averaging down (1:3 win/loss ratio)")
    print(f"    + Max {MAX_ENTRIES_PER_TRADE} entries per trade (averaging UP only)")
    print(f"    + Max {MAX_CONCURRENT_POSITIONS} concurrent positions")
    print("=" * 60 + "\n")

    if args.mode == "validate":
        run_validate(args)
    elif args.mode == "train":
        run_train(args)
    elif args.mode == "backtest":
        run_backtest_cmd(args)


if __name__ == "__main__":
    main()
