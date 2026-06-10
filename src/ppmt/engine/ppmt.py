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
from ppmt.core.trie import PPMTTrie, TrieNode
from ppmt.core.matcher import FuzzyMatcher, MatchResult
from ppmt.core.metadata import BlockLifecycleMetadata
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
        self.trie_n1 = PPMTTrie(name=f"universal")
        self.trie_n2 = PPMTTrie(name=f"asset_class:{asset_class}")
        self.trie_n3 = PPMTTrie(name=f"per_asset:{symbol}")
        self.trie_n4 = PPMTTrie(name=f"per_asset_regime:{symbol}")

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

    def set_regime(self, regime: str) -> None:
        """Set the current market regime for N4 Trie selection."""
        self._current_regime = regime

    def build(self, df: pd.DataFrame, pattern_length: int = 5) -> int:
        """
        Build the 4-level Trie from historical OHLCV data.

        Processes the DataFrame into SAX symbols, then creates
        overlapping pattern sequences and inserts them into all
        4 Trie levels with Block Lifecycle Metadata.

        v0.3.3: Uses trade-simulation "won" classification instead of
        the crude move_pct > 0. Now computes ATR at each position and
        classifies "won" based on whether the price would have reached
        the take-profit level before the stop-loss, exactly as the
        paper trader does. This aligns build-time win_rate with
        trading-time win_rate, producing more differentiated confidence
        scores across patterns.

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

        # Pre-compute ATR for trade-simulation "won" classification
        # ATR measures volatility — we use it to determine what SL/TP
        # would have been at each position, then check if the price
        # reached TP (won) or not.
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)
        close = df['close'].values.astype(float)
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - prev_close),
                np.abs(low - prev_close)
            )
        )
        atr = np.zeros_like(tr)
        period = 14
        if len(tr) >= period:
            atr[period - 1] = np.mean(tr[:period])
            for i in range(period, len(tr)):
                atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        atr_pct = np.where(close > 0, atr / close * 100, 0)

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

            win_high = window_df["high"].max()
            win_low = window_df["low"].min()
            drawdown_pct = ((win_low - entry_price) / entry_price) * 100.0
            favorable_pct = ((win_high - entry_price) / entry_price) * 100.0

            duration = len(window_df)

            # v0.3.3: Trade-simulation "won" classification
            # Instead of crude move_pct > 0 (which gives ~50% win_rate for
            # random data), simulate whether a trade would have hit TP.
            # This aligns build-time win_rate with trading-time reality.
            #
            # For LONG (move_pct > 0):
            #   SL = max(ATR*1.5, 1.5%) cap 5%, TP = SL*2.0
            #   won = favorable_pct >= TP_distance (would have hit LONG TP)
            #
            # For SHORT (move_pct <= 0):
            #   SL = max(ATR*2.0, 2.0%) cap 7%, TP = SL*1.5
            #   won = |drawdown_pct| >= TP_distance (would have hit SHORT TP)
            entry_candle_idx = start_candle
            atr_at_entry = atr_pct[entry_candle_idx] if entry_candle_idx < len(atr_pct) else 2.0

            if move_pct > 0:  # Bullish pattern → LONG trade simulation
                sl_dist = min(max(atr_at_entry * 1.5, 1.5), 5.0)
                tp_dist = sl_dist * 2.0  # R:R = 2.0
                won = favorable_pct >= tp_dist
            else:  # Bearish pattern → SHORT trade simulation
                sl_dist = min(max(atr_at_entry * 2.0, 2.0), 7.0)
                tp_dist = sl_dist * 1.5  # R:R = 1.5
                won = abs(drawdown_pct) >= tp_dist

            # Insert into all 4 levels
            for trie in [self.trie_n1, self.trie_n2, self.trie_n3, self.trie_n4]:
                trie.insert_with_observations(
                    symbols=pattern,
                    move_pct=move_pct,
                    drawdown_pct=drawdown_pct,
                    favorable_pct=favorable_pct,
                    duration=duration,
                    won=won,
                    next_symbol=next_sym,
                )

            count += 1

        # Propagate metadata from terminal nodes to intermediate nodes
        # This is critical: after building, only terminal nodes have metadata.
        # Propagation computes aggregate statistics for intermediate nodes
        # so that PredictionEngine can find meaningful data at any depth.
        for trie in [self.trie_n1, self.trie_n2, self.trie_n3, self.trie_n4]:
            trie.propagate_metadata()

        self._total_patterns_built += count
        return count

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
            # Check continuation
            pnl_pct = ((current_price - entry_price) / entry_price) * 100.0

            # Get continuation from best matching level
            last_sym = current_symbols[-1] if current_symbols else ""
            cont_result = self.matcher.check_continuation(
                self.trie_n3, current_symbols[:-1], last_sym
            )

            signal = self.signal_generator.generate_continuation_signal(
                continuation_result=cont_result,
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
    ) -> Optional[PPMTResult]:
        """
        Process a single new candle through the SAX pipeline.

        Incrementally encodes the candle into SAX symbols and
        triggers pattern matching when a new symbol is produced.

        This is the primary method for real-time operation.

        Args:
            candle_df: Single-row DataFrame with OHLCV data
            current_price: Current price (usually close)
            is_in_position: Whether we have an open position
            entry_price: Entry price of current position

        Returns:
            PPMTResult if a new SAX symbol was produced, None otherwise
        """
        new_symbols, self._sax_buffer = self.sax.encode_incremental(
            candle_df, self._sax_buffer
        )

        if not new_symbols:
            return None

        # We need a pattern of sufficient length
        # Keep track of all recent symbols
        # For simplicity, we'll need to maintain a sliding window
        # This would be enhanced with the streaming buffer
        # For now, return None — full implementation needs state
        return None  # TODO: Implement streaming pattern buffer

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
