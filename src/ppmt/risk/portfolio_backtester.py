"""
Portfolio Backtester - Multi-Token Simultaneous Backtesting for PPMT v0.16.0

Runs backtests across multiple tokens simultaneously, with shared
capital management, cross-token correlation awareness, and regime-based
allocation shifts during the backtest.

This is fundamentally different from running individual backtests and
combining results — it simulates the REAL portfolio experience where:

  1. Capital is SHARED across tokens (opening BTC uses capital that SOL can't use)
  2. Correlation AFFECTS portfolio risk (BTC+ETH crash together)
  3. Regime changes REBALANCE the portfolio mid-backtest
  4. Circuit breakers PROTECT the entire portfolio simultaneously

Architecture:
  ┌──────────────────────────────────────────────────────────┐
  │                 PortfolioBacktester                       │
  │                                                          │
  │  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   │
  │  │  BTC Data   │   │  SOL Data   │   │  DOGE Data  │   │
  │  │  (OHLCV)    │   │  (OHLCV)    │   │  (OHLCV)    │   │
  │  └──────┬──────┘   └──────┬──────┘   └──────┬──────┘   │
  │         │                 │                  │           │
  │         ▼                 ▼                  ▼           │
  │  ┌──────────────────────────────────────────────────┐   │
  │  │         Time-Synchronized Simulation Loop         │   │
  │  │  For each candle timestamp:                       │   │
  │  │    1. Feed candle to each token's PPMT engine     │   │
  │  │    2. Collect signals from all tokens              │   │
  │  │    3. Portfolio-level signal prioritization         │   │
  │  │    4. Capital allocation check                     │   │
  │  │    5. Execute approved positions                   │   │
  │  │    6. Check SL/TP across all positions             │   │
  │  │    7. Update correlation matrix                    │   │
  │  │    8. Rebalance if needed                          │   │
  │  └──────────────────────────────────────────────────┘   │
  │         │                                                │
  │         ▼                                                │
  │  ┌──────────────────────────────────────────────────┐   │
  │  │         PortfolioBacktestResult                    │   │
  │  │  • Per-token PnL, win rate, Sharpe                │   │
  │  │  • Portfolio-level equity curve, drawdown, Sharpe │   │
  │  │  • Correlation evolution over time                │   │
  │  │  • Rebalance history                              │   │
  │  │  • Regime transitions log                         │   │
  │  └──────────────────────────────────────────────────┘   │
  └──────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn

from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig, TokenSlot
from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine, CorrelationMethod
from ppmt.risk.regime_allocator import RegimeAwareAllocator
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.signal import Signal, SignalType

console = Console()


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class TokenBacktestResult:
    """Backtest result for a single token within the portfolio.

    Attributes:
        symbol: Token symbol.
        total_trades: Number of completed trades.
        wins: Number of winning trades.
        losses: Number of losing trades.
        win_rate: Win rate (0-1).
        total_pnl: Total realized PnL in USD.
        total_pnl_pct: Total PnL as percentage of allocated capital.
        max_drawdown_pct: Maximum drawdown experienced.
        sharpe_ratio: Annualized Sharpe ratio.
        avg_trade_pnl: Average PnL per trade.
        best_trade: Best single trade PnL.
        worst_trade: Worst single trade PnL.
        avg_hold_candles: Average candles held per trade.
        signals_generated: Number of PPMT signals generated.
        signals_executed: Number of signals actually executed.
    """
    symbol: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    avg_trade_pnl: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_hold_candles: float = 0.0
    signals_generated: int = 0
    signals_executed: int = 0


@dataclass
class PortfolioBacktestResult:
    """Complete portfolio backtest result.

    Attributes:
        tokens: Per-token backtest results.
        total_capital: Starting capital.
        final_value: Final portfolio value.
        total_return_pct: Total portfolio return.
        max_drawdown_pct: Maximum portfolio drawdown.
        sharpe_ratio: Annualized portfolio Sharpe ratio.
        sortino_ratio: Annualized Sortino ratio.
        calmar_ratio: Return / max drawdown.
        total_trades: Total trades across all tokens.
        total_wins: Total winning trades.
        total_losses: Total losing trades.
        equity_curve: Portfolio value at each candle.
        drawdown_curve: Drawdown at each candle.
        correlation_evolution: Avg correlation over time.
        rebalance_count: Number of rebalances executed.
        regime_transitions: List of regime changes.
        duration_candles: Total candles in backtest.
    """
    tokens: dict = field(default_factory=dict)  # symbol -> TokenBacktestResult
    total_capital: float = 0.0
    final_value: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    equity_curve: list = field(default_factory=list)
    drawdown_curve: list = field(default_factory=list)
    correlation_evolution: list = field(default_factory=list)
    rebalance_count: int = 0
    regime_transitions: list = field(default_factory=list)
    duration_candles: int = 0

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "total_capital": round(self.total_capital, 2),
            "final_value": round(self.final_value, 2),
            "total_return_pct": round(self.total_return_pct * 100, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct * 100, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "sortino_ratio": round(self.sortino_ratio, 3),
            "calmar_ratio": round(self.calmar_ratio, 3),
            "total_trades": self.total_trades,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "win_rate": round(self.total_wins / max(1, self.total_trades), 3),
            "rebalance_count": self.rebalance_count,
            "regime_transitions": len(self.regime_transitions),
            "duration_candles": self.duration_candles,
            "tokens": {
                sym: {
                    "trades": r.total_trades,
                    "win_rate": round(r.win_rate, 3),
                    "pnl": round(r.total_pnl, 2),
                    "pnl_pct": round(r.total_pnl_pct * 100, 2),
                    "max_dd": round(r.max_drawdown_pct * 100, 2),
                    "sharpe": round(r.sharpe_ratio, 3),
                    "signals_gen": r.signals_generated,
                    "signals_exec": r.signals_executed,
                }
                for sym, r in self.tokens.items()
            },
        }


@dataclass
class PortfolioBacktestConfig:
    """Configuration for portfolio backtest.

    Attributes:
        initial_capital: Starting capital.
        tokens: Token symbols to backtest.
        allocation_method: How to allocate capital.
        rebalance_interval: How often to rebalance (candles). 0 = never.
        regime_shift_rebalance: Whether to rebalance on regime changes.
        max_portfolio_exposure: Maximum portfolio exposure.
        correlation_lookback: Window for correlation computation.
    """
    initial_capital: float = 50_000.0
    tokens: list = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    allocation_method: str = "REGIME_AWARE"
    rebalance_interval: int = 24  # Every 24 candles (1 day on 1h)
    regime_shift_rebalance: bool = True
    max_portfolio_exposure: float = 0.80
    correlation_lookback: int = 60


# ---------------------------------------------------------------------------
# PortfolioBacktester
# ---------------------------------------------------------------------------

class PortfolioBacktester:
    """
    Multi-Token Portfolio Backtester for PPMT v0.16.0.

    Simulates portfolio trading across multiple tokens with shared
    capital, correlation-aware risk management, and regime-based
    allocation adjustments.

    The backtester processes candles time-synchronously: at each
    timestamp, it feeds the candle to all token PPMT engines,
    collects signals, and makes portfolio-level decisions about
    which signals to execute based on available capital and
    correlation constraints.

    Usage:
        backtester = PortfolioBacktester(
            config=PortfolioBacktestConfig(
                initial_capital=50_000,
                tokens=["BTC/USDT", "SOL/USDT", "DOGE/USDT"],
            )
        )

        # Load historical data
        data = {
            "BTC/USDT": btc_candles,  # list of (timestamp, O, H, L, C, V)
            "SOL/USDT": sol_candles,
            "DOGE/USDT": doge_candles,
        }

        # Run backtest
        result = backtester.run(data)

        # Display results
        backtester.display_result(result)
    """

    def __init__(self, config: Optional[PortfolioBacktestConfig] = None):
        self.config = config or PortfolioBacktestConfig()

        # Portfolio manager for capital governance
        pm_config = PortfolioConfig(
            initial_capital=self.config.initial_capital,
            tokens=self.config.tokens,
            allocation_method=self.config.allocation_method,
            max_portfolio_exposure_pct=self.config.max_portfolio_exposure,
        )
        self.portfolio = PortfolioManager(config=pm_config)

        # Correlation engine
        self.correlation = CrossTokenCorrelationEngine(
            tokens=self.config.tokens,
            window=self.config.correlation_lookback,
        )

        # Regime-aware allocator
        self.allocator = RegimeAwareAllocator()

        # Asset classifier
        self._classifier = AssetClassifier()

        # Internal state
        self._equity_curve: list[float] = []
        self._drawdown_curve: list[float] = []
        self._peak_value: float = self.config.initial_capital
        self._candle_idx: int = 0
        self._regime_transitions: list[dict] = []
        self._rebalance_count: int = 0
        self._prev_regimes: dict[str, str] = {}

        # Async execution state (v0.19.0)
        self._is_running: bool = False
        self._is_cancelled: bool = False
        self._progress_pct: float = 0.0
        self._total_candles: int = 0
        self._result: Optional[PortfolioBacktestResult] = None
        self._last_error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._data: Optional[dict] = None
        self._regime_data: Optional[dict] = None
        self._signal_func: Optional[callable] = None

    # -------------------------------------------------------------------
    # Async Execution (v0.19.0)
    # -------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the backtest is currently executing."""
        return self._is_running

    @property
    def progress_pct(self) -> float:
        """Progress as a float 0.0–1.0."""
        return self._progress_pct

    @property
    def current_candle(self) -> int:
        """Current candle index being processed."""
        return self._candle_idx

    @property
    def last_result(self) -> Optional[PortfolioBacktestResult]:
        """Result from the last completed backtest run."""
        return self._result

    @property
    def last_error(self) -> Optional[str]:
        """Error from the last failed backtest run."""
        return self._last_error

    def get_live_status(self) -> dict:
        """Get current backtest status for API consumers.

        Returns a dict with: running, progress_pct, candle, total_candles,
        portfolio_value, open_positions, tokens, error.
        """
        return {
            "running": self._is_running,
            "progress_pct": round(self._progress_pct, 3),
            "candle": self._candle_idx,
            "total_candles": self._total_candles,
            "portfolio_value": round(self.portfolio.total_value, 2) if self.portfolio else 0.0,
            "open_positions": self.portfolio.total_open_positions if self.portfolio else 0,
            "tokens": self.config.tokens,
            "error": self._last_error,
        }

    def cancel(self) -> None:
        """Request graceful cancellation of the running backtest.

        The backtest will stop at the next candle boundary
        and produce a partial result.
        """
        self._is_cancelled = True

    def run_async(
        self,
        data: Optional[dict[str, list]] = None,
        regime_data: Optional[dict[str, list[str]]] = None,
        signal_generator_func: Optional[callable] = None,
    ) -> None:
        """Start the backtest in a background thread (non-blocking).

        If data was previously set (via set_data), it will be used.
        Otherwise, data must be provided here.

        Check progress via is_running / progress_pct / get_live_status().
        Get the result via last_result when is_running becomes False.
        """
        if data is not None:
            self._data = data
        if regime_data is not None:
            self._regime_data = regime_data
        if signal_generator_func is not None:
            self._signal_func = signal_generator_func

        if self._data is None:
            raise ValueError("No data provided for backtest. Pass data to run_async() or call set_data() first.")

        self._thread = threading.Thread(
            target=self._run_in_thread,
            daemon=True,
            name="portfolio-backtest",
        )
        self._thread.start()

    def set_data(
        self,
        data: dict[str, list],
        regime_data: Optional[dict[str, list[str]]] = None,
    ) -> None:
        """Pre-load data for a subsequent run_async() call."""
        self._data = data
        self._regime_data = regime_data

    def _run_in_thread(self) -> None:
        """Execute backtest in background thread."""
        try:
            self._is_running = True
            self._is_cancelled = False
            self._progress_pct = 0.0
            self._result = None
            self._last_error = None

            result = self.run(
                data=self._data,
                regime_data=self._regime_data,
                signal_generator_func=self._signal_func,
                progress=False,  # No CLI progress bar in API mode
            )
            self._result = result
            self._progress_pct = 1.0
        except Exception as e:
            self._last_error = str(e)
        finally:
            self._is_running = False

    # -------------------------------------------------------------------
    # Synchronous Run
    # -------------------------------------------------------------------

    def run(
        self,
        data: dict[str, list],
        regime_data: Optional[dict[str, list[str]]] = None,
        signal_generator_func: Optional[callable] = None,
        progress: bool = True,
    ) -> PortfolioBacktestResult:
        """
        Run the portfolio backtest.

        Args:
            data: Dict mapping symbol -> list of candle data.
                  Each candle is a dict with keys: 'timestamp', 'open', 'high',
                  'low', 'close', 'volume'.
            regime_data: Optional dict mapping symbol -> list of regime labels
                         aligned with the candle data.
            signal_generator_func: Optional callable that takes (symbol, candle_idx, data)
                                   and returns a Signal or None. If None, uses simplified
                                   price-action signals for the backtest.
            progress: Whether to show a progress bar.

        Returns:
            PortfolioBacktestResult with full backtest metrics.
        """
        # Determine backtest length (use shortest data series)
        lengths = {sym: len(candles) for sym, candles in data.items() if candles}
        if not lengths:
            return PortfolioBacktestResult()

        max_candles = min(lengths.values())
        total_candles = max_candles

        self._total_candles = max_candles
        self._reset()

        if progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=console,
            ) as progress_bar:
                task = progress_bar.add_task(
                    f"[cyan]Backtesting {len(data)} tokens...",
                    total=total_candles,
                )
                self._run_loop(
                    data, regime_data, signal_generator_func,
                    total_candles, progress_bar, task,
                )
        else:
            self._run_loop(
                data, regime_data, signal_generator_func,
                total_candles, None, None,
            )

        # Build final result
        return self._build_result(total_candles)

    def _reset(self) -> None:
        """Reset backtest state."""
        self._equity_curve = []
        self._drawdown_curve = []
        self._peak_value = self.config.initial_capital
        self._candle_idx = 0
        self._regime_transitions = []
        self._rebalance_count = 0
        self._prev_regimes = {}

        # Reset portfolio
        pm_config = PortfolioConfig(
            initial_capital=self.config.initial_capital,
            tokens=self.config.tokens,
            allocation_method=self.config.allocation_method,
            max_portfolio_exposure_pct=self.config.max_portfolio_exposure,
        )
        self.portfolio = PortfolioManager(config=pm_config)
        self.correlation = CrossTokenCorrelationEngine(
            tokens=self.config.tokens,
            window=self.config.correlation_lookback,
        )

    def _run_loop(
        self,
        data: dict[str, list],
        regime_data: Optional[dict[str, list[str]]],
        signal_generator_func: Optional[callable],
        total_candles: int,
        progress_bar: Optional[object],
        task: Optional[object],
    ) -> None:
        """Main backtest simulation loop."""
        for candle_idx in range(total_candles):
            # Check cancellation (v0.19.0)
            if self._is_cancelled:
                break

            self._candle_idx = candle_idx

            # Update progress for async consumers
            if total_candles > 0:
                self._progress_pct = candle_idx / total_candles

            # Process each token's candle
            for symbol, candles in data.items():
                if candle_idx >= len(candles):
                    continue

                candle = candles[candle_idx]
                close = candle.get("close", candle.get("c", 0))
                if close <= 0:
                    continue

                # Update correlation engine with new price
                self.correlation.update_price(symbol, close)

                # Update regime if provided
                if regime_data and symbol in regime_data:
                    regime_list = regime_data[symbol]
                    if candle_idx < len(regime_list):
                        regime = regime_list[candle_idx]
                        self.portfolio.update_regime(symbol, regime)

                        # Track regime transitions
                        prev = self._prev_regimes.get(symbol, "UNKNOWN")
                        if regime != prev:
                            self._regime_transitions.append({
                                "candle": candle_idx,
                                "symbol": symbol,
                                "from": prev,
                                "to": regime,
                            })
                            self._prev_regimes[symbol] = regime

                # Generate signal if function provided
                if signal_generator_func:
                    signal = signal_generator_func(symbol, candle_idx, data)
                    if signal and signal.is_entry:
                        self._process_entry_signal(signal, close)

                # Check SL/TP for existing positions
                slot = self.portfolio.get_slot(symbol)
                if slot and slot.risk_manager:
                    for pos in list(slot.risk_manager.open_positions):
                        # Check stop loss
                        if self.portfolio.check_stop_loss(pos.symbol, close):
                            self.portfolio.close_position(pos.symbol, close)
                        # Check take profit
                        elif self.portfolio.check_take_profit(pos.symbol, close):
                            self.portfolio.close_position(pos.symbol, close)

            # Record equity curve
            current_value = self.portfolio.total_value
            self._equity_curve.append(current_value)

            # Update peak and drawdown
            if current_value > self._peak_value:
                self._peak_value = current_value
            dd = (self._peak_value - current_value) / self._peak_value if self._peak_value > 0 else 0
            self._drawdown_curve.append(dd)

            # Periodic rebalance
            if (self.config.rebalance_interval > 0 and
                    candle_idx > 0 and
                    candle_idx % self.config.rebalance_interval == 0):
                self._do_rebalance(candle_idx)

            # Update progress
            if progress_bar and task is not None:
                progress_bar.update(task, advance=1)

    def _process_entry_signal(self, signal: Signal, current_price: float) -> None:
        """Process an entry signal through portfolio governance."""
        slot = self.portfolio.get_slot(signal.symbol)
        if slot is None or not slot.is_active:
            return

        # Calculate position size from the slot's RiskManager
        if slot.risk_manager:
            size = slot.risk_manager.calculate_position_size(signal)
            if size <= 0:
                return
        else:
            return

        # Check portfolio-level approval
        allowed, reason = self.portfolio.can_open_position(signal, size, current_price)
        if not allowed:
            return

        # Execute the position
        self.portfolio.open_position(signal, size)

    def _do_rebalance(self, candle_idx: int) -> None:
        """Execute a portfolio rebalance."""
        # Get current regime
        dominant_regime = self.portfolio._get_dominant_regime()

        # Get correlation regime
        corr_result = self.correlation.compute_matrix()
        corr_regime = corr_result.regime.value

        # Get current allocations
        current_alloc = {
            sym: slot.capital_allocated
            for sym, slot in self.portfolio._slots.items()
        }

        # Get token performance data
        perf_data = {}
        quality_data = {}
        for sym, slot in self.portfolio._slots.items():
            perf_data[sym] = {
                "win_rate": slot.win_rate,
                "pnl_pct": slot.pnl_pct,
                "trades": slot.trades_completed,
            }
            # Use win rate as proxy for quality if no pattern quality available
            quality_data[sym] = 0.5 + slot.win_rate * 0.3

        # Compute new allocation
        alloc_result = self.allocator.allocate(
            regime=dominant_regime,
            tokens=list(self.portfolio._slots.keys()),
            total_capital=self.portfolio.total_value,
            current_allocations=current_alloc,
            token_performance=perf_data,
            pattern_quality=quality_data,
            correlation_regime=corr_regime,
            portfolio_drawdown_pct=self.portfolio.current_drawdown_pct,
        )

        # Apply allocation changes
        for instr in alloc_result.instructions:
            slot = self.portfolio._slots.get(instr.symbol)
            if slot:
                slot.capital_allocated = instr.target_capital
                if slot.risk_manager:
                    slot.risk_manager.capital = instr.target_capital - slot.capital_used
                    slot.risk_manager.initial_capital = instr.target_capital

        self._rebalance_count += 1

    def _build_result(self, total_candles: int) -> PortfolioBacktestResult:
        """Build the final PortfolioBacktestResult from simulation state."""
        # Per-token results
        token_results = {}
        for sym, slot in self.portfolio._slots.items():
            # Compute Sharpe for this token
            returns = slot._returns if slot._returns else [0.0]
            sharpe = 0.0
            if len(returns) > 10:
                mean_ret = np.mean(returns)
                std_ret = np.std(returns)
                if std_ret > 0:
                    sharpe = mean_ret / std_ret * np.sqrt(252 * 24)  # Annualized for 1h candles

            token_results[sym] = TokenBacktestResult(
                symbol=sym,
                total_trades=slot.trades_completed,
                wins=slot.wins,
                losses=slot.losses,
                win_rate=slot.win_rate,
                total_pnl=slot.total_pnl,
                total_pnl_pct=slot.pnl_pct,
                max_drawdown_pct=slot.current_drawdown_pct,
                sharpe_ratio=sharpe,
                avg_trade_pnl=slot.total_pnl / max(1, slot.trades_completed),
                best_trade=0.0,  # Would need per-trade tracking
                worst_trade=0.0,
                signals_generated=slot.signals_generated,
                signals_executed=slot.trades_completed,
            )

        # Portfolio-level metrics
        final_value = self.portfolio.total_value
        total_return_pct = (final_value - self.config.initial_capital) / self.config.initial_capital

        # Portfolio Sharpe from equity curve
        portfolio_sharpe = 0.0
        portfolio_sortino = 0.0
        if len(self._equity_curve) > 10:
            returns = np.diff(self._equity_curve) / self._equity_curve[:-1]
            returns = returns[np.isfinite(returns)]
            if len(returns) > 10:
                mean_ret = np.mean(returns)
                std_ret = np.std(returns)
                downside = np.std(returns[returns < 0]) if np.any(returns < 0) else std_ret
                if std_ret > 0:
                    portfolio_sharpe = mean_ret / std_ret * np.sqrt(252 * 24)
                if downside > 0:
                    portfolio_sortino = mean_ret / downside * np.sqrt(252 * 24)

        max_dd = max(self._drawdown_curve) if self._drawdown_curve else 0.0
        calmar = total_return_pct / max_dd if max_dd > 0 else 0.0

        total_trades = sum(r.total_trades for r in token_results.values())
        total_wins = sum(r.wins for r in token_results.values())
        total_losses = sum(r.losses for r in token_results.values())

        return PortfolioBacktestResult(
            tokens=token_results,
            total_capital=self.config.initial_capital,
            final_value=round(final_value, 2),
            total_return_pct=total_return_pct,
            max_drawdown_pct=max_dd,
            sharpe_ratio=portfolio_sharpe,
            sortino_ratio=portfolio_sortino,
            calmar_ratio=calmar,
            total_trades=total_trades,
            total_wins=total_wins,
            total_losses=total_losses,
            equity_curve=self._equity_curve,
            drawdown_curve=self._drawdown_curve,
            rebalance_count=self._rebalance_count,
            regime_transitions=self._regime_transitions,
            duration_candles=total_candles,
        )

    # -------------------------------------------------------------------
    # Display
    # -------------------------------------------------------------------

    def display_result(self, result: PortfolioBacktestResult) -> None:
        """Display a rich portfolio backtest result."""
        pnl_color = "green" if result.total_return_pct >= 0 else "red"

        # Portfolio overview
        console.print(Panel(
            f"[bold]Capital:[/bold] ${result.total_capital:,.0f} -> ${result.final_value:,.0f}  "
            f"[{pnl_color}]Return: {result.total_return_pct * 100:+.1f}%[/{pnl_color}]  "
            f"[bold]Max DD:[/bold] {result.max_drawdown_pct * 100:.1f}%  "
            f"[bold]Sharpe:[/bold] {result.sharpe_ratio:.2f}  "
            f"[bold]Calmar:[/bold] {result.calmar_ratio:.2f}",
            title="[bold cyan]Portfolio Backtest Result[/bold cyan]",
            border_style="cyan",
        ))

        # Per-token results table
        table = Table(title="Per-Token Performance", show_lines=True)
        table.add_column("Token", style="bold")
        table.add_column("Trades", justify="right")
        table.add_column("Win Rate", justify="right")
        table.add_column("PnL", justify="right")
        table.add_column("PnL %", justify="right")
        table.add_column("Max DD", justify="right")
        table.add_column("Sharpe", justify="right")
        table.add_column("Signals", justify="right")

        for sym, r in result.tokens.items():
            pnl_str = f"${r.total_pnl:,.2f}"
            if r.total_pnl < 0:
                pnl_str = f"[red]{pnl_str}[/red]"
            elif r.total_pnl > 0:
                pnl_str = f"[green]{pnl_str}[/green]"

            table.add_row(
                sym,
                str(r.total_trades),
                f"{r.win_rate:.0%}",
                pnl_str,
                f"{r.total_pnl_pct * 100:+.1f}%",
                f"{r.max_drawdown_pct * 100:.1f}%",
                f"{r.sharpe_ratio:.2f}",
                f"{r.signals_generated}/{r.signals_executed}",
            )

        console.print(table)

        # Summary stats
        console.print(
            f"\n  Total Trades: {result.total_trades}  "
            f"Wins: {result.total_wins}  "
            f"Losses: {result.total_losses}  "
            f"Win Rate: {result.total_wins / max(1, result.total_trades):.0%}  "
            f"Rebalances: {result.rebalance_count}  "
            f"Regime Changes: {len(result.regime_transitions)}"
        )

    def save_result(
        self,
        result: PortfolioBacktestResult,
        filepath: str,
    ) -> None:
        """Save backtest result to JSON file."""
        output = result.to_dict()
        # Add equity curve (sample to keep file size manageable)
        if result.equity_curve:
            step = max(1, len(result.equity_curve) // 500)
            output["equity_curve_sampled"] = [
                {"idx": i, "value": round(v, 2)}
                for i, v in enumerate(result.equity_curve[::step])
            ]

        with open(filepath, "w") as f:
            json.dump(output, f, indent=2, default=str)
