"""
comprehensive_sweep.py — Multi-token, multi-horizon, rolling-window sweep.

Tests whether the LightGBM binary classifier has a real edge across:
  - Multiple tokens (ETH, SOL, DOGE, AVAX, LINK, XRP, BTC as baseline)
  - Multiple prediction horizons (30m to 24h on 5m bars)
  - Multiple quantile configs (Q90/10, Q85/15, Q80/20, L+S)
  - Rolling 4-window evaluation for robustness

Key questions this answers:
  1. Which tokens have a signal at all? (vs just noise/overfitting on ETH)
  2. Does shorter horizon = more trades but worse signal? Where's the sweet spot?
  3. Is Q90/10 robust across tokens, or does it overfit to ETH?
  4. How consistent is the edge across time windows? (3/4? 2/4? 1/4?)

This is the REAL test before paper trading. No hand-waving.

Usage:
    python scripts/v7/comprehensive_sweep.py
    python scripts/v7/comprehensive_sweep.py --symbols "ETH/USDT,SOL/USDT"
    python scripts/v7/comprehensive_sweep.py --days 90
    python scripts/v7/comprehensive_sweep.py --quick   # fast mode: fewer tokens/horizons
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent))

from scripts.v7.paper_trader.feed import Feed
from scripts.v7.paper_trader.model import (
    FEATURE_NAMES, DEFAULT_PARAMS, COST_PCT,
)
from scripts.v7.paper_trader.features import extract_features
from scripts.v7.v7_layer2_rolling_retrain import (
    fetch_30d_data, split_walk_forward, _auc,
)

LOG = logging.getLogger("csweep")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# All symbols to test (BTC is baseline — expected to fail)
ALL_SYMBOLS = [
    "ETH/USDT",
    "SOL/USDT",
    "DOGE/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "XRP/USDT",
    "BTC/USDT",  # baseline — known dead end
]

# Horizons to test on 5m bars
# HORIZON = number of 5m bars forward
# 6=30m, 12=1h, 36=3h, 72=6h, 144=12h, 288=24h
ALL_HORIZONS = [6, 12, 36, 72, 144, 288]

# Quantile configs (all LONG+SHORT since LONG-only always loses)
ALL_Q_CONFIGS = [
    (90, 10),   # very selective — top/bottom 10%
    (85, 15),   # selective
    (80, 20),   # moderate
]

# Rolling window settings
N_WINDOWS = 4  # number of rolling windows for cross-validation

# Minimum trades to consider a result meaningful
MIN_TRADES_THRESHOLD = 3


def compute_labels(feat_df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Compute forward return and binary label for given horizon."""
    df = feat_df.copy()
    c = df["close"].values
    n = len(df)
    fwd = np.full(n, np.nan)
    for i in range(n - horizon):
        fwd[i] = (c[i + horizon] - c[i]) / c[i] * 100
    df["fwd_ret"] = fwd
    df["label"] = (fwd > 0).astype(int)
    return df


def rolling_windows(feat_df: pd.DataFrame, days: int, n_windows: int = 4):
    """Generate n_windows of walk-forward splits by shifting the test window back.

    Window 1 = most recent (same as single split).
    Window 2 = shifted back by test_size.
    Window 3 = shifted back by 2*test_size.
    etc.

    This gives us n_windows independent test periods for robustness.
    """
    ts = feat_df["timestamp"].values
    ts_first, ts_last = ts[0], ts[-1]
    span_ms = ts_last - ts_first
    span_days = span_ms / (1000 * 86400)

    test_days = max(span_days * 0.07, 0.5)
    val_days = max(span_days * 0.10, 0.5)

    windows = []
    for w in range(n_windows):
        # Shift test window back by w * test_days
        offset_ms = int(w * test_days * 86400 * 1000)
        test_end_ts = ts_last - offset_ms
        test_start_ts = test_end_ts - int(test_days * 86400 * 1000)
        val_start_ts = test_start_ts - int(val_days * 86400 * 1000)

        train_df = feat_df[feat_df["timestamp"] < val_start_ts].reset_index(drop=True)
        val_df = feat_df[(feat_df["timestamp"] >= val_start_ts) & (feat_df["timestamp"] < test_start_ts)].reset_index(drop=True)
        test_df = feat_df[feat_df["timestamp"] >= test_start_ts].copy()
        test_df = test_df[test_df["timestamp"] < test_end_ts].reset_index(drop=True)

        if len(train_df) < 500 or len(val_df) < 50 or len(test_df) < 50:
            continue

        windows.append((train_df, val_df, test_df))

    return windows


def train_model(train_df: pd.DataFrame, val_df: pd.DataFrame) -> tuple[lgb.Booster, dict]:
    """Train LightGBM binary classifier. Returns (booster, metrics)."""
    X_tr = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_tr = train_df["label"].values.astype(np.float32)
    X_val = val_df[FEATURE_NAMES].values.astype(np.float32)
    y_val = val_df["label"].values.astype(np.float32)

    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES, free_raw_data=False)
    d_val = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, free_raw_data=False)

    params = dict(DEFAULT_PARAMS)
    callbacks = [lgb.log_evaluation(period=0)]  # silent
    es = params.get("early_stopping_rounds", -1)
    if es and es > 0:
        callbacks.append(lgb.early_stopping(es, verbose=False))

    bst = lgb.train(params, d_tr, num_boost_round=params.get("n_estimators", 2000),
                    valid_sets=[d_tr, d_val], valid_names=["train", "val"],
                    callbacks=callbacks)

    pred_val = bst.predict(X_val)
    auc_val = float(_auc(y_val, pred_val))
    best_iter = int(bst.best_iteration) if bst.best_iteration else params.get("n_estimators", 2000)

    metrics = {"auc_val": auc_val, "best_iter": best_iter, "n_train": len(X_tr), "n_val": len(X_val)}
    return bst, metrics


def sequential_backtest(pred: np.ndarray, fwd_ret: np.ndarray,
                        q_long: int, q_short: int, hold_bars: int,
                        long_only: bool = False) -> dict:
    """Run sequential backtest with given parameters."""
    n_trades = 0
    n_win = 0
    pnl = 0.0
    in_trade = False
    exit_bar = 0
    WINDOW = 200
    recent_preds = []
    trade_returns = []
    n_long = 0
    n_short = 0

    for i in range(len(pred)):
        p_val = float(pred[i])
        recent_preds.append(p_val)
        if len(recent_preds) > WINDOW:
            recent_preds.pop(0)

        if in_trade:
            if i >= exit_bar:
                in_trade = False
            else:
                continue

        if len(recent_preds) < 20:
            continue

        q_high = np.percentile(recent_preds, q_long)
        q_low = np.percentile(recent_preds, q_short)

        sig = 0
        if p_val > q_high:
            sig = 1
            n_long += 1
        elif not long_only and p_val < q_low:
            sig = -1
            n_short += 1

        if sig != 0 and not np.isnan(fwd_ret[i]):
            n_trades += 1
            trade_ret = sig * fwd_ret[i] - COST_PCT
            pnl += trade_ret
            trade_returns.append(trade_ret)
            in_trade = True
            exit_bar = i + hold_bars
            if trade_ret > 0:
                n_win += 1

    win_rate = n_win / n_trades if n_trades > 0 else 0
    avg_ret = pnl / n_trades if n_trades > 0 else 0
    sharpe = (np.mean(trade_returns) / np.std(trade_returns)) if len(trade_returns) > 1 else 0

    return {
        "n_trades": n_trades,
        "n_long": n_long,
        "n_short": n_short,
        "win_rate": win_rate,
        "avg_ret_pct": avg_ret,
        "pnl_pct": pnl,
        "sharpe": sharpe,
    }


def run_symbol_sweep(feed: Feed, symbol: str, days: int = 90,
                     horizons: list[int] | None = None,
                     q_configs: list[tuple[int, int]] | None = None,
                     n_windows: int = 4) -> list[dict]:
    """Run comprehensive sweep for one symbol across all horizons and configs."""
    if horizons is None:
        horizons = ALL_HORIZONS
    if q_configs is None:
        q_configs = ALL_Q_CONFIGS

    results = []

    # 1. Fetch data ONCE per symbol
    LOG.info("=" * 70)
    LOG.info("FETCHING DATA: %s (%d days)", symbol, days)
    LOG.info("=" * 70)
    try:
        sym_df, btc_df, eth_df = fetch_30d_data(feed, symbol, days=days)
    except Exception as e:
        LOG.error("FETCH FAILED for %s: %s", symbol, e)
        return results

    # 2. Compute features ONCE
    feat_df = extract_features(sym_df, btc_df, eth_df)

    # 3. For each horizon
    for horizon in horizons:
        LOG.info("-" * 50)
        LOG.info("  HORIZON=%d (%.0fh forward) for %s", horizon, horizon * 5 / 60, symbol)

        # Compute labels for this horizon
        labeled_df = compute_labels(feat_df, horizon)
        keep_mask = labeled_df[FEATURE_NAMES].notna().all(axis=1) & labeled_df["fwd_ret"].notna()
        labeled_df = labeled_df.loc[keep_mask].reset_index(drop=True)

        if len(labeled_df) < 1000:
            LOG.warning("  Too few clean rows (%d) for horizon=%d, skipping", len(labeled_df), horizon)
            continue

        label_up_pct = labeled_df["label"].mean() * 100
        LOG.info("  Clean rows=%d, label_up=%.1f%%", len(labeled_df), label_up_pct)

        # 4. Rolling windows
        windows = rolling_windows(labeled_df, days=days, n_windows=n_windows)
        if len(windows) == 0:
            LOG.warning("  Not enough data for rolling windows at horizon=%d", horizon)
            continue

        LOG.info("  %d rolling windows available", len(windows))

        # 5. For each window: train, evaluate, sweep configs
        for w_idx, (train_df, val_df, test_df) in enumerate(windows):
            # Train
            try:
                bst, train_m = train_model(train_df, val_df)
            except Exception as e:
                LOG.warning("  Window %d training failed: %s", w_idx + 1, e)
                continue

            # Predict on test
            X_test = test_df[FEATURE_NAMES].values.astype(np.float32)
            y_test = test_df["label"].values.astype(np.float32)
            pred = bst.predict(X_test)
            fwd_ret = test_df["fwd_ret"].values.astype(np.float64)
            test_auc = float(_auc(y_test, pred))

            # Sweep quantile configs
            for q_long, q_short in q_configs:
                bt = sequential_backtest(pred, fwd_ret, q_long, q_short, horizon, long_only=False)

                results.append({
                    "symbol": symbol,
                    "horizon": horizon,
                    "horizon_h": round(horizon * 5 / 60, 1),
                    "q_long": q_long,
                    "q_short": q_short,
                    "window": w_idx + 1,
                    "test_auc": round(test_auc, 4),
                    "best_iter": train_m["best_iter"],
                    "n_trades": bt["n_trades"],
                    "n_long": bt["n_long"],
                    "n_short": bt["n_short"],
                    "win_rate": round(bt["win_rate"], 4),
                    "avg_ret_pct": round(bt["avg_ret_pct"], 4),
                    "pnl_pct": round(bt["pnl_pct"], 4),
                    "sharpe": round(bt["sharpe"], 4),
                })

        LOG.info("  Horizon %d done: %d result rows so far", horizon, len(results))

    return results


def aggregate_results(results: list[dict]) -> pd.DataFrame:
    """Aggregate per-window results into per-config summary."""
    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)

    # Aggregate across windows for each (symbol, horizon, q_long, q_short)
    agg = df.groupby(["symbol", "horizon", "horizon_h", "q_long", "q_short"]).agg(
        test_auc=("test_auc", "mean"),
        n_trades=("n_trades", "sum"),
        n_long=("n_long", "sum"),
        n_short=("n_short", "sum"),
        win_rate=("win_rate", "mean"),
        avg_ret_pct=("avg_ret_pct", "mean"),
        pnl_pct=("pnl_pct", "sum"),
        sharpe=("sharpe", "mean"),
        n_windows=("pnl_pct", "count"),
        # Consistency: how many windows had positive PnL
        pnl_positive=("pnl_pct", lambda x: (x > 0).sum()),
    ).reset_index()

    agg["consistency"] = agg.apply(lambda r: f"{int(r['pnl_positive'])}/{int(r['n_windows'])}", axis=1)
    return agg


def print_report(agg_df: pd.DataFrame, per_window_df: pd.DataFrame | None = None):
    """Print comprehensive report."""
    print("\n" + "=" * 120)
    print("COMPREHENSIVE SWEEP RESULTS — AGGREGATED ACROSS WINDOWS")
    print("=" * 120)

    # 1. Best config per symbol
    print("\n--- BEST CONFIG PER SYMBOL (by PnL) ---")
    for sym in agg_df["symbol"].unique():
        sym_df = agg_df[agg_df["symbol"] == sym].sort_values("pnl_pct", ascending=False)
        if len(sym_df) == 0:
            continue
        best = sym_df.iloc[0]
        print(f"  {sym:12s} | H={int(best['horizon']):>3d} ({best['horizon_h']:>5.1f}h) | "
              f"Q{int(best['q_long']):>2d}/{int(best['q_short']):>2d} | "
              f"PnL={best['pnl_pct']:>+8.2f}% | Sharpe={best['sharpe']:>+.3f} | "
              f"Trades={int(best['n_trades']):>3d} | WR={best['win_rate']*100:>5.1f}% | "
              f"AUC={best['test_auc']:.3f} | Cons={best['consistency']}")

    # 2. Best horizon per symbol
    print("\n--- BEST HORIZON PER SYMBOL (Q90/10, by PnL) ---")
    q90_df = agg_df[agg_df["q_long"] == 90]
    for sym in q90_df["symbol"].unique():
        sym_df = q90_df[q90_df["symbol"] == sym].sort_values("pnl_pct", ascending=False)
        if len(sym_df) == 0:
            continue
        best = sym_df.iloc[0]
        all_h = sym_df.sort_values("horizon")
        h_str = " | ".join([f"H={int(r['horizon']):>3d}→PnL={r['pnl_pct']:>+7.2f}%" for _, r in all_h.iterrows()])
        print(f"  {sym:12s} | Best: H={int(best['horizon']):>3d} ({best['horizon_h']:>5.1f}h) | {h_str}")

    # 3. Full table sorted by PnL
    print("\n--- ALL CONFIGS SORTED BY PnL (top 30) ---")
    top = agg_df.sort_values("pnl_pct", ascending=False).head(30)
    print(top[["symbol", "horizon", "horizon_h", "q_long", "q_short", "n_trades",
               "win_rate", "avg_ret_pct", "pnl_pct", "sharpe", "test_auc", "consistency"]].to_string(index=False))

    # 4. Tokens with ANY positive edge
    print("\n--- TOKENS WITH POSITIVE AGGREGATE PnL ---")
    positive = agg_df[agg_df["pnl_pct"] > 0].groupby("symbol").agg(
        best_pnl=("pnl_pct", "max"),
        best_sharpe=("sharpe", "max"),
        n_positive_configs=("pnl_pct", lambda x: (x > 0).sum()),
        n_total_configs=("pnl_pct", "count"),
    ).sort_values("best_pnl", ascending=False)
    print(positive.to_string())

    # 5. Tokens with NO edge
    print("\n--- TOKENS WITH NO POSITIVE CONFIG (DEAD ENDS) ---")
    all_syms = set(agg_df["symbol"].unique())
    positive_syms = set(positive.index) if len(positive) > 0 else set()
    dead = all_syms - positive_syms
    if dead:
        for s in sorted(dead):
            sym_df = agg_df[agg_df["symbol"] == s]
            best_pnl = sym_df["pnl_pct"].max()
            print(f"  {s}: best PnL = {best_pnl:+.2f}%")
    else:
        print("  None — all tokens have at least one positive config!")

    # 6. Horizon comparison across ALL tokens
    print("\n--- HORIZON COMPARISON (all tokens, Q90/10) ---")
    h_compare = agg_df[agg_df["q_long"] == 90].groupby("horizon").agg(
        avg_pnl=("pnl_pct", "mean"),
        med_pnl=("pnl_pct", "median"),
        avg_sharpe=("sharpe", "mean"),
        avg_trades=("n_trades", "mean"),
        pct_positive=("pnl_pct", lambda x: (x > 0).mean() * 100),
    ).sort_index()
    print(h_compare.to_string())

    # 7. Per-window breakdown for best configs (honest assessment)
    if per_window_df is not None and len(per_window_df) > 0:
        print("\n--- PER-WINDOW BREAKDOWN (top 5 configs by total PnL) ---")
        top_configs = agg_df.sort_values("pnl_pct", ascending=False).head(5)
        for _, cfg in top_configs.iterrows():
            mask = ((per_window_df["symbol"] == cfg["symbol"]) &
                    (per_window_df["horizon"] == cfg["horizon"]) &
                    (per_window_df["q_long"] == cfg["q_long"]) &
                    (per_window_df["q_short"] == cfg["q_short"]))
            windows = per_window_df[mask].sort_values("window")
            print(f"\n  {cfg['symbol']} H={int(cfg['horizon'])} Q{int(cfg['q_long'])}/{int(cfg['q_short'])} "
                  f"(total PnL={cfg['pnl_pct']:+.2f}%, Sharpe={cfg['sharpe']:+.3f}):")
            for _, w in windows.iterrows():
                print(f"    Window {int(w['window'])}: trades={int(w['n_trades'])} "
                      f"WR={w['win_rate']*100:.1f}% PnL={w['pnl_pct']:+.2f}% "
                      f"Sharpe={w['sharpe']:+.3f} AUC={w['test_auc']:.3f}")

    # 8. HONEST ASSESSMENT
    print("\n" + "=" * 120)
    print("HONEST ASSESSMENT")
    print("=" * 120)

    # Check for overfitting: configs that only work on 1 window
    one_hit_wonders = agg_df[(agg_df["pnl_pct"] > 0) & (agg_df["pnl_positive"] <= 1)]
    if len(one_hit_wonders) > 0:
        print(f"\n  WARNING: {len(one_hit_wonders)} configs positive in only 1 window — likely overfitting")

    robust = agg_df[(agg_df["pnl_pct"] > 0) & (agg_df["pnl_positive"] >= 2) & (agg_df["n_trades"] >= MIN_TRADES_THRESHOLD * 2)]
    if len(robust) > 0:
        print(f"\n  ROBUST configs (PnL>0, >=2 windows positive, >=6 trades): {len(robust)}")
        for _, r in robust.sort_values("pnl_pct", ascending=False).head(10).iterrows():
            print(f"    {r['symbol']:12s} H={int(r['horizon']):>3d} Q{int(r['q_long'])}/{int(r['q_short'])} "
                  f"PnL={r['pnl_pct']:+.2f}% Sharpe={r['sharpe']:+.3f} Cons={r['consistency']}")
    else:
        print("\n  NO ROBUST CONFIGS FOUND — edge is not statistically significant")

    # Check AUC vs PnL disconnect
    auc_positive = agg_df[(agg_df["test_auc"] > 0.52) & (agg_df["pnl_pct"] > 0)]
    auc_negative = agg_df[(agg_df["test_auc"] < 0.48) & (agg_df["pnl_pct"] > 0)]
    print(f"\n  AUC>0.52 AND PnL>0: {len(auc_positive)} configs")
    print(f"  AUC<0.48 AND PnL>0: {len(auc_negative)} configs (PnL from noise, not signal)")

    # Recommendation
    print("\n  RECOMMENDATION:")
    very_robust = agg_df[(agg_df["pnl_pct"] > 5) & (agg_df["pnl_positive"] >= 3) & (agg_df["n_trades"] >= 10)]
    if len(very_robust) > 0:
        best = very_robust.sort_values("pnl_pct", ascending=False).iloc[0]
        print(f"    STRONGEST signal: {best['symbol']} H={int(best['horizon'])} "
              f"Q{int(best['q_long'])}/{int(best['q_short'])} "
              f"PnL={best['pnl_pct']:+.2f}% Cons={best['consistency']}")
        print(f"    → Launch paper trading with this config")
    else:
        somewhat_robust = agg_df[(agg_df["pnl_pct"] > 0) & (agg_df["pnl_positive"] >= 2)]
        if len(somewhat_robust) > 0:
            print(f"    WEAK signal found in {len(somewhat_robust)} configs")
            print(f"    → Paper trading with tight risk controls to validate OOS")
        else:
            print(f"    NO signal found — model needs fundamental changes (features, architecture)")


def main():
    parser = argparse.ArgumentParser(description="Comprehensive multi-token multi-horizon sweep")
    parser.add_argument("--symbols", default=None,
                        help="comma-separated list (default: all)")
    parser.add_argument("--days", type=int, default=90,
                        help="training window days (default 90)")
    parser.add_argument("--exchange", default="bybit")
    parser.add_argument("--quick", action="store_true",
                        help="fast mode: 3 tokens, 3 horizons, 2 windows")
    parser.add_argument("--save-csv", default=None,
                        help="save results to CSV file")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Config
    symbols = args.symbols.split(",") if args.symbols else ALL_SYMBOLS
    if args.quick:
        symbols = ["ETH/USDT", "SOL/USDT", "DOGE/USDT"]
        horizons = [12, 72, 288]
        q_configs = [(90, 10), (80, 20)]
        n_windows = 2
    else:
        horizons = ALL_HORIZONS
        q_configs = ALL_Q_CONFIGS
        n_windows = N_WINDOWS

    LOG.info("COMPREHENSIVE SWEEP: %d symbols × %d horizons × %d Q-configs × %d windows",
             len(symbols), len(horizons), len(q_configs), n_windows)
    LOG.info("Symbols: %s", symbols)
    LOG.info("Horizons: %s", horizons)
    LOG.info("Q configs: %s", q_configs)

    # Run sweep
    feed = Feed(exchange_id=args.exchange)
    all_results = []

    for sym in symbols:
        t0 = time.time()
        sym_results = run_symbol_sweep(feed, sym, days=args.days,
                                        horizons=horizons, q_configs=q_configs,
                                        n_windows=n_windows)
        elapsed = time.time() - t0
        LOG.info("%s done in %.1fs — %d result rows", sym, elapsed, len(sym_results))
        all_results.extend(sym_results)

    if not all_results:
        LOG.error("No results generated — check data availability")
        return

    # Save per-window results
    per_window_df = pd.DataFrame(all_results)
    if args.save_csv:
        per_window_df.to_csv(args.save_csv, index=False)
        LOG.info("Per-window results saved to %s", args.save_csv)

    # Aggregate and report
    agg_df = aggregate_results(all_results)
    print_report(agg_df, per_window_df)

    # Save aggregated results too
    if args.save_csv:
        agg_path = args.save_csv.replace(".csv", "_agg.csv")
        agg_df.to_csv(agg_path, index=False)
        LOG.info("Aggregated results saved to %s", agg_path)


if __name__ == "__main__":
    main()
