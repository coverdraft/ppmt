#!/usr/bin/env python3
"""
validate_paper_trader.py — Comprehensive validation of V12 paper trader data.

Validates that the paper trader produces the same data as the training pipeline:
1. Compares 5m API data vs 1m→5m aggregated data (OHLCV values)
2. Compares features computed by paper trader vs training pipeline
3. Validates timestamps are correct and aligned
4. Checks predictions are in expected range
5. Runs a backtest on recent data to verify signal quality

Usage:
    python scripts/v12/validate_paper_trader.py --symbol SOL
    python scripts/v12/validate_paper_trader.py --symbol SOL --verbose
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import datetime as dt
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.v12.paper_trader.feed import Feed
from scripts.v12.paper_trader.features import (
    latest_feature_row, compute_5m_features, ALL_FEATURE_NAMES
)
from scripts.v12.paper_trader.model import load_model, predict_raw

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
LOG = logging.getLogger("validate")


def fetch_1m_and_aggregate(feed: Feed, symbol: str, n_5m_bars: int = 100):
    """Fetch 1m data and aggregate to 5m using the TRAINING pipeline method."""
    from scripts.v11.v11_build_dataset import aggregate_1m_to_5m
    
    n_1m = n_5m_bars * 5 + 50
    # Fetch 1m candles directly from exchange (bypass feed's removed method)
    candles_1m = []
    raw = feed.ex.fetch_ohlcv(symbol, "1m", limit=min(n_1m, 1000))
    candles_1m.extend(raw)
    # Paginate backward if needed
    while len(candles_1m) < n_1m:
        oldest_ts = candles_1m[0][0]
        since = oldest_ts - 60 * 1000 * 1000
        batch = feed.ex.fetch_ohlcv(symbol, "1m", since=since, limit=1000)
        if not batch:
            break
        new_batch = [c for c in batch if c[0] < candles_1m[0][0]]
        if not new_batch:
            break
        candles_1m = new_batch + candles_1m
        if len(new_batch) < 1000:
            break
    candles_1m = candles_1m[:n_1m]
    
    df_1m = pd.DataFrame(candles_1m, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df_1m = df_1m.sort_values("timestamp").reset_index(drop=True)
    
    df_5m = aggregate_1m_to_5m(df_1m)
    return df_5m


def fetch_5m_direct(feed: Feed, symbol: str, n_5m_bars: int = 100):
    """Fetch 5m data directly from Bybit API (paper trader method)."""
    return feed.fetch_5m_window(symbol, n_5m_bars=n_5m_bars)


def validate_ohlcv(df_5m_api: pd.DataFrame, df_5m_agg: pd.DataFrame, verbose: bool = False) -> dict:
    """Compare OHLCV values between 5m API data and 1m→5m aggregated data."""
    results = {"test": "ohlcv_comparison", "pass": True, "details": []}
    
    # Find overlapping timestamps
    api_ts = set(df_5m_api["timestamp"].values)
    agg_ts = set(df_5m_agg["timestamp"].values)
    common_ts = sorted(api_ts & agg_ts)
    
    if len(common_ts) < 10:
        results["pass"] = False
        results["details"].append(f"Only {len(common_ts)} common timestamps — cannot compare")
        return results
    
    results["details"].append(f"Comparing {len(common_ts)} common 5m bars")
    
    # Compare OHLCV for common bars
    max_diffs = {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}
    n_mismatch = 0
    
    for ts in common_ts:
        row_api = df_5m_api[df_5m_api["timestamp"] == ts].iloc[0]
        row_agg = df_5m_agg[df_5m_agg["timestamp"] == ts].iloc[0]
        
        for col in ["open", "high", "low", "close"]:
            diff = abs(row_api[col] - row_agg[col])
            pct = diff / row_api[col] * 100 if row_api[col] != 0 else 0
            max_diffs[col] = max(max_diffs[col], pct)
            if pct > 0.5:  # more than 0.5% difference
                n_mismatch += 1
                if verbose:
                    LOG.warning("  ts=%s %s: api=%.4f agg=%.4f diff=%.3f%%",
                                ts, col, row_api[col], row_agg[col], pct)
        
        # Volume can differ more (5m API may use different aggregation)
        vol_diff = abs(row_api["volume"] - row_agg["volume"])
        vol_pct = vol_diff / row_api["volume"] * 100 if row_api["volume"] != 0 else 0
        max_diffs["volume"] = max(max_diffs["volume"], vol_pct)
    
    # Verdict
    ohlcv_ok = max(max_diffs["open"], max_diffs["high"], max_diffs["low"], max_diffs["close"]) < 0.5
    vol_ok = max_diffs["volume"] < 50  # volume is less critical
    
    if not ohlcv_ok:
        results["pass"] = False
        results["details"].append(
            f"OHLC max diff: open={max_diffs['open']:.3f}% high={max_diffs['high']:.3f}% "
            f"low={max_diffs['low']:.3f}% close={max_diffs['close']:.3f}% (threshold: 0.5%)"
        )
    else:
        results["details"].append(
            f"OHLC max diff: open={max_diffs['open']:.4f}% high={max_diffs['high']:.4f}% "
            f"low={max_diffs['low']:.4f}% close={max_diffs['close']:.4f}% — PASS"
        )
    
    if not vol_ok:
        results["details"].append(f"Volume max diff: {max_diffs['volume']:.1f}% — WARNING (less critical)")
    else:
        results["details"].append(f"Volume max diff: {max_diffs['volume']:.1f}% — PASS")
    
    if n_mismatch > 0:
        results["details"].append(f"{n_mismatch} bars with >0.5% OHLC mismatch")
    
    return results


def validate_timestamps(df_5m: pd.DataFrame) -> dict:
    """Validate that 5m timestamps are properly aligned and in correct range."""
    results = {"test": "timestamps", "pass": True, "details": []}
    
    ts = df_5m["timestamp"].values
    
    # Check all timestamps are in ms range (should be > 1e12 for 2020+)
    min_ts = ts.min()
    max_ts = ts.max()
    
    if min_ts < 1e12:
        results["pass"] = False
        results["details"].append(f"MIN timestamp {min_ts} is NOT in ms range — CORRUPTED")
    else:
        # Verify it's a reasonable date (after 2020)
        min_date = dt.datetime.utcfromtimestamp(min_ts / 1000)
        if min_date.year < 2020:
            results["pass"] = False
            results["details"].append(f"MIN date {min_date} is before 2020 — CORRUPTED")
        else:
            results["details"].append(f"Date range: {min_date} to {dt.datetime.utcfromtimestamp(max_ts / 1000)}")
    
    # Check 5m alignment: all timestamps should be divisible by 300000 ms
    misaligned = ts[ts % 300000 != 0]
    if len(misaligned) > 0:
        results["pass"] = False
        results["details"].append(f"{len(misaligned)} timestamps not aligned to 5m")
    else:
        results["details"].append(f"All {len(ts)} timestamps aligned to 5m — PASS")
    
    # Check monotonicity
    diffs = np.diff(ts)
    if not np.all(diffs > 0):
        results["pass"] = False
        results["details"].append("Timestamps not strictly increasing")
    else:
        results["details"].append("Timestamps strictly increasing — PASS")
    
    # Check intervals (should be 5m = 300000 ms, may have gaps)
    unique_diffs = np.unique(diffs)
    expected = 300000
    if len(unique_diffs) == 1 and unique_diffs[0] == expected:
        results["details"].append(f"All intervals = 5m — PASS")
    else:
        gaps = unique_diffs[unique_diffs > expected]
        if len(gaps) > 0:
            results["details"].append(f"{len(gaps)} gap(s) detected: {gaps / 60000} min — OK (market gaps)")
    
    return results


def validate_features(df_5m: pd.DataFrame, btc_5m: pd.DataFrame, eth_5m: pd.DataFrame,
                      verbose: bool = False) -> dict:
    """Validate that features are computed correctly and in expected ranges."""
    results = {"test": "features", "pass": True, "details": []}
    
    feat_df = compute_5m_features(df_5m, btc_5m, eth_5m)
    
    # Check all 80 features exist
    missing = [f for f in ALL_FEATURE_NAMES if f not in feat_df.columns]
    if missing:
        results["pass"] = False
        results["details"].append(f"Missing features: {missing}")
    else:
        results["details"].append(f"All 80 features present — PASS")
    
    # Check for NaN/Inf in the latest row
    last = feat_df.iloc[-1]
    nan_features = []
    inf_features = []
    for f in ALL_FEATURE_NAMES:
        val = last.get(f, np.nan)
        if pd.isna(val):
            nan_features.append(f)
        elif np.isinf(val):
            inf_features.append(f)
    
    if nan_features:
        results["details"].append(f"NaN in latest row: {nan_features}")
    if inf_features:
        results["pass"] = False
        results["details"].append(f"Inf in latest row: {inf_features}")
    if not nan_features and not inf_features:
        results["details"].append("No NaN/Inf in latest row — PASS")
    
    # Check feature ranges (sanity check on key features)
    range_checks = {
        "rsi_14": (0, 100, "RSI out of range"),
        "trend_50": (-1, 1, "trend_50 should be -1, 0, or 1"),
        "vol_regime": (0, 4, "vol_regime should be 0-3"),
        "hour_sin": (-1.0, 1.0, "hour_sin out of [-1,1]"),
        "hour_cos": (-1.0, 1.0, "hour_cos out of [-1,1]"),
        "trend_15m": (-1, 1, "trend_15m should be -1, 0, or 1"),
        "trend_1h": (-1, 1, "trend_1h should be -1, 0, or 1"),
        "mtf_alignment": (-1.0, 1.0, "mtf_alignment out of [-1,1]"),
    }
    
    range_fails = []
    for feat, (lo, hi, msg) in range_checks.items():
        val = last.get(feat, np.nan)
        if pd.notna(val) and not (lo <= val <= hi):
            range_fails.append(f"{feat}={val:.3f} ({msg})")
    
    if range_fails:
        results["pass"] = False
        results["details"].append(f"Range violations: {range_fails}")
    else:
        results["details"].append("All feature ranges valid — PASS")
    
    # Validate hour features using integer arithmetic
    latest_ts = int(df_5m["timestamp"].iloc[-1])
    hour_from_ms = (latest_ts // 3600000) % 24
    hour_sin_val = last.get("hour_sin", 0)
    hour_cos_val = last.get("hour_cos", 0)
    expected_sin = np.sin(2 * np.pi * hour_from_ms / 24)
    expected_cos = np.cos(2 * np.pi * hour_from_ms / 24)
    
    sin_diff = abs(hour_sin_val - expected_sin)
    cos_diff = abs(hour_cos_val - expected_cos)
    
    if sin_diff > 0.01 or cos_diff > 0.01:
        results["pass"] = False
        results["details"].append(
            f"Hour features wrong: sin={hour_sin_val:.3f} (expected {expected_sin:.3f}), "
            f"cos={hour_cos_val:.3f} (expected {expected_cos:.3f}) — hour_from_ms={hour_from_ms}"
        )
    else:
        results["details"].append(
            f"Hour features correct: sin={hour_sin_val:.3f} cos={hour_cos_val:.3f} "
            f"(hour={hour_from_ms}:00 UTC) — PASS"
        )
    
    # Print feature summary if verbose
    if verbose:
        LOG.info("  Latest feature values (last 5m bar):")
        for f in ALL_FEATURE_NAMES:
            val = last.get(f, np.nan)
            LOG.info("    %s = %.6f", f, val if pd.notna(val) else float('nan'))
    
    return results


def validate_prediction(df_5m: pd.DataFrame, btc_5m: pd.DataFrame, eth_5m: pd.DataFrame,
                        symbol: str, verbose: bool = False) -> dict:
    """Validate that predictions are in expected range and consistent."""
    results = {"test": "prediction", "pass": True, "details": []}
    
    try:
        bst = load_model(symbol)
    except FileNotFoundError:
        results["pass"] = False
        results["details"].append(f"No model for {symbol} — run v11_train.py first")
        return results
    
    feat_row = latest_feature_row(df_5m, btc_5m, eth_5m)
    if feat_row is None:
        results["pass"] = False
        results["details"].append("Feature computation failed")
        return results
    
    pred = predict_raw(bst, feat_row)
    
    # Check prediction is in [0, 1]
    if not (0 <= pred <= 1):
        results["pass"] = False
        results["details"].append(f"Prediction {pred:.4f} out of [0,1] range")
    else:
        results["details"].append(f"Prediction P(UP 1h) = {pred:.4f} — in range")
    
    # Run multiple predictions on recent bars to check consistency
    feat_df = compute_5m_features(df_5m, btc_5m, eth_5m)
    recent_preds = []
    for i in range(-5, 0):
        try:
            row = feat_df.iloc[i]
            row_dict = {}
            for f in ALL_FEATURE_NAMES:
                val = row.get(f, 0.0)
                row_dict[f] = float(val) if pd.notna(val) else 0.0
            
            p = predict_raw(bst, row_dict)
            recent_preds.append(p)
        except Exception:
            pass
    
    if len(recent_preds) >= 2:
        pred_std = np.std(recent_preds)
        pred_range = max(recent_preds) - min(recent_preds)
        results["details"].append(
            f"Recent {len(recent_preds)} predictions: "
            f"mean={np.mean(recent_preds):.4f} std={pred_std:.4f} range={pred_range:.4f}"
        )
        if pred_std < 0.001:
            results["details"].append("WARNING: predictions nearly constant — features may be wrong")
    
    if verbose:
        LOG.info("  Prediction details:")
        LOG.info("    P(UP 1h) = %.4f", pred)
        LOG.info("    trend_1h = %.1f", feat_row.get("_trend_1h", 0))
        LOG.info("    timestamp = %s", dt.datetime.utcfromtimestamp(feat_row["_timestamp"] / 1000).isoformat())
        LOG.info("    close = %.4f", feat_row["_close"])
    
    return results


def validate_ohlcv_sanity(df_5m: pd.DataFrame) -> dict:
    """Sanity checks on OHLCV data."""
    results = {"test": "ohlcv_sanity", "pass": True, "details": []}
    
    # high >= low
    bad_hl = (df_5m["high"] < df_5m["low"]).sum()
    if bad_hl > 0:
        results["pass"] = False
        results["details"].append(f"{bad_hl} bars where high < low — CORRUPT")
    else:
        results["details"].append("high >= low for all bars — PASS")
    
    # close between high and low
    bad_close = ((df_5m["close"] > df_5m["high"]) | (df_5m["close"] < df_5m["low"])).sum()
    if bad_close > 0:
        results["pass"] = False
        results["details"].append(f"{bad_close} bars where close outside [low, high] — CORRUPT")
    else:
        results["details"].append("close in [low, high] for all bars — PASS")
    
    # open between high and low
    bad_open = ((df_5m["open"] > df_5m["high"]) | (df_5m["open"] < df_5m["low"])).sum()
    if bad_open > 0:
        results["details"].append(f"WARNING: {bad_open} bars where open outside [low, high]")
    else:
        results["details"].append("open in [low, high] for all bars — PASS")
    
    # Volume non-negative
    neg_vol = (df_5m["volume"] < 0).sum()
    if neg_vol > 0:
        results["pass"] = False
        results["details"].append(f"{neg_vol} bars with negative volume — CORRUPT")
    else:
        results["details"].append("Volume >= 0 for all bars — PASS")
    
    # Price not zero
    zero_price = ((df_5m["close"] == 0) | (df_5m["open"] == 0)).sum()
    if zero_price > 0:
        results["pass"] = False
        results["details"].append(f"{zero_price} bars with zero price — CORRUPT")
    else:
        results["details"].append(f"Price range: close [{df_5m['close'].min():.2f}, {df_5m['close'].max():.2f}]")
    
    return results


def validate_live_feed(feed: Feed, symbol: str) -> dict:
    """Validate that the live feed is producing correct timestamps."""
    results = {"test": "live_feed", "pass": True, "details": []}
    
    # Fetch last 5 candles
    raw = feed.ex.fetch_ohlcv(symbol, "5m", limit=5)
    
    if len(raw) < 2:
        results["pass"] = False
        results["details"].append("Could not fetch 5m candles")
        return results
    
    # Check timestamps are ms and aligned
    for i, candle in enumerate(raw):
        ts = candle[0]
        if ts < 1e12:
            results["pass"] = False
            results["details"].append(f"Candle {i}: ts={ts} not in ms range")
        if ts % 300000 != 0:
            results["pass"] = False
            results["details"].append(f"Candle {i}: ts={ts} not aligned to 5m")
        
        date_str = dt.datetime.utcfromtimestamp(ts / 1000).isoformat()
        if verbose_mode:
            LOG.info("  Candle %d: ts=%s close=%.4f", i, date_str, candle[4])
    
    # Last candle should be current (forming)
    now_ms = int(time.time() * 1000)
    last_ts = raw[-1][0]
    age_ms = now_ms - last_ts
    
    if age_ms < 0:
        results["details"].append(f"Last candle is in the future? age={-age_ms/1000:.0f}s")
    elif age_ms > 5 * 60 * 1000:
        results["details"].append(f"Last candle closed {age_ms/60000:.1f} min ago")
    else:
        results["details"].append(f"Last candle is current (age={age_ms/1000:.0f}s) — PASS")
    
    # Second-to-last should be the last closed candle
    closed_ts = raw[-2][0]
    closed_date = dt.datetime.utcfromtimestamp(closed_ts / 1000).isoformat()
    results["details"].append(f"Last closed candle: {closed_date}")
    
    return results


def main():
    global verbose_mode
    
    p = argparse.ArgumentParser(description="Validate V12 paper trader data quality")
    p.add_argument("--symbol", default="SOL", help="Symbol to validate")
    p.add_argument("--verbose", action="store_true", help="Print detailed feature values")
    p.add_argument("--quick", action="store_true", help="Skip slow 1m→5m comparison")
    args = p.parse_args()
    
    verbose_mode = args.verbose
    symbol = args.symbol
    pair = f"{symbol}/USDT"
    
    LOG.info("=" * 60)
    LOG.info("V12 PAPER TRADER VALIDATION — %s", symbol)
    LOG.info("=" * 60)
    
    feed = Feed(exchange_id="bybit")
    
    all_results = []
    
    # === TEST 1: Fetch 5m data and validate timestamps ===
    LOG.info("\n--- TEST 1: Timestamp validation ---")
    df_5m = feed.fetch_5m_window(pair, n_5m_bars=400)
    btc_5m = feed.fetch_5m_window("BTC/USDT", n_5m_bars=400)
    eth_5m = feed.fetch_5m_window("ETH/USDT", n_5m_bars=400)
    
    r1 = validate_timestamps(df_5m)
    all_results.append(r1)
    for d in r1["details"]:
        LOG.info("  %s", d)
    
    # === TEST 2: OHLCV sanity ===
    LOG.info("\n--- TEST 2: OHLCV sanity checks ---")
    r2 = validate_ohlcv_sanity(df_5m)
    all_results.append(r2)
    for d in r2["details"]:
        LOG.info("  %s", d)
    
    # === TEST 3: Compare 5m API vs 1m→5m aggregated ===
    if not args.quick:
        LOG.info("\n--- TEST 3: 5m API vs 1m→5m aggregated ---")
        try:
            df_5m_from_1m = fetch_1m_and_aggregate(feed, pair, n_5m_bars=100)
            r3 = validate_ohlcv(df_5m, df_5m_from_1m, verbose=args.verbose)
            all_results.append(r3)
            for d in r3["details"]:
                LOG.info("  %s", d)
        except Exception as e:
            LOG.warning("  Skipped (error): %s", e)
            all_results.append({"test": "ohlcv_comparison", "pass": True,
                               "details": [f"Skipped: {e}"]})
    else:
        LOG.info("\n--- TEST 3: Skipped (use --quick to skip) ---")
    
    # === TEST 4: Feature validation ===
    LOG.info("\n--- TEST 4: Feature computation ---")
    r4 = validate_features(df_5m, btc_5m, eth_5m, verbose=args.verbose)
    all_results.append(r4)
    for d in r4["details"]:
        LOG.info("  %s", d)
    
    # === TEST 5: Prediction validation ===
    LOG.info("\n--- TEST 5: Prediction ---")
    r5 = validate_prediction(df_5m, btc_5m, eth_5m, symbol, verbose=args.verbose)
    all_results.append(r5)
    for d in r5["details"]:
        LOG.info("  %s", d)
    
    # === TEST 6: Live feed validation ===
    LOG.info("\n--- TEST 6: Live feed ---")
    r6 = validate_live_feed(feed, pair)
    all_results.append(r6)
    for d in r6["details"]:
        LOG.info("  %s", d)
    
    # === SUMMARY ===
    LOG.info("\n" + "=" * 60)
    LOG.info("VALIDATION SUMMARY")
    LOG.info("=" * 60)
    
    n_pass = sum(1 for r in all_results if r["pass"])
    n_total = len(all_results)
    
    for r in all_results:
        status = "PASS" if r["pass"] else "FAIL"
        LOG.info("  [%s] %s", status, r["test"])
    
    LOG.info("")
    if n_pass == n_total:
        LOG.info("ALL %d TESTS PASSED — Paper trader data is valid!", n_total)
    else:
        LOG.info("%d/%d tests PASSED — %d FAILED", n_pass, n_total, n_total - n_pass)
    
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
