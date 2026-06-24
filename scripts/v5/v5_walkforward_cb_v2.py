#!/usr/bin/env python3
"""
v5_walkforward_cb_v2.py — Walk-forward validation of the cb_v2 LGBM model.

Question this answers: is the +12,272% backtest result a stable edge,
or an artifact of one lucky OOS window?

Method:
  1. Split RANGE_2025 + RECENT_2026 (180 days total) into 6 monthly windows.
  2. For each window:
       - Train a fresh LGBM on ALL data BEFORE the window start.
       - Predict on the window.
       - Compute AUC, precision@0.70, profit factor, total PnL.
  3. Compare per-window metrics to the published RECENT_2026 metrics.
  4. If AUC drops >0.05 vs published → recommend retrain cadence.

Window dates (UTC, derived from actual data ts ranges):
  Window 1: RANGE_2025 month 1 (≈ 2024-12-22 → 2025-01-21)
  Window 2: RANGE_2025 month 2 (≈ 2025-01-22 → 2025-02-21)
  Window 3: RANGE_2025 month 3 (≈ 2025-02-22 → 2025-03-24)
  Window 4: RECENT_2026 month 1 (≈ 2025-03-24 → 2025-04-23)
  Window 5: RECENT_2026 month 2 (≈ 2025-04-23 → 2025-05-23)
  Window 6: RECENT_2026 month 3 (≈ 2025-05-23 → 2025-06-22)

For window N, training data = all observations with ts < window_N.ts_start
(that includes BULL_2024 + BEAR_2022 + all preceding windows).

Outputs:
  /home/z/my-project/download/v5_walkforward_cb_v2.json
  /home/z/my-project/download/v5_walkforward_cb_v2_summary.txt
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except ImportError:
    print("ERROR: lightgbm not installed")
    sys.exit(1)

LOG = logging.getLogger("v5_walkforward_cb_v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB = "/home/z/my-project/data/ppmt.db"
OUT_JSON = Path("/home/z/my-project/download/v5_walkforward_cb_v2.json")
OUT_TXT = Path("/home/z/my-project/download/v5_walkforward_cb_v2_summary.txt")

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

# Bar-level SL/TP (cb_v2 label)
TP_RETURN = 0.6
SL_RETURN = -0.4
TAKER_FEE_PCT = 0.05
SLIPPAGE_PCT = 0.02
BASE_LEVERAGE = 7


def load_all_observations() -> pd.DataFrame:
    """Load all labeled observations from feature_observations_cb."""
    conn = sqlite3.connect(DB, timeout=30)
    sql = """
        SELECT symbol, timeframe, ts, pattern_hash,
               historical_regime, runtime_regime, asset_class,
               features_json, prior_win_rate, prior_expected_move, prior_count,
               label_win, label_pnl, label_max_fav, label_max_adv, label_hit_tp_first
        FROM feature_observations_cb
        WHERE label_hit_tp_first IS NOT NULL
        ORDER BY ts ASC
    """
    rows = conn.execute(sql).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()

    cols = ["symbol", "timeframe", "ts", "pattern_hash",
            "historical_regime", "runtime_regime", "asset_class",
            "features_json", "prior_win_rate", "prior_expected_move", "prior_count",
            "label_win", "label_pnl", "label_max_fav", "label_max_adv", "label_hit_tp_first"]
    df = pd.DataFrame(rows, columns=cols)

    fe = pd.json_normalize(df["features_json"].apply(json.loads))
    for f in FEATURE_NAMES:
        if f not in fe.columns:
            fe[f] = 0.0
    df = pd.concat([df.drop(columns=["features_json"]).reset_index(drop=True),
                    fe[FEATURE_NAMES].reset_index(drop=True)], axis=1)

    # Edge features (same as trainer)
    hour = pd.to_datetime(df["ts"], unit="s", utc=True).dt.hour
    asia = hour.isin([0, 1, 2, 18, 19, 20, 21, 22, 23])
    alt = ~df["asset_class"].isin(["blue_chip"])
    scalp = df["timeframe"].isin(["1m", "5m", "15m"])
    df["edge_strong"] = (alt & scalp & asia).astype(int)
    score = alt.astype(int) + scalp.astype(int) + asia.astype(int)
    df["edge_marginal"] = ((score == 2) & ~df["edge_strong"]).astype(int)
    return df


def define_windows(df: pd.DataFrame) -> list[dict]:
    """Define 6 monthly walk-forward windows covering RANGE_2025 + RECENT_2026.

    Window boundaries are derived from actual ts range of the two regimes.
    """
    # Filter to RANGE_2025 + RECENT_2026 (the OOS period we want to walk-forward)
    oos = df[df["historical_regime"].isin(["RANGE_2025", "RECENT_2026"])].copy()
    if len(oos) == 0:
        return []

    ts_min = int(oos["ts"].min())
    ts_max = int(oos["ts"].max())
    total_days = (ts_max - ts_min) / 86400
    LOG.info("OOS range: %s → %s (%.1f days)",
             pd.to_datetime(ts_min, unit="s", utc=True),
             pd.to_datetime(ts_max, unit="s", utc=True),
             total_days)

    # 6 equal windows
    n_windows = 6
    window_span = (ts_max - ts_min) // n_windows
    windows = []
    for i in range(n_windows):
        ts_start = ts_min + i * window_span
        ts_end = ts_min + (i + 1) * window_span if i < n_windows - 1 else ts_max + 1
        # Identify regime(s) in window
        sub = oos[(oos["ts"] >= ts_start) & (oos["ts"] < ts_end)]
        regimes = sorted(sub["historical_regime"].unique().tolist())
        windows.append({
            "window_idx": i + 1,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "date_start": pd.to_datetime(ts_start, unit="s", utc=True).isoformat(),
            "date_end": pd.to_datetime(ts_end, unit="s", utc=True).isoformat(),
            "regimes": regimes,
            "n_obs": len(sub),
        })
    return windows


def train_lgbm(X_train, y_train, X_valid, y_valid) -> tuple[lgb.Booster, int, float]:
    """Train one LGBM with same params as v5_train_lgbm_cb_v2.py."""
    train_set = lgb.Dataset(X_train, label=y_train, feature_name=FEATURE_NAMES + ["edge_strong", "edge_marginal"])
    valid_set = lgb.Dataset(X_valid, label=y_valid, feature_name=FEATURE_NAMES + ["edge_strong", "edge_marginal"], reference=train_set)
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
        valid_sets=[train_set, valid_set],
        valid_names=["train", "valid"],
        callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)],
    )
    train_time = time.time() - t0
    return model, model.best_iteration, train_time


def compute_window_metrics(y, p, label_pnl, threshold=0.70) -> dict:
    """Compute AUC, precision@threshold, profit factor, total PnL for one window."""
    from sklearn.metrics import roc_auc_score

    if len(y) == 0:
        return {"n_obs": 0}

    auc = float(roc_auc_score(y, p))
    pred = (p >= threshold).astype(int)
    n_signals = int(pred.sum())
    if n_signals == 0:
        return {
            "n_obs": len(y),
            "auc": auc,
            "n_signals": 0,
            "precision@thr": None,
            "pf": None,
            "total_net_pnl_pct": None,
            "avg_net_pnl_pct": None,
        }

    precision = float((pred & y).sum() / pred.sum())

    # Simulate trade PnL per signal (LONG, 7x lev, costs)
    pnls = []
    for i in np.where(pred == 1)[0]:
        hit_tp = y[i]
        lpnl = label_pnl[i]
        if hit_tp == 1:
            gross = TP_RETURN * BASE_LEVERAGE
        elif hit_tp == 0:
            gross = SL_RETURN * BASE_LEVERAGE
        else:
            gross = float(lpnl) * BASE_LEVERAGE
        fee = TAKER_FEE_PCT * BASE_LEVERAGE * 2
        slip = SLIPPAGE_PCT * BASE_LEVERAGE * 2
        net = gross - fee - slip
        pnls.append(net)

    pnls = np.array(pnls)
    gw = pnls[pnls > 0].sum()
    gl = -pnls[pnls < 0].sum()
    pf = float(gw / gl) if gl > 0 else float("inf")
    total = float(pnls.sum())
    avg = float(pnls.mean())

    return {
        "n_obs": len(y),
        "auc": auc,
        "n_signals": n_signals,
        "precision@thr": precision,
        "pf": pf,
        "total_net_pnl_pct": total,
        "avg_net_pnl_pct": avg,
    }


def main():
    LOG.info("=== v5 walk-forward validation (cb_v2) ===")
    LOG.info("DB: %s", DB)

    df = load_all_observations()
    LOG.info("Loaded %d labeled observations total", len(df))
    LOG.info("By regime:")
    for r, n in df.groupby("historical_regime").size().items():
        LOG.info("  %s: %d", r, n)

    windows = define_windows(df)
    LOG.info("Defined %d walk-forward windows:", len(windows))
    for w in windows:
        LOG.info("  W%d: %s → %s  regimes=%s  n_obs=%d",
                 w["window_idx"], w["date_start"][:10], w["date_end"][:10],
                 w["regimes"], w["n_obs"])

    feature_cols = FEATURE_NAMES + ["edge_strong", "edge_marginal"]
    results = {"windows": [], "config": {
        "threshold": 0.70,
        "tp_return": TP_RETURN,
        "sl_return": SL_RETURN,
        "taker_fee_pct": TAKER_FEE_PCT,
        "slippage_pct": SLIPPAGE_PCT,
        "base_leverage": BASE_LEVERAGE,
        "n_windows": len(windows),
    }}

    for w in windows:
        LOG.info("")
        LOG.info("=== Window W%d: %s → %s ===", w["window_idx"], w["date_start"][:10], w["date_end"][:10])

        # Train = all obs with ts < window.ts_start
        train_df = df[df["ts"] < w["ts_start"]].copy()
        # Valid = use last 20% of train chronologically (for early stopping)
        train_ts_max = int(train_df["ts"].max())
        train_ts_cutoff = int(train_df["ts"].quantile(0.8))
        valid_for_es = train_df[train_df["ts"] >= train_ts_cutoff].copy()
        train_for_fit = train_df[train_df["ts"] < train_ts_cutoff].copy()

        # Test = window
        test_df = df[(df["ts"] >= w["ts_start"]) & (df["ts"] < w["ts_end"])].copy()

        LOG.info("  train_for_fit: %d  valid_for_es: %d  test: %d",
                 len(train_for_fit), len(valid_for_es), len(test_df))

        if len(train_for_fit) < 1000 or len(valid_for_es) < 100 or len(test_df) < 100:
            LOG.warning("  Skipping window — insufficient data")
            results["windows"].append({
                **w,
                "skipped": True,
                "reason": "insufficient data",
            })
            continue

        X_train = train_for_fit[feature_cols].values
        y_train = train_for_fit["label_hit_tp_first"].astype(int).values
        X_valid = valid_for_es[feature_cols].values
        y_valid = valid_for_es["label_hit_tp_first"].astype(int).values
        X_test = test_df[feature_cols].values
        y_test = test_df["label_hit_tp_first"].astype(int).values

        LOG.info("  Train baseline win rate: %.3f", y_train.mean())
        LOG.info("  Test baseline win rate:  %.3f", y_test.mean())

        model, best_iter, train_time = train_lgbm(X_train, y_train, X_valid, y_valid)
        LOG.info("  Trained in %.1fs, best_iter=%d", train_time, best_iter)

        p_test = model.predict(X_test)
        label_pnl_test = test_df["label_pnl"].fillna(0).values

        m = compute_window_metrics(y_test, p_test, label_pnl_test, threshold=0.70)
        LOG.info("  Results: AUC=%.4f  signals=%d  prec@0.70=%.4f  PF=%.2f  totalPnL=%.1f%%  avgPnL=%.3f%%",
                 m["auc"], m["n_signals"], m.get("precision@thr") or 0,
                 m.get("pf") or 0, m.get("total_net_pnl_pct") or 0, m.get("avg_net_pnl_pct") or 0)

        # Per-symbol breakdown at threshold 0.70
        test_df = test_df.copy()
        test_df["proba"] = p_test
        sig = test_df[test_df["proba"] >= 0.70].copy()
        per_symbol = {}
        for sym, sub in sig.groupby("symbol"):
            sub_y = sub["label_hit_tp_first"].astype(int).values
            sub_pnl = sub["label_pnl"].fillna(0).values
            sub_m = compute_window_metrics(sub_y, sub["proba"].values, sub_pnl, threshold=0.70)
            per_symbol[sym] = {
                "n_signals": sub_m["n_signals"],
                "precision": sub_m.get("precision@thr"),
                "pf": sub_m.get("pf"),
                "avg_pnl_pct": sub_m.get("avg_net_pnl_pct"),
            }

        # Per-tf breakdown
        per_tf = {}
        for tf, sub in sig.groupby("timeframe"):
            sub_y = sub["label_hit_tp_first"].astype(int).values
            sub_pnl = sub["label_pnl"].fillna(0).values
            sub_m = compute_window_metrics(sub_y, sub["proba"].values, sub_pnl, threshold=0.70)
            per_tf[tf] = {
                "n_signals": sub_m["n_signals"],
                "precision": sub_m.get("precision@thr"),
                "pf": sub_m.get("pf"),
                "avg_pnl_pct": sub_m.get("avg_net_pnl_pct"),
            }

        results["windows"].append({
            **w,
            "skipped": False,
            "train_rows": len(train_for_fit),
            "valid_rows": len(valid_for_es),
            "test_rows": len(test_df),
            "train_baseline_win_rate": float(y_train.mean()),
            "test_baseline_win_rate": float(y_test.mean()),
            "best_iter": int(best_iter) if best_iter else 0,
            "train_time_sec": round(train_time, 2),
            "metrics@0.70": m,
            "per_symbol@0.70": per_symbol,
            "per_tf@0.70": per_tf,
        })

    # Compute summary stats
    valid_windows = [w for w in results["windows"] if not w.get("skipped", False)]
    if valid_windows:
        aucs = [w["metrics@0.70"]["auc"] for w in valid_windows]
        precs = [w["metrics@0.70"].get("precision@thr") or 0 for w in valid_windows]
        pfs = [w["metrics@0.70"].get("pf") or 0 for w in valid_windows]
        pnls = [w["metrics@0.70"].get("total_net_pnl_pct") or 0 for w in valid_windows]
        results["summary"] = {
            "n_windows_evaluated": len(valid_windows),
            "auc_mean": float(np.mean(aucs)),
            "auc_std": float(np.std(aucs)),
            "auc_min": float(np.min(aucs)),
            "auc_max": float(np.max(aucs)),
            "precision_mean": float(np.mean(precs)),
            "precision_std": float(np.std(precs)),
            "pf_mean": float(np.mean(pfs)),
            "pf_min": float(np.min(pfs)),
            "total_pnl_sum_pct": float(np.sum(pnls)),
            "total_pnl_mean_pct": float(np.mean(pnls)),
            # Stability flags
            "auc_drop_from_first_to_last": float(aucs[0] - aucs[-1]) if len(aucs) >= 2 else 0.0,
            "any_window_auc_drop_gt_0.05": any(abs(a - aucs[0]) > 0.05 for a in aucs),
        }

    # Write summary
    lines = []
    lines.append("=" * 110)
    lines.append("V5 WALK-FORWARD VALIDATION — cb_v2 LGBM (6 monthly windows on RANGE_2025 + RECENT_2026)")
    lines.append("=" * 110)
    lines.append(f"Config: thr=0.70, lev={BASE_LEVERAGE}x, TP=+{TP_RETURN}% SL={SL_RETURN}% "
                 f"fee={TAKER_FEE_PCT}%*2 slip={SLIPPAGE_PCT}%*2")
    lines.append("")
    lines.append(f"{'W':<3} {'Date Start':<12} {'Date End':<12} {'Regime':<14} "
                 f"{'Train':>7} {'Test':>7} {'AUC':>7} {'Signals':>8} {'Prec':>6} {'PF':>7} {'TotalPnL%':>11} {'AvgPnL%':>8}")
    lines.append("-" * 110)
    for w in results["windows"]:
        if w.get("skipped"):
            lines.append(f"W{w['window_idx']:<2} {w['date_start'][:10]:<12} {w['date_end'][:10]:<12} "
                         f"{'SKIPPED':<14} — {w.get('reason', '')}")
            continue
        m = w["metrics@0.70"]
        regime_str = "+".join(w["regimes"])
        lines.append(
            f"W{w['window_idx']:<2} {w['date_start'][:10]:<12} {w['date_end'][:10]:<12} "
            f"{regime_str:<14} {w['train_rows']:>7} {w['test_rows']:>7} "
            f"{m['auc']:>7.4f} {m['n_signals']:>8} "
            f"{(m.get('precision@thr') or 0):>6.3f} {(m.get('pf') or 0):>7.2f} "
            f"{(m.get('total_net_pnl_pct') or 0):>+11.1f} {(m.get('avg_net_pnl_pct') or 0):>+8.3f}"
        )

    if "summary" in results:
        s = results["summary"]
        lines.append("")
        lines.append("=" * 110)
        lines.append("STABILITY SUMMARY")
        lines.append("=" * 110)
        lines.append(f"Windows evaluated:    {s['n_windows_evaluated']}")
        lines.append(f"AUC mean ± std:       {s['auc_mean']:.4f} ± {s['auc_std']:.4f}")
        lines.append(f"AUC range:            [{s['auc_min']:.4f}, {s['auc_max']:.4f}]")
        lines.append(f"AUC drop W1→W6:       {s['auc_drop_from_first_to_last']:+.4f}")
        lines.append(f"Precision mean ± std: {s['precision_mean']:.4f} ± {s['precision_std']:.4f}")
        lines.append(f"PF mean (min):        {s['pf_mean']:.2f} ({s['pf_min']:.2f})")
        lines.append(f"Total PnL sum:        {s['total_pnl_sum_pct']:+.1f}% of margin")
        lines.append(f"Total PnL mean/win:   {s['total_pnl_mean_pct']:+.1f}% of margin")
        lines.append("")
        if s["any_window_auc_drop_gt_0.05"]:
            lines.append("⚠ AUC drops >0.05 in at least one window — model edge may not be stable.")
            lines.append("  RECOMMENDATION: deploy with weekly retrain cadence and monitor AUC drift.")
        else:
            lines.append("✓ AUC stable across windows — model edge appears robust to regime changes.")
            lines.append("  RECOMMENDATION: deploy with monthly retrain cadence (or alert when AUC drops >0.05).")
        lines.append("")
        # Profitability check
        losing_windows = [w for w in valid_windows if (w["metrics@0.70"].get("total_net_pnl_pct") or 0) < 0]
        if not losing_windows:
            lines.append("✓ ALL windows profitable — no regime produced net losses.")
        else:
            lines.append(f"⚠ {len(losing_windows)} window(s) had net negative PnL:")
            for w in losing_windows:
                lines.append(f"    W{w['window_idx']} ({w['date_start'][:10]} → {w['date_end'][:10]}): "
                             f"{w['metrics@0.70'].get('total_net_pnl_pct'):+.1f}%")

    summary = "\n".join(lines)
    print(summary)

    OUT_TXT.write_text(summary)
    OUT_JSON.write_text(json.dumps(results, indent=2, default=str))
    LOG.info("Saved: %s", OUT_TXT)
    LOG.info("Saved: %s", OUT_JSON)


if __name__ == "__main__":
    main()
