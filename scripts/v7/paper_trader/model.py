"""
model.py — v6-LONG LightGBM regression model wrapper.

On first run (no model file present), bootstrap-train a fresh v6-LONG model
on ~30d of historical 5m data from the same exchange. Saves to disk.

On subsequent runs, load existing model. Supports `retrain` command to
refresh on demand.

Architecture follows v6_train_wf.py:
- Single LightGBM regression on ALL labels (no sign filter — keeps directional learning)
- Label = fwd_ret_3 (15-minute forward return in %)
- 59 features (matches v6_extract_features.py)
- Anti-leakage: num_leaves=31, lr=0.05, n_estimators=500, early_stopping=50
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

from .features import FEATURE_NAMES, extract_features

LOG = logging.getLogger("pt_model")

MODEL_DIR = Path("/home/z/my-project/data/paper_trading/models")
MODEL_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PARAMS = {
    "objective": "regression",
    "metric": ["rmse", "mae"],
    "num_leaves": 31,
    "learning_rate": 0.05,
    "n_estimators": 500,
    "early_stopping_rounds": 50,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "min_data_in_leaf": 50,
    "verbosity": -1,
}

# Decision thresholds (from v7.5 walk-forward backtest: thr_long=0.20, thr_short=0.50)
THR_LONG = 0.20
THR_SHORT = 0.50
# Cost per round-trip trade (entry + exit) in %
COST_PCT = 0.14
HORIZON = 3  # 3 * 5m = 15m forward


def model_path(symbol: str) -> Path:
    safe = symbol.replace("/", "_")
    return MODEL_DIR / f"v6_long_{safe}.txt"


def metadata_path(symbol: str) -> Path:
    safe = symbol.replace("/", "_")
    return MODEL_DIR / f"v6_long_{safe}_meta.json"


def is_trained(symbol: str) -> bool:
    return model_path(symbol).exists() and metadata_path(symbol).exists()


def load_model(symbol: str) -> lgb.Booster:
    p = model_path(symbol)
    if not p.exists():
        raise FileNotFoundError(f"no model at {p}; train first")
    return lgb.Booster(model_file=str(p))


def load_metadata(symbol: str) -> dict:
    p = metadata_path(symbol)
    if not p.exists():
        raise FileNotFoundError(f"no meta at {p}")
    return json.loads(p.read_text())


def train(symbol: str, ohlcv_df: pd.DataFrame, btc_df: pd.DataFrame, eth_df: pd.DataFrame,
          val_frac: float = 0.2, params: dict | None = None) -> dict:
    """Train v6-LONG regression model.

    ohlcv_df : full historical OHLCV for the target symbol (5m, >=2000 rows recommended)
    btc_df   : BTC/USDT 5m aligned by timestamp
    eth_df   : ETH/USDT 5m aligned by timestamp
    """
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)

    LOG.info("model.train: symbol=%s rows=%d", symbol, len(ohlcv_df))
    t0 = time.time()

    # Compute features for every row
    feat_df = extract_features(ohlcv_df, btc_df, eth_df)

    # Compute fwd_ret_3 label for every row that has enough future data
    c = feat_df["close"].values
    n = len(feat_df)
    fwd = np.full(n, np.nan)
    for i in range(n - HORIZON):
        fwd[i] = (c[i + HORIZON] - c[i]) / c[i] * 100
    feat_df["fwd_ret_3"] = fwd

    # Drop rows with NaN features or NaN label
    # (first ~50 rows have NaN due to rolling windows; last HORIZON rows have NaN label)
    keep_mask = feat_df[FEATURE_NAMES].notna().all(axis=1) & feat_df["fwd_ret_3"].notna()
    feat_df = feat_df.loc[keep_mask].reset_index(drop=True)
    LOG.info("model.train: clean rows after dropna=%d", len(feat_df))

    if len(feat_df) < 500:
        raise ValueError(f"too few clean rows ({len(feat_df)}) to train; need >=500")

    X = feat_df[FEATURE_NAMES].values.astype(np.float32)
    y = feat_df["fwd_ret_3"].values.astype(np.float32)

    # Time-based split (no shuffle — respects causality)
    n_val = max(int(len(X) * val_frac), 100)
    n_tr = len(X) - n_val
    X_tr, X_val = X[:n_tr], X[n_tr:]
    y_tr, y_val = y[:n_tr], y[n_tr:]
    LOG.info("model.train: split n_tr=%d n_val=%d", n_tr, n_val)

    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES, free_raw_data=False)
    d_val = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, free_raw_data=False)

    # early_stopping_rounds is a constructor-level arg in lgb 4.x
    # (passed via params dict)
    bst = lgb.train(
        p,
        d_tr,
        num_boost_round=p.get("n_estimators", 500),
        valid_sets=[d_tr, d_val],
        valid_names=["train", "val"],
        callbacks=[lgb.log_evaluation(period=50), lgb.early_stopping(p.get("early_stopping_rounds", 50), verbose=False)],
    )

    # Metrics
    pred_val = bst.predict(X_val)
    rmse_val = float(np.sqrt(np.mean((pred_val - y_val) ** 2)))
    mae_val = float(np.mean(np.abs(pred_val - y_val)))
    corr_val = float(np.corrcoef(pred_val, y_val)[0, 1]) if len(y_val) > 1 else 0.0
    dir_acc = float(((pred_val > 0) == (y_val > 0)).mean())

    # Save
    mp = model_path(symbol)
    bst.save_model(str(mp))
    meta = {
        "symbol": symbol,
        "trained_at": int(time.time()),
        "n_train": int(n_tr),
        "n_val": int(n_val),
        "best_iteration": int(bst.best_iteration) if bst.best_iteration else 0,
        "rmse_val": rmse_val,
        "mae_val": mae_val,
        "corr_val": corr_val,
        "dir_acc_val": dir_acc,
        "feature_names": FEATURE_NAMES,
        "params": {k: v for k, v in p.items() if k != "early_stopping_rounds"},
        "horizon": HORIZON,
        "cost_pct": COST_PCT,
        "thr_long": THR_LONG,
        "thr_short": THR_SHORT,
        "training_rows_time_range": {
            "first_ts": int(feat_df["timestamp"].iloc[0]),
            "last_ts": int(feat_df["timestamp"].iloc[-1]),
        },
    }
    metadata_path(symbol).write_text(json.dumps(meta, indent=2))
    LOG.info("model.train: saved %s (rmse=%.4f corr=%.3f dir_acc=%.3f) in %.1fs",
             mp, rmse_val, corr_val, dir_acc, time.time() - t0)
    return meta


def predict(bst: lgb.Booster, feature_row: dict) -> tuple[float, str]:
    """Predict fwd_ret_3 from a feature row.

    Returns (pred, decision) where decision ∈ {"LONG", "SHORT", "WAIT"}.
    """
    x = np.array([[feature_row[f] for f in FEATURE_NAMES]], dtype=np.float32)
    pred = float(bst.predict(x)[0])
    if pred > THR_LONG:
        return pred, "LONG"
    if pred < -THR_SHORT:
        return pred, "SHORT"
    return pred, "WAIT"
