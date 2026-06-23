#!/usr/bin/env python3
"""
v5_extract_features_cb.py — Coinbase feature extractor.

Reads from ohlcv_ext_cb (Coinbase OHLCV), computes 38 features + forward
labels + SAX pattern hash, writes to feature_observations_cb table.

Each (symbol, timeframe, window) combo is one self-contained job. State
is checkpointed so we can resume across short-lived invocations.

Usage:
    python scripts/v5_extract_features_cb.py [--timeframes 5m 15m] \\
        [--symbols BTCUSDT DOGEUSDT] [--windows BULL_2024 RANGE_2025] \\
        [--max-seconds 110]
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))
from ppmt.core.pattern_hash import pattern_hash
from ppmt.core.sax import SAXEncoder

LOG = logging.getLogger("v5_extract_cb")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DB = "/home/z/my-project/data/ppmt.db"
STATE_FILE = "/home/z/my-project/download/extract_cb_state.json"

TOKEN_CLASS = {
    "BTCUSDT": "blue_chip", "ETHUSDT": "blue_chip",
    "SOLUSDT": "large_cap", "XRPUSDT": "large_cap",
    "ADAUSDT": "mid_cap", "AVAXUSDT": "mid_cap", "LINKUSDT": "mid_cap",
    "DOGEUSDT": "meme", "SHIBUSDT": "meme", "PEPEUSDT": "meme",
    "WIFUSDT": "meme", "BONKUSDT": "meme",
}

TF_SAX_CONFIG = {
    "1m":  {"window": 10, "alpha": 3, "pl": 3},
    "5m":  {"window": 10, "alpha": 3, "pl": 3},
    "15m": {"window": 6,  "alpha": 3, "pl": 3},
}

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

    df["high_20"] = h.rolling(20).max()
    df["low_20"]  = l.rolling(20).min()
    df["breakout_up"]   = (h > df["high_20"].shift(1)).astype(int)
    df["breakout_down"] = (l < df["low_20"].shift(1)).astype(int)
    df["dist_to_high_20"] = (c - df["high_20"]) / df["high_20"] * 100
    df["dist_to_low_20"]  = (c - df["low_20"])  / df["low_20"]  * 100

    df["trend_50"] = np.sign(df["ema_9"] - df["ema_50"]).astype(int)
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


def make_labels(df: pd.DataFrame, horizons=(3, 6, 12)) -> pd.DataFrame:
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
        tp_p = 0.6
        sl_p = 0.4

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


def compute_sax_paths(df: pd.DataFrame, window: int, alpha: int, pl: int) -> list:
    n = len(df)
    if n < window * pl:
        return [None] * n
    try:
        encoder = SAXEncoder(alphabet_size=alpha, window_size=window, strategy="close")
        sym_list = encoder.encode(df)
    except Exception as e:
        LOG.warning("SAX failed: %s — using None for all rows", e)
        return [None] * n
    if not sym_list:
        return [None] * n
    out = [None] * n
    n_symbols = len(sym_list)
    for i in range(n):
        k = i // window
        if k >= n_symbols or k + 1 < pl:
            continue
        path = tuple(sym_list[k + 1 - pl : k + 1])
        out[i] = path
    return out


def extract_one(conn, symbol: str, asset_class: str, timeframe: str, window: str) -> int:
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM ohlcv_ext_cb "
        "WHERE symbol = ? AND timeframe = ? AND window = ? ORDER BY timestamp ASC",
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

    H_PRIMARY = 6
    label_col_win = f"tp_first_{H_PRIMARY}"
    label_col_pnl = f"ret_{H_PRIMARY}"
    label_col_fav = f"mfe_{H_PRIMARY}"
    label_col_adv = f"mae_{H_PRIMARY}"

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

    # Pre-compute features as numpy arrays for speed
    feat_arrays = {k: df[k].values for k in FEATURE_NAMES}
    rsi_arr = df["rsi_14"].values
    ts_arr = df["timestamp"].values
    lbl_win_arr = df[label_col_win].values
    lbl_pnl_arr = df[label_col_pnl].values
    lbl_fav_arr = df[label_col_fav].values
    lbl_adv_arr = df[label_col_adv].values
    regime_arr = df["_runtime_regime"].values

    for i in range(n_total):
        path = sax_paths[i]
        if path is None:
            continue
        if np.isnan(rsi_arr[i]):
            continue

        features = {k: float(feat_arrays[k][i]) if not np.isnan(feat_arrays[k][i]) else 0.0
                    for k in FEATURE_NAMES}

        ph = pattern_hash(list(path), timeframe, "N3")

        lw = None if np.isnan(lbl_win_arr[i]) else int(lbl_win_arr[i])
        lp = None if np.isnan(lbl_pnl_arr[i]) else float(lbl_pnl_arr[i])
        lf = None if np.isnan(lbl_fav_arr[i]) else float(lbl_fav_arr[i])
        la = None if np.isnan(lbl_adv_arr[i]) else float(lbl_adv_arr[i])

        ts = int(ts_arr[i])

        batch_rows.append((
            symbol, timeframe, ts, ph,
            window, regime_arr[i], asset_class,
            json.dumps(features, default=str),
            0.0, 0.0, 0,
            lw, lp, lf, la,
            lw,
        ))

        if len(batch_rows) >= BATCH:
            _flush_batch(conn, batch_rows)
            inserted += len(batch_rows)
            batch_rows = []

    if batch_rows:
        _flush_batch(conn, batch_rows)
        inserted += len(batch_rows)

    return inserted


def _flush_batch(conn, batch: list) -> None:
    placeholders = ",".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(batch))
    flat = [v for row in batch for v in row]
    conn.execute(
        f"""
        INSERT OR REPLACE INTO feature_observations_cb
            (symbol, timeframe, ts, pattern_hash,
             historical_regime, runtime_regime, asset_class,
             features_json, prior_win_rate, prior_expected_move, prior_count,
             label_win, label_pnl, label_max_fav, label_max_adv, label_hit_tp_first)
        VALUES {placeholders}
        """,
        flat,
    )
    conn.commit()


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feature_observations_cb (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts INTEGER NOT NULL,
            pattern_hash TEXT,
            historical_regime TEXT NOT NULL,
            runtime_regime TEXT,
            asset_class TEXT,
            features_json TEXT,
            prior_win_rate REAL,
            prior_expected_move REAL,
            prior_count INTEGER,
            label_win INTEGER,
            label_pnl REAL,
            label_max_fav REAL,
            label_max_adv REAL,
            label_hit_tp_first INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, timeframe, ts, pattern_hash, historical_regime)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_fo_cb_sym_tf
        ON feature_observations_cb(symbol, timeframe)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_fo_cb_regime
        ON feature_observations_cb(historical_regime)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_fo_cb_sym_tf_regime
        ON feature_observations_cb(symbol, timeframe, historical_regime, ts)
    """)
    conn.commit()


def load_state():
    if Path(STATE_FILE).exists():
        return json.loads(Path(STATE_FILE).read_text())
    return {"completed": [], "failed": []}


def save_state(state):
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframes", nargs="+", default=["5m", "15m"])
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--windows", nargs="+", default=None)
    parser.add_argument("--max-seconds", type=int, default=110)
    args = parser.parse_args()

    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    ensure_table(conn)

    # Discover available combos in ohlcv_ext_cb
    rows = conn.execute(
        "SELECT DISTINCT symbol, timeframe, window FROM ohlcv_ext_cb"
    ).fetchall()
    available = []
    for sym, tf, win in rows:
        if args.symbols and sym not in args.symbols:
            continue
        if args.timeframes and tf not in args.timeframes:
            continue
        if args.windows and win not in args.windows:
            continue
        if sym not in TOKEN_CLASS:
            continue  # skip BNBUSDT etc.
        available.append((sym, tf, win))

    LOG.info("Found %d (symbol, tf, window) combos to extract", len(available))

    # Filter out completed
    state = load_state()
    done_keys = set()
    for r in state["completed"]:
        done_keys.add(f"{r['sym']}|{r['tf']}|{r['window']}")
    for r in state["failed"]:
        done_keys.add(f"{r['sym']}|{r['tf']}|{r['window']}")

    remaining = [(s, t, w) for (s, t, w) in available
                 if f"{s}|{t}|{w}" not in done_keys]
    LOG.info("Remaining: %d (after progress filter)", len(remaining))

    if not remaining:
        LOG.info("All done. ✓")
        # Print final summary
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM feature_observations_cb")
        print(f"\nTotal feature_observations_cb rows: {cur.fetchone()[0]:,}")
        cur.execute("SELECT historical_regime, COUNT(*) FROM feature_observations_cb GROUP BY historical_regime")
        for r, n in cur.fetchall():
            print(f"  {r}: {n:,}")
        return

    t_start = time.time()
    n_processed = 0
    for i, (sym, tf, win) in enumerate(remaining, 1):
        if time.time() - t_start > args.max_seconds:
            LOG.info("Hit max_seconds, exiting")
            break
        cls = TOKEN_CLASS.get(sym, "default")
        t0 = time.time()
        try:
            n = extract_one(conn, sym, cls, tf, win)
            elapsed = time.time() - t0
            LOG.info("[%d/%d] %s %s %s: %d feature rows in %.1fs",
                     i, len(remaining), sym, tf, win, n, elapsed)
            state["completed"].append({"sym": sym, "tf": tf, "window": win, "rows": n})
        except Exception as e:
            LOG.exception("FAILED %s %s %s: %s", sym, tf, win, e)
            state["failed"].append({"sym": sym, "tf": tf, "window": win, "error": str(e)})
        save_state(state)
        n_processed += 1

    LOG.info("Processed %d combos in %.1fs", n_processed, time.time() - t_start)
    conn.close()


if __name__ == "__main__":
    main()
