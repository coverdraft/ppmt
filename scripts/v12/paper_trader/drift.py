"""
drift.py — Model drift detection for V12 paper trading.

Detects when the model's performance degrades or the market regime changes,
triggering recommendations for retraining.

Drift types:
  1. WR decline: Recent win rate drops below baseline (walk-forward or recent best)
  2. Prediction shift: Distribution of P(UP) changes significantly
  3. Sharpe decline: Risk-adjusted return drops below baseline
  4. Regime change: Market volatility or trend regime shifts dramatically

Design:
  - Uses the predictions table in SQLite to track model behavior over time
  - Compares recent metrics to baseline (from walk-forward validation or
    the first stable period of trading)
  - Severity levels: 'warning' (monitor) vs 'critical' (action needed)
  - Recommendations: 'monitor', 'reduce_position', 'retrain_recommended', 'retrain_urgent'
"""
from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from .database import TradeDB
from .metrics import compute_trade_metrics, compute_prediction_metrics

LOG = logging.getLogger("v12_drift")

# ============================================================================
# Configuration
# ============================================================================

# WR decline thresholds
WR_WARNING_THRESHOLD = 0.10    # 10pp below baseline → warning
WR_CRITICAL_THRESHOLD = 0.20   # 20pp below baseline → critical

# Prediction distribution shift (KS-like test on means)
PRED_SHIFT_WARNING = 0.05      # 5pp mean shift → warning
PRED_SHIFT_CRITICAL = 0.10     # 10pp mean shift → critical

# Sharpe decline
SHARPE_WARNING_THRESHOLD = 0.5  # 50% below baseline → warning
SHARPE_CRITICAL_THRESHOLD = 0.0 # Negative Sharpe → critical

# Minimum data needed for drift detection
MIN_PREDICTIONS_FOR_DRIFT = 50
MIN_TRADES_FOR_DRIFT = 10

# Lookback windows
SHORT_WINDOW_H = 6             # 6 hours for recent metrics
MEDIUM_WINDOW_H = 24           # 24 hours for medium-term
LONG_WINDOW_H = 168            # 7 days for baseline


# ============================================================================
# Drift detectors
# ============================================================================

def detect_wr_decline(db: TradeDB, baseline_wr: float = 0.60) -> list[dict]:
    """Detect win rate decline vs baseline.

    Args:
        db: TradeDB instance
        baseline_wr: Baseline win rate (from walk-forward validation)

    Returns:
        List of drift events (may be empty).
    """
    events = []

    # Need enough trades
    stats = db.get_trade_stats()
    if stats["n_trades"] < MIN_TRADES_FOR_DRIFT:
        return events

    current_wr = stats["win_rate"]

    # Also check recent trades (last 20)
    recent_stats = db.get_trade_stats(last_n=20)
    if recent_stats["n_trades"] >= MIN_TRADES_FOR_DRIFT:
        recent_wr = recent_stats["win_rate"]
    else:
        recent_wr = current_wr

    # Overall WR check
    delta = current_wr - baseline_wr
    if delta < -WR_CRITICAL_THRESHOLD:
        events.append({
            "drift_type": "wr_decline",
            "severity": "critical",
            "metric_name": "win_rate_overall",
            "current_value": current_wr,
            "baseline_value": baseline_wr,
            "delta": delta,
            "threshold": -WR_CRITICAL_THRESHOLD,
            "recommendation": "retrain_urgent",
        })
    elif delta < -WR_WARNING_THRESHOLD:
        events.append({
            "drift_type": "wr_decline",
            "severity": "warning",
            "metric_name": "win_rate_overall",
            "current_value": current_wr,
            "baseline_value": baseline_wr,
            "delta": delta,
            "threshold": -WR_WARNING_THRESHOLD,
            "recommendation": "retrain_recommended",
        })

    # Recent WR check (more sensitive to recent changes)
    if recent_stats["n_trades"] >= MIN_TRADES_FOR_DRIFT:
        delta_recent = recent_wr - baseline_wr
        if delta_recent < -WR_CRITICAL_THRESHOLD and current_wr >= baseline_wr - WR_WARNING_THRESHOLD:
            # Recent is worse but overall is still OK — early warning
            events.append({
                "drift_type": "wr_decline",
                "severity": "warning",
                "metric_name": "win_rate_recent_20",
                "current_value": recent_wr,
                "baseline_value": baseline_wr,
                "delta": delta_recent,
                "threshold": -WR_CRITICAL_THRESHOLD,
                "recommendation": "monitor",
            })

    return events


def detect_prediction_shift(db: TradeDB, baseline_mean: float = 0.55) -> list[dict]:
    """Detect shifts in prediction distribution.

    If the model's average P(UP) shifts significantly from baseline,
    it may indicate the model is miscalibrated for current market conditions.

    Args:
        db: TradeDB instance
        baseline_mean: Baseline average prediction (from training data)

    Returns:
        List of drift events.
    """
    events = []

    # Get recent predictions
    preds = db.get_recent_predictions(n=200)
    if len(preds) < MIN_PREDICTIONS_FOR_DRIFT:
        return events

    pred_values = [p["pred"] for p in preds]

    # Recent vs older comparison
    n = len(pred_values)
    mid = n // 2
    recent_half = pred_values[:mid]      # Most recent
    older_half = pred_values[mid:]        # Older

    if len(recent_half) < 20 or len(older_half) < 20:
        return events

    recent_mean = float(np.mean(recent_half))
    older_mean = float(np.mean(older_half))
    baseline_delta = recent_mean - baseline_mean
    temporal_delta = recent_mean - older_mean

    # Check shift from baseline
    if abs(baseline_delta) > PRED_SHIFT_CRITICAL:
        events.append({
            "drift_type": "pred_shift",
            "severity": "critical",
            "metric_name": "pred_mean_vs_baseline",
            "current_value": recent_mean,
            "baseline_value": baseline_mean,
            "delta": baseline_delta,
            "threshold": PRED_SHIFT_CRITICAL,
            "recommendation": "retrain_recommended",
        })
    elif abs(baseline_delta) > PRED_SHIFT_WARNING:
        events.append({
            "drift_type": "pred_shift",
            "severity": "warning",
            "metric_name": "pred_mean_vs_baseline",
            "current_value": recent_mean,
            "baseline_value": baseline_mean,
            "delta": baseline_delta,
            "threshold": PRED_SHIFT_WARNING,
            "recommendation": "monitor",
        })

    # Check temporal shift (recent vs older within the trading period)
    if abs(temporal_delta) > PRED_SHIFT_CRITICAL:
        events.append({
            "drift_type": "pred_shift",
            "severity": "warning",
            "metric_name": "pred_mean_temporal_shift",
            "current_value": recent_mean,
            "baseline_value": older_mean,
            "delta": temporal_delta,
            "threshold": PRED_SHIFT_CRITICAL,
            "recommendation": "monitor",
        })

    return events


def detect_sharpe_decline(db: TradeDB, baseline_sharpe: float = 0.3) -> list[dict]:
    """Detect Sharpe ratio decline.

    Args:
        db: TradeDB instance
        baseline_sharpe: Baseline Sharpe (from walk-forward validation)

    Returns:
        List of drift events.
    """
    events = []

    stats = db.get_trade_stats()
    if stats["n_trades"] < MIN_TRADES_FOR_DRIFT:
        return events

    current_sharpe = stats["sharpe"]

    # Critical: negative Sharpe
    if current_sharpe < SHARPE_CRITICAL_THRESHOLD:
        events.append({
            "drift_type": "sharpe_decline",
            "severity": "critical",
            "metric_name": "sharpe_overall",
            "current_value": current_sharpe,
            "baseline_value": baseline_sharpe,
            "delta": current_sharpe - baseline_sharpe,
            "threshold": SHARPE_CRITICAL_THRESHOLD,
            "recommendation": "retrain_urgent",
        })
    # Warning: significant decline
    elif current_sharpe < baseline_sharpe * SHARPE_WARNING_THRESHOLD:
        events.append({
            "drift_type": "sharpe_decline",
            "severity": "warning",
            "metric_name": "sharpe_overall",
            "current_value": current_sharpe,
            "baseline_value": baseline_sharpe,
            "delta": current_sharpe - baseline_sharpe,
            "threshold": baseline_sharpe * SHARPE_WARNING_THRESHOLD,
            "recommendation": "reduce_position",
        })

    return events


def detect_regime_change(db: TradeDB) -> list[dict]:
    """Detect market regime changes from prediction features.

    Monitors:
    - trend_1h: If recent average trend is very different from older
    - vol_regime_1h: If volatility regime has shifted

    Returns:
        List of drift events.
    """
    events = []
    preds = db.get_recent_predictions(n=200)

    if len(preds) < MIN_PREDICTIONS_FOR_DRIFT:
        return events

    # Split into recent and older
    n = len(preds)
    mid = n // 2
    recent = preds[:mid]
    older = preds[mid:]

    # Trend shift
    recent_trends = [p.get("trend_1h", 0) or 0 for p in recent]
    older_trends = [p.get("trend_1h", 0) or 0 for p in older]

    if len(recent_trends) >= 20 and len(older_trends) >= 20:
        recent_trend_mean = float(np.mean(recent_trends))
        older_trend_mean = float(np.mean(older_trends))
        trend_delta = recent_trend_mean - older_trend_mean

        # Large trend shift (> 1.0 on [-1, 1] scale)
        if abs(trend_delta) > 1.0:
            events.append({
                "drift_type": "regime_change",
                "severity": "warning",
                "metric_name": "trend_1h_mean",
                "current_value": recent_trend_mean,
                "baseline_value": older_trend_mean,
                "delta": trend_delta,
                "threshold": 1.0,
                "recommendation": "monitor",
            })

    # Volatility shift
    recent_vols = [p.get("vol_regime_1h", 0) or 0 for p in recent]
    older_vols = [p.get("vol_regime_1h", 0) or 0 for p in older]

    if len(recent_vols) >= 20 and len(older_vols) >= 20:
        recent_vol_mean = float(np.mean(recent_vols))
        older_vol_mean = float(np.mean(older_vols))
        vol_delta = recent_vol_mean - older_vol_mean

        # Large volatility shift (> 0.5 change)
        if abs(vol_delta) > 0.5:
            severity = "critical" if abs(vol_delta) > 1.0 else "warning"
            events.append({
                "drift_type": "regime_change",
                "severity": severity,
                "metric_name": "vol_regime_1h_mean",
                "current_value": recent_vol_mean,
                "baseline_value": older_vol_mean,
                "delta": vol_delta,
                "threshold": 0.5,
                "recommendation": "reduce_position" if severity == "warning" else "retrain_recommended",
            })

    return events


# ============================================================================
# Main drift check
# ============================================================================

def run_drift_check(db: TradeDB, baseline: dict | None = None) -> list[dict]:
    """Run all drift detection checks and record events.

    Args:
        db: TradeDB instance
        baseline: Dict with baseline metrics (wr, sharpe, pred_mean).
                  If None, uses walk-forward values from V12 config.

    Returns:
        List of all drift events found.
    """
    # Default baselines from V12 walk-forward validation
    if baseline is None:
        from .model import get_symbol_config
        cfg = get_symbol_config(db.symbol)
        baseline = {
            "wr": cfg.get("wr_wf", 0.60),
            "sharpe": cfg.get("sharpe_wf", 0.30),
            "pred_mean": 0.55,  # Typical for binary classification
        }

    all_events = []

    # 1. Win rate decline
    wr_events = detect_wr_decline(db, baseline_wr=baseline["wr"])
    all_events.extend(wr_events)

    # 2. Prediction distribution shift
    pred_events = detect_prediction_shift(db, baseline_mean=baseline["pred_mean"])
    all_events.extend(pred_events)

    # 3. Sharpe decline
    sharpe_events = detect_sharpe_decline(db, baseline_sharpe=baseline["sharpe"])
    all_events.extend(sharpe_events)

    # 4. Regime change
    regime_events = detect_regime_change(db)
    all_events.extend(regime_events)

    # Record all events to database
    ts_now = int(time.time() * 1000)
    model_version = db.get_active_model_version()
    for event in all_events:
        event["ts_utc"] = ts_now
        event["model_version"] = model_version
        db.insert_drift_event(event)

    if all_events:
        critical = [e for e in all_events if e["severity"] == "critical"]
        warnings = [e for e in all_events if e["severity"] == "warning"]
        LOG.warning("drift_check: %s — %d critical, %d warning",
                    db.symbol, len(critical), len(warnings))
        for e in critical:
            LOG.warning("  CRITICAL: %s %s current=%.4f baseline=%.4f delta=%.4f → %s",
                        e["drift_type"], e["metric_name"],
                        e["current_value"], e["baseline_value"],
                        e["delta"], e["recommendation"])
        for e in warnings:
            LOG.info("  WARNING: %s %s current=%.4f baseline=%.4f delta=%.4f → %s",
                     e["drift_type"], e["metric_name"],
                     e["current_value"], e["baseline_value"],
                     e["delta"], e["recommendation"])
    else:
        LOG.info("drift_check: %s — no drift detected", db.symbol)

    return all_events


def should_retrain(db: TradeDB, baseline: dict | None = None) -> tuple[bool, str]:
    """Check if retraining is recommended.

    Returns:
        (should_retrain, reason) tuple.
    """
    events = run_drift_check(db, baseline=baseline)

    for e in events:
        if e["recommendation"] in ("retrain_urgent", "retrain_recommended"):
            return True, f"{e['drift_type']}/{e['metric_name']}: {e['recommendation']}"

    return False, ""
