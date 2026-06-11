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
from ppmt.core.regime import RegimeDetector
from ppmt.engine.weights import AdaptiveWeights, LevelStats, WEIGHT_PROFILES
from ppmt.engine.signal import SignalGenerator, Signal, SignalType
from ppmt.engine.prediction import PredictionEngine


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

    def build(self, df: pd.DataFrame, pattern_length: int = 5,
             symbols: list[str] | None = None) -> int:
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

        v0.6.3: Added `symbols` parameter (V7.9 backport). When provided,
        uses pre-computed SAX symbols instead of re-encoding. This enables
        out-of-sample validation where training normalization stats are
        propagated to test encoding via encode_with_normalization().

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume
            pattern_length: Number of SAX blocks per pattern sequence
            symbols: Pre-computed SAX symbols (optional, v0.6.3). When None,
                     calls self.sax.encode(df) as before.

        Returns:
            Number of patterns inserted
        """
        # Encode entire history to SAX symbols
        if symbols is None:
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

        # V4: Pre-compute regime at each candle position using RegimeDetector
        # This enables storing the market regime in each node's metadata,
        # making the Trie regime-aware. Each pattern knows what regime
        # it was observed under, enabling regime-specific matching.
        regime_detector = RegimeDetector(lookback=50, vol_threshold=0.6, trend_threshold=0.005)
        regime_at_candle = ["ranging"] * len(close)  # default
        regime_conf_at_candle = [0.0] * len(close)
        for ci in range(50, len(close)):
            regime_prices = close[max(0, ci - 200):ci + 1]
            if len(regime_prices) >= 50:
                info = regime_detector.detect_detailed(regime_prices)
                regime_at_candle[ci] = info.regime
                regime_conf_at_candle[ci] = info.confidence

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
            # V4: Detect regime at this pattern's entry position and pass
            # it to insert_with_observations so each node stores what regime
            # it was observed under. This is the key V4 enhancement that
            # makes the Trie regime-aware.
            entry_candle = start_candle
            pattern_regime = regime_at_candle[entry_candle] if entry_candle < len(regime_at_candle) else "ranging"
            pattern_regime_conf = regime_conf_at_candle[entry_candle] if entry_candle < len(regime_conf_at_candle) else 0.0

            # V4.2: N1-N3 receive ALL patterns (universal/class/asset).
            # N4 (per_asset_regime) ONLY receives patterns that match the
            # current regime. This makes N4 truly regime-specific — when
            # you query N4 during trending_up, you only see patterns that
            # were observed during trending_up. Previously N4 was a duplicate
            # of N3 because it received all patterns regardless of regime.
            for trie in [self.trie_n1, self.trie_n2, self.trie_n3]:
                trie.insert_with_observations(
                    symbols=pattern,
                    move_pct=move_pct,
                    drawdown_pct=drawdown_pct,
                    favorable_pct=favorable_pct,
                    duration=duration,
                    won=won,
                    next_symbol=next_sym,
                    regime=pattern_regime,
                    regime_confidence=pattern_regime_conf,
                )
            # N4: only insert if this pattern's regime matches the trie's regime
            # The N4 trie name encodes the regime, e.g. "per_asset_regime:BTC/USDT:trending_up"
            # For build time, we insert ALL regimes into N4 (it stores regime-aware data)
            # but we tag each pattern with its regime so N4 matching can filter at query time.
            # NOTE: N4 receives all patterns with regime metadata, but during MATCHING
            # the paper trader/PredictionEngine filters by current_regime.
            self.trie_n4.insert_with_observations(
                symbols=pattern,
                move_pct=move_pct,
                drawdown_pct=drawdown_pct,
                favorable_pct=favorable_pct,
                duration=duration,
                won=won,
                next_symbol=next_sym,
                regime=pattern_regime,
                regime_confidence=pattern_regime_conf,
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

        # N4: regime-specific trie — V4.2: Apply regime_match_score to N4
        # confidence so that N4 patterns matching the current regime get a boost,
        # while mismatched regime patterns get penalized. Previously N4 returned
        # raw confidence without considering the current regime at all.
        n4_match = self.matcher.best_match(self.trie_n4, current_symbols)

        # Get confidence from each level
        n1_conf = n1_match.node.metadata.confidence if n1_match.node else 0.0
        n2_conf = n2_match.node.metadata.confidence if n2_match.node else 0.0
        n3_conf = n3_match.node.metadata.confidence if n3_match.node else 0.0
        n4_conf = n4_match.node.metadata.confidence if n4_match.node else 0.0

        # V4.2: Apply regime_match_score to N4 confidence
        # N4 is the per-asset-regime trie — its patterns are tagged with regimes.
        # When the current regime matches a pattern's dominant regime, boost N4 confidence.
        # When it doesn't match, penalize. This makes N4 actually regime-aware.
        if n4_conf > 0 and self._current_regime and n4_match.node:
            n4_regime_score = n4_match.node.metadata.regime_match_score(self._current_regime)
            n4_conf *= n4_regime_score

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

    def set_tries(
        self,
        trie_n1: PPMTTrie,
        trie_n2: PPMTTrie,
        trie_n3: PPMTTrie,
        trie_n4: PPMTTrie,
    ) -> None:
        """
        Inject pre-loaded tries into the engine.

        Used by PaperTrader when tries are loaded from storage
        separately (avoiding a redundant build step).

        Args:
            trie_n1: Universal trie
            trie_n2: Asset class trie
            trie_n3: Per-asset trie
            trie_n4: Per-asset+regime trie
        """
        self.trie_n1 = trie_n1
        self.trie_n2 = trie_n2
        self.trie_n3 = trie_n3
        self.trie_n4 = trie_n4

    def match_raw(
        self,
        current_symbols: list[str],
        current_price: float,
    ) -> PPMTResult:
        """
        Raw 4-level matching without signal generation.

        Returns the PPMTResult with all match details and weighted confidence,
        but does NOT apply SignalGenerator entry filters (min_confidence,
        min_risk_reward, etc.). The caller (PaperTrader) applies its own
        entry logic.

        This is the recommended method for PaperTrader integration —
        it provides the 4-level weighted confidence while letting the
        PaperTrader retain full control over entry/exit decisions.

        Args:
            current_symbols: Current SAX symbol sequence
            current_price: Current market price

        Returns:
            PPMTResult with match details and weighted_confidence
        """
        start_time = time.perf_counter()

        n1_match = self.matcher.best_match(self.trie_n1, current_symbols)
        n2_match = self.matcher.best_match(self.trie_n2, current_symbols)
        n3_match = self.matcher.best_match(self.trie_n3, current_symbols)
        n4_match = self.matcher.best_match(self.trie_n4, current_symbols)

        n1_conf = n1_match.node.metadata.confidence if n1_match.node else 0.0
        n2_conf = n2_match.node.metadata.confidence if n2_match.node else 0.0
        n3_conf = n3_match.node.metadata.confidence if n3_match.node else 0.0
        n4_conf = n4_match.node.metadata.confidence if n4_match.node else 0.0

        # V4.2: Apply regime_match_score to N4 confidence
        if n4_conf > 0 and self._current_regime and n4_match.node:
            n4_regime_score = n4_match.node.metadata.regime_match_score(self._current_regime)
            n4_conf *= n4_regime_score

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

    def bootstrap(
        self,
        df: pd.DataFrame,
        pattern_length: int = 5,
        bootstrap_ratio: float = 0.7,
        verbose: bool = True,
    ) -> dict:
        """
        Run a bootstrap paper trading pass on historical data.

        v0.4.0: After building the trie from historical patterns, automatically
        run a simplified paper trading simulation on a portion of the data.
        This accumulates trading observations in the N3 trie BEFORE the user
        runs `ppmt run`, giving fresh tries meaningful metadata from day one.

        The bootstrap uses the SAME SL/TP logic as PaperTrader to align metadata:
          LONG:  SL = max(ATR*1.5, 1.5%) cap 5%, TP = SL*2.0
          SHORT: SL = max(ATR*2.0, 2.0%) cap 7%, TP = SL*1.5

        The simulation is simplified compared to the full PaperTrader:
          - No risk management, no position sizing, no capital tracking
          - No catastrophic protection
          - SAX boundary SL/TP checking (like v0.2.8)
          - Trailing stop at 75% of TP distance
          - Pattern break grace = 2
          - Re-entry cooldown = 1
          - Living Trie = ON (recording observations)

        Only the N3 (per-asset) trie receives Living Trie treatment.
        N1, N2, N4 tries are NOT modified.

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume
            pattern_length: Number of SAX blocks per pattern sequence
            bootstrap_ratio: Fraction of data to use for bootstrap (0.7 = 70%)
            verbose: Whether to print progress

        Returns:
            Dict with bootstrap statistics:
            - trades: total number of simulated trades
            - winning_trades: number of winning trades
            - win_rate: win rate as fraction
            - observations_recorded: number of Living Trie observations
            - new_nodes_created: number of new trie nodes from pattern breaks
        """
        # Lazy imports to avoid circular dependency
        # (ppmt.py ← paper_trader.py ← ppmt.py)
        from ppmt.engine.paper_trader import PaperTrade, compute_atr_pct, _record_observation

        # Encode entire history to SAX symbols
        symbols = self.sax.encode(df)

        if len(symbols) < pattern_length + 1:
            if verbose:
                print(f"  Bootstrap: skipped (not enough SAX symbols: {len(symbols)})")
            return {"trades": 0, "winning_trades": 0, "win_rate": 0.0,
                    "observations_recorded": 0, "new_nodes_created": 0}

        # Compute ATR for SL/TP calculation
        atr_pct = compute_atr_pct(df, period=14)

        # Pre-extract price arrays for fast access
        df_close = df['close'].values.astype(float)
        df_high = df['high'].values.astype(float)
        df_low = df['low'].values.astype(float)

        # Create prediction engine using the N3 trie
        trie = self.trie_n3
        pred_engine = PredictionEngine(trie, prediction_depth=pattern_length)

        # Determine bootstrap boundary (in SAX symbol space)
        bootstrap_end_sym_idx = int(len(symbols) * bootstrap_ratio)

        # Warm-up offset: skip first few SAX symbols for warm-up
        start_sym_idx = pattern_length

        # Simulation state
        current_position = None  # PaperTrade when in position
        trade_counter = 0
        winning_trades = 0
        consecutive_breaks = 0
        last_losing_trade_sym_idx = -999
        observations_recorded = 0
        new_nodes_created = 0

        # Trailing stop state
        trailing_sl_pct = 0.0  # current trailing SL as distance from entry in %

        for sym_idx in range(start_sym_idx, bootstrap_end_sym_idx):
            # Candle range for this SAX symbol
            candle_start = sym_idx * self.sax.window_size
            candle_end = min((sym_idx + 1) * self.sax.window_size, len(df))
            last_candle_idx = candle_end - 1

            if last_candle_idx < 0 or last_candle_idx >= len(df_close):
                continue

            current_price = df_close[last_candle_idx]

            # Current SAX pattern
            current_symbols = symbols[sym_idx - pattern_length:sym_idx]

            # ============================================================
            # PHASE 1: SL/TP checking (SAX boundary, like v0.2.8)
            # ============================================================
            if current_position is not None:
                entry_price = current_position.entry_price
                direction = current_position.direction
                sl_price = current_position.sl_price
                tp_price = current_position.tp_price

                # Trailing stop update
                if tp_price is not None and entry_price is not None:
                    if direction == "LONG":
                        unrealized_pct = (current_price - entry_price) / entry_price * 100
                        tp_distance_pct = (tp_price - entry_price) / entry_price * 100
                    else:
                        unrealized_pct = (entry_price - current_price) / entry_price * 100
                        tp_distance_pct = (entry_price - tp_price) / entry_price * 100

                    # Trailing stop activates at 75% of TP distance
                    if not current_position.trailing_activated and tp_distance_pct > 0 and unrealized_pct >= tp_distance_pct * 0.75:
                        current_position.trailing_activated = True

                    if current_position.trailing_activated:
                        current_atr = atr_pct[last_candle_idx] if last_candle_idx < len(atr_pct) else 2.0
                        trailing_distance = current_atr * 1.5
                        if direction == "LONG":
                            new_sl = max(sl_price, current_price * (1 - trailing_distance / 100))
                        else:
                            new_sl = min(sl_price, current_price * (1 + trailing_distance / 100))
                        current_position.sl_price = new_sl
                        sl_price = new_sl

                # Check SL/TP at SAX boundary
                sl_hit = False
                tp_hit = False

                if direction == "LONG":
                    if current_price <= sl_price:
                        sl_hit = True
                    elif current_price >= tp_price:
                        tp_hit = True
                else:  # SHORT
                    if current_price >= sl_price:
                        sl_hit = True
                    elif current_price <= tp_price:
                        tp_hit = True

                if sl_hit or tp_hit:
                    # Close position
                    current_position.exit_price = current_price
                    if direction == "LONG":
                        current_position.pnl_pct = (current_price - entry_price) / entry_price * 100
                    else:
                        current_position.pnl_pct = (entry_price - current_price) / entry_price * 100
                    current_position.actual_move_pct = current_position.pnl_pct

                    if tp_hit:
                        current_position.exit_reason = "take_profit"
                    elif current_position.trailing_activated:
                        current_position.exit_reason = "trailing_stop"
                    else:
                        current_position.exit_reason = "stop_loss"

                    # Record observation via Living Trie mechanism
                    next_sym = symbols[sym_idx] if sym_idx < len(symbols) else None
                    obs_result = _record_observation(
                        trie, current_position, sym_idx, next_sym
                    )
                    observations_recorded += obs_result["observations"]
                    new_nodes_created += obs_result["new_nodes"]

                    if current_position.pnl_pct > 0:
                        winning_trades += 1
                    else:
                        last_losing_trade_sym_idx = sym_idx

                    trade_counter += 1
                    current_position = None
                    consecutive_breaks = 0
                    continue

            # ============================================================
            # PHASE 2: Pattern break check with grace period
            # ============================================================
            if current_position is not None and len(current_symbols) >= 2:
                pattern_to_check = current_symbols[:-1]
                latest_symbol = current_symbols[-1]
                continues, _ = trie.check_continuation(pattern_to_check, latest_symbol)

                if not continues and current_position.confidence > 0:
                    consecutive_breaks += 1
                    if consecutive_breaks >= 2:  # pattern_break_grace = 2
                        # Close position due to pattern break
                        entry_price = current_position.entry_price
                        direction = current_position.direction
                        if direction == "LONG":
                            current_position.pnl_pct = (current_price - entry_price) / entry_price * 100
                        else:
                            current_position.pnl_pct = (entry_price - current_price) / entry_price * 100
                        current_position.actual_move_pct = current_position.pnl_pct
                        current_position.exit_price = current_price
                        current_position.exit_reason = "pattern_break"

                        # Record observation with the break symbol as next_symbol
                        obs_result = _record_observation(
                            trie, current_position, sym_idx, latest_symbol
                        )
                        observations_recorded += obs_result["observations"]
                        new_nodes_created += obs_result["new_nodes"]

                        if current_position.pnl_pct > 0:
                            winning_trades += 1
                        else:
                            last_losing_trade_sym_idx = sym_idx

                        trade_counter += 1
                        current_position = None
                        consecutive_breaks = 0
                        continue
                else:
                    consecutive_breaks = 0

            # ============================================================
            # PHASE 3: Entry signal generation
            # ============================================================
            if current_position is None:
                # Re-entry cooldown = 1 symbol step
                if sym_idx - last_losing_trade_sym_idx < 1:
                    continue

                try:
                    prediction = pred_engine.predict(
                        current_symbols=current_symbols,
                        entry_price=current_price,
                        timeframe_hours=1,
                        symbol=self.symbol,
                    )
                except Exception:
                    continue

                # Entry conditions (v0.4.1→v0.11.0: bootstrap uses very loose thresholds
                # since the trie is being built from scratch. The whole PURPOSE of
                # bootstrap is to accumulate observations, so we must be very permissive.
                # v0.10.0's 0.10 threshold produced 0 trades because fresh tries with
                # historical_count=1 per node produce max confidence ~0.07 (due to
                # Bayesian shrinkage + dependency penalty). Lowering to 0.03 ensures
                # bootstrap can always generate observations on fresh tries.)
                if (prediction.direction == "FLAT"
                    or prediction.confidence <= 0
                    or prediction.confidence < 0.03  # v0.11.0: lowered from 0.10 → 0.03 for fresh tries
                    or abs(prediction.expected_total_move_pct) < 0.5  # v0.11.0: lowered from 1.0 → 0.5
                    or prediction.overall_probability <= 0.10):  # v0.11.0: lowered from 0.20 → 0.10
                    continue

                # SHORT requires slightly higher confidence but still very permissive
                # for bootstrap — the goal is to gather SHORT observations.
                effective_min_conf = 0.03
                if prediction.direction == "SHORT":
                    effective_min_conf = 0.04  # v0.11.0: minimal SHORT penalty for bootstrap

                if prediction.confidence < effective_min_conf:
                    continue

                # Direction-specific SL/TP (SAME as PaperTrader)
                current_atr_pct_val = atr_pct[last_candle_idx] if last_candle_idx < len(atr_pct) else 2.0

                if prediction.direction == "LONG":
                    sl_distance_pct = min(max(current_atr_pct_val * 1.5, 1.5), 5.0)
                    tp_distance_pct = sl_distance_pct * 2.0  # R:R = 2.0
                    sl_price = current_price * (1 - sl_distance_pct / 100)
                    tp_price = current_price * (1 + tp_distance_pct / 100)
                else:  # SHORT
                    sl_distance_pct = min(max(current_atr_pct_val * 2.0, 2.0), 7.0)
                    tp_distance_pct = sl_distance_pct * 1.5  # R:R = 1.5
                    sl_price = current_price * (1 + sl_distance_pct / 100)
                    tp_price = current_price * (1 - tp_distance_pct / 100)

                # Open position
                current_position = PaperTrade(
                    trade_id=trade_counter + 1,
                    symbol=self.symbol,
                    direction=prediction.direction,
                    entry_price=current_price,
                    exit_price=0.0,
                    confidence=prediction.confidence,
                    win_rate=prediction.overall_probability,
                    expected_move_pct=prediction.expected_total_move_pct,
                    matched_pattern=list(current_symbols),
                    sl_price=sl_price,
                    tp_price=tp_price,
                    entry_sym_idx=sym_idx,
                )

        # Close any open position at end of bootstrap
        if current_position is not None:
            entry_price = current_position.entry_price
            direction = current_position.direction
            last_price = df_close[min(bootstrap_end_sym_idx * self.sax.window_size - 1, len(df_close) - 1)]
            if direction == "LONG":
                current_position.pnl_pct = (last_price - entry_price) / entry_price * 100
            else:
                current_position.pnl_pct = (entry_price - last_price) / entry_price * 100
            current_position.actual_move_pct = current_position.pnl_pct
            current_position.exit_price = last_price
            current_position.exit_reason = "end_of_data"

            # Record final observation
            obs_result = _record_observation(
                trie, current_position, bootstrap_end_sym_idx, None
            )
            observations_recorded += obs_result["observations"]
            new_nodes_created += obs_result["new_nodes"]

            if current_position.pnl_pct > 0:
                winning_trades += 1
            trade_counter += 1

        # Re-propagate metadata so intermediate nodes are updated
        trie.propagate_metadata()

        # Compute results
        win_rate = winning_trades / trade_counter if trade_counter > 0 else 0.0

        result = {
            "trades": trade_counter,
            "winning_trades": winning_trades,
            "win_rate": win_rate,
            "observations_recorded": observations_recorded,
            "new_nodes_created": new_nodes_created,
        }

        if verbose:
            print(f"  Bootstrap: {trade_counter} trades simulated, "
                  f"WR {win_rate:.1%}, {observations_recorded} observations recorded")

        return result

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
