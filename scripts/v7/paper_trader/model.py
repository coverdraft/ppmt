"""
model.py — v6-LONG LightGBM binary classification model wrapper.

On first run (no model file present), bootstrap-train a fresh model
on ~90d of historical 5m data from the same exchange. Saves to disk.

On subsequent runs, load existing model. Supports `retrain` command to
refresh on demand.

Architecture:
- LightGBM binary classification: predict P(price UP in 24h)
- Label = 1 if fwd_ret_3 > 0, 0 otherwise
- 58 features (v6 minus 'dow' — spurious with 24h horizon)
- HORIZON=288 (24h forward, 288 × 5min bars)
- Decision: LONG if P(up) > PROB_LONG, SHORT if P(up) < PROB_SHORT, else WAIT
- Fixed 50 trees, no early stopping (avoids instability)
- Quantile-based trading in evaluate_test (ranking-based)

Why classification instead of regression:
- Regression predicts MAGNITUDE, which varies with market regime
- Classification predicts DIRECTION, which is more stable across regimes
- With walk-forward validation, train/val are in different regimes
- Direction is what we actually trade on (LONG or SHORT)
- best_iter=1 on regression = model can't generalize magnitudes
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

MODEL_DIR = Path(__file__).resolve().parents[3] / "data" / "paper_trading" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PARAMS = {
    "objective": "binary",
    "metric": ["binary_logloss", "auc"],
    "num_leaves": 31,
    "learning_rate": 0.01,
    "n_estimators": 200,
    "early_stopping_rounds": 50,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "bagging_freq": 3,
    "min_data_in_leaf": 30,
    "lambda_l1": 0.3,
    "lambda_l2": 3.0,
    "verbosity": -1,
}

# Decision thresholds for binary classification
# P(up) > PROB_LONG → LONG, P(up) < PROB_SHORT → SHORT, else WAIT
# Asymmetric: crypto has structural upward drift, so SHORT needs higher conviction
PROB_LONG = 0.51   # P(UP) > this → LONG (used in model.predict)
PROB_SHORT = 0.46   # P(UP) < this → SHORT (used in model.predict)
# Cost per round-trip trade (entry + exit) in %
COST_PCT = 0.14
HORIZON = 288  # 288 * 5m = 24h forward


def model_path(symbol: str) -> Path:
    safe = symbol.replace("/", "_")
    return MODEL_DIR / f"v7_clf_{safe}.txt"


def metadata_path(symbol: str) -> Path:
    safe = symbol.replace("/", "_")
    return MODEL_DIR / f"v7_clf_{safe}_meta.json"


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
    """Train binary classification model: P(price UP in HORIZON bars).

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

    # Compute fwd_ret_3 and binary label
    c = feat_df["close"].values
    n = len(feat_df)
    fwd = np.full(n, np.nan)
    for i in range(n - HORIZON):
        fwd[i] = (c[i + HORIZON] - c[i]) / c[i] * 100
    feat_df["fwd_ret_3"] = fwd
    feat_df["label"] = (fwd > 0).astype(int)  # 1 = UP, 0 = DOWN

    # Drop rows with NaN features or NaN label
    keep_mask = feat_df[FEATURE_NAMES].notna().all(axis=1) & feat_df["fwd_ret_3"].notna()
    feat_df = feat_df.loc[keep_mask].reset_index(drop=True)
    LOG.info("model.train: clean rows=%d label_dist=%.1f%% UP",
             len(feat_df), feat_df["label"].mean() * 100)

    if len(feat_df) < 500:
        raise ValueError(f"too few clean rows ({len(feat_df)}) to train; need >=500")

    X = feat_df[FEATURE_NAMES].values.astype(np.float32)
    y = feat_df["label"].values.astype(np.float32)

    # Time-based split (no shuffle — respects causality)
    n_val = max(int(len(X) * val_frac), 100)
    n_tr = len(X) - n_val
    X_tr, X_val = X[:n_tr], X[n_tr:]
    y_tr, y_val = y[:n_tr], y[n_tr:]
    LOG.info("model.train: split n_tr=%d n_val=%d  tr_up=%.1f%% val_up=%.1f%%",
             n_tr, n_val, y_tr.mean()*100, y_val.mean()*100)

    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES, free_raw_data=False)
    d_val = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, free_raw_data=False)

    callbacks = [lgb.log_evaluation(period=50)]
    es_rounds = p.get("early_stopping_rounds", -1)
    if es_rounds and es_rounds > 0:
        callbacks.append(lgb.early_stopping(es_rounds, verbose=False))
    bst = lgb.train(
        p,
        d_tr,
        num_boost_round=p.get("n_estimators", 1000),
        valid_sets=[d_tr, d_val],
        valid_names=["train", "val"],
        callbacks=callbacks,
    )

    # Metrics
    pred_val = bst.predict(X_val)
    auc_val = float(_auc(y_val, pred_val))
    logloss_val = float(-np.mean(y_val * np.log(pred_val + 1e-15) + (1 - y_val) * np.log(1 - pred_val + 1e-15)))
    dir_acc = float(((pred_val > 0.5) == (y_val > 0.5)).mean())

    # Save
    mp = model_path(symbol)
    bst.save_model(str(mp))
    meta = {
        "symbol": symbol,
        "trained_at": int(time.time()),
        "n_train": int(n_tr),
        "n_val": int(n_val),
        "best_iteration": int(bst.best_iteration) if bst.best_iteration else 0,
        "auc_val": auc_val,
        "logloss_val": logloss_val,
        "dir_acc_val": dir_acc,
        "label_up_pct_train": float(y_tr.mean() * 100),
        "label_up_pct_val": float(y_val.mean() * 100),
        "feature_names": FEATURE_NAMES,
        "params": {k: v for k, v in p.items() if k != "early_stopping_rounds"},
        "horizon": HORIZON,
        "cost_pct": COST_PCT,
        "prob_long": PROB_LONG,
        "prob_short": PROB_SHORT,
        "model_type": "binary_classification",
        "training_rows_time_range": {
            "first_ts": int(feat_df["timestamp"].iloc[0]),
            "last_ts": int(feat_df["timestamp"].iloc[-1]),
        },
    }
    metadata_path(symbol).write_text(json.dumps(meta, indent=2))
    LOG.info("model.train: saved %s (auc=%.3f logloss=%.4f dir_acc=%.3f best_iter=%d) in %.1fs",
             mp, auc_val, logloss_val, dir_acc, meta["best_iteration"], time.time() - t0)
    return meta


def predict(bst: lgb.Booster, feature_row: dict) -> tuple[float, str]:
    """Predict P(price UP in 24h) from a feature row.

    Returns (prob_up, decision) where decision ∈ {"LONG", "SHORT", "WAIT"}.
    """
    x = np.array([[feature_row[f] for f in FEATURE_NAMES]], dtype=np.float32)
    prob_up = float(bst.predict(x)[0])
    if prob_up > PROB_LONG:
        return prob_up, "LONG"
    if prob_up < PROB_SHORT:
        return prob_up, "SHORT"
    return prob_up, "WAIT"


def _auc(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Simple AUC calculation without sklearn dependency."""
    # Sort by predicted score descending
    order = np.argsort(-y_pred)
    y_sorted = y_true[order]
    n_pos = y_sorted.sum()
    n_neg = len(y_sorted) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    # Count pairs where positive is ranked above negative
    tp = 0.0
    auc = 0.0
    for y in y_sorted:
        if y == 1:
            tp += 1
        else:
            auc += tp
    return auc / (n_pos * n_neg)
