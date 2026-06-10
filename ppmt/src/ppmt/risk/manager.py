"""
Risk Manager Module

Centralized risk management for live and backtest trading.
Handles stop-loss, take-profit, position limits, and drawdown controls.
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


class RiskAction(Enum):
    ALLOW = "allow"
    REDUCE = "reduce"
    BLOCK = "block"


@dataclass
class RiskAssessment:
    """Result of risk assessment for a proposed trade."""
    action: RiskAction
    max_position_pct: float
    reason: str
    current_drawdown_pct: float
    daily_trades: int
    max_daily_trades: int


class RiskManager:
    """
    Risk manager with drawdown-based circuit breakers.
    
    Prevents the catastrophic drawdowns seen in ETH (-218%) and SOL (-194%)
    by implementing:
    - Hard drawdown stop at 30%
    - Position reduction at 15% drawdown
    - Daily trade limits
    - Maximum consecutive losses stop
    - Equity curve monitoring
    """

    def __init__(
        self,
        max_drawdown_pct: float = 30.0,
        reduction_drawdown_pct: float = 15.0,
        max_daily_trades: int = 10,
        max_consecutive_losses: int = 5,
        max_position_pct: float = 5.0,
        initial_equity: float = 10000.0,
    ):
        self.max_drawdown_pct = max_drawdown_pct
        self.reduction_drawdown_pct = reduction_drawdown_pct
        self.max_daily_trades = max_daily_trades
        self.max_consecutive_losses = max_consecutive_losses
        self.max_position_pct = max_position_pct
        
        self.peak_equity = initial_equity
        self.current_equity = initial_equity
        self.daily_trades = 0
        self.consecutive_losses = 0
        self.equity_history: List[float] = [initial_equity]
        self.is_halted = False

    def assess_trade(self, proposed_size_pct: float) -> RiskAssessment:
        """Assess whether a proposed trade should be allowed."""
        if self.is_halted:
            return RiskAssessment(
                action=RiskAction.BLOCK,
                max_position_pct=0,
                reason="Trading halted due to max drawdown breach",
                current_drawdown_pct=self._current_dd(),
                daily_trades=self.daily_trades,
                max_daily_trades=self.max_daily_trades,
            )
        
        dd = self._current_dd()
        
        # Drawdown circuit breaker
        if dd >= self.max_drawdown_pct:
            self.is_halted = True
            return RiskAssessment(
                action=RiskAction.BLOCK,
                max_position_pct=0,
                reason=f"Max drawdown breached: {dd:.1f}% >= {self.max_drawdown_pct}%",
                current_drawdown_pct=dd,
                daily_trades=self.daily_trades,
                max_daily_trades=self.max_daily_trades,
            )
        
        # Daily trade limit
        if self.daily_trades >= self.max_daily_trades:
            return RiskAssessment(
                action=RiskAction.BLOCK,
                max_position_pct=0,
                reason=f"Daily trade limit reached: {self.daily_trades}/{self.max_daily_trades}",
                current_drawdown_pct=dd,
                daily_trades=self.daily_trades,
                max_daily_trades=self.max_daily_trades,
            )
        
        # Consecutive losses
        if self.consecutive_losses >= self.max_consecutive_losses:
            return RiskAssessment(
                action=RiskAction.REDUCE,
                max_position_pct=self.max_position_pct * 0.25,
                reason=f"Consecutive losses: {self.consecutive_losses}",
                current_drawdown_pct=dd,
                daily_trades=self.daily_trades,
                max_daily_trades=self.max_daily_trades,
            )
        
        # Drawdown reduction
        if dd >= self.reduction_drawdown_pct:
            reduction = 1.0 - (dd - self.reduction_drawdown_pct) / (self.max_drawdown_pct - self.reduction_drawdown_pct)
            reduction = max(0.25, reduction)
            return RiskAssessment(
                action=RiskAction.REDUCE,
                max_position_pct=self.max_position_pct * reduction,
                reason=f"Drawdown reduction: {dd:.1f}% DD, position reduced to {reduction:.0%}",
                current_drawdown_pct=dd,
                daily_trades=self.daily_trades,
                max_daily_trades=self.max_daily_trades,
            )
        
        return RiskAssessment(
            action=RiskAction.ALLOW,
            max_position_pct=min(proposed_size_pct, self.max_position_pct),
            reason="Trade allowed",
            current_drawdown_pct=dd,
            daily_trades=self.daily_trades,
            max_daily_trades=self.max_daily_trades,
        )

    def record_trade(self, pnl: float):
        """Record trade result for risk tracking."""
        self.current_equity += pnl
        self.peak_equity = max(self.peak_equity, self.current_equity)
        self.equity_history.append(self.current_equity)
        self.daily_trades += 1
        
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def reset_daily(self):
        """Reset daily counters."""
        self.daily_trades = 0

    def _current_dd(self) -> float:
        """Calculate current drawdown percentage."""
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity * 100
