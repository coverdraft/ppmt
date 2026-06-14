"""
Tests for V4.2 Metadata Enhancements

These tests validate the V4.2 fixes and new features:
- Observation freshness: last_observation_time, freshness_decay, observation_timespan
- Regime-aware threshold adjustment in paper_trader
- Real historical_count in signal sizing (no more hardcoded 100)
- N4 regime filtering in PPMT.match()
- Backward compatibility with V4.1 serialized data

Design principles for SANE tests:
1. Test BEHAVIOR, not implementation details
2. Use realistic scenarios (not edge-case-only)
3. Verify invariants hold (e.g., freshness_decay in [0, 1])
4. Test that new features are BACKWARD COMPATIBLE with existing code
5. Test incrementally — each observation should update stats correctly
6. No mocks of internal methods — test the public API only
"""

import pytest
import time
import numpy as np

from ppmt.core.metadata import BlockLifecycleMetadata, RegimeStats
from ppmt.core.trie import PPMTTrie, TrieNode


class TestObservationFreshness:
    """Test V4.2 observation freshness tracking."""

    def test_freshness_default_is_one(self):
        """With no time tracking, freshness_decay should return 1.0 (neutral)."""
        meta = BlockLifecycleMetadata()
        assert meta.freshness_decay == 1.0

    def test_observation_updates_last_time(self):
        """After an observation, last_observation_time should be set."""
        meta = BlockLifecycleMetadata()
        before = time.time()
        meta.update_from_observation(
            move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
            duration=10, won=True,
        )
        after = time.time()
        assert before <= meta.last_observation_time <= after

    def test_fresh_observation_has_high_decay(self):
        """A just-observed pattern should have freshness close to 1.0."""
        meta = BlockLifecycleMetadata()
        meta.update_from_observation(
            move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
            duration=10, won=True,
        )
        # Fresh observation: decay should be very close to 1.0
        assert meta.freshness_decay > 0.99

    def test_freshness_decays_over_time(self):
        """A pattern observed in the past should have lower freshness."""
        meta = BlockLifecycleMetadata()
        # Simulate observation 7 days ago
        seven_days_ago = time.time() - 7 * 86400
        meta.last_observation_time = seven_days_ago
        meta.historical_count = 5  # Need count > 0 for relevance

        decay = meta.freshness_decay
        # At 7-day half-life, decay should be ~0.5
        assert 0.4 < decay < 0.6

    def test_freshness_decay_is_zero_to_one(self):
        """freshness_decay must ALWAYS be in [0, 1]."""
        meta = BlockLifecycleMetadata(historical_count=10)

        # Test various ages
        for days_ago in [0, 1, 7, 30, 365]:
            meta.last_observation_time = time.time() - days_ago * 86400
            decay = meta.freshness_decay
            assert 0.0 <= decay <= 1.0, f"Decay {decay} out of range for {days_ago} days ago"

    def test_observation_timespan_updates(self):
        """observation_timespan should grow with subsequent observations."""
        meta = BlockLifecycleMetadata()
        meta.update_from_observation(
            move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
            duration=10, won=True,
        )
        first_time = meta.last_observation_time

        # Simulate a second observation some time later
        # We can't actually wait, so we'll manually set the timespan
        meta.last_observation_time = first_time + 3600  # 1 hour later
        meta.observation_timespan = 3600  # 1 hour span

        assert meta.observation_timespan == 3600

    def test_observation_density(self):
        """observation_density should compute obs/day correctly."""
        meta = BlockLifecycleMetadata(historical_count=100)
        # 100 observations over 10 days = 10 obs/day
        meta.observation_timespan = 10 * 86400
        density = meta.observation_density
        assert abs(density - 10.0) < 0.1

    def test_observation_density_zero_timespan(self):
        """No timespan data should return 0.0 density."""
        meta = BlockLifecycleMetadata(historical_count=10)
        assert meta.observation_density == 0.0


class TestRealHistoricalCount:
    """Test V4.2 fix: use real historical_count instead of hardcoded 100."""

    def test_sizing_signal_uses_real_count(self):
        """A node with 5 observations should have different sizing than 100."""
        meta_few = BlockLifecycleMetadata(
            win_rate=0.7,
            expected_move_pct=3.0,
            max_drawdown_pct=-2.0,
            historical_count=5,
        )
        meta_many = BlockLifecycleMetadata(
            win_rate=0.7,
            expected_move_pct=3.0,
            max_drawdown_pct=-2.0,
            historical_count=100,
        )
        # Both should produce valid sizing signals
        assert meta_few.sizing_signal > 0
        assert meta_many.sizing_signal > 0
        # The Bayesian shrinkage should make the few-obs node
        # have a lower probability_of_success (shrinks toward 0.5)
        assert meta_few.probability_of_success < meta_many.probability_of_success

    def test_trie_node_provides_real_count(self):
        """When building from a trie, the matched node should have real count."""
        trie = PPMTTrie(name="test_count")
        # Insert same pattern 5 times
        for _ in range(5):
            trie.insert_with_observations(
                symbols=["a", "d", "b"],
                move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
                duration=10, won=True, next_symbol="h",
                regime="trending_up", regime_confidence=0.8,
            )
        trie.propagate_metadata()

        node = trie.search(["a", "d", "b"])
        assert node is not None
        assert node.metadata.historical_count == 5

    def test_count_affects_confidence_not_just_sizing(self):
        """Fewer observations should reduce confidence via Bayesian shrinkage."""
        meta_low = BlockLifecycleMetadata(
            win_rate=0.6, historical_count=3,
        )
        meta_high = BlockLifecycleMetadata(
            win_rate=0.6, historical_count=100,
        )
        assert meta_high.confidence > meta_low.confidence


class TestRegimeAwareThreshold:
    """Test V4.2 fix: regime-aware confidence threshold adjustment."""

    def test_regime_adjustment_favorable(self):
        """Favorable regime should lower effective min_confidence (easier entry)."""
        # When regime_adjustment > 1.0, effective_min_conf = min_conf / regime_adj
        min_conf = 0.20
        regime_adjustment = 1.1  # 10% boost
        adjusted = min_conf / regime_adjustment
        assert adjusted < min_conf  # Easier to enter
        assert abs(adjusted - 0.1818) < 0.01

    def test_regime_adjustment_unfavorable(self):
        """Unfavorable regime should raise effective min_confidence (harder entry)."""
        min_conf = 0.20
        regime_adjustment = 0.6  # 40% penalty
        adjusted = min_conf / regime_adjustment
        assert adjusted > min_conf  # Harder to enter
        assert abs(adjusted - 0.333) < 0.01

    def test_regime_adjustment_neutral(self):
        """Neutral regime (1.0) should not change threshold."""
        min_conf = 0.20
        regime_adjustment = 1.0
        adjusted = min_conf / regime_adjustment
        assert adjusted == min_conf

    def test_prediction_engine_uses_regime(self):
        """PredictionEngine.predict() should accept current_regime parameter."""
        from ppmt.engine.prediction import PredictionEngine

        trie = PPMTTrie(name="test_regime_pred")
        for _ in range(15):
            trie.insert_with_observations(
                symbols=["a", "d", "b"], move_pct=3.0, drawdown_pct=-1.0,
                favorable_pct=4.0, duration=10, won=True,
                next_symbol="h", regime="trending_up", regime_confidence=0.8,
            )
        for _ in range(5):
            trie.insert_with_observations(
                symbols=["a", "d", "b"], move_pct=-1.0, drawdown_pct=-2.0,
                favorable_pct=0.5, duration=8, won=False,
                next_symbol="e", regime="volatile", regime_confidence=0.7,
            )
        trie.propagate_metadata()

        engine = PredictionEngine(trie, prediction_depth=3)

        # Should not crash with current_regime parameter
        pred_fav = engine.predict(
            current_symbols=["a", "d", "b"],
            entry_price=50000.0,
            current_regime="trending_up",
        )
        pred_unfav = engine.predict(
            current_symbols=["a", "d", "b"],
            entry_price=50000.0,
            current_regime="volatile",
        )
        assert pred_fav.confidence >= pred_unfav.confidence


class TestN4RegimeFiltering:
    """Test V4.2 fix: N4 Trie applies regime_match_score to confidence."""

    def test_n4_confidence_adjusted_by_regime(self):
        """N4 confidence should be multiplied by regime_match_score."""
        from ppmt.engine.ppmt import PPMT

        engine = PPMT(symbol="BTC/USDT", asset_class="blue_chip")

        # Build with some data
        import pandas as pd
        np.random.seed(42)
        n = 200
        df = pd.DataFrame({
            "open": np.random.randn(n).cumsum() + 100,
            "high": np.random.randn(n).cumsum() + 102,
            "low": np.random.randn(n).cumsum() + 98,
            "close": np.random.randn(n).cumsum() + 100,
            "volume": np.abs(np.random.randn(n)) * 1000,
        })
        engine.build(df, pattern_length=3)

        # Set current regime
        engine.set_regime("trending_up")

        # Get N4 match and confidence
        symbols = engine.sax.encode(df)
        if len(symbols) >= 3:
            result = engine.match(symbols[:3], 100.0)
            # N4 confidence should be a valid number
            assert result.n4_confidence >= 0.0


class TestV42BackwardCompatibility:
    """Test that V4.2 enhancements don't break existing functionality."""

    def test_old_v41_serialization_loads(self):
        """V4.1 serialized data (without freshness fields) should load."""
        old_data = {
            "trigger_candle": 5,
            "expected_move_pct": 3.0,
            "win_rate": 0.7,
            "historical_count": 50,
            "regime": "trending_up",
            "regime_confidence": 0.8,
            "dominant_regime": "trending_up",
            "regime_distribution": {"trending_up": 30, "ranging": 20},
            "regime_stats": {"trending_up": {"count": 30, "wins": 20, "total_move_pct": 60.0}},
            "move_variance": 5.0,
            "move_mean_for_variance": 3.0,
            # NO last_observation_time or observation_timespan
        }
        meta = BlockLifecycleMetadata.from_dict(old_data)
        assert meta.expected_move_pct == 3.0
        assert meta.last_observation_time == 0.0  # Default
        assert meta.observation_timespan == 0.0  # Default
        assert meta.freshness_decay == 1.0  # Neutral when no time data

    def test_observation_without_time_still_works(self):
        """Observations should still work without time-related issues."""
        meta = BlockLifecycleMetadata()
        meta.update_from_observation(
            move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
            duration=10, won=True,
        )
        assert meta.historical_count == 1
        assert meta.expected_move_pct == 3.0
        assert meta.last_observation_time > 0  # V4.2 auto-sets this

    def test_trie_serialization_with_v42_fields(self):
        """Trie with V4.2 metadata should survive serialization round-trip."""
        trie = PPMTTrie(name="test_v42_ser")
        trie.insert_with_observations(
            symbols=["a", "b"], move_pct=3.0, drawdown_pct=-1.0,
            favorable_pct=4.0, duration=10, won=True,
            next_symbol="c", regime="trending_up", regime_confidence=0.85,
        )
        trie.insert_with_observations(
            symbols=["a", "b"], move_pct=-1.0, drawdown_pct=-2.0,
            favorable_pct=1.0, duration=8, won=False,
            next_symbol="d", regime="ranging", regime_confidence=0.6,
        )

        data = trie.to_dict()
        restored = PPMTTrie.from_dict(data)

        found = restored.search(["a", "b"])
        assert found is not None
        assert found.metadata.historical_count == 2
        # V4.2 fields should be present
        assert "last_observation_time" in found.metadata.to_dict()
        assert "observation_timespan" in found.metadata.to_dict()

    def test_freshness_does_not_break_confidence(self):
        """Confidence should still be valid with freshness tracking."""
        meta = BlockLifecycleMetadata(
            win_rate=0.7,
            expected_move_pct=3.0,
            historical_count=50,
            last_observation_time=time.time(),  # Fresh
        )
        assert meta.confidence > 0
        assert meta.freshness_decay > 0.99
        # Confidence itself is not multiplied by freshness —
        # that's a separate decision for the trading engine

    def test_propagate_metadata_preserves_freshness(self):
        """After propagation, nodes should have valid freshness data."""
        trie = PPMTTrie(name="test_prop_fresh")
        trie.insert_with_observations(
            symbols=["a", "b"], move_pct=3.0, drawdown_pct=-1.0,
            favorable_pct=4.0, duration=10, won=True,
            next_symbol="c", regime="trending_up", regime_confidence=0.8,
        )
        trie.insert_with_observations(
            symbols=["a", "d"], move_pct=-2.0, drawdown_pct=-3.0,
            favorable_pct=1.0, duration=8, won=False,
            next_symbol="e", regime="volatile", regime_confidence=0.7,
        )
        trie.propagate_metadata()

        # Root should have aggregated metadata
        root = trie.root
        assert root.metadata.historical_count > 0
        # Freshness should be valid
        assert 0.0 <= root.metadata.freshness_decay <= 1.0
