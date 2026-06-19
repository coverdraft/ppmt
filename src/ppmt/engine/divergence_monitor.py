"""
Pattern Divergence Monitor - FASE 2, Tarea 2.2

Monitors open positions for pattern divergence. On each new candle,
compares the real SAX sequence with the expected_sequences from the
node that generated the signal. If divergence exceeds threshold,
triggers an exit with reason='pattern_broken'.

This is an early exit mechanism that detects when the market is NOT
following the expected continuation of the matched pattern. It's more
nuanced than the simple pattern-break check (which only looks at whether
the next symbol exists as a child node) because it tracks the full
3-symbol forward sequence and computes a divergence ratio.

Usage:
    from ppmt.engine.divergence_monitor import PatternDivergenceMonitor

    monitor = PatternDivergenceMonitor(divergence_threshold=0.667)
    monitor.set_expected(entry_node_metadata)

    # On each new candle:
    result = monitor.check_divergence(real_symbols=['a', 'x', 'z'])
    if result['diverged']:
        # Exit position with reason='pattern_broken'
"""

from __future__ import annotations

from typing import Optional

from ppmt.core.metadata import BlockLifecycleMetadata


class PatternDivergenceMonitor:
    """Monitors open positions for pattern divergence.

    On each new candle, compares the real SAX sequence with
    the expected_sequences from the node that generated the signal.
    If divergence exceeds threshold → exit with reason='pattern_broken'.
    """

    def __init__(self, divergence_threshold: float = 0.667):
        """
        Args:
            divergence_threshold: Fraction of symbols that must differ
                to trigger pattern_broken. 0.667 = 2/3 symbols different.
        """
        self.divergence_threshold = divergence_threshold
        self.expected_sequence: Optional[tuple] = None
        self.entry_node_metadata: Optional[BlockLifecycleMetadata] = None

    def set_expected(self, metadata: BlockLifecycleMetadata):
        """Set the expected sequence from the entry node's metadata."""
        if metadata.expected_sequences:
            # Pick the most frequent sequence
            self.expected_sequence = max(
                metadata.expected_sequences.items(),
                key=lambda x: x[1],
            )[0]
        self.entry_node_metadata = metadata

    def check_divergence(self, real_symbols: list[str]) -> dict:
        """Check if real symbols diverge from expected sequence.

        Returns dict with:
            'diverged': bool
            'divergence_ratio': float (0-1)
            'reason': str ('pattern_broken' or 'pattern_intact')
        """
        if self.expected_sequence is None or len(real_symbols) == 0:
            return {'diverged': False, 'divergence_ratio': 0.0, 'reason': 'no_expectation'}

        # Compare up to min(len(expected), len(real))
        compare_len = min(len(self.expected_sequence), len(real_symbols))
        if compare_len == 0:
            return {'diverged': False, 'divergence_ratio': 0.0, 'reason': 'no_data'}

        mismatches = sum(
            1 for i in range(compare_len)
            if real_symbols[i] != self.expected_sequence[i]
        )
        divergence_ratio = mismatches / compare_len

        return {
            'diverged': divergence_ratio >= self.divergence_threshold,
            'divergence_ratio': divergence_ratio,
            'reason': 'pattern_broken' if divergence_ratio >= self.divergence_threshold else 'pattern_intact',
        }
