"""
rolling_sweep.py — Rolling window validation + backtest sweep.

Instead of one train/test split, uses MULTIPLE rolling windows:
  - Window 1: train days 1-60,  test days 61-67
  - Window 2: train days 8-67,  test days 68-74
  - Window 3: train days 15-74, test days 75-81
  - Window 4: train days 22-81, test days 82-88

For each window, trains a model and runs the sweep.
Aggregates results across ALL windows to find configs that work consistently.

This is the only reliable way to distinguish signal from luck with ~7 trades per window.

Usage:
    python scripts/v7/rolling_sweep.py --symbol ETH/USDT
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent))

from scripts.v7.paper_trader.feed import Feed
from scripts.v7.paper_trader.model import (
    FEATURE_NAMES, DEFAULT_PARAMS, HORIZON, COST_PCT,
)
from scripts.v7.paper_trader.features import extract_features
from scripts.v7.v7_layer2_rolling_retrain import _auc

LOG = logging.getLogger("rolling_sweep")


def make_split(feat_df, train_start_idx, train_end_idx, val_days_bars, test_days_bars):
    """Split by index ranges."""
    val_start = train_end_idx
    val_end = val_start + val_days_bars
    test_start = val_end
    test_end = test_start + test_days_bars

    if test_end > len(feat_df):
        return None, None, None

    train_df = feat_df.iloc[train_start_idx:train_end_idx].reset_index(drop=True)
    val_df = feat_df.iloc[val_start:val_end].reset_index(drop=True)
    test_df = feat_df.iloc[test_start:test_end].reset_index(drop=True)
    return train_df, val_df, test_df


def train_model(train_df, val_df):
    """Train LightGBM binary classifier."""
    X_tr = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_tr = train_df["label"].values.astype(np.float32)
    X_val = val_df[FEATURE_NAMES].values.astype(np.float32)
    y_val = val_df["label"].values.astype(np.float32)

    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES, free_raw_data=False)
    d_val = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, free_raw_data=False)

    params = dict(DEFAULT_PARAMS)
    callbacks = [lgb.log_evaluation(period=0)]
    es = params.get("early_stopping_rounds", -1)
    if es and es > 0:
        callbacks.append(lgb.early_stopping(es, verbose=False))

    bst = lgb.train(params, d_tr, num_boost_round=params.get("n_estimators", 2000),
                    valid_sets=[d_tr, d_val], valid_names=["train", "val"],
                    callbacks=callbacks)

    pred_val = bst.predict(X_val)
    val_auc = _auc(y_val, pred_val)
    best_iter = bst.best_iteration if bst.best_iteration else params.get("n_estimators", 2000)

    return bst, val_auc, best_iter


def evaluate_config(pred, fwd_ret, q_long, q_short, hold_bars, long_only):
    """Run sequential backtest with given parameters."""
    n_trades = 0
    n_long = 0
    n_short = 0
    n_win = 0
    pnl = 0.0
    in_trade = False
    exit_bar = 0
    WINDOW = 200
    recent_preds = []
    trade_returns = []

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


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="ETH/USDT")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--exchange", default="bybit")
    p.add_argument("--n_windows", type=int, default=4, help="number of rolling windows")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    # 1. Fetch data
    feed = Feed(exchange_id=args.exchange)
    from scripts.v7.v7_layer2_rolling_retrain import fetch_30d_data
    sym_df, btc_df, eth_df = fetch_30d_data(feed, args.symbol, days=args.days)

    # 2. Features + labels
    feat_df = extract_features(sym_df, btc_df, eth_df)
    c = feat_df["close"].values
    n = len(feat_df)
    fwd = np.full(n, np.nan)
    for i in range(n - HORIZON):
        fwd[i] = (c[i + HORIZON] - c[i]) / c[i] * 100
    feat_df["fwd_ret_3"] = fwd
    feat_df["label"] = (fwd > 0).astype(int)

    keep_mask = feat_df[FEATURE_NAMES].notna().all(axis=1) & feat_df["fwd_ret_3"].notna()
    feat_df = feat_df.loc[keep_mask].reset_index(drop=True)
    LOG.info("Total clean rows: %d", len(feat_df))

    # 3. Define rolling windows
    bars_per_day = 288
    val_bars = 9 * bars_per_day    # ~9 days val
    test_bars = 7 * bars_per_day   # ~7 days test

    total_bars = len(feat_df)
    n_windows = args.n_windows
    # Each window needs: train + val + test
    # We shift the start by (total - min_train - val - test) / n_windows
    min_train_bars = 45 * bars_per_day  # at least 45 days of training

    window_shift = (total_bars - min_train_bars - val_bars - test_bars) // n_windows

    # 4. Configs to sweep
    configs = []
    for q in [70, 75, 80, 85, 90]:
        configs.append((q, 100 - q, HORIZON, True))   # LONG-only
        configs.append((q, 100 - q, HORIZON, False))   # L+S
    # Also test shorter holds with best quantile
    for hold in [144, 288, 576]:
        configs.append((80, 20, hold, False))

    # 5. Run rolling windows
    all_results = []

    for w in range(n_windows):
        train_start = w * window_shift
        train_end = total_bars - (n_windows - w) * (val_bars + test_bars + window_shift)
        train_end = max(train_end, train_start + min_train_bars)

        LOG.info("=" * 60)
        LOG.info("WINDOW %d/%d: train=%d-%d, val=%d, test=%d",
                 w + 1, n_windows, train_start, train_end, val_bars, test_bars)

        train_df, val_df, test_df = make_split(feat_df, train_start, train_end, val_bars, test_bars)
        if train_df is None:
            LOG.warning("Window %d: not enough data, skipping", w + 1)
            continue

        LOG.info("  split: train=%d val=%d test=%d", len(train_df), len(val_df), len(test_df))

        # Train
        bst, val_auc, best_iter = train_model(train_df, val_df)
        LOG.info("  val_auc=%.3f best_iter=%d", val_auc, best_iter)

        # Evaluate model
        pred_test = bst.predict(test_df[FEATURE_NAMES].values.astype(np.float32))
        y_test = test_df["label"].values.astype(np.float32)
        test_auc = _auc(y_test, pred_test)
        LOG.info("  test_auc=%.3f", test_auc)

        # Sweep configs on this window
        fwd_ret = test_df["fwd_ret_3"].values.astype(np.float64)

        for q_long, q_short, hold, long_only in configs:
            r = evaluate_config(pred_test, fwd_ret, q_long, q_short, hold, long_only)
            r["window"] = w + 1
            r["val_auc"] = val_auc
            r["test_auc"] = test_auc
            r["best_iter"] = best_iter
            r["q_long"] = q_long
            r["q_short"] = q_short
            r["hold_bars"] = hold
            r["long_only"] = long_only
            all_results.append(r)

    # 6. Aggregate across windows
    df = pd.DataFrame(all_results)

    # Summary per config
    print("\n" + "=" * 110)
    print("ROLLING SWEEP RESULTS — {} ({} windows)".format(args.symbol, n_windows))
    print("=" * 110)

    # Group by config
    config_cols = ["q_long", "q_short", "hold_bars", "long_only"]
    metric_cols = ["n_trades", "win_rate", "avg_ret_pct", "pnl_pct", "sharpe"]

    agg = df.groupby(config_cols).agg({
        "n_trades": "sum",
        "win_rate": "mean",
        "avg_ret_pct": "mean",
        "pnl_pct": "sum",
        "sharpe": "mean",
        "test_auc": "mean",
        "n_long": "sum",
        "n_short": "sum",
    }).reset_index()

    agg["win_rate"] = agg["win_rate"] * 100  # to pct
    agg = agg.sort_values("pnl_pct", ascending=False)

    print("\nAggregated across ALL windows:")
    print(agg.to_string(index=False))

    print("\nTop 5 by total PnL:")
    print(agg.head(5).to_string(index=False))

    print("\nTop 5 by avg Sharpe:")
    agg_sharpe = agg.sort_values("sharpe", ascending=False)
    print(agg_sharpe.head(5).to_string(index=False))

    # Per-window breakdown for top config
    best = agg.iloc[0]
    best_config = (best["q_long"], best["q_short"], best["hold_bars"], best["long_only"])
    mask = (df["q_long"] == best_config[0]) & (df["q_short"] == best_config[1]) & \
           (df["hold_bars"] == best_config[2]) & (df["long_only"] == best_config[3])

    print(f"\nPer-window breakdown for best config (Q{int(best_config[0])} hold={int(best_config[2])} L-only={best_config[3]}):")
    print(df[mask][["window", "test_auc", "n_trades", "win_rate", "avg_ret_pct", "pnl_pct", "sharpe"]].to_string(index=False))

    # Consistency check: how many windows have positive PnL?
    print("\nConsistency (positive PnL windows / total):")
    consistency = df.groupby(config_cols).apply(
        lambda g: f"{(g['pnl_pct'] > 0).sum()}/{len(g)}"
    ).reset_index(name="positive_windows")
    consistency = consistency.sort_values(
        by=config_cols,
        key=lambda x: agg.set_index(config_cols).loc[
            [tuple(row) for row in consistency[config_cols].values]
        ]["pnl_pct"].values if len(agg) > 0 else [0] * len(x)
    )
    # Just merge with agg
    agg_with_consistency = agg.merge(
        df.groupby(config_cols).apply(lambda g: f"{(g['pnl_pct'] > 0).sum()}/{len(g)}").reset_index(name="consistency"),
        on=config_cols
    )
    print(agg_with_consistency[config_cols + ["pnl_pct", "sharpe", "n_trades", "consistency"]].to_string(index=False))


if __name__ == "__main__":
    main()
