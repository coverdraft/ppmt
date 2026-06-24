"""
v7_f7_backtest.py — F7: Walk-forward backtest with dual experts (LONG+SHORT F5).

WHAT THIS DOES
--------------
Per PPMT_v7_MASTER_PLAN.md §5.5 (Decision layer) and §12.1 (F7 checkpoint):
  - Load F5a LONG expert + F5b SHORT expert (71 features each, no trie)
  - For each walk-forward test window (2025-04, 05, 06, 09, 10):
    * Predict pred_long and pred_short on EVERY candle (not just sign-filtered)
    * Apply decision rule: pick the side with higher conviction if it clears thr
        LONG  if pred_long  > thr_long  AND pred_long  > |pred_short|
        SHORT if |pred_short| > thr_short AND |pred_short| >  pred_long
        WAIT  otherwise
    * Hold 15 minutes (3 × 5m candles = fwd_ret_3 horizon)
    * PnL per trade = |actual move in predicted direction| - 0.14% round-trip cost
    * Aggregate: trades, WR, PF, total PnL %, Sharpe (annualized 5m), MaxDD
  - Apply §12.1 SHORT WR checkpoint: < 50% → SHORT not unlocked, fall back to v6
  - Per-symbol breakdown to see which tokens drive PnL

CRITICAL: No leakage. Each window's models were trained ONLY on data BEFORE
that window (verified by F5a/F5b walk_forward_splits). We use them as-is.

OUTPUTS:
  - data/v7_models/f7_backtest/v7_f7_backtest_summary.json
  - data/v7_models/f7_backtest/v7_f7_trades_{window}.parquet  (per-trade detail)
  - data/v7_models/f7_backtest/v7_f7_equity_curve_{window}.parquet

USAGE:
    python /home/z/my-project/scripts/v7/v7_f7_backtest.py
    python /home/z/my-project/scripts/v7/v7_f7_backtest.py --thr-long 0.35 --thr-short 0.45
"""
from __future__ import annotations

import argparse
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

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

LONG_PARQUET  = Path("/home/z/my-project/data/v7_models/long_expert/long_features.parquet")
SHORT_PARQUET = Path("/home/z/my-project/data/v7_models/short_expert/short_features.parquet")
LONG_MODEL_DIR  = Path("/home/z/my-project/data/v7_models/long_expert")
SHORT_MODEL_DIR = Path("/home/z/my-project/data/v7_models/short_expert")
OUTPUT_DIR = Path("/home/z/my-project/data/v7_models/f7_backtest")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v7_f7_backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# 71 features (must match v7_train_long_expert.py / v7_train_short_expert.py)
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
FEATURE_NAMES_F4 = [
    "funding_rate", "funding_rate_z",
    "oi_change_1h", "oi_change_4h",
    "sector_blue_chip", "sector_large_cap", "sector_old_meme", "sector_new_meme",
    "sector_idx",
    "day_of_week_sin", "day_of_week_cos", "day_of_week",
]
FEATURE_NAMES = FEATURE_NAMES_V5 + FEATURE_NAMES_V6_NEW + FEATURE_NAMES_F4
assert len(FEATURE_NAMES) == 71

LABEL = "fwd_ret_3"

WF_WINDOWS = ["2025-04", "2025-05", "2025-06", "2025-09", "2025-10"]

# Cost model (master plan §8.3)
ROUND_TRIP_COST_PCT = 0.14   # 0.14% per round-trip (7bps each side + slippage)

# Default thresholds (master plan §5.5)
DEFAULT_THR_LONG  = 0.30
DEFAULT_THR_SHORT = 0.40

# Annualization: 5m candles → 288/day → 365*288 = 105,120 candles/year
CANDLES_PER_YEAR = 288 * 365


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------

def load_unified_test_set() -> pd.DataFrame:
    """Load LONG + SHORT parquets and union them into a single df.

    LONG parquet  contains rows where fwd_ret_3 > 0
    SHORT parquet contains rows where fwd_ret_3 < 0
    They are disjoint (verified). Union covers all candle observations.

    Both parquets share the same 71 features + (symbol, ts, window, fwd_ret_3).
    """
    LOG.info("Loading LONG parquet: %s", LONG_PARQUET)
    t0 = time.time()
    lf = pd.read_parquet(LONG_PARQUET, columns=FEATURE_NAMES + ["symbol", "ts", "window", LABEL])
    LOG.info("  LONG rows: %d (%.1fs)", len(lf), time.time() - t0)

    LOG.info("Loading SHORT parquet: %s", SHORT_PARQUET)
    t0 = time.time()
    sf = pd.read_parquet(SHORT_PARQUET, columns=FEATURE_NAMES + ["symbol", "ts", "window", LABEL])
    LOG.info("  SHORT rows: %d (%.1fs)", len(sf), time.time() - t0)

    df = pd.concat([lf, sf], ignore_index=True)
    del lf, sf
    df["ts_dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df["year"]  = df["ts_dt"].dt.year
    df["month"] = df["ts_dt"].dt.month
    df["window_key"] = df["year"].astype(str) + "-" + df["month"].astype(str).str.zfill(2)
    LOG.info("Unified set: %d rows, %d symbols, windows=%s",
             len(df), df["symbol"].nunique(),
             sorted(df["window_key"].unique().tolist()))
    return df


def load_models(window: str) -> Tuple[lgb.Booster, lgb.Booster]:
    """Load F5a LONG and F5b SHORT models for the given walk-forward window."""
    long_path  = LONG_MODEL_DIR  / f"v7_long_expert_{window}.txt"
    short_path = SHORT_MODEL_DIR / f"v7_short_expert_{window}.txt"
    if not long_path.exists():
        raise FileNotFoundError(f"LONG model not found: {long_path}")
    if not short_path.exists():
        raise FileNotFoundError(f"SHORT model not found: {short_path}")
    long_model  = lgb.Booster(model_file=str(long_path))
    short_model = lgb.Booster(model_file=str(short_path))
    LOG.info("  loaded LONG  model: %s", long_path.name)
    LOG.info("  loaded SHORT model: %s", short_path.name)
    return long_model, short_model


# ----------------------------------------------------------------------------
# Backtest logic
# ----------------------------------------------------------------------------

def backtest_window(
    window: str,
    test_df: pd.DataFrame,
    long_model: lgb.Booster,
    short_model: lgb.Booster,
    thr_long: float,
    thr_short: float,
) -> Tuple[pd.DataFrame, Dict]:
    """Run dual-expert backtest on a single walk-forward test window.

    Returns:
        trades_df: per-trade detail (one row per signal triggered)
        metrics: aggregate metrics dict
    """
    t0 = time.time()
    X = test_df[FEATURE_NAMES].values.astype(np.float32)

    pred_long  = long_model.predict(X)
    pred_short = short_model.predict(X)   # SHORT expert trained on LABEL < 0 → pred ≤ 0 typically
    pred_short_abs = np.abs(pred_short)

    # Decision rule (master plan §5.5)
    # LONG  if pred_long  > thr_long  AND pred_long  > pred_short_abs
    # SHORT if pred_short_abs > thr_short AND pred_short_abs > pred_long
    # WAIT  otherwise
    long_mask  = (pred_long  > thr_long)  & (pred_long  > pred_short_abs)
    short_mask = (pred_short_abs > thr_short) & (pred_short_abs > pred_long)
    # If both somehow trigger (shouldn't due to ">"), prefer higher conviction —
    # the AND conditions above already enforce mutual exclusion by requiring
    # each side to be strictly greater than the other, but for safety we
    # give priority to neither (LONG wins ties → first mask wins, second overridden)
    action = np.where(long_mask, "LONG",
              np.where(short_mask, "SHORT", "WAIT"))

    # PnL per candle
    # LONG  PnL = fwd_ret_3           - cost   (we hold long, profit if price rises)
    # SHORT PnL = -fwd_ret_3          - cost   (we hold short, profit if price falls)
    # WAIT  PnL = 0
    fwd_ret = test_df[LABEL].values
    pnl_pct = np.where(action == "LONG",  fwd_ret - ROUND_TRIP_COST_PCT,
                np.where(action == "SHORT", -fwd_ret - ROUND_TRIP_COST_PCT, 0.0))

    trades_df = pd.DataFrame({
        "symbol":     test_df["symbol"].values,
        "ts":         test_df["ts"].values,
        "ts_dt":      test_df["ts_dt"].values,
        "window":     test_df["window"].values,
        "fwd_ret_3":  fwd_ret,
        "pred_long":  pred_long,
        "pred_short": pred_short,
        "pred_short_abs": pred_short_abs,
        "action":     action,
        "pnl_pct":    pnl_pct,
    })
    trades_df = trades_df[trades_df["action"] != "WAIT"].reset_index(drop=True)

    # Aggregate metrics
    n_total_candles = len(test_df)
    n_long  = int((action == "LONG").sum())
    n_short = int((action == "SHORT").sum())
    n_wait  = int((action == "WAIT").sum())
    n_trades = n_long + n_short

    long_trades  = trades_df[trades_df["action"] == "LONG"]
    short_trades = trades_df[trades_df["action"] == "SHORT"]

    long_wins  = (long_trades["pnl_pct"]  > 0).sum() if len(long_trades)  else 0
    short_wins = (short_trades["pnl_pct"] > 0).sum() if len(short_trades) else 0
    long_wr  = long_wins  / max(n_long,  1)
    short_wr = short_wins / max(n_short, 1)
    overall_wr = (long_wins + short_wins) / max(n_trades, 1)

    long_pnl  = float(long_trades["pnl_pct"].sum())  if len(long_trades)  else 0.0
    short_pnl = float(short_trades["pnl_pct"].sum()) if len(short_trades) else 0.0
    total_pnl = long_pnl + short_pnl

    long_gross_wins  = float(long_trades[long_trades["pnl_pct"]  > 0]["pnl_pct"].sum())  if len(long_trades)  else 0.0
    short_gross_wins = float(short_trades[short_trades["pnl_pct"] > 0]["pnl_pct"].sum()) if len(short_trades) else 0.0
    long_gross_losses  = -float(long_trades[long_trades["pnl_pct"]  < 0]["pnl_pct"].sum())  if len(long_trades)  else 0.0
    short_gross_losses = -float(short_trades[short_trades["pnl_pct"] < 0]["pnl_pct"].sum()) if len(short_trades) else 0.0
    pf = (long_gross_wins + short_gross_wins) / max(long_gross_losses + short_gross_losses, 1e-9)

    avg_pnl_per_trade = total_pnl / n_trades if n_trades > 0 else 0.0

    # Equity curve (sorted by ts, cumulative PnL)
    eq = trades_df.sort_values("ts").reset_index(drop=True).copy()
    eq["cum_pnl_pct"] = eq["pnl_pct"].cumsum()
    if len(eq) > 0:
        # Max drawdown on cumulative PnL curve
        running_max = eq["cum_pnl_pct"].cummax()
        dd = eq["cum_pnl_pct"] - running_max
        max_dd_pct = float(dd.min())
        # Sharpe: per-trade PnL mean / std × sqrt(trades per year)
        # trades/year ≈ n_trades * (CANDLES_PER_YEAR / n_total_candles)
        trades_per_year = n_trades * (CANDLES_PER_YEAR / max(n_total_candles, 1))
        if eq["pnl_pct"].std() > 0:
            sharpe = float(eq["pnl_pct"].mean() / eq["pnl_pct"].std() * np.sqrt(trades_per_year))
        else:
            sharpe = 0.0
    else:
        max_dd_pct = 0.0
        sharpe = 0.0

    # Per-symbol breakdown
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
        "test_candles": n_total_candles,
        "n_long":  n_long,
        "n_short": n_short,
        "n_wait":  n_wait,
        "n_trades_total": n_trades,
        "long_wr":  float(long_wr),
        "short_wr": float(short_wr),
        "overall_wr": float(overall_wr),
        "long_pnl_pct":  float(long_pnl),
        "short_pnl_pct": float(short_pnl),
        "total_pnl_pct": float(total_pnl),
        "avg_pnl_per_trade_pct": float(avg_pnl_per_trade),
        "profit_factor": float(pf),
        "max_drawdown_pct": float(max_dd_pct),
        "sharpe_annualized": float(sharpe),
        "long_gross_wins_pct":  float(long_gross_wins),
        "long_gross_losses_pct": float(long_gross_losses),
        "short_gross_wins_pct":  float(short_gross_wins),
        "short_gross_losses_pct": float(short_gross_losses),
        "per_symbol": per_symbol,
        "thr_long":  float(thr_long),
        "thr_short": float(thr_short),
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "backtest_time_seconds": float(time.time() - t0),
    }
    LOG.info(
        "[%s] candles=%d  L=%d (wr=%.3f, pnl=%+.2f%%)  S=%d (wr=%.3f, pnl=%+.2f%%)  "
        "total=%+.2f%%  PF=%.2f  Sharpe=%.2f  MaxDD=%.2f%%  (%.1fs)",
        window, n_total_candles,
        n_long, long_wr, long_pnl,
        n_short, short_wr, short_pnl,
        total_pnl, pf, sharpe, max_dd_pct,
        time.time() - t0,
    )
    return trades_df, metrics


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--thr-long",  type=float, default=DEFAULT_THR_LONG,
                        help="LONG signal threshold on pred_long (default 0.30%)")
    parser.add_argument("--thr-short", type=float, default=DEFAULT_THR_SHORT,
                        help="SHORT signal threshold on |pred_short| (default 0.40%)")
    parser.add_argument("--windows", default=None,
                        help="Comma-separated YYYY-MM windows (default: all 5)")
    args = parser.parse_args()

    windows = args.windows.split(",") if args.windows else WF_WINDOWS

    print("=" * 80)
    print("v7 F7 — Walk-forward backtest with dual experts (F5a LONG + F5b SHORT)")
    print(f"  features: {len(FEATURE_NAMES)} (no trie — F5 shipping config)")
    print(f"  cost model: {ROUND_TRIP_COST_PCT:.2f}% round-trip")
    print(f"  thresholds: thr_long={args.thr_long:.2f}%  thr_short={args.thr_short:.2f}%")
    print(f"  walk-forward windows: {windows}")
    print("=" * 80)

    df = load_unified_test_set()

    all_results = []
    all_trades = []
    t_start = time.time()
    for window in windows:
        if window not in df["window_key"].unique():
            LOG.warning("Window %s not in dataset — skipping", window)
            continue
        test_df = df[df["window_key"] == window].copy()
        LOG.info("=== Window %s: %d test candles ===", window, len(test_df))

        try:
            long_model, short_model = load_models(window)
        except FileNotFoundError as e:
            LOG.error("Model missing for window %s: %s — skipping", window, e)
            continue

        trades_df, metrics = backtest_window(
            window, test_df, long_model, short_model,
            thr_long=args.thr_long, thr_short=args.thr_short,
        )
        all_results.append(metrics)
        all_trades.append(trades_df)

        # Save per-window artifacts
        trades_df.to_parquet(OUTPUT_DIR / f"v7_f7_trades_{window}.parquet", index=False)
        eq = trades_df.sort_values("ts").reset_index(drop=True).copy()
        eq["cum_pnl_pct"] = eq["pnl_pct"].cumsum()
        eq[["ts", "ts_dt", "symbol", "action", "pnl_pct", "cum_pnl_pct"]].to_parquet(
            OUTPUT_DIR / f"v7_f7_equity_curve_{window}.parquet", index=False
        )

    if not all_results:
        LOG.error("No backtest results produced. Exiting.")
        sys.exit(1)

    # Aggregate across windows
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
    # SHORT WR checkpoint (§12.1)
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

    # Ship decision: Sharpe > 1.0 AND MaxDD > -15%
    ship_decision = (
        "SHIP v7" if (agg["mean_sharpe"] > 1.0 and agg["max_drawdown_pct"] > -15.0)
        else "DO NOT SHIP — needs iteration"
    )
    agg["ship_decision"] = ship_decision

    # Per-symbol aggregation across all windows
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

    # Save summary
    summary_path = OUTPUT_DIR / "v7_f7_backtest_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "config": {
                "features": len(FEATURE_NAMES),
                "feature_set": "F5 (59 v6 + 12 F4, no trie)",
                "models": "F5a LONG + F5b SHORT, walk-forward",
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
                "short_wr_gt_50": short_wr > 0.50,
            },
            "total_runtime_seconds": time.time() - t_start,
        }, f, indent=2)
    print(f"\nSummary saved: {summary_path}")

    # Print summary table
    print("\n" + "=" * 100)
    print("v7 F7 BACKTEST — WALK-FORWARD RESULTS (5 windows)")
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
    print(f"SHORT WR checkpoint (§12.1): {agg['short_wr_checkpoint']}")
    print(f"Ship decision (Sharpe>1.0 & MaxDD>-15%): {agg['ship_decision']}")
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
