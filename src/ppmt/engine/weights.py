"""
Adaptive Weight Management for Multi-Level PPMT

Distributes confidence across the Trie levels:
  N1: Universal (all assets, all regimes)
  N2: Asset Class (Blue Chip, Large Cap, Mid Cap, DeFi, Meme, New Launch)
  N3: Per-Asset
  N4: Per-Asset + Regime
  N5: Per-Asset + BTC Context (1m only)

Default weights (5m): N1=10%, N2=0%, N3=55%, N4=35%  (v0.53.0: N2 removed)
Default weights (1m): N1=35%, N2=0%, N3=55%, N4=10%, N5=0%
Default weights (15m): N1=10%, N2=30%, N3=30%, N4=30%

For meme/new assets (5m) — same as default (N2=0% for all):
  N1=10%, N2=0%, N3=55%, N4=35%

For meme assets (1m) — micro-structure prioritized:
  N1=35%, N2=0%, N3=55%, N4=10%, N5=0%

Weight redistribution rules:
  - If a level has < min_observations, its weight redistributes
    proportionally to the other levels
  - New assets start with meme weights and graduate as data grows
  - Graduation thresholds are configurable
  - v0.52.0: 1m timeframe uses micro-structure-first weights where N3/N4
    (W=10 micro-structure) dominate over N1/N2 (W=60 macro context)
  - v0.53.0: 5m also uses micro-structure-first weights (N2=0%, N3=55%, N4=35%)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

import numpy as np


# Predefined weight profiles (5m/15m baseline — overridden for 1m below)
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

# v0.52.0: Per-timeframe weight overrides.
# In 1m, N3/N4 look at W=10 (micro-structure) and are the PRIMARY signal.
# N1/N2 look at W=60 (macro) and provide CONTEXT, not the entry signal.
# N5 (BTC context) is only present in 1m.
#
# v0.52.0-hotfix: After OOS validation on DOGE/USDT 1m (500 candles):
#   - N1 (universal pool) = 0.4044 confidence (229 obs, 45.4% WR) — STRONGEST
#   - N3 (per-asset)      = 0.3897 confidence (118 obs, 46.6% WR) — 2nd
#   - N4 (per-asset+reg)  = 0.3546 confidence (15 obs, 60% WR)   — 3rd, sparse
#   - N2 (class pool)     = 0.2443 confidence (63 obs, 28.6% WR)  — WEAKEST
#   - N5 (BTC context)    = 0.0000 (trie not built yet)
#
# Key findings:
# 1. N1 (universal) is the STRONGEST signal in 1m — cross-asset transfer
#    learning works. Old meme profile gave N1=10%, which was wrong.
# 2. N2 (meme class pool) is the WEAKEST signal with only 28.6% WR.
#    Old meme profile gave N2=60%, which was catastrophically wrong.
# 3. N5 trie not built yet — set to 10% for future activation.
# 4. The weighted_confidence ceiling is set by the strongest active level.
#    With N1≈0.43 and N3≈0.39, the theoretical max is ~0.43.
#
# Profile: N1+N3 dominate, N2=0 (too weak for 1m), N4 moderate, N5 reserved.
# N5 set to 0 since trie doesn't exist yet. When built, re-enable at 0.10.
#
# v0.53.0: 5m now also uses N2=0% — same root cause as 1m.
# The N2 asset-class pool is too generic (α=3, P=5 = 243 patterns shared
# across all assets in the class). This gives confidence ~0.20-0.27, which
# dominates the weighted average when N2=60% (meme) or 30% (default).
# Solution: N2=0%, boost N3 (per-asset, most specific) and N4 (per-asset+regime).
# Redistribution logic also switched to micro-structure-first for 5m.
TIMEFRAME_WEIGHT_OVERRIDES = {
    "1m": {
        "default": {
            "n1_universal": 0.35,
            "n2_asset_class": 0.00,
            "n3_per_asset": 0.55,
            "n4_per_asset_regime": 0.10,
            "n5_btc_context": 0.00,
        },
        "meme": {
            "n1_universal": 0.35,
            "n2_asset_class": 0.00,
            "n3_per_asset": 0.55,
            "n4_per_asset_regime": 0.10,
            "n5_btc_context": 0.00,
        },
        "new_launch": {
            "n1_universal": 0.35,
            "n2_asset_class": 0.00,
            "n3_per_asset": 0.55,
            "n4_per_asset_regime": 0.10,
            "n5_btc_context": 0.00,
        },
        "blue_chip": {
            "n1_universal": 0.30,
            "n2_asset_class": 0.00,
            "n3_per_asset": 0.60,
            "n4_per_asset_regime": 0.10,
            "n5_btc_context": 0.00,
        },
    },
    "5m": {
        # v2.1 (TERMINAL-v2.1): Config C — validated 30-day OOS on 4 tokens.
        # P&L=+31.00%, PF=1.52, WR=45.7%, MaxDD=18.29%
        # Key: N3=80% (per-asset dominant), N4=10% (sparse, regime-specific)
        "default": {
            "n1_universal": 0.10,
            "n2_asset_class": 0.00,
            "n3_per_asset": 0.80,
            "n4_per_asset_regime": 0.10,
            "n5_btc_context": 0.00,
        },
        "meme": {
            "n1_universal": 0.10,
            "n2_asset_class": 0.00,
            "n3_per_asset": 0.80,
            "n4_per_asset_regime": 0.10,
            "n5_btc_context": 0.00,
        },
        "new_launch": {
            "n1_universal": 0.10,
            "n2_asset_class": 0.00,
            "n3_per_asset": 0.80,
            "n4_per_asset_regime": 0.10,
            "n5_btc_context": 0.00,
        },
        "blue_chip": {
            "n1_universal": 0.10,
            "n2_asset_class": 0.00,
            "n3_per_asset": 0.80,
            "n4_per_asset_regime": 0.10,
            "n5_btc_context": 0.00,
        },
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
    Adaptive weight manager for the multi-level PPMT architecture.

    Weights determine how much each Trie level contributes to the
    final signal confidence. They adapt based on data availability
    and quality at each level.

    Key principles:
    1. More specific levels (N3, N4) get more weight when data is rich
    2. Less specific levels (N1, N2) compensate when data is sparse
    3. Dead asset knowledge transfers through N2 persistence
    4. v0.52.0: In 1m, micro-structure levels (N3/N4/N5) dominate
       because N1/N2 use W=60 (macro context, not entry signal)
    """

    # Current weights
    n1_universal: float = 0.10
    n2_asset_class: float = 0.30
    n3_per_asset: float = 0.30
    n4_per_asset_regime: float = 0.30
    n5_btc_context: float = 0.0  # v0.52.0: N5 weight, only >0 for 1m

    # Minimum observations before a level gets its full weight
    min_observations: int = 50

    # Graduation threshold: observations needed for 'default' weights
    graduation_threshold: int = 500

    # Current profile
    profile: str = "default"

    # v0.52.0: Timeframe for per-TF weight overrides
    timeframe: str = ""

    @classmethod
    def from_profile(
        cls,
        profile: Literal["default", "meme", "new_launch", "blue_chip"],
        timeframe: str = "",
    ) -> AdaptiveWeights:
        """Create weights from a predefined profile.

        v0.52.0: When timeframe is provided and has overrides in
        TIMEFRAME_WEIGHT_OVERRIDES, those take precedence over the
        base WEIGHT_PROFILES. This allows 1m to use micro-structure-
        first weights while 5m/15m use the classic macro-first profile.
        """
        # Check for per-timeframe override first
        if timeframe and timeframe in TIMEFRAME_WEIGHT_OVERRIDES:
            tf_overrides = TIMEFRAME_WEIGHT_OVERRIDES[timeframe]
            if profile in tf_overrides:
                pw = tf_overrides[profile]
                return cls(
                    n1_universal=pw["n1_universal"],
                    n2_asset_class=pw["n2_asset_class"],
                    n3_per_asset=pw["n3_per_asset"],
                    n4_per_asset_regime=pw["n4_per_asset_regime"],
                    n5_btc_context=pw.get("n5_btc_context", 0.0),
                    profile=profile,
                    timeframe=timeframe,
                )

        # Fallback to base profile (5m/15m or missing TF override)
        pw = WEIGHT_PROFILES[profile]
        return cls(
            n1_universal=pw["n1_universal"],
            n2_asset_class=pw["n2_asset_class"],
            n3_per_asset=pw["n3_per_asset"],
            n4_per_asset_regime=pw["n4_per_asset_regime"],
            n5_btc_context=0.0,
            profile=profile,
            timeframe=timeframe,
        )

    def to_array(self, include_n5: bool = False) -> np.ndarray:
        """Return weights as a numpy array.

        Args:
            include_n5: If True, include N5 weight (5-element array).
                If False, return [n1, n2, n3, n4] (4-element array).
        """
        base = [
            self.n1_universal,
            self.n2_asset_class,
            self.n3_per_asset,
            self.n4_per_asset_regime,
        ]
        if include_n5:
            base.append(self.n5_btc_context)
        return np.array(base)

    def normalize(self) -> None:
        """Ensure weights sum to 1.0 (including N5 when present)."""
        total = (self.n1_universal + self.n2_asset_class + self.n3_per_asset
                 + self.n4_per_asset_regime + self.n5_btc_context)
        if total > 0:
            self.n1_universal /= total
            self.n2_asset_class /= total
            self.n3_per_asset /= total
            self.n4_per_asset_regime /= total
            self.n5_btc_context /= total

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
        n5_confidence: float = 0.0,
    ) -> float:
        """
        Compute the weighted confidence across all active levels.

        This is the final confidence score that determines whether
        a signal is generated. It combines evidence from all levels,
        with more specific levels weighted higher (when data allows).

        v0.52.0: N5 (BTC Context) is now part of the weight system
        instead of a hardcoded post-hoc blend. When n5_confidence > 0
        and self.n5_btc_context > 0, N5 participates in the weighted
        average naturally.

        Args:
            n1_confidence: Confidence from Universal Trie
            n2_confidence: Confidence from Asset Class Trie
            n3_confidence: Confidence from Per-Asset Trie
            n4_confidence: Confidence from Per-Asset+Regime Trie
            n5_confidence: Confidence from BTC Context Trie (1m only)

        Returns:
            Weighted confidence score (0-1)
        """
        has_n5 = self.n5_btc_context > 0 and n5_confidence > 0
        confidences = np.array([n1_confidence, n2_confidence, n3_confidence, n4_confidence]
                              + ([n5_confidence] if has_n5 else []))
        weights = self.to_array(include_n5=has_n5)

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
                             n2_avg_obs: float = 0.0,
                             n5_pattern_count: int = 0) -> 'AdaptiveWeights':
        """Compute safe weights for tokens with immature local tries.

        If N3 has < 20 patterns, redistribute its weight.
        If N4 has < 10 patterns, redistribute its weight.

        v0.43.0: When N2 is also sparse (avg_obs < 2), shift MORE weight
        to N1 (universal pool) which is always dense (243 patterns, ~27 obs/node).
        This prevents a sparse N2 from dominating confidence when N3/N4 are empty,
        which was causing weighted_conf to stay below 0.20 even when N1 conf was 0.30+.

        v0.52.0: For 1m timeframe, redistribution prioritizes micro-structure
        levels (N3/N4/N5) over macro context (N1/N2). When N3/N4 are sparse in 1m,
        their weight goes to N4/N5 (other micro-structure) rather than N1/N2.
        When N2 is sparse in 1m, its weight shifts to N3 (main signal), not N1.

        N5 sparsity: If N5 has < 5 patterns, redistribute to N3/N4.

        The redistribution logic (v0.53.0: 5m now same as 1m):
        1. N3/N4 weight → redistribute (1m/5m: to N4/N5; 15m: to N1/N2)
        2. If N2 is sparse: (1m/5m: shift to N3; 15m: shift to N1)
        3. If N5 is sparse in 1m/5m: shift to N3/N4

        Args:
            n3_pattern_count: Number of patterns in the per-asset (N3) trie.
            n4_pattern_count: Number of patterns in the per-asset+regime (N4) trie.
            n2_avg_obs: Average observations per node in N2 class pool.
                When < 2, N2 is considered sparse and weight shifts.
            n5_pattern_count: Number of patterns in the BTC context (N5) trie.
                Only relevant for 1m timeframe.

        Returns:
            self (for chaining)
        """
        MIN_N3_PATTERNS = 20
        MIN_N4_PATTERNS = 10
        MIN_N5_PATTERNS = 5
        MIN_N2_OBS = 2.0  # Below this, N2 is considered sparse

        # v0.53.0: micro-structure-first redistribution for 1m AND 5m.
        # Both timeframes now have N2=0%, so sparse weight should go to
        # N3/N4 (micro-structure) rather than N1/N2 (macro context).
        micro_first = self.timeframe in ("1m", "5m")

        n3_weight = self.n3_per_asset
        n4_weight = self.n4_per_asset_regime

        if n3_pattern_count < MIN_N3_PATTERNS:
            redistribute = n3_weight * (1 - n3_pattern_count / MIN_N3_PATTERNS)
            n3_weight -= redistribute
            if micro_first:
                # 1m: Redistribute N3 weight to N4 and N5 (micro-structure peers)
                total_micro = self.n4_per_asset_regime + self.n5_btc_context
                if total_micro > 0:
                    self.n4_per_asset_regime += redistribute * (self.n4_per_asset_regime / total_micro)
                    self.n5_btc_context += redistribute * (self.n5_btc_context / total_micro)
                else:
                    # Fallback to N1/N2 if N4/N5 are also empty
                    total_n1n2 = self.n1_universal + self.n2_asset_class
                    if total_n1n2 > 0:
                        self.n1_universal += redistribute * (self.n1_universal / total_n1n2)
                        self.n2_asset_class += redistribute * (self.n2_asset_class / total_n1n2)
            else:
                # 5m/15m: Redistribute N3 weight to N1/N2 (classic behavior)
                total_n1n2 = self.n1_universal + self.n2_asset_class
                if total_n1n2 > 0:
                    self.n1_universal += redistribute * (self.n1_universal / total_n1n2)
                    self.n2_asset_class += redistribute * (self.n2_asset_class / total_n1n2)
            self.n3_per_asset = n3_weight

        if n4_pattern_count < MIN_N4_PATTERNS:
            redistribute = n4_weight * (1 - n4_pattern_count / MIN_N4_PATTERNS)
            n4_weight -= redistribute
            if micro_first:
                # 1m: Redistribute N4 weight to N3 and N5 (micro-structure peers)
                total_micro = self.n3_per_asset + self.n5_btc_context
                if total_micro > 0:
                    self.n3_per_asset += redistribute * (self.n3_per_asset / total_micro)
                    self.n5_btc_context += redistribute * (self.n5_btc_context / total_micro)
                else:
                    total_n1n2 = self.n1_universal + self.n2_asset_class
                    if total_n1n2 > 0:
                        self.n1_universal += redistribute * (self.n1_universal / total_n1n2)
                        self.n2_asset_class += redistribute * (self.n2_asset_class / total_n1n2)
            else:
                total_n1n2 = self.n1_universal + self.n2_asset_class
                if total_n1n2 > 0:
                    self.n1_universal += redistribute * (self.n1_universal / total_n1n2)
                    self.n2_asset_class += redistribute * (self.n2_asset_class / total_n1n2)
            self.n4_per_asset_regime = n4_weight

        # N5 sparsity check (1m only)
        if micro_first and self.n5_btc_context > 0 and n5_pattern_count < MIN_N5_PATTERNS:
            redistribute = self.n5_btc_context * (1 - n5_pattern_count / MIN_N5_PATTERNS)
            self.n5_btc_context -= redistribute
            # Redistribute to N3 and N4 (micro-structure peers)
            total_micro = self.n3_per_asset + self.n4_per_asset_regime
            if total_micro > 0:
                self.n3_per_asset += redistribute * (self.n3_per_asset / total_micro)
                self.n4_per_asset_regime += redistribute * (self.n4_per_asset_regime / total_micro)

        # v0.43.0: If N2 is sparse, shift its weight.
        # v0.52.0: In 1m, shift to N3 (main signal) instead of N1.
        if n2_avg_obs < MIN_N2_OBS and n2_avg_obs > 0:
            sparsity_factor = 1.0 - (n2_avg_obs / MIN_N2_OBS)
            shift = self.n2_asset_class * sparsity_factor * 0.5  # Cap at 50% shift
            self.n2_asset_class -= shift
            if micro_first:
                self.n3_per_asset += shift  # N3 is the main signal in 1m
            else:
                self.n1_universal += shift  # N1 is the fallback in 5m/15m

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
        d = {
            "n1_universal": round(self.n1_universal, 4),
            "n2_asset_class": round(self.n2_asset_class, 4),
            "n3_per_asset": round(self.n3_per_asset, 4),
            "n4_per_asset_regime": round(self.n4_per_asset_regime, 4),
            "n5_btc_context": round(self.n5_btc_context, 4),
            "profile": self.profile,
            "timeframe": self.timeframe,
            "min_observations": self.min_observations,
            "graduation_threshold": self.graduation_threshold,
        }
        return d

    def __repr__(self) -> str:
        n5_part = f", N5={self.n5_btc_context:.0%}" if self.n5_btc_context > 0 else ""
        tf_part = f", tf='{self.timeframe}'" if self.timeframe else ""
        return (
            f"AdaptiveWeights(N1={self.n1_universal:.0%}, "
            f"N2={self.n2_asset_class:.0%}, "
            f"N3={self.n3_per_asset:.0%}, "
            f"N4={self.n4_per_asset_regime:.0%}"
            f"{n5_part}{tf_part}, "
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
