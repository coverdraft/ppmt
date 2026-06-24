"""
v7_train_long_expert_f6.py — F6: Train LightGBM LONG expert with 96 features.

Extends F5a (71 features: 59 v6 + 12 F4) by adding 25 trie features
extracted by v7_extract_trie_features.py:
  - trie_n1_pred_3/5, trie_n1_conf_3/5, trie_n1_count_3/5
  - trie_n2_pred_3/5, trie_n2_conf_3/5, trie_n2_count_3/5, trie_n2_source_3/5
  - trie_agreement_3/5, trie_conflict_3/5, trie_strength_3/5
  - trie_n1_pred_avg, trie_n2_pred_avg, trie_agreement_avg, trie_strength_avg, trie_any_signal

Goal: Break ATR dominance (Guard #3) by diversifying signal sources.
Target: top_feat_pct < 30% (was 38-48% in F5a).

Anti-leakage: trie features at time T contain outcomes from rows with ts < T only
(INSERT-AFTER-PREDICT enforced by v7_trie_conflict.py).

USAGE:
    python /home/z/my-project/scripts/v7/v7_train_long_expert_f6.py
    python /home/z/my-project/scripts/v7/v7_train_long_expert_f6.py --windows 2025-04,2025-10
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_squared_error, mean_absolute_error

# Make v7 module importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Import everything from F5a trainer
from v7_train_long_expert import (
    DB_PATH,
    OUTPUT_DIR as F5A_OUTPUT_DIR,
    LOG,
    FEATURE_NAMES_V6,
    FEATURE_NAMES_F4,
    LABEL,
    PUMP_WEIGHT,
    BEAR_WEIGHT,
    PUMP_PERCENTILE,
    WF_WINDOWS,
    LGB_PARAMS,
    N_BOOST_ROUND,
    EARLY_STOPPING,
    compute_sample_weights,
    walk_forward_splits,
    run_anti_leakage_checks,
)
from v7_trie_conflict import TRIE_FEATURE_NAMES as FEATURE_NAMES_TRIE

# F6 output dir (separate from F5a to keep both for comparison)
OUTPUT_DIR = Path("/home/z/my-project/data/v7_models/long_expert_f6")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# F6 feature list (96 = 59 + 12 + 25)
FEATURE_NAMES = FEATURE_NAMES_V6 + FEATURE_NAMES_F4 + FEATURE_NAMES_TRIE
assert len(FEATURE_NAMES) == 96, f"Expected 96, got {len(FEATURE_NAMES)}"

# F6 parquet (created by v7_materialize_f6_features.py)
PARQUET_PATH = OUTPUT_DIR.parent / "long_expert" / "long_features_f6.parquet"


def load_dataset() -> pd.DataFrame:
    """Load F6 LONG-filtered parquet (96 features)."""
    if not PARQUET_PATH.exists():
        LOG.error("F6 parquet not found: %s", PARQUET_PATH)
        LOG.error("Run: python scripts/v7/v7_materialize_f6_features.py")
        sys.exit(1)

    LOG.info("Loading F6 parquet: %s", PARQUET_PATH)
    t0 = time.time()
    df = pd.read_parquet(PARQUET_PATH)
    LOG.info(
        "  loaded %d rows × %d cols in %.1fs (%.1f MB)",
        len(df), len(df.columns), time.time() - t0,
        PARQUET_PATH.stat().st_size / 1e6,
    )
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    LOG.info("  label stats: mean=%.4f%% std=%.4f%% n=%d",
             float(df[LABEL].mean()), float(df[LABEL].std()), len(df))
    LOG.info("  windows: %s", df["window"].value_counts().to_dict())

    # Verify all 96 features present
    missing = [f for f in FEATURE_NAMES if f not in df.columns]
    if missing:
        LOG.error("Missing %d features in parquet: %s", len(missing), missing[:5])
        sys.exit(1)
    LOG.info("  all %d features present", len(FEATURE_NAMES))
    return df


def train_one_window(name: str, train_df: pd.DataFrame, test_df: pd.DataFrame) -> Dict:
    """Train LightGBM-LONG F6 expert on one walk-forward window (96 features)."""
    t0 = time.time()

    X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_df[LABEL].values.astype(np.float32)
    w_train = compute_sample_weights(y_train, train_df["window"].values)
    X_test = test_df[FEATURE_NAMES].values.astype(np.float32)
    y_test = test_df[LABEL].values.astype(np.float32)

    # Validation split (15% of train)
    rng = np.random.default_rng(seed=42)
    n_val = max(int(len(X_train) * 0.15), 100)
    n_val = min(n_val, len(X_train) - 1)
    val_idx = rng.choice(len(X_train), size=n_val, replace=False)
    val_mask = np.zeros(len(X_train), dtype=bool)
    val_mask[val_idx] = True
    X_val, y_val, w_val = X_train[val_mask], y_train[val_mask], w_train[val_mask]
    X_tr, y_tr, w_tr = X_train[~val_mask], y_train[~val_mask], w_train[~val_mask]

    LOG.info("[%s] train=%d val=%d test=%d  (effective_sample=%.0f)",
             name, len(X_tr), len(X_val), len(X_test), w_tr.sum())

    dtrain = lgb.Dataset(X_tr, label=y_tr, weight=w_tr, feature_name=FEATURE_NAMES)
    dval = lgb.Dataset(X_val, label=y_val, weight=w_val, feature_name=FEATURE_NAMES, reference=dtrain)

    model = lgb.train(
        LGB_PARAMS,
        dtrain,
        num_boost_round=N_BOOST_ROUND,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False), lgb.log_evaluation(0)],
    )
    best_iter = int(model.best_iteration) if model.best_iteration else N_BOOST_ROUND

    pred_train = model.predict(X_tr, num_iteration=best_iter)
    pred_val = model.predict(X_val, num_iteration=best_iter)
    pred_test = model.predict(X_test, num_iteration=best_iter)

    rmse_test = float(np.sqrt(mean_squared_error(y_test, pred_test)))
    mae_test = float(mean_absolute_error(y_test, pred_test))
    rmse_train = float(np.sqrt(mean_squared_error(y_tr, pred_train)))
    try: corr_test = float(np.corrcoef(y_test, pred_test)[0, 1])
    except Exception: corr_test = 0.0
    try: corr_train = float(np.corrcoef(y_tr, pred_train)[0, 1])
    except Exception: corr_train = 0.0
    try: corr_val = float(np.corrcoef(y_val, pred_val)[0, 1])
    except Exception: corr_val = 0.0

    dir_acc_test = float((pred_test > 0).mean())
    dir_acc_train = float((pred_train > 0).mean())

    thresholds = [0.20, 0.30, 0.40, 0.50, 0.75, 1.00]
    results_by_thr = {}
    for thr in thresholds:
        long_mask = pred_test > thr
        n_long = int(long_mask.sum())
        if n_long > 0:
            actuals_long = y_test[long_mask]
            net_long = actuals_long - 0.14  # LONG PnL = actual - 0.14% round-trip
            wins = float(net_long[net_long > 0].sum())
            losses = float(-net_long[net_long < 0].sum())
            pf = wins / losses if losses > 0 else 99.0
            wr = float((net_long > 0).mean())
            avg_pnl = float(net_long.mean())
            tot_dollars = float(net_long.sum() / 100 * 700)
        else:
            pf = wr = avg_pnl = tot_dollars = 0.0
        results_by_thr[f"thr_{thr:.2f}"] = {
            "n_signals": n_long, "wr": float(wr), "pf": float(pf),
            "avg_pnl_pct": float(avg_pnl), "tot_dollars": float(tot_dollars),
        }

    importance = model.feature_importance(importance_type="gain")
    feat_imp = sorted(zip(FEATURE_NAMES, importance), key=lambda x: -x[1])
    total = max(float(importance.sum()), 1.0)
    top_feat_pct = feat_imp[0][1] / total

    model_path = OUTPUT_DIR / f"v7_long_expert_f6_{name}.txt"
    model.save_model(str(model_path))

    result = {
        "window": name,
        "model_type": "v7_long_expert_f6",
        "label": LABEL,
        "n_features": len(FEATURE_NAMES),
        "n_train": len(X_tr),
        "n_val": len(X_val),
        "n_test": len(X_test),
        "effective_sample_size": float(w_tr.sum()),
        "n_pumps_train": int((y_tr >= np.percentile(y_tr, PUMP_PERCENTILE)).sum()),
        "n_bear_train": int((train_df["window"].values == "BEAR_2022").sum()),
        "best_iteration": best_iter,
        "rmse_train": rmse_train,
        "rmse_test": rmse_test,
        "mae_test": mae_test,
        "corr_train": corr_train,
        "corr_val": corr_val,
        "corr_test": corr_test,
        "dir_acc_test": dir_acc_test,
        "dir_acc_train": dir_acc_train,
        "long_thresholds": results_by_thr,
        "top_feat_pct": float(top_feat_pct),
        "top_feat_name": feat_imp[0][0],
        "top_20_features": [
            {"name": n, "gain": float(g), "pct": float(g / total * 100)}
            for n, g in feat_imp[:20]
        ],
        "guards": {
            "top_feat_under_30pct": bool(top_feat_pct < 0.30),
            "train_corr_under_085": bool(corr_train < 0.85),
        },
        "model_path": str(model_path),
        "train_time_seconds": float(time.time() - t0),
    }
    LOG.info(
        "[%s] done in %.1fs  best_iter=%d  rmse_test=%.4f  corr_test=%+.4f  "
        "thr_0.30: n=%d wr=%.3f pf=%.2f tot=$%+.0f  top=%s(%.1f%%)",
        name, result["train_time_seconds"], best_iter,
        rmse_test, corr_test,
        results_by_thr["thr_0.30"]["n_signals"],
        results_by_thr["thr_0.30"]["wr"],
        results_by_thr["thr_0.30"]["pf"],
        results_by_thr["thr_0.30"]["tot_dollars"],
        feat_imp[0][0], top_feat_pct * 100,
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", default=None,
                        help="Comma-separated YYYY-MM windows")
    parser.add_argument("--max-seconds", type=int, default=600,
                        help="Safety cap on total runtime (seconds)")
    args = parser.parse_args()

    if args.windows:
        global WF_WINDOWS
        WF_WINDOWS = args.windows.split(",")

    print("=" * 76)
    print("v7 LONG EXPERT F6 — 96 features (59 v6 + 12 F4 + 25 trie)")
    print(f"  parquet: {PARQUET_PATH}")
    print(f"  label: {LABEL} (15m forward return %, filtered > 0)")
    print(f"  sample weights: 2x top-{PUMP_PERCENTILE}% pumps + 2x BEAR_2022")
    print(f"  LGB params: {LGB_PARAMS}")
    print(f"  walk-forward windows: {WF_WINDOWS}")
    print(f"  output: {OUTPUT_DIR}")
    print("=" * 76)

    df = load_dataset()
    splits = walk_forward_splits(df)
    del df
    gc.collect()

    if not splits:
        LOG.error("No valid walk-forward splits found.")
        sys.exit(1)

    results = []
    t0 = time.time()
    for i, (name, train_df, test_df) in enumerate(splits, 1):
        if time.time() - t0 > args.max_seconds:
            LOG.warning("Hit max_seconds=%d before window %s", args.max_seconds, name)
            break
        LOG.info("=== Window %s (%d/%d) ===", name, i, len(splits))
        r = train_one_window(name, train_df, test_df)
        results.append(r)
        del train_df, test_df
        gc.collect()

    checks = run_anti_leakage_checks(results)

    # Print summary
    print("\n" + "=" * 110)
    print("v7 LONG EXPERT F6 RESULTS (walk-forward, 96 features)")
    print("=" * 110)
    print(f"{'window':<10} {'n_tr':>7} {'n_te':>6} {'rmse_t':>8} {'corr_t':>7} {'dir_t':>7} "
          f"{'thr0.30_n':>9} {'thr0.30_wr':>10} {'thr0.30_pf':>10} {'top_feat%':>10}")
    for r in results:
        thr30 = r["long_thresholds"]["thr_0.30"]
        print(f"{r['window']:<10} {r['n_train']:>7,} {r['n_test']:>6,} "
              f"{r['rmse_test']:>8.4f} {r['corr_test']:>+7.4f} {r['dir_acc_test']:>7.4f} "
              f"{thr30['n_signals']:>9,} {thr30['wr']:>10.3f} {thr30['pf']:>10.2f} "
              f"{r['top_feat_pct']*100:>9.1f}%")
    print()
    print(f"Mean test corr:     {checks['test_corr_mean']:+.4f}")
    print(f"Test corr std:      {checks['guard_5_corr_std']:.4f}  (guard #5 threshold: 0.05)")
    print(f"Max train corr:     {checks['guard_4_max_train_corr']:+.4f}  (guard #4 threshold: 0.85)")
    print(f"Max top-feat pct:   {checks['guard_3_max_top_feat_pct']*100:.1f}%  (guard #3 threshold: 30%)")
    print()
    if checks["alerts"]:
        print("=== ANTI-LEAKAGE ALERTS ===")
        for a in checks["alerts"]:
            print(f"  WARNING: {a}")
    else:
        print("OK: All anti-leakage guards passed")
    print()

    if results:
        print("=== Top 20 features (first window) ===")
        for f in results[0]["top_20_features"]:
            print(f"  {f['name']:<28} {f['gain']:>12.0f}  ({f['pct']:.2f}%)")

    summary_path = OUTPUT_DIR / "v7_long_expert_f6_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "results": results,
            "anti_leakage_checks": checks,
            "feature_names": FEATURE_NAMES,
            "label": LABEL,
            "n_features": len(FEATURE_NAMES),
            "feature_breakdown": {
                "v6_base": len(FEATURE_NAMES_V6),
                "f4_extras": len(FEATURE_NAMES_F4),
                "trie_features": len(FEATURE_NAMES_TRIE),
            },
            "sample_weights": {
                "pump_weight": PUMP_WEIGHT,
                "bear_weight": BEAR_WEIGHT,
                "pump_percentile": PUMP_PERCENTILE,
            },
            "lgb_params": LGB_PARAMS,
            "n_boost_round": N_BOOST_ROUND,
            "early_stopping": EARLY_STOPPING,
            "parquet_path": str(PARQUET_PATH),
        }, f, indent=2)
    print(f"\nSummary saved: {summary_path}")


if __name__ == "__main__":
    main()
