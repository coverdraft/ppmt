"""
backtest.py — Backtest v9 classifier with mechanical exits

Uses the trained classifier to predict entry, then applies STRICT exit rules:
  - SL: 0.5% (market order, tight)
  - Time stop: 15 minutes (median winning trade duration)
  - No DCA (single entry per signal)
  - No re-entry within 5 minutes of exit

This is the OPPOSITE of what the trader does wrong:
  - Trader: holds losers 68min → we cut at 15min
  - Trader: DCA into losers → we never add
  - Trader: lets losses grow to -$200 → we cap at 0.5%

Usage:
  python3 -m scripts.v9.backtest --symbols SOL/USDT,AVAX/USDT,XRP/USDT --days 30
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

pd.options.mode.copy_on_write = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.v9.build_dataset import compute_features_1m, download_1m, FEATURE_NAMES

LOG = logging.getLogger("v9_bt")

DATA_DIR = PROJECT_ROOT / "data" / "v9"
MODEL_DIR = DATA_DIR / "models"


@dataclass
class Trade:
    entry_bar: int
    exit_bar: int
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    exit_reason: str  # "SL", "TIME_STOP"
    pnl_pct: float
    pnl_net: float
    bars_held: int
    prob: float  # classifier probability
    symbol: str = ""


def run_backtest(
    df: pd.DataFrame,
    model: lgb.Booster,
    feature_cols: list,
    threshold: float = 0.5,
    sl_pct: float = 0.5,
    max_hold_min: float = 15,
    cost_pct: float = 0.06,
    min_hold_bars: int = 2,
    cooldown_bars: int = 5,
) -> list[Trade]:
    """Run backtest with mechanical exits."""
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    symbols = df["symbol"].values if "symbol" in df.columns else np.array(["all"] * len(df))

    # Predict
    X = df[feature_cols].values.astype(np.float32)
    probs = model.predict(X)

    trades = []
    positions = {}  # symbol → position
    last_exit_bar = {}  # symbol → last exit bar (cooldown)

    for i in range(60, len(df)):  # skip warmup
        sym = str(symbols[i])

        # ── Check existing position ──
        if sym in positions:
            pos = positions[sym]
            should_close = False
            exit_reason = ""
            exit_price = closes[i]
            bars_held = i - pos["entry_bar"]

            # SL check
            if pos["direction"] == "LONG":
                sl_price = pos["entry_price"] * (1 - sl_pct / 100)
                if lows[i] <= sl_price:
                    should_close = True
                    exit_reason = "SL"
                    exit_price = sl_price
            else:  # SHORT
                sl_price = pos["entry_price"] * (1 + sl_pct / 100)
                if highs[i] >= sl_price:
                    should_close = True
                    exit_reason = "SL"
                    exit_price = sl_price

            # Time stop (bars = minutes in 1m data)
            if bars_held >= max_hold_min and not should_close:
                should_close = True
                exit_reason = "TIME_STOP"
                exit_price = closes[i]

            # Min hold
            if should_close and bars_held < min_hold_bars:
                should_close = False

            if should_close:
                if pos["direction"] == "LONG":
                    pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
                else:
                    pnl_pct = (pos["entry_price"] - exit_price) / pos["entry_price"] * 100
                pnl_net = pnl_pct - cost_pct

                trades.append(Trade(
                    entry_bar=pos["entry_bar"], exit_bar=i,
                    direction=pos["direction"],
                    entry_price=pos["entry_price"], exit_price=exit_price,
                    exit_reason=exit_reason,
                    pnl_pct=pnl_pct, pnl_net=pnl_net,
                    bars_held=bars_held,
                    prob=pos["prob"], symbol=sym,
                ))
                del positions[sym]
                last_exit_bar[sym] = i

        # ── Check for new entry ──
        if sym in positions:
            continue

        # Cooldown
        if sym in last_exit_bar and i - last_exit_bar[sym] < cooldown_bars:
            continue

        # Classifier prediction
        prob = float(probs[i])
        if prob < threshold:
            continue

        # Direction from trade_direction feature
        td = float(df["trade_direction"].iloc[i]) if "trade_direction" in df.columns else 0
        # If trade_direction is NaN, predict both and pick better
        if pd.isna(td) or td == 0:
            # Use ema_alignment as direction hint
            td = 1.0 if float(df["ema_alignment"].iloc[i]) > 0 else -1.0

        direction = "LONG" if td > 0 else "SHORT"

        positions[sym] = {
            "entry_bar": i,
            "direction": direction,
            "entry_price": closes[i],
            "prob": prob,
        }

    return trades


def compute_stats(trades: list[Trade]) -> dict:
    if not trades:
        return {"n_trades": 0, "pnl": 0, "wr": 0}

    pnls = [t.pnl_net for t in trades]
    n = len(pnls)
    wr = sum(1 for p in pnls if p > 0) / n * 100
    total = sum(pnls)
    avg = np.mean(pnls)

    n_long = sum(1 for t in trades if t.direction == "LONG")
    n_short = sum(1 for t in trades if t.direction == "SHORT")
    n_sl = sum(1 for t in trades if t.exit_reason == "SL")
    n_ts = sum(1 for t in trades if t.exit_reason == "TIME_STOP")

    gains = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    pf = gains / max(losses, 1e-10)

    sharpe = np.mean(pnls) / max(np.std(pnls), 1e-10) * np.sqrt(525600) if n > 1 else 0  # annualized for 1m bars

    cumulative = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cumulative)
    max_dd = float((cumulative - running_max).min())

    # Per direction
    long_pnls = [t.pnl_net for t in trades if t.direction == "LONG"]
    short_pnls = [t.pnl_net for t in trades if t.direction == "SHORT"]
    long_wr = sum(1 for p in long_pnls if p > 0) / max(len(long_pnls), 1) * 100
    short_wr = sum(1 for p in short_pnls if p > 0) / max(len(short_pnls), 1) * 100

    return {
        "n_trades": n, "n_long": n_long, "n_short": n_short,
        "n_sl": n_sl, "n_ts": n_ts,
        "wr": wr, "long_wr": long_wr, "short_wr": short_wr,
        "total_pnl": total, "avg_pnl": avg,
        "pf": pf, "sharpe": sharpe, "max_dd": max_dd,
    }


def main():
    parser = argparse.ArgumentParser(description="v9 Backtest")
    parser.add_argument("--symbols", default="SOL/USDT,AVAX/USDT,XRP/USDT")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=None,
                        help="Classifier threshold (default: from model meta)")
    parser.add_argument("--sl-pct", type=float, default=0.5,
                        help="Stop loss %% (default: 0.5)")
    parser.add_argument("--max-hold-min", type=float, default=15,
                        help="Max hold minutes (default: 15)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")

    # Load model
    model_path = MODEL_DIR / "v9_trader_classifier.lgb"
    meta_path = MODEL_DIR / "v9_meta.json"
    if not model_path.exists():
        LOG.error("No model. Run train.py first!")
        sys.exit(1)

    bst = lgb.Booster(model_file=str(model_path))
    with open(meta_path) as f:
        meta = json.load(f)

    feature_cols = meta["feature_cols"]
    threshold = args.threshold or meta.get("best_threshold", 0.5)

    LOG.info("Model loaded. Threshold=%.3f Features=%d", threshold, len(feature_cols))

    # Build 1m data — use download_1m from build_dataset (same code as training)
    symbols = args.symbols.split(",")

    # Calculate timestamp range for download_1m
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.days * 86400 * 1000

    all_dfs = []
    for symbol in symbols:
        base = symbol.split("/")[0]
        LOG.info("Fetching 1m data for %s (%d days)...", symbol, args.days)
        try:
            # Use download_1m (same as build_dataset) — guaranteed to return
            # DataFrame with [timestamp, open, high, low, close, volume]
            ohlcv_df = download_1m(base, start_ts_ms=start_ms, end_ts_ms=now_ms)

            if ohlcv_df is None or len(ohlcv_df) < 60:
                LOG.warning("  %s: insufficient data (%d bars), skipping", symbol,
                            len(ohlcv_df) if ohlcv_df is not None else 0)
                continue

            # Save OHLCV columns before feature computation (they get dropped)
            ohlcv_cols = ohlcv_df[["open", "high", "low", "close", "volume"]].copy()

            feat_df = compute_features_1m(ohlcv_df, symbol=base)

            if len(feat_df) == 0:
                LOG.warning("  %s: compute_features_1m returned empty, skipping", symbol)
                continue

            # Merge OHLCV columns back (needed for backtest SL/exit logic)
            # Use explicit length check to avoid misalignment
            n_feat = len(feat_df)
            n_ohlcv = len(ohlcv_cols)
            if n_feat != n_ohlcv:
                LOG.warning("  %s: row mismatch feat=%d ohlcv=%d, aligning by index",
                            symbol, n_feat, n_ohlcv)
                # Take the last n_feat rows from ohlcv (features may drop warmup rows)
                ohlcv_cols = ohlcv_cols.iloc[-n_feat:].reset_index(drop=True)
                feat_df = feat_df.reset_index(drop=True)

            for col in ["open", "high", "low", "close", "volume"]:
                feat_df[col] = ohlcv_cols[col].values

            feat_df["symbol"] = base

            # Validate OHLCV columns present
            missing_ohlcv = [col for col in ["open", "high", "low", "close"] if col not in feat_df.columns]
            if missing_ohlcv:
                LOG.error("  %s: BUG — columns missing after merge: %s", symbol, missing_ohlcv)
                continue

            all_dfs.append(feat_df)
            LOG.info("  %s: %d bars, columns include OHLCV ✓", symbol, len(feat_df))
        except Exception as e:
            LOG.error("Failed for %s: %s", symbol, e)
            import traceback
            traceback.print_exc()
            continue

    if not all_dfs:
        LOG.error("No data!")
        sys.exit(1)

    combined = pd.concat(all_dfs, ignore_index=True)

    # Sort by timestamp to interleave symbols (critical for multi-symbol backtest)
    if "timestamp" in combined.columns:
        combined = combined.sort_values("timestamp", ignore_index=True)

    LOG.info("Combined: %d bars  columns=%s", len(combined),
             [c for c in combined.columns if c in ("open","high","low","close","volume","symbol")])

    # Validate combined has required columns
    for col in ["close", "high", "low"]:
        if col not in combined.columns:
            LOG.error("FATAL: column '%s' missing from combined DataFrame! Available: %s",
                      col, combined.columns.tolist())
            sys.exit(1)

    # Fill NaN in feature columns (LightGBM handles NaN natively, but fill for safety)
    for col in feature_cols:
        if col in combined.columns and combined[col].isna().any():
            combined[col] = combined[col].fillna(0)

    # Fill trade_direction for prediction
    if "trade_direction" not in combined.columns or combined["trade_direction"].isna().all():
        combined["trade_direction"] = np.where(combined["ema_alignment"] > 0, 1.0, -1.0)

    # Run backtest with different configs
    configs = [
        ("SL0.5_TS15", 0.5, 15),
        ("SL0.3_TS10", 0.3, 10),
        ("SL0.5_TS20", 0.5, 20),
        ("SL1.0_TS15", 1.0, 15),
        ("SL0.5_TS30", 0.5, 30),
        ("SL1.0_TS30", 1.0, 30),
    ]

    print(f"\n{'='*110}")
    print(f"V9 BACKTEST — Supervised Trader Classifier + Mechanical Exits")
    print(f"{'='*110}")
    print(f"  Model: AUC_test={meta.get('auc_test', 0):.4f}  Threshold={threshold:.3f}")
    print(f"  Data: {len(combined)} bars  Symbols: {symbols}")
    print(f"{'='*110}")
    print(f"  {'Config':<16} {'Trades':>6} {'LONG':>5} {'SHORT':>6} {'SL':>5} {'TS':>5} "
          f"{'WR%':>6} {'L_WR%':>6} {'S_WR%':>6} {'PnL%':>8} {'PF':>6} {'MaxDD':>7}")
    print(f"  {'-'*100}")

    for cfg_name, sl, ts in configs:
        trades = run_backtest(combined, bst, feature_cols,
                              threshold=threshold,
                              sl_pct=sl, max_hold_min=ts)
        stats = compute_stats(trades)
        print(f"  {cfg_name:<16} {stats['n_trades']:>6} {stats['n_long']:>5} {stats['n_short']:>6} "
              f"{stats['n_sl']:>5} {stats['n_ts']:>5} "
              f"{stats['wr']:>6.1f} {stats['long_wr']:>6.1f} {stats['short_wr']:>6.1f} "
              f"{stats['total_pnl']:>+8.2f} {stats['pf']:>6.2f} {stats['max_dd']:>7.2f}")

    # Per-threshold scan
    print(f"\n  ── THRESHOLD SCAN (SL=0.5% TS=15min) ──")
    for thr in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        trades = run_backtest(combined, bst, feature_cols,
                              threshold=thr, sl_pct=0.5, max_hold_min=15)
        stats = compute_stats(trades)
        print(f"  thr={thr:.1f}  N={stats['n_trades']:>5}  L={stats['n_long']:>4} S={stats['n_short']:>4}  "
              f"WR={stats['wr']:>5.1f}%  PnL={stats['total_pnl']:>+8.2f}%  PF={stats['pf']:.2f}")

    print(f"{'='*110}")


if __name__ == "__main__":
    main()
