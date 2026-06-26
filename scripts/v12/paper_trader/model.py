"""
model.py — V12 LightGBM model wrapper with optimized trading configs.

Loads pre-trained V11 models and applies V12-validated trading parameters.
The model predicts P(UP in 1h) and uses quantile-based signal generation
with direction mode and trend alignment filters.

Key difference from v7 model.py:
- Uses V11 models (1m microstructure, 80 features)
- V12 SYMBOL_CONFIG with direction_mode and trend_filter
- H=12 (1h) instead of H=288 (24h)
- Supports "both" direction mode (long + short)
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import lightgbm as lgb

from .features import ALL_FEATURE_NAMES

LOG = logging.getLogger("v12_model")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_DIR = PROJECT_ROOT / "data" / "v11" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# V12 validated trading configs (from walk-forward validation)
# Each symbol has balanced + conservative profiles
V12_SYMBOL_CONFIG = {
    "SOL": {
        "balanced": {
            "q_long": 95, "q_short": 5,
            "direction": "both", "trend_filter": "none",
            "window_size": 200, "cost_pct": 0.04,
            "wr_wf": 0.693, "pf_wf": 3.35, "sharpe_wf": 0.385,
            "consistency": "4/4",
        },
        "conservative": {
            "q_long": 95, "q_short": 5,
            "direction": "long_only", "trend_filter": "aligned",
            "window_size": 200, "cost_pct": 0.04,
            "wr_wf": 0.738, "pf_wf": 3.35, "sharpe_wf": 0.383,
            "consistency": "4/4",
        },
    },
    "DOGE": {
        "balanced": {
            "q_long": 95, "q_short": 5,
            "direction": "both", "trend_filter": "none",
            "window_size": 200, "cost_pct": 0.04,
            "wr_wf": 0.649, "pf_wf": 2.40, "sharpe_wf": 0.277,
            "consistency": "6/6",
        },
        "conservative": {
            "q_long": 98, "q_short": 2,
            "direction": "both", "trend_filter": "none",
            "window_size": 200, "cost_pct": 0.04,
            "wr_wf": 0.681, "pf_wf": 3.03, "sharpe_wf": 0.343,
            "consistency": "6/6",
        },
    },
    "AVAX": {
        "balanced": {
            "q_long": 95, "q_short": 5,
            "direction": "both", "trend_filter": "aligned",
            "window_size": 200, "cost_pct": 0.04,
            "wr_wf": 0.622, "pf_wf": 2.62, "sharpe_wf": 0.301,
            "consistency": "6/6",
        },
        "conservative": {
            "q_long": 97, "q_short": 3,
            "direction": "long_only", "trend_filter": "aligned",
            "window_size": 200, "cost_pct": 0.04,
            "wr_wf": 0.625, "pf_wf": 3.35, "sharpe_wf": 0.383,
            "consistency": "6/6",
        },
    },
}

# Default profile to use
DEFAULT_PROFILE = "balanced"

# Horizon: H=12 (1h) — 12 x 5min bars
HORIZON = 12

# Fixed decision thresholds (fallback before quantile window warms up)
PROB_LONG = 0.55
PROB_SHORT = 0.42

# Cost model
COST_PCT = 0.04  # maker fees (limit orders)


def get_symbol_config(symbol: str, profile: str = DEFAULT_PROFILE) -> dict:
    """Get V12 trading config for a symbol.

    Args:
        symbol: Token symbol without /USDT suffix (e.g. "SOL", "DOGE")
        profile: "balanced" or "conservative"
    """
    # Strip /USDT if present
    sym = symbol.replace("/USDT", "").replace("/usdt", "")
    configs = V12_SYMBOL_CONFIG.get(sym, {})
    if profile in configs:
        return configs[profile]
    if "balanced" in configs:
        return configs["balanced"]
    # Fallback defaults
    return {
        "q_long": 95, "q_short": 5,
        "direction": "both", "trend_filter": "none",
        "window_size": 200, "cost_pct": 0.04,
    }


def model_path(symbol: str) -> Path:
    sym = symbol.replace("/USDT", "").replace("/usdt", "")
    return MODEL_DIR / f"v11_clf_{sym}_h12.txt"


def metadata_path(symbol: str) -> Path:
    sym = symbol.replace("/USDT", "").replace("/usdt", "")
    return MODEL_DIR / f"v11_clf_{sym}_h12_meta.json"


def is_trained(symbol: str) -> bool:
    return model_path(symbol).exists()


def load_model(symbol: str) -> lgb.Booster:
    p = model_path(symbol)
    if not p.exists():
        raise FileNotFoundError(f"no V12 model at {p}; train V11 first with v11_train.py")
    return lgb.Booster(model_file=str(p))


def load_metadata(symbol: str) -> dict:
    p = metadata_path(symbol)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def predict(bst: lgb.Booster, feature_row: dict) -> tuple[float, str]:
    """Predict P(UP in 1h) from a feature row.

    Returns (prob_up, decision) where decision is "LONG", "SHORT", or "WAIT".
    This uses fixed thresholds as fallback. The engine uses quantile-based decisions.
    """
    x = np.array([[feature_row.get(f, 0.0) for f in ALL_FEATURE_NAMES]], dtype=np.float32)
    x = np.nan_to_num(x, nan=0.0)
    prob_up = float(bst.predict(x)[0])
    if prob_up > PROB_LONG:
        return prob_up, "LONG"
    if prob_up < PROB_SHORT:
        return prob_up, "SHORT"
    return prob_up, "WAIT"


def predict_raw(bst: lgb.Booster, feature_row: dict) -> float:
    """Predict P(UP in 1h) from a feature row. Returns raw probability."""
    x = np.array([[feature_row.get(f, 0.0) for f in ALL_FEATURE_NAMES]], dtype=np.float32)
    x = np.nan_to_num(x, nan=0.0)
    return float(bst.predict(x)[0])
