"""
build_dataset.py — Build labeled dataset from trader's entries + random bars

Pipeline:
  1. Load filtered trades from parse_trades.py
  2. Download 1m OHLCV for each symbol (paginated, multi-exchange)
  3. Compute features at each trade entry bar (POSITIVE samples)
  4. Compute features at random bars where no trade was taken (NEGATIVE samples)
  5. Save dataset for training

Features are computed on 1m bars to match the trader's decision timeframe.

FIXED vs v1:
  - entry_ts_ms: robust conversion using view('int64') with astype fallback
  - download_1m: fixed pagination — don't break when len < limit; continue until
    last_ts >= end_ts_ms or truly empty response
  - entry matching: round entry_ts_ms DOWN to minute boundary for primary lookup,
    then fallback to closest-within-2min for sub-minute entries
  - cache invalidation: wipe stale cache that doesn't overlap with trade dates
  - diagnostic logging: print date ranges, overlap checks, match stats per symbol
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

pd.options.mode.copy_on_write = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LOG = logging.getLogger("v9_build")

DATA_DIR = PROJECT_ROOT / "data" / "v9"
CACHE_DIR = DATA_DIR / "ohlcv_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── EMA / ATR helpers ──

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    result = np.empty_like(arr, dtype=np.float64)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def _atr(h, l, c, period=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.append(c[0], c[:-1])),
                                       np.abs(l - np.append(c[0], c[:-1]))))
    atr = pd.Series(tr).rolling(period, min_periods=5).mean().values
    return atr


# ── Feature computation (1m bars) ──

FEATURE_NAMES = [
    # G1: Price microstructure
    "body_pct", "close_pos", "range_pct", "wick_imbalance",
    "body_consistency_5", "range_expansion_3",

    # G2: Returns + momentum
    "ret_1", "ret_3", "ret_6", "ret_12", "ret_30",
    "consecutive_dir", "momentum_strength", "ret_z_12",

    # G3: Volatility
    "atr_pct", "atr_percentile_50", "squeeze_score", "vol_regime",

    # G4: Volume
    "vol_ratio", "vol_z", "vol_skew", "vol_acceleration", "volume_conviction",

    # G5: Breakout context
    "close_position_20", "breakout_strength", "is_at_high_20", "is_at_low_20",
    "breakout_volume_score", "breakout_age",

    # G6: Trend alignment
    "dist_ema9_atr", "dist_ema21_atr", "ema_alignment",
    "ema_trend_strength", "ema21_bounce_score",

    # G7: Candle patterns
    "is_doji", "is_hammer", "is_shooting_star",
    "is_bullish_engulf", "is_bearish_engulf",
    "is_bull_pin", "is_bear_pin",

    # G8: Reversal
    "v_reversal", "pullback_depth",

    # G9: Temporal
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",

    # G10: Direction (label-related)
    "trade_direction",  # +1=LONG, -1=SHORT

    # LABEL
    "label",  # 1=trader entered, 0=did not enter
]

N_FEATURES = len(FEATURE_NAMES) - 1  # exclude label


def compute_features_1m(df: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
    """Compute all features for 1m OHLCV data. Returns DataFrame with FEATURE_NAMES columns."""
    o = df["open"].values.astype(np.float64)
    h = df["high"].values.astype(np.float64)
    l = df["low"].values.astype(np.float64)
    c = df["close"].values.astype(np.float64)
    v = df["volume"].values.astype(np.float64)
    n = len(df)

    if n < 60:
        LOG.warning("  %s: too few bars (%d) for features, need >=60", symbol, n)
        empty = pd.DataFrame(columns=FEATURE_NAMES + ["timestamp", "_atr_14_price", "symbol"])
        return empty

    rng = np.maximum(h - l, 1e-10)
    body = c - o

    feat = pd.DataFrame(index=df.index)
    feat["timestamp"] = df["timestamp"].values

    # G1: Price microstructure
    feat["body_pct"] = body / rng
    feat["close_pos"] = (c - l) / rng
    feat["range_pct"] = rng / np.maximum(c, 1e-10) * 100

    upper_wick = (h - np.maximum(o, c)) / rng
    lower_wick = (np.minimum(o, c) - l) / rng
    feat["wick_imbalance"] = lower_wick - upper_wick

    body_sign = (c > o).astype(float)
    body_consist = np.zeros(n)
    for i in range(1, n):
        if body_sign[i] == body_sign[i - 1] and body_sign[i] != 0:
            body_consist[i] = body_consist[i - 1] + 1
        else:
            body_consist[i] = 1 if body_sign[i] != 0 else 0
    feat["body_consistency_5"] = pd.Series(body_consist).rolling(5, min_periods=1).mean().values

    range_pct_s = pd.Series(feat["range_pct"].values.copy())
    avg_rng_3 = range_pct_s.rolling(3, min_periods=1).mean()
    avg_rng_20 = range_pct_s.rolling(20, min_periods=1).mean().replace(0, 1e-10)
    feat["range_expansion_3"] = (avg_rng_3 / avg_rng_20).clip(0, 10).values

    # G2: Returns + momentum
    for p, name in [(1, "ret_1"), (3, "ret_3"), (6, "ret_6"), (12, "ret_12"), (30, "ret_30")]:
        feat[name] = pd.Series(c).pct_change(p, fill_method=None).values

    ret1 = pd.Series(c).pct_change(1, fill_method=None).fillna(0).values
    signs = np.sign(ret1)
    consec = np.zeros(n)
    for i in range(1, n):
        if signs[i] == signs[i - 1] and signs[i] != 0:
            consec[i] = consec[i - 1] + signs[i]
        else:
            consec[i] = signs[i] if signs[i] != 0 else 0
    feat["consecutive_dir"] = consec

    ret1_std = pd.Series(ret1).rolling(50, min_periods=5).std().replace(0, 1e-10).values
    feat["momentum_strength"] = np.clip(np.abs(ret1) / ret1_std, 0, 10)

    ret12 = pd.Series(c).pct_change(12, fill_method=None)
    ret12_mean = ret12.rolling(50, min_periods=5).mean()
    ret12_std = ret12.rolling(50, min_periods=5).std().replace(0, 1e-10)
    feat["ret_z_12"] = ((ret12 - ret12_mean) / ret12_std).clip(-5, 5).values

    # G3: Volatility
    atr_14 = _atr(h, l, c, 14)
    feat["atr_pct"] = atr_14 / np.maximum(c, 1e-10) * 100
    feat["atr_percentile_50"] = pd.Series(feat["atr_pct"].values.copy()).rolling(50, min_periods=5).rank(pct=True).values

    sma_20 = pd.Series(c).rolling(20, min_periods=5).mean().values
    std_20 = pd.Series(c).rolling(20, min_periods=5).std().values
    bollinger_bw = 2 * std_20 / np.maximum(sma_20, 1e-10) * 100
    feat["squeeze_score"] = np.clip(bollinger_bw / np.maximum(feat["atr_pct"].values, 1e-10), 0, 20)
    feat["vol_regime"] = np.digitize(np.nan_to_num(feat["atr_pct"].values, nan=0.0), [0.3, 0.8, 2.0]).astype(float)

    # G4: Volume
    vol_ma = pd.Series(v).rolling(20, min_periods=5).mean().values
    vol_std = pd.Series(v).rolling(20, min_periods=5).std().values
    vol_ma_safe = np.maximum(vol_ma, 1e-10)
    vol_std_safe = np.maximum(vol_std, 1e-10)
    feat["vol_ratio"] = v / vol_ma_safe
    feat["vol_z"] = (v - vol_ma_safe) / vol_std_safe

    up_vol = pd.Series(np.where(c > o, v, 0.0)).rolling(10, min_periods=2).sum().values
    total_vol = pd.Series(v).rolling(10, min_periods=2).sum().values
    feat["vol_skew"] = (up_vol / np.maximum(total_vol, 1e-10) - 0.5) * 2
    feat["vol_acceleration"] = pd.Series(feat["vol_ratio"].values.copy()).diff(3).values
    feat["volume_conviction"] = np.clip(feat["vol_ratio"].values * np.abs(feat["body_pct"].values), 0, 5)

    # G5: Breakout context — shifted by 1 bar
    h_20 = pd.Series(h).rolling(20, min_periods=1).max().shift(1).values.copy()
    l_20 = pd.Series(l).rolling(20, min_periods=1).min().shift(1).values.copy()
    h_20[:20] = np.nanmax(h_20[20:40]) if len(h_20) > 40 else h_20[len(h_20) // 2] if len(h_20) > 0 else 0
    l_20[:20] = np.nanmin(l_20[20:40]) if len(l_20) > 40 else l_20[len(l_20) // 2] if len(l_20) > 0 else 0
    # Replace any remaining NaN
    h_20 = np.nan_to_num(h_20, nan=h_20[~np.isnan(h_20)][0] if np.any(~np.isnan(h_20)) else 0)
    l_20 = np.nan_to_num(l_20, nan=l_20[~np.isnan(l_20)][0] if np.any(~np.isnan(l_20)) else 0)

    range_20 = np.maximum(h_20 - l_20, 1e-10)
    close_pos_20 = (c - l_20) / range_20
    feat["close_position_20"] = close_pos_20

    breakout_up_dist = np.maximum(c - h_20, 0)
    breakout_down_dist = np.maximum(l_20 - c, 0)
    breakout_strength = np.maximum(breakout_up_dist, breakout_down_dist) / range_20
    feat["breakout_strength"] = breakout_strength

    feat["is_at_high_20"] = np.clip((close_pos_20 - 0.9) / 0.1, 0, 1)
    feat["is_at_low_20"] = np.clip((0.1 - close_pos_20) / 0.1, 0, 1)
    feat["breakout_volume_score"] = feat["vol_ratio"].values * breakout_strength

    # Breakout age
    at_high = c >= h_20 * 0.999
    at_low = c <= l_20 * 1.001
    breakout_age = np.zeros(n)
    age_counter = 999
    for i in range(n):
        if at_high[i] or at_low[i]:
            age_counter = 0 if age_counter == 999 else age_counter + 1
        else:
            age_counter = 999
        breakout_age[i] = min(age_counter, 50) / 50.0
    feat["breakout_age"] = breakout_age

    # G6: Trend alignment
    ema_9 = _ema(c, 9)
    ema_21 = _ema(c, 21)
    ema_50 = _ema(c, 50)
    atr_safe = np.maximum(atr_14, 1e-10)

    feat["dist_ema9_atr"] = (c - ema_9) / atr_safe
    feat["dist_ema21_atr"] = (c - ema_21) / atr_safe
    feat["ema_alignment"] = np.sign(ema_9 - ema_21)
    feat["ema_trend_strength"] = (ema_9 - ema_50) / np.maximum(np.abs(ema_50), 1e-10) * 100

    # EMA21 bounce score
    ema21_bounce = np.zeros(n)
    for i in range(1, n):
        for j in range(max(0, i - 3), i):
            touch_dist = min(
                abs(l[j] - ema_21[j]) / atr_safe[i],
                abs(h[j] - ema_21[j]) / atr_safe[i],
            )
            if touch_dist < 0.5:
                ema21_bounce[i] = max(ema21_bounce[i], 1.0 - touch_dist / 0.5)
    feat["ema21_bounce_score"] = ema21_bounce

    # G7: Candle patterns
    feat["is_doji"] = (feat["body_pct"].abs() < 0.1).astype(float)
    feat["is_hammer"] = (lower_wick > 0.4).astype(float) * (upper_wick < 0.15).astype(float)
    feat["is_shooting_star"] = (upper_wick > 0.4).astype(float) * (lower_wick < 0.15).astype(float)
    feat["is_bull_pin"] = ((lower_wick > 0.6) & (feat["body_pct"].abs() < 0.3)).astype(float)
    feat["is_bear_pin"] = ((upper_wick > 0.6) & (feat["body_pct"].abs() < 0.3)).astype(float)

    prev_body = np.append(0, c[:-1] - o[:-1])
    feat["is_bullish_engulf"] = ((body > 0) & (prev_body < 0) & (c > np.append(c[0], o[:-1])) &
                                  (o < np.append(o[0], c[:-1]))).astype(float)
    feat["is_bearish_engulf"] = ((body < 0) & (prev_body > 0) & (c < np.append(c[0], o[:-1])) &
                                  (o > np.append(o[0], c[:-1]))).astype(float)

    # G8: Reversal + pullback
    if n >= 7:
        r1 = np.zeros(n)
        r2 = np.zeros(n)
        for i in range(6, n):
            r1[i] = (c[i - 3] - c[i - 6]) / max(c[i - 6], 1e-10)
            r2[i] = (c[i] - c[i - 3]) / max(c[i - 3], 1e-10)
        feat["v_reversal"] = ((np.sign(r1) != np.sign(r2)) & (np.abs(r2) > np.abs(r1) * 0.5)).astype(float)
    else:
        feat["v_reversal"] = 0.0

    trend = feat["ema_alignment"].values
    high_6 = pd.Series(h).rolling(6, min_periods=2).max().values
    low_6 = pd.Series(l).rolling(6, min_periods=2).min().values
    range_6 = np.maximum(high_6 - low_6, 1e-10)
    pullback = np.where(
        trend > 0, (high_6 - c) / range_6, (c - low_6) / range_6
    )
    feat["pullback_depth"] = np.clip(pullback, 0, 1)

    # G9: Temporal
    ts_col = df["timestamp"] if "timestamp" in df.columns else df.index
    try:
        ts_dt = pd.to_datetime(ts_col, unit="ms", utc=True)
        hour = ts_dt.dt.hour
        dow = ts_dt.dt.dayofweek
        feat["hour_sin"] = np.sin(2 * np.pi * hour / 24).values
        feat["hour_cos"] = np.cos(2 * np.pi * hour / 24).values
        feat["dow_sin"] = np.sin(2 * np.pi * dow / 7).values
        feat["dow_cos"] = np.cos(2 * np.pi * dow / 7).values
    except Exception:
        feat["hour_sin"] = 0.0
        feat["hour_cos"] = 0.0
        feat["dow_sin"] = 0.0
        feat["dow_cos"] = 0.0

    # Placeholders for direction + label (filled later)
    feat["trade_direction"] = np.nan
    feat["label"] = np.nan
    feat["_atr_14_price"] = atr_14
    feat["symbol"] = symbol

    # Clean inf/nan
    for col in FEATURE_NAMES:
        if col in feat.columns:
            feat[col] = feat[col].replace([np.inf, -np.inf], np.nan)

    return feat


# ── Robust timestamp-to-milliseconds conversion ──

def _ts_to_ms(series: pd.Series) -> pd.Series:
    """Convert tz-aware datetime Series to milliseconds (int64).
    
    CRITICAL: In pandas 2.x, astype(np.int64) on tz-aware DatetimeIndex
    returns SECONDS (not nanoseconds). We use .dt.as_unit('ms') which
    is the reliable cross-version method, with fallbacks.
    """
    # Method 1: .dt.as_unit('ms') — pandas 2.0+ recommended
    try:
        ms_series = series.dt.as_unit("ms")
        return ms_series.view("int64").astype(np.int64)
    except (TypeError, ValueError, AttributeError):
        pass

    # Method 2: .apply with .timestamp() — always correct, slower
    try:
        return series.apply(lambda ts: int(ts.timestamp() * 1000)).astype(np.int64)
    except (TypeError, ValueError, AttributeError):
        pass

    # Method 3: convert to naive UTC first, then as_unit
    try:
        naive = series.dt.tz_convert(None)
        ms_series = naive.as_unit("ms")
        return ms_series.view("int64").astype(np.int64)
    except (TypeError, ValueError, AttributeError):
        pass

    # Method 4: astype(int64) — in pandas 2.x gives seconds, multiply by 1000
    try:
        val = series.astype(np.int64)
        # Check if values look like seconds (~1.7e9) or nanoseconds (~1.7e18)
        sample = int(val.iloc[0]) if len(val) > 0 else 0
        if 1e9 < sample < 2e9:
            # Seconds since epoch — multiply by 1000
            return (val * 1000).astype(np.int64)
        elif 1e18 < sample < 2e18:
            # Nanoseconds since epoch — divide by 1e6
            return (val // 1_000_000).astype(np.int64)
        else:
            raise ValueError(f"Unexpected timestamp value: {sample}")
    except (TypeError, ValueError):
        pass

    raise ValueError("Cannot convert tz-aware datetime to milliseconds — all methods failed!")


# ── Download 1m OHLCV with proper pagination ──

def download_1m(symbol: str, start_ts_ms: int, end_ts_ms: int) -> pd.DataFrame:
    """Download 1m OHLCV with caching. Paginates correctly across the full date range.
    
    Key fixes vs v1:
      - Don't break pagination when len(ohlcv) < 1000; only break when
        the response is truly empty OR last_ts >= end_ts_ms
      - Different exchanges have different max limits per request
    """
    cache_file = CACHE_DIR / f"{symbol}_1m.parquet"

    # ── Check cache with overlap validation ──
    if cache_file.exists():
        cached = pd.read_parquet(cache_file)
        if len(cached) > 0:
            cache_start = int(cached["timestamp"].min())
            cache_end = int(cached["timestamp"].max())

            # Check if cache covers our range (with 1-day tolerance on each side)
            if cache_start <= start_ts_ms + 86400000 and cache_end >= end_ts_ms - 86400000:
                mask = (cached["timestamp"] >= start_ts_ms) & (cached["timestamp"] <= end_ts_ms)
                result = cached[mask].copy()
                if len(result) > 0:
                    LOG.info("  %s: %d bars from cache (1m)  [%s → %s]",
                             symbol, len(result),
                             pd.to_datetime(result["timestamp"].min(), unit="ms").strftime("%Y-%m-%d"),
                             pd.to_datetime(result["timestamp"].max(), unit="ms").strftime("%Y-%m-%d"))
                    return result
            else:
                # Cache exists but doesn't cover our range — log it
                LOG.info("  %s: cache exists but doesn't cover needed range", symbol)
                LOG.info("    Need:    %s → %s",
                         pd.to_datetime(start_ts_ms, unit="ms").strftime("%Y-%m-%d"),
                         pd.to_datetime(end_ts_ms, unit="ms").strftime("%Y-%m-%d"))
                LOG.info("    Have:    %s → %s",
                         pd.to_datetime(cache_start, unit="ms").strftime("%Y-%m-%d"),
                         pd.to_datetime(cache_end, unit="ms").strftime("%Y-%m-%d"))
                # Fall through to download — will merge with existing cache

    import ccxt

    # Binance FIRST — supports 1000 bars/request (5x faster than Bybit 200)
    for exchange_id in ["binance", "bybit", "mexc"]:
        try:
            exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
            exchange.load_markets()

            # Try multiple ticker variants: SOL/USDT, SOLUSDT, SOL/USDT:USDT
            candidates = [
                f"{symbol}/USDT",
                f"{symbol}/USDT:USDT",  # Binance futures
                f"{symbol}USDT",         # Some exchanges use this format
            ]
            ccxt_sym = None
            for cand in candidates:
                if cand in exchange.markets:
                    ccxt_sym = cand
                    break

            if ccxt_sym is None:
                # Fuzzy match: find any market containing this symbol
                for market_id in exchange.markets:
                    base = exchange.markets[market_id].get("base", "")
                    if base.upper() == symbol and "USDT" in market_id.upper():
                        ccxt_sym = market_id
                        LOG.info("  %s: found as %s on %s", symbol, market_id, exchange_id)
                        break

            if ccxt_sym is None:
                LOG.info("  %s: not listed on %s", symbol, exchange_id)
                continue

            # Binance/Bybit both support 1000 bars/request via ccxt
            limit = 1000

            all_ohlcv = []
            since = start_ts_ms
            max_iterations = 2000  # safety limit (covers ~375 days of 1m data)
            iteration = 0
            last_fetched_count = limit  # track to detect truly no more data

            total_days = (end_ts_ms - start_ts_ms) / 86400000
            LOG.info("  %s: fetching from %s (%.0f days, ~%d requests)...",
                     symbol, exchange_id, total_days,
                     int(total_days * 1440 / limit) + 1)

            while since < end_ts_ms and iteration < max_iterations:
                iteration += 1
                try:
                    ohlcv = exchange.fetch_ohlcv(ccxt_sym, "1m", since=since, limit=limit)
                except Exception as e:
                    LOG.warning("  %s on %s: fetch error at %s: %s",
                                symbol, exchange_id,
                                pd.to_datetime(since, unit="ms").strftime("%Y-%m-%d"),
                                str(e)[:80])
                    time.sleep(3)
                    # Retry once
                    try:
                        ohlcv = exchange.fetch_ohlcv(ccxt_sym, "1m", since=since, limit=limit)
                    except Exception:
                        break

                if not ohlcv or len(ohlcv) == 0:
                    # Truly no more data
                    break

                all_ohlcv.extend(ohlcv)
                last_ts = ohlcv[-1][0]

                # If last timestamp is past our range, we're done
                if last_ts >= end_ts_ms:
                    break

                # FIX: Don't break when len < limit!
                # Some exchanges return slightly fewer bars than requested.
                # Only stop if we get truly empty response or past end_ts.
                # However, if we got much fewer than expected, the exchange
                # might not have data for this period at all.
                if len(ohlcv) < 5:
                    # Very few bars — probably no more data available
                    break

                # Move since to the next bar after the last one we got
                since = last_ts + 60000  # +1 minute

                # Rate limit
                time.sleep(exchange.rateLimit / 1000)

            if all_ohlcv:
                df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

                # Merge with existing cache (keep all unique timestamps)
                if cache_file.exists():
                    try:
                        old = pd.read_parquet(cache_file)
                        df = pd.concat([old, df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
                    except Exception:
                        pass  # If cache is corrupted, just use new data

                # Save full cache (all data we have)
                df.to_parquet(cache_file, index=False)

                # Filter to requested range BEFORE returning
                result = df[(df["timestamp"] >= start_ts_ms) & (df["timestamp"] <= end_ts_ms)].copy()
                LOG.info("  %s: %d bars from %s (1m)  [%s → %s]",
                         symbol, len(result), exchange_id,
                         pd.to_datetime(result["timestamp"].min(), unit="ms").strftime("%Y-%m-%d"),
                         pd.to_datetime(result["timestamp"].max(), unit="ms").strftime("%Y-%m-%d"))
                return result

        except Exception as e:
            LOG.warning("  %s on %s: %s", symbol, exchange_id, str(e)[:80])
            time.sleep(2)

    LOG.warning("  %s: no 1m data available on any exchange", symbol)
    return pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description="v9 Build Dataset")
    parser.add_argument("--neg-ratio", type=float, default=3.0,
                        help="Ratio of negative samples per positive (default: 3)")
    parser.add_argument("--big-loss", type=float, default=5.0,
                        help="Re-filter with this threshold (default: $5)")
    parser.add_argument("--max-symbols", type=int, default=50,
                        help="Max symbols to process (default: 50 = all)")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Delete all cached OHLCV data and re-download")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")

    # Optional: clear cache
    if args.clear_cache:
        LOG.info("Clearing OHLCV cache...")
        for f in CACHE_DIR.glob("*.parquet"):
            f.unlink()
        LOG.info("Cache cleared")

    # Load filtered trades
    trades_path = DATA_DIR / "filtered_trades.json"
    if not trades_path.exists():
        LOG.error("No filtered_trades.json. Run parse_trades.py first!")
        sys.exit(1)

    with open(trades_path) as f:
        trades = json.load(f)

    LOG.info("Loaded %d filtered trades", len(trades))
    tdf = pd.DataFrame(trades)

    # ── FIX: Robust timestamp conversion ──
    tdf["entry_ts"] = pd.to_datetime(tdf["entry_time"], utc=True)
    tdf["exit_ts"] = pd.to_datetime(tdf["exit_time"], utc=True)

    # Use robust conversion method
    tdf["entry_ts_ms"] = _ts_to_ms(tdf["entry_ts"])
    tdf["exit_ts_ms"] = _ts_to_ms(tdf["exit_ts"])

    # Verify timestamps are sane (should be ~1.7e12 for 2024-2025, ~1.75e12 for 2025-2026)
    sample_ts = int(tdf["entry_ts_ms"].iloc[0]) if len(tdf) > 0 else 0
    sample_date = pd.to_datetime(sample_ts, unit="ms").strftime("%Y-%m-%d %H:%M")
    LOG.info("Sample entry_ts_ms: %d (%s)", sample_ts, sample_date)

    if sample_ts < 1e12 or sample_ts > 2e12:
        LOG.error("Timestamp conversion looks wrong! Expected ~1.7-1.8e12 for 2024-2026, got %d", sample_ts)
        LOG.error("Raw entry_time: %s", tdf["entry_time"].iloc[0] if len(tdf) > 0 else "N/A")
        sys.exit(1)

    # Log overall trade date range
    trade_min = int(tdf["entry_ts_ms"].min())
    trade_max = int(tdf["entry_ts_ms"].max())
    LOG.info("Trade dates: %s → %s (%.0f days)",
             pd.to_datetime(trade_min, unit="ms").strftime("%Y-%m-%d"),
             pd.to_datetime(trade_max, unit="ms").strftime("%Y-%m-%d"),
             (trade_max - trade_min) / 86400000)

    # Top symbols
    sym_counts = tdf["symbol"].value_counts()
    top_syms = sym_counts.head(args.max_symbols).index.tolist()
    LOG.info("Processing %d symbols: %s", len(top_syms), top_syms[:10])

    # Build lookup: symbol → list of (entry_ts_ms_rounded, entry_ts_ms_exact, direction)
    # We use the ROUNDED timestamp for primary lookup and EXACT for fallback
    entry_lookup = {}
    for sym in top_syms:
        sym_trades = tdf[tdf["symbol"] == sym]
        lookup = []
        for _, row in sym_trades.iterrows():
            exact_ms = int(row["entry_ts_ms"])
            # Round DOWN to minute boundary (OHLCV bars are at minute boundaries)
            rounded_ms = (exact_ms // 60000) * 60000
            direction = row["direction"]
            lookup.append((rounded_ms, exact_ms, direction))
        entry_lookup[sym] = lookup

    # Download 1m data per symbol
    all_features = []

    for i_sym, symbol in enumerate(top_syms):
        sym_trades = tdf[tdf["symbol"] == symbol]
        LOG.info("[%d/%d] %s: %d trades", i_sym + 1, len(top_syms), symbol, len(sym_trades))

        # Time range for download (with 3 days buffer before first trade for warmup)
        start_ts = int(sym_trades["entry_ts_ms"].min()) - 3 * 86400000
        end_ts = int(sym_trades["exit_ts_ms"].max()) + 86400000

        LOG.info("  %s: downloading from %s to %s (%.0f days)",
                 symbol,
                 pd.to_datetime(start_ts, unit="ms").strftime("%Y-%m-%d"),
                 pd.to_datetime(end_ts, unit="ms").strftime("%Y-%m-%d"),
                 (end_ts - start_ts) / 86400000)

        ohlcv = download_1m(symbol, start_ts, end_ts)
        if len(ohlcv) < 100:
            LOG.warning("  %s: insufficient data (%d bars), skipping", symbol, len(ohlcv))
            continue

        # Verify OHLCV date range overlaps with trades
        ohlcv_min = int(ohlcv["timestamp"].min())
        ohlcv_max = int(ohlcv["timestamp"].max())
        trade_min_sym = int(sym_trades["entry_ts_ms"].min())
        trade_max_sym = int(sym_trades["entry_ts_ms"].max())
        overlap = ohlcv_min <= trade_max_sym and ohlcv_max >= trade_min_sym
        LOG.info("  %s: OHLCV [%s → %s]  Trades [%s → %s]  Overlap: %s",
                 symbol,
                 pd.to_datetime(ohlcv_min, unit="ms").strftime("%Y-%m-%d"),
                 pd.to_datetime(ohlcv_max, unit="ms").strftime("%Y-%m-%d"),
                 pd.to_datetime(trade_min_sym, unit="ms").strftime("%Y-%m-%d"),
                 pd.to_datetime(trade_max_sym, unit="ms").strftime("%Y-%m-%d"),
                 "✅" if overlap else "❌ NO OVERLAP")

        if not overlap:
            LOG.warning("  %s: OHLCV data doesn't overlap with trades! Try --clear-cache", symbol)
            continue

        # Compute features for ALL bars
        feat_df = compute_features_1m(ohlcv, symbol=symbol)
        if len(feat_df) == 0:
            LOG.warning("  %s: no features computed, skipping", symbol)
            continue

        # ── Mark POSITIVE samples (trader entry bars) ──
        feat_ts = feat_df["timestamp"].values.astype(np.int64)

        # Build index of timestamp → bar position for fast lookup
        feat_ts_index = {int(ts): i for i, ts in enumerate(feat_ts)}

        sym_lookup = entry_lookup.get(symbol, [])
        n_matched = 0
        n_exact = 0
        n_closest = 0

        for rounded_ms, exact_ms, direction in sym_lookup:
            matched = False

            # Try 1: Exact match on the rounded (minute-boundary) timestamp
            if rounded_ms in feat_ts_index:
                bar_idx = feat_ts_index[rounded_ms]
                feat_df.iloc[bar_idx, feat_df.columns.get_loc("trade_direction")] = 1.0 if direction == "long" else -1.0
                feat_df.iloc[bar_idx, feat_df.columns.get_loc("label")] = 1.0
                n_matched += 1
                n_exact += 1
                matched = True

            if not matched:
                # Try 2: Exact match on the original (unrounded) timestamp
                if exact_ms in feat_ts_index:
                    bar_idx = feat_ts_index[exact_ms]
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("trade_direction")] = 1.0 if direction == "long" else -1.0
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("label")] = 1.0
                    n_matched += 1
                    n_exact += 1
                    matched = True

            if not matched:
                # Try 3: Closest bar within 2 minutes
                diffs = np.abs(feat_ts - exact_ms)
                bar_idx = int(np.argmin(diffs))
                if diffs[bar_idx] <= 120000:  # within 2 minutes
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("trade_direction")] = 1.0 if direction == "long" else -1.0
                    feat_df.iloc[bar_idx, feat_df.columns.get_loc("label")] = 1.0
                    n_matched += 1
                    n_closest += 1
                    matched = True

            if not matched and n_matched == 0:
                # Debug: log why first trade didn't match
                diffs = np.abs(feat_ts - exact_ms)
                closest_idx = int(np.argmin(diffs))
                LOG.warning("  %s: First unmatched trade — entry=%s (%s)  closest_bar=%s (diff=%.1f min)",
                            symbol, exact_ms,
                            pd.to_datetime(exact_ms, unit="ms").strftime("%Y-%m-%d %H:%M:%S"),
                            pd.to_datetime(int(feat_ts[closest_idx]), unit="ms").strftime("%Y-%m-%d %H:%M:%S"),
                            diffs[closest_idx] / 60000)

        LOG.info("  %s: matched %d / %d trades to bars (exact=%d, closest=%d)",
                 symbol, n_matched, len(sym_trades), n_exact, n_closest)

        # ── Mark NEGATIVE samples ──
        # Bars that are NOT within ±15min of any entry
        entry_ms_set = set()
        for rounded_ms, exact_ms, _ in sym_lookup:
            entry_ms_set.add(int(rounded_ms))
            entry_ms_set.add(int(exact_ms))

        # Create windows around entries
        entry_windows = set()
        for ems in entry_ms_set:
            low_ms = ems - 900000
            high_ms = ems + 900000
            window_mask = (feat_ts >= low_ms) & (feat_ts <= high_ms)
            window_ts = feat_ts[window_mask]
            entry_windows.update(window_ts.tolist())

        non_entry_mask = ~feat_df["timestamp"].astype(np.int64).isin(entry_windows)
        non_entry_bars = feat_df[non_entry_mask]

        # Sample negative bars
        n_pos = int((feat_df["label"] == 1.0).sum())
        n_neg = min(int(n_pos * args.neg_ratio), len(non_entry_bars))

        if n_neg > 0 and n_pos > 0:
            neg_sample = non_entry_bars.sample(n=n_neg, random_state=42)
            for idx in neg_sample.index:
                feat_df.loc[idx, "trade_direction"] = np.random.choice([1.0, -1.0])
                feat_df.loc[idx, "label"] = 0.0

        # Collect positive + negative samples only
        labeled = feat_df[feat_df["label"].notna() & feat_df["trade_direction"].notna()].copy()
        n_pos_sym = int((labeled["label"] == 1.0).sum())
        n_neg_sym = int((labeled["label"] == 0.0).sum())
        LOG.info("  %s: %d positive + %d negative = %d total",
                 symbol, n_pos_sym, n_neg_sym, len(labeled))

        all_features.append(labeled)

    if not all_features:
        LOG.error("No features computed! Check that OHLCV data was downloaded successfully.")
        LOG.error("Try running with --clear-cache to force re-download")
        sys.exit(1)

    combined = pd.concat(all_features, ignore_index=True)
    n_total = len(combined)
    n_pos_total = int((combined["label"] == 1.0).sum())
    n_neg_total = int((combined["label"] == 0.0).sum())

    LOG.info("Combined dataset: %d rows (%d pos / %d neg = %.1f:1 ratio)",
             n_total, n_pos_total, n_neg_total,
             n_neg_total / max(n_pos_total, 1))

    # Save
    output_path = DATA_DIR / "dataset.parquet"
    combined.to_parquet(output_path, index=False)
    LOG.info("Saved to %s", output_path)

    # Feature columns for training
    feat_cols_path = DATA_DIR / "feature_columns.json"
    with open(feat_cols_path, "w") as f:
        json.dump([c for c in FEATURE_NAMES if c != "label"], f, indent=2)

    print(f"\n{'='*70}")
    print(f"V9 DATASET BUILT")
    print(f"{'='*70}")
    print(f"  Total: {n_total} rows")
    print(f"  Positive (trader entries): {n_pos_total}")
    print(f"  Negative (random bars): {n_neg_total}")
    print(f"  Ratio: {n_neg_total / max(n_pos_total, 1):.1f}:1")
    print(f"  Features: {N_FEATURES}")
    print(f"  Output: {output_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
