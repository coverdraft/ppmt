"""
backtest.py — V10: Adaptive Exit Backtest using dual models

Uses both models:
  - Binary classifier: "Should I enter?" (probability > threshold)
  - MFE regressor: "How much potential does this entry have?" → sets exit params

Adaptive exit logic:
  - If predicted MFE/MAE > 3: WIDE SL (1.0%), LONG hold (30min), loose trail
  - If predicted MFE/MAE 1.5-3: MEDIUM SL (0.5%), MEDIUM hold (15min)
  - If predicted MFE/MAE < 1.5: TIGHT SL (0.3%), SHORT hold (10min)

This captures the insight: good entries deserve room to breathe,
bad entries should be cut quickly. The regressor tells us WHICH is which.

Also runs standard V9-style backtests for comparison.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

pd.options.mode.copy_on_write = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.v10.build_dataset import compute_features_1m, download_1m, FEATURE_NAMES, N_FEATURES

LOG = logging.getLogger("v10_bt")

DATA_DIR = PROJECT_ROOT / "data" / "v10"
MODEL_DIR = DATA_DIR / "models"


@dataclass
class Trade:
    entry_bar: int
    exit_bar: int
    direction: str
    entry_price: float
    exit_price: float
    exit_reason: str  # "SL", "TIME_STOP", "TRAILING_STOP", "TP"
    pnl_pct: float
    pnl_net: float
    bars_held: int
    prob: float  # binary classifier probability
    mfe_pred: float  # predicted MFE/MAE ratio
    symbol: str = ""


def run_backtest_adaptive(
    df: pd.DataFrame,
    model_binary: lgb.Booster,
    model_mfe: lgb.Booster,
    feature_cols: list,
    threshold: float = 0.5,
    cost_pct: float = 0.06,
    min_hold_bars: int = 2,
    cooldown_bars: int = 5,
) -> list[Trade]:
    """Run backtest with ADAPTIVE exits based on predicted MFE/MAE ratio."""
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    symbols = df["symbol"].values if "symbol" in df.columns else np.array(["all"] * len(df))

    # Predict with both models
    X = df[feature_cols].values.astype(np.float32)
    probs = model_binary.predict(X)
    mfe_preds = model_mfe.predict(X)

    trades = []
    positions = {}
    last_exit_bar = {}

    for i in range(60, len(df)):
        sym = str(symbols[i])

        # ── Check existing position ──
        if sym in positions:
            pos = positions[sym]
            should_close = False
            exit_reason = ""
            exit_price = closes[i]
            bars_held = i - pos["entry_bar"]

            sl_pct = pos["sl_pct"]
            max_hold = pos["max_hold"]
            tp_pct = pos["tp_pct"]
            trail_pct = pos["trail_pct"]

            # Update trailing stop high/low
            if pos["direction"] == "LONG":
                if highs[i] > pos.get("trail_high", pos["entry_price"]):
                    pos["trail_high"] = highs[i]
                    # Move trailing stop up
                    pos["trail_sl"] = highs[i] * (1 - trail_pct / 100)
            else:
                if lows[i] < pos.get("trail_low", pos["entry_price"]):
                    pos["trail_low"] = lows[i]
                    pos["trail_sl"] = lows[i] * (1 + trail_pct / 100)

            # SL check
            if pos["direction"] == "LONG":
                sl_price = pos["entry_price"] * (1 - sl_pct / 100)
                if lows[i] <= sl_price:
                    should_close = True
                    exit_reason = "SL"
                    exit_price = sl_price
            else:
                sl_price = pos["entry_price"] * (1 + sl_pct / 100)
                if highs[i] >= sl_price:
                    should_close = True
                    exit_reason = "SL"
                    exit_price = sl_price

            # TP check
            if not should_close and tp_pct > 0:
                if pos["direction"] == "LONG":
                    tp_price = pos["entry_price"] * (1 + tp_pct / 100)
                    if highs[i] >= tp_price:
                        should_close = True
                        exit_reason = "TP"
                        exit_price = tp_price
                else:
                    tp_price = pos["entry_price"] * (1 - tp_pct / 100)
                    if lows[i] <= tp_price:
                        should_close = True
                        exit_reason = "TP"
                        exit_price = tp_price

            # Trailing stop check
            if not should_close and "trail_sl" in pos and bars_held > 3:
                if pos["direction"] == "LONG":
                    if lows[i] <= pos["trail_sl"]:
                        should_close = True
                        exit_reason = "TRAILING_STOP"
                        exit_price = pos["trail_sl"]
                else:
                    if highs[i] >= pos["trail_sl"]:
                        should_close = True
                        exit_reason = "TRAILING_STOP"
                        exit_price = pos["trail_sl"]

            # Time stop
            if not should_close and bars_held >= max_hold:
                should_close = True
                exit_reason = "TIME_STOP"
                exit_price = closes[i]

            # Min hold
            if should_close and bars_held < min_hold_bars:
                should_close = False

            if should_close:
                if pos["direction"] == "LONG":
                    pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
                else:
                    pnl_pct = (pos["entry_price"] - exit_price) / pos["entry_price"] * 100
                pnl_net = pnl_pct - cost_pct

                trades.append(Trade(
                    entry_bar=pos["entry_bar"], exit_bar=i,
                    direction=pos["direction"],
                    entry_price=pos["entry_price"], exit_price=exit_price,
                    exit_reason=exit_reason,
                    pnl_pct=pnl_pct, pnl_net=pnl_net,
                    bars_held=bars_held,
                    prob=pos["prob"],
                    mfe_pred=pos["mfe_pred"],
                    symbol=sym,
                ))
                del positions[sym]
                last_exit_bar[sym] = i

        # ── Check for new entry ──
        if sym in positions:
            continue

        if sym in last_exit_bar and i - last_exit_bar[sym] < cooldown_bars:
            continue

        prob = float(probs[i])
        if prob < threshold:
            continue

        mfe_pred = float(mfe_preds[i])

        # Direction
        td = float(df["trade_direction"].iloc[i]) if "trade_direction" in df.columns else 0
        if pd.isna(td) or td == 0:
            td = 1.0 if float(df["ema_alignment"].iloc[i]) > 0 else -1.0

        direction = "LONG" if td > 0 else "SHORT"

        # ── ADAPTIVE EXIT PARAMETERS based on predicted MFE/MAE ──
        if mfe_pred >= 3.0:
            # HIGH potential: give it room
            sl_pct = 1.0      # wide SL
            tp_pct = 1.5      # take profit at 1.5%
            max_hold = 30     # 30 minutes
            trail_pct = 0.5   # trail at 0.5% from high
        elif mfe_pred >= 1.5:
            # MEDIUM potential: balanced
            sl_pct = 0.5
            tp_pct = 0.8
            max_hold = 15
            trail_pct = 0.4
        else:
            # LOW potential: tight management
            sl_pct = 0.3
            tp_pct = 0.5
            max_hold = 10
            trail_pct = 0.3

        positions[sym] = {
            "entry_bar": i,
            "direction": direction,
            "entry_price": closes[i],
            "prob": prob,
            "mfe_pred": mfe_pred,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "max_hold": max_hold,
            "trail_pct": trail_pct,
        }

    return trades


def run_backtest_fixed(
    df: pd.DataFrame,
    model: lgb.Booster,
    feature_cols: list,
    threshold: float = 0.5,
    sl_pct: float = 0.5,
    max_hold_min: float = 15,
    cost_pct: float = 0.06,
    min_hold_bars: int = 2,
    cooldown_bars: int = 5,
) -> list[Trade]:
    """Run backtest with FIXED exits (same as V9, for comparison)."""
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    symbols = df["symbol"].values if "symbol" in df.columns else np.array(["all"] * len(df))

    if "_prob" in df.columns:
        probs = df["_prob"].values
    else:
        X = df[feature_cols].values.astype(np.float32)
        probs = model.predict(X)

    trades = []
    positions = {}
    last_exit_bar = {}

    for i in range(60, len(df)):
        sym = str(symbols[i])

        if sym in positions:
            pos = positions[sym]
            should_close = False
            exit_reason = ""
            exit_price = closes[i]
            bars_held = i - pos["entry_bar"]

            if pos["direction"] == "LONG":
                sl_price = pos["entry_price"] * (1 - sl_pct / 100)
                if lows[i] <= sl_price:
                    should_close = True
                    exit_reason = "SL"
                    exit_price = sl_price
            else:
                sl_price = pos["entry_price"] * (1 + sl_pct / 100)
                if highs[i] >= sl_price:
                    should_close = True
                    exit_reason = "SL"
                    exit_price = sl_price

            if bars_held >= max_hold_min and not should_close:
                should_close = True
                exit_reason = "TIME_STOP"
                exit_price = closes[i]

            if should_close and bars_held < min_hold_bars:
                should_close = False

            if should_close:
                if pos["direction"] == "LONG":
                    pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
                else:
                    pnl_pct = (pos["entry_price"] - exit_price) / pos["entry_price"] * 100
                pnl_net = pnl_pct - cost_pct

                trades.append(Trade(
                    entry_bar=pos["entry_bar"], exit_bar=i,
                    direction=pos["direction"],
                    entry_price=pos["entry_price"], exit_price=exit_price,
                    exit_reason=exit_reason,
                    pnl_pct=pnl_pct, pnl_net=pnl_net,
                    bars_held=bars_held,
                    prob=pos["prob"], mfe_pred=0.0,
                    symbol=sym,
                ))
                del positions[sym]
                last_exit_bar[sym] = i

        if sym in positions:
            continue
        if sym in last_exit_bar and i - last_exit_bar[sym] < cooldown_bars:
            continue

        prob = float(probs[i])
        if prob < threshold:
            continue

        td = float(df["trade_direction"].iloc[i]) if "trade_direction" in df.columns else 0
        if pd.isna(td) or td == 0:
            td = 1.0 if float(df["ema_alignment"].iloc[i]) > 0 else -1.0

        direction = "LONG" if td > 0 else "SHORT"

        positions[sym] = {
            "entry_bar": i,
            "direction": direction,
            "entry_price": closes[i],
            "prob": prob,
        }

    return trades


def compute_stats(trades: list[Trade]) -> dict:
    if not trades:
        return {"n_trades": 0, "n_long": 0, "n_short": 0,
                "n_sl": 0, "n_ts": 0, "n_tp": 0, "n_trail": 0,
                "wr": 0, "total_pnl": 0, "pf": 0, "max_dd": 0}

    pnls = [t.pnl_net for t in trades]
    n = len(pnls)
    wr = sum(1 for p in pnls if p > 0) / n * 100
    total = sum(pnls)

    n_long = sum(1 for t in trades if t.direction == "LONG")
    n_short = sum(1 for t in trades if t.direction == "SHORT")
    n_sl = sum(1 for t in trades if t.exit_reason == "SL")
    n_ts = sum(1 for t in trades if t.exit_reason == "TIME_STOP")
    n_tp = sum(1 for t in trades if t.exit_reason == "TP")
    n_trail = sum(1 for t in trades if t.exit_reason == "TRAILING_STOP")

    gains = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    pf = gains / max(losses, 1e-10)

    cumulative = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cumulative)
    max_dd = float((cumulative - running_max).min())

    return {
        "n_trades": n, "n_long": n_long, "n_short": n_short,
        "n_sl": n_sl, "n_ts": n_ts, "n_tp": n_tp, "n_trail": n_trail,
        "wr": wr, "total_pnl": total, "pf": pf, "max_dd": max_dd,
    }


def main():
    parser = argparse.ArgumentParser(description="V10 Backtest")
    parser.add_argument("--symbols", default="SOL/USDT,AVAX/USDT,XRP/USDT")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")

    # Load binary classifier
    binary_path = MODEL_DIR / "v10_binary_classifier.lgb"
    binary_meta_path = MODEL_DIR / "v10_binary_meta.json"
    if not binary_path.exists():
        LOG.error("No binary model. Run train.py first!")
        sys.exit(1)

    model_binary = lgb.Booster(model_file=str(binary_path))
    with open(binary_meta_path) as f:
        binary_meta = json.load(f)

    feature_cols = binary_meta["feature_cols"]
    threshold = args.threshold or binary_meta.get("best_threshold", 0.5)

    LOG.info("Binary model loaded. Threshold=%.3f Features=%d", threshold, len(feature_cols))

    # Load MFE regressor (optional — adaptive exits)
    mfe_path = MODEL_DIR / "v10_mfe_regressor.lgb"
    model_mfe = None
    if mfe_path.exists():
        model_mfe = lgb.Booster(model_file=str(mfe_path))
        LOG.info("MFE regressor loaded")
    else:
        LOG.warning("No MFE regressor found. Adaptive exits disabled.")

    # Build 1m data
    symbols = args.symbols.split(",")
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.days * 86400 * 1000

    # Download BTC data for correlation features
    LOG.info("Downloading BTC data for backtest...")
    btc_df = download_1m("BTC", start_ms, now_ms)
    if len(btc_df) < 100:
        LOG.warning("BTC data insufficient, correlation features will be 0")
        btc_df = None

    all_dfs = []
    for symbol in symbols:
        base = symbol.split("/")[0]
        LOG.info("Fetching 1m data for %s (%d days)...", symbol, args.days)
        try:
            ohlcv_df = download_1m(base, start_ts_ms=start_ms, end_ts_ms=now_ms)
            if ohlcv_df is None or len(ohlcv_df) < 60:
                LOG.warning("  %s: insufficient data, skipping", symbol)
                continue

            ohlcv_cols = ohlcv_df[["open", "high", "low", "close", "volume"]].copy()
            feat_df = compute_features_1m(ohlcv_df, symbol=base, btc_df=btc_df)

            if len(feat_df) == 0:
                continue

            n_feat = len(feat_df)
            n_ohlcv = len(ohlcv_cols)
            if n_feat != n_ohlcv:
                ohlcv_cols = ohlcv_cols.iloc[-n_feat:].reset_index(drop=True)
                feat_df = feat_df.reset_index(drop=True)

            for col in ["open", "high", "low", "close", "volume"]:
                feat_df[col] = ohlcv_cols[col].values

            feat_df["symbol"] = base

            for col in feature_cols:
                if col in feat_df.columns and feat_df[col].isna().any():
                    feat_df[col] = feat_df[col].fillna(0)

            if "trade_direction" not in feat_df.columns or feat_df["trade_direction"].isna().all():
                feat_df["trade_direction"] = np.where(feat_df["ema_alignment"] > 0, 1.0, -1.0)

            all_dfs.append(feat_df)
            LOG.info("  %s: %d bars OK", symbol, len(feat_df))
        except Exception as e:
            LOG.error("Failed for %s: %s", symbol, e)
            continue

    if not all_dfs:
        LOG.error("No data!")
        sys.exit(1)

    combined = pd.concat(all_dfs, ignore_index=True)
    if "timestamp" in combined.columns:
        combined = combined.sort_values("timestamp", ignore_index=True)

    LOG.info("Combined: %d bars", len(combined))

    # ── Probability distribution analysis ──
    X = combined[feature_cols].values.astype(np.float32)
    all_probs = model_binary.predict(X)

    pcts = [50, 75, 90, 95, 99]
    pctiles = np.percentile(all_probs, pcts)
    print(f"\n  -- BINARY CLASSIFIER PROBABILITY DISTRIBUTION --")
    print(f"  Mean={np.mean(all_probs):.4f}  Std={np.std(all_probs):.4f}")
    for p, v in zip(pcts, pctiles):
        n_above = int(np.sum(all_probs >= v))
        print(f"  P{p:<5} = {v:.4f}  ({n_above} bars above)")

    # ── MFE prediction distribution ──
    if model_mfe:
        all_mfe = model_mfe.predict(X)
        print(f"\n  -- MFE/MAE PREDICTION DISTRIBUTION --")
        print(f"  Mean={np.mean(all_mfe):.3f}  Std={np.std(all_mfe):.3f}")
        print(f"  P25={np.percentile(all_mfe, 25):.3f}  P50={np.percentile(all_mfe, 50):.3f}  P75={np.percentile(all_mfe, 75):.3f}")

        # Attach predictions to combined
        combined["_mfe_pred"] = all_mfe

    combined["_prob"] = all_probs

    # ══════════════════════════════════════════════════════════════
    # PRINT RESULTS
    # ══════════════════════════════════════════════════════════════

    print(f"\n{'='*110}")
    print(f"V10 BACKTEST — Dual Model (Classifier + MFE Regressor)")
    print(f"{'='*110}")
    print(f"  Binary: AUC_test={binary_meta.get('auc_test', 0):.4f}  "
          f"AUC_WvL={binary_meta.get('auc_win_vs_lose', 0):.4f}  Thr={threshold:.3f}")
    if model_mfe:
        mfe_meta_path = MODEL_DIR / "v10_mfe_meta.json"
        if mfe_meta_path.exists():
            with open(mfe_meta_path) as f:
                mfe_meta = json.load(f)
            print(f"  MFE: Corr_test={mfe_meta.get('corr_spearman_test', 0):.4f}  "
                  f"AUC_rank={mfe_meta.get('auc_mfe_ranking', 0):.4f}")
    print(f"  Data: {len(combined)} bars  Symbols: {symbols}")
    print(f"{'='*110}")

    # ── Section 1: V9-compatible fixed exits ──
    print(f"\n  -- SECTION 1: FIXED EXITS (V9-compatible) --")
    print(f"  {'Config':<16} {'Trades':>6} {'LONG':>5} {'SHORT':>6} "
          f"{'SL':>5} {'TS':>5} {'WR%':>6} {'PnL%':>8} {'PF':>6} {'MaxDD':>7}")
    print(f"  {'-'*80}")

    configs = [
        ("SL0.5_TS15", 0.5, 15),
        ("SL0.3_TS10", 0.3, 10),
        ("SL0.5_TS30", 0.5, 30),
        ("SL1.0_TS15", 1.0, 15),
        ("SL1.0_TS30", 1.0, 30),
    ]

    for cfg_name, sl, ts in configs:
        trades = run_backtest_fixed(combined, model_binary, feature_cols,
                                     threshold=threshold, sl_pct=sl, max_hold_min=ts)
        stats = compute_stats(trades)
        print(f"  {cfg_name:<16} {stats['n_trades']:>6} {stats['n_long']:>5} {stats['n_short']:>6} "
              f"{stats['n_sl']:>5} {stats['n_ts']:>5} "
              f"{stats['wr']:>6.1f} {stats['total_pnl']:>+8.2f} {stats['pf']:>6.2f} {stats['max_dd']:>7.2f}")

    # ── Section 2: Adaptive exits (MFE-guided) ──
    if model_mfe:
        print(f"\n  -- SECTION 2: ADAPTIVE EXITS (MFE-guided) --")
        print(f"  {'Config':<20} {'Trades':>6} {'LONG':>5} {'SHORT':>6} "
              f"{'SL':>4} {'TP':>4} {'Trail':>5} {'TS':>4} "
              f"{'WR%':>6} {'PnL%':>8} {'PF':>6} {'MaxDD':>7}")
        print(f"  {'-'*95}")

        # Adaptive with different cooldowns
        for cd_name, cd in [("cd5", 5), ("cd15", 15), ("cd30", 30), ("cd60", 60), ("cd120", 120)]:
            trades = run_backtest_adaptive(combined, model_binary, model_mfe,
                                            feature_cols, threshold=threshold,
                                            cooldown_bars=cd)
            stats = compute_stats(trades)
            print(f"  Adaptive_{cd_name:<13} {stats['n_trades']:>6} {stats['n_long']:>5} {stats['n_short']:>6} "
                  f"{stats['n_sl']:>4} {stats['n_tp']:>4} {stats['n_trail']:>5} {stats['n_ts']:>4} "
                  f"{stats['wr']:>6.1f} {stats['total_pnl']:>+8.2f} {stats['pf']:>6.2f} {stats['max_dd']:>7.2f}")

        # Adaptive with different thresholds
        print(f"\n  -- ADAPTIVE THRESHOLD SCAN (cd=15) --")
        for thr in [0.25, 0.30, 0.35, 0.40, 0.50]:
            trades = run_backtest_adaptive(combined, model_binary, model_mfe,
                                            feature_cols, threshold=thr,
                                            cooldown_bars=15)
            stats = compute_stats(trades)
            print(f"  thr={thr:.2f}  N={stats['n_trades']:>5}  "
                  f"SL={stats['n_sl']:>4} TP={stats['n_tp']:>4} Trail={stats['n_trail']:>4} "
                  f"WR={stats['wr']:>5.1f}%  PnL={stats['total_pnl']:>+8.2f}%  PF={stats['pf']:.2f}")

        # ── Section 3: MFE-selective backtest ──
        # Only enter when MFE prediction is HIGH (>= 2.0) — cherry-pick the best entries
        print(f"\n  -- SECTION 3: MFE-SELECTIVE (only high-potential entries) --")
        print(f"  {'Config':<24} {'Trades':>6} {'WR%':>6} {'PnL%':>8} {'PF':>6} {'MaxDD':>7}")
        print(f"  {'-'*60}")

        for mfe_min, cd in [(1.0, 15), (1.5, 15), (2.0, 15), (2.0, 30), (3.0, 15), (3.0, 30)]:
            # Run adaptive but filter: only enter if mfe_pred >= mfe_min
            trades = run_backtest_adaptive(combined, model_binary, model_mfe,
                                            feature_cols, threshold=threshold,
                                            cooldown_bars=cd)
            # Filter: keep only trades with mfe_pred >= mfe_min
            filtered_trades = [t for t in trades if t.mfe_pred >= mfe_min]
            stats = compute_stats(filtered_trades)
            name = f"mfe>={mfe_min:.1f}_cd{cd}"
            print(f"  {name:<24} {stats['n_trades']:>6} {stats['wr']:>6.1f} "
                  f"{stats['total_pnl']:>+8.2f} {stats['pf']:>6.2f} {stats['max_dd']:>7.2f}")

    # ── Cooldown scan for fixed exits ──
    print(f"\n  -- COOLDOWN SCAN (SL=0.5% TS=15min) --")
    for cd in [5, 15, 30, 60, 120, 240]:
        trades = run_backtest_fixed(combined, model_binary, feature_cols,
                                     threshold=threshold, sl_pct=0.5,
                                     max_hold_min=15, cooldown_bars=cd)
        stats = compute_stats(trades)
        print(f"  cd={cd:>3}min  N={stats['n_trades']:>5}  WR={stats['wr']:>5.1f}%  "
              f"PnL={stats['total_pnl']:>+8.2f}%  PF={stats['pf']:.2f}")

    print(f"{'='*110}")


if __name__ == "__main__":
    main()
