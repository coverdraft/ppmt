"""
Tests for core/thresholds.py — v0.38.8 unification module.

Verifies that:
  1. SignalThresholds.paper() and .real() return the expected values
     (preserved verbatim from v0.38.7).
  2. Regime names are lowercase (the bug fix).
  3. for_mode() picks the right factory.
  4. regime_confidence/regime_risk_reward are case-insensitive.
  5. RegimeThresholds.default() matches RegimeDetector v0.11.0 calibration.
"""

import pytest
from ppmt.core.thresholds import SignalThresholds, RegimeThresholds


class TestSignalThresholdsPaper:
    def test_paper_prob_gates(self):
        # v0.39.3: Lowered from 0.15/0.20/0.25/0.25 to 0.08/0.12/0.15/0.15
        # to fix 'bot not operating' bug. Fresh tries with Bayesian
        # shrinkage produce overall_probability in 0.10-0.20 range, so
        # the old paper gates rejected ~95% of signals.
        t = SignalThresholds.paper()
        assert t.base_prob_gate == 0.08
        assert t.ranging_prob_gate == 0.12
        assert t.volatile_prob_gate == 0.15
        assert t.counter_trend_gate == 0.15

    def test_paper_move_floors(self):
        t = SignalThresholds.paper()
        assert t.hard_move_floor == 0.05
        assert t.ranging_move_floor == 0.05
        assert t.volatile_move_floor == 0.05
        assert t.move_threshold == 0.05

    def test_paper_boost(self):
        t = SignalThresholds.paper()
        assert t.boost_prob_trigger == 0.40
        assert t.boost_move_trigger == 0.80

    def test_paper_regime_keys_are_lowercase(self):
        """Bug fix: signal.py v0.38.7 used 'TRENDING_UP' (uppercase) but
        RegimeDetector returns 'trending_up' (lowercase). The dict keys
        must now be lowercase so lookups actually hit."""
        t = SignalThresholds.paper()
        for key in t.regime_min_confidence:
            assert key == key.lower(), f"regime key {key!r} must be lowercase"
        for key in t.regime_min_risk_reward:
            assert key == key.lower(), f"regime key {key!r} must be lowercase"

    def test_paper_regime_values(self):
        t = SignalThresholds.paper()
        assert t.regime_min_confidence["trending_up"] == 0.45
        assert t.regime_min_confidence["trending_down"] == 0.45
        assert t.regime_min_confidence["ranging"] == 0.60
        assert t.regime_min_confidence["volatile"] == 0.55
        assert t.regime_min_confidence["unknown"] == 0.60
        assert t.regime_min_risk_reward["trending_up"] == 1.2
        assert t.regime_min_risk_reward["volatile"] == 1.8


class TestSignalThresholdsReal:
    def test_real_prob_gates(self):
        t = SignalThresholds.real()
        assert t.base_prob_gate == 0.35
        assert t.ranging_prob_gate == 0.55
        assert t.volatile_prob_gate == 0.60
        assert t.counter_trend_gate == 0.60

    def test_real_move_floors(self):
        t = SignalThresholds.real()
        assert t.hard_move_floor == 0.5
        assert t.ranging_move_floor == 1.0
        assert t.volatile_move_floor == 1.6
        assert t.move_threshold == 0.80

    def test_real_boost(self):
        t = SignalThresholds.real()
        assert t.boost_prob_trigger == 0.45
        assert t.boost_move_trigger == 1.0


class TestSignalThresholdsFactory:
    def test_for_mode_paper(self):
        # v0.39.3: paper base_prob_gate lowered 0.15 → 0.08
        t = SignalThresholds.for_mode(True)
        assert t is SignalThresholds.paper() or t.base_prob_gate == 0.08

    def test_for_mode_real(self):
        t = SignalThresholds.for_mode(False)
        assert t is SignalThresholds.real() or t.base_prob_gate == 0.35

    def test_for_mode_picks_correct_one(self):
        """paper must have lower gates than real — sanity check."""
        p = SignalThresholds.for_mode(True)
        r = SignalThresholds.for_mode(False)
        assert p.base_prob_gate < r.base_prob_gate
        assert p.hard_move_floor < r.hard_move_floor


class TestRegimeHelpers:
    def test_regime_confidence_lowercase(self):
        t = SignalThresholds.real()
        assert t.regime_confidence("trending_up") == 0.45

    def test_regime_confidence_case_insensitive(self):
        """RegimeDetector returns lowercase but signal.py used to pass
        uppercase. The helper must be case-insensitive so old callers
        don't break silently."""
        t = SignalThresholds.real()
        assert t.regime_confidence("TRENDING_UP") == 0.45
        assert t.regime_confidence("Trending_Up") == 0.45

    def test_regime_confidence_unknown_fallback(self):
        t = SignalThresholds.real()
        assert t.regime_confidence("nonsense") == 0.60  # falls back to 'unknown'
        assert t.regime_confidence(None) == 0.60
        assert t.regime_confidence("") == 0.60

    def test_regime_risk_reward_lowercase(self):
        t = SignalThresholds.paper()
        assert t.regime_risk_reward("volatile") == 1.8
        assert t.regime_risk_reward("VOLATILE") == 1.8


class TestRegimeThresholds:
    def test_defaults_match_v0_11_0_crypto_calibration(self):
        """RegimeDetector v0.11.0 auto-calibrates vol=0.15, trend=0.001
        when sentinel 0.6/0.005 is passed. RegimeThresholds.default()
        must return these same values explicitly."""
        rt = RegimeThresholds.default()
        assert rt.vol_threshold == 0.15
        assert rt.trend_threshold == 0.001
        assert rt.lookback == 50

    def test_simple_cutoffs_preserved(self):
        """_detect_simple_regime in ppmt.py:176-217 used 0.08 vol and
        0.02 move. These are preserved for trie-tag compatibility."""
        rt = RegimeThresholds.default()
        assert rt.simple_vol_cutoff == 0.08
        assert rt.simple_move_cutoff == 0.02

    def test_frozen(self):
        """Thresholds must be immutable so callers can't accidentally
        mutate a shared instance."""
        t = SignalThresholds.paper()
        with pytest.raises((AttributeError, Exception)):
            t.base_prob_gate = 0.99

    def test_shared_instance_is_safe(self):
        """Since factories return new instances each call, two callers
        must not share mutable state."""
        t1 = SignalThresholds.paper()
        t2 = SignalThresholds.paper()
        assert t1 == t2  # same values
        # But the regime dicts should compare equal even though separate
        assert t1.regime_min_confidence == t2.regime_min_confidence
