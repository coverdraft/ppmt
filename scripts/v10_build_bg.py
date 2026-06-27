#!/usr/bin/env python3
"""Quick V10 dataset build — only Binance-friendly symbols."""
import sys
sys.path.insert(0, '/home/z/my-project/ppmt')

from scripts.v10.build_dataset import (
    download_1m, compute_features_1m, compute_mfe_mae,
    _ts_to_ms, FEATURE_NAMES, DATA_DIR, CACHE_DIR
)
import json
import logging
import numpy as np
import pandas as pd
import time

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                    datefmt="%H:%M:%S")
LOG = logging.getLogger("v10_build_fast")

# Only well-known symbols available on Binance
BINANCE_SYMBOLS = ['SOL', 'XRP', 'AVAX', 'SUI', 'ZEC', 'TAO', 'ENA', 'HYPE',
                    'DOGE', 'LINK', 'ADA', 'WIF', 'FARTCOIN', 'PENGU', 'PUMP']

def main():
    # Load filtered trades
    trades_path = '/home/z/my-project/ppmt/data/v9/filtered_trades.json'
    with open(trades_path) as f:
        trades = json.load(f)

    tdf = pd.DataFrame(trades)
    tdf["entry_ts"] = pd.to_datetime(tdf["entry_time"], utc=True)
    tdf["exit_ts"] = pd.to_datetime(tdf["exit_time"], utc=True)
    tdf["entry_ts_ms"] = _ts_to_ms(tdf["entry_ts"])
    tdf["exit_ts_ms"] = _ts_to_ms(tdf["exit_ts"])

    # Filter to Binance symbols only
    tdf = tdf[tdf["symbol"].isin(BINANCE_SYMBOLS)]
    LOG.info("Filtered to %d trades across %d Binance symbols",
             len(tdf), tdf["symbol"].nunique())

    # Build lookup
    entry_lookup = {}
    for sym in BINANCE_SYMBOLS:
        sym_trades = tdf[tdf["symbol"] == sym]
        if len(sym_trades) == 0:
            continue
        lookup = []
        for _, row in sym_trades.iterrows():
            exact_ms = int(row["entry_ts_ms"])
            rounded_ms = (exact_ms // 60000) * 60000
            direction = row["direction"]
            is_win = bool(row.get("is_win", True))
            lookup.append({
                "rounded_ms": rounded_ms,
                "exact_ms": exact_ms,
                "direction": direction,
                "is_win": is_win,
                "exit_ts_ms": int(row["exit_ts_ms"]),
            })
        entry_lookup[sym] = lookup

    # Download BTC first (for correlation features)
    LOG.info("Downloading BTC data...")
    trade_min = int(tdf["entry_ts_ms"].min())
    trade_max = int(tdf["exit_ts_ms"].max())
    btc_start = trade_min - 3 * 86400000
    btc_end = trade_max + 86400000
    btc_df = download_1m("BTC", btc_start, btc_end)
    if len(btc_df) < 100:
        LOG.warning("BTC data insufficient, skipping correlation features")
        btc_df = None
    else:
        LOG.info("BTC data: %d bars", len(btc_df))

    all_features = []
    mfe_stats = {"winners": [], "losers": []}

    for i_sym, symbol in enumerate(BINANCE_SYMBOLS):
        if symbol not in entry_lookup:
            continue
        sym_trades = tdf[tdf["symbol"] == symbol]
        LOG.info("[%d/%d] %s: %d trades", i_sym + 1, len(entry_lookup), symbol, len(sym_trades))

        start_ts = int(sym_trades["entry_ts_ms"].min()) - 3 * 86400000
        end_ts = int(sym_trades["exit_ts_ms"].max()) + 86400000

        ohlcv = download_1m(symbol, start_ts, end_ts)
        if len(ohlcv) < 100:
            LOG.warning("  %s: insufficient data (%d bars), skipping", symbol, len(ohlcv))
            continue

        # Verify overlap
        ohlcv_min = int(ohlcv["timestamp"].min())
        ohlcv_max = int(ohlcv["timestamp"].max())
        trade_min_sym = int(sym_trades["entry_ts_ms"].min())
        trade_max_sym = int(sym_trades["exit_ts_ms"].max())
        if not (ohlcv_min <= trade_max_sym and ohlcv_max >= trade_min_sym):
            LOG.warning("  %s: no OHLCV/trade overlap!", symbol)
            continue

        feat_df = compute_features_1m(ohlcv, symbol=symbol, btc_df=btc_df)
        if len(feat_df) == 0:
            continue

        # Mark entries
        feat_ts = feat_df["timestamp"].values.astype(np.int64)
        feat_ts_index = {int(ts): i for i, ts in enumerate(feat_ts)}
        highs = ohlcv["high"].values.astype(np.float64)
        lows = ohlcv["low"].values.astype(np.float64)

        sym_lookup = entry_lookup[symbol]
        n_matched = 0

        for trade_info in sym_lookup:
            rounded_ms = trade_info["rounded_ms"]
            exact_ms = trade_info["exact_ms"]
            direction = trade_info["direction"]
            is_win = trade_info["is_win"]
            exit_ts_ms = trade_info["exit_ts_ms"]

            bar_idx = None
            if rounded_ms in feat_ts_index:
                bar_idx = feat_ts_index[rounded_ms]
            elif exact_ms in feat_ts_index:
                bar_idx = feat_ts_index[exact_ms]
            else:
                diffs = np.abs(feat_ts - exact_ms)
                closest = int(np.argmin(diffs))
                if diffs[closest] <= 120000:
                    bar_idx = closest

            if bar_idx is not None:
                feat_df.iloc[bar_idx, feat_df.columns.get_loc("trade_direction")] = 1.0 if direction == "long" else -1.0
                feat_df.iloc[bar_idx, feat_df.columns.get_loc("label")] = 1.0 if is_win else -1.0

                # MFE/MAE
                exit_rounded = (exit_ts_ms // 60000) * 60000
                exit_bar = feat_ts_index.get(exit_rounded)
                if exit_bar is None:
                    exit_diffs = np.abs(feat_ts - exit_ts_ms)
                    exit_bar = int(np.argmin(exit_diffs))

                entry_price = float(ohlcv["close"].iloc[bar_idx]) if bar_idx < len(ohlcv) else 0
                if entry_price > 0 and exit_bar > bar_idx:
                    mfe_data = compute_mfe_mae(entry_price, bar_idx, exit_bar, direction, highs, lows)
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("mfe_pct")] = mfe_data["mfe_pct"]
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("mae_pct")] = mfe_data["mae_pct"]
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("mfe_mae_ratio")] = mfe_data["mfe_mae_ratio"]
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("time_to_mfe")] = mfe_data["time_to_mfe"]
                    mfe_stats["winners" if is_win else "losers"].append(mfe_data)

                n_matched += 1

        LOG.info("  %s: matched %d / %d trades", symbol, n_matched, len(sym_lookup))

        # Negative samples
        entry_ms_set = set()
        for trade_info in sym_lookup:
            entry_ms_set.add(int(trade_info["rounded_ms"]))
            entry_ms_set.add(int(trade_info["exact_ms"]))

        entry_windows = set()
        for ems in entry_ms_set:
            window_mask = (feat_ts >= ems - 900000) & (feat_ts <= ems + 900000)
            entry_windows.update(feat_ts[window_mask].tolist())

        non_entry_mask = ~feat_df["timestamp"].astype(np.int64).isin(entry_windows)
        non_entry_bars = feat_df[non_entry_mask]

        n_winners = int((feat_df["label"] == 1.0).sum())
        n_random_neg = min(int(n_winners * 3), len(non_entry_bars))

        if n_random_neg > 0 and n_winners > 0:
            neg_sample = non_entry_bars.sample(n=n_random_neg, random_state=42)
            for idx in neg_sample.index:
                feat_df.loc[idx, "trade_direction"] = np.random.choice([1.0, -1.0])
                feat_df.loc[idx, "label"] = 0.0
                feat_df.loc[idx, "mfe_pct"] = 0.0
                feat_df.loc[idx, "mae_pct"] = 0.0
                feat_df.loc[idx, "mfe_mae_ratio"] = 0.0
                feat_df.loc[idx, "time_to_mfe"] = 0.0

        labeled = feat_df[feat_df["label"].notna() & feat_df["trade_direction"].notna()].copy()
        LOG.info("  %s: %d winners + %d losers + %d random = %d total",
                 symbol,
                 int((labeled["label"] == 1.0).sum()),
                 int((labeled["label"] == -1.0).sum()),
                 int((labeled["label"] == 0.0).sum()),
                 len(labeled))

        all_features.append(labeled)

    if not all_features:
        LOG.error("No features!")
        sys.exit(1)

    combined = pd.concat(all_features, ignore_index=True)
    n_total = len(combined)
    n_win = int((combined["label"] == 1.0).sum())
    n_lose = int((combined["label"] == -1.0).sum())
    n_rand = int((combined["label"] == 0.0).sum())

    LOG.info("Combined: %d rows (winners=%d losers=%d random=%d)", n_total, n_win, n_lose, n_rand)

    # MFE summary
    if mfe_stats["winners"]:
        w = mfe_stats["winners"]
        LOG.info("WINNERS: MFE=%.3f%% MAE=%.3f%% Ratio=%.2f Time=%.1f",
                 np.mean([m["mfe_pct"] for m in w]),
                 np.mean([m["mae_pct"] for m in w]),
                 np.mean([m["mfe_mae_ratio"] for m in w]),
                 np.mean([m["time_to_mfe"] for m in w]))
    if mfe_stats["losers"]:
        l = mfe_stats["losers"]
        LOG.info("LOSERS:  MFE=%.3f%% MAE=%.3f%% Ratio=%.2f Time=%.1f",
                 np.mean([m["mfe_pct"] for m in l]),
                 np.mean([m["mae_pct"] for m in l]),
                 np.mean([m["mfe_mae_ratio"] for m in l]),
                 np.mean([m["time_to_mfe"] for m in l]))

    # Save
    output_path = DATA_DIR / "dataset.parquet"
    combined.to_parquet(output_path, index=False)

    feat_cols = [c for c in FEATURE_NAMES if c not in
                 ("label", "mfe_pct", "mae_pct", "mfe_mae_ratio", "time_to_mfe")]
    with open(DATA_DIR / "feature_columns.json", "w") as f:
        json.dump(feat_cols, f, indent=2)
    with open(DATA_DIR / "regression_targets.json", "w") as f:
        json.dump(["mfe_pct", "mae_pct", "mfe_mae_ratio", "time_to_mfe"], f, indent=2)

    print(f"\n{'='*70}")
    print(f"V10 DATASET BUILT")
    print(f"  Total: {n_total}")
    print(f"  Winners: {n_win}  Losers: {n_lose}  Random: {n_rand}")
    print(f"  Features: {len(feat_cols)}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
