"""
Streaming Pattern Buffer - Real-Time SAX Symbol Management

Maintains a sliding window of SAX symbols for real-time pattern matching
against the PPMT Trie. Designed for incremental, one-candle-at-a-time
processing with zero look-ahead bias.

Architecture:
  Candle → SAXEncoder.encode_incremental() → [new_symbols]
    → StreamingPatternBuffer.append(new_symbols)
      → buffer.auto_trim()
      → buffer.get_pattern() → [s1, s2, ..., sN]
        → PPMT.match(pattern)
        → PredictionEngine.predict(pattern)

The buffer tracks:
  - Current SAX symbol window (pattern_buffer)
  - Partial SAX window (sax_buffer) for incremental encoding
  - Symbol statistics (counts, entropy)
  - Pattern history for Living Trie updates

FASE 1 Tarea 1.1: Added MultiLevelStreamingBuffer that manages
4 parallel StreamingPatternBuffers (one per trie level), each
driven by its own SAXEncoder with a differentiated alphabet size.

v0.13.0: New module — extracted from RealtimeTrader for reusability.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class PatternEvent:
    """Record of a pattern event (new symbol, match, break)."""
    timestamp: float
    symbol: str
    event_type: str  # "new_symbol", "pattern_match", "pattern_break", "trim"
    pattern_snapshot: list[str] = field(default_factory=list)
    confidence: float = 0.0
    metadata: dict = field(default_factory=dict)


class StreamingPatternBuffer:
    """
    Thread-safe sliding window buffer for SAX symbols in real-time trading.

    Maintains the streaming state needed for incremental SAX encoding and
    pattern matching. Replaces the raw list-based approach in RealtimeTrader
    with a structured, observable buffer.

    Features:
      - Sliding window with configurable max length
      - SAX partial buffer management (for incremental encoding)
      - Symbol frequency tracking (entropy monitoring)
      - Pattern event history (for Living Trie updates)
      - Pattern break detection helpers

    Usage:
        buf = StreamingPatternBuffer(pattern_length=5, max_buffer_length=20)
        # After SAX encoding:
        new_symbols, sax_buffer = encoder.encode_incremental(candle_df, sax_buffer)
        buf.update(new_symbols, sax_buffer)
        if buf.has_pattern():
            pattern = buf.get_pattern()
            prediction = pred_engine.predict(pattern)
    """

    def __init__(
        self,
        pattern_length: int = 5,
        max_buffer_length: int = 0,
        track_history: bool = True,
        max_history: int = 1000,
    ):
        """
        Initialize the streaming pattern buffer.

        Args:
            pattern_length: Number of SAX symbols per pattern for Trie matching.
            max_buffer_length: Maximum symbols to keep. 0 = auto (pattern_length * 3).
            track_history: Whether to record pattern events.
            max_history: Maximum number of events to keep in history.
        """
        self.pattern_length = pattern_length
        self.max_buffer_length = max_buffer_length or (pattern_length * 3)
        self.track_history = track_history
        self.max_history = max_history

        # Core buffers
        self._pattern_buffer: list[str] = []
        self._sax_buffer: list = []  # SAXEncoder's partial window buffer

        # Statistics
        self._symbol_counts: Counter = Counter()
        self._total_symbols: int = 0
        self._total_candles: int = 0
        self._symbols_produced: int = 0
        self._patterns_matched: int = 0
        self._patterns_broken: int = 0

        # History for Living Trie
        self._history: deque[PatternEvent] = deque(maxlen=max_history)

        # Timing
        self._last_symbol_time: float = 0.0
        self._created_at: float = time.time()

    # ================================================================
    # PROPERTIES
    # ================================================================

    @property
    def pattern_buffer(self) -> list[str]:
        """Current SAX symbol buffer (read-only copy)."""
        return list(self._pattern_buffer)

    @property
    def sax_buffer(self) -> list:
        """Current SAX partial window buffer (for incremental encoding)."""
        return self._sax_buffer

    @sax_buffer.setter
    def sax_buffer(self, value: list):
        """Update SAX partial buffer (set by SAXEncoder.encode_incremental)."""
        self._sax_buffer = value

    @property
    def length(self) -> int:
        """Number of symbols currently in the pattern buffer."""
        return len(self._pattern_buffer)

    @property
    def symbols_produced(self) -> int:
        """Total SAX symbols produced since initialization."""
        return self._symbols_produced

    @property
    def total_candles(self) -> int:
        """Total candles processed (including those that didn't produce symbols)."""
        return self._total_candles

    @property
    def entropy(self) -> float:
        """
        Shannon entropy of the symbol distribution.

        High entropy = diverse symbols (good for pattern matching).
        Low entropy = concentrated symbols (may indicate poor SAX params).
        """
        if self._total_symbols == 0:
            return 0.0

        total = self._total_symbols
        probs = [count / total for count in self._symbol_counts.values() if count > 0]
        return -sum(p * np.log2(p) for p in probs if p > 0)

    @property
    def symbol_concentration(self) -> float:
        """
        Fraction of symbols belonging to the most common symbol.

        High concentration (>0.5) suggests SAX parameters may need tuning.
        """
        if self._total_symbols == 0:
            return 0.0
        return max(self._symbol_counts.values()) / self._total_symbols

    @property
    def last_symbol(self) -> Optional[str]:
        """Most recently added symbol, or None if buffer is empty."""
        return self._pattern_buffer[-1] if self._pattern_buffer else None

    @property
    def history(self) -> list[PatternEvent]:
        """Recent pattern events (for Living Trie updates)."""
        return list(self._history)

    @property
    def uptime_seconds(self) -> float:
        """Seconds since buffer was created."""
        return time.time() - self._created_at

    # ================================================================
    # CORE OPERATIONS
    # ================================================================

    def update(self, new_symbols: list[str], sax_buffer: list) -> list[str]:
        """
        Update the buffer with new SAX symbols.

        Called after SAXEncoder.encode_incremental() produces new symbols.
        Automatically trims the buffer to max_buffer_length.

        Args:
            new_symbols: New SAX symbols from incremental encoding.
            sax_buffer: Updated SAX partial window buffer.

        Returns:
            List of patterns that can be matched (length == pattern_length).
        """
        self._total_candles += 1
        self._sax_buffer = sax_buffer

        if not new_symbols:
            return []

        for sym in new_symbols:
            self._pattern_buffer.append(sym)
            self._symbol_counts[sym] += 1
            self._total_symbols += 1
            self._symbols_produced += 1
            self._last_symbol_time = time.time()

            # Record event
            if self.track_history:
                self._history.append(PatternEvent(
                    timestamp=self._last_symbol_time,
                    symbol=sym,
                    event_type="new_symbol",
                    pattern_snapshot=list(self._pattern_buffer[-self.pattern_length:]),
                ))

        # Auto-trim to max length
        self._trim()

        # Return matchable patterns
        matchable = []
        if len(self._pattern_buffer) >= self.pattern_length:
            matchable = self._pattern_buffer[-self.pattern_length:]
        return matchable

    def get_pattern(self, length: int = 0) -> list[str]:
        """
        Get the current pattern for Trie matching.

        Args:
            length: Pattern length. 0 = use default (self.pattern_length).

        Returns:
            List of SAX symbols of the requested length, or empty list
            if buffer doesn't have enough symbols.
        """
        pl = length or self.pattern_length
        if len(self._pattern_buffer) < pl:
            return []
        return self._pattern_buffer[-pl:]

    def has_pattern(self, length: int = 0) -> bool:
        """Check if buffer has enough symbols for a pattern."""
        pl = length or self.pattern_length
        return len(self._pattern_buffer) >= pl

    def record_match(self, confidence: float, matched_pattern: list[str],
                     metadata: Optional[dict] = None) -> None:
        """Record a successful pattern match event."""
        self._patterns_matched += 1
        if self.track_history:
            self._history.append(PatternEvent(
                timestamp=time.time(),
                symbol=matched_pattern[-1] if matched_pattern else "",
                event_type="pattern_match",
                pattern_snapshot=list(matched_pattern),
                confidence=confidence,
                metadata=metadata or {},
            ))

    def record_break(self, pattern: list[str], break_score: float = 0.0) -> None:
        """Record a pattern break event."""
        self._patterns_broken += 1
        if self.track_history:
            self._history.append(PatternEvent(
                timestamp=time.time(),
                symbol=pattern[-1] if pattern else "",
                event_type="pattern_break",
                pattern_snapshot=list(pattern),
                confidence=0.0,
                metadata={"break_score": break_score},
            ))

    # ================================================================
    # LIVING TRIE SUPPORT
    # ================================================================

    def get_recent_observations(self, n: int = 50) -> list[dict]:
        """
        Get recent observations for Living Trie updates.

        Returns a list of dicts with:
          - symbols: pattern snapshot
          - timestamp: when the pattern occurred
          - event_type: "pattern_match" or "new_symbol"

        Args:
            n: Maximum number of observations to return.

        Returns:
            List of observation dicts, most recent first.
        """
        observations = []
        for event in reversed(self._history):
            if event.pattern_snapshot and len(event.pattern_snapshot) >= self.pattern_length:
                observations.append({
                    "symbols": event.pattern_snapshot[-self.pattern_length:],
                    "timestamp": event.timestamp,
                    "event_type": event.event_type,
                    "confidence": event.confidence,
                })
            if len(observations) >= n:
                break
        return observations

    # ================================================================
    # INTERNAL
    # ================================================================

    def _trim(self) -> None:
        """Trim pattern buffer to max_buffer_length."""
        if len(self._pattern_buffer) > self.max_buffer_length:
            excess = len(self._pattern_buffer) - self.max_buffer_length
            del self._pattern_buffer[:excess]

    # ================================================================
    # SERIALIZATION
    # ================================================================

    def get_state(self) -> dict:
        """Serialize buffer state for persistence."""
        return {
            "pattern_buffer": list(self._pattern_buffer),
            "sax_buffer": list(self._sax_buffer) if self._sax_buffer else [],
            "symbols_produced": self._symbols_produced,
            "total_candles": self._total_candles,
            "patterns_matched": self._patterns_matched,
            "patterns_broken": self._patterns_broken,
            "symbol_counts": dict(self._symbol_counts),
        }

    def restore_state(self, state: dict) -> None:
        """Restore buffer state from persistence."""
        self._pattern_buffer = state.get("pattern_buffer", [])
        self._sax_buffer = state.get("sax_buffer", [])
        self._symbols_produced = state.get("symbols_produced", 0)
        self._total_candles = state.get("total_candles", 0)
        self._patterns_matched = state.get("patterns_matched", 0)
        self._patterns_broken = state.get("patterns_broken", 0)
        if "symbol_counts" in state:
            self._symbol_counts = Counter(state["symbol_counts"])
            self._total_symbols = sum(self._symbol_counts.values())

    # ================================================================
    # DISPLAY
    # ================================================================

    def format_summary(self) -> str:
        """Format a summary string for display."""
        current = " -> ".join(self._pattern_buffer[-self.pattern_length:]) if self.has_pattern() else "(insufficient data)"
        return (
            f"Buffer: {self.length} symbols | "
            f"Pattern: [{current}] | "
            f"Entropy: {self.entropy:.2f} bits | "
            f"Concentration: {self.symbol_concentration:.1%} | "
            f"Produced: {self._symbols_produced} | "
            f"Matched: {self._patterns_matched} | "
            f"Broken: {self._patterns_broken}"
        )


class MultiLevelStreamingBuffer:
    """
    Manages 4 parallel StreamingPatternBuffers for the 4-level Trie.

    FASE 1 Tarea 1.1: Each trie level has its own SAXEncoder with a
    different alphabet_size. This class wraps 4 StreamingPatternBuffers,
    one per level, and provides a unified interface for incremental
    encoding and pattern retrieval.

    Usage:
        from ppmt.core.sax import SAXEncoder, get_alpha_for_level

        sax_n1 = SAXEncoder(alphabet_size=get_alpha_for_level("n1", "blue_chip"))
        sax_n2 = SAXEncoder(alphabet_size=get_alpha_for_level("n2", "blue_chip"))
        sax_n3 = SAXEncoder(alphabet_size=4)
        sax_n4 = SAXEncoder(alphabet_size=4)

        mlb = MultiLevelStreamingBuffer(
            sax_encoders={"n1": sax_n1, "n2": sax_n2, "n3": sax_n3, "n4": sax_n4},
            pattern_length=5,
        )

        # Process a candle
        patterns = mlb.process_candle(candle_df)
        if patterns:
            result = engine.match(
                current_symbols=patterns["n3"],
                current_price=price,
                current_symbols_n1=patterns["n1"],
                current_symbols_n2=patterns["n2"],
                current_symbols_n4=patterns["n4"],
            )
    """

    def __init__(
        self,
        sax_encoders: dict[str, "SAXEncoder"],
        pattern_length: int = 5,
        max_buffer_length: int = 0,
        track_history: bool = True,
        max_history: int = 1000,
    ):
        """
        Initialize multi-level streaming buffer.

        Args:
            sax_encoders: Dict mapping level name ("n1","n2","n3","n4")
                to SAXEncoder instances. Each encoder has its own alphabet_size.
            pattern_length: Number of SAX symbols per pattern.
            max_buffer_length: Max symbols per buffer. 0 = auto.
            track_history: Whether to record pattern events.
            max_history: Max events per buffer.
        """
        self._encoders = sax_encoders
        self._buffers: dict[str, StreamingPatternBuffer] = {}
        for level in ("n1", "n2", "n3", "n4"):
            self._buffers[level] = StreamingPatternBuffer(
                pattern_length=pattern_length,
                max_buffer_length=max_buffer_length,
                track_history=track_history,
                max_history=max_history,
            )

    @property
    def n1(self) -> StreamingPatternBuffer:
        """N1 (Universal) buffer."""
        return self._buffers["n1"]

    @property
    def n2(self) -> StreamingPatternBuffer:
        """N2 (Asset Class) buffer."""
        return self._buffers["n2"]

    @property
    def n3(self) -> StreamingPatternBuffer:
        """N3 (Per-Token) buffer — primary, backwards compat."""
        return self._buffers["n3"]

    @property
    def n4(self) -> StreamingPatternBuffer:
        """N4 (Per-Token+Regime) buffer."""
        return self._buffers["n4"]

    def process_candle(
        self,
        candle_df: "pd.DataFrame",
        paa_mean: float | None = None,
        paa_std: float | None = None,
    ) -> dict[str, list[str]] | None:
        """
        Encode a new candle with all 4 SAX encoders and update buffers.

        Args:
            candle_df: Single-row DataFrame with OHLCV data.
            paa_mean: Training PAA mean for consistent z-scoring.
            paa_std: Training PAA std for consistent z-scoring.

        Returns:
            Dict mapping level names to pattern lists if N3 has a matchable
            pattern, None otherwise. E.g. {"n1": [...], "n2": [...],
            "n3": [...], "n4": [...]}. If a level's buffer doesn't have
            enough symbols yet, its value is an empty list.
        """
        for level in ("n1", "n2", "n3", "n4"):
            encoder = self._encoders[level]
            buf = self._buffers[level]

            new_symbols, updated_sax_buffer = encoder.encode_incremental(
                candle_df, buf.sax_buffer,
                paa_mean=paa_mean, paa_std=paa_std,
            )
            buf.update(new_symbols, updated_sax_buffer)

        # Return patterns only if N3 (primary) has a matchable pattern
        if not self._buffers["n3"].has_pattern():
            return None

        return {
            level: self._buffers[level].get_pattern()
            for level in ("n1", "n2", "n3", "n4")
        }

    def has_pattern(self) -> bool:
        """Check if N3 (primary) buffer has a matchable pattern."""
        return self._buffers["n3"].has_pattern()

    def get_patterns(self) -> dict[str, list[str]]:
        """Get current patterns from all levels (empty list if not enough symbols)."""
        return {
            level: self._buffers[level].get_pattern()
            for level in ("n1", "n2", "n3", "n4")
        }

    def record_match(self, confidence: float, matched_pattern: list[str],
                     metadata: Optional[dict] = None) -> None:
        """Record a match in the N3 (primary) buffer."""
        self._buffers["n3"].record_match(confidence, matched_pattern, metadata)

    def get_state(self) -> dict:
        """Serialize all buffer states for persistence."""
        return {
            level: self._buffers[level].get_state()
            for level in ("n1", "n2", "n3", "n4")
        }

    def restore_state(self, state: dict) -> None:
        """Restore all buffer states from persistence."""
        for level in ("n1", "n2", "n3", "n4"):
            if level in state:
                self._buffers[level].restore_state(state[level])

    def format_summary(self) -> str:
        """Format summary for all levels."""
        parts = []
        for level in ("n1", "n2", "n3", "n4"):
            buf = self._buffers[level]
            alpha = self._encoders[level].alphabet_size
            parts.append(f"  {level}(α={alpha}): {buf.format_summary()}")
        return "MultiLevelBuffer:\n" + "\n".join(parts)
