"""
PPMT Dashboard Server - Entry point for `ppmt dashboard` command.

Starts the Flask development server with the PPMT dashboard.
"""

from __future__ import annotations

import os
import webbrowser
import threading
from typing import Optional

from ppmt.dashboard.app import create_app


def start_dashboard(
    port: int = 5000,
    host: str = "127.0.0.1",
    backtest_dir: Optional[str] = None,
    open_browser: bool = True,
) -> None:
    """
    Start the PPMT dashboard server.

    Args:
        port: Port to run the server on (default: 5000)
        host: Host to bind to (default: 127.0.0.1)
        backtest_dir: Override backtest results directory
        open_browser: Whether to open the browser automatically
    """
    app = create_app(backtest_dir=backtest_dir)

    # Ensure backtest results directory exists
    bt_dir = backtest_dir or os.path.join(os.path.expanduser("~/.ppmt"), "backtest_results")
    os.makedirs(bt_dir, exist_ok=True)

    if open_browser:
        # Open browser in a separate thread after a short delay
        def _open():
            import time
            time.sleep(1.0)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()

    print(f"\n  PPMT Dashboard starting on http://{host}:{port}")
    print(f"  Backtest results: {bt_dir}")
    print(f"  Press Ctrl+C to stop\n")

    app.run(host=host, port=port, debug=False)
