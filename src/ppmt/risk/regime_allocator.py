"""
Regime-Aware Allocator - Dynamic Capital Allocation by Market Regime for PPMT v0.16.0

Adjusts portfolio capital allocation based on the current market regime.
Each regime has different optimal allocation strategies:

  TRENDING_UP:   Concentrate in blue chips + large caps (they trend best)
  TRENDING_DOWN: Defensive — blue chips only, reduced position sizes
  RANGING:       Spread across mid-cap + DeFi (range-bound strategies work)
  VOLATILE:      Ultra-conservative — minimal exposure, tight stops
  CRISIS:        Cash-heavy, only strongest patterns get capital

The allocator also considers:
  - Token-specific performance (win rate, Sharpe)
  - Correlation regime (normal vs crisis correlations)
  - Drawdown state (reduce allocation during drawdowns)
  - PPMT pattern quality per token (better patterns → more capital)

Architecture:
  ┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐
  │ Regime       │───>│ RegimeAware      │───>│ Portfolio        │
  │ Detector     │    │ Allocator        │    │ Manager          │
  │ (per token)  │    │                  │    │ (applies alloc)  │
  └──────────────┘    │ Input:           │    └──────────────────┘
                      │ • Regime per token│
  ┌──────────────┐    │ • Correlation mat│    ┌──────────────────┐
  │ Cross-Token  │───>│ • Token perf     │───>│ Rebalance        │
  │ Corr Engine  │    │ • Pattern quality│    │ Decision         │
  └──────────────┘    │ • Drawdown state │    └──────────────────┘
                      └──────────────────┘
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from rich.console import Console

from ppmt.data.classifier import AssetClassifier

console = Console()


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class RegimeProfile:
    """Allocation profile for a specific market regime.

    Defines how capital should be distributed across asset classes
    when the portfolio is in this regime.

    Attributes:
        name: Regime name (e.g., 'TRENDING_UP').
        class_weights: Target weight for each asset class (must sum to ~1.0).
        position_size_multiplier: Global position size scaling.
        max_portfolio_exposure: Maximum total portfolio exposure.
        min_cash_reserve: Minimum cash to keep unallocated.
        description: Human-readable description of this regime profile.
    """
    name: str
    class_weights: dict = field(default_factory=dict)
    position_size_multiplier: float = 1.0
    max_portfolio_exposure: float = 0.80
    min_cash_reserve: float = 0.10
    description: str = ""


# Pre-defined regime profiles
REGIME_PROFILES: dict[str, RegimeProfile] = {
    "TRENDING_UP": RegimeProfile(
        name="TRENDING_UP",
        class_weights={
            "blue_chip": 0.40,
            "large_cap": 0.30,
            "mid_cap": 0.15,
            "defi": 0.08,
            "meme": 0.05,
            "new_launch": 0.02,
        },
        position_size_multiplier=1.2,
        max_portfolio_exposure=0.85,
        min_cash_reserve=0.10,
        description="Bull market: concentrate in blue chips + large caps",
    ),
    "TRENDING_DOWN": RegimeProfile(
        name="TRENDING_DOWN",
        class_weights={
            "blue_chip": 0.55,
            "large_cap": 0.25,
            "mid_cap": 0.10,
            "defi": 0.05,
            "meme": 0.03,
            "new_launch": 0.02,
        },
        position_size_multiplier=0.6,
        max_portfolio_exposure=0.60,
        min_cash_reserve=0.25,
        description="Bear market: defensive, blue chips only, reduced sizes",
    ),
    "RANGING": RegimeProfile(
        name="RANGING",
        class_weights={
            "blue_chip": 0.25,
            "large_cap": 0.25,
            "mid_cap": 0.20,
            "defi": 0.15,
            "meme": 0.10,
            "new_launch": 0.05,
        },
        position_size_multiplier=1.0,
        max_portfolio_exposure=0.75,
        min_cash_reserve=0.15,
        description="Range-bound: spread across mid-cap + DeFi",
    ),
    "VOLATILE": RegimeProfile(
        name="VOLATILE",
        class_weights={
            "blue_chip": 0.50,
            "large_cap": 0.25,
            "mid_cap": 0.10,
            "defi": 0.08,
            "meme": 0.05,
            "new_launch": 0.02,
        },
        position_size_multiplier=0.4,
        max_portfolio_exposure=0.50,
        min_cash_reserve=0.30,
        description="Volatile: ultra-conservative, minimal exposure",
    ),
    "CRISIS": RegimeProfile(
        name="CRISIS",
        class_weights={
            "blue_chip": 0.70,
            "large_cap": 0.20,
            "mid_cap": 0.05,
            "defi": 0.03,
            "meme": 0.01,
            "new_launch": 0.01,
        },
        position_size_multiplier=0.25,
        max_portfolio_exposure=0.35,
        min_cash_reserve=0.50,
        description="Crisis: cash-heavy, only strongest patterns",
    ),
    "UNKNOWN": RegimeProfile(
        name="UNKNOWN",
        class_weights={
            "blue_chip": 0.30,
            "large_cap": 0.25,
            "mid_cap": 0.15,
            "defi": 0.10,
            "meme": 0.08,
            "new_launch": 0.12,
        },
        position_size_multiplier=0.8,
        max_portfolio_exposure=0.70,
        min_cash_reserve=0.20,
        description="Unknown regime: balanced with caution",
    ),
}


@dataclass
class AllocationInstruction:
    """A single capital allocation instruction for a token.

    Attributes:
        symbol: Token symbol.
        target_weight: Target portfolio weight (0-1).
        target_capital: Target capital allocation (USD).
        current_capital: Current capital allocation (USD).
        capital_delta: Difference between target and current.
        reasoning: Why this allocation was chosen.
    """
    symbol: str
    target_weight: float
    target_capital: float
    current_capital: float
    capital_delta: float
    reasoning: str = ""


@dataclass
class AllocationResult:
    """Result of a regime-aware allocation computation.

    Attributes:
        regime: The regime used for this allocation.
        instructions: Per-token allocation instructions.
        total_allocated: Total capital allocated across tokens.
        cash_reserve: Capital kept as cash reserve.
        position_size_multiplier: Global position size multiplier.
        max_exposure: Maximum portfolio exposure for this regime.
        timestamp: When the allocation was computed.
    """
    regime: str
    instructions: list = field(default_factory=list)
    total_allocated: float = 0.0
    cash_reserve: float = 0.0
    position_size_multiplier: float = 1.0
    max_exposure: float = 0.80
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


# ---------------------------------------------------------------------------
# RegimeAwareAllocator
# ---------------------------------------------------------------------------

class RegimeAwareAllocator:
    """
    Dynamic Capital Allocator driven by Market Regime for PPMT v0.16.0.

    The allocator takes the current market regime and produces optimal
    capital allocations across all portfolio tokens. It considers:

    1. **Regime Profile**: Pre-defined allocation targets per asset class
    2. **Token Performance**: Winning tokens get more, losing tokens get less
    3. **Pattern Quality**: Tokens with better PPMT patterns get more capital
    4. **Correlation State**: Crisis correlations → more conservative
    5. **Drawdown State**: Deep drawdown → reduce all allocations

    Usage:
        allocator = RegimeAwareAllocator()

        # Compute allocation for current regime
        result = allocator.allocate(
            regime="TRENDING_UP",
            tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"],
            total_capital=50_000.0,
            current_allocations={"BTC/USDT": 15_000, ...},
            token_performance={"BTC/USDT": {"win_rate": 0.65, "pnl_pct": 0.12}, ...},
        )

        # Apply the allocation
        for instr in result.instructions:
            print(f"{instr.symbol}: {instr.current_capital} -> {instr.target_capital}")
    """

    def __init__(
        self,
        custom_profiles: Optional[dict[str, RegimeProfile]] = None,
        performance_weight: float = 0.25,
        quality_weight: float = 0.20,
        drawdown_penalty: float = 2.0,
    ):
        """
        Initialize the allocator.

        Args:
            custom_profiles: Override or add regime profiles.
            performance_weight: How much token performance affects allocation (0-1).
            quality_weight: How much pattern quality affects allocation (0-1).
            drawdown_penalty: How strongly drawdown reduces allocation.
        """
        self.profiles = dict(REGIME_PROFILES)
        if custom_profiles:
            self.profiles.update(custom_profiles)

        self._performance_weight = performance_weight
        self._quality_weight = quality_weight
        self._drawdown_penalty = drawdown_penalty
        self._classifier = AssetClassifier()

        # Allocation history for smooth transitions
        self._prev_result: Optional[AllocationResult] = None

    def allocate(
        self,
        regime: str,
        tokens: list[str],
        total_capital: float,
        current_allocations: Optional[dict[str, float]] = None,
        token_performance: Optional[dict[str, dict]] = None,
        pattern_quality: Optional[dict[str, float]] = None,
        correlation_regime: str = "NORMAL",
        portfolio_drawdown_pct: float = 0.0,
        smooth_transition: bool = True,
        max_single_change_pct: float = 0.15,
    ) -> AllocationResult:
        """
        Compute regime-aware capital allocation.

        Args:
            regime: Current market regime.
            tokens: List of token symbols in the portfolio.
            total_capital: Total portfolio capital.
            current_allocations: Current capital per token.
            token_performance: Performance data per token.
            pattern_quality: PPMT pattern quality per token (0-1).
            correlation_regime: Correlation regime (NORMAL, ELEVATED, CRISIS).
            portfolio_drawdown_pct: Current portfolio drawdown (0-1).
            smooth_transition: Whether to limit per-rebalance changes.
            max_single_change_pct: Maximum change per token per rebalance.

        Returns:
            AllocationResult with per-token instructions.
        """
        current_allocations = current_allocations or {}
        token_performance = token_performance or {}
        pattern_quality = pattern_quality or {}

        # Get the regime profile
        profile = self.profiles.get(regime, self.profiles["UNKNOWN"])

        # Adjust for correlation regime
        if correlation_regime == "CRISIS":
            # Crisis correlations → even more conservative
            profile = self._make_conservative(profile)

        # Adjust for portfolio drawdown
        dd_multiplier = max(0.1, 1.0 - portfolio_drawdown_pct * self._drawdown_penalty)

        # Classify tokens
        token_classes = {}
        for t in tokens:
            info = self._classifier.classify(t)
            token_classes[t] = info.asset_class

        # Compute raw allocations based on regime profile
        raw_weights = {}
        for t in tokens:
            cls = token_classes.get(t, "mid_cap")
            base_weight = profile.class_weights.get(cls, 0.05)

            # Adjust by token performance
            perf = token_performance.get(t, {})
            perf_adjustment = self._performance_adjustment(perf)
            base_weight *= perf_adjustment

            # Adjust by pattern quality
            quality = pattern_quality.get(t, 0.5)
            quality_adjustment = 0.5 + quality  # Range: 0.5 to 1.5
            quality_adjustment = (
                (1 - self._quality_weight) + self._quality_weight * quality_adjustment
            )
            base_weight *= quality_adjustment

            # Apply drawdown reduction
            base_weight *= dd_multiplier

            raw_weights[t] = max(0.01, base_weight)  # Minimum 1%

        # Normalize weights to sum to 1.0
        total_weight = sum(raw_weights.values())
        if total_weight > 0:
            norm_weights = {t: w / total_weight for t, w in raw_weights.items()}
        else:
            per_token = 1.0 / len(tokens) if tokens else 0.0
            norm_weights = {t: per_token for t in tokens}

        # Apply cash reserve (don't allocate everything)
        investable_capital = total_capital * (1 - profile.min_cash_reserve)

        # Compute target capital per token
        instructions = []
        for t in tokens:
            target_cap = norm_weights[t] * investable_capital
            current_cap = current_allocations.get(t, 0.0)
            delta = target_cap - current_cap

            # Smooth transition: limit max change per rebalance
            if smooth_transition and current_cap > 0 and self._prev_result:
                max_change = current_cap * max_single_change_pct
                delta = max(-max_change, min(max_change, delta))
                target_cap = current_cap + delta

            reasoning_parts = []
            cls = token_classes.get(t, "mid_cap")
            reasoning_parts.append(f"class={cls}")
            reasoning_parts.append(f"regime={regime}")

            perf = token_performance.get(t, {})
            if perf.get("win_rate", 0) > 0.55:
                reasoning_parts.append("strong_performer")
            elif perf.get("win_rate", 0) < 0.40:
                reasoning_parts.append("weak_performer")

            quality = pattern_quality.get(t, 0)
            if quality > 0.7:
                reasoning_parts.append("high_quality_patterns")
            elif quality < 0.3:
                reasoning_parts.append("low_quality_patterns")

            instructions.append(AllocationInstruction(
                symbol=t,
                target_weight=round(norm_weights[t], 4),
                target_capital=round(target_cap, 2),
                current_capital=round(current_cap, 2),
                capital_delta=round(delta, 2),
                reasoning=", ".join(reasoning_parts),
            ))

        result = AllocationResult(
            regime=regime,
            instructions=instructions,
            total_allocated=round(investable_capital, 2),
            cash_reserve=round(total_capital - investable_capital, 2),
            position_size_multiplier=profile.position_size_multiplier,
            max_exposure=profile.max_portfolio_exposure,
        )

        self._prev_result = result
        return result

    def _performance_adjustment(self, perf: dict) -> float:
        """Compute performance-based allocation adjustment.

        Winning tokens get up to 1.5x, losing tokens get down to 0.5x.
        """
        win_rate = perf.get("win_rate", 0.5)
        pnl_pct = perf.get("pnl_pct", 0.0)
        sharpe = perf.get("sharpe", 0.0)
        trades = perf.get("trades", 0)

        # Not enough trades to evaluate
        if trades < 3:
            return 1.0

        # Win rate component (0.7 to 1.3)
        wr_adj = 0.7 + 0.6 * win_rate

        # PnL component (0.8 to 1.5 based on profitability)
        pnl_adj = 1.0
        if pnl_pct > 0.10:
            pnl_adj = 1.3
        elif pnl_pct > 0.05:
            pnl_adj = 1.1
        elif pnl_pct < -0.10:
            pnl_adj = 0.7
        elif pnl_pct < -0.05:
            pnl_adj = 0.85

        # Sharpe component (0.8 to 1.3)
        sharpe_adj = 1.0
        if sharpe > 1.5:
            sharpe_adj = 1.2
        elif sharpe > 0.5:
            sharpe_adj = 1.1
        elif sharpe < -0.5:
            sharpe_adj = 0.8
        elif sharpe < 0:
            sharpe_adj = 0.9

        # Blend with performance_weight
        raw = wr_adj * pnl_adj * sharpe_adj
        adjusted = (1 - self._performance_weight) + self._performance_weight * raw
        return max(0.3, min(1.8, adjusted))

    def _make_conservative(self, profile: RegimeProfile) -> RegimeProfile:
        """Make a profile more conservative for crisis correlation regimes."""
        conservative_weights = {}
        for cls, weight in profile.class_weights.items():
            if cls == "blue_chip":
                conservative_weights[cls] = weight * 1.3
            elif cls == "meme":
                conservative_weights[cls] = weight * 0.3
            elif cls == "new_launch":
                conservative_weights[cls] = weight * 0.2
            else:
                conservative_weights[cls] = weight * 0.8

        # Normalize
        total = sum(conservative_weights.values())
        if total > 0:
            conservative_weights = {k: v / total for k, v in conservative_weights.items()}

        return RegimeProfile(
            name=f"{profile.name}_CRISIS_CORR",
            class_weights=conservative_weights,
            position_size_multiplier=profile.position_size_multiplier * 0.7,
            max_portfolio_exposure=profile.max_portfolio_exposure * 0.7,
            min_cash_reserve=min(0.50, profile.min_cash_reserve * 2),
            description=f"{profile.description} (crisis correlation adjustment)",
        )

    def get_regime_summary(self, regime: str) -> dict:
        """Get a summary of the allocation profile for a regime."""
        profile = self.profiles.get(regime, self.profiles["UNKNOWN"])
        return {
            "regime": profile.name,
            "class_weights": profile.class_weights,
            "position_size_multiplier": profile.position_size_multiplier,
            "max_portfolio_exposure": profile.max_portfolio_exposure,
            "min_cash_reserve": profile.min_cash_reserve,
            "description": profile.description,
        }

    def display_allocation(self, result: AllocationResult) -> None:
        """Display a rich allocation result table."""
        from rich.table import Table

        table = Table(title=f"Regime-Aware Allocation ({result.regime})")
        table.add_column("Token", style="bold")
        table.add_column("Weight", justify="right")
        table.add_column("Target", justify="right")
        table.add_column("Current", justify="right")
        table.add_column("Delta", justify="right")
        table.add_column("Reasoning", style="dim")

        for instr in result.instructions:
            delta_color = "green" if instr.capital_delta >= 0 else "red"
            delta_str = f"[{delta_color}]{instr.capital_delta:+,.0f}[/{delta_color}]"

            table.add_row(
                instr.symbol,
                f"{instr.target_weight:.1%}",
                f"${instr.target_capital:,.0f}",
                f"${instr.current_capital:,.0f}",
                delta_str,
                instr.reasoning,
            )

        console.print(table)
        console.print(
            f"  Allocated: ${result.total_allocated:,.0f}  "
            f"Reserve: ${result.cash_reserve:,.0f}  "
            f"Size Mult: {result.position_size_multiplier:.1f}x  "
            f"Max Exposure: {result.max_exposure:.0%}"
        )
