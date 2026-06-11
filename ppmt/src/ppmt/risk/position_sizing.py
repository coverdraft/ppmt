"""
Position Sizing Module

Improved position sizing that fixes the Max DD > 100% issue for ETH/SOL.
Uses Kelly Criterion, regime awareness, and drawdown-based reduction.
"""

import numpy as np
from typing import Optional
from dataclasses import dataclass


@dataclass
class PositionSizeResult:
    """Result of position sizing calculation."""
    size_pct: float          # Position size as % of equity
    size_abs: float          # Position size in absolute terms
    risk_amount: float       # Amount at risk
    kelly_fraction: float    # Kelly criterion fraction
    regime_mult: float       # Regime multiplier
    dd_mult: float           # Drawdown multiplier


class AdvancedPositionSizer:
    """
    Advanced position sizing with multiple safety mechanisms:
    
    1. Kelly Criterion for base sizing
    2. Regime-based scaling (reduce in volatile markets)
    3. Drawdown-based reduction (reduce after losses)
    4. Hard cap on maximum position size
    5. Minimum position size floor
    
    This fixes the ETH/SOL Max DD > 100% issue by:
    - Capping position size at 5% of equity
    - Reducing size by 50%+ in volatile regimes
    - Halving position after 20% drawdown
    - Never allowing more than 2x leverage equivalent
    """

    def __init__(
        self,
        max_position_pct: float = 0.05,
        min_position_pct: float = 0.005,
        kelly_fraction: float = 0.25,  # Quarter-Kelly for safety
        dd_reduction_factor: float = 3.0,
        vol_lookback: int = 20,
    ):
        self.max_position_pct = max_position_pct
        self.min_position_pct = min_position_pct
        self.kelly_fraction = kelly_fraction
        self.dd_reduction_factor = dd_reduction_factor
        self.vol_lookback = vol_lookback
        self.peak_equity = 0.0

    def calculate(
        self,
        equity: float,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        regime: str = "ranging",
        confidence: float = 0.5,
        recent_volatility: Optional[float] = None,
    ) -> PositionSizeResult:
        """
        Calculate position size with all safety mechanisms.
        """
        # Update peak equity
        self.peak_equity = max(self.peak_equity, equity)
        
        # 1. Kelly Criterion
        if avg_loss_pct > 0 and win_rate > 0:
            w = win_rate
            r = avg_win_pct / avg_loss_pct
            kelly = w - (1 - w) / r
            kelly = max(0, kelly)
        else:
            kelly = 0.01
        
        # Use fractional Kelly
        base_size = kelly * self.kelly_fraction
        
        # 2. Confidence scaling
        if confidence > 0.5:
            conf_mult = 0.5 + (confidence - 0.5)
        else:
            conf_mult = 0.5
        
        # 3. Regime scaling
        regime_mults = {
            "trending_up": 1.2,
            "trending_down": 0.6,
            "ranging": 1.0,
            "volatile": 0.4,
        }
        regime_mult = regime_mults.get(regime, 1.0)
        
        # 4. Volatility scaling (if provided)
        vol_mult = 1.0
        if recent_volatility is not None:
            # High vol → reduce size
            if recent_volatility > 0.8:
                vol_mult = 0.3
            elif recent_volatility > 0.5:
                vol_mult = 0.6
        
        # 5. Drawdown reduction
        if self.peak_equity > 0:
            current_dd = (self.peak_equity - equity) / self.peak_equity
        else:
            current_dd = 0.0
        dd_mult = max(0.05, 1.0 - current_dd * self.dd_reduction_factor)
        
        # Combine
        size_pct = base_size * conf_mult * regime_mult * vol_mult * dd_mult
        
        # Hard cap
        size_pct = min(size_pct, self.max_position_pct)
        size_pct = max(size_pct, self.min_position_pct)
        
        size_abs = equity * size_pct
        risk_amount = size_abs * avg_loss_pct / 100
        
        return PositionSizeResult(
            size_pct=size_pct * 100,
            size_abs=size_abs,
            risk_amount=risk_amount,
            kelly_fraction=kelly,
            regime_mult=regime_mult,
            dd_mult=dd_mult,
        )

    def reset(self):
        """Reset peak equity tracking."""
        self.peak_equity = 0.0
