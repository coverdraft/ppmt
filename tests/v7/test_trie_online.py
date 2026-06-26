"""
test_trie_online.py — Unit tests for v7_trie_online.OnlineTrie.

Covers:
- Quantization stability (same features → same key)
- Insert-after-predict flow (cannot insert without prior predict_and_record)
- Trie hygiene (prune, LRU eviction)
- Time decay (effective_obs decreases over time)
- Ensemble helper
- Save / load roundtrip
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

# Add scripts/v7 to path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # tests/v7/.. = repo root
sys.path.insert(0, str(ROOT / "scripts" / "v7"))

from v7_trie_online import (
    OnlineTrie,
    TrieNodeOnline,
    ensemble_prediction,
    PRUNE_EVERY_N_INSERTS,
)


# ----------------------------------------------------------------------
# TrieNodeOnline
# ----------------------------------------------------------------------

class TestTrieNodeOnline:
    def test_insert_and_stats(self):
        node = TrieNodeOnline()
        node.insert(1.0, ts=1000)
        node.insert(-1.0, ts=1010)
        node.insert(2.0, ts=1020)
        mean, std, n_obs, eff = node.stats()
        assert n_obs == 3
        assert eff == pytest.approx(3.0)
        assert mean == pytest.approx((1.0 - 1.0 + 2.0) / 3)
        assert std >= 0

    def test_decay_on_insert(self):
        """When inserting with decay=0.5, prior accumulations halve."""
        node = TrieNodeOnline()
        node.insert(1.0, ts=1000, decay_factor=1.0)
        # After first insert: sum=1, eff=1
        node.insert(1.0, ts=2000, decay_factor=0.5)
        # After second: sum = 1*0.5 + 1 = 1.5, eff = 1*0.5 + 1 = 1.5
        mean, _std, n_obs, eff = node.stats()
        assert n_obs == 2
        assert eff == pytest.approx(1.5)
        assert mean == pytest.approx(1.0)  # 1.5/1.5


# ----------------------------------------------------------------------
# OnlineTrie — quantization
# ----------------------------------------------------------------------

class TestQuantization:
    def test_same_features_same_key(self):
        trie = OnlineTrie(n_bins=3)
        X = np.random.RandomState(42).randn(100, 5)
        trie.fit_bins(X)
        feat = np.array([0.1, -0.5, 1.2, 0.0, 2.5])
        k1 = trie._quantize(feat)
        k2 = trie._quantize(feat)
        assert k1 == k2, "Same features must produce same key"

    def test_different_features_different_key(self):
        trie = OnlineTrie(n_bins=3)
        X = np.random.RandomState(42).randn(100, 5)
        trie.fit_bins(X)
        feat_a = np.array([0.1, -0.5, 1.2, 0.0, 2.5])
        feat_b = np.array([3.0, 3.0, 3.0, 3.0, 3.0])  # very different
        assert trie._quantize(feat_a) != trie._quantize(feat_b)

    def test_must_fit_bins_before_quantize(self):
        trie = OnlineTrie(n_bins=3)
        with pytest.raises(RuntimeError, match="fit_bins"):
            trie._quantize(np.array([0.1, 0.2]))

    def test_feature_shape_validation(self):
        trie = OnlineTrie(n_bins=3)
        trie.fit_bins(np.random.randn(50, 4))
        with pytest.raises(ValueError, match="shape mismatch"):
            trie._quantize(np.array([0.1, 0.2, 0.3]))  # wrong shape


# ----------------------------------------------------------------------
# OnlineTrie — insert-after-predict flow
# ----------------------------------------------------------------------

class TestInsertAfterPredict:
    def test_cannot_commit_without_predict(self):
        """If we never called predict_and_record, commit_outcome returns 0."""
        trie = OnlineTrie(n_bins=3)
        trie.fit_bins(np.random.randn(50, 4))
        n = trie.commit_outcome(ts=1000, outcome=0.5)
        assert n == 0

    def test_predict_then_commit(self):
        trie = OnlineTrie(n_bins=3)
        trie.fit_bins(np.random.randn(50, 4))
        feat = np.array([0.1, -0.2, 0.5, 1.0])
        trie.predict_and_record(feat, ts=1000, symbol="BTCUSDT")
        n = trie.commit_outcome(ts=1000, outcome=0.5)
        assert n == 1
        # Trie now has 1 node
        assert len(trie.nodes) == 1
        mean, _std, n_obs, eff = trie.lookup_pattern(feat)
        assert n_obs == 1
        assert mean == pytest.approx(0.5)

    def test_commit_outcome_pops_pending(self):
        """After commit, the pending entry is removed."""
        trie = OnlineTrie(n_bins=3)
        trie.fit_bins(np.random.randn(50, 4))
        feat = np.array([0.1, -0.2, 0.5, 1.0])
        trie.predict_and_record(feat, ts=1000, symbol="BTCUSDT")
        assert len(trie.pending) == 1
        trie.commit_outcome(ts=1000, outcome=0.5)
        assert len(trie.pending) == 0
        # Committing again does nothing
        n = trie.commit_outcome(ts=1000, outcome=0.5)
        assert n == 0

    def test_multiple_symbols_same_ts(self):
        """Multiple symbols predicted at same ts → commit selectively by symbol."""
        trie = OnlineTrie(n_bins=3)
        trie.fit_bins(np.random.randn(50, 4))
        feat_btc = np.array([0.1, -0.2, 0.5, 1.0])
        feat_eth = np.array([0.5, 0.5, 0.5, 0.5])  # different pattern
        trie.predict_and_record(feat_btc, ts=1000, symbol="BTCUSDT")
        trie.predict_and_record(feat_eth, ts=1000, symbol="ETHUSDT")
        assert len(trie.pending[1000]) == 2
        # Commit only BTC (with outcome=0.5); ETH stays pending
        n = trie.commit_outcome(ts=1000, outcome=0.5, symbol="BTCUSDT")
        assert n == 1
        assert len(trie.pending) == 1  # ETH still pending
        # Commit ETH with different outcome
        n = trie.commit_outcome(ts=1000, outcome=-0.3, symbol="ETHUSDT")
        assert n == 1
        assert len(trie.pending) == 0
        # Both nodes exist with correct means
        mean_btc, _, n_btc, _ = trie.lookup_pattern(feat_btc)
        mean_eth, _, n_eth, _ = trie.lookup_pattern(feat_eth)
        assert n_btc == 1
        assert mean_btc == pytest.approx(0.5)
        assert n_eth == 1
        assert mean_eth == pytest.approx(-0.3)


# ----------------------------------------------------------------------
# OnlineTrie — hygiene
# ----------------------------------------------------------------------

class TestTrieHygiene:
    def test_prune_removes_low_obs_nodes(self):
        """Nodes with effective_obs < prune_min_obs get pruned."""
        trie = OnlineTrie(n_bins=3, prune_min_obs=5)
        trie.fit_bins(np.random.randn(50, 4))
        # Insert 3 nodes with only 1 obs each
        for i in range(3):
            feat = np.random.RandomState(i).randn(4)
            trie.predict_and_record(feat, ts=1000 + i, symbol="X")
            trie.commit_outcome(ts=1000 + i, outcome=0.1)
        assert len(trie.nodes) == 3
        trie.prune()
        assert len(trie.nodes) == 0  # all removed (effective_obs=1 < 5)

    def test_prune_keeps_high_obs_nodes(self):
        trie = OnlineTrie(n_bins=3, prune_min_obs=3)
        trie.fit_bins(np.random.randn(50, 4))
        feat = np.array([0.1, -0.2, 0.5, 1.0])
        # Insert 5 times into same node
        for i in range(5):
            trie.predict_and_record(feat, ts=1000 + i, symbol="X")
            trie.commit_outcome(ts=1000 + i, outcome=0.1 * i)
        trie.prune()
        assert len(trie.nodes) == 1

    def test_lru_eviction(self):
        """When nodes > max_nodes, oldest (last_ts) are evicted."""
        trie = OnlineTrie(n_bins=2, max_nodes=3, prune_min_obs=0)
        trie.fit_bins(np.random.randn(50, 4))
        # Insert 5 different patterns at increasing ts
        for i in range(5):
            feat = np.random.RandomState(i).randn(4) * 10  # diverse
            trie.predict_and_record(feat, ts=1000 + i, symbol="X")
            trie.commit_outcome(ts=1000 + i, outcome=0.1)
            trie.prune()  # prune uses prune_min_obs=0 from constructor
        # Should be capped at 3
        assert len(trie.nodes) <= 3

    def test_decay_reduces_effective_obs(self):
        """After decay, effective_obs should drop."""
        trie = OnlineTrie(n_bins=3, half_life_hours=1.0)
        trie.fit_bins(np.random.randn(50, 4))
        feat = np.array([0.1, -0.2, 0.5, 1.0])
        # Insert at ts=0
        trie.predict_and_record(feat, ts=0, symbol="X")
        trie.commit_outcome(ts=0, outcome=1.0, current_ts=0)
        node_key = list(trie.nodes.keys())[0]
        node = trie.nodes[node_key]
        assert node.effective_obs == pytest.approx(1.0)
        # Insert another (same node) but 3 hours later → decay = 0.5^3 = 0.125
        trie.predict_and_record(feat, ts=3 * 3600, symbol="X")
        trie.commit_outcome(ts=3 * 3600, outcome=1.0, current_ts=3 * 3600)
        # eff = 1.0 * 0.125 + 1.0 = 1.125
        assert node.effective_obs == pytest.approx(1.125, rel=1e-3)


# ----------------------------------------------------------------------
# Ensemble helper
# ----------------------------------------------------------------------

class TestEnsemble:
    def test_low_obs_returns_lgb_only(self):
        """If trie_n_obs < trie_min_obs, return lgb_pred unchanged."""
        result = ensemble_prediction(
            lgb_pred=0.5, trie_mean=1.0, trie_n_obs=2,
            trie_min_obs=5, trie_weight=0.2,
        )
        assert result == pytest.approx(0.5)

    def test_high_obs_blends(self):
        """If trie_n_obs >= trie_min_obs, blend with trie_weight."""
        result = ensemble_prediction(
            lgb_pred=0.5, trie_mean=1.0, trie_n_obs=10,
            trie_min_obs=5, trie_weight=0.2,
        )
        # 0.5 * 0.8 + 1.0 * 0.2 = 0.4 + 0.2 = 0.6
        assert result == pytest.approx(0.6)

    def test_weight_zero_is_pure_lgb(self):
        result = ensemble_prediction(
            lgb_pred=0.5, trie_mean=99.0, trie_n_obs=100,
            trie_min_obs=5, trie_weight=0.0,
        )
        assert result == pytest.approx(0.5)

    def test_weight_one_is_pure_trie(self):
        result = ensemble_prediction(
            lgb_pred=0.5, trie_mean=1.0, trie_n_obs=100,
            trie_min_obs=5, trie_weight=1.0,
        )
        assert result == pytest.approx(1.0)


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------

class TestPersistence:
    def test_save_load_roundtrip(self):
        trie = OnlineTrie(n_bins=3, half_life_hours=12.0, max_nodes=1000)
        trie.fit_bins(np.random.randn(50, 4))
        feat = np.array([0.1, -0.2, 0.5, 1.0])
        trie.predict_and_record(feat, ts=1000, symbol="X")
        trie.commit_outcome(ts=1000, outcome=0.5)

        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            path = f.name
        try:
            trie.save(path)
            loaded = OnlineTrie.load(path)
            assert loaded.n_bins == 3
            assert loaded.half_life_hours == 12.0
            assert loaded.max_nodes == 1000
            assert len(loaded.nodes) == 1
            mean, _std, n_obs, _eff = loaded.lookup_pattern(feat)
            assert n_obs == 1
            assert mean == pytest.approx(0.5)
            # Pending is NOT restored
            assert len(loaded.pending) == 0
        finally:
            os.unlink(path)


# ----------------------------------------------------------------------
# Integration test — mini online loop
# ----------------------------------------------------------------------

class TestIntegrationMiniLoop:
    def test_full_loop_predict_commit_lookup(self):
        """End-to-end: predict at t, commit at t+15m, lookup returns outcome."""
        trie = OnlineTrie(n_bins=4, half_life_hours=24.0)
        rng = np.random.RandomState(0)
        trie.fit_bins(rng.randn(200, 4))

        feat = np.array([0.5, -0.5, 1.0, -1.0])
        # Predict at t=0
        trie.predict_and_record(feat, ts=0, symbol="BTCUSDT")
        # 15 minutes later, commit outcome
        trie.commit_outcome(ts=0, outcome=0.3, current_ts=900)
        # Lookup the same pattern → should return mean=0.3, n_obs=1
        mean, std, n, eff = trie.lookup_pattern(feat)
        assert n == 1
        assert mean == pytest.approx(0.3)

        # Predict again on same pattern, commit again
        trie.predict_and_record(feat, ts=900, symbol="BTCUSDT")
        trie.commit_outcome(ts=900, outcome=0.5, current_ts=1800)
        mean, std, n, eff = trie.lookup_pattern(feat)
        assert n == 2
        # mean = (0.3 + 0.5) / 2 = 0.4 (approx; decay may shift slightly)
        assert 0.35 < mean < 0.45

    def test_stats_summary(self):
        trie = OnlineTrie(n_bins=3)
        trie.fit_bins(np.random.randn(50, 4))
        s = trie.stats()
        assert "n_nodes" in s
        assert "n_inserts_total" in s
        assert "n_pending" in s
        assert "obs_per_node_mean" in s
        assert s["n_nodes"] == 0  # nothing inserted yet


if __name__ == "__main__":
    # Run without pytest if invoked directly
    print("Running OnlineTrie tests...")
    test_classes = [
        TestTrieNodeOnline(),
        TestQuantization(),
        TestInsertAfterPredict(),
        TestTrieHygiene(),
        TestEnsemble(),
        TestPersistence(),
        TestIntegrationMiniLoop(),
    ]
    n_passed = 0
    n_failed = 0
    for cls in test_classes:
        for method_name in dir(cls):
            if method_name.startswith("test_"):
                try:
                    getattr(cls, method_name)()
                    print(f"  ✅ {cls.__class__.__name__}.{method_name}")
                    n_passed += 1
                except Exception as e:
                    print(f"  ❌ {cls.__class__.__name__}.{method_name}: {e}")
                    n_failed += 1
    print(f"\n{n_passed} passed, {n_failed} failed")
    sys.exit(1 if n_failed > 0 else 0)
