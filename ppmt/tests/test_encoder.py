"""Tests for Delta Encoder."""

import pytest

from ppmt.core.encoder import DeltaEncoder


class TestDeltaEncoder:
    """Test delta encoding for SAX sequences."""

    def test_basic_encoding(self):
        """Test basic delta encoding."""
        encoder = DeltaEncoder(alphabet_size=8)
        symbols = ["a", "d", "b", "h"]
        deltas = encoder.encode(symbols)

        # First element is index of first symbol
        assert deltas[0] == 0  # 'a' = index 0

        # Subsequent elements are differences
        assert deltas[1] == 3  # d - a = 3
        assert deltas[2] == -2  # b - d = -2
        assert deltas[3] == 6  # h - b = 6

    def test_decode_roundtrip(self):
        """Test encode/decode roundtrip."""
        encoder = DeltaEncoder(alphabet_size=8)
        original = ["a", "d", "b", "h", "e"]

        deltas = encoder.encode(original)
        decoded = encoder.decode(deltas)

        assert decoded == original

    def test_empty_sequence(self):
        """Test empty sequence handling."""
        encoder = DeltaEncoder(alphabet_size=8)
        assert encoder.encode([]) == []
        assert encoder.decode([]) == []

    def test_single_symbol(self):
        """Test single symbol encoding."""
        encoder = DeltaEncoder(alphabet_size=8)
        deltas = encoder.encode(["d"])
        decoded = encoder.decode(deltas)
        assert decoded == ["d"]

    def test_constant_sequence(self):
        """Test constant (flat) sequence — all deltas should be 0."""
        encoder = DeltaEncoder(alphabet_size=8)
        symbols = ["c", "c", "c", "c"]
        deltas = encoder.encode(symbols)

        assert deltas[0] == 2  # 'c' index
        assert all(d == 0 for d in deltas[1:])  # No change

    def test_delta_distance_identical(self):
        """Test delta distance for identical sequences."""
        encoder = DeltaEncoder(alphabet_size=8)
        deltas = encoder.encode(["a", "d", "b"])
        assert encoder.delta_distance(deltas, deltas) == 0.0

    def test_delta_distance_different(self):
        """Test delta distance for different sequences."""
        encoder = DeltaEncoder(alphabet_size=8)
        deltas_a = encoder.encode(["a", "b", "c"])
        deltas_b = encoder.encode(["a", "d", "e"])

        dist = encoder.delta_distance(deltas_a, deltas_b)
        assert dist > 0.0

    def test_compress_trie_path(self):
        """Test compact string compression."""
        encoder = DeltaEncoder(alphabet_size=8)
        compressed = encoder.compress_trie_path(["a", "d", "b", "h"])
        assert "a" in compressed
        assert isinstance(compressed, str)

    def test_different_alphabet_sizes(self):
        """Test encoding with different alphabet sizes."""
        for size in [4, 8, 16]:
            encoder = DeltaEncoder(alphabet_size=size)
            symbols = [chr(ord('a') + i % size) for i in range(5)]
            deltas = encoder.encode(symbols)
            decoded = encoder.decode(deltas)
            assert decoded == symbols
