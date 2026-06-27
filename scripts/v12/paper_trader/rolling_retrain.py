"""
rolling_retrain.py — Rolling retrain pipeline for V12 models.

Adapted from scripts/v7/v7_layer2_rolling_retrain.py for the V11/V12 pipeline.

Pipeline:
  1. Fetch recent data from Bybit (5m OHLCV for symbol + BTC + ETH)
  2. Compute 80 features using V12 feature pipeline
  3. Create binary labels (UP/DOWN) for H=12 (1h horizon)
  4. Walk-forward split: train/val/test
  5. Train LightGBM binary classifier with V11 HP presets
  6. Sequential backtest with V12 quantile configs
  7. Acceptance gate: compare to current model's AUC
  8. If accepted → atomic swap (deploy new model version)
  9. Register model version in SQLite

Usage:
  # Retrain one symbol
  python -m scripts.v12.paper_trader.rolling_retrain --symbol SOL

  # Dry-run (train + evaluate but don't deploy)
  python -m scripts.v12.paper_trader.rolling_retrain --symbol SOL --dry-run

  # Custom window
  python -m scripts.v12.paper_trader.rolling_retrain --symbol SOL --days 30

Cron (every 6h):
  0 */6 * * * cd /path/to/ppmt && python3 -m scripts.v12.paper_trader.rolling_retrain \
      --symbol SOL >> /tmp/v12_retrain_SOL.log 2>&1
"""
from __future__ import annotations

import argparse
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

# Make project importable
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.v12.paper_trader.feed import Feed
from scripts.v12.paper_trader.features import (
    compute_5m_features, latest_feature_row, ALL_FEATURE_NAMES,
)
from scripts.v12.paper_trader.model import (
    V12_SYMBOL_CONFIG, get_symbol_config, HORIZON, COST_PCT,
    PROB_LONG, PROB_SHORT, MODEL_DIR,
)
from scripts.v12.paper_trader.database import TradeDB

LOG = logging.getLogger("v12_retrain")

# ============================================================================
# Training configuration
# ============================================================================

# V11 HP presets (from v11_train.py)
HP_PRESETS = {
    "ltf_ultra_reg": {
        "objective": "binary",
        "metric": ["binary_logloss", "auc"],
        "num_leaves": 7,
        "learning_rate": 0.003,
        "min_data_in_leaf": 150,
        "lambda_l1": 5.0,
        "lambda_l2": 15.0,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.7,
        "bagging_freq": 3,
        "verbosity": -1,
        "seed": 42,
    },
    "ltf_reg": {
        "objective": "binary",
        "metric": ["binary_logloss", "auc"],
        "num_leaves": 15,
        "learning_rate": 0.005,
        "min_data_in_leaf": 80,
        "lambda_l1": 2.0,
        "lambda_l2": 8.0,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.7,
        "bagging_freq": 3,
        "verbosity": -1,
        "seed": 42,
    },
}

NUM_BOOST_ROUND = 500  # Fixed iterations, no early stopping (V7 experience)

# Acceptance gate
ACCEPT_TOLERANCE = 0.02   # 2pp AUC — within this, accept (noise)
REJECT_THRESHOLD = 0.05   # 5pp AUC — beyond this, reject (significant regression)


# ============================================================================
# Data acquisition
# ============================================================================

def fetch_training_data(feed: Feed, symbol: str, days: int = 30) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch `days` of 5m candles for symbol + BTC + ETH from Bybit.

    Uses the V12 feed's 5m API (no 1m→5m aggregation, avoids timestamp bugs).
    """
    bars_needed = 288 * days  # 288 5m bars per day
    LOG.info("fetch_training_data: %s bars_needed=%d (~%d days)", symbol, bars_needed, days)

    sym_pair = f"{symbol}/USDT"
    sym_5m = feed.fetch_5m_window(sym_pair, n_5m_bars=bars_needed)
    btc_5m = feed.fetch_5m_window("BTC/USDT", n_5m_bars=bars_needed)
    eth_5m = feed.fetch_5m_window("ETH/USDT", n_5m_bars=bars_needed)

    if len(sym_5m) < bars_needed * 0.8:
        raise RuntimeError(f"insufficient data for {symbol}: got {len(sym_5m)} / {bars_needed}")

    LOG.info("fetch_training_data: sym=%d btc=%d eth=%d bars",
             len(sym_5m), len(btc_5m), len(eth_5m))
    return sym_5m, btc_5m, eth_5m


# ============================================================================
# Feature + label computation
# ============================================================================

def build_features_and_labels(sym_5m: pd.DataFrame, btc_5m: pd.DataFrame,
                               eth_5m: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Compute 80 features and binary labels for H=12 (1h horizon).

    This mirrors what v11_build_dataset.py does but uses the V12 5m pipeline
    (no 1m microstructure — uses 5m approximation from features.py).
    """
    LOG.info("build_features_and_labels: computing features for %s", symbol)

    # Compute features
    feat_df = compute_5m_features(sym_5m, btc_5m, eth_5m)

    # Compute forward returns and binary labels for H=12
    c = feat_df["close"].values
    n = len(feat_df)
    fwd = np.full(n, np.nan)
    for i in range(n - HORIZON):
        fwd[i] = (c[i + HORIZON] - c[i]) / c[i] * 100

    feat_df["fwd_ret_h12"] = fwd
    feat_df["label_h12"] = (fwd > 0).astype(int)  # 1 = UP, 0 = DOWN

    # Drop rows with NaN features or labels
    feature_cols = [f for f in ALL_FEATURE_NAMES if f in feat_df.columns]
    keep_mask = feat_df[feature_cols].notna().all(axis=1) & feat_df["label_h12"].notna()
    feat_df = feat_df.loc[keep_mask].reset_index(drop=True)

    LOG.info("build_features_and_labels: %d clean rows, label_up=%.1f%%",
             len(feat_df), feat_df["label_h12"].mean() * 100)

    return feat_df


# ============================================================================
# Walk-forward split + training
# ============================================================================

def split_walk_forward(feat_df: pd.DataFrame, days: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split features into train/val/test by time.

    For 30d window:
      - Train: 83%
      - Val:   10% (for monitoring, not early stopping)
      - Test:  7%  (acceptance gate)
    """
    ts = feat_df["timestamp"].values
    ts_first, ts_last = ts[0], ts[-1]
    span_ms = ts_last - ts_first
    span_days = span_ms / (1000 * 86400)

    if span_days < days * 0.5:
        raise RuntimeError(f"data span {span_days:.2f}d < requested {days}d")

    test_days = max(span_days * 0.07, 0.5)
    val_days = max(span_days * 0.10, 0.5)

    test_start_ts = ts_last - int(test_days * 86400 * 1000)
    val_start_ts = test_start_ts - int(val_days * 86400 * 1000)

    train_df = feat_df[feat_df["timestamp"] < val_start_ts].reset_index(drop=True)
    val_df = feat_df[(feat_df["timestamp"] >= val_start_ts) & (feat_df["timestamp"] < test_start_ts)].reset_index(drop=True)
    test_df = feat_df[feat_df["timestamp"] >= test_start_ts].reset_index(drop=True)

    LOG.info("split: train=%d val=%d test=%d", len(train_df), len(val_df), len(test_df))
    return train_df, val_df, test_df


def _auc(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Simple AUC calculation."""
    order = np.argsort(-y_pred)
    y_sorted = y_true[order]
    n_pos = y_sorted.sum()
    n_neg = len(y_sorted) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    tp = 0.0
    auc = 0.0
    for y in y_sorted:
        if y == 1:
            tp += 1
        else:
            auc += tp
    return auc / (n_pos * n_neg)


def train_model(train_df: pd.DataFrame, val_df: pd.DataFrame,
                hp_preset: str = "ltf_ultra_reg") -> tuple[lgb.Booster, dict]:
    """Train LightGBM binary classifier."""
    feature_cols = [f for f in ALL_FEATURE_NAMES if f in train_df.columns]

    X_tr = train_df[feature_cols].values.astype(np.float32)
    y_tr = train_df["label_h12"].values.astype(np.float32)
    X_val = val_df[feature_cols].values.astype(np.float32)
    y_val = val_df["label_h12"].values.astype(np.float32)

    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=feature_cols, free_raw_data=False)
    d_val = lgb.Dataset(X_val, label=y_val, feature_name=feature_cols, free_raw_data=False)

    params = dict(HP_PRESETS.get(hp_preset, HP_PRESETS["ltf_ultra_reg"]))

    callbacks = [lgb.log_evaluation(period=0)]  # silent
    bst = lgb.train(
        params,
        d_tr,
        num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[d_tr, d_val],
        valid_names=["train", "val"],
        callbacks=callbacks,
    )

    pred_val = bst.predict(X_val)
    auc_val = float(_auc(y_val, pred_val))
    dir_acc_val = float(((pred_val > 0.5) == (y_val > 0.5)).mean())
    logloss_val = float(-np.mean(
        y_val * np.log(pred_val + 1e-15) + (1 - y_val) * np.log(1 - pred_val + 1e-15)
    ))

    metrics = {
        "best_iteration": int(bst.best_iteration) if bst.best_iteration else 0,
        "auc_val": auc_val,
        "dir_acc_val": dir_acc_val,
        "logloss_val": logloss_val,
        "label_up_pct_train": float(y_tr.mean() * 100),
        "label_up_pct_val": float(y_val.mean() * 100),
        "n_train": len(X_tr),
        "n_val": len(X_val),
    }
    return bst, metrics


def evaluate_test(bst: lgb.Booster, test_df: pd.DataFrame, symbol: str) -> dict:
    """Evaluate trained model with sequential quantile backtest."""
    feature_cols = [f for f in ALL_FEATURE_NAMES if f in test_df.columns]

    if len(test_df) == 0:
        return {"n_test": 0}

    X_test = test_df[feature_cols].values.astype(np.float32)
    y_label = test_df["label_h12"].values.astype(np.float32)
    fwd_ret = test_df["fwd_ret_h12"].values.astype(np.float64)
    pred = bst.predict(X_test)

    auc_test = float(_auc(y_label, pred))
    dir_acc = float(((pred > 0.5) == (y_label > 0.5)).mean())

    # Sequential backtest with V12 config
    cfg = get_symbol_config(symbol)
    WINDOW = cfg.get("window_size", 200)
    Q_LONG = cfg.get("q_long", 95)
    Q_SHORT = cfg.get("q_short", 5)
    trade_cost = cfg.get("cost_pct", COST_PCT)

    n_trades = 0
    n_win = 0
    pnl = 0.0
    in_trade = False
    exit_bar = 0
    recent_preds = []
    trade_returns = []

    for i in range(len(pred)):
        p_val = float(pred[i])
        recent_preds.append(p_val)
        if len(recent_preds) > WINDOW:
            recent_preds.pop(0)

        if in_trade:
            if i >= exit_bar:
                in_trade = False
            else:
                continue

        if len(recent_preds) < 20:
            continue

        q_high = np.percentile(recent_preds, Q_LONG)
        q_low = np.percentile(recent_preds, Q_SHORT)

        sig = 0
        if p_val > q_high:
            sig = 1
        elif p_val < q_low:
            sig = -1

        if sig != 0 and not np.isnan(fwd_ret[i]):
            n_trades += 1
            trade_ret = sig * fwd_ret[i] - trade_cost
            pnl += trade_ret
            trade_returns.append(trade_ret)
            in_trade = True
            exit_bar = i + HORIZON
            if trade_ret > 0:
                n_win += 1

    win_rate = n_win / n_trades if n_trades > 0 else 0
    sharpe = (np.mean(trade_returns) / np.std(trade_returns)) if len(trade_returns) > 1 else 0

    return {
        "n_test": len(X_test),
        "auc_test": auc_test,
        "dir_acc_test": dir_acc,
        "n_trades": n_trades,
        "win_rate": win_rate,
        "pnl_total_pct": float(pnl),
        "sharpe": float(sharpe),
    }


# ============================================================================
# Atomic deploy
# ============================================================================

def atomic_deploy(bst: lgb.Booster, meta: dict, symbol: str) -> None:
    """Write model + meta to .tmp files, fsync, then atomic rename."""
    from scripts.v12.paper_trader.model import model_path, metadata_path

    mp = model_path(symbol)
    mt = metadata_path(symbol)

    tmp_mp = mp.with_suffix(".txt.tmp")
    tmp_mt = mt.with_suffix(".json.tmp")

    bst.save_model(str(tmp_mp))
    tmp_mt.write_text(json.dumps(meta, indent=2))

    with open(tmp_mp, "r") as f:
        os.fsync(f.fileno())
    with open(tmp_mt, "r") as f:
        os.fsync(f.fileno())

    tmp_mp.replace(mp)
    tmp_mt.replace(mt)
    LOG.info("atomic_deploy: %s + %s", mp, mt)


# ============================================================================
# Main retrain pipeline
# ============================================================================

def run_one_retrain(symbol: str, days: int = 30, dry_run: bool = False,
                    exchange: str = "bybit") -> tuple[int, dict]:
    """Run one retrain cycle for a single symbol.

    Returns (exit_code, result_dict):
        0 = accepted (or first deploy)
        1 = rejected
        2 = error
    """
    from scripts.v12.paper_trader.model import is_trained, load_metadata

    ts_now = int(time.time())
    ts_iso = dt.datetime.utcfromtimestamp(ts_now).isoformat()

    # 1. Fetch data
    feed = Feed(exchange_id=exchange)
    try:
        sym_5m, btc_5m, eth_5m = fetch_training_data(feed, symbol, days=days)
    except Exception as e:
        LOG.exception("fetch failed for %s: %s", symbol, e)
        return 2, {"symbol": symbol, "decision": "ERROR", "error": str(e)}

    # 2. Build features + labels
    try:
        feat_df = build_features_and_labels(sym_5m, btc_5m, eth_5m, symbol)
    except Exception as e:
        LOG.exception("feature computation failed for %s: %s", symbol, e)
        return 2, {"symbol": symbol, "decision": "ERROR", "error": str(e)}

    if len(feat_df) < 1000:
        LOG.error("%s: insufficient data (%d rows); skipping", symbol, len(feat_df))
        return 2, {"symbol": symbol, "decision": "ERROR", "error": f"insufficient data: {len(feat_df)}"}

    # 3. Walk-forward split
    try:
        train_df, val_df, test_df = split_walk_forward(feat_df, days=days)
    except Exception as e:
        LOG.exception("split failed for %s: %s", symbol, e)
        return 2, {"symbol": symbol, "decision": "ERROR", "error": str(e)}

    if len(train_df) < 500:
        LOG.error("%s: train set too small (%d); skipping", symbol, len(train_df))
        return 2, {"symbol": symbol, "decision": "ERROR", "error": f"train too small: {len(train_df)}"}

    # 4. Train
    LOG.info("%s: training on %d rows", symbol, len(train_df))
    bst, train_metrics = train_model(train_df, val_df)
    LOG.info("%s: trained — val_auc=%.3f val_dir_acc=%.3f val_logloss=%.4f",
             symbol, train_metrics["auc_val"], train_metrics["dir_acc_val"],
             train_metrics["logloss_val"])

    # 5. Evaluate on test set
    test_metrics = evaluate_test(bst, test_df, symbol)
    LOG.info("%s: test — auc=%.3f dir_acc=%.3f n_trades=%d pnl=%.3f%% sharpe=%.3f",
             symbol, test_metrics.get("auc_test", 0), test_metrics.get("dir_acc_test", 0),
             test_metrics.get("n_trades", 0), test_metrics.get("pnl_total_pct", 0),
             test_metrics.get("sharpe", 0))

    # 6. Acceptance gate
    has_prior = is_trained(symbol)
    old_auc = 0.5
    if has_prior:
        try:
            old_meta = load_metadata(symbol)
            old_auc = float(old_meta.get("auc_val", 0.5))
        except Exception:
            has_prior = False

    new_auc = train_metrics["auc_val"]
    delta = new_auc - (old_auc if has_prior else 0.5)

    if not has_prior:
        decision = "FIRST_DEPLOY"
    elif delta >= -ACCEPT_TOLERANCE:
        decision = "ACCEPT"
    elif delta < -REJECT_THRESHOLD:
        decision = "REJECT"
    else:
        decision = "ACCEPT_WITH_WARNING"

    LOG.info("%s: acceptance gate — decision=%s delta_auc=%+.3f (new=%.3f old=%.3f)",
             symbol, decision, delta, new_auc, old_auc if has_prior else 0.5)

    # 7. Deploy (or skip)
    deployed = False
    if decision in ("FIRST_DEPLOY", "ACCEPT", "ACCEPT_WITH_WARNING") and not dry_run:
        # Generate version string
        version = f"v11_{symbol}_h12_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M')}"

        meta = {
            "symbol": symbol,
            "version": version,
            "trained_at": ts_now,
            "training_window_days": days,
            "model_type": "binary_classification",
            "n_train": train_metrics["n_train"],
            "n_val": train_metrics["n_val"],
            "n_test": test_metrics.get("n_test", 0),
            "best_iteration": train_metrics["best_iteration"],
            "auc_val": train_metrics["auc_val"],
            "logloss_val": train_metrics["logloss_val"],
            "dir_acc_val": train_metrics["dir_acc_val"],
            "auc_test": test_metrics.get("auc_test"),
            "dir_acc_test": test_metrics.get("dir_acc_test"),
            "n_trades_test": test_metrics.get("n_trades"),
            "win_rate_test": test_metrics.get("win_rate"),
            "pnl_total_test": test_metrics.get("pnl_total_pct"),
            "sharpe_test": test_metrics.get("sharpe"),
            "horizon": HORIZON,
            "feature_names": ALL_FEATURE_NAMES,
            "acceptance": {
                "decision": decision,
                "delta_auc": delta,
                "old_auc": old_auc if has_prior else None,
                "new_auc": new_auc,
            },
        }
        atomic_deploy(bst, meta, symbol)

        # Register in DB
        db = TradeDB(symbol)
        db.register_model_version(version, str(
            Path(__file__).resolve().parents[3] / "data" / "v11" / "models" / f"v11_clf_{symbol}_h12.txt"
        ), {
            **train_metrics,
            "acceptance_decision": decision,
            "delta_auc": delta,
            "training_window_days": days,
            "wf_win_rate": test_metrics.get("win_rate"),
            "wf_sharpe": test_metrics.get("sharpe"),
            "wf_pnl_pct": test_metrics.get("pnl_total_pct"),
        })
        db.close()

        deployed = True
        LOG.info("%s: DEPLOYED new model version %s", symbol, version)
    elif dry_run:
        LOG.info("%s: dry-run — would have deployed (decision=%s)", symbol, decision)
    else:
        LOG.warning("%s: REJECTED — keeping previous model", symbol)

    result = {
        "symbol": symbol,
        "decision": decision,
        "delta_auc": delta,
        "new_auc": new_auc,
        "old_auc": old_auc if has_prior else 0,
        "deployed": deployed,
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
    }

    exit_code = 0 if decision in ("FIRST_DEPLOY", "ACCEPT", "ACCEPT_WITH_WARNING") else 1
    return exit_code, result


# ============================================================================
# CLI
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="V12 rolling retrain")
    p.add_argument("--symbol", default="SOL",
                   help="Symbol to retrain (SOL, DOGE, AVAX)")
    p.add_argument("--days", type=int, default=30,
                   help="Training window in days")
    p.add_argument("--exchange", default="bybit",
                   choices=["bybit", "okx", "kraken", "coinbase"])
    p.add_argument("--dry-run", action="store_true",
                   help="Train + evaluate but don't deploy")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    symbol = args.symbol.replace("/USDT", "").replace("/usdt", "")

    LOG.info("=" * 60)
    LOG.info("V12 ROLLING RETRAIN — %s (days=%d dry_run=%s)", symbol, args.days, args.dry_run)
    LOG.info("=" * 60)

    try:
        ec, result = run_one_retrain(symbol, days=args.days, dry_run=args.dry_run,
                                      exchange=args.exchange)
        LOG.info("Retrain complete: decision=%s exit_code=%d", result.get("decision"), ec)
        return ec
    except Exception as e:
        LOG.exception("UNEXPECTED ERROR: %s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
