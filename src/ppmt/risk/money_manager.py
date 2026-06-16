"""
Money Manager - Portfolio-Level Position & Capital Management for PPMT v0.14.0

The Money Manager sits ABOVE the existing RiskManager and provides
portfolio-level controls that the per-trade RiskManager cannot:

  RiskManager  → "How much should THIS trade be sized?"
  MoneyManager → "How much TOTAL exposure should the PORTFOLIO have?"

The Money Manager wraps RiskManager and adds:
  - Portfolio state tracking (total value, equity curve, drawdown)
  - Multi-position management (max positions, correlation limits)
  - Exposure & leverage control (portfolio-level caps)
  - Kill switch / circuit breakers (emergency controls)
  - Portfolio analytics (summary, exposure breakdown, risk reports)
  - Session persistence (save/load state to JSON)

Architecture:
  Signal → RiskManager.calculate_position_size() → MoneyManager.can_open()
                                                       │
                                                       ├─ Check portfolio exposure limits
                                                       ├─ Check correlation limits
                                                       ├─ Check circuit breakers
                                                       └─ If OK → RiskManager.open_position()

The Money Manager NEVER replaces RiskManager's per-trade logic.
It adds a second layer of portfolio-level governance on top.

Usage:
    from ppmt.risk.money_manager import MoneyManager, MoneyManagerConfig

    config = MoneyManagerConfig(initial_capital=50_000.0, max_open_positions=8)
    mm = MoneyManager(config=config)

    # Before opening a position
    if mm.can_open(signal, asset_class="blue_chip"):
        size = risk_manager.calculate_position_size(signal)
        position = mm.open_position(signal, size)

    # Portfolio analytics
    summary = mm.get_portfolio_summary()
    report = mm.get_risk_report()

    # Emergency control
    mm.activate_kill_switch(current_prices)

    # Persistence
    mm.save_state()
    mm.load_state()
"""

from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from ppmt.risk.manager import RiskManager, RiskConfig, Position
from ppmt.engine.signal import Signal
from ppmt.data.classifier import AssetClassifier

console = Console()


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class MoneyManagerConfig:
    """Configuration for the Money Manager.

    Controls portfolio-level risk limits that apply ACROSS all positions,
    as opposed to per-trade limits in RiskConfig.

    Attributes:
        initial_capital: Starting capital for the portfolio.
        max_open_positions: Maximum number of simultaneous open positions.
        max_correlated_positions: Maximum positions in the same asset class
            (prevents over-concentration in one sector).
        max_portfolio_exposure_pct: Maximum total exposure as a fraction of
            portfolio value. 0.80 = 80% of portfolio value at risk.
        max_single_position_exposure_pct: Maximum single position as a
            fraction of portfolio value. 0.25 = no single position > 25%.
        kill_switch_exposure_pct: If total exposure exceeds this fraction,
            automatically close ALL positions (emergency). 0.95 = 95%.
        max_daily_loss_pct: If daily realized P&L exceeds this negative
            fraction of initial capital, stop opening new positions.
        max_drawdown_pct: If drawdown from portfolio peak exceeds this
            fraction, stop opening new positions (circuit breaker).
        auto_save_interval_minutes: How often to auto-save state (0 = disabled).
        state_file: Path to JSON file for state persistence. Empty = no persistence.
    """

    initial_capital: float = 10_000.0
    max_open_positions: int = 5
    max_correlated_positions: int = 2
    max_portfolio_exposure_pct: float = 0.80
    max_single_position_exposure_pct: float = 0.25
    kill_switch_exposure_pct: float = 0.95
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.15
    auto_save_interval_minutes: int = 5
    state_file: str = ""


@dataclass
class PortfolioSnapshot:
    """A point-in-time snapshot of portfolio state.

    Stored in the equity curve for historical analysis and metric computation.

    Attributes:
        timestamp: Unix timestamp when this snapshot was taken.
        total_value: Total portfolio value (cash + unrealized P&L).
        cash: Available cash (not allocated to positions).
        unrealized_pnl: Sum of unrealized P&L across all open positions.
        realized_pnl: Cumulative realized P&L for the session.
        exposure_pct: Total exposure as a fraction of portfolio value.
        num_positions: Number of open positions.
        daily_return_pct: Return since the last daily reset, as a fraction.
    """

    timestamp: float
    total_value: float
    cash: float
    unrealized_pnl: float
    realized_pnl: float
    exposure_pct: float
    num_positions: int
    daily_return_pct: float


# ---------------------------------------------------------------------------
# MoneyManager
# ---------------------------------------------------------------------------

class MoneyManager:
    """
    Portfolio-Level Money Manager for PPMT v0.14.0.

    The Money Manager provides a governance layer on top of the per-trade
    RiskManager. It tracks portfolio-level state and enforces aggregate
    risk limits that no single-position risk manager can enforce:

    1. **Exposure Caps**: Total portfolio exposure cannot exceed
       ``max_portfolio_exposure_pct`` (default 80%). No single position
       can exceed ``max_single_position_exposure_pct`` (default 25%).

    2. **Correlation Limits**: Maximum positions per asset class
       (``max_correlated_positions``, default 2). Prevents loading up
       on correlated assets (e.g., BTC + ETH + SOL together).

    3. **Circuit Breakers**:
       - Daily loss limit: Stops new positions after ``max_daily_loss_pct``
         loss in one session.
       - Drawdown breaker: Stops new positions after ``max_drawdown_pct``
         drawdown from peak.
       - Kill switch: Emergency close-all when exposure exceeds
         ``kill_switch_exposure_pct``.

    4. **Portfolio Analytics**: Equity curve tracking, rolling Sharpe ratio,
       exposure breakdowns, and comprehensive risk reports.

    5. **Persistence**: Save/load portfolio state to JSON for session
       continuity across restarts.

    The Money Manager delegates per-trade sizing to RiskManager and only
    adds portfolio-level checks on top. The typical flow is::

        # Step 1: Per-trade sizing (RiskManager)
        size = risk_manager.calculate_position_size(signal)

        # Step 2: Portfolio-level approval (MoneyManager)
        if money_manager.can_open(signal, asset_class="blue_chip"):
            money_manager.open_position(signal, size)

    Args:
        config: MoneyManagerConfig with portfolio-level parameters.
        risk_config: Optional RiskConfig for the wrapped RiskManager.
            If None, a default RiskConfig is created.
    """

    def __init__(
        self,
        config: Optional[MoneyManagerConfig] = None,
        risk_config: Optional[RiskConfig] = None,
    ):
        self.config = config or MoneyManagerConfig()

        # Build the wrapped RiskManager with compatible capital settings
        if risk_config is None:
            risk_config = RiskConfig(
                max_open_positions=self.config.max_open_positions,
                max_correlated_positions=self.config.max_correlated_positions,
                max_daily_loss_pct=self.config.max_daily_loss_pct,
                max_drawdown_pct=self.config.max_drawdown_pct,
            )
        self.risk_manager = RiskManager(
            capital=self.config.initial_capital,
            config=risk_config,
        )

        # Portfolio state
        self._initial_capital: float = self.config.initial_capital
        self._cash: float = self.config.initial_capital
        self._realized_pnl: float = 0.0
        self._session_start_time: float = time.time()

        # Peak tracking for drawdown computation
        self._peak_value: float = self.config.initial_capital
        self._previous_day_value: float = self.config.initial_capital

        # Daily P&L tracking (separate from session P&L)
        self._daily_realized_pnl: float = 0.0
        self._last_daily_reset: float = time.time()

        # Equity curve: list of PortfolioSnapshot
        self._equity_curve: list[PortfolioSnapshot] = []

        # Asset classifier for correlation checks
        self._classifier = AssetClassifier()

        # Position → asset class mapping (for correlation tracking)
        self._position_asset_classes: dict[str, str] = {}

        # Position → mark-to-market notional value (for exposure tracking)
        self._position_notional: dict[str, float] = {}

        # Position → entry notional (original cash committed at entry)
        # Needed to correctly return cash on close without double-counting P&L
        self._position_entry_notional: dict[str, float] = {}

        # Circuit breaker state
        self._kill_switch_active: bool = False
        self._daily_loss_breaker_active: bool = False
        self._drawdown_breaker_active: bool = False
        self._kill_switch_triggered_at: Optional[float] = None

        # Auto-save state
        self._last_save_time: float = 0.0
        self._auto_save_lock = threading.Lock()

        # Closed position history (for analytics)
        self._closed_positions: list[dict] = []

        # Take initial snapshot
        self._record_snapshot()

    # -------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------

    @property
    def cash(self) -> float:
        """Available cash not allocated to open positions."""
        return self._cash

    @property
    def realized_pnl(self) -> float:
        """Cumulative realized P&L for the session."""
        return self._realized_pnl

    @property
    def unrealized_pnl(self) -> float:
        """Total unrealized P&L across all open positions."""
        total = 0.0
        for pos in self.risk_manager.open_positions:
            if pos.direction == "LONG":
                total += pos.unrealized_pnl_pct / 100.0 * pos.entry_price * pos.size
            else:
                total += pos.unrealized_pnl_pct / 100.0 * pos.entry_price * pos.size
        return total

    @property
    def total_value(self) -> float:
        """Total portfolio value: initial capital + realized P&L + unrealized P&L.

        This is the correct way to compute total portfolio value. It avoids
        the pitfall of using ``cash + unrealized_pnl`` because ``cash`` has
        already been reduced by the position notional when a trade is opened.
        The formula ``initial_capital + realized_pnl + unrealized_pnl``
        correctly tracks value through the full lifecycle:

          - At open: cash decreases, but position value equals cash spent
            → total_value unchanged
          - As price moves: unrealized P&L captures the change
            → total_value moves with the position
          - At close: unrealized → realized, cash returned + P&L
            → total_value reflects the gain/loss
        """
        return self._initial_capital + self._realized_pnl + self.unrealized_pnl

    @property
    def total_return_pct(self) -> float:
        """Total return as a fraction since inception."""
        if self._initial_capital == 0:
            return 0.0
        return (self.total_value - self._initial_capital) / self._initial_capital

    @property
    def daily_return_pct(self) -> float:
        """Return since the last daily reset, as a fraction."""
        if self._previous_day_value == 0:
            return 0.0
        return (self.total_value - self._previous_day_value) / self._previous_day_value

    @property
    def current_drawdown(self) -> float:
        """Current drawdown from peak portfolio value, as a fraction."""
        if self._peak_value == 0:
            return 0.0
        return max(0.0, (self._peak_value - self.total_value) / self._peak_value)

    @property
    def equity_curve(self) -> list[PortfolioSnapshot]:
        """Historical equity curve snapshots."""
        return list(self._equity_curve)

    @property
    def open_positions(self) -> list[Position]:
        """All currently open positions."""
        return self.risk_manager.open_positions

    @property
    def open_count(self) -> int:
        """Number of currently open positions."""
        return self.risk_manager.open_count

    @property
    def total_exposure(self) -> float:
        """Total notional exposure across all open positions."""
        return sum(self._position_notional.values())

    @property
    def exposure_pct(self) -> float:
        """Total exposure as a fraction of portfolio value."""
        if self.total_value == 0:
            return 0.0
        return self.total_exposure / self.total_value

    @property
    def leverage_ratio(self) -> float:
        """Current leverage ratio: total exposure / portfolio value."""
        if self.total_value == 0:
            return 0.0
        return self.total_exposure / self.total_value

    @property
    def net_long_short_ratio(self) -> float:
        """Ratio of long exposure to short exposure.

        Returns:
            > 1.0: Portfolio is net long
            < 1.0: Portfolio is net short
            1.0: Balanced
            0.0: No positions
        """
        long_exposure = 0.0
        short_exposure = 0.0
        for pos in self.risk_manager.open_positions:
            notional = self._position_notional.get(pos.symbol, 0.0)
            if pos.direction == "LONG":
                long_exposure += notional
            else:
                short_exposure += notional
        if short_exposure == 0:
            return float("inf") if long_exposure > 0 else 0.0
        return long_exposure / short_exposure

    # -------------------------------------------------------------------
    # Position Management
    # -------------------------------------------------------------------

    def can_open(
        self,
        signal: Signal,
        asset_class: str = "",
        proposed_size: float = 0.0,
        current_price: Optional[float] = None,
    ) -> tuple[bool, str]:
        """
        Check if a new position can be opened based on PORTFOLIO-LEVEL constraints.

        This method checks portfolio-level limits that the per-trade RiskManager
        does not enforce:
          - Total portfolio exposure cap
          - Single position exposure cap
          - Correlation limit (same asset class)
          - Circuit breaker status
          - Kill switch status

        The per-trade checks (quality, R:R, daily loss) are delegated to
        RiskManager.can_open().

        Args:
            signal: The trading signal to evaluate.
            asset_class: Asset class of the symbol (e.g., "blue_chip", "meme").
                If empty, auto-classified using AssetClassifier.
            proposed_size: Proposed position size (units of base currency).
                Used to compute notional exposure for limit checks.
            current_price: Current market price for notional calculation.
                If None, uses signal.entry_price.

        Returns:
            Tuple of (allowed: bool, reason: str).
        """
        # 1. Check kill switch
        if self._kill_switch_active:
            return False, "Kill switch is active — no new positions allowed"

        # 2. Check circuit breakers
        if not self.is_trading_allowed():
            breakers = self.circuit_breaker_status()
            active = [k for k, v in breakers.items() if v["active"]]
            return False, f"Circuit breakers active: {', '.join(active)}"

        # 3. Delegate per-trade checks to RiskManager
        rm_allowed, rm_reason = self.risk_manager.can_open(signal, asset_class)
        if not rm_allowed:
            return False, f"RiskManager rejected: {rm_reason}"

        # 4. Portfolio-level exposure cap
        if self.exposure_pct >= self.config.max_portfolio_exposure_pct:
            return False, (
                f"Portfolio exposure {self.exposure_pct:.1%} "
                f"at limit {self.config.max_portfolio_exposure_pct:.1%}"
            )

        # 5. Single position exposure cap
        price = current_price or signal.entry_price or 0.0
        if proposed_size > 0 and price > 0:
            proposed_notional = proposed_size * price
            max_allowed = self.config.max_single_position_exposure_pct * self.total_value
            if proposed_notional > max_allowed:
                return False, (
                    f"Position notional {proposed_notional:,.2f} exceeds "
                    f"single-position limit {max_allowed:,.2f} "
                    f"({self.config.max_single_position_exposure_pct:.0%} of portfolio)"
                )

        # 6. Would new position push total exposure over limit?
        if proposed_size > 0 and price > 0 and self.total_value > 0:
            proposed_notional = proposed_size * price
            projected_exposure = (self.total_exposure + proposed_notional) / self.total_value
            if projected_exposure > self.config.max_portfolio_exposure_pct:
                return False, (
                    f"Opening would push exposure to {projected_exposure:.1%} "
                    f"(limit {self.config.max_portfolio_exposure_pct:.1%})"
                )

        # 7. Correlation limit (max positions in same asset class)
        if not asset_class:
            info = self._classifier.classify(signal.symbol)
            asset_class = info.asset_class

        same_class_count = sum(
            1 for cls in self._position_asset_classes.values() if cls == asset_class
        )
        if same_class_count >= self.config.max_correlated_positions:
            return False, (
                f"Correlation limit: {same_class_count} positions already in "
                f"'{asset_class}' (max {self.config.max_correlated_positions})"
            )

        # 8. Check total exposure would not trigger kill switch
        if proposed_size > 0 and price > 0 and self.total_value > 0:
            proposed_notional = proposed_size * price
            projected_exposure = (self.total_exposure + proposed_notional) / self.total_value
            if projected_exposure > self.config.kill_switch_exposure_pct:
                return False, (
                    f"Opening would push exposure to {projected_exposure:.1%} "
                    f"— dangerously close to kill switch at "
                    f"{self.config.kill_switch_exposure_pct:.1%}"
                )

        return True, "OK"

    def open_position(
        self,
        signal: Signal,
        size: float,
        asset_class: str = "",
    ) -> Position:
        """
        Open a new position through the Money Manager.

        Delegates position creation to the wrapped RiskManager and
        updates portfolio-level tracking (cash, notional, asset class).

        Args:
            signal: The trading signal to execute.
            size: Position size (units of base currency), typically from
                RiskManager.calculate_position_size().
            asset_class: Asset class for correlation tracking. If empty,
                auto-classified using AssetClassifier.

        Returns:
            The newly created Position object.

        Raises:
            ValueError: If the position would violate portfolio limits.
        """
        # Open via RiskManager
        position = self.risk_manager.open_position(signal, size)

        # Update portfolio-level tracking
        notional = size * position.entry_price
        self._position_notional[position.symbol] = notional
        self._position_entry_notional[position.symbol] = notional
        self._cash -= notional  # Cash allocated to position

        # Track asset class for correlation
        if not asset_class:
            info = self._classifier.classify(signal.symbol)
            asset_class = info.asset_class
        self._position_asset_classes[position.symbol] = asset_class

        # Update peak
        if self.total_value > self._peak_value:
            self._peak_value = self.total_value

        # Check if exposure triggers kill switch
        if self.exposure_pct >= self.config.kill_switch_exposure_pct:
            self._kill_switch_active = True
            self._kill_switch_triggered_at = time.time()
            console.print(
                f"[bold red]KILL SWITCH TRIGGERED:[/bold red] "
                f"Exposure {self.exposure_pct:.1%} >= "
                f"{self.config.kill_switch_exposure_pct:.1%}"
            )

        # Record snapshot
        self._record_snapshot()

        # Auto-save if configured
        self._maybe_auto_save()

        return position

    def close_position(
        self,
        symbol: str,
        exit_price: float,
    ) -> Optional[tuple[Position, float]]:
        """
        Close an existing position and realize P&L.

        Delegates to RiskManager and updates portfolio-level tracking.

        Args:
            symbol: Symbol of the position to close.
            exit_price: Exit price for P&L calculation.

        Returns:
            Tuple of (closed_position, pnl_amount) or None if not found.
        """
        # Get entry notional before close (to release original cash committed)
        entry_notional = self._position_entry_notional.pop(symbol, 0.0)
        self._position_notional.pop(symbol, None)  # Clean up mark-to-market
        asset_class = self._position_asset_classes.pop(symbol, "")

        # Close via RiskManager
        result = self.risk_manager.close_position(symbol, exit_price)
        if result is None:
            return None

        position, pnl = result

        # Update cash: release original entry notional and add/subtract realized P&L
        # entry_notional is what was originally committed; pnl is the gain/loss.
        # This equals size * exit_price, but we compute it this way to avoid
        # any confusion with mark-to-market notional updates.
        self._cash += entry_notional + pnl

        # Update P&L tracking
        self._realized_pnl += pnl
        self._daily_realized_pnl += pnl

        # Track closed position for analytics
        self._closed_positions.append({
            "symbol": position.symbol,
            "direction": position.direction,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "size": position.size,
            "pnl": pnl,
            "asset_class": asset_class,
            "quality_score": position.quality_score,
            "close_time": time.time(),
        })

        # Check daily loss breaker
        if self._initial_capital > 0:
            daily_loss_pct = abs(min(0.0, self._daily_realized_pnl)) / self._initial_capital
            if daily_loss_pct >= self.config.max_daily_loss_pct:
                self._daily_loss_breaker_active = True

        # Check drawdown breaker
        if self.current_drawdown >= self.config.max_drawdown_pct:
            self._drawdown_breaker_active = True

        # Update peak
        if self.total_value > self._peak_value:
            self._peak_value = self.total_value

        # If kill switch was active and exposure is now below threshold,
        # we keep it active — it requires manual reset
        # (intentional: kill switch is a deliberate emergency stop)

        # Record snapshot
        self._record_snapshot()

        # Auto-save if configured
        self._maybe_auto_save()

        return result

    def update_position(self, symbol: str, current_price: float) -> Optional[Position]:
        """
        Update unrealized P&L for a position.

        Delegates to RiskManager and updates notional tracking.

        Args:
            symbol: Symbol of the position to update.
            current_price: Current market price.

        Returns:
            Updated Position or None if not found.
        """
        position = self.risk_manager.update_position(symbol, current_price)
        if position is None:
            return None

        # Update notional tracking (position size stays the same,
        # but mark-to-market value changes)
        self._position_notional[symbol] = position.size * current_price

        # Update peak
        if self.total_value > self._peak_value:
            self._peak_value = self.total_value

        # Check drawdown breaker on every update
        if self.current_drawdown >= self.config.max_drawdown_pct:
            self._drawdown_breaker_active = True

        return position

    def update_all_positions(self, prices: dict[str, float]) -> None:
        """
        Update all open positions with current prices.

        Args:
            prices: Dict mapping symbol → current price.
        """
        for pos in self.risk_manager.open_positions:
            if pos.symbol in prices:
                self.update_position(pos.symbol, prices[pos.symbol])

        # Record snapshot after bulk update
        self._record_snapshot()

    # -------------------------------------------------------------------
    # Kill Switch / Emergency Controls
    # -------------------------------------------------------------------

    def activate_kill_switch(self, current_prices: dict[str, float]) -> list[tuple[str, float]]:
        """
        Close ALL positions immediately (emergency).

        This is the nuclear option: close every open position at the
        current market price. Used when:
        - Total exposure exceeds kill_switch_exposure_pct
        - A black swan event occurs
        - Manual emergency stop

        Args:
            current_prices: Dict mapping symbol → current market price.
                Used as exit price for each position.

        Returns:
            List of (symbol, pnl) tuples for all closed positions.
        """
        self._kill_switch_active = True
        self._kill_switch_triggered_at = time.time()

        results = []
        symbols = [p.symbol for p in self.risk_manager.open_positions]

        console.print(
            f"[bold red]⚠ KILL SWITCH ACTIVATED — Closing {len(symbols)} positions[/bold red]"
        )

        for symbol in symbols:
            price = current_prices.get(symbol, 0.0)
            result = self.close_position(symbol, price)
            if result is not None:
                _, pnl = result
                results.append((symbol, pnl))
                color = "green" if pnl >= 0 else "red"
                console.print(f"  Closed {symbol}: [{color}]{pnl:+,.2f}[/{color}]")

        total_pnl = sum(p for _, p in results)
        console.print(
            f"[bold]Kill switch complete. Total realized P&L: "
            f"{total_pnl:+,.2f}[/bold]"
        )

        return results

    def reset_kill_switch(self) -> None:
        """
        Manually reset the kill switch after review.

        The kill switch does NOT auto-reset. It requires explicit
        human intervention to re-enable trading after an emergency.
        """
        self._kill_switch_active = False
        self._kill_switch_triggered_at = None
        console.print("[bold yellow]Kill switch manually reset.[/bold yellow]")

    def is_trading_allowed(self) -> bool:
        """
        Check whether trading is currently allowed.

        Returns False if ANY circuit breaker is active:
          - Kill switch
          - Daily loss limit exceeded
          - Max drawdown exceeded

        Returns:
            True if new positions can be opened, False otherwise.
        """
        if self._kill_switch_active:
            return False
        if self._daily_loss_breaker_active:
            return False
        if self._drawdown_breaker_active:
            return False
        return True

    def circuit_breaker_status(self) -> dict[str, dict]:
        """
        Get the status of all circuit breakers.

        Returns:
            Dict mapping breaker name → status dict with:
              - active: bool
              - reason: str explaining why the breaker is active (or "OK")
              - threshold: float — the configured threshold
              - current: float — the current value
        """
        daily_loss_pct = (
            abs(min(0.0, self._daily_realized_pnl)) / self._initial_capital
            if self._initial_capital > 0
            else 0.0
        )

        return {
            "kill_switch": {
                "active": self._kill_switch_active,
                "reason": (
                    f"Triggered at {self._kill_switch_triggered_at}"
                    if self._kill_switch_active
                    else "OK"
                ),
                "threshold": self.config.kill_switch_exposure_pct,
                "current": self.exposure_pct,
            },
            "daily_loss": {
                "active": self._daily_loss_breaker_active,
                "reason": (
                    f"Daily loss {daily_loss_pct:.2%} >= "
                    f"limit {self.config.max_daily_loss_pct:.2%}"
                    if self._daily_loss_breaker_active
                    else "OK"
                ),
                "threshold": self.config.max_daily_loss_pct,
                "current": daily_loss_pct,
            },
            "drawdown": {
                "active": self._drawdown_breaker_active,
                "reason": (
                    f"Drawdown {self.current_drawdown:.2%} >= "
                    f"limit {self.config.max_drawdown_pct:.2%}"
                    if self._drawdown_breaker_active
                    else "OK"
                ),
                "threshold": self.config.max_drawdown_pct,
                "current": self.current_drawdown,
            },
        }

    def reset_daily(self) -> None:
        """
        Reset daily P&L tracking and circuit breakers.

        Call at the start of each trading day. Resets:
          - Daily realized P&L counter
          - Daily loss circuit breaker
          - Previous day value (for daily return computation)

        Does NOT reset: kill switch (requires manual reset),
        drawdown breaker (requires peak to be recovered).
        """
        self._daily_realized_pnl = 0.0
        self._daily_loss_breaker_active = False
        self._previous_day_value = self.total_value
        self._last_daily_reset = time.time()
        self.risk_manager.reset_daily()

        # If drawdown has recovered below threshold, reset breaker
        if self.current_drawdown < self.config.max_drawdown_pct:
            self._drawdown_breaker_active = False

        self._record_snapshot()

    # -------------------------------------------------------------------
    # Portfolio Analytics
    # -------------------------------------------------------------------

    def rolling_sharpe_ratio(self, window: int = 30) -> float:
        """
        Compute rolling Sharpe ratio from the equity curve.

        Uses the last ``window`` snapshots to compute annualized
        Sharpe ratio (assuming ~252 trading days/year).

        Args:
            window: Number of recent snapshots to include.

        Returns:
            Annualized Sharpe ratio, or 0.0 if insufficient data.
        """
        if len(self._equity_curve) < 2:
            return 0.0

        # Take the last `window` snapshots
        snapshots = self._equity_curve[-window:]
        if len(snapshots) < 2:
            return 0.0

        # Compute returns
        values = np.array([s.total_value for s in snapshots])
        returns = np.diff(values) / values[:-1]

        if len(returns) == 0 or np.std(returns) == 0:
            return 0.0

        # Annualize (assume daily snapshots → 252 trading days)
        # If snapshots are intraday, Sharpe will be different
        mean_return = np.mean(returns)
        std_return = np.std(returns, ddof=1)

        if std_return == 0:
            return 0.0

        # v0.19.1: Annualize assuming 365 trading days (crypto markets trade 24/7)
        sharpe = (mean_return / std_return) * np.sqrt(365)
        return float(sharpe)

    def get_portfolio_summary(self) -> dict:
        """
        Get a comprehensive portfolio summary.

        Returns:
            Dict with all portfolio-level metrics including:
              - total_value, cash, unrealized_pnl, realized_pnl
              - total_return_pct, daily_return_pct
              - exposure_pct, leverage_ratio, net_long_short_ratio
              - current_drawdown, rolling_sharpe_ratio
              - num_positions, circuit_breakers
        """
        breakers = self.circuit_breaker_status()

        return {
            # Value
            "total_value": round(self.total_value, 2),
            "cash": round(self._cash, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "realized_pnl": round(self._realized_pnl, 2),
            "initial_capital": round(self._initial_capital, 2),

            # Returns
            "total_return_pct": round(self.total_return_pct * 100, 2),
            "daily_return_pct": round(self.daily_return_pct * 100, 2),
            "rolling_sharpe_ratio": round(self.rolling_sharpe_ratio(), 4),

            # Exposure
            "total_exposure": round(self.total_exposure, 2),
            "exposure_pct": round(self.exposure_pct * 100, 1),
            "leverage_ratio": round(self.leverage_ratio, 2),
            "net_long_short_ratio": round(self.net_long_short_ratio, 2),

            # Risk
            "current_drawdown_pct": round(self.current_drawdown * 100, 2),
            "peak_value": round(self._peak_value, 2),
            "daily_realized_pnl": round(self._daily_realized_pnl, 2),

            # Positions
            "num_positions": self.open_count,
            "max_positions": self.config.max_open_positions,

            # Circuit breakers
            "trading_allowed": self.is_trading_allowed(),
            "kill_switch_active": self._kill_switch_active,
            "daily_loss_breaker_active": self._daily_loss_breaker_active,
            "drawdown_breaker_active": self._drawdown_breaker_active,
            "circuit_breakers": breakers,

            # Session
            "session_duration_hours": round(
                (time.time() - self._session_start_time) / 3600, 2
            ),
            "equity_curve_length": len(self._equity_curve),
        }

    def get_position_report(self) -> list[dict]:
        """
        Get detailed report for all open positions.

        Returns:
            List of dicts, one per position, with:
              - symbol, direction, entry_price, size
              - notional, exposure_pct, asset_class
              - quality_score, sizing_multiplier
              - unrealized_pnl, unrealized_pnl_pct
              - sl_price, tp_price
        """
        report = []
        for pos in self.risk_manager.open_positions:
            notional = self._position_notional.get(pos.symbol, 0.0)
            asset_class = self._position_asset_classes.get(pos.symbol, "unknown")
            pos_exposure_pct = (
                notional / self.total_value if self.total_value > 0 else 0.0
            )

            # Compute unrealized P&L in currency terms
            if pos.direction == "LONG":
                upnl = pos.unrealized_pnl_pct / 100.0 * pos.entry_price * pos.size
            else:
                upnl = pos.unrealized_pnl_pct / 100.0 * pos.entry_price * pos.size

            report.append({
                "symbol": pos.symbol,
                "direction": pos.direction,
                "entry_price": round(pos.entry_price, 4),
                "size": round(pos.size, 6),
                "notional": round(notional, 2),
                "exposure_pct": round(pos_exposure_pct * 100, 1),
                "asset_class": asset_class,
                "quality_score": round(pos.quality_score, 3),
                "sizing_multiplier": round(pos.sizing_multiplier, 2),
                "unrealized_pnl": round(upnl, 2),
                "unrealized_pnl_pct": round(pos.unrealized_pnl_pct, 2),
                "sl_price": round(pos.sl_price, 4),
                "tp_price": round(pos.tp_price, 4) if pos.tp_price else None,
                "signal_confidence": round(pos.signal_confidence, 3),
            })

        return report

    def get_exposure_breakdown(self) -> dict:
        """
        Get exposure broken down by symbol, direction, and asset class.

        Returns:
            Dict with:
              - by_symbol: {symbol: notional, ...}
              - by_direction: {"LONG": notional, "SHORT": notional}
              - by_asset_class: {class: notional, ...}
              - total_exposure: float
              - exposure_pct: float
        """
        by_symbol: dict[str, float] = {}
        by_direction: dict[str, float] = {"LONG": 0.0, "SHORT": 0.0}
        by_asset_class: dict[str, float] = {}

        for pos in self.risk_manager.open_positions:
            notional = self._position_notional.get(pos.symbol, 0.0)

            # By symbol
            by_symbol[pos.symbol] = notional

            # By direction
            if pos.direction == "LONG":
                by_direction["LONG"] += notional
            else:
                by_direction["SHORT"] += notional

            # By asset class
            ac = self._position_asset_classes.get(pos.symbol, "unknown")
            by_asset_class[ac] = by_asset_class.get(ac, 0.0) + notional

        return {
            "by_symbol": {k: round(v, 2) for k, v in by_symbol.items()},
            "by_direction": {k: round(v, 2) for k, v in by_direction.items()},
            "by_asset_class": {k: round(v, 2) for k, v in by_asset_class.items()},
            "total_exposure": round(self.total_exposure, 2),
            "exposure_pct": round(self.exposure_pct * 100, 1),
        }

    def get_risk_report(self) -> dict:
        """
        Get a comprehensive risk assessment report.

        Returns:
            Dict combining portfolio summary, exposure breakdown,
            position report, and circuit breaker status.
        """
        return {
            "portfolio_summary": self.get_portfolio_summary(),
            "exposure_breakdown": self.get_exposure_breakdown(),
            "positions": self.get_position_report(),
            "circuit_breakers": self.circuit_breaker_status(),
            "config": {
                "max_open_positions": self.config.max_open_positions,
                "max_correlated_positions": self.config.max_correlated_positions,
                "max_portfolio_exposure_pct": self.config.max_portfolio_exposure_pct,
                "max_single_position_exposure_pct": self.config.max_single_position_exposure_pct,
                "kill_switch_exposure_pct": self.config.kill_switch_exposure_pct,
                "max_daily_loss_pct": self.config.max_daily_loss_pct,
                "max_drawdown_pct": self.config.max_drawdown_pct,
            },
            "closed_position_count": len(self._closed_positions),
            "closed_position_total_pnl": round(
                sum(p["pnl"] for p in self._closed_positions), 2
            ),
        }

    # -------------------------------------------------------------------
    # Equity Curve & Metrics
    # -------------------------------------------------------------------

    def _record_snapshot(self) -> None:
        """Record a point-in-time snapshot of portfolio state."""
        snapshot = PortfolioSnapshot(
            timestamp=time.time(),
            total_value=self.total_value,
            cash=self._cash,
            unrealized_pnl=self.unrealized_pnl,
            realized_pnl=self._realized_pnl,
            exposure_pct=self.exposure_pct,
            num_positions=self.open_count,
            daily_return_pct=self.daily_return_pct,
        )
        self._equity_curve.append(snapshot)

    def get_equity_curve_array(self) -> np.ndarray:
        """
        Get equity curve as a numpy array of total values.

        Returns:
            1-D numpy array of portfolio total values at each snapshot.
        """
        if not self._equity_curve:
            return np.array([])
        return np.array([s.total_value for s in self._equity_curve])

    def get_returns_array(self) -> np.ndarray:
        """
        Compute period-over-period returns from the equity curve.

        Returns:
            1-D numpy array of fractional returns between snapshots.
        """
        values = self.get_equity_curve_array()
        if len(values) < 2:
            return np.array([])
        return np.diff(values) / values[:-1]

    # -------------------------------------------------------------------
    # Session Persistence
    # -------------------------------------------------------------------

    def save_state(self, filepath: Optional[str] = None) -> str:
        """
        Save the full portfolio state to a JSON file.

        Includes positions, cash, P&L, equity curve, circuit breaker
        status, and closed position history.

        Args:
            filepath: Path to save to. If None, uses config.state_file.
                If both are empty, raises ValueError.

        Returns:
            The path the state was saved to.

        Raises:
            ValueError: If no filepath is provided and config.state_file is empty.
        """
        path = filepath or self.config.state_file
        if not path:
            raise ValueError("No state file path configured. Set config.state_file or pass filepath.")

        state = {
            "version": "0.14.0",
            "timestamp": time.time(),

            # Config
            "config": asdict(self.config),

            # Portfolio state
            "cash": self._cash,
            "initial_capital": self._initial_capital,
            "realized_pnl": self._realized_pnl,
            "daily_realized_pnl": self._daily_realized_pnl,
            "peak_value": self._peak_value,
            "previous_day_value": self._previous_day_value,
            "last_daily_reset": self._last_daily_reset,
            "session_start_time": self._session_start_time,

            # Circuit breakers
            "kill_switch_active": self._kill_switch_active,
            "kill_switch_triggered_at": self._kill_switch_triggered_at,
            "daily_loss_breaker_active": self._daily_loss_breaker_active,
            "drawdown_breaker_active": self._drawdown_breaker_active,

            # Positions (from RiskManager)
            "positions": [
                {
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "entry_price": p.entry_price,
                    "sl_price": p.sl_price,
                    "tp_price": p.tp_price,
                    "size": p.size,
                    "entry_time": p.entry_time,
                    "signal_confidence": p.signal_confidence,
                    "quality_score": p.quality_score,
                    "sizing_multiplier": p.sizing_multiplier,
                    "expected_move_pct": p.expected_move_pct,
                    "remaining_candles": p.remaining_candles,
                    "unrealized_pnl_pct": p.unrealized_pnl_pct,
                }
                for p in self.risk_manager.open_positions
            ],

            # Position metadata
            "position_notional": dict(self._position_notional),
            "position_entry_notional": dict(self._position_entry_notional),
            "position_asset_classes": dict(self._position_asset_classes),

            # Equity curve (last 1000 snapshots to keep file size manageable)
            "equity_curve": [
                asdict(s) for s in self._equity_curve[-1000:]
            ],

            # Closed positions (last 500)
            "closed_positions": self._closed_positions[-500:],

            # RiskManager state
            "risk_manager": {
                "capital": self.risk_manager.capital,
                "initial_capital": self.risk_manager.initial_capital,
                "daily_pnl": self.risk_manager._daily_pnl,
                "peak_capital": self.risk_manager._peak_capital,
            },
        }

        # Ensure directory exists
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(state, f, indent=2, default=float)

        self._last_save_time = time.time()
        return path

    def load_state(self, filepath: Optional[str] = None) -> None:
        """
        Restore portfolio state from a JSON file.

        Restores positions, cash, P&L, equity curve, and circuit
        breaker status from a previously saved state file.

        Args:
            filepath: Path to load from. If None, uses config.state_file.

        Raises:
            ValueError: If no filepath is provided and config.state_file is empty.
            FileNotFoundError: If the state file does not exist.
        """
        path = filepath or self.config.state_file
        if not path:
            raise ValueError("No state file path configured. Set config.state_file or pass filepath.")

        state_path = Path(path)
        if not state_path.exists():
            raise FileNotFoundError(f"State file not found: {path}")

        with open(path) as f:
            state = json.load(f)

        # Restore portfolio state
        self._cash = state.get("cash", self.config.initial_capital)
        self._initial_capital = state.get("initial_capital", self.config.initial_capital)
        self._realized_pnl = state.get("realized_pnl", 0.0)
        self._daily_realized_pnl = state.get("daily_realized_pnl", 0.0)
        self._peak_value = state.get("peak_value", self._initial_capital)
        self._previous_day_value = state.get("previous_day_value", self._initial_capital)
        self._last_daily_reset = state.get("last_daily_reset", time.time())
        self._session_start_time = state.get("session_start_time", time.time())

        # Restore circuit breakers
        self._kill_switch_active = state.get("kill_switch_active", False)
        self._kill_switch_triggered_at = state.get("kill_switch_triggered_at")
        self._daily_loss_breaker_active = state.get("daily_loss_breaker_active", False)
        self._drawdown_breaker_active = state.get("drawdown_breaker_active", False)

        # Restore position metadata
        self._position_notional = state.get("position_notional", {})
        self._position_entry_notional = state.get("position_entry_notional", {})
        self._position_asset_classes = state.get("position_asset_classes", {})

        # Restore positions into RiskManager
        # First, clear existing positions
        self.risk_manager._positions.clear()
        for pos_data in state.get("positions", []):
            position = Position(
                symbol=pos_data["symbol"],
                direction=pos_data["direction"],
                entry_price=pos_data["entry_price"],
                sl_price=pos_data["sl_price"],
                tp_price=pos_data.get("tp_price"),
                size=pos_data["size"],
                entry_time=pos_data["entry_time"],
                signal_confidence=pos_data["signal_confidence"],
                quality_score=pos_data.get("quality_score", 0.0),
                sizing_multiplier=pos_data.get("sizing_multiplier", 1.0),
                expected_move_pct=pos_data.get("expected_move_pct", 0.0),
                remaining_candles=pos_data.get("remaining_candles", 0),
                unrealized_pnl_pct=pos_data.get("unrealized_pnl_pct", 0.0),
            )
            self.risk_manager._positions[position.symbol] = position

        # Restore RiskManager state
        rm_state = state.get("risk_manager", {})
        self.risk_manager.capital = rm_state.get("capital", self._initial_capital)
        self.risk_manager.initial_capital = rm_state.get(
            "initial_capital", self._initial_capital
        )
        self.risk_manager._daily_pnl = rm_state.get("daily_pnl", 0.0)
        self.risk_manager._peak_capital = rm_state.get("peak_capital", self._initial_capital)

        # Restore equity curve
        self._equity_curve.clear()
        for snap_data in state.get("equity_curve", []):
            self._equity_curve.append(PortfolioSnapshot(**snap_data))

        # Restore closed positions
        self._closed_positions = state.get("closed_positions", [])

        console.print(
            f"[green]State loaded from {path}[/green] — "
            f"{len(self.risk_manager._positions)} positions restored, "
            f"equity curve: {len(self._equity_curve)} snapshots"
        )

    def _maybe_auto_save(self) -> None:
        """Auto-save state if enough time has elapsed since last save."""
        if self.config.auto_save_interval_minutes <= 0:
            return
        if not self.config.state_file:
            return

        elapsed = time.time() - self._last_save_time
        interval = self.config.auto_save_interval_minutes * 60

        if elapsed >= interval:
            with self._auto_save_lock:
                # Double-check after acquiring lock
                if time.time() - self._last_save_time >= interval:
                    try:
                        self.save_state()
                    except Exception as e:
                        console.print(f"[yellow]Auto-save failed: {e}[/yellow]")

    # -------------------------------------------------------------------
    # Rich Console Display
    # -------------------------------------------------------------------

    def print_portfolio_summary(self) -> None:
        """Print a formatted portfolio summary to the console using Rich."""
        summary = self.get_portfolio_summary()

        # Portfolio value panel
        value_text = Text()
        value_text.append(f"Total Value:  ${summary['total_value']:>12,.2f}\n")
        value_text.append(f"Cash:         ${summary['cash']:>12,.2f}\n")
        value_text.append(f"Unrealized:   ${summary['unrealized_pnl']:>+11,.2f}\n")
        value_text.append(f"Realized:     ${summary['realized_pnl']:>+11,.2f}")

        pnl_color = "green" if summary["total_return_pct"] >= 0 else "red"
        console.print(
            Panel(
                value_text,
                title=f"[{pnl_color}]Portfolio  "
                f"{summary['total_return_pct']:+.2f}%[/{pnl_color}]",
                border_style=pnl_color,
            )
        )

        # Metrics table
        table = Table(title="Portfolio Metrics", show_header=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right")

        table.add_row("Daily Return", f"{summary['daily_return_pct']:+.2f}%")
        table.add_row("Exposure", f"{summary['exposure_pct']:.1f}%")
        table.add_row("Leverage", f"{summary['leverage_ratio']:.2f}x")
        table.add_row("L/S Ratio", f"{summary['net_long_short_ratio']:.2f}")
        table.add_row("Drawdown", f"{summary['current_drawdown_pct']:.2f}%")
        table.add_row("Sharpe (rolling)", f"{summary['rolling_sharpe_ratio']:.4f}")
        table.add_row("Positions", f"{summary['num_positions']}/{summary['max_positions']}")
        table.add_row("Trading Allowed", str(summary["trading_allowed"]))

        console.print(table)

        # Circuit breaker status
        breakers = summary["circuit_breakers"]
        for name, status in breakers.items():
            icon = "🔴" if status["active"] else "🟢"
            console.print(f"  {icon} {name}: {status['reason']}")

    def print_position_report(self) -> None:
        """Print a formatted position report to the console using Rich."""
        positions = self.get_position_report()

        if not positions:
            console.print("[dim]No open positions[/dim]")
            return

        table = Table(title="Open Positions", show_header=True)
        table.add_column("Symbol", style="cyan")
        table.add_column("Dir", style="bold")
        table.add_column("Entry", justify="right")
        table.add_column("Size", justify="right")
        table.add_column("Notional", justify="right")
        table.add_column("Exposure", justify="right")
        table.add_column("uP&L", justify="right")
        table.add_column("uP&L%", justify="right")
        table.add_column("Class", style="dim")
        table.add_column("Quality", justify="right")

        for p in positions:
            pnl_color = "green" if p["unrealized_pnl"] >= 0 else "red"
            table.add_row(
                p["symbol"],
                p["direction"],
                f"{p['entry_price']:.4f}",
                f"{p['size']:.6f}",
                f"${p['notional']:,.2f}",
                f"{p['exposure_pct']:.1f}%",
                f"[{pnl_color}]{p['unrealized_pnl']:+,.2f}[/{pnl_color}]",
                f"[{pnl_color}]{p['unrealized_pnl_pct']:+.2f}%[/{pnl_color}]",
                p["asset_class"],
                f"{p['quality_score']:.3f}",
            )

        console.print(table)

    def print_exposure_breakdown(self) -> None:
        """Print a formatted exposure breakdown to the console using Rich."""
        breakdown = self.get_exposure_breakdown()

        # By symbol
        if breakdown["by_symbol"]:
            table = Table(title="Exposure by Symbol", show_header=True)
            table.add_column("Symbol", style="cyan")
            table.add_column("Notional", justify="right")
            table.add_column("% of Total", justify="right")

            for symbol, notional in sorted(
                breakdown["by_symbol"].items(), key=lambda x: x[1], reverse=True
            ):
                pct = notional / self.total_value * 100 if self.total_value > 0 else 0
                table.add_row(symbol, f"${notional:,.2f}", f"{pct:.1f}%")

            console.print(table)

        # By direction
        dir_table = Table(title="Exposure by Direction", show_header=True)
        dir_table.add_column("Direction", style="cyan")
        dir_table.add_column("Notional", justify="right")

        for direction, notional in breakdown["by_direction"].items():
            dir_table.add_row(direction, f"${notional:,.2f}")

        console.print(dir_table)

        # By asset class
        if breakdown["by_asset_class"]:
            ac_table = Table(title="Exposure by Asset Class", show_header=True)
            ac_table.add_column("Asset Class", style="cyan")
            ac_table.add_column("Notional", justify="right")
            ac_table.add_column("Positions", justify="right")

            for ac, notional in sorted(
                breakdown["by_asset_class"].items(), key=lambda x: x[1], reverse=True
            ):
                count = sum(
                    1 for v in self._position_asset_classes.values() if v == ac
                )
                ac_table.add_row(ac, f"${notional:,.2f}", str(count))

            console.print(ac_table)

    def print_risk_report(self) -> None:
        """Print a comprehensive formatted risk report using Rich."""
        report = self.get_risk_report()

        console.print(Panel("[bold]PPMT Money Manager — Risk Report[/bold]", border_style="red"))

        # Summary
        s = report["portfolio_summary"]
        console.print(
            f"\n[bold]Portfolio Value:[/bold] ${s['total_value']:,.2f}  "
            f"[{'green' if s['total_return_pct'] >= 0 else 'red'}]"
            f"({s['total_return_pct']:+.2f}%)[/{'green' if s['total_return_pct'] >= 0 else 'red'}]"
        )
        console.print(
            f"[bold]Drawdown:[/bold] {s['current_drawdown_pct']:.2f}%  |  "
            f"[bold]Exposure:[/bold] {s['exposure_pct']:.1f}%  |  "
            f"[bold]Leverage:[/bold] {s['leverage_ratio']:.2f}x"
        )
        console.print(
            f"[bold]Sharpe:[/bold] {s['rolling_sharpe_ratio']:.4f}  |  "
            f"[bold]Daily P&L:[/bold] ${s['daily_realized_pnl']:+,.2f}  |  "
            f"[bold]Session P&L:[/bold] ${s['realized_pnl']:+,.2f}"
        )

        # Circuit breakers
        console.print("\n[bold]Circuit Breakers:[/bold]")
        for name, status in report["circuit_breakers"].items():
            icon = "🔴" if status["active"] else "🟢"
            console.print(f"  {icon} {name}: {status['reason']}")

        # Positions
        self.print_position_report()

        # Exposure
        self.print_exposure_breakdown()

        # Closed positions summary
        console.print(
            f"\n[dim]Closed positions: {report['closed_position_count']}  |  "
            f"Total realized: ${report['closed_position_total_pnl']:+,.2f}[/dim]"
        )


# ---------------------------------------------------------------------------
# v0.23.0: Parent-Child Node Architecture & Leverage Control
# ---------------------------------------------------------------------------

@dataclass
class ChildNodeConfig:
    """Configuration for a child trading node.

    A child node represents a subprocess or independent strategy running
    under a parent MoneyManager. Each child gets allocated a portion of
    the parent's capital and can operate semi-independently.

    Attributes:
        node_id: Unique identifier for this child node (e.g., 'btc_1m', 'eth_5m').
        symbol: Trading pair this node handles (e.g., 'BTC/USDT').
        timeframe: Candle timeframe (e.g., '1m', '5m', '1h').
        capital_allocation_pct: Fraction of parent capital allocated to this node (0-1).
        leverage: Leverage multiplier (1 = spot, 2-125 = futures).
        auto_mode: If True, signals execute automatically. If False, require manual confirmation.
        max_position_pct: Max position size as fraction of this node's allocated capital.
        enabled: Whether this child node is actively trading.
    """
    node_id: str = ""
    symbol: str = ""
    timeframe: str = "1h"
    capital_allocation_pct: float = 0.20  # 20% of parent capital by default
    leverage: int = 1
    auto_mode: bool = True
    max_position_pct: float = 0.10  # 10% of allocated capital per trade
    enabled: bool = True


@dataclass
class ChildNodeState:
    """Runtime state for a child trading node.

    Tracks the child's allocated capital, P&L, and position state
    independently from the parent MoneyManager.
    """
    node_id: str
    allocated_capital: float = 0.0
    available_capital: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    open_positions: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    leverage: int = 1
    last_heartbeat: float = 0.0


class ParentNodeManager:
    """
    Parent-Child Node Architecture for Multi-Strategy Capital Distribution (v0.23.0).

    The Parent Node manages a pool of capital and distributes it among
    Child Nodes, each running an independent PPMT strategy. This enables:

    1. **Capital Distribution**: Allocate specific % of total capital to each child.
       Parent retains a reserve for safety.

    2. **Leverage Control**: Each child can have different leverage settings.
       BTC 1h might use 1x (spot), while BTC 5m uses 3x leverage.

    3. **Auto/Manual Modes**: Each child operates in auto or manual mode.
       Auto mode executes signals automatically. Manual mode displays signals
       but waits for human confirmation.

    4. **Parent-Child Communication**: Parent sends capital allocation updates,
       children report back P&L and position state.

    5. **Risk Aggregation**: Parent monitors total exposure across all children,
       can trigger kill switches globally or per-child.

    Architecture:
        ParentNodeManager (capital pool, risk aggregation)
          ├── ChildNode (BTC/USDT, 1h, 30% capital, leverage=1, auto)
          ├── ChildNode (BTC/USDT, 5m, 20% capital, leverage=3, auto)
          ├── ChildNode (ETH/USDT, 1h, 25% capital, leverage=1, manual)
          └── Reserve (25% capital, not allocated)

    Usage:
        parent = ParentNodeManager(total_capital=100_000)

        # Register children
        parent.register_child(ChildNodeConfig(
            node_id="btc_1h", symbol="BTC/USDT", timeframe="1h",
            capital_allocation_pct=0.30, leverage=1, auto_mode=True,
        ))
        parent.register_child(ChildNodeConfig(
            node_id="btc_5m", symbol="BTC/USDT", timeframe="5m",
            capital_allocation_pct=0.20, leverage=3, auto_mode=True,
        ))

        # Distribute capital
        parent.distribute_capital()

        # Get capital for a specific child
        child_capital = parent.get_child_capital("btc_1h")

        # Update child P&L
        parent.update_child_pnl("btc_1h", realized=150.0, unrealized=-20.0)

        # Check if child can open a position
        if parent.can_child_open("btc_1h", position_notional=5000):
            parent.allocate_child_capital("btc_1h", 5000)

        # Emergency: kill all children
        parent.activate_global_kill_switch()
    """

    def __init__(self, total_capital: float = 10_000.0):
        self.total_capital = total_capital
        self._children: dict[str, ChildNodeConfig] = {}
        self._child_states: dict[str, ChildNodeState] = {}
        self._global_kill_switch: bool = False
        self._global_kill_switch_time: Optional[float] = None

    @property
    def reserve_capital(self) -> float:
        """Capital not allocated to any child node."""
        allocated = sum(s.allocated_capital for s in self._child_states.values())
        return self.total_capital - allocated

    @property
    def total_realized_pnl(self) -> float:
        """Sum of realized P&L across all children."""
        return sum(s.realized_pnl for s in self._child_states.values())

    @property
    def total_unrealized_pnl(self) -> float:
        """Sum of unrealized P&L across all children."""
        return sum(s.unrealized_pnl for s in self._child_states.values())

    @property
    def total_portfolio_value(self) -> float:
        """Total portfolio value across all children + reserve."""
        return (self.reserve_capital +
                self.total_realized_pnl +
                self.total_unrealized_pnl)

    @property
    def total_exposure_pct(self) -> float:
        """Total exposure as fraction of total portfolio value."""
        if self.total_portfolio_value <= 0:
            return 0.0
        total_notional = 0.0
        for node_id, state in self._child_states.items():
            if state.open_positions > 0:
                config = self._children.get(node_id)
                if config:
                    # Estimate notional from available capital and leverage
                    position_capital = state.allocated_capital - state.available_capital
                    total_notional += position_capital * state.leverage
        return total_notional / self.total_portfolio_value

    def register_child(self, config: ChildNodeConfig) -> None:
        """Register a new child node with the parent.

        Args:
            config: ChildNodeConfig with allocation and trading parameters.
        """
        if config.node_id in self._children:
            raise ValueError(f"Child node '{config.node_id}' already registered")

        if config.capital_allocation_pct <= 0 or config.capital_allocation_pct > 1:
            raise ValueError(f"capital_allocation_pct must be (0, 1], got {config.capital_allocation_pct}")

        self._children[config.node_id] = config
        self._child_states[config.node_id] = ChildNodeState(
            node_id=config.node_id,
            leverage=config.leverage,
            last_heartbeat=time.time(),
        )

    def unregister_child(self, node_id: str) -> None:
        """Remove a child node. Its allocated capital returns to reserve."""
        if node_id not in self._children:
            raise ValueError(f"Child node '{node_id}' not found")

        # Close any open positions first
        state = self._child_states[node_id]
        if state.open_positions > 0:
            raise RuntimeError(
                f"Cannot unregister '{node_id}': has {state.open_positions} open positions. "
                f"Close all positions first."
            )

        # Return capital to reserve
        state.available_capital = 0.0
        state.allocated_capital = 0.0

        del self._children[node_id]
        del self._child_states[node_id]

    def distribute_capital(self) -> None:
        """Distribute capital among all registered child nodes.

        Allocates capital based on each child's capital_allocation_pct.
        If total allocation exceeds 100%, scales down proportionally.
        """
        total_allocation = sum(c.capital_allocation_pct for c in self._children.values())

        if total_allocation > 1.0:
            # Scale down proportionally to fit within 100%
            scale_factor = 0.95 / total_allocation  # Leave 5% reserve
            for config in self._children.values():
                effective_pct = config.capital_allocation_pct * scale_factor
                allocated = self.total_capital * effective_pct
                self._child_states[config.node_id].allocated_capital = allocated
                self._child_states[config.node_id].available_capital = allocated
        else:
            for config in self._children.values():
                allocated = self.total_capital * config.capital_allocation_pct
                self._child_states[config.node_id].allocated_capital = allocated
                self._child_states[config.node_id].available_capital = allocated

    def get_child_capital(self, node_id: str) -> float:
        """Get the current available capital for a child node."""
        if node_id not in self._child_states:
            raise ValueError(f"Child node '{node_id}' not found")
        return self._child_states[node_id].available_capital

    def get_child_leverage(self, node_id: str) -> int:
        """Get the leverage setting for a child node."""
        if node_id not in self._children:
            raise ValueError(f"Child node '{node_id}' not found")
        return self._children[node_id].leverage

    def set_child_leverage(self, node_id: str, leverage: int) -> None:
        """Update leverage for a child node (v0.23.0).

        Leverage can be changed dynamically. Must be between 1 (spot)
        and 125 (max futures leverage). Changes take effect on the next
        trade — existing positions are not affected.
        """
        if node_id not in self._children:
            raise ValueError(f"Child node '{node_id}' not found")
        if leverage < 1 or leverage > 125:
            raise ValueError(f"Leverage must be 1-125, got {leverage}")

        self._children[node_id].leverage = leverage
        self._child_states[node_id].leverage = leverage

    def set_child_auto_mode(self, node_id: str, auto: bool) -> None:
        """Switch a child node between auto and manual mode.

        Auto mode: signals execute automatically.
        Manual mode: signals are displayed but require confirmation.
        """
        if node_id not in self._children:
            raise ValueError(f"Child node '{node_id}' not found")
        self._children[node_id].auto_mode = auto

    def can_child_open(self, node_id: str, position_notional: float) -> tuple[bool, str]:
        """Check if a child node can open a new position.

        Checks:
          1. Child node exists and is enabled
          2. Global kill switch is not active
          3. Child has enough available capital
          4. Position doesn't exceed max_position_pct of allocated capital
          5. Total portfolio exposure is within limits
        """
        if self._global_kill_switch:
            return False, "Global kill switch active"

        if node_id not in self._children:
            return False, f"Child node '{node_id}' not found"

        config = self._children[node_id]
        if not config.enabled:
            return False, f"Child node '{node_id}' is disabled"

        state = self._child_states[node_id]

        # Check available capital (with leverage)
        leveraged_capital = state.available_capital * config.leverage
        if position_notional > leveraged_capital:
            return False, f"Insufficient capital: need ${position_notional:,.2f}, have ${leveraged_capital:,.2f}"

        # Check max position size
        max_notional = state.allocated_capital * config.max_position_pct * config.leverage
        if position_notional > max_notional:
            return False, f"Position exceeds max: ${max_notional:,.2f}"

        # Check total exposure
        if self.total_exposure_pct > 0.90:
            return False, f"Portfolio exposure too high: {self.total_exposure_pct:.1%}"

        return True, "OK"

    def allocate_child_capital(self, node_id: str, amount: float) -> None:
        """Allocate capital from a child's available pool to a position."""
        if node_id not in self._child_states:
            raise ValueError(f"Child node '{node_id}' not found")

        state = self._child_states[node_id]
        if amount > state.available_capital:
            raise ValueError(
                f"Cannot allocate ${amount:,.2f}: only ${state.available_capital:,.2f} available"
            )
        state.available_capital -= amount
        state.open_positions += 1

    def release_child_capital(self, node_id: str, amount: float, pnl: float = 0.0) -> None:
        """Release capital back to a child's available pool when a position closes.

        Args:
            node_id: Child node identifier.
            amount: Original allocated amount to return.
            pnl: Realized P&L from the closed position (positive = profit).
        """
        if node_id not in self._child_states:
            raise ValueError(f"Child node '{node_id}' not found")

        state = self._child_states[node_id]
        state.available_capital += amount + pnl
        state.realized_pnl += pnl
        state.open_positions = max(0, state.open_positions - 1)
        state.total_trades += 1
        if pnl > 0:
            state.winning_trades += 1

    def update_child_pnl(self, node_id: str, realized: float = 0.0, unrealized: float = 0.0) -> None:
        """Update P&L tracking for a child node."""
        if node_id not in self._child_states:
            raise ValueError(f"Child node '{node_id}' not found")

        state = self._child_states[node_id]
        state.realized_pnl += realized
        state.unrealized_pnl = unrealized
        state.last_heartbeat = time.time()

    def activate_global_kill_switch(self) -> None:
        """Emergency: activate kill switch for ALL child nodes.

        All children should immediately close all positions and stop trading.
        """
        self._global_kill_switch = True
        self._global_kill_switch_time = time.time()
        # Disable all children
        for config in self._children.values():
            config.enabled = False

    def deactivate_global_kill_switch(self) -> None:
        """Deactivate the global kill switch, re-enabling all children."""
        self._global_kill_switch = False
        self._global_kill_switch_time = None
        for config in self._children.values():
            config.enabled = True

    def activate_child_kill_switch(self, node_id: str) -> None:
        """Emergency: activate kill switch for a single child node."""
        if node_id not in self._children:
            raise ValueError(f"Child node '{node_id}' not found")
        self._children[node_id].enabled = False

    def redistribute_capital(self, allocations: dict[str, float]) -> None:
        """Re-distribute capital among children with new allocation percentages.

        This is used when market conditions change and you want to shift
        capital between strategies. Children with open positions will not
        have their capital reduced below current exposure.

        Args:
            allocations: Dict of node_id → new capital_allocation_pct.
        """
        for node_id, new_pct in allocations.items():
            if node_id not in self._children:
                raise ValueError(f"Child node '{node_id}' not found")
            if new_pct < 0 or new_pct > 1:
                raise ValueError(f"Allocation must be [0, 1], got {new_pct}")

            config = self._children[node_id]
            state = self._child_states[node_id]

            # Calculate new allocation
            new_allocated = self.total_capital * new_pct

            # Don't reduce below current exposure
            current_exposure = state.allocated_capital - state.available_capital
            if new_allocated < current_exposure:
                new_allocated = current_exposure

            # Calculate difference and adjust available capital
            delta = new_allocated - state.allocated_capital
            state.allocated_capital = new_allocated
            state.available_capital = max(0, state.available_capital + delta)

            config.capital_allocation_pct = new_pct

    def get_status(self) -> dict:
        """Get comprehensive status of all child nodes and the parent."""
        children_status = {}
        for node_id, config in self._children.items():
            state = self._child_states[node_id]
            allocated = state.allocated_capital
            available = state.available_capital
            children_status[node_id] = {
                "symbol": config.symbol,
                "timeframe": config.timeframe,
                "allocation_pct": f"{config.capital_allocation_pct:.0%}",
                "allocated_capital": f"${allocated:,.2f}",
                "available_capital": f"${available:,.2f}",
                "leverage": f"{config.leverage}x",
                "auto_mode": config.auto_mode,
                "enabled": config.enabled,
                "open_positions": state.open_positions,
                "total_trades": state.total_trades,
                "win_rate": f"{state.winning_trades / state.total_trades:.1%}" if state.total_trades > 0 else "N/A",
                "realized_pnl": f"${state.realized_pnl:+,.2f}",
                "unrealized_pnl": f"${state.unrealized_pnl:+,.2f}",
            }

        return {
            "total_capital": f"${self.total_capital:,.2f}",
            "portfolio_value": f"${self.total_portfolio_value:,.2f}",
            "reserve_capital": f"${self.reserve_capital:,.2f}",
            "total_exposure_pct": f"{self.total_exposure_pct:.1%}",
            "total_realized_pnl": f"${self.total_realized_pnl:+,.2f}",
            "total_unrealized_pnl": f"${self.total_unrealized_pnl:+,.2f}",
            "global_kill_switch": self._global_kill_switch,
            "children": children_status,
        }

    def print_status(self) -> None:
        """Print a formatted status table of all child nodes."""
        status = self.get_status()

        console.print(Panel(
            f"[bold]PPMT Parent Node Manager[/bold]\n"
            f"Total Capital: {status['total_capital']}  |  "
            f"Portfolio Value: {status['portfolio_value']}  |  "
            f"Reserve: {status['reserve_capital']}\n"
            f"Exposure: {status['total_exposure_pct']}  |  "
            f"P&L: {status['total_realized_pnl']}  |  "
            f"Kill Switch: {'🔴 ACTIVE' if status['global_kill_switch'] else '🟢 OFF'}",
            border_style="cyan",
        ))

        if status["children"]:
            table = Table(title="Child Nodes", show_header=True)
            table.add_column("Node ID", style="cyan")
            table.add_column("Symbol")
            table.add_column("TF")
            table.add_column("Alloc", justify="right")
            table.add_column("Available", justify="right")
            table.add_column("Lev", justify="right")
            table.add_column("Mode")
            table.add_column("Positions", justify="right")
            table.add_column("P&L", justify="right")
            table.add_column("WR", justify="right")

            for node_id, info in status["children"].items():
                pnl_color = "green" if "+" in info["realized_pnl"] else "red"
                mode_color = "green" if info["auto_mode"] else "yellow"
                mode_text = "AUTO" if info["auto_mode"] else "MANUAL"
                enabled_text = "✓" if info["enabled"] else "✗"

                table.add_row(
                    f"{enabled_text} {node_id}",
                    info["symbol"],
                    info["timeframe"],
                    info["allocation_pct"],
                    info["available_capital"],
                    info["leverage"],
                    f"[{mode_color}]{mode_text}[/{mode_color}]",
                    str(info["open_positions"]),
                    f"[{pnl_color}]{info['realized_pnl']}[/{pnl_color}]",
                    info["win_rate"],
                )

            console.print(table)
