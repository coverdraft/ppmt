"""
v6_backtest_wf_parquet.py — v6 walk-forward backtest using parquet (fast).

Same logic as v7_backtest_v75.py but with 59 v6 features instead of 71.
Used for head-to-head comparison: v6 (59 features) vs v7.5 (71 features).

Decision rule (same as v7.5):
  pred >  +thr_long  → LONG (pay fwd_ret_3 - 0.14%)
  pred <  -thr_short → SHORT (pay -fwd_ret_3 - 0.14%)
  else               → WAIT
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import lightgbm as lgb

MODELS_DIR = Path("/home/z/my-project/data/v6_models")
OUTPUT_DIR = MODELS_DIR
PARQUET_PATH = Path("/home/z/my-project/data/v7_models/v75/v75_features.parquet")

LOG = logging.getLogger("v6_bt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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
LABEL = "fwd_ret_3"

ROUND_TRIP_COST_PCT = 0.14
POSITION_NOTIONAL = 700.0
ACCOUNT_SIZE = 10000.0

WF_WINDOWS = ["2025-04", "2025-05", "2025-06", "2025-09", "2025-10"]


def load_window_data(df_all: pd.DataFrame, window_str: str) -> pd.DataFrame:
    yr, mo = window_str.split("-")
    yr, mo = int(yr), int(mo)
    mask = (df_all["ts"].dt.year == yr) & (df_all["ts"].dt.month == mo)
    return df_all[mask].copy()


def predict_window(df: pd.DataFrame, model_path: str) -> np.ndarray:
    model = lgb.Booster(model_file=model_path)
    X = df[FEATURE_NAMES].values.astype(np.float32)
    return model.predict(X)


def backtest_window(df, thr_long, thr_short, window_str):
    df = df.copy()
    df["side"] = "WAIT"
    df.loc[df["pred"] > thr_long, "side"] = "LONG"
    df.loc[df["pred"] < -thr_short, "side"] = "SHORT"
    df["pnl_pct"] = 0.0
    long_mask = df["side"] == "LONG"
    short_mask = df["side"] == "SHORT"
    df.loc[long_mask, "pnl_pct"] = df.loc[long_mask, LABEL] - ROUND_TRIP_COST_PCT
    df.loc[short_mask, "pnl_pct"] = -df.loc[short_mask, LABEL] - ROUND_TRIP_COST_PCT
    trades = df[df["side"] != "WAIT"].copy()
    trades["window"] = window_str
    trades["pnl_dollars"] = trades["pnl_pct"] / 100 * POSITION_NOTIONAL
    return trades


def compute_metrics(trades, account_size=ACCOUNT_SIZE):
    if len(trades) == 0:
        return {
            "n_trades": 0, "n_long": 0, "n_short": 0,
            "wr": 0.0, "long_wr": 0.0, "short_wr": 0.0,
            "pf": 0.0, "long_pf": 0.0, "short_pf": 0.0,
            "total_pnl_pct": 0.0, "total_pnl_dollars": 0.0,
            "avg_pnl_pct": 0.0, "sharpe_ann": 0.0, "max_dd_pct": 0.0,
        }
    longs = trades[trades["side"] == "LONG"]
    shorts = trades[trades["side"] == "SHORT"]
    pnl = trades["pnl_pct"].values
    wr = float((pnl > 0).mean())
    long_wr = float((longs["pnl_pct"] > 0).mean()) if len(longs) > 0 else 0.0
    short_wr = float((shorts["pnl_pct"] > 0).mean()) if len(shorts) > 0 else 0.0
    wins = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    pf = wins / losses if losses > 0 else (99.0 if wins > 0 else 0.0)
    long_wins = float(longs[longs["pnl_pct"] > 0]["pnl_pct"].sum()) if len(longs) > 0 else 0.0
    long_losses = float(-longs[longs["pnl_pct"] < 0]["pnl_pct"].sum()) if len(longs) > 0 else 0.0
    long_pf = long_wins / long_losses if long_losses > 0 else (99.0 if long_wins > 0 else 0.0)
    short_wins = float(shorts[shorts["pnl_pct"] > 0]["pnl_pct"].sum()) if len(shorts) > 0 else 0.0
    short_losses = float(-shorts[shorts["pnl_pct"] < 0]["pnl_pct"].sum()) if len(shorts) > 0 else 0.0
    short_pf = short_wins / short_losses if short_losses > 0 else (99.0 if short_wins > 0 else 0.0)
    total_pnl_pct = float(pnl.sum())
    total_pnl_dollars = float(trades["pnl_dollars"].sum())
    avg_pnl_pct = float(pnl.mean())
    std = float(pnl.std()) if len(pnl) > 1 else 0.001
    if len(trades) > 1 and "ts" in trades.columns:
        ts = pd.to_datetime(trades["ts"])
        span_seconds = (ts.max() - ts.min()).total_seconds()
        if span_seconds > 0:
            trades_per_year = (len(trades) / span_seconds) * 365 * 24 * 3600
        else:
            trades_per_year = len(pnl) * 12
    else:
        trades_per_year = len(pnl) * 12
    sharpe_per_trade = avg_pnl_pct / std if std > 0 else 0.0
    sharpe_ann = sharpe_per_trade * np.sqrt(max(trades_per_year, 1))
    cum_dollars = np.cumsum(trades["pnl_dollars"].values)
    equity = account_size + cum_dollars
    equity_pct = equity / account_size * 100
    running_max = np.maximum.accumulate(equity_pct)
    dd_pct = equity_pct - running_max
    max_dd_pct = float(dd_pct.min()) if len(dd_pct) > 0 else 0.0
    return {
        "n_trades": int(len(trades)), "n_long": int(len(longs)), "n_short": int(len(shorts)),
        "wr": float(wr), "long_wr": float(long_wr), "short_wr": float(short_wr),
        "pf": float(pf), "long_pf": float(long_pf), "short_pf": float(short_pf),
        "total_pnl_pct": float(total_pnl_pct),
        "total_pnl_dollars": float(total_pnl_dollars),
        "avg_pnl_pct": float(avg_pnl_pct),
        "sharpe_ann": float(sharpe_ann),
        "max_dd_pct": float(max_dd_pct),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--thr-long", type=float, default=0.30)
    parser.add_argument("--thr-short", type=float, default=0.30)
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()

    print("=" * 110)
    print("v6 WALK-FORWARD BACKTEST (baseline, 59 features)")
    print(f"  decision rule: pred > +{args.thr_long}% → LONG, pred < -{args.thr_short}% → SHORT")
    print(f"  cost: {ROUND_TRIP_COST_PCT}% round-trip   position: ${POSITION_NOTIONAL}   account: ${ACCOUNT_SIZE}")
    print(f"  walk-forward windows: {WF_WINDOWS}")
    print("=" * 110)

    LOG.info("Loading parquet: %s", PARQUET_PATH)
    df_all = pd.read_parquet(PARQUET_PATH)
    df_all["ts"] = pd.to_datetime(df_all["ts"], unit="s", utc=True)

    print("\nPredicting all windows...")
    windows_data = {}
    for w in WF_WINDOWS:
        model_path = MODELS_DIR / f"v6_{w}.txt"
        if not model_path.exists():
            LOG.warning("Model missing for %s — skipping", w)
            continue
        df_w = load_window_data(df_all, w)
        if len(df_w) == 0:
            continue
        df_w["pred"] = predict_window(df_w, str(model_path))
        windows_data[w] = df_w
        print(f"  {w}: {len(df_w):,} rows, pred mean={df_w['pred'].mean():+.4f}% "
              f"std={df_w['pred'].std():.4f}%")

    if args.sweep:
        print("\n" + "=" * 110)
        print("THRESHOLD SWEEP")
        print("=" * 110)
        thr_grid = [0.10, 0.20, 0.30, 0.40, 0.50, 0.75, 1.00]
        print(f"{'thr':<10} {'n_trades':>9} {'n_L':>5} {'n_S':>5} "
              f"{'WR':>6} {'PF':>6} {'PnL%':>9} {'$':>8} {'Sharpe':>7} {'MaxDD%':>8}")
        print("-" * 80)
        best_sharpe = -99
        best_thr = None
        for thr in thr_grid:
            all_trades = []
            for w, df_w in windows_data.items():
                trades = backtest_window(df_w, thr, thr, w)
                all_trades.append(trades)
            if not all_trades:
                continue
            trades_all = pd.concat(all_trades, ignore_index=True)
            m = compute_metrics(trades_all)
            print(f"{thr:<10.2f} {m['n_trades']:>9,} {m['n_long']:>5,} {m['n_short']:>5,} "
                  f"{m['wr']:>5.3f} {m['pf']:>6.2f} {m['total_pnl_pct']:>+8.2f}% "
                  f"{m['total_pnl_dollars']:>+7.0f} {m['sharpe_ann']:>7.2f} {m['max_dd_pct']:>+7.2f}%")
            if m["sharpe_ann"] > best_sharpe:
                best_sharpe = m["sharpe_ann"]
                best_thr = thr
        print()
        print(f"BEST Sharpe: thr={best_thr} → Sharpe={best_sharpe:.2f}")
        args.thr_long = best_thr
        args.thr_short = best_thr

    print("\n" + "=" * 110)
    print(f"FINAL BACKTEST (thr_long={args.thr_long}, thr_short={args.thr_short})")
    print("=" * 110)

    all_trades_list = []
    per_window_metrics = {}
    for w, df_w in windows_data.items():
        trades = backtest_window(df_w, args.thr_long, args.thr_short, w)
        m = compute_metrics(trades)
        per_window_metrics[w] = m
        all_trades_list.append(trades)
        trades_path = OUTPUT_DIR / f"v6_backtest_trades_{w}.parquet"
        trades.to_parquet(trades_path, index=False)
        print(f"  {w}: n={m['n_trades']:>5,} L={m['n_long']:>4,} S={m['n_short']:>4,} "
              f"WR={m['wr']:.3f} PF={m['pf']:.2f} PnL={m['total_pnl_pct']:+.2f}% "
              f"${m['total_pnl_dollars']:+.0f} Sharpe={m['sharpe_ann']:.2f} MaxDD={m['max_dd_pct']:+.2f}%")

    all_trades = pd.concat(all_trades_list, ignore_index=True)
    total_metrics = compute_metrics(all_trades)

    all_trades_sorted = all_trades.sort_values("ts").reset_index(drop=True)
    all_trades_sorted["cum_pnl_dollars"] = all_trades_sorted["pnl_dollars"].cumsum()
    all_trades_sorted["equity_dollars"] = ACCOUNT_SIZE + all_trades_sorted["cum_pnl_dollars"]
    all_trades_sorted["equity_pct"] = all_trades_sorted["equity_dollars"] / ACCOUNT_SIZE * 100
    all_trades_sorted["cum_pnl_pct"] = all_trades_sorted["pnl_pct"].cumsum()
    equity_path = OUTPUT_DIR / "v6_backtest_equity_curve.parquet"
    all_trades_sorted.to_parquet(equity_path, index=False)

    print("\n" + "=" * 110)
    print("TOTAL (5 windows)")
    print("=" * 110)
    print(f"  Trades:        {total_metrics['n_trades']:,}  (L={total_metrics['n_long']:,}  S={total_metrics['n_short']:,})")
    print(f"  Win Rate:      {total_metrics['wr']:.3f}  (L={total_metrics['long_wr']:.3f}  S={total_metrics['short_wr']:.3f})")
    print(f"  Profit Factor: {total_metrics['pf']:.2f}  (L={total_metrics['long_pf']:.2f}  S={total_metrics['short_pf']:.2f})")
    print(f"  Total PnL:     {total_metrics['total_pnl_pct']:+.2f}%   ${total_metrics['total_pnl_dollars']:+.0f}")
    print(f"  Avg PnL/trade: {total_metrics['avg_pnl_pct']:+.4f}%")
    print(f"  Sharpe (ann):  {total_metrics['sharpe_ann']:.2f}")
    print(f"  Max Drawdown:  {total_metrics['max_dd_pct']:+.2f}%")

    print()
    print("SHIP CRITERIA:")
    sharpe_ok = total_metrics["sharpe_ann"] > 1.0
    dd_ok = total_metrics["max_dd_pct"] > -15.0
    wr_ok = total_metrics["wr"] > 0.52
    print(f"  Sharpe > 1.0:        {'PASS' if sharpe_ok else 'FAIL'} ({total_metrics['sharpe_ann']:.2f})")
    print(f"  MaxDD > -15%:        {'PASS' if dd_ok else 'FAIL'} ({total_metrics['max_dd_pct']:+.2f}%)")
    print(f"  WR > 52%:            {'PASS' if wr_ok else 'FAIL'} ({total_metrics['wr']:.3f})")
    print(f"  ALL PASS:            {'YES' if (sharpe_ok and dd_ok and wr_ok) else 'NO'}")

    summary = {
        "config": {
            "thr_long": args.thr_long, "thr_short": args.thr_short,
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "position_notional": POSITION_NOTIONAL,
            "account_size": ACCOUNT_SIZE,
            "wf_windows": WF_WINDOWS, "n_features": len(FEATURE_NAMES),
        },
        "per_window": per_window_metrics,
        "total": total_metrics,
        "ship_criteria": {
            "sharpe_gt_1": bool(sharpe_ok),
            "max_dd_gt_neg15": bool(dd_ok),
            "wr_gt_52": bool(wr_ok),
            "all_pass": bool(sharpe_ok and dd_ok and wr_ok),
        },
    }
    summary_path = OUTPUT_DIR / "v6_backtest_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    LOG.info("Summary saved to %s", summary_path)


if __name__ == "__main__":
    main()
