"""
Track C1: V5 Feature extractor + persistence to feature_observations.

For each (symbol, timeframe, window) in ohlcv_ext:
  1. Load OHLCV
  2. Compute 38 indicators (body anatomy, returns, ATR, RSI, EMAs, vol, microstructure, time)
  3. Compute forward labels (TP-first hit at +0.6% before -0.4% SL, multi-horizon)
  4. Compute SAX pattern hash per bar (so we can join with the Trie prior)
  5. Insert into feature_observations table

Output: feature_observations table populated with millions of labeled rows
        ready for LightGBM training.

Usage:
    python /home/z/my-project/scripts/v5_extract_features.py --timeframes 5m 15m
    python /home/z/my-project/scripts/v5_extract_features.py --timeframes 5m --symbols BTCUSDT ETHUSDT
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))

from ppmt.data.storage import PPMTStorage  # noqa: E402
from ppmt.core.pattern_hash import pattern_hash  # noqa: E402
from ppmt.core.sax import SAXEncoder  # noqa: E402

LOG = logging.getLogger("v5_extract")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

TOKEN_CLASS = {
    "BTCUSDT":  "blue_chip", "ETHUSDT": "blue_chip", "BNBUSDT": "blue_chip",
    "SOLUSDT":  "large_cap", "XRPUSDT": "large_cap",
    "ADAUSDT":  "mid_cap", "AVAXUSDT": "mid_cap", "LINKUSDT": "mid_cap",
    "DOGEUSDT": "meme", "SHIBUSDT": "meme", "PEPEUSDT": "meme",
    "WIFUSDT":  "meme", "BONKUSDT": "meme",
    "BTC/USDT":  "blue_chip", "ETH/USDT": "blue_chip", "BNB/USDT": "blue_chip",
    "SOL/USDT":  "large_cap", "XRP/USDT": "large_cap",
    "ADA/USDT":  "mid_cap", "AVAX/USDT": "mid_cap", "LINK/USDT": "mid_cap",
    "DOGE/USDT": "meme", "SHIB/USDT": "meme", "PEPE/USDT": "meme",
    "WIF/USDT":  "meme", "BONK/USDT": "meme",
}

# Per-timeframe SAX config (window size, alphabet size, pattern length)
# These must match the engine's LEVEL_WINDOW_CONFIG for N3 (per-asset).
TF_SAX_CONFIG = {
    "1m":  {"window": 10, "alpha": 3, "pl": 3},
    "5m":  {"window": 10, "alpha": 3, "pl": 3},
    "15m": {"window": 6,  "alpha": 3, "pl": 3},
    "30m": {"window": 8,  "alpha": 3, "pl": 3},
    "1h":  {"window": 8,  "alpha": 3, "pl": 3},
}


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add 38 technical features to OHLCV df. Returns new df."""
    df = df.copy().reset_index(drop=True)
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]

    rng = (h - l).replace(0, 1e-10)
    body = (c - o)
    df["body_pct"]     = body / rng
    df["upper_wick"]   = (h - np.maximum(o, c)) / rng
    df["lower_wick"]   = (np.minimum(o, c) - l) / rng
    df["body_abs"]     = body.abs() / rng
    df["close_pos"]    = (c - l) / rng
    df["range_pct"]    = rng / c * 100

    df["ret_1"]  = c.pct_change(1)
    df["ret_3"]  = c.pct_change(3)
    df["ret_5"]  = c.pct_change(5)
    df["ret_10"] = c.pct_change(10)
    df["log_ret_1"] = np.log(c / c.shift(1))

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr_14"]   = tr.rolling(14).mean()
    df["atr_pct"]  = df["atr_14"] / c * 100
    df["vol_std_10"] = df["log_ret_1"].rolling(10).std()

    delta = c.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-10)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    for p in [9, 20, 50]:
        df[f"ema_{p}"] = c.ewm(span=p, adjust=False).mean()
        df[f"ema_{p}_slope"] = df[f"ema_{p}"].pct_change(3)
    df["ema_9_20_cross"] = (df["ema_9"] - df["ema_20"]) / c * 100
    df["ema_20_50_cross"] = (df["ema_20"] - df["ema_50"]) / c * 100
    df["price_vs_ema20"] = (c - df["ema_20"]) / c * 100
    df["price_vs_ema50"] = (c - df["ema_50"]) / c * 100

    vol_ma = v.rolling(20).mean().replace(0, 1e-10)
    df["vol_ratio"] = v / vol_ma
    df["vol_z"] = (v - vol_ma) / v.rolling(20).std().replace(0, 1e-10)

    df["last_3_body_sum"] = df["body_pct"].rolling(3).sum()
    df["last_3_range_sum"] = df["range_pct"].rolling(3).sum()

    df["bullish_engulf_2"] = ((df["body_pct"].shift(1) < 0) & (df["body_pct"] > 0) &
                              (df["close"] > df["open"].shift(1)) &
                              (df["open"] < df["close"].shift(1))).astype(int)
    df["hammer_like"] = ((df["lower_wick"] > 2 * df["body_abs"]) & (df["body_abs"] > 0)).astype(int)
    df["shooting_star"] = ((df["upper_wick"] > 2 * df["body_abs"]) & (df["body_abs"] > 0)).astype(int)

    # Breakouts / range position
    df["high_20"] = h.rolling(20).max()
    df["low_20"]  = l.rolling(20).min()
    df["breakout_up"]   = (h > df["high_20"].shift(1)).astype(int)
    df["breakout_down"] = (l < df["low_20"].shift(1)).astype(int)
    df["dist_to_high_20"] = (c - df["high_20"]) / df["high_20"] * 100
    df["dist_to_low_20"]  = (c - df["low_20"])  / df["low_20"]  * 100

    # Trend / regime tags
    df["trend_50"] = np.sign(df["ema_9"] - df["ema_50"]).astype(int)
    # vol_regime: bucket atr_pct into 4 categories (low/normal/high/extreme).
    # Use np.digitize for NaN safety (returns 0..3).
    atr_p = df["atr_pct"].fillna(0).values
    bins = [0.5, 1.5, 5.0]
    df["vol_regime"] = np.digitize(atr_p, bins).astype(int)
    df["trending"] = (df["atr_pct"] > df["atr_pct"].rolling(50, min_periods=5).mean()).astype(int)

    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df["hour_utc"] = ts.dt.hour
        df["dow"] = ts.dt.dayofweek
        df["hour_sin"] = np.sin(2 * np.pi * df["hour_utc"] / 24)
        df["hour_cos"] = np.cos(2 * np.pi * df["hour_utc"] / 24)
    return df


# Feature list (must match what we persist to feature_observations)
FEATURE_NAMES = [
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


def make_labels(df: pd.DataFrame, horizons=(3, 6, 12)) -> pd.DataFrame:
    """Forward labels: for each horizon H bars, compute:
       - ret_H: forward return %
       - max_fav_H: max favorable excursion %
       - max_adv_H: max adverse excursion %
       - tp_first_H: did +0.6% TP hit before -0.4% SL? (1/0/NaN)
    """
    df = df.copy()
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    n = len(df)

    for H in horizons:
        ret = np.full(n, np.nan)
        mfe = np.full(n, np.nan)
        mae = np.full(n, np.nan)
        tp_first = np.full(n, np.nan)
        tp_p = 0.6  # %
        sl_p = 0.4  # %

        for i in range(n - H):
            entry = c[i]
            ret[i] = (c[i + H] - entry) / entry * 100
            hh = h[i + 1 : i + H + 1]
            ll = l[i + 1 : i + H + 1]
            mfe[i] = (hh.max() - entry) / entry * 100 if len(hh) else 0
            mae[i] = (ll.min() - entry) / entry * 100 if len(ll) else 0

            tp_price = entry * (1 + tp_p / 100)
            sl_price = entry * (1 - sl_p / 100)
            for j in range(1, H + 1):
                if i + j >= n: break
                if l[i + j] <= sl_price:
                    tp_first[i] = 0; break
                if h[i + j] >= tp_price:
                    tp_first[i] = 1; break

        df[f"ret_{H}"] = ret
        df[f"mfe_{H}"] = mfe
        df[f"mae_{H}"] = mae
        df[f"tp_first_{H}"] = tp_first

    return df


def compute_sax_paths(df: pd.DataFrame, window: int, alpha: int, pl: int) -> list[Optional[tuple]]:
    """Compute the SAX pattern (length `pl`) ending at each bar.

    Uses close-price-only SAX (matches N3 configuration for 5m/15m).
    The SAXEncoder.encode() call takes the WHOLE df and returns one symbol
    per window of `window_size` bars. We then assemble sliding `pl`-symbol
    paths ending at each bar.
    """
    n = len(df)
    if n < window * pl:
        return [None] * n

    encoder = SAXEncoder(
        alphabet_size=alpha,
        window_size=window,
        strategy="close",
    )

    # encode() returns one symbol per window of `window_size` bars.
    sym_list: list[str] = encoder.encode(df)
    if not sym_list:
        return [None] * n

    out: list[Optional[tuple]] = [None] * n
    n_symbols = len(sym_list)

    for i in range(n):
        # Bar i belongs to symbol k = i // window.
        k = i // window
        if k >= n_symbols or k + 1 < pl:
            continue
        path = tuple(sym_list[k + 1 - pl : k + 1])
        out[i] = path

    return out


def extract_one(
    storage: PPMTStorage,
    symbol: str,
    asset_class: str,
    timeframe: str,
    window: str,
) -> int:
    """Extract features + labels + SAX hash for one combo. Returns rows inserted."""
    conn = storage._ensure_conn()
    rows = conn.execute(
        """
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_ext
        WHERE symbol = ? AND timeframe = ? AND window = ?
        ORDER BY timestamp ASC
        """,
        (symbol, timeframe, window),
    ).fetchall()
    if len(rows) < 200:
        LOG.warning("  Skipping %s %s %s: only %d rows", symbol, timeframe, window, len(rows))
        return 0

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = compute_indicators(df)
    df = make_labels(df, horizons=(3, 6, 12))

    cfg = TF_SAX_CONFIG.get(timeframe)
    if cfg is None:
        LOG.warning("  No SAX config for TF %s, skipping", timeframe)
        return 0
    sax_paths = compute_sax_paths(df, cfg["window"], cfg["alpha"], cfg["pl"])

    # Use the 6-bar horizon as primary label (matches 30min on 5m, 90min on 15m)
    H_PRIMARY = 6
    label_col_win = f"tp_first_{H_PRIMARY}"
    label_col_pnl = f"ret_{H_PRIMARY}"
    label_col_fav = f"mfe_{H_PRIMARY}"
    label_col_adv = f"mae_{H_PRIMARY}"

    # Compute runtime regime (simple: trending vs ranging based on atr_pct)
    atr_median = df["atr_pct"].rolling(50, min_periods=5).median()
    trend_sign = df["trend_50"]
    df["_runtime_regime"] = np.where(
        df["atr_pct"] > atr_median * 1.2, "volatile",
        np.where(trend_sign > 0, "trending_up",
                 np.where(trend_sign < 0, "trending_down", "ranging"))
    )

    inserted = 0
    n_total = len(df)
    BATCH = 500

    batch_rows = []
    for i in range(n_total):
        path = sax_paths[i]
        if path is None:
            continue

        # Skip rows without complete features (early warmup)
        if pd.isna(df["rsi_14"].iloc[i]):
            continue

        features = {k: float(df[k].iloc[i]) if not pd.isna(df[k].iloc[i]) else 0.0
                    for k in FEATURE_NAMES}

        ph = pattern_hash(list(path), timeframe, "N3")

        label_win = df[label_col_win].iloc[i]
        label_pnl = df[label_col_pnl].iloc[i]
        label_fav = df[label_col_fav].iloc[i]
        label_adv = df[label_col_adv].iloc[i]

        # tp_first only valid if not NaN
        lw = None if pd.isna(label_win) else int(label_win)
        lp = None if pd.isna(label_pnl) else float(label_pnl)
        lf = None if pd.isna(label_fav) else float(label_fav)
        la = None if pd.isna(label_adv) else float(label_adv)

        ts = int(df["timestamp"].iloc[i])

        batch_rows.append((
            symbol, timeframe, ts, ph,
            window, df["_runtime_regime"].iloc[i], asset_class,
            json.dumps(features, default=str),
            0.0, 0.0, 0,  # prior_* filled in later from trie
            lw, lp, lf, la,
            lw,  # hit_tp_first == tp_first (1 iff win)
        ))

        if len(batch_rows) >= BATCH:
            _flush_batch(conn, batch_rows)
            inserted += len(batch_rows)
            batch_rows = []

    if batch_rows:
        _flush_batch(conn, batch_rows)
        inserted += len(batch_rows)

    return inserted


def _flush_batch(conn, batch: list[tuple]) -> None:
    placeholders = ",".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(batch))
    flat = [v for row in batch for v in row]
    conn.execute(
        f"""
        INSERT OR REPLACE INTO feature_observations
            (symbol, timeframe, ts, pattern_hash,
             historical_regime, runtime_regime, asset_class,
             features_json, prior_win_rate, prior_expected_move, prior_count,
             label_win, label_pnl, label_max_fav, label_max_adv, label_hit_tp_first)
        VALUES {placeholders}
        """,
        flat,
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframes", nargs="+", default=["5m", "15m"])
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--windows", nargs="+", default=None)
    args = parser.parse_args()

    storage = PPMTStorage()
    conn = storage._ensure_conn()

    # Discover available combos
    rows = conn.execute(
        "SELECT DISTINCT symbol, timeframe, window FROM ohlcv_ext"
    ).fetchall()
    available = []
    for sym, tf, win in rows:
        if args.symbols and sym not in args.symbols:
            continue
        if args.timeframes and tf not in args.timeframes:
            continue
        if args.windows and win not in args.windows:
            continue
        available.append((sym, tf, win))

    LOG.info("Found %d (symbol, tf, window) combos to extract features from", len(available))

    total_rows = 0
    t0 = time.time()
    for i, (sym, tf, win) in enumerate(available, 1):
        cls = TOKEN_CLASS.get(sym, "default")
        try:
            n = extract_one(storage, sym, cls, tf, win)
            total_rows += n
            LOG.info("[%d/%d] %s %s %s: %d feature rows", i, len(available), sym, tf, win, n)
        except Exception as e:
            LOG.exception("FAILED %s %s %s: %s", sym, tf, win, e)

    elapsed = time.time() - t0
    LOG.info("=" * 60)
    LOG.info("Extraction complete: %d feature rows in %.1fs (%.0f rows/s)",
             total_rows, elapsed, total_rows / max(elapsed, 1))

    # Final count
    counts = storage.count_feature_observations()
    LOG.info("feature_observations table: %s", counts)


if __name__ == "__main__":
    main()
