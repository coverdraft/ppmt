"""
sweep_backtest.py — Sweep backtest parameters on a single trained model.

Trains once, then tests multiple Q_LONG / hold period / LONG-only vs L+S combos.
Outputs a comparison table to quickly find the best config.

Usage:
    python scripts/v7/sweep_backtest.py --symbol ETH/USDT
    python scripts/v7/sweep_backtest.py --symbol ETH/USDT --days 90
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
from scripts.v7.v7_layer2_rolling_retrain import (
    fetch_30d_data, split_walk_forward, _auc,
)

LOG = logging.getLogger("sweep")


def evaluate_config(pred, fwd_ret, closes, q_long, q_short, hold_bars, long_only):
    """Run sequential backtest with given parameters. Returns dict of metrics."""
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
        "q_long": q_long,
        "q_short": q_short,
        "hold_bars": hold_bars,
        "long_only": long_only,
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
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    # 1. Fetch data
    feed = Feed(exchange_id=args.exchange)
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

    # 3. Split
    train_df, val_df, test_df = split_walk_forward(feat_df, days=args.days)

    # 4. Train
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

    # 5. Evaluate model quality
    pred_val = bst.predict(X_val)
    val_auc = _auc(y_val, pred_val)
    pred_test = bst.predict(test_df[FEATURE_NAMES].values.astype(np.float32))
    y_test = test_df["label"].values.astype(np.float32)
    test_auc = _auc(y_test, pred_test)
    best_iter = bst.best_iteration if bst.best_iteration else params.get("n_estimators", 2000)

    LOG.info("Model: val_auc=%.3f test_auc=%.3f best_iter=%d", val_auc, test_auc, best_iter)
    LOG.info("Pred distribution: min=%.4f p50=%.4f max=%.4f std=%.4f",
             float(pred_test.min()), float(np.percentile(pred_test, 50)),
             float(pred_test.max()), float(pred_test.std()))

    # 6. Sweep configurations
    fwd_ret = test_df["fwd_ret_3"].values.astype(np.float64)
    closes = test_df["close"].values.astype(np.float64)

    configs = []
    # Vary Q_LONG
    for q in [70, 75, 80, 85, 90, 95]:
        configs.append((q, 100 - q, HORIZON, True))   # LONG-only
        configs.append((q, 100 - q, HORIZON, False))   # LONG+SHORT
    # Vary hold period (LONG-only, q=80)
    for hold in [1, 12, 36, 72, 144, 288, 576]:
        configs.append((80, 20, hold, True))

    results = []
    for q_long, q_short, hold, long_only in configs:
        r = evaluate_config(pred_test, fwd_ret, closes, q_long, q_short, hold, long_only)
        results.append(r)

    # 7. Print comparison table
    df = pd.DataFrame(results)
    df = df.sort_values("pnl_pct", ascending=False)

    print("\n" + "=" * 100)
    print("BACKTEST SWEEP RESULTS — {} (test_auc={:.3f}, best_iter={}, val_auc={:.3f})".format(
        args.symbol, test_auc, best_iter, val_auc))
    print("=" * 100)
    print(df.to_string(index=False))
    print("\nTop 5 by PnL:")
    print(df.head(5).to_string(index=False))
    print("\nTop 5 by Sharpe:")
    df_sharpe = df.sort_values("sharpe", ascending=False)
    print(df_sharpe.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
