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
class DirectionStats:
    """
    V4.3 (FIX-A): Per-direction statistics for a node's observation history.

    Tracks win_rate, expected_move, and count separately for LONG and SHORT
    observations, enabling the trading engine to make direction-aware decisions
    like: "This pattern wins 62% as LONG but only 33% as SHORT."

    PROBLEM (v0.40.17-audit): The legacy `win_rate` field mixes LONG and SHORT
    observations. `won = move_pct > 0` (ppmt.py:336) is valid for LONG only —
    a SHORT trade "wins" when `move_pct < 0`. The mixed win_rate systematically
    overestimates LONG win_rate and underestimates SHORT win_rate, producing
    the LONG/SHORT asymmetry observed in the audit:
      - LONG PnL medio = -0.017% (negative in ALL confidence deciles >0.40)
      - SHORT PnL medio = +0.013% (positive in ALL confidence deciles)
      - Spearman confidence<->PnL LONG = -0.008 (no predictive power)

    SOLUTION: Classify each observation by sign of move_pct at insert time.
    `long_stats` accumulates observations where move_pct > 0 (LONG wins).
    `short_stats` accumulates observations where move_pct < 0 (SHORT wins).
    At match time, the engine queries the stats for the direction it intends
    to trade, getting an unbiased win_rate.
    """
    count: int = 0
    """Number of observations in this direction (move_pct > 0 for LONG, < 0 for SHORT)."""

    wins: int = 0
    """Number of winning observations in this direction.
    For LONG: move_pct > 0 (always wins by definition of classification).
    For SHORT: move_pct < 0 (always wins by definition of classification).
    So wins == count for DirectionStats — but kept for API symmetry with RegimeStats."""

    total_move_pct: float = 0.0
    """Cumulative move_pct across all observations in this direction.
    For LONG: sum of positive move_pct values (LONG's expected profit).
    For SHORT: sum of negative move_pct values (SHORT's expected profit, negative)."""

    total_drawdown_pct: float = 0.0
    """Cumulative drawdown observed in this direction's observations."""

    @property
    def win_rate(self) -> float:
        """Win rate within this direction. By construction always 1.0 since
        classification IS by winning direction. Kept for API symmetry."""
        if self.count == 0:
            return 0.0
        return self.wins / self.count

    @property
    def avg_move_pct(self) -> float:
        """Average move_pct within this direction.
        For LONG: average positive move (expected profit).
        For SHORT: average negative move (expected SHORT profit, returned as negative)."""
        if self.count == 0:
            return 0.0
        return self.total_move_pct / self.count

    @property
    def avg_drawdown_pct(self) -> float:
        """Average max drawdown within this direction."""
        if self.count == 0:
            return 0.0
        return self.total_drawdown_pct / self.count

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "count": self.count,
            "wins": self.wins,
            "total_move_pct": round(self.total_move_pct, 4),
            "total_drawdown_pct": round(self.total_drawdown_pct, 4),
        }

    @classmethod
    def from_dict(cls, data: dict) -> DirectionStats:
        """Deserialize from dictionary."""
        return cls(
            count=data.get("count", 0),
            wins=data.get("wins", 0),
            total_move_pct=data.get("total_move_pct", 0.0),
            total_drawdown_pct=data.get("total_drawdown_pct", 0.0),
        )


@dataclass
class RegimeStats:
    """
    Statistics for a single regime within a node's observation history.

    Tracks win_rate, expected_move, and count separately for each regime,
    enabling the trading engine to make regime-aware decisions like:
      "This pattern wins 62% in trending_up but only 33% in volatile."

    This is the key V4.1 enhancement that makes regime_distribution actionable.
    Without per-regime win_rate, we only know HOW MANY times a pattern was
    observed in each regime, but not WHETHER IT WON there.
    """
    count: int = 0
    """Number of observations in this regime."""

    wins: int = 0
    """Number of winning observations in this regime."""

    total_move_pct: float = 0.0
    """Cumulative expected_move across all observations in this regime.
    Used to compute average expected_move per regime."""

    @property
    def win_rate(self) -> float:
        """Win rate within this regime."""
        if self.count == 0:
            return 0.0
        return self.wins / self.count

    @property
    def avg_move_pct(self) -> float:
        """Average expected_move within this regime."""
        if self.count == 0:
            return 0.0
        return self.total_move_pct / self.count

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "count": self.count,
            "wins": self.wins,
            "total_move_pct": round(self.total_move_pct, 4),
        }

    @classmethod
    def from_dict(cls, data: dict) -> RegimeStats:
        """Deserialize from dictionary."""
        return cls(
            count=data.get("count", 0),
            wins=data.get("wins", 0),
            total_move_pct=data.get("total_move_pct", 0.0),
        )


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

    regime_stats: dict[str, RegimeStats] = field(default_factory=dict)
    """V4.1: Per-regime statistics including win_rate and expected_move.
    While regime_distribution only counts observations, regime_stats tracks
    the actual performance within each regime. This is critical because:
    - A pattern observed 50 times in trending_up with 60% WR is valuable
    - A pattern observed 50 times in volatile with 30% WR is dangerous
    Without this, the trading engine cannot make regime-aware decisions.
    Key: regime name (e.g., 'trending_up'), Value: RegimeStats."""

    # === V4.3 (FIX-A): Per-direction statistics ===
    long_stats: DirectionStats = field(default_factory=DirectionStats)
    """Statistics for observations where move_pct > 0 (LONG instances).
    At match time, when the engine decides LONG, it should query
    long_stats.win_rate and long_stats.avg_move_pct instead of the
    mixed-aggregate `win_rate` field, which conflates LONG and SHORT outcomes."""

    short_stats: DirectionStats = field(default_factory=DirectionStats)
    """Statistics for observations where move_pct < 0 (SHORT instances).
    At match time, when the engine decides SHORT, it should query
    short_stats.win_rate and short_stats.avg_move_pct (the latter is negative,
    representing SHORT's expected profit when expressed as a signed move)."""

    # === V4.1: Move Variance Tracking ===
    move_variance: float = 0.0
    """Variance of observed moves (Welford's online algorithm).
    High variance = unreliable pattern (move_pct swings wildly).
    Low variance = reliable pattern (consistent outcomes).
    Used to compute move_std which adjusts confidence downward
    for patterns with inconsistent historical outcomes."""

    move_mean_for_variance: float = 0.0
    """Running mean used by Welford's algorithm for move_variance.
    Not the same as expected_move_pct (which is the incremental mean).
    This is maintained separately for the Welford M2 computation."""

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

    # === V4.2: Observation Freshness ===
    last_observation_time: float = 0.0
    """Timestamp (epoch seconds) of the most recent observation.
    V4.2: Enables observation freshness tracking — patterns that
    haven't been observed recently can be deprioritized. This is
    critical for the Living Trie: as market conditions change,
    old patterns become less relevant. A pattern observed 5000
    candles ago should carry less weight than one observed 50 candles
    ago. The freshness_decay property computes a multiplier [0, 1]
    based on how long since the last observation."""

    observation_timespan: float = 0.0
    """Time span (in seconds) between the first and last observation.
    V4.2: Measures how spread out observations are. A pattern observed
    100 times in one day is less reliable than one observed 100 times
    over 30 days. Longer timespan = more robust pattern that works
    across different conditions. Short timespan = potentially overfit
    to a specific market condition."""

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

    @property
    def move_std(self) -> float:
        """
        Standard deviation of observed moves.
        Computed from move_variance using Welford's online algorithm.
        High std = unpredictable pattern = lower effective confidence.
        Low std = consistent pattern = higher effective confidence.
        Returns 0.0 if fewer than 2 observations (can't compute variance).
        """
        if self.historical_count < 2:
            return 0.0
        return float(np.sqrt(self.move_variance / (self.historical_count - 1)))

    @property
    def move_coefficient_of_variation(self) -> float:
        """
        Coefficient of variation (CV) of observed moves.
        CV = std / |mean|. Normalized measure of dispersion.
        CV < 0.5 = tight clustering around the mean (reliable)
        CV 0.5-1.0 = moderate dispersion (acceptable)
        CV > 1.0 = high dispersion (unreliable — move direction uncertain)
        Returns 0.0 if expected_move_pct is zero (can't normalize).
        """
        if abs(self.expected_move_pct) < 1e-10:
            return 0.0
        return self.move_std / abs(self.expected_move_pct)

    @property
    def freshness_decay(self) -> float:
        """
        V4.2: Observation freshness multiplier based on time since last observation.

        Returns a value in [0, 1] that decays as the last observation gets older.
        Uses exponential decay with a half-life of 7 days (604800 seconds).

        - 0 days old → 1.0 (fresh, fully trusted)
        - 7 days old → 0.5 (half-weight)
        - 30 days old → ~0.06 (nearly expired)

        This prevents stale patterns from having the same influence as
        recently-observed ones. In fast-moving markets, patterns that
        haven't been seen in weeks may no longer be valid.

        Returns 1.0 if last_observation_time is 0 (not tracked).
        """
        if self.last_observation_time <= 0:
            return 1.0  # No tracking info, assume fresh
        import time as _time
        age_seconds = _time.time() - self.last_observation_time
        if age_seconds <= 0:
            return 1.0  # Future timestamp or same second
        # Half-life of 7 days = 604800 seconds
        half_life = 604800.0
        return float(np.exp(-0.693 * age_seconds / half_life))  # ln(2) ≈ 0.693

    @property
    def observation_density(self) -> float:
        """
        V4.2: Observations per unit time (observations/day).

        Measures how concentrated observations are. Low density = pattern
        observed occasionally over a long time (robust). High density =
        pattern observed many times in a short period (potentially overfit).

        Returns 0.0 if no timespan data.
        """
        if self.observation_timespan <= 0 or self.historical_count <= 0:
            return 0.0
        days = self.observation_timespan / 86400.0
        if days < 0.01:  # Less than ~15 minutes
            return float(self.historical_count) / 0.01  # Cap at 100/day
        return self.historical_count / days

    def regime_match_score(self, current_regime: str) -> float:
        """
        V4.1: Compute a confidence multiplier based on regime match.

        If the current market regime matches the node's dominant regime,
        confidence is boosted (up to 1.2x). If the current regime is
        unfavorable for this pattern, confidence is penalized (down to 0.5x).

        The scoring uses both regime_distribution (how often) AND
        regime_stats (how well it performed):

        1. If current regime matches dominant_regime → boost (1.0 to 1.2)
           Boost is proportional to how dominant the regime is.
        2. If current regime exists but is not dominant → neutral (0.8 to 1.0)
           Scaled by the ratio of observations in that regime.
        3. If current regime has NO observations → penalty (0.5 to 0.7)
           Unknown territory, reduce confidence.
        4. If regime_stats available, further adjust by regime-specific win_rate
           vs overall win_rate. A regime where the pattern underperforms
           gets an additional penalty.

        For independent nodes (sufficient observations), the regime match
        has MORE impact because we have reliable per-regime data.
        For dependent nodes (few observations), we apply less adjustment
        because the per-regime data is unreliable.

        Args:
            current_regime: Current market regime string

        Returns:
            Multiplier in range [0.5, 1.2] to apply to confidence
        """
        if not current_regime:
            return 1.0  # No regime info available, neutral

        if not self.regime_distribution:
            return 1.0  # No regime data on this node, neutral

        total_obs = sum(self.regime_distribution.values())
        if total_obs == 0:
            return 1.0

        current_count = self.regime_distribution.get(current_regime, 0)
        current_ratio = current_count / total_obs

        # Determine adjustment based on whether current regime is known
        if current_count == 0:
            # Regime never observed for this pattern — penalty
            # Less severe for independent nodes (more diverse data)
            base_mult = 0.7 if self.node_type == "independent" else 0.5
            return base_mult

        # Regime has been observed — compute base multiplier
        if current_regime == self.dominant_regime:
            # Current regime is the dominant one — boost
            # Boost proportional to dominance (how concentrated)
            boost = 1.0 + 0.2 * current_ratio  # 1.0 to 1.2
        else:
            # Regime exists but not dominant — neutral to slight penalty
            # Scale by how rare this regime is for this pattern
            base_mult = 0.8 + 0.2 * current_ratio  # 0.8 to 1.0
            boost = base_mult

        # V4.1: Further adjust using regime-specific win_rate if available
        if current_regime in self.regime_stats:
            rs = self.regime_stats[current_regime]
            if rs.count >= 3:  # Need at least 3 obs for reliable regime WR
                regime_wr = rs.win_rate
                overall_wr = self.win_rate
                if overall_wr > 0:
                    wr_ratio = regime_wr / overall_wr
                    # If regime WR is much worse than overall, penalize more
                    # If regime WR is better, slight extra boost
                    # Clamp wr_adjustment to [0.8, 1.1] to avoid extreme swings
                    wr_adjustment = max(0.8, min(1.1, wr_ratio ** 0.5))
                    boost *= wr_adjustment

        # Clamp final result
        return max(0.5, min(1.2, boost))

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

        # V4.3 (FIX-A): Track per-direction statistics.
        # Classify by sign of move_pct: positive => LONG instance, negative => SHORT.
        # This is the unbiased replacement for the legacy `won = move_pct > 0` flag
        # which mixes LONG-wins with SHORT-losses into a single `win_rate`.
        if move_pct > 0:
            self.long_stats.count += 1
            self.long_stats.wins += 1  # By definition, move_pct > 0 is a LONG win
            self.long_stats.total_move_pct += move_pct
            self.long_stats.total_drawdown_pct += drawdown_pct
        elif move_pct < 0:
            self.short_stats.count += 1
            self.short_stats.wins += 1  # By definition, move_pct < 0 is a SHORT win
            self.short_stats.total_move_pct += move_pct  # negative
            self.short_stats.total_drawdown_pct += drawdown_pct
        # move_pct == 0: don't classify — degenerate case.

        # V4.1: Track move variance using Welford's online algorithm
        # This is numerically stable and doesn't require storing raw data.
        # After n observations, move_variance holds the M2 statistic
        # (sum of squared differences from the running mean).
        if self.historical_count >= 2:
            delta = move_pct - self.move_mean_for_variance
            self.move_mean_for_variance = (
                (self.move_mean_for_variance * (self.historical_count - 1) + move_pct)
                / self.historical_count
            )
            delta2 = move_pct - self.move_mean_for_variance
            self.move_variance += delta * delta2
        elif self.historical_count == 1:
            self.move_mean_for_variance = move_pct
            self.move_variance = 0.0

        # V4: Track regime distribution
        if regime and regime in ("trending_up", "trending_down", "ranging", "volatile"):
            self.regime_distribution[regime] = self.regime_distribution.get(regime, 0) + 1
            # V4.1: Track per-regime statistics (win_rate, expected_move)
            if regime not in self.regime_stats:
                self.regime_stats[regime] = RegimeStats()
            rs = self.regime_stats[regime]
            rs.count += 1
            rs.total_move_pct += move_pct
            if won:
                rs.wins += 1
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

        # V4.2: Track observation freshness
        import time as _time
        now = _time.time()
        if n == 0:
            # First observation — set initial time, timespan is 0
            self.last_observation_time = now
            self.observation_timespan = 0.0
        else:
            # Update timespan: difference between first and latest observation
            if self.last_observation_time > 0:
                self.observation_timespan = max(
                    self.observation_timespan, now - (self.last_observation_time - self.observation_timespan)
                )
            self.last_observation_time = now

    @property
    def win_rate_long(self) -> float:
        """V4.3 (FIX-A): Win rate when this pattern is traded as LONG.

        This is the count of LONG-favorable observations divided by total
        observations. A pattern with `win_rate_long = 0.70` means: when this
        pattern was observed historically, 70% of the time the price went UP
        afterward. The engine should use this — NOT `win_rate` — when deciding
        whether to enter a LONG position.

        Returns 0.0 if no observations.
        """
        if self.historical_count == 0:
            return 0.0
        return self.long_stats.count / self.historical_count

    @property
    def win_rate_short(self) -> float:
        """V4.3 (FIX-A): Win rate when this pattern is traded as SHORT.

        This is the count of SHORT-favorable observations divided by total
        observations. A pattern with `win_rate_short = 0.70` means: when this
        pattern was observed historically, 70% of the time the price went DOWN
        afterward. The engine should use this — NOT `win_rate` — when deciding
        whether to enter a SHORT position.

        Returns 0.0 if no observations.
        """
        if self.historical_count == 0:
            return 0.0
        return self.short_stats.count / self.historical_count

    @property
    def avg_move_long(self) -> float:
        """V4.3 (FIX-A): Average move_pct when LONG was favorable.
        Positive number = expected profit per LONG trade on this pattern.
        Returns 0.0 if no LONG-favorable observations."""
        return self.long_stats.avg_move_pct

    @property
    def avg_move_short(self) -> float:
        """V4.3 (FIX-A): Average move_pct when SHORT was favorable.
        Negative number — its absolute value is the expected profit per SHORT
        trade on this pattern.
        Returns 0.0 if no SHORT-favorable observations."""
        return self.short_stats.avg_move_pct

    def confidence_for_direction(self, direction: str) -> float:
        """
        V4.3 (FIX-A): Direction-aware confidence score.

        This is the drop-in replacement for `confidence()` when the engine
        knows which direction it intends to trade. Uses the per-direction
        win_rate (unbiased) instead of the mixed-aggregate `win_rate` field.

        Args:
            direction: 'LONG' or 'SHORT' (case-insensitive).

        Returns:
            Confidence score in [0, 1]. Returns 0.0 if no observations.

        Formula mirrors `confidence()` but with direction-specific win_rate:
            adjusted_wr = (wr_dir * count + 0.5 * 10) / (count + 10)
            count_bonus = min(1.0, sqrt(log1p(count) / log(1000)))
            base_confidence = adjusted_wr * count_bonus
        Plus the dependent-node penalty from `confidence()`.
        """
        if self.historical_count == 0:
            return 0.0

        direction = direction.upper()
        if direction == "LONG":
            wr = self.win_rate_long
        elif direction == "SHORT":
            wr = self.win_rate_short
        else:
            # Unknown direction: fall back to mixed-aggregate (legacy behavior)
            wr = self.win_rate

        prior_strength = 10.0
        adjusted_wr = (
            (wr * self.historical_count + 0.5 * prior_strength)
            / (self.historical_count + prior_strength)
        )
        count_bonus = min(1.0, np.sqrt(np.log1p(self.historical_count) / np.log(1000)))
        base_confidence = adjusted_wr * count_bonus

        if self.node_type == "dependent" and self.historical_count > 0:
            dependency_ratio = min(
                1.0, self.historical_count / self.min_independent_count
            )
            dependency_penalty = 0.5 + 0.5 * dependency_ratio
            base_confidence *= dependency_penalty

        return base_confidence

    def expected_move_for_direction(self, direction: str) -> float:
        """
        V4.3 (FIX-A): Direction-aware expected move.

        For LONG: returns avg_move_long (positive number = expected % profit).
        For SHORT: returns abs(avg_move_short) (positive number = expected % profit).

        This is the drop-in replacement for `expected_move_pct` when the engine
        knows the direction. The legacy `expected_move_pct` mixes LONG and SHORT
        moves and can be near-zero even when both directions have strong edges.
        """
        direction = direction.upper()
        if direction == "LONG":
            return self.avg_move_long
        if direction == "SHORT":
            # SHORT profit = abs(move_pct) when move_pct < 0
            return abs(self.avg_move_short)
        return self.expected_move_pct  # fallback

    def compute_sl_tp(self, entry_price: float, safety_margin: float = 0.2) -> None:
        """
        Compute stop loss and take profit prices from metadata.

        Args:
            entry_price: Current entry price
            safety_margin: Margin added to SL/TP for safety (0.2 = 20% extra)

        v0.40.1 FIX-4: Rebalanced SL/TP to preserve the directional edge
          identified in CAPA 3 audit (EM→PnL corr = +0.11).
          BEFORE: SL = max_drawdown × 1.2, TP = min(|EM|, max_fav) × 0.9
            → SL/TP hit ratio = 2.14 (SL hits 64.8% of trades, TP 30.3%)
            → Break-even requires 28.6% TP rate, motor delivered 30.3%
              but END trades (4.9%) destroyed the margin → net losing.
          AFTER: SL = max_drawdown × 1.5 (more slack), TP = max(|EM|, max_fav) × 1.0 (full)
            → Logic: if motor has directional edge (+0.11 EM→PnL), we want:
              - SL holgado so noisy drawdowns don't kick us out
              - TP completo so we capture the full predicted move
            → Target RR ≈ 0.67 (TP_dist / SL_dist), break-even at 60% TP rate
              but the bet is that with looser SL we go from 30% → 50%+ TP rate
              because the noisy drawdowns that hit SL before now resolve.
          The old safety_margin param is preserved for API compat but no
          longer used in the calculation (we use explicit 1.5 and 1.0 factors).
        """
        # Stop loss: max_drawdown × 1.5 (was × 1.2)
        # More slack to absorb noisy drawdowns from sparse-trie metadata
        sl_distance = abs(self.max_drawdown_pct) * 1.5
        self.sl_price = entry_price * (1.0 - sl_distance / 100.0)

        # Take profit: max(|expected_move|, max_favorable) × 1.0 (was min(...) × 0.9)
        # - Use MAX instead of MIN: if the motor predicts +0.5% but history
        #   shows max_favorable of +0.8%, target the higher (more optimistic)
        #   since we believe the directional signal.
        # - No haircut (× 1.0 instead of × 0.9): capture the full predicted
        #   move. The old 0.9 haircut was leaving profit on the table.
        tp_distance = max(
            abs(self.expected_move_pct),
            self.max_favorable_pct,
        ) * 1.0
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
            # V4.1: Per-regime statistics
            "regime_stats": {k: v.to_dict() for k, v in self.regime_stats.items()},
            # V4.3 (FIX-A): Per-direction statistics
            "long_stats": self.long_stats.to_dict(),
            "short_stats": self.short_stats.to_dict(),
            # V4.1: Move variance tracking
            "move_variance": round(self.move_variance, 6),
            "move_mean_for_variance": round(self.move_mean_for_variance, 6),
            "node_type": self.node_type,
            "min_independent_count": self.min_independent_count,
            # V4.2: Observation freshness
            "last_observation_time": self.last_observation_time,
            "observation_timespan": self.observation_timespan,
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
            # V4.1: Per-regime statistics
            regime_stats={
                k: RegimeStats.from_dict(v) if isinstance(v, dict) else RegimeStats()
                for k, v in data.get("regime_stats", {}).items()
            },
            # V4.3 (FIX-A): Per-direction statistics
            long_stats=DirectionStats.from_dict(data.get("long_stats", {}))
                if isinstance(data.get("long_stats", {}), dict) else DirectionStats(),
            short_stats=DirectionStats.from_dict(data.get("short_stats", {}))
                if isinstance(data.get("short_stats", {}), dict) else DirectionStats(),
            # V4.1: Move variance tracking
            move_variance=data.get("move_variance", 0.0),
            move_mean_for_variance=data.get("move_mean_for_variance", 0.0),
            node_type=data.get("node_type", "dependent"),
            min_independent_count=data.get("min_independent_count", 10),
            # V4.2: Observation freshness
            last_observation_time=data.get("last_observation_time", 0.0),
            observation_timespan=data.get("observation_timespan", 0.0),
        )
