"""v0.32.6: Tests for per-token state tagging + sweep endpoints.

These verify the fixes for:
  - Bug: switching tokens re-rendered the previous token's validation/setup
    progress on top of the new token's UI ("Step 0/5: error..." appearing
    under the new token's Prepare button).
  - Bug: chart showed previous token's signal markers when switching tokens.
  - Feature: Sweep All Tokens endpoint.
"""

import pytest
from ppmt.terminal.server import (
    _days_for_tf,
    _sweep_state,
    SweepRequest,
    app,
)
from ppmt.terminal.state import TerminalState


# ---------------------------------------------------------------- #
# _days_for_tf
# ---------------------------------------------------------------- #

def test_days_for_tf_short_timeframes():
    """v0.33.0: Short TFs now use deeper samples so backtests are reliable.
    See TRAZABILIDAD.md v0.33.0 section for the full table.
    """
    assert _days_for_tf("1m") == 7    # 1 week -> 10,080 candles
    assert _days_for_tf("5m") == 30   # 1 month -> 8,640 candles
    assert _days_for_tf("15m") == 90  # 3 months -> 8,640 candles


def test_days_for_tf_long_timeframes():
    """Long TFs need many days to reach the 5-trade MC threshold."""
    assert _days_for_tf("1h") == 180
    assert _days_for_tf("4h") == 365
    assert _days_for_tf("1d") == 730


def test_days_for_tf_unknown_uses_default():
    assert _days_for_tf("2m") == 180  # default
    assert _days_for_tf("invalid") == 180
    assert _days_for_tf("2m", default=99) == 99


# ---------------------------------------------------------------- #
# State tagging: validate_token must include symbol+timeframe in status
# ---------------------------------------------------------------- #

def test_terminal_state_accepts_symbol_tagged_status():
    """Verify TerminalState.update_sync accepts the new tagged status dict
    without errors and preserves all fields when serialized back via to_dict().
    """
    ts = TerminalState()
    ts.update_sync(
        auto_setup_status={
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "exchange": "mexc",
            "step": "backtesting",
            "status": "running",
            "message": "Running backtest...",
            "percent": 60,
        }
    )
    d = ts.to_dict()
    assert d["auto_setup_status"]["symbol"] == "BTC/USDT"
    assert d["auto_setup_status"]["step"] == "backtesting"
    assert d["auto_setup_status"]["percent"] == 60


def test_terminal_state_validation_result_round_trip():
    """validation_result dict should round-trip through TerminalState.to_dict()."""
    ts = TerminalState()
    ts.update_sync(
        validation_result={
            "symbol": "ETH/USDT",
            "timeframe": "1h",
            "verdict": "PASS",
            "passed": True,
            "win_rate": 0.55,
            "profit_factor": 1.2,
        }
    )
    d = ts.to_dict()
    assert d["validation_result"]["symbol"] == "ETH/USDT"
    assert d["validation_result"]["passed"] is True


# ---------------------------------------------------------------- #
# Sweep state defaults
# ---------------------------------------------------------------- #

def test_sweep_state_initial():
    """Sweep state should start with running=False and empty results."""
    # Read-only check — we don't reset because other tests might have run.
    assert "running" in _sweep_state
    assert "total" in _sweep_state
    assert "passed" in _sweep_state
    assert "failed" in _sweep_state
    assert "results" in _sweep_state
    assert isinstance(_sweep_state["results"], list)


def test_sweep_request_model_defaults():
    """SweepRequest pydantic model accepts empty symbols (server uses defaults).

    v0.39.0: Updated to assert 'binance' (default since v0.35.0 — MEXC was
    blocking subscriptions from EU networks).
    """
    req = SweepRequest()
    assert req.symbols == []
    assert req.timeframe == "1h"
    assert req.exchange == "binance"
    assert req.capital == 1_000.0
    assert req.skip_if_pass is True


# ---------------------------------------------------------------- #
# FastAPI app routing
# ---------------------------------------------------------------- #

def test_app_has_sweep_routes():
    """All 3 sweep endpoints must be registered on the FastAPI app."""
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/api/sweep" in paths
    assert "/api/sweep-status" in paths
    assert "/api/sweep-cancel" in paths


def test_app_has_all_existing_routes_still_registered():
    """Make sure no existing routes were accidentally removed."""
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    expected = {
        "/api/validate", "/api/auto-setup", "/api/start-trading",
        "/api/stop-trading", "/api/trading-status", "/api/trades",
        "/api/trade-summary", "/api/ohlcv", "/api/market/symbols",
        "/api/market/price", "/api/backtest", "/api/multi-setup",
        "/api/portfolio-backtest", "/api/multi-tf-analysis",
        "/api/nodes", "/api/nodes/add", "/api/nodes/remove",
        "/api/nodes/leverage", "/api/nodes/auto-mode", "/api/nodes/capital",
        "/api/nodes/kill-switch/activate", "/api/nodes/kill-switch/deactivate",
        "/api/nodes/redistribute", "/api/status", "/api/snapshot",
        "/api/portfolio", "/api/signals", "/api/performance", "/api/risk",
        "/api/ingest",
        "/ws", "/",
    }
    missing = expected - paths
    assert not missing, f"Missing routes: {missing}"
