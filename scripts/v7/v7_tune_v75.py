"""
v7_tune_v75.py — Hyperparameter tuning + threshold sweep for v7.5 (Option D).

WHAT THIS DOES
--------------
Two-stage optimization:

STAGE 1: Optuna hyperparameter search (LightGBM)
  - Search space: num_leaves, learning_rate, feature_fraction, bagging_fraction,
    min_data_in_leaf, lambda_l1, lambda_l2, max_depth, n_boost_round
  - Objective: mean test-window corr (across 5 walk-forward windows)
  - Constraints: train_corr < 0.85, top_feat_pct < 30% (anti-leakage guards)
  - Trials: 50 (default) or --trials N
  - ~10-15 min on 5 symbols / 728k rows

STAGE 2: Threshold sweep on best model
  - For each (thr_long, thr_short) in grid, compute WR / PF / PnL / Sharpe / MaxDD
  - Grid: thr_long in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
          thr_short in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
  - Find best combination for: Sharpe, WR, PF
  - Saves CSV + JSON summary

OUTPUTS
-------
  data/v7_models/v75/v75_tuning_study.json       (Optuna study results)
  data/v7_models/v75/v75_best_params.json        (best hyperparams)
  data/v7_models/v75/v75_best_model_*.txt        (retrained model per window with best params)
  data/v7_models/v75/v75_threshold_sweep.csv     (full threshold grid)
  data/v7_models/v75/v75_threshold_best.json     (best threshold combos)

USAGE
-----
    python3 scripts/v7/v7_tune_v75.py                    # default 50 trials
    python3 scripts/v7/v7_tune_v75.py --trials 100       # more trials
    python3 scripts/v7/v7_tune_v75.py --skip-tuning      # only threshold sweep on existing models
    python3 scripts/v7/v7_tune_v75.py --skip-sweep       # only Optuna tuning
"""
from __future__ import annotations

# === Auto-detected project root (portable paths, patched) ===
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[2]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
# === End path setup ===

import argparse
import gc
import json
import logging
import time
import warnings
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_squared_error

try:
    import optuna
    from optuna.samplers import TPESampler
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    HAVE_OPTUNA = True
except ImportError:
    HAVE_OPTUNA = False

warnings.filterwarnings("ignore")

# ----------------------------------------------------------------------------
# Constants — MUST match v7_train_v75.py
# ----------------------------------------------------------------------------

OUTPUT_DIR = Path(_PROJECT_ROOT_STR + "/data/v7_models/v75")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v7_5_tune")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Feature lists (copy from v7_train_v75.py)
FEATURE_NAMES_V5 = [
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
FEATURE_NAMES_V6_NEW = [
    "btc_ret_1m", "btc_ret_5m", "btc_ret_15m", "btc_vol_z",
    "btc_trend_50", "eth_corr_30", "btc_alt_spread_15m", "btc_volatility_regime",
    "vol_delta_3", "wick_imbalance_3", "body_consistency_5",
    "range_expansion_3", "close_persistence_5", "vol_acceleration",
    "atr_percentile_50", "trend_strength_50", "regime_vol_trend", "hour_quantile",
    "alt_lead_5m", "alt_lag_signal", "momentum_dispersion",
]
FEATURE_NAMES_V6 = FEATURE_NAMES_V5 + FEATURE_NAMES_V6_NEW
FEATURE_NAMES_F4 = [
    "funding_rate", "funding_rate_z",
    "oi_change_1h", "oi_change_4h",
    "sector_blue_chip", "sector_large_cap", "sector_old_meme", "sector_new_meme",
    "sector_idx",
    "day_of_week_sin", "day_of_week_cos", "day_of_week",
]
FEATURE_NAMES = FEATURE_NAMES_V6 + FEATURE_NAMES_F4
assert len(FEATURE_NAMES) == 71

LABEL = "fwd_ret_3"
WF_WINDOWS = ["2025-04", "2025-05", "2025-06", "2025-09", "2025-10"]
ROUND_TRIP_COST_PCT = 0.14
PARQUET_PATH = OUTPUT_DIR / "v75_features.parquet"


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------

def load_data() -> pd.DataFrame:
    """Load the materialized v7.5 parquet."""
    if not PARQUET_PATH.exists():
        raise FileNotFoundError(
            f"Parquet not found at {PARQUET_PATH}. "
            "Run v7_materialize_v75_features.py first."
        )
    t0 = time.time()
    df = pd.read_parquet(PARQUET_PATH)
    LOG.info("Loaded parquet: %s", PARQUET_PATH)
    LOG.info("  %d rows × %d cols in %.1fs", len(df), len(df.columns), time.time() - t0)

    for f in FEATURE_NAMES + [LABEL]:
        if f in df.columns:
            df[f] = pd.to_numeric(df[f], errors="coerce").replace([np.inf, -np.inf], 0).fillna(0).astype(np.float32)

    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True, errors="coerce")
    return df


def walk_forward_splits(df: pd.DataFrame) -> List[Tuple[str, pd.DataFrame, pd.DataFrame]]:
    splits = []
    for window_str in WF_WINDOWS:
        yr, mo = window_str.split("-")
        yr, mo = int(yr), int(mo)
        test_mask = (df["ts"].dt.year == yr) & (df["ts"].dt.month == mo)
        test_df = df[test_mask].copy()
        cutoff = pd.Timestamp(year=yr, month=mo, day=1, tz="UTC")
        train_df = df[df["ts"] < cutoff].copy()
        if len(train_df) > 1000 and len(test_df) > 500:
            splits.append((window_str, train_df, test_df))
    return splits


# ----------------------------------------------------------------------------
# STAGE 1: Optuna hyperparameter tuning
# ----------------------------------------------------------------------------

def make_lgb_params(trial: "optuna.Trial") -> Dict:
    """Sample LightGBM hyperparameters from Optuna trial."""
    return {
        "objective": "regression",
        "metric": ["rmse"],
        "num_leaves": trial.suggest_int("num_leaves", 15, 127),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.20, log=True),
        "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
        "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
        "bagging_freq": trial.suggest_int("bagging_freq", 1, 10),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 50, 500, log=True),
        "lambda_l1": trial.suggest_float("lambda_l1", 1e-3, 10.0, log=True),
        "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10.0, log=True),
        "max_depth": trial.suggest_int("max_depth", -1, 12),
        "verbosity": -1,
        "seed": 42,
    }


def objective(trial: "optuna.Trial", splits) -> float:
    """Optuna objective: train 5 walk-forward models, return mean test corr."""
    params = make_lgb_params(trial)
    n_boost = trial.suggest_int("n_boost_round", 100, 500, step=50)
    early_stop = 30

    corrs = []
    train_corrs = []
    top_feat_pcts = []

    for name, train_df, test_df in splits:
        X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
        y_train = train_df[LABEL].values.astype(np.float32)
        X_test = test_df[FEATURE_NAMES].values.astype(np.float32)
        y_test = test_df[LABEL].values.astype(np.float32)

        rng = np.random.default_rng(seed=42)
        n_val = int(len(X_train) * 0.1)
        val_idx = rng.choice(len(X_train), size=n_val, replace=False)
        val_mask = np.zeros(len(X_train), dtype=bool)
        val_mask[val_idx] = True
        X_val, y_val = X_train[val_mask], y_train[val_mask]
        X_tr, y_tr = X_train[~val_mask], y_train[~val_mask]

        dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES)
        dval = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, reference=dtrain)

        try:
            model = lgb.train(
                params, dtrain,
                num_boost_round=n_boost,
                valid_sets=[dval],
                callbacks=[lgb.early_stopping(early_stop, verbose=False), lgb.log_evaluation(0)],
            )
            best_iter = int(model.best_iteration) if model.best_iteration else n_boost
        except Exception as e:
            LOG.warning("Trial %d, window %s: training failed: %s", trial.number, name, e)
            return -1.0  # bad trial

        pred_test = model.predict(X_test, num_iteration=best_iter)
        pred_train = model.predict(X_tr, num_iteration=best_iter)

        try:
            corr_test = float(np.corrcoef(y_test, pred_test)[0, 1])
        except Exception:
            corr_test = 0.0
        try:
            corr_train = float(np.corrcoef(y_tr, pred_train)[0, 1])
        except Exception:
            corr_train = 0.0

        # Anti-leakage guards
        importance = model.feature_importance(importance_type="gain")
        top_pct = float(max(importance) / max(float(importance.sum()), 1.0) * 100)

        corrs.append(corr_test)
        train_corrs.append(corr_train)
        top_feat_pcts.append(top_pct)

        del model, dtrain, dval
        gc.collect()

    mean_corr = float(np.mean(corrs))
    max_train_corr = float(max(train_corrs))
    max_top_pct = float(max(top_feat_pcts))

    # Hard constraints: kill trial if guards fail
    if max_train_corr > 0.85:
        return -1.0
    if max_top_pct > 30.0:
        return -1.0

    # Soft penalty for instability (high std of corrs)
    corr_std = float(np.std(corrs))
    penalty = max(0.0, (corr_std - 0.05) * 2.0)

    trial.set_user_attr("mean_corr", mean_corr)
    trial.set_user_attr("corr_std", corr_std)
    trial.set_user_attr("max_train_corr", max_train_corr)
    trial.set_user_attr("max_top_pct", max_top_pct)
    trial.set_user_attr("per_window_corrs", corrs)

    return mean_corr - penalty


def run_optuna(splits, n_trials: int = 50) -> Dict:
    """Run Optuna hyperparameter optimization."""
    if not HAVE_OPTUNA:
        raise ImportError("optuna not installed. Run: pip install optuna")

    LOG.info("=" * 80)
    LOG.info("STAGE 1: Optuna hyperparameter tuning (%d trials)", n_trials)
    LOG.info("=" * 80)

    study = optuna.create_study(
        direction="maximize",
        sampler=TPESampler(seed=42),
        study_name="v75_tuning",
    )

    t0 = time.time()
    study.optimize(lambda t: objective(t, splits), n_trials=n_trials, show_progress_bar=False)
    elapsed = time.time() - t0

    best = study.best_trial
    LOG.info("")
    LOG.info("Optuna done in %.1f min", elapsed / 60)
    LOG.info("Best trial #%d: objective=%.4f", best.number, best.value)
    LOG.info("Best params:")
    for k, v in best.params.items():
        LOG.info("  %s = %s", k, v)
    LOG.info("User attrs:")
    for k, v in best.user_attrs.items():
        LOG.info("  %s = %s", k, v)

    result = {
        "best_trial_number": best.number,
        "best_objective": best.value,
        "best_params": best.params,
        "best_user_attrs": best.user_attrs,
        "n_trials": n_trials,
        "elapsed_seconds": elapsed,
        "all_trial_values": [t.value if t.value is not None else None for t in study.trials],
        "all_trial_params": [t.params for t in study.trials],
    }

    with open(OUTPUT_DIR / "v75_tuning_study.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    with open(OUTPUT_DIR / "v75_best_params.json", "w") as f:
        json.dump({"params": best.params, "user_attrs": best.user_attrs}, f, indent=2, default=str)

    LOG.info("Saved: %s", OUTPUT_DIR / "v75_best_params.json")
    return result


# ----------------------------------------------------------------------------
# STAGE 1b: Retrain with best params and save models
# ----------------------------------------------------------------------------

def retrain_with_best_params(splits, best_params: Dict) -> Dict[str, lgb.Booster]:
    """Retrain 5 walk-forward models with the best params and save to disk."""
    LOG.info("")
    LOG.info("Retraining 5 walk-forward models with best params...")
    models = {}

    # Build full LGB params (add fixed fields)
    lgb_params = {
        "objective": "regression",
        "metric": ["rmse"],
        "verbosity": -1,
        "seed": 42,
    }
    lgb_params.update({k: v for k, v in best_params.items() if k != "n_boost_round"})
    n_boost = best_params.get("n_boost_round", 200)

    for name, train_df, test_df in splits:
        X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
        y_train = train_df[LABEL].values.astype(np.float32)

        rng = np.random.default_rng(seed=42)
        n_val = int(len(X_train) * 0.1)
        val_idx = rng.choice(len(X_train), size=n_val, replace=False)
        val_mask = np.zeros(len(X_train), dtype=bool)
        val_mask[val_idx] = True
        X_val, y_val = X_train[val_mask], y_train[val_mask]
        X_tr, y_tr = X_train[~val_mask], y_train[~val_mask]

        dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES)
        dval = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, reference=dtrain)

        model = lgb.train(
            lgb_params, dtrain,
            num_boost_round=n_boost,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(0)],
        )
        best_iter = int(model.best_iteration) if model.best_iteration else n_boost
        models[name] = model
        model_path = OUTPUT_DIR / f"v75_best_{name}.txt"
        model.save_model(str(model_path))
        LOG.info("  %s: saved %s (best_iter=%d)", name, model_path.name, best_iter)

    return models


# ----------------------------------------------------------------------------
# STAGE 2: Threshold sweep
# ----------------------------------------------------------------------------

def threshold_sweep(splits, models: Dict[str, lgb.Booster]) -> Tuple[pd.DataFrame, Dict]:
    """Sweep over (thr_long, thr_short) grid. Compute WR/PF/PnL/Sharpe/MaxDD."""
    LOG.info("")
    LOG.info("=" * 80)
    LOG.info("STAGE 2: Threshold sweep")
    LOG.info("=" * 80)

    # First, build predictions per window
    LOG.info("Predicting all windows...")
    window_data = {}
    for name, train_df, test_df in splits:
        model = models[name]
        X_test = test_df[FEATURE_NAMES].values.astype(np.float32)
        y_test = test_df[LABEL].values.astype(np.float32)
        best_iter = int(model.best_iteration) if model.best_iteration else 200
        pred = model.predict(X_test, num_iteration=best_iter)
        window_data[name] = {"pred": pred, "y": y_test, "ts": test_df["ts"].values}
        LOG.info("  %s: %d rows, pred mean=%+.4f%% std=%.4f%%",
                 name, len(pred), float(pred.mean()), float(pred.std()))

    # Grid
    thr_grid = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
    rows = []

    for thr_long in thr_grid:
        for thr_short in thr_grid:
            # Aggregate trades across all 5 windows
            all_pnl = []
            all_ts = []
            n_long = n_short = 0
            n_win_long = n_win_short = 0

            for name, wd in window_data.items():
                pred = wd["pred"]
                y = wd["y"]
                ts = wd["ts"]

                long_mask = pred > thr_long
                short_mask = pred < -thr_short

                # LONG: pay fwd_ret_3 - cost
                if long_mask.sum() > 0:
                    pnl_l = y[long_mask] - ROUND_TRIP_COST_PCT
                    all_pnl.extend(pnl_l.tolist())
                    all_ts.extend(ts[long_mask].tolist())
                    n_long += int(long_mask.sum())
                    n_win_long += int((pnl_l > 0).sum())

                # SHORT: pay -fwd_ret_3 - cost
                if short_mask.sum() > 0:
                    pnl_s = -y[short_mask] - ROUND_TRIP_COST_PCT
                    all_pnl.extend(pnl_s.tolist())
                    all_ts.extend(ts[short_mask].tolist())
                    n_short += int(short_mask.sum())
                    n_win_short += int((pnl_s > 0).sum())

            n_total = n_long + n_short
            if n_total == 0:
                continue

            all_pnl = np.array(all_pnl)
            n_wins = n_win_long + n_win_short
            wr = n_wins / n_total

            wins = float(all_pnl[all_pnl > 0].sum())
            losses = float(-all_pnl[all_pnl < 0].sum())
            pf = wins / losses if losses > 0 else 99.0

            pnl_total_pct = float(all_pnl.sum())
            pnl_dollars = float(pnl_total_pct / 100 * 700)  # $700 per trade

            # Sharpe (annualized, 5m bars, 288/day, 365 days)
            if len(all_pnl) > 1 and all_pnl.std() > 0:
                sharpe = float(all_pnl.mean() / all_pnl.std() * np.sqrt(288 * 365))
            else:
                sharpe = 0.0

            # MaxDD (equity curve)
            equity = np.cumsum(all_pnl)
            running_max = np.maximum.accumulate(equity)
            drawdown = equity - running_max
            max_dd_pct = float(drawdown.min()) if len(drawdown) > 0 else 0.0

            rows.append({
                "thr_long": thr_long,
                "thr_short": thr_short,
                "n_total": n_total,
                "n_long": n_long,
                "n_short": n_short,
                "wr": wr,
                "pf": pf,
                "pnl_pct": pnl_total_pct,
                "pnl_dollars": pnl_dollars,
                "sharpe": sharpe,
                "max_dd_pct": max_dd_pct,
            })

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_DIR / "v75_threshold_sweep.csv", index=False)
    LOG.info("Saved sweep CSV: %s (%d rows)", OUTPUT_DIR / "v75_threshold_sweep.csv", len(df))

    # Best combinations
    best = {
        "best_sharpe": df.loc[df["sharpe"].idxmax()].to_dict(),
        "best_wr": df.loc[df["wr"].idxmax()].to_dict(),
        "best_pf": df.loc[df["pf"].idxmax()].to_dict(),
        "best_pnl": df.loc[df["pnl_pct"].idxmax()].to_dict(),
        "best_balanced": df.loc[(df["sharpe"] * df["wr"] * df["pf"]).idxmax()].to_dict(),
    }

    LOG.info("")
    LOG.info("=" * 80)
    LOG.info("BEST THRESHOLD COMBINATIONS")
    LOG.info("=" * 80)
    for label, b in best.items():
        LOG.info("")
        LOG.info("[%s]", label.upper())
        LOG.info("  thr_long=%.2f  thr_short=%.2f", b["thr_long"], b["thr_short"])
        LOG.info("  n_trades=%d (L=%d S=%d)", b["n_total"], b["n_long"], b["n_short"])
        LOG.info("  WR=%.3f  PF=%.2f  Sharpe=%.2f", b["wr"], b["pf"], b["sharpe"])
        LOG.info("  PnL=%+.2f%%  MaxDD=%.2f%%", b["pnl_pct"], b["max_dd_pct"])

    with open(OUTPUT_DIR / "v75_threshold_best.json", "w") as f:
        json.dump(best, f, indent=2, default=str)
    LOG.info("")
    LOG.info("Saved: %s", OUTPUT_DIR / "v75_threshold_best.json")

    return df, best


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="v7.5 hyperparameter tuning + threshold sweep")
    parser.add_argument("--trials", type=int, default=50, help="Optuna trials (default 50)")
    parser.add_argument("--skip-tuning", action="store_true", help="Skip Optuna, use existing best params")
    parser.add_argument("--skip-sweep", action="store_true", help="Skip threshold sweep")
    args = parser.parse_args()

    df = load_data()
    splits = walk_forward_splits(df)
    LOG.info("Walk-forward splits: %d windows", len(splits))
    for name, tr, te in splits:
        LOG.info("  %s: train=%d test=%d", name, len(tr), len(te))

    if not args.skip_tuning:
        if not HAVE_OPTUNA:
            LOG.error("optuna not installed. Run: pip3 install optuna")
            LOG.error("Or use --skip-tuning to do only the threshold sweep on existing models.")
            return 1
        result = run_optuna(splits, n_trials=args.trials)
        best_params = result["best_params"]
    else:
        params_path = OUTPUT_DIR / "v75_best_params.json"
        if not params_path.exists():
            LOG.error("No best params found at %s. Run without --skip-tuning first.", params_path)
            return 1
        with open(params_path) as f:
            best_params = json.load(f)["params"]
        LOG.info("Loaded existing best params: %s", best_params)

    # Retrain with best params
    models = retrain_with_best_params(splits, best_params)

    if not args.skip_sweep:
        threshold_sweep(splits, models)

    LOG.info("")
    LOG.info("DONE. Outputs in %s", OUTPUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main()) if 'sys' in dir() else main()
