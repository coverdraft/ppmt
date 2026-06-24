"""
PPMT v7 — Trie Node Metadata (F1)
==================================

Simplified metadata for v7 trie nodes. Replaces the 27-field
BlockLifecycleMetadata from v0.x with an 8-field regression-focused
structure.

Key differences from v0.x:
- NO SAX symbols (works with OHLCV composite quantized keys)
- NO LONG/SHORT classification (regression on fwd_ret_15m)
- NO SL/TP simulation (risk layer handles that separately)
- NO continuation/break nodes (predictive exit concept removed)
- Uses Welford's online algorithm for variance (memory-efficient)
- Numeric vol_regime (0-3) instead of string regime names

Reference: PPMT_v7_MASTER_PLAN.md §4.3
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class RegimeStatsV6:
    """
    Per-regime statistics for a trie node.
    
    Tracks count, sum, and sum-of-squares of fwd_ret_15m observations
    that occurred under a specific vol_regime (0-3).
    """
    count: int = 0
    sum_fwd_ret: float = 0.0
    sum_sq_fwd_ret: float = 0.0
    last_observation_time: float = 0.0
    
    def update(self, fwd_ret: float, ts: float) -> None:
        """Add a new observation to this regime's stats."""
        self.count += 1
        self.sum_fwd_ret += fwd_ret
        self.sum_sq_fwd_ret += fwd_ret * fwd_ret
        if ts > self.last_observation_time:
            self.last_observation_time = ts
    
    @property
    def mean(self) -> float:
        """Mean fwd_ret_15m for this regime."""
        if self.count == 0:
            return 0.0
        return self.sum_fwd_ret / self.count
    
    @property
    def variance(self) -> float:
        """Variance using Welford's numerically-stable formula."""
        if self.count < 2:
            return 0.0
        mean = self.mean
        # Var = E[X^2] - (E[X])^2
        return max(0.0, (self.sum_sq_fwd_ret / self.count) - mean * mean)
    
    @property
    def std(self) -> float:
        """Standard deviation."""
        return math.sqrt(self.variance)
    
    @property
    def prediction(self) -> float:
        """
        Best estimate of fwd_ret_15m for this regime.
        Returns mean if enough observations, else 0.0 (no signal).
        """
        if self.count < 3:  # min observations for trustworthy prediction
            return 0.0
        return self.mean
    
    @property
    def confidence(self) -> float:
        """
        Confidence in the prediction, based on:
        - Count (more observations = more confidence)
        - Variance (lower variance = more confidence)
        
        Returns a value in [0, 1].
        """
        if self.count == 0:
            return 0.0
        # Confidence grows with sqrt(count) but is capped
        count_factor = min(1.0, math.sqrt(self.count / 30.0))  # saturates at 30 obs
        # Variance factor: lower variance = higher confidence
        # Typical fwd_ret_15m std is ~0.5%, so 0.5% variance = 0.5 factor
        if self.variance > 0:
            var_factor = 1.0 / (1.0 + self.variance * 4.0)  # 0.25% var -> 0.5 factor
        else:
            var_factor = 1.0
        return count_factor * var_factor
    
    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "sum_fwd_ret": self.sum_fwd_ret,
            "sum_sq_fwd_ret": self.sum_sq_fwd_ret,
            "last_observation_time": self.last_observation_time,
        }
    
    @classmethod
    def from_dict(cls, d: dict) -> "RegimeStatsV6":
        return cls(
            count=d.get("count", 0),
            sum_fwd_ret=d.get("sum_fwd_ret", 0.0),
            sum_sq_fwd_ret=d.get("sum_sq_fwd_ret", 0.0),
            last_observation_time=d.get("last_observation_time", 0.0),
        )


@dataclass
class TrieNodeV6Metadata:
    """
    v7 trie node metadata — 8 fields, regression-focused.
    
    Replaces the 27-field BlockLifecycleMetadata from v0.x.
    
    Fields:
        historical_count: total observations across all regimes
        sum_fwd_ret_15m: cumulative fwd_ret_15m (for global mean)
        sum_sq_fwd_ret_15m: cumulative squared (for global variance via Welford)
        last_observation_time: epoch seconds of most recent insert
        vol_regime_distribution: count of observations per vol_regime {0: N, 1: M, ...}
        vol_regime_stats: per-regime RegimeStatsV6 (lazy-populated)
        node_type: "independent" (mature) or "dependent" (sparse)
        trading_observations: count of observations used in live trading decisions
                              (gates whether node's prediction is trustworthy)
    
    Derived (not stored):
        mean_fwd_ret_15m: sum / count
        variance_fwd_ret_15m: Welford's online
        std_fwd_ret_15m: sqrt(variance)
        prediction: mean if historical_count >= min_obs else 0.0
        confidence: count_factor * var_factor (in [0, 1])
        freshness_decay: exponential decay based on last_observation_time
    """
    
    # Stored fields (8 total — keep this number low for memory efficiency)
    historical_count: int = 0
    sum_fwd_ret_15m: float = 0.0
    sum_sq_fwd_ret_15m: float = 0.0
    last_observation_time: float = 0.0
    vol_regime_distribution: Dict[int, int] = field(default_factory=dict)
    vol_regime_stats: Dict[int, RegimeStatsV6] = field(default_factory=dict)
    node_type: str = "dependent"  # "independent" if mature, "dependent" if sparse
    trading_observations: int = 0
    
    # Configuration (class-level, not stored per-node)
    MIN_INDEPENDENT_COUNT: int = 10  # threshold for "independent" node_type
    MIN_OBS_FOR_PREDICTION: int = 3  # min observations for trustworthy prediction
    FRESHNESS_HALF_LIFE_HOURS: float = 24.0  # 24h half-life for online learning
    
    def update_from_observation(
        self,
        fwd_ret_15m: float,
        vol_regime: int,
        timestamp: float,
        is_trading_observation: bool = False,
    ) -> None:
        """
        Add a new observation to this node.
        
        CRITICAL: This must be called AFTER prediction is made for the
        candle, never before. Enforced by Layer 1 (Trie online) which
        delays insertion by 15 minutes (the fwd_ret_15m lookahead).
        
        Args:
            fwd_ret_15m: forward 15m return (the regression target)
            vol_regime: 0-3 (low to extreme volatility)
            timestamp: epoch seconds of the candle close
            is_trading_observation: True if this obs was used in a live
                                    trading decision (gates trust)
        """
        self.historical_count += 1
        self.sum_fwd_ret_15m += fwd_ret_15m
        self.sum_sq_fwd_ret_15m += fwd_ret_15m * fwd_ret_15m
        self.last_observation_time = max(self.last_observation_time, timestamp)
        
        # Update regime distribution
        self.vol_regime_distribution[vol_regime] = (
            self.vol_regime_distribution.get(vol_regime, 0) + 1
        )
        
        # Update per-regime stats (lazy-init)
        if vol_regime not in self.vol_regime_stats:
            self.vol_regime_stats[vol_regime] = RegimeStatsV6()
        self.vol_regime_stats[vol_regime].update(fwd_ret_15m, timestamp)
        
        # Update trading observation counter
        if is_trading_observation:
            self.trading_observations += 1
        
        # Update node_type (independent if mature enough)
        if self.historical_count >= self.MIN_INDEPENDENT_COUNT:
            self.node_type = "independent"
    
    # ---- Derived properties (computed on demand, not stored) ----
    
    @property
    def mean_fwd_ret_15m(self) -> float:
        """Global mean fwd_ret_15m across all regimes."""
        if self.historical_count == 0:
            return 0.0
        return self.sum_fwd_ret_15m / self.historical_count
    
    @property
    def variance_fwd_ret_15m(self) -> float:
        """Global variance using Welford's numerically-stable formula."""
        if self.historical_count < 2:
            return 0.0
        mean = self.mean_fwd_ret_15m
        # Var = E[X^2] - (E[X])^2
        return max(0.0, (self.sum_sq_fwd_ret_15m / self.historical_count) - mean * mean)
    
    @property
    def std_fwd_ret_15m(self) -> float:
        """Global standard deviation."""
        return math.sqrt(self.variance_fwd_ret_15m)
    
    @property
    def prediction(self) -> float:
        """
        Best estimate of fwd_ret_15m for this node.
        Returns mean if enough observations, else 0.0 (no signal).
        """
        if self.historical_count < self.MIN_OBS_FOR_PREDICTION:
            return 0.0
        return self.mean_fwd_ret_15m
    
    @property
    def prediction_for_current_regime(self) -> float:
        """
        N2 prediction: mean(fwd_ret_15m) for the most-recently-updated
        regime. The caller should ideally pass the current vol_regime,
        but if not, we use the last-updated regime as a proxy.
        
        For explicit regime lookup, use prediction_for_regime(vol_regime).
        """
        if not self.vol_regime_stats:
            return 0.0
        # Use the regime with most recent observation
        latest_regime = max(
            self.vol_regime_stats.keys(),
            key=lambda r: self.vol_regime_stats[r].last_observation_time,
        )
        return self.vol_regime_stats[latest_regime].prediction
    
    def prediction_for_regime(self, vol_regime: int) -> float:
        """
        N2 prediction: mean(fwd_ret_15m) for a specific vol_regime.
        Returns 0.0 if no observations for that regime.
        """
        if vol_regime not in self.vol_regime_stats:
            return 0.0
        return self.vol_regime_stats[vol_regime].prediction
    
    @property
    def confidence(self) -> float:
        """
        Confidence in the global prediction, in [0, 1].
        
        Combines:
        - Count factor: sqrt(count / saturation_count), capped at 1.0
        - Variance factor: 1 / (1 + variance * 4)
        
        Saturation count = 30 (after 30 obs, more don't help much)
        Variance scale: 0.25%² variance -> 0.5 factor (typical for crypto)
        """
        if self.historical_count == 0:
            return 0.0
        count_factor = min(1.0, math.sqrt(self.historical_count / 30.0))
        if self.variance_fwd_ret_15m > 0:
            var_factor = 1.0 / (1.0 + self.variance_fwd_ret_15m * 4.0)
        else:
            var_factor = 1.0
        return count_factor * var_factor
    
    @property
    def freshness_decay(self) -> float:
        """
        Exponential decay based on time since last observation.
        
        Returns a multiplier in [0, 1]:
        - 1.0 if observed in the last few minutes
        - 0.5 if observed one half-life ago (24h)
        - 0.25 if observed two half-lives ago (48h)
        - Approaches 0 for very stale nodes
        
        Half-life = 24h (configurable via FRESHNESS_HALF_LIFE_HOURS).
        """
        if self.last_observation_time == 0:
            return 0.0  # never observed
        now = time.time()
        elapsed_hours = (now - self.last_observation_time) / 3600.0
        if elapsed_hours < 0:
            # Clock skew or backtest mode — treat as fresh
            return 1.0
        # Exponential decay: 0.5^(elapsed / half_life)
        return 0.5 ** (elapsed_hours / self.FRESHNESS_HALF_LIFE_HOURS)
    
    @property
    def is_trustworthy(self) -> bool:
        """
        Whether this node's prediction should be trusted for trading.
        
        A node is trustworthy if:
        - Has at least MIN_OBS_FOR_PREDICTION observations
        - Has been used in at least 1 trading decision (trading_observations > 0)
          OR has at least 5 historical observations (build-time maturity)
        - Freshness decay > 0.1 (not too stale)
        """
        if self.historical_count < self.MIN_OBS_FOR_PREDICTION:
            return False
        if self.trading_observations == 0 and self.historical_count < 5:
            return False
        if self.freshness_decay < 0.1:
            return False
        return True
    
    @property
    def dominant_regime(self) -> int:
        """
        The vol_regime with most observations. Returns -1 if empty.
        """
        if not self.vol_regime_distribution:
            return -1
        return max(self.vol_regime_distribution.items(), key=lambda x: x[1])[0]
    
    @property
    def regime_concentration(self) -> float:
        """
        How concentrated observations are in one regime.
        Returns 0.0 (uniform across 4 regimes) to 1.0 (all in one regime).
        Useful for detecting regime-specific patterns.
        """
        if not self.vol_regime_distribution:
            return 0.0
        total = sum(self.vol_regime_distribution.values())
        if total == 0:
            return 0.0
        max_count = max(self.vol_regime_distribution.values())
        return max_count / total
    
    # ---- Serialization ----
    
    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "historical_count": self.historical_count,
            "sum_fwd_ret_15m": self.sum_fwd_ret_15m,
            "sum_sq_fwd_ret_15m": self.sum_sq_fwd_ret_15m,
            "last_observation_time": self.last_observation_time,
            "vol_regime_distribution": dict(self.vol_regime_distribution),
            "vol_regime_stats": {
                str(r): s.to_dict() for r, s in self.vol_regime_stats.items()
            },
            "node_type": self.node_type,
            "trading_observations": self.trading_observations,
        }
    
    @classmethod
    def from_dict(cls, d: dict) -> "TrieNodeV6Metadata":
        """Deserialize from dict."""
        regime_stats = {}
        for r_str, s_dict in d.get("vol_regime_stats", {}).items():
            regime_stats[int(r_str)] = RegimeStatsV6.from_dict(s_dict)
        return cls(
            historical_count=d.get("historical_count", 0),
            sum_fwd_ret_15m=d.get("sum_fwd_ret_15m", 0.0),
            sum_sq_fwd_ret_15m=d.get("sum_sq_fwd_ret_15m", 0.0),
            last_observation_time=d.get("last_observation_time", 0.0),
            vol_regime_distribution={
                int(k): v for k, v in d.get("vol_regime_distribution", {}).items()
            },
            vol_regime_stats=regime_stats,
            node_type=d.get("node_type", "dependent"),
            trading_observations=d.get("trading_observations", 0),
        )
    
    def __repr__(self) -> str:
        return (
            f"TrieNodeV6Metadata(count={self.historical_count}, "
            f"mean={self.mean_fwd_ret_15m:.4f}%, "
            f"std={self.std_fwd_ret_15m:.4f}%, "
            f"regimes={len(self.vol_regime_distribution)}, "
            f"type={self.node_type})"
        )
