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
        """Count of observations recorded from actual trading (Living Trie).
        This distinguishes build-time observations from post-build trading
        observations, enabling confidence scaling for fresh tries."""

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
    ) -> TrieNode:
        """
        Insert a pattern and update metadata from a single observation.

        This is the primary method for building the Trie from historical data.
        Each call represents one observed instance of the pattern.

        Args:
            symbols: SAX symbol sequence
            move_pct: Observed percentage move
            drawdown_pct: Maximum drawdown observed
            favorable_pct: Maximum favorable excursion
            duration: Duration in candles
            won: Whether the pattern completed successfully
            next_symbol: What followed this pattern (for continuation tracking)
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

    def propagate_metadata(self) -> None:
        """
        Propagate metadata from leaf nodes up to the root.

        After building the Trie, intermediate nodes (including the root)
        may have zero historical_count because only terminal nodes received
        observations during insertion. This method aggregates child metadata
        into each parent node so that every node has meaningful statistics.

        The aggregation computes:
        - historical_count: sum of all children's counts
        - win_rate: weighted average of children's win_rates
        - expected_move_pct: weighted average of children's moves
        - max_drawdown_pct: minimum (worst) across children
        - max_favorable_pct: maximum (best) across children
        - avg_duration: weighted average of children's durations
        - continuation_nodes: union of all children's continuation symbols

        This is called once after Trie construction and periodically during
        Living Trie operation (every 200 symbol steps in paper_trader.py).
        """
        self._propagate_node(self.root)

    def _propagate_node(self, node: TrieNode) -> BlockLifecycleMetadata:
        """
        Recursively propagate metadata from children to this node.

        For leaf nodes (no children), returns the node's own metadata.
        For internal nodes, aggregates children's metadata with the node's
        own observations (if any).

        The node's OWN observations take precedence — children's data
        augments but doesn't replace the node's direct observations.
        """
        if not node.children:
            # Leaf node: return its own metadata
            return node.metadata

        # First, recursively propagate all children
        child_metas = []
        for child in node.children.values():
            child_meta = self._propagate_node(child)
            child_metas.append(child_meta)

        # Aggregate children's metadata
        total_count = sum(m.historical_count for m in child_metas)

        if total_count == 0:
            return node.metadata

        # Weighted averages
        weighted_win_rate = sum(
            m.win_rate * m.historical_count for m in child_metas
        ) / total_count

        weighted_move = sum(
            m.expected_move_pct * m.historical_count for m in child_metas
        ) / total_count

        weighted_duration = sum(
            m.avg_duration * m.historical_count for m in child_metas
        ) / total_count

        # Min/max across children
        worst_drawdown = min(m.max_drawdown_pct for m in child_metas)
        best_favorable = max(m.max_favorable_pct for m in child_metas)

        # Union of continuation symbols
        all_continuations = set()
        for m in child_metas:
            all_continuations.update(m.continuation_nodes)

        # Merge with node's own observations (if any)
        own_count = node.metadata.historical_count
        if own_count > 0:
            combined_count = own_count + total_count
            node.metadata.win_rate = (
                node.metadata.win_rate * own_count + weighted_win_rate * total_count
            ) / combined_count
            node.metadata.expected_move_pct = (
                node.metadata.expected_move_pct * own_count + weighted_move * total_count
            ) / combined_count
            node.metadata.avg_duration = int(
                (node.metadata.avg_duration * own_count + weighted_duration * total_count)
                / combined_count
            )
            node.metadata.historical_count = combined_count
        else:
            # Node has no own observations: use aggregated children data
            node.metadata.historical_count = total_count
            node.metadata.win_rate = weighted_win_rate
            node.metadata.expected_move_pct = weighted_move
            node.metadata.avg_duration = int(weighted_duration)

        node.metadata.max_drawdown_pct = min(
            node.metadata.max_drawdown_pct, worst_drawdown
        )
        node.metadata.max_favorable_pct = max(
            node.metadata.max_favorable_pct, best_favorable
        )
        node.metadata.remaining_candles = node.metadata.avg_duration

        for sym in all_continuations:
            if sym not in node.metadata.continuation_nodes:
                node.metadata.continuation_nodes.append(sym)

        return node.metadata

    # === Serialization (PPMTTrie) ===

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

    def __len__(self) -> int:
        return self._pattern_count

    def __repr__(self) -> str:
        return (
            f"PPMTTrie(name='{self.name}', patterns={self._pattern_count}, "
            f"max_depth={self._max_depth})"
        )
