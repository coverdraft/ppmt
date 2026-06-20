"""
Adaptive Weight Management for 4-Level PPMT

Distributes confidence across the 4 Trie levels:
  N1: Universal (all assets, all regimes)
  N2: Asset Class (Blue Chip, Large Cap, Mid Cap, DeFi, Meme, New Launch)
  N3: Per-Asset
  N4: Per-Asset + Regime

Default weights: N1=10%, N2=30%, N3=30%, N4=30%

For meme/new assets with insufficient data:
  N1=10%, N2=60%, N3=20%, N4=10%

Weight redistribution rules:
  - If a level has < min_observations, its weight redistributes
    proportionally to the other levels
  - New assets start with meme weights and graduate as data grows
  - Graduation thresholds are configurable
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

import numpy as np


# Predefined weight profiles
WEIGHT_PROFILES = {
    "default": {
        "n1_universal": 0.10,
        "n2_asset_class": 0.30,
        "n3_per_asset": 0.30,
        "n4_per_asset_regime": 0.30,
    },
    "meme": {
        "n1_universal": 0.10,
        "n2_asset_class": 0.60,
        "n3_per_asset": 0.20,
        "n4_per_asset_regime": 0.10,
    },
    "new_launch": {
        "n1_universal": 0.15,
        "n2_asset_class": 0.55,
        "n3_per_asset": 0.20,
        "n4_per_asset_regime": 0.10,
    },
    "blue_chip": {
        "n1_universal": 0.05,
        "n2_asset_class": 0.20,
        "n3_per_asset": 0.35,
        "n4_per_asset_regime": 0.40,
    },
}


@dataclass
class LevelStats:
    """Statistics for a single Trie level, used for weight adaptation."""
    pattern_count: int = 0
    avg_historical_count: float = 0.0
    avg_win_rate: float = 0.0
    avg_confidence: float = 0.0


@dataclass
class AdaptiveWeights:
    """
    Adaptive weight manager for the 4-level PPMT architecture.

    Weights determine how much each Trie level contributes to the
    final signal confidence. They adapt based on data availability
    and quality at each level.

    Key principles:
    1. More specific levels (N3, N4) get more weight when data is rich
    2. Less specific levels (N1, N2) compensate when data is sparse
    3. Dead asset knowledge transfers through N2 persistence
    """

    # Current weights
    n1_universal: float = 0.10
    n2_asset_class: float = 0.30
    n3_per_asset: float = 0.30
    n4_per_asset_regime: float = 0.30

    # Minimum observations before a level gets its full weight
    min_observations: int = 50

    # Graduation threshold: observations needed for 'default' weights
    graduation_threshold: int = 500

    # Current profile
    profile: str = "default"

    @classmethod
    def from_profile(
        cls,
        profile: Literal["default", "meme", "new_launch", "blue_chip"],
    ) -> AdaptiveWeights:
        """Create weights from a predefined profile."""
        pw = WEIGHT_PROFILES[profile]
        return cls(
            n1_universal=pw["n1_universal"],
            n2_asset_class=pw["n2_asset_class"],
            n3_per_asset=pw["n3_per_asset"],
            n4_per_asset_regime=pw["n4_per_asset_regime"],
            profile=profile,
        )

    def to_array(self) -> np.ndarray:
        """Return weights as a numpy array [n1, n2, n3, n4]."""
        return np.array([
            self.n1_universal,
            self.n2_asset_class,
            self.n3_per_asset,
            self.n4_per_asset_regime,
        ])

    def normalize(self) -> None:
        """Ensure weights sum to 1.0."""
        total = self.n1_universal + self.n2_asset_class + self.n3_per_asset + self.n4_per_asset_regime
        if total > 0:
            self.n1_universal /= total
            self.n2_asset_class /= total
            self.n3_per_asset /= total
            self.n4_per_asset_regime /= total

    def adapt(
        self,
        level_stats: dict[str, LevelStats],
    ) -> None:
        """
        Adapt weights based on data availability at each level.

        If a level has insufficient observations, its weight
        redistributes proportionally to the other levels.

        This is the key mechanism that makes PPMT work for
        new/meme assets with limited data.

        Args:
            level_stats: Statistics for each level
                Keys: 'n1', 'n2', 'n3', 'n4'
        """
        weights = self.to_array()
        level_keys = ['n1', 'n2', 'n3', 'n4']

        # Check which levels have sufficient data
        sufficient = []
        insufficient = []

        for i, key in enumerate(level_keys):
            stats = level_stats.get(key, LevelStats())
            if stats.pattern_count >= self.min_observations:
                sufficient.append(i)
            else:
                insufficient.append(i)

        # Redistribute weight from insufficient to sufficient levels
        if insufficient and sufficient:
            # Calculate weight to redistribute
            redistribute_total = sum(weights[i] for i in insufficient)

            # Proportional redistribution based on existing weights
            sufficient_weights = sum(weights[i] for i in sufficient)
            if sufficient_weights > 0:
                for i in insufficient:
                    weights[i] = 0.0

                for i in sufficient:
                    share = weights[i] / sufficient_weights
                    weights[i] += redistribute_total * share

        # Apply quality bonus: levels with higher avg_confidence get a boost
        quality_scores = np.zeros(4)
        for i, key in enumerate(level_keys):
            stats = level_stats.get(key, LevelStats())
            quality_scores[i] = stats.avg_confidence if stats.pattern_count > 0 else 0.0

        if quality_scores.sum() > 0:
            # Small quality adjustment (max 10% shift)
            quality_normalized = quality_scores / quality_scores.sum()
            weights = weights * 0.9 + quality_normalized * 0.1

        # Store back
        self.n1_universal = weights[0]
        self.n2_asset_class = weights[1]
        self.n3_per_asset = weights[2]
        self.n4_per_asset_regime = weights[3]

        # Normalize
        self.normalize()

    def compute_weighted_confidence(
        self,
        n1_confidence: float,
        n2_confidence: float,
        n3_confidence: float,
        n4_confidence: float,
    ) -> float:
        """
        Compute the weighted confidence across all 4 levels.

        This is the final confidence score that determines whether
        a signal is generated. It combines evidence from all levels,
        with more specific levels weighted higher (when data allows).

        Args:
            n1_confidence: Confidence from Universal Trie
            n2_confidence: Confidence from Asset Class Trie
            n3_confidence: Confidence from Per-Asset Trie
            n4_confidence: Confidence from Per-Asset+Regime Trie

        Returns:
            Weighted confidence score (0-1)
        """
        confidences = np.array([n1_confidence, n2_confidence, n3_confidence, n4_confidence])
        weights = self.to_array()

        # Weighted average
        total_weight = 0.0
        weighted_sum = 0.0

        for w, c in zip(weights, confidences):
            if c > 0:  # Only count levels with actual data
                weighted_sum += w * c
                total_weight += w

        if total_weight == 0:
            return 0.0

        return weighted_sum / total_weight

    def safe_default_weights(self, n3_pattern_count: int, n4_pattern_count: int,
                             n2_avg_obs: float = 0.0) -> 'AdaptiveWeights':
        """Compute safe weights for tokens with immature local tries.

        If N3 has < 20 patterns, redistribute its weight to N1/N2.
        If N4 has < 10 patterns, redistribute its weight to N1/N2.

        v0.43.0: When N2 is also sparse (avg_obs < 2), shift MORE weight
        to N1 (universal pool) which is always dense (243 patterns, ~27 obs/node).
        This prevents a sparse N2 from dominating confidence when N3/N4 are empty,
        which was causing weighted_conf to stay below 0.20 even when N1 conf was 0.30+.

        The redistribution logic:
        1. N3/N4 weight → redistribute to N1/N2
        2. If N2 is sparse (avg_obs < MIN_N2_OBS), redistribute N2 weight → N1
        3. This gives N1 (dense, reliable) more weight for OOS tokens

        Args:
            n3_pattern_count: Number of patterns in the per-asset (N3) trie.
            n4_pattern_count: Number of patterns in the per-asset+regime (N4) trie.
            n2_avg_obs: Average observations per node in N2 class pool.
                When < 2, N2 is considered sparse and weight shifts to N1.

        Returns:
            self (for chaining)
        """
        MIN_N3_PATTERNS = 20
        MIN_N4_PATTERNS = 10
        MIN_N2_OBS = 2.0  # Below this, N2 is considered sparse

        n3_weight = self.n3_per_asset
        n4_weight = self.n4_per_asset_regime

        if n3_pattern_count < MIN_N3_PATTERNS:
            # Redistribute N3 weight proportionally to N1/N2
            redistribute = n3_weight * (1 - n3_pattern_count / MIN_N3_PATTERNS)
            n3_weight -= redistribute
            # Proportional split to N1/N2 based on their current weights
            total_n1n2 = self.n1_universal + self.n2_asset_class
            if total_n1n2 > 0:
                self.n1_universal += redistribute * (self.n1_universal / total_n1n2)
                self.n2_asset_class += redistribute * (self.n2_asset_class / total_n1n2)
            self.n3_per_asset = n3_weight

        if n4_pattern_count < MIN_N4_PATTERNS:
            redistribute = n4_weight * (1 - n4_pattern_count / MIN_N4_PATTERNS)
            n4_weight -= redistribute
            total_n1n2 = self.n1_universal + self.n2_asset_class
            if total_n1n2 > 0:
                self.n1_universal += redistribute * (self.n1_universal / total_n1n2)
                self.n2_asset_class += redistribute * (self.n2_asset_class / total_n1n2)
            self.n4_per_asset_regime = n4_weight

        # v0.43.0: If N2 is sparse, shift its weight to N1.
        # This is critical for Transfer Learning: N1 (universal) is always dense
        # because it accumulates ALL token observations with α=3 (max 243 patterns).
        # When N2 has < 2 obs/node, its confidence is dominated by the Bayesian prior
        # and provides almost no signal. Shifting weight to N1 ensures the dense,
        # reliable universal pool drives decisions.
        if n2_avg_obs < MIN_N2_OBS and n2_avg_obs > 0:
            # Shift N2 weight to N1 proportional to N2 sparsity
            # If N2 has 0.5 avg_obs → shift 75% of N2 weight to N1
            # If N2 has 1.5 avg_obs → shift 25% of N2 weight to N1
            sparsity_factor = 1.0 - (n2_avg_obs / MIN_N2_OBS)
            shift = self.n2_asset_class * sparsity_factor * 0.5  # Cap at 50% shift
            self.n2_asset_class -= shift
            self.n1_universal += shift

        self.normalize()
        return self

    @property
    def immaturity_factor(self) -> float:
        """0-1 factor: 0 = fully mature, 1 = completely immature.
        Used to reduce position sizing for new tokens."""
        n3_ratio = min(1.0, self.n3_per_asset / 0.20)  # 20% weight = mature
        return max(0.0, 1.0 - n3_ratio)

    @property
    def sizing_multiplier(self) -> float:
        """Position sizing multiplier. Lower for immature tries."""
        return 1.0 - (self.immaturity_factor * 0.7)  # Max 70% reduction

    def should_graduate(self, n3_observations: int, n4_observations: int) -> bool:
        """
        Check if an asset should graduate to 'default' weights.

        Assets start with 'meme' or 'new_launch' weights and
        graduate as they accumulate sufficient history.
        """
        return (
            n3_observations >= self.graduation_threshold
            and n4_observations >= self.graduation_threshold // 2
        )

    def to_dict(self) -> dict:
        """Serialize weights to dictionary."""
        return {
            "n1_universal": round(self.n1_universal, 4),
            "n2_asset_class": round(self.n2_asset_class, 4),
            "n3_per_asset": round(self.n3_per_asset, 4),
            "n4_per_asset_regime": round(self.n4_per_asset_regime, 4),
            "profile": self.profile,
            "min_observations": self.min_observations,
            "graduation_threshold": self.graduation_threshold,
        }

    def __repr__(self) -> str:
        return (
            f"AdaptiveWeights(N1={self.n1_universal:.0%}, "
            f"N2={self.n2_asset_class:.0%}, "
            f"N3={self.n3_per_asset:.0%}, "
            f"N4={self.n4_per_asset_regime:.0%}, "
            f"profile='{self.profile}')"
        )


# ================================================================
# v0.41.0 (FASE 2, Tarea 2.4): Time Decay Function
# ================================================================

def apply_time_decay(
    confidence: float,
    last_seen_timestamp: float,
    half_life_days: float = 30.0,
) -> float:
    """Apply exponential time decay to confidence.

    Patterns seen recently should have higher confidence than old ones.

    Args:
        confidence: Original confidence (0-1)
        last_seen_timestamp: Unix timestamp when pattern was last observed
        half_life_days: Days for confidence to decay by 50%. Default 30 days.

    Returns:
        Decay-adjusted confidence
    """
    if last_seen_timestamp <= 0:
        return confidence  # No timestamp, no decay

    now = time.time()
    days_since = (now - last_seen_timestamp) / 86400.0

    if days_since < 0:
        return confidence  # Future timestamp? Don't decay

    # Exponential decay: conf * 0.5^(days/half_life)
    decay_factor = 0.5 ** (days_since / half_life_days)
    return confidence * decay_factor
