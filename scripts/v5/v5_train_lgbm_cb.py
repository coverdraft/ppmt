"""Lightweight v5 LightGBM trainer (Coinbase features).

Same logic as v5_train_lgbm.py but:
- Loads only 5m + 15m features (no 1m)
- Uses LightGBM with smaller params (num_leaves=15, n_rounds=200)
- Saves model + predictions + a JSON metrics report
- Runs in <60s
"""
from __future__ import annotations
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))
import lightgbm as lgb
from ppmt.data.storage import PPMTStorage

LOG = logging.getLogger("v5_train_cb")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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

TRAIN_REGIMES = ["BULL_2024", "RANGE_2023", "BEAR_2022"]
VALID_REGIMES = ["RANGE_2025"]
TEST_REGIMES  = ["RECENT_2026"]

MODEL_OUT = Path("/home/z/my-project/download/v5_lgbm_model_cb.txt")
MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
PREDS_OUT = Path("/home/z/my-project/download/v5_test_predictions_cb.csv")
METRICS_OUT = Path("/home/z/my-project/download/v5_train_metrics_cb.json")


def load_dataset(storage, regimes):
    conn = storage._ensure_conn()
    placeholders = ",".join("?" * len(regimes))
    sql = f"""
        SELECT symbol, timeframe, ts, historical_regime, runtime_regime, asset_class,
               features_json, label_pnl, label_hit_tp_first
        FROM feature_observations
        WHERE historical_regime IN ({placeholders})
          AND label_hit_tp_first IS NOT NULL
        ORDER BY ts ASC
    """
    rows = conn.execute(sql, regimes).fetchall()
    if not rows:
        return pd.DataFrame()
    cols = ["symbol", "timeframe", "ts", "historical_regime", "runtime_regime",
            "asset_class", "features_json", "label_pnl", "label_hit_tp_first"]
    df = pd.DataFrame(rows, columns=cols)
    feats = pd.json_normalize(df["features_json"].apply(json.loads))
    for f in FEATURE_NAMES:
        if f not in feats.columns:
            feats[f] = 0.0
    df = pd.concat([df.drop(columns=["features_json"]).reset_index(drop=True),
                    feats[FEATURE_NAMES].reset_index(drop=True)], axis=1)
    return df


def main():
    storage = PPMTStorage()
    LOG.info("Loading train (%s)...", TRAIN_REGIMES)
    train_df = load_dataset(storage, TRAIN_REGIMES)
    LOG.info("  Train rows: %d", len(train_df))
    LOG.info("Loading valid (%s)...", VALID_REGIMES)
    valid_df = load_dataset(storage, VALID_REGIMES)
    LOG.info("  Valid rows: %d", len(valid_df))
    LOG.info("Loading test (%s)...", TEST_REGIMES)
    test_df = load_dataset(storage, TEST_REGIMES)
    LOG.info("  Test rows: %d", len(test_df))

    feature_cols = FEATURE_NAMES  # use base features only

    X_train = train_df[feature_cols].values
    y_train = train_df["label_hit_tp_first"].astype(int).values
    X_valid = valid_df[feature_cols].values
    y_valid = valid_df["label_hit_tp_first"].astype(int).values
    X_test = test_df[feature_cols].values
    y_test = test_df["label_hit_tp_first"].astype(int).values

    LOG.info("Train: X=%s y_mean=%.3f", X_train.shape, y_train.mean())
    LOG.info("Valid: X=%s y_mean=%.3f", X_valid.shape, y_valid.mean())
    LOG.info("Test:  X=%s y_mean=%.3f", X_test.shape, y_test.mean())

    train_set = lgb.Dataset(X_train, label=y_train, feature_name=feature_cols)
    valid_set = lgb.Dataset(X_valid, label=y_valid, feature_name=feature_cols, reference=train_set)

    params = {
        "objective": "binary",
        "metric": ["binary_logloss", "auc"],
        "boosting": "gbdt",
        "num_leaves": 15,
        "learning_rate": 0.1,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 5,
        "min_data_in_leaf": 100,
        "lambda_l1": 0.1,
        "lambda_l2": 0.1,
        "verbose": -1,
        "is_unbalanced": True,
    }

    LOG.info("Training LightGBM...")
    t0 = time.time()
    model = lgb.train(
        params, train_set, num_boost_round=200,
        valid_sets=[train_set, valid_set], valid_names=["train", "valid"],
        callbacks=[lgb.early_stopping(20), lgb.log_evaluation(50)],
    )
    elapsed = time.time() - t0
    LOG.info("Trained in %.1fs, best_iter=%d", elapsed, model.best_iteration)

    model.save_model(str(MODEL_OUT))
    LOG.info("Saved model to %s", MODEL_OUT)

    # Feature importance
    imp = model.feature_importance(importance_type="gain")
    ranked = sorted(zip(feature_cols, imp), key=lambda x: -x[1])
    LOG.info("Top 10 features by gain:")
    for name, score in ranked[:10]:
        LOG.info("  %-25s %10.1f", name, score)

    # Predictions
    p_train = model.predict(X_train)
    p_valid = model.predict(X_valid)
    p_test = model.predict(X_test)

    # Eval function
    def eval_set(name, df, y, p, threshold):
        pred = (p >= threshold).astype(int)
        n_signals = int(pred.sum())
        if n_signals == 0:
            return {"n_signals": 0, "precision": 0, "recall": 0, "pf": 0, "avg_pnl": 0}
        mask_idx = np.where(pred == 1)[0]
        pnls = df["label_pnl"].iloc[mask_idx].fillna(0).values
        gross_win = pnls[pnls > 0].sum()
        gross_loss = -pnls[pnls < 0].sum()
        pf = float(gross_win / gross_loss) if gross_loss > 0 else float("inf")
        precision = float((pred & y).sum() / n_signals)
        recall = float((pred & y).sum() / max(y.sum(), 1))
        avg_pnl = float(pnls.mean())
        return {
            "n_signals": n_signals,
            "precision": precision,
            "recall": recall,
            "pf": pf,
            "avg_pnl": avg_pnl,
        }

    metrics = {}
    for name, df, y, p in [("train", train_df, y_train, p_train),
                            ("valid", valid_df, y_valid, p_valid),
                            ("test",  test_df,  y_test,  p_test)]:
        metrics[name] = {}
        for t in [0.5, 0.6, 0.7, 0.8]:
            metrics[name][f"thresh_{t}"] = eval_set(name, df, y, p, t)
        LOG.info("--- %s ---", name.upper())
        for t in [0.5, 0.6, 0.7, 0.8]:
            m = metrics[name][f"thresh_{t}"]
            LOG.info("  thresh=%.1f  signals=%d  precision=%.3f  recall=%.3f  pf=%.2f  avg_pnl=%.3f%%",
                     t, m["n_signals"], m["precision"], m["recall"], m["pf"], m["avg_pnl"])

    with open(METRICS_OUT, "w") as f:
        json.dump({
            "model": "v5_lgbm_coinbase",
            "features": feature_cols,
            "train_rows": len(X_train),
            "valid_rows": len(X_valid),
            "test_rows": len(X_test),
            "train_win_rate": float(y_train.mean()),
            "valid_win_rate": float(y_valid.mean()),
            "test_win_rate": float(y_test.mean()),
            "best_iteration": int(model.best_iteration) if model.best_iteration else 200,
            "train_time_sec": elapsed,
            "top_features": [{"name": n, "gain": float(s)} for n, s in ranked[:15]],
            "metrics": metrics,
            "data_source": "coinbase_ohlcv_ext_cb",
            "train_regimes": TRAIN_REGIMES,
            "valid_regimes": VALID_REGIMES,
            "test_regimes": TEST_REGIMES,
        }, f, indent=2, default=str)
    LOG.info("Metrics saved to %s", METRICS_OUT)

    # Save test predictions
    out = test_df[["symbol", "timeframe", "ts", "historical_regime",
                   "label_hit_tp_first", "label_pnl"]].copy()
    out["pred_proba"] = p_test
    out.to_csv(PREDS_OUT, index=False)
    LOG.info("Test predictions saved to %s", PREDS_OUT)


if __name__ == "__main__":
    main()
