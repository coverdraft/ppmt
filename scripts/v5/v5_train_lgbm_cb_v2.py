#!/usr/bin/env python3
"""
v5_train_lgbm_cb.py — Train LightGBM on Coinbase features.

Walk-forward split:
  Train:  BULL_2024 + BEAR_2022 (no RANGE_2023 — was lost in env reset)
  Valid:  RANGE_2025
  Test:   RECENT_2026 (out-of-sample)

Saves model + metrics + test predictions.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False

LOG = logging.getLogger("v5_train_cb")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DB = "/home/z/my-project/data/ppmt.db"

FEATURE_NAMES = [
    "body_pct", "upper_wick", "lower_wick", "body_abs", "close_pos", "range_pct",
    "ret_1", "ret_3", "ret_5", "ret_10", "log_ret_1",
    "atr_pct", "vol_std_10", "rsi_14",
    "ema_9_20_cross", "ema_20_50_cross", "ema_9_slope", "ema_20_slope", "ema_50_slope",
    "price_vs_ema20", "price_vs_ema50", "vol_ratio", "vol_z",
    "last_3_body_sum", "last_3_range_sum",
    "bullish_engulf_2", "hammer_like", "shooting_star",
    "breakout_up", "breakout_down", "dist_to_high_20", "dist_to_low_20",
    "trend_50", "vol_regime", "trending",
    "hour_sin", "hour_cos", "dow",
]

TRAIN_REGIMES = ["BULL_2024", "BEAR_2022"]  # RANGE_2023 missing in current DB
VALID_REGIMES = ["RANGE_2025"]
TEST_REGIMES  = ["RECENT_2026"]

MODEL_OUT = Path("/home/z/my-project/download/v5_lgbm_model_cb_v2.txt")
METRICS_OUT = Path("/home/z/my-project/download/v5_train_metrics_cb_v2.json")
PRED_OUT = Path("/home/z/my-project/download/v5_test_predictions_cb_v2.csv")


def load_dataset(regimes: list[str], labeled_only: bool = True) -> pd.DataFrame:
    import sqlite3
    conn = sqlite3.connect(DB, timeout=30)
    placeholders = ",".join("?" * len(regimes))
    sql = f"""
        SELECT symbol, timeframe, ts, pattern_hash,
               historical_regime, runtime_regime, asset_class,
               features_json, prior_win_rate, prior_expected_move, prior_count,
               label_win, label_pnl, label_max_fav, label_max_adv, label_hit_tp_first
        FROM feature_observations_cb
        WHERE historical_regime IN ({placeholders})
    """
    if labeled_only:
        sql += " AND label_hit_tp_first IS NOT NULL"
    sql += " ORDER BY ts ASC"

    rows = conn.execute(sql, regimes).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()

    cols = ["symbol", "timeframe", "ts", "pattern_hash",
            "historical_regime", "runtime_regime", "asset_class",
            "features_json", "prior_win_rate", "prior_expected_move", "prior_count",
            "label_win", "label_pnl", "label_max_fav", "label_max_adv", "label_hit_tp_first"]
    df = pd.DataFrame(rows, columns=cols)

    # Expand features_json
    features_expanded = pd.json_normalize(df["features_json"].apply(json.loads))
    for f in FEATURE_NAMES:
        if f not in features_expanded.columns:
            features_expanded[f] = 0.0
    df = pd.concat([df.drop(columns=["features_json"]).reset_index(drop=True),
                    features_expanded[FEATURE_NAMES].reset_index(drop=True)], axis=1)
    return df


def compute_edge_label(df: pd.DataFrame) -> pd.Series:
    hour = pd.to_datetime(df["ts"], unit="s", utc=True).dt.hour
    asia_hours = hour.isin([0, 1, 2, 18, 19, 20, 21, 22, 23])
    is_altcoin = ~df["asset_class"].isin(["blue_chip"])
    is_scalp_tf = df["timeframe"].isin(["1m", "5m", "15m"])
    strong = is_altcoin & is_scalp_tf & asia_hours
    score = is_altcoin.astype(int) + is_scalp_tf.astype(int) + asia_hours.astype(int)
    marginal = (score == 2) & ~strong
    label = pd.Series("no_edge", index=df.index)
    label[strong] = "strong_edge"
    label[marginal] = "marginal_edge"
    return label


def main():
    import sqlite3
    conn = sqlite3.connect(DB, timeout=30)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM feature_observations_cb")
    total = cur.fetchone()[0]
    LOG.info("feature_observations_cb total: %d", total)
    cur.execute("SELECT historical_regime, COUNT(*) FROM feature_observations_cb GROUP BY historical_regime")
    for r, n in cur.fetchall():
        LOG.info("  %s: %d", r, n)
    conn.close()

    LOG.info("Loading training set (%s)...", TRAIN_REGIMES)
    train_df = load_dataset(TRAIN_REGIMES, labeled_only=True)
    LOG.info("  Train rows: %d", len(train_df))

    LOG.info("Loading validation set (%s)...", VALID_REGIMES)
    valid_df = load_dataset(VALID_REGIMES, labeled_only=True)
    LOG.info("  Valid rows: %d", len(valid_df))

    LOG.info("Loading test set (%s)...", TEST_REGIMES)
    test_df = load_dataset(TEST_REGIMES, labeled_only=True)
    LOG.info("  Test rows: %d", len(test_df))

    if len(train_df) < 100:
        LOG.error("Not enough training rows. Need >=100, got %d", len(train_df))
        return

    # Add edge_label features
    for df in [train_df, valid_df, test_df]:
        df["edge_label"] = compute_edge_label(df)
        df["edge_strong"] = (df["edge_label"] == "strong_edge").astype(int)
        df["edge_marginal"] = (df["edge_label"] == "marginal_edge").astype(int)

    feature_cols = FEATURE_NAMES + ["edge_strong", "edge_marginal"]

    y_train = train_df["label_hit_tp_first"].astype(int).values
    y_valid = valid_df["label_hit_tp_first"].astype(int).values if len(valid_df) > 0 else None
    y_test  = test_df["label_hit_tp_first"].astype(int).values if len(test_df) > 0 else None

    X_train = train_df[feature_cols].values
    X_valid = valid_df[feature_cols].values if len(valid_df) > 0 else None
    X_test  = test_df[feature_cols].values if len(test_df) > 0 else None

    LOG.info("Train: X=%s, y mean=%.3f", X_train.shape, y_train.mean())
    if y_valid is not None:
        LOG.info("Valid: X=%s, y mean=%.3f", X_valid.shape, y_valid.mean())
    if y_test is not None:
        LOG.info("Test:  X=%s, y mean=%.3f", X_test.shape, y_test.mean())

    if not HAS_LGB:
        LOG.error("lightgbm not installed")
        return

    LOG.info("Training LightGBM...")
    train_set = lgb.Dataset(X_train, label=y_train, feature_name=feature_cols)
    valid_sets = [train_set]
    valid_names = ["train"]
    if y_valid is not None and len(y_valid) > 0:
        valid_set = lgb.Dataset(X_valid, label=y_valid, feature_name=feature_cols, reference=train_set)
        valid_sets.append(valid_set)
        valid_names.append("valid")

    params = {
        "objective": "binary",
        "metric": ["binary_logloss", "auc"],
        "boosting": "gbdt",
        "num_leaves": 15,
        "learning_rate": 0.1,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 5,
        "min_data_in_leaf": 50,
        "lambda_l1": 0.1,
        "lambda_l2": 0.1,
        "verbose": -1,
        "is_unbalanced": True,
    }
    t0 = time.time()
    model = lgb.train(
        params,
        train_set,
        num_boost_round=200,
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=[lgb.early_stopping(30), lgb.log_evaluation(50)],
    )
    train_time = time.time() - t0
    LOG.info("Training took %.1fs, best iter=%d", train_time, model.best_iteration)
    model.save_model(str(MODEL_OUT))
    LOG.info("Model saved to %s", MODEL_OUT)

    # Feature importance
    imp = model.feature_importance(importance_type="gain")
    ranked = sorted(zip(feature_cols, imp), key=lambda x: -x[1])
    LOG.info("Top 10 features by gain:")
    for name, score in ranked[:10]:
        LOG.info("  %-25s %10.1f", name, score)

    p_train = model.predict(X_train)
    p_valid = model.predict(X_valid) if X_valid is not None else None
    p_test  = model.predict(X_test) if X_test is not None else None

    # Evaluate at multiple thresholds
    def eval_set(name, y, p, df_lookup, threshold):
        if y is None or p is None or len(y) == 0:
            return None
        pred = (p >= threshold).astype(int)
        n_signals = int(pred.sum())
        if n_signals == 0:
            return None
        precision = float((pred & y).sum() / pred.sum())
        recall = float((pred & y).sum() / max(y.sum(), 1))
        mask_idx = np.where(pred == 1)[0]
        pnls = df_lookup["label_pnl"].iloc[mask_idx].fillna(0).values
        gross_win = pnls[pnls > 0].sum()
        gross_loss = -pnls[pnls < 0].sum()
        pf = float(gross_win / gross_loss) if gross_loss > 0 else float("inf")
        avg_pnl = float(pnls.mean())
        return {
            "n_signals": n_signals,
            "precision": precision,
            "recall": recall,
            "pf": pf,
            "avg_pnl": avg_pnl,
        }

    metrics = {
        "model": "v5_lgbm_coinbase_v2",
        "features": feature_cols,
        "train_rows": len(X_train),
        "valid_rows": len(X_valid) if X_valid is not None else 0,
        "test_rows": len(X_test) if X_test is not None else 0,
        "train_win_rate": float(y_train.mean()),
        "valid_win_rate": float(y_valid.mean()) if y_valid is not None else 0,
        "test_win_rate": float(y_test.mean()) if y_test is not None else 0,
        "best_iteration": int(model.best_iteration) if model.best_iteration else 0,
        "train_time_sec": round(train_time, 2),
        "top_features": [{"name": n, "gain": float(s)} for n, s in ranked[:10]],
        "thresholds": {},
        "data_source": "coinbase_ohlcv_ext_cb",
        "train_regimes": TRAIN_REGIMES,
        "valid_regimes": VALID_REGIMES,
        "test_regimes": TEST_REGIMES,
    }

    for t in [0.5, 0.6, 0.7, 0.8]:
        metrics["thresholds"][f"thresh_{t}"] = {
            "train": eval_set("train", y_train, p_train, train_df, t),
            "valid": eval_set("valid", y_valid, p_valid, valid_df, t) if y_valid is not None else None,
            "test":  eval_set("test",  y_test,  p_test,  test_df,  t)  if y_test  is not None else None,
        }
        LOG.info("=== threshold %.2f ===", t)
        LOG.info("  test:  %s", metrics["thresholds"][f"thresh_{t}"]["test"])

    METRICS_OUT.write_text(json.dumps(metrics, indent=2))
    LOG.info("Metrics saved to %s", METRICS_OUT)

    # Save test predictions
    if test_df is not None and len(test_df) > 0:
        out = test_df[["symbol", "timeframe", "ts", "historical_regime",
                       "label_hit_tp_first", "label_pnl"]].copy()
        out["pred_proba"] = p_test
        out.to_csv(PRED_OUT, index=False)
        LOG.info("Test predictions saved to %s", PRED_OUT)


if __name__ == "__main__":
    main()
