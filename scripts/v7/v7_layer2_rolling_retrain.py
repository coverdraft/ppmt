"""
v7_layer2_rolling_retrain.py — F9 Layer 2 rolling retrain.

Per PPMT_v7_MASTER_PLAN.md §6.2 (adapted to v7.5 single-regression architecture):

  Trigger  : every 6h (00:00, 06:00, 12:00, 18:00 UTC) — driven by cron.
  Window   : last 90 days of 5m candles (= 25920 bars per symbol).
  Symbols  : BTC/USDT, ETH/USDT, SOL/USDT (configurable).
  Pipeline :
    1. Fetch 90d OHLCV for symbol + BTC + ETH from Bybit (paginated).
    2. Compute 58 v6 features (no leakage — all backward-looking, 'dow' removed).
    3. Compute fwd_ret_3 labels (24h forward return, HORIZON=288).
    4. Walk-forward split: train=days 1-75, val=days 76-84, test=days 85-90.
    5. Train v6-LONG LightGBM regression (single regression on ALL labels,
       no sign filter — preserves directional learning per F7b finding).
       Strong regularization (L1/L2, min_data_in_leaf=100) handles label
       autocorrelation from 24h overlapping windows.
    6. Acceptance gate:
         - new_val_dir_acc >= current_val_dir_acc - ACCEPT_TOLERANCE (2pp)
           → ACCEPT (deploy new model via atomic swap)
         - new_val_dir_acc < current_val_dir_acc - REJECT_THRESHOLD (5pp)
           → REJECT (keep old model, log alert)
         - In between → ACCEPT with warning (within noise band)
    7. Atomic swap: write to .tmp, fsync, rename.
    8. Log row to data/paper_trading/logs/retrain_<SYM>.csv.

Exit codes (cron-friendly):
  0 = accepted (or no prior model — first deploy)
  1 = rejected (significant regression vs prior)
  2 = error (data fetch / training failure)

Usage:
  # Retrain one symbol
  python3 scripts/v7/v7_layer2_rolling_retrain.py --symbol SOL/USDT

  # Retrain multiple symbols
  python3 scripts/v7/v7_layer2_rolling_retrain.py \
      --symbols "BTC/USDT,ETH/USDT,SOL/USDT"

  # Custom window (e.g. 14d for testing)
  python3 scripts/v7/v7_layer2_rolling_retrain.py --symbol SOL/USDT --days 14

  # Dry-run: train + evaluate but don't deploy
  python3 scripts/v7/v7_layer2_rolling_retrain.py --symbol SOL/USDT --dry-run

Cron (every 6h at 00:30, 06:30, 12:30, 18:30 UTC):
  30 */6 * * * cd /home/z/my-project && \\
      python3 scripts/v7/v7_layer2_rolling_retrain.py \\
          --symbols "BTC/USDT,ETH/USDT,SOL/USDT" \\
          >> /tmp/pt_layer2.cron.log 2>&1
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

# Make paper_trader package importable
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent))  # add /home/z/my-project

from scripts.v7.paper_trader.feed import Feed
from scripts.v7.paper_trader.model import (
    FEATURE_NAMES, train, load_model, load_metadata, is_trained,
    model_path, metadata_path, MODEL_DIR, DEFAULT_PARAMS, HORIZON,
)
from scripts.v7.paper_trader.features import extract_features

LOG = logging.getLogger("pt_layer2")

LOGS_DIR = SCRIPT_DIR.parents[2] / "data" / "paper_trading" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Acceptance gate thresholds (percentage points)
ACCEPT_TOLERANCE = 0.02     # 2pp — within this band of old dir_acc, accept (noise)
REJECT_THRESHOLD = 0.05     # 5pp — beyond this, reject (significant regression)

RETRAIN_LOG_HEADER = [
    "ts_utc", "ts_iso", "symbol",
    "window_days", "n_train", "n_val", "n_test",
    "new_val_dir_acc", "new_val_rmse", "new_val_corr",
    "old_val_dir_acc", "old_val_rmse",
    "decision",  # ACCEPT, ACCEPT_WITH_WARNING, REJECT, FIRST_DEPLOY, ERROR
    "delta_dir_acc",
    "model_path", "trained_at",
]


# ----------------------------------------------------------------------------
# Data acquisition
# ----------------------------------------------------------------------------

def fetch_30d_data(feed: Feed, symbol: str, days: int = 30) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch `days` of 5m candles for symbol + BTC + ETH from Bybit.

    5m × 288/day × days = bars needed. Bybit allows 1000 candles per call,
    so we paginate backward via fetch_history (already implemented in Feed).
    """
    bars_needed = 288 * days
    LOG.info("fetch_30d_data: %s bars_needed=%d (~%d days)", symbol, bars_needed, days)

    sym_raw = feed.fetch_history(symbol, "5m", limit=bars_needed)
    btc_raw = feed.fetch_history("BTC/USDT", "5m", limit=bars_needed)
    eth_raw = feed.fetch_history("ETH/USDT", "5m", limit=bars_needed)

    if len(sym_raw) < bars_needed * 0.9:
        raise RuntimeError(f"insufficient data for {symbol}: got {len(sym_raw)} / {bars_needed}")
    if len(btc_raw) < bars_needed * 0.9:
        raise RuntimeError(f"insufficient BTC data: got {len(btc_raw)} / {bars_needed}")
    if len(eth_raw) < bars_needed * 0.9:
        raise RuntimeError(f"insufficient ETH data: got {len(eth_raw)} / {bars_needed}")

    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    sym_df = pd.DataFrame(sym_raw, columns=cols)
    btc_df = pd.DataFrame(btc_raw, columns=cols)
    eth_df = pd.DataFrame(eth_raw, columns=cols)

    # Intersection of timestamps (in case BTC/ETH have slightly different listing dates)
    common_ts = set(sym_df["timestamp"]) & set(btc_df["timestamp"]) & set(eth_df["timestamp"])
    sym_df = sym_df[sym_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
    btc_df = btc_df[btc_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
    eth_df = eth_df[eth_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)

    LOG.info("fetch_30d_data: aligned rows=%d (span: %s → %s)",
             len(sym_df),
             dt.datetime.utcfromtimestamp(sym_df["timestamp"].iloc[0] / 1000).isoformat(),
             dt.datetime.utcfromtimestamp(sym_df["timestamp"].iloc[-1] / 1000).isoformat())
    return sym_df, btc_df, eth_df


# ----------------------------------------------------------------------------
# Walk-forward split + evaluation
# ----------------------------------------------------------------------------

def split_walk_forward(feat_df: pd.DataFrame, days: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split features into train/val/test by time, proportional to window.

    For 30d window (default):
      - Train : days 1-25 (83%)
      - Val   : days 26-28 (10% — early stopping)
      - Test  : days 29-30 (7% — final acceptance gate, NOT used for early stopping)

    For smaller windows, the same proportions are applied (with a minimum of
    100 rows in each split to be useful).
    """
    ts = feat_df["timestamp"].values
    ts_first, ts_last = ts[0], ts[-1]
    span_ms = ts_last - ts_first
    span_days = span_ms / (1000 * 86400)
    if span_days < days * 0.9:
        raise RuntimeError(f"data span {span_days:.2f}d < requested {days}d")

    # Proportional split: 83/10/7
    test_days = max(span_days * 0.07, 0.5)
    val_days = max(span_days * 0.10, 0.5)

    test_start_ts = ts_last - int(test_days * 86400 * 1000)
    val_start_ts = test_start_ts - int(val_days * 86400 * 1000)

    train_df = feat_df[feat_df["timestamp"] < val_start_ts].reset_index(drop=True)
    val_df = feat_df[(feat_df["timestamp"] >= val_start_ts) & (feat_df["timestamp"] < test_start_ts)].reset_index(drop=True)
    test_df = feat_df[feat_df["timestamp"] >= test_start_ts].reset_index(drop=True)

    LOG.info("split: train=%d val=%d test=%d (val_start=%s test_start=%s)",
             len(train_df), len(val_df), len(test_df),
             dt.datetime.utcfromtimestamp(val_start_ts / 1000).isoformat(),
             dt.datetime.utcfromtimestamp(test_start_ts / 1000).isoformat())
    return train_df, val_df, test_df


def train_with_split(train_df: pd.DataFrame, val_df: pd.DataFrame, params: dict | None = None) -> tuple[lgb.Booster, dict]:
    """Train LightGBM on train_df, evaluate on val_df. Returns (booster, metrics)."""
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)

    X_tr = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_tr = train_df["fwd_ret_3"].values.astype(np.float32)
    X_val = val_df[FEATURE_NAMES].values.astype(np.float32)
    y_val = val_df["fwd_ret_3"].values.astype(np.float32)

    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES, free_raw_data=False)
    d_val = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, free_raw_data=False)

    bst = lgb.train(
        p,
        d_tr,
        num_boost_round=p.get("n_estimators", 500),
        valid_sets=[d_tr, d_val],
        valid_names=["train", "val"],
        callbacks=[lgb.log_evaluation(period=100), lgb.early_stopping(p.get("early_stopping_rounds", 50), verbose=False)],
    )

    pred_val = bst.predict(X_val)
    rmse_val = float(np.sqrt(np.mean((pred_val - y_val) ** 2)))
    mae_val = float(np.mean(np.abs(pred_val - y_val)))
    corr_val = float(np.corrcoef(pred_val, y_val)[0, 1]) if len(y_val) > 1 else 0.0
    dir_acc = float(((pred_val > 0) == (y_val > 0)).mean())

    metrics = {
        "best_iteration": int(bst.best_iteration) if bst.best_iteration else 0,
        "rmse_val": rmse_val,
        "mae_val": mae_val,
        "corr_val": corr_val,
        "dir_acc_val": dir_acc,
        "n_train": len(X_tr),
        "n_val": len(X_val),
    }
    return bst, metrics


def evaluate_test(bst: lgb.Booster, test_df: pd.DataFrame) -> dict:
    """Evaluate trained model on held-out test set (final acceptance gate)."""
    if len(test_df) == 0:
        return {"n_test": 0}
    X_test = test_df[FEATURE_NAMES].values.astype(np.float32)
    y_test = test_df["fwd_ret_3"].values.astype(np.float32)
    pred = bst.predict(X_test)
    rmse = float(np.sqrt(np.mean((pred - y_test) ** 2)))
    corr = float(np.corrcoef(pred, y_test)[0, 1]) if len(y_test) > 1 else 0.0
    dir_acc = float(((pred > 0) == (y_test > 0)).mean())
    # Trading simulation: LONG if pred > thr_long, SHORT if pred < -thr_short
    from scripts.v7.paper_trader.model import THR_LONG, THR_SHORT, COST_PCT
    longs = pred > THR_LONG
    shorts = pred < -THR_SHORT
    pnl_long = (y_test[longs] - COST_PCT).sum() if longs.any() else 0.0
    pnl_short = (-y_test[shorts] - COST_PCT).sum() if shorts.any() else 0.0
    n_trades = int(longs.sum() + shorts.sum())
    return {
        "n_test": len(X_test),
        "rmse_test": rmse,
        "corr_test": corr,
        "dir_acc_test": dir_acc,
        "n_trades": n_trades,
        "pnl_long_pct": float(pnl_long),
        "pnl_short_pct": float(pnl_short),
        "pnl_total_pct": float(pnl_long + pnl_short),
    }


# ----------------------------------------------------------------------------
# Atomic swap + acceptance gate
# ----------------------------------------------------------------------------

def atomic_deploy(bst: lgb.Booster, meta: dict, symbol: str) -> None:
    """Write model + meta to .tmp files, fsync, then atomic rename."""
    mp = model_path(symbol)
    mt = metadata_path(symbol)

    # Write to .tmp
    tmp_mp = mp.with_suffix(".txt.tmp")
    tmp_mt = mt.with_suffix(".json.tmp")

    bst.save_model(str(tmp_mp))
    tmp_mt.write_text(json.dumps(meta, indent=2))

    # fsync
    with open(tmp_mp, "r") as f:
        os.fsync(f.fileno())
    with open(tmp_mt, "r") as f:
        os.fsync(f.fileno())

    # Atomic rename
    tmp_mp.replace(mp)
    tmp_mt.replace(mt)
    LOG.info("atomic_deploy: %s + %s", mp, mt)


def log_retrain_row(row: dict, symbol: str) -> None:
    log_path = LOGS_DIR / f"retrain_{symbol.replace('/', '_')}.csv"
    header_exists = log_path.exists() and log_path.stat().st_size > 0
    with open(log_path, "a", newline="") as f:
        w = csv.writer(f)
        if not header_exists:
            w.writerow(RETRAIN_LOG_HEADER)
        w.writerow([row.get(h, "") for h in RETRAIN_LOG_HEADER])


def run_one_retrain(feed: Feed, symbol: str, days: int = 30, dry_run: bool = False) -> tuple[int, dict]:
    """Run one retrain cycle for a single symbol. Returns (exit_code, result_dict)."""
    ts_now = int(time.time())
    ts_iso = dt.datetime.utcfromtimestamp(ts_now).isoformat()

    # 1. Fetch data
    try:
        sym_df, btc_df, eth_df = fetch_30d_data(feed, symbol, days=days)
    except Exception as e:
        LOG.exception("fetch failed for %s: %s", symbol, e)
        row = {"ts_utc": ts_now, "ts_iso": ts_iso, "symbol": symbol,
               "symbol": symbol, "decision": "ERROR", "model_path": ""}
        log_retrain_row(row, symbol)
        return 2, row

    # 2. Compute features + labels
    feat_df = extract_features(sym_df, btc_df, eth_df)
    c = feat_df["close"].values
    n = len(feat_df)
    fwd = np.full(n, np.nan)
    for i in range(n - HORIZON):
        fwd[i] = (c[i + HORIZON] - c[i]) / c[i] * 100
    feat_df["fwd_ret_3"] = fwd

    keep_mask = feat_df[FEATURE_NAMES].notna().all(axis=1) & feat_df["fwd_ret_3"].notna()
    feat_df = feat_df.loc[keep_mask].reset_index(drop=True)
    LOG.info("%s: clean feature rows=%d", symbol, len(feat_df))

    # 3. Walk-forward split
    try:
        train_df, val_df, test_df = split_walk_forward(feat_df, days=days)
    except Exception as e:
        LOG.exception("split failed for %s: %s", symbol, e)
        return 2, {"symbol": symbol, "decision": "ERROR", "error": str(e)}

    if len(train_df) < 1000:
        LOG.error("%s: train set too small (%d); skipping", symbol, len(train_df))
        return 2, {"symbol": symbol, "decision": "ERROR", "error": f"train set too small: {len(train_df)}"}

    # 3b. NO subsampling — train/val on all rows with strong regularization instead.
    #     With HORIZON=288 (24h), consecutive labels are 99.97% overlapping, so the
    #     effective independent sample size is ~days, not ~bars. But LightGBM with
    #     min_data_in_leaf=100, L1/L2 reg, low lr, and feature/bagging subsampling
    #     is constrained enough to not overfit the autocorrelation structure.
    #     Val is temporally out-of-sample (walk-forward), so direction accuracy
    #     comparison between models is fair even with correlated rows.
    LOG.info("%s: training on all %d rows (no subsample — regularization handles autocorr)",
             symbol, len(train_df))

    # 4. Train
    bst, train_metrics = train_with_split(train_df, val_df)
    LOG.info("%s: trained — val_dir_acc=%.3f val_rmse=%.4f val_corr=%.3f best_iter=%d",
             symbol, train_metrics["dir_acc_val"], train_metrics["rmse_val"],
             train_metrics["corr_val"], train_metrics["best_iteration"])

    # 5. Evaluate on test set
    test_metrics = evaluate_test(bst, test_df)
    LOG.info("%s: test — dir_acc=%.3f n_trades=%d pnl_total=%.3f%%",
             symbol, test_metrics.get("dir_acc_test", 0), test_metrics.get("n_trades", 0),
             test_metrics.get("pnl_total_pct", 0))

    # 6. Acceptance gate — compare to existing model
    has_prior = is_trained(symbol)
    if has_prior:
        try:
            old_meta = load_metadata(symbol)
            old_dir_acc = float(old_meta.get("dir_acc_val", 0))
            old_rmse = float(old_meta.get("rmse_val", 0))
        except Exception:
            has_prior = False
            old_dir_acc = 0.0
            old_rmse = 0.0

    new_dir_acc = train_metrics["dir_acc_val"]
    delta = new_dir_acc - (old_dir_acc if has_prior else 0)

    if not has_prior:
        decision = "FIRST_DEPLOY"
    elif delta >= -ACCEPT_TOLERANCE:
        decision = "ACCEPT"
    elif delta < -REJECT_THRESHOLD:
        decision = "REJECT"
    else:
        decision = "ACCEPT_WITH_WARNING"

    LOG.info("%s: acceptance gate — decision=%s delta_dir_acc=%+.3f (new=%.3f old=%.3f)",
             symbol, decision, delta, new_dir_acc, old_dir_acc if has_prior else 0)

    # 7. Deploy (or skip)
    deployed = False
    if decision in ("FIRST_DEPLOY", "ACCEPT", "ACCEPT_WITH_WARNING") and not dry_run:
        meta = {
            "symbol": symbol,
            "trained_at": ts_now,
            "training_window_days": days,
            "n_train": train_metrics["n_train"],
            "n_val": train_metrics["n_val"],
            "n_test": test_metrics.get("n_test", 0),
            "best_iteration": train_metrics["best_iteration"],
            "rmse_val": train_metrics["rmse_val"],
            "mae_val": train_metrics["mae_val"],
            "corr_val": train_metrics["corr_val"],
            "dir_acc_val": train_metrics["dir_acc_val"],
            "rmse_test": test_metrics.get("rmse_test"),
            "corr_test": test_metrics.get("corr_test"),
            "dir_acc_test": test_metrics.get("dir_acc_test"),
            "n_trades_test": test_metrics.get("n_trades"),
            "pnl_long_test": test_metrics.get("pnl_long_pct"),
            "pnl_short_test": test_metrics.get("pnl_short_pct"),
            "pnl_total_test": test_metrics.get("pnl_total_pct"),
            "feature_names": FEATURE_NAMES,
            "horizon": HORIZON,
            "acceptance": {
                "decision": decision,
                "delta_dir_acc": delta,
                "old_dir_acc": old_dir_acc if has_prior else None,
                "new_dir_acc": new_dir_acc,
                "accept_tolerance": ACCEPT_TOLERANCE,
                "reject_threshold": REJECT_THRESHOLD,
            },
            "training_rows_time_range": {
                "first_ts": int(train_df["timestamp"].iloc[0]),
                "last_ts": int(val_df["timestamp"].iloc[-1]),
                "test_first_ts": int(test_df["timestamp"].iloc[0]) if len(test_df) else None,
                "test_last_ts": int(test_df["timestamp"].iloc[-1]) if len(test_df) else None,
            },
        }
        atomic_deploy(bst, meta, symbol)
        deployed = True
        LOG.info("%s: DEPLOYED new model", symbol)
    elif dry_run:
        LOG.info("%s: dry-run — would have deployed (decision=%s)", symbol, decision)
    else:
        LOG.warning("%s: REJECTED — keeping previous model", symbol)

    # 8. Log row
    row = {
        "ts_utc": ts_now,
        "ts_iso": ts_iso,
        "symbol": symbol,
        "window_days": days,
        "n_train": train_metrics["n_train"],
        "n_val": train_metrics["n_val"],
        "n_test": test_metrics.get("n_test", 0),
        "new_val_dir_acc": new_dir_acc,
        "new_val_rmse": train_metrics["rmse_val"],
        "new_val_corr": train_metrics["corr_val"],
        "old_val_dir_acc": old_dir_acc if has_prior else 0,
        "old_val_rmse": old_rmse if has_prior else 0,
        "decision": decision,
        "delta_dir_acc": delta,
        "model_path": str(model_path(symbol)) if deployed else "",
        "trained_at": ts_now if deployed else "",
    }
    log_retrain_row(row, symbol)

    exit_code = 0 if decision in ("FIRST_DEPLOY", "ACCEPT", "ACCEPT_WITH_WARNING") else 1
    return exit_code, row


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="F9 Layer 2 — rolling retrain for v7.5 paper trader")
    p.add_argument("--symbol", default="SOL/USDT")
    p.add_argument("--symbols", default=None,
                   help="comma-separated list (overrides --symbol)")
    p.add_argument("--days", type=int, default=90,
                   help="training window in days (default 90 for 24h label horizon)")
    p.add_argument("--exchange", default="bybit", choices=["bybit", "okx", "kraken", "coinbase"])
    p.add_argument("--dry-run", action="store_true",
                   help="train + evaluate but don't deploy")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    symbols = args.symbols.split(",") if args.symbols else [args.symbol]
    feed = Feed(exchange_id=args.exchange)

    exit_codes = []
    for sym in symbols:
        LOG.info("=" * 60)
        LOG.info("LAYER2 RETRAIN — %s (days=%d dry_run=%s)", sym, args.days, args.dry_run)
        LOG.info("=" * 60)
        try:
            ec, _ = run_one_retrain(feed, sym, days=args.days, dry_run=args.dry_run)
            exit_codes.append(ec)
        except Exception as e:
            LOG.exception("UNEXPECTED ERROR for %s: %s", sym, e)
            exit_codes.append(2)

    # Aggregate: 0 if all accepted, 1 if any rejected, 2 if any error
    if any(ec == 2 for ec in exit_codes):
        return 2
    if any(ec == 1 for ec in exit_codes):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
