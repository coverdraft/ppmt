"""
deep_optimize.py — Deep parameter optimization for PPMT v7.

Focuses on finding REAL edge improvements by testing:
  1. More data (180d vs 90d) → more trades, better significance
  2. LightGBM hyperparameter tuning (lr, num_leaves, regularization)
  3. Finer Q configs (Q82, Q87, Q92, etc.)
  4. Different rolling window sizes (100, 200, 400)
  5. Lower cost assumptions (Bybit maker fees vs taker)
  6. Feature subsets (which features actually help?)

Only tests H=288 (24h) since shorter horizons are confirmed dead.

Targets the top 4 tokens: DOGE, SOL, ETH, AVAX (4/4 or 3/4 consistency)

Usage:
    python scripts/v7/deep_optimize.py
    python scripts/v7/deep_optimize.py --symbols "DOGE/USDT,AVAX/USDT"
    python scripts/v7/deep_optimize.py --days 180
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import itertools
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

LOG = logging.getLogger("deepopt")

# Only test tokens with confirmed edge
BEST_TOKENS = ["DOGE/USDT", "SOL/USDT", "ETH/USDT", "AVAX/USDT"]
HORIZON = 288  # 24h — confirmed ONLY viable horizon

# LightGBM hyperparameter grid
HP_GRID = [
    {
        "learning_rate": 0.01,
        "num_leaves": 31,
        "min_data_in_leaf": 30,
        "lambda_l1": 0.3,
        "lambda_l2": 3.0,
        "label": "default",
    },
    {
        "learning_rate": 0.005,
        "num_leaves": 15,
        "min_data_in_leaf": 50,
        "lambda_l1": 1.0,
        "lambda_l2": 5.0,
        "label": "more_reg",
    },
    {
        "learning_rate": 0.02,
        "num_leaves": 63,
        "min_data_in_leaf": 20,
        "lambda_l1": 0.1,
        "lambda_l2": 1.0,
        "label": "less_reg",
    },
    {
        "learning_rate": 0.01,
        "num_leaves": 15,
        "min_data_in_leaf": 100,
        "lambda_l1": 1.0,
        "lambda_l2": 10.0,
        "label": "very_reg",
    },
    {
        "learning_rate": 0.005,
        "num_leaves": 63,
        "min_data_in_leaf": 30,
        "lambda_l1": 0.5,
        "lambda_l2": 3.0,
        "label": "slow_deep",
    },
]

# Q config grid — finer granularity
Q_CONFIGS = [
    (95, 5),   # ultra-selective: top/bottom 5%
    (92, 8),   # very selective
    (90, 10),  # selective
    (87, 13),  # moderately selective
    (85, 15),  # moderate
    (82, 18),  # moderately loose
    (80, 20),  # loose
]

# Rolling window sizes
WINDOW_SIZES = [100, 200, 400]

# Cost assumptions
COST_CONFIGS = [
    ("taker", 0.14),    # current: 0.07% each way (taker)
    ("maker", 0.04),    # Bybit maker: 0.02% each way (limit orders)
    ("mid", 0.09),      # average
]

N_WINDOWS = 4


def compute_labels(feat_df: pd.DataFrame, horizon: int = HORIZON) -> pd.DataFrame:
    """Compute forward return and binary label."""
    df = feat_df.copy()
    c = df["close"].values
    n = len(df)
    fwd = np.full(n, np.nan)
    for i in range(n - horizon):
        fwd[i] = (c[i + horizon] - c[i]) / c[i] * 100
    df["fwd_ret"] = fwd
    df["label"] = (fwd > 0).astype(int)
    return df


def rolling_windows(feat_df: pd.DataFrame, days: int, n_windows: int = N_WINDOWS):
    """Generate rolling walk-forward splits."""
    ts = feat_df["timestamp"].values
    ts_last = ts[-1]
    span_ms = ts[-1] - ts[0]
    span_days = span_ms / (1000 * 86400)

    test_days = max(span_days * 0.07, 0.5)
    val_days = max(span_days * 0.10, 0.5)

    windows = []
    for w in range(n_windows):
        offset_ms = int(w * test_days * 86400 * 1000)
        test_end_ts = ts_last - offset_ms
        test_start_ts = test_end_ts - int(test_days * 86400 * 1000)
        val_start_ts = test_start_ts - int(val_days * 86400 * 1000)

        train_df = feat_df[feat_df["timestamp"] < val_start_ts].reset_index(drop=True)
        val_df = feat_df[(feat_df["timestamp"] >= val_start_ts) & (feat_df["timestamp"] < test_start_ts)].reset_index(drop=True)
        test_df = feat_df[(feat_df["timestamp"] >= test_start_ts) & (feat_df["timestamp"] < test_end_ts)].reset_index(drop=True)

        if len(train_df) < 1000 or len(val_df) < 100 or len(test_df) < 100:
            continue

        windows.append((train_df, val_df, test_df))

    return windows


def train_model_custom(train_df: pd.DataFrame, val_df: pd.DataFrame,
                       hp: dict) -> tuple[lgb.Booster, dict]:
    """Train LightGBM with custom hyperparameters."""
    X_tr = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_tr = train_df["label"].values.astype(np.float32)
    X_val = val_df[FEATURE_NAMES].values.astype(np.float32)
    y_val = val_df["label"].values.astype(np.float32)

    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_NAMES, free_raw_data=False)
    d_val = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, free_raw_data=False)

    params = {
        "objective": "binary",
        "metric": ["binary_logloss", "auc"],
        "num_leaves": hp["num_leaves"],
        "learning_rate": hp["learning_rate"],
        "n_estimators": 2000,
        "early_stopping_rounds": 150,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.7,
        "bagging_freq": 3,
        "min_data_in_leaf": hp["min_data_in_leaf"],
        "lambda_l1": hp["lambda_l1"],
        "lambda_l2": hp["lambda_l2"],
        "verbosity": -1,
    }

    callbacks = [lgb.log_evaluation(period=0)]
    callbacks.append(lgb.early_stopping(150, verbose=False))

    bst = lgb.train(params, d_tr, num_boost_round=2000,
                    valid_sets=[d_tr, d_val], valid_names=["train", "val"],
                    callbacks=callbacks)

    pred_val = bst.predict(X_val)
    auc_val = float(_auc(y_val, pred_val))
    best_iter = int(bst.best_iteration) if bst.best_iteration else 2000

    # Feature importance
    imp = bst.feature_importance(importance_type="gain")
    top_features = [(FEATURE_NAMES[i], int(imp[i])) for i in np.argsort(imp)[::-1][:10]]

    return bst, {
        "auc_val": auc_val,
        "best_iter": best_iter,
        "n_train": len(X_tr),
        "hp_label": hp["label"],
        "top_features": top_features,
    }


def sequential_backtest(pred: np.ndarray, fwd_ret: np.ndarray,
                        q_long: int, q_short: int, hold_bars: int,
                        window_size: int = 200,
                        cost_pct: float = COST_PCT,
                        long_only: bool = False) -> dict:
    """Sequential backtest with configurable parameters."""
    n_trades = 0
    n_win = 0
    pnl = 0.0
    in_trade = False
    exit_bar = 0
    recent_preds = []
    trade_returns = []
    n_long = 0
    n_short = 0

    for i in range(len(pred)):
        p_val = float(pred[i])
        recent_preds.append(p_val)
        if len(recent_preds) > window_size:
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
            trade_ret = sig * fwd_ret[i] - cost_pct
            pnl += trade_ret
            trade_returns.append(trade_ret)
            in_trade = True
            exit_bar = i + hold_bars
            if trade_ret > 0:
                n_win += 1

    win_rate = n_win / n_trades if n_trades > 0 else 0
    avg_ret = pnl / n_trades if n_trades > 0 else 0
    sharpe = (np.mean(trade_returns) / np.std(trade_returns)) if len(trade_returns) > 1 else 0

    # Max drawdown
    cumulative = np.cumsum(trade_returns) if trade_returns else np.array([0])
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = cumulative - running_max
    max_dd = float(drawdowns.min()) if len(drawdowns) > 0 else 0

    # Profit factor
    gains = sum(r for r in trade_returns if r > 0)
    losses = abs(sum(r for r in trade_returns if r < 0))
    profit_factor = gains / losses if losses > 0 else float('inf') if gains > 0 else 0

    return {
        "n_trades": n_trades,
        "n_long": n_long,
        "n_short": n_short,
        "win_rate": win_rate,
        "avg_ret_pct": avg_ret,
        "pnl_pct": pnl,
        "sharpe": sharpe,
        "max_dd_pct": max_dd,
        "profit_factor": profit_factor,
    }


def run_deep_optimization(feed: Feed, symbols: list[str], days: int = 180) -> list[dict]:
    """Run deep optimization for given symbols."""
    results = []

    for symbol in symbols:
        LOG.info("=" * 70)
        LOG.info("DEEP OPTIMIZATION: %s (%d days)", symbol, days)
        LOG.info("=" * 70)

        # 1. Fetch data
        try:
            sym_df, btc_df, eth_df = fetch_30d_data(feed, symbol, days=days)
        except Exception as e:
            LOG.error("FETCH FAILED for %s: %s", symbol, e)
            continue

        # 2. Compute features ONCE
        feat_df = extract_features(sym_df, btc_df, eth_df)

        # 3. Labels for H=288
        labeled_df = compute_labels(feat_df)
        keep_mask = labeled_df[FEATURE_NAMES].notna().all(axis=1) & labeled_df["fwd_ret"].notna()
        labeled_df = labeled_df.loc[keep_mask].reset_index(drop=True)
        LOG.info("%s: %d clean rows, label_up=%.1f%%", symbol, len(labeled_df), labeled_df["label"].mean() * 100)

        # 4. Rolling windows
        windows = rolling_windows(labeled_df, days=days, n_windows=N_WINDOWS)
        if len(windows) < 3:
            LOG.warning("%s: only %d windows, skipping", symbol, len(windows))
            continue
        LOG.info("%s: %d rolling windows", symbol, len(windows))

        # 5. For each hyperparameter set
        for hp_idx, hp in enumerate(HP_GRID):
            LOG.info("  HP config %d/%d: %s", hp_idx + 1, len(HP_GRID), hp["label"])

            # Train one model per window (retrain from scratch each window)
            for w_idx, (train_df, val_df, test_df) in enumerate(windows):
                try:
                    bst, train_m = train_model_custom(train_df, val_df, hp)
                except Exception as e:
                    LOG.warning("  Training failed: %s", e)
                    continue

                X_test = test_df[FEATURE_NAMES].values.astype(np.float32)
                y_test = test_df["label"].values.astype(np.float32)
                pred = bst.predict(X_test)
                fwd_ret = test_df["fwd_ret"].values.astype(np.float64)
                test_auc = float(_auc(y_test, pred))

                # 6. Sweep Q configs × window sizes × costs
                for q_long, q_short in Q_CONFIGS:
                    for win_size in WINDOW_SIZES:
                        for cost_label, cost_pct in COST_CONFIGS:
                            bt = sequential_backtest(
                                pred, fwd_ret, q_long, q_short,
                                HORIZON, window_size=win_size, cost_pct=cost_pct,
                            )

                            results.append({
                                "symbol": symbol,
                                "hp_label": hp["label"],
                                "q_long": q_long,
                                "q_short": q_short,
                                "window_size": win_size,
                                "cost_label": cost_label,
                                "cost_pct": cost_pct,
                                "window": w_idx + 1,
                                "test_auc": round(test_auc, 4),
                                "best_iter": train_m["best_iter"],
                                "n_trades": bt["n_trades"],
                                "win_rate": round(bt["win_rate"], 4),
                                "avg_ret_pct": round(bt["avg_ret_pct"], 4),
                                "pnl_pct": round(bt["pnl_pct"], 4),
                                "sharpe": round(bt["sharpe"], 4),
                                "max_dd_pct": round(bt["max_dd_pct"], 4),
                                "profit_factor": round(bt["profit_factor"], 4),
                            })

            LOG.info("  HP %s done: %d total result rows", hp["label"], len(results))

    return results


def aggregate_results(results: list[dict]) -> pd.DataFrame:
    """Aggregate per-window results."""
    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    agg = df.groupby(["symbol", "hp_label", "q_long", "q_short", "window_size", "cost_label"]).agg(
        test_auc=("test_auc", "mean"),
        best_iter=("best_iter", "mean"),
        n_trades=("n_trades", "sum"),
        win_rate=("win_rate", "mean"),
        avg_ret_pct=("avg_ret_pct", "mean"),
        pnl_pct=("pnl_pct", "sum"),
        sharpe=("sharpe", "mean"),
        max_dd_pct=("max_dd_pct", "min"),   # worst drawdown across windows
        profit_factor=("profit_factor", "mean"),
        n_windows=("pnl_pct", "count"),
        pnl_positive=("pnl_pct", lambda x: (x > 0).sum()),
    ).reset_index()

    agg["consistency"] = agg.apply(lambda r: f"{int(r['pnl_positive'])}/{int(r['n_windows'])}", axis=1)
    return agg


def print_deep_report(agg_df: pd.DataFrame, per_window_df: pd.DataFrame):
    """Print comprehensive deep optimization report."""
    print("\n" + "=" * 130)
    print("DEEP OPTIMIZATION RESULTS")
    print("=" * 130)

    # 1. Best overall configs
    print("\n--- TOP 30 CONFIGS BY PnL ---")
    top = agg_df.sort_values("pnl_pct", ascending=False).head(30)
    cols = ["symbol", "hp_label", "q_long", "q_short", "window_size", "cost_label",
            "n_trades", "win_rate", "pnl_pct", "sharpe", "max_dd_pct", "profit_factor", "consistency"]
    print(top[cols].to_string(index=False))

    # 2. Impact of hyperparameters
    print("\n--- HYPERPARAMETER IMPACT (avg across all configs per HP) ---")
    hp_impact = agg_df.groupby("hp_label").agg(
        avg_pnl=("pnl_pct", "mean"),
        med_pnl=("pnl_pct", "median"),
        best_pnl=("pnl_pct", "max"),
        avg_sharpe=("sharpe", "mean"),
        pct_positive=("pnl_pct", lambda x: (x > 0).mean() * 100),
    ).sort_values("avg_pnl", ascending=False)
    print(hp_impact.to_string())

    # 3. Impact of Q config
    print("\n--- QUANTILE CONFIG IMPACT ---")
    q_impact = agg_df.groupby(["q_long", "q_short"]).agg(
        avg_pnl=("pnl_pct", "mean"),
        med_pnl=("pnl_pct", "median"),
        best_pnl=("pnl_pct", "max"),
        avg_sharpe=("sharpe", "mean"),
        avg_trades=("n_trades", "mean"),
        pct_positive=("pnl_pct", lambda x: (x > 0).mean() * 100),
    ).sort_values("avg_pnl", ascending=False)
    print(q_impact.to_string())

    # 4. Impact of window size
    print("\n--- ROLLING WINDOW SIZE IMPACT ---")
    win_impact = agg_df.groupby("window_size").agg(
        avg_pnl=("pnl_pct", "mean"),
        med_pnl=("pnl_pct", "median"),
        avg_sharpe=("sharpe", "mean"),
        pct_positive=("pnl_pct", lambda x: (x > 0).mean() * 100),
    ).sort_values("avg_pnl", ascending=False)
    print(win_impact.to_string())

    # 5. Impact of cost assumption
    print("\n--- COST ASSUMPTION IMPACT ---")
    cost_impact = agg_df.groupby("cost_label").agg(
        avg_pnl=("pnl_pct", "mean"),
        med_pnl=("pnl_pct", "median"),
        avg_sharpe=("sharpe", "mean"),
        pct_positive=("pnl_pct", lambda x: (x > 0).mean() * 100),
    ).sort_values("avg_pnl", ascending=False)
    print(cost_impact.to_string())

    # 6. Best config per token
    print("\n--- BEST CONFIG PER TOKEN ---")
    for sym in sorted(agg_df["symbol"].unique()):
        sym_df = agg_df[agg_df.symbol == sym].sort_values("pnl_pct", ascending=False)
        best = sym_df.iloc[0]
        print(f"\n  {sym}:")
        print(f"    Best: HP={best['hp_label']} Q{int(best['q_long'])}/{int(best['q_short'])} "
              f"Win={int(best['window_size'])} Cost={best['cost_label']}")
        print(f"    PnL={best['pnl_pct']:+.2f}% Sharpe={best['sharpe']:+.3f} "
              f"WR={best['win_rate']*100:.1f}% Trades={int(best['n_trades'])} Cons={best['consistency']}")
        print(f"    MaxDD={best['max_dd_pct']:.2f}% PF={best['profit_factor']:.2f}")
        # Show top 3
        for _, r in sym_df.head(3).iterrows():
            print(f"    #{sym_df.index.get_loc(_)+1}: HP={r['hp_label']} Q{int(r['q_long'])}/{int(r['q_short'])} "
                  f"Win={int(r['window_size'])} Cost={r['cost_label']} PnL={r['pnl_pct']:+.2f}% Sharpe={r['sharpe']:+.3f} Cons={r['consistency']}")

    # 7. 180d vs 90d comparison
    print("\n--- COMPARISON WITH 90d BASELINE ---")
    # We compare with the previous sweep results
    for sym in sorted(agg_df["symbol"].unique()):
        sym_df = agg_df[agg_df.symbol == sym]
        best = sym_df.sort_values("pnl_pct", ascending=False).iloc[0]
        total_trades = int(best["n_trades"])
        avg_per_window = total_trades / int(best["n_windows"]) if best["n_windows"] > 0 else 0
        print(f"  {sym}: {avg_per_window:.1f} trades/window (90d was ~6-7) → "
              f"{'MORE' if avg_per_window > 10 else 'SIMILAR'} statistical power")

    # 8. HONEST ASSESSMENT
    print("\n" + "=" * 130)
    print("HONEST ASSESSMENT")
    print("=" * 130)

    robust = agg_df[(agg_df.pnl_pct > 10) & (agg_df.pnl_positive >= 3)]
    if len(robust) > 0:
        print(f"\n  CONFIGS with PnL>10% AND >=3/4 windows positive: {len(robust)}")
        for _, r in robust.sort_values("pnl_pct", ascending=False).head(15).iterrows():
            print(f"    {r['symbol']:12s} HP={r['hp_label']:10s} Q{int(r['q_long']):2d}/{int(r['q_short']):2d} "
                  f"Win={int(r['window_size']):3d} Cost={r['cost_label']:5s} "
                  f"PnL={r['pnl_pct']:+7.2f}% Sharpe={r['sharpe']:+.3f} DD={r['max_dd_pct']:.1f}% PF={r['profit_factor']:.2f} Cons={r['consistency']}")
    else:
        print("\n  NO robust configs with PnL>10% across 3/4 windows")

    very_robust = agg_df[(agg_df.pnl_pct > 10) & (agg_df.pnl_positive >= 4)]
    if len(very_robust) > 0:
        print(f"\n  VERY ROBUST (PnL>10%, 4/4 windows): {len(very_robust)} — STRONG signal")
    else:
        print(f"\n  NO configs with 4/4 windows AND PnL>10% — signal is weak")

    # Check if HP matters
    print("\n  DOES HYPERPARAMETER TUNING HELP?")
    for sym in sorted(agg_df["symbol"].unique()):
        sym_df = agg_df[agg_df.symbol == sym]
        by_hp = sym_df.groupby("hp_label")["pnl_pct"].max().sort_values(ascending=False)
        best_hp = by_hp.index[0]
        worst_hp = by_hp.index[-1]
        print(f"    {sym}: best HP={best_hp} ({by_hp[best_hp]:+.1f}%), worst HP={worst_hp} ({by_hp[worst_hp]:+.1f}%), "
              f"Δ={by_hp[best_hp]-by_hp[worst_hp]:+.1f}pp")

    # Final recommendation
    print("\n  RECOMMENDATION:")
    if len(very_robust) > 0:
        best_overall = very_robust.sort_values("pnl_pct", ascending=False).iloc[0]
        print(f"    STRONG edge found: {best_overall['symbol']} with {best_overall['hp_label']} "
              f"Q{int(best_overall['q_long'])}/{int(best_overall['q_short'])} "
              f"PnL={best_overall['pnl_pct']:+.1f}% 4/4 consistency")
        print(f"    → Ready for paper trading with this config")
    elif len(robust) > 0:
        print(f"    MODERATE edge found in {len(robust)} configs")
        print(f"    → Paper trading with tight risk controls")
        print(f"    → Expected to be marginal; may need feature engineering")
    else:
        print(f"    NO real edge found — model needs fundamental changes")
        print(f"    → Consider: funding rate, OI, orderbook, alternative architectures")


def main():
    parser = argparse.ArgumentParser(description="Deep parameter optimization for PPMT")
    parser.add_argument("--symbols", default=None, help="comma-separated (default: top 4)")
    parser.add_argument("--days", type=int, default=180, help="data window (default 180)")
    parser.add_argument("--exchange", default="bybit")
    parser.add_argument("--save-csv", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    symbols = args.symbols.split(",") if args.symbols else BEST_TOKENS
    save_csv = args.save_csv or f"data/sweep_results/deep_opt_{args.days}d.csv"

    total_configs = len(symbols) * len(HP_GRID) * len(Q_CONFIGS) * len(WINDOW_SIZES) * len(COST_CONFIGS) * N_WINDOWS
    LOG.info("DEEP OPTIMIZATION: %d symbols × %d HP × %d Q × %d windows × %d costs × %d folds = %d configs",
             len(symbols), len(HP_GRID), len(Q_CONFIGS), len(WINDOW_SIZES), len(COST_CONFIGS), N_WINDOWS, total_configs)

    feed = Feed(exchange_id=args.exchange)
    t0 = time.time()

    results = run_deep_optimization(feed, symbols, days=args.days)

    elapsed = time.time() - t0
    LOG.info("Total time: %.1fs, %d result rows", elapsed, len(results))

    if not results:
        LOG.error("No results generated")
        return

    per_window_df = pd.DataFrame(results)
    Path(save_csv).parent.mkdir(parents=True, exist_ok=True)
    per_window_df.to_csv(save_csv, index=False)
    LOG.info("Per-window results saved to %s", save_csv)

    agg_df = aggregate_results(results)
    agg_path = save_csv.replace(".csv", "_agg.csv")
    agg_df.to_csv(agg_path, index=False)

    print_deep_report(agg_df, per_window_df)


if __name__ == "__main__":
    main()
