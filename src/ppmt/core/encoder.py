"""
Delta Encoder for Trie Compression

Compresses SAX symbol sequences using delta encoding:
  - Store first symbol as-is
  - Store subsequent symbols as differences from previous

Example:
  Original:  ['a', 'd', 'b', 'h', 'e']
  Delta:     ['a', +3, -2, +6, -3]

This reduces the alphabet space for deltas and improves
fuzzy matching efficiency since small deltas are more
common than absolute symbols in similar patterns.
"""

from __future__ import annotations

SAX_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


class DeltaEncoder:
    """
    Delta encoder for SAX symbol sequences.

    Converts absolute SAX sequences into delta-encoded form,
    where each symbol is represented as the difference from
    the previous symbol. This achieves:
    1. Compression: Delta values have smaller range than absolute
    2. Noise tolerance: Similar patterns have similar deltas
    3. Faster fuzzy matching: Compare deltas instead of absolute symbols
    """

    def __init__(self, alphabet_size: int = 8):
        self.alphabet_size = alphabet_size
        self._symbol_to_idx = {c: i for i, c in enumerate(SAX_ALPHABET[:alphabet_size])}
        self._idx_to_symbol = {i: c for i, c in enumerate(SAX_ALPHABET[:alphabet_size])}

    def encode(self, symbols: list[str]) -> list[int]:
        """
        Encode a SAX symbol sequence into delta form.

        Args:
            symbols: List of SAX symbols (e.g., ['a', 'd', 'b', 'h'])

        Returns:
            List of delta values. First element is the index of the
            first symbol, rest are differences.
            Example: [0, 3, -2, 6]
        """
        if not symbols:
            return []

        deltas = [self._symbol_to_idx[symbols[0]]]
        for i in range(1, len(symbols)):
            prev = self._symbol_to_idx[symbols[i - 1]]
            curr = self._symbol_to_idx[symbols[i]]
            deltas.append(curr - prev)

        return deltas

    def decode(self, deltas: list[int]) -> list[str]:
        """
        Decode a delta sequence back to SAX symbols.

        Args:
            deltas: Delta-encoded sequence

        Returns:
            Original SAX symbol sequence
        """
        if not deltas:
            return []

        symbols = [self._idx_to_symbol[deltas[0]]]
        for i in range(1, len(deltas)):
            idx = self._symbol_to_idx[symbols[-1]] + deltas[i]
            # Wrap around alphabet
            idx = idx % self.alphabet_size
            symbols.append(self._idx_to_symbol[idx])

        return symbols

    def delta_distance(self, delta_a: list[int], delta_b: list[int]) -> float:
        """
        Compute distance between two delta-encoded sequences.

        This is more noise-tolerant than absolute symbol comparison
        because patterns with the same shape (but different base levels)
        will have similar deltas.

        Args:
            delta_a: First delta sequence
            delta_b: Second delta sequence

        Returns:
            Normalized distance (0 = identical, higher = more different)
        """
        if len(delta_a) != len(delta_b):
            raise ValueError("Sequences must be equal length")

        if len(delta_a) <= 1:
            return 0.0 if delta_a == delta_b else 1.0

        # Compare deltas (skip first element which is absolute)
        total = sum(abs(a - b) for a, b in zip(delta_a[1:], delta_b[1:]))
        max_diff = self.alphabet_size - 1  # Maximum possible delta difference
        return total / (len(delta_a) - 1) / max_diff

    def compress_trie_path(self, symbols: list[str]) -> str:
        """
        Compress a SAX sequence into a compact string for storage keys.

        Example: ['a', 'd', 'b', 'h'] → 'a3-2+6'
        This is more compact than the raw sequence for long patterns.
        """
        if not symbols:
            return ""

        deltas = self.encode(symbols)
        parts = [symbols[0]]
        for d in deltas[1:]:
            if d >= 0:
                parts.append(f"+{d}")
            else:
                parts.append(str(d))

        return "".join(parts)

    @staticmethod
    def estimate_compression_ratio(alphabet_size: int, sequence_length: int) -> float:
        """
        Estimate the compression ratio for delta encoding.

        Delta encoding is most effective when:
        - Sequence length is long
        - Alphabet size is large
        - Consecutive symbols are similar (common in price patterns)

        Returns:
            Estimated ratio (compressed/original size)
        """
        # Absolute: each symbol needs log2(alphabet_size) bits
        bits_absolute = sequence_length * np.ceil(np.log2(alphabet_size))

        # Delta: first symbol same, rest need fewer bits
        # (deltas cluster around 0, so entropy is lower)
        # Empirically, deltas need about 60% of the bits
        bits_delta = np.ceil(np.log2(alphabet_size)) + (sequence_length - 1) * np.ceil(np.log2(alphabet_size)) * 0.6

        return bits_delta / bits_absolute


# Need numpy for estimate_compression_ratio
import numpy as np
