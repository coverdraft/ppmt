"""
train.py — Train binary classifier: winning entries vs everything else

Model: LightGBM binary classifier
  - Positive class (label=1): bars where the trader entered AND WON
  - Negative class (label=0): bars where the trader entered and LOST (hard neg)
                           + random bars where the trader didn't enter (easy neg)

The model learns: "given market features at this bar, is this a WINNING entry?"

Key insight from v1: labeling ALL entries (winners+losers) as positive taught the
model "what does any entry look like" → 38% WR in backtest. By separating winners
from losers, the model learns to distinguish good entries from bad ones.

Hard negatives (loser entries) get 3x sample weight vs easy negatives (random bars)
because they're much more informative — the market looked like an entry but wasn't
profitable.
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

LOG = logging.getLogger("v9_train")

DATA_DIR = PROJECT_ROOT / "data" / "v9"
MODEL_DIR = DATA_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

FEATURE_COLS_PATH = DATA_DIR / "feature_columns.json"


def train_model(dataset_path: Path, params: dict = None) -> tuple:
    """Train LightGBM binary classifier: winning entries vs rest."""
    LOG.info("Loading dataset from %s", dataset_path)
    df = pd.read_parquet(dataset_path)

    with open(FEATURE_COLS_PATH) as f:
        feature_cols = json.load(f)

    # Filter to available columns
    feature_cols = [c for c in feature_cols if c in df.columns]
    LOG.info("Features: %d", len(feature_cols))

    # Clean
    df = df.dropna(subset=["label"])
    for col in feature_cols:
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)

    # ── Convert 3-class labels to binary ──
    # Original: 1.0=winner, -1.0=loser, 0.0=random
    # Binary:   1=winner, 0=loser or random
    df["label_binary"] = (df["label"] == 1.0).astype(float)

    n_winners = int(df["label_binary"].sum())
    n_losers = int((df["label"] == -1.0).sum())
    n_random = int((df["label"] == 0.0).sum())
    n_neg = n_losers + n_random

    LOG.info("Dataset: %d rows (winners=%d / losers=%d / random=%d / total_neg=%d)",
             len(df), n_winners, n_losers, n_random, n_neg)

    # ── Guard: need at least some data ──
    if len(df) < 50 or n_winners < 10 or n_neg < 10:
        LOG.error("Dataset too small! Need >= 50 rows with >= 10 winners and >= 10 negatives.")
        LOG.error("Got: %d total, %d winners, %d neg", len(df), n_winners, n_neg)
        LOG.error("Run: python3 -m scripts.v9.diagnose_build to debug")
        sys.exit(1)

    # ── Sample weights: hard negatives get 3x weight ──
    # Loser entries are much more informative than random bars
    sample_weight = np.ones(len(df))
    loser_mask = df["label"] == -1.0
    sample_weight[loser_mask] = 3.0  # hard negatives: 3x weight
    # Winners and random bars keep weight=1.0
    df["_sample_weight"] = sample_weight

    # Time-based split: 80% train, 20% test
    df = df.sort_values("timestamp").reset_index(drop=True)
    split = int(len(df) * 0.8)

    train = df.iloc[:split]
    test = df.iloc[split:]

    LOG.info("Train: %d  Test: %d", len(train), len(test))

    X_train = train[feature_cols].values.astype(np.float32)
    y_train = train["label_binary"].values.astype(np.float32)
    w_train = train["_sample_weight"].values.astype(np.float32)
    X_test = test[feature_cols].values.astype(np.float32)
    y_test = test["label_binary"].values.astype(np.float32)
    w_test = test["_sample_weight"].values.astype(np.float32)

    # Class weights (winners are rarer)
    n_pos_tr = int((y_train == 1).sum())
    n_neg_tr = int((y_train == 0).sum())
    scale_pos = n_neg_tr / max(n_pos_tr, 1)
    LOG.info("Class balance: winners=%d neg=%d scale_pos=%.1f", n_pos_tr, n_neg_tr, scale_pos)

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
        from sklearn.metrics import roc_auc_score, classification_report, precision_recall_curve

        # Overall AUC (winners vs all negatives)
        auc_test = roc_auc_score(y_test, pred_test)
        auc_train = roc_auc_score(y_train, pred_train)

        # AUC winners vs losers ONLY (most important metric!)
        test_losers_mask = test["label"] == -1.0
        test_winners_or_losers = test[test_losers_mask | (test["label"] == 1.0)]
        if len(test_winners_or_losers) > 20:
            y_wl = (test_winners_or_losers["label"] == 1.0).astype(float)
            p_wl = bst.predict(test_winners_or_losers[feature_cols].values.astype(np.float32))
            auc_win_vs_lose = roc_auc_score(y_wl, p_wl)
            LOG.info("AUC winners-vs-losers: %.4f (KEY METRIC — can model tell them apart?)", auc_win_vs_lose)
        else:
            auc_win_vs_lose = 0.0

        # Find optimal threshold
        precision, recall, thresholds = precision_recall_curve(y_test, pred_test)
        f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-10)
        best_idx = np.argmax(f1)
        best_threshold = float(thresholds[best_idx]) if best_idx < len(thresholds) else 0.5

        LOG.info("Train AUC: %.4f  Test AUC: %.4f", auc_train, auc_test)
        LOG.info("Best threshold: %.3f (F1=%.3f, P=%.3f, R=%.3f)",
                 best_threshold, f1[best_idx], precision[best_idx], recall[best_idx])

        # Classification report at best threshold
        y_pred = (pred_test > best_threshold).astype(int)
        report = classification_report(y_test, y_pred, target_names=["NO_WIN", "WINNER_ENTRY"])
        LOG.info("\n%s", report)

    except ImportError:
        LOG.warning("sklearn not available, skipping detailed metrics")
        auc_test = 0.0
        auc_train = 0.0
        auc_win_vs_lose = 0.0
        best_threshold = 0.5

    # Feature importance
    imp = bst.feature_importance(importance_type="gain")
    imp_df = pd.DataFrame({"feature": feature_cols, "importance": imp})
    imp_df = imp_df.sort_values("importance", ascending=False)

    LOG.info("Top 10 features:")
    for _, row in imp_df.head(10).iterrows():
        LOG.info("  %s: %.1f", row["feature"], row["importance"])

    # Save model
    model_path = MODEL_DIR / "v9_trader_classifier.lgb"
    bst.save_model(str(model_path))

    # Save metadata
    meta = {
        "feature_cols": feature_cols,
        "auc_train": float(auc_train),
        "auc_test": float(auc_test),
        "auc_win_vs_lose": float(auc_win_vs_lose),
        "best_threshold": best_threshold,
        "n_train": len(train),
        "n_test": len(test),
        "n_winners_train": n_pos_tr,
        "n_neg_train": n_neg_tr,
        "n_winners_total": n_winners,
        "n_losers_total": n_losers,
        "n_random_total": n_random,
        "training_time_s": elapsed,
        "params": p,
        "top_features": imp_df.head(20).to_dict(orient="records"),
        "label_scheme": "v2_winners_only",
    }
    meta_path = MODEL_DIR / "v9_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)

    LOG.info("Model saved to %s", model_path)

    # Print summary
    print(f"\n{'='*70}")
    print(f"V9 CLASSIFIER TRAINED (v2 — Winners-Only Labels)")
    print(f"{'='*70}")
    print(f"  Train: {len(train)} rows ({n_pos_tr} winners / {n_neg_tr} neg)")
    print(f"  Test:  {len(test)} rows")
    print(f"  AUC Train:  {auc_train:.4f}")
    print(f"  AUC Test:   {auc_test:.4f}")
    print(f"  AUC Win vs Lose: {auc_win_vs_lose:.4f}  ← KEY METRIC")
    print(f"  Best Threshold: {best_threshold:.3f}")
    print(f"  Top 5 Features:")
    for _, row in imp_df.head(5).iterrows():
        print(f"    {row['feature']:<25} {row['importance']:.1f}")
    print(f"  Model: {model_path}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"{'='*70}")

    return bst, meta


def main():
    parser = argparse.ArgumentParser(description="v9 Train Classifier")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")

    dataset_path = DATA_DIR / "dataset.parquet"
    if not dataset_path.exists():
        LOG.error("No dataset.parquet. Run build_dataset.py first!")
        sys.exit(1)

    train_model(dataset_path)


if __name__ == "__main__":
    main()
