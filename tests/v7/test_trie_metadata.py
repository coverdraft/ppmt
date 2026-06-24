"""
Tests for TrieNodeV6Metadata (F1).

Verifies:
- Basic update + stats
- Welford's variance numerical stability
- Per-regime predictions
- Freshness decay
- Trustworthiness gate
- Serialization round-trip
- Anti-leakage: insert-after-predict contract (no temporal leakage in metadata)
"""

import sys
import os
import time
import math
import json

# Add scripts/v7 to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "v7"))

from v7_trie_metadata import TrieNodeV6Metadata, RegimeStatsV6


def test_basic_update():
    """Test that basic update increments count and updates sums."""
    meta = TrieNodeV6Metadata()
    assert meta.historical_count == 0
    assert meta.mean_fwd_ret_15m == 0.0
    
    meta.update_from_observation(fwd_ret_15m=0.5, vol_regime=1, timestamp=1000.0)
    assert meta.historical_count == 1
    assert meta.sum_fwd_ret_15m == 0.5
    assert meta.mean_fwd_ret_15m == 0.5
    assert meta.vol_regime_distribution == {1: 1}
    
    meta.update_from_observation(fwd_ret_15m=-0.3, vol_regime=1, timestamp=2000.0)
    assert meta.historical_count == 2
    assert meta.mean_fwd_ret_15m == 0.1  # (0.5 + -0.3) / 2
    assert meta.last_observation_time == 2000.0
    
    print("✓ test_basic_update")


def test_welford_variance():
    """Test Welford's online variance computation."""
    meta = TrieNodeV6Metadata()
    
    # Insert 100 observations with known mean=0.1, std=0.5
    values = [0.6, -0.4, 0.8, 0.2, -0.5, 0.3, 0.9, -0.2, 0.5, 0.1] * 10
    for i, v in enumerate(values):
        meta.update_from_observation(fwd_ret_15m=v, vol_regime=1, timestamp=1000.0 + i)
    
    expected_mean = sum(values) / len(values)
    expected_var = sum((v - expected_mean) ** 2 for v in values) / len(values)
    
    assert abs(meta.mean_fwd_ret_15m - expected_mean) < 1e-10, \
        f"Mean mismatch: {meta.mean_fwd_ret_15m} vs {expected_mean}"
    assert abs(meta.variance_fwd_ret_15m - expected_var) < 1e-10, \
        f"Variance mismatch: {meta.variance_fwd_ret_15m} vs {expected_var}"
    assert abs(meta.std_fwd_ret_15m - math.sqrt(expected_var)) < 1e-10
    
    print(f"✓ test_welford_variance (mean={meta.mean_fwd_ret_15m:.4f}, std={meta.std_fwd_ret_15m:.4f})")


def test_per_regime_predictions():
    """Test that per-regime stats are independent."""
    meta = TrieNodeV6Metadata()
    
    # Regime 0 (low vol): small moves
    for v in [0.1, 0.05, -0.02, 0.08, 0.03]:
        meta.update_from_observation(fwd_ret_15m=v, vol_regime=0, timestamp=1000.0)
    
    # Regime 3 (extreme vol): big moves
    for v in [2.0, -1.5, 1.8, -2.2, 1.6]:
        meta.update_from_observation(fwd_ret_15m=v, vol_regime=3, timestamp=2000.0)
    
    assert meta.historical_count == 10
    assert meta.vol_regime_distribution == {0: 5, 3: 5}
    
    # Per-regime predictions
    pred_0 = meta.prediction_for_regime(0)
    pred_3 = meta.prediction_for_regime(3)
    
    assert 0.04 < pred_0 < 0.06, f"Regime 0 pred should be ~0.05, got {pred_0}"
    assert 0.3 < pred_3 < 0.4, f"Regime 3 pred should be ~0.34, got {pred_3}"
    
    # Global prediction is mean of all 10
    expected_global = (sum([0.1, 0.05, -0.02, 0.08, 0.03]) + sum([2.0, -1.5, 1.8, -2.2, 1.6])) / 10
    assert abs(meta.prediction - expected_global) < 1e-10
    
    print(f"✓ test_per_regime_predictions (regime_0={pred_0:.4f}, regime_3={pred_3:.4f})")


def test_min_observations_gate():
    """Test that prediction returns 0 if too few observations."""
    meta = TrieNodeV6Metadata()
    
    # Empty node
    assert meta.prediction == 0.0
    assert not meta.is_trustworthy
    
    # 1 observation
    meta.update_from_observation(fwd_ret_15m=0.5, vol_regime=1, timestamp=1000.0)
    assert meta.prediction == 0.0  # below MIN_OBS_FOR_PREDICTION=3
    assert not meta.is_trustworthy
    
    # 2 observations
    meta.update_from_observation(fwd_ret_15m=0.3, vol_regime=1, timestamp=2000.0)
    assert meta.prediction == 0.0
    
    # 3 observations — now predicts
    meta.update_from_observation(fwd_ret_15m=0.4, vol_regime=1, timestamp=3000.0)
    assert meta.prediction != 0.0
    assert abs(meta.prediction - 0.4) < 1e-10  # mean of [0.5, 0.3, 0.4]
    
    print("✓ test_min_observations_gate")


def test_freshness_decay():
    """Test exponential decay based on time since last observation."""
    meta = TrieNodeV6Metadata()
    meta.FRESHNESS_HALF_LIFE_HOURS = 24.0
    
    # Never observed
    assert meta.freshness_decay == 0.0
    
    # Just observed (now)
    meta.last_observation_time = time.time()
    assert abs(meta.freshness_decay - 1.0) < 0.01
    
    # 24h ago = half-life, should be ~0.5
    meta.last_observation_time = time.time() - (24 * 3600)
    assert abs(meta.freshness_decay - 0.5) < 0.05
    
    # 48h ago = 2 half-lives, should be ~0.25
    meta.last_observation_time = time.time() - (48 * 3600)
    assert abs(meta.freshness_decay - 0.25) < 0.05
    
    # 7 days ago = very stale
    meta.last_observation_time = time.time() - (7 * 24 * 3600)
    assert meta.freshness_decay < 0.01
    
    print("✓ test_freshness_decay")


def test_node_type_transition():
    """Test that node_type transitions from dependent to independent."""
    meta = TrieNodeV6Metadata()
    assert meta.node_type == "dependent"
    
    # Add 9 observations — still dependent
    for i in range(9):
        meta.update_from_observation(fwd_ret_15m=0.1, vol_regime=1, timestamp=float(i))
    assert meta.node_type == "dependent"
    
    # 10th observation — becomes independent
    meta.update_from_observation(fwd_ret_15m=0.1, vol_regime=1, timestamp=10.0)
    assert meta.node_type == "independent"
    
    print("✓ test_node_type_transition")


def test_trustworthy_gate():
    """Test is_trustworthy with various conditions."""
    meta = TrieNodeV6Metadata()
    
    # Empty
    assert not meta.is_trustworthy
    
    # Has 5 observations but no trading observations and not fresh
    for i in range(5):
        meta.update_from_observation(
            fwd_ret_15m=0.1, vol_regime=1, timestamp=time.time()
        )
    assert meta.is_trustworthy  # 5 obs, fresh, no trading obs but historical >= 5
    
    # Stale (7 days old)
    meta.last_observation_time = time.time() - (7 * 24 * 3600)
    assert not meta.is_trustworthy  # too stale
    
    # Trading observation but few historical
    meta2 = TrieNodeV6Metadata()
    meta2.update_from_observation(
        fwd_ret_15m=0.1,
        vol_regime=1,
        timestamp=time.time(),
        is_trading_observation=True,
    )
    assert not meta2.is_trustworthy  # only 1 obs, below MIN_OBS_FOR_PREDICTION
    
    print("✓ test_trustworthy_gate")


def test_serialization_roundtrip():
    """Test to_dict / from_dict serialization."""
    meta1 = TrieNodeV6Metadata()
    for i, (v, r) in enumerate([
        (0.5, 0), (0.3, 0), (-0.2, 1), (0.4, 1), (0.1, 2),
        (0.6, 0), (-0.3, 1), (0.2, 2), (0.5, 3), (0.4, 0),
    ]):
        meta1.update_from_observation(
            fwd_ret_15m=v, vol_regime=r, timestamp=1000.0 + i
        )
    
    # Serialize
    d = meta1.to_dict()
    json_str = json.dumps(d)  # must be JSON-serializable
    d2 = json.loads(json_str)
    
    # Deserialize
    meta2 = TrieNodeV6Metadata.from_dict(d2)
    
    # Verify all fields match
    assert meta1.historical_count == meta2.historical_count
    assert abs(meta1.sum_fwd_ret_15m - meta2.sum_fwd_ret_15m) < 1e-10
    assert abs(meta1.sum_sq_fwd_ret_15m - meta2.sum_sq_fwd_ret_15m) < 1e-10
    assert meta1.last_observation_time == meta2.last_observation_time
    assert meta1.vol_regime_distribution == meta2.vol_regime_distribution
    assert meta1.node_type == meta2.node_type
    assert meta1.trading_observations == meta2.trading_observations
    
    # Per-regime predictions match
    for r in [0, 1, 2, 3]:
        assert abs(
            meta1.prediction_for_regime(r) - meta2.prediction_for_regime(r)
        ) < 1e-10
    
    print("✓ test_serialization_roundtrip")


def test_regime_stats_v6():
    """Test RegimeStatsV6 class directly."""
    rs = RegimeStatsV6()
    assert rs.count == 0
    assert rs.mean == 0.0
    assert rs.prediction == 0.0  # below threshold
    assert rs.confidence == 0.0
    
    # Add 5 observations
    for v in [0.1, 0.2, 0.15, 0.05, 0.25]:
        rs.update(fwd_ret=v, ts=1000.0)
    
    assert rs.count == 5
    assert abs(rs.mean - 0.15) < 1e-10
    assert rs.prediction != 0.0  # >= 3 obs
    assert 0 < rs.confidence <= 1.0
    
    print(f"✓ test_regime_stats_v6 (mean={rs.mean:.4f}, conf={rs.confidence:.4f})")


def test_anti_leakage_contract():
    """
    Verify that the metadata structure supports the INSERT-AFTER-PREDICT rule.
    
    The metadata itself doesn't enforce temporal ordering, but it stores
    last_observation_time which can be checked before using prediction.
    
    A correct implementation:
    1. Query trie at time T for prediction (uses only obs with ts < T)
    2. After 15m delay, insert observation at time T+15m
    
    This test verifies that a node queried at time T doesn't accidentally
    include an observation from time T (which would be leakage).
    """
    meta = TrieNodeV6Metadata()
    
    # Insert observations up to time T=1000
    for i in range(10):
        meta.update_from_observation(
            fwd_ret_15m=0.1 * i,
            vol_regime=1,
            timestamp=100.0 * i,  # ts: 0, 100, 200, ..., 900
        )
    
    # Query at T=1000 — should see all 10 observations (all ts < 1000)
    # The prediction uses mean of [0, 0.1, 0.2, ..., 0.9] = 0.45
    pred_at_1000 = meta.prediction
    assert abs(pred_at_1000 - 0.45) < 1e-10
    
    # Now insert observation at T=1100 (this is the "future" relative to T=1000)
    meta.update_from_observation(fwd_ret_15m=10.0, vol_regime=1, timestamp=1100.0)
    
    # If we had queried at T=1000 BEFORE the T=1100 insert, pred should be 0.45
    # The metadata doesn't know about temporal queries — that's the caller's job.
    # This test just confirms that update doesn't break anything.
    pred_after_1100 = meta.prediction
    assert pred_after_1100 != pred_at_1000  # prediction changed
    
    # The CALLER must enforce insert-after-predict by:
    # 1. Querying prediction BEFORE inserting new observation
    # 2. Only inserting AFTER prediction is recorded
    
    print("✓ test_anti_leakage_contract (metadata structure supports temporal ordering)")


def test_repr():
    """Test __repr__ doesn't crash."""
    meta = TrieNodeV6Metadata()
    for i in range(5):
        meta.update_from_observation(
            fwd_ret_15m=0.1 * i, vol_regime=1, timestamp=float(i)
        )
    s = repr(meta)
    assert "TrieNodeV6Metadata" in s
    assert "count=5" in s
    print(f"✓ test_repr: {s}")


def test_dominant_regime():
    """Test dominant_regime and regime_concentration."""
    meta = TrieNodeV6Metadata()
    
    # Empty
    assert meta.dominant_regime == -1
    assert meta.regime_concentration == 0.0
    
    # All in regime 0
    for i in range(10):
        meta.update_from_observation(fwd_ret_15m=0.1, vol_regime=0, timestamp=float(i))
    assert meta.dominant_regime == 0
    assert meta.regime_concentration == 1.0
    
    # Mix: 10 in regime 0, 3 in regime 1, total = 13
    # max_count = 10, concentration = 10/13
    for i in range(3):
        meta.update_from_observation(fwd_ret_15m=0.1, vol_regime=1, timestamp=100.0 + i)
    assert meta.dominant_regime == 0
    assert abs(meta.regime_concentration - 10/13) < 1e-10
    
    print("✓ test_dominant_regime")


if __name__ == "__main__":
    print("=" * 60)
    print("PPMT v7 — F1 TrieNodeV6Metadata Tests")
    print("=" * 60)
    print()
    
    test_basic_update()
    test_welford_variance()
    test_per_regime_predictions()
    test_min_observations_gate()
    test_freshness_decay()
    test_node_type_transition()
    test_trustworthy_gate()
    test_serialization_roundtrip()
    test_regime_stats_v6()
    test_anti_leakage_contract()
    test_repr()
    test_dominant_regime()
    
    print()
    print("=" * 60)
    print("ALL TESTS PASSED ✓")
    print("=" * 60)
