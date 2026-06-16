"""
PPMT Terminal Server — FastAPI + WebSocket web dashboard with Money Management.

Serves the real-time trading dashboard and provides both REST and WebSocket
endpoints for the front-end to consume state from :class:`TerminalState`.

v0.27.0: Complete dashboard with candlestick chart, entry/exit markers,
real-time MEXC data, paper trading, and backtesting with real data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from ppmt.terminal.state import TerminalState, get_terminal_state
from ppmt.data.storage import PPMTStorage
from ppmt.data.collector import DataCollector

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Static files path
# ------------------------------------------------------------------ #
_STATIC_DIR = Path(__file__).parent / "static"
_DASHBOARD_HTML_PATH = _STATIC_DIR / "index.html"

# ------------------------------------------------------------------ #
# Config directory for persistent state
# ------------------------------------------------------------------ #
CONFIG_DIR = os.path.expanduser("~/.ppmt")

# ------------------------------------------------------------------ #
# FastAPI application
# ------------------------------------------------------------------ #
app = FastAPI(title="PPMT Terminal", version="0.27.0")

# Global terminal state (shared with engine)
terminal_state: TerminalState = get_terminal_state()

# Connected WebSocket clients
_ws_clients: set[WebSocket] = set()

# Parent Node Manager (lazy-loaded)
_parent_manager = None


def _get_parent_manager():
    """Get or create the ParentNodeManager."""
    global _parent_manager
    if _parent_manager is None:
        from ppmt.risk.money_manager import ParentNodeManager, ChildNodeConfig
        state_file = os.path.join(CONFIG_DIR, "parent_node_state.json")
        import yaml

        # Default capital from terminal state
        capital = terminal_state.portfolio_value or 10_000.0
        _parent_manager = ParentNodeManager(total_capital=capital)

        # Load saved state
        if os.path.exists(state_file):
            try:
                with open(state_file) as f:
                    saved = yaml.safe_load(f) or {}
                _parent_manager.total_capital = saved.get("total_capital", capital)
                for child_data in saved.get("children", []):
                    cfg = ChildNodeConfig(**child_data)
                    _parent_manager.register_child(cfg)
                if _parent_manager._children:
                    _parent_manager.distribute_capital()
            except Exception as e:
                logger.warning(f"Failed to load parent node state: {e}")

    return _parent_manager


def _save_parent_manager():
    """Save ParentNodeManager state to disk."""
    import yaml
    pm = _get_parent_manager()
    state_file = os.path.join(CONFIG_DIR, "parent_node_state.json")
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        state = {
            "total_capital": pm.total_capital,
            "children": [
                {
                    "node_id": cfg.node_id,
                    "symbol": cfg.symbol,
                    "timeframe": cfg.timeframe,
                    "capital_allocation_pct": cfg.capital_allocation_pct,
                    "leverage": cfg.leverage,
                    "auto_mode": cfg.auto_mode,
                    "max_position_pct": cfg.max_position_pct,
                    "enabled": cfg.enabled,
                }
                for cfg in pm._children.values()
            ],
        }
        with open(state_file, "w") as f:
            yaml.dump(state, f, default_flow_style=False)
    except Exception as e:
        logger.warning(f"Failed to save parent node state: {e}")


# ------------------------------------------------------------------ #
# HTML serving
# ------------------------------------------------------------------ #


def _load_dashboard_html() -> str:
    """Load the dashboard HTML from the static directory."""
    if _DASHBOARD_HTML_PATH.exists():
        return _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    return _FALLBACK_HTML


@app.get("/")
async def dashboard() -> HTMLResponse:
    """Serve the dashboard HTML."""
    return HTMLResponse(content=_load_dashboard_html())


# ------------------------------------------------------------------ #
# REST endpoints — State
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
        "leverage": terminal_state.leverage,
        "auto_mode": terminal_state.auto_mode,
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
        "kill_switch_active": terminal_state.kill_switch_active,
        "exposure_pct": terminal_state.exposure_pct,
        "daily_return_pct": terminal_state.daily_return_pct,
        "max_drawdown_pct": terminal_state.max_drawdown_pct,
        "daily_loss_pct": terminal_state.daily_loss_pct,
    }


# ------------------------------------------------------------------ #
# REST endpoints — Money Management & Nodes (v0.25.0)
# ------------------------------------------------------------------ #


@app.get("/api/nodes")
async def get_nodes() -> dict:
    """Get all child nodes and parent state."""
    pm = _get_parent_manager()
    children = []
    for node_id, cfg in pm._children.items():
        state = pm._child_states.get(node_id)
        children.append({
            "node_id": cfg.node_id,
            "symbol": cfg.symbol,
            "timeframe": cfg.timeframe,
            "capital_allocation_pct": cfg.capital_allocation_pct,
            "leverage": cfg.leverage,
            "auto_mode": cfg.auto_mode,
            "max_position_pct": cfg.max_position_pct,
            "enabled": cfg.enabled,
            "allocated_capital": state.allocated_capital if state else 0,
            "available_capital": state.available_capital if state else 0,
            "realized_pnl": state.realized_pnl if state else 0,
            "unrealized_pnl": state.unrealized_pnl if state else 0,
            "open_positions": state.open_positions if state else 0,
            "total_trades": state.total_trades if state else 0,
            "winning_trades": state.winning_trades if state else 0,
        })

    return {
        "total_capital": pm.total_capital,
        "reserve_capital": pm.reserve_capital,
        "total_portfolio_value": pm.total_portfolio_value,
        "total_exposure_pct": pm.total_exposure_pct,
        "total_realized_pnl": pm.total_realized_pnl,
        "total_unrealized_pnl": pm.total_unrealized_pnl,
        "kill_switch_active": pm._global_kill_switch,
        "children": children,
    }


class AddNodeRequest(BaseModel):
    symbol: str
    timeframe: str = "1h"
    capital_allocation_pct: float = 0.20
    leverage: int = 1
    auto_mode: bool = True


@app.post("/api/nodes/add")
async def add_node(req: AddNodeRequest) -> dict:
    """Add a child node."""
    from ppmt.risk.money_manager import ChildNodeConfig
    pm = _get_parent_manager()
    node_id = f"{req.symbol.split('/')[0].lower()}_{req.timeframe}"

    if node_id in pm._children:
        return {"ok": False, "error": f"Node '{node_id}' already exists"}

    cfg = ChildNodeConfig(
        node_id=node_id,
        symbol=req.symbol,
        timeframe=req.timeframe,
        capital_allocation_pct=req.capital_allocation_pct,
        leverage=req.leverage,
        auto_mode=req.auto_mode,
    )

    try:
        pm.register_child(cfg)
        pm.distribute_capital()
        _save_parent_manager()
        return {"ok": True, "node_id": node_id, "capital": pm.get_child_capital(node_id)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class RemoveNodeRequest(BaseModel):
    node_id: str


@app.post("/api/nodes/remove")
async def remove_node(req: RemoveNodeRequest) -> dict:
    """Remove a child node."""
    pm = _get_parent_manager()
    try:
        pm.unregister_child(req.node_id)
        _save_parent_manager()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class SetLeverageRequest(BaseModel):
    node_id: str
    leverage: int


@app.post("/api/nodes/leverage")
async def set_leverage(req: SetLeverageRequest) -> dict:
    """Set leverage for a child node."""
    pm = _get_parent_manager()
    try:
        pm.set_child_leverage(req.node_id, req.leverage)
        _save_parent_manager()
        return {"ok": True, "node_id": req.node_id, "leverage": req.leverage}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class SetAutoModeRequest(BaseModel):
    node_id: str
    auto_mode: bool


@app.post("/api/nodes/auto-mode")
async def set_auto_mode(req: SetAutoModeRequest) -> dict:
    """Set auto/manual mode for a child node."""
    pm = _get_parent_manager()
    try:
        pm.set_child_auto_mode(req.node_id, req.auto_mode)
        _save_parent_manager()
        return {"ok": True, "node_id": req.node_id, "auto_mode": req.auto_mode}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class SetCapitalRequest(BaseModel):
    total_capital: float


@app.post("/api/nodes/capital")
async def set_capital(req: SetCapitalRequest) -> dict:
    """Set total parent capital."""
    pm = _get_parent_manager()
    pm.total_capital = req.total_capital
    pm.distribute_capital()
    _save_parent_manager()
    return {"ok": True, "total_capital": pm.total_capital}


@app.post("/api/nodes/kill-switch/activate")
async def activate_kill_switch() -> dict:
    """Activate the global kill switch."""
    pm = _get_parent_manager()
    pm.activate_global_kill_switch()
    terminal_state.kill_switch_active = True
    terminal_state.is_trading_allowed = False
    _save_parent_manager()
    return {"ok": True, "kill_switch": True}


@app.post("/api/nodes/kill-switch/deactivate")
async def deactivate_kill_switch() -> dict:
    """Deactivate the global kill switch."""
    pm = _get_parent_manager()
    pm.deactivate_global_kill_switch()
    terminal_state.kill_switch_active = False
    terminal_state.is_trading_allowed = True
    _save_parent_manager()
    return {"ok": True, "kill_switch": False}


class RedistributeRequest(BaseModel):
    allocations: dict  # node_id -> pct (as fraction 0-1)


@app.post("/api/nodes/redistribute")
async def redistribute_capital(req: RedistributeRequest) -> dict:
    """Redistribute capital among child nodes."""
    pm = _get_parent_manager()
    try:
        pm.redistribute_capital(req.allocations)
        _save_parent_manager()
        return {"ok": True, "allocations": req.allocations}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# REST endpoint — Run backtest via dashboard (v0.25.0)
# ------------------------------------------------------------------ #


class BacktestRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    capital: float = 10_000.0


@app.post("/api/backtest")
async def run_backtest(req: BacktestRequest) -> dict:
    """Run a quick backtest and return results."""
    try:
        from ppmt.engine.realtime import RealtimeTrader, ReplayConfig
        config = ReplayConfig(
            symbol=req.symbol,
            timeframe=req.timeframe,
            initial_capital=req.capital,
            speed=0,
            verbose=False,
        )
        trader = RealtimeTrader(config=config)
        result = trader.run_replay()

        trades = []
        for t in result.trades:
            trades.append({
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason,
                "regime": t.regime,
            })

        return {
            "ok": True,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "total_pnl_pct": result.total_pnl_pct,
            "max_drawdown": result.max_drawdown,
            "trades": trades,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# REST endpoints — Market Data & OHLCV for Chart (v0.27.0)
# ------------------------------------------------------------------ #


@app.get("/api/ohlcv")
async def get_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "1m",
    limit: int = 200,
    exchange: str = "mexc",
) -> dict:
    """Fetch real OHLCV data from exchange via ccxt for the candlestick chart."""
    try:
        import ccxt
        ex = getattr(ccxt, exchange, None)
        if ex is None:
            return {"ok": False, "error": f"Exchange '{exchange}' not found"}
        exc = ex()
        try:
            ohlcv = exc.fetch_ohlcv(symbol, timeframe, limit=min(limit, 1000))
            candles = []
            for c in ohlcv:
                candles.append({
                    "t": c[0],       # timestamp
                    "o": c[1],       # open
                    "h": c[2],       # high
                    "l": c[3],       # low
                    "c": c[4],       # close
                    "v": c[5],       # volume
                })
            return {"ok": True, "symbol": symbol, "timeframe": timeframe, "candles": candles}
        finally:
            if hasattr(exc, 'close'):
                exc.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/market/price")
async def get_market_price(
    symbol: str = "BTC/USDT",
    exchange: str = "mexc",
) -> dict:
    """Get current market price from exchange."""
    try:
        import ccxt
        ex = getattr(ccxt, exchange, None)
        if ex is None:
            return {"ok": False, "error": f"Exchange '{exchange}' not found"}
        exc = ex()
        try:
            ticker = exc.fetch_ticker(symbol)
            return {
                "ok": True,
                "symbol": symbol,
                "price": ticker.get("last", 0),
                "change_24h": ticker.get("percentage", 0),
                "high_24h": ticker.get("high", 0),
                "low_24h": ticker.get("low", 0),
                "volume_24h": ticker.get("quoteVolume", 0),
            }
        finally:
            if hasattr(exc, 'close'):
                exc.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/market/symbols")
async def get_market_symbols(exchange: str = "mexc") -> dict:
    """Get available trading symbols from exchange."""
    try:
        import ccxt
        ex = getattr(ccxt, exchange, None)
        if ex is None:
            return {"ok": False, "error": f"Exchange '{exchange}' not found"}
        exc = ex()
        try:
            markets = exc.load_markets()
            usdt_pairs = sorted([
                s for s in markets.keys()
                if s.endswith("/USDT") and markets[s].get("active", True)
            ])
            # Return top 100 by default
            return {"ok": True, "exchange": exchange, "symbols": usdt_pairs[:100]}
        finally:
            if hasattr(exc, 'close'):
                exc.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


class IngestRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1m"
    exchange: str = "mexc"
    days: int = 7


@app.post("/api/ingest")
async def ingest_data(req: IngestRequest) -> dict:
    """Download historical OHLCV data and store it in the PPMT database."""
    try:
        storage = PPMTStorage()
        collector = DataCollector(exchange=req.exchange, storage=storage)
        df = collector.fetch_historical(req.symbol, req.timeframe, days=req.days)
        if df is None or df.empty:
            return {"ok": False, "error": "No data fetched"}
        count = len(df)
        collector.close()
        storage.close()
        return {"ok": True, "symbol": req.symbol, "timeframe": req.timeframe, "candles": count}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
            # Add nodes data to snapshot
            try:
                pm = _get_parent_manager()
                snapshot["nodes"] = {
                    "total_capital": pm.total_capital,
                    "reserve_capital": pm.reserve_capital,
                    "kill_switch_active": pm._global_kill_switch,
                    "children": [
                        {
                            "node_id": cfg.node_id,
                            "symbol": cfg.symbol,
                            "timeframe": cfg.timeframe,
                            "capital_allocation_pct": cfg.capital_allocation_pct,
                            "leverage": cfg.leverage,
                            "auto_mode": cfg.auto_mode,
                            "enabled": cfg.enabled,
                        }
                        for cfg in pm._children.values()
                    ],
                }
            except Exception:
                snapshot["nodes"] = None

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
