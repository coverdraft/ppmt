"""
Fuzzy Matching Engine

Provides noise-tolerant pattern matching for the PPMT Trie.
Instead of requiring exact symbol-by-symbol matches, the fuzzy
matcher allows small deviations that represent market noise.

Methods:
  1. Exact match: O(k) — standard Trie lookup
  2. 1-edit match: O(k * a) — allows one symbol substitution
  3. Fuzzy match: O(k * a^d) — allows d deviations within threshold
  4. Best match: Returns highest-confidence match above threshold

The fuzzy_threshold from configuration controls how much deviation
is acceptable. Higher threshold = stricter matching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie, TrieNode


@dataclass
class MatchResult:
    """Result of a fuzzy match operation."""

    matched: bool
    """Whether a match was found above the confidence threshold."""

    node: Optional[TrieNode] = None
    """The best matching TrieNode (if any)."""

    symbols: list[str] = None  # type: ignore
    """The matched SAX sequence."""

    similarity: float = 0.0
    """Similarity score (0-1). 1.0 = exact match."""

    depth: int = 0
    """How deep the match went in the Trie."""

    is_exact: bool = False
    """Whether this was an exact match (no fuzzy needed)."""

    unknown_block: bool = False
    """Whether the pattern ended at an unknown block (no continuation)."""

    def __post_init__(self):
        if self.symbols is None:
            self.symbols = []


class FuzzyMatcher:
    """
    Fuzzy pattern matcher for PPMT Trie.

    Supports multiple matching strategies with increasing computational
    cost, from exact O(k) to fuzzy O(k * a^d) where a = alphabet size
    and d = allowed deviations.

    In practice, the 1-edit match is sufficient for most cases and
    remains very fast (sub-millisecond for k < 50).

    Usage:
        matcher = FuzzyMatcher(sax_encoder, threshold=0.85)

        # Check if a pattern exists exactly
        result = matcher.exact_match(trie, ['a', 'd', 'b'])

        # Find best fuzzy match
        result = matcher.best_match(trie, ['a', 'd', 'b'])

        # Check continuation for real-time trading
        result = matcher.check_continuation(trie, ['a', 'd', 'b'], 'e')
    """

    def __init__(
        self,
        sax_encoder: SAXEncoder,
        threshold: float = 0.85,
        max_edit_distance: int = 1,
    ):
        self.sax = sax_encoder
        self.threshold = threshold
        self.max_edit_distance = max_edit_distance

    def exact_match(self, trie: PPMTTrie, symbols: list[str]) -> MatchResult:
        """
        O(k) exact match. Fastest possible lookup.

        Returns MatchResult with is_exact=True if found, False otherwise.
        """
        node = trie.search(symbols)
        if node is not None:
            return MatchResult(
                matched=True,
                node=node,
                symbols=symbols,
                similarity=1.0,
                depth=len(symbols),
                is_exact=True,
            )
        return MatchResult(matched=False, symbols=symbols)

    def prefix_match(self, trie: PPMTTrie, symbols: list[str]) -> MatchResult:
        """
        Match the longest prefix of the symbol sequence.

        Useful for real-time matching where we're observing
        the pattern as it unfolds candle by candle.
        """
        node, depth = trie.search_prefix(symbols)
        if node is None:
            return MatchResult(matched=False, symbols=symbols)

        matched_symbols = symbols[:depth]
        total = len(symbols)
        similarity = depth / total if total > 0 else 0.0

        return MatchResult(
            matched=similarity >= self.threshold,
            node=node,
            symbols=matched_symbols,
            similarity=similarity,
            depth=depth,
            is_exact=(depth == total),
        )

    def one_edit_match(self, trie: PPMTTrie, symbols: list[str]) -> MatchResult:
        """
        O(k * a) match allowing one symbol substitution.

        For each position in the sequence, tries all possible
        symbol substitutions and returns the best match found.

        This is the recommended strategy for most use cases —
        it handles single-symbol noise without significant cost.
        """
        # First try exact match
        exact = self.exact_match(trie, symbols)
        if exact.matched:
            return exact

        best_result = MatchResult(matched=False, symbols=symbols)
        alphabet = [chr(ord('a') + i) for i in range(self.sax.alphabet_size)]

        for i in range(len(symbols)):
            original = symbols[i]
            for replacement in alphabet:
                if replacement == original:
                    continue

                # Try this substitution
                candidate = symbols.copy()
                candidate[i] = replacement

                node = trie.search(candidate)
                if node is not None and node.metadata.historical_count > 0:
                    # Compute similarity: penalize by SAX distance
                    symbol_dist = self.sax.symbol_distance(original, replacement)
                    max_dist = self.sax.breakpoints[-1] * 2 if len(self.sax.breakpoints) > 0 else 1.0
                    similarity = max(0.0, 1.0 - symbol_dist / max(max_dist, 1e-10))

                    confidence = node.metadata.confidence
                    score = similarity * confidence

                    if score > best_result.similarity:
                        best_result = MatchResult(
                            matched=score >= self.threshold,
                            node=node,
                            symbols=candidate,
                            similarity=similarity,
                            depth=len(candidate),
                            is_exact=False,
                        )

        return best_result

    def best_match(self, trie: PPMTTrie, symbols: list[str]) -> MatchResult:
        """
        Find the best match using progressive matching strategy.

        Tries in order:
        1. Exact match (O(k))
        2. Prefix match (O(k))
        3. 1-edit match (O(k * a))

        Returns the first good match found, with exact preferred.
        """
        # Step 1: Exact match
        exact = self.exact_match(trie, symbols)
        if exact.matched:
            return exact

        # Step 2: Prefix match (partial match)
        prefix = self.prefix_match(trie, symbols)
        if prefix.matched and prefix.similarity >= self.threshold:
            return prefix

        # Step 3: 1-edit fuzzy match
        fuzzy = self.one_edit_match(trie, symbols)
        if fuzzy.matched:
            return fuzzy

        # No good match found
        return MatchResult(matched=False, symbols=symbols, unknown_block=True)

    def check_continuation(
        self,
        trie: PPMTTrie,
        current_pattern: list[str],
        next_symbol: str,
    ) -> MatchResult:
        """
        Check if the next symbol continues an existing pattern.

        This implements the core PPMT real-time logic:
        1. Find the current pattern in the Trie
        2. Check if next_symbol exists as a child
        3. If yes → pattern continues → hold position
        4. If no → Unknown Block → consider predictive exit

        For fuzzy continuation, we also check if a similar symbol
        (within SAX distance threshold) exists as a child.
        """
        # Find current pattern
        current_node = trie.search(current_pattern)
        if current_node is None:
            # Current pattern not in Trie — can't determine continuation
            return MatchResult(
                matched=False,
                symbols=current_pattern,
                unknown_block=True,
            )

        # Exact continuation check
        continues, next_node = trie.check_continuation(current_pattern, next_symbol)
        if continues and next_node is not None:
            return MatchResult(
                matched=True,
                node=next_node,
                symbols=current_pattern + [next_symbol],
                similarity=1.0,
                depth=len(current_pattern) + 1,
                is_exact=True,
            )

        # Fuzzy continuation: check if a similar symbol exists as child
        continuation_symbols = current_node.get_continuation_symbols()
        if continuation_symbols:
            best_sim = 0.0
            best_sym = None

            for cont_sym in continuation_symbols:
                dist = self.sax.symbol_distance(next_symbol, cont_sym)
                max_dist = self.sax.breakpoints[-1] * 2 if len(self.sax.breakpoints) > 0 else 1.0
                sim = max(0.0, 1.0 - dist / max(max_dist, 1e-10))

                if sim > best_sim:
                    best_sim = sim
                    best_sym = cont_sym

            if best_sim >= self.threshold and best_sym is not None:
                fuzzy_node = current_node.get_child(best_sym)
                return MatchResult(
                    matched=True,
                    node=fuzzy_node,
                    symbols=current_pattern + [best_sym],
                    similarity=best_sim,
                    depth=len(current_pattern) + 1,
                    is_exact=False,
                )

        # Unknown block — pattern has no known continuation
        return MatchResult(
            matched=False,
            node=current_node,
            symbols=current_pattern + [next_symbol],
            similarity=0.0,
            depth=len(current_pattern),
            unknown_block=True,
        )
