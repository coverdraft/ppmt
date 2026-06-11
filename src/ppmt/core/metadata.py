"""
Block Lifecycle Metadata - The V3 Innovation

Each Trie node carries 12 metadata fields that encode:
- When to enter (trigger_candle)
- How long the pattern should last (remaining_candles)
- Expected move and risk parameters
- Forward continuation and backward context
- Historical statistics

This metadata makes PPMT autonomous — all entry/exit/SL/TP decisions
emerge directly from the Trie without external indicators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class BlockLifecycleMetadata:
    """
    Block Lifecycle Metadata attached to each Trie node.

    This is the core innovation of PPMT V3/V4. Every node in the Trie
    carries these fields, enabling the Trie itself to make all
    trading decisions without external indicators.

    Key insight: If a next SAX block does NOT exist as a child node,
    the pattern broke BEFORE price hit SL → earliest possible exit signal.

    V4 Enhancement: Regime-Aware Node Metadata
    Each node now stores the market regime(s) under which its pattern
    was observed. This enables:
      - Regime-specific pattern matching (N4 Trie segmentation)
      - Regime-aware confidence scoring (patterns in favorable regimes
        get higher confidence, unfavorable regimes get lower)
      - Independent vs Dependent node classification:
          * Independent: has enough observations (>= min_independent_count)
            to be self-sufficient — its metadata is reliable on its own
          * Dependent: relies on parent/ancestor metadata (low count,
            inherited regime info) — confidence is scaled down
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
    If the next observed block is in this list → continue holding.
    These represent the 'forward' metadata — what comes after."""

    break_nodes: list[str] = field(default_factory=list)
    """SAX symbols that historically broke this pattern.
    These represent transitions to a different regime.
    Not all missing blocks are breaks — some are just noise."""

    # === V4: Regime-Aware Node Metadata ===
    regime: str = ""
    """The market regime under which this pattern was observed.
    One of: trending_up, trending_down, ranging, volatile.
    Empty string means not yet set (legacy nodes or before V4)."""

    regime_confidence: float = 0.0
    """Confidence of the regime detection when this pattern was observed.
    Range [0, 1]. Higher = more certain about the regime classification."""

    dominant_regime: str = ""
    """The most common regime across all observations of this pattern.
    For terminal nodes, this is the same as `regime`.
    For intermediate nodes (after propagation), this is the regime
    with the highest count in regime_distribution. This enables
    regime-aware routing at any Trie depth."""

    regime_distribution: dict[str, int] = field(default_factory=dict)
    """Distribution of regimes across observations of this pattern.
    E.g., {'trending_up': 45, 'ranging': 30, 'volatile': 5}
    Used to compute regime-specific win rates and confidence.
    Enables the trading engine to say: 'This pattern works 60% of
    the time in trending_up but only 35% in volatile regimes.'"""

    node_type: str = "dependent"
    """Whether this node is independent or dependent.
    - 'independent': Has enough observations (>= min_independent_count)
      to be self-sufficient. Its metadata is reliable on its own.
      Confidence is used at full strength.
    - 'dependent': Relies on parent/ancestor metadata. Low count,
      inherited regime info. Confidence is scaled down by a factor
      based on the ratio of actual vs minimum observations.
    Classification is performed during propagate_metadata() and
    updated during Living Trie observations."""

    min_independent_count: int = 10
    """Minimum historical_count for a node to be classified as independent.
    Nodes with count >= this threshold are 'independent'. Below it,
    they are 'dependent' and their metadata inherits from parents.
    10 is a reasonable default: below 10 observations, the node's
    statistics are too noisy to be reliable on their own."""

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
        More observations and higher win rate → higher confidence.
        Uses Bayesian-inspired shrinking toward 0.5 for low counts.

        V4: Dependent nodes have their confidence scaled down because
        their metadata is inherited/aggregated rather than directly
        observed. An independent node with 50 observations is more
        trustworthy than a dependent node with 3 observations whose
        statistics were propagated from children.
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
        base_confidence = adjusted_win_rate * count_bonus

        # V4: Dependent node penalty
        # Dependent nodes have less reliable metadata, so we scale
        # their confidence down. The penalty is proportional to how
        # far they are from the minimum independent count.
        if self.node_type == "dependent" and self.historical_count > 0:
            dependency_ratio = min(
                1.0, self.historical_count / self.min_independent_count
            )
            # Scale between 0.5 (0 observations toward independent)
            # and 1.0 (at the threshold)
            dependency_penalty = 0.5 + 0.5 * dependency_ratio
            base_confidence *= dependency_penalty

        return base_confidence

    @property
    def expected_profit_ahead(self) -> float:
        """
        Expected profit percentage looking ahead from this block.
        Combines win_rate × expected_move to give a realistic expectation.

        This is the key metric the Money Manager uses to decide allocation:
          - High expected_profit_ahead → allocate more capital
          - Low/negative → allocate less or skip

        Formula: win_rate × expected_move_pct + (1 - win_rate) × max_drawdown_pct
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
        but without the count bonus — just the pure probability estimate.
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
          sizing_signal >= 1.5  → 2.0x base position (high conviction)
          sizing_signal 1.0-1.5 → 1.0x base position (normal)
          sizing_signal 0.5-1.0 → 0.5x base position (low conviction)
          sizing_signal < 0.5   → 0.25x or reject (very low)

        This creates the tight PPMT → RiskManager integration where
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
        block is NOT among them — meaning pattern broke.
        """
        return len(self.continuation_nodes) > 0

    def update_from_observation(
        self,
        move_pct: float,
        drawdown_pct: float,
        favorable_pct: float,
        duration: int,
        won: bool,
        next_symbol: Optional[str] = None,
        regime: Optional[str] = None,
        regime_confidence: Optional[float] = None,
    ) -> None:
        """
        Update metadata with a new observation using incremental statistics.

        This method updates all fields incrementally without storing raw data,
        making it memory-efficient for millions of patterns.

        V4: Now also tracks regime information per observation.
        Each observation can carry the market regime under which it
        was observed, building the regime_distribution histogram.

        Args:
            move_pct: Actual percentage move observed
            drawdown_pct: Maximum drawdown observed during pattern
            favorable_pct: Maximum favorable excursion observed
            duration: Actual duration in candles
            won: Whether the pattern completed successfully
            next_symbol: SAX symbol that followed this block (if any)
            regime: Market regime at time of observation
                    (trending_up, trending_down, ranging, volatile)
            regime_confidence: Confidence of the regime detection [0, 1]
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

        # V4: Track regime distribution
        if regime and regime in ("trending_up", "trending_down", "ranging", "volatile"):
            self.regime_distribution[regime] = self.regime_distribution.get(regime, 0) + 1
            # Update dominant_regime to the most common regime
            if self.regime_distribution:
                self.dominant_regime = max(
                    self.regime_distribution, key=self.regime_distribution.get
                )
            # On first observation, set the regime directly
            if n == 0:
                self.regime = regime
                self.regime_confidence = regime_confidence if regime_confidence is not None else 0.0
            else:
                # Blend regime_confidence incrementally
                if regime_confidence is not None:
                    self.regime_confidence = (
                        (self.regime_confidence * n + regime_confidence)
                        / self.historical_count
                    )

        # V4: Update node_type based on count
        if self.historical_count >= self.min_independent_count:
            self.node_type = "independent"
        else:
            self.node_type = "dependent"

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
            # V4: Regime-aware node metadata
            "regime": self.regime,
            "regime_confidence": round(self.regime_confidence, 4),
            "dominant_regime": self.dominant_regime,
            "regime_distribution": self.regime_distribution,
            "node_type": self.node_type,
            "min_independent_count": self.min_independent_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BlockLifecycleMetadata:
        """Deserialize metadata from dictionary."""
        return cls(
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
            # V4: Regime-aware node metadata
            regime=data.get("regime", ""),
            regime_confidence=data.get("regime_confidence", 0.0),
            dominant_regime=data.get("dominant_regime", ""),
            regime_distribution=data.get("regime_distribution", {}),
            node_type=data.get("node_type", "dependent"),
            min_independent_count=data.get("min_independent_count", 10),
        )
