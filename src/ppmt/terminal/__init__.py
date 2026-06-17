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

# NOTE: Do NOT import `server` here eagerly. Doing so causes a double-import
# warning ("'ppmt.terminal.server' found in sys.modules after import of package
# 'ppmt.terminal'") when the user runs `python -m ppmt.terminal.server`.
# Lazy import via __getattr__ keeps `from ppmt.terminal import run_server` working.

__all__ = ["app", "run_server", "terminal_state"]


def __getattr__(name):
    if name in ("app", "run_server", "terminal_state"):
        from ppmt.terminal import server as _server
        return getattr(_server, name)
    raise AttributeError(f"module 'ppmt.terminal' has no attribute {name!r}")
