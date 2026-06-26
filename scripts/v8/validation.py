"""
validation.py — v8 Scalpel: Purged K-Fold Cross Validation with Embargo

This is the MOST CRITICAL component for honest evaluation. Without purged
validation, ALL backtest results are unreliable due to temporal leakage.

The Problem:
  In time-series data, consecutive samples are highly correlated.
  Standard K-Fold randomly splits data, putting correlated train/test
  samples in different folds → artificially high test performance.

The Solution (López de Prado, "Advances in Financial Machine Learning"):
  1. Purged K-Fold: Remove K bars between train and test where K = lookahead
     (because the label for bar i uses information from bars i+1..i+lookahead)
  2. Embargo: Remove L extra bars after the purge to account for
     autocorrelation in features (rolling indicators like EMA have memory)
  3. Purge + Embargo = K + L bars of separation between train and test

Implementation:
  - K (purge) = lookahead = 6 bars (30min at 5m) — from pattern analysis
  - L (embargo) = 3 bars (15min, covers EMA-9 decay)
  - Total separation = 9 bars = 45min

Walk-Forward vs Purged K-Fold:
  Walk-forward is more realistic (train on past, test on future) but
  gives only 1 test estimate → high variance.
  Purged K-Fold gives K estimates → lower variance, more robust.
  Best practice: USE BOTH. Purged K-Fold for model selection,
  walk-forward for final performance estimate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Generator, Tuple

import numpy as np
import pandas as pd

LOG = logging.getLogger("v8_validation")


@dataclass
class PurgedKFold:
    """Purged K-Fold Cross Validation with Embargo.

    Splits time-ordered data into K folds while ensuring:
    1. Train and test are temporally separated
    2. Purge gap = lookahead bars (label leakage)
    3. Embargo gap = extra bars (feature autocorrelation)

    Usage:
        cv = PurgedKFold(n_splits=5, lookahead=12, embargo=3)
        for train_idx, test_idx in cv.split(df):
            train = df.iloc[train_idx]
            test = df.iloc[test_idx]
    """
    n_splits: int = 5
    lookahead: int = 6   # bars to purge (30min — from pattern analysis)
    embargo: int = 3     # extra bars to drop after purge (feature memory)

    def split(self, df: pd.DataFrame) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
        """Generate purged train/test indices.

        Yields (train_indices, test_indices) for each fold.
        Data must be time-ordered (ascending).
        """
        n = len(df)
        total_gap = self.lookahead + self.embargo

        # Create fold boundaries
        fold_size = n // self.n_splits
        fold_starts = [i * fold_size for i in range(self.n_splits)]
        fold_ends = [(i + 1) * fold_size if i < self.n_splits - 1 else n
                     for i in range(self.n_splits)]

        for fold_idx in range(self.n_splits):
            test_start = fold_starts[fold_idx]
            test_end = fold_ends[fold_idx]

            # Train = everything before test (with purge gap)
            train_before_end = test_start - total_gap
            train_before = np.arange(0, max(train_before_end, 0))

            # Train = everything after test (with embargo gap)
            train_after_start = test_end + total_gap
            train_after = np.arange(max(train_after_start, n), n)

            # Combine train indices
            train_idx = np.concatenate([train_before, train_after]).astype(int)

            # Test indices
            test_idx = np.arange(test_start, test_end).astype(int)

            if len(train_idx) < 100 or len(test_idx) < 50:
                LOG.warning(
                    "Fold %d: insufficient data (train=%d test=%d), skipping",
                    fold_idx, len(train_idx), len(test_idx),
                )
                continue

            # Verify no overlap
            assert len(np.intersect1d(train_idx, test_idx)) == 0, \
                f"Fold {fold_idx}: train/test overlap detected!"

            yield train_idx, test_idx

    def get_n_splits(self, df: pd.DataFrame = None) -> int:
        """Return actual number of valid splits."""
        if df is None:
            return self.n_splits
        count = 0
        for _ in self.split(df):
            count += 1
        return count


@dataclass
class WalkForwardValidation:
    """Walk-Forward Validation with Purge + Embargo.

    More realistic than K-Fold (train strictly on past, test on future)
    but gives fewer test estimates → higher variance.

    Usage:
        wf = WalkForwardValidation(n_windows=4, test_pct=0.15,
                                    lookahead=12, embargo=3)
        for train_idx, test_idx in wf.split(df):
            ...
    """
    n_windows: int = 4
    test_pct: float = 0.15    # fraction of data for each test window
    val_pct: float = 0.10     # fraction for validation (between train and test)
    lookahead: int = 6   # 30min — from pattern analysis
    embargo: int = 3

    def split(self, df: pd.DataFrame) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
        """Generate walk-forward splits with purging.

        Each window uses the LAST portion of data as test, stepping backward.
        """
        n = len(df)
        total_gap = self.lookahead + self.embargo
        test_size = int(n * self.test_pct)
        val_size = int(n * self.val_pct)

        for w in range(self.n_windows):
            # Test window: offset backward from the end
            test_end = n - w * test_size
            test_start = test_end - test_size

            if test_start < total_gap + 200:
                LOG.warning("Window %d: insufficient data, stopping", w)
                break

            # Val window: just before test (with purge gap)
            val_start = test_start - total_gap - val_size
            val_end = test_start - total_gap

            # Train window: everything before val (with purge gap)
            train_end = val_start - total_gap if val_start > total_gap else val_start

            test_idx = np.arange(max(test_start, 0), min(test_end, n))
            val_idx = np.arange(max(val_start, 0), max(val_end, 0))
            train_idx = np.arange(0, max(train_end, 0))

            if len(train_idx) < 500 or len(test_idx) < 50:
                LOG.warning(
                    "Window %d: insufficient data (train=%d val=%d test=%d), skipping",
                    w, len(train_idx), len(val_idx), len(test_idx),
                )
                continue

            yield train_idx, val_idx, test_idx


def purged_cross_val_score(
    model_fn,
    df: pd.DataFrame,
    feature_names: list[str],
    label_col: str,
    cv: PurgedKFold,
    metric_fn=None,
) -> list[float]:
    """Run purged cross-validation and return scores per fold.

    Args:
        model_fn: callable(train_df, val_df) → trained_model
        df: full dataset
        feature_names: list of feature column names
        label_col: name of label column
        cv: PurgedKFold instance
        metric_fn: callable(y_true, y_pred) → float (default: correlation)

    Returns:
        List of scores, one per fold
    """
    if metric_fn is None:
        def metric_fn(y_true, y_pred):
            if len(y_true) < 2 or np.std(y_true) < 1e-10 or np.std(y_pred) < 1e-10:
                return 0.0
            return float(np.corrcoef(y_true, y_pred)[0, 1])

    scores = []

    for fold_idx, (train_idx, test_idx) in enumerate(cv.split(df)):
        train_df = df.iloc[train_idx]
        test_df = df.iloc[test_idx]

        # Drop NaN labels
        train_clean = train_df.dropna(subset=[label_col])
        test_clean = test_df.dropna(subset=[label_col])

        if len(train_clean) < 100 or len(test_clean) < 20:
            LOG.warning("Fold %d: too few clean samples, skipping", fold_idx)
            continue

        try:
            model = model_fn(train_clean, test_clean)

            X_test = test_clean[feature_names].values.astype(np.float32)
            y_test = test_clean[label_col].values.astype(np.float32)
            y_pred = model.predict(X_test)

            score = metric_fn(y_test, y_pred)
            scores.append(score)

            LOG.info("Fold %d: score=%.4f (train=%d test=%d)",
                     fold_idx, score, len(train_clean), len(test_clean))

        except Exception as e:
            LOG.warning("Fold %d failed: %s", fold_idx, e)
            continue

    if scores:
        LOG.info("Purged CV: mean=%.4f std=%.4f n_folds=%d",
                 np.mean(scores), np.std(scores), len(scores))
    return scores


def combinatorial_purged_cv(
    df: pd.DataFrame,
    n_groups: int = 6,
    n_test_groups: int = 2,
    lookahead: int = 6,   # 30min — from pattern analysis
    embargo: int = 3,
) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
    """Combinatorial Purged Cross-Validation (CPCV).

    More robust than Purged K-Fold. Divides data into N groups,
    then tests all combinations of n_test_groups.

    For n_groups=6, n_test_groups=2: C(6,2) = 15 test paths.
    This gives a DISTRIBUTION of backtest results, not a point estimate.

    From López de Prado (2018) — the gold standard for financial ML validation.
    """
    from itertools import combinations

    n = len(df)
    group_size = n // n_groups
    total_gap = lookahead + embargo

    # Assign each row to a group
    groups = []
    for g in range(n_groups):
        start = g * group_size
        end = (g + 1) * group_size if g < n_groups - 1 else n
        groups.append(np.arange(start, end))

    # Generate all combinations of test groups
    for test_combo in combinations(range(n_groups), n_test_groups):
        test_idx = np.concatenate([groups[g] for g in test_combo])

        # Train = all groups NOT in test combo, minus purge+embargo
        train_groups = [g for g in range(n_groups) if g not in test_combo]
        train_idx = np.concatenate([groups[g] for g in train_groups])

        # Apply purge: remove bars near test boundaries
        test_min = test_idx.min()
        test_max = test_idx.max()

        # Remove from train: bars within total_gap of test boundaries
        mask = ~(
            ((train_idx >= test_min - total_gap) & (train_idx < test_min)) |
            ((train_idx > test_max) & (train_idx <= test_max + total_gap))
        )
        train_idx = train_idx[mask]

        if len(train_idx) < 500 or len(test_idx) < 50:
            continue

        yield train_idx, test_idx
