"""
grid_search.py — v8 Parametric Grid Search

Builds dataset ONCE, then tests multiple configurations:
  - Signal thresholds (breakout_up/down sensitivity)
  - EV thresholds
  - TP/SL ratios
  - Direction-aware vs full two-sided training
  - Pattern gating rules

Usage:
    python -m scripts.v8.grid_search --symbols SOL/USDT,AVAX/USDT,XRP/USDT --days 90

Output: comparison table of all configurations
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from itertools import product
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

pd.options.mode.copy_on_write = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.v7.paper_trader.feed import Feed
from scripts.v8.features import FEATURE_NAMES, N_FEATURES, compute_features
from scripts.v8.labels import compute_ev_labels_both_sides, label_stats
from scripts.v8.model import (
    train_model, build_dataset, save_model,
    LOOKAHEAD, ATR_LAG_OFFSET, DEFAULT_COST, DEFAULT_PARAMS,
    MAX_HOLD_BARS, MODEL_DIR, _expand_to_two_sided,
)
from scripts.v8.backtest import run_backtest

LOG = logging.getLogger("v8_grid")

DEFAULT_SYMBOLS = ["SOL/USDT", "AVAX/USDT", "XRP/USDT"]


# ── Configurable signal generation ──────────────────────────────────

def recompute_signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Recompute pattern signal columns based on config thresholds.

    Uses the CONTINUOUS features already in df (close_position_20,
    vol_ratio, breakout_strength, ema_alignment, ema21_bounce_score)
    to regenerate binary signal columns with different thresholds.
    """
    bu_thresh = cfg.get("bu_pos_thresh", 0.85)
    bd_pos_thresh = cfg.get("bd_pos_thresh", 0.15)
    vol_thresh = cfg.get("vol_thresh", 1.1)
    brk_thresh = cfg.get("brk_thresh", 0.005)
    eb_thresh = cfg.get("eb_thresh", 0.5)
    use_ema_filter = cfg.get("use_ema_filter", True)

    ema_long = df["ema_alignment"].values > 0 if use_ema_filter else np.ones(len(df), dtype=bool)
    ema_short = df["ema_alignment"].values < 0 if use_ema_filter else np.ones(len(df), dtype=bool)

    df = df.copy()

    df["signal_breakout_up"] = (
        (df["close_position_20"].values > bu_thresh) &
        (df["vol_ratio"].values > vol_thresh) &
        (df["breakout_strength"].values > brk_thresh) &
        ema_long
    ).astype(float)

    df["signal_breakout_down"] = (
        (df["close_position_20"].values < bd_pos_thresh) &
        (df["vol_ratio"].values > vol_thresh) &
        (df["breakout_strength"].values > brk_thresh) &
        ema_short
    ).astype(float)

    df["signal_ema_bounce"] = (
        df["ema21_bounce_score"].values > eb_thresh
    ).astype(float)

    # level_test: low position + low vol + downtrend
    df["signal_level_test"] = (
        (df["close_position_20"].values < 0.15) &
        (df["vol_ratio"].values < 1.5) &
        (df["ema_alignment"].values < 0)
    ).astype(float)

    return df


# ── Configurable expansion ──────────────────────────────────────────

def expand_direction_aware(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Direction-aware expansion — only create rows for allowed directions."""
    has_bu = df["signal_breakout_up"].values > 0.5
    has_bd = df["signal_breakout_down"].values > 0.5
    has_eb = df["signal_ema_bounce"].values > 0.5
    has_lt = df["signal_level_test"].values > 0.5
    has_any = has_bu | has_bd | has_eb | has_lt
    no_signal = ~has_any

    include_no_signal = cfg.get("include_no_signal", True)

    if include_no_signal:
        long_mask = has_bu | no_signal
        short_mask = has_eb | has_lt | no_signal
    else:
        # ONLY pattern bars — much smaller training set
        long_mask = has_bu
        short_mask = has_eb | has_lt

    long_rows = df[long_mask].copy()
    long_rows["trade_direction"] = 1.0
    long_rows["ev_label"] = long_rows["long_ev"]

    short_rows = df[short_mask].copy()
    short_rows["trade_direction"] = -1.0
    short_rows["ev_label"] = short_rows["short_ev"]

    result = pd.concat([long_rows, short_rows], ignore_index=True)
    result = result.dropna(subset=["ev_label"])

    return result


def expand_full_two_sided(df: pd.DataFrame) -> pd.DataFrame:
    """Original expansion: each bar -> LONG + SHORT."""
    long_rows = df.copy()
    long_rows["trade_direction"] = 1.0
    long_rows["ev_label"] = long_rows["long_ev"]

    short_rows = df.copy()
    short_rows["trade_direction"] = -1.0
    short_rows["ev_label"] = short_rows["short_ev"]

    result = pd.concat([long_rows, short_rows], ignore_index=True)
    result = result.dropna(subset=["ev_label"])
    return result


# ── Configurable backtest ───────────────────────────────────────────

def run_config_backtest(
    model: lgb.Booster,
    holdout: pd.DataFrame,
    cfg: dict,
) -> dict:
    """Run backtest with specific config parameters."""
    tp_atr = cfg.get("tp_atr_mult", 1.5)
    sl_atr = cfg.get("sl_atr_mult", 1.0)
    ev_thr_long = cfg.get("ev_threshold_long", 0.01)
    ev_thr_short = cfg.get("ev_threshold_short", 0.01)

    # Recompute signals for holdout with this config's thresholds
    holdout_cfg = recompute_signals(holdout, cfg)

    # Predict both directions
    X_hold = holdout_cfg[FEATURE_NAMES].values.astype(np.float32)
    td_idx = FEATURE_NAMES.index("trade_direction")

    X_long = X_hold.copy()
    X_long[:, td_idx] = 1.0
    preds_long = model.predict(X_long)

    X_short = X_hold.copy()
    X_short[:, td_idx] = -1.0
    preds_short = model.predict(X_short)

    result = run_backtest(
        predictions_long=preds_long,
        predictions_short=preds_short,
        closes=holdout_cfg["close"].values,
        highs=holdout_cfg["high"].values,
        lows=holdout_cfg["low"].values,
        atr_14=holdout_cfg["_atr_14_price"].values,
        symbols=holdout_cfg["symbol"].values,
        signals_breakout_up=holdout_cfg["signal_breakout_up"].values,
        signals_breakout_down=holdout_cfg["signal_breakout_down"].values,
        signals_ema_bounce=holdout_cfg["signal_ema_bounce"].values,
        signals_level_test=holdout_cfg["signal_level_test"].values,
        ev_threshold_long=ev_thr_long,
        ev_threshold_short=ev_thr_short,
        tp_atr_mult=tp_atr,
        sl_atr_mult=sl_atr,
        max_hold=MAX_HOLD_BARS,
        atr_lag_offset=ATR_LAG_OFFSET,
        pattern_gating=True,
    )

    return {
        "trades": result.n_trades,
        "long": result.n_long,
        "short": result.n_short,
        "tp": result.n_tp,
        "sl": result.n_sl,
        "time_stop": result.n_time_stop,
        "no_signal": result.n_no_signal,
        "blocked_hole": result.n_blocked_hole,
        "win_rate": result.win_rate * 100,
        "avg_pnl": result.avg_pnl_pct,
        "total_pnl": result.total_pnl_pct,
        "sharpe": result.sharpe,
        "max_dd": result.max_dd_pct,
        "pf": result.profit_factor,
    }


# ── Main grid search ────────────────────────────────────────────────

def build_raw_dataset(feed, symbols, days):
    """Build dataset with MOST RELAXED signal thresholds so we can
    recompute tighter thresholds later without rebuilding."""
    from scripts.v8.features import compute_features, symbol_to_sector, SECTOR_INDEX

    all_dfs = []
    for symbol in symbols:
        LOG.info("Building dataset for %s...", symbol)
        try:
            ohlcv = feed.fetch_history(symbol, "5m", limit=int(days * 288 * 1.1))
            btc = feed.fetch_history("BTC/USDT", "5m", limit=int(days * 288 * 1.1))
            eth = feed.fetch_history("ETH/USDT", "5m", limit=int(days * 288 * 1.1))

            ohlcv_df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            btc_df = pd.DataFrame(btc, columns=["timestamp", "open", "high", "low", "close", "volume"])
            eth_df = pd.DataFrame(eth, columns=["timestamp", "open", "high", "low", "close", "volume"])

            ohlcv_df = ohlcv_df.drop_duplicates(subset=["timestamp"], keep="first")
            btc_df = btc_df.drop_duplicates(subset=["timestamp"], keep="first")
            eth_df = eth_df.drop_duplicates(subset=["timestamp"], keep="first")

            common_ts = set(ohlcv_df["timestamp"]) & set(btc_df["timestamp"]) & set(eth_df["timestamp"])
            ohlcv_df = ohlcv_df[ohlcv_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
            btc_df = btc_df[btc_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
            eth_df = eth_df[eth_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)

            ohlcv_df = pd.DataFrame({col: ohlcv_df[col].values.copy() for col in ohlcv_df.columns})
            btc_df = pd.DataFrame({col: btc_df[col].values.copy() for col in btc_df.columns})
            eth_df = pd.DataFrame({col: eth_df[col].values.copy() for col in eth_df.columns})

            feat_df = compute_features(ohlcv_df, btc_df, eth_df, symbol=symbol)
            feat_df = pd.DataFrame({col: feat_df[col].values.copy() for col in feat_df.columns})

            atr_14_price = feat_df["_atr_14_price"].values.copy()
            long_labels, short_labels = compute_ev_labels_both_sides(
                closes=feat_df["close"].values,
                highs=feat_df["high"].values,
                lows=feat_df["low"].values,
                atr_14=atr_14_price,
                tp_atr_mult=1.5,
                sl_atr_mult=1.0,
                lookahead=LOOKAHEAD,
                atr_lag_offset=ATR_LAG_OFFSET,
            )
            feat_df["long_ev"] = long_labels
            feat_df["short_ev"] = short_labels
            feat_df["symbol"] = symbol

            all_dfs.append(feat_df)
            LOG.info("  %s: %d rows", symbol, len(feat_df))

        except Exception as e:
            LOG.error("Failed for %s: %s", symbol, e)
            continue

    combined = pd.concat(all_dfs, ignore_index=True)
    LOG.info("Combined raw dataset: %d rows", len(combined))
    return combined


def prepare_holdout(dataset: pd.DataFrame):
    """Split dataset into train + holdout (per-symbol temporal 85/15)."""
    # Use raw rows (before expansion) — take one row per timestamp
    # Use LONG rows (trade_direction=1.0) which have both long_ev and short_ev
    raw = dataset[dataset["trade_direction"] == 1.0].copy() if "trade_direction" in dataset.columns else dataset.copy()
    raw = raw.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    holdout_parts = []
    for sym in raw["symbol"].unique():
        sym_data = raw[raw["symbol"] == sym]
        cutoff = int(len(sym_data) * 0.85)
        holdout_parts.append(sym_data.iloc[cutoff:])

    holdout = pd.concat(holdout_parts) if holdout_parts else pd.DataFrame()
    return holdout


def run_grid_search(args):
    """Main grid search entry point."""
    feed = Feed(exchange_id=args.exchange)
    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS

    # ── 1. Build raw dataset ONCE ──
    LOG.info("=" * 60)
    LOG.info("V8 GRID SEARCH — Building dataset ONCE")
    LOG.info("=" * 60)
    t0 = time.time()
    raw_df = build_raw_dataset(feed, symbols, args.days)
    LOG.info("Dataset built in %.1fs: %d rows", time.time() - t0, len(raw_df))

    # ── 2. Define grid configurations ──
    # Each config is a dict of parameters to test
    configs = []

    # Signal threshold variations
    signal_configs = [
        {"name": "S1_relax85", "bu_pos_thresh": 0.85, "vol_thresh": 1.1,
         "bd_pos_thresh": 0.15, "use_ema_filter": True},
        {"name": "S2_relax80", "bu_pos_thresh": 0.80, "vol_thresh": 1.0,
         "bd_pos_thresh": 0.20, "use_ema_filter": True},
        {"name": "S3_no_ema", "bu_pos_thresh": 0.85, "vol_thresh": 1.1,
         "bd_pos_thresh": 0.15, "use_ema_filter": False},
        {"name": "S4_strict90", "bu_pos_thresh": 0.90, "vol_thresh": 1.2,
         "bd_pos_thresh": 0.10, "use_ema_filter": True},
    ]

    # Training mode variations
    train_configs = [
        {"name": "T1_dir_aware", "expansion": "direction_aware", "include_no_signal": True},
        {"name": "T2_pattern_only", "expansion": "direction_aware", "include_no_signal": False},
        {"name": "T3_full", "expansion": "full"},
    ]

    # Backtest parameter variations
    bt_configs = [
        {"name": "B1_1.5x1.0", "tp_atr_mult": 1.5, "sl_atr_mult": 1.0,
         "ev_threshold_long": 0.01, "ev_threshold_short": 0.01},
        {"name": "B2_2.0x0.8", "tp_atr_mult": 2.0, "sl_atr_mult": 0.8,
         "ev_threshold_long": 0.01, "ev_threshold_short": 0.01},
        {"name": "B3_2.5x0.8", "tp_atr_mult": 2.5, "sl_atr_mult": 0.8,
         "ev_threshold_long": 0.01, "ev_threshold_short": 0.01},
        {"name": "B4_ev0", "tp_atr_mult": 1.5, "sl_atr_mult": 1.0,
         "ev_threshold_long": 0.0, "ev_threshold_short": 0.0},
    ]

    # Generate full grid (signal × train × backtest)
    for s_cfg, t_cfg, b_cfg in product(signal_configs, train_configs, bt_configs):
        cfg = {}
        cfg["config_name"] = f"{s_cfg['name']}_{t_cfg['name']}_{b_cfg['name']}"
        cfg.update(s_cfg)
        cfg.update(t_cfg)
        cfg.update(b_cfg)
        # Remove name keys from merged (they're just for labeling)
        for key in ["name"]:
            cfg.pop(key, None)
        configs.append(cfg)

    LOG.info("Grid: %d configurations (%d signals × %d training × %d backtest)",
             len(configs), len(signal_configs), len(train_configs), len(bt_configs))

    # ── 3. Run each configuration ──
    results = []
    for i, cfg in enumerate(configs):
        cfg_name = cfg["config_name"]
        LOG.info("\n── Config %d/%d: %s ──", i + 1, len(configs), cfg_name)

        try:
            # Recompute signals with this config's thresholds
            df_cfg = recompute_signals(raw_df.copy(), cfg)

            # Expand to two-sided
            expansion = cfg.get("expansion", "direction_aware")
            if expansion == "direction_aware":
                dataset = expand_direction_aware(df_cfg, cfg)
            else:
                dataset = expand_full_two_sided(df_cfg)

            if len(dataset) < 500:
                LOG.warning("  Too few rows (%d), skipping", len(dataset))
                continue

            LOG.info("  Dataset: %d rows (LONG=%d SHORT=%d)",
                     len(dataset),
                     (dataset["trade_direction"] == 1.0).sum(),
                     (dataset["trade_direction"] == -1.0).sum())

            # Train model
            clean = dataset.dropna(subset=["ev_label"])
            model, metrics = train_model(clean, num_boost_round=300)

            # Quick CV score (3-fold to save time)
            from scripts.v8.validation import PurgedKFold
            cv = PurgedKFold(n_splits=3, lookahead=LOOKAHEAD, embargo=3)
            cv_sharpes = []
            for fold_idx, (train_idx, test_idx) in enumerate(cv.split(clean)):
                train_fold = clean.iloc[train_idx]
                test_fold = clean.iloc[test_idx]
                if len(train_fold) < 500 or len(test_fold) < 50:
                    continue
                try:
                    bst_fold, _ = train_model(train_fold, val_df=test_fold, num_boost_round=200)
                    X_t = test_fold[FEATURE_NAMES].values.astype(np.float32)
                    y_t = test_fold["ev_label"].values.astype(np.float32)
                    p_t = bst_fold.predict(X_t)
                    pnl = np.sign(p_t) * y_t
                    cv_sharpes.append(float(np.mean(pnl) / max(np.std(pnl), 1e-10)))
                except Exception:
                    pass
            cv_sharpe = np.mean(cv_sharpes) if cv_sharpes else 0.0

            # Prepare holdout
            holdout = prepare_holdout(dataset)
            if len(holdout) < 50:
                LOG.warning("  Holdout too small (%d), skipping backtest", len(holdout))
                bt_result = {"trades": 0, "long": 0, "short": 0, "total_pnl": 0,
                             "win_rate": 0, "sharpe": 0, "max_dd": 0, "pf": 0}
            else:
                bt_result = run_config_backtest(model, holdout, cfg)

            result = {
                "config": cfg_name,
                "n_rows": len(dataset),
                "n_long": int((dataset["trade_direction"] == 1.0).sum()),
                "n_short": int((dataset["trade_direction"] == -1.0).sum()),
                "cv_sharpe": cv_sharpe,
                **bt_result,
            }
            results.append(result)

            LOG.info("  → CV=%.3f  BT: %d trades (%dL/%dS) PnL=%.1f%% WR=%.1f%% Sharpe=%.1f",
                     cv_sharpe, bt_result["trades"], bt_result["long"],
                     bt_result["short"], bt_result["total_pnl"],
                     bt_result["win_rate"], bt_result["sharpe"])

        except Exception as e:
            LOG.error("  Config %s FAILED: %s", cfg_name, e)
            continue

    # ── 4. Print comparison table ──
    print("\n" + "=" * 140)
    print("V8 GRID SEARCH RESULTS")
    print("=" * 140)
    print(f"  {'Config':<35} {'Rows':>7} {'L':>5} {'S':>5} {'CV_Shp':>7} "
          f"{'Trades':>6} {'Long':>5} {'Short':>5} {'WR%':>5} {'PnL%':>8} "
          f"{'Sharpe':>8} {'MaxDD':>7} {'PF':>5}")
    print("-" * 140)

    # Sort by total PnL descending
    results.sort(key=lambda r: r.get("total_pnl", -999), reverse=True)

    for r in results:
        print(f"  {r['config']:<35} {r['n_rows']:>7} {r['n_long']:>5} {r['n_short']:>5} "
              f"{r['cv_sharpe']:>7.3f} {r['trades']:>6} {r['long']:>5} {r['short']:>5} "
              f"{r['win_rate']:>5.1f} {r['total_pnl']:>+8.1f} "
              f"{r['sharpe']:>8.1f} {r['max_dd']:>7.1f} {r['pf']:>5.2f}")

    # Highlight best
    if results:
        best = results[0]
        print(f"\n  🏆 BEST: {best['config']} — PnL={best['total_pnl']:+.1f}% "
              f"Sharpe={best['sharpe']:.1f} WR={best['win_rate']:.1f}% "
              f"{best['trades']} trades ({best['long']}L/{best['short']}S)")

    print("=" * 140)

    # Save results
    results_path = MODEL_DIR / "grid_search_results.csv"
    pd.DataFrame(results).to_csv(results_path, index=False)
    LOG.info("Results saved to %s", results_path)

    return results


def main():
    parser = argparse.ArgumentParser(description="v8 Grid Search")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--exchange", default="bybit")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    run_grid_search(args)


if __name__ == "__main__":
    main()
