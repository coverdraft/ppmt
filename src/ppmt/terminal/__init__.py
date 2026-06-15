"""
PPMT Terminal - Real-Time Trading Dashboard

Web-based dashboard for monitoring and controlling the PPMT trading engine.
Provides a FastAPI server with WebSocket and REST endpoints, serving a
self-contained dark-themed trading terminal UI.

Quick start::

    from ppmt.terminal import run_server, terminal_state

    # Update state from the engine
    terminal_state.update_sync(current_price=65432.10, symbol="BTC/USDT")

    # Start the dashboard server (blocks)
    run_server(port=8420)
"""

from ppmt.terminal.server import app, run_server, terminal_state

__all__ = ["app", "run_server", "terminal_state"]
