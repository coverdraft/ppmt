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
from typing import Dict


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
        )

    @classmethod
    def for_mode(cls, validation_mode: bool) -> "SignalThresholds":
        """Pick the right thresholds based on cfg.validation_mode."""
        return cls.paper() if validation_mode else cls.real()

    # ---------------------------------------------------------------- #
    # Helpers
    # ---------------------------------------------------------------- #

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
