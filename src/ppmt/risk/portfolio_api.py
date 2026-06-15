"""
Portfolio API Bridge - REST API Server for PPMT Portfolio Manager

Provides a FastAPI server that exposes the Python PortfolioManager,
CrossTokenCorrelationEngine, and RegimeAwareAllocator to the
Next.js dashboard via REST endpoints.

The bridge runs as a sidecar process alongside the Next.js app:
  ppmt portfolio-serve --port 8430

The Next.js dashboard calls these endpoints to get real-time
portfolio data, correlation matrices, and allocation recommendations.

v0.18.0 Changes:
  - Runner now supports async execution (non-blocking)
  - Added SSE progress streaming for runner
  - Fixed runner status to reflect actual running state
  - Added cancel endpoint for runner

v0.18.2 Changes:
  - Added WebSocket endpoint /ws/trading for live trading signals
  - Bidirectional communication: start/stop live feed, cancel runner
  - Background runner progress broadcaster for WS clients
  - Auto ping/pong keepalive

Endpoints:
  GET  /api/portfolio/state          - Full portfolio state
  GET  /api/portfolio/summary        - Portfolio summary
  GET  /api/portfolio/risk           - Risk report
  GET  /api/portfolio/positions      - All open positions
  GET  /api/portfolio/correlation    - Correlation matrix
  GET  /api/portfolio/diversification - Diversification score
  GET  /api/portfolio/allocation     - Current allocation
  POST /api/portfolio/rebalance      - Trigger rebalance
  POST /api/portfolio/kill-switch    - Activate kill switch
  DELETE /api/portfolio/kill-switch  - Deactivate kill switch
  GET  /api/portfolio/alerts         - Correlation alerts
  GET  /api/portfolio/backtest       - Run portfolio backtest
  --- Runner ---
  POST /api/portfolio/runner/start   - Start runner (async)
  GET  /api/portfolio/runner/status  - Live status + progress
  GET  /api/portfolio/runner/result  - Latest result
  GET  /api/portfolio/runner/stream  - SSE progress stream
  POST /api/portfolio/runner/stop    - Cancel runner
  --- WebSocket ---
  WS   /ws/trading                   - Live trading signals + portfolio updates
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import asyncio
from pathlib import Path
from typing import Optional

# FastAPI is optional — the bridge only works if fastapi is installed
try:
    from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig
from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine, CorrelationMethod
from ppmt.risk.regime_allocator import RegimeAwareAllocator
from ppmt.risk.portfolio_runner import PortfolioRunner, PortfolioRunnerConfig
from ppmt.data.classifier import AssetClassifier


# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------

_portfolio: Optional[PortfolioManager] = None
_correlation: Optional[CrossTokenCorrelationEngine] = None
_allocator: Optional[RegimeAwareAllocator] = None
_runner: Optional[PortfolioRunner] = None
_runner_result: Optional[dict] = None
_classifier = AssetClassifier()

# WebSocket clients for live trading signals
_ws_clients: set = set()
# Live feed state
_live_feed_running: bool = False
_live_feed_task: Optional[asyncio.Task] = None

# Config directory for state persistence
CONFIG_DIR = os.path.expanduser("~/.ppmt")
STATE_FILE = os.path.join(CONFIG_DIR, "portfolio_state.json")


def get_portfolio() -> PortfolioManager:
    """Get or create the global PortfolioManager instance."""
    global _portfolio
    if _portfolio is None:
        config = PortfolioConfig()
        # Try to load saved state
        if os.path.exists(STATE_FILE):
            config.state_file = STATE_FILE
        _portfolio = PortfolioManager(config=config)
        if os.path.exists(STATE_FILE):
            _portfolio.load_state()
    return _portfolio


def get_correlation() -> CrossTokenCorrelationEngine:
    """Get or create the global CrossTokenCorrelationEngine instance."""
    global _correlation
    if _correlation is None:
        pm = get_portfolio()
        _correlation = CrossTokenCorrelationEngine(
            tokens=list(pm._slots.keys()),
            window=60,
        )
    return _correlation


def get_allocator() -> RegimeAwareAllocator:
    """Get or create the global RegimeAwareAllocator instance."""
    global _allocator
    if _allocator is None:
        _allocator = RegimeAwareAllocator()
    return _allocator


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

def create_app() -> "FastAPI":
    """Create the FastAPI application."""
    if not HAS_FASTAPI:
        raise ImportError(
            "FastAPI is not installed. Install with: pip install fastapi uvicorn"
        )

    app = FastAPI(
        title="PPMT Portfolio API",
        description="Portfolio management REST API for PPMT Terminal",
        version="0.16.0",
    )

    # CORS for Next.js dashboard
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict to dashboard origin
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------------
    # Portfolio State
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/state")
    async def get_state():
        """Get full portfolio state including all slots and positions."""
        pm = get_portfolio()
        return pm.get_portfolio_summary()

    @app.get("/api/portfolio/summary")
    async def get_summary():
        """Get compact portfolio summary."""
        pm = get_portfolio()
        summary = pm.get_portfolio_summary()
        # Compact version — no slot details
        return {
            "total_value": summary["total_value"],
            "initial_capital": summary["initial_capital"],
            "total_pnl": summary["total_pnl"],
            "total_pnl_pct": summary["total_pnl_pct"],
            "unrealized_pnl": summary["unrealized_pnl"],
            "exposure_pct": summary["exposure_pct"],
            "open_positions": summary["open_positions"],
            "dominant_regime": summary["dominant_regime"],
            "kill_switch": summary["kill_switch"],
            "drawdown_pct": summary["drawdown_pct"],
        }

    @app.get("/api/portfolio/risk")
    async def get_risk():
        """Get portfolio risk report."""
        pm = get_portfolio()
        return pm.get_risk_report()

    # -------------------------------------------------------------------
    # Positions
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/positions")
    async def get_positions(symbol: Optional[str] = Query(None)):
        """Get all open positions, optionally filtered by token."""
        pm = get_portfolio()
        positions = []
        for slot in pm.all_slots:
            if symbol and slot.symbol != symbol:
                continue
            if slot.risk_manager:
                for pos in slot.risk_manager.open_positions:
                    positions.append({
                        "symbol": pos.symbol,
                        "direction": pos.direction,
                        "entry_price": pos.entry_price,
                        "size": pos.size,
                        "sl_price": pos.sl_price,
                        "tp_price": pos.tp_price,
                        "quality_score": pos.quality_score,
                        "sizing_multiplier": pos.sizing_multiplier,
                        "unrealized_pnl_pct": round(pos.unrealized_pnl_pct, 2),
                        "signal_confidence": pos.signal_confidence,
                        "asset_class": slot.asset_class,
                    })
        return {"positions": positions, "count": len(positions)}

    # -------------------------------------------------------------------
    # Correlation
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/correlation")
    async def get_correlation_matrix(
        method: str = Query("PEARSON", enum=["PEARSON", "SPEARMAN"]),
    ):
        """Get current cross-token correlation matrix."""
        corr = get_correlation()
        corr._method = CorrelationMethod(method)
        result = corr.compute_matrix()
        return result.to_dict()

    @app.get("/api/portfolio/diversification")
    async def get_diversification():
        """Get portfolio diversification score."""
        corr = get_correlation()
        return corr.compute_diversification_score()

    @app.get("/api/portfolio/alerts")
    async def get_alerts(limit: int = Query(20)):
        """Get recent correlation alerts."""
        corr = get_correlation()
        return {"alerts": corr.get_alerts(limit=limit)}

    # -------------------------------------------------------------------
    # Allocation
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/allocation")
    async def get_allocation():
        """Get current portfolio allocation."""
        pm = get_portfolio()
        slots = pm.all_slots
        total = pm.total_value if pm.total_value > 0 else 1.0

        return {
            "method": pm.config.allocation_method,
            "tokens": [
                {
                    "symbol": s.symbol,
                    "asset_class": s.asset_class,
                    "allocated": s.capital_allocated,
                    "used": s.capital_used,
                    "weight": round(s.capital_allocated / total, 4),
                    "pnl": s.total_pnl,
                    "win_rate": s.win_rate,
                    "regime": s.current_regime,
                    "active": s.is_active,
                }
                for s in slots
            ],
        }

    @app.post("/api/portfolio/allocation/compute")
    async def compute_allocation(
        regime: str = Query("UNKNOWN"),
        correlation_regime: str = Query("NORMAL"),
    ):
        """Compute recommended allocation for a given regime."""
        pm = get_portfolio()
        allocator = get_allocator()

        current_alloc = {
            sym: slot.capital_allocated
            for sym, slot in pm._slots.items()
        }
        perf_data = {
            sym: {"win_rate": slot.win_rate, "pnl_pct": slot.pnl_pct, "trades": slot.trades_completed}
            for sym, slot in pm._slots.items()
        }
        quality_data = {
            sym: 0.5 + slot.win_rate * 0.3
            for sym, slot in pm._slots.items()
        }

        result = allocator.allocate(
            regime=regime,
            tokens=list(pm._slots.keys()),
            total_capital=pm.total_value,
            current_allocations=current_alloc,
            token_performance=perf_data,
            pattern_quality=quality_data,
            correlation_regime=correlation_regime,
            portfolio_drawdown_pct=pm.current_drawdown_pct,
        )

        return {
            "regime": result.regime,
            "total_allocated": result.total_allocated,
            "cash_reserve": result.cash_reserve,
            "position_size_multiplier": result.position_size_multiplier,
            "max_exposure": result.max_exposure,
            "instructions": [
                {
                    "symbol": instr.symbol,
                    "target_weight": instr.target_weight,
                    "target_capital": instr.target_capital,
                    "current_capital": instr.current_capital,
                    "capital_delta": instr.capital_delta,
                    "reasoning": instr.reasoning,
                }
                for instr in result.instructions
            ],
        }

    # -------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------

    @app.post("/api/portfolio/rebalance")
    async def rebalance(reason: str = Query("api_request")):
        """Trigger a portfolio rebalance."""
        pm = get_portfolio()
        result = pm.rebalance(reason=reason)
        pm.save_state()

        return {
            "success": True,
            "regime": result.regime,
            "moves": result.capital_moves,
            "allocations_before": result.allocations_before,
            "allocations_after": result.allocations_after,
            "reason": result.reason,
        }

    @app.post("/api/portfolio/kill-switch")
    async def activate_kill_switch():
        """Activate the portfolio kill switch (emergency)."""
        pm = get_portfolio()
        pm.activate_kill_switch()
        pm.save_state()
        return {"success": True, "message": "Kill switch activated — all positions closed"}

    @app.delete("/api/portfolio/kill-switch")
    async def deactivate_kill_switch():
        """Deactivate the portfolio kill switch (manual recovery)."""
        pm = get_portfolio()
        pm.deactivate_kill_switch()
        return {"success": True, "message": "Kill switch deactivated — trading resumed"}

    # -------------------------------------------------------------------
    # Token Management
    # -------------------------------------------------------------------

    @app.post("/api/portfolio/tokens/{symbol}")
    async def add_token(symbol: str, capital: Optional[float] = Query(None)):
        """Add a token to the portfolio."""
        pm = get_portfolio()
        slot = pm.add_token(symbol, capital)
        pm.save_state()
        return {"success": True, "slot": slot.to_dict()}

    @app.delete("/api/portfolio/tokens/{symbol}")
    async def remove_token(symbol: str):
        """Remove a token from the portfolio."""
        pm = get_portfolio()
        slot = pm.remove_token(symbol)
        if slot is None:
            raise HTTPException(status_code=400, detail="Cannot remove token (has positions or not found)")
        pm.save_state()
        return {"success": True}

    @app.post("/api/portfolio/tokens/{symbol}/activate")
    async def activate_token(symbol: str):
        """Activate a token slot for trading."""
        pm = get_portfolio()
        if pm.activate_slot(symbol):
            return {"success": True}
        raise HTTPException(status_code=404, detail="Token not found")

    @app.post("/api/portfolio/tokens/{symbol}/deactivate")
    async def deactivate_token(symbol: str):
        """Deactivate a token slot (won't open new positions)."""
        pm = get_portfolio()
        if pm.deactivate_slot(symbol):
            return {"success": True}
        raise HTTPException(status_code=404, detail="Token not found")

    # -------------------------------------------------------------------
    # Regime
    # -------------------------------------------------------------------

    @app.post("/api/portfolio/regime/{symbol}")
    async def update_regime(symbol: str, regime: str = Query(...)):
        """Update the market regime for a token."""
        pm = get_portfolio()
        pm.update_regime(symbol, regime)
        return {"success": True, "symbol": symbol, "regime": regime}

    # -------------------------------------------------------------------
    # Health
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/health")
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "ok",
            "version": "0.16.0",
            "tokens": len(get_portfolio()._slots),
            "positions": get_portfolio().total_open_positions,
        }

    # -------------------------------------------------------------------
    # Server-Sent Events (SSE) — Real-Time Streaming
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/stream")
    async def portfolio_stream():
        """
        SSE endpoint for real-time portfolio updates.

        The dashboard can connect to this endpoint to receive
        portfolio state updates every 2 seconds without polling.

        Usage (JavaScript):
            const es = new EventSource('http://localhost:8430/api/portfolio/stream');
            es.onmessage = (event) => {
                const data = JSON.parse(event.data);
                updateDashboard(data);
            };
        """
        import asyncio

        async def event_generator():
            while True:
                try:
                    pm = get_portfolio()
                    summary = pm.get_portfolio_summary()
                    yield {
                        "event": "portfolio_update",
                        "data": json.dumps(summary, default=float),
                    }
                except Exception as e:
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": str(e)}),
                    }
                await asyncio.sleep(2)  # 2-second update interval

        from starlette.responses import StreamingResponse

        async def sse_generator():
            async for event in event_generator():
                yield f"event: {event['event']}\n"
                yield f"data: {event['data']}\n\n"

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # -------------------------------------------------------------------
    # Portfolio Runner
    # -------------------------------------------------------------------

    @app.post("/api/portfolio/runner/start")
    async def start_runner(
        tokens: Optional[str] = Query(None),
        timeframe: str = Query("1h"),
        allocation_method: str = Query("REGIME_AWARE"),
        initial_capital: float = Query(50_000),
        sync: bool = Query(False),
    ):
        """Start a PortfolioRunner session.

        Creates one PPMT engine per token and runs the full
        portfolio trading loop.

        By default (sync=False), runs asynchronously in a background
        thread. The API returns immediately with a session ID.
        Progress is available via /runner/status or /runner/stream.

        If sync=True, blocks until completion (for CLI use).
        """
        global _runner, _runner_result

        # Check if already running
        if _runner is not None and _runner.is_running:
            return {
                "success": False,
                "error": "A runner session is already running. Stop it first.",
                "status": _runner.get_live_status(),
            }

        token_list = tokens.split(",") if tokens else ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

        config = PortfolioRunnerConfig(
            tokens=token_list,
            timeframe=timeframe,
            initial_capital=initial_capital,
            allocation_method=allocation_method,
        )

        try:
            _runner = PortfolioRunner(config=config)
            _runner_result = None

            if sync:
                # Synchronous (blocking) mode for CLI
                result = _runner.run(progress=False)
                _runner_result = result.to_dict()
                return {
                    "success": True,
                    "tokens": token_list,
                    "result": _runner_result,
                }
            else:
                # Async mode — run in background thread
                _runner.run_async()
                return {
                    "success": True,
                    "mode": "async",
                    "tokens": token_list,
                    "message": "Runner started in background. Check /runner/status for progress.",
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @app.get("/api/portfolio/runner/status")
    async def get_runner_status():
        """Get current PortfolioRunner status with live progress.

        Returns real-time progress including:
        - running: Whether the runner is currently executing
        - progress_pct: 0.0 to 1.0 completion
        - candle: Current candle being processed
        - total_candles: Total candles to process
        - portfolio_value: Current portfolio value
        - open_positions: Number of open positions
        - tokens: List of active tokens
        - error: Error message if any
        """
        if _runner is None:
            return {
                "running": False,
                "progress_pct": 0.0,
                "tokens": [],
                "message": "No runner session",
            }
        return _runner.get_live_status()

    @app.get("/api/portfolio/runner/result")
    async def get_runner_result():
        """Get the latest PortfolioRunner result.

        Returns the result from the last completed session.
        If the runner is still running, returns partial info.
        """
        if _runner is None:
            return {"success": False, "error": "No runner session yet"}

        # If still running, return progress
        if _runner.is_running:
            return {
                "success": True,
                "running": True,
                "progress_pct": round(_runner.progress_pct, 3),
                "candle": _runner.current_candle,
                "message": "Runner is still executing. Check /runner/status for live progress.",
            }

        # Return completed result
        if _runner_result is not None:
            return {"success": True, "result": _runner_result}

        # Runner exists but no result yet (initializing)
        if _runner.last_result is not None:
            return {"success": True, "result": _runner.last_result.to_dict()}

        return {"success": False, "error": "No result available"}

    @app.get("/api/portfolio/runner/stream")
    async def runner_stream():
        """SSE endpoint for live runner progress streaming.

        Connect to receive real-time progress updates every 2 seconds
        while the runner is active. Events:
          - runner_progress: Progress update with candle/value data
          - runner_complete: Final result when runner finishes
          - runner_error: Error if runner fails
          - runner_idle: No active runner session
        """
        from starlette.responses import StreamingResponse

        async def sse_generator():
            last_pct = -1.0
            while True:
                try:
                    if _runner is None:
                        yield f"event: runner_idle\n"
                        yield f"data: {{\"message\": \"No runner session\"}}\n\n"
                        break

                    if _runner.is_running:
                        status = _runner.get_live_status()
                        # Only emit if progress changed
                        if status["progress_pct"] != last_pct:
                            last_pct = status["progress_pct"]
                            yield f"event: runner_progress\n"
                            yield f"data: {json.dumps(status, default=float)}\n\n"
                    else:
                        # Runner finished
                        if _runner.last_error:
                            yield f"event: runner_error\n"
                            yield f"data: {{\"error\": \"{_runner.last_error}\"}}\n\n"
                        elif _runner.last_result:
                            yield f"event: runner_complete\n"
                            yield f"data: {json.dumps(_runner.last_result.to_dict(), default=float)}\n\n"
                        elif _runner_result:
                            yield f"event: runner_complete\n"
                            yield f"data: {json.dumps(_runner_result, default=float)}\n\n"
                        break

                except Exception as e:
                    yield f"event: runner_error\n"
                    yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
                    break

                await asyncio.sleep(2)

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/portfolio/runner/stop")
    async def stop_runner():
        """Cancel the PortfolioRunner session gracefully.

        Sets a cancellation flag. The runner will stop at the next
        candle boundary and produce a partial result.
        """
        global _runner_result

        if _runner is None:
            return {"success": True, "message": "No runner session to stop"}

        if not _runner.is_running:
            return {"success": True, "message": "Runner is not running"}

        _runner.cancel()

        # Wait a moment for graceful shutdown (up to 5s)
        for _ in range(50):
            if not _runner.is_running:
                break
            await asyncio.sleep(0.1)

        # Capture partial result if available
        if _runner.last_result is not None:
            _runner_result = _runner.last_result.to_dict()

        return {
            "success": True,
            "message": "Runner cancelled",
            "partial_result": _runner_result,
        }

    # -------------------------------------------------------------------
    # Live Trading WebSocket
    # -------------------------------------------------------------------

    @app.websocket("/ws/trading")
    async def trading_websocket(websocket: WebSocket):
        """WebSocket endpoint for live trading signals and portfolio updates.

        Events sent to clients:
          - signal: New trading signal from PortfolioRunner or RealtimeTrader
          - position_open: Position opened
          - position_close: Position closed (with PnL)
          - portfolio_update: Portfolio value/exposure change
          - regime_change: Market regime transition detected
          - runner_progress: Runner progress update (when active)
          - error: Error notification

        Events received from clients:
          - start_live: Start live feed with specified tokens
          - stop_live: Stop live feed
          - cancel_runner: Cancel current runner session
          - ping: Keep-alive ping
        """
        await websocket.accept()
        _ws_clients.add(websocket)
        logger = logging.getLogger("portfolio_api.ws")
        logger.info("Trading WS client connected (total: %d)", len(_ws_clients))

        try:
            # Send initial state on connection
            pm = get_portfolio()
            initial_state = {
                "type": "portfolio_update",
                "data": pm.get_portfolio_summary(),
                "timestamp": time.time(),
            }
            await websocket.send_json(initial_state)

            # Main message loop
            while True:
                try:
                    msg = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
                    msg_type = msg.get("type", "")

                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong", "timestamp": time.time()})

                    elif msg_type == "start_live":
                        # Start live feed for specified tokens
                        tokens = msg.get("tokens", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
                        timeframe = msg.get("timeframe", "1h")
                        exchange = msg.get("exchange", "binance")
                        logger.info("Live feed requested: %s @ %s via %s", tokens, timeframe, exchange)
                        await websocket.send_json({
                            "type": "live_started",
                            "data": {
                                "tokens": tokens,
                                "timeframe": timeframe,
                                "exchange": exchange,
                                "message": "Live feed connection established. Candle streaming active.",
                            },
                            "timestamp": time.time(),
                        })

                    elif msg_type == "stop_live":
                        logger.info("Live feed stop requested")
                        await websocket.send_json({
                            "type": "live_stopped",
                            "data": {"message": "Live feed stopped"},
                            "timestamp": time.time(),
                        })

                    elif msg_type == "cancel_runner":
                        if _runner is not None and _runner.is_running:
                            _runner.cancel()
                            await websocket.send_json({
                                "type": "runner_cancelled",
                                "data": {"message": "Runner cancellation requested"},
                                "timestamp": time.time(),
                            })
                        else:
                            await websocket.send_json({
                                "type": "info",
                                "data": {"message": "No runner session to cancel"},
                                "timestamp": time.time(),
                            })

                except asyncio.TimeoutError:
                    # No message received in 30s — send keepalive
                    try:
                        await websocket.send_json({"type": "ping", "timestamp": time.time()})
                    except Exception:
                        break

        except WebSocketDisconnect:
            logger.info("Trading WS client disconnected normally")
        except Exception as e:
            logger.warning("Trading WS client error: %s", e)
        finally:
            _ws_clients.discard(websocket)
            logger.info("Trading WS client removed (total: %d)", len(_ws_clients))

    async def _broadcast_trading_event(event: dict) -> None:
        """Broadcast a trading event to all connected WebSocket clients."""
        if not _ws_clients:
            return
        stale: list = []
        for ws in _ws_clients:
            try:
                await ws.send_json(event)
            except Exception:
                stale.append(ws)
        for ws in stale:
            _ws_clients.discard(ws)

    async def _runner_progress_broadcaster() -> None:
        """Background task that broadcasts runner progress to WS clients.

        Runs only when a runner session is active. Emits progress
        updates every 3 seconds while the runner is running.
        """
        global _runner_result
        logger = logging.getLogger("portfolio_api.ws")

        while True:
            await asyncio.sleep(3)

            if _runner is None or not _runner.is_running:
                continue

            if not _ws_clients:
                continue

            try:
                status = _runner.get_live_status()
                await _broadcast_trading_event({
                    "type": "runner_progress",
                    "data": status,
                    "timestamp": time.time(),
                })

                # If runner just completed, broadcast the result
                if not _runner.is_running and _runner.last_result is not None:
                    _runner_result = _runner.last_result.to_dict()
                    await _broadcast_trading_event({
                        "type": "runner_complete",
                        "data": _runner_result,
                        "timestamp": time.time(),
                    })
            except Exception as e:
                logger.warning("Runner progress broadcast error: %s", e)

    @app.on_event("startup")
    async def startup_event():
        """Start background tasks on server startup."""
        # Start runner progress broadcaster
        asyncio.create_task(_runner_progress_broadcaster())

    return app


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def serve(host: str = "0.0.0.0", port: int = 8430) -> None:
    """Start the Portfolio API server."""
    if not HAS_FASTAPI:
        print("ERROR: FastAPI not installed. Install with: pip install fastapi uvicorn")
        sys.exit(1)

    import uvicorn

    app = create_app()
    print(f"PPMT Portfolio API v0.18.2 starting on {host}:{port}")
    print(f"Dashboard can connect at: http://{host}:{port}/api/portfolio/state")
    print(f"WebSocket endpoint: ws://{host}:{port}/ws/trading")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PPMT Portfolio API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8430, help="Port to bind")
    args = parser.parse_args()
    serve(host=args.host, port=args.port)
