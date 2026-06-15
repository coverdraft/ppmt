"""
Portfolio Manager - Multi-Token Portfolio Governance for PPMT v0.16.0

The Portfolio Manager orchestrates MULTIPLE token slots under a single
portfolio umbrella. Each slot runs its own PPMT engine (Trie + SAX +
RiskManager), but capital allocation, correlation limits, and circuit
breakers are managed at the PORTFOLIO level.

Architecture:
  ┌──────────────────────────────────────────────────────────┐
  │                  PortfolioManager                         │
  │  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
  │  │ BTC Slot │  │ SOL Slot │  │ DOGE Slot│  ...          │
  │  │ ├─Trie   │  │ ├─Trie   │  │ ├─Trie   │              │
  │  │ ├─SAX    │  │ ├─SAX    │  │ ├─SAX    │              │
  │  │ └─RiskMgr│  │ └─RiskMgr│  │ └─RiskMgr│              │
  │  └──────────┘  └──────────┘  └──────────┘              │
  │         │             │             │                     │
  │         ▼             ▼             ▼                     │
  │  ┌───────────────────────────────────────────────────┐   │
  │  │            Portfolio Governor                     │   │
  │  │  • Capital allocation per token (risk budgeting)  │   │
  │  │  • Cross-token correlation matrix                │   │
  │  │  • Regime-aware allocation shifts                │   │
  │  │  • Exposure caps + circuit breakers              │   │
  │  │  • Kill switch (portfolio-wide)                  │   │
  │  └───────────────────────────────────────────────────┘   │
  └──────────────────────────────────────────────────────────┘

Key Innovation:
  Unlike traditional portfolio managers that treat all assets equally,
  PPMT's PortfolioManager uses the QUALITY of each token's pattern
  matches to drive capital allocation:

    Better PPMT patterns → More capital allocated
    Weaker PPMT patterns → Less capital allocated
    No PPMT pattern      → No allocation (skip token)

  This creates a feedback loop where the Trie's metadata directly
  determines portfolio composition.

Usage:
    from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

    config = PortfolioConfig(
        initial_capital=50_000.0,
        tokens=["BTC/USDT", "SOL/USDT", "DOGE/USDT"],
    )
    pm = PortfolioManager(config=config)

    # Process a new candle for a specific token
    pm.process_candle("BTC/USDT", candle_data)

    # Get portfolio state
    summary = pm.get_portfolio_summary()

    # Rebalance based on current regime
    pm.rebalance("TRENDING_UP")
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

from ppmt.risk.manager import RiskManager, RiskConfig, Position
from ppmt.risk.money_manager import MoneyManager, MoneyManagerConfig, PortfolioSnapshot
from ppmt.risk.position_sizing import AdvancedPositionSizer
from ppmt.risk.correlation_engine import (
    CrossTokenCorrelationEngine,
    CorrelationMethod,
    CorrelationRegime,
    CorrelationMatrixResult,
)
from ppmt.risk.regime_allocator import RegimeAwareAllocator, AllocationResult
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.signal import Signal, SignalType

console = Console()


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class TokenSlot:
    """A single token's trading slot within the portfolio.

    Each slot has its own RiskManager for per-trade sizing,
    plus state tracking for portfolio-level governance.

    Attributes:
        symbol: Trading pair (e.g., 'BTC/USDT').
        asset_class: Asset classification (blue_chip, meme, etc.).
        risk_manager: Per-trade risk manager for this token.
        capital_allocated: Capital allocated to this slot (USD).
        capital_used: Capital currently in open positions (USD).
        is_active: Whether this slot is actively trading.
        last_signal_time: Timestamp of the last signal for this token.
        signals_generated: Count of signals generated this session.
        trades_completed: Count of trades completed this session.
        wins: Number of winning trades.
        losses: Number of losing trades.
        total_pnl: Cumulative realized PnL for this slot.
        max_drawdown_pct: Maximum drawdown experienced by this slot.
        current_regime: Current market regime for this token.
    """
    symbol: str
    asset_class: str = ""
    risk_manager: Optional[RiskManager] = None
    capital_allocated: float = 0.0
    capital_used: float = 0.0
    is_active: bool = True
    last_signal_time: float = 0.0
    signals_generated: int = 0
    trades_completed: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    current_regime: str = "UNKNOWN"
    # Performance tracking
    _peak_capital: float = 0.0
    _returns: list = field(default_factory=list)

    def __post_init__(self):
        if self._peak_capital == 0.0 and self.capital_allocated > 0:
            self._peak_capital = self.capital_allocated

    @property
    def win_rate(self) -> float:
        """Win rate for this token slot."""
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def pnl_pct(self) -> float:
        """Return percentage for this slot."""
        if self.capital_allocated == 0:
            return 0.0
        return self.total_pnl / self.capital_allocated

    @property
    def available_capital(self) -> float:
        """Capital available for new positions in this slot."""
        return self.capital_allocated - self.capital_used

    @property
    def current_drawdown_pct(self) -> float:
        """Current drawdown for this slot."""
        if self._peak_capital == 0:
            return 0.0
        current = self.capital_allocated + self.total_pnl
        return max(0.0, (self._peak_capital - current) / self._peak_capital) if self._peak_capital > 0 else 0.0

    def to_dict(self) -> dict:
        """Serialize token slot state."""
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "capital_allocated": round(self.capital_allocated, 2),
            "capital_used": round(self.capital_used, 2),
            "available_capital": round(self.available_capital, 2),
            "is_active": self.is_active,
            "signals_generated": self.signals_generated,
            "trades_completed": self.trades_completed,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 3),
            "total_pnl": round(self.total_pnl, 2),
            "pnl_pct": round(self.pnl_pct * 100, 2),
            "current_drawdown_pct": round(self.current_drawdown_pct * 100, 2),
            "current_regime": self.current_regime,
        }


@dataclass
class PortfolioConfig:
    """Configuration for the Portfolio Manager.

    Attributes:
        initial_capital: Total starting capital for the portfolio.
        tokens: List of trading pairs to include (e.g., ['BTC/USDT', 'SOL/USDT']).
        max_positions_per_token: Maximum open positions per token slot.
        max_portfolio_positions: Maximum total open positions across all tokens.
        max_portfolio_exposure_pct: Maximum total exposure as fraction of portfolio.
        max_single_token_exposure_pct: Maximum single token exposure as fraction.
        max_correlated_tokens: Maximum tokens in the same asset class.
        rebalance_interval_candles: How often to rebalance (in candles). 0 = manual.
        allocation_method: How to allocate capital across tokens.
        kill_switch_drawdown_pct: Portfolio drawdown that triggers kill switch.
        daily_loss_limit_pct: Daily loss that stops new positions.
        state_file: Path to JSON file for persistence.
    """
    initial_capital: float = 50_000.0
    tokens: list = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    max_positions_per_token: int = 2
    max_portfolio_positions: int = 8
    max_portfolio_exposure_pct: float = 0.80
    max_single_token_exposure_pct: float = 0.30
    max_correlated_tokens: int = 2
    rebalance_interval_candles: int = 0
    allocation_method: str = "EQUAL_WEIGHT"  # EQUAL_WEIGHT, RISK_PARITY, REGIME_AWARE, QUALITY_WEIGHTED
    kill_switch_drawdown_pct: float = 0.20
    daily_loss_limit_pct: float = 0.05
    state_file: str = ""


@dataclass
class RebalanceResult:
    """Result of a portfolio rebalance operation.

    Attributes:
        timestamp: When the rebalance happened.
        regime: Current market regime.
        allocations_before: Token -> allocation % before rebalance.
        allocations_after: Token -> allocation % after rebalance.
        capital_moves: List of capital transfer instructions.
        reason: Why the rebalance was triggered.
    """
    timestamp: float
    regime: str
    allocations_before: dict = field(default_factory=dict)
    allocations_after: dict = field(default_factory=dict)
    capital_moves: list = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# PortfolioManager
# ---------------------------------------------------------------------------

class PortfolioManager:
    """
    Multi-Token Portfolio Manager for PPMT v0.16.0.

    Manages multiple token slots under unified portfolio governance.
    Each token has its own RiskManager for per-trade decisions, but
    capital allocation, exposure limits, and correlation constraints
    are enforced at the portfolio level.

    Key responsibilities:
    1. **Capital Allocation**: Distribute capital across tokens based on
       allocation method (equal weight, risk parity, regime-aware, quality-weighted).
    2. **Exposure Management**: Enforce portfolio-level exposure caps
       that no single-token risk manager can enforce.
    3. **Correlation Governance**: Limit exposure to correlated asset classes.
    4. **Circuit Breakers**: Portfolio-wide kill switch, daily loss limit,
       drawdown breaker.
    5. **Rebalancing**: Periodically adjust allocations based on regime,
       performance, and quality metrics.
    6. **Analytics**: Portfolio-level summary, risk report, equity curve.

    The PortfolioManager wraps MoneyManager for portfolio-level tracking
    and adds multi-token orchestration on top.
    """

    def __init__(self, config: Optional[PortfolioConfig] = None):
        self.config = config or PortfolioConfig()

        # Portfolio-level money manager (for equity curve, circuit breakers, etc.)
        mm_config = MoneyManagerConfig(
            initial_capital=self.config.initial_capital,
            max_open_positions=self.config.max_portfolio_positions,
            max_portfolio_exposure_pct=self.config.max_portfolio_exposure_pct,
            max_single_position_exposure_pct=self.config.max_single_token_exposure_pct,
            max_drawdown_pct=self.config.kill_switch_drawdown_pct,
            max_daily_loss_pct=self.config.daily_loss_limit_pct,
        )
        self.money_manager = MoneyManager(config=mm_config)

        # Token slots: symbol -> TokenSlot
        self._slots: dict[str, TokenSlot] = {}

        # Asset classifier
        self._classifier = AssetClassifier()

        # Cross-token correlation engine (REAL correlation matrix)
        self._correlation_engine = CrossTokenCorrelationEngine(
            tokens=self.config.tokens,
            window=60,
            method=CorrelationMethod.PEARSON,
        )

        # Regime-aware allocator (for REGIME_AWARE and dynamic rebalancing)
        self._regime_allocator = RegimeAwareAllocator()

        # Portfolio state
        self._initial_capital: float = self.config.initial_capital
        self._candles_processed: int = 0
        self._last_rebalance: float = 0.0
        self._rebalance_history: list[RebalanceResult] = []

        # Circuit breaker state
        self._kill_switch_active: bool = False
        self._daily_loss_active: bool = False
        self._drawdown_active: bool = False

        # Correlation-aware circuit breaker
        self._correlation_crisis_active: bool = False

        # Auto-save
        self._auto_save_lock = threading.Lock()
        self._last_save_time: float = 0.0

        # Equity curve for portfolio-level tracking
        self._equity_curve: list[dict] = []

        # Initialize token slots
        self._initialize_slots()

    def _initialize_slots(self) -> None:
        """Create TokenSlots for all configured tokens."""
        for symbol in self.config.tokens:
            info = self._classifier.classify(symbol)
            risk_config = RiskConfig(
                max_open_positions=self.config.max_positions_per_token,
            )
            slot = TokenSlot(
                symbol=symbol,
                asset_class=info.asset_class,
                risk_manager=RiskManager(
                    capital=0,  # Will be set during allocation
                    config=risk_config,
                ),
            )
            self._slots[symbol] = slot

        # Initial capital allocation
        self._allocate_capital()

    def _allocate_capital(self) -> None:
        """Distribute capital across token slots based on allocation method."""
        method = self.config.allocation_method
        active_tokens = [s for s in self._slots.values() if s.is_active]

        if not active_tokens:
            return

        allocations = self._compute_allocations(active_tokens, method)

        for slot in active_tokens:
            new_capital = allocations.get(slot.symbol, 0.0)
            old_capital = slot.capital_allocated

            slot.capital_allocated = new_capital
            slot._peak_capital = max(slot._peak_capital, new_capital)

            # Update the RiskManager's capital
            if slot.risk_manager is not None:
                slot.risk_manager.capital = new_capital
                slot.risk_manager.initial_capital = new_capital

    def _compute_allocations(self, slots: list[TokenSlot], method: str) -> dict[str, float]:
        """Compute capital allocations for the given slots.

        Args:
            slots: Active token slots to allocate to.
            method: Allocation method name.

        Returns:
            Dict mapping symbol -> allocated capital (USD).
        """
        total = self.config.initial_capital
        n = len(slots)

        if method == "EQUAL_WEIGHT":
            per_token = total / n
            return {s.symbol: per_token for s in slots}

        elif method == "RISK_PARITY":
            # Inverse-volatility weighting: more capital to less volatile tokens
            # Use asset_class as proxy for volatility
            vol_proxies = {
                "blue_chip": 0.40,
                "large_cap": 0.55,
                "mid_cap": 0.70,
                "defi": 0.80,
                "meme": 1.00,
                "new_launch": 1.20,
            }
            inverse_vols = []
            for s in slots:
                vol = vol_proxies.get(s.asset_class, 0.80)
                # Use actual drawdown if available to refine
                if s.current_drawdown_pct > 0:
                    vol = max(vol, s.current_drawdown_pct * 5)  # Scale up observed DD
                inverse_vols.append(1.0 / vol)

            total_inv = sum(inverse_vols)
            return {
                s.symbol: total * (iv / total_inv)
                for s, iv in zip(slots, inverse_vols)
            }

        elif method == "REGIME_AWARE":
            # Regime-dependent allocation multipliers
            regime_mults: dict[str, dict[str, float]] = {
                "TRENDING_UP": {
                    "blue_chip": 1.5, "large_cap": 1.3, "mid_cap": 1.0,
                    "defi": 0.8, "meme": 0.6, "new_launch": 0.3,
                },
                "TRENDING_DOWN": {
                    "blue_chip": 1.2, "large_cap": 0.8, "mid_cap": 0.5,
                    "defi": 0.3, "meme": 0.2, "new_launch": 0.1,
                },
                "RANGING": {
                    "blue_chip": 1.0, "large_cap": 1.0, "mid_cap": 0.8,
                    "defi": 0.7, "meme": 0.5, "new_launch": 0.3,
                },
                "VOLATILE": {
                    "blue_chip": 1.3, "large_cap": 0.7, "mid_cap": 0.4,
                    "defi": 0.3, "meme": 0.2, "new_launch": 0.1,
                },
                "UNKNOWN": {
                    "blue_chip": 1.0, "large_cap": 1.0, "mid_cap": 0.8,
                    "defi": 0.6, "meme": 0.4, "new_launch": 0.2,
                },
            }
            # Use dominant regime across all slots
            dominant_regime = self._get_dominant_regime()
            mults = regime_mults.get(dominant_regime, regime_mults["UNKNOWN"])

            raw_weights = []
            for s in slots:
                w = mults.get(s.asset_class, 0.5)
                # Adjust by performance: winning slots get more
                if s.win_rate > 0.55:
                    w *= 1.2
                elif s.win_rate < 0.40 and s.trades_completed > 5:
                    w *= 0.5
                raw_weights.append(w)

            total_w = sum(raw_weights)
            if total_w == 0:
                per_token = total / n
                return {s.symbol: per_token for s in slots}

            return {
                s.symbol: total * (w / total_w)
                for s, w in zip(slots, raw_weights)
            }

        elif method == "QUALITY_WEIGHTED":
            # Weight by PPMT signal quality: more capital to tokens with
            # better pattern quality and more signals
            quality_scores = []
            for s in slots:
                # Base quality from win rate and PnL
                quality = max(0.1, 0.5 + s.pnl_pct * 2)  # PnL contribution
                quality *= (0.5 + s.win_rate)  # Win rate contribution
                # Boost for active signal generation
                if s.signals_generated > 0:
                    quality *= min(1.5, 1.0 + s.signals_generated / 50.0)
                quality_scores.append(max(0.1, quality))

            total_q = sum(quality_scores)
            if total_q == 0:
                per_token = total / n
                return {s.symbol: per_token for s in slots}

            return {
                s.symbol: total * (q / total_q)
                for s, q in zip(slots, quality_scores)
            }

        else:
            # Default to equal weight
            per_token = total / n
            return {s.symbol: per_token for s in slots}

    def _get_dominant_regime(self) -> str:
        """Get the dominant regime across all token slots."""
        regime_counts: dict[str, int] = {}
        for slot in self._slots.values():
            if slot.is_active:
                regime_counts[slot.current_regime] = regime_counts.get(slot.current_regime, 0) + 1

        if not regime_counts:
            return "UNKNOWN"
        return max(regime_counts, key=regime_counts.get)

    # -------------------------------------------------------------------
    # Token Management
    # -------------------------------------------------------------------

    def add_token(self, symbol: str, capital: Optional[float] = None) -> TokenSlot:
        """Add a new token slot to the portfolio.

        Args:
            symbol: Trading pair to add.
            capital: Optional capital to allocate. If None, auto-calculated.

        Returns:
            The new TokenSlot.
        """
        if symbol in self._slots:
            return self._slots[symbol]

        info = self._classifier.classify(symbol)
        risk_config = RiskConfig(
            max_open_positions=self.config.max_positions_per_token,
        )

        # Calculate allocation
        if capital is None:
            n = len(self._slots) + 1
            capital = self.config.initial_capital / n
            # Rebalance all slots to make room
            self.config.tokens.append(symbol)
            self._allocate_capital()
        else:
            self.config.tokens.append(symbol)

        slot = TokenSlot(
            symbol=symbol,
            asset_class=info.asset_class,
            risk_manager=RiskManager(capital=capital, config=risk_config),
            capital_allocated=capital,
            _peak_capital=capital,
        )
        self._slots[symbol] = slot

        # Add to correlation engine so it starts tracking this token
        self._correlation_engine.add_token(symbol)

        return slot

    def remove_token(self, symbol: str) -> Optional[TokenSlot]:
        """Remove a token slot from the portfolio.

        Will not remove if the token has open positions.

        Returns:
            The removed TokenSlot, or None if not found or has positions.
        """
        slot = self._slots.get(symbol)
        if slot is None:
            return None

        if slot.risk_manager and slot.risk_manager.open_count > 0:
            return None  # Can't remove with open positions

        del self._slots[symbol]
        if symbol in self.config.tokens:
            self.config.tokens.remove(symbol)

        # Remove from correlation engine
        self._correlation_engine.remove_token(symbol)

        # Rebalance remaining slots
        self._allocate_capital()
        return slot

    def get_slot(self, symbol: str) -> Optional[TokenSlot]:
        """Get a token slot by symbol."""
        return self._slots.get(symbol)

    def activate_slot(self, symbol: str) -> bool:
        """Activate a token slot for trading."""
        slot = self._slots.get(symbol)
        if slot:
            slot.is_active = True
            return True
        return False

    def deactivate_slot(self, symbol: str) -> bool:
        """Deactivate a token slot (won't open new positions)."""
        slot = self._slots.get(symbol)
        if slot:
            slot.is_active = False
            return True
        return False

    # -------------------------------------------------------------------
    # Signal Processing & Position Management
    # -------------------------------------------------------------------

    def process_candle(
        self,
        symbol: str,
        candle: dict,
        regime: Optional[str] = None,
    ) -> Optional[Signal]:
        """
        Process a new candle for a specific token.

        This is the main entry point for the portfolio's trading loop.
        It updates the correlation engine with the new price, updates
        the token slot's regime, and increments the candle counter.

        The actual PPMT engine (Trie + SAX) signal generation is done
        externally — this method handles the portfolio-level bookkeeping
        and returns None. Signals should be fed via open_position().

        Args:
            symbol: Token symbol (e.g., 'BTC/USDT').
            candle: Dict with at minimum 'close' (price) and optionally
                'open', 'high', 'low', 'volume', 'timestamp'.
            regime: Optional regime override for this token.

        Returns:
            None (signal generation happens in the PPMT engine layer).
        """
        slot = self._slots.get(symbol)
        if slot is None:
            return None

        close_price = candle.get("close", 0.0)
        if close_price <= 0:
            return None

        # 1. Update correlation engine with new price
        self._correlation_engine.update_price(symbol, close_price)

        # 2. Update token regime if provided
        if regime is not None:
            self.update_regime(symbol, regime)

        # 3. Update position mark-to-market
        if slot.risk_manager:
            for pos in slot.risk_manager.open_positions:
                slot.risk_manager.update_position(pos.symbol, close_price)

        # 4. Increment candle counter
        self._candles_processed += 1

        # 5. Check correlation regime for circuit breaker
        self._check_correlation_regime()

        # 6. Auto-rebalance check
        if self._should_rebalance():
            self.rebalance(reason=f"auto_rebalance_candle_{self._candles_processed}")

        # 7. Record equity curve snapshot
        self._record_equity_snapshot()

        return None

    def _check_correlation_regime(self) -> None:
        """Check if correlation regime has shifted to crisis.

        When the cross-token correlation enters CRISIS mode, it means
        all tokens are moving together and diversification benefits
        are gone. This activates a special circuit breaker that reduces
        portfolio exposure.
        """
        result = self._correlation_engine.compute_matrix()
        if result.regime == CorrelationRegime.CRISIS:
            if not self._correlation_crisis_active:
                self._correlation_crisis_active = True
                console.print(
                    "[bold yellow]CORRELATION CRISIS DETECTED:[/bold yellow] "
                    f"Avg correlation = {result.avg_correlation:.2f}. "
                    "Reducing exposure limits."
                )
        elif result.regime == CorrelationRegime.NORMAL:
            if self._correlation_crisis_active:
                self._correlation_crisis_active = False
                console.print(
                    "[green]Correlation regime returned to NORMAL.[/green]"
                )

    def _record_equity_snapshot(self) -> None:
        """Record a point-in-time equity curve snapshot."""
        self._equity_curve.append({
            "timestamp": time.time(),
            "total_value": round(self.total_value, 2),
            "realized_pnl": round(self.total_realized_pnl, 2),
            "unrealized_pnl": round(self.total_unrealized_pnl, 2),
            "exposure_pct": round(self.portfolio_exposure_pct * 100, 1),
            "positions": self.total_open_positions,
            "candle": self._candles_processed,
        })

    def get_correlation_matrix(self) -> CorrelationMatrixResult:
        """Get the current cross-token correlation matrix.

        Returns the real correlation matrix computed from actual price
        returns, with fallback to proxy correlations when insufficient
        data exists.

        Returns:
            CorrelationMatrixResult with the NxN matrix and metadata.
        """
        return self._correlation_engine.compute_matrix()

    def get_correlation_between(self, token_a: str, token_b: str) -> Optional[float]:
        """Get the real correlation between two tokens.

        Uses the CrossTokenCorrelationEngine's rolling window of
        actual returns to compute the correlation. Falls back to
        asset-class proxy correlations if insufficient data.

        Args:
            token_a: First token symbol.
            token_b: Second token symbol.

        Returns:
            Correlation coefficient (-1 to 1), or None if tokens not found.
        """
        result = self._correlation_engine.compute_matrix()
        return result.get_pair_correlation(token_a, token_b)

    def get_diversification_score(self) -> dict:
        """Get the portfolio diversification score.

        Considers average correlation, HHI of eigenvalues, and number
        of correlation clusters. Score ranges from 0 (poor) to 1 (excellent).

        Returns:
            Dict with score, rating, avg_correlation, clusters, effective_positions.
        """
        return self._correlation_engine.compute_diversification_score()

    def get_allocation_recommendation(self, regime: Optional[str] = None) -> AllocationResult:
        """Get a regime-aware allocation recommendation.

        Uses the RegimeAwareAllocator to compute optimal capital
        allocations based on regime, token performance, and pattern quality.

        Args:
            regime: Override regime. If None, uses the dominant regime.

        Returns:
            AllocationResult with per-token allocation instructions.
        """
        if regime is None:
            regime = self._get_dominant_regime()

        # Build performance and quality data from current slots
        current_alloc = {
            sym: slot.capital_allocated for sym, slot in self._slots.items()
        }
        perf_data = {
            sym: {
                "win_rate": slot.win_rate,
                "pnl_pct": slot.pnl_pct,
                "trades": slot.trades_completed,
                "sharpe": 0.0,  # Could compute from returns
            }
            for sym, slot in self._slots.items()
        }
        quality_data = {
            sym: 0.5 + slot.win_rate * 0.3  # Proxy from win rate
            for sym, slot in self._slots.items()
        }

        # Get correlation regime
        corr_result = self._correlation_engine.compute_matrix()
        corr_regime = corr_result.regime.value

        return self._regime_allocator.allocate(
            regime=regime,
            tokens=list(self._slots.keys()),
            total_capital=self.total_value,
            current_allocations=current_alloc,
            token_performance=perf_data,
            pattern_quality=quality_data,
            correlation_regime=corr_regime,
            portfolio_drawdown_pct=self.current_drawdown_pct,
        )

    def can_open_position(
        self,
        signal: Signal,
        proposed_size: float = 0.0,
        current_price: Optional[float] = None,
    ) -> tuple[bool, str]:
        """
        Check if a new position can be opened, considering BOTH
        portfolio-level and per-token constraints.

        Checks in order:
        1. Kill switch status
        2. Portfolio circuit breakers
        3. Portfolio exposure cap
        4. Token slot active status
        5. Token slot capital availability
        6. Per-token RiskManager check
        7. Correlation limit (same asset class)
        8. Single-token exposure cap

        Returns:
            Tuple of (allowed: bool, reason: str).
        """
        # 1. Kill switch
        if self._kill_switch_active:
            return False, "Portfolio kill switch active"

        # 2. Circuit breakers
        if not self.is_trading_allowed():
            breakers = self.circuit_breaker_status()
            active = [k for k, v in breakers.items() if v["active"]]
            return False, f"Portfolio circuit breakers: {', '.join(active)}"

        # 3. Portfolio exposure cap
        if self.portfolio_exposure_pct >= self.config.max_portfolio_exposure_pct:
            return False, (
                f"Portfolio exposure {self.portfolio_exposure_pct:.1%} "
                f"at limit {self.config.max_portfolio_exposure_pct:.1%}"
            )

        # 4. Token slot active
        slot = self._slots.get(signal.symbol)
        if slot is None:
            return False, f"No slot for {signal.symbol}"

        if not slot.is_active:
            return False, f"Slot {signal.symbol} is deactivated"

        # 5. Token slot capital
        if slot.available_capital <= 0:
            return False, f"No available capital in {signal.symbol} slot"

        # 6. Per-token RiskManager check
        if slot.risk_manager:
            rm_ok, rm_reason = slot.risk_manager.can_open(signal, slot.asset_class)
            if not rm_ok:
                return False, f"Token RiskManager: {rm_reason}"

        # 7. Correlation limit — use REAL correlation matrix
        # Instead of just counting same-asset-class positions, check actual
        # pairwise correlations. If the new token is highly correlated
        # (>0.7) with any token that already has an open position, reject.
        asset_class = slot.asset_class
        same_class_count = sum(
            1 for s in self._slots.values()
            if s.asset_class == asset_class
            and s.risk_manager and s.risk_manager.open_count > 0
        )
        if same_class_count >= self.config.max_correlated_tokens:
            return False, (
                f"Correlation limit: {same_class_count} positions in "
                f"'{asset_class}' (max {self.config.max_correlated_tokens})"
            )

        # 7b. Real correlation check: reject if new token is highly
        # correlated with any token already in a position
        for other_slot in self._slots.values():
            if other_slot.symbol == signal.symbol:
                continue
            if not other_slot.risk_manager or other_slot.risk_manager.open_count == 0:
                continue
            pair_corr = self.get_correlation_between(signal.symbol, other_slot.symbol)
            if pair_corr is not None and pair_corr > 0.80:
                return False, (
                    f"High real correlation: {signal.symbol} <-> "
                    f"{other_slot.symbol} = {pair_corr:.2f} "
                    f"(threshold: 0.80). Diversification risk."
                )

        # 7c. Correlation crisis breaker
        if self._correlation_crisis_active:
            # During correlation crisis, only allow blue_chip positions
            if asset_class != "blue_chip":
                return False, (
                    f"Correlation crisis active — only blue_chip positions allowed. "
                    f"{signal.symbol} is {asset_class}"
                )

        # 8. Single-token exposure cap
        price = current_price or signal.entry_price or 0.0
        if proposed_size > 0 and price > 0 and self.total_value > 0:
            proposed_notional = proposed_size * price
            max_allowed = self.config.max_single_token_exposure_pct * self.total_value
            # Include existing positions for this token
            existing_notional = slot.capital_used
            if (existing_notional + proposed_notional) > max_allowed:
                return False, (
                    f"Token exposure would be {existing_notional + proposed_notional:,.0f} "
                    f"(limit {max_allowed:,.0f})"
                )

        # 9. Total portfolio positions
        total_positions = sum(
            s.risk_manager.open_count for s in self._slots.values()
            if s.risk_manager
        )
        if total_positions >= self.config.max_portfolio_positions:
            return False, (
                f"Portfolio positions at limit: {total_positions}/{self.config.max_portfolio_positions}"
            )

        return True, "OK"

    def open_position(
        self,
        signal: Signal,
        size: float,
    ) -> Optional[Position]:
        """
        Open a position in the appropriate token slot.

        Args:
            signal: Trading signal to execute.
            size: Position size (from RiskManager.calculate_position_size).

        Returns:
            The opened Position, or None if rejected.
        """
        # First check portfolio-level constraints
        price = signal.entry_price or 0.0
        allowed, reason = self.can_open_position(signal, size, price)
        if not allowed:
            return None

        slot = self._slots.get(signal.symbol)
        if slot is None or slot.risk_manager is None:
            return None

        # Open via the token's RiskManager
        position = slot.risk_manager.open_position(signal, size)

        # Update slot capital tracking
        notional = size * (signal.entry_price or 0.0)
        slot.capital_used += notional

        # Update money manager
        self.money_manager.open_position(signal, size, slot.asset_class)

        # Update signal counter
        slot.signals_generated += 1
        slot.last_signal_time = time.time()

        return position

    def close_position(
        self,
        symbol: str,
        exit_price: float,
    ) -> Optional[tuple[Position, float]]:
        """
        Close a position in a token slot.

        Returns:
            Tuple of (closed_position, pnl_amount) or None.
        """
        slot = self._slots.get(symbol)
        if slot is None or slot.risk_manager is None:
            return None

        result = slot.risk_manager.close_position(symbol, exit_price)
        if result is None:
            return None

        position, pnl = result

        # Update slot tracking
        entry_notional = position.size * position.entry_price
        slot.capital_used = max(0, slot.capital_used - entry_notional)
        slot.total_pnl += pnl
        slot.trades_completed += 1

        if pnl > 0:
            slot.wins += 1
        else:
            slot.losses += 1

        # Update peak capital for drawdown tracking
        current_slot_value = slot.capital_allocated + slot.total_pnl
        if current_slot_value > slot._peak_capital:
            slot._peak_capital = current_slot_value

        # Record return for correlation computation
        if slot.capital_allocated > 0:
            slot._returns.append(pnl / slot.capital_allocated)

        # Also close in money manager
        self.money_manager.close_position(symbol, exit_price)

        return position, pnl

    def update_position(self, symbol: str, current_price: float) -> Optional[Position]:
        """Update unrealized PnL for a position."""
        slot = self._slots.get(symbol)
        if slot is None or slot.risk_manager is None:
            return None
        return slot.risk_manager.update_position(symbol, current_price)

    def check_stop_loss(self, symbol: str, current_price: float) -> bool:
        """Check if a position's stop loss has been hit."""
        slot = self._slots.get(symbol)
        if slot is None or slot.risk_manager is None:
            return False
        return slot.risk_manager.check_stop_loss(symbol, current_price)

    def check_take_profit(self, symbol: str, current_price: float) -> bool:
        """Check if a position's take profit has been hit."""
        slot = self._slots.get(symbol)
        if slot is None or slot.risk_manager is None:
            return False
        return slot.risk_manager.check_take_profit(symbol, current_price)

    # -------------------------------------------------------------------
    # Regime & Rebalancing
    # -------------------------------------------------------------------

    def update_regime(self, symbol: str, regime: str) -> None:
        """Update the market regime for a token slot.

        If the regime change is significant, may trigger a rebalance.
        """
        slot = self._slots.get(symbol)
        if slot:
            old_regime = slot.current_regime
            slot.current_regime = regime

            # Check if rebalance needed
            if old_regime != regime and self.config.allocation_method == "REGIME_AWARE":
                # Regime changed — check if we should rebalance
                if self._should_rebalance():
                    self.rebalance(reason=f"Regime change: {symbol} {old_regime} -> {regime}")

    def _should_rebalance(self) -> bool:
        """Check if portfolio should be rebalanced."""
        if self.config.rebalance_interval_candles == 0:
            return False  # Manual rebalance only

        return self._candles_processed % self.config.rebalance_interval_candles == 0

    def rebalance(self, reason: str = "manual") -> RebalanceResult:
        """
        Rebalance the portfolio: adjust capital allocations across tokens.

        This is the key portfolio governance action. It:
        1. Evaluates current allocations vs ideal allocations
        2. Computes capital transfer instructions
        3. Updates slot capital allocations
        4. Records the rebalance in history

        Capital is NOT moved from open positions — only AVAILABLE capital
        is redistributed. Positions must be closed naturally.

        Args:
            reason: Why the rebalance was triggered.

        Returns:
            RebalanceResult with before/after allocations and moves.
        """
        active_slots = [s for s in self._slots.values() if s.is_active]
        if not active_slots:
            return RebalanceResult(
                timestamp=time.time(),
                regime=self._get_dominant_regime(),
                reason="No active slots",
            )

        # Record before state
        allocations_before = {
            s.symbol: s.capital_allocated for s in active_slots
        }

        # Compute new allocations
        # Use available capital (total - used in positions)
        available_total = self.total_value - sum(s.capital_used for s in active_slots)
        new_allocations = self._compute_allocations(active_slots, self.config.allocation_method)

        # Scale to available capital (don't touch used capital)
        # For slots with open positions, ensure allocation >= used
        capital_moves = []
        allocations_after = {}

        for slot in active_slots:
            ideal = new_allocations.get(slot.symbol, 0.0)
            current = slot.capital_allocated
            used = slot.capital_used

            # Never allocate less than what's already in use
            target = max(ideal, used)

            # Compute the move
            move = target - current
            if abs(move) > 1.0:  # Only move if > $1
                capital_moves.append({
                    "symbol": slot.symbol,
                    "from": round(current, 2),
                    "to": round(target, 2),
                    "move": round(move, 2),
                })

            allocations_after[slot.symbol] = target

            # Apply the new allocation
            slot.capital_allocated = target
            if slot.risk_manager:
                slot.risk_manager.capital = target - used  # Available capital for new trades

        result = RebalanceResult(
            timestamp=time.time(),
            regime=self._get_dominant_regime(),
            allocations_before=allocations_before,
            allocations_after=allocations_after,
            capital_moves=capital_moves,
            reason=reason,
        )

        self._rebalance_history.append(result)
        self._last_rebalance = time.time()

        return result

    # -------------------------------------------------------------------
    # Portfolio Properties
    # -------------------------------------------------------------------

    @property
    def total_value(self) -> float:
        """Total portfolio value across all slots.

        Computed as: initial_capital + total_realized_pnl + total_unrealized_pnl.
        This correctly sums across all positions in all token slots, avoiding
        the bug of only counting the first position per slot.
        """
        return self._initial_capital + self.total_realized_pnl + self.total_unrealized_pnl

    @property
    def total_realized_pnl(self) -> float:
        """Total realized PnL across all slots."""
        return sum(s.total_pnl for s in self._slots.values())

    @property
    def total_unrealized_pnl(self) -> float:
        """Total unrealized PnL across all slots."""
        total = 0.0
        for slot in self._slots.values():
            if slot.risk_manager:
                for pos in slot.risk_manager.open_positions:
                    if pos.direction == "LONG":
                        total += pos.unrealized_pnl_pct / 100.0 * pos.entry_price * pos.size
                    else:
                        total += pos.unrealized_pnl_pct / 100.0 * pos.entry_price * pos.size
        return total

    @property
    def total_return_pct(self) -> float:
        """Total portfolio return as fraction."""
        if self._initial_capital == 0:
            return 0.0
        return (self.total_value - self._initial_capital) / self._initial_capital

    @property
    def portfolio_exposure_pct(self) -> float:
        """Total portfolio exposure as fraction of value."""
        total_used = sum(s.capital_used for s in self._slots.values())
        if self.total_value == 0:
            return 0.0
        return total_used / self.total_value

    @property
    def total_open_positions(self) -> int:
        """Total number of open positions across all slots."""
        return sum(
            s.risk_manager.open_count if s.risk_manager else 0
            for s in self._slots.values()
        )

    @property
    def current_drawdown_pct(self) -> float:
        """Current portfolio drawdown from peak."""
        if self._initial_capital == 0:
            return 0.0
        # Use money manager's peak tracking
        return self.money_manager.current_drawdown

    @property
    def active_slots(self) -> list[TokenSlot]:
        """List of active token slots."""
        return [s for s in self._slots.values() if s.is_active]

    @property
    def all_slots(self) -> list[TokenSlot]:
        """List of all token slots."""
        return list(self._slots.values())

    # -------------------------------------------------------------------
    # Circuit Breakers & Kill Switch
    # -------------------------------------------------------------------

    def is_trading_allowed(self) -> bool:
        """Check if trading is allowed (no circuit breakers active)."""
        if self._kill_switch_active:
            return False

        # Check daily loss
        daily_pnl = sum(s.total_pnl for s in self._slots.values())
        if self._initial_capital > 0:
            daily_loss_pct = abs(min(0, daily_pnl)) / self._initial_capital
            if daily_loss_pct >= self.config.daily_loss_limit_pct:
                self._daily_loss_active = True
                return False

        # Check drawdown
        if self.current_drawdown_pct >= self.config.kill_switch_drawdown_pct:
            self._drawdown_active = True
            return False

        return True

    def circuit_breaker_status(self) -> dict:
        """Get status of all circuit breakers including correlation crisis."""
        daily_pnl = sum(s.total_pnl for s in self._slots.values())
        daily_loss_pct = abs(min(0, daily_pnl)) / self._initial_capital if self._initial_capital > 0 else 0

        return {
            "kill_switch": {
                "active": self._kill_switch_active,
                "threshold": self.config.kill_switch_drawdown_pct,
                "current_dd": self.current_drawdown_pct,
            },
            "daily_loss": {
                "active": self._daily_loss_active,
                "threshold": self.config.daily_loss_limit_pct,
                "current": daily_loss_pct,
            },
            "drawdown": {
                "active": self._drawdown_active,
                "threshold": self.config.kill_switch_drawdown_pct,
                "current": self.current_drawdown_pct,
            },
            "correlation_crisis": {
                "active": self._correlation_crisis_active,
                "description": "All tokens moving together — diversification benefits reduced",
            },
        }

    def activate_kill_switch(self) -> None:
        """Activate the portfolio kill switch. Closes all positions."""
        self._kill_switch_active = True
        console.print("[bold red]PORTFOLIO KILL SWITCH ACTIVATED[/bold red]")

        # Close all positions across all slots
        for slot in self._slots.values():
            if slot.risk_manager:
                for pos in list(slot.risk_manager.open_positions):
                    # Close at current SL (worst case)
                    exit_price = pos.sl_price if pos.sl_price > 0 else pos.entry_price
                    self.close_position(pos.symbol, exit_price)

    def deactivate_kill_switch(self) -> None:
        """Deactivate the kill switch (manual recovery)."""
        self._kill_switch_active = False
        self._daily_loss_active = False
        self._drawdown_active = False

    # -------------------------------------------------------------------
    # Analytics
    # -------------------------------------------------------------------

    def get_portfolio_summary(self) -> dict:
        """Get comprehensive portfolio summary."""
        slot_summaries = []
        for slot in self._slots.values():
            slot_summaries.append(slot.to_dict())

        # Per-asset-class breakdown
        class_breakdown: dict[str, dict] = {}
        for slot in self._slots.values():
            cls = slot.asset_class
            if cls not in class_breakdown:
                class_breakdown[cls] = {
                    "count": 0, "pnl": 0.0, "capital": 0.0,
                    "positions": 0, "wins": 0, "losses": 0,
                }
            class_breakdown[cls]["count"] += 1
            class_breakdown[cls]["pnl"] += slot.total_pnl
            class_breakdown[cls]["capital"] += slot.capital_allocated
            class_breakdown[cls]["positions"] += slot.risk_manager.open_count if slot.risk_manager else 0
            class_breakdown[cls]["wins"] += slot.wins
            class_breakdown[cls]["losses"] += slot.losses

        # Get real correlation data
        corr_result = self._correlation_engine.compute_matrix()
        diversification = self._correlation_engine.compute_diversification_score()

        return {
            "total_value": round(self.total_value, 2),
            "initial_capital": round(self._initial_capital, 2),
            "total_pnl": round(self.total_realized_pnl, 2),
            "total_pnl_pct": round(self.total_return_pct * 100, 2),
            "unrealized_pnl": round(self.total_unrealized_pnl, 2),
            "exposure_pct": round(self.portfolio_exposure_pct * 100, 1),
            "open_positions": self.total_open_positions,
            "max_positions": self.config.max_portfolio_positions,
            "active_slots": len(self.active_slots),
            "total_slots": len(self._slots),
            "dominant_regime": self._get_dominant_regime(),
            "allocation_method": self.config.allocation_method,
            "circuit_breakers": self.circuit_breaker_status(),
            "kill_switch": self._kill_switch_active,
            "drawdown_pct": round(self.current_drawdown_pct * 100, 2),
            "candles_processed": self._candles_processed,
            "rebalance_count": len(self._rebalance_history),
            "class_breakdown": class_breakdown,
            "slots": slot_summaries,
            # Real correlation data (v0.16.1 — was missing before)
            "correlation": {
                "regime": corr_result.regime.value,
                "avg_correlation": round(corr_result.avg_correlation, 4),
                "max_correlation": round(corr_result.max_correlation, 4),
                "window_size": corr_result.window_size,
                "method": corr_result.method.value,
                "diversification_score": diversification.get("score", 0.0),
                "diversification_rating": diversification.get("rating", "UNKNOWN"),
                "effective_positions": diversification.get("effective_positions", 0.0),
                "correlation_crisis_active": self._correlation_crisis_active,
            },
            "equity_curve_length": len(self._equity_curve),
        }

    def get_risk_report(self) -> dict:
        """Get detailed risk report for the portfolio.

        Uses the CrossTokenCorrelationEngine for correlation-adjusted VaR,
        replacing the previous simple weighted-average approach.
        """
        # Portfolio-level VaR estimation using REAL correlation matrix
        total_vol = self._estimate_portfolio_volatility()

        # Use correlation engine for more accurate VaR
        corr_result = self._correlation_engine.compute_matrix()
        weights = {}
        volatilities = {}
        total_cap = sum(s.capital_allocated for s in self._slots.values())
        for s in self._slots.values():
            if total_cap > 0:
                weights[s.symbol] = s.capital_allocated / total_cap
            else:
                weights[s.symbol] = 1.0 / max(len(self._slots), 1)
            volatilities[s.symbol] = self._slot_volatility(s)

        # Correlation-adjusted portfolio variance
        port_var = self._correlation_engine.compute_portfolio_variance(weights, volatilities)
        port_vol = np.sqrt(port_var) if port_var > 0 else total_vol

        var_95 = self.total_value * 1.645 * port_vol  # 1-day 95% VaR
        var_99 = self.total_value * 2.326 * port_vol  # 1-day 99% VaR

        # HHI (Herfindahl-Hirschman Index) for concentration
        if total_cap > 0:
            weight_list = [s.capital_allocated / total_cap for s in self._slots.values()]
            hhi = sum(w * w for w in weight_list)
        else:
            hhi = 0.0

        # Diversification ratio
        if port_vol > 0:
            avg_slot_vol = np.mean([
                self._slot_volatility(s) for s in self._slots.values()
            ]) if self._slots else 0.0
            diversification_ratio = avg_slot_vol / port_vol if port_vol > 0 else 1.0
        else:
            diversification_ratio = 1.0

        return {
            "portfolio_volatility": round(port_vol * 100, 2),
            "var_95_1d": round(var_95, 2),
            "var_99_1d": round(var_99, 2),
            "cvar_estimate": round(var_95 * 1.5, 2),  # Approximate CVaR
            "hhi_concentration": round(hhi, 3),
            "diversification_ratio": round(diversification_ratio, 2),
            "max_drawdown_pct": round(self.current_drawdown_pct * 100, 2),
            "correlation_regime": corr_result.regime.value,
            "avg_correlation": round(corr_result.avg_correlation, 4),
            "exposure_breakdown": {
                s.symbol: round(s.capital_used / self.total_value * 100, 1)
                for s in self._slots.values()
                if self.total_value > 0 and s.capital_used > 0
            },
            "class_concentration": {
                cls: round(data["capital"] / total_cap * 100, 1)
                for cls, data in self.get_portfolio_summary().get("class_breakdown", {}).items()
                if total_cap > 0
            },
        }

    def _estimate_portfolio_volatility(self) -> float:
        """Estimate portfolio-level daily volatility."""
        if not self._slots:
            return 0.0

        vols = [self._slot_volatility(s) for s in self._slots.values()]
        if not vols:
            return 0.0

        # Simple weighted average (ignoring correlation for speed)
        total_cap = sum(s.capital_allocated for s in self._slots.values())
        if total_cap == 0:
            return np.mean(vols)

        weighted = sum(
            s.capital_allocated / total_cap * self._slot_volatility(s)
            for s in self._slots.values()
        )
        return weighted

    def _slot_volatility(self, slot: TokenSlot) -> float:
        """Estimate daily volatility for a token slot."""
        if len(slot._returns) > 5:
            return float(np.std(slot._returns))
        # Use asset class proxy
        vol_proxies = {
            "blue_chip": 0.025,
            "large_cap": 0.035,
            "mid_cap": 0.045,
            "defi": 0.055,
            "meme": 0.08,
            "new_launch": 0.10,
        }
        return vol_proxies.get(slot.asset_class, 0.04)

    # -------------------------------------------------------------------
    # Display
    # -------------------------------------------------------------------

    def display_summary(self) -> None:
        """Display a rich portfolio summary table."""
        summary = self.get_portfolio_summary()

        # Portfolio overview
        pnl_color = "green" if summary["total_pnl"] >= 0 else "red"
        console.print(Panel(
            f"[bold]Portfolio Value:[/bold] ${summary['total_value']:,.2f}  "
            f"[{pnl_color}]PnL: ${summary['total_pnl']:,.2f} ({summary['total_pnl_pct']:.1f}%)[/{pnl_color}]  "
            f"[bold]Exposure:[/bold] {summary['exposure_pct']:.1f}%  "
            f"[bold]Positions:[/bold] {summary['open_positions']}/{summary['max_positions']}  "
            f"[bold]Regime:[/bold] {summary['dominant_regime']}",
            title="[bold cyan]PPMT Portfolio Manager[/bold cyan]",
            border_style="cyan",
        ))

        # Token slots table
        table = Table(title="Token Slots", show_lines=True)
        table.add_column("Token", style="bold")
        table.add_column("Class", style="dim")
        table.add_column("Capital", justify="right")
        table.add_column("Used", justify="right")
        table.add_column("PnL", justify="right")
        table.add_column("WR", justify="right")
        table.add_column("Trades", justify="right")
        table.add_column("Regime", style="dim")
        table.add_column("Active", justify="center")

        for slot_data in summary["slots"]:
            pnl_str = f"${slot_data['total_pnl']:,.2f}"
            if slot_data['total_pnl'] < 0:
                pnl_str = f"[red]{pnl_str}[/red]"
            elif slot_data['total_pnl'] > 0:
                pnl_str = f"[green]{pnl_str}[/green]"

            active_str = "[green]ON[/green]" if slot_data["is_active"] else "[red]OFF[/red]"

            table.add_row(
                slot_data["symbol"],
                slot_data["asset_class"],
                f"${slot_data['capital_allocated']:,.0f}",
                f"${slot_data['capital_used']:,.0f}",
                pnl_str,
                f"{slot_data['win_rate']:.0%}",
                str(slot_data["trades_completed"]),
                slot_data["current_regime"],
                active_str,
            )

        console.print(table)

        # Circuit breaker status
        breakers = summary["circuit_breakers"]
        for name, status in breakers.items():
            if status["active"]:
                console.print(f"[bold red]  BREAKER: {name} active![/bold red]")

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------

    def save_state(self, filepath: Optional[str] = None) -> None:
        """Save portfolio state to JSON file."""
        path = filepath or self.config.state_file
        if not path:
            return

        state = {
            "version": "0.16.0",
            "timestamp": time.time(),
            "config": {
                "initial_capital": self.config.initial_capital,
                "tokens": self.config.tokens,
                "allocation_method": self.config.allocation_method,
                "max_portfolio_positions": self.config.max_portfolio_positions,
                "max_positions_per_token": self.config.max_positions_per_token,
            },
            "slots": {
                sym: s.to_dict() for sym, s in self._slots.items()
            },
            "total_value": round(self.total_value, 2),
            "total_pnl": round(self.total_realized_pnl, 2),
            "kill_switch_active": self._kill_switch_active,
            "candles_processed": self._candles_processed,
        }

        with open(path, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def load_state(self, filepath: Optional[str] = None) -> bool:
        """Load portfolio state from JSON file."""
        path = filepath or self.config.state_file
        if not path or not Path(path).exists():
            return False

        try:
            with open(path) as f:
                state = json.load(f)

            # Restore basic state
            self._kill_switch_active = state.get("kill_switch_active", False)
            self._candles_processed = state.get("candles_processed", 0)

            # Restore slots
            for sym, slot_data in state.get("slots", {}).items():
                if sym in self._slots:
                    slot = self._slots[sym]
                    slot.total_pnl = slot_data.get("total_pnl", 0.0)
                    slot.wins = slot_data.get("wins", 0)
                    slot.losses = slot_data.get("losses", 0)
                    slot.trades_completed = slot_data.get("trades_completed", 0)
                    slot.signals_generated = slot_data.get("signals_generated", 0)
                    slot.is_active = slot_data.get("is_active", True)
                    slot.current_regime = slot_data.get("current_regime", "UNKNOWN")

            return True
        except (json.JSONDecodeError, OSError):
            return False
