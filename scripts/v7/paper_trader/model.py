"""
model.py — v7 LightGBM binary classification model wrapper.

On first run (no model file present), bootstrap-train a fresh model
on ~180d of historical 5m data from the same exchange. Saves to disk.

On subsequent runs, load existing model. Supports `retrain` command to
refresh on demand.

Architecture:
- LightGBM binary classification: predict P(price UP in 24h)
- Label = 1 if fwd_ret_3 > 0, 0 otherwise
- 58 features (v6 minus 'dow' — spurious with 24h horizon)
- HORIZON=288 (24h forward, 288 × 5min bars) — ONLY viable horizon per comprehensive sweep
- Decision: LONG if P(up) > PROB_LONG, SHORT if P(up) < PROB_SHORT, else WAIT
- Per-symbol config overrides in SYMBOL_CONFIG (from 180d deep optimization, 5040 configs)
- Quantile-based trading in evaluate_test: per-symbol Q/window/cost from SYMBOL_CONFIG
- 3/4 core tokens have 4/4 consistency: DOGE Q95/5, AVAX Q82/18, SOL Q85/15
- ETH Q87/13 is the only 3/4 token (no 4/4 config found in deep opt)
- SHORT is essential — LONG-only always loses across all tokens
- BTC = dead end (confirmed across all configs)

Training strategy (v7 fix):
- NO early stopping — train on ALL non-test data with fixed num_boost_round=500
- The val set has massive regime shift (e.g., AVAX: train 46.7% UP, val 29.5% UP)
  causing val AUC ≈ 0.48 from iteration 1, triggering early stopping at best_iter=1
- A 1-tree model is useless; 500 trees with regularization is far more robust
- Overfitting is controlled by regularization (L1/L2, min_data_in_leaf, feature/bagging fraction)
- Test set (last 7%) is used for metrics reporting ONLY
- deep_optimize.py validated that quantile-based trading works even with imperfect models

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

# Fixed boosting iterations — NO early stopping.
# Previous approach used early_stopping_rounds=150 with a val set, but regime shift
# in the val set (e.g., AVAX: train 46.7% UP, val 29.5% UP) caused val AUC ≈ 0.48
# from iteration 1, triggering best_iter=1 and producing useless 1-tree models.
# 500 rounds with regularization is far more robust for quantile-based trading.
NUM_BOOST_ROUND = 500

DEFAULT_PARAMS = {
    "objective": "binary",
    "metric": ["binary_logloss", "auc"],
    "num_leaves": 31,
    "learning_rate": 0.01,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "bagging_freq": 3,
    "min_data_in_leaf": 30,
    "lambda_l1": 0.3,
    "lambda_l2": 3.0,
    "verbosity": -1,
}

# Per-symbol config overrides from deep optimization (180d, 5040 configs total)
# Deep opt tested: 5 HP × 7 Q × 3 windows × 3 costs × 4 folds × 4 tokens
# All 4 core tokens now have 180d-validated configs with maker fees
# Remaining tokens (LINK, XRP) use 90d sweep results
SYMBOL_CONFIG = {
    "DOGE/USDT": {
        "q_long": 95, "q_short": 5,      # Q95/5 ultra-selective
        "window_size": 400,               # longer lookback for quantile
        "cost_pct": 0.04,                  # maker fees (limit orders)
        "hp": "default",                   # default HP is best for DOGE
        "pnl_180d": 41.55, "consistency": "4/4",
        "sharpe_180d": 0.725, "max_dd": -4.61, "pf": 30.78,
    },
    "AVAX/USDT": {
        "q_long": 82, "q_short": 18,      # Q82/18 moderate-selective
        "window_size": 200,
        "cost_pct": 0.04,                  # maker fees
        "hp": "more_reg",                   # more regularization
        "pnl_180d": 44.76, "consistency": "4/4",
        "sharpe_180d": 0.292, "max_dd": -10.37, "pf": 4.69,
    },
    "SOL/USDT": {
        "q_long": 85, "q_short": 15,      # Q85/15 — 4/4 robust config
        "window_size": 200,
        "cost_pct": 0.04,                  # maker fees (upgraded from taker)
        "hp": "very_reg",                   # very_reg gives 4/4 consistency (+41.5%)
        "pnl_180d": 41.46, "consistency": "4/4",
        "sharpe_180d": 0.325, "max_dd": -12.88, "pf": 3.86,
        # Note: slow_deep Q85/15 Win=200 PnL=+48.67% but only 3/4 consistency
    },
    "ETH/USDT": {
        "q_long": 87, "q_short": 13,      # Q87/13 best for ETH (upgraded from Q80/20)
        "window_size": 400,                 # Win=400 best (upgraded from 200)
        "cost_pct": 0.04,                  # maker fees (upgraded from taker)
        "hp": "default",                   # default HP best for ETH
        "pnl_180d": 36.56, "consistency": "3/4",
        "sharpe_180d": 0.324, "max_dd": -9.15, "pf": 2.55,
    },
    # Below: from 90d comprehensive sweep (pending 180d deep optimization)
    "LINK/USDT": {
        "q_long": 90, "q_short": 10,
        "window_size": 200,
        "cost_pct": 0.14,
        "hp": "default",
        "pnl_90d": 13.0, "consistency": "3/4",
    },
    "XRP/USDT": {
        "q_long": 80, "q_short": 20,
        "window_size": 200,
        "cost_pct": 0.14,
        "hp": "default",
        "pnl_90d": 21.7, "consistency": "3/4",
    },
    # BTC intentionally excluded — dead end
}

# Backwards-compatible Q overrides (used by evaluate_test)
SYMBOL_Q_OVERRIDES = {
    sym: (cfg["q_long"], cfg["q_short"]) for sym, cfg in SYMBOL_CONFIG.items()
}

# Hyperparameter presets per symbol
HP_PRESETS = {
    "default": {
        "learning_rate": 0.01,
        "num_leaves": 31,
        "min_data_in_leaf": 30,
        "lambda_l1": 0.3,
        "lambda_l2": 3.0,
    },
    "more_reg": {
        "learning_rate": 0.005,
        "num_leaves": 15,
        "min_data_in_leaf": 50,
        "lambda_l1": 1.0,
        "lambda_l2": 5.0,
    },
    "less_reg": {
        "learning_rate": 0.02,
        "num_leaves": 63,
        "min_data_in_leaf": 20,
        "lambda_l1": 0.1,
        "lambda_l2": 1.0,
    },
    "very_reg": {
        "learning_rate": 0.01,
        "num_leaves": 15,
        "min_data_in_leaf": 100,
        "lambda_l1": 1.0,
        "lambda_l2": 10.0,
    },
    "slow_deep": {
        "learning_rate": 0.005,
        "num_leaves": 63,
        "min_data_in_leaf": 30,
        "lambda_l1": 0.5,
        "lambda_l2": 3.0,
    },
}

def get_params_for_symbol(symbol: str) -> dict:
    """Get LightGBM params with per-symbol HP overrides.

    Returns a dict of VALID LightGBM training params (no sklearn-style keys).
    """
    p = dict(DEFAULT_PARAMS)
    cfg = SYMBOL_CONFIG.get(symbol)
    if cfg and "hp" in cfg:
        hp = HP_PRESETS.get(cfg["hp"], {})
        p.update(hp)
    return p

# Decision thresholds for binary classification
# P(up) > PROB_LONG → LONG, P(up) < PROB_SHORT → SHORT, else WAIT
# For live predict(), fixed thresholds. evaluate_test() uses rolling quantiles.
PROB_LONG = 0.55   # P(UP) > this → LONG
PROB_SHORT = 0.42   # P(UP) < this → SHORT
# Default cost per round-trip trade (entry + exit) in %
# Per-symbol overrides in SYMBOL_CONFIG (DOGE/AVAX use maker=0.04%)
COST_PCT = 0.14   # taker default
HORIZON = 288  # 288 * 5m = 24h forward — ONLY viable horizon


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

    v7 FIX: Train on ALL non-test data with FIXED num_boost_round, NO early stopping.

    Previous approach used early stopping with a val set (83/10/7 split). This caused
    best_iter=1 because the val set has massive regime shift vs training:
      - AVAX: train 46.7% UP, val 29.5% UP → val AUC 0.476
      - SOL:  train 49.5% UP, val 36.4% UP → val AUC 0.463
      - DOGE: train 46.9% UP, val 41.1% UP → val AUC 0.531
    The val AUC is ≈0.48 from iteration 1 and never improves, so early stopping
    triggers at best_iter=1 producing a useless 1-tree model.

    New approach: train on ALL data except a small test holdout (7%), with
    NUM_BOOST_ROUND=500 and NO early stopping. Overfitting is controlled by
    regularization (L1/L2, min_data_in_leaf, feature/bagging fraction), not by
    early stopping on a regime-shifted val set. This produces models with 500
    trees that generate smooth, diverse predictions — essential for quantile-
    based trading which only needs rank ordering, not high AUC.

    deep_optimize.py validated this: quantile-based trading produces positive
    PnL even with imperfect models, because it trades on the RANK of predictions,
    not their absolute value.

    ohlcv_df : full historical OHLCV for the target symbol (5m, >=2000 rows)
    btc_df   : BTC/USDT 5m aligned by timestamp
    eth_df   : ETH/USDT 5m aligned by timestamp
    val_frac : IGNORED — kept for API compatibility
    params   : LightGBM params override (if None, uses per-symbol or default)
    """
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    # Remove sklearn-style params that confuse lgb.train()
    p.pop("n_estimators", None)
    p.pop("early_stopping_rounds", None)

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

    # Split: train on ALL non-test data (93%), test for metrics (7%)
    # NO validation set — no early stopping needed.
    # The previous val set caused regime shift → best_iter=1 → useless model.
    ts = feat_df["timestamp"].values
    ts_first, ts_last = ts[0], ts[-1]
    span_ms = ts_last - ts_first
    span_days = span_ms / (1000 * 86400)

    test_days = max(span_days * 0.07, 0.5)
    test_start_ts = ts_last - int(test_days * 86400 * 1000)

    train_df = feat_df[feat_df["timestamp"] < test_start_ts].reset_index(drop=True)
    test_df = feat_df[feat_df["timestamp"] >= test_start_ts].reset_index(drop=True)

    X_tr = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_tr = train_df["label"].values.astype(np.float32)

    LOG.info("model.train: train=%d test=%d  tr_up=%.1f%% test_up=%.1f%%",
             len(X_tr), len(test_df),
             y_tr.mean() * 100,
             test_df["label"].mean() * 100 if len(test_df) > 0 else 0)

    if len(X_tr) < 200:
        raise ValueError(f"insufficient training data: {len(X_tr)}")

    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES, free_raw_data=False)

    # FIXED iteration count — NO early stopping
    # 500 rounds with regularization controls overfitting better than
    # early stopping on a regime-shifted val set that triggers best_iter=1.
    callbacks = [lgb.log_evaluation(period=50)]

    bst = lgb.train(
        p,
        d_tr,
        num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[d_tr],
        valid_names=["train"],
        callbacks=callbacks,
    )

    # Metrics on train set (diagnostic) and test set (honest evaluation)
    pred_tr = bst.predict(X_tr)
    auc_tr = float(_auc(y_tr, pred_tr))

    auc_test = 0.5
    logloss_test = 0.0
    dir_acc_test = 0.5
    if len(test_df) > 0:
        X_test = test_df[FEATURE_NAMES].values.astype(np.float32)
        y_test = test_df["label"].values.astype(np.float32)
        pred_test = bst.predict(X_test)
        auc_test = float(_auc(y_test, pred_test))
        logloss_test = float(-np.mean(
            y_test * np.log(pred_test + 1e-15) +
            (1 - y_test) * np.log(1 - pred_test + 1e-15)
        ))
        dir_acc_test = float(((pred_test > 0.5) == (y_test > 0.5)).mean())

    # Save
    mp = model_path(symbol)
    bst.save_model(str(mp))
    meta = {
        "symbol": symbol,
        "trained_at": int(time.time()),
        "n_train": int(len(X_tr)),
        "n_test": int(len(test_df)),
        "num_boost_round": NUM_BOOST_ROUND,
        "auc_train": auc_tr,
        "auc_test": auc_test,
        "logloss_test": logloss_test,
        "dir_acc_test": dir_acc_test,
        "label_up_pct_train": float(y_tr.mean() * 100),
        "label_up_pct_test": float(test_df["label"].mean() * 100) if len(test_df) > 0 else 0,
        "feature_names": FEATURE_NAMES,
        "params": {k: v for k, v in p.items()},
        "horizon": HORIZON,
        "cost_pct": COST_PCT,
        "prob_long": PROB_LONG,
        "prob_short": PROB_SHORT,
        "model_type": "binary_classification",
        "split_method": "train_all_no_early_stop",
        "training_rows_time_range": {
            "first_ts": int(feat_df["timestamp"].iloc[0]),
            "last_ts": int(feat_df["timestamp"].iloc[-1]),
        },
    }
    metadata_path(symbol).write_text(json.dumps(meta, indent=2))
    LOG.info("model.train: saved %s (auc_train=%.3f auc_test=%.3f logloss_test=%.4f dir_acc_test=%.3f n_rounds=%d) in %.1fs",
             mp, auc_tr, auc_test, logloss_test, dir_acc_test, NUM_BOOST_ROUND, time.time() - t0)
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
