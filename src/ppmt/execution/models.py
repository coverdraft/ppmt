"""
PPMT Execution Models — Shared data structures for all executors.

v0.44.0: Extracted from terminal/paper_executor.py so that both
PaperExecutor and MexcFuturesExecutor share the same PositionState.
The paper_executor.py module now re-exports from here for backwards
compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Literal
from datetime import datetime, timezone


PositionStatus = Literal[
    "ACTIVE",
    "BREAK_EVEN_SECURED",
    "TP_EXTENDED",
    "CLOSED_BY_TP",
    "CLOSED_BY_SL",
    "CLOSED_BY_DIVERGENCE",
    "CLOSED_CATASTROPHIC",
    "CLOSED_KILL_SWITCH",
]

Direction = Literal["LONG", "SHORT"]


@dataclass
class PositionState:
    """
    Canonical position state shared by all executors.

    Fields are identical to the TypeScript PositionState interface
    in the V2 Terminal frontend. Both PaperExecutor and
    MexcFuturesExecutor produce instances of this class.

    Exchange-specific fields (order IDs, etc.) are stored in the
    ``exchange_meta`` dict so the core model stays clean.
    """

    symbol: str
    direction: Direction
    status: PositionStatus

    entry_price: float
    entry_time: str
    size_usdt: float

    current_sl: float          # Dynamic — moves with Walk-Forward
    current_tp: float          # Dynamic — extends with Walk-Forward
    catastrophic_sl: float     # Static — NEVER moves

    expected_sequence: list[list[str]]  # e.g. [['d','x'], ['e','y'], ['f','z']]
    sequence_index: int        # 0 at start, advances with each match

    close_price: Optional[float] = None
    close_reason: Optional[str] = None
    pnl_pct: Optional[float] = None
    pnl_usdt: Optional[float] = None

    # v0.44.0: Exchange-specific metadata (order IDs, fill prices, etc.)
    # PaperExecutor leaves this empty. MexcFuturesExecutor populates it.
    exchange_meta: Optional[dict] = None

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict matching the TS interface."""
        d = asdict(self)
        # exchange_meta is optional — omit if None for clean wire format
        if d.get("exchange_meta") is None:
            d.pop("exchange_meta", None)
        return d
