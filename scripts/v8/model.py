"""
model.py — v8 Pattern-Informed LightGBM on EV Labels

Based on CORRECTED pattern analysis (446 entries, both long+short):
  BREAKOUT long:  230 trades, 73.9% WR, PnL +251.1 → model should strongly favor
  BREAKOUT short: 165 trades, 68.5% WR, PnL -556.2 → model must filter these out
  EMA_BOUNCE short: 14 trades, 85.7% WR, PnL +27.3  → counter-trend edge
  LEVEL_TEST short: 11 trades, 100% WR, PnL +33.2   → support bounce edge

  KEY: Direction matters! Same breakout features → opposite EV per direction.
  The `trade_direction` feature is the most critical signal.

Strategy:
  1. Regression on EV labels — predict E[trade return] for 30min window
  2. Two-sided expansion: each bar → LONG + SHORT rows
  3. `trade_direction` feature lets model learn direction-dependent EV
  4. Sample weighting by uniqueness (López de Prado)
  5. Kelly Criterion position sizing from predicted EV
  6. Hard rules: TIME STOP 30min, NO averaging down, max 3 entries
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path
from typing import Optional

import lightgbm as lgb
import numpy as np
import pandas as pd

# Force-disable Copy-on-Write — causes "assignment destination is read-only"
pd.options.mode.copy_on_write = False

from .features import FEATURE_NAMES, N_FEATURES
from .labels import compute_ev_labels_fast, compute_ev_labels_both_sides, label_stats
from .validation import PurgedKFold, purged_cross_val_score

LOG = logging.getLogger("v8_model")

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "v8_models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Training configuration — from pattern analysis
# ---------------------------------------------------------------------------

# Label parameters (MUST match labels.py)
TP_ATR_MULT = 1.5
SL_ATR_MULT = 1.0
LOOKAHEAD = 6            # 6 bars = 30min at 5m
ATR_LAG_OFFSET = 3

# LightGBM parameters — regression on EV
DEFAULT_PARAMS = {
    "objective": "regression",
    "metric": ["rmse", "mae"],
    "num_leaves": 31,
    "learning_rate": 0.02,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "bagging_freq": 3,
    "min_data_in_leaf": 200,
    "lambda_l1": 0.5,
    "lambda_l2": 3.0,
    "verbosity": -1,
    "seed": 42,
}

NUM_BOOST_ROUND = 500

# Trading parameters
COST_PCT_MAKER = 0.04
COST_PCT_TAKER = 0.14
DEFAULT_COST = COST_PCT_MAKER

# Decision thresholds
EV_THRESHOLD_LONG = 0.05
EV_THRESHOLD_SHORT = 0.05

# Hard rules from pattern analysis
MAX_HOLD_BARS = LOOKAHEAD   # Time stop = 30min
MAX_ENTRIES_PER_TRADE = 3   # Allow up to 3 (like trader DCA, but only with EV confirmation)
MAX_CONCURRENT_POSITIONS = 5


def compute_sample_weights(labels: np.ndarray, label_timestamps: np.ndarray = None) -> np.ndarray:
    """Compute sample weights based on uniqueness (López de Prado)."""
    n = len(labels)
    if n == 0:
        return np.array([])

    signs = np.sign(labels)
    weights = np.ones(n, dtype=np.float64)

    run_length = 1
    for i in range(1, n):
        if signs[i] == signs[i - 1] and signs[i] != 0:
            run_length += 1
        else:
            run_length = 1
        weights[i] = max(1.0 / run_length, 0.2)

    run_length = 1
    for i in range(n - 2, -1, -1):
        if signs[i] == signs[i + 1] and signs[i] != 0:
            run_length += 1
        else:
            run_length = 1
        weights[i] = max(weights[i], max(1.0 / run_length, 0.2))

    weights = weights / weights.mean()
    return weights


def custom_sharpe_eval(preds: np.ndarray, train_data: lgb.Dataset) -> tuple[str, float, bool]:
    """Custom evaluation metric: negative Sharpe ratio."""
    labels = train_data.get_label()
    pnl = np.sign(preds) * labels
    if len(pnl) < 2 or np.std(pnl) < 1e-10:
        return "neg_sharpe", 0.0, False
    sharpe = np.mean(pnl) / np.std(pnl)
    return "neg_sharpe", -sharpe, False


def train_model(
    train_df: pd.DataFrame,
    val_df: Optional[pd.DataFrame] = None,
    params: Optional[dict] = None,
    num_boost_round: int = NUM_BOOST_ROUND,
    use_sample_weights: bool = True,
) -> tuple[lgb.Booster, dict]:
    """Train multi-token LightGBM regression model on EV labels."""
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)

    train_clean = train_df.dropna(subset=["ev_label"])
    if len(train_clean) < 500:
        raise ValueError(f"Insufficient training data: {len(train_clean)} rows")

    X_tr = train_clean[FEATURE_NAMES].values.astype(np.float32)
    y_tr = train_clean["ev_label"].values.astype(np.float32)

    weights = None
    if use_sample_weights:
        weights = compute_sample_weights(y_tr)
        LOG.info("Sample weights: mean=%.3f std=%.3f", weights.mean(), weights.std())

    d_tr = lgb.Dataset(
        X_tr, label=y_tr, weight=weights,
        feature_name=FEATURE_NAMES, free_raw_data=False,
    )

    valid_sets = [d_tr]
    valid_names = ["train"]
    callbacks = [lgb.log_evaluation(period=50)]

    if val_df is not None and len(val_df) > 50:
        val_clean = val_df.dropna(subset=["ev_label"])
        X_val = val_clean[FEATURE_NAMES].values.astype(np.float32)
        y_val = val_clean["ev_label"].values.astype(np.float32)
        d_val = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, free_raw_data=False)
        valid_sets.append(d_val)
        valid_names.append("val")
        callbacks.append(lgb.early_stopping(50, verbose=False))

    t0 = time.time()
    bst = lgb.train(
        p, d_tr,
        num_boost_round=num_boost_round,
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=callbacks,
    )
    elapsed = time.time() - t0

    pred_tr = bst.predict(X_tr)
    corr_tr = float(np.corrcoef(y_tr, pred_tr)[0, 1]) if len(y_tr) > 2 else 0.0
    dir_acc_tr = float((np.sign(pred_tr) == np.sign(y_tr)).mean())
    pnl_tr = np.sign(pred_tr) * y_tr
    sharpe_tr = float(np.mean(pnl_tr) / max(np.std(pnl_tr), 1e-10))

    imp = bst.feature_importance(importance_type="gain")
    top_feat_idx = int(np.argmax(imp))

    val_metrics = {}
    if val_df is not None and len(val_df) > 50:
        val_clean = val_df.dropna(subset=["ev_label"])
        X_val = val_clean[FEATURE_NAMES].values.astype(np.float32)
        y_val = val_clean["ev_label"].values.astype(np.float32)
        pred_val = bst.predict(X_val)
        corr_val = float(np.corrcoef(y_val, pred_val)[0, 1])
        dir_acc_val = float((np.sign(pred_val) == np.sign(y_val)).mean())
        pnl_val = np.sign(pred_val) * y_val
        sharpe_val = float(np.mean(pnl_val) / max(np.std(pnl_val), 1e-10))
        val_metrics = {
            "corr_val": corr_val,
            "dir_acc_val": dir_acc_val,
            "sharpe_val": sharpe_val,
        }

    metrics = {
        "n_train": len(X_tr),
        "corr_train": corr_tr,
        "dir_acc_train": dir_acc_tr,
        "sharpe_train": sharpe_tr,
        "top_feat_name": FEATURE_NAMES[top_feat_idx],
        "training_time_s": elapsed,
        "best_iteration": int(bst.best_iteration) if bst.best_iteration else num_boost_round,
        **val_metrics,
    }

    LOG.info(
        "train: n=%d corr_tr=%.4f dir_tr=%.3f sharpe_tr=%.3f top=%s in %.1fs",
        len(X_tr), corr_tr, dir_acc_tr, sharpe_tr,
        FEATURE_NAMES[top_feat_idx], elapsed,
    )

    return bst, metrics


def predict_ev(bst: lgb.Booster, feature_row: dict) -> tuple[float, str, float]:
    """Predict E[trade return] for BOTH directions and pick the best."""
    row_long = dict(feature_row)
    row_long["trade_direction"] = 1.0
    x_long = np.array([[row_long.get(f, np.nan) for f in FEATURE_NAMES]], dtype=np.float32)
    ev_long = float(bst.predict(x_long)[0])
    
    row_short = dict(feature_row)
    row_short["trade_direction"] = -1.0
    x_short = np.array([[row_short.get(f, np.nan) for f in FEATURE_NAMES]], dtype=np.float32)
    ev_short = float(bst.predict(x_short)[0])
    
    if ev_long > ev_short:
        ev = ev_long
        direction = "LONG"
    else:
        ev = ev_short
        direction = "SHORT"
    
    if direction == "LONG" and ev > EV_THRESHOLD_LONG:
        size_signal = ev / EV_THRESHOLD_LONG
    elif direction == "SHORT" and ev > EV_THRESHOLD_SHORT:
        size_signal = ev / EV_THRESHOLD_SHORT
    else:
        direction = "WAIT"
        size_signal = 0.0
    
    return ev, direction, size_signal


def save_model(bst: lgb.Booster, metrics: dict, path: Path) -> None:
    """Save model and metadata."""
    bst.save_model(str(path))
    meta_path = path.with_suffix(".json")
    meta = {
        **metrics,
        "feature_names": FEATURE_NAMES,
        "label_type": "ev_regression",
        "tp_atr_mult": TP_ATR_MULT,
        "sl_atr_mult": SL_ATR_MULT,
        "lookahead": LOOKAHEAD,
        "atr_lag_offset": ATR_LAG_OFFSET,
        "ev_threshold_long": EV_THRESHOLD_LONG,
        "ev_threshold_short": EV_THRESHOLD_SHORT,
        "cost_pct": DEFAULT_COST,
        "max_hold_bars": MAX_HOLD_BARS,
        "model_version": "v8_pattern",
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    LOG.info("Model saved to %s", path)


def load_model(path: Path) -> lgb.Booster:
    """Load a saved model."""
    return lgb.Booster(model_file=str(path))


# ---------------------------------------------------------------------------
# Full training pipeline
# ---------------------------------------------------------------------------

def build_dataset(
    feed,
    symbols: list[str],
    days: int = 180,
) -> pd.DataFrame:
    """Build multi-token training dataset."""
    from .features import compute_features, symbol_to_sector, SECTOR_INDEX

    all_dfs = []

    for symbol in symbols:
        LOG.info("Building dataset for %s...", symbol)
        try:
            ohlcv = feed.fetch_history(symbol, "5m", limit=int(days * 288 * 1.1))
            btc = feed.fetch_history("BTC/USDT", "5m", limit=int(days * 288 * 1.1))
            eth = feed.fetch_history("ETH/USDT", "5m", limit=int(days * 288 * 1.1))

            ohlcv_df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            btc_df = pd.DataFrame(btc, columns=["timestamp", "open", "high", "low", "close", "volume"])
            eth_df = pd.DataFrame(eth, columns=["timestamp", "open", "high", "low", "close", "volume"])

            # Drop duplicate timestamps (exchange data can have duplicates)
            ohlcv_df = ohlcv_df.drop_duplicates(subset=["timestamp"], keep="first")
            btc_df = btc_df.drop_duplicates(subset=["timestamp"], keep="first")
            eth_df = eth_df.drop_duplicates(subset=["timestamp"], keep="first")

            common_ts = set(ohlcv_df["timestamp"]) & set(btc_df["timestamp"]) & set(eth_df["timestamp"])
            ohlcv_df = ohlcv_df[ohlcv_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
            btc_df = btc_df[btc_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
            eth_df = eth_df[eth_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)

            # Reconstruct DataFrames from fresh arrays — pandas CoW can leave
            # read-only backing arrays after boolean indexing + reset_index
            ohlcv_df = pd.DataFrame({col: ohlcv_df[col].values.copy() for col in ohlcv_df.columns})
            btc_df = pd.DataFrame({col: btc_df[col].values.copy() for col in btc_df.columns})
            eth_df = pd.DataFrame({col: eth_df[col].values.copy() for col in eth_df.columns})

            feat_df = compute_features(ohlcv_df, btc_df, eth_df, symbol=symbol)
            # Reconstruct from fresh arrays after compute_features
            feat_df = pd.DataFrame({col: feat_df[col].values.copy() for col in feat_df.columns})

            atr_14_price = feat_df["_atr_14_price"].values.copy()
            long_labels, short_labels = compute_ev_labels_both_sides(
                closes=feat_df["close"].values,
                highs=feat_df["high"].values,
                lows=feat_df["low"].values,
                atr_14=atr_14_price,
                tp_atr_mult=TP_ATR_MULT,
                sl_atr_mult=SL_ATR_MULT,
                lookahead=LOOKAHEAD,
                atr_lag_offset=ATR_LAG_OFFSET,
            )
            feat_df["long_ev"] = long_labels
            feat_df["short_ev"] = short_labels
            feat_df["symbol"] = symbol

            all_dfs.append(feat_df)
            valid_long = long_labels[~np.isnan(long_labels)]
            valid_short = short_labels[~np.isnan(short_labels)]
            LOG.info("  %s: %d rows, long_EV=%.4f%% short_EV=%.4f%%",
                     symbol, len(feat_df),
                     np.mean(valid_long) if len(valid_long) > 0 else 0,
                     np.mean(valid_short) if len(valid_short) > 0 else 0)

        except Exception as e:
            LOG.error("Failed building dataset for %s: %s\n%s", symbol, e, traceback.format_exc())
            continue

    if not all_dfs:
        raise ValueError("No data built for any symbol")

    combined = pd.concat(all_dfs, ignore_index=True)
    LOG.info("Combined dataset: %d rows from %d symbols", len(combined), len(all_dfs))

    combined = _expand_to_two_sided(combined)

    return combined


def _expand_to_two_sided(df: pd.DataFrame) -> pd.DataFrame:
    """Expand dataset: each bar -> 2 rows (LONG + SHORT)."""
    long_rows = df.copy()
    long_rows["trade_direction"] = 1.0
    long_rows["ev_label"] = long_rows["long_ev"]
    
    short_rows = df.copy()
    short_rows["trade_direction"] = -1.0
    short_rows["ev_label"] = short_rows["short_ev"]
    
    result = pd.concat([long_rows, short_rows], ignore_index=True)
    
    n_before = len(result)
    result = result.dropna(subset=["ev_label"])
    n_after = len(result)
    
    LOG.info("Expanded to two-sided: %d -> %d rows (after NaN drop), positive=%.1f%%",
             n_before, n_after, (result["ev_label"] > 0).mean() * 100)
    
    return result


def train_with_purged_cv(
    dataset: pd.DataFrame,
    n_splits: int = 5,
    params: Optional[dict] = None,
) -> tuple[lgb.Booster, dict]:
    """Train model with Purged K-Fold CV for model selection."""
    cv = PurgedKFold(n_splits=n_splits, lookahead=LOOKAHEAD, embargo=3)

    cv_scores = []
    for fold_idx, (train_idx, test_idx) in enumerate(cv.split(dataset)):
        train_fold = dataset.iloc[train_idx]
        test_fold = dataset.iloc[test_idx]

        train_clean = train_fold.dropna(subset=["ev_label"])
        test_clean = test_fold.dropna(subset=["ev_label"])

        if len(train_clean) < 500 or len(test_clean) < 50:
            continue

        try:
            bst, metrics = train_model(train_clean, val_df=test_clean, params=params)

            X_test = test_clean[FEATURE_NAMES].values.astype(np.float32)
            y_test = test_clean["ev_label"].values.astype(np.float32)
            pred_test = bst.predict(X_test)

            pnl = np.sign(pred_test) * y_test
            sharpe = float(np.mean(pnl) / max(np.std(pnl), 1e-10))
            corr = float(np.corrcoef(y_test, pred_test)[0, 1])
            dir_acc = float((np.sign(pred_test) == np.sign(y_test)).mean())

            cv_scores.append({
                "fold": fold_idx,
                "sharpe": sharpe,
                "corr": corr,
                "dir_acc": dir_acc,
                "n_test": len(test_clean),
            })

            LOG.info("CV Fold %d: sharpe=%.3f corr=%.4f dir_acc=%.3f",
                     fold_idx, sharpe, corr, dir_acc)

        except Exception as e:
            LOG.warning("CV Fold %d failed: %s", fold_idx, e)

    if cv_scores:
        sharpe_mean = np.mean([s["sharpe"] for s in cv_scores])
        sharpe_std = np.std([s["sharpe"] for s in cv_scores])
        corr_mean = np.mean([s["corr"] for s in cv_scores])

        cv_summary = {
            "n_folds": len(cv_scores),
            "sharpe_mean": sharpe_mean,
            "sharpe_std": sharpe_std,
            "corr_mean": corr_mean,
            "per_fold": cv_scores,
        }

        LOG.info("Purged CV: Sharpe=%.3f+-%.3f Corr=%.4f (%d folds)",
                 sharpe_mean, sharpe_std, corr_mean, len(cv_scores))
    else:
        cv_summary = {"n_folds": 0}

    full_clean = dataset.dropna(subset=["ev_label"])
    LOG.info("Retraining on full dataset: %d rows", len(full_clean))
    final_model, final_metrics = train_model(full_clean, params=params)
    final_metrics["cv"] = cv_summary

    return final_model, final_metrics
