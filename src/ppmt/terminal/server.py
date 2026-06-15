"""
PPMT Terminal Server — FastAPI + WebSocket web dashboard.

Serves the real-time trading dashboard and provides both REST and WebSocket
endpoints for the front-end to consume state from :class:`TerminalState`.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse

from ppmt.terminal.state import TerminalState, get_terminal_state

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Static files path
# ------------------------------------------------------------------ #
_STATIC_DIR = Path(__file__).parent / "static"
_DASHBOARD_HTML_PATH = _STATIC_DIR / "index.html"

# ------------------------------------------------------------------ #
# FastAPI application
# ------------------------------------------------------------------ #
app = FastAPI(title="PPMT Terminal", version="1.0.0")

# Global terminal state (shared with engine)
terminal_state: TerminalState = get_terminal_state()

# Connected WebSocket clients
_ws_clients: set[WebSocket] = set()


# ------------------------------------------------------------------ #
# HTML serving
# ------------------------------------------------------------------ #


def _load_dashboard_html() -> str:
    """Load the dashboard HTML from the static directory."""
    if _DASHBOARD_HTML_PATH.exists():
        return _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    # Fallback inline dashboard
    return _FALLBACK_HTML


@app.get("/")
async def dashboard() -> HTMLResponse:
    """Serve the dashboard HTML."""
    return HTMLResponse(content=_load_dashboard_html())


# ------------------------------------------------------------------ #
# REST endpoints
# ------------------------------------------------------------------ #


@app.get("/api/status")
async def get_status() -> dict:
    """REST endpoint for current terminal status."""
    return terminal_state.to_dict()


@app.get("/api/snapshot")
async def get_snapshot() -> dict:
    """Full state snapshot including uptime."""
    return terminal_state.get_snapshot()


@app.get("/api/portfolio")
async def get_portfolio() -> dict:
    """Portfolio summary."""
    return {
        "value": terminal_state.portfolio_value,
        "cash": terminal_state.cash,
        "unrealized_pnl": terminal_state.unrealized_pnl,
        "realized_pnl": terminal_state.realized_pnl,
        "total_pnl_pct": terminal_state.total_pnl_pct,
        "exposure_pct": terminal_state.exposure_pct,
        "daily_return_pct": terminal_state.daily_return_pct,
        "positions": terminal_state.positions,
    }


@app.get("/api/signals")
async def get_signals() -> dict:
    """Recent signals."""
    return {"signals": terminal_state.signals_history}


@app.get("/api/performance")
async def get_performance() -> dict:
    """Performance metrics."""
    return {
        "total_trades": terminal_state.total_trades,
        "winning_trades": terminal_state.winning_trades,
        "win_rate": terminal_state.win_rate,
        "max_drawdown": terminal_state.max_drawdown,
        "equity_curve": terminal_state.equity_curve,
        "equity_timestamps": terminal_state.equity_timestamps,
    }


@app.get("/api/risk")
async def get_risk() -> dict:
    """Risk state."""
    return {
        "circuit_breakers": terminal_state.circuit_breakers,
        "is_trading_allowed": terminal_state.is_trading_allowed,
        "exposure_pct": terminal_state.exposure_pct,
        "daily_return_pct": terminal_state.daily_return_pct,
        "max_drawdown": terminal_state.max_drawdown,
    }


# ------------------------------------------------------------------ #
# WebSocket endpoint
# ------------------------------------------------------------------ #


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Real-time WebSocket endpoint for live state updates."""
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info("WebSocket client connected (total: %d)", len(_ws_clients))
    try:
        while True:
            snapshot = terminal_state.to_dict()
            try:
                await websocket.send_json(snapshot)
            except Exception:
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)
        logger.info("WebSocket client disconnected (total: %d)", len(_ws_clients))


async def _broadcast_state() -> None:
    """Broadcast current state to all connected WebSocket clients."""
    if not _ws_clients:
        return
    snapshot = terminal_state.to_dict()
    stale: list[WebSocket] = []
    for ws in _ws_clients:
        try:
            await ws.send_json(snapshot)
        except Exception:
            stale.append(ws)
    for ws in stale:
        _ws_clients.discard(ws)


# ------------------------------------------------------------------ #
# Server runner
# ------------------------------------------------------------------ #


def run_server(host: str = "0.0.0.0", port: int = 8420) -> None:
    """Run the terminal dashboard server."""
    import uvicorn

    logger.info("Starting PPMT Terminal Dashboard on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


# ------------------------------------------------------------------ #
# Fallback HTML (minimal, used only if static/index.html is missing)
# ------------------------------------------------------------------ #
_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PPMT Terminal</title>
<style>
body{background:#0d1117;color:#c9d1d9;font-family:monospace;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
h1{color:#58a6ff}
</style>
</head>
<body><h1>PPMT Terminal — Dashboard file not found</h1></body>
</html>"""
