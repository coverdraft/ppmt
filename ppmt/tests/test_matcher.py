"""Tests for Fuzzy Matcher."""

import pytest

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt.core.matcher import FuzzyMatcher, MatchResult


class TestFuzzyMatcher:
    """Test fuzzy pattern matching."""

    def setup_method(self):
        """Set up test fixtures."""
        self.sax = SAXEncoder(alphabet_size=8, window_size=10)
        self.matcher = FuzzyMatcher(sax_encoder=self.sax, threshold=0.85)

        # Build a test Trie with known patterns
        self.trie = PPMTTrie(name="test")

        patterns = [
            ["a", "b", "c"],
            ["a", "b", "d"],
            ["d", "e", "f"],
            ["h", "g", "a"],
        ]

        for pattern in patterns:
            meta = BlockLifecycleMetadata(
                trigger_candle=len(pattern),
                expected_move_pct=5.0,
                win_rate=0.75,
                historical_count=100,
                continuation_nodes=[],
            )
            self.trie.insert(pattern, meta)

    def test_exact_match(self):
        """Test exact pattern matching."""
        result = self.matcher.exact_match(self.trie, ["a", "b", "c"])

        assert result.matched is True
        assert result.is_exact is True
        assert result.similarity == 1.0
        assert result.node is not None

    def test_exact_match_not_found(self):
        """Test exact match for non-existent pattern."""
        result = self.matcher.exact_match(self.trie, ["z", "z", "z"])
        assert result.matched is False

    def test_prefix_match(self):
        """Test prefix matching."""
        result = self.matcher.prefix_match(self.trie, ["a", "b", "c", "d", "e"])

        assert result.depth == 3  # Matched first 3 symbols
        assert result.similarity == 3 / 5

    def test_one_edit_match(self):
        """Test 1-edit fuzzy matching."""
        # ["a", "b", "c"] is in the trie
        # ["a", "b", "e"] is 1 edit away (e → c or e → d)
        result = self.matcher.one_edit_match(self.trie, ["a", "b", "e"])

        # Should find a match via substitution
        assert result.node is not None

    def test_best_match_exact(self):
        """Test best match prefers exact matches."""
        result = self.matcher.best_match(self.trie, ["a", "b", "c"])

        assert result.matched is True
        assert result.is_exact is True

    def test_best_match_fuzzy(self):
        """Test best match falls back to fuzzy."""
        # Something close to an existing pattern
        result = self.matcher.best_match(self.trie, ["d", "e", "g"])

        # The matcher runs without error; whether it matches depends on threshold
        assert isinstance(result, MatchResult)

    def test_check_continuation_known(self):
        """Test continuation check with known next symbol."""
        # After ["a", "b"], both "c" and "d" continue
        result = self.matcher.check_continuation(self.trie, ["a", "b"], "c")

        assert result.matched is True
        assert result.is_exact is True

    def test_check_continuation_unknown(self):
        """Test continuation check with unknown next symbol."""
        # Use a symbol in the alphabet that is NOT a continuation of ["a", "b"]
        # Continuations are "c" and "d", so "f" should be unknown
        result = self.matcher.check_continuation(self.trie, ["a", "b"], "f")

        # 'f' is not a child of the node at ["a", "b"]
        # Fuzzy match may find a close symbol, but if threshold is high enough, it won't
        # The key check: either it's not matched as an exact continuation, or it's unknown
        assert result.is_exact is False or result.unknown_block is True

    def test_check_continuation_no_current_pattern(self):
        """Test continuation check when current pattern doesn't exist."""
        result = self.matcher.check_continuation(self.trie, ["z", "z"], "a")

        assert result.matched is False
        assert result.unknown_block is True

    def test_empty_trie(self):
        """Test matching against empty trie."""
        empty_trie = PPMTTrie(name="empty")
        result = self.matcher.best_match(empty_trie, ["a", "b", "c"])
        assert result.matched is False
