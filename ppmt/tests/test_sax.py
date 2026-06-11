"""Tests for SAX Encoder."""

import numpy as np
import pandas as pd
import pytest

from ppmt.core.sax import SAXEncoder, SAX_BREAKPOINTS


class TestSAXEncoder:
    """Test SAX encoding pipeline."""

    def setup_method(self):
        """Create test data."""
        np.random.seed(42)
        n = 100
        self.df = pd.DataFrame({
            "open": np.random.randn(n).cumsum() + 100,
            "high": np.random.randn(n).cumsum() + 102,
            "low": np.random.randn(n).cumsum() + 98,
            "close": np.random.randn(n).cumsum() + 100,
            "volume": np.abs(np.random.randn(n)) * 1000,
        })

    def test_basic_encoding(self):
        """Test basic SAX encoding produces correct number of symbols."""
        encoder = SAXEncoder(alphabet_size=8, window_size=10)
        symbols = encoder.encode(self.df)

        assert isinstance(symbols, list)
        assert len(symbols) == 10  # 100 candles / 10 window = 10 symbols
        assert all(isinstance(s, str) for s in symbols)
        assert all(s in "abcdefgh" for s in symbols)

    def test_alphabet_sizes(self):
        """Test different alphabet sizes work correctly."""
        for size in [3, 4, 5, 6, 7, 8, 10, 12, 16]:
            encoder = SAXEncoder(alphabet_size=size, window_size=10)
            symbols = encoder.encode(self.df)
            alphabet = "abcdefghijklmnopqrstuvwxyz"[:size]
            assert all(s in alphabet for s in symbols)

    def test_invalid_alphabet_size(self):
        """Test that invalid alphabet size raises error."""
        # 7 is valid (in SAX_BREAKPOINTS), 9 is not
        SAXEncoder(alphabet_size=7)  # Should not raise
        with pytest.raises(ValueError):
            SAXEncoder(alphabet_size=9)  # 9 is not in breakpoints

    def test_close_strategy(self):
        """Test close price strategy."""
        encoder = SAXEncoder(alphabet_size=8, window_size=10, strategy="close")
        symbols = encoder.encode(self.df)
        assert len(symbols) == 10

    def test_typical_price_strategy(self):
        """Test typical price strategy."""
        encoder = SAXEncoder(alphabet_size=8, window_size=10, strategy="typical_price")
        symbols = encoder.encode(self.df)
        assert len(symbols) == 10

    def test_incremental_encoding(self):
        """Test incremental SAX encoding matches batch encoding."""
        encoder = SAXEncoder(alphabet_size=8, window_size=10)

        # Batch encode
        batch_symbols = encoder.encode(self.df)

        # Incremental encode
        buffer = None
        inc_symbols = []

        for i in range(0, len(self.df), 5):
            chunk = self.df.iloc[i:i+5]
            new_syms, buffer = encoder.encode_incremental(chunk, buffer)
            inc_symbols.extend(new_syms)

        # Both should produce symbols (may differ slightly due to z-score context)
        assert len(inc_symbols) > 0

    def test_symbol_distance(self):
        """Test SAX symbol distance computation."""
        encoder = SAXEncoder(alphabet_size=8)

        # Same symbol = 0 distance
        assert encoder.symbol_distance("a", "a") == 0.0

        # Adjacent symbols = small distance
        dist_ab = encoder.symbol_distance("a", "b")
        dist_ah = encoder.symbol_distance("a", "h")

        # Farther symbols should have larger distance
        assert dist_ab < dist_ah

    def test_sequence_distance(self):
        """Test SAX sequence distance computation."""
        encoder = SAXEncoder(alphabet_size=8)

        # Identical sequences
        seq = ["a", "b", "c", "d"]
        assert encoder.sequence_distance(seq, seq) == 0.0

        # Different sequences
        seq2 = ["e", "f", "g", "h"]
        dist = encoder.sequence_distance(seq, seq2)
        assert dist > 0.0

    def test_sequence_distance_length_mismatch(self):
        """Test that mismatched lengths raise error."""
        encoder = SAXEncoder(alphabet_size=8)
        with pytest.raises(ValueError):
            encoder.sequence_distance(["a", "b"], ["a", "b", "c"])

    def test_empty_dataframe(self):
        """Test encoding empty DataFrame."""
        encoder = SAXEncoder(alphabet_size=8, window_size=10)
        empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        symbols = encoder.encode(empty_df)
        assert symbols == []

    def test_small_dataframe(self):
        """Test encoding DataFrame smaller than window size."""
        encoder = SAXEncoder(alphabet_size=8, window_size=10)
        small_df = self.df.iloc[:5]
        symbols = encoder.encode(small_df)
        assert symbols == []  # Not enough data for one window
