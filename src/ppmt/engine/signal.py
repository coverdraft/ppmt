"""
Signal Generator - Trading Signals from Block Lifecycle Metadata

Generates entry/exit/hold signals directly from the PPMT Trie's
Block Lifecycle Metadata. No external indicators needed.

Signal Types:
  - ENTRY_LONG:  Pattern triggers bullish entry
  - ENTRY_SHORT: Pattern triggers bearish entry
  - EXIT:        Unknown block or pattern completion → exit
  - HOLD:        Pattern continues as expected
  - TRAILING:    Activate trailing stop (profit protection)

V3 Enhancement: Signals now carry full prediction data:
  - predicted_path: Most likely future SAX blocks
  - estimated_time: When the move should complete
  - quality_score: Composite metric for adaptive sizing
  - sizing_hint: Suggested position size multiplier
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt.core.matcher import MatchResult


class SignalType(Enum):
    """Trading signal types."""
    ENTRY_LONG = "ENTRY_LONG"
    ENTRY_SHORT = "ENTRY_SHORT"
    EXIT = "EXIT"
    HOLD = "HOLD"
    TRAILING = "TRAILING"
    NO_SIGNAL = "NO_SIGNAL"


@dataclass
class PredictionBlock:
    """
    A predicted future SAX block with its probability.

    Represents one step in the predicted future path.
    Each block carries the same metadata as a real block,
    but with probability estimates.
    """
    symbol: str
    """SAX symbol for this predicted block."""

    probability: float = 0.0
    """Probability of this block occurring (0-1)."""

    expected_move_pct: float = 0.0
    """Expected price move if this block occurs."""

    cumulative_probability: float = 0.0
    """Running probability from start to this block."""

    estimated_candles: int = 0
    """Estimated number of candles for this block."""

    metadata: Optional[BlockLifecycleMetadata] = None
    """Full metadata from the Trie node (if available)."""


@dataclass
class Signal:
    """
    A trading signal generated from PPMT pattern matching.

    V3 Enhancement: The signal carries complete prediction data,
    enabling the Risk Manager to make intelligent sizing decisions
    based on pattern quality, not just entry/exit.
    """

    signal_type: SignalType
    """Type of signal (entry, exit, hold, etc.)."""

    confidence: float = 0.0
    """Signal confidence (0-1). Weighted across all 4 Trie levels."""

    symbol: str = ""
    """Trading pair (e.g., 'BTC/USDT')."""

    entry_price: Optional[float] = None
    """Suggested entry price (from current market)."""

    sl_price: Optional[float] = None
    """Stop loss price level (from Block Lifecycle Metadata)."""

    tp_price: Optional[float] = None
    """Take profit price level (from Block Lifecycle Metadata)."""

    expected_move_pct: float = 0.0
    """Expected percentage move from Block Lifecycle Metadata."""

    max_drawdown_pct: float = 0.0
    """Maximum expected drawdown from metadata."""

    max_favorable_pct: float = 0.0
    """Maximum expected favorable excursion from metadata."""

    remaining_candles: int = 0
    """Predicted candles remaining in this pattern."""

    trigger_candle: int = 0
    """Which candle in the pattern triggers this signal."""

    risk_reward_ratio: float = 0.0
    """Risk:reward ratio computed from metadata."""

    win_rate: float = 0.0
    """Historical win rate for this pattern."""

    historical_count: int = 0
    """Number of historical observations for this pattern."""

    matched_pattern: list[str] = field(default_factory=list)
    """The SAX symbol sequence that matched."""

    trie_level: str = ""
    """Which Trie level generated the strongest signal."""

    is_fuzzy: bool = False
    """Whether this signal came from a fuzzy (non-exact) match."""

    unknown_block_exit: bool = False
    """Whether this exit was triggered by an unknown block."""

    timestamp: Optional[float] = None
    """Unix timestamp when the signal was generated."""

    # === V3 Prediction Fields ===

    predicted_path: list[PredictionBlock] = field(default_factory=list)
    """Most likely future SAX blocks with probabilities.
    This is the 'forward vision' of the pattern — what comes next
    and how likely each step is."""

    estimated_completion_time: Optional[float] = None
    """Estimated Unix timestamp when the pattern should complete.
    Computed from remaining_candles × timeframe."""

    # === V3 Quality & Sizing Fields ===

    quality_score: float = 0.0
    """
    Composite quality score (0-1) for adaptive position sizing.
    Combines: confidence × win_rate × risk_reward_bonus × sample_size_bonus
    This is what the Risk Manager uses to decide position size.
    """

    sizing_multiplier: float = 1.0
    """
    Position size multiplier based on quality_score.
    - quality_score > 0.8 → multiplier 2.0 (high conviction)
    - quality_score 0.6-0.8 → multiplier 1.0 (normal)
    - quality_score < 0.6 → multiplier 0.5 (low conviction)
    The Risk Manager uses this as: base_size × sizing_multiplier
    """

    # === V3: Metadata-Driven Sizing Signal ===

    probability_of_success: float = 0.0
    """
    Bayesian-adjusted probability that this pattern succeeds.
    Comes from BlockLifecycleMetadata.probability_of_success.
    The Money Manager reads this to gauge conviction.
    """

    expected_profit_ahead: float = 0.0
    """
    Expected profit % including losses (expected value).
    Comes from BlockLifecycleMetadata.expected_profit_ahead.
    Combines win_rate × expected_move + (1-win_rate) × max_drawdown.
    """

    metadata_sizing_signal: float = 0.0
    """
    Composite sizing signal from BlockLifecycleMetadata (0-2.0).
    This is the TIGHT INTEGRATION signal: the Trie's metadata
    directly tells the Risk Manager how much capital to allocate.
    
    Formula: 0.4×probability + 0.35×profit_score + 0.25×rr_score, scaled to 2.0
    
    Mapping:
      >= 1.5 → 2.0x base (high conviction)
      1.0-1.5 → 1.0x base (normal)
      0.5-1.0 → 0.5x base (low conviction)
      < 0.5  → 0.25x or reject
    """

    # === Computed Properties ===

    @property
    def is_entry(self) -> bool:
        return self.signal_type in (SignalType.ENTRY_LONG, SignalType.ENTRY_SHORT)

    @property
    def is_exit(self) -> bool:
        return self.signal_type in (SignalType.EXIT, SignalType.TRAILING)

    @property
    def direction(self) -> Optional[str]:
        if self.signal_type == SignalType.ENTRY_LONG:
            return "LONG"
        elif self.signal_type == SignalType.ENTRY_SHORT:
            return "SHORT"
        return None

    @property
    def predicted_path_symbols(self) -> list[str]:
        """Get just the symbols from the predicted path."""
        return [b.symbol for b in self.predicted_path]

    @property
    def path_probability(self) -> float:
        """Overall probability of the predicted path completing."""
        if not self.predicted_path:
            return 0.0
        return self.predicted_path[-1].cumulative_probability

    def compute_quality_score(self) -> float:
        """
        Compute composite quality score from signal metadata.

        This is the key metric for adaptive position sizing.
        High quality = high confidence + high win rate + good R:R + many observations.

        Formula:
          quality = confidence × (0.4 + 0.3 × win_rate + 0.2 × rr_bonus + 0.1 × sample_bonus)

        Where:
          - win_rate: directly from metadata (0-1)
          - rr_bonus: risk_reward_ratio normalized (cap at 5 → 1.0)
          - sample_bonus: log(count) / log(1000) → caps at 1.0 at 1000 observations
        """
        import numpy as np

        # Win rate component (0-1)
        wr = self.win_rate

        # Risk:reward bonus (normalize: RR of 5+ is max)
        rr_bonus = min(self.risk_reward_ratio / 5.0, 1.0)

        # Sample size bonus (more observations = more reliable)
        if self.historical_count > 0:
            sample_bonus = min(np.log1p(self.historical_count) / np.log(1000), 1.0)
        else:
            sample_bonus = 0.0

        # Composite
        quality = self.confidence * (0.4 + 0.3 * wr + 0.2 * rr_bonus + 0.1 * sample_bonus)
        return min(quality, 1.0)

    def compute_sizing_multiplier(
        self,
        min_multiplier: float = 0.25,
        max_multiplier: float = 3.0,
    ) -> float:
        """
        Compute position sizing multiplier from quality score.

        The multiplier scales the base position size:
          actual_size = base_size × multiplier

        Mapping:
          quality > 0.8  → 2.0x  (high conviction, size up)
          quality 0.6-0.8 → 1.0x  (normal)
          quality 0.4-0.6 → 0.5x  (low conviction, size down)
          quality < 0.4  → 0.25x  (very low, minimal exposure)
        """
        quality = self.quality_score if self.quality_score > 0 else self.compute_quality_score()

        if quality >= 0.8:
            multiplier = 2.0
        elif quality >= 0.6:
            multiplier = 1.0
        elif quality >= 0.4:
            multiplier = 0.5
        else:
            multiplier = 0.25

        return max(min_multiplier, min(max_multiplier, multiplier))

    def to_dict(self) -> dict:
        """Serialize signal to dictionary."""
        return {
            "signal_type": self.signal_type.value,
            "confidence": round(self.confidence, 4),
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "expected_move_pct": round(self.expected_move_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "max_favorable_pct": round(self.max_favorable_pct, 4),
            "remaining_candles": self.remaining_candles,
            "risk_reward_ratio": round(self.risk_reward_ratio, 4),
            "win_rate": round(self.win_rate, 4),
            "historical_count": self.historical_count,
            "matched_pattern": self.matched_pattern,
            "trie_level": self.trie_level,
            "is_fuzzy": self.is_fuzzy,
            "unknown_block_exit": self.unknown_block_exit,
            "quality_score": round(self.quality_score, 4),
            "sizing_multiplier": round(self.sizing_multiplier, 2),
            "probability_of_success": round(self.probability_of_success, 4),
            "expected_profit_ahead": round(self.expected_profit_ahead, 4),
            "metadata_sizing_signal": round(self.metadata_sizing_signal, 4),
            "predicted_path": [
                {
                    "symbol": b.symbol,
                    "probability": round(b.probability, 4),
                    "cumulative_probability": round(b.cumulative_probability, 4),
                    "expected_move_pct": round(b.expected_move_pct, 4),
                    "estimated_candles": b.estimated_candles,
                }
                for b in self.predicted_path
            ],
            "estimated_completion_time": self.estimated_completion_time,
        }


class SignalGenerator:
    """
    Generates trading signals from PPMT pattern matching results.

    V3 Enhancement: Signals now carry prediction data and quality scores,
    creating a rich communication channel between PPMT and the Risk Manager.

    The Risk Manager doesn't just see "buy/sell" — it sees:
    - How confident is this pattern?
    - What's the expected path ahead?
    - How much should we size this position?
    - When should it complete?

    This makes the PPMT-RiskManager integration fundamentally different
    from a traditional indicator → signal → fixed-size pipeline.
    """

    def __init__(
        self,
        min_confidence: float = 0.60,
        min_risk_reward: float = 1.5,
        unknown_block_exit: bool = True,
        trailing_activation_pct: float = 0.03,
        trailing_distance_pct: float = 0.015,
        prediction_depth: int = 5,
    ):
        self.min_confidence = min_confidence
        self.min_risk_reward = min_risk_reward
        self.unknown_block_exit = unknown_block_exit
        self.trailing_activation_pct = trailing_activation_pct
        self.trailing_distance_pct = trailing_distance_pct
        self.prediction_depth = prediction_depth

        # Regime-adaptive thresholds
        # In trending markets, we lower min_confidence to generate more signals
        # In ranging/volatile, we keep it high to avoid false signals
        self.regime_thresholds = {
            'TRENDING_UP': {'min_confidence': 0.45, 'min_risk_reward': 1.2},
            'TRENDING_DOWN': {'min_confidence': 0.45, 'min_risk_reward': 1.2},
            'RANGING': {'min_confidence': 0.60, 'min_risk_reward': 1.5},
            'VOLATILE': {'min_confidence': 0.55, 'min_risk_reward': 1.8},
            'UNKNOWN': {'min_confidence': 0.60, 'min_risk_reward': 1.5},
        }

    def get_adaptive_thresholds(self, regime_name: str = 'UNKNOWN') -> tuple[float, float]:
        """
        Get min_confidence and min_risk_reward adjusted for the current regime.

        Regime-adaptive signal thresholds:
        - TRENDING: Lower confidence (0.45) + lower R:R (1.2) → more signals
        - RANGING: Standard confidence (0.60) + standard R:R (1.5)
        - VOLATILE: Higher R:R (1.8) required → fewer but better signals

        This directly addresses the problem of too few signals in trending markets.
        """
        thresholds = self.regime_thresholds.get(regime_name, self.regime_thresholds['UNKNOWN'])
        return thresholds['min_confidence'], thresholds['min_risk_reward']

    def generate_prediction_path(
        self,
        node: "TrieNode",
        current_prob: float = 1.0,
        depth: int = 0,
    ) -> list[PredictionBlock]:
        """
        Generate predicted future path from a Trie node.

        Walks the Trie forward, choosing the highest-probability
        continuation at each step. Each step's probability is
        conditioned on the previous steps occurring.

        Args:
            node: Current Trie node
            current_prob: Running probability
            depth: Current prediction depth
        """
        from ppmt.core.trie import TrieNode

        if depth >= self.prediction_depth or node is None:
            return []

        predictions = []

        # Get all children sorted by historical count (most frequent first)
        children = sorted(
            node.children.items(),
            key=lambda x: x[1].metadata.historical_count if x[1].metadata else 0,
            reverse=True,
        )

        if not children:
            return []

        # Take the most likely continuation
        total_count = sum(
            c.metadata.historical_count for _, c in children if c.metadata
        )

        if total_count == 0:
            return []

        # Generate predictions for top continuations
        for sym, child in children[:3]:  # Top 3 continuations
            child_count = child.metadata.historical_count if child.metadata else 0
            prob = (child_count / total_count) * current_prob
            cum_prob = prob

            pred = PredictionBlock(
                symbol=sym,
                probability=child_count / total_count,
                cumulative_probability=cum_prob,
                expected_move_pct=child.metadata.expected_move_pct if child.metadata else 0.0,
                estimated_candles=child.metadata.avg_duration if child.metadata else 0,
                metadata=child.metadata if child.metadata else None,
            )

            # Recurse for the most likely path
            if depth < self.prediction_depth - 1:
                sub_path = self.generate_prediction_path(child, cum_prob, depth + 1)
                predictions.append(pred)
                predictions.extend(sub_path)
                break  # Only follow the most likely path for deep prediction
            else:
                predictions.append(pred)

        return predictions

    def generate_entry_signal(
        self,
        match_result: MatchResult,
        symbol: str,
        current_price: float,
        confidence: float,
        trie_level: str = "",
        regime_name: str = "UNKNOWN",
    ) -> Optional[Signal]:
        """
        Generate an entry signal from a pattern match.

        Entry conditions:
        1. Match confidence >= min_confidence (regime-adaptive)
        2. Expected move is significant enough
        3. Risk:reward ratio meets minimum threshold (regime-adaptive)
        4. Sufficient historical observations

        V3: Also generates prediction path and quality score.
        V4: Regime-adaptive thresholds — trending markets need lower confidence.
        """
        if not match_result.matched or match_result.node is None:
            return None

        meta = match_result.node.metadata

        # Get regime-adaptive thresholds
        adaptive_min_conf, adaptive_min_rr = self.get_adaptive_thresholds(regime_name)

        # Check minimum confidence (regime-adaptive)
        if confidence < adaptive_min_conf:
            return None

        # Check sufficient observations
        if meta.historical_count < 3:
            return None

        # Determine direction from expected move
        if abs(meta.expected_move_pct) < 0.5:
            return None

        signal_type = (
            SignalType.ENTRY_LONG if meta.expected_move_pct > 0
            else SignalType.ENTRY_SHORT
        )

        # Compute SL/TP from metadata
        meta.compute_sl_tp(current_price)

        # Check risk:reward (regime-adaptive)
        if meta.risk_reward_ratio < adaptive_min_rr:
            return None

        # Generate prediction path
        predicted_path = self.generate_prediction_path(match_result.node)

        # Create signal with all metadata
        signal = Signal(
            signal_type=signal_type,
            confidence=confidence,
            symbol=symbol,
            entry_price=current_price,
            sl_price=meta.sl_price,
            tp_price=meta.tp_price,
            expected_move_pct=meta.expected_move_pct,
            max_drawdown_pct=meta.max_drawdown_pct,
            max_favorable_pct=meta.max_favorable_pct,
            remaining_candles=meta.remaining_candles,
            trigger_candle=meta.trigger_candle,
            risk_reward_ratio=meta.risk_reward_ratio,
            win_rate=meta.win_rate,
            historical_count=meta.historical_count,
            matched_pattern=match_result.symbols,
            trie_level=trie_level,
            is_fuzzy=not match_result.is_exact,
            unknown_block_exit=False,
            predicted_path=predicted_path,
        )

        # Compute quality score and sizing multiplier
        signal.quality_score = signal.compute_quality_score()
        signal.sizing_multiplier = signal.compute_sizing_multiplier()

        # V3: Pass metadata-driven sizing signal from BlockLifecycleMetadata
        signal.probability_of_success = meta.probability_of_success
        signal.expected_profit_ahead = meta.expected_profit_ahead
        signal.metadata_sizing_signal = meta.sizing_signal

        return signal

    def generate_continuation_signal(
        self,
        continuation_result: MatchResult,
        current_price: float,
        entry_price: float,
        current_pnl_pct: float,
        symbol: str = "",
    ) -> Signal:
        """
        Generate a continuation signal based on pattern state.

        v0.6.5: Fuzzy Pattern Break — graduated decisions based on
        pattern_break_score instead of binary HOLD/EXIT:
          - break_score >= 0.7: HOLD (pattern continues confidently)
          - break_score 0.4-0.7: TRAILING (pattern weakening)
          - break_score < 0.4: EXIT (pattern broken)

        This prevents premature exits on noisy continuation symbols
        while still exiting when the pattern truly breaks down.
        """
        break_score = continuation_result.pattern_break_score

        # Unknown block → check break score for graduated exit
        if continuation_result.unknown_block or not continuation_result.matched:
            meta = continuation_result.node.metadata if continuation_result.node else None

            # Graduated decision based on pattern break score
            if break_score >= 0.4:
                # Pattern weakening but not broken → TRAILING stop
                # Protect profits while giving the pattern a chance
                trailing_sl = current_price * (1 - self.trailing_distance_pct / 100.0)
                return Signal(
                    signal_type=SignalType.TRAILING,
                    confidence=0.7 + 0.2 * break_score,  # 0.7-0.9 based on break score
                    symbol=symbol,
                    sl_price=trailing_sl,
                    tp_price=meta.tp_price if meta else None,
                    unknown_block_exit=False,  # Not a full exit — trailing
                    is_fuzzy=True,
                    quality_score=break_score,
                    sizing_multiplier=0.5 if break_score < 0.6 else 1.0,
                )

            # Pattern truly broken → EXIT
            if current_pnl_pct >= self.trailing_activation_pct:
                trailing_sl = current_price * (1 - self.trailing_distance_pct / 100.0)
                return Signal(
                    signal_type=SignalType.TRAILING,
                    confidence=0.9,
                    symbol=symbol,
                    sl_price=trailing_sl,
                    tp_price=None,
                    unknown_block_exit=True,
                    is_fuzzy=continuation_result.is_exact is False,
                    quality_score=0.9,
                    sizing_multiplier=0.0,
                )

            sl = meta.sl_price if meta else entry_price * 0.97
            return Signal(
                signal_type=SignalType.EXIT,
                confidence=0.9,
                symbol=symbol,
                sl_price=sl,
                unknown_block_exit=True,
                quality_score=break_score,
                sizing_multiplier=0.0,
            )

        # Pattern continues → check if fuzzy or exact, adjust confidence
        if continuation_result.matched and continuation_result.node:
            meta = continuation_result.node.metadata

            # Generate prediction path for hold signal
            predicted_path = self.generate_prediction_path(continuation_result.node)

            # Fuzzy continuation → lower confidence, may trigger trailing
            is_fuzzy_cont = not continuation_result.is_exact
            effective_confidence = meta.confidence
            if is_fuzzy_cont:
                # Reduce confidence for fuzzy matches
                effective_confidence *= continuation_result.similarity

            if current_pnl_pct >= self.trailing_activation_pct:
                trailing_sl = current_price * (1 - self.trailing_distance_pct / 100.0)
                signal = Signal(
                    signal_type=SignalType.TRAILING,
                    confidence=effective_confidence,
                    symbol=symbol,
                    sl_price=trailing_sl,
                    tp_price=meta.tp_price,
                    remaining_candles=meta.remaining_candles,
                    win_rate=meta.win_rate,
                    matched_pattern=continuation_result.symbols,
                    expected_move_pct=meta.expected_move_pct,
                    historical_count=meta.historical_count,
                    risk_reward_ratio=meta.risk_reward_ratio,
                    predicted_path=predicted_path,
                    is_fuzzy=is_fuzzy_cont,
                )
                signal.quality_score = signal.compute_quality_score()
                return signal

            signal = Signal(
                signal_type=SignalType.HOLD,
                confidence=effective_confidence,
                symbol=symbol,
                sl_price=meta.sl_price,
                tp_price=meta.tp_price,
                expected_move_pct=meta.expected_move_pct,
                remaining_candles=meta.remaining_candles,
                win_rate=meta.win_rate,
                matched_pattern=continuation_result.symbols,
                historical_count=meta.historical_count,
                risk_reward_ratio=meta.risk_reward_ratio,
                max_drawdown_pct=meta.max_drawdown_pct,
                max_favorable_pct=meta.max_favorable_pct,
                predicted_path=predicted_path,
                is_fuzzy=is_fuzzy_cont,
            )
            signal.quality_score = signal.compute_quality_score()
            signal.sizing_multiplier = signal.compute_sizing_multiplier()
            return signal

        return Signal(signal_type=SignalType.NO_SIGNAL, symbol=symbol)
