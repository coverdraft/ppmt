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

    FASE 1 Tarea 1.1: Each trie level now uses its own FuzzyMatcher
    with the level's SAXEncoder. This ensures:
      - alphabet_size matches the trie's symbol space (α=3 for N1, etc.)
      - symbol_distance() uses the correct breakpoints for the level
      - one_edit_match / two_edit_match enumerate the right alphabet

    Usage:
        # Per-level matchers (FASE 1 Tarea 1.1)
        matcher_n1 = FuzzyMatcher(sax_encoder_n1, threshold=0.85)  # α=3
        matcher_n3 = FuzzyMatcher(sax_encoder_n3, threshold=0.85)  # α=5

        # Search N1 with N1's encoder → alphabet = {a, b, c}
        result_n1 = matcher_n1.best_match(trie_n1, n1_symbols)

        # Search N3 with N3's encoder → alphabet = {a, b, c, d, e}
        result_n3 = matcher_n3.best_match(trie_n3, n3_symbols)

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
        min_similarity: float = 0.70,
        min_confidence: float = 0.15,
    ):
        """
        v0.40.1 FIX-2: Separar similarity y confidence thresholds.

        Antes: matched = (similarity × confidence) >= threshold
          - Con confidence en 0.08-0.20 (CAPA 1), 1-edit/2-edit eran
            estructuralmente inalcanzables (necesitaban sim >= 4.25).
          - 1-edit y 2-edit eran DEAD CODE en la práctica.

        Ahora: matched = (similarity >= min_similarity) AND (confidence >= min_confidence)
          - 1-edit con similarity >= 0.70 se vuelve alcanzable.
          - Confidence >= 0.15 filtra nodos puramente decorativos.
          - `threshold` se preserva para backwards compat (composite score
            still computed, just no longer used as the match gate).
        """
        self.sax = sax_encoder
        self.threshold = threshold
        self.max_edit_distance = max_edit_distance
        self.min_similarity = min_similarity
        self.min_confidence = min_confidence

    def _passes_gate(self, similarity: float, confidence: float) -> bool:
        """
        v0.40.1 FIX-2: Combined match gate using SEPARATE thresholds
        for similarity and confidence instead of a composite score.

        Composite `similarity × confidence >= 0.85` was structurally
        unreachable for 1-edit and 2-edit matches when confidence
        was in the 0.08-0.20 range (typical for sparse tries).
        See docs/AUDIT_TRAZABILIDAD_CAPAS_1_2_3.md CAPA 2 #1.
        """
        return bool(similarity >= self.min_similarity and confidence >= self.min_confidence)

    def exact_match(self, trie: PPMTTrie, symbols: list[str]) -> MatchResult:
        """
        O(k) exact match. Fastest possible lookup.

        Returns MatchResult with is_exact=True if found, False otherwise.
        """
        node = trie.search(symbols)
        if node is not None:
            conf = node.metadata.confidence if node.metadata else 0.0
            # v0.40.1 FIX-2: separate gates. For exact match, similarity is 1.0
            # so the only filter is min_confidence. This still filters nodes
            # with metadata in the 0.00-0.14 dead zone (purely decorative).
            matched = self._passes_gate(similarity=1.0, confidence=conf)
            return MatchResult(
                matched=matched,
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

        # v0.40.1 FIX-2: separate gates (was: similarity >= self.threshold)
        matched = self._passes_gate(similarity=similarity, confidence=conf)

        return MatchResult(
            matched=matched,
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
                    # v0.40.1 FIX-2: gate uses separate thresholds, not composite score
                    if score > best_score:
                        best_score = score
                        best_result = MatchResult(
                            matched=self._passes_gate(similarity=similarity, confidence=confidence),
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
                                # v0.40.1 FIX-2: gate uses separate thresholds.
                                # Note: 2-edit threshold stays slightly relaxed
                                # (min_similarity * 0.9) since 2 substitutions
                                # inherently reduce similarity more.
                                matched = (
                                    similarity >= self.min_similarity * 0.9
                                    and confidence >= self.min_confidence
                                )
                                best_result = MatchResult(
                                    matched=matched,
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

        v0.40.7 FIX-10: best_match ahora RETORNA el mejor node encontrado
        aunque `matched=False`. Antes, si TODAS las leaves tenían confidence
        < min_confidence (0.15) — común en tries sparse de TF bajos — el
        método descartaba los nodes y devolvía `node=None`. Esto hacía que
        match() computara weighted_confidence=0.0 y jamás generara señales,
        aún cuando el patrón EXISTÍA en el trie.

        Ahora: el node se retorna junto con `matched` como flag soft.
        El engine usa `n3_match.node` para leer metadata.confidence y pasa
        el weighted_confidence al signal_generator, que aplica sus propios
        thresholds (per_trade_min_confidence=0.08).

        Strategy priority (by computational cost):
        1. Exact match — O(k) — always try first
        2. 1-edit match — O(k * a)
        3. Prefix match — O(k) — partial matches
        4. 2-edit match — O(k^2 * a^2) — only if enabled

        Returns the MatchResult with the highest score (or with node set
        even if matched=False, so downstream can still read metadata).
        """
        # Step 1: Exact match — if found, return it (matched may be False
        # if confidence is below gate, but node is still useful).
        exact = self.exact_match(trie, symbols)
        if exact.node is not None:
            return exact

        # Step 2: Collect candidates from all strategies. We collect any
        # candidate with `node is not None`, not just `matched=True`. The
        # `matched` flag becomes a SOFT signal that downstream code can
        # use to apply stricter filtering (e.g., signal_generator's
        # per_trade_min_confidence).
        candidates = []

        # 1-edit match
        one_edit = self.one_edit_match(trie, symbols)
        if one_edit.node is not None:
            candidates.append(one_edit)

        # Prefix match (partial)
        prefix = self.prefix_match(trie, symbols)
        if prefix.node is not None:
            candidates.append(prefix)

        # 2-edit match (only if enabled and no good candidates yet)
        if self.max_edit_distance >= 2:
            # Only try 2-edit if 1-edit didn't find a strong match
            best_one_edit_score = max((c.score for c in candidates), default=0.0)
            if best_one_edit_score < self.threshold:
                two_edit = self.two_edit_match(trie, symbols)
                if two_edit.node is not None:
                    candidates.append(two_edit)

        # Step 3: Return the best-scoring candidate (regardless of matched).
        # Downstream code reads node.metadata.confidence and applies its
        # own thresholds via signal_generator.
        if candidates:
            best = max(candidates, key=lambda c: c.score)
            return best

        # No node found at all — true unknown block.
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
                # v0.40.1 FIX-2: separate gates (was: best_sim >= self.threshold)
                matched = self._passes_gate(similarity=best_sim, confidence=best_conf)
                return MatchResult(
                    matched=matched,
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
