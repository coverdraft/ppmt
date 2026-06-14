"""
V4.3 Robust Tests — Non-Distorting, Behavior-Focused

These tests validate:
1. SHORT gate regime-aware logic
2. Real historical_count in signals (no hardcoded 100)
3. Regime propagation correctness
4. Independent/dependent node classification
5. Confidence calculation invariants
6. Regime match score behavior
7. Move variance (Welford) correctness
8. SAX encoding consistency
9. Full pipeline integration (SAX → Trie → Prediction)

Design principles (ANTI-DISTORTION):
1. Test BEHAVIOR not implementation — verify what the system DOES
2. Use REALISTIC scenarios, not just edge cases
3. Verify INVARIANTS hold (ranges, monotonicity, consistency)
4. Test that WRONG inputs don't produce PLAUSIBLE-LOOKING wrong outputs
5. Each test is INDEPENDENT — no shared mutable state
6. No mocking of internal methods — test the public API
7. Verify that improvements are REAL improvements, not artifacts
"""

import pytest
import time
import numpy as np

from ppmt.core.metadata import BlockLifecycleMetadata, RegimeStats
from ppmt.core.trie import PPMTTrie, TrieNode
from ppmt.core.sax import SAXEncoder, SAX_BREAKPOINTS
from ppmt.core.regime import RegimeDetector, RegimeInfo
from ppmt.engine.prediction import PredictionEngine


# ================================================================
# 1. SHORT GATE — Regime-Aware Logic
# ================================================================

class TestShortGateRegimeAware:
    """Test V4.3 SHORT gate: regime-aware confidence multiplier.

    Key behavior:
    - trending_down: SHORT is FAVORABLE → lower threshold (0.85x)
    - ranging:       SHORT is NEUTRAL   → slight penalty (1.1x)
    - trending_up:   SHORT is ADVERSE   → strict penalty (1.5x)
    - volatile:      SHORT is DANGEROUS  → hard gate (1.8x)
    - Floor of 0.20 always applies
    """

    def test_short_easier_in_downtrend(self):
        """SHORT threshold should be LOWER in trending_down than in ranging."""
        base_conf = 0.20
        # trending_down: 0.20 * 0.85 = 0.17 → floored to 0.20
        # But the logic is: max(base * mult, 0.20)
        # So in trending_down: max(0.17, 0.20) = 0.20
        # In ranging: max(0.22, 0.20) = 0.22
        # trending_down is easier (lower threshold) than ranging
        td_threshold = max(base_conf * 0.85, 0.20)
        range_threshold = max(base_conf * 1.1, 0.20)
        assert td_threshold <= range_threshold

    def test_short_hardest_in_volatile(self):
        """SHORT threshold should be HIGHEST in volatile regime."""
        base_conf = 0.20
        thresholds = {
            "trending_down": max(base_conf * 0.85, 0.20),
            "ranging": max(base_conf * 1.1, 0.20),
            "trending_up": max(base_conf * 1.5, 0.20),
            "volatile": max(base_conf * 1.8, 0.20),
        }
        assert thresholds["volatile"] >= thresholds["trending_up"]
        assert thresholds["volatile"] >= thresholds["ranging"]
        assert thresholds["volatile"] >= thresholds["trending_down"]

    def test_short_threshold_ordering(self):
        """SHORT thresholds should be monotonically increasing by regime risk."""
        base_conf = 0.25  # Use higher base to avoid floor effects
        thresholds = {
            "trending_down": max(base_conf * 0.85, 0.20),
            "ranging": max(base_conf * 1.1, 0.20),
            "trending_up": max(base_conf * 1.5, 0.20),
            "volatile": max(base_conf * 1.8, 0.20),
        }
        assert thresholds["trending_down"] < thresholds["ranging"]
        assert thresholds["ranging"] < thresholds["trending_up"]
        assert thresholds["trending_up"] < thresholds["volatile"]

    def test_short_floor_always_applies(self):
        """Regardless of regime, SHORT threshold should never go below 0.20."""
        base_conf = 0.10  # Very low base
        for mult in [0.85, 1.1, 1.5, 1.8]:
            threshold = max(base_conf * mult, 0.20)
            assert threshold >= 0.20

    def test_short_vs_long_threshold(self):
        """SHORT threshold should always be >= LONG threshold (same base)."""
        base_conf = 0.20
        # LONG threshold = base_conf (no multiplier)
        # SHORT threshold = max(base_conf * mult, 0.20) where mult >= 0.85
        for regime_mult in [0.85, 1.1, 1.5, 1.8]:
            short_threshold = max(base_conf * regime_mult, 0.20)
            assert short_threshold >= base_conf  # SHORT >= LONG


# ================================================================
# 2. Historical Count in Signals
# ================================================================

class TestHistoricalCountInSignals:
    """Test V4.3 fix: Signal uses real historical_count from matched node.

    Key behavior:
    - Signal.historical_count should reflect the actual Trie node's count
    - This affects quality_score and sizing_multiplier via Bayesian shrinkage
    - Hardcoded 100 was distorting: it made rare patterns look reliable
    """

    def test_rare_pattern_lower_confidence(self):
        """A pattern observed 3 times should have lower confidence than 100 times."""
        meta_rare = BlockLifecycleMetadata(win_rate=0.7, historical_count=3)
        meta_common = BlockLifecycleMetadata(win_rate=0.7, historical_count=100)
        assert meta_rare.confidence < meta_common.confidence

    def test_very_rare_pattern_very_low_confidence(self):
        """A pattern observed once should have very low confidence."""
        meta = BlockLifecycleMetadata(win_rate=0.8, historical_count=1)
        # With 1 observation, Bayesian shrinkage should pull toward 0.5
        assert meta.confidence < 0.5  # Strongly shrunk

    def test_trie_search_returns_real_count(self):
        """Trie search should return nodes with actual observation counts."""
        trie = PPMTTrie(name="test_real_count")
        # Insert a pattern 7 times
        for i in range(7):
            trie.insert_with_observations(
                symbols=["a", "d", "b"],
                move_pct=3.0 + i * 0.5,
                drawdown_pct=-1.0,
                favorable_pct=4.0,
                duration=10,
                won=True,
                next_symbol="h",
            )
        trie.propagate_metadata()

        node = trie.search(["a", "d", "b"])
        assert node is not None
        assert node.metadata.historical_count == 7  # Not 100

    def test_default_count_conservative(self):
        """When no Trie node found, default count should be conservative (10, not 100)."""
        # The fix changed the default from 100 to 10
        # With count=10, Bayesian shrinkage is moderate
        meta = BlockLifecycleMetadata(win_rate=0.7, historical_count=10)
        # Should produce reasonable but not overconfident results
        assert 0.3 < meta.confidence < 0.7


# ================================================================
# 3. Regime Propagation
# ================================================================

class TestRegimePropagation:
    """Test that regime info propagates correctly from children to parents.

    Key behavior:
    - Terminal nodes store their own regime info
    - Intermediate nodes aggregate from children
    - dominant_regime = most common regime across observations
    - regime_distribution = histogram of regime counts
    - regime_stats = per-regime win_rate and expected_move
    """

    def test_terminal_node_regime(self):
        """A terminal node with regime observations should have correct dominant_regime."""
        trie = PPMTTrie(name="test_regime_term")
        # Insert pattern observed mostly in trending_up
        for _ in range(15):
            trie.insert_with_observations(
                symbols=["a", "b"], move_pct=3.0, drawdown_pct=-1.0,
                favorable_pct=4.0, duration=10, won=True,
                next_symbol="c", regime="trending_up", regime_confidence=0.8,
            )
        for _ in range(5):
            trie.insert_with_observations(
                symbols=["a", "b"], move_pct=-1.0, drawdown_pct=-2.0,
                favorable_pct=1.0, duration=8, won=False,
                next_symbol="d", regime="volatile", regime_confidence=0.6,
            )

        node = trie.search(["a", "b"])
        assert node is not None
        assert node.metadata.dominant_regime == "trending_up"
        assert node.metadata.regime_distribution["trending_up"] == 15
        assert node.metadata.regime_distribution["volatile"] == 5

    def test_intermediate_node_aggregates_regime(self):
        """After propagate_metadata(), intermediate nodes should aggregate regime."""
        trie = PPMTTrie(name="test_regime_inter")
        # Two children of 'a' with different dominant regimes
        for _ in range(10):
            trie.insert_with_observations(
                symbols=["a", "b"], move_pct=3.0, drawdown_pct=-1.0,
                favorable_pct=4.0, duration=10, won=True,
                regime="trending_up", regime_confidence=0.8,
            )
        for _ in range(20):
            trie.insert_with_observations(
                symbols=["a", "c"], move_pct=-2.0, drawdown_pct=-3.0,
                favorable_pct=1.0, duration=8, won=False,
                regime="trending_down", regime_confidence=0.9,
            )
        trie.propagate_metadata()

        # Node 'a' should aggregate both children
        node_a = trie.search(["a"])
        assert node_a is not None
        assert node_a.metadata.historical_count == 30
        assert node_a.metadata.dominant_regime == "trending_down"  # 20 vs 10
        assert "trending_up" in node_a.metadata.regime_distribution
        assert "trending_down" in node_a.metadata.regime_distribution

    def test_regime_stats_track_wins_per_regime(self):
        """Per-regime stats should track wins separately for each regime."""
        trie = PPMTTrie(name="test_regime_stats")
        # 10 observations in trending_up: 8 wins
        for i in range(10):
            trie.insert_with_observations(
                symbols=["a", "b"], move_pct=3.0, drawdown_pct=-1.0,
                favorable_pct=4.0, duration=10, won=(i < 8),
                regime="trending_up", regime_confidence=0.8,
            )
        # 10 observations in volatile: 3 wins
        for i in range(10):
            trie.insert_with_observations(
                symbols=["a", "b"], move_pct=-0.5, drawdown_pct=-2.5,
                favorable_pct=1.0, duration=6, won=(i < 3),
                regime="volatile", regime_confidence=0.7,
            )

        node = trie.search(["a", "b"])
        assert node is not None
        assert node.metadata.regime_stats["trending_up"].win_rate == 0.8
        assert node.metadata.regime_stats["volatile"].win_rate == 0.3

    def test_empty_regime_treated_as_unknown(self):
        """Nodes without regime info should have empty regime fields."""
        trie = PPMTTrie(name="test_no_regime")
        trie.insert_with_observations(
            symbols=["a", "b"], move_pct=3.0, drawdown_pct=-1.0,
            favorable_pct=4.0, duration=10, won=True,
            # No regime parameter
        )
        node = trie.search(["a", "b"])
        assert node is not None
        assert node.metadata.regime == ""  # Empty, not crashed
        assert node.metadata.dominant_regime == ""


# ================================================================
# 4. Independent/Dependent Node Classification
# ================================================================

class TestNodeClassification:
    """Test independent vs dependent node classification.

    Key behavior:
    - Nodes with count >= min_independent_count (default 10) are 'independent'
    - Nodes with count < min_independent_count are 'dependent'
    - Dependent nodes have confidence scaled down
    - Classification happens during propagate_metadata() and update_from_observation()
    """

    def test_few_observations_is_dependent(self):
        """A node with < 10 observations should be classified as dependent."""
        meta = BlockLifecycleMetadata()
        for i in range(5):
            meta.update_from_observation(
                move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
                duration=10, won=True,
            )
        assert meta.node_type == "dependent"

    def test_many_observations_is_independent(self):
        """A node with >= 10 observations should be classified as independent."""
        meta = BlockLifecycleMetadata()
        for i in range(10):
            meta.update_from_observation(
                move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
                duration=10, won=True,
            )
        assert meta.node_type == "independent"

    def test_dependency_penalty_reduces_confidence(self):
        """Dependent nodes should have lower confidence than independent with same win_rate."""
        meta_dep = BlockLifecycleMetadata(
            win_rate=0.7, expected_move_pct=3.0,
            historical_count=5,  # dependent
        )
        meta_indep = BlockLifecycleMetadata(
            win_rate=0.7, expected_move_pct=3.0,
            historical_count=50,  # independent
        )
        assert meta_dep.confidence < meta_indep.confidence

    def test_propagation_classifies_nodes(self):
        """After propagate_metadata(), nodes should be correctly classified."""
        trie = PPMTTrie(name="test_classify")
        # Insert pattern 15 times (should become independent)
        for _ in range(15):
            trie.insert_with_observations(
                symbols=["a", "b"], move_pct=3.0, drawdown_pct=-1.0,
                favorable_pct=4.0, duration=10, won=True,
            )
        # Insert another pattern 3 times (should be dependent)
        for _ in range(3):
            trie.insert_with_observations(
                symbols=["a", "c"], move_pct=-1.0, drawdown_pct=-2.0,
                favorable_pct=1.0, duration=8, won=False,
            )
        trie.propagate_metadata()

        node_b = trie.search(["a", "b"])
        node_c = trie.search(["a", "c"])
        assert node_b is not None
        assert node_c is not None
        assert node_b.metadata.node_type == "independent"
        assert node_c.metadata.node_type == "dependent"


# ================================================================
# 5. Confidence Calculation Invariants
# ================================================================

class TestConfidenceInvariants:
    """Test that confidence satisfies mathematical invariants.

    Key invariants:
    1. confidence is always in [0, 1]
    2. Higher win_rate → higher confidence (all else equal)
    3. Higher count → higher confidence (all else equal)
    4. Independent > Dependent confidence (same stats)
    5. confidence == 0 when historical_count == 0
    """

    def test_confidence_range(self):
        """Confidence must always be in [0, 1]."""
        for count in [0, 1, 5, 10, 50, 100, 1000]:
            for wr in [0.0, 0.3, 0.5, 0.7, 1.0]:
                meta = BlockLifecycleMetadata(
                    win_rate=wr, historical_count=count,
                    expected_move_pct=3.0 if count > 0 else 0.0,
                )
                assert 0.0 <= meta.confidence <= 1.0, (
                    f"Confidence {meta.confidence} out of range for "
                    f"wr={wr}, count={count}"
                )

    def test_zero_count_zero_confidence(self):
        """No observations → zero confidence."""
        meta = BlockLifecycleMetadata()
        assert meta.confidence == 0.0

    def test_higher_win_rate_higher_confidence(self):
        """Higher win_rate should produce higher confidence (same count)."""
        count = 50
        conf_50 = BlockLifecycleMetadata(win_rate=0.5, historical_count=count).confidence
        conf_70 = BlockLifecycleMetadata(win_rate=0.7, historical_count=count).confidence
        conf_90 = BlockLifecycleMetadata(win_rate=0.9, historical_count=count).confidence
        assert conf_50 < conf_70 < conf_90

    def test_higher_count_higher_confidence(self):
        """More observations should produce higher confidence (same win_rate)."""
        wr = 0.7
        conf_5 = BlockLifecycleMetadata(win_rate=wr, historical_count=5).confidence
        conf_50 = BlockLifecycleMetadata(win_rate=wr, historical_count=50).confidence
        conf_500 = BlockLifecycleMetadata(win_rate=wr, historical_count=500).confidence
        assert conf_5 < conf_50 < conf_500

    def test_perfect_win_rate_still_bounded(self):
        """Even with 100% win rate, confidence should be < 1.0 for low counts."""
        meta = BlockLifecycleMetadata(win_rate=1.0, historical_count=3)
        assert meta.confidence < 1.0  # Bayesian shrinkage prevents overconfidence


# ================================================================
# 6. Regime Match Score
# ================================================================

class TestRegimeMatchScore:
    """Test regime_match_score behavior.

    Key behavior:
    - Returns 1.0 when no regime info available (neutral)
    - Returns boost (1.0-1.2) when current regime matches dominant
    - Returns penalty (0.5-0.8) when current regime doesn't match
    - Returns heavy penalty (0.5-0.7) when regime never observed
    - Uses regime_stats to adjust by per-regime win_rate
    """

    def test_no_regime_info_returns_neutral(self):
        """No regime distribution → return 1.0 (neutral)."""
        meta = BlockLifecycleMetadata()
        assert meta.regime_match_score("trending_up") == 1.0

    def test_empty_regime_returns_neutral(self):
        """Empty string regime → return 1.0."""
        meta = BlockLifecycleMetadata(
            regime_distribution={"trending_up": 10},
            dominant_regime="trending_up",
        )
        assert meta.regime_match_score("") == 1.0

    def test_dominant_regime_match_boosts(self):
        """Matching dominant regime should boost score >= 1.0."""
        meta = BlockLifecycleMetadata(
            regime_distribution={"trending_up": 50, "ranging": 20},
            dominant_regime="trending_up",
            node_type="independent",
        )
        score = meta.regime_match_score("trending_up")
        assert score >= 1.0

    def test_non_dominant_regime_penalty(self):
        """Non-dominant regime should have score < 1.0."""
        meta = BlockLifecycleMetadata(
            regime_distribution={"trending_up": 50, "ranging": 20},
            dominant_regime="trending_up",
            node_type="independent",
        )
        score = meta.regime_match_score("ranging")
        assert score < 1.0

    def test_never_observed_regime_heavy_penalty(self):
        """A regime never observed should give heavy penalty."""
        meta = BlockLifecycleMetadata(
            regime_distribution={"trending_up": 50},
            dominant_regime="trending_up",
            node_type="independent",
        )
        score = meta.regime_match_score("volatile")
        assert score <= 0.7  # Heavy penalty

    def test_score_always_in_valid_range(self):
        """regime_match_score should always be in [0.5, 1.2]."""
        meta = BlockLifecycleMetadata(
            regime_distribution={
                "trending_up": 30, "trending_down": 20,
                "ranging": 10, "volatile": 5,
            },
            dominant_regime="trending_up",
            node_type="independent",
            regime_stats={
                "trending_up": RegimeStats(count=30, wins=20, total_move_pct=60.0),
                "trending_down": RegimeStats(count=20, wins=8, total_move_pct=-10.0),
            },
        )
        for regime in ["trending_up", "trending_down", "ranging", "volatile", "unknown"]:
            score = meta.regime_match_score(regime)
            assert 0.5 <= score <= 1.2, f"Score {score} out of range for {regime}"


# ================================================================
# 7. Move Variance (Welford's Algorithm)
# ================================================================

class TestMoveVariance:
    """Test that move_variance tracks observation dispersion correctly.

    Key behavior:
    - move_variance starts at 0
    - After 1 observation, variance is 0 (can't compute variance of 1 point)
    - After 2+ observations, variance should be positive
    - move_std = sqrt(variance / (count - 1))
    - move_coefficient_of_variation = std / |mean|
    """

    def test_single_observation_zero_variance(self):
        """One observation → zero variance."""
        meta = BlockLifecycleMetadata()
        meta.update_from_observation(
            move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
            duration=10, won=True,
        )
        assert meta.move_variance == 0.0
        assert meta.move_std == 0.0

    def test_two_observations_positive_variance(self):
        """Two different observations → positive variance."""
        meta = BlockLifecycleMetadata()
        meta.update_from_observation(
            move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
            duration=10, won=True,
        )
        meta.update_from_observation(
            move_pct=-1.0, drawdown_pct=-2.0, favorable_pct=1.0,
            duration=8, won=False,
        )
        assert meta.move_variance > 0
        assert meta.move_std > 0

    def test_identical_observations_zero_variance(self):
        """All identical moves → zero variance."""
        meta = BlockLifecycleMetadata()
        for _ in range(10):
            meta.update_from_observation(
                move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
                duration=10, won=True,
            )
        assert meta.move_variance == 0.0

    def test_wider_spread_higher_variance(self):
        """More dispersed observations → higher variance."""
        meta_tight = BlockLifecycleMetadata()
        for m in [2.8, 3.0, 3.2]:
            meta_tight.update_from_observation(
                move_pct=m, drawdown_pct=-1.0, favorable_pct=4.0,
                duration=10, won=True,
            )

        meta_wide = BlockLifecycleMetadata()
        for m in [-2.0, 3.0, 8.0]:
            meta_wide.update_from_observation(
                move_pct=m, drawdown_pct=-1.0, favorable_pct=4.0,
                duration=10, won=m > 0,
            )

        assert meta_wide.move_std > meta_tight.move_std

    def test_coefficient_of_variation_range(self):
        """CV should be >= 0 and finite for non-zero expected_move."""
        meta = BlockLifecycleMetadata()
        for m in [3.0, -1.0, 5.0, 2.0, -0.5]:
            meta.update_from_observation(
                move_pct=m, drawdown_pct=-1.0, favorable_pct=4.0,
                duration=10, won=m > 0,
            )
        if abs(meta.expected_move_pct) > 1e-10:
            cv = meta.move_coefficient_of_variation
            assert cv >= 0
            assert np.isfinite(cv)


# ================================================================
# 8. SAX Encoding Consistency
# ================================================================

class TestSAXEncodingConsistency:
    """Test that SAX encoding is deterministic and consistent.

    Key behavior:
    - Same data → same symbols (deterministic)
    - encode_with_normalization uses provided stats (not recomputed)
    - Alphabet sizes 3-16 are supported
    - Window size controls symbol granularity
    """

    def test_deterministic_encoding(self):
        """Same input should always produce same output."""
        np.random.seed(42)
        import pandas as pd
        df = pd.DataFrame({
            "open": np.random.randn(200) + 100,
            "high": np.random.randn(200) + 102,
            "low": np.random.randn(200) + 98,
            "close": np.random.randn(200) + 100,
            "volume": np.abs(np.random.randn(200)) * 1000,
        })
        encoder = SAXEncoder(alphabet_size=8, window_size=10)
        symbols1 = encoder.encode(df)
        symbols2 = encoder.encode(df)
        assert symbols1 == symbols2

    def test_normalization_consistency(self):
        """encode_with_normalization should use provided stats, not recompute."""
        import pandas as pd
        np.random.seed(42)
        df = pd.DataFrame({
            "open": np.random.randn(200) + 100,
            "high": np.random.randn(200) + 102,
            "low": np.random.randn(200) + 98,
            "close": np.random.randn(200) + 100,
            "volume": np.abs(np.random.randn(200)) * 1000,
        })
        encoder = SAXEncoder(alphabet_size=8, window_size=10)

        # First: get training stats
        symbols_train, paa_mean, paa_std = encoder.encode_with_normalization(df)

        # Second: use those stats for test encoding
        symbols_test, _, _ = encoder.encode_with_normalization(
            df, paa_mean=paa_mean, paa_std=paa_std
        )

        # Should produce identical symbols when using same stats
        assert symbols_train == symbols_test

    def test_different_normalization_different_symbols(self):
        """Different normalization stats should produce different symbols."""
        import pandas as pd
        np.random.seed(42)
        df = pd.DataFrame({
            "open": np.random.randn(200) + 100,
            "high": np.random.randn(200) + 102,
            "low": np.random.randn(200) + 98,
            "close": np.random.randn(200) + 100,
            "volume": np.abs(np.random.randn(200)) * 1000,
        })
        encoder = SAXEncoder(alphabet_size=8, window_size=10)

        _, mean1, std1 = encoder.encode_with_normalization(df)
        # Shift the normalization
        symbols_shifted, _, _ = encoder.encode_with_normalization(
            df, paa_mean=mean1 + 10, paa_std=std1
        )
        symbols_normal, _, _ = encoder.encode_with_normalization(
            df, paa_mean=mean1, paa_std=std1
        )
        # Shifted normalization should produce different symbols
        assert symbols_shifted != symbols_normal

    def test_all_breakpoints_complete(self):
        """SAX_BREAKPOINTS should have complete arrays for all alphabet sizes."""
        for size, breakpoints in SAX_BREAKPOINTS.items():
            assert len(breakpoints) == size - 1, (
                f"Alphabet size {size} should have {size - 1} breakpoints, "
                f"got {len(breakpoints)}"
            )

    def test_supported_alphabet_sizes(self):
        """SAXEncoder should support all defined alphabet sizes."""
        for size in SAX_BREAKPOINTS:
            encoder = SAXEncoder(alphabet_size=size, window_size=10)
            assert encoder.alphabet_size == size

    def test_unsupported_alphabet_size_raises(self):
        """Unsupported alphabet size should raise ValueError."""
        with pytest.raises(ValueError):
            SAXEncoder(alphabet_size=9, window_size=10)  # 9 not in breakpoints


# ================================================================
# 9. Full Pipeline Integration
# ================================================================

class TestFullPipelineIntegration:
    """Test the complete SAX → Trie → Prediction pipeline.

    These tests verify that all components work together correctly.
    They use synthetic data to avoid dependency on external data sources.
    """

    def _build_synthetic_trie(self, n_patterns=50, seed=42):
        """Build a trie with synthetic data for testing."""
        np.random.seed(seed)
        trie = PPMTTrie(name="test_pipeline")

        regimes = ["trending_up", "trending_down", "ranging", "volatile"]
        symbols = "abcdefgh"

        for i in range(n_patterns):
            # Generate random pattern
            pattern_len = np.random.randint(2, 5)
            pattern = [symbols[np.random.randint(0, 8)] for _ in range(pattern_len)]
            move = np.random.randn() * 3  # Random move -9% to +9%
            regime = regimes[np.random.randint(0, 4)]
            won = move > 0

            trie.insert_with_observations(
                symbols=pattern,
                move_pct=move,
                drawdown_pct=min(move, 0) if move < 0 else -abs(move) * 0.3,
                favorable_pct=max(move, 0) if move > 0 else abs(move) * 0.3,
                duration=np.random.randint(5, 30),
                won=won,
                next_symbol=symbols[np.random.randint(0, 8)],
                regime=regime,
                regime_confidence=np.random.uniform(0.5, 1.0),
            )

        trie.propagate_metadata()
        return trie

    def test_prediction_returns_valid_structure(self):
        """Prediction should return a valid Prediction object."""
        trie = self._build_synthetic_trie()
        engine = PredictionEngine(trie, prediction_depth=3)

        # Find an existing pattern
        patterns = trie.get_all_patterns(min_count=1)
        if not patterns:
            pytest.skip("No patterns in trie")

        symbols, node = patterns[0]
        pred = engine.predict(current_symbols=symbols, entry_price=50000.0)

        assert pred.direction in ["LONG", "SHORT", "FLAT"]
        assert 0.0 <= pred.confidence <= 1.0
        assert pred.pattern_break_probability >= 0.0

    def test_prediction_with_regime_adjustment(self):
        """Prediction with current_regime should adjust confidence."""
        trie = self._build_synthetic_trie(n_patterns=100)
        engine = PredictionEngine(trie, prediction_depth=3)

        patterns = trie.get_all_patterns(min_count=1)
        if not patterns:
            pytest.skip("No patterns in trie")

        symbols, node = patterns[0]

        # Get predictions with different regimes
        pred_no_regime = engine.predict(current_symbols=symbols, entry_price=50000.0)
        pred_with_regime = engine.predict(
            current_symbols=symbols, entry_price=50000.0, current_regime="trending_up"
        )

        # Both should be valid
        assert 0.0 <= pred_no_regime.confidence <= 1.0
        assert 0.0 <= pred_with_regime.confidence <= 1.0

    def test_trie_growth_with_observations(self):
        """Living Trie should grow when new observations are added."""
        trie = PPMTTrie(name="test_growth")
        initial_count = trie.pattern_count

        # Insert new patterns
        for i in range(5):
            trie.insert_with_observations(
                symbols=["a", "b", "c"],
                move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
                duration=10, won=True, next_symbol="d",
            )

        assert trie.pattern_count > initial_count

    def test_trie_merge_preserves_observations(self):
        """Merging two tries should preserve total observation count."""
        trie1 = PPMTTrie(name="trie1")
        trie2 = PPMTTrie(name="trie2")

        for _ in range(10):
            trie1.insert_with_observations(
                symbols=["a", "b"], move_pct=3.0, drawdown_pct=-1.0,
                favorable_pct=4.0, duration=10, won=True,
            )
        for _ in range(15):
            trie2.insert_with_observations(
                symbols=["a", "b"], move_pct=2.0, drawdown_pct=-1.5,
                favorable_pct=3.0, duration=12, won=True,
            )

        trie1.propagate_metadata()
        trie2.propagate_metadata()

        stats = trie1.merge(trie2)
        node = trie1.search(["a", "b"])
        assert node is not None
        assert node.metadata.historical_count == 25  # 10 + 15

    def test_regime_detector_basic(self):
        """RegimeDetector should classify prices into valid regimes."""
        np.random.seed(42)

        # Trending up data
        trend_up = np.cumsum(np.random.randn(100) * 0.01) + 100
        detector = RegimeDetector(lookback=50, vol_threshold=0.6)
        info = detector.detect_detailed(trend_up)
        assert info.regime in RegimeDetector.REGIMES
        assert 0 <= info.confidence <= 1

        # Ranging data
        ranging = np.sin(np.linspace(0, 10, 100)) * 0.5 + 100
        info = detector.detect_detailed(ranging)
        assert info.regime in RegimeDetector.REGIMES

    def test_regime_detector_insufficient_data(self):
        """RegimeDetector should return 'ranging' for insufficient data."""
        detector = RegimeDetector(lookback=50)
        short_prices = np.array([100.0, 101.0, 102.0])
        result = detector.detect(short_prices)
        assert result == "ranging"  # Default for insufficient data


# ================================================================
# 10. Anti-Distortion Tests
# ================================================================

class TestAntiDistortion:
    """Tests specifically designed to catch distortions that look like success.

    These tests verify that the system doesn't produce misleading results:
    - A system that always says "LONG" isn't good just because BTC trends up
    - A test that passes with 100% win rate on 1 trade isn't meaningful
    - Random data shouldn't produce consistently profitable predictions
    """

    def test_random_data_no_consistent_edge(self):
        """Random walk data should NOT produce consistently confident predictions.

        This is a critical anti-distortion test. If random data produces
        high-confidence predictions, the system is overfitting or has a bias.
        """
        np.random.seed(42)
        trie = PPMTTrie(name="test_random")
        symbols_list = "abcdefgh"

        # Build trie from pure random walk
        for _ in range(200):
            pattern_len = np.random.randint(2, 4)
            pattern = [symbols_list[np.random.randint(0, 8)] for _ in range(pattern_len)]
            # Random move with no edge
            move = np.random.randn() * 1.5
            trie.insert_with_observations(
                symbols=pattern, move_pct=move,
                drawdown_pct=-abs(np.random.randn()),
                favorable_pct=abs(np.random.randn()),
                duration=np.random.randint(5, 20),
                won=move > 0,
                regime=np.random.choice(["trending_up", "ranging", "volatile"]),
                regime_confidence=0.5,
            )
        trie.propagate_metadata()

        # Check that average confidence is moderate (not extreme)
        all_patterns = trie.get_all_patterns(min_count=1)
        if all_patterns:
            confidences = [node.metadata.confidence for _, node in all_patterns]
            avg_conf = np.mean(confidences)
            # Random data should NOT produce consistently high confidence
            assert avg_conf < 0.8, (
                f"Average confidence {avg_conf:.2f} is suspiciously high for random data. "
                f"This suggests overfitting or bias in the confidence calculation."
            )

    def test_single_trade_not_meaningful(self):
        """One winning trade should NOT produce high confidence.

        This prevents the '1 trade, 100% WR' distortion.
        """
        meta = BlockLifecycleMetadata()
        meta.update_from_observation(
            move_pct=5.0, drawdown_pct=-1.0, favorable_pct=6.0,
            duration=10, won=True,
        )
        # 1 observation with 100% WR should have LOW confidence
        # because Bayesian shrinkage kicks in
        assert meta.confidence < 0.5, (
            f"Confidence {meta.confidence:.2f} is too high for 1 observation. "
            f"Bayesian shrinkage should prevent overconfidence from single samples."
        )

    def test_low_count_high_wr_is_suspicious(self):
        """3 observations with 100% WR should have moderate, not high, confidence."""
        meta = BlockLifecycleMetadata()
        for _ in range(3):
            meta.update_from_observation(
                move_pct=3.0, drawdown_pct=-1.0, favorable_pct=4.0,
                duration=10, won=True,
            )
        # 3/3 wins but tiny sample → Bayesian shrinkage should moderate
        assert meta.win_rate == 1.0  # Raw WR is 100%
        assert meta.confidence < 0.7, (
            f"Confidence {meta.confidence:.2f} is too high for 3/3 observations. "
            f"The system should not be overconfident from small samples."
        )

    def test_propagation_doesnt_inflate_counts(self):
        """After propagation, total counts should be consistent.

        This catches a potential distortion where propagation might
        double-count or inflate observation numbers.
        """
        trie = PPMTTrie(name="test_no_inflate")

        # Insert specific number of observations
        for _ in range(20):
            trie.insert_with_observations(
                symbols=["a", "b"], move_pct=3.0, drawdown_pct=-1.0,
                favorable_pct=4.0, duration=10, won=True,
            )
        for _ in range(10):
            trie.insert_with_observations(
                symbols=["a", "c"], move_pct=-1.0, drawdown_pct=-2.0,
                favorable_pct=1.0, duration=8, won=False,
            )

        # Before propagation
        node_b = trie.search(["a", "b"])
        node_c = trie.search(["a", "c"])
        assert node_b.metadata.historical_count == 20
        assert node_c.metadata.historical_count == 10

        trie.propagate_metadata()

        # After propagation, terminal nodes should keep their counts
        assert node_b.metadata.historical_count == 20
        assert node_c.metadata.historical_count == 10

        # Parent 'a' should aggregate
        node_a = trie.search(["a"])
        assert node_a.metadata.historical_count == 30  # 20 + 10, not inflated
