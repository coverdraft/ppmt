"""
PaperExecutor — V2 Paper Trading Executor for the PPMT Terminal.

In-memory executor that manages a single position per token.
Mirrors the PositionState TypeScript interface exactly.

Rules (from V2 spec):
  - SL Initial   = Entry - (expected_move * 1.2)
  - TP Initial   = Entry + (expected_move * 2.5)
  - Catastrophic SL = Entry - (expected_move * 3.0) — NEVER moves
  - Walk-Forward: Match expected_sequence[index]
      → Move SL to break-even on first match
      → Extend TP if new expected_move > remaining margin
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
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
    """Exact mirror of the TypeScript PositionState interface."""

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

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict matching the TS interface."""
        return asdict(self)


class PaperExecutor:
    """
    In-memory paper trading executor for a single token.

    Usage:
        executor = PaperExecutor(capital_usdt=100.0)
        executor.open_position(signal, current_price)
        executor.check_walk_forward(matched_symbol)
        executor.check_price(current_price)  # SL/TP hit?
    """

    def __init__(self, capital_usdt: float = 100.0):
        self.capital_usdt = capital_usdt
        self._position: Optional[PositionState] = None

    @property
    def position(self) -> Optional[PositionState]:
        return self._position

    @property
    def is_in_position(self) -> bool:
        return self._position is not None and self._position.status in (
            "ACTIVE", "BREAK_EVEN_SECURED", "TP_EXTENDED"
        )

    def open_position(
        self,
        symbol: str,
        direction: Direction,
        entry_price: float,
        expected_move_pct: float,
        predicted_path_symbols: list[str] | None = None,
        size_usdt: float | None = None,
    ) -> PositionState:
        """
        Open a new paper position.

        Args:
            symbol: e.g. "DOGE/USDT"
            direction: "LONG" or "SHORT"
            entry_price: Current price at signal time
            expected_move_pct: Expected % move from signal metadata
            predicted_path_symbols: Predicted SAX path for Walk-Forward
            size_usdt: Position size (defaults to capital_usdt)
        """
        if self.is_in_position:
            raise RuntimeError(f"Already in position: {self._position}")

        # Compute move in absolute price terms
        expected_move = entry_price * (expected_move_pct / 100.0)

        if direction == "LONG":
            sl = entry_price - (expected_move * 1.2)
            tp = entry_price + (expected_move * 2.5)
            cat_sl = entry_price - (expected_move * 3.0)
        else:
            sl = entry_price + (expected_move * 1.2)
            tp = entry_price - (expected_move * 2.5)
            cat_sl = entry_price + (expected_move * 3.0)

        # Build expected_sequence from predicted path
        # Each step is a single-symbol list for compatibility with the TS interface
        if predicted_path_symbols:
            expected_sequence = [[s] for s in predicted_path_symbols]
        else:
            expected_sequence = []

        self._position = PositionState(
            symbol=symbol,
            direction=direction,
            status="ACTIVE",
            entry_price=entry_price,
            entry_time=datetime.now(timezone.utc).isoformat(),
            size_usdt=size_usdt or self.capital_usdt,
            current_sl=sl,
            current_tp=tp,
            catastrophic_sl=cat_sl,
            expected_sequence=expected_sequence,
            sequence_index=0,
        )

        return self._position

    def check_walk_forward(
        self,
        current_symbol: str | list[str],
        current_price: float,
    ) -> Optional[PositionState]:
        """
        Check if the current SAX symbol matches the expected sequence.

        Walk-Forward rules:
        1. First match → SL moves to break-even (entry_price)
        2. Subsequent matches → TP extends if expected_move grows
        3. Mismatch → no action (wait for next candle)
        """
        if not self.is_in_position or self._position is None:
            return None

        pos = self._position

        # Normalize symbol to list for comparison
        if isinstance(current_symbol, str):
            check_sym = [current_symbol]
        else:
            check_sym = current_symbol

        # Check if we have an expected sequence to match against
        if pos.sequence_index >= len(pos.expected_sequence):
            return None  # No more expected steps

        expected = pos.expected_sequence[pos.sequence_index]

        # Match check
        if check_sym == expected or (len(check_sym) == 1 and len(expected) == 1 and check_sym[0] == expected[0]):
            # Match found!
            pos.sequence_index += 1

            if pos.status == "ACTIVE":
                # First match → move SL to break-even
                pos.current_sl = pos.entry_price
                pos.status = "BREAK_EVEN_SECURED"

            elif pos.status in ("BREAK_EVEN_SECURED", "TP_EXTENDED"):
                # Subsequent match → extend TP
                move_pct = 0.5  # Default extension: 0.5% per matched step
                extension = pos.entry_price * (move_pct / 100.0)

                if pos.direction == "LONG":
                    new_tp = pos.current_tp + extension
                    if new_tp > pos.current_tp:
                        pos.current_tp = new_tp
                        pos.status = "TP_EXTENDED"
                else:
                    new_tp = pos.current_tp - extension
                    if new_tp < pos.current_tp:
                        pos.current_tp = new_tp
                        pos.status = "TP_EXTENDED"

            return pos

        return None  # No match

    def check_price(self, current_price: float) -> Optional[PositionState]:
        """
        Check if current price hits SL, TP, or catastrophic SL.

        Returns updated position if closed, None if still open.
        """
        if not self.is_in_position or self._position is None:
            return None

        pos = self._position

        if pos.direction == "LONG":
            # Catastrophic SL first (never moves)
            if current_price <= pos.catastrophic_sl:
                return self._close_position(current_price, "CLOSED_CATASTROPHIC")
            # Normal SL
            if current_price <= pos.current_sl:
                return self._close_position(current_price, "CLOSED_BY_SL")
            # TP
            if current_price >= pos.current_tp:
                return self._close_position(current_price, "CLOSED_BY_TP")

        else:  # SHORT
            if current_price >= pos.catastrophic_sl:
                return self._close_position(current_price, "CLOSED_CATASTROPHIC")
            if current_price >= pos.current_sl:
                return self._close_position(current_price, "CLOSED_BY_SL")
            if current_price <= pos.current_tp:
                return self._close_position(current_price, "CLOSED_BY_TP")

        return None

    def force_close(self, current_price: float, reason: str = "CLOSED_KILL_SWITCH") -> Optional[PositionState]:
        """Force close position (kill switch)."""
        if self._position is None:
            return None
        return self._close_position(current_price, reason)

    def _close_position(self, close_price: float, reason: PositionStatus) -> PositionState:
        """Close the position and compute P&L."""
        pos = self._position
        assert pos is not None

        pos.close_price = close_price
        pos.close_reason = reason
        pos.status = reason

        if pos.direction == "LONG":
            pos.pnl_pct = ((close_price - pos.entry_price) / pos.entry_price) * 100.0
        else:
            pos.pnl_pct = ((pos.entry_price - close_price) / pos.entry_price) * 100.0

        pos.pnl_usdt = pos.size_usdt * (pos.pnl_pct / 100.0)

        self._position = pos  # Keep for history, but is_in_position will be False
        return pos
