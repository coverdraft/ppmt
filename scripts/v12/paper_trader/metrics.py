"""
metrics.py — Performance metrics for V12 paper trading.

Computes:
  - Win Rate (overall, by side, by regime)
  - Sharpe Ratio (annualized)
  - Max Drawdown and current drawdown
  - Profit Factor
  - Prediction accuracy and calibration
  - Performance stability (WR over rolling windows)
  - Regime-aware metrics (trending vs ranging, volatile vs calm)
"""
from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from .database import TradeDB

LOG = logging.getLogger("v12_metrics")


# ============================================================================
# Core metrics
# ============================================================================

def compute_trade_metrics(db: TradeDB, last_n: int | None = None) -> dict:
    """Compute comprehensive trade performance metrics.

    Args:
        db: TradeDB instance
        last_n: If set, only use last N trades (for recency)

    Returns:
        Dict with all performance metrics.
    """
    stats = db.get_trade_stats(last_n=last_n)
    return stats


def compute_equity_metrics(db: TradeDB) -> dict:
    """Compute equity curve metrics.

    Returns:
        Dict with: current_equity, peak_equity, current_drawdown,
        max_drawdown, equity_curve_length, last_update_ts.
    """
    curve = db.get_equity_curve()
    if not curve:
        return {
            "current_equity": 0, "peak_equity": 0,
            "current_drawdown": 0, "max_drawdown": 0,
            "equity_curve_length": 0, "last_update_ts": 0,
        }

    equities = [r["equity_pct"] for r in curve]
    current = equities[-1]
    peak = max(equities)

    # Drawdowns
    cum_max = np.maximum.accumulate(equities)
    drawdowns = np.array(equities) - cum_max
    current_dd = float(drawdowns[-1])
    max_dd = float(drawdowns.min())

    return {
        "current_equity": current,
        "peak_equity": peak,
        "current_drawdown": current_dd,
        "max_drawdown": max_dd,
        "equity_curve_length": len(curve),
        "last_update_ts": curve[-1]["ts_utc"],
    }


def compute_prediction_metrics(db: TradeDB, hours: int = 24) -> dict:
    """Compute prediction accuracy metrics.

    Args:
        db: TradeDB instance
        hours: Lookback window in hours

    Returns:
        Dict with accuracy, calibration, and distribution info.
    """
    return db.get_prediction_accuracy(hours=hours)


# ============================================================================
# Regime-aware metrics
# ============================================================================

def compute_regime_metrics(db: TradeDB) -> dict:
    """Compute performance broken down by market regime.

    Uses trend_1h and vol_regime from predictions to categorize trades:
    - trending_up: trend_1h > 0.5
    - trending_down: trend_1h < -0.5
    - ranging: |trend_1h| <= 0.5
    - volatile: vol_regime_1h > 1.0
    - calm: vol_regime_1h <= 1.0

    Returns:
        Dict with per-regime win rates and trade counts.
    """
    preds = db.get_recent_predictions(n=500)

    if not preds:
        return {"regimes": {}, "total_with_regime": 0}

    # Group by regime
    regimes = {
        "trending_up": {"preds": [], "outcomes": []},
        "trending_down": {"preds": [], "outcomes": []},
        "ranging": {"preds": [], "outcomes": []},
        "volatile": {"preds": [], "outcomes": []},
        "calm": {"preds": [], "outcomes": []},
    }

    for p in preds:
        if p["actual_outcome"] is None:
            continue

        trend = p.get("trend_1h", 0) or 0
        vol = p.get("vol_regime_1h", 0) or 0
        outcome = p["actual_outcome"]

        # Trend regime
        if trend > 0.5:
            regimes["trending_up"]["preds"].append(p["pred"])
            regimes["trending_up"]["outcomes"].append(outcome)
        elif trend < -0.5:
            regimes["trending_down"]["preds"].append(p["pred"])
            regimes["trending_down"]["outcomes"].append(outcome)
        else:
            regimes["ranging"]["preds"].append(p["pred"])
            regimes["ranging"]["outcomes"].append(outcome)

        # Vol regime
        if vol > 1.0:
            regimes["volatile"]["preds"].append(p["pred"])
            regimes["volatile"]["outcomes"].append(outcome)
        else:
            regimes["calm"]["preds"].append(p["pred"])
            regimes["calm"]["outcomes"].append(outcome)

    result = {}
    total = 0
    for name, data in regimes.items():
        n = len(data["outcomes"])
        total += n
        if n > 0:
            correct = sum(1 for p, o in zip(data["preds"], data["outcomes"])
                          if (p > 0.5 and o == 1) or (p <= 0.5 and o == 0))
            result[name] = {
                "n": n,
                "accuracy": correct / n,
                "avg_pred_up": sum(data["preds"]) / n,
                "actual_up_pct": sum(data["outcomes"]) / n,
            }
        else:
            result[name] = {"n": 0, "accuracy": 0, "avg_pred_up": 0, "actual_up_pct": 0}

    result["total_with_regime"] = total
    return {"regimes": result, "total_with_regime": total}


# ============================================================================
# Stability metrics
# ============================================================================

def compute_stability_metrics(db: TradeDB, window_size: int = 20) -> dict:
    """Compute performance stability over rolling windows.

    Splits trades into non-overlapping windows and computes WR per window.
    Stability = how consistent the WR is across windows.

    Returns:
        Dict with: wr_windows, wr_mean, wr_std, wr_min, wr_max,
        stability_score (1.0 = perfectly consistent, 0.0 = wildly varying).
    """
    trades = db.get_trades(limit=500)
    if len(trades) < window_size:
        return {
            "wr_windows": [], "wr_mean": 0, "wr_std": 0,
            "wr_min": 0, "wr_max": 0, "stability_score": 0,
            "n_windows": 0,
        }

    # Reverse to chronological order
    trades = list(reversed(trades))
    pnls = [t.get("pnl_net_pct", 0) or 0 for t in trades]

    # Split into windows
    wr_windows = []
    for i in range(0, len(pnls) - window_size + 1, window_size):
        window = pnls[i:i + window_size]
        wins = sum(1 for p in window if p > 0)
        wr_windows.append(wins / len(window))

    if not wr_windows:
        return {
            "wr_windows": [], "wr_mean": 0, "wr_std": 0,
            "wr_min": 0, "wr_max": 0, "stability_score": 0,
            "n_windows": 0,
        }

    wr_mean = float(np.mean(wr_windows))
    wr_std = float(np.std(wr_windows))
    wr_min = float(min(wr_windows))
    wr_max = float(max(wr_windows))

    # Stability score: 1 - coefficient of variation (capped at 0)
    cv = wr_std / wr_mean if wr_mean > 0 else 1.0
    stability = max(0.0, 1.0 - cv)

    return {
        "wr_windows": [round(w, 3) for w in wr_windows],
        "wr_mean": round(wr_mean, 4),
        "wr_std": round(wr_std, 4),
        "wr_min": round(wr_min, 4),
        "wr_max": round(wr_max, 4),
        "stability_score": round(stability, 4),
        "n_windows": len(wr_windows),
    }


# ============================================================================
# Full report
# ============================================================================

def generate_report(db: TradeDB) -> dict:
    """Generate a complete performance report.

    Combines all metrics into a single comprehensive report.
    """
    trade_metrics = compute_trade_metrics(db)
    equity_metrics = compute_equity_metrics(db)
    pred_metrics = compute_prediction_metrics(db, hours=24)
    pred_metrics_7d = compute_prediction_metrics(db, hours=168)
    regime_metrics = compute_regime_metrics(db)
    stability_metrics = compute_stability_metrics(db)

    return {
        "symbol": db.symbol,
        "generated_at": int(time.time() * 1000),
        "trade": trade_metrics,
        "equity": equity_metrics,
        "predictions_24h": pred_metrics,
        "predictions_7d": pred_metrics_7d,
        "regime": regime_metrics,
        "stability": stability_metrics,
    }


def format_report(report: dict) -> str:
    """Format a report dict into a human-readable string."""
    lines = []
    sym = report["symbol"]
    lines.append(f"{'='*60}")
    lines.append(f"  V12 PERFORMANCE REPORT — {sym}")
    lines.append(f"{'='*60}")

    # Trade metrics
    t = report["trade"]
    lines.append(f"\n  TRADE PERFORMANCE")
    lines.append(f"  {'─'*40}")
    lines.append(f"  Total trades:    {t['n_trades']}")
    lines.append(f"  Win rate:        {t['win_rate']:.1%}")
    lines.append(f"  Long:  {t['n_long']} (WR {t['wr_long']:.1%})  Short: {t['n_short']} (WR {t['wr_short']:.1%})")
    lines.append(f"  Total PnL:       {t['total_pnl']:+.3f}%")
    lines.append(f"  Avg PnL/trade:   {t['avg_pnl']:+.4f}%")
    lines.append(f"  Profit factor:   {t['profit_factor']:.2f}")
    lines.append(f"  Sharpe (ann.):   {t['sharpe']:.2f}")
    lines.append(f"  Max drawdown:    {t['max_drawdown']:.3f}%")
    lines.append(f"  Max win:         {t['max_win']:+.3f}%  Max loss: {t['max_loss']:+.3f}%")
    lines.append(f"  Avg bars held:   {t['avg_bars_held']:.1f}")

    # Equity
    e = report["equity"]
    lines.append(f"\n  EQUITY")
    lines.append(f"  {'─'*40}")
    lines.append(f"  Current equity:  {e['current_equity']:.3f}%")
    lines.append(f"  Peak equity:     {e['peak_equity']:.3f}%")
    lines.append(f"  Current DD:      {e['current_drawdown']:.3f}%")
    lines.append(f"  Max DD:          {e['max_drawdown']:.3f}%")
    lines.append(f"  Snapshots:       {e['equity_curve_length']}")

    # Predictions
    for label, p in [("24h", report["predictions_24h"]), ("7d", report["predictions_7d"])]:
        lines.append(f"\n  PREDICTIONS ({label})")
        lines.append(f"  {'─'*40}")
        lines.append(f"  Predicted:       {p['n_predicted']}")
        lines.append(f"  With outcome:    {p['n_with_outcome']}")
        if p['n_with_outcome'] > 0:
            lines.append(f"  Accuracy:        {p['accuracy']:.1%}")
            lines.append(f"  Avg P(UP):       {p['avg_pred_up']:.4f}")
            lines.append(f"  Actual UP%:      {p['actual_up_pct']:.1%}")
            lines.append(f"  Calibration err: {p['calibration_error']:.4f}")

    # Regime
    r = report["regime"]
    lines.append(f"\n  REGIME PERFORMANCE")
    lines.append(f"  {'─'*40}")
    for regime_name in ["trending_up", "trending_down", "ranging", "volatile", "calm"]:
        rd = r["regimes"].get(regime_name, {})
        if rd.get("n", 0) > 0:
            lines.append(f"  {regime_name:<16} n={rd['n']:>3}  acc={rd['accuracy']:.1%}  "
                         f"pred_up={rd['avg_pred_up']:.3f}  actual_up={rd['actual_up_pct']:.3f}")

    # Stability
    s = report["stability"]
    lines.append(f"\n  STABILITY")
    lines.append(f"  {'─'*40}")
    lines.append(f"  WR windows:      {s['n_windows']}")
    if s['n_windows'] > 0:
        lines.append(f"  WR mean:         {s['wr_mean']:.3f}")
        lines.append(f"  WR std:          {s['wr_std']:.3f}")
        lines.append(f"  WR range:        [{s['wr_min']:.3f}, {s['wr_max']:.3f}]")
        lines.append(f"  Stability score: {s['stability_score']:.3f}")

    lines.append(f"\n{'='*60}")
    return "\n".join(lines)
