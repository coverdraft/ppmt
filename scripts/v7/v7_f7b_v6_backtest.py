"""
v7_f7b_v6_backtest.py — F7-Option-C: Backtest v6 LONG-only baseline.

PURPOSE
-------
Per master plan §12.1, after F7 FAILed (v7 dual-expert, all 5 windows negative,
WR 36-39%), we need to verify whether v6 (single LightGBM regression on ALL
labels, no sign filter) has any directional edge.

v6's walk-forward summary shows:
  - mean_test_corr = +0.0362 (low but non-zero)
  - mean_dir_acc   = 0.5086 (above 0.4789 baseline)
  - top feature = btc_vol_z (7.0%, NO ATR dominance unlike v7's 39-55%)
  - v6 filtered backtest baseline: WR=58.4%, PF=3.94, Sharpe=4.19, ROI=8.72%

This script does a CLEAN F7-style backtest using v6 models, single-expert,
direction = sign(pred). For apples-to-apples comparison with F7.

CONFIGURATION
-------------
- v6 has 59 features (no F4 extras)
- 5 walk-forward windows (2025-04, 05, 06, 09, 10)
- v6 trained on ALL labels (no sign filter)
- Decision: LONG if pred > thr_long, SHORT if pred < -thr_short
- PnL: LONG pays fwd_ret_3 - 0.14%, SHORT pays -fwd_ret_3 - 0.14%

USAGE:
    python /home/z/my-project/scripts/v7/v7_f7b_v6_backtest.py
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import lightgbm as lgb

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

DB_PATH = "/home/z/my-project/data/ppmt.db"
V6_MODEL_DIR = Path("/home/z/my-project/data/v6_models")
OUTPUT_DIR = Path("/home/z/my-project/data/v7_models/f7b_v6_backtest")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v7_f7b_v6_backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# v6 feature set (59 features, no F4 extras)
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
FEATURE_NAMES = FEATURE_NAMES_V5 + FEATURE_NAMES_V6_NEW
assert len(FEATURE_NAMES) == 59

LABEL = "fwd_ret_3"
WF_WINDOWS = ["2025-04", "2025-05", "2025-06", "2025-09", "2025-10"]

# Cost model (same as F7, master plan §8.3)
ROUND_TRIP_COST_PCT = 0.14

# Default thresholds — v6 is a single regression so direction = sign(pred).
# A directional trade fires when |pred| > thr.
DEFAULT_THR_LONG  = 0.30
DEFAULT_THR_SHORT = 0.30   # symmetric for v6 (single model, symmetric)

CANDLES_PER_YEAR = 288 * 365


# ----------------------------------------------------------------------------
# Data loading — load v6 features directly from SQLite (no parquet cached)
# ----------------------------------------------------------------------------

def load_dataset() -> pd.DataFrame:
    """Load ALL rows from feature_observations_v6 (no sign filter)."""
    LOG.info("Loading v6 features from DB (one-time, ~1-2 min)...")
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    feat_cols = ", ".join([f"json_extract(features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES])
    sql = f"""
        SELECT symbol, ts, window, {LABEL}, {feat_cols}
        FROM feature_observations_v6
        WHERE {LABEL} IS NOT NULL
    """
    df = pd.read_sql_query(sql, conn)
    conn.close()
    for f in FEATURE_NAMES + [LABEL]:
        df[f] = pd.to_numeric(df[f], errors="coerce").replace([np.inf, -np.inf], 0).fillna(0).astype(np.float32)
    df["ts_dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df["year"]  = df["ts_dt"].dt.year
    df["month"] = df["ts_dt"].dt.month
    df["window_key"] = df["year"].astype(str) + "-" + df["month"].astype(str).str.zfill(2)
    LOG.info("  loaded %d rows in %.1fs", len(df), time.time() - t0)
    return df


def load_model(window: str) -> lgb.Booster:
    path = V6_MODEL_DIR / f"v6_{window}.txt"
    if not path.exists():
        raise FileNotFoundError(f"v6 model not found: {path}")
    LOG.info("  loaded v6 model: %s", path.name)
    return lgb.Booster(model_file=str(path))


# ----------------------------------------------------------------------------
# Backtest
# ----------------------------------------------------------------------------

def backtest_window(
    window: str,
    test_df: pd.DataFrame,
    model: lgb.Booster,
    thr_long: float,
    thr_short: float,
) -> Tuple[pd.DataFrame, Dict]:
    t0 = time.time()
    X = test_df[FEATURE_NAMES].values.astype(np.float32)
    pred = model.predict(X)

    # Decision: LONG if pred > thr_long, SHORT if pred < -thr_short
    long_mask  = pred >  thr_long
    short_mask = pred < -thr_short
    action = np.where(long_mask, "LONG",
              np.where(short_mask, "SHORT", "WAIT"))

    fwd_ret = test_df[LABEL].values
    pnl_pct = np.where(action == "LONG",  fwd_ret - ROUND_TRIP_COST_PCT,
                np.where(action == "SHORT", -fwd_ret - ROUND_TRIP_COST_PCT, 0.0))

    trades_df = pd.DataFrame({
        "symbol":     test_df["symbol"].values,
        "ts":         test_df["ts"].values,
        "ts_dt":      test_df["ts_dt"].values,
        "window":     test_df["window"].values,
        "fwd_ret_3":  fwd_ret,
        "pred":       pred,
        "action":     action,
        "pnl_pct":    pnl_pct,
    })
    trades_df = trades_df[trades_df["action"] != "WAIT"].reset_index(drop=True)

    n_total = len(test_df)
    n_long  = int((action == "LONG").sum())
    n_short = int((action == "SHORT").sum())
    n_trades = n_long + n_short

    long_trades  = trades_df[trades_df["action"] == "LONG"]
    short_trades = trades_df[trades_df["action"] == "SHORT"]

    long_wr  = (long_trades["pnl_pct"]  > 0).mean() if len(long_trades)  else 0.0
    short_wr = (short_trades["pnl_pct"] > 0).mean() if len(short_trades) else 0.0
    overall_wr = (trades_df["pnl_pct"] > 0).mean() if len(trades_df) else 0.0

    long_pnl  = float(long_trades["pnl_pct"].sum())  if len(long_trades)  else 0.0
    short_pnl = float(short_trades["pnl_pct"].sum()) if len(short_trades) else 0.0
    total_pnl = long_pnl + short_pnl

    gross_w = float(trades_df[trades_df["pnl_pct"] > 0]["pnl_pct"].sum())
    gross_l = -float(trades_df[trades_df["pnl_pct"] < 0]["pnl_pct"].sum())
    pf = gross_w / max(gross_l, 1e-9)

    avg_pnl = total_pnl / n_trades if n_trades > 0 else 0.0

    # Equity curve, MaxDD, Sharpe
    eq = trades_df.sort_values("ts").reset_index(drop=True).copy()
    eq["cum_pnl_pct"] = eq["pnl_pct"].cumsum()
    if len(eq) > 0:
        running_max = eq["cum_pnl_pct"].cummax()
        dd = eq["cum_pnl_pct"] - running_max
        max_dd_pct = float(dd.min())
        trades_per_year = n_trades * (CANDLES_PER_YEAR / max(n_total, 1))
        if eq["pnl_pct"].std() > 0:
            sharpe = float(eq["pnl_pct"].mean() / eq["pnl_pct"].std() * np.sqrt(trades_per_year))
        else:
            sharpe = 0.0
    else:
        max_dd_pct = 0.0
        sharpe = 0.0

    # Per-symbol
    per_symbol = {}
    for sym, grp in trades_df.groupby("symbol"):
        s_wins = (grp["pnl_pct"] > 0).sum()
        s_n = len(grp)
        s_pnl = float(grp["pnl_pct"].sum())
        s_pf_w = float(grp[grp["pnl_pct"] > 0]["pnl_pct"].sum())
        s_pf_l = -float(grp[grp["pnl_pct"] < 0]["pnl_pct"].sum())
        per_symbol[sym] = {
            "n_trades": int(s_n),
            "wr": float(s_wins / max(s_n, 1)),
            "pnl_pct": s_pnl,
            "pf": float(s_pf_w / max(s_pf_l, 1e-9)),
            "long_trades":  int((grp["action"] == "LONG").sum()),
            "short_trades": int((grp["action"] == "SHORT").sum()),
        }

    metrics = {
        "window": window,
        "test_candles": n_total,
        "n_long":  n_long,
        "n_short": n_short,
        "n_trades_total": n_trades,
        "long_wr":  float(long_wr),
        "short_wr": float(short_wr),
        "overall_wr": float(overall_wr),
        "long_pnl_pct":  float(long_pnl),
        "short_pnl_pct": float(short_pnl),
        "total_pnl_pct": float(total_pnl),
        "avg_pnl_per_trade_pct": float(avg_pnl),
        "profit_factor": float(pf),
        "max_drawdown_pct": float(max_dd_pct),
        "sharpe_annualized": float(sharpe),
        "per_symbol": per_symbol,
        "thr_long":  float(thr_long),
        "thr_short": float(thr_short),
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "backtest_time_seconds": float(time.time() - t0),
    }
    LOG.info(
        "[%s] candles=%d  L=%d (wr=%.3f, pnl=%+.2f%%)  S=%d (wr=%.3f, pnl=%+.2f%%)  "
        "total=%+.2f%%  PF=%.2f  Sharpe=%.2f  MaxDD=%.2f%%  (%.1fs)",
        window, n_total,
        n_long, long_wr, long_pnl,
        n_short, short_wr, short_pnl,
        total_pnl, pf, sharpe, max_dd_pct,
        time.time() - t0,
    )
    return trades_df, metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--thr-long",  type=float, default=DEFAULT_THR_LONG)
    parser.add_argument("--thr-short", type=float, default=DEFAULT_THR_SHORT)
    parser.add_argument("--windows", default=None)
    parser.add_argument("--multi-thr", action="store_true",
                        help="Test multiple thresholds to find best operating point")
    args = parser.parse_args()

    windows = args.windows.split(",") if args.windows else WF_WINDOWS

    print("=" * 80)
    print("v7 F7-Option-C — v6 LONG-only baseline backtest (single-expert, 59 features)")
    print(f"  features: {len(FEATURE_NAMES)} (no F4)")
    print(f"  cost: {ROUND_TRIP_COST_PCT:.2f}% round-trip")
    print(f"  thresholds: thr_long={args.thr_long:.2f}%  thr_short={args.thr_short:.2f}%")
    print(f"  windows: {windows}")
    print("=" * 80)

    df = load_dataset()

    # If multi-thr mode, test multiple thresholds to find best operating point
    if args.multi_thr:
        print("\n=== Multi-threshold sweep (using window 2025-04 as tuning set) ===")
        tune_df = df[df["window_key"] == "2025-04"].copy()
        if len(tune_df) == 0:
            LOG.error("No tuning data for 2025-04")
            sys.exit(1)
        model = load_model("2025-04")
        X = tune_df[FEATURE_NAMES].values.astype(np.float32)
        tune_df["pred"] = model.predict(X)

        best_pf = -1
        best_thr = None
        for thr in [0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.75, 1.00]:
            long_mask  = tune_df["pred"] >  thr
            short_mask = tune_df["pred"] < -thr
            n = long_mask.sum() + short_mask.sum()
            if n < 100:
                continue
            pnl_long  = tune_df.loc[long_mask,  LABEL] - ROUND_TRIP_COST_PCT
            pnl_short = -tune_df.loc[short_mask, LABEL] - ROUND_TRIP_COST_PCT
            pnl = pd.concat([pnl_long, pnl_short])
            wr = (pnl > 0).mean()
            pf_w = pnl[pnl > 0].sum()
            pf_l = -pnl[pnl < 0].sum()
            pf = pf_w / max(pf_l, 1e-9)
            print(f"  thr={thr:.2f}%  n={n:,}  WR={wr*100:.1f}%  PF={pf:.2f}  avg_pnl={pnl.mean():+.4f}%  tot={pnl.sum():+.2f}%")
            if pf > best_pf:
                best_pf = pf
                best_thr = thr
        print(f"\n  → best thr={best_thr:.2f}%  (PF={best_pf:.2f})")
        args.thr_long = best_thr
        args.thr_short = best_thr
        print(f"  Using thr_long={args.thr_long} thr_short={args.thr_short} for full backtest\n")

    # Main backtest
    all_results = []
    t_start = time.time()
    for window in windows:
        if window not in df["window_key"].unique():
            LOG.warning("Window %s not in dataset — skipping", window)
            continue
        test_df = df[df["window_key"] == window].copy()
        LOG.info("=== Window %s: %d test candles ===", window, len(test_df))
        try:
            model = load_model(window)
        except FileNotFoundError as e:
            LOG.error("%s — skipping", e)
            continue

        trades_df, metrics = backtest_window(
            window, test_df, model,
            thr_long=args.thr_long, thr_short=args.thr_short,
        )
        all_results.append(metrics)

        # Save per-window
        trades_df.to_parquet(OUTPUT_DIR / f"v6_trades_{window}.parquet", index=False)
        eq = trades_df.sort_values("ts").reset_index(drop=True).copy()
        eq["cum_pnl_pct"] = eq["pnl_pct"].cumsum()
        eq[["ts", "ts_dt", "symbol", "action", "pnl_pct", "cum_pnl_pct"]].to_parquet(
            OUTPUT_DIR / f"v6_equity_curve_{window}.parquet", index=False
        )

    if not all_results:
        LOG.error("No results produced.")
        sys.exit(1)

    # Aggregate
    agg = {
        "n_windows": len(all_results),
        "total_trades":  sum(r["n_trades_total"] for r in all_results),
        "total_long":    sum(r["n_long"]  for r in all_results),
        "total_short":   sum(r["n_short"] for r in all_results),
        "total_pnl_pct": sum(r["total_pnl_pct"] for r in all_results),
        "long_pnl_pct":  sum(r["long_pnl_pct"]  for r in all_results),
        "short_pnl_pct": sum(r["short_pnl_pct"] for r in all_results),
        "mean_long_wr":  float(np.mean([r["long_wr"]  for r in all_results])),
        "mean_short_wr": float(np.mean([r["short_wr"] for r in all_results])),
        "mean_overall_wr": float(np.mean([r["overall_wr"] for r in all_results])),
        "mean_pf":       float(np.mean([r["profit_factor"] for r in all_results])),
        "mean_sharpe":   float(np.mean([r["sharpe_annualized"] for r in all_results])),
        "min_sharpe":    float(min(r["sharpe_annualized"] for r in all_results)),
        "max_drawdown_pct": float(min(r["max_drawdown_pct"] for r in all_results)),
        "mean_max_drawdown_pct": float(np.mean([r["max_drawdown_pct"] for r in all_results])),
    }
    short_wr = agg["mean_short_wr"]
    if short_wr > 0.55:
        checkpoint = "EXCELLENT — continue F8-F13"
    elif short_wr > 0.52:
        checkpoint = "ACCEPTABLE — continue but monitor in F11"
    elif short_wr > 0.50:
        checkpoint = "MARGINAL — investigate per-sector, per-window"
    else:
        checkpoint = "FAIL — SHORT not unlocked, fall back to v6 LONG-only"
    agg["short_wr_checkpoint"] = checkpoint

    ship = (
        "SHIP v6 as production fallback"
        if (agg["mean_sharpe"] > 1.0 and agg["max_drawdown_pct"] > -15.0 and agg["mean_overall_wr"] > 0.52)
        else "DO NOT SHIP — needs redesign"
    )
    agg["ship_decision"] = ship

    per_symbol_total = {}
    for r in all_results:
        for sym, s in r["per_symbol"].items():
            if sym not in per_symbol_total:
                per_symbol_total[sym] = {"n_trades": 0, "pnl_pct": 0.0,
                                          "long_trades": 0, "short_trades": 0, "wins": 0}
            per_symbol_total[sym]["n_trades"]     += s["n_trades"]
            per_symbol_total[sym]["pnl_pct"]      += s["pnl_pct"]
            per_symbol_total[sym]["long_trades"]  += s["long_trades"]
            per_symbol_total[sym]["short_trades"] += s["short_trades"]
            per_symbol_total[sym]["wins"]         += int(s["wr"] * s["n_trades"])
    for sym in per_symbol_total:
        s = per_symbol_total[sym]
        s["wr"] = s["wins"] / max(s["n_trades"], 1)
    agg["per_symbol_total"] = per_symbol_total

    summary_path = OUTPUT_DIR / "v6_backtest_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "config": {
                "features": len(FEATURE_NAMES),
                "feature_set": "v6 (59 base, no F4)",
                "model": "v6 single LightGBM regression on all labels",
                "cost_pct": ROUND_TRIP_COST_PCT,
                "thr_long": args.thr_long,
                "thr_short": args.thr_short,
                "windows": windows,
            },
            "per_window_results": all_results,
            "aggregate": agg,
            "ship_criteria": {
                "sharpe_gt_1":  agg["mean_sharpe"] > 1.0,
                "max_dd_gt_neg15": agg["max_drawdown_pct"] > -15.0,
                "overall_wr_gt_52": agg["mean_overall_wr"] > 0.52,
            },
            "total_runtime_seconds": time.time() - t_start,
        }, f, indent=2)
    print(f"\nSummary saved: {summary_path}")

    # Print summary
    print("\n" + "=" * 100)
    print("v6 BASELINE BACKTEST — WALK-FORWARD (5 windows)")
    print("=" * 100)
    print(f"{'window':<10} {'candles':>8} {'L':>5} {'S':>5} {'L_wr':>6} {'S_wr':>6} "
          f"{'L_pnl%':>8} {'S_pnl%':>8} {'tot_pnl%':>9} {'PF':>5} {'Sharpe':>7} {'MaxDD%':>8}")
    for r in all_results:
        print(f"{r['window']:<10} {r['test_candles']:>8,} {r['n_long']:>5,} {r['n_short']:>5,} "
              f"{r['long_wr']*100:>5.1f}% {r['short_wr']*100:>5.1f}% "
              f"{r['long_pnl_pct']:>+8.2f} {r['short_pnl_pct']:>+8.2f} "
              f"{r['total_pnl_pct']:>+9.2f} {r['profit_factor']:>5.2f} "
              f"{r['sharpe_annualized']:>7.2f} {r['max_drawdown_pct']:>8.2f}")
    print("-" * 100)
    print(f"{'TOTAL':<10} {agg['total_trades']:>8,} "
          f"{'':>5} {'':>5} {agg['mean_long_wr']*100:>5.1f}% {agg['mean_short_wr']*100:>5.1f}% "
          f"{agg['long_pnl_pct']:>+8.2f} {agg['short_pnl_pct']:>+8.2f} "
          f"{agg['total_pnl_pct']:>+9.2f} {agg['mean_pf']:>5.2f} "
          f"{agg['mean_sharpe']:>7.2f} {agg['max_drawdown_pct']:>8.2f}")
    print("=" * 100)
    print(f"Ship decision (Sharpe>1.0 & MaxDD>-15% & WR>52%): {agg['ship_decision']}")
    print()

    # Per-symbol breakdown
    print("=== Per-symbol aggregate (all windows) ===")
    print(f"{'symbol':<10} {'trades':>7} {'L':>5} {'S':>5} {'WR':>6} {'PnL%':>9}")
    for sym in sorted(per_symbol_total.keys()):
        s = per_symbol_total[sym]
        print(f"{sym:<10} {s['n_trades']:>7,} {s['long_trades']:>5,} {s['short_trades']:>5,} "
              f"{s['wr']*100:>5.1f}% {s['pnl_pct']:>+9.2f}")

    print(f"\nTotal runtime: {time.time() - t_start:.1f}s")


if __name__ == "__main__":
    main()
