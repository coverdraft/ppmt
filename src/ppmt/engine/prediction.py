"""
Prediction Engine - Forward-looking pattern prediction

Generates visual and structured predictions from the PPMT Trie,
showing the most likely future path with estimated timing,
probability, and price targets.

This enables manual oversight while the system runs autonomously.
A trader can see what PPMT expects to happen next.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ppmt.core.trie import PPMTTrie, TrieNode
from ppmt.core.metadata import BlockLifecycleMetadata


@dataclass
class PathStep:
    """One step in a predicted future path."""
    block_index: int
    """Step number in the prediction."""

    symbol: str
    """SAX symbol for this step."""

    probability: float = 0.0
    """Probability of reaching this step from the current position."""

    cumulative_probability: float = 0.0
    """Overall probability of the path up to this step."""

    expected_move_pct: float = 0.0
    """Expected price move at this step."""

    cumulative_move_pct: float = 0.0
    """Running expected move from entry to this step."""

    estimated_candles: int = 0
    """Estimated candles for this block."""

    total_candles_remaining: int = 0
    """Total candles remaining from current position to this step."""

    win_rate: float = 0.0
    """Win rate at this step."""

    is_continuation: bool = True
    """Whether this is a known continuation (False = pattern break)."""


@dataclass
class Prediction:
    """
    Complete prediction result from PPMT.

    Shows the most likely future path, alternative paths,
    and key statistics for manual oversight.
    """
    symbol: str = ""
    """Trading pair."""

    current_pattern: list[str] = field(default_factory=list)
    """Current observed SAX sequence."""

    predicted_path: list[PathStep] = field(default_factory=list)
    """Most likely future path."""

    alternative_paths: list[list[PathStep]] = field(default_factory=list)
    """Less likely alternative paths (top 2-3)."""

    overall_probability: float = 0.0
    """Probability of the main predicted path completing."""

    expected_total_move_pct: float = 0.0
    """Expected total move from entry to end of prediction."""

    total_estimated_candles: int = 0
    """Total estimated candles for the predicted move."""

    estimated_time_hours: float = 0.0
    """Estimated time in hours for the predicted move."""

    direction: str = "FLAT"
    """Predicted direction: LONG, SHORT, or FLAT."""

    confidence: float = 0.0
    """Overall confidence in the prediction."""

    pattern_break_probability: float = 0.0
    """Probability that the pattern breaks before completing."""

    # Price levels (if entry price provided)
    entry_price: Optional[float] = None
    predicted_target: Optional[float] = None
    predicted_sl: Optional[float] = None

    def format_summary(self, timeframe_hours: float = 1.0) -> str:
        """
        Format prediction as a human-readable summary.

        Args:
            timeframe_hours: Hours per candle (1h=1, 4h=4, 15m=0.25)
        """
        lines = []
        lines.append(f"╔══════════════════════════════════════════════════╗")
        lines.append(f"║  PPMT PREDICTION: {self.symbol:<32}║")
        lines.append(f"╠══════════════════════════════════════════════════╣")

        # Current pattern
        pattern_str = " → ".join(self.current_pattern[-5:]) if self.current_pattern else "N/A"
        lines.append(f"║  Current:  {pattern_str:<37}║")

        # Predicted path
        if self.predicted_path:
            future_str = " → ".join([s.symbol for s in self.predicted_path])
            lines.append(f"║  Predict:  {future_str:<37}║")

        # Direction & move
        arrow = "▲" if self.direction == "LONG" else "▼" if self.direction == "SHORT" else "►"
        lines.append(f"║  Direction: {arrow} {self.direction:<35}║")
        lines.append(f"║  Expected Move: {self.expected_total_move_pct:>+6.2f}%{' ' * 27}║")
        lines.append(f"║  Probability:   {self.overall_probability:>6.1%}{' ' * 27}║")
        lines.append(f"║  Confidence:    {self.confidence:>6.1%}{' ' * 27}║")

        # Timing
        total_hours = self.total_estimated_candles * timeframe_hours
        lines.append(f"║  Est. Candles:  {self.total_estimated_candles:>6}{' ' * 27}║")
        lines.append(f"║  Est. Time:     {total_hours:>6.1f}h{' ' * 27}║")

        # Risk
        lines.append(f"║  Break Prob:    {self.pattern_break_probability:>6.1%}{' ' * 27}║")

        # Price levels
        if self.entry_price:
            lines.append(f"╠══════════════════════════════════════════════════╣")
            lines.append(f"║  Entry:   ${self.entry_price:>12,.2f}{' ' * 22}║")
            if self.predicted_target:
                lines.append(f"║  Target:  ${self.predicted_target:>12,.2f}{' ' * 22}║")
            if self.predicted_sl:
                lines.append(f"║  Stop:    ${self.predicted_sl:>12,.2f}{' ' * 22}║")

        # Path detail
        if self.predicted_path:
            lines.append(f"╠══════════════════════════════════════════════════╣")
            lines.append(f"║  Path Detail:                                    ║")
            for step in self.predicted_path:
                marker = "✓" if step.is_continuation else "✗"
                lines.append(
                    f"║  {marker} [{step.symbol}] "
                    f"prob={step.probability:.0%} "
                    f"move={step.cumulative_move_pct:>+5.2f}% "
                    f"candles={step.total_candles_remaining:<4} "
                    f"wr={step.win_rate:.0%}{' ' * 6}║"
                )

        lines.append(f"╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)


class PredictionEngine:
    """
    Generates forward-looking predictions from PPMT Trie.

    Walks the Trie from the current position and builds the most
    likely future path with probabilities, timing, and price levels.

    This is the 'crystal ball' of PPMT — it shows what the Trie
    expects to happen, based on millions of historical patterns.

    Usage:
        engine = PredictionEngine(trie)
        prediction = engine.predict(
            current_symbols=['a', 'd', 'b'],
            entry_price=100000.0,
            timeframe_hours=1.0,
        )
        print(prediction.format_summary())
    """

    def __init__(
        self,
        trie: PPMTTrie,
        prediction_depth: int = 5,
        max_alternatives: int = 2,
    ):
        self.trie = trie
        self.prediction_depth = prediction_depth
        self.max_alternatives = max_alternatives

    def predict(
        self,
        current_symbols: list[str],
        entry_price: Optional[float] = None,
        timeframe_hours: float = 1.0,
        symbol: str = "",
    ) -> Prediction:
        """
        Generate a prediction from the current pattern position.

        Args:
            current_symbols: Current SAX symbol sequence
            entry_price: Current price (for price level estimates)
            timeframe_hours: Hours per candle
            symbol: Trading pair name
        """
        # Find current position in Trie
        node = self.trie.search(current_symbols)

        if node is None:
            # Try prefix match
            node, depth = self.trie.search_prefix(current_symbols)
            if node is None:
                return Prediction(
                    symbol=symbol,
                    current_pattern=current_symbols,
                    direction="FLAT",
                    confidence=0.0,
                )

        # Build main predicted path
        main_path = self._walk_path(node, current_prob=1.0, depth=self.prediction_depth)

        # Build alternative paths
        alternatives = self._build_alternatives(node, max_paths=self.max_alternatives)

        # Compute overall stats
        overall_prob = 0.0
        total_move = 0.0
        total_candles = 0
        direction = "FLAT"
        pattern_break_prob = 0.0

        if main_path:
            overall_prob = main_path[-1].cumulative_probability if main_path else 0.0
            total_move = main_path[-1].cumulative_move_pct if main_path else 0.0
            total_candles = main_path[-1].total_candles_remaining if main_path else 0
            direction = "LONG" if total_move > 0.5 else "SHORT" if total_move < -0.5 else "FLAT"

            # Pattern break probability = 1 - probability of any continuation
            if node.metadata.historical_count > 0:
                continuation_count = sum(
                    child.metadata.historical_count
                    for child in node.children.values()
                    if child.metadata
                )
                pattern_break_prob = 1.0 - (continuation_count / node.metadata.historical_count)
            else:
                pattern_break_prob = 1.0

        # Compute price levels
        predicted_target = None
        predicted_sl = None
        if entry_price and total_move != 0:
            predicted_target = entry_price * (1 + total_move / 100.0)
            if node.metadata.max_drawdown_pct != 0:
                predicted_sl = entry_price * (1 + node.metadata.max_drawdown_pct / 100.0)

        # Confidence from metadata
        confidence = node.metadata.confidence if node.metadata else 0.0

        return Prediction(
            symbol=symbol,
            current_pattern=current_symbols,
            predicted_path=main_path,
            alternative_paths=alternatives,
            overall_probability=overall_prob,
            expected_total_move_pct=total_move,
            total_estimated_candles=total_candles,
            estimated_time_hours=total_candles * timeframe_hours,
            direction=direction,
            confidence=confidence,
            pattern_break_probability=pattern_break_prob,
            entry_price=entry_price,
            predicted_target=predicted_target,
            predicted_sl=predicted_sl,
        )

    def _walk_path(
        self,
        node: TrieNode,
        current_prob: float = 1.0,
        depth: int = 5,
        cumulative_move: float = 0.0,
        cumulative_candles: int = 0,
    ) -> list[PathStep]:
        """Walk the most likely path forward from a node."""
        steps = []

        if depth <= 0 or node is None:
            return steps

        # Get children sorted by count (most likely first)
        children = sorted(
            node.children.items(),
            key=lambda x: x[1].metadata.historical_count if x[1].metadata else 0,
            reverse=True,
        )

        if not children:
            return steps

        # Total count for probability calculation
        total_count = sum(
            c.metadata.historical_count for _, c in children if c.metadata
        )
        if total_count == 0:
            return steps

        # Take the most likely continuation
        sym, child = children[0]
        child_meta = child.metadata
        child_count = child_meta.historical_count if child_meta else 0

        step_prob = child_count / total_count
        cum_prob = current_prob * step_prob
        step_move = child_meta.expected_move_pct if child_meta else 0.0
        step_candles = child_meta.avg_duration if child_meta else 0
        cum_move = cumulative_move + step_move
        cum_candles = cumulative_candles + step_candles
        wr = child_meta.win_rate if child_meta else 0.0

        step = PathStep(
            block_index=len(steps) + 1,
            symbol=sym,
            probability=step_prob,
            cumulative_probability=cum_prob,
            expected_move_pct=step_move,
            cumulative_move_pct=cum_move,
            estimated_candles=step_candles,
            total_candles_remaining=cum_candles,
            win_rate=wr,
            is_continuation=True,
        )
        steps.append(step)

        # Recurse
        sub_steps = self._walk_path(child, cum_prob, depth - 1, cum_move, cum_candles)
        steps.extend(sub_steps)

        return steps

    def _build_alternatives(
        self,
        node: TrieNode,
        max_paths: int = 2,
    ) -> list[list[PathStep]]:
        """Build alternative (less likely) predicted paths."""
        alternatives = []

        children = sorted(
            node.children.items(),
            key=lambda x: x[1].metadata.historical_count if x[1].metadata else 0,
            reverse=True,
        )

        if len(children) < 2:
            return alternatives

        total_count = sum(
            c.metadata.historical_count for _, c in children if c.metadata
        )
        if total_count == 0:
            return alternatives

        # Skip the first (most likely) — already in main path
        for sym, child in children[1:1 + max_paths]:
            child_meta = child.metadata
            child_count = child_meta.historical_count if child_meta else 0

            step_prob = child_count / total_count
            step_move = child_meta.expected_move_pct if child_meta else 0.0
            step_candles = child_meta.avg_duration if child_meta else 0
            wr = child_meta.win_rate if child_meta else 0.0

            step = PathStep(
                block_index=1,
                symbol=sym,
                probability=step_prob,
                cumulative_probability=step_prob,
                expected_move_pct=step_move,
                cumulative_move_pct=step_move,
                estimated_candles=step_candles,
                total_candles_remaining=step_candles,
                win_rate=wr,
                is_continuation=True,
            )

            # Walk one more step for alternatives
            sub_steps = self._walk_path(child, step_prob, depth=2, cumulative_move=step_move, cumulative_candles=step_candles)
            path = [step] + sub_steps
            alternatives.append(path)

        return alternatives
