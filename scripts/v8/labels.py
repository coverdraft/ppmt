"""
labels.py — v8 Pattern-Informed EV Regression Labels

Based on REAL pattern analysis results:
  BREAKOUT_UP: 88.2% WR, PF 3.10, median 7.1min → TP first = highly likely
  BREAKOUT_DOWN: 66.3% WR, PF 0.65, median 27.9min → SL first or timeout

Label design:
  - Lookahead = 6 bars (30min at 5m) — matches BREAKOUT_UP median duration
  - TP = 1.5×ATR (asymmetric: winners run fast, 7min median)
  - SL = 1.0×ATR (tight: cut BREAKOUT_DOWN entries quickly)
  - TIME STOP at 30min — catches the slow bleed of BREAKOUT_DOWN
  - ATR lagged 3 bars for anti-leakage

Anti-leakage:
  ATR from LAGGED period (3 bars before i).
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

LOG = logging.getLogger("v8_labels")

# Default parameters — tuned from real pattern analysis
DEFAULT_LOOKAHEAD = 6       # 6 bars = 30min at 5m
DEFAULT_TP_ATR_MULT = 1.5   # Winners run fast (7min median) — give room
DEFAULT_SL_ATR_MULT = 1.0   # Tight SL — cut losers fast (BREAKDOWN_DOWN PF 0.65)
DEFAULT_ATR_LAG_OFFSET = 3  # 15min lag for anti-leakage


def compute_ev_labels(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    atr_14: np.ndarray,
    tp_atr_mult: float = DEFAULT_TP_ATR_MULT,
    sl_atr_mult: float = DEFAULT_SL_ATR_MULT,
    lookahead: int = DEFAULT_LOOKAHEAD,
    atr_lag_offset: int = DEFAULT_ATR_LAG_OFFSET,
) -> np.ndarray:
    """Compute Expected Value regression labels with time stop.

    For each bar, simulates a LONG entry with ATR-adaptive TP/SL and time stop.
    The label is the actual return of that trade in %.

    Args:
        closes:      Array of close prices
        highs:       Array of high prices
        lows:        Array of low prices
        atr_14:      Array of ATR(14) values (in price units)
        tp_atr_mult: TP = entry + tp_atr_mult * lagged_atr
        sl_atr_mult: SL = entry - sl_atr_mult * lagged_atr
        lookahead:   Max bars to hold (6 = 30min at 5m)
        atr_lag_offset: Bars to lag ATR for anti-leakage

    Returns:
        Array of float labels (NaN = insufficient data).
    """
    n = len(closes)
    labels = np.full(n, np.nan, dtype=np.float64)

    n_tp = 0
    n_sl = 0
    n_timeout = 0
    sum_tp = 0.0
    sum_sl = 0.0
    sum_timeout = 0.0

    for i in range(atr_lag_offset, n - lookahead):
        atr_val = atr_14[i - atr_lag_offset]
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        entry = closes[i]
        tp_price = entry + tp_atr_mult * atr_val
        sl_price = entry - sl_atr_mult * atr_val

        if tp_price <= entry or sl_price >= entry:
            continue

        tp_pct_val = (tp_price - entry) / entry * 100
        sl_pct_val = (entry - sl_price) / entry * 100

        hit = False
        for j in range(i + 1, i + 1 + lookahead):
            h = highs[j]
            l = lows[j]

            tp_hit = h >= tp_price
            sl_hit = l <= sl_price

            if tp_hit and sl_hit:
                labels[i] = -sl_pct_val
                n_sl += 1
                sum_sl += sl_pct_val
                hit = True
                break
            elif tp_hit:
                labels[i] = tp_pct_val
                n_tp += 1
                sum_tp += tp_pct_val
                hit = True
                break
            elif sl_hit:
                labels[i] = -sl_pct_val
                n_sl += 1
                sum_sl += sl_pct_val
                hit = True
                break

        if not hit:
            actual_ret = (closes[i + lookahead] - entry) / entry * 100
            labels[i] = actual_ret
            n_timeout += 1
            sum_timeout += actual_ret

    n_valid = n_tp + n_sl + n_timeout
    ev = (sum_tp - sum_sl + sum_timeout) / max(n_valid, 1)

    LOG.info(
        "ev_labels: total=%d valid=%d (TP=%d SL=%d timeout=%d) "
        "avg_tp=%.3f%% avg_sl=%.3f%% avg_timeout=%.3f%% EV=%.4f%%",
        n, n_valid, n_tp, n_sl, n_timeout,
        sum_tp / max(n_tp, 1), sum_sl / max(n_sl, 1),
        sum_timeout / max(n_timeout, 1), ev,
    )

    return labels


def compute_ev_labels_both_sides(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    atr_14: np.ndarray,
    tp_atr_mult: float = DEFAULT_TP_ATR_MULT,
    sl_atr_mult: float = DEFAULT_SL_ATR_MULT,
    lookahead: int = DEFAULT_LOOKAHEAD,
    atr_lag_offset: int = DEFAULT_ATR_LAG_OFFSET,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute EV labels for BOTH LONG and SHORT entries."""
    n = len(closes)
    long_labels = np.full(n, np.nan, dtype=np.float64)
    short_labels = np.full(n, np.nan, dtype=np.float64)

    for i in range(atr_lag_offset, n - lookahead):
        atr_val = atr_14[i - atr_lag_offset]
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        entry = closes[i]

        # LONG side
        long_tp = entry + tp_atr_mult * atr_val
        long_sl = entry - sl_atr_mult * atr_val
        long_tp_pct = (long_tp - entry) / entry * 100
        long_sl_pct = (entry - long_sl) / entry * 100

        # SHORT side
        short_tp = entry - tp_atr_mult * atr_val
        short_sl = entry + sl_atr_mult * atr_val
        short_tp_pct = (entry - short_tp) / entry * 100
        short_sl_pct = (short_sl - entry) / entry * 100

        if long_tp <= entry or long_sl >= entry:
            continue

        long_hit = False
        short_hit = False

        for j in range(i + 1, i + 1 + lookahead):
            h = highs[j]
            l = lows[j]

            if not long_hit:
                tp_hit = h >= long_tp
                sl_hit = l <= long_sl
                if tp_hit and sl_hit:
                    long_labels[i] = -long_sl_pct
                    long_hit = True
                elif tp_hit:
                    long_labels[i] = long_tp_pct
                    long_hit = True
                elif sl_hit:
                    long_labels[i] = -long_sl_pct
                    long_hit = True

            if not short_hit:
                tp_hit = l <= short_tp
                sl_hit = h >= short_sl
                if tp_hit and sl_hit:
                    short_labels[i] = -short_sl_pct
                    short_hit = True
                elif tp_hit:
                    short_labels[i] = short_tp_pct
                    short_hit = True
                elif sl_hit:
                    short_labels[i] = -short_sl_pct
                    short_hit = True

            if long_hit and short_hit:
                break

        actual_ret = (closes[i + lookahead] - entry) / entry * 100
        if not long_hit:
            long_labels[i] = actual_ret
        if not short_hit:
            short_labels[i] = -actual_ret

    n_long_valid = int(np.isfinite(long_labels).sum())
    n_short_valid = int(np.isfinite(short_labels).sum())

    LOG.info(
        "ev_labels_both: long_valid=%d (ev=%.4f%%) short_valid=%d (ev=%.4f%%)",
        n_long_valid, np.nanmean(long_labels) if n_long_valid > 0 else 0,
        n_short_valid, np.nanmean(short_labels) if n_short_valid > 0 else 0,
    )

    return long_labels, short_labels


def compute_ev_labels_fast(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    atr_14: np.ndarray,
    tp_atr_mult: float = DEFAULT_TP_ATR_MULT,
    sl_atr_mult: float = DEFAULT_SL_ATR_MULT,
    lookahead: int = DEFAULT_LOOKAHEAD,
    atr_lag_offset: int = DEFAULT_ATR_LAG_OFFSET,
) -> np.ndarray:
    """Fast vectorized EV label computation with time stop."""
    n = len(closes)
    labels = np.full(n, np.nan, dtype=np.float64)

    if n < lookahead + atr_lag_offset + 1:
        return labels

    lagged_atr = np.roll(atr_14, atr_lag_offset)
    lagged_atr[:atr_lag_offset] = np.nan

    tp_prices = closes + tp_atr_mult * lagged_atr
    sl_prices = closes - sl_atr_mult * lagged_atr
    tp_pct_vals = (tp_prices - closes) / np.where(closes > 0, closes, 1) * 100
    sl_pct_vals = (closes - sl_prices) / np.where(closes > 0, closes, 1) * 100

    tp_hit_offset = np.full(n, lookahead + 1, dtype=np.int32)
    sl_hit_offset = np.full(n, lookahead + 1, dtype=np.int32)

    for offset in range(1, lookahead + 1):
        future_h = highs[offset:]
        future_l = lows[offset:]
        current_n = len(future_h)

        tp_at_this = future_h >= tp_prices[:current_n]
        sl_at_this = future_l <= sl_prices[:current_n]

        tp_not_yet = tp_hit_offset[:current_n] > offset
        sl_not_yet = sl_hit_offset[:current_n] > offset

        tp_hit_offset[:current_n] = np.where(
            tp_at_this & tp_not_yet, offset, tp_hit_offset[:current_n]
        )
        sl_hit_offset[:current_n] = np.where(
            sl_at_this & sl_not_yet, offset, sl_hit_offset[:current_n]
        )

    valid_start = atr_lag_offset
    valid_end = n - lookahead

    for i in range(valid_start, valid_end):
        if np.isnan(lagged_atr[i]) or lagged_atr[i] <= 0:
            continue

        tp_off = tp_hit_offset[i]
        sl_off = sl_hit_offset[i]

        if tp_off == sl_off and tp_off <= lookahead:
            labels[i] = -sl_pct_vals[i]
        elif tp_off < sl_off and tp_off <= lookahead:
            labels[i] = tp_pct_vals[i]
        elif sl_off < tp_off and sl_off <= lookahead:
            labels[i] = -sl_pct_vals[i]
        elif tp_off <= lookahead or sl_off <= lookahead:
            if tp_off <= lookahead:
                labels[i] = tp_pct_vals[i]
            else:
                labels[i] = -sl_pct_vals[i]
        else:
            if i + lookahead < n:
                labels[i] = (closes[i + lookahead] - closes[i]) / closes[i] * 100

    n_valid = int(np.isfinite(labels).sum())
    n_tp = int((labels > 0).sum())
    n_sl = int((labels < 0).sum())
    ev = float(np.nanmean(labels)) if n_valid > 0 else 0.0

    LOG.info(
        "ev_labels_fast: total=%d valid=%d (TP=%d SL=%d) EV=%.4f%%",
        n, n_valid, n_tp, n_sl, ev,
    )

    return labels


def label_stats(labels: np.ndarray) -> dict:
    """Return comprehensive statistics for EV labels."""
    valid = labels[np.isfinite(labels)]
    n_total = len(labels)
    n_valid = len(valid)
    n_nan = int(np.isnan(labels).sum())

    if n_valid == 0:
        return {
            "n_total": n_total, "n_valid": 0, "n_nan": n_nan,
            "ev": 0.0, "std": 0.0, "sharpe": 0.0,
            "pct_positive": 0.0, "mean_positive": 0.0, "mean_negative": 0.0,
        }

    pos = valid[valid > 0]
    neg = valid[valid < 0]

    return {
        "n_total": n_total,
        "n_valid": n_valid,
        "n_nan": n_nan,
        "valid_pct": n_valid / max(n_total, 1) * 100,
        "ev": float(np.mean(valid)),
        "median": float(np.median(valid)),
        "std": float(np.std(valid)),
        "sharpe": float(np.mean(valid) / max(np.std(valid), 1e-10)),
        "pct_positive": float((valid > 0).mean() * 100),
        "pct_negative": float((valid < 0).mean() * 100),
        "pct_zero": float((valid == 0).mean() * 100),
        "mean_positive": float(np.mean(pos)) if len(pos) > 0 else 0.0,
        "mean_negative": float(np.mean(neg)) if len(neg) > 0 else 0.0,
        "max_positive": float(np.max(valid)) if len(valid) > 0 else 0.0,
        "max_negative": float(np.min(valid)) if len(valid) > 0 else 0.0,
        "skew": float(np.mean(((valid - np.mean(valid)) / max(np.std(valid), 1e-10)) ** 3)),
    }
