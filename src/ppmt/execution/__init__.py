"""
PPMT Execution Layer — Unified executor interface for paper and live trading.

v0.44.0: ENTREGABLE 4 — MEXC Futures Executor architecture.
Provides IExecutor abstract interface, shared PositionState model,
PaperExecutor (in-memory), and MexcFuturesExecutor (real money).
"""

from ppmt.execution.models import PositionState, PositionStatus, Direction
from ppmt.execution.interfaces import IExecutor

__all__ = [
    "IExecutor",
    "PositionState",
    "PositionStatus",
    "Direction",
]
