"""
PPMT Terminal State — Shared state between trading engine and web dashboard.

This module provides a singleton-like state object that the trading engine updates
and the dashboard reads from. Thread-safe updates are handled via asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional


class TerminalState:
    """Shared state between PPMT engine and web dashboard.

    The trading engine calls ``update(**kwargs)`` to push new data, and the
    FastAPI dashboard reads via ``to_dict()`` or ``get_snapshot()``.

    All mutable list fields are bounded to avoid unbounded memory growth.
    """

    # ------------------------------------------------------------------ #
    # Connection status
    # ------------------------------------------------------------------ #
    is_running: bool = False
    mode: str = ""  # "live", "replay", "paper"
    started_at: float = 0.0

    # ------------------------------------------------------------------ #
    # Current market data
    # ------------------------------------------------------------------ #
    current_price: float = 0.0
    symbol: str = ""
    timeframe: str = ""
    exchange: str = ""

    # ------------------------------------------------------------------ #
    # Pattern / SAX state
    # ------------------------------------------------------------------ #
    pattern_buffer: list[str] = field(default_factory=list)
    entropy: float = 0.0
    regime: str = ""
    sax_symbols_produced: int = 0

    # ------------------------------------------------------------------ #
    # Signals
    # ------------------------------------------------------------------ #
    latest_signal: Optional[dict] = None
    signals_history: list[dict] = field(default_factory=list)  # last 50

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #
    positions: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Portfolio
    # ------------------------------------------------------------------ #
    portfolio_value: float = 0.0
    cash: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    exposure_pct: float = 0.0
    daily_return_pct: float = 0.0

    # ------------------------------------------------------------------ #
    # Risk
    # ------------------------------------------------------------------ #
    circuit_breakers: dict = field(default_factory=dict)
    is_trading_allowed: bool = True

    # ------------------------------------------------------------------ #
    # Performance
    # ------------------------------------------------------------------ #
    total_trades: int = 0
    winning_trades: int = 0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    equity_curve: list[float] = field(default_factory=list)  # last 200
    equity_timestamps: list[float] = field(default_factory=list)  # last 200

    # ------------------------------------------------------------------ #
    # Feed stats
    # ------------------------------------------------------------------ #
    candles_processed: int = 0
    websocket_status: str = "disconnected"
    reconnect_count: int = 0

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    # Keep lists bounded
    _MAX_SIGNALS: int = 50
    _MAX_EQUITY: int = 200
    _MAX_PATTERN: int = 30

    def __init__(self) -> None:
        # Re-initialise mutable defaults so instances don't share references
        self.pattern_buffer = []
        self.signals_history = []
        self.positions = []
        self.circuit_breakers = {}
        self.equity_curve = []
        self.equity_timestamps = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def update(self, **kwargs) -> None:
        """Thread-safe update of state fields.

        List fields are appended, not replaced.  Scalar fields are overwritten.
        Special keys:
            * ``signal`` — appended to ``signals_history``
            * ``equity_point`` — dict with ``value`` and ``timestamp``
            * ``pattern_symbol`` — appended to ``pattern_buffer``
        """
        async with self._lock:
            for key, value in kwargs.items():
                if key.startswith("_"):
                    continue

                # Special handling for list appends (checked BEFORE hasattr)
                if key == "signal":
                    self.signals_history.append(value)
                    if len(self.signals_history) > self._MAX_SIGNALS:
                        self.signals_history = self.signals_history[-self._MAX_SIGNALS:]
                    self.latest_signal = value
                    continue

                if key == "equity_point":
                    if isinstance(value, dict):
                        self.equity_curve.append(value.get("value", 0.0))
                        self.equity_timestamps.append(value.get("timestamp", time.time()))
                    else:
                        self.equity_curve.append(float(value))
                        self.equity_timestamps.append(time.time())
                    if len(self.equity_curve) > self._MAX_EQUITY:
                        self.equity_curve = self.equity_curve[-self._MAX_EQUITY:]
                        self.equity_timestamps = self.equity_timestamps[-self._MAX_EQUITY:]
                    continue

                if key == "pattern_symbol":
                    self.pattern_buffer.append(str(value))
                    if len(self.pattern_buffer) > self._MAX_PATTERN:
                        self.pattern_buffer = self.pattern_buffer[-self._MAX_PATTERN:]
                    continue

                # Default: direct attribute assignment
                if hasattr(self, key):
                    setattr(self, key, value)

    def update_sync(self, **kwargs) -> None:
        """Synchronous (non-async) convenience wrapper for update().

        Should only be called from contexts where no event loop is running,
        or from within an already-running loop via ``asyncio.create_task``.
        For safe usage from synchronous code that might be called inside or
        outside an event loop, we fall back to direct attribute assignment.
        """
        for key, value in kwargs.items():
            if key.startswith("_"):
                continue

            if key == "signal":
                self.signals_history.append(value)
                if len(self.signals_history) > self._MAX_SIGNALS:
                    self.signals_history = self.signals_history[-self._MAX_SIGNALS:]
                self.latest_signal = value
                continue

            if key == "equity_point":
                if isinstance(value, dict):
                    self.equity_curve.append(value.get("value", 0.0))
                    self.equity_timestamps.append(value.get("timestamp", time.time()))
                else:
                    self.equity_curve.append(float(value))
                    self.equity_timestamps.append(time.time())
                if len(self.equity_curve) > self._MAX_EQUITY:
                    self.equity_curve = self.equity_curve[-self._MAX_EQUITY:]
                    self.equity_timestamps = self.equity_timestamps[-self._MAX_EQUITY:]
                continue

            if key == "pattern_symbol":
                self.pattern_buffer.append(str(value))
                if len(self.pattern_buffer) > self._MAX_PATTERN:
                    self.pattern_buffer = self.pattern_buffer[-self._MAX_PATTERN:]
                continue

            if hasattr(self, key):
                setattr(self, key, value)

    def to_dict(self) -> dict:
        """Serialize the state for WebSocket broadcast."""
        return {
            "is_running": self.is_running,
            "mode": self.mode,
            "started_at": self.started_at,
            "current_price": self.current_price,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "exchange": self.exchange,
            "pattern_buffer": self.pattern_buffer,
            "entropy": self.entropy,
            "regime": self.regime,
            "sax_symbols_produced": self.sax_symbols_produced,
            "latest_signal": self.latest_signal,
            "signals_history": self.signals_history,
            "positions": self.positions,
            "portfolio_value": self.portfolio_value,
            "cash": self.cash,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "total_pnl_pct": self.total_pnl_pct,
            "exposure_pct": self.exposure_pct,
            "daily_return_pct": self.daily_return_pct,
            "circuit_breakers": self.circuit_breakers,
            "is_trading_allowed": self.is_trading_allowed,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "win_rate": self.win_rate,
            "max_drawdown": self.max_drawdown,
            "equity_curve": self.equity_curve,
            "equity_timestamps": self.equity_timestamps,
            "candles_processed": self.candles_processed,
            "websocket_status": self.websocket_status,
            "reconnect_count": self.reconnect_count,
        }

    def get_snapshot(self) -> dict:
        """Full state snapshot — alias for ``to_dict()`` with uptime."""
        data = self.to_dict()
        if self.started_at > 0:
            data["uptime_seconds"] = time.time() - self.started_at
        else:
            data["uptime_seconds"] = 0.0
        return data

    def reset(self) -> None:
        """Reset all state to defaults."""
        # Reset scalar fields
        self.is_running = False
        self.mode = ""
        self.started_at = 0.0
        self.current_price = 0.0
        self.symbol = ""
        self.timeframe = ""
        self.exchange = ""
        self.entropy = 0.0
        self.regime = ""
        self.sax_symbols_produced = 0
        self.latest_signal = None
        self.portfolio_value = 0.0
        self.cash = 0.0
        self.unrealized_pnl = 0.0
        self.realized_pnl = 0.0
        self.total_pnl_pct = 0.0
        self.exposure_pct = 0.0
        self.daily_return_pct = 0.0
        self.is_trading_allowed = True
        self.total_trades = 0
        self.winning_trades = 0
        self.win_rate = 0.0
        self.max_drawdown = 0.0
        self.candles_processed = 0
        self.websocket_status = "disconnected"
        self.reconnect_count = 0
        # Reset mutable containers
        self.pattern_buffer = []
        self.signals_history = []
        self.positions = []
        self.circuit_breakers = {}
        self.equity_curve = []
        self.equity_timestamps = []


# ------------------------------------------------------------------ #
# Module-level singleton for convenience
# ------------------------------------------------------------------ #
_global_state: Optional[TerminalState] = None


def get_terminal_state() -> TerminalState:
    """Return the global TerminalState singleton."""
    global _global_state
    if _global_state is None:
        _global_state = TerminalState()
    return _global_state
