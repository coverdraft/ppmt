"""
PPMT Trie Data Structure

The core Trie that stores SAX symbol sequences with Block Lifecycle Metadata.
Supports:
  - O(k) insertion and lookup (k = pattern length)
  - Block Lifecycle Metadata at every node
  - Forward/backward navigation
  - Unknown block detection (predictive exit)
  - Serialization for persistence
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ppmt.core.metadata import BlockLifecycleMetadata


@dataclass
class TrieNode:
    """
    A single node in the PPMT Trie.

    Each node represents one SAX symbol in a pattern sequence.
    The node carries Block Lifecycle Metadata that encodes:
    - When to enter (trigger_candle)
    - How long the pattern should last
    - Expected move, SL, TP
    - What patterns continue vs break

    Key insight: If a child for the next observed symbol does NOT exist,
    the pattern broke → Unknown Block = Predictive Exit signal.
    """

    symbol: str
    """The SAX symbol this node represents (a, b, c, ...)."""

    children: dict[str, TrieNode] = field(default_factory=dict)
    """Child nodes keyed by their SAX symbol.
    These represent the 'continuation_nodes' in metadata —
    known patterns that historically followed this block."""

    metadata: BlockLifecycleMetadata = field(default_factory=BlockLifecycleMetadata)
    """Block Lifecycle Metadata attached to this node.
    All trading decisions emerge from this metadata."""

    depth: int = 0
    """Depth of this node in the Trie (0 = root)."""

    parent: Optional[TrieNode] = field(default=None, repr=False)
    """Reference to parent node for backward traversal.
    Enables 'backward metadata' — what led to this pattern."""

    # === Navigation Methods ===

    def has_child(self, symbol: str) -> bool:
        """Check if a continuation symbol exists as a child."""
        return symbol in self.children

    def get_child(self, symbol: str) -> Optional[TrieNode]:
        """Get child node for a symbol, or None if unknown."""
        return self.children.get(symbol)

    def add_child(self, symbol: str) -> TrieNode:
        """Add a new child node and return it."""
        if symbol not in self.children:
            child = TrieNode(
                symbol=symbol,
                depth=self.depth + 1,
                parent=self,
            )
            self.children[symbol] = child
        return self.children[symbol]

    def get_continuation_symbols(self) -> list[str]:
        """Get all known continuation symbols from this node."""
        return list(self.children.keys())

    def get_backward_path(self) -> list[str]:
        """
        Trace path from root to this node.
        This represents the 'backward metadata' — what sequence led here.
        """
        path = []
        node: Optional[TrieNode] = self
        while node is not None and node.parent is not None:
            path.append(node.symbol)
            node = node.parent
        return list(reversed(path))

    # === Serialization ===

    def to_dict(self) -> dict:
        """Serialize this node and all descendants to a dictionary."""
        return {
            "symbol": self.symbol,
            "depth": self.depth,
            "metadata": self.metadata.to_dict(),
            "children": {
                sym: child.to_dict() for sym, child in self.children.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict, parent: Optional[TrieNode] = None) -> TrieNode:
        """Deserialize a node and all descendants from a dictionary."""
        node = cls(
            symbol=data["symbol"],
            depth=data.get("depth", 0),
            parent=parent,
            metadata=BlockLifecycleMetadata.from_dict(data.get("metadata", {})),
        )
        for sym, child_data in data.get("children", {}).items():
            child = TrieNode.from_dict(child_data, parent=node)
            node.children[sym] = child
        return node


class PPMTTrie:
    """
    Progressive Pattern Matching Trie.

    Stores SAX symbol sequences with Block Lifecycle Metadata.
    Each Trie instance represents one level of the 4-level architecture:
      - N1: Universal Trie
      - N2: Asset Class Trie
      - N3: Per-Asset Trie
      - N4: Per-Asset+Regime Trie

    Operations are O(k) where k is the pattern length,
    regardless of the total number of stored patterns.

    Usage:
        trie = PPMTTrie(name="BTC_USDT")

        # Insert a pattern with metadata
        pattern = ['a', 'd', 'b', 'h']
        trie.insert(pattern, metadata)

        # Search for a pattern
        node = trie.search(pattern)

        # Check if next symbol continues the pattern
        node = trie.search(pattern[:3])
        if node and node.has_child('h'):
            # Pattern continues → hold position
            pass
        else:
            # Unknown block → predictive exit
            pass
    """

    def __init__(self, name: str = "root"):
        self.name = name
        self.root = TrieNode(symbol="", depth=0)
        self._pattern_count = 0
        self._max_depth = 0
        self.trading_observations: int = 0
        """Number of trading-time observations recorded via Living Trie.
        Distinguishes fresh builds (0) from tries with accumulated
        trading metadata (>0). Used for adaptive confidence scaling."""

    @property
    def pattern_count(self) -> int:
        """Total number of unique patterns stored in this Trie."""
        return self._pattern_count

    @property
    def max_depth(self) -> int:
        """Maximum depth (longest pattern) in this Trie."""
        return self._max_depth

    def insert(
        self,
        symbols: list[str],
        metadata: Optional[BlockLifecycleMetadata] = None,
    ) -> TrieNode:
        """
        Insert a SAX symbol sequence into the Trie.

        If the sequence already exists, updates the metadata incrementally.
        If new, creates nodes along the path and attaches metadata to the
        terminal node.

        Args:
            symbols: List of SAX symbols forming the pattern
            metadata: Block Lifecycle Metadata to attach to the terminal node

        Returns:
            The terminal TrieNode of the inserted pattern
        """
        if not symbols:
            return self.root

        node = self.root
        is_new = False

        for i, symbol in enumerate(symbols):
            if not node.has_child(symbol):
                node = node.add_child(symbol)
                is_new = True
            else:
                node = node.get_child(symbol)

        # Update metadata on the terminal node
        if metadata is not None:
            if is_new or node.metadata.historical_count == 0:
                node.metadata = metadata
            else:
                # Merge: update continuation_nodes from metadata
                for sym in metadata.continuation_nodes:
                    if sym not in node.metadata.continuation_nodes:
                        node.metadata.continuation_nodes.append(sym)
                for sym in metadata.break_nodes:
                    if sym not in node.metadata.break_nodes:
                        node.metadata.break_nodes.append(sym)

        # Update parent's continuation_nodes
        if node.parent is not None:
            if node.symbol not in node.parent.metadata.continuation_nodes:
                node.parent.metadata.continuation_nodes.append(node.symbol)

        if is_new:
            self._pattern_count += 1

        self._max_depth = max(self._max_depth, len(symbols))

        return node

    def insert_with_observations(
        self,
        symbols: list[str],
        move_pct: float = 0.0,
        drawdown_pct: float = 0.0,
        favorable_pct: float = 0.0,
        duration: int = 0,
        won: bool = False,
        next_symbol: Optional[str] = None,
        regime: Optional[str] = None,
        regime_confidence: Optional[float] = None,
    ) -> TrieNode:
        """
        Insert a pattern and update metadata from a single observation.

        This is the primary method for building the Trie from historical data.
        Each call represents one observed instance of the pattern.

        V4: Now accepts regime and regime_confidence parameters to store
        the market regime under which this pattern was observed.

        Args:
            symbols: SAX symbol sequence
            move_pct: Observed percentage move
            drawdown_pct: Maximum drawdown observed
            favorable_pct: Maximum favorable excursion
            duration: Duration in candles
            won: Whether the pattern completed successfully
            next_symbol: What followed this pattern (for continuation tracking)
            regime: Market regime at observation time (V4)
            regime_confidence: Confidence of regime detection (V4)
        """
        node = self.insert(symbols)

        # Set trigger_candle on first observation
        if node.metadata.historical_count == 0:
            node.metadata.trigger_candle = len(symbols)  # Pattern fully formed

        # Update metadata from this observation
        node.metadata.update_from_observation(
            move_pct=move_pct,
            drawdown_pct=drawdown_pct,
            favorable_pct=favorable_pct,
            duration=duration,
            won=won,
            next_symbol=next_symbol,
            regime=regime,
            regime_confidence=regime_confidence,
        )

        return node

    def search(self, symbols: list[str]) -> Optional[TrieNode]:
        """
        Search for a pattern in the Trie.

        Returns the terminal node if found, None otherwise.
        Time complexity: O(k) where k = len(symbols)

        Args:
            symbols: SAX symbol sequence to search for
        """
        node = self.root
        for symbol in symbols:
            child = node.get_child(symbol)
            if child is None:
                return None
            node = child
        return node

    def search_prefix(self, symbols: list[str]) -> tuple[Optional[TrieNode], int]:
        """
        Search for the longest matching prefix in the Trie.

        Useful for real-time matching where we may not have
        observed the full pattern yet.

        Returns:
            Tuple of (deepest matching node, matched depth)
        """
        node = self.root
        matched = 0

        for symbol in symbols:
            child = node.get_child(symbol)
            if child is None:
                break
            node = child
            matched += 1

        if matched == 0:
            return None, 0
        return node, matched

    def check_continuation(
        self,
        pattern: list[str],
        next_symbol: str,
    ) -> tuple[bool, Optional[TrieNode]]:
        """
        Check if a next symbol continues an existing pattern.

        This is the core of the 'Unknown Block = Predictive Exit' logic:
        - If the symbol exists as a child → pattern continues → hold
        - If NOT → pattern may have broken → consider exit

        Args:
            pattern: Current observed SAX sequence
            next_symbol: The next observed SAX symbol

        Returns:
            Tuple of (continues: bool, next_node: Optional[TrieNode])
        """
        node = self.search(pattern)
        if node is None:
            return False, None

        child = node.get_child(next_symbol)
        if child is not None:
            return True, child
        else:
            return False, None

    def get_all_patterns(
        self,
        prefix: list[str] | None = None,
        min_count: int = 0,
    ) -> list[tuple[list[str], TrieNode]]:
        """
        Get all patterns in the Trie with optional filtering.

        Args:
            prefix: Only return patterns starting with this prefix
            min_count: Minimum historical_count to include

        Returns:
            List of (symbol_sequence, terminal_node) tuples
        """
        results = []

        if prefix:
            start_node = self.search(prefix)
            if start_node is None:
                return results
            start_path = prefix
        else:
            start_node = self.root
            start_path = []

        self._collect_patterns(start_node, start_path, results, min_count)
        return results

    def _collect_patterns(
        self,
        node: TrieNode,
        current_path: list[str],
        results: list[tuple[list[str], TrieNode]],
        min_count: int,
    ) -> None:
        """Recursively collect all patterns from a node."""
        if node.depth > 0 and node.metadata.historical_count >= min_count:
            results.append((current_path.copy(), node))

        for sym, child in node.children.items():
            current_path.append(sym)
            self._collect_patterns(child, current_path, results, min_count)
            current_path.pop()

    # === Serialization ===

    def to_dict(self) -> dict:
        """Serialize the entire Trie to a dictionary."""
        return {
            "name": self.name,
            "pattern_count": self._pattern_count,
            "max_depth": self._max_depth,
            "trading_observations": self.trading_observations,
            "root": self.root.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> PPMTTrie:
        """Deserialize a Trie from a dictionary."""
        trie = cls(name=data.get("name", "root"))
        trie._pattern_count = data.get("pattern_count", 0)
        trie._max_depth = data.get("max_depth", 0)
        trie.trading_observations = data.get("trading_observations", 0)

        # Reconstruct children from root
        root_data = data.get("root", {})
        for sym, child_data in root_data.get("children", {}).items():
            child = TrieNode.from_dict(child_data, parent=trie.root)
            trie.root.children[sym] = child

        return trie

    def propagate_metadata(self) -> None:
        """
        Propagate metadata from terminal nodes up to intermediate nodes.

        After building, only terminal nodes (at the full pattern length depth)
        have real metadata from observations. Intermediate nodes have
        historical_count=0 and default values.

        This method walks the Trie bottom-up and computes aggregate statistics
        for each intermediate node from its terminal descendants. This enables:
          1. PredictionEngine to find meaningful metadata at any depth
          2. Better confidence estimates from larger sample sizes
          3. Forward path walking from intermediate nodes

        Must be called after build() and before saving/loading.
        Idempotent: calling multiple times produces the same result.
        """
        self._propagate_node(self.root)

    def _propagate_node(self, node: TrieNode) -> BlockLifecycleMetadata:
        """
        Recursively propagate metadata from children to parent.

        Returns the aggregate metadata for this subtree (including all descendants).

        V4: Also propagates regime information and classifies nodes as
        independent or dependent based on their observation count.
        """
        if not node.children:
            # Leaf node — return its own metadata
            # V4: Classify leaf nodes
            if node.metadata.historical_count >= node.metadata.min_independent_count:
                node.metadata.node_type = "independent"
            else:
                node.metadata.node_type = "dependent"
            return node.metadata

        # Recursively propagate to children first (bottom-up)
        child_metas = []
        for sym, child in node.children.items():
            child_meta = self._propagate_node(child)
            child_metas.append(child_meta)

        # If this node already has real observations, keep them
        # (terminal nodes have their own observations)
        if node.metadata.historical_count > 0 and node.depth > 0:
            # But still ensure continuation_nodes are populated
            for sym in node.children:
                if sym not in node.metadata.continuation_nodes:
                    node.metadata.continuation_nodes.append(sym)
            # V4: Classify based on count
            if node.metadata.historical_count >= node.metadata.min_independent_count:
                node.metadata.node_type = "independent"
            else:
                node.metadata.node_type = "dependent"
            # V4: Update dominant_regime from distribution if available
            if node.metadata.regime_distribution:
                node.metadata.dominant_regime = max(
                    node.metadata.regime_distribution,
                    key=node.metadata.regime_distribution.get,
                )
            return node.metadata

        # Compute aggregate from children that have metadata
        children_with_data = [m for m in child_metas if m.historical_count > 0]

        if not children_with_data:
            return node.metadata

        total_count = sum(m.historical_count for m in children_with_data)

        # Weighted average of children's statistics
        weighted_move = sum(
            m.expected_move_pct * m.historical_count
            for m in children_with_data
        ) / total_count

        weighted_wr = sum(
            m.win_rate * m.historical_count
            for m in children_with_data
        ) / total_count

        weighted_duration = int(sum(
            m.avg_duration * m.historical_count
            for m in children_with_data
        ) / total_count)

        min_dd = min(m.max_drawdown_pct for m in children_with_data)

        max_fav = max(m.max_favorable_pct for m in children_with_data)

        # Update the node's metadata with aggregated values
        node.metadata.historical_count = total_count
        node.metadata.expected_move_pct = weighted_move
        node.metadata.win_rate = weighted_wr
        node.metadata.avg_duration = weighted_duration
        node.metadata.remaining_candles = weighted_duration
        node.metadata.max_drawdown_pct = min_dd
        node.metadata.max_favorable_pct = max_fav

        # Ensure continuation_nodes include all children
        for sym in node.children:
            if sym not in node.metadata.continuation_nodes:
                node.metadata.continuation_nodes.append(sym)

        # V4: Aggregate regime distribution from children
        # Merge all children's regime distributions into this node
        merged_regime_dist: dict[str, int] = {}
        for m in children_with_data:
            for regime_name, count in m.regime_distribution.items():
                merged_regime_dist[regime_name] = merged_regime_dist.get(regime_name, 0) + count
        node.metadata.regime_distribution = merged_regime_dist

        # V4.1: Aggregate regime_stats from children
        # Merge per-regime statistics (win_rate, expected_move) from children
        from ppmt.core.metadata import RegimeStats
        merged_regime_stats: dict[str, RegimeStats] = {}
        for m in children_with_data:
            for regime_name, rs in m.regime_stats.items():
                if regime_name not in merged_regime_stats:
                    merged_regime_stats[regime_name] = RegimeStats()
                merged_regime_stats[regime_name].count += rs.count
                merged_regime_stats[regime_name].wins += rs.wins
                merged_regime_stats[regime_name].total_move_pct += rs.total_move_pct
        node.metadata.regime_stats = merged_regime_stats

        # V4: Set dominant_regime from merged distribution
        if merged_regime_dist:
            node.metadata.dominant_regime = max(
                merged_regime_dist, key=merged_regime_dist.get
            )
            # Inherit regime from the dominant regime of children
            if not node.metadata.regime:
                node.metadata.regime = node.metadata.dominant_regime

        # V4: Aggregate regime_confidence as weighted average
        total_regime_conf = sum(
            m.regime_confidence * m.historical_count
            for m in children_with_data
        )
        if total_count > 0:
            node.metadata.regime_confidence = total_regime_conf / total_count

        # V4.1: Aggregate move variance from children using pooled variance
        # Pooled variance formula: weighted average of children's variances
        # plus variance between children's means (between-group variance)
        if total_count > 1:
            # Within-group variance (weighted average of children's variances)
            within_var = sum(
                m.move_variance * m.historical_count
                for m in children_with_data
            ) / total_count if total_count > 0 else 0.0

            # Between-group variance (variance of children's means)
            if len(children_with_data) > 1:
                child_means = [m.expected_move_pct for m in children_with_data]
                child_weights = [m.historical_count for m in children_with_data]
                weighted_mean = sum(m * w for m, w in zip(child_means, child_weights)) / total_count
                between_var = sum(
                    w * (m - weighted_mean) ** 2
                    for m, w in zip(child_means, child_weights)
                ) / total_count
            else:
                between_var = 0.0

            node.metadata.move_variance = within_var + between_var
            node.metadata.move_mean_for_variance = weighted_move

        # V4: Classify intermediate nodes based on count
        if node.metadata.historical_count >= node.metadata.min_independent_count:
            node.metadata.node_type = "independent"
        else:
            node.metadata.node_type = "dependent"

        return node.metadata

    def merge(self, other: PPMTTrie) -> dict:
        """
        Merge another trie into this one, preserving and combining metadata.

        This is critical for the Living Trie: when `ppmt build` creates a fresh
        trie, we merge it INTO the existing Living Trie rather than replacing it.
        This preserves all accumulated trading observations while adding any new
        patterns from the rebuild.

        Merge rules for shared paths:
        - historical_count: sum of both
        - expected_move_pct, win_rate, avg_duration: weighted average by count
        - max_drawdown_pct: min (worst case)
        - max_favorable_pct: max (best case)
        - continuation_nodes, break_nodes: set union

        Paths only in `other` are deep-copied into this trie.

        Args:
            other: The source trie to merge from (typically a fresh build)

        Returns:
            Dict with merge statistics: 'new_patterns', 'merged_patterns',
            'total_observations_added'
        """
        stats = {"new_patterns": 0, "merged_patterns": 0, "total_observations_added": 0}

        # Collect all terminal patterns from the source trie
        source_patterns = other.get_all_patterns()

        for symbols, source_node in source_patterns:
            source_meta = source_node.metadata
            if source_meta.historical_count == 0:
                continue

            # Find or create the path in this trie
            target_node = self.search(symbols)

            if target_node is None or target_node.metadata.historical_count == 0:
                # Path doesn't exist or has no observations — insert it fresh
                self.insert_with_observations(
                    symbols=symbols,
                    move_pct=source_meta.expected_move_pct,
                    drawdown_pct=source_meta.max_drawdown_pct,
                    favorable_pct=source_meta.max_favorable_pct,
                    duration=source_meta.avg_duration,
                    won=True,  # Default to won; win_rate will be set below
                    next_symbol=source_meta.continuation_nodes[0] if source_meta.continuation_nodes else None,
                )
                # Override the single-observation stats with the source's aggregate
                target_node = self.search(symbols)
                if target_node is not None:
                    target_node.metadata.historical_count = source_meta.historical_count
                    target_node.metadata.win_rate = source_meta.win_rate
                    target_node.metadata.expected_move_pct = source_meta.expected_move_pct
                    target_node.metadata.avg_duration = source_meta.avg_duration
                    target_node.metadata.remaining_candles = source_meta.avg_duration
                    target_node.metadata.max_drawdown_pct = source_meta.max_drawdown_pct
                    target_node.metadata.max_favorable_pct = source_meta.max_favorable_pct
                    for sym in source_meta.continuation_nodes:
                        if sym not in target_node.metadata.continuation_nodes:
                            target_node.metadata.continuation_nodes.append(sym)
                    for sym in source_meta.break_nodes:
                        if sym not in target_node.metadata.break_nodes:
                            target_node.metadata.break_nodes.append(sym)
                stats["new_patterns"] += 1
                stats["total_observations_added"] += source_meta.historical_count
            else:
                # Path exists with observations — merge metadata using weighted average
                t_meta = target_node.metadata
                s_meta = source_meta

                t_count = t_meta.historical_count
                s_count = s_meta.historical_count
                total = t_count + s_count

                # Weighted averages
                t_meta.expected_move_pct = (
                    t_meta.expected_move_pct * t_count + s_meta.expected_move_pct * s_count
                ) / total
                t_meta.win_rate = (
                    t_meta.win_rate * t_count + s_meta.win_rate * s_count
                ) / total
                t_meta.avg_duration = int(
                    (t_meta.avg_duration * t_count + s_meta.avg_duration * s_count) / total
                )
                t_meta.remaining_candles = t_meta.avg_duration

                # Min/max for extremes
                t_meta.max_drawdown_pct = min(t_meta.max_drawdown_pct, s_meta.max_drawdown_pct)
                t_meta.max_favorable_pct = max(t_meta.max_favorable_pct, s_meta.max_favorable_pct)

                # Sum counts
                t_meta.historical_count = total

                # Set union for navigation lists
                for sym in s_meta.continuation_nodes:
                    if sym not in t_meta.continuation_nodes:
                        t_meta.continuation_nodes.append(sym)
                for sym in s_meta.break_nodes:
                    if sym not in t_meta.break_nodes:
                        t_meta.break_nodes.append(sym)

                stats["merged_patterns"] += 1
                stats["total_observations_added"] += s_count

        # Merge trading observations count
        self.trading_observations += other.trading_observations

        # Recompute pattern count and max depth
        self._recompute_counts()

        # Re-propagate metadata so intermediate nodes are updated
        self.propagate_metadata()

        return stats

    def _recompute_counts(self) -> None:
        """Recompute _pattern_count and _max_depth from the actual trie structure."""
        count = [0]
        max_depth = [0]

        def _walk(node: TrieNode, depth: int):
            if depth > 0 and node.metadata.historical_count > 0:
                count[0] += 1
            if depth > max_depth[0]:
                max_depth[0] = depth
            for child in node.children.values():
                _walk(child, depth + 1)

        _walk(self.root, 0)
        self._pattern_count = count[0]
        self._max_depth = max_depth[0]

    def __len__(self) -> int:
        return self._pattern_count

    def __repr__(self) -> str:
        return (
            f"PPMTTrie(name='{self.name}', patterns={self._pattern_count}, "
            f"max_depth={self._max_depth})"
        )
