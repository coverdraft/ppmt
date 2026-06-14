"""
Fuzzy Matching Engine

Provides noise-tolerant pattern matching for the PPMT Trie.
Instead of requiring exact symbol-by-symbol matches, the fuzzy
matcher allows small deviations that represent market noise.

Methods:
  1. Exact match: O(k) — standard Trie lookup
  2. 1-edit match: O(k * a) — allows one symbol substitution
  3. 2-edit match: O(k^2 * a^2) — allows two symbol substitutions
  4. Best match: Returns highest-confidence match across all strategies
  5. Pattern break score: Graduated 0-1 score for continuation quality

v0.6.5 Changes (Fuzzy Pattern Break):
  - best_match() now evaluates ALL strategies and returns the true best
  - one_edit_match() scoring fixed (was comparing score vs similarity)
  - New: pattern_break_score() for graduated HOLD → TRAILING → EXIT
  - New: two_edit_match() for patterns differing in 2 positions
  - check_continuation() enhanced with pattern break scoring
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

    edit_distance: int = 0
    """Number of symbol substitutions needed (0 = exact, 1 = 1-edit, etc.)."""

    score: float = 0.0
    """Composite score: similarity × node confidence. Used for ranking."""

    pattern_break_score: float = 0.0
    """Graduated break score (0-1). 1.0 = perfect continuation, 0.0 = broken."""

    def __post_init__(self):
        if self.symbols is None:
            self.symbols = []


class FuzzyMatcher:
    """
    Fuzzy pattern matcher for PPMT Trie.

    Supports multiple matching strategies with increasing computational
    cost, from exact O(k) to 2-edit O(k^2 * a^2).

    v0.6.5: best_match() now evaluates all strategies and returns the
    true best match (highest score), not the first passing waterfall.

    Usage:
        matcher = FuzzyMatcher(sax_encoder, threshold=0.85)

        # Find best match across all strategies
        result = matcher.best_match(trie, ['a', 'd', 'b'])

        # Check continuation with graduated break score
        result = matcher.check_continuation(trie, ['a', 'd', 'b'], 'e')

        # Get pattern break score for exit decisions
        score = matcher.pattern_break_score(trie, ['a', 'd', 'b'], 'e')
    """

    def __init__(
        self,
        sax_encoder: SAXEncoder,
        threshold: float = 0.85,
        max_edit_distance: int = 2,
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
            conf = node.metadata.confidence if node.metadata else 0.0
            return MatchResult(
                matched=True,
                node=node,
                symbols=symbols,
                similarity=1.0,
                depth=len(symbols),
                is_exact=True,
                edit_distance=0,
                score=1.0 * conf,
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
        conf = node.metadata.confidence if node.metadata else 0.0
        score = similarity * conf

        return MatchResult(
            matched=similarity >= self.threshold,
            node=node,
            symbols=matched_symbols,
            similarity=similarity,
            depth=depth,
            is_exact=(depth == total),
            edit_distance=0,
            score=score,
        )

    def one_edit_match(self, trie: PPMTTrie, symbols: list[str]) -> MatchResult:
        """
        O(k * a) match allowing one symbol substitution.

        For each position in the sequence, tries all possible
        symbol substitutions and returns the best match found.

        v0.6.5 FIX: Now properly compares score against score
        (was comparing score against similarity, causing incorrect ranking).
        """
        # First try exact match
        exact = self.exact_match(trie, symbols)
        if exact.matched:
            return exact

        best_score = 0.0
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

                    # FIX: Compare score against best_score (not against similarity)
                    if score > best_score:
                        best_score = score
                        best_result = MatchResult(
                            matched=score >= self.threshold,
                            node=node,
                            symbols=candidate,
                            similarity=similarity,
                            depth=len(candidate),
                            is_exact=False,
                            edit_distance=1,
                            score=score,
                        )

        return best_result

    def two_edit_match(self, trie: PPMTTrie, symbols: list[str]) -> MatchResult:
        """
        O(k^2 * a^2) match allowing two symbol substitutions.

        Only used when max_edit_distance >= 2 and 1-edit match fails.
        For short patterns (k < 5), this is fast enough for real-time.
        For longer patterns, it's gated behind best_match() which tries
        cheaper strategies first.

        Returns the best 2-edit match if score >= threshold.
        """
        if len(symbols) < 3:
            # Too short for 2-edit to be meaningful
            return MatchResult(matched=False, symbols=symbols)

        best_score = 0.0
        best_result = MatchResult(matched=False, symbols=symbols)
        alphabet = [chr(ord('a') + i) for i in range(self.sax.alphabet_size)]

        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                orig_i = symbols[i]
                orig_j = symbols[j]

                for repl_i in alphabet:
                    if repl_i == orig_i:
                        continue
                    for repl_j in alphabet:
                        if repl_j == orig_j:
                            continue

                        candidate = symbols.copy()
                        candidate[i] = repl_i
                        candidate[j] = repl_j

                        node = trie.search(candidate)
                        if node is not None and node.metadata.historical_count > 0:
                            # Combined similarity from both edits
                            dist_i = self.sax.symbol_distance(orig_i, repl_i)
                            dist_j = self.sax.symbol_distance(orig_j, repl_j)
                            max_dist = self.sax.breakpoints[-1] * 2 if len(self.sax.breakpoints) > 0 else 1.0
                            sim_i = max(0.0, 1.0 - dist_i / max(max_dist, 1e-10))
                            sim_j = max(0.0, 1.0 - dist_j / max(max_dist, 1e-10))

                            # Combined similarity: weighted average
                            # Each edit reduces similarity; more edits = lower score
                            similarity = (sim_i + sim_j) / 2.0 * 0.9  # 10% penalty for 2 edits

                            confidence = node.metadata.confidence
                            score = similarity * confidence

                            if score > best_score:
                                best_score = score
                                best_result = MatchResult(
                                    matched=score >= self.threshold * 0.9,  # Slightly lower threshold for 2-edit
                                    node=node,
                                    symbols=candidate,
                                    similarity=similarity,
                                    depth=len(candidate),
                                    is_exact=False,
                                    edit_distance=2,
                                    score=score,
                                )

        return best_result

    def best_match(self, trie: PPMTTrie, symbols: list[str]) -> MatchResult:
        """
        Find the best match using ALL matching strategies.

        v0.6.5 CHANGE: Instead of returning the first passing result
        (waterfall), we now evaluate all strategies and return the one
        with the highest score. This ensures we never miss a better match.

        Strategy priority (by computational cost):
        1. Exact match — O(k) — always try first
        2. 1-edit match — O(k * a)
        3. Prefix match — O(k) — partial matches
        4. 2-edit match — O(k^2 * a^2) — only if enabled

        Returns the MatchResult with the highest score.
        """
        # Step 1: Exact match — if found, it's always the best
        exact = self.exact_match(trie, symbols)
        if exact.matched:
            return exact

        # Step 2: Collect candidates from all strategies
        candidates = []

        # 1-edit match
        one_edit = self.one_edit_match(trie, symbols)
        if one_edit.matched:
            candidates.append(one_edit)

        # Prefix match (partial)
        prefix = self.prefix_match(trie, symbols)
        if prefix.matched:
            candidates.append(prefix)

        # 2-edit match (only if enabled and no good candidates yet)
        if self.max_edit_distance >= 2:
            # Only try 2-edit if 1-edit didn't find a strong match
            best_one_edit_score = max((c.score for c in candidates), default=0.0)
            if best_one_edit_score < self.threshold:
                two_edit = self.two_edit_match(trie, symbols)
                if two_edit.matched:
                    candidates.append(two_edit)

        # Step 3: Return the best-scoring candidate
        if candidates:
            best = max(candidates, key=lambda c: c.score)
            return best

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
        4. If no → check fuzzy continuation
        5. If no fuzzy → Unknown Block → consider predictive exit

        v0.6.5: Now computes pattern_break_score for graduated exits.
        """
        # Find current pattern
        current_node = trie.search(current_pattern)
        if current_node is None:
            # Current pattern not in Trie — can't determine continuation
            return MatchResult(
                matched=False,
                symbols=current_pattern,
                unknown_block=True,
                pattern_break_score=0.0,
            )

        # Exact continuation check
        continues, next_node = trie.check_continuation(current_pattern, next_symbol)
        if continues and next_node is not None:
            conf = next_node.metadata.confidence if next_node.metadata else 0.0
            return MatchResult(
                matched=True,
                node=next_node,
                symbols=current_pattern + [next_symbol],
                similarity=1.0,
                depth=len(current_pattern) + 1,
                is_exact=True,
                edit_distance=0,
                score=1.0 * conf,
                pattern_break_score=1.0,  # Perfect continuation
            )

        # Fuzzy continuation: check if a similar symbol exists as child
        continuation_symbols = current_node.get_continuation_symbols()
        if continuation_symbols:
            best_sim = 0.0
            best_sym = None
            best_conf = 0.0

            for cont_sym in continuation_symbols:
                dist = self.sax.symbol_distance(next_symbol, cont_sym)
                max_dist = self.sax.breakpoints[-1] * 2 if len(self.sax.breakpoints) > 0 else 1.0
                sim = max(0.0, 1.0 - dist / max(max_dist, 1e-10))

                if sim > best_sim:
                    best_sim = sim
                    best_sym = cont_sym
                    cont_child = current_node.get_child(cont_sym)
                    best_conf = cont_child.metadata.confidence if cont_child and cont_child.metadata else 0.0

            if best_sim >= self.threshold and best_sym is not None:
                fuzzy_node = current_node.get_child(best_sym)
                score = best_sim * best_conf
                return MatchResult(
                    matched=True,
                    node=fuzzy_node,
                    symbols=current_pattern + [best_sym],
                    similarity=best_sim,
                    depth=len(current_pattern) + 1,
                    is_exact=False,
                    edit_distance=1,
                    score=score,
                    pattern_break_score=best_sim,  # Fuzzy continuation
                )

        # Unknown block — pattern has no known continuation
        # Compute break score: how far is next_symbol from any continuation?
        break_score = 0.0
        if continuation_symbols:
            # Find the closest continuation symbol even if below threshold
            closest_dist = float('inf')
            for cont_sym in continuation_symbols:
                dist = self.sax.symbol_distance(next_symbol, cont_sym)
                if dist < closest_dist:
                    closest_dist = dist
            max_dist = self.sax.breakpoints[-1] * 2 if len(self.sax.breakpoints) > 0 else 1.0
            break_score = max(0.0, 1.0 - closest_dist / max(max_dist, 1e-10))
            # Scale down: unknown block = low break score
            break_score *= 0.3

        return MatchResult(
            matched=False,
            node=current_node,
            symbols=current_pattern + [next_symbol],
            similarity=0.0,
            depth=len(current_pattern),
            unknown_block=True,
            pattern_break_score=break_score,
        )

    def pattern_break_score(
        self,
        trie: PPMTTrie,
        current_pattern: list[str],
        next_symbol: str,
    ) -> float:
        """
        Compute a graduated pattern break score (0-1).

        This is the key method for Fuzzy Pattern Break — instead of
        a binary HOLD/EXIT decision, it returns a continuous score:

          1.0  = Exact continuation (perfect match)
          0.8  = Fuzzy continuation (1-edit, close symbol)
          0.5  = Weak fuzzy continuation (far symbol)
          0.2  = Close miss (similar to some continuation)
          0.0  = Complete break (no similar continuation exists)

        The PaperTrader uses this score to decide:
          - >= 0.7: HOLD (pattern continues)
          - 0.4-0.7: TRAILING (pattern weakening, protect profits)
          - < 0.4: EXIT (pattern broken)
        """
        result = self.check_continuation(trie, current_pattern, next_symbol)
        return result.pattern_break_score
