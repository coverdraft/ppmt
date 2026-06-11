"""
Block Lifecycle Metadata - The V3 Innovation

Each Trie node carries 14+ metadata fields that encode:
- When to enter (trigger_candle)
- How long the pattern should last (remaining_candles)
- Expected move and risk parameters
- Forward continuation and backward context
- Historical statistics
- Regime context (which market regimes this pattern was observed in)

This metadata makes PPMT autonomous -- all entry/exit/SL/TP decisions
emerge directly from the Trie without external indicators.

v0.10.0: Added regime fields (regime, regime_distribution) so each node
knows in which market regimes it was observed. This enables:
  - Regime-aware confidence: patterns matching current regime get boosted
  - Independent vs dependent nodes: some patterns work across all regimes,
    others only in specific regimes
  - N4 regime-specific tries that actually filter by regime
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class BlockLifecycleMetadata:
    """
    Block Lifecycle Metadata attached to each Trie node.

    This is the core innovation of PPMT V3. Every node in the Trie
    carries these fields, enabling the Trie itself to make all
    trading decisions without external indicators.

    Key insight: If a next SAX block does NOT exist as a child node,
    the pattern broke BEFORE price hit SL -> earliest possible exit signal.

    v0.10.0: Added regime fields. Each node now knows which market
    regimes it was observed in, enabling regime-aware confidence scoring.
    Nodes can be "independent" (work in any regime) or "dependent"
    (only work in specific regimes).
    """

    # === Entry/Exit Timing ===
    trigger_candle: int = 0
    """Which candle within the pattern sequence activates this block.
    E.g., if trigger_candle=10 in a 50-candle pattern, the signal
    fires at candle 10 with 40 candles of predicted movement remaining."""

    remaining_candles: int = 0
    """Predicted number of candles remaining in this pattern phase.
    Derived from historical average duration at this node."""

    # === Price Prediction ===
    expected_move_pct: float = 0.0
    """Expected percentage price move from this point.
    Positive = bullish, Negative = bearish.
    Computed as median of historical moves from this node."""

    max_drawdown_pct: float = 0.0
    """Maximum observed drawdown (negative) from entry at this node.
    Used to set stop loss levels dynamically."""

    max_favorable_pct: float = 0.0
    """Maximum observed favorable excursion from entry.
    Used to set take profit and trailing stop levels."""

    # === Historical Statistics ===
    win_rate: float = 0.0
    """Percentage of times this pattern completed successfully.
    Success = price reached expected_move before max_drawdown."""

    avg_duration: int = 0
    """Average number of candles this pattern phase lasts.
    Used to predict remaining_candles for new observations."""

    historical_count: int = 0
    """Number of times this exact SAX sequence has been observed.
    More observations = higher confidence in metadata accuracy."""

    # === Risk Parameters (computed from history) ===
    sl_price: Optional[float] = None
    """Dynamic stop loss price level.
    Computed from max_drawdown_pct with safety margin."""

    tp_price: Optional[float] = None
    """Dynamic take profit price level.
    Computed from max_favorable_pct with safety margin."""

    # === Forward/Backward Navigation ===
    continuation_nodes: list[str] = field(default_factory=list)
    """SAX symbols that historically continued this pattern.
    If the next observed block is in this list -> continue holding.
    These represent the 'forward' metadata -- what comes after."""

    break_nodes: list[str] = field(default_factory=list)
    """SAX symbols that historically broke this pattern.
    These represent transitions to a different regime.
    Not all missing blocks are breaks -- some are just noise."""

    # === Regime Context (v0.10.0) ===
    regime: str = ""
    """Primary market regime for this pattern.
    The regime with the most observations. One of:
    trending_up, trending_down, ranging, volatile.
    Empty string means no regime info yet."""

    regime_distribution: dict[str, int] = field(default_factory=dict)
    """Distribution of observations across market regimes.
    Keys are regime names, values are observation counts.
    E.g., {'trending_up': 45, 'ranging': 30, 'volatile': 5}
    This distinguishes:
      - Independent nodes: spread across many regimes (works anywhere)
      - Dependent nodes: concentrated in one regime (regime-specific)
    Children inherit regime context from their observations."""

    regime_confidence: float = 0.0
    """Average confidence of regime detection at observation times (0-1).
    High regime_confidence means the regime classification was clear.
    Low regime_confidence suggests ambiguous regime -- trading
    decisions should be more conservative."""

    trend_strength: float = 0.0
    """Average trend strength (R-squared) at observation time.
    0.0 = no trend (ranging/volatile), 1.0 = perfect linear trend.
    Used by Risk Manager for regime-scaling position sizing."""

    volatility_regime: float = 0.0
    """Average annualized volatility at observation time.
    Used to adjust SL/TP distances and position sizing.
    Higher volatility -> wider stops, smaller positions."""

    hurst_exponent: float = 0.5
    """Average Hurst exponent at observation time.
    > 0.5 = trending (momentum works)
    = 0.5 = random walk
    < 0.5 = mean-reverting (reversal works)
    Propagated from parent to child nodes."""

    regime_transitions: dict[str, int] = field(default_factory=dict)
    """Count of transitions from this node's regime to other regimes.
    Key = target regime, Value = number of times observed.
    Critical for detecting regime changes in real-time."""

    is_regime_dependent: bool = False
    """Whether this node's metadata is regime-specific.
    True for N3/N4 nodes (per-asset, per-asset+regime).
    False for N1/N2 nodes (universal, asset class).
    When True, trading operations should check regime alignment."""

    # === Computed Properties ===

    @property
    def risk_reward_ratio(self) -> float:
        """Compute risk:reward ratio from expected move vs max drawdown."""
        if self.max_drawdown_pct == 0:
            return 0.0
        return abs(self.expected_move_pct / self.max_drawdown_pct)

    @property
    def confidence(self) -> float:
        """
        Confidence score based on historical observations and win rate.
        More observations and higher win rate -> higher confidence.
        Uses Bayesian-inspired shrinking toward 0.5 for low counts.
        """
        if self.historical_count == 0:
            return 0.0
        # Bayesian shrinkage: prior of 0.5 with strength of 10 observations
        prior_strength = 10.0
        adjusted_win_rate = (
            (self.win_rate * self.historical_count + 0.5 * prior_strength)
            / (self.historical_count + prior_strength)
        )
        # Scale by sqrt of log(count) for sample size bonus
        count_bonus = min(1.0, np.sqrt(np.log1p(self.historical_count) / np.log(1000)))
        return adjusted_win_rate * count_bonus

    @property
    def expected_profit_ahead(self) -> float:
        """
        Expected profit percentage looking ahead from this block.
        Combines win_rate x expected_move to give a realistic expectation.

        This is the key metric the Money Manager uses to decide allocation:
          - High expected_profit_ahead -> allocate more capital
          - Low/negative -> allocate less or skip

        Formula: win_rate x expected_move_pct + (1 - win_rate) x max_drawdown_pct
        This gives the expected value including losses.
        """
        if self.historical_count == 0:
            return 0.0
        win_expectation = self.win_rate * self.expected_move_pct
        loss_expectation = (1.0 - self.win_rate) * self.max_drawdown_pct
        return win_expectation + loss_expectation

    @property
    def probability_of_success(self) -> float:
        """
        Probability that this pattern succeeds (reaches expected move
        before hitting stop loss).

        This is more nuanced than raw win_rate because it accounts
        for the Bayesian shrinkage and sample size. The Money Manager
        uses this as the primary signal for position sizing.

        Returns the same Bayesian-adjusted win_rate used in confidence(),
        but without the count bonus -- just the pure probability estimate.
        """
        if self.historical_count == 0:
            return 0.0
        prior_strength = 10.0
        return (
            (self.win_rate * self.historical_count + 0.5 * prior_strength)
            / (self.historical_count + prior_strength)
        )

    @property
    def sizing_signal(self) -> float:
        """
        Composite sizing signal for the Money Manager (0.0 to 2.0+).

        This is the single number the Risk Manager reads to decide
        position size. It combines:
          - probability_of_success: How likely is this pattern to win?
          - expected_profit_ahead: How much do we expect to make?
          - risk_reward_ratio: Is the payoff worth the risk?

        Mapping (used by RiskManager):
          sizing_signal >= 1.5  : 2x base position (high conviction)
          sizing_signal 1.0-1.5 : 1x base position (normal)
          sizing_signal 0.5-1.0 : 0.5x base position (low conviction)
          sizing_signal < 0.5   : 0.25x or reject (very low)

        This creates the tight PPMT -> RiskManager integration where
        the Trie's metadata directly drives capital allocation.
        """
        if self.historical_count == 0:
            return 0.0

        # Normalize components
        prob = self.probability_of_success  # 0-1

        # Expected profit: normalize to 0-1 range
        # A 2% expected profit is already very good for crypto
        profit_score = min(abs(self.expected_profit_ahead) / 2.0, 1.0)

        # Risk:reward: normalize (RR of 3+ is excellent)
        rr_score = min(self.risk_reward_ratio / 3.0, 1.0)

        # Weighted composite: probability is most important
        signal = 0.4 * prob + 0.35 * profit_score + 0.25 * rr_score

        # Scale to 0-2 range for the multiplier
        return signal * 2.0

    @property
    def is_unknown_block_exit(self) -> bool:
        """
        Whether an unknown next block should trigger an exit.
        True when there are continuation_nodes defined but the observed
        block is NOT among them -- meaning pattern broke.
        """
        return len(self.continuation_nodes) > 0

    # === Regime-Aware Properties (v0.10.0 + v0.11.0 V4) ===

    @property
    def regime_aligned(self) -> bool:
        """Whether this node has regime context that should be checked
        before using its metadata for trading decisions."""
        return self.regime != "" and self.regime_confidence > 0.3

    @property
    def suggested_direction(self) -> str:
        """Suggested trading direction based on regime + expected_move.
        Combines the regime context with the expected price move
        to determine the optimal trading direction.
        Returns: 'LONG', 'SHORT', 'FLAT', or 'AVOID'

        This is what makes nodes 'give info to possible trading operations':
          - trending_up + bullish expected_move -> LONG (regime confirms)
          - trending_down + bearish expected_move -> SHORT (regime confirms)
          - volatile + high confidence -> AVOID (too chaotic)
          - ranging + small expected_move -> FLAT (no edge)
        """
        if self.regime == "volatile" and self.regime_confidence > 0.5:
            return "AVOID"
        if self.regime == "trending_up" and self.expected_move_pct > 0:
            return "LONG"
        if self.regime == "trending_down" and self.expected_move_pct < 0:
            return "SHORT"
        if self.regime == "ranging":
            if abs(self.expected_move_pct) < 0.5:
                return "FLAT"
            return "LONG" if self.expected_move_pct > 0 else "SHORT"
        if abs(self.expected_move_pct) < 0.5:
            return "FLAT"
        return "LONG" if self.expected_move_pct > 0 else "SHORT"

    @property
    def regime_independence(self) -> float:
        """
        How regime-independent this pattern is (0.0 to 1.0).

        A pattern observed equally across all regimes has independence ~ 1.0
        (works everywhere = independent). A pattern concentrated in one
        regime has independence ~ 0.0 (regime-dependent).

        Computed using normalized entropy of the regime distribution.
        High entropy = spread across regimes = independent.
        Low entropy = concentrated in one regime = dependent.

        Independent patterns are more robust -- they don't need regime
        matching to work. Dependent patterns need the right regime.
        """
        if not self.regime_distribution:
            return 0.5  # No info -> neutral
        total = sum(self.regime_distribution.values())
        if total == 0:
            return 0.5
        # Compute Shannon entropy
        n_regimes = 4  # trending_up, trending_down, ranging, volatile
        probs = [count / total for count in self.regime_distribution.values()]
        entropy = -sum(p * np.log2(p) for p in probs if p > 0)
        # Normalize by max entropy (uniform distribution)
        max_entropy = np.log2(max(n_regimes, 1))
        if max_entropy == 0:
            return 1.0
        return float(entropy / max_entropy)

    def regime_match_score(self, current_regime: str) -> float:
        """
        Score how well the current regime matches this node's history.

        Returns a multiplier (0.0 to 1.5) for adjusting confidence:
          - Current regime is the primary regime -> 1.2 (boost)
          - Current regime has some observations -> 1.0 (neutral)
          - Current regime has NO observations -> 0.6 (penalize)
          - No regime info available -> 1.0 (neutral, don't adjust)

        For independent nodes (high regime_independence), the score
        is closer to 1.0 regardless of match -- because they work
        across all regimes.

        For dependent nodes (low regime_independence), the score
        varies more -- because they NEED the right regime.

        This creates the "independent vs dependent" node behavior:
          - Independent: pattern works regardless -> regime barely matters
          - Dependent: pattern needs specific regime -> regime is critical
        """
        if not self.regime_distribution or current_regime == "":
            return 1.0  # No regime info -> don't adjust

        total = sum(self.regime_distribution.values())
        if total == 0:
            return 1.0

        regime_count = self.regime_distribution.get(current_regime, 0)
        regime_fraction = regime_count / total

        # Base score from regime match fraction
        if regime_count == 0:
            # Never seen in this regime -> penalize
            base_score = 0.6
        elif current_regime == self.regime:
            # Current regime is the primary (most common) -> boost
            base_score = 1.0 + 0.2 * regime_fraction
        else:
            # Current regime has some observations -> neutral
            base_score = 0.8 + 0.2 * regime_fraction

        # Blend with independence: independent nodes stay near 1.0
        independence = self.regime_independence
        # Weight: high independence -> flatten toward 1.0
        #         low independence -> keep the base_score
        blended = independence * 1.0 + (1.0 - independence) * base_score

        return float(np.clip(blended, 0.4, 1.5))

    def update_from_observation(
        self,
        move_pct: float,
        drawdown_pct: float,
        favorable_pct: float,
        duration: int,
        won: bool,
        next_symbol: Optional[str] = None,
        regime: Optional[str] = None,
        regime_confidence: float = 0.0,
        trend_strength: float = 0.0,
        volatility_regime: float = 0.0,
        hurst_exponent: float = 0.5,
        is_regime_dependent: bool = False,
    ) -> None:
        """
        Update metadata with a new observation using incremental statistics.

        This method updates all fields incrementally without storing raw data,
        making it memory-efficient for millions of patterns.

        v0.11.0 (V4): Now also updates regime metrics. If the observation's
        regime differs from the node's current primary regime, this indicates
        a potential regime transition that is tracked in regime_transitions.

        Args:
            move_pct: Actual percentage move observed
            drawdown_pct: Maximum drawdown observed during pattern
            favorable_pct: Maximum favorable excursion observed
            duration: Actual duration in candles
            won: Whether the pattern completed successfully
            next_symbol: SAX symbol that followed this block (if any)
            regime: Market regime at observation time (v0.10.0).
                One of: trending_up, trending_down, ranging, volatile.
                None means regime info not available (backward compatible).
            regime_confidence: Confidence of regime detection (v0.11.0)
            trend_strength: R-squared trend strength (v0.11.0)
            volatility_regime: Annualized volatility (v0.11.0)
            hurst_exponent: Hurst exponent (v0.11.0)
            is_regime_dependent: Whether this node is regime-specific (v0.11.0)
        """
        n = self.historical_count
        self.historical_count += 1

        # Incremental mean update
        self.expected_move_pct = (
            (self.expected_move_pct * n + move_pct) / self.historical_count
        )

        # Track worst drawdown and best favorable
        self.max_drawdown_pct = min(self.max_drawdown_pct, drawdown_pct)
        self.max_favorable_pct = max(self.max_favorable_pct, favorable_pct)

        # Incremental win rate update
        wins = self.win_rate * n + (1.0 if won else 0.0)
        self.win_rate = wins / self.historical_count

        # Incremental average duration
        self.avg_duration = int(
            (self.avg_duration * n + duration) / self.historical_count
        )

        # Update remaining_candles from average duration
        self.remaining_candles = self.avg_duration

        # Track continuation/break nodes
        if next_symbol is not None:
            if next_symbol not in self.continuation_nodes:
                self.continuation_nodes.append(next_symbol)

        # v0.10.0: Track regime distribution
        if regime is not None and regime != "":
            self.regime_distribution[regime] = self.regime_distribution.get(regime, 0) + 1
            # Update primary regime (the one with most observations)
            if self.regime == "" or self.regime_distribution.get(regime, 0) > self.regime_distribution.get(self.regime, 0):
                self.regime = regime

            # v0.11.0 (V4): Track regime transitions
            if self.regime != "" and regime != self.regime:
                self.regime_transitions[regime] = self.regime_transitions.get(regime, 0) + 1

            # v0.11.0 (V4): Incremental regime metrics update
            self.regime_confidence = (
                (self.regime_confidence * n + regime_confidence) / self.historical_count
            )
            self.trend_strength = (
                (self.trend_strength * n + trend_strength) / self.historical_count
            )
            self.volatility_regime = (
                (self.volatility_regime * n + volatility_regime) / self.historical_count
            )
            self.hurst_exponent = (
                (self.hurst_exponent * n + hurst_exponent) / self.historical_count
            )

            # Set regime dependency on first observation with it
            if is_regime_dependent and n == 0:
                self.is_regime_dependent = True

    def compute_sl_tp(self, entry_price: float, safety_margin: float = 0.2) -> None:
        """
        Compute stop loss and take profit prices from metadata.

        Args:
            entry_price: Current entry price
            safety_margin: Margin added to SL/TP for safety (0.2 = 20% extra)
        """
        # Stop loss: max_drawdown with safety margin
        sl_distance = abs(self.max_drawdown_pct) * (1.0 + safety_margin)
        self.sl_price = entry_price * (1.0 - sl_distance / 100.0)

        # Take profit: expected_move or max_favorable, whichever is more conservative
        tp_distance = min(
            abs(self.expected_move_pct),
            self.max_favorable_pct,
        ) * (1.0 - safety_margin * 0.5)
        self.tp_price = entry_price * (1.0 + tp_distance / 100.0)

    def to_dict(self) -> dict:
        """Serialize metadata to dictionary for storage."""
        return {
            "trigger_candle": self.trigger_candle,
            "remaining_candles": self.remaining_candles,
            "expected_move_pct": round(self.expected_move_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "max_favorable_pct": round(self.max_favorable_pct, 4),
            "win_rate": round(self.win_rate, 4),
            "avg_duration": self.avg_duration,
            "historical_count": self.historical_count,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "continuation_nodes": self.continuation_nodes,
            "break_nodes": self.break_nodes,
            # V3: Computed sizing signals for Risk Manager
            "confidence": round(self.confidence, 4),
            "probability_of_success": round(self.probability_of_success, 4),
            "expected_profit_ahead": round(self.expected_profit_ahead, 4),
            "sizing_signal": round(self.sizing_signal, 4),
            "risk_reward_ratio": round(self.risk_reward_ratio, 4),
            # v0.10.0/v0.11.0: Regime context
            "regime": self.regime,
            "regime_distribution": self.regime_distribution,
            "regime_confidence": round(self.regime_confidence, 4),
            "trend_strength": round(self.trend_strength, 4),
            "volatility_regime": round(self.volatility_regime, 4),
            "hurst_exponent": round(self.hurst_exponent, 4),
            "regime_transitions": self.regime_transitions,
            "is_regime_dependent": self.is_regime_dependent,
            "suggested_direction": self.suggested_direction,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BlockLifecycleMetadata:
        """Deserialize metadata from dictionary."""
        meta = cls(
            trigger_candle=data.get("trigger_candle", 0),
            remaining_candles=data.get("remaining_candles", 0),
            expected_move_pct=data.get("expected_move_pct", 0.0),
            max_drawdown_pct=data.get("max_drawdown_pct", 0.0),
            max_favorable_pct=data.get("max_favorable_pct", 0.0),
            win_rate=data.get("win_rate", 0.0),
            avg_duration=data.get("avg_duration", 0),
            historical_count=data.get("historical_count", 0),
            sl_price=data.get("sl_price"),
            tp_price=data.get("tp_price"),
            continuation_nodes=data.get("continuation_nodes", []),
            break_nodes=data.get("break_nodes", []),
            regime=data.get("regime", ""),
            regime_distribution=data.get("regime_distribution", {}),
            regime_confidence=data.get("regime_confidence", 0.0),
            trend_strength=data.get("trend_strength", 0.0),
            volatility_regime=data.get("volatility_regime", 0.0),
            hurst_exponent=data.get("hurst_exponent", 0.5),
            regime_transitions=data.get("regime_transitions", {}),
            is_regime_dependent=data.get("is_regime_dependent", False),
        )
        return meta
