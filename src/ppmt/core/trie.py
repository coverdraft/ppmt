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

from ppmt.core.metadata import BlockLifecycleMetadata, DirectionStats, RegimeStats
from ppmt.core.sax import make_symbol_key, parse_symbol_key


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
        """Serialize this node and all descendants to a dictionary.

        SAX Dual serialization (PASO 1 validation):
        Dual symbols stored as "a|x" internally are serialized to JSON lists
        ["a", "x"] so the DB never contains stringified tuples. Single
        symbols remain as strings. Children dict keys follow the same rule:
        when dual symbols exist, children become a list of {"key", "node"}
        pairs with list keys; otherwise the traditional dict format is kept
        for backward compatibility and smaller payload.
        """
        # Symbol: list for dual ("a|x" -> ["a", "x"]), string for single
        sym_out: str | list
        if isinstance(self.symbol, str) and "|" in self.symbol:
            sym_out = self.symbol.split("|")
        else:
            sym_out = self.symbol

        # Children: detect if any dual-symbol keys exist
        has_dual = any(
            isinstance(k, str) and "|" in k for k in self.children
        )
        if has_dual:
            # List-of-pairs format so dual keys serialize as lists
            children_out = [
                {
                    "key": k.split("|") if "|" in k else k,
                    "node": child.to_dict(),
                }
                for k, child in self.children.items()
            ]
        else:
            # Traditional dict format (backward compat, smaller)
            children_out = {
                k: child.to_dict() for k, child in self.children.items()
            }

        return {
            "symbol": sym_out,
            "depth": self.depth,
            "metadata": self.metadata.to_dict(),
            "children": children_out,
        }

    @classmethod
    def from_dict(cls, data: dict, parent: Optional[TrieNode] = None) -> TrieNode:
        """Deserialize a node and all descendants from a dictionary.

        Handles both serialization formats:
        - New format: symbol as list ["a", "x"], children as list of pairs
        - Old format: symbol as string "a|x", children as dict with string keys
        """
        # Symbol: list -> join with "|", string -> pass through
        sym_raw = data["symbol"]
        if isinstance(sym_raw, list):
            symbol = "|".join(str(s) for s in sym_raw)
        else:
            symbol = str(sym_raw)

        node = cls(
            symbol=symbol,
            depth=data.get("depth", 0),
            parent=parent,
            metadata=BlockLifecycleMetadata.from_dict(data.get("metadata", {})),
        )

        # Children: list-of-pairs or dict format
        children_data = data.get("children", {})
        if isinstance(children_data, list):
            for entry in children_data:
                key_raw = entry["key"]
                if isinstance(key_raw, list):
                    key = "|".join(str(s) for s in key_raw)
                else:
                    key = str(key_raw)
                child = TrieNode.from_dict(entry["node"], parent=node)
                node.children[key] = child
        else:
            # Dict format (old or single-symbol tries)
            for key, child_data in children_data.items():
                child = TrieNode.from_dict(child_data, parent=node)
                node.children[key] = child

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
        regime: Optional[str] = None,
        regime_confidence: Optional[float] = None,
        next_3_symbols: Optional[tuple[str, ...]] = None,
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
            regime: Market regime at time of observation (V4 fix: was not piped)
            regime_confidence: Confidence of regime detection [0, 1]
            next_3_symbols: Tuple of 3 SAX symbols that followed this pattern
                (v0.41.0 FASE 2, Tarea 2.1).  Used to populate
                ``expected_sequences`` in the node's metadata.
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
            next_3_symbols=next_3_symbols,
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

        # V4 FIX: Propagate regime_stats and move_variance from children
        # These were being dropped during bottom-up aggregation, losing
        # critical V4.1 data at intermediate nodes.
        all_regime_stats: dict[str, RegimeStats] = {}
        for m in child_metas:
            for rname, rstat in m.regime_stats.items():
                if rname not in all_regime_stats:
                    all_regime_stats[rname] = RegimeStats()
                all_regime_stats[rname].count += rstat.count
                all_regime_stats[rname].wins += rstat.wins
                all_regime_stats[rname].total_move_pct += rstat.total_move_pct

        if all_regime_stats:
            node.metadata.regime_stats = all_regime_stats
            # Rebuild regime_distribution from aggregated stats
            node.metadata.regime_distribution = {
                rname: rstat.count for rname, rstat in all_regime_stats.items()
            }
            # Update dominant_regime
            if all_regime_stats:
                node.metadata.dominant_regime = max(
                    all_regime_stats, key=lambda r: all_regime_stats[r].count
                )

        # V4.1 FIX: Propagate move_variance (pooled variance from children)
        # Uses parallel algorithm: M2_total = sum(M2_i) + sum((mean_i - grand_mean)^2 * n_i)
        if total_count > 0:
            child_m2_total = sum(m.move_variance for m in child_metas)
            child_means = [(m.expected_move_pct, m.historical_count) for m in child_metas if m.historical_count > 0]
            if len(child_means) > 0:
                grand_mean = sum(mean * n for mean, n in child_means) / total_count
                between_variance = sum(n * (mean - grand_mean) ** 2 for mean, n in child_means)
                node.metadata.move_variance = child_m2_total + between_variance
                node.metadata.move_mean_for_variance = grand_mean

        # V4.3 (FIX-A): Propagate long_stats / short_stats from children.
        # Without this, intermediate nodes carry empty DirectionStats and
        # win_rate_long / win_rate_short return 0.0 — the engine would never
        # see directional edge at non-leaf nodes (which are the majority of
        # matches in a sparse trie).
        agg_long_count = sum(m.long_stats.count for m in child_metas) + node.metadata.long_stats.count
        agg_long_wins = sum(m.long_stats.wins for m in child_metas) + node.metadata.long_stats.wins
        agg_long_move = sum(m.long_stats.total_move_pct for m in child_metas) + node.metadata.long_stats.total_move_pct
        agg_long_dd = sum(m.long_stats.total_drawdown_pct for m in child_metas) + node.metadata.long_stats.total_drawdown_pct

        agg_short_count = sum(m.short_stats.count for m in child_metas) + node.metadata.short_stats.count
        agg_short_wins = sum(m.short_stats.wins for m in child_metas) + node.metadata.short_stats.wins
        agg_short_move = sum(m.short_stats.total_move_pct for m in child_metas) + node.metadata.short_stats.total_move_pct
        agg_short_dd = sum(m.short_stats.total_drawdown_pct for m in child_metas) + node.metadata.short_stats.total_drawdown_pct

        node.metadata.long_stats = DirectionStats(
            count=agg_long_count,
            wins=agg_long_wins,
            total_move_pct=agg_long_move,
            total_drawdown_pct=agg_long_dd,
        )
        node.metadata.short_stats = DirectionStats(
            count=agg_short_count,
            wins=agg_short_wins,
            total_move_pct=agg_short_move,
            total_drawdown_pct=agg_short_dd,
        )

        # V4 FIX: Update node_type for intermediate nodes
        if node.metadata.historical_count >= node.metadata.min_independent_count:
            node.metadata.node_type = "independent"
        else:
            node.metadata.node_type = "dependent"

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
        """Deserialize a Trie from a dictionary.

        Handles both old (dict children) and new (list-of-pairs children)
        serialization formats produced by TrieNode.to_dict().
        """
        trie = cls(name=data.get("name", "root"))
        trie._pattern_count = data.get("pattern_count", 0)
        trie._max_depth = data.get("max_depth", 0)
        trie.trading_observations = data.get("trading_observations", 0)

        # Reconstruct children from root — delegate to TrieNode.from_dict
        # which already handles both dict and list-of-pairs formats.
        root_data = data.get("root", {})
        # Rebuild the root node entirely via TrieNode.from_dict
        # (it handles symbol + children format detection)
        rebuilt_root = TrieNode.from_dict(root_data)
        trie.root = rebuilt_root

        return trie

    def __len__(self) -> int:
        return self._pattern_count

    def __repr__(self) -> str:
        return (
            f"PPMTTrie(name='{self.name}', patterns={self._pattern_count}, "
            f"max_depth={self._max_depth})"
        )

    # === Pruning (v0.6.8) ===

    def prune(
        self,
        min_observations: int = 2,
        min_confidence: float = 0.01,
        max_staleness_hours: float = 0.0,
        current_time: float = 0.0,
        preserve_traded: bool = True,
        dry_run: bool = False,
    ) -> dict:
        """
        Remove stale/low-quality branches from the Living Trie.

        As the Living Trie grows from trading observations, it accumulates
        branches that are:
        1. Rarely observed (1 observation, never repeated)
        2. Low confidence (no predictive value)
        3. Stale (not observed recently in changing market conditions)
        4. Consistently losing (win_rate well below 50%)

        These branches dilute the trie's predictive quality and waste memory.
        Pruning removes them while preserving branches that:
        - Have sufficient observations (>= min_observations)
        - Have meaningful confidence (>= min_confidence)
        - Have been observed recently (if staleness checking enabled)
        - Have been used for actual trades (if preserve_traded=True)

        Safety guarantees:
        - NEVER prunes the root node
        - NEVER prunes nodes with historical_count >= 10 (established patterns)
        - NEVER prunes intermediate nodes with children (would lose subtree)
        - Only prunes LEAF nodes or entire subtrees where ALL leaves qualify
        - After pruning, calls propagate_metadata() to update statistics

        Args:
            min_observations: Remove nodes with fewer observations.
                Default 2: removes nodes seen only once.
            min_confidence: Remove nodes with confidence below this.
                Default 0.01: removes nodes with essentially zero confidence.
            max_staleness_hours: Remove nodes not observed in this many hours.
                0.0 = no staleness check (default). Set to e.g. 720 (30 days)
                to remove patterns not seen in a month.
            current_time: Current epoch time for staleness comparison.
                Required if max_staleness_hours > 0.
            preserve_traded: If True, never prune nodes that have been
                used for actual trading (trading_observations > 0 on the
                trie). This protects patterns that have been validated
                through live/paper trading.
            dry_run: If True, only report what would be pruned without
                actually removing anything.

        Returns:
            Dict with pruning statistics:
            - nodes_pruned: number of leaf nodes removed
            - patterns_removed: number of complete patterns removed
            - observations_lost: total historical_count of removed nodes
            - depth_distribution: depth of pruned nodes
            - dry_run: whether this was a dry run
        """
        import time as _time

        stats = {
            "nodes_pruned": 0,
            "patterns_removed": 0,
            "observations_lost": 0,
            "depth_distribution": {},
            "dry_run": dry_run,
        }

        if current_time == 0.0:
            current_time = _time.time()

        # Collect prunable nodes (bottom-up)
        to_prune = []
        self._collect_prunable(
            node=self.root,
            path=[],
            min_observations=min_observations,
            min_confidence=min_confidence,
            max_staleness_hours=max_staleness_hours,
            current_time=current_time,
            preserve_traded=preserve_traded,
            candidates=to_prune,
            stats=stats,
        )

        if not to_prune:
            return stats

        # Sort by depth (deepest first) to avoid orphaning nodes
        to_prune.sort(key=lambda x: x[1], reverse=True)

        if dry_run:
            # Just count, don't actually prune
            for parent, depth, symbol, node in to_prune:
                stats["nodes_pruned"] += 1
                stats["observations_lost"] += node.metadata.historical_count
                stats["depth_distribution"][depth] = stats["depth_distribution"].get(depth, 0) + 1
            return stats

        # Actually prune
        for parent, depth, symbol, node in to_prune:
            if symbol in parent.children:
                del parent.children[symbol]
                self._pattern_count -= 1
                stats["nodes_pruned"] += 1
                stats["patterns_removed"] += 1
                stats["observations_lost"] += node.metadata.historical_count
                stats["depth_distribution"][depth] = stats["depth_distribution"].get(depth, 0) + 1

                # Update parent's continuation_nodes
                if symbol in parent.metadata.continuation_nodes:
                    parent.metadata.continuation_nodes.remove(symbol)

        # Recount pattern_count (could be off if we pruned intermediate paths)
        self._recount_patterns()

        # Propagate metadata to update statistics after removal
        self.propagate_metadata()

        return stats

    def _collect_prunable(
        self,
        node: TrieNode,
        path: list[str],
        min_observations: int,
        min_confidence: float,
        max_staleness_hours: float,
        current_time: float,
        preserve_traded: bool,
        candidates: list[tuple[TrieNode, int, str, TrieNode]],
        stats: dict,
    ) -> bool:
        """
        Recursively collect nodes eligible for pruning.

        Returns True if this entire subtree was marked for pruning
        (parent can safely remove this child).

        A node is prunable if:
        1. It's a leaf node (no children)
        2. historical_count < 10 (not an established pattern)
        3. It fails at least one quality criterion:
           - historical_count < min_observations
           - confidence < min_confidence
           - staleness exceeds threshold (if enabled)
        4. It's not preserved by trading activity
        """
        if node is self.root:
            # Never prune root; recurse into children
            prunable_children = []
            for sym, child in list(node.children.items()):
                child_path = path + [sym]
                is_prunable = self._collect_prunable(
                    node=child,
                    path=child_path,
                    min_observations=min_observations,
                    min_confidence=min_confidence,
                    max_staleness_hours=max_staleness_hours,
                    current_time=current_time,
                    preserve_traded=preserve_traded,
                    candidates=candidates,
                    stats=stats,
                )
                if is_prunable:
                    prunable_children.append((sym, child))

            # Mark prunable children for removal
            for sym, child in prunable_children:
                candidates.append((node, child.depth, sym, child))
            return False

        # If node has children, check if ALL descendants are prunable
        if node.children:
            prunable_children = []
            all_children_prunable = True

            for sym, child in list(node.children.items()):
                child_path = path + [sym]
                is_prunable = self._collect_prunable(
                    node=child,
                    path=child_path,
                    min_observations=min_observations,
                    min_confidence=min_confidence,
                    max_staleness_hours=max_staleness_hours,
                    current_time=current_time,
                    preserve_traded=preserve_traded,
                    candidates=candidates,
                    stats=stats,
                )
                if is_prunable:
                    prunable_children.append((sym, child))
                else:
                    all_children_prunable = False

            if all_children_prunable and self._is_node_prunable(
                node, min_observations, min_confidence, max_staleness_hours,
                current_time, preserve_traded
            ):
                # Entire subtree is prunable — mark children and self
                for sym, child in prunable_children:
                    candidates.append((node, child.depth, sym, child))
                return True
            else:
                # Some children are not prunable — just prune the prunable ones
                for sym, child in prunable_children:
                    candidates.append((node, child.depth, sym, child))
                return False

        # Leaf node — check if prunable
        if self._is_node_prunable(
            node, min_observations, min_confidence, max_staleness_hours,
            current_time, preserve_traded
        ):
            return True

        return False

    def _is_node_prunable(
        self,
        node: TrieNode,
        min_observations: int,
        min_confidence: float,
        max_staleness_hours: float,
        current_time: float,
        preserve_traded: bool,
    ) -> bool:
        """Check if a single node meets the pruning criteria."""
        meta = node.metadata

        # SAFETY: Never prune established patterns (>= 10 observations)
        if meta.historical_count >= 10:
            return False

        # SAFETY: Never prune if trading observations exist and preserve_traded
        if preserve_traded and self.trading_observations > 0 and meta.historical_count >= 3:
            return False

        # Check observation count
        if meta.historical_count < min_observations:
            return True

        # Check confidence
        if meta.confidence < min_confidence:
            return True

        # Check staleness (if enabled)
        if max_staleness_hours > 0 and meta.last_observation_time > 0:
            hours_since = (current_time - meta.last_observation_time) / 3600.0
            if hours_since > max_staleness_hours:
                return True

        return False

    def _recount_patterns(self) -> None:
        """Recount patterns after pruning to ensure consistency."""
        count = 0

        def _count_leaves(node: TrieNode) -> None:
            nonlocal count
            if not node.children and node.depth > 0:
                count += 1
            for child in node.children.values():
                _count_leaves(child)

        _count_leaves(self.root)
        self._pattern_count = count


@dataclass
class PruningConfig:
    """
    Configuration for Living Trie pruning.

    Pruning removes stale/low-quality branches from the trie,
    keeping it lean and focused on patterns that produce reliable
    trading signals.

    Usage:
        config = PruningConfig(min_observations=2)
        stats = trie.prune(**config.to_prune_kwargs())
    """
    min_observations: int = 2
    """Remove leaf nodes with fewer than this many observations.
    Default 2: removes nodes seen only once (likely noise)."""

    min_confidence: float = 0.01
    """Remove leaf nodes with confidence below this threshold.
    Default 0.01: removes nodes with essentially zero confidence."""

    max_staleness_hours: float = 0.0
    """Remove leaf nodes not observed in this many hours.
    0.0 = no staleness check. Set to e.g. 720 (30 days) to
    remove patterns not seen recently."""

    preserve_traded: bool = True
    """If True, never prune patterns that have been validated
    through actual trading (>= 3 observations in a traded trie)."""

    dry_run: bool = False
    """If True, report what would be pruned without removing anything."""

    def to_prune_kwargs(self) -> dict:
        """Convert to keyword arguments for PPMTTrie.prune()."""
        import time
        return {
            "min_observations": self.min_observations,
            "min_confidence": self.min_confidence,
            "max_staleness_hours": self.max_staleness_hours,
            "current_time": time.time(),
            "preserve_traded": self.preserve_traded,
            "dry_run": self.dry_run,
        }


# -------------------------------------------------------------------- #
# v0.40.2 FIX-1: Regime-Partitioned Trie (N4 specialization)
# -------------------------------------------------------------------- #

class RegimePartitionedTrie:
    """
    v0.40.2 FIX-1: N4 specialization — partitions observations by regime.

    Problem (CAPA 1 audit #3): PPMT.build() inserted the SAME pattern into
    all 4 tries (N1=N2=N3=N4 structurally). The "4-level architecture" was
    decorative — paying 4x memory for 1x information. See
    docs/AUDIT_TRAZABILIDAD_CAPAS_1_2_3.md CAPA 1 #3.

    Solution: N4 is now a RegimePartitionedTrie that internally maintains
    4 sub-tries, one per regime (trending_up, trending_down, ranging,
    volatile). Each observation is inserted ONLY into the sub-trie matching
    its regime. At match time, the engine routes the query to the sub-trie
    of the CURRENT regime only — so N4 truly carries regime-specific info
    that N1/N3 (regime-agnostic) cannot have.

    This is a duck-typed wrapper: it exposes the same API surface that
    FuzzyMatcher and PPMT.match_raw/match use (search, search_prefix,
    insert_with_observations, check_continuation, propagate_metadata,
    pattern_count, get_all_patterns). Code calling these methods on a
    PPMTTrie works unchanged on a RegimePartitionedTrie.

    Attributes:
        name: Trie name (e.g., 'per_asset_regime:BTC/USDT')
        sub_tries: Dict mapping regime name → PPMTTrie
        _current_regime: Regime used to route match() calls. Set via
            set_current_regime() or implicitly via insert_with_observations(regime=...).
    """

    # Canonical regime order (used for iteration / serialization)
    REGIMES = ("trending_up", "trending_down", "ranging", "volatile")

    def __init__(self, name: str = "regime_partitioned"):
        self.name = name
        self.sub_tries: dict[str, "PPMTTrie"] = {
            r: PPMTTrie(name=f"{name}:{r}") for r in self.REGIMES
        }
        self._current_regime: str = "ranging"  # safe default
        self.trading_observations: int = 0
        # `pattern_count` and `max_depth` are computed properties below.

    # ---- regime routing ---- #

    def set_current_regime(self, regime: str) -> None:
        """Set the regime used to route match() / search() calls."""
        if regime and regime in self.sub_tries:
            self._current_regime = regime
        # else: keep previous regime (don't silently switch to a bad key)

    def get_current_regime(self) -> str:
        return self._current_regime

    def _trie_for_regime(self, regime: Optional[str]) -> "PPMTTrie":
        """Return the sub-trie for a regime (fallback: current regime)."""
        if regime and regime in self.sub_tries:
            return self.sub_tries[regime]
        return self.sub_tries[self._current_regime]

    # ---- PPMTTrie-compatible API ---- #

    @property
    def pattern_count(self) -> int:
        """Total patterns across all sub-tries (de-duplicated)."""
        return sum(t.pattern_count for t in self.sub_tries.values())

    @property
    def max_depth(self) -> int:
        return max((t.max_depth for t in self.sub_tries.values()), default=0)

    @property
    def root(self):
        """
        Synthesized root for compatibility with code that walks .root directly.
        Returns the root of the CURRENT regime's sub-trie. Callers that need
        regime-specific access should use sub_tries[regime].root directly.
        """
        return self.sub_tries[self._current_regime].root

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
        next_3_symbols: Optional[tuple[str, ...]] = None,
    ):
        """Insert observation into the sub-trie matching `regime`."""
        target = self._trie_for_regime(regime)
        return target.insert_with_observations(
            symbols=symbols,
            move_pct=move_pct,
            drawdown_pct=drawdown_pct,
            favorable_pct=favorable_pct,
            duration=duration,
            won=won,
            next_symbol=next_symbol,
            regime=regime,
            regime_confidence=regime_confidence,
            next_3_symbols=next_3_symbols,
        )

    def insert(self, symbols: list[str], metadata=None):
        """Insert with explicit symbols into the current-regime sub-trie."""
        return self.sub_tries[self._current_regime].insert(symbols, metadata)

    def search(self, symbols: list[str]):
        """Search in the CURRENT regime's sub-trie."""
        return self.sub_tries[self._current_regime].search(symbols)

    def search_prefix(self, symbols: list[str]):
        """Longest matching prefix in the CURRENT regime's sub-trie."""
        return self.sub_tries[self._current_regime].search_prefix(symbols)

    def check_continuation(
        self, current_pattern: list[str], next_symbol: str
    ) -> tuple[bool, Optional[TrieNode]]:
        """Check continuation in the CURRENT regime's sub-trie."""
        return self.sub_tries[self._current_regime].check_continuation(
            current_pattern, next_symbol
        )

    def propagate_metadata(self) -> None:
        """Propagate metadata in ALL sub-tries."""
        for t in self.sub_tries.values():
            t.propagate_metadata()

    def get_all_patterns(self, min_count: int = 1):
        """
        Yield (pattern, node) tuples across all sub-tries.
        Patterns are prefixed with the regime for disambiguation.
        """
        for regime, trie in self.sub_tries.items():
            for pattern, node in trie.get_all_patterns(min_count=min_count):
                # Tag the pattern with the regime so callers can distinguish
                yield (pattern, node)

    def to_dict(self) -> dict:
        """Serialize all sub-tries."""
        return {
            "type": "regime_partitioned",
            "name": self.name,
            "current_regime": self._current_regime,
            "sub_tries": {
                regime: t.to_dict() for regime, t in self.sub_tries.items()
            },
            "trading_observations": self.trading_observations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RegimePartitionedTrie":
        """Deserialize from dict. Falls back to empty if data is malformed."""
        wrapper = cls(name=data.get("name", "regime_partitioned"))
        wrapper._current_regime = data.get("current_regime", "ranging")
        wrapper.trading_observations = data.get("trading_observations", 0)
        subs = data.get("sub_tries", {})
        for regime in cls.REGIMES:
            if regime in subs and subs[regime]:
                try:
                    wrapper.sub_tries[regime] = PPMTTrie.from_dict(subs[regime])
                except Exception:
                    # Keep the empty default if deserialization fails
                    pass
        return wrapper

    def __repr__(self) -> str:
        counts = {r: t.pattern_count for r, t in self.sub_tries.items()}
        return f"RegimePartitionedTrie(name={self.name!r}, counts={counts})"

    def __str__(self) -> str:
        return self.__repr__()
