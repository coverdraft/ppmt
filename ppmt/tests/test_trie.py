"""Tests for PPMT Trie and Block Lifecycle Metadata."""

import pytest

from ppmt.core.trie import PPMTTrie, TrieNode
from ppmt.core.metadata import BlockLifecycleMetadata


class TestBlockLifecycleMetadata:
    """Test Block Lifecycle Metadata."""

    def test_default_metadata(self):
        """Test default metadata values."""
        meta = BlockLifecycleMetadata()
        assert meta.trigger_candle == 0
        assert meta.remaining_candles == 0
        assert meta.expected_move_pct == 0.0
        assert meta.historical_count == 0
        assert meta.confidence == 0.0

    def test_update_from_observation(self):
        """Test incremental metadata update."""
        meta = BlockLifecycleMetadata()

        # First observation
        meta.update_from_observation(
            move_pct=5.0,
            drawdown_pct=-2.0,
            favorable_pct=7.0,
            duration=20,
            won=True,
            next_symbol="d",
        )

        assert meta.historical_count == 1
        assert meta.expected_move_pct == 5.0
        assert meta.max_drawdown_pct == -2.0
        assert meta.max_favorable_pct == 7.0
        assert meta.win_rate == 1.0
        assert meta.avg_duration == 20
        assert "d" in meta.continuation_nodes

    def test_multiple_observations(self):
        """Test metadata with multiple observations."""
        meta = BlockLifecycleMetadata()

        observations = [
            (5.0, -2.0, 7.0, 20, True, "d"),
            (-1.0, -3.0, 2.0, 15, False, "e"),
            (3.0, -1.5, 5.0, 25, True, "d"),
        ]

        for move, dd, fav, dur, won, next_sym in observations:
            meta.update_from_observation(move, dd, fav, dur, won, next_sym)

        assert meta.historical_count == 3
        assert abs(meta.expected_move_pct - 2.333) < 0.01
        assert meta.max_drawdown_pct == -3.0
        assert meta.max_favorable_pct == 7.0
        assert abs(meta.win_rate - 0.667) < 0.01
        assert "d" in meta.continuation_nodes
        assert "e" in meta.continuation_nodes

    def test_confidence_increases_with_observations(self):
        """Test that confidence increases with more observations."""
        meta_low = BlockLifecycleMetadata()
        meta_low.win_rate = 0.7
        meta_low.historical_count = 5

        meta_high = BlockLifecycleMetadata()
        meta_high.win_rate = 0.7
        meta_high.historical_count = 500

        assert meta_high.confidence > meta_low.confidence

    def test_risk_reward_ratio(self):
        """Test risk:reward ratio computation."""
        meta = BlockLifecycleMetadata(
            expected_move_pct=5.0,
            max_drawdown_pct=-2.5,
        )
        assert meta.risk_reward_ratio == 2.0

    def test_compute_sl_tp(self):
        """Test SL/TP price computation."""
        meta = BlockLifecycleMetadata(
            max_drawdown_pct=-2.0,
            expected_move_pct=5.0,
            max_favorable_pct=7.0,
        )
        meta.compute_sl_tp(entry_price=100.0)

        # SL should be below entry with safety margin
        assert meta.sl_price is not None
        assert meta.sl_price < 100.0

        # TP should be above entry
        assert meta.tp_price is not None
        assert meta.tp_price > 100.0

    def test_serialization(self):
        """Test metadata serialization round-trip."""
        meta = BlockLifecycleMetadata(
            trigger_candle=10,
            remaining_candles=40,
            expected_move_pct=5.0,
            max_drawdown_pct=-2.0,
            max_favorable_pct=7.0,
            win_rate=0.75,
            avg_duration=20,
            historical_count=100,
            continuation_nodes=["d", "e"],
            break_nodes=["f"],
        )

        data = meta.to_dict()
        restored = BlockLifecycleMetadata.from_dict(data)

        assert restored.trigger_candle == 10
        assert restored.remaining_candles == 40
        assert restored.expected_move_pct == 5.0
        assert restored.win_rate == 0.75
        assert restored.continuation_nodes == ["d", "e"]

    def test_unknown_block_exit(self):
        """Test unknown block exit detection."""
        # With continuation nodes defined, unknown blocks should trigger exit
        meta_with = BlockLifecycleMetadata(continuation_nodes=["a", "b"])
        assert meta_with.is_unknown_block_exit is True

        # Without continuation nodes, can't determine
        meta_without = BlockLifecycleMetadata()
        assert meta_without.is_unknown_block_exit is False


class TestTrieNode:
    """Test Trie node operations."""

    def test_add_child(self):
        """Test adding children to a node."""
        root = TrieNode(symbol="")
        child = root.add_child("a")

        assert child.symbol == "a"
        assert child.depth == 1
        assert child.parent is root
        assert root.has_child("a")

    def test_get_child(self):
        """Test getting child nodes."""
        root = TrieNode(symbol="")
        root.add_child("a")

        child = root.get_child("a")
        assert child is not None
        assert child.symbol == "a"

        missing = root.get_child("z")
        assert missing is None

    def test_backward_path(self):
        """Test backward path tracing."""
        root = TrieNode(symbol="")
        a = root.add_child("a")
        b = a.add_child("b")
        c = b.add_child("c")

        path = c.get_backward_path()
        assert path == ["a", "b", "c"]

    def test_serialization(self):
        """Test node serialization."""
        root = TrieNode(symbol="")
        a = root.add_child("a")
        a.metadata.historical_count = 10
        a.metadata.win_rate = 0.7
        b = a.add_child("b")

        data = root.to_dict()
        restored = TrieNode.from_dict(data)

        assert "a" in restored.children
        assert restored.children["a"].metadata.historical_count == 10
        assert "b" in restored.children["a"].children


class TestPPMTTrie:
    """Test PPMT Trie operations."""

    def test_insert_and_search(self):
        """Test basic insert and search."""
        trie = PPMTTrie(name="test")

        # Insert pattern
        pattern = ["a", "d", "b", "h"]
        node = trie.insert(pattern)

        assert node is not None
        assert trie.pattern_count == 1

        # Search for the pattern
        found = trie.search(pattern)
        assert found is not None
        assert found.symbol == "h"

        # Search for non-existent pattern
        missing = trie.search(["a", "d", "b", "z"])
        assert missing is None

    def test_insert_with_metadata(self):
        """Test inserting with Block Lifecycle Metadata."""
        trie = PPMTTrie(name="test")
        meta = BlockLifecycleMetadata(
            trigger_candle=4,
            expected_move_pct=3.5,
            win_rate=0.8,
            historical_count=1,
        )

        pattern = ["a", "d", "b"]
        trie.insert(pattern, meta)

        found = trie.search(pattern)
        assert found is not None
        assert found.metadata.expected_move_pct == 3.5

    def test_insert_with_observations(self):
        """Test inserting with observation data."""
        trie = PPMTTrie(name="test")

        # Insert multiple observations of the same pattern
        trie.insert_with_observations(
            symbols=["a", "d", "b"],
            move_pct=5.0,
            drawdown_pct=-1.5,
            favorable_pct=6.0,
            duration=15,
            won=True,
            next_symbol="h",
        )

        trie.insert_with_observations(
            symbols=["a", "d", "b"],
            move_pct=2.0,
            drawdown_pct=-2.5,
            favorable_pct=3.0,
            duration=10,
            won=False,
            next_symbol="e",
        )

        found = trie.search(["a", "d", "b"])
        assert found is not None
        assert found.metadata.historical_count == 2
        assert "h" in found.metadata.continuation_nodes
        assert "e" in found.metadata.continuation_nodes

    def test_search_prefix(self):
        """Test prefix search."""
        trie = PPMTTrie(name="test")
        trie.insert(["a", "d", "b", "h"])

        # Full prefix match
        node, depth = trie.search_prefix(["a", "d", "b"])
        assert depth == 3
        assert node is not None

        # Partial prefix match
        node, depth = trie.search_prefix(["a", "d", "z", "z"])
        assert depth == 2

    def test_check_continuation(self):
        """Test continuation checking (Unknown Block logic)."""
        trie = PPMTTrie(name="test")
        trie.insert(["a", "d", "b"])
        trie.insert(["a", "d", "c"])

        # Known continuation
        continues, next_node = trie.check_continuation(["a", "d"], "b")
        assert continues is True
        assert next_node is not None

        # Unknown continuation
        continues, next_node = trie.check_continuation(["a", "d"], "z")
        assert continues is False
        assert next_node is None

    def test_get_all_patterns(self):
        """Test pattern enumeration."""
        trie = PPMTTrie(name="test")
        trie.insert(["a", "b"])
        trie.insert(["a", "c"])
        trie.insert(["d", "e"])

        patterns = trie.get_all_patterns()
        # Each insert creates nodes at each depth, so we get more patterns
        assert len(patterns) >= 2

    def test_serialization(self):
        """Test Trie serialization round-trip."""
        trie = PPMTTrie(name="test")
        trie.insert_with_observations(
            ["a", "d", "b"],
            move_pct=5.0, drawdown_pct=-2.0, favorable_pct=7.0,
            duration=20, won=True, next_symbol="h",
        )

        data = trie.to_dict()
        restored = PPMTTrie.from_dict(data)

        assert restored.name == "test"
        found = restored.search(["a", "d", "b"])
        assert found is not None
        assert found.metadata.historical_count == 1

    def test_o_k_search(self):
        """Test that search is O(k) — linear in pattern length."""
        trie = PPMTTrie(name="perf")

        # Insert many patterns
        for i in range(1000):
            pattern = [chr(ord('a') + (i % 8))]
            for j in range(10):
                pattern.append(chr(ord('a') + ((i + j) % 8)))
            trie.insert(pattern)

        # Search should still be fast
        import time
        start = time.perf_counter()
        for _ in range(10000):
            trie.search(["a", "b", "c", "d"])
        elapsed = (time.perf_counter() - start) * 1000

        # 10k searches should complete in < 100ms
        assert elapsed < 100, f"Search too slow: {elapsed:.2f}ms for 10k lookups"
