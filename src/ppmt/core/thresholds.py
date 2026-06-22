"""
Threshold configuration for PPMT signal filtering and regime detection.

v0.38.8 — Unifies the 3 previously hardcoded threshold sets that lived in:
  - src/ppmt/engine/signal.py    (regime-adaptive min_confidence + min_rr)
  - src/ppmt/engine/realtime.py  (skip filters: prob gates, move floors, boost)
  - src/ppmt/engine/ppmt.py      (_detect_simple_regime vol/move cutoffs)

All numeric values are PRESERVED from v0.38.7 — this module is a pure
refactor that moves literals into named dataclasses. The only behavioural
fix is that regime names are now lowercase everywhere (matching what
RegimeDetector returns), so signal.py stops falling back to UNKNOWN.

Two factory methods cover the two operating modes:
  - SignalThresholds.paper()  → validation_mode=True  (paper trading)
  - SignalThresholds.real()   → validation_mode=False (real money)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


# -------------------------------------------------------------------- #
# Per-timeframe hard_move_floor
# -------------------------------------------------------------------- #
# v0.43.0 (TERMINAL-v2.1): The old flat hard_move_floor (0.05% paper,
# 0.5% real) caused a critical bug — 100% LONG, 0 SHORT signals in 5m
# replay. Root cause: SHORT moves are typically smaller than LONG moves
# in crypto uptrends, so a single flat floor either:
#   - Was too low (0.05%) → let noise through, SHORT signals survived
#     but were too weak to be useful.
#   - Was too high (0.5%) → killed all SHORT signals because their
#     expected_move was below 0.5% on lower timeframes.
#
# The per-timeframe floor scales with typical candle volatility:
#   1m: ~0.04% per candle → floor 0.10%
#   5m: ~0.10% per candle → floor 0.10%  (v2.1 Config F: lowered from 0.15%)
#   15m: ~0.20% per candle → floor 0.20%
#   1h+: larger moves → floor 0.30-0.50%
#
# These are applied in BOTH paper and real mode. The old separate
# paper/real move floors (0.05 vs 0.5) are replaced by this single
# per-timeframe scale.

TIMEFRAME_HARD_MOVE_FLOOR: Dict[str, float] = {
    "1m":  0.10,
    "5m":  0.10,
    "15m": 0.20,
    "30m": 0.25,
    "1h":  0.30,
    "4h":  0.40,
    "1d":  0.50,
}

TIMEFRAME_RANGING_MOVE_FLOOR: Dict[str, float] = {
    "1m":  0.15,
    "5m":  0.20,
    "15m": 0.30,
    "30m": 0.35,
    "1h":  0.50,
    "4h":  0.70,
    "1d":  1.00,
}

TIMEFRAME_VOLATILE_MOVE_FLOOR: Dict[str, float] = {
    "1m":  0.20,
    "5m":  0.30,
    "15m": 0.40,
    "30m": 0.50,
    "1h":  0.80,
    "4h":  1.20,
    "1d":  1.60,
}


def get_hard_move_floor(timeframe: str, mode: str = "paper") -> float:
    """Get the per-timeframe hard_move_floor.

    Args:
        timeframe: Candle interval (e.g. '5m', '15m', '1h').
        mode: 'paper' or 'real'. Both use the same per-timeframe value
            now — the mode parameter is kept for API compatibility.

    Returns:
        hard_move_floor value for this timeframe.
    """
    return TIMEFRAME_HARD_MOVE_FLOOR.get(timeframe, 0.30)  # default = 1h


def get_ranging_move_floor(timeframe: str) -> float:
    """Get the per-timeframe ranging_move_floor."""
    return TIMEFRAME_RANGING_MOVE_FLOOR.get(timeframe, 0.50)


def get_volatile_move_floor(timeframe: str) -> float:
    """Get the per-timeframe volatile_move_floor."""
    return TIMEFRAME_VOLATILE_MOVE_FLOOR.get(timeframe, 0.80)


# -------------------------------------------------------------------- #
# Signal thresholds
# -------------------------------------------------------------------- #

@dataclass(frozen=True)
class SignalThresholds:
    """
    All thresholds used by SignalGenerator (signal.py) and RealtimeTrader
    skip filters (realtime.py).

    Paper mode (validation_mode=True): relaxed thresholds so the pipeline
    can produce enough trades to verify end-to-end behaviour (signal →
    order → fill → PnL tracking). Goal is NOT to be picky about signal
    quality; it is to exercise the plumbing.

    Real mode (validation_mode=False): strict thresholds for live trading
    with real money. Only high-conviction setups in favourable regimes.
    """

    # ----- Probability gates (overall_probability) ----- #
    base_prob_gate: float
    """Hard floor on prediction.overall_probability. Below this → skip."""

    ranging_prob_gate: float
    """Additional gate when regime == 'ranging'. Below this → skip."""

    volatile_prob_gate: float
    """Additional gate when regime == 'volatile'. Below this → skip."""

    counter_trend_gate: float
    """Gate for counter-trend signals (LONG in downtrend, SHORT in uptrend)."""

    # ----- Move floors (% of price, absolute value) ----- #
    hard_move_floor: float
    """Hard floor on abs(expected_total_move_pct). Below this → skip."""

    ranging_move_floor: float
    """Additional move floor when regime == 'ranging'."""

    volatile_move_floor: float
    """Additional move floor when regime == 'volatile'."""

    move_threshold: float
    """Final entry gate on abs(expected_total_move_pct). Must be >= hard_move_floor."""

    # ----- Confidence boost ----- #
    boost_prob_trigger: float
    """overall_probability above this triggers confidence boost."""

    boost_move_trigger: float
    """expected_total_move_pct above this triggers confidence boost."""

    # ----- Regime-adaptive min_confidence / min_risk_reward ----- #
    # Keys are LOWERCASE regime names: 'trending_up', 'trending_down',
    # 'ranging', 'volatile', 'unknown'. This matches RegimeDetector output.
    regime_min_confidence: Dict[str, float] = field(default_factory=dict)
    regime_min_risk_reward: Dict[str, float] = field(default_factory=dict)

    # ----- Default min_confidence (used by SignalGenerator constructor) ----- #
    default_min_confidence: float = 0.60
    """Fallback min_confidence for SignalGenerator when no regime info."""

    # ----- Per-trade risk gate (was in risk/manager.py) ----- #
    per_trade_min_confidence: float = 0.08
    per_trade_min_risk_reward: float = 0.5

    # ----- P7 (V4.4): directional_edge policy ----- #
    # v0.40.22-audit: P7 replaces `dir = sign(expected_move_pct)` with a
    # bayesian-shrunk per-direction edge + quality gate. Validation on
    # 8 tokens × 3 windows (304,685 trades) showed +560pp PnL total vs P1
    # and improvement in 7/8 tokens (vs 4/8 for P6 magnitude-only).
    p7_min_edge_pct: float = 0.10
    """Hard floor on max(long_edge, short_edge) for P7 policy.
    Below this → no directional edge strong enough to trade → skip.
    0.10% = 10 bps — below this the expected edge is indistinguishable
    from fee noise (0.08% RT). Validated in v0.40.22-audit."""

    p7_bayesian_alpha: float = 1.0
    """Laplace prior α for bayesian shrinkage of per-direction win_rate.
    bayesian_wr = (wins + α) / (count + α + β).
    α=β=1 = uniform prior (Laplace's rule of succession).
    Shrinks low-N observations toward 0.5 — a pattern with 80% WR over
    5 cases gets shrunk to 67%, reducing false confidence."""

    p7_bayesian_beta: float = 1.0
    """Laplace prior β for bayesian shrinkage. See p7_bayesian_alpha."""

    # ---------------------------------------------------------------- #
    # Factory methods
    # ---------------------------------------------------------------- #

    @classmethod
    def paper(cls) -> "SignalThresholds":
        """
        Paper trading thresholds (validation_mode=True).

        v0.39.3: Lowered probability gates to fix 'bot not operating' bug.
        Root cause: fresh tries with 200-500 patterns produce Bayesian-
        shrunk overall_probability values in the 0.10-0.20 range. The
        v0.38.7 paper gates (0.15 / 0.20 / 0.25 / 0.25) rejected 31 of 33
        signals in a BTC/USDT 1h validation run, leaving only 2 trades —
        well below the 5-trade MC threshold and visually 'dead' to the
        user. The new gates (0.08 / 0.12 / 0.15 / 0.15) let ~80% of
        signals through so paper trading actually exercises the pipeline.
        Move floors unchanged: 0.05% is already permissive.
        """
        return cls(
            # Probability gates — v0.39.3: lowered for paper-mode visibility
            base_prob_gate=0.08,
            ranging_prob_gate=0.12,
            volatile_prob_gate=0.15,
            counter_trend_gate=0.15,
            # Move floors (realtime.py:957, 1006, 1018 — all 0.05 in paper)
            hard_move_floor=0.05,
            ranging_move_floor=0.05,
            volatile_move_floor=0.05,
            move_threshold=0.05,
            # Boost (realtime.py:975-976)
            boost_prob_trigger=0.40,
            boost_move_trigger=0.80,
            # Regime-adaptive (signal.py:364-369, case-fixed)
            regime_min_confidence={
                "trending_up":   0.45,
                "trending_down": 0.45,
                "ranging":       0.60,
                "volatile":      0.55,
                "unknown":       0.60,
            },
            regime_min_risk_reward={
                "trending_up":   1.2,
                "trending_down": 1.2,
                "ranging":       1.5,
                "volatile":      1.8,
                "unknown":       1.5,
            },
            # Defaults (paper uses the relaxed 0.08 confidence from ReplayConfig)
            default_min_confidence=0.08,
            per_trade_min_confidence=0.08,
            per_trade_min_risk_reward=0.5,
            # P7 (V4.4): directional_edge policy — same params in paper & real
            # mode (validated in v0.40.22-audit on 8 tokens × 3 windows).
            p7_min_edge_pct=0.10,
            p7_bayesian_alpha=1.0,
            p7_bayesian_beta=1.0,
        )

    @classmethod
    def real(cls) -> "SignalThresholds":
        """
        Real-money thresholds (validation_mode=False).

        Values preserved verbatim from realtime.py v0.38.7 lines 965-976
        and signal.py v0.38.7 lines 364-369 (with case fix UPPER→lower).
        """
        return cls(
            # Probability gates (realtime.py:966-970)
            base_prob_gate=0.35,
            ranging_prob_gate=0.55,
            volatile_prob_gate=0.60,           # = prob_threshold * 2.0
            counter_trend_gate=0.60,
            # Move floors (realtime.py:965, 1006, 1018)
            hard_move_floor=0.5,
            ranging_move_floor=1.0,
            volatile_move_floor=1.6,           # = move_threshold * 2.0
            move_threshold=0.80,
            # Boost (realtime.py:975-976)
            boost_prob_trigger=0.45,
            boost_move_trigger=1.0,
            # Regime-adaptive (signal.py:364-369, case-fixed)
            regime_min_confidence={
                "trending_up":   0.45,
                "trending_down": 0.45,
                "ranging":       0.60,
                "volatile":      0.55,
                "unknown":       0.60,
            },
            regime_min_risk_reward={
                "trending_up":   1.2,
                "trending_down": 1.2,
                "ranging":       1.5,
                "volatile":      1.8,
                "unknown":       1.5,
            },
            # Defaults (real uses stricter 0.60 confidence)
            default_min_confidence=0.60,
            per_trade_min_confidence=0.08,
            per_trade_min_risk_reward=0.5,
            # P7 (V4.4): same params in real mode — the gate is what protects
            # against bad trades, not stricter bayesian params.
            p7_min_edge_pct=0.10,
            p7_bayesian_alpha=1.0,
            p7_bayesian_beta=1.0,
        )

    @classmethod
    def for_mode(cls, validation_mode: bool) -> "SignalThresholds":
        """Pick the right thresholds based on cfg.validation_mode."""
        return cls.paper() if validation_mode else cls.real()

    # ---------------------------------------------------------------- #
    # Helpers
    # ---------------------------------------------------------------- #

    def hard_move_floor_for_timeframe(self, timeframe: str) -> float:
        """Get the per-timeframe hard_move_floor, overriding the flat default.

        v0.43.0 (TERMINAL-v2.1): The flat hard_move_floor caused 100% LONG
        signals. Per-timeframe floors ensure SHORT signals on lower TFs
        aren't killed by an inappropriately high floor, while still
        filtering noise on higher TFs.

        Returns the per-timeframe value from TIMEFRAME_HARD_MOVE_FLOOR,
        falling back to self.hard_move_floor if no TF-specific value exists.
        """
        tf_floor = TIMEFRAME_HARD_MOVE_FLOOR.get(timeframe)
        if tf_floor is not None:
            return tf_floor
        return self.hard_move_floor

    def ranging_move_floor_for_timeframe(self, timeframe: str) -> float:
        """Get the per-timeframe ranging_move_floor."""
        tf_floor = TIMEFRAME_RANGING_MOVE_FLOOR.get(timeframe)
        if tf_floor is not None:
            return tf_floor
        return self.ranging_move_floor

    def volatile_move_floor_for_timeframe(self, timeframe: str) -> float:
        """Get the per-timeframe volatile_move_floor."""
        tf_floor = TIMEFRAME_VOLATILE_MOVE_FLOOR.get(timeframe)
        if tf_floor is not None:
            return tf_floor
        return self.volatile_move_floor

    def regime_confidence(self, regime_name: str) -> float:
        """Get min_confidence for a regime (case-insensitive). Defaults to 'unknown'."""
        key = (regime_name or "unknown").lower()
        return self.regime_min_confidence.get(key, self.regime_min_confidence["unknown"])

    def regime_risk_reward(self, regime_name: str) -> float:
        """Get min_risk_reward for a regime (case-insensitive). Defaults to 'unknown'."""
        key = (regime_name or "unknown").lower()
        return self.regime_min_risk_reward.get(key, self.regime_min_risk_reward["unknown"])


# -------------------------------------------------------------------- #
# Regime thresholds
# -------------------------------------------------------------------- #

# Per-timeframe simple mode cutoffs (FASE 3 Tarea 3.2)
# Lower timeframes need tighter cutoffs because:
# - 1m candles rarely move 2% (the old default simple_move_cutoff)
# - Most 1m candles would be classified as 'ranging'
# - N4 needs balanced regime distribution to be useful
TIMEFRAME_REGIME_CUTOFFS = {
    "1m":  {"simple_vol_cutoff": 0.025, "simple_move_cutoff": 0.008},
    "5m":  {"simple_vol_cutoff": 0.035, "simple_move_cutoff": 0.012},
    "15m": {"simple_vol_cutoff": 0.050, "simple_move_cutoff": 0.018},
    "30m": {"simple_vol_cutoff": 0.060, "simple_move_cutoff": 0.020},
    "1h":  {"simple_vol_cutoff": 0.080, "simple_move_cutoff": 0.020},  # Historical defaults
    "4h":  {"simple_vol_cutoff": 0.120, "simple_move_cutoff": 0.030},
    "1d":  {"simple_vol_cutoff": 0.150, "simple_move_cutoff": 0.040},
}


@dataclass(frozen=True)
class RegimeThresholds:
    """
    Thresholds for RegimeDetector (full mode) and the simple regime
    detector used during trie build (ppmt.py:_detect_simple_regime).

    Full-mode values are crypto-calibrated (v0.11.0): vol_threshold=0.15
    annualized, trend_threshold=0.001 per candle. These are the defaults
    in RegimeDetector when called with vol_threshold=0.6 (sentinel value
    that triggers auto-calibration).

    Simple-mode values are for trie tagging only. They use the historical
    0.08 / 0.02 cutoffs so existing tries remain tag-compatible. The
    simple detector is intentionally lightweight — the full RegimeDetector
    runs at trade time.
    """

    # Full-mode (Hurst + R² + annualized vol)
    lookback: int = 50
    vol_threshold: float = 0.15
    """Annualized volatility threshold for 'volatile' regime (crypto: 0.15)."""

    trend_threshold: float = 0.001
    """Per-candle relative slope for 'trending' regime (crypto: 0.001)."""

    # Simple-mode (used by RegimeDetector.detect_simple + trie tagging)
    simple_vol_cutoff: float = 0.08
    """Window range/entry above this → 'volatile' (simple mode)."""

    simple_move_cutoff: float = 0.02
    """Window move above this (abs) → 'trending_*' (simple mode)."""

    @classmethod
    def default(cls) -> "RegimeThresholds":
        """Crypto-calibrated defaults (matches RegimeDetector v0.11.0)."""
        return cls()

    @classmethod
    def for_timeframe(cls, timeframe: str) -> "RegimeThresholds":
        """Create thresholds calibrated for a specific timeframe.

        Lower timeframes need tighter cutoffs because:
        - 1m candles rarely move 2% (the old default)
        - Most 1m candles would be classified as 'ranging'
        - N4 needs balanced regime distribution to be useful

        Args:
            timeframe: Candle interval string (e.g. '1m', '5m', '15m', '1h', '4h', '1d').
                       Unknown timeframes fall back to 1h defaults.

        Returns:
            RegimeThresholds with timeframe-appropriate simple-mode cutoffs.
        """
        cutoffs = TIMEFRAME_REGIME_CUTOFFS.get(timeframe, {
            "simple_vol_cutoff": 0.080, "simple_move_cutoff": 0.020
        })
        return cls(
            simple_vol_cutoff=cutoffs["simple_vol_cutoff"],
            simple_move_cutoff=cutoffs["simple_move_cutoff"],
        )
