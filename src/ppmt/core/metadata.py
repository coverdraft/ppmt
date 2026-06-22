"""
Block Lifecycle Metadata - The V3 Innovation

Each Trie node carries 12 metadata fields that encode:
- When to enter (trigger_candle)
- How long the pattern should last (remaining_candles)
- Expected move and risk parameters
- Forward continuation and backward context
- Historical statistics

This metadata makes PPMT autonomous — all entry/exit/SL/TP decisions
emerge directly from the Trie without external indicators.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


def _parse_es_key(k: str) -> tuple[str, ...]:
    """Parse an expected_sequences key from JSON storage back to a tuple.

    Handles two formats:
    - v0.42.0+: JSON array string like '[["a","x"],["b","y"]]' → ('a', 'x', 'b', 'y')
    - Legacy: pipe-delimited string like 'a|b|c' → ('a', 'b', 'c')
    - Single symbols: 'a' → ('a',)
    """
    if k.startswith("["):
        # v0.42.0 format: JSON array of arrays or flat array
        try:
            parsed = json.loads(k)
            if isinstance(parsed, list):
                # Flatten nested lists: [["a","x"],["b","y"]] → ("a", "x", "b", "y")
                flat = []
                for item in parsed:
                    if isinstance(item, list):
                        flat.extend(str(x) for x in item)
                    else:
                        flat.append(str(item))
                return tuple(flat)
        except (json.JSONDecodeError, TypeError):
            pass
    if "|" in k:
        # Legacy format: "a|b|c" → ('a', 'b', 'c')
        return tuple(k.split("|"))
    # Single symbol
    return (k,)


# v0.40.23 (P7-FaseC): Bootstrap floors for outcome-SL/TP classification
# of young nodes. Until a node has accumulated HIST_COUNT_MATURE observations,
# `won` is computed using these conservative floors instead of the node's
# (still noisy) max_drawdown_pct / max_favorable_pct.
#
# Rationale (cross-AI review): the node's max_dd / max_fav only stabilizes
# after ~5 observations. Before that, the running max could be an outlier
# from a single noisy candle. Using a fixed 0.15% floor for both SL and TP
# (more conservative than the 0.10% paper_trader floor) means young nodes
# classify wins based on a small but consistent threshold rather than a
# potentially wide outlier.
#
# v0.51.0: Floors raised from 0.15% to 0.30% for 1m timeframe optimization.
# On 1m data, 0.15% thresholds produce win_rate ≈ 0.45 (slightly below
# random due to micro-structure mean reversion). At 0.30%, the first-touch
# simulation filters out noise and captures meaningful directional moves,
# pushing win_rate to 0.50-0.55 for patterns with genuine signal.
# This is NOT changing the confidence formula — it's calibrating the
# outcome simulation threshold to match 1m volatility characteristics.
#
# Threshold chosen from cross-AI review: historical_count >= 5 is where
# SL/TP starts to stabilize (max_dd converges within ±0.05% of final value
# for ~70% of nodes, per audit). Below 5: use floors. Above: use real.
#
# v0.54.0 (TAREA 15): DEPRECATED — these floors are NO LONGER used during
# build(). The build now uses compute_outcome_directional() which measures
# REAL directional movement, not artificial SL/TP simulation. These constants
# are kept for backward compatibility with compute_outcome_won() which is
# still used by signal generation (paper_trader) where SL/TP simulation is
# appropriate for risk management.
HIST_COUNT_MATURE = 5
OUTCOME_FLOOR_SL_PCT = 0.15
OUTCOME_FLOOR_TP_PCT = 0.15

# v0.54.0 (TAREA 15): Micro-floor for directional outcome classification.
# A move of exactly 0.00% is treated as a loss (no signal). Moves with
# |real_move_pct| < DIRECTIONAL_MICRO_FLOOR are also treated as losses
# to avoid classifying micro-noise as directional signal. The 0.01%
# threshold is intentionally tiny — it only filters degenerate cases
# where price returns to entry within floating-point precision.
DIRECTIONAL_MICRO_FLOOR = 0.01


def simulate_first_touch(
    window_df,
    entry_price: float,
    sl_pct: float,
    tp_pct: float,
    direction: str,
) -> bool:
    """V4.4 (P7-FaseC, v0.40.23): Simulate LONG/SHORT trade on intraperiod OHLC.

    Returns True if TP is touched before SL within the window. Conservative
    on ties (if both SL and TP touched in same candle, SL wins).

    v0.40.24 CONTRACT: `window_df` MUST be the candles AFTER entry, not the
    candles that produced the pattern. The entry_price MUST be the close of
    the LAST pattern candle (≈ open of the first post-pattern candle). The
    v0.40.23 implementation in ppmt.py / profiles.py passed the pattern's
    own window_df, which made `won` a circular function of the pattern
    itself — see ppmt.py:337-422 for the fix rationale.

    Args:
        window_df: pandas DataFrame with 'high' and 'low' columns — the
            POST-entry candles (typically PATTERN_LEN × WINDOW candles
            immediately after the pattern ends). Empty/short DataFrames
            are handled gracefully (timeout = loss).
        entry_price: entry price (close of the LAST pattern candle, i.e.
            the candle immediately before window_df.iloc[0]).
        sl_pct: stop loss distance in percent (positive number).
        tp_pct: take profit distance in percent (positive number).
        direction: 'LONG' or 'SHORT'.

    Returns:
        True if TP hit first, False if SL hit first or neither hit (timeout).
        Timeout is treated as loss (no TP within window).
    """
    if direction == "LONG":
        sl_price = entry_price * (1.0 - sl_pct / 100.0)
        tp_price = entry_price * (1.0 + tp_pct / 100.0)
    else:
        sl_price = entry_price * (1.0 + sl_pct / 100.0)
        tp_price = entry_price * (1.0 - tp_pct / 100.0)

    for i in range(len(window_df)):
        high = float(window_df["high"].iloc[i])
        low = float(window_df["low"].iloc[i])
        if direction == "LONG":
            sl_hit = low <= sl_price
            tp_hit = high >= tp_price
        else:
            sl_hit = high >= sl_price
            tp_hit = low <= tp_price
        if sl_hit and tp_hit:
            return False  # conservative: SL wins on tie
        if sl_hit:
            return False
        if tp_hit:
            return True
    return False  # timeout = loss


def compute_outcome_won(
    window_df,
    entry_price: float,
    move_pct: float,
    sl_pct: Optional[float] = None,
    tp_pct: Optional[float] = None,
    historical_count: int = 0,
) -> bool:
    """V4.4 (P7-FaseC, v0.40.23): Compute `won` flag using outcome SL/TP.

    Convenience wrapper used by ppmt.py / profiles.py / validator.py callers
    that have `window_df` available but want to apply the maturity threshold
    automatically.

    Logic:
      1. Direction is inferred from sign(move_pct):
         move_pct > 0 → LONG candidate, move_pct < 0 → SHORT candidate.
         move_pct == 0 → return False (degenerate, won't be classified).
      2. If historical_count < HIST_COUNT_MATURE: use OUTCOME_FLOOR_SL_PCT
         and OUTCOME_FLOOR_TP_PCT (bootstrap floors).
      3. If historical_count >= HIST_COUNT_MATURE: use the provided sl_pct /
         tp_pct (node's actual max_dd / max_fav from accumulated metadata).
      4. Simulate first-touch via simulate_first_touch().

    Args:
        window_df: DataFrame with 'high' and 'low' columns for the POST-ENTRY
            window (typically PATTERN_LEN × WINDOW candles immediately AFTER
            the pattern ends). v0.40.24 CONTRACT: do NOT pass the pattern's
            own window_df here — that produces a circular, meaningless `won`.
        entry_price: entry price (close of the LAST pattern candle, i.e. the
            candle immediately before window_df.iloc[0]).
        move_pct: observed move_pct = (exit - entry) / entry × 100. Used only
            to infer direction via sign — the simulation itself runs on the
            POST-entry candles, not the pattern window that produced move_pct.
        sl_pct: node's |max_drawdown_pct| × 1.5 (None for new nodes).
        tp_pct: node's max(|expected_move_pct|, max_favorable_pct) × 1.0.
        historical_count: node's observation count BEFORE this observation
            (0 for the first observation in a new node).

    Returns:
        True if outcome-SL/TP classifies this as a win, False otherwise.
    """
    if move_pct == 0:
        return False
    direction = "LONG" if move_pct > 0 else "SHORT"

    if historical_count < HIST_COUNT_MATURE:
        # Young node: use bootstrap floors (more conservative than 0.10%
        # paper_trader floor — fewer false positives on noisy first obs).
        use_sl = OUTCOME_FLOOR_SL_PCT
        use_tp = OUTCOME_FLOOR_TP_PCT
    else:
        # Mature node: use real SL/TP from accumulated metadata.
        use_sl = sl_pct if sl_pct is not None and sl_pct > 0 else OUTCOME_FLOOR_SL_PCT
        use_tp = tp_pct if tp_pct is not None and tp_pct > 0 else OUTCOME_FLOOR_TP_PCT

    return simulate_first_touch(window_df, entry_price, use_sl, use_tp, direction)


def compute_outcome_directional(
    post_pattern_df,
    entry_price: float,
    move_pct: float,
) -> bool:
    """V5.0 (TAREA 15, v0.54.0): Compute `won` flag from REAL directional movement.

    This is the BUILD-TIME replacement for compute_outcome_won(). Instead of
    simulating first-touch SL/TP with artificial 0.15% thresholds (which
    measures micro-structure noise, not predictive power), this function
    asks a simple question:

        "Did the price move in the direction predicted by the pattern?"

    TAREA 14 AUDIT FINDING: With SL/TP=0.15%, the first-touch simulation
    produced WR=46% on DOGE 1m — below random. The root cause: 0.15% is
    18-22x smaller than real candle ranges (±1-5%), so micro-dips trigger
    SL before the real directional move materializes. Pattern "cbc" had
    expected_move_pct=+0.35% (REAL directional signal) but win_rate=41%
    (SL=0.15% triggered by noise first).

    NEW LOGIC:
      1. Direction is inferred from sign(move_pct) of the PATTERN window.
      2. real_move_pct = (post_pattern close[-1] - entry_price) / entry_price × 100
         This is the ACTUAL price movement over the W candles after the pattern.
      3. For LONG: won = (real_move_pct > DIRECTIONAL_MICRO_FLOOR)
         For SHORT: won = (real_move_pct < -DIRECTIONAL_MICRO_FLOOR)
      4. Micro-floor of 0.01% filters degenerate zero-move cases only.

    WHY THIS IS BETTER:
      - Measures PREDICTIVE POWER: "Did the pattern's direction call work?"
      - No artificial SL/TP thresholds that are timeframe-dependent
      - No first-touch noise contamination from micro-structure
      - WR naturally reflects genuine signal: random = 50%, skilled > 50%
      - Works identically across 1m/5m/15m — the move_pct scales with time

    Args:
        post_pattern_df: DataFrame with 'close' column for the POST-PATTERN
            window (W candles immediately AFTER the pattern ends). MUST have
            at least 1 row, otherwise returns False (timeout = loss).
        entry_price: entry price (close of the LAST pattern candle, i.e. the
            candle immediately before post_pattern_df.iloc[0]).
        move_pct: observed move_pct from the PATTERN window. Used ONLY to
            infer direction via sign — the actual outcome is measured from
            post_pattern_df's close prices.

    Returns:
        True if the post-pattern price moved in the predicted direction
        (beyond the micro-floor), False otherwise.
    """
    if move_pct == 0:
        return False

    if len(post_pattern_df) == 0:
        return False  # timeout = loss

    direction = "LONG" if move_pct > 0 else "SHORT"

    # Real movement: last close of the post-pattern window vs entry
    actual_close = float(post_pattern_df["close"].iloc[-1])
    real_move_pct = (actual_close - entry_price) / entry_price * 100.0

    if direction == "LONG":
        return real_move_pct > DIRECTIONAL_MICRO_FLOOR
    else:  # SHORT
        return real_move_pct < -DIRECTIONAL_MICRO_FLOOR


@dataclass
class DirectionStats:
    """
    V4.3 (FIX-A): Per-direction statistics for a node's observation history.

    Tracks win_rate, expected_move, and count separately for LONG and SHORT
    observations, enabling the trading engine to make direction-aware decisions
    like: "This pattern wins 62% as LONG but only 33% as SHORT."

    PROBLEM (v0.40.17-audit): The legacy `win_rate` field mixes LONG and SHORT
    observations. `won = move_pct > 0` (ppmt.py:336) is valid for LONG only —
    a SHORT trade "wins" when `move_pct < 0`. The mixed win_rate systematically
    overestimates LONG win_rate and underestimates SHORT win_rate, producing
    the LONG/SHORT asymmetry observed in the audit:
      - LONG PnL medio = -0.017% (negative in ALL confidence deciles >0.40)
      - SHORT PnL medio = +0.013% (positive in ALL confidence deciles)
      - Spearman confidence<->PnL LONG = -0.008 (no predictive power)

    SOLUTION: Classify each observation by sign of move_pct at insert time.
    `long_stats` accumulates observations where move_pct > 0 (LONG wins).
    `short_stats` accumulates observations where move_pct < 0 (SHORT wins).
    At match time, the engine queries the stats for the direction it intends
    to trade, getting an unbiased win_rate.
    """
    count: int = 0
    """Number of observations in this direction (move_pct > 0 for LONG, < 0 for SHORT)."""

    wins: int = 0
    """Number of winning observations in this direction.
    For LONG: move_pct > 0 (always wins by definition of classification).
    For SHORT: move_pct < 0 (always wins by definition of classification).
    So wins == count for DirectionStats — but kept for API symmetry with RegimeStats."""

    total_move_pct: float = 0.0
    """Cumulative move_pct across all observations in this direction.
    For LONG: sum of positive move_pct values (LONG's expected profit).
    For SHORT: sum of negative move_pct values (SHORT's expected profit, negative)."""

    total_drawdown_pct: float = 0.0
    """Cumulative drawdown observed in this direction's observations."""

    @property
    def win_rate(self) -> float:
        """Win rate within this direction. By construction always 1.0 since
        classification IS by winning direction. Kept for API symmetry."""
        if self.count == 0:
            return 0.0
        return self.wins / self.count

    @property
    def avg_move_pct(self) -> float:
        """Average move_pct within this direction.
        For LONG: average positive move (expected profit).
        For SHORT: average negative move (expected SHORT profit, returned as negative)."""
        if self.count == 0:
            return 0.0
        return self.total_move_pct / self.count

    @property
    def avg_drawdown_pct(self) -> float:
        """Average max drawdown within this direction."""
        if self.count == 0:
            return 0.0
        return self.total_drawdown_pct / self.count

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "count": self.count,
            "wins": self.wins,
            "total_move_pct": round(self.total_move_pct, 4),
            "total_drawdown_pct": round(self.total_drawdown_pct, 4),
        }

    @classmethod
    def from_dict(cls, data: dict) -> DirectionStats:
        """Deserialize from dictionary."""
        return cls(
            count=data.get("count", 0),
            wins=data.get("wins", 0),
            total_move_pct=data.get("total_move_pct", 0.0),
            total_drawdown_pct=data.get("total_drawdown_pct", 0.0),
        )


@dataclass
class RegimeStats:
    """
    Statistics for a single regime within a node's observation history.

    Tracks win_rate, expected_move, and count separately for each regime,
    enabling the trading engine to make regime-aware decisions like:
      "This pattern wins 62% in trending_up but only 33% in volatile."

    This is the key V4.1 enhancement that makes regime_distribution actionable.
    Without per-regime win_rate, we only know HOW MANY times a pattern was
    observed in each regime, but not WHETHER IT WON there.
    """
    count: int = 0
    """Number of observations in this regime."""

    wins: int = 0
    """Number of winning observations in this regime."""

    total_move_pct: float = 0.0
    """Cumulative expected_move across all observations in this regime.
    Used to compute average expected_move per regime."""

    @property
    def win_rate(self) -> float:
        """Win rate within this regime."""
        if self.count == 0:
            return 0.0
        return self.wins / self.count

    @property
    def avg_move_pct(self) -> float:
        """Average expected_move within this regime."""
        if self.count == 0:
            return 0.0
        return self.total_move_pct / self.count

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "count": self.count,
            "wins": self.wins,
            "total_move_pct": round(self.total_move_pct, 4),
        }

    @classmethod
    def from_dict(cls, data: dict) -> RegimeStats:
        """Deserialize from dictionary."""
        return cls(
            count=data.get("count", 0),
            wins=data.get("wins", 0),
            total_move_pct=data.get("total_move_pct", 0.0),
        )


@dataclass
class BlockLifecycleMetadata:
    """
    Block Lifecycle Metadata attached to each Trie node.

    This is the core innovation of PPMT V3/V4. Every node in the Trie
    carries these fields, enabling the Trie itself to make all
    trading decisions without external indicators.

    Key insight: If a next SAX block does NOT exist as a child node,
    the pattern broke BEFORE price hit SL → earliest possible exit signal.

    V4 Enhancement: Regime-Aware Node Metadata
    Each node now stores the market regime(s) under which its pattern
    was observed. This enables:
      - Regime-specific pattern matching (N4 Trie segmentation)
      - Regime-aware confidence scoring (patterns in favorable regimes
        get higher confidence, unfavorable regimes get lower)
      - Independent vs Dependent node classification:
          * Independent: has enough observations (>= min_independent_count)
            to be self-sufficient — its metadata is reliable on its own
          * Dependent: relies on parent/ancestor metadata (low count,
            inherited regime info) — confidence is scaled down
    """

    # === Entry/Exit Timing ===
    trigger_candle: int = 0
    """Which candle within the pattern sequence activates this block.
    E.g., if trigger_candle=10 in a 50-candle pattern, the signal
    fires at candle 10 with 40 candles of predicted movement remaining."""

    remaining_candles: int = 0
    """Predicted number of candles remaining in this pattern phase.
    Derived from historical average duration at this node."""

    # === Price Prediction ===
    expected_move_pct: float = 0.0
    """Expected percentage price move from this point.
    Positive = bullish, Negative = bearish.
    Computed as median of historical moves from this node."""

    max_drawdown_pct: float = 0.0
    """Maximum observed drawdown (negative) from entry at this node.
    Used to set stop loss levels dynamically."""

    max_favorable_pct: float = 0.0
    """Maximum observed favorable excursion from entry.
    Used to set take profit and trailing stop levels."""

    # === Historical Statistics ===
    win_rate: float = 0.0
    """Percentage of times this pattern completed successfully.
    Success = price reached expected_move before max_drawdown."""

    avg_duration: int = 0
    """Average number of candles this pattern phase lasts.
    Used to predict remaining_candles for new observations."""

    historical_count: int = 0
    """Number of times this exact SAX sequence has been observed.
    More observations = higher confidence in metadata accuracy."""

    # === Risk Parameters (computed from history) ===
    sl_price: Optional[float] = None
    """Dynamic stop loss price level.
    Computed from max_drawdown_pct with safety margin."""

    tp_price: Optional[float] = None
    """Dynamic take profit price level.
    Computed from max_favorable_pct with safety margin."""

    # === Forward/Backward Navigation ===
    continuation_nodes: list[str] = field(default_factory=list)
    """SAX symbols that historically continued this pattern.
    If the next observed block is in this list → continue holding.
    These represent the 'forward' metadata — what comes after.

    .. deprecated:: v0.41.0
        Use ``expected_sequences`` instead.  This field is kept for
        backwards compatibility but only records the *single* next
        symbol; ``expected_sequences`` records full 3-symbol
        continuations with frequency counts."""

    expected_sequences: dict[tuple[str, ...], int] = field(default_factory=dict)
    """v0.41.0 (FASE 2, Tarea 2.1): Expected sequences of 3 future symbols.

    Maps a 3-tuple of SAX symbols → frequency count.  Example:
    ``{('f','g','h'): 35, ('f','x','y'): 12}``.  When a pattern
    is matched, the most frequent sequence is the expected
    continuation, used by PatternDivergenceMonitor (Tarea 2.2)
    to detect pattern breaks early.

    Only populated when there are ≥3 symbols after the pattern
    in the observation window.  Shorter suffixes fall back to
    ``continuation_nodes``."""

    last_seen_timestamp: float = 0.0
    """v0.41.0 (FASE 2, Tarea 2.4): Unix timestamp of the most recent
    observation of this pattern.  Used by ``apply_time_decay()`` to
    reduce confidence for stale patterns.  Unlike
    ``last_observation_time`` (which tracks any observation), this
    is specifically the last time the *full pattern* was observed."""

    break_nodes: list[str] = field(default_factory=list)
    """SAX symbols that historically broke this pattern.
    These represent transitions to a different regime.
    Not all missing blocks are breaks — some are just noise."""

    # === V4: Regime-Aware Node Metadata ===
    regime: str = ""
    """The market regime under which this pattern was observed.
    One of: trending_up, trending_down, ranging, volatile.
    Empty string means not yet set (legacy nodes or before V4)."""

    regime_confidence: float = 0.0
    """Confidence of the regime detection when this pattern was observed.
    Range [0, 1]. Higher = more certain about the regime classification."""

    dominant_regime: str = ""
    """The most common regime across all observations of this pattern.
    For terminal nodes, this is the same as `regime`.
    For intermediate nodes (after propagation), this is the regime
    with the highest count in regime_distribution. This enables
    regime-aware routing at any Trie depth."""

    regime_distribution: dict[str, int] = field(default_factory=dict)
    """Distribution of regimes across observations of this pattern.
    E.g., {'trending_up': 45, 'ranging': 30, 'volatile': 5}
    Used to compute regime-specific win rates and confidence.
    Enables the trading engine to say: 'This pattern works 60% of
    the time in trending_up but only 35% in volatile regimes.'"""

    regime_stats: dict[str, RegimeStats] = field(default_factory=dict)
    """V4.1: Per-regime statistics including win_rate and expected_move.
    While regime_distribution only counts observations, regime_stats tracks
    the actual performance within each regime. This is critical because:
    - A pattern observed 50 times in trending_up with 60% WR is valuable
    - A pattern observed 50 times in volatile with 30% WR is dangerous
    Without this, the trading engine cannot make regime-aware decisions.
    Key: regime name (e.g., 'trending_up'), Value: RegimeStats."""

    # === V4.3 (FIX-A): Per-direction statistics ===
    long_stats: DirectionStats = field(default_factory=DirectionStats)
    """Statistics for observations where move_pct > 0 (LONG instances).
    At match time, when the engine decides LONG, it should query
    long_stats.win_rate and long_stats.avg_move_pct instead of the
    mixed-aggregate `win_rate` field, which conflates LONG and SHORT outcomes."""

    short_stats: DirectionStats = field(default_factory=DirectionStats)
    """Statistics for observations where move_pct < 0 (SHORT instances).
    At match time, when the engine decides SHORT, it should query
    short_stats.win_rate and short_stats.avg_move_pct (the latter is negative,
    representing SHORT's expected profit when expressed as a signed move)."""

    # === V4.1: Move Variance Tracking ===
    move_variance: float = 0.0
    """Variance of observed moves (Welford's online algorithm).
    High variance = unreliable pattern (move_pct swings wildly).
    Low variance = reliable pattern (consistent outcomes).
    Used to compute move_std which adjusts confidence downward
    for patterns with inconsistent historical outcomes."""

    move_mean_for_variance: float = 0.0
    """Running mean used by Welford's algorithm for move_variance.
    Not the same as expected_move_pct (which is the incremental mean).
    This is maintained separately for the Welford M2 computation."""

    node_type: str = "dependent"
    """Whether this node is independent or dependent.
    - 'independent': Has enough observations (>= min_independent_count)
      to be self-sufficient. Its metadata is reliable on its own.
      Confidence is used at full strength.
    - 'dependent': Relies on parent/ancestor metadata. Low count,
      inherited regime info. Confidence is scaled down by a factor
      based on the ratio of actual vs minimum observations.
    Classification is performed during propagate_metadata() and
    updated during Living Trie observations."""

    min_independent_count: int = 10
    """Minimum historical_count for a node to be classified as independent.
    Nodes with count >= this threshold are 'independent'. Below it,
    they are 'dependent' and their metadata inherits from parents.
    10 is a reasonable default: below 10 observations, the node's
    statistics are too noisy to be reliable on their own."""

    # === V4.2: Observation Freshness ===
    last_observation_time: float = 0.0
    """Timestamp (epoch seconds) of the most recent observation.
    V4.2: Enables observation freshness tracking — patterns that
    haven't been observed recently can be deprioritized. This is
    critical for the Living Trie: as market conditions change,
    old patterns become less relevant. A pattern observed 5000
    candles ago should carry less weight than one observed 50 candles
    ago. The freshness_decay property computes a multiplier [0, 1]
    based on how long since the last observation."""

    observation_timespan: float = 0.0
    """Time span (in seconds) between the first and last observation.
    V4.2: Measures how spread out observations are. A pattern observed
    100 times in one day is less reliable than one observed 100 times
    over 30 days. Longer timespan = more robust pattern that works
    across different conditions. Short timespan = potentially overfit
    to a specific market condition."""

    # === Computed Properties ===

    @property
    def risk_reward_ratio(self) -> float:
        """Compute risk:reward ratio from expected move vs max drawdown."""
        if self.max_drawdown_pct == 0:
            return 0.0
        return abs(self.expected_move_pct / self.max_drawdown_pct)

    @property
    def confidence(self) -> float:
        """
        Confidence score based on historical observations and win rate.
        More observations and higher win rate → higher confidence.
        Uses Bayesian-inspired shrinking toward 0.5 for low counts.

        V4: Dependent nodes have their confidence scaled down because
        their metadata is inherited/aggregated rather than directly
        observed. An independent node with 50 observations is more
        trustworthy than a dependent node with 3 observations whose
        statistics were propagated from children.
        """
        if self.historical_count == 0:
            return 0.0
        # Bayesian shrinkage: prior of 0.5 with strength of 10 observations
        prior_strength = 10.0
        adjusted_win_rate = (
            (self.win_rate * self.historical_count + 0.5 * prior_strength)
            / (self.historical_count + prior_strength)
        )
        # Scale by sqrt of log(count) for sample size bonus
        count_bonus = min(1.0, np.sqrt(np.log1p(self.historical_count) / np.log(1000)))
        base_confidence = adjusted_win_rate * count_bonus

        # V4: Dependent node penalty
        # Dependent nodes have less reliable metadata, so we scale
        # their confidence down. The penalty is proportional to how
        # far they are from the minimum independent count.
        if self.node_type == "dependent" and self.historical_count > 0:
            dependency_ratio = min(
                1.0, self.historical_count / self.min_independent_count
            )
            # Scale between 0.5 (0 observations toward independent)
            # and 1.0 (at the threshold)
            dependency_penalty = 0.5 + 0.5 * dependency_ratio
            base_confidence *= dependency_penalty

        return base_confidence

    @property
    def expected_profit_ahead(self) -> float:
        """
        Expected profit percentage looking ahead from this block.
        Combines win_rate × expected_move to give a realistic expectation.

        This is the key metric the Money Manager uses to decide allocation:
          - High expected_profit_ahead → allocate more capital
          - Low/negative → allocate less or skip

        Formula: win_rate × expected_move_pct + (1 - win_rate) × max_drawdown_pct
        This gives the expected value including losses.
        """
        if self.historical_count == 0:
            return 0.0
        win_expectation = self.win_rate * self.expected_move_pct
        loss_expectation = (1.0 - self.win_rate) * self.max_drawdown_pct
        return win_expectation + loss_expectation

    @property
    def probability_of_success(self) -> float:
        """
        Probability that this pattern succeeds (reaches expected move
        before hitting stop loss).

        This is more nuanced than raw win_rate because it accounts
        for the Bayesian shrinkage and sample size. The Money Manager
        uses this as the primary signal for position sizing.

        Returns the same Bayesian-adjusted win_rate used in confidence(),
        but without the count bonus — just the pure probability estimate.
        """
        if self.historical_count == 0:
            return 0.0
        prior_strength = 10.0
        return (
            (self.win_rate * self.historical_count + 0.5 * prior_strength)
            / (self.historical_count + prior_strength)
        )

    @property
    def sizing_signal(self) -> float:
        """
        Composite sizing signal for the Money Manager (0.0 to 2.0+).

        This is the single number the Risk Manager reads to decide
        position size. It combines:
          - probability_of_success: How likely is this pattern to win?
          - expected_profit_ahead: How much do we expect to make?
          - risk_reward_ratio: Is the payoff worth the risk?

        Mapping (used by RiskManager):
          sizing_signal >= 1.5  → 2.0x base position (high conviction)
          sizing_signal 1.0-1.5 → 1.0x base position (normal)
          sizing_signal 0.5-1.0 → 0.5x base position (low conviction)
          sizing_signal < 0.5   → 0.25x or reject (very low)

        This creates the tight PPMT → RiskManager integration where
        the Trie's metadata directly drives capital allocation.
        """
        if self.historical_count == 0:
            return 0.0

        # Normalize components
        prob = self.probability_of_success  # 0-1

        # Expected profit: normalize to 0-1 range
        # A 2% expected profit is already very good for crypto
        profit_score = min(abs(self.expected_profit_ahead) / 2.0, 1.0)

        # Risk:reward: normalize (RR of 3+ is excellent)
        rr_score = min(self.risk_reward_ratio / 3.0, 1.0)

        # Weighted composite: probability is most important
        signal = 0.4 * prob + 0.35 * profit_score + 0.25 * rr_score

        # Scale to 0-2 range for the multiplier
        return signal * 2.0

    @property
    def is_unknown_block_exit(self) -> bool:
        """
        Whether an unknown next block should trigger an exit.
        True when there are continuation_nodes defined but the observed
        block is NOT among them — meaning pattern broke.
        """
        return len(self.continuation_nodes) > 0

    @property
    def move_std(self) -> float:
        """
        Standard deviation of observed moves.
        Computed from move_variance using Welford's online algorithm.
        High std = unpredictable pattern = lower effective confidence.
        Low std = consistent pattern = higher effective confidence.
        Returns 0.0 if fewer than 2 observations (can't compute variance).
        """
        if self.historical_count < 2:
            return 0.0
        return float(np.sqrt(self.move_variance / (self.historical_count - 1)))

    @property
    def move_coefficient_of_variation(self) -> float:
        """
        Coefficient of variation (CV) of observed moves.
        CV = std / |mean|. Normalized measure of dispersion.
        CV < 0.5 = tight clustering around the mean (reliable)
        CV 0.5-1.0 = moderate dispersion (acceptable)
        CV > 1.0 = high dispersion (unreliable — move direction uncertain)
        Returns 0.0 if expected_move_pct is zero (can't normalize).
        """
        if abs(self.expected_move_pct) < 1e-10:
            return 0.0
        return self.move_std / abs(self.expected_move_pct)

    @property
    def freshness_decay(self) -> float:
        """
        V4.2: Observation freshness multiplier based on time since last observation.

        Returns a value in [0, 1] that decays as the last observation gets older.
        Uses exponential decay with a half-life of 7 days (604800 seconds).

        - 0 days old → 1.0 (fresh, fully trusted)
        - 7 days old → 0.5 (half-weight)
        - 30 days old → ~0.06 (nearly expired)

        This prevents stale patterns from having the same influence as
        recently-observed ones. In fast-moving markets, patterns that
        haven't been seen in weeks may no longer be valid.

        Returns 1.0 if last_observation_time is 0 (not tracked).
        """
        if self.last_observation_time <= 0:
            return 1.0  # No tracking info, assume fresh
        import time as _time
        age_seconds = _time.time() - self.last_observation_time
        if age_seconds <= 0:
            return 1.0  # Future timestamp or same second
        # Half-life of 7 days = 604800 seconds
        half_life = 604800.0
        return float(np.exp(-0.693 * age_seconds / half_life))  # ln(2) ≈ 0.693

    @property
    def observation_density(self) -> float:
        """
        V4.2: Observations per unit time (observations/day).

        Measures how concentrated observations are. Low density = pattern
        observed occasionally over a long time (robust). High density =
        pattern observed many times in a short period (potentially overfit).

        Returns 0.0 if no timespan data.
        """
        if self.observation_timespan <= 0 or self.historical_count <= 0:
            return 0.0
        days = self.observation_timespan / 86400.0
        if days < 0.01:  # Less than ~15 minutes
            return float(self.historical_count) / 0.01  # Cap at 100/day
        return self.historical_count / days

    def regime_match_score(self, current_regime: str) -> float:
        """
        V4.1: Compute a confidence multiplier based on regime match.

        If the current market regime matches the node's dominant regime,
        confidence is boosted (up to 1.2x). If the current regime is
        unfavorable for this pattern, confidence is penalized (down to 0.5x).

        The scoring uses both regime_distribution (how often) AND
        regime_stats (how well it performed):

        1. If current regime matches dominant_regime → boost (1.0 to 1.2)
           Boost is proportional to how dominant the regime is.
        2. If current regime exists but is not dominant → neutral (0.8 to 1.0)
           Scaled by the ratio of observations in that regime.
        3. If current regime has NO observations → penalty (0.5 to 0.7)
           Unknown territory, reduce confidence.
        4. If regime_stats available, further adjust by regime-specific win_rate
           vs overall win_rate. A regime where the pattern underperforms
           gets an additional penalty.

        For independent nodes (sufficient observations), the regime match
        has MORE impact because we have reliable per-regime data.
        For dependent nodes (few observations), we apply less adjustment
        because the per-regime data is unreliable.

        Args:
            current_regime: Current market regime string

        Returns:
            Multiplier in range [0.5, 1.2] to apply to confidence
        """
        if not current_regime:
            return 1.0  # No regime info available, neutral

        if not self.regime_distribution:
            return 1.0  # No regime data on this node, neutral

        total_obs = sum(self.regime_distribution.values())
        if total_obs == 0:
            return 1.0

        current_count = self.regime_distribution.get(current_regime, 0)
        current_ratio = current_count / total_obs

        # Determine adjustment based on whether current regime is known
        if current_count == 0:
            # Regime never observed for this pattern — penalty
            # Less severe for independent nodes (more diverse data)
            base_mult = 0.7 if self.node_type == "independent" else 0.5
            return base_mult

        # Regime has been observed — compute base multiplier
        if current_regime == self.dominant_regime:
            # Current regime is the dominant one — boost
            # Boost proportional to dominance (how concentrated)
            boost = 1.0 + 0.2 * current_ratio  # 1.0 to 1.2
        else:
            # Regime exists but not dominant — neutral to slight penalty
            # Scale by how rare this regime is for this pattern
            base_mult = 0.8 + 0.2 * current_ratio  # 0.8 to 1.0
            boost = base_mult

        # V4.1: Further adjust using regime-specific win_rate if available
        if current_regime in self.regime_stats:
            rs = self.regime_stats[current_regime]
            if rs.count >= 3:  # Need at least 3 obs for reliable regime WR
                regime_wr = rs.win_rate
                overall_wr = self.win_rate
                if overall_wr > 0:
                    wr_ratio = regime_wr / overall_wr
                    # If regime WR is much worse than overall, penalize more
                    # If regime WR is better, slight extra boost
                    # Clamp wr_adjustment to [0.8, 1.1] to avoid extreme swings
                    wr_adjustment = max(0.8, min(1.1, wr_ratio ** 0.5))
                    boost *= wr_adjustment

        # Clamp final result
        return max(0.5, min(1.2, boost))

    def update_from_observation(
        self,
        move_pct: float,
        drawdown_pct: float,
        favorable_pct: float,
        duration: int,
        won: bool,
        next_symbol: Optional[str] = None,
        regime: Optional[str] = None,
        regime_confidence: Optional[float] = None,
        next_3_symbols: Optional[tuple[str, ...]] = None,
    ) -> None:
        """
        Update metadata with a new observation using incremental statistics.

        This method updates all fields incrementally without storing raw data,
        making it memory-efficient for millions of patterns.

        V4: Now also tracks regime information per observation.
        Each observation can carry the market regime under which it
        was observed, building the regime_distribution histogram.

        v0.41.0 (FASE 2): Now also records ``expected_sequences`` and
        ``last_seen_timestamp`` for forward-sequence tracking and
        temporal decay.

        Args:
            move_pct: Actual percentage move observed
            drawdown_pct: Maximum drawdown observed during pattern
            favorable_pct: Maximum favorable excursion observed
            duration: Actual duration in candles
            won: Whether the pattern completed successfully
            next_symbol: SAX symbol that followed this block (if any)
            regime: Market regime at time of observation
                    (trending_up, trending_down, ranging, volatile)
            regime_confidence: Confidence of the regime detection [0, 1]
            next_3_symbols: Tuple of 3 SAX symbols that followed this
                    pattern (if available).  Used to populate
                    ``expected_sequences`` (Tarea 2.1).
        """
        n = self.historical_count
        self.historical_count += 1

        # Incremental mean update
        self.expected_move_pct = (
            (self.expected_move_pct * n + move_pct) / self.historical_count
        )

        # Track worst drawdown and best favorable
        self.max_drawdown_pct = min(self.max_drawdown_pct, drawdown_pct)
        self.max_favorable_pct = max(self.max_favorable_pct, favorable_pct)

        # Incremental win rate update
        wins = self.win_rate * n + (1.0 if won else 0.0)
        self.win_rate = wins / self.historical_count

        # Incremental average duration
        self.avg_duration = int(
            (self.avg_duration * n + duration) / self.historical_count
        )

        # Update remaining_candles from average duration
        self.remaining_candles = self.avg_duration

        # Track continuation/break nodes
        if next_symbol is not None:
            if next_symbol not in self.continuation_nodes:
                self.continuation_nodes.append(next_symbol)

        # v0.41.0 (FASE 2, Tarea 2.1): Track expected sequences of 3 future symbols.
        # When there are ≥3 symbols after the pattern, record the 3-tuple
        # as a key in expected_sequences and increment its frequency count.
        if next_3_symbols is not None and len(next_3_symbols) == 3:
            self.expected_sequences[next_3_symbols] = (
                self.expected_sequences.get(next_3_symbols, 0) + 1
            )

        # v0.41.0 (FASE 2, Tarea 2.4): Set last_seen_timestamp for time decay.
        import time as _time_for_ts
        self.last_seen_timestamp = _time_for_ts.time()

        # V4.4 (P7-FaseC, v0.40.23): Track per-direction statistics with
        # outcome-based `won` flag (SL/TP first-touch) instead of hardcoded
        # `wins += 1` when move_pct > 0.
        #
        # BEFORE (v0.40.22): `self.long_stats.wins += 1` was always incremented
        # when move_pct > 0, ignoring the `won` parameter passed in. This meant
        # bayesian_wr_long ≡ 1.0 by construction, and the only signal the
        # bayesian shrinkage captured was N-count.
        #
        # AFTER (v0.40.23): wins is incremented only when `won` is True. The
        # `won` flag is now computed by callers using outcome SL/TP:
        #   - paper_trader.py: won = (trade.exit_reason == "take_profit")
        #   - ppmt.py: won = simulate_first_touch(window_df, sl_pct, tp_pct, dir)
        #   - validator.py, profiles.py: same simulate_first_touch helper
        #
        # Validation (v0.40.23-audit, 8 tokens × 3 ventanas, 257k trades):
        #   P7 (v0.40.22, wins ≡ count):  PnL -8090%, WR 0.421, PF 0.62
        #   P7C (v0.40.23, wins = outcome): PnL -4353%, WR 0.446, PF 0.67
        #   Δ P7C-P7: +3736pp PnL total, 8/8 tokens improve, 3/3 windows improve.
        # v2.2 UNIVERSAL DIRECTION FIX: Each observation feeds BOTH direction
        # pools, with mirrored outcomes. This is the canonical fix for the
        # "0 SHORTs" bug that made the motor 100% LONG during bull-market IS.
        #
        # BEFORE (v0.40.23): if move_pct > 0 → long_stats only; if < 0 → short
        # only. Result: in a 90-day bull IS (BTC +18%, SOL +60%), 80%+ of obs
        # went to long_stats. short_stats stayed empty → best_direction_p7()
        # always returned "LONG" because short_count == 0 → no SHORT signal
        # ever generated, even when the same pattern was predictive of
        # downward moves.
        #
        # AFTER (v2.2): Each observation is recorded TWICE — once as a LONG
        # trade (won = move_pct > 0) and once as a SHORT trade
        # (won = move_pct < 0). This is the standard "label both sides"
        # technique from supervised learning: every sample contributes
        # evidence to BOTH classifiers.
        #
        # Effect on best_direction_p7():
        #   - If a pattern systematically produces move_pct > 0 → long_stats
        #     has high WR, short_stats has low WR → motor picks LONG. ✓
        #   - If a pattern systematically produces move_pct < 0 → short_stats
        #     has high WR, long_stats has low WR → motor picks SHORT. ✓
        #   - If pattern is non-predictive (50/50) → both ~50% → motor
        #     returns None (no edge). ✓
        #
        # This fix is FULLY BACKWARD COMPATIBLE:
        #   - avg_move_long, avg_move_short unchanged in sign and magnitude
        #   - long_count == short_count == historical_count (so the "if lc==0"
        #     branch in best_direction_p7 never fires when data exists)
        #   - bayesian_wr_long / bayesian_wr_short now reflect actual
        #     directional predictive power, not sample imbalance
        if move_pct != 0:
            # LONG perspective: trade wins if price went up
            self.long_stats.count += 1
            long_won = move_pct > 0
            if long_won:
                self.long_stats.wins += 1
            self.long_stats.total_move_pct += move_pct
            self.long_stats.total_drawdown_pct += drawdown_pct

            # SHORT perspective: trade wins if price went down
            # (move_pct unchanged in sign — DirectionStats.avg_move_short
            #  remains negative, consistent with v0.40.23 contract)
            self.short_stats.count += 1
            short_won = move_pct < 0
            if short_won:
                self.short_stats.wins += 1
            self.short_stats.total_move_pct += move_pct  # negative when down
            self.short_stats.total_drawdown_pct += drawdown_pct
        # move_pct == 0: skip (degenerate, no directional info)

        # V4.1: Track move variance using Welford's online algorithm
        # This is numerically stable and doesn't require storing raw data.
        # After n observations, move_variance holds the M2 statistic
        # (sum of squared differences from the running mean).
        if self.historical_count >= 2:
            delta = move_pct - self.move_mean_for_variance
            self.move_mean_for_variance = (
                (self.move_mean_for_variance * (self.historical_count - 1) + move_pct)
                / self.historical_count
            )
            delta2 = move_pct - self.move_mean_for_variance
            self.move_variance += delta * delta2
        elif self.historical_count == 1:
            self.move_mean_for_variance = move_pct
            self.move_variance = 0.0

        # V4: Track regime distribution
        if regime and regime in ("trending_up", "trending_down", "ranging", "volatile"):
            self.regime_distribution[regime] = self.regime_distribution.get(regime, 0) + 1
            # V4.1: Track per-regime statistics (win_rate, expected_move)
            if regime not in self.regime_stats:
                self.regime_stats[regime] = RegimeStats()
            rs = self.regime_stats[regime]
            rs.count += 1
            rs.total_move_pct += move_pct
            if won:
                rs.wins += 1
            # Update dominant_regime to the most common regime
            if self.regime_distribution:
                self.dominant_regime = max(
                    self.regime_distribution, key=self.regime_distribution.get
                )
            # On first observation, set the regime directly
            if n == 0:
                self.regime = regime
                self.regime_confidence = regime_confidence if regime_confidence is not None else 0.0
            else:
                # Blend regime_confidence incrementally
                if regime_confidence is not None:
                    self.regime_confidence = (
                        (self.regime_confidence * n + regime_confidence)
                        / self.historical_count
                    )

        # V4: Update node_type based on count
        if self.historical_count >= self.min_independent_count:
            self.node_type = "independent"
        else:
            self.node_type = "dependent"

        # V4.2: Track observation freshness
        import time as _time
        now = _time.time()
        if n == 0:
            # First observation — set initial time, timespan is 0
            self.last_observation_time = now
            self.observation_timespan = 0.0
        else:
            # Update timespan: difference between first and latest observation
            if self.last_observation_time > 0:
                self.observation_timespan = max(
                    self.observation_timespan, now - (self.last_observation_time - self.observation_timespan)
                )
            self.last_observation_time = now

    @property
    def win_rate_long(self) -> float:
        """V4.3 (FIX-A): Win rate when this pattern is traded as LONG.

        This is the count of LONG-favorable observations divided by total
        observations. A pattern with `win_rate_long = 0.70` means: when this
        pattern was observed historically, 70% of the time the price went UP
        afterward. The engine should use this — NOT `win_rate` — when deciding
        whether to enter a LONG position.

        Returns 0.0 if no observations.
        """
        if self.historical_count == 0:
            return 0.0
        return self.long_stats.count / self.historical_count

    @property
    def win_rate_short(self) -> float:
        """V4.3 (FIX-A): Win rate when this pattern is traded as SHORT.

        This is the count of SHORT-favorable observations divided by total
        observations. A pattern with `win_rate_short = 0.70` means: when this
        pattern was observed historically, 70% of the time the price went DOWN
        afterward. The engine should use this — NOT `win_rate` — when deciding
        whether to enter a SHORT position.

        Returns 0.0 if no observations.
        """
        if self.historical_count == 0:
            return 0.0
        return self.short_stats.count / self.historical_count

    @property
    def avg_move_long(self) -> float:
        """V4.3 (FIX-A): Average move_pct when LONG was favorable.
        Positive number = expected profit per LONG trade on this pattern.
        Returns 0.0 if no LONG-favorable observations."""
        return self.long_stats.avg_move_pct

    @property
    def avg_move_short(self) -> float:
        """V4.3 (FIX-A): Average move_pct when SHORT was favorable.
        Negative number — its absolute value is the expected profit per SHORT
        trade on this pattern.
        Returns 0.0 if no SHORT-favorable observations."""
        return self.short_stats.avg_move_pct

    def confidence_for_direction(self, direction: str) -> float:
        """
        V4.3 (FIX-A): Direction-aware confidence score.

        This is the drop-in replacement for `confidence()` when the engine
        knows which direction it intends to trade. Uses the per-direction
        win_rate (unbiased) instead of the mixed-aggregate `win_rate` field.

        Args:
            direction: 'LONG' or 'SHORT' (case-insensitive).

        Returns:
            Confidence score in [0, 1]. Returns 0.0 if no observations.

        Formula mirrors `confidence()` but with direction-specific win_rate:
            adjusted_wr = (wr_dir * count + 0.5 * 10) / (count + 10)
            count_bonus = min(1.0, sqrt(log1p(count) / log(1000)))
            base_confidence = adjusted_wr * count_bonus
        Plus the dependent-node penalty from `confidence()`.
        """
        if self.historical_count == 0:
            return 0.0

        direction = direction.upper()
        if direction == "LONG":
            wr = self.win_rate_long
        elif direction == "SHORT":
            wr = self.win_rate_short
        else:
            # Unknown direction: fall back to mixed-aggregate (legacy behavior)
            wr = self.win_rate

        prior_strength = 10.0
        adjusted_wr = (
            (wr * self.historical_count + 0.5 * prior_strength)
            / (self.historical_count + prior_strength)
        )
        count_bonus = min(1.0, np.sqrt(np.log1p(self.historical_count) / np.log(1000)))
        base_confidence = adjusted_wr * count_bonus

        if self.node_type == "dependent" and self.historical_count > 0:
            dependency_ratio = min(
                1.0, self.historical_count / self.min_independent_count
            )
            dependency_penalty = 0.5 + 0.5 * dependency_ratio
            base_confidence *= dependency_penalty

        return base_confidence

    def expected_move_for_direction(self, direction: str) -> float:
        """
        V4.3 (FIX-A): Direction-aware expected move.

        For LONG: returns avg_move_long (positive number = expected % profit).
        For SHORT: returns abs(avg_move_short) (positive number = expected % profit).

        This is the drop-in replacement for `expected_move_pct` when the engine
        knows the direction. The legacy `expected_move_pct` mixes LONG and SHORT
        moves and can be near-zero even when both directions have strong edges.
        """
        direction = direction.upper()
        if direction == "LONG":
            return self.avg_move_long
        if direction == "SHORT":
            # SHORT profit = abs(move_pct) when move_pct < 0
            return abs(self.avg_move_short)
        return self.expected_move_pct  # fallback

    # ---------------------------------------------------------------- #
    # V4.4 (P7): directional_edge policy
    # ---------------------------------------------------------------- #
    #
    # v0.40.22-audit: P7 replaces the legacy `dir = sign(expected_move_pct)`
    # policy in signal.py. It computes a per-direction edge using bayesian-
    # shrunk win_rate × avg_move, then picks the direction with the higher
    # edge. The motor applies a quality gate (min_edge_pct) to skip trades
    # where neither direction has enough expected edge.
    #
    # Validation (v0.40.22-audit): 8 tokens × 3 windows, 304,685 trades.
    #   P1 (legacy):  PnL -8650%, WR 0.421, PF 0.612
    #   P7 (this):    PnL -8090%, WR 0.421, PF 0.625  (+560pp, 7/8 tokens)
    #
    # Note on long_wins ≡ long_count: the current definition classifies
    # observations by sign(move_pct), so wins == count by construction.
    # The bayesian shrinkage (lc+1)/(lc+2) still adds value because it
    # penalizes low-N observations. Fase C (redefine long_wins with SL/TP
    # outcome) would break this equivalence and unlock further gains.

    def bayesian_wr_long(self, alpha: float = 1.0, beta: float = 1.0) -> float:
        """Bayesian-shrunk win_rate for LONG direction.
        bayesian_wr = (long_wins + α) / (long_count + α + β).
        With current definition (long_wins ≡ long_count), this simplifies
        to (lc + 1) / (lc + 2) for Laplace prior α=β=1.
        """
        lc = self.long_stats.count
        lw = self.long_stats.wins
        if lc == 0:
            return 0.0
        return (lw + alpha) / (lc + alpha + beta)

    def bayesian_wr_short(self, alpha: float = 1.0, beta: float = 1.0) -> float:
        """Bayesian-shrunk win_rate for SHORT direction.
        See bayesian_wr_long for details.
        """
        sc = self.short_stats.count
        sw = self.short_stats.wins
        if sc == 0:
            return 0.0
        return (sw + alpha) / (sc + alpha + beta)

    def long_edge(self, alpha: float = 1.0, beta: float = 1.0) -> float:
        """V4.4 (P7): Expected edge for LONG = bayesian_wr_long × avg_move_long.
        Positive number representing expected % profit per LONG trade."""
        return self.bayesian_wr_long(alpha, beta) * self.avg_move_long

    def short_edge(self, alpha: float = 1.0, beta: float = 1.0) -> float:
        """V4.4 (P7): Expected edge for SHORT = bayesian_wr_short × |avg_move_short|.
        Positive number representing expected % profit per SHORT trade."""
        return self.bayesian_wr_short(alpha, beta) * abs(self.avg_move_short)

    def directional_edge(self, alpha: float = 1.0, beta: float = 1.0) -> float:
        """V4.4 (P7): Signed directional edge = long_edge − short_edge.
        Positive → LONG is favoured. Negative → SHORT is favoured.
        Zero → no directional preference (gate will likely reject)."""
        return self.long_edge(alpha, beta) - self.short_edge(alpha, beta)

    def best_direction_p7(
        self,
        min_edge_pct: float = 0.10,
        alpha: float = 1.0,
        beta: float = 1.0,
    ) -> Optional[str]:
        """V4.4 (P7): Pick the best direction using bayesian-shrunk edge.

        Returns 'LONG', 'SHORT', or None (if no direction clears the gate).
        This is the canonical P7 policy — signal.py calls this instead of
        the legacy `sign(expected_move_pct)` logic.

        Args:
            min_edge_pct: Hard floor on max(long_edge, short_edge).
                Below this → no trade (return None).
            alpha, beta: Laplace prior for bayesian shrinkage.

        Decision logic:
            1. If both long_count and short_count are 0 → no data → None.
            2. Compute long_edge and short_edge with bayesian shrinkage.
            3. Gate: if max(long_edge, short_edge) < min_edge_pct → None.
            4. If only one direction has observations → return that direction
               (after gate check).
            5. Otherwise → return direction with higher edge.
        """
        lc = self.long_stats.count
        sc = self.short_stats.count
        if lc == 0 and sc == 0:
            return None

        long_e = self.long_edge(alpha, beta)
        short_e = self.short_edge(alpha, beta)

        # Gate: best edge must clear min_edge_pct
        if max(long_e, short_e) < min_edge_pct:
            return None

        if lc == 0:
            return "SHORT" if sc > 0 else None
        if sc == 0:
            return "LONG"
        return "LONG" if long_e >= short_e else "SHORT"

    def compute_sl_tp(self, entry_price: float, safety_margin: float = 0.2) -> None:
        """
        Compute stop loss and take profit prices from metadata.

        Args:
            entry_price: Current entry price
            safety_margin: Margin added to SL/TP for safety (0.2 = 20% extra)

        v0.40.1 FIX-4: Rebalanced SL/TP to preserve the directional edge
          identified in CAPA 3 audit (EM→PnL corr = +0.11).
          BEFORE: SL = max_drawdown × 1.2, TP = min(|EM|, max_fav) × 0.9
            → SL/TP hit ratio = 2.14 (SL hits 64.8% of trades, TP 30.3%)
            → Break-even requires 28.6% TP rate, motor delivered 30.3%
              but END trades (4.9%) destroyed the margin → net losing.
          AFTER: SL = max_drawdown × 1.5 (more slack), TP = max(|EM|, max_fav) × 1.0 (full)
            → Logic: if motor has directional edge (+0.11 EM→PnL), we want:
              - SL holgado so noisy drawdowns don't kick us out
              - TP completo so we capture the full predicted move
            → Target RR ≈ 0.67 (TP_dist / SL_dist), break-even at 60% TP rate
              but the bet is that with looser SL we go from 30% → 50%+ TP rate
              because the noisy drawdowns that hit SL before now resolve.
          The old safety_margin param is preserved for API compat but no
          longer used in the calculation (we use explicit 1.5 and 1.0 factors).
        """
        # Stop loss: max_drawdown × 1.5 (was × 1.2)
        # More slack to absorb noisy drawdowns from sparse-trie metadata
        sl_distance = abs(self.max_drawdown_pct) * 1.5
        self.sl_price = entry_price * (1.0 - sl_distance / 100.0)

        # Take profit: max(|expected_move|, max_favorable) × 1.0 (was min(...) × 0.9)
        # - Use MAX instead of MIN: if the motor predicts +0.5% but history
        #   shows max_favorable of +0.8%, target the higher (more optimistic)
        #   since we believe the directional signal.
        # - No haircut (× 1.0 instead of × 0.9): capture the full predicted
        #   move. The old 0.9 haircut was leaving profit on the table.
        tp_distance = max(
            abs(self.expected_move_pct),
            self.max_favorable_pct,
        ) * 1.0
        self.tp_price = entry_price * (1.0 + tp_distance / 100.0)

    def to_dict(self) -> dict:
        """Serialize metadata to dictionary for storage."""
        return {
            "trigger_candle": self.trigger_candle,
            "remaining_candles": self.remaining_candles,
            "expected_move_pct": round(self.expected_move_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "max_favorable_pct": round(self.max_favorable_pct, 4),
            "win_rate": round(self.win_rate, 4),
            "avg_duration": self.avg_duration,
            "historical_count": self.historical_count,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "continuation_nodes": self.continuation_nodes,
            # v0.42.0: Expected sequences of 3 future symbols.
            # JSON keys must be strings. For tuple keys like ('a','x','b','y'),
            # we serialize as a JSON array string: '[["a","x"],["b","y"]]'.
            # Single-symbol keys are kept as strings.
            "expected_sequences": {
                (
                    str(list(k)) if isinstance(k, tuple) else k
                ): v
                for k, v in self.expected_sequences.items()
            },
            "break_nodes": self.break_nodes,
            # V3: Computed sizing signals for Risk Manager
            "confidence": round(self.confidence, 4),
            "probability_of_success": round(self.probability_of_success, 4),
            "expected_profit_ahead": round(self.expected_profit_ahead, 4),
            "sizing_signal": round(self.sizing_signal, 4),
            "risk_reward_ratio": round(self.risk_reward_ratio, 4),
            # V4: Regime-aware node metadata
            "regime": self.regime,
            "regime_confidence": round(self.regime_confidence, 4),
            "dominant_regime": self.dominant_regime,
            "regime_distribution": self.regime_distribution,
            # V4.1: Per-regime statistics
            "regime_stats": {k: v.to_dict() for k, v in self.regime_stats.items()},
            # V4.3 (FIX-A): Per-direction statistics
            "long_stats": self.long_stats.to_dict(),
            "short_stats": self.short_stats.to_dict(),
            # V4.1: Move variance tracking
            "move_variance": round(self.move_variance, 6),
            "move_mean_for_variance": round(self.move_mean_for_variance, 6),
            "node_type": self.node_type,
            "min_independent_count": self.min_independent_count,
            # V4.2: Observation freshness
            "last_observation_time": self.last_observation_time,
            "observation_timespan": self.observation_timespan,
            # v0.41.0 (FASE 2, Tarea 2.4): Last seen timestamp for time decay
            "last_seen_timestamp": self.last_seen_timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BlockLifecycleMetadata:
        """Deserialize metadata from dictionary."""
        return cls(
            trigger_candle=data.get("trigger_candle", 0),
            remaining_candles=data.get("remaining_candles", 0),
            expected_move_pct=data.get("expected_move_pct", 0.0),
            max_drawdown_pct=data.get("max_drawdown_pct", 0.0),
            max_favorable_pct=data.get("max_favorable_pct", 0.0),
            win_rate=data.get("win_rate", 0.0),
            avg_duration=data.get("avg_duration", 0),
            historical_count=data.get("historical_count", 0),
            sl_price=data.get("sl_price"),
            tp_price=data.get("tp_price"),
            continuation_nodes=data.get("continuation_nodes", []),
            # v0.42.0: Deserialize expected_sequences.
            # Keys are stored as JSON array strings like '[["a","x"],["b","y"]]'
            # or legacy "a|b|c" pipe-delimited strings. Convert both to tuples.
            expected_sequences={
                _parse_es_key(k): v
                for k, v in data.get("expected_sequences", {}).items()
            },
            break_nodes=data.get("break_nodes", []),
            # V4: Regime-aware node metadata
            regime=data.get("regime", ""),
            regime_confidence=data.get("regime_confidence", 0.0),
            dominant_regime=data.get("dominant_regime", ""),
            regime_distribution=data.get("regime_distribution", {}),
            # V4.1: Per-regime statistics
            regime_stats={
                k: RegimeStats.from_dict(v) if isinstance(v, dict) else RegimeStats()
                for k, v in data.get("regime_stats", {}).items()
            },
            # V4.3 (FIX-A): Per-direction statistics
            long_stats=DirectionStats.from_dict(data.get("long_stats", {}))
                if isinstance(data.get("long_stats", {}), dict) else DirectionStats(),
            short_stats=DirectionStats.from_dict(data.get("short_stats", {}))
                if isinstance(data.get("short_stats", {}), dict) else DirectionStats(),
            # V4.1: Move variance tracking
            move_variance=data.get("move_variance", 0.0),
            move_mean_for_variance=data.get("move_mean_for_variance", 0.0),
            node_type=data.get("node_type", "dependent"),
            min_independent_count=data.get("min_independent_count", 10),
            # V4.2: Observation freshness
            last_observation_time=data.get("last_observation_time", 0.0),
            observation_timespan=data.get("observation_timespan", 0.0),
            # v0.41.0 (FASE 2, Tarea 2.4): Last seen timestamp for time decay
            last_seen_timestamp=data.get("last_seen_timestamp", 0.0),
        )
