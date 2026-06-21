"""
PPMT Executor Interface — Abstract contract for all trading executors.

v0.44.0: ENTREGABLE 4 — Unified IExecutor that both PaperExecutor
and MexcFuturesExecutor must implement. This guarantees the PPMT
engine can swap between paper and live execution with zero code changes.

Design decisions:
  - All methods are async (even PaperExecutor) so the caller doesn't
    need to know whether the underlying operation is I/O-bound or not.
  - PositionState is the shared return type — no executor-specific
    wrapper types. Exchange-specific data lives in exchange_meta.
  - open_position receives a generic metadata dict for SL/TP/expected_move
    rather than engine-specific types, keeping the interface decoupled
    from the PPMT engine internals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ppmt.execution.models import PositionState


class IExecutor(ABC):
    """
    Abstract executor interface for PPMT trading.

    All executors (paper, live, test) must implement these four methods.
    The PPMT engine calls only these methods to manage positions — it
    never touches exchange APIs directly.
    """

    @abstractmethod
    async def open_position(
        self,
        symbol: str,
        direction: str,
        size_usdt: float,
        metadata: dict,
    ) -> PositionState:
        """
        Open a new position.

        Args:
            symbol: Trading pair in PPMT format, e.g. "DOGE/USDT".
            direction: "LONG" or "SHORT".
            size_usdt: Position size in USDT.
            metadata: Executor-specific parameters. Expected keys:
                - entry_price (float): Current market price.
                - expected_move_pct (float): Expected % move from signal.
                - sl_price (float, optional): Initial stop-loss price.
                - tp_price (float, optional): Initial take-profit price.
                - predicted_path_symbols (list[str], optional): Walk-Forward path.
                - leverage (int, optional): Leverage for futures (default 20).

        Returns:
            PositionState with status="ACTIVE" and all SL/TP set.

        Raises:
            RuntimeError: If already in a position for this symbol.
            ConnectionError: If the exchange is unreachable (live only).
        """
        ...

    @abstractmethod
    async def update_position(
        self,
        position: PositionState,
        new_sl: Optional[float] = None,
        new_tp: Optional[float] = None,
    ) -> bool:
        """
        Update an open position's SL and/or TP.

        For live executors this means: cancel the old conditional order
        and place a new one. For paper executors this is a simple
        field update.

        Args:
            position: The position to update.
            new_sl: New stop-loss price (None = keep current).
            new_tp: New take-profit price (None = keep current).

        Returns:
            True if the update was applied successfully, False otherwise.
            Returns False (not raises) if the old order was already filled
            or cancellation failed — the caller should check position status.
        """
        ...

    @abstractmethod
    async def close_position(
        self,
        position: PositionState,
        reason: str,
    ) -> PositionState:
        """
        Close a specific position.

        For live executors: cancel pending SL/TP orders, then close
        the position via market order or close-position endpoint.

        Args:
            position: The position to close.
            reason: Close reason string (e.g. "CLOSED_BY_SL",
                "CLOSED_KILL_SWITCH").

        Returns:
            Updated PositionState with close_price, pnl, etc.

        Raises:
            RuntimeError: If the position cannot be closed.
        """
        ...

    @abstractmethod
    async def close_all_positions(self) -> bool:
        """
        Emergency close — kill switch.

        Closes ALL open positions immediately. Used when the user
        hits the Kill Switch or when the Portfolio Governor triggers
        a circuit breaker.

        Returns:
            True if all positions were closed, False if any failed.
        """
        ...
