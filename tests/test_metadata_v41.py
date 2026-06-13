"""
Tests for V4.1 Metadata Enhancements

These tests validate the new metadata features without distorting results:
- RegimeStats: per-regime win_rate and expected_move tracking
- regime_match_score: confidence multiplier based on regime match
- move_variance: Welford's online algorithm for move dispersion
- move_std / move_coefficient_of_variation: reliability metrics
- Propagation of new fields in the Trie

Design principles for SANE tests:
1. Test BEHAVIOR, not implementation details
2. Use realistic scenarios (not edge-case-only)
3. Verify invariants hold (e.g., regime_match_score in [0.5, 1.2])
4. Test that new features are BACKWARD COMPATIBLE with existing code
5. Test incrementally — each observation should update stats correctly
"""

import pytest
import numpy as np

from ppmt.core.metadata import BlockLifecycleMetadata, RegimeStats
from ppmt.core.trie import PPMTTrie, TrieNode


class TestRegimeStats:
    """Test the RegimeStats dataclass for per-regime statistics."""

    def test_empty_stats(self):
        """Empty RegimeStats should return 0.0 for derived properties."""
        rs = RegimeStats()
        assert rs.count == 0
        assert rs.win_rate == 0.0
        assert rs.avg_move_pct == 0.0

    def test_single_observation(self):
        """Single observation should yield win_rate 0 or 1."""
        rs = RegimeStats(count=1, wins=1, total_move_pct=3.5)
        assert rs.win_rate == 1.0
        assert rs.avg_move_pct == 3.5

    def test_multiple_observations(self):
        """Multiple observations should compute correct averages."""
        rs = RegimeStats(count=10, wins=6, total_move_pct=25.0)
        assert rs.win_rate == 0.6
        assert rs.avg_move_pct == 2.5

    def test_serialization_roundtrip(self):
        """RegimeStats should survive serialization round-trip."""
        rs = RegimeStats(count=50, wins=30, total_move_pct=120.0)
        data = rs.to_dict()
        restored = RegimeStats.from_dict(data)
        assert restored.count == 50
        assert restored.wins == 30
        assert abs(restored.total_move_pct - 120.0) < 0.01
        assert abs(restored.win_rate - 0.6) < 0.01
        assert abs(restored.avg_move_pct - 2.4) < 0.01


class TestMoveVariance:
    """Test Welford's online algorithm for move variance tracking."""

    def test_single_observation_no_variance(self):
        """A single observation should have zero variance."""
        meta = BlockLifecycleMetadata()
        meta.update_from_observation(
            move_pct=5.0, drawdown_pct=-1.0, favorable_pct=6.0,
            duration=10, won=True,
        )
        assert meta.historical_count == 1
        assert meta.move_variance == 0.0
        assert meta.move_std == 0.0

    def test_two_observations(self):
        """Two observations should compute correct variance."""
        meta = BlockLifecycleMetadata()
        for move in [3.0, 5.0]:
            meta.update_from_observation(
                move_pct=move, drawdown_pct=-1.0, favorable_pct=move + 1.0,
                duration=10, won=True,
            )
        # With values [3.0, 5.0], mean=4.0, variance = ((3-4)^2 + (5-4)^2) / (2-1) = 2.0
        # std = sqrt(2.0) ≈ 1.414
        assert meta.historical_count == 2
        assert abs(meta.move_std - np.sqrt(2.0)) < 0.01

    def test_constant_moves_zero_variance(self):
        """All identical moves should have zero variance."""
        meta = BlockLifecycleMetadata()
        for _ in range(5):
            meta.update_from_observation(
                move_pct=2.0, drawdown_pct=-1.0, favorable_pct=3.0,
                duration=10, won=True,
            )
        assert meta.historical_count == 5
        assert meta.move_std == 0.0

    def test_coefficient_of_variation(self):
        """CV should measure relative dispersion correctly."""
        meta = BlockLifecycleMetadata()
        # Low CV: moves tightly clustered around the mean
        for move in [1.9, 2.0, 2.1, 2.0, 2.0]:
            meta.update_from_observation(
                move_pct=move, drawdown_pct=-0.5, favorable_pct=move + 0.5,
                duration=10, won=True,
            )
        cv_low = meta.move_coefficient_of_variation
        assert cv_low < 0.2  # Very tight clustering

        # High CV: moves spread widely
        meta2 = BlockLifecycleMetadata()
        for move in [-3.0, 7.0, -1.0, 5.0, 2.0]:
            meta2.update_from_observation(
                move_pct=move, drawdown_pct=-2.0, favorable_pct=max(move + 1.0, 1.0),
                duration=10, won=move > 0,
            )
        cv_high = meta2.move_coefficient_of_variation
        assert cv_high > cv_low  # High dispersion should have higher CV


class TestRegimeMatchScore:
    """Test the regime_match_score method for confidence adjustment."""

    def test_no_regime_info_returns_neutral(self):
        """No regime data should return 1.0 (neutral multiplier)."""
        meta = BlockLifecycleMetadata()
        assert meta.regime_match_score("trending_up") == 1.0
        assert meta.regime_match_score("") == 1.0

    def test_dominant_regime_match_gives_boost(self):
        """Matching the dominant regime should boost confidence."""
        meta = BlockLifecycleMetadata(
            dominant_regime="trending_up",
            historical_count=20,
            node_type="independent",
        )
        meta.regime_distribution = {"trending_up": 15, "ranging": 5}
        meta.regime_stats = {
            "trending_up": RegimeStats(count=15, wins=10, total_move_pct=45.0),
            "ranging": RegimeStats(count=5, wins=2, total_move_pct=5.0),
        }
        meta.win_rate = 12 / 20  # 0.6

        score = meta.regime_match_score("trending_up")
        assert 1.0 <= score <= 1.2  # Should be a boost

    def test_unknown_regime_gives_penalty(self):
        """A regime never observed should penalize confidence."""
        meta = BlockLifecycleMetadata(
            dominant_regime="trending_up",
            historical_count=20,
            node_type="independent",
        )
        meta.regime_distribution = {"trending_up": 20}

        score = meta.regime_match_score("volatile")
        assert 0.5 <= score <= 0.7  # Should be a penalty

    def test_dependent_node_gets_stronger_penalty(self):
        """Dependent nodes should get stronger penalty for unknown regimes."""
        meta_indep = BlockLifecycleMetadata(
            dominant_regime="trending_up",
            historical_count=50,
            node_type="independent",
        )
        meta_indep.regime_distribution = {"trending_up": 50}

        meta_dep = BlockLifecycleMetadata(
            dominant_regime="trending_up",
            historical_count=3,
            node_type="dependent",
        )
        meta_dep.regime_distribution = {"trending_up": 3}

        score_indep = meta_indep.regime_match_score("volatile")
        score_dep = meta_dep.regime_match_score("volatile")

        # Dependent node should get a stronger penalty
        assert score_dep < score_indep

    def test_regime_stats_adjustment(self):
        """Regime-specific WR worse than overall should reduce score."""
        meta = BlockLifecycleMetadata(
            dominant_regime="ranging",
            historical_count=30,
            win_rate=0.6,
        )
        meta.regime_distribution = {"ranging": 20, "volatile": 10}
        # Volatile regime has much worse WR (20%) vs overall (60%)
        meta.regime_stats = {
            "ranging": RegimeStats(count=20, wins=16, total_move_pct=60.0),
            "volatile": RegimeStats(count=10, wins=2, total_move_pct=5.0),
        }

        score_ranging = meta.regime_match_score("ranging")
        score_volatile = meta.regime_match_score("volatile")

        # Ranging should score higher than volatile
        assert score_ranging > score_volatile

    def test_score_always_in_valid_range(self):
        """regime_match_score must ALWAYS return a value in [0.5, 1.2]."""
        meta = BlockLifecycleMetadata(
            dominant_regime="trending_up",
            historical_count=100,
            win_rate=0.5,
        )
        meta.regime_distribution = {
            "trending_up": 40, "trending_down": 30,
            "ranging": 20, "volatile": 10,
        }
        meta.regime_stats = {
            "trending_up": RegimeStats(count=40, wins=25, total_move_pct=80.0),
            "trending_down": RegimeStats(count=30, wins=10, total_move_pct=-15.0),
            "ranging": RegimeStats(count=20, wins=12, total_move_pct=10.0),
            "volatile": RegimeStats(count=10, wins=3, total_move_pct=5.0),
        }

        for regime in ["trending_up", "trending_down", "ranging", "volatile", "unknown"]:
            score = meta.regime_match_score(regime)
            assert 0.5 <= score <= 1.2, f"Score {score} out of range for regime '{regime}'"


class TestRegimeStatsTracking:
    """Test that regime_stats are correctly updated during observations."""

    def test_regime_stats_accumulated(self):
        """Multiple observations in same regime should accumulate stats."""
        meta = BlockLifecycleMetadata()
        for i in range(5):
            meta.update_from_observation(
                move_pct=2.0 + i * 0.5,
                drawdown_pct=-1.0,
                favorable_pct=3.0 + i * 0.5,
                duration=10,
                won=True,
                regime="trending_up",
                regime_confidence=0.8,
            )
        for i in range(3):
            meta.update_from_observation(
                move_pct=-1.0 - i * 0.3,
                drawdown_pct=-2.0,
                favorable_pct=0.5,
                duration=8,
                won=False,
                regime="volatile",
                regime_confidence=0.6,
            )

        assert "trending_up" in meta.regime_stats
        assert "volatile" in meta.regime_stats
        assert meta.regime_stats["trending_up"].count == 5
        assert meta.regime_stats["trending_up"].wins == 5
        assert meta.regime_stats["volatile"].count == 3
        assert meta.regime_stats["volatile"].wins == 0

    def test_regime_stats_win_rate(self):
        """Per-regime win_rate should be independently computed."""
        meta = BlockLifecycleMetadata()
        # 5 wins out of 5 in trending_up (100% WR)
        for _ in range(5):
            meta.update_from_observation(
                move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
                duration=10, won=True,
                regime="trending_up", regime_confidence=0.8,
            )
        # 1 win out of 5 in volatile (20% WR)
        for won in [True, False, False, False, False]:
            meta.update_from_observation(
                move_pct=1.0 if won else -2.0,
                drawdown_pct=-3.0, favorable_pct=1.5,
                duration=8, won=won,
                regime="volatile", regime_confidence=0.7,
            )

        assert meta.regime_stats["trending_up"].win_rate == 1.0
        assert abs(meta.regime_stats["volatile"].win_rate - 0.2) < 0.01


class TestV41BackwardCompatibility:
    """Test that V4.1 enhancements don't break existing functionality."""

    def test_old_serialization_loads_with_defaults(self):
        """Serialized data without V4.1 fields should load with defaults."""
        old_data = {
            "trigger_candle": 5,
            "expected_move_pct": 3.0,
            "win_rate": 0.7,
            "historical_count": 50,
            "regime": "trending_up",
            "regime_confidence": 0.8,
            "dominant_regime": "trending_up",
            "regime_distribution": {"trending_up": 30, "ranging": 20},
            # NO regime_stats, move_variance, move_mean_for_variance
        }
        meta = BlockLifecycleMetadata.from_dict(old_data)
        assert meta.expected_move_pct == 3.0
        assert meta.regime_stats == {}  # Default empty
        assert meta.move_variance == 0.0  # Default zero
        assert meta.move_mean_for_variance == 0.0  # Default zero

    def test_observation_without_regime_still_works(self):
        """update_from_observation without regime should still work."""
        meta = BlockLifecycleMetadata()
        meta.update_from_observation(
            move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
            duration=10, won=True,
            # No regime or regime_confidence
        )
        assert meta.historical_count == 1
        assert meta.expected_move_pct == 3.0
        assert meta.regime == ""  # Unchanged
        assert meta.regime_stats == {}

    def test_trie_insert_with_observations_and_regime(self):
        """Trie insert_with_observations should work with regime param."""
        trie = PPMTTrie(name="test_regime")
        node = trie.insert_with_observations(
            symbols=["a", "d", "b"],
            move_pct=3.0,
            drawdown_pct=-1.0,
            favorable_pct=4.0,
            duration=10,
            won=True,
            next_symbol="h",
            regime="trending_up",
            regime_confidence=0.85,
        )

        found = trie.search(["a", "d", "b"])
        assert found is not None
        assert found.metadata.regime == "trending_up"
        assert "trending_up" in found.metadata.regime_stats
        assert found.metadata.regime_stats["trending_up"].count == 1
        assert found.metadata.regime_stats["trending_up"].wins == 1

    def test_propagate_metadata_with_regime_stats(self):
        """propagate_metadata should merge regime_stats from children."""
        trie = PPMTTrie(name="test_propagate")

        # Insert patterns with different regimes
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

        # Propagate metadata
        trie.propagate_metadata()

        # Root should have merged regime_stats from children
        root = trie.root
        assert "trending_up" in root.metadata.regime_stats
        assert "volatile" in root.metadata.regime_stats
        assert root.metadata.regime_stats["trending_up"].count == 1
        assert root.metadata.regime_stats["volatile"].count == 1

    def test_trie_serialization_with_v41_fields(self):
        """Trie with V4.1 metadata should survive serialization round-trip."""
        trie = PPMTTrie(name="test_ser")
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
        assert "trending_up" in found.metadata.regime_stats
        assert "ranging" in found.metadata.regime_stats
        assert found.metadata.move_variance > 0  # Should have non-zero variance


class TestRegimeAwarePredictionIntegration:
    """Test that regime_match_score integrates correctly with PredictionEngine."""

    def test_prediction_uses_regime_match_score(self):
        """PredictionEngine._compute_confidence should use regime_match_score."""
        from ppmt.engine.prediction import PredictionEngine

        trie = PPMTTrie(name="test_pred")
        # Insert pattern observed mainly in trending_up
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

        # Prediction in trending_up (favorable regime) should have higher confidence
        pred_favorable = engine.predict(
            current_symbols=["a", "d", "b"],
            entry_price=50000.0,
            current_regime="trending_up",
        )
        # Prediction in volatile (unfavorable regime) should have lower confidence
        pred_unfavorable = engine.predict(
            current_symbols=["a", "d", "b"],
            entry_price=50000.0,
            current_regime="volatile",
        )

        assert pred_favorable.confidence >= pred_unfavorable.confidence

    def test_prediction_without_regime_still_works(self):
        """PredictionEngine should work when current_regime is empty."""
        from ppmt.engine.prediction import PredictionEngine

        trie = PPMTTrie(name="test_no_regime")
        trie.insert_with_observations(
            symbols=["a", "b"], move_pct=3.0, drawdown_pct=-1.0,
            favorable_pct=4.0, duration=10, won=True,
        )
        trie.propagate_metadata()

        engine = PredictionEngine(trie, prediction_depth=3)
        # Should not crash
        pred = engine.predict(
            current_symbols=["a", "b"],
            entry_price=50000.0,
            current_regime="",  # No regime
        )
        assert pred is not None
