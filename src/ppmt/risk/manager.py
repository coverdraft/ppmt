"""
Capital Risk Manager - Adaptive Sizing via PPMT Metadata

The Risk Manager receives enriched signals from PPMT that include:
  - quality_score: How good is this pattern?
  - sizing_multiplier: How much to scale the position
  - predicted_path: What's expected ahead
  - win_rate, confidence, R:R: All from metadata

This is fundamentally different from traditional systems where the
risk manager is blind to signal quality. Here, PPMT's metadata
directly influences position sizing:

  High quality pattern (0.8+) → 2x base size
  Normal quality (0.6-0.8)    → 1x base size
  Low quality (0.4-0.6)       → 0.5x base size
  Very low (<0.4)             → 0.25x base size (or reject)

This creates a tight integration where better patterns get more
capital and weaker patterns get less — without arbitrary rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ppmt.engine.signal import Signal, SignalType


@dataclass
class Position:
    """An open trading position."""
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    sl_price: float
    tp_price: Optional[float]
    size: float  # Position size in base currency
    entry_time: float
    signal_confidence: float
    quality_score: float = 0.0
    """Quality score from the entry signal. Used for exit decisions."""
    sizing_multiplier: float = 1.0
    """Sizing multiplier used to enter this position."""
    expected_move_pct: float = 0.0
    """Expected move from entry signal metadata."""
    remaining_candles: int = 0
    """Predicted candles remaining from metadata."""
    unrealized_pnl_pct: float = 0.0


@dataclass
class RiskConfig:
    """Risk management configuration."""
    base_position_size_pct: float = 0.02  # 2% base risk per trade
    max_position_size_pct: float = 0.06   # 6% max (base × 3x multiplier)
    min_position_size_pct: float = 0.005  # 0.5% min (base × 0.25x)
    max_daily_loss_pct: float = 0.05      # 5% max daily loss
    max_drawdown_pct: float = 0.15        # 15% max portfolio drawdown
    min_risk_reward: float = 1.0          # Minimum R:R to accept signal (v0.14.1: lowered from 1.5)
    min_quality_score: float = 0.10       # Minimum quality to accept (v0.14.1: lowered from 0.3, quality_score = confidence × factor so 0.3 filtered out most signals)
    max_open_positions: int = 5           # Max simultaneous positions
    max_correlated_positions: int = 2     # Max positions in same asset class


class RiskManager:
    """
    Capital Risk Manager with Adaptive Sizing for PPMT.

    The key innovation: position sizing is NOT fixed. It's driven by
    the quality_score from Block Lifecycle Metadata, creating a
    feedback loop where:

    PPMT Metadata → quality_score → sizing_multiplier → position_size

    This means:
    - A pattern with 85% win rate and 3:1 R:R gets 2x the normal size
    - A pattern with 55% win rate and 1.5:1 R:R gets 0.5x the normal size
    - The Risk Manager still enforces hard limits (max daily loss, max drawdown)

    Usage:
        rm = RiskManager(capital=10000.0)
        signal = Signal(signal_type=SignalType.ENTRY_LONG, ...)

        if rm.can_open(signal):
            size = rm.calculate_position_size(signal)
            rm.open_position(signal, size)
    """

    def __init__(
        self,
        capital: float = 10000.0,
        config: Optional[RiskConfig] = None,
    ):
        self.initial_capital = capital
        self.capital = capital
        self.config = config or RiskConfig()

        self._positions: dict[str, Position] = {}
        self._daily_pnl: float = 0.0
        self._peak_capital: float = capital
        self._last_reset_day: Optional[int] = None

    @property
    def open_positions(self) -> list[Position]:
        return list(self._positions.values())

    @property
    def open_count(self) -> int:
        return len(self._positions)

    @property
    def current_drawdown(self) -> float:
        """Current drawdown from peak capital."""
        if self._peak_capital == 0:
            return 0.0
        return (self._peak_capital - self.capital) / self._peak_capital

    def can_open(self, signal: Signal, asset_class: str = "") -> tuple[bool, str]:
        """
        Check if a new position can be opened based on risk constraints.

        V3: Also checks quality_score from PPMT metadata.
        Low quality patterns are rejected outright.
        """
        # Must be an entry signal
        if not signal.is_entry:
            return False, "Not an entry signal"

        # Check minimum quality score (from metadata)
        if signal.quality_score < self.config.min_quality_score:
            return False, f"Quality too low: {signal.quality_score:.2f}"

        # Check minimum confidence
        # v0.6.2: Use min_risk_reward as proxy for confidence threshold flexibility.
        # The hardcoded 0.5 threshold was too strict for alpha=3 patterns where
        # Bayesian confidence maxes at ~0.47. Now using a configurable approach:
        # confidence must be >= 0.20 (reasonable minimum for any trade).
        if signal.confidence < 0.20:
            return False, f"Confidence too low: {signal.confidence:.2f}"

        # Check minimum risk:reward
        if signal.risk_reward_ratio < self.config.min_risk_reward:
            return False, f"R:R too low: {signal.risk_reward_ratio:.2f}"

        # Check max open positions
        if self.open_count >= self.config.max_open_positions:
            return False, f"Max positions reached: {self.open_count}"

        # Check if already in this symbol
        if signal.symbol in self._positions:
            return False, f"Already in position: {signal.symbol}"

        # Check daily loss limit
        if abs(self._daily_pnl) / self.initial_capital >= self.config.max_daily_loss_pct:
            return False, "Daily loss limit reached"

        # Check max drawdown
        if self.current_drawdown >= self.config.max_drawdown_pct:
            return False, f"Max drawdown reached: {self.current_drawdown:.2%}"

        # Check SL is set
        if signal.sl_price is None:
            return False, "No stop loss set"

        return True, "OK"

    def calculate_position_size(self, signal: Signal) -> float:
        """
        Calculate position size with ADAPTIVE sizing from PPMT metadata.

        V3 Formula:
          sizing_signal comes from BlockLifecycleMetadata.sizing_signal
          risk_pct = base_pct × sizing_signal
          risk_amount = capital × risk_pct
          size = risk_amount / |entry - sl|

        The sizing_signal combines probability_of_success, expected_profit_ahead,
        and risk_reward_ratio into a single number (0-2.0):
          - sizing_signal >= 1.5  → 2.0x base size (high conviction, size up)
          - sizing_signal 1.0-1.5 → 1.0x base size (normal)
          - sizing_signal 0.5-1.0 → 0.5x base size (low conviction, size down)
          - sizing_signal < 0.5   → 0.25x or reject (very low)

        This creates the tight PPMT → RiskManager integration where
        the Trie's metadata directly drives capital allocation.
        Better patterns get more capital, weaker patterns get less.

        Hard caps ensure we never exceed risk limits:
          - max_position_size_pct (6%) is never exceeded
          - min_position_size_pct (0.5%) ensures meaningful exposure
        """
        if signal.entry_price is None or signal.sl_price is None:
            return 0.0

        # Use metadata_sizing_signal from BlockLifecycleMetadata (preferred)
        # This creates the tight PPMT → RiskManager integration
        # Fallback to sizing_multiplier if metadata signal not available
        multiplier = signal.sizing_multiplier
        if signal.metadata_sizing_signal > 0:
            multiplier = signal.metadata_sizing_signal

        # Adaptive risk percentage
        risk_pct = self.config.base_position_size_pct * multiplier

        # Enforce hard caps
        risk_pct = max(self.config.min_position_size_pct, risk_pct)
        risk_pct = min(self.config.max_position_size_pct, risk_pct)

        risk_amount = self.capital * risk_pct
        sl_distance = abs(signal.entry_price - signal.sl_price)

        if sl_distance == 0:
            return 0.0

        # Position size in base currency
        size = risk_amount / sl_distance

        # Cap at available capital
        max_size = self.capital / signal.entry_price
        return min(size, max_size)

    def open_position(self, signal: Signal, size: float) -> Position:
        """Open a new position from a signal."""
        import time as _time

        position = Position(
            symbol=signal.symbol,
            direction=signal.direction or "LONG",
            entry_price=signal.entry_price or 0.0,
            sl_price=signal.sl_price or 0.0,
            tp_price=signal.tp_price,
            size=size,
            entry_time=_time.time(),
            signal_confidence=signal.confidence,
            quality_score=signal.quality_score,
            sizing_multiplier=signal.sizing_multiplier,
            expected_move_pct=signal.expected_move_pct,
            remaining_candles=signal.remaining_candles,
        )

        self._positions[signal.symbol] = position
        return position

    def close_position(
        self,
        symbol: str,
        exit_price: float,
    ) -> Optional[tuple[Position, float]]:
        """
        Close a position and realize P&L.

        Returns:
            Tuple of (closed_position, pnl_amount) or None
        """
        position = self._positions.pop(symbol, None)
        if position is None:
            return None

        if position.direction == "LONG":
            pnl = (exit_price - position.entry_price) * position.size
        else:
            pnl = (position.entry_price - exit_price) * position.size

        self.capital += pnl
        self._daily_pnl += pnl

        # Update peak capital
        if self.capital > self._peak_capital:
            self._peak_capital = self.capital

        return position, pnl

    def update_position(self, symbol: str, current_price: float) -> Optional[Position]:
        """Update unrealized P&L for a position."""
        position = self._positions.get(symbol)
        if position is None:
            return None

        if position.direction == "LONG":
            position.unrealized_pnl_pct = (
                (current_price - position.entry_price) / position.entry_price * 100
            )
        else:
            position.unrealized_pnl_pct = (
                (position.entry_price - current_price) / position.entry_price * 100
            )

        return position

    def check_stop_loss(self, symbol: str, current_price: float) -> bool:
        """Check if a position's stop loss has been hit."""
        position = self._positions.get(symbol)
        if position is None:
            return False

        if position.direction == "LONG":
            return current_price <= position.sl_price
        else:
            return current_price >= position.sl_price

    def check_take_profit(self, symbol: str, current_price: float) -> bool:
        """Check if a position's take profit has been hit."""
        position = self._positions.get(symbol)
        if position is None or position.tp_price is None:
            return False

        if position.direction == "LONG":
            return current_price >= position.tp_price
        else:
            return current_price <= position.tp_price

    def reset_daily(self) -> None:
        """Reset daily P&L tracking (call at start of each day)."""
        self._daily_pnl = 0.0

    def get_status(self) -> dict:
        """Get current risk manager status."""
        return {
            "capital": round(self.capital, 2),
            "initial_capital": round(self.initial_capital, 2),
            "pnl_pct": round((self.capital - self.initial_capital) / self.initial_capital * 100, 2),
            "current_drawdown": round(self.current_drawdown * 100, 2),
            "daily_pnl": round(self._daily_pnl, 2),
            "open_positions": self.open_count,
            "positions": [
                {
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "entry_price": p.entry_price,
                    "size": p.size,
                    "quality_score": round(p.quality_score, 3),
                    "sizing_multiplier": round(p.sizing_multiplier, 2),
                    "expected_move_pct": round(p.expected_move_pct, 2),
                    "remaining_candles": p.remaining_candles,
                    "unrealized_pnl_pct": round(p.unrealized_pnl_pct, 2),
                }
                for p in self._positions.values()
            ],
        }
