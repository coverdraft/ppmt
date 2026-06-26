"""
train.py — V10: Dual model training (Binary Classifier + MFE Regressor)

Model A: Binary classifier (same as V9 but with enhanced features)
  - Positive: winning entries
  - Negative: losing entries (3x weight) + random bars
  - Predicts: "Is this a winning entry?"

Model B: MFE regressor
  - Trained ONLY on actual trade entries (winners + losers)
  - Predicts: "How much potential does this entry have?" (MFE/MAE ratio)
  - Key insight: An entry that hits +3% then reverses has HIGH potential
    but was mismanaged. The entry itself was good — the exit was bad.

The regressor captures what the binary classifier misses:
  - Binary says: "this is a loser" → model says DON'T enter
  - Regressor says: "this entry had 2:1 MFE/MAE but lost" → the entry
    was GOOD, the trader's exit was bad → with mechanical TP, this would win
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

pd.options.mode.copy_on_write = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LOG = logging.getLogger("v10_train")

DATA_DIR = PROJECT_ROOT / "data" / "v10"
MODEL_DIR = DATA_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

FEATURE_COLS_PATH = DATA_DIR / "feature_columns.json"
REG_TARGETS_PATH = DATA_DIR / "regression_targets.json"


def train_binary_classifier(dataset_path: Path, params: dict = None) -> tuple:
    """Train LightGBM binary classifier: winning entries vs rest."""
    LOG.info("=" * 60)
    LOG.info("MODEL A: Binary Classifier")
    LOG.info("=" * 60)

    df = pd.read_parquet(dataset_path)

    with open(FEATURE_COLS_PATH) as f:
        feature_cols = json.load(f)

    feature_cols = [c for c in feature_cols if c in df.columns]
    LOG.info("Features: %d", len(feature_cols))

    df = df.dropna(subset=["label"])
    for col in feature_cols:
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)

    # Binary labels: 1=winner, 0=loser or random
    df["label_binary"] = (df["label"] == 1.0).astype(float)

    n_winners = int(df["label_binary"].sum())
    n_losers = int((df["label"] == -1.0).sum())
    n_random = int((df["label"] == 0.0).sum())
    n_neg = n_losers + n_random

    LOG.info("Dataset: %d rows (winners=%d / losers=%d / random=%d / total_neg=%d)",
             len(df), n_winners, n_losers, n_random, n_neg)

    if len(df) < 50 or n_winners < 10 or n_neg < 10:
        LOG.error("Dataset too small!")
        sys.exit(1)

    # Sample weights: hard negatives get 3x weight
    sample_weight = np.ones(len(df))
    sample_weight[df["label"] == -1.0] = 3.0
    df["_sample_weight"] = sample_weight

    # Time-based split
    df = df.sort_values("timestamp").reset_index(drop=True)
    split = int(len(df) * 0.8)

    train = df.iloc[:split]
    test = df.iloc[split:]

    X_train = train[feature_cols].values.astype(np.float32)
    y_train = train["label_binary"].values.astype(np.float32)
    w_train = train["_sample_weight"].values.astype(np.float32)
    X_test = test[feature_cols].values.astype(np.float32)
    y_test = test["label_binary"].values.astype(np.float32)
    w_test = test["_sample_weight"].values.astype(np.float32)

    n_pos_tr = int((y_train == 1).sum())
    n_neg_tr = int((y_train == 0).sum())
    scale_pos = n_neg_tr / max(n_pos_tr, 1)

    p = {
        "objective": "binary",
        "metric": ["binary_logloss", "auc"],
        "num_leaves": 63,
        "learning_rate": 0.03,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.7,
        "bagging_freq": 3,
        "min_data_in_leaf": 30,
        "lambda_l1": 1.0,
        "lambda_l2": 5.0,
        "scale_pos_weight": scale_pos,
        "verbosity": -1,
        "seed": 42,
    }
    if params:
        p.update(params)

    d_train = lgb.Dataset(X_train, label=y_train, weight=w_train,
                          feature_name=feature_cols, free_raw_data=False)
    d_test = lgb.Dataset(X_test, label=y_test, weight=w_test,
                         feature_name=feature_cols, free_raw_data=False)

    t0 = time.time()
    bst = lgb.train(
        p, d_train,
        num_boost_round=500,
        valid_sets=[d_train, d_test],
        valid_names=["train", "test"],
        callbacks=[
            lgb.early_stopping(50, verbose=False),
            lgb.log_evaluation(50),
        ],
    )
    elapsed = time.time() - t0

    # Evaluate
    pred_test = bst.predict(X_test)
    pred_train = bst.predict(X_train)

    try:
        from sklearn.metrics import roc_auc_score, precision_recall_curve

        auc_test = roc_auc_score(y_test, pred_test)
        auc_train = roc_auc_score(y_train, pred_train)

        # AUC winners vs losers ONLY
        test_losers_mask = test["label"] == -1.0
        test_wl = test[test_losers_mask | (test["label"] == 1.0)]
        if len(test_wl) > 20:
            y_wl = (test_wl["label"] == 1.0).astype(float)
            p_wl = bst.predict(test_wl[feature_cols].values.astype(np.float32))
            auc_win_vs_lose = roc_auc_score(y_wl, p_wl)
        else:
            auc_win_vs_lose = 0.0

        # Optimal threshold
        precision, recall, thresholds = precision_recall_curve(y_test, pred_test)
        f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-10)
        best_idx = np.argmax(f1)
        best_threshold = float(thresholds[best_idx]) if best_idx < len(thresholds) else 0.5

        LOG.info("AUC Train: %.4f  Test: %.4f  Win-vs-Lose: %.4f",
                 auc_train, auc_test, auc_win_vs_lose)
        LOG.info("Best threshold: %.3f (F1=%.3f)", best_threshold, f1[best_idx])

    except ImportError:
        auc_test = auc_train = auc_win_vs_lose = 0.0
        best_threshold = 0.5

    # Feature importance
    imp = bst.feature_importance(importance_type="gain")
    imp_df = pd.DataFrame({"feature": feature_cols, "importance": imp})
    imp_df = imp_df.sort_values("importance", ascending=False)

    LOG.info("Top 10 features:")
    for _, row in imp_df.head(10).iterrows():
        LOG.info("  %s: %.1f", row["feature"], row["importance"])

    # Save model
    model_path = MODEL_DIR / "v10_binary_classifier.lgb"
    bst.save_model(str(model_path))

    meta = {
        "model_type": "binary_classifier",
        "feature_cols": feature_cols,
        "auc_train": float(auc_train),
        "auc_test": float(auc_test),
        "auc_win_vs_lose": float(auc_win_vs_lose),
        "best_threshold": best_threshold,
        "n_train": len(train), "n_test": len(test),
        "n_winners_total": n_winners,
        "n_losers_total": n_losers,
        "training_time_s": elapsed,
        "params": p,
        "top_features": imp_df.head(20).to_dict(orient="records"),
    }
    meta_path = MODEL_DIR / "v10_binary_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)

    print(f"\n{'='*70}")
    print(f"MODEL A: Binary Classifier")
    print(f"{'='*70}")
    print(f"  AUC Train:  {auc_train:.4f}")
    print(f"  AUC Test:   {auc_test:.4f}")
    print(f"  AUC Win vs Lose: {auc_win_vs_lose:.4f}  <- KEY METRIC")
    print(f"  Best Threshold: {best_threshold:.3f}")
    print(f"  Top 5 Features:")
    for _, row in imp_df.head(5).iterrows():
        print(f"    {row['feature']:<25} {row['importance']:.1f}")
    print(f"  Model: {model_path}")
    print(f"{'='*70}")

    return bst, meta


def train_mfe_regressor(dataset_path: Path, params: dict = None) -> tuple:
    """Train LightGBM regressor to predict MFE/MAE ratio.

    This model is trained ONLY on actual trade entries (winners + losers).
    It learns: "given market features at entry, how much potential does
    this trade have?" — measured by MFE/MAE ratio.

    High predicted MFE/MAE = good entry (the price DID move in our favor)
    Low predicted MFE/MAE = bad entry (price went against us immediately)
    """
    LOG.info("=" * 60)
    LOG.info("MODEL B: MFE/MAE Regressor")
    LOG.info("=" * 60)

    df = pd.read_parquet(dataset_path)

    with open(FEATURE_COLS_PATH) as f:
        feature_cols = json.load(f)

    feature_cols = [c for c in feature_cols if c in df.columns]

    # Only use actual trade entries (winners + losers), NOT random bars
    df_trades = df[df["label"].isin([1.0, -1.0])].copy()
    LOG.info("Trade entries: %d (winners=%d losers=%d)",
             len(df_trades),
             int((df_trades["label"] == 1.0).sum()),
             int((df_trades["label"] == -1.0).sum()))

    if len(df_trades) < 30:
        LOG.error("Not enough trade entries for regression!")
        return None, None

    # Clean
    for col in feature_cols:
        df_trades[col] = df_trades[col].replace([np.inf, -np.inf], np.nan)

    # Target: mfe_mae_ratio (primary), also predict mfe_pct and mae_pct
    target_col = "mfe_mae_ratio"
    df_trades = df_trades.dropna(subset=[target_col])

    # Clip extreme outliers for stability
    df_trades[target_col] = df_trades[target_col].clip(-10, 100)

    LOG.info("Target (%s): mean=%.2f std=%.2f min=%.2f max=%.2f",
             target_col,
             df_trades[target_col].mean(),
             df_trades[target_col].std(),
             df_trades[target_col].min(),
             df_trades[target_col].max())

    # Sample weights: winners and losers equally important
    # (unlike binary where losers are 3x weight)
    sample_weight = np.ones(len(df_trades))

    # Extra weight for extreme values (both very good and very bad entries)
    target_vals = df_trades[target_col].values
    extreme_mask = (np.abs(target_vals) > np.percentile(np.abs(target_vals), 90))
    sample_weight[extreme_mask] = 2.0

    df_trades["_sample_weight"] = sample_weight

    # Time-based split
    df_trades = df_trades.sort_values("timestamp").reset_index(drop=True)
    split = int(len(df_trades) * 0.8)

    train = df_trades.iloc[:split]
    test = df_trades.iloc[split:]

    X_train = train[feature_cols].values.astype(np.float32)
    y_train = train[target_col].values.astype(np.float32)
    w_train = train["_sample_weight"].values.astype(np.float32)
    X_test = test[feature_cols].values.astype(np.float32)
    y_test = test[target_col].values.astype(np.float32)
    w_test = test["_sample_weight"].values.astype(np.float32)

    p = {
        "objective": "regression",
        "metric": ["rmse", "mae"],
        "num_leaves": 31,  # smaller for regression (less data)
        "learning_rate": 0.05,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.7,
        "bagging_freq": 3,
        "min_data_in_leaf": 10,  # smaller since we have fewer samples
        "lambda_l1": 1.0,
        "lambda_l2": 10.0,
        "verbosity": -1,
        "seed": 42,
    }
    if params:
        p.update(params)

    d_train = lgb.Dataset(X_train, label=y_train, weight=w_train,
                          feature_name=feature_cols, free_raw_data=False)
    d_test = lgb.Dataset(X_test, label=y_test, weight=w_test,
                         feature_name=feature_cols, free_raw_data=False)

    t0 = time.time()
    bst = lgb.train(
        p, d_train,
        num_boost_round=500,
        valid_sets=[d_train, d_test],
        valid_names=["train", "test"],
        callbacks=[
            lgb.early_stopping(50, verbose=False),
            lgb.log_evaluation(50),
        ],
    )
    elapsed = time.time() - t0

    # Evaluate
    pred_test = bst.predict(X_test)
    pred_train = bst.predict(X_train)

    # Correlation between predicted and actual MFE/MAE
    from scipy.stats import spearmanr, pearsonr

    corr_train_p, _ = pearsonr(pred_train, y_train)
    corr_test_p, _ = pearsonr(pred_test, y_test)
    corr_train_s, _ = spearmanr(pred_train, y_train)
    corr_test_s, _ = spearmanr(pred_test, y_test)

    # RMSE
    rmse_train = np.sqrt(np.mean((pred_train - y_train) ** 2))
    rmse_test = np.sqrt(np.mean((pred_test - y_test) ** 2))

    # KEY METRIC: Can the regressor distinguish high-MFE from low-MFE entries?
    # Split test predictions by actual outcome
    test_labels = test["label"].values
    pred_winners = pred_test[test_labels == 1.0]
    pred_losers = pred_test[test_labels == -1.0]

    if len(pred_winners) > 5 and len(pred_losers) > 5:
        mean_pred_win = np.mean(pred_winners)
        mean_pred_lose = np.mean(pred_losers)
        # AUC-style: can we rank winners higher than losers?
        from sklearn.metrics import roc_auc_score
        binary_labels = (test_labels == 1.0).astype(float)
        auc_mfe = roc_auc_score(binary_labels, pred_test)
    else:
        mean_pred_win = mean_pred_lose = 0.0
        auc_mfe = 0.0

    LOG.info("Pearson corr: train=%.4f test=%.4f", corr_train_p, corr_test_p)
    LOG.info("Spearman corr: train=%.4f test=%.4f", corr_train_s, corr_test_s)
    LOG.info("RMSE: train=%.3f test=%.3f", rmse_train, rmse_test)
    LOG.info("Mean pred MFE/MAE: winners=%.2f losers=%.2f", mean_pred_win, mean_pred_lose)
    LOG.info("AUC (ranking winners vs losers by MFE pred): %.4f", auc_mfe)

    # Feature importance
    imp = bst.feature_importance(importance_type="gain")
    imp_df = pd.DataFrame({"feature": feature_cols, "importance": imp})
    imp_df = imp_df.sort_values("importance", ascending=False)

    LOG.info("Top 10 features:")
    for _, row in imp_df.head(10).iterrows():
        LOG.info("  %s: %.1f", row["feature"], row["importance"])

    # Save model
    model_path = MODEL_DIR / "v10_mfe_regressor.lgb"
    bst.save_model(str(model_path))

    meta = {
        "model_type": "mfe_regressor",
        "target_col": target_col,
        "feature_cols": feature_cols,
        "corr_pearson_train": float(corr_train_p),
        "corr_pearson_test": float(corr_test_p),
        "corr_spearman_train": float(corr_train_s),
        "corr_spearman_test": float(corr_test_s),
        "rmse_train": float(rmse_train),
        "rmse_test": float(rmse_test),
        "mean_pred_winners": float(mean_pred_win),
        "mean_pred_losers": float(mean_pred_lose),
        "auc_mfe_ranking": float(auc_mfe),
        "n_train": len(train),
        "n_test": len(test),
        "training_time_s": elapsed,
        "params": p,
        "top_features": imp_df.head(20).to_dict(orient="records"),
    }
    meta_path = MODEL_DIR / "v10_mfe_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)

    print(f"\n{'='*70}")
    print(f"MODEL B: MFE/MAE Regressor")
    print(f"{'='*70}")
    print(f"  Target: {target_col}")
    print(f"  Pearson:  train={corr_train_p:.4f}  test={corr_test_p:.4f}")
    print(f"  Spearman: train={corr_train_s:.4f}  test={corr_test_s:.4f}")
    print(f"  RMSE:     train={rmse_train:.3f}  test={rmse_test:.3f}")
    print(f"  Mean pred: winners={mean_pred_win:.2f}  losers={mean_pred_lose:.2f}")
    print(f"  AUC (MFE ranking): {auc_mfe:.4f}  <- KEY METRIC")
    print(f"  Top 5 Features:")
    for _, row in imp_df.head(5).iterrows():
        print(f"    {row['feature']:<25} {row['importance']:.1f}")
    print(f"  Model: {model_path}")
    print(f"{'='*70}")

    return bst, meta


def main():
    parser = argparse.ArgumentParser(description="V10 Train Models")
    parser.add_argument("--skip-regressor", action="store_true",
                        help="Skip MFE regressor training")
    parser.add_argument("--skip-classifier", action="store_true",
                        help="Skip binary classifier training")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")

    dataset_path = DATA_DIR / "dataset.parquet"
    if not dataset_path.exists():
        LOG.error("No dataset.parquet. Run build_dataset.py first!")
        sys.exit(1)

    if not args.skip_classifier:
        train_binary_classifier(dataset_path)

    if not args.skip_regressor:
        train_mfe_regressor(dataset_path)

    print(f"\n{'='*70}")
    print(f"V10 TRAINING COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
