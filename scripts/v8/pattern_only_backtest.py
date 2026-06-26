"""
pattern_only_backtest.py — Pure Pattern Signal Backtest (NO MODEL)

Tests whether the PATTERN SIGNALS themselves have edge, without any ML model.
This is the baseline — if pure patterns don't work, no model will help.

Logic:
  - breakout_up → enter LONG (TP=1.5×ATR, SL=1.0×ATR, time_stop=30min)
  - ema_bounce → enter SHORT
  - level_test → enter SHORT
  - breakout_down → NO TRADE (THE HOLE)

Also tests variations:
  - Different TP/SL ratios
  - With/without ema_alignment filter
  - Different signal thresholds
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

pd.options.mode.copy_on_write = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.v7.paper_trader.feed import Feed
from scripts.v8.features import compute_features, symbol_to_sector, SECTOR_INDEX
from scripts.v8.labels import compute_ev_labels_both_sides
from scripts.v8.model import LOOKAHEAD, ATR_LAG_OFFSET

LOG = logging.getLogger("v8_pattern_bt")


def recompute_signals(df, bu_thresh=0.85, vol_thresh=1.1, brk_thresh=0.005,
                      use_ema_filter=False, eb_thresh=0.5):
    """Recompute pattern signals with given thresholds."""
    df = df.copy()

    ema_long = df["ema_alignment"].values > 0 if use_ema_filter else np.ones(len(df), dtype=bool)
    ema_short = df["ema_alignment"].values < 0 if use_ema_filter else np.ones(len(df), dtype=bool)

    df["signal_breakout_up"] = (
        (df["close_position_20"].values > bu_thresh) &
        (df["vol_ratio"].values > vol_thresh) &
        (df["breakout_strength"].values > brk_thresh) &
        ema_long
    ).astype(float)

    df["signal_breakout_down"] = (
        (df["close_position_20"].values < (1.0 - bu_thresh)) &
        (df["vol_ratio"].values > vol_thresh) &
        (df["breakout_strength"].values > brk_thresh) &
        ema_short
    ).astype(float)

    df["signal_ema_bounce"] = (
        df["ema21_bounce_score"].values > eb_thresh
    ).astype(float)

    df["signal_level_test"] = (
        (df["close_position_20"].values < 0.15) &
        (df["vol_ratio"].values < 1.5) &
        ema_short
    ).astype(float)

    return df


def run_pure_pattern_backtest(df, tp_atr=1.5, sl_atr=1.0, max_hold=6,
                               atr_lag=3, cost_pct=0.04, allow_long=True,
                               allow_short=True):
    """Backtest pure pattern signals — no model, just pattern → trade."""
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    atr = df["_atr_14_price"].values
    symbols = df["symbol"].values if "symbol" in df.columns else np.array(["all"] * len(df))

    sbu = df["signal_breakout_up"].values > 0.5
    sbd = df["signal_breakout_down"].values > 0.5
    seb = df["signal_ema_bounce"].values > 0.5
    slt = df["signal_level_test"].values > 0.5

    trades = []
    positions = {}  # symbol → position dict

    for i in range(atr_lag, len(closes)):
        sym = str(symbols[i])

        # ── Check existing position ──
        if sym in positions:
            pos = positions[sym]
            should_close = False
            exit_reason = ""
            exit_price = closes[i]
            bars_held = i - pos["entry_bar"]

            # TP
            if pos["direction"] == "LONG" and highs[i] >= pos["tp_price"]:
                should_close = True; exit_reason = "TP"; exit_price = pos["tp_price"]
            elif pos["direction"] == "SHORT" and lows[i] <= pos["tp_price"]:
                should_close = True; exit_reason = "TP"; exit_price = pos["tp_price"]

            # SL (conservative: SL wins tie)
            if pos["direction"] == "LONG" and lows[i] <= pos["sl_price"]:
                if not should_close or exit_reason == "TP":
                    should_close = True; exit_reason = "SL"; exit_price = pos["sl_price"]
            elif pos["direction"] == "SHORT" and highs[i] >= pos["sl_price"]:
                if not should_close or exit_reason == "TP":
                    should_close = True; exit_reason = "SL"; exit_price = pos["sl_price"]

            # Time stop
            if bars_held >= max_hold and not should_close:
                should_close = True; exit_reason = "TIME_STOP"; exit_price = closes[i]

            if should_close:
                if pos["direction"] == "LONG":
                    pnl = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
                else:
                    pnl = (pos["entry_price"] - exit_price) / pos["entry_price"] * 100
                pnl_net = pnl - cost_pct
                trades.append({
                    "direction": pos["direction"],
                    "signal": pos["signal"],
                    "entry_bar": pos["entry_bar"],
                    "exit_bar": i,
                    "bars_held": bars_held,
                    "exit_reason": exit_reason,
                    "pnl_pct": pnl,
                    "pnl_net": pnl_net,
                    "symbol": sym,
                })
                del positions[sym]

        # ── Check for new signal ──
        if sym in positions:
            continue

        lagged_atr = atr[i - atr_lag] if i >= atr_lag else 0
        if np.isnan(lagged_atr) or lagged_atr <= 0:
            continue

        # Pattern gating
        direction = None
        signal_type = ""

        # THE HOLE: breakout_down alone → block
        if sbd[i] and not sbu[i] and not seb[i] and not slt[i]:
            continue

        if sbu[i] and allow_long:
            direction = "LONG"
            signal_type = "breakout_up"
        elif seb[i] and allow_short:
            direction = "SHORT"
            signal_type = "ema_bounce"
        elif slt[i] and allow_short:
            direction = "SHORT"
            signal_type = "level_test"

        if direction is None:
            continue

        # Enter trade
        if direction == "LONG":
            tp_price = closes[i] + tp_atr * lagged_atr
            sl_price = closes[i] - sl_atr * lagged_atr
        else:
            tp_price = closes[i] - tp_atr * lagged_atr
            sl_price = closes[i] + sl_atr * lagged_atr

        positions[sym] = {
            "entry_bar": i,
            "direction": direction,
            "entry_price": closes[i],
            "tp_price": tp_price,
            "sl_price": sl_price,
            "signal": signal_type,
        }

    return trades


def compute_stats(trades):
    """Compute stats from trade list."""
    if not trades:
        return {"n_trades": 0, "pnl": 0, "wr": 0, "pf": 0, "sharpe": 0}

    pnls = [t["pnl_net"] for t in trades]
    n = len(pnls)
    wr = sum(1 for p in pnls if p > 0) / n * 100
    total_pnl = sum(pnls)
    gains = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    pf = gains / max(losses, 1e-10)
    sharpe = np.mean(pnls) / max(np.std(pnls), 1e-10) * np.sqrt(288 * 365) if n > 1 else 0

    n_long = sum(1 for t in trades if t["direction"] == "LONG")
    n_short = sum(1 for t in trades if t["direction"] == "SHORT")
    n_tp = sum(1 for t in trades if t["exit_reason"] == "TP")
    n_sl = sum(1 for t in trades if t["exit_reason"] == "SL")
    n_ts = sum(1 for t in trades if t["exit_reason"] == "TIME_STOP")

    # Per signal stats
    per_signal = {}
    for t in trades:
        sig = t["signal"]
        if sig not in per_signal:
            per_signal[sig] = {"trades": [], "long": 0, "short": 0}
        per_signal[sig]["trades"].append(t)
        if t["direction"] == "LONG":
            per_signal[sig]["long"] += 1
        else:
            per_signal[sig]["short"] += 1

    return {
        "n_trades": n,
        "n_long": n_long,
        "n_short": n_short,
        "n_tp": n_tp,
        "n_sl": n_sl,
        "n_time_stop": n_ts,
        "wr": wr,
        "pnl": total_pnl,
        "pf": pf,
        "sharpe": sharpe,
        "per_signal": per_signal,
    }


def main():
    parser = argparse.ArgumentParser(description="Pure Pattern Backtest (no model)")
    parser.add_argument("--symbols", default="SOL/USDT,AVAX/USDT,XRP/USDT")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--exchange", default="bybit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%H:%M:%S")

    feed = Feed(exchange_id=args.exchange)
    symbols = args.symbols.split(",")

    # Build dataset
    LOG.info("Building dataset...")
    all_dfs = []
    for symbol in symbols:
        LOG.info("  Fetching %s...", symbol)
        try:
            ohlcv = feed.fetch_history(symbol, "5m", limit=int(args.days * 288 * 1.1))
            btc = feed.fetch_history("BTC/USDT", "5m", limit=int(args.days * 288 * 1.1))
            eth = feed.fetch_history("ETH/USDT", "5m", limit=int(args.days * 288 * 1.1))

            ohlcv_df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            btc_df = pd.DataFrame(btc, columns=["timestamp", "open", "high", "low", "close", "volume"])
            eth_df = pd.DataFrame(eth, columns=["timestamp", "open", "high", "low", "close", "volume"])

            for d in [ohlcv_df, btc_df, eth_df]:
                d.drop_duplicates(subset=["timestamp"], keep="first", inplace=True)

            common_ts = set(ohlcv_df["timestamp"]) & set(btc_df["timestamp"]) & set(eth_df["timestamp"])
            ohlcv_df = ohlcv_df[ohlcv_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
            btc_df = btc_df[btc_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)
            eth_df = eth_df[eth_df["timestamp"].isin(common_ts)].sort_values("timestamp").reset_index(drop=True)

            # Fresh arrays for CoW safety
            ohlcv_df = pd.DataFrame({col: ohlcv_df[col].values.copy() for col in ohlcv_df.columns})
            btc_df = pd.DataFrame({col: btc_df[col].values.copy() for col in btc_df.columns})
            eth_df = pd.DataFrame({col: eth_df[col].values.copy() for col in eth_df.columns})

            feat_df = compute_features(ohlcv_df, btc_df, eth_df, symbol=symbol)
            feat_df = pd.DataFrame({col: feat_df[col].values.copy() for col in feat_df.columns})
            feat_df["symbol"] = symbol

            all_dfs.append(feat_df)
            LOG.info("  %s: %d rows", symbol, len(feat_df))
        except Exception as e:
            LOG.error("Failed for %s: %s", symbol, e)
            continue

    if not all_dfs:
        LOG.error("No data built!")
        return

    combined = pd.concat(all_dfs, ignore_index=True)
    LOG.info("Combined: %d rows", len(combined))

    # Count signals
    for col in ["signal_breakout_up", "signal_breakout_down", "signal_ema_bounce", "signal_level_test"]:
        n_sig = int((combined[col].values > 0.5).sum())
        LOG.info("  %s = %d (%.1f%%)", col, n_sig, n_sig / len(combined) * 100)

    # ── Test configurations ──
    configs = [
        # name, bu_thresh, vol_thresh, brk_thresh, use_ema, tp_atr, sl_atr
        ("BASE_0.85_1.5x1.0", 0.85, 1.1, 0.005, False, 1.5, 1.0),
        ("BASE_0.85_2.0x0.8", 0.85, 1.1, 0.005, False, 2.0, 0.8),
        ("BASE_0.85_2.5x0.8", 0.85, 1.1, 0.005, False, 2.5, 0.8),
        ("RELAX_0.80_1.5x1.0", 0.80, 1.0, 0.003, False, 1.5, 1.0),
        ("RELAX_0.80_2.0x0.8", 0.80, 1.0, 0.003, False, 2.0, 0.8),
        ("STRICT_0.90_1.5x1.0", 0.90, 1.2, 0.008, False, 1.5, 1.0),
        ("STRICT_0.90_2.0x0.8", 0.90, 1.2, 0.008, False, 2.0, 0.8),
        ("EMA_FILT_0.85_1.5x1.0", 0.85, 1.1, 0.005, True, 1.5, 1.0),
        ("EMA_FILT_0.85_2.0x0.8", 0.85, 1.1, 0.005, True, 2.0, 0.8),
        # LONG-only and SHORT-only tests
        ("LONG_ONLY_0.85_1.5x1.0", 0.85, 1.1, 0.005, False, 1.5, 1.0),
        ("SHORT_ONLY_0.85_1.5x1.0", 0.85, 1.1, 0.005, False, 1.5, 1.0),
    ]

    print("\n" + "=" * 120)
    print("PURE PATTERN BACKTEST — NO MODEL (just pattern signal → trade)")
    print("=" * 120)
    print(f"  {'Config':<28} {'Trades':>6} {'LONG':>5} {'SHORT':>6} {'TP':>5} {'SL':>5} {'TS':>5} "
          f"{'WR%':>6} {'PnL%':>8} {'Sharpe':>8} {'PF':>5}")
    print("-" * 120)

    for cfg in configs:
        name = cfg[0]
        bu_thresh = cfg[1]
        vol_thresh = cfg[2]
        brk_thresh = cfg[3]
        use_ema = cfg[4]
        tp_atr = cfg[5]
        sl_atr = cfg[6]

        allow_long = "SHORT_ONLY" not in name
        allow_short = "LONG_ONLY" not in name

        df = recompute_signals(combined, bu_thresh=bu_thresh, vol_thresh=vol_thresh,
                               brk_thresh=brk_thresh, use_ema_filter=use_ema)

        trades = run_pure_pattern_backtest(df, tp_atr=tp_atr, sl_atr=sl_atr,
                                            allow_long=allow_long, allow_short=allow_short)
        stats = compute_stats(trades)

        print(f"  {name:<28} {stats['n_trades']:>6} {stats['n_long']:>5} {stats['n_short']:>6} "
              f"{stats['n_tp']:>5} {stats['n_sl']:>5} {stats['n_time_stop']:>5} "
              f"{stats['wr']:>6.1f} {stats['pnl']:>+8.1f} {stats['sharpe']:>8.1f} {stats['pf']:>5.2f}")

        # Per-signal breakdown
        if stats.get("per_signal"):
            for sig, data in sorted(stats["per_signal"].items()):
                sig_pnls = [t["pnl_net"] for t in data["trades"]]
                sig_wr = sum(1 for p in sig_pnls if p > 0) / max(len(sig_pnls), 1) * 100
                sig_pnl = sum(sig_pnls)
                print(f"    └─ {sig:<20} {len(sig_pnls):>4}  L={data['long']:>3} S={data['short']:>3}  "
                      f"WR={sig_wr:.1f}%  PnL={sig_pnl:+.1f}%")

    print("=" * 120)

    # Also run per-token breakdown for the BASE config
    print("\n  ── PER-TOKEN BREAKDOWN (BASE_0.85_1.5x1.0) ──")
    df_base = recompute_signals(combined)
    trades_base = run_pure_pattern_backtest(df_base)

    for sym in combined["symbol"].unique():
        sym_trades = [t for t in trades_base if t["symbol"] == sym]
        if not sym_trades:
            continue
        pnls = [t["pnl_net"] for t in sym_trades]
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        pnl = sum(pnls)
        n_l = sum(1 for t in sym_trades if t["direction"] == "LONG")
        n_s = sum(1 for t in sym_trades if t["direction"] == "SHORT")
        print(f"    {sym:<15} {len(sym_trades):>5} trades  L={n_l:>3} S={n_s:>3}  "
              f"WR={wr:.1f}%  PnL={pnl:+.1f}%")

        # Per signal for this token
        sig_types = {}
        for t in sym_trades:
            sig = t["signal"]
            if sig not in sig_types:
                sig_types[sig] = []
            sig_types[sig].append(t)
        for sig, sig_trades in sorted(sig_types.items()):
            sp = [t["pnl_net"] for t in sig_trades]
            swr = sum(1 for p in sp if p > 0) / len(sp) * 100
            spnl = sum(sp)
            print(f"      └─ {sig:<18} {len(sp):>4}  WR={swr:.1f}%  PnL={spnl:+.1f}%")


if __name__ == "__main__":
    main()
