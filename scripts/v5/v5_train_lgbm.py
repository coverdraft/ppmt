"""
Track C2: V5 LightGBM training on feature_observations.

Loads feature_observations from the DB, splits by historical_regime
(walk-forward: train on BULL_2024 + RANGE_2023 + BEAR_2022, validate
on RANGE_2025, test on RECENT_2026), trains a LightGBM binary
classifier on label_hit_tp_first (TP=+0.6% before SL=-0.4% in 6 bars).

Saves the model to /home/z/my-project/download/v5_lgbm_model.txt
and registers metadata in ml_models table.

Usage:
    python /home/z/my-project/scripts/v5_train_lgbm.py
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

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False
    print("WARNING: lightgbm not installed. Install with: pip install lightgbm")
    print("Falling back to sklearn GradientBoostingClassifier.")
    from sklearn.ensemble import GradientBoostingClassifier

from ppmt.data.storage import PPMTStorage  # noqa: E402

LOG = logging.getLogger("v5_train")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# Feature list (must match v5_extract_features.py)
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

# Walk-forward split
TRAIN_REGIMES = ["BULL_2024", "RANGE_2023", "BEAR_2022"]
VALID_REGIMES = ["RANGE_2025"]
TEST_REGIMES  = ["RECENT_2026"]

MODEL_OUT = Path("/home/z/my-project/download/v5_lgbm_model.txt")
MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)


def load_dataset(storage: PPMTStorage, regimes: list[str], labeled_only: bool = True) -> pd.DataFrame:
    """Load feature observations for the given regimes into a DataFrame."""
    conn = storage._ensure_conn()
    placeholders = ",".join("?" * len(regimes))
    sql = f"""
        SELECT symbol, timeframe, ts, pattern_hash,
               historical_regime, runtime_regime, asset_class,
               features_json, prior_win_rate, prior_expected_move, prior_count,
               label_win, label_pnl, label_max_fav, label_max_adv, label_hit_tp_first
        FROM feature_observations
        WHERE historical_regime IN ({placeholders})
    """
    if labeled_only:
        sql += " AND label_hit_tp_first IS NOT NULL"
    sql += " ORDER BY ts ASC"

    rows = conn.execute(sql, regimes).fetchall()
    if not rows:
        return pd.DataFrame()

    cols = ["symbol", "timeframe", "ts", "pattern_hash",
            "historical_regime", "runtime_regime", "asset_class",
            "features_json", "prior_win_rate", "prior_expected_move", "prior_count",
            "label_win", "label_pnl", "label_max_fav", "label_max_adv", "label_hit_tp_first"]
    df = pd.DataFrame(rows, columns=cols)

    # Expand features_json into columns
    features_expanded = pd.json_normalize(df["features_json"].apply(json.loads))
    for f in FEATURE_NAMES:
        if f not in features_expanded.columns:
            features_expanded[f] = 0.0
    df = pd.concat([df.drop(columns=["features_json"]).reset_index(drop=True),
                    features_expanded[FEATURE_NAMES].reset_index(drop=True)], axis=1)
    return df


def compute_edge_label(df: pd.DataFrame) -> pd.Series:
    """Compute the V5 edge_label: matches trader's profitable profile.

    Profile: LONG + scalp<15min TF (5m/15m/1m) + Asia hours (UTC 18-23, 0-2)
             + altcoin (not blue_chip) + 5-10x leverage (we tag any).

    Returns: 'strong_edge' | 'marginal_edge' | 'no_edge'
    """
    # hour from ts (epoch seconds)
    hour = pd.to_datetime(df["ts"], unit="s", utc=True).dt.hour
    asia_hours = hour.isin([0, 1, 2, 18, 19, 20, 21, 22, 23])

    is_altcoin = ~df["asset_class"].isin(["blue_chip"])
    is_scalp_tf = df["timeframe"].isin(["1m", "5m", "15m"])

    # strong_edge: all 3 conditions
    strong = is_altcoin & is_scalp_tf & asia_hours
    # marginal: any 2 of 3
    score = is_altcoin.astype(int) + is_scalp_tf.astype(int) + asia_hours.astype(int)
    marginal = (score == 2) & ~strong
    no_edge = ~strong & ~marginal

    label = pd.Series("no_edge", index=df.index)
    label[strong] = "strong_edge"
    label[marginal] = "marginal_edge"
    return label


def main() -> None:
    storage = PPMTStorage()
    counts = storage.count_feature_observations()
    LOG.info("feature_observations counts: %s", counts)

    if counts.get("total", 0) == 0:
        LOG.error("No feature observations in DB. Run v5_extract_features.py first.")
        return

    LOG.info("Loading training set (%s)...", TRAIN_REGIMES)
    train_df = load_dataset(storage, TRAIN_REGIMES, labeled_only=True)
    LOG.info("  Train rows: %d", len(train_df))

    LOG.info("Loading validation set (%s)...", VALID_REGIMES)
    valid_df = load_dataset(storage, VALID_REGIMES, labeled_only=True)
    LOG.info("  Valid rows: %d", len(valid_df))

    LOG.info("Loading test set (%s)...", TEST_REGIMES)
    test_df = load_dataset(storage, TEST_REGIMES, labeled_only=True)
    LOG.info("  Test rows: %d", len(test_df))

    if len(train_df) < 100:
        LOG.error("Not enough training rows. Need >=100, got %d", len(train_df))
        return

    # Add edge_label as an additional feature
    for df in [train_df, valid_df, test_df]:
        df["edge_label"] = compute_edge_label(df)
        df["edge_strong"] = (df["edge_label"] == "strong_edge").astype(int)
        df["edge_marginal"] = (df["edge_label"] == "marginal_edge").astype(int)

    feature_cols = FEATURE_NAMES + ["edge_strong", "edge_marginal"]

    # Target: label_hit_tp_first (1 = TP hit first, 0 = SL hit first)
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

    if HAS_LGB:
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
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.85,
            "bagging_fraction": 0.85,
            "bagging_freq": 5,
            "min_data_in_leaf": 50,
            "lambda_l1": 0.1,
            "lambda_l2": 0.1,
            "verbose": -1,
            "is_unbalanced": True,
        }
        model = lgb.train(
            params,
            train_set,
            num_boost_round=500,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=[lgb.early_stopping(30), lgb.log_evaluation(50)],
        )
        model.save_model(str(MODEL_OUT))
        LOG.info("Model saved to %s", MODEL_OUT)

        # Feature importance
        imp = model.feature_importance(importance_type="gain")
        ranked = sorted(zip(feature_cols, imp), key=lambda x: -x[1])
        LOG.info("Top 10 features by gain:")
        for name, score in ranked[:10]:
            LOG.info("  %-25s %10.1f", name, score)

        # Predictions
        p_train = model.predict(X_train)
        p_valid = model.predict(X_valid) if X_valid is not None else None
        p_test  = model.predict(X_test) if X_test is not None else None
    else:
        LOG.info("Training GradientBoostingClassifier (fallback)...")
        model = GradientBoostingClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05, random_state=42
        )
        model.fit(X_train, y_train)
        # Save via joblib
        import joblib
        MODEL_OUT_JOBLIB = MODEL_OUT.with_suffix(".joblib")
        joblib.dump(model, MODEL_OUT_JOBLIB)
        LOG.info("Model saved to %s", MODEL_OUT_JOBLIB)

        p_train = model.predict_proba(X_train)[:, 1]
        p_valid = model.predict_proba(X_valid)[:, 1] if X_valid is not None else None
        p_test  = model.predict_proba(X_test)[:, 1] if X_test is not None else None

    # Evaluate
    def eval_set(name, y, p, threshold=0.5):
        if y is None or p is None or len(y) == 0:
            return
        pred = (p >= threshold).astype(int)
        win_rate = (pred & y).sum() / max(pred.sum(), 1)
        precision = win_rate
        recall = (pred & y).sum() / max(y.sum(), 1)
        n_signals = pred.sum()
        # PF: sum of wins / sum of losses on label_pnl (only for signal rows)
        # Use the threshold-filtered subset
        mask_idx = np.where(pred == 1)[0]
        if len(mask_idx) > 0:
            # Need pnl per row — pull from df
            df_lookup = {"train": train_df, "valid": valid_df, "test": test_df}.get(name)
            if df_lookup is not None and len(df_lookup) == len(y):
                pnls = df_lookup["label_pnl"].iloc[mask_idx].fillna(0).values
                gross_win = pnls[pnls > 0].sum()
                gross_loss = -pnls[pnls < 0].sum()
                pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
                avg_pnl = pnls.mean()
            else:
                pf = float("nan")
                avg_pnl = float("nan")
        else:
            pf = float("nan")
            avg_pnl = float("nan")
        LOG.info("=== %s @ thresh=%.2f ===", name.upper(), threshold)
        LOG.info("  Signals: %d / %d (%.1f%%)", n_signals, len(y), 100 * n_signals / len(y))
        LOG.info("  Precision (TP-rate when signaled): %.3f", precision)
        LOG.info("  Recall:    %.3f", recall)
        LOG.info("  Profit factor: %.3f", pf)
        LOG.info("  Avg PnL per signal: %.3f%%", avg_pnl)

    eval_set("train", y_train, p_train, 0.5)
    eval_set("valid", y_valid, p_valid, 0.5)
    eval_set("test",  y_test,  p_test,  0.5)

    # Try higher thresholds for quality
    for t in [0.6, 0.7, 0.8]:
        eval_set("test", y_test, p_test, t)

    # Register in ml_models
    storage.save_ml_model(
        model_name="v5_lgbm_ppmt",
        version="v5.0.0",
        model_path=str(MODEL_OUT),
        features=feature_cols,
        train_rows=len(X_train),
        train_win_rate=float(y_train.mean()),
        train_pf=float("nan"),
        valid_win_rate=float(y_valid.mean()) if y_valid is not None else 0.0,
        valid_pf=float("nan"),
        hyperparams={"num_leaves": 31, "lr": 0.05, "min_data": 50},
    )
    LOG.info("Model registered in ml_models table")

    # Save test predictions for inspection
    if test_df is not None and len(test_df) > 0:
        out = test_df[["symbol", "timeframe", "ts", "historical_regime",
                       "label_hit_tp_first", "label_pnl"]].copy()
        out["pred_proba"] = p_test
        out.to_csv("/home/z/my-project/download/v5_test_predictions.csv", index=False)
        LOG.info("Test predictions saved to /home/z/my-project/download/v5_test_predictions.csv")


if __name__ == "__main__":
    main()
