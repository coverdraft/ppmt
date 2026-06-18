"""
PPMT Engine - 4-Level Progressive Pattern Matching

The main engine that orchestrates:
  1. SAX symbolization of incoming OHLCV data
  2. Parallel search across 4 Trie levels
  3. Adaptive weight-based confidence computation
  4. Signal generation from Block Lifecycle Metadata

Architecture:
  ┌─────────────────────────────────┐
  │         OHLCV Data              │
  └────────────┬────────────────────┘
               │
        ┌──────▼──────┐
        │  SAX Encode  │
        └──────┬──────┘
               │
    ┌──────────┼──────────────────────┐
    │          │                      │
 ┌──▼──┐  ┌───▼───┐  ┌────▼────┐  ┌──▼───┐
 │ N1  │  │  N2   │  │   N3    │  │  N4  │
 │10%  │  │  30%  │  │  30%    │  │ 30%  │
 └──┬──┘  └───┬───┘  └────┬────┘  └──┬───┘
    │         │           │           │
    └─────────┼───────────┼───────────┘
              │           │
        ┌─────▼───────────▼──────┐
        │  Adaptive Weight Merge  │
        └───────────┬────────────┘
                    │
           ┌────────▼────────┐
           │ Signal Generator │
           └────────┬────────┘
                    │
           ┌────────▼────────┐
           │  Trading Signal  │
           └─────────────────┘
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie, TrieNode, RegimePartitionedTrie
from ppmt.core.matcher import FuzzyMatcher, MatchResult
from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt.core.regime import RegimeDetector
from ppmt.engine.weights import AdaptiveWeights, LevelStats, WEIGHT_PROFILES
from ppmt.engine.signal import SignalGenerator, Signal, SignalType


@dataclass
class PPMTResult:
    """Complete result from a PPMT pattern matching cycle."""

    signal: Signal
    """The generated trading signal."""

    n1_match: Optional[MatchResult] = None
    n2_match: Optional[MatchResult] = None
    n3_match: Optional[MatchResult] = None
    n4_match: Optional[MatchResult] = None

    n1_confidence: float = 0.0
    n2_confidence: float = 0.0
    n3_confidence: float = 0.0
    n4_confidence: float = 0.0

    weighted_confidence: float = 0.0
    sax_symbols: list[str] = field(default_factory=list)

    search_time_ms: float = 0.0
    """Time spent on pattern matching in milliseconds."""


class PPMT:
    """
    Progressive Pattern Matching Trie Engine.

    The main engine that coordinates 4 Trie levels with adaptive weights
    and generates autonomous trading signals from Block Lifecycle Metadata.

    Usage:
        engine = PPMT(symbol="BTC/USDT", asset_class="blue_chip")

        # Build from historical data
        engine.build(ohlcv_df)

        # Real-time pattern matching
        result = engine.match(current_sax_symbols, current_price)
        if result.signal.is_entry:
            # Execute entry with result.signal.sl_price, tp_price
            pass

    The engine is designed to run locally with all data stored in SQLite.
    It requires only an external Capital Risk Manager to decide position sizing.
    """

    def __init__(
        self,
        symbol: str,
        asset_class: str = "default",
        sax_alphabet_size: int = 8,
        sax_window_size: int = 10,
        sax_strategy: str = "ohlcv",
        fuzzy_threshold: float = 0.85,
        min_confidence: float = 0.60,
        min_risk_reward: float = 1.5,
        weight_profile: Optional[str] = None,
    ):
        self.symbol = symbol
        self.asset_class = asset_class

        # SAX encoder
        self.sax = SAXEncoder(
            alphabet_size=sax_alphabet_size,
            window_size=sax_window_size,
            strategy=sax_strategy,
        )

        # Fuzzy matcher
        self.matcher = FuzzyMatcher(
            sax_encoder=self.sax,
            threshold=fuzzy_threshold,
        )

        # Signal generator
        self.signal_generator = SignalGenerator(
            min_confidence=min_confidence,
            min_risk_reward=min_risk_reward,
        )

        # 4-Level Tries
        # v0.40.2 FIX-1: N4 is now a RegimePartitionedTrie — internally
        # maintains 4 sub-tries (one per regime). This breaks the
        # N1==N2==N3==N4 structural identity that CAPA 1 audit #3 found
        # was making the 4-level architecture purely decorative.
        # N1 (universal), N2 (asset_class), N3 (per_asset) stay as plain
        # PPMTTrie — they remain structurally identical in single-symbol
        # operation, but their *role* is differentiated when tries are
        # shared across PPMT instances via set_tries() (e.g., PaperTrader
        # loads N1 from a global pool, N2 from an asset_class pool, N3
        # from the per-symbol storage).
        self.trie_n1 = PPMTTrie(name=f"universal")
        self.trie_n2 = PPMTTrie(name=f"asset_class:{asset_class}")
        self.trie_n3 = PPMTTrie(name=f"per_asset:{symbol}")
        self.trie_n4 = RegimePartitionedTrie(name=f"per_asset_regime:{symbol}")

        # Adaptive weights
        if weight_profile:
            self.weights = AdaptiveWeights.from_profile(weight_profile)
        else:
            self.weights = AdaptiveWeights.from_profile(
                self._infer_weight_profile(asset_class)
            )

        # Incremental SAX buffer
        self._sax_buffer: list[float] = []

        # Current regime
        self._current_regime: Optional[str] = None

        # v0.38.8: RegimeDetector instance (used for detect_simple during
        # trie build). Auto-calibrated for crypto (vol=0.15, trend=0.001).
        # The detect_simple method uses RegimeThresholds.simple_vol_cutoff
        # (0.08) and simple_move_cutoff (0.02) — preserved verbatim from
        # the previous _detect_simple_regime static method.
        self.regime_detector = RegimeDetector()

        # Statistics
        self._total_patterns_built = 0

    @staticmethod
    def _infer_weight_profile(asset_class: str) -> str:
        """Infer the weight profile from asset class."""
        profile_map = {
            "blue_chip": "blue_chip",
            "large_cap": "default",
            "mid_cap": "default",
            "defi": "default",
            "meme": "meme",
            "new_launch": "new_launch",
        }
        return profile_map.get(asset_class, "default")

    @staticmethod
    def _detect_simple_regime(window_df: pd.DataFrame) -> str:
        """
        Detect simple market regime from a window of OHLCV data.

        v0.38.8: DEPRECATED — delegates to RegimeDetector.detect_simple().
        Kept as a thin static wrapper for backwards compatibility with
        any external callers. The thresholds (0.08 vol, 0.02 move) now
        live in RegimeThresholds (core/thresholds.py) and are preserved
        verbatim, so behaviour is identical to v0.38.7.

        New code should use:
            engine.regime_detector.detect_simple(window_df)
        instead of:
            PPMT._detect_simple_regime(window_df)

        Classification:
        - trending_up:   move > 0.02 (2%+ up)
        - trending_down: move < -0.02 (2%+ down)
        - volatile:      range/entry > 0.08 (8%+ range)
        - ranging:       none of the above
        """
        return RegimeDetector().detect_simple(window_df)

    def set_tries(
        self,
        trie_n1: PPMTTrie,
        trie_n2: PPMTTrie,
        trie_n3: PPMTTrie,
        trie_n4: PPMTTrie,
    ) -> None:
        """
        Inject pre-built Tries into the engine.

        Used by PaperTrader to load serialized Tries from storage
        instead of building new ones from scratch.

        Args:
            trie_n1: Universal Trie
            trie_n2: Asset Class Trie
            trie_n3: Per-Asset Trie
            trie_n4: Per-Asset+Regime Trie
        """
        self.trie_n1 = trie_n1
        self.trie_n2 = trie_n2
        self.trie_n3 = trie_n3
        self.trie_n4 = trie_n4

    def set_regime(self, regime: str) -> None:
        """Set the current market regime for N4 Trie selection.

        v0.40.2 FIX-1: N4 is now a RegimePartitionedTrie. Setting the
        regime routes ALL subsequent N4 search/match operations to the
        sub-trie for that regime. This is what makes N4 actually carry
        regime-specific information (vs. before, when N4 was a plain
        PPMTTrie with the same data as N1/N2/N3).
        """
        self._current_regime = regime
        # Propagate to N4's wrapper so search/match go to the right sub-trie
        if isinstance(self.trie_n4, RegimePartitionedTrie):
            self.trie_n4.set_current_regime(regime)

    def build(self, df: pd.DataFrame, pattern_length: int = 5) -> int:
        """
        Build the 4-level Trie from historical OHLCV data.

        Processes the DataFrame into SAX symbols, then creates
        overlapping pattern sequences and inserts them into all
        4 Trie levels with Block Lifecycle Metadata.

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume
            pattern_length: Number of SAX blocks per pattern sequence

        Returns:
            Number of patterns inserted
        """
        # Encode entire history to SAX symbols
        symbols = self.sax.encode(df)

        if len(symbols) < pattern_length:
            return 0

        # Create overlapping sequences
        count = 0
        for i in range(len(symbols) - pattern_length):
            pattern = symbols[i:i + pattern_length]
            next_sym = symbols[i + pattern_length] if i + pattern_length < len(symbols) else None

            # Compute metadata from the actual price data
            # Map SAX window indices to candle indices
            start_candle = i * self.sax.window_size
            end_candle = (i + pattern_length) * self.sax.window_size

            if end_candle > len(df):
                break

            window_df = df.iloc[start_candle:end_candle]

            # Compute move, drawdown, favorable from actual prices
            entry_price = window_df["close"].iloc[0]
            exit_price = window_df["close"].iloc[-1]
            move_pct = ((exit_price - entry_price) / entry_price) * 100.0

            high = window_df["high"].max()
            low = window_df["low"].min()
            drawdown_pct = ((low - entry_price) / entry_price) * 100.0
            favorable_pct = ((high - entry_price) / entry_price) * 100.0

            duration = len(window_df)
            won = move_pct > 0  # Simple: positive move = win

            # V4 FIX: Detect simple regime from price action for this window
            # This pipes regime into insert_with_observations (was dead code before)
            # v0.38.8: Now uses self.regime_detector.detect_simple() (delegates
            # to RegimeDetector with RegimeThresholds.simple_*_cutoff).
            regime = self.regime_detector.detect_simple(window_df)

            # v0.40.2 FIX-1: Differentiate the 4 tries structurally.
            #
            # BEFORE: `for trie in [N1, N2, N3, N4]: trie.insert_with_observations(...)`
            # inserted the SAME observation into all 4 tries → N1=N2=N3=N4
            # structurally (CAPA 1 audit #3).
            #
            # AFTER: N1/N2/N3 still receive the observation (they remain
            # structurally identical in single-symbol operation — that's
            # by design: when multiple PPMT instances share tries via
            # set_tries(), N1/N2/N3 are loaded from different storage
            # pools and ARE differentiated at runtime). N4 receives the
            # observation ONLY in the sub-trie for its regime. This makes
            # N4 truly regime-specialized: a `ranging` query will not see
            # observations made during `trending_up` and vice versa.
            for trie in [self.trie_n1, self.trie_n2, self.trie_n3]:
                trie.insert_with_observations(
                    symbols=pattern,
                    move_pct=move_pct,
                    drawdown_pct=drawdown_pct,
                    favorable_pct=favorable_pct,
                    duration=duration,
                    won=won,
                    next_symbol=next_sym,
                    regime=regime,
                )

            # N4: insert into the regime-matched sub-trie only.
            # RegimePartitionedTrie.insert_with_observations routes via
            # the `regime` kwarg internally.
            self.trie_n4.insert_with_observations(
                symbols=pattern,
                move_pct=move_pct,
                drawdown_pct=drawdown_pct,
                favorable_pct=favorable_pct,
                duration=duration,
                won=won,
                next_symbol=next_sym,
                regime=regime,
            )

            count += 1

        self._total_patterns_built += count
        return count

    def match_raw(
        self,
        current_symbols: list[str],
        current_price: float = 0.0,
    ) -> PPMTResult:
        """
        Raw 4-level match without signal generation.

        Used by PaperTrader to compute weighted confidence across all
        4 trie levels. Returns match results with confidence values
        but does NOT generate a trading signal (that's done by the
        PaperTrader's own entry logic).

        Args:
            current_symbols: Current SAX symbol sequence
            current_price: Current market price (unused, for compatibility)

        Returns:
            PPMTResult with match details and weighted confidence
        """
        start_time = time.perf_counter()

        # Search all 4 levels
        n1_match = self.matcher.best_match(self.trie_n1, current_symbols)
        n2_match = self.matcher.best_match(self.trie_n2, current_symbols)
        n3_match = self.matcher.best_match(self.trie_n3, current_symbols)
        n4_match = self.matcher.best_match(self.trie_n4, current_symbols)

        # Get confidence from each level
        n1_conf = n1_match.node.metadata.confidence if n1_match.node else 0.0
        n2_conf = n2_match.node.metadata.confidence if n2_match.node else 0.0
        n3_conf = n3_match.node.metadata.confidence if n3_match.node else 0.0
        n4_conf = n4_match.node.metadata.confidence if n4_match.node else 0.0

        # Compute weighted confidence
        weighted_conf = self.weights.compute_weighted_confidence(
            n1_confidence=n1_conf,
            n2_confidence=n2_conf,
            n3_confidence=n3_conf,
            n4_confidence=n4_conf,
        )

        search_time = (time.perf_counter() - start_time) * 1000.0

        return PPMTResult(
            signal=Signal(signal_type=SignalType.NO_SIGNAL, symbol=self.symbol),
            n1_match=n1_match,
            n2_match=n2_match,
            n3_match=n3_match,
            n4_match=n4_match,
            n1_confidence=n1_conf,
            n2_confidence=n2_conf,
            n3_confidence=n3_conf,
            n4_confidence=n4_conf,
            weighted_confidence=weighted_conf,
            sax_symbols=current_symbols,
            search_time_ms=search_time,
        )

    def match(
        self,
        current_symbols: list[str],
        current_price: float,
        is_in_position: bool = False,
        entry_price: Optional[float] = None,
    ) -> PPMTResult:
        """
        Match current SAX sequence against all 4 Trie levels.

        Performs parallel search across N1-N4, computes adaptive
        weighted confidence, and generates a trading signal.

        This is the main real-time method called on each new candle.

        Args:
            current_symbols: Current SAX symbol sequence
            current_price: Current market price
            is_in_position: Whether we already have an open position
            entry_price: Entry price of current position (if any)

        Returns:
            PPMTResult with signal and matching details
        """
        start_time = time.perf_counter()

        # Search all 4 levels
        n1_match = self.matcher.best_match(self.trie_n1, current_symbols)
        n2_match = self.matcher.best_match(self.trie_n2, current_symbols)
        n3_match = self.matcher.best_match(self.trie_n3, current_symbols)

        # N4: regime-specific trie
        n4_match = self.matcher.best_match(self.trie_n4, current_symbols)

        # Get confidence from each level
        n1_conf = n1_match.node.metadata.confidence if n1_match.node else 0.0
        n2_conf = n2_match.node.metadata.confidence if n2_match.node else 0.0
        n3_conf = n3_match.node.metadata.confidence if n3_match.node else 0.0
        n4_conf = n4_match.node.metadata.confidence if n4_match.node else 0.0

        # Compute weighted confidence
        weighted_conf = self.weights.compute_weighted_confidence(
            n1_confidence=n1_conf,
            n2_confidence=n2_conf,
            n3_confidence=n3_conf,
            n4_confidence=n4_conf,
        )

        # Determine best matching level
        best_level = "n1"
        best_conf = n1_conf
        best_match = n1_match

        for level_name, conf, match in [
            ("n2", n2_conf, n2_match),
            ("n3", n3_conf, n3_match),
            ("n4", n4_conf, n4_match),
        ]:
            if conf > best_conf:
                best_conf = conf
                best_level = level_name
                best_match = match

        # Generate signal
        signal: Signal

        if is_in_position and entry_price is not None:
            # Check continuation using ALL trie levels (not just n3)
            # v0.6.5: Fuzzy Pattern Break — graduated continuation across levels
            pnl_pct = ((current_price - entry_price) / entry_price) * 100.0

            last_sym = current_symbols[-1] if current_symbols else ""

            # Check continuation at all 4 levels, pick best break score
            cont_results = []
            for trie in [self.trie_n1, self.trie_n2, self.trie_n3, self.trie_n4]:
                cont = self.matcher.check_continuation(
                    trie, current_symbols[:-1], last_sym
                )
                cont_results.append(cont)

            # Select the continuation result with the highest pattern_break_score
            best_cont = max(cont_results, key=lambda c: c.pattern_break_score)

            signal = self.signal_generator.generate_continuation_signal(
                continuation_result=best_cont,
                current_price=current_price,
                entry_price=entry_price,
                current_pnl_pct=pnl_pct,
                symbol=self.symbol,
            )
        else:
            # Look for entry signal
            signal = self.signal_generator.generate_entry_signal(
                match_result=best_match,
                symbol=self.symbol,
                current_price=current_price,
                confidence=weighted_conf,
                trie_level=best_level,
            ) or Signal(signal_type=SignalType.NO_SIGNAL, symbol=self.symbol)

        search_time = (time.perf_counter() - start_time) * 1000.0

        return PPMTResult(
            signal=signal,
            n1_match=n1_match,
            n2_match=n2_match,
            n3_match=n3_match,
            n4_match=n4_match,
            n1_confidence=n1_conf,
            n2_confidence=n2_conf,
            n3_confidence=n3_conf,
            n4_confidence=n4_conf,
            weighted_confidence=weighted_conf,
            sax_symbols=current_symbols,
            search_time_ms=search_time,
        )

    def process_new_candle(
        self,
        candle_df: pd.DataFrame,
        current_price: float,
        is_in_position: bool = False,
        entry_price: Optional[float] = None,
        paa_mean: Optional[float] = None,
        paa_std: Optional[float] = None,
    ) -> Optional[PPMTResult]:
        """
        Process a single new candle through the SAX pipeline.

        Incrementally encodes the candle into SAX symbols and
        triggers pattern matching when a new symbol is produced.

        This is the primary method for real-time operation.

        v0.19.1: Fully implemented using StreamingPatternBuffer.
        The streaming buffer maintains a sliding window of SAX symbols
        and automatically provides the current pattern for matching.

        Args:
            candle_df: Single-row DataFrame with OHLCV data
            current_price: Current price (usually close)
            is_in_position: Whether we have an open position
            entry_price: Entry price of current position
            paa_mean: Training PAA mean for consistent incremental encoding
            paa_std: Training PAA std for consistent incremental encoding

        Returns:
            PPMTResult if a new SAX symbol was produced, None otherwise
        """
        from ppmt.engine.buffer import StreamingPatternBuffer

        # Initialize streaming buffer on first call
        if not hasattr(self, '_streaming_buffer') or self._streaming_buffer is None:
            self._streaming_buffer = StreamingPatternBuffer(
                pattern_length=5,  # default, should match build() pattern_length
                max_buffer_length=0,  # auto
            )

        buf = self._streaming_buffer

        # Incremental SAX encoding (v0.19.1: fixed z-score bug)
        new_symbols, updated_sax_buffer = self.sax.encode_incremental(
            candle_df, buf.sax_buffer,
            paa_mean=paa_mean, paa_std=paa_std,
        )

        # Update streaming buffer with new symbols
        matchable = buf.update(new_symbols, updated_sax_buffer)

        if not matchable or not buf.has_pattern():
            return None

        # Get current pattern for matching
        current_pattern = buf.get_pattern()

        # Run 4-level match
        result = self.match(
            current_symbols=current_pattern,
            current_price=current_price,
            is_in_position=is_in_position,
            entry_price=entry_price,
        )

        # Record match/break in buffer
        if result.signal.signal_type != SignalType.NO_SIGNAL:
            buf.record_match(
                confidence=result.weighted_confidence,
                matched_pattern=current_pattern,
            )

        return result

    def adapt_weights(self) -> None:
        """
        Adapt weights based on current data availability.

        Should be called periodically (e.g., every 100 new candles)
        to ensure weights reflect the current state of each Trie level.
        """
        stats = {}
        for key, trie in [
            ("n1", self.trie_n1),
            ("n2", self.trie_n2),
            ("n3", self.trie_n3),
            ("n4", self.trie_n4),
        ]:
            patterns = trie.get_all_patterns(min_count=1)
            if patterns:
                avg_count = np.mean([
                    node.metadata.historical_count for _, node in patterns
                ])
                avg_wr = np.mean([
                    node.metadata.win_rate for _, node in patterns
                ])
                avg_conf = np.mean([
                    node.metadata.confidence for _, node in patterns
                ])
            else:
                avg_count = 0.0
                avg_wr = 0.0
                avg_conf = 0.0

            stats[key] = LevelStats(
                pattern_count=trie.pattern_count,
                avg_historical_count=avg_count,
                avg_win_rate=avg_wr,
                avg_confidence=avg_conf,
            )

        self.weights.adapt(stats)

    def get_stats(self) -> dict:
        """Get engine statistics."""
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "weights": self.weights.to_dict(),
            "trie_n1": str(self.trie_n1),
            "trie_n2": str(self.trie_n2),
            "trie_n3": str(self.trie_n3),
            "trie_n4": str(self.trie_n4),
            "total_patterns_built": self._total_patterns_built,
            "current_regime": self._current_regime,
        }
