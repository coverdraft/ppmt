"""Risk management for PPMT trading."""

from ppmt.risk.manager import RiskManager, Position, RiskConfig
from ppmt.risk.money_manager import MoneyManager, MoneyManagerConfig, PortfolioSnapshot
from ppmt.risk.position_sizing import AdvancedPositionSizer, PositionSizeResult
from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig, TokenSlot
from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine, CorrelationMatrixResult, CorrelationMethod
from ppmt.risk.regime_allocator import RegimeAwareAllocator, RegimeProfile, AllocationResult
from ppmt.risk.portfolio_backtester import PortfolioBacktester, PortfolioBacktestConfig, PortfolioBacktestResult

__all__ = [
    # Per-trade risk
    "RiskManager",
    "Position",
    "RiskConfig",
    # Per-portfolio money management
    "MoneyManager",
    "MoneyManagerConfig",
    "PortfolioSnapshot",
    # Advanced position sizing
    "AdvancedPositionSizer",
    "PositionSizeResult",
    # Multi-token portfolio management (v0.16.0)
    "PortfolioManager",
    "PortfolioConfig",
    "TokenSlot",
    # Cross-token correlation (v0.16.0)
    "CrossTokenCorrelationEngine",
    "CorrelationMatrixResult",
    "CorrelationMethod",
    # Regime-aware allocation (v0.16.0)
    "RegimeAwareAllocator",
    "RegimeProfile",
    "AllocationResult",
    # Portfolio backtesting (v0.16.0)
    "PortfolioBacktester",
    "PortfolioBacktestConfig",
    "PortfolioBacktestResult",
]
