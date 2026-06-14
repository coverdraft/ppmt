"""
Paper Trading Engine - Simulated Trading with PPMT Predictions

Runs a simulated trading loop using PPMT predictions on historical data
without real money. This enables:

  1. Validating PPMT prediction quality in real-time
  2. Testing the full pipeline: SAX → Trie Match → Signal → Risk → Trade
  3. Building a track record before going live
  4. Monte Carlo simulation from paper trading results

The paper trader steps through historical candles one at a time,
generating predictions and executing simulated trades with the
RiskManager's adaptive sizing system.

Usage:
    from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig

    trader = PaperTrader(config=PaperTraderConfig(symbol="BTC/USDT"))
    result = trader.run()
    print(result.format_summary())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

import numpy as np

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.core.sax import SAXEncoder
from ppmt.core.regime import RegimeDetector, RegimeInfo
from ppmt.core.profiles import TokenProfile, TIMEFRAME_ALPHA_DEFAULTS
from ppmt.core.matcher import FuzzyMatcher
from ppmt.engine.ppmt import PPMT
from ppmt.engine.prediction import PredictionEngine
from ppmt.engine.signal import SignalType, Signal
from ppmt.risk.manager import RiskManager, RiskConfig


console = Console()


def compute_atr_pct(df, period: int = 14) -> np.ndarray:
    """
    Compute ATR (Average True Range) as a percentage of close price.

    ATR measures volatility — higher ATR = more volatile = wider stops needed.
    Expressing ATR as % of price makes it comparable across price levels.

    Args:
        df: OHLCV DataFrame
        period: ATR lookback period (default 14)

    Returns:
        numpy array of ATR % values, same length as df
    """
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    close = df['close'].values.astype(float)

    # True Range = max(H-L, |H-prev_C|, |L-prev_C|)
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(
        high - low,
        np.maximum(
            np.abs(high - prev_close),
            np.abs(low - prev_close)
        )
    )

    # Wilder's smoothing (exponential moving average)
    atr = np.zeros_like(tr)
    if len(tr) >= period:
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    # Convert to percentage of close price
    atr_pct = np.where(close > 0, atr / close * 100, 0)
    return atr_pct


def _record_observation(
    trie: "PPMTTrie",
    trade: PaperTrade,
    exit_sym_idx: int,
    next_symbol: Optional[str] = None,
    fuzzy_matcher: Optional["FuzzyMatcher"] = None,
) -> dict:
    """
    Living Trie: Record a trade's outcome back into the Trie.

    This is the core of the "Living Trie" concept — the Trie learns from
    its own trading results. Every closed trade becomes a new observation
    that updates the node's metadata, creating a feedback loop:

      Trie predicts → Trade executes → Outcome observed → Trie updated

    v0.6.6 FIX: Read/Write Path Alignment
    Previously, the READ path used FuzzyMatcher (allowing 1-edit matches)
    but the WRITE path used trie.search() (exact match only). When a trade
    was entered via fuzzy match, _record_observation couldn't find the exact
    pattern and created a NEW branch — causing node proliferation.

    Now, if fuzzy_matcher is provided:
    - Path A: Use best_match() to find the closest existing node instead
      of creating a new branch for patterns that are 1-edit away.
    - Path B: Use check_continuation() to find fuzzy-close children
      instead of always creating new children for unseen continuations.

    Two things happen:
    1. The entry node's metadata is updated with the actual outcome
       (win/loss, actual move, duration)
    2. If a pattern break occurred with a new symbol, that symbol is
       added as a new child node — the Trie literally grows

    Args:
        trie: The PPMT Trie to update
        trade: The closed PaperTrade with outcome data
        exit_sym_idx: SAX symbol index at trade exit
        next_symbol: The SAX symbol that followed (especially important
                     for pattern breaks — this is the NEW symbol)
        fuzzy_matcher: Optional FuzzyMatcher for read/write alignment.
            When provided, uses fuzzy matching to find the closest
            existing node instead of creating new branches.

    Returns:
        Dict with 'observations' and 'new_nodes' counts
    """
    from ppmt.core.metadata import BlockLifecycleMetadata

    observations = 0
    new_nodes = 0

    if not trade.matched_pattern:
        return {"observations": 0, "new_nodes": 0}

    # 1. Find the Trie node for the matched entry pattern
    #    v0.6.6: Try exact match first, then fuzzy, then prefix fallback.
    #    This aligns the WRITE path with the READ path — if a trade was
    #    entered via fuzzy match, we find the same node to write back to.
    node = trie.search(trade.matched_pattern)
    matched_via_fuzzy = False
    matched_pattern = trade.matched_pattern  # Track which pattern we matched

    if node is None and fuzzy_matcher is not None:
        # v0.6.6 FIX: Use FuzzyMatcher to find closest existing node.
        # This prevents creating duplicate branches for patterns that
        # are 1-edit away from existing patterns. The trade was likely
        # entered via a fuzzy match, so we should write back to the
        # same node that generated the entry signal.
        fuzzy_result = fuzzy_matcher.best_match(trie, trade.matched_pattern)
        if fuzzy_result.matched and fuzzy_result.node is not None:
            node = fuzzy_result.node
            matched_via_fuzzy = True
            matched_pattern = fuzzy_result.symbols

    if node is None:
        # Try shorter prefixes (the prediction may have used a prefix match)
        for prefix_len in range(len(trade.matched_pattern) - 1, 0, -1):
            node = trie.search(trade.matched_pattern[:prefix_len])
            if node is not None:
                matched_pattern = trade.matched_pattern[:prefix_len]
                break

    if node is None:
        # Pattern not in Trie at all — create the entry as a new pattern
        # V4.4: Pass regime/regime_confidence so new nodes inherit regime context.
        # Previously these were missing, causing newly-created Living Trie nodes
        # to have empty regime info, which broke regime-aware confidence scoring.
        #
        # v0.6.6 NOTE: This should be RARE with fuzzy_matcher enabled.
        # Only truly novel patterns (no fuzzy match) create new branches.
        trie.insert_with_observations(
            symbols=trade.matched_pattern,
            move_pct=trade.actual_move_pct,
            drawdown_pct=min(trade.actual_move_pct, 0) if trade.actual_move_pct < 0 else 0,
            favorable_pct=max(trade.actual_move_pct, 0) if trade.actual_move_pct > 0 else 0,
            duration=max(1, exit_sym_idx - trade.entry_sym_idx) if trade.entry_sym_idx > 0 else 1,
            won=trade.pnl_pct > 0,
            next_symbol=next_symbol,
            regime=trade.regime if trade.regime else None,
            regime_confidence=trade.regime_confidence if trade.regime_confidence > 0 else None,
        )
        new_nodes += 1
        observations += 1
        trie.trading_observations += 1
        trade.trie_updated = True
        return {"observations": observations, "new_nodes": new_nodes}

    # 2. Update the node's metadata with the actual trade outcome
    duration = max(1, exit_sym_idx - trade.entry_sym_idx) if trade.entry_sym_idx > 0 else 1
    won = trade.pnl_pct > 0

    # Drawdown: SL distance is max drawdown estimate
    if trade.sl_price > 0 and trade.entry_price > 0:
        if trade.direction == "LONG":
            max_dd_pct = -(trade.entry_price - trade.sl_price) / trade.entry_price * 100
        else:
            max_dd_pct = -(trade.sl_price - trade.entry_price) / trade.entry_price * 100
    else:
        max_dd_pct = min(trade.actual_move_pct, 0)

    # Favorable: TP distance is max favorable estimate
    if trade.tp_price > 0 and trade.entry_price > 0:
        if trade.direction == "LONG":
            max_fav_pct = (trade.tp_price - trade.entry_price) / trade.entry_price * 100
        else:
            max_fav_pct = (trade.entry_price - trade.tp_price) / trade.entry_price * 100
    else:
        max_fav_pct = max(trade.actual_move_pct, 0)

    # Update entry node metadata
    node.metadata.update_from_observation(
        move_pct=trade.actual_move_pct,
        drawdown_pct=max_dd_pct,
        favorable_pct=max_fav_pct,
        duration=duration,
        won=won,
        next_symbol=next_symbol,
        regime=trade.regime if trade.regime else None,
        regime_confidence=trade.regime_confidence if trade.regime_confidence > 0 else None,
    )
    observations += 1
    trie.trading_observations += 1

    # 3. If next_symbol is NOT already a child, add it — the Trie GROWS
    #    v0.6.6 FIX: Check fuzzy continuation before creating new child.
    #    If a fuzzy-close symbol already exists as a child, write the
    #    observation to THAT child instead of creating a new branch.
    if next_symbol:
        if node.has_child(next_symbol):
            # Exact child exists — update it
            child_node = node.get_child(next_symbol)
            child_node.metadata.update_from_observation(
                move_pct=trade.actual_move_pct * 0.5,
                drawdown_pct=max_dd_pct,
                favorable_pct=max_fav_pct,
                duration=duration,
                won=won,
                next_symbol=None,
                regime=trade.regime if trade.regime else None,
                regime_confidence=trade.regime_confidence if trade.regime_confidence > 0 else None,
            )
        elif fuzzy_matcher is not None:
            # v0.6.6: Check if a fuzzy-close continuation exists.
            # If the next symbol is similar to an existing child,
            # write to that child instead of creating a new one.
            cont_result = fuzzy_matcher.check_continuation(
                trie, matched_pattern, next_symbol
            )
            if cont_result.matched and cont_result.node is not None:
                # Fuzzy continuation found — write to existing child
                cont_result.node.metadata.update_from_observation(
                    move_pct=trade.actual_move_pct * 0.5,
                    drawdown_pct=max_dd_pct,
                    favorable_pct=max_fav_pct,
                    duration=duration,
                    won=won,
                    next_symbol=None,
                    regime=trade.regime if trade.regime else None,
                    regime_confidence=trade.regime_confidence if trade.regime_confidence > 0 else None,
                )
            else:
                # No fuzzy continuation — create new child (genuinely novel)
                extended_pattern = matched_pattern + [next_symbol]
                child_node = trie.insert(extended_pattern)
                child_node.metadata.update_from_observation(
                    move_pct=trade.actual_move_pct * 0.5,
                    drawdown_pct=max_dd_pct,
                    favorable_pct=max_fav_pct,
                    duration=duration,
                    won=won,
                    next_symbol=None,
                    regime=trade.regime if trade.regime else None,
                    regime_confidence=trade.regime_confidence if trade.regime_confidence > 0 else None,
                )
                new_nodes += 1
        else:
            # No fuzzy matcher — original behavior (always create new child)
            extended_pattern = matched_pattern + [next_symbol]
            child_node = trie.insert(extended_pattern)
            child_node.metadata.update_from_observation(
                move_pct=trade.actual_move_pct * 0.5,
                drawdown_pct=max_dd_pct,
                favorable_pct=max_fav_pct,
                duration=duration,
                won=won,
                next_symbol=None,
                regime=trade.regime if trade.regime else None,
                regime_confidence=trade.regime_confidence if trade.regime_confidence > 0 else None,
            )
            new_nodes += 1

    trade.trie_updated = True
    return {"observations": observations, "new_nodes": new_nodes}


@dataclass
class PaperTraderConfig:
    """Configuration for paper trading simulation."""
    symbol: str = "BTC/USDT"
    """Trading pair to simulate."""

    timeframe: str = "1h"
    """Candle timeframe."""

    initial_capital: float = 10_000.0
    """Starting capital for simulation."""

    pattern_length: int = 5
    """SAX blocks per pattern."""

    sax_alphabet_size: int = 0
    """SAX alphabet size. 0 = auto from TokenProfile (timeframe-adaptive).
    v0.6.4: Changed from hardcoded 8 to auto. Previous default alpha=8 was
    OUTSIDE the calibration grid (3-5) and caused zero trades at 1m/5m.
    Now uses TokenProfile.from_timeframe() which selects alpha based on
    validated timeframe-alpha mapping: 1h→3, 5m→4, 1m→5."""

    sax_window_size: int = 0
    """SAX window size. 0 = auto from TokenProfile (timeframe-adaptive).
    v0.6.4: Changed from hardcoded 10 to auto. Validated mapping:
    1h→7, 5m→7, 1m→7."""

    sax_strategy: str = "ohlcv"
    """SAX encoding strategy."""

    use_token_profile: bool = True
    """v0.6.4: Use TokenProfile for automatic parameter selection.
    When True (default), SAX alpha/window, catastrophic_loss_pct,
    short_allowed, short_confidence_multiplier, and fuzzy_threshold
    are all automatically set from TokenProfile based on asset class
    + timeframe. This replaces manual per-token tuning with
    data-driven auto-configuration validated across 22 token-TF combos.
    When False, falls back to explicit config values (backward compat)."""

    min_confidence: float = 0.20
    """Minimum confidence to generate entry signal.
    v0.6.2: Raised from 0.15 to 0.20 based on Cycle 5 regression analysis.
    Cycle 5 (v0.6.0 with probability bonus) allowed 10% confidence trades
    via the bonus loophole, resulting in +86.82% P&L vs Cycle 4's +1434%.
    v0.6.1 removed the bonus but min_confidence stayed at 15%. Raising
    to 20% further filters low-quality entries. Cycle 5 data shows trades
    with 15-19% confidence had WR of ~38% — removing these should
    improve overall quality. The SHORT gate is also relaxed (1.2x vs 1.5x)
    to compensate and allow SHORT diversification."""

    min_quality_score: float = 0.0
    """Minimum quality score to enter a trade.
    v0.4.2: Reverted to 0.0 (v0.4.0 value). The v0.4.1 quality filter
    (0.10) removed valid entries that were profitable. Quality filtering
    is better handled by the Living Trie metadata and min_confidence.
    Explicit quality thresholds have historically caused more harm
    than good by filtering valid entries."""

    min_risk_reward: float = 1.0
    """Minimum risk:reward ratio for entry.
    Kept at 1.0 to allow SHORT trades with R:R=1.5."""

    start_offset: int = 200
    """Number of initial candles to skip (warm-up for SAX encoding)."""

    end_offset: int = 0
    """Maximum candle index for trading (0 = use all data).
    v0.6.2: Added for out-of-sample validation. When set to a non-zero
    value, the paper trader will only trade up to this candle index.
    This allows building a trie on full data but only trading on a
    specific portion (e.g., the last 30% for OOS testing)."""

    paa_mean: float | None = None
    """SAX normalization mean from training data. v0.6.3: When set (along
    with paa_std), the SAX encoder uses encode_with_normalization() with
    these training stats instead of computing z-scores from current data.
    This ensures consistent symbol mapping between training and test periods,
    which is critical for out-of-sample validation."""

    paa_std: float | None = None
    """SAX normalization std from training data. See paa_mean docs."""

    living_trie: bool = True
    """Whether to update the Trie with observations during paper trading.
    When True, every trade outcome updates the Trie node's metadata,
    and pattern breaks create new child nodes. This makes the Trie
    'alive' — it learns and improves from its own trading results."""

    pattern_break_grace: int = 2
    """Number of consecutive pattern breaks before closing position.
    v0.2.9+: instead of closing on the first pattern break, wait
    for N consecutive breaks. This avoids closing on temporary noise.
    A single break may be a false signal; two consecutive breaks
    confirm the pattern has actually changed."""

    reentry_cooldown: int = 1
    """Number of SAX symbol steps to wait after a losing trade before
    entering a new position. v0.2.10: reduced from 3 to 1 — v0.2.9's
    cooldown of 3 blocked 358 entries, which was too aggressive.
    A cooldown of 1 still prevents immediate revenge trading while
    allowing the system to capture the next valid signal."""

    catastrophic_loss_pct: float = 0.0
    """Catastrophic loss threshold (percentage). 0.0 = use TokenProfile value.
    v0.6.4: Changed from hardcoded 8.0 to 0.0 (auto from TokenProfile).
    When use_token_profile=True, this is overridden by the asset-class-
    specific value from TokenProfile:
      blue_chip: 8%, large_cap: 10%, defi: 12%, meme: 15%, new_launch: 20%
    When use_token_profile=False or explicit value > 0, uses that value.
    Previous versions hardcoded 8.0%, which was too tight for meme tokens
    and too loose for blue chips."""

    regime_aware: bool = True
    """v0.8.0: Enable regime-aware position sizing. When True, the paper
    trader detects the current market regime at each SAX boundary and
    adjusts position sizing accordingly. Regime multipliers:
    - trending_up: 1.2x (favorable, increase exposure)
    - ranging:     1.0x (neutral, base sizing)
    - trending_down: 0.6x (unfavorable, reduce exposure)
    - volatile:    0.4x (dangerous, minimal exposure)
    The AdvancedPositionSizer already supports these multipliers;
    this flag activates the regime detection that feeds them."""

    use_multi_level: bool = True
    """v0.10.0: Enable 4-level matching (N1+N2+N3+N4) with adaptive weights.
    When True and all 4 tries are available, uses PPMT.match_raw() to compute
    weighted confidence across all 4 trie levels. When False or when only N3
    is available, falls back to single-trie PredictionEngine (backward
    compatible). This is the fix for GAP-1: PaperTrader previously only
    used N3, ignoring N1/N2/N4 entirely."""

    verbose: bool = True
    """Whether to print step-by-step details."""


@dataclass
class PaperTrade:
    """Record of a single paper trade."""
    trade_id: int = 0
    symbol: str = ""
    direction: str = ""  # "LONG" or "SHORT"
    entry_price: float = 0.0
    exit_price: float = 0.0
    entry_time: str = ""
    exit_time: str = ""
    size: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    confidence: float = 0.0
    quality_score: float = 0.0
    sizing_multiplier: float = 1.0
    win_rate: float = 0.0
    risk_reward_ratio: float = 0.0
    expected_move_pct: float = 0.0
    actual_move_pct: float = 0.0
    matched_pattern: list[str] = field(default_factory=list)
    exit_reason: str = ""
    """Why the trade closed: 'take_profit', 'stop_loss', 'trailing_stop', 'pattern_break', 'end_of_data'"""
    sl_price: float = 0.0
    """Stop loss price at entry."""
    tp_price: float = 0.0
    """Take profit price at entry."""
    trailing_activated: bool = False
    """Whether trailing stop was activated during this trade."""
    entry_sym_idx: int = 0
    """SAX symbol index at entry (for duration calculation)."""
    trie_updated: bool = False
    """Whether this trade's outcome was recorded in the Trie (living Trie)."""
    regime: str = ""
    """Market regime at entry time. v0.8.0: One of trending_up, trending_down, ranging, volatile."""
    regime_confidence: float = 0.0
    """Confidence of the regime detection at entry time."""


@dataclass
class PaperTraderResult:
    """Result of a paper trading simulation run."""
    symbol: str = ""
    timeframe: str = ""
    initial_capital: float = 10_000.0
    final_capital: float = 10_000.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    avg_trade_pnl_pct: float = 0.0
    best_trade_pnl_pct: float = 0.0
    worst_trade_pnl_pct: float = 0.0
    avg_confidence: float = 0.0
    avg_quality_score: float = 0.0
    trades: list[PaperTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    capital_history: list[float] = field(default_factory=list)

    def format_summary(self) -> str:
        """Format as a Rich panel summary."""
        pnl_color = "green" if self.total_pnl >= 0 else "red"
        pnl_sign = "+" if self.total_pnl >= 0 else ""

        lines = []
        lines.append(f"[bold]Paper Trading Results: {self.symbol} ({self.timeframe})[/bold]")
        lines.append("")
        lines.append(f"  Capital:  ${self.initial_capital:,.2f} -> ${self.final_capital:,.2f}  [{pnl_color}]{pnl_sign}{self.total_pnl_pct:.2f}%[/{pnl_color}]")
        lines.append(f"  P&L:      [{pnl_color}]${self.total_pnl:+,.2f}[/{pnl_color}]")
        lines.append("")
        lines.append(f"  Trades:   {self.total_trades}  (W:{self.winning_trades} L:{self.losing_trades})")
        lines.append(f"  Win Rate: {self.win_rate:.1%}")
        lines.append(f"  Profit Factor: {self.profit_factor:.2f}")
        lines.append(f"  Max DD:   {self.max_drawdown:.1%}")
        lines.append(f"  Sharpe:   {self.sharpe_ratio:.2f}")
        lines.append("")
        lines.append(f"  Avg Trade:  {self.avg_trade_pnl_pct:+.2f}%")
        lines.append(f"  Best Trade: {self.best_trade_pnl_pct:+.2f}%")
        lines.append(f"  Worst Trade: {self.worst_trade_pnl_pct:+.2f}%")
        lines.append(f"  Avg Confidence: {self.avg_confidence:.1%}")
        lines.append(f"  Avg Quality:    {self.avg_quality_score:.2f}")

        return "\n".join(lines)

    def format_trades_table(self) -> Table:
        """Format trades as a Rich table."""
        table = Table(title=f"Paper Trades: {self.symbol}")
        table.add_column("#", justify="right", style="cyan", width=4)
        table.add_column("Dir", width=5)
        table.add_column("Entry", justify="right", width=12)
        table.add_column("Exit", justify="right", width=12)
        table.add_column("PnL%", justify="right", width=8)
        table.add_column("Conf", justify="right", width=6)
        table.add_column("Quality", justify="right", width=6)
        table.add_column("WR", justify="right", width=5)
        table.add_column("Exit Reason", width=15)
        table.add_column("Regime", width=8)

        for t in self.trades:
            pnl_style = "green" if t.pnl_pct >= 0 else "red"
            dir_style = "green" if t.direction == "LONG" else "red"
            table.add_row(
                str(t.trade_id),
                f"[{dir_style}]{t.direction}[/{dir_style}]",
                f"${t.entry_price:,.2f}",
                f"${t.exit_price:,.2f}",
                f"[{pnl_style}]{t.pnl_pct:+.2f}%[/{pnl_style}]",
                f"{t.confidence:.0%}",
                f"{t.quality_score:.2f}",
                f"{t.win_rate:.0%}",
                t.exit_reason,
                t.regime or "-",
            )

        return table


class PaperTrader:
    """
    Paper Trading Engine using PPMT predictions.

    Steps through historical candles, generating predictions and
    executing simulated trades with adaptive position sizing.

    This is the key validation tool: run paper trading on historical
    data to verify PPMT predictions before risking real capital.
    """

    def __init__(self, config: Optional[PaperTraderConfig] = None):
        self.config = config or PaperTraderConfig()
        self.risk_config = RiskConfig(
            base_position_size_pct=0.01,  # 1% risk per trade (conservative while tuning)
            max_position_size_pct=0.04,
            min_position_size_pct=0.005,
            min_risk_reward=1.0,         # v0.2.9: lowered from 1.5 to allow SHORT R:R=1.5
            max_daily_loss_pct=0.10,      # 10% daily loss limit
            max_drawdown_pct=0.80,        # 80% for paper trading (don't block signals while tuning)
            min_quality_score=0.0,        # Checked in paper_trader, not RiskManager
            # min_confidence is checked in PaperTrader.run(), not RiskManager
        )

    def run(self) -> PaperTraderResult:
        """
        Run paper trading simulation on stored historical data.

        v0.6.4: TokenProfile integration.
        The paper trader now auto-configures from TokenProfile:
        - SAX alpha/window from timeframe-adaptive mapping (1h→3, 5m→4, 1m→5)
        - catastrophic_loss_pct from asset class (blue_chip:8%, meme:15%, etc.)
        - short_allowed / short_confidence_multiplier from asset class
        - fuzzy_threshold from asset class
        This replaces manual per-token tuning with data-driven
        auto-configuration validated across 22 token-TF combos with
        6+ months of real Binance data each.

        v0.3.0: Revert to v0.2.8 SL/TP behavior for maximum P&L.
        v0.2.10's "improvements" reduced P&L from +1578% to +371%:
        - Catastrophic protection (5%) cut trades that would reach TP
        - Trailing stop activated too early (50% of TP distance)
        - LONG SL floor of 2.0% was too wide vs v0.2.8's 1.5%
        - min_confidence 0.12 filtered valid entries

        v0.3.0 changes (back to v0.2.8 baseline):
        - SL/TP checked at SAX window boundaries ONLY (no intra-window)
        - No catastrophic protection (disabled by default)
        - LONG SL: max(ATR*1.5, 1.5%), cap 5% (v0.2.8 values)
        - SHORT SL: max(ATR*2.0, 2.0%), cap 7% (v0.2.8 values)
        - Trailing stop activates at 75% of TP distance (not 50%)
        - min_confidence = 0.10 (v0.2.8 value)
        - min_quality_score = 0.0 (removed, Living Trie handles this)

        Kept from v0.2.9/v0.2.10:
        - Pattern break grace period (2 consecutive)
        - Re-entry cooldown = 1 symbol step
        - Living Trie (ON by default)
        - Direction-specific SL/TP (SHORT gets wider stops)
        """
        cfg = self.config
        storage = PPMTStorage()

        # Load data
        df = storage.load_ohlcv(cfg.symbol, cfg.timeframe)
        if df.empty:
            console.print(f"[red]No data for {cfg.symbol}. Run 'ppmt ingest' first.[/red]")
            return PaperTraderResult(symbol=cfg.symbol, timeframe=cfg.timeframe)

        # Classify asset
        classifier = AssetClassifier()
        info = classifier.classify(cfg.symbol)

        # ================================================================
        # v0.6.4: TokenProfile integration
        # Auto-configure SAX parameters, risk params, and gating from
        # the TokenProfile based on asset class + timeframe.
        # ================================================================
        token_profile = None
        if cfg.use_token_profile:
            token_profile = TokenProfile.from_timeframe(
                symbol=cfg.symbol,
                asset_class=info.asset_class,
                timeframe=cfg.timeframe,
            )
            # Override SAX params from profile (unless explicitly set)
            if cfg.sax_alphabet_size == 0:
                cfg.sax_alphabet_size = token_profile.sax_alphabet_size
            if cfg.sax_window_size == 0:
                cfg.sax_window_size = token_profile.sax_window_size
            # Override catastrophic_loss from profile (unless explicitly set)
            if cfg.catastrophic_loss_pct == 0.0:
                cfg.catastrophic_loss_pct = token_profile.catastrophic_loss_pct * 100.0

            console.print(f"[bold green]TokenProfile loaded:[/bold green] "
                          f"{info.asset_class} @ {cfg.timeframe} → "
                          f"alpha={token_profile.sax_alphabet_size}, "
                          f"window={token_profile.sax_window_size}, "
                          f"cat_loss={token_profile.catastrophic_loss_pct:.0%}, "
                          f"short_allowed={token_profile.short_allowed}, "
                          f"fuzzy={token_profile.fuzzy_threshold:.2f}")
        else:
            # Fallback: use timeframe alpha defaults even without full profile
            if cfg.sax_alphabet_size == 0 or cfg.sax_window_size == 0:
                tf_defaults = TIMEFRAME_ALPHA_DEFAULTS.get(
                    cfg.timeframe, TIMEFRAME_ALPHA_DEFAULTS["1h"]
                )
                if cfg.sax_alphabet_size == 0:
                    cfg.sax_alphabet_size = tf_defaults["sax_alphabet_size"]
                if cfg.sax_window_size == 0:
                    cfg.sax_window_size = tf_defaults["sax_window_size"]

            if cfg.catastrophic_loss_pct == 0.0:
                cfg.catastrophic_loss_pct = 8.0  # safe default

        # Try to load existing Tries, or build new ones
        # v0.10.0: Load all 4 levels for GAP-1 4-level matching
        all_tries = storage.load_all_tries(cfg.symbol)
        trie_n1 = all_tries["n1"]
        trie_n2 = all_tries["n2"]
        trie_n3 = all_tries["n3"]
        trie_n4 = all_tries["n4"]

        initial_pattern_count = 0
        has_multi_level = (
            cfg.use_multi_level
            and trie_n1 is not None
            and trie_n2 is not None
            and trie_n4 is not None
        )

        # v0.6.4: Get fuzzy_threshold from TokenProfile if available
        fuzzy_threshold = 0.80  # safe default
        if token_profile is not None:
            fuzzy_threshold = token_profile.fuzzy_threshold

        if trie_n3 is None:
            console.print(f"[yellow]No Trie for {cfg.symbol}. Building from data...[/yellow]")
            engine = PPMT(
                symbol=cfg.symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=cfg.sax_alphabet_size,
                sax_window_size=cfg.sax_window_size,
                sax_strategy=cfg.sax_strategy,
                fuzzy_threshold=fuzzy_threshold,
                weight_profile=info.weight_profile,
            )
            engine.build(df, pattern_length=cfg.pattern_length)
            trie_n1 = engine.trie_n1
            trie_n2 = engine.trie_n2
            trie_n3 = engine.trie_n3
            trie_n4 = engine.trie_n4
            has_multi_level = True
        else:
            initial_pattern_count = trie_n3.pattern_count
            console.print(f"[green]Loaded N3 Trie for {cfg.symbol} ({trie_n3.pattern_count} patterns)[/green]")
            if has_multi_level:
                console.print(f"[green]All 4 levels loaded: N1={trie_n1.pattern_count}, "
                              f"N2={trie_n2.pattern_count}, N3={trie_n3.pattern_count}, "
                              f"N4={trie_n4.pattern_count}[/green]")
            else:
                console.print(f"[yellow]Only N3 trie available — running in single-level mode[/yellow]")

        # Primary trie for PredictionEngine + Living Trie
        trie = trie_n3

        # CRITICAL: Propagate metadata so intermediate nodes have statistics
        trie.propagate_metadata()
        if has_multi_level:
            for t in [trie_n1, trie_n2, trie_n4]:
                t.propagate_metadata()
        console.print(f"[green]Metadata propagated: root now has {trie.root.metadata.historical_count} aggregated observations[/green]")

        # Compute ATR for dynamic SL/TP sizing
        atr_pct = compute_atr_pct(df, period=14)
        valid_atr = atr_pct[atr_pct > 0]
        if len(valid_atr) > 0:
            console.print(f"  ATR(14): avg={np.mean(valid_atr):.2f}%, recent={atr_pct[-1]:.2f}%")

        # Create SAX encoder
        sax_encoder = SAXEncoder(
            alphabet_size=cfg.sax_alphabet_size,
            window_size=cfg.sax_window_size,
            strategy=cfg.sax_strategy,
        )

        # v0.6.5: Create FuzzyMatcher for pattern break checks
        # This replaces direct trie.check_continuation() with fuzzy-aware
        # continuation that computes pattern_break_score for graduated exits
        fuzzy_matcher = FuzzyMatcher(
            sax_encoder=sax_encoder,
            threshold=fuzzy_threshold,
            max_edit_distance=2,
        )

        # Encode the FULL DataFrame
        # v0.6.3: Use encode_with_normalization() when training stats are provided.
        # This ensures consistent symbol mapping between train and test periods.
        if cfg.paa_mean is not None and cfg.paa_std is not None:
            all_sax_symbols, _, _ = sax_encoder.encode_with_normalization(
                df, paa_mean=cfg.paa_mean, paa_std=cfg.paa_std
            )
            console.print(f"  SAX encoding: using training normalization "
                          f"(mean={cfg.paa_mean:.6f}, std={cfg.paa_std:.6f})")
        else:
            all_sax_symbols = sax_encoder.encode(df)

        if not all_sax_symbols:
            console.print(f"[red]Could not SAX encode data for {cfg.symbol}.[/red]")
            return PaperTraderResult(symbol=cfg.symbol, timeframe=cfg.timeframe)

        console.print(f"  SAX symbols: {len(all_sax_symbols)} (from {len(df)} candles)")

        # Create engines
        pred_engine = PredictionEngine(trie, prediction_depth=cfg.pattern_length)
        risk_mgr = RiskManager(capital=cfg.initial_capital, config=self.risk_config)

        # v0.10.0: Create PPMT engine for 4-level matching (GAP-1 fix)
        ppmt_engine = None
        if has_multi_level:
            ppmt_engine = PPMT(
                symbol=cfg.symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=cfg.sax_alphabet_size,
                sax_window_size=cfg.sax_window_size,
                sax_strategy=cfg.sax_strategy,
                fuzzy_threshold=fuzzy_threshold,
                weight_profile=info.weight_profile,
            )
            # Inject loaded tries instead of building new ones
            ppmt_engine.set_tries(trie_n1, trie_n2, trie_n3, trie_n4)
            # Adapt weights based on available data
            ppmt_engine.adapt_weights()
            console.print(f"  [bold cyan]4-level matching enabled[/bold cyan]: weights={ppmt_engine.weights}")

        # v0.3.3: Reverted adaptive confidence scaling (was raising min_confidence
        # to 0.20 for fresh tries). v0.3.2 proved this was COUNTERPRODUCTIVE:
        # fresh WR dropped from 45.3% (v0.3.1, no scaling) to 43.9% (v0.3.2, 20%).
        # The fix is NOT a higher threshold but better build-time metadata.
        # v0.3.3's trade-simulation "won" classification during build produces
        # more differentiated win_rates, so confidence scores are naturally
        # more meaningful even for fresh tries.
        if trie.trading_observations == 0:
            console.print(f"  [yellow]Fresh trie detected (0 trading observations) — "
                          f"v0.3.3 trade-simulation build should provide better metadata[/yellow]")
        else:
            console.print(f"  [green]Trie has {trie.trading_observations} trading observations "
                          f"— metadata quality: good[/green]")

        # Timeframe to hours
        tf_hours = {
            "1m": 1/60, "5m": 5/60, "15m": 15/60,
            "1h": 1, "4h": 4, "1d": 24,
        }.get(cfg.timeframe, 1)

        # Simulation state
        result = PaperTraderResult(
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            initial_capital=cfg.initial_capital,
            final_capital=cfg.initial_capital,
        )
        result.equity_curve = [cfg.initial_capital]
        result.capital_history = [cfg.initial_capital]

        current_position = None  # PaperTrade when in position
        trade_counter = 0
        peak_capital = cfg.initial_capital

        # v0.2.9: Pattern break grace period tracking
        consecutive_breaks = 0

        # v0.2.9: Re-entry cooldown after losing trades
        last_losing_trade_sym_idx = -999  # Symbol index of last losing trade
        cooldown_filter_count = 0

        # Start from warm-up offset (in candle space)
        start_candle = cfg.start_offset
        if start_candle >= len(df):
            console.print(f"[red]Not enough data. Need at least {start_candle} candles, have {len(df)}.[/red]")
            return result

        # v0.6.2: End offset for out-of-sample validation
        end_candle = cfg.end_offset if cfg.end_offset > 0 else len(df)
        if end_candle > len(df):
            end_candle = len(df)

        living_trie_status = "ON" if cfg.living_trie else "OFF"
        oos_status = f", ending at index {end_candle}" if cfg.end_offset > 0 else ""
        console.print(f"\n[bold cyan]Starting Paper Trading: {cfg.symbol} ({cfg.timeframe})[/bold cyan]")
        console.print(f"  Capital: ${cfg.initial_capital:,.2f}")
        console.print(f"  Data: {len(df)} candles, starting from index {start_candle}{oos_status}")
        console.print(f"  Trading range: candles {start_candle}-{end_candle} ({end_candle - start_candle} candles)")
        console.print(f"  Trie: {trie.pattern_count} patterns")
        console.print(f"  Min confidence: {cfg.min_confidence:.0%} | Min quality: {cfg.min_quality_score:.2f}")
        console.print(f"  Entry: move > 1.0%, probability > 20%, ATR-based SL/TP")
        console.print(f"  LONG SL: max(ATR*1.5, 1.5%) cap 5% | SHORT SL: max(ATR*2.0, 2.0%) cap 7%")
        cat_status = f"{cfg.catastrophic_loss_pct:.0f}%" if cfg.catastrophic_loss_pct > 0 else "OFF"
        console.print(f"  Catastrophic protection: {cat_status}")
        console.print(f"  Trailing stop: activates at 75% of TP distance")
        console.print(f"  Pattern break grace: {cfg.pattern_break_grace} consecutive")
        console.print(f"  Re-entry cooldown: {cfg.reentry_cooldown} symbols after loss")
        console.print(f"  Living Trie: [bold]{living_trie_status}[/bold]")
        console.print(f"  Regime-aware sizing: [bold]{'ON' if cfg.regime_aware else 'OFF'}[/bold]")
        console.print(f"  4-level matching: [bold]{'ON' if has_multi_level else 'OFF'}[/bold]\n")

        # v0.8.0: Regime detection
        regime_detector = None
        current_regime = "ranging"
        regime_info = None
        regime_stats = {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0}
        if cfg.regime_aware:
            regime_detector = RegimeDetector(lookback=50, vol_threshold=0.6, trend_threshold=0.005)
            console.print(f"  [dim]Regime detector initialized (lookback=50)[/dim]")

        # We iterate over SAX symbol positions
        start_sym_idx = start_candle // cfg.sax_window_size
        if start_sym_idx < cfg.pattern_length:
            start_sym_idx = cfg.pattern_length

        # v0.6.2: End symbol index for out-of-sample
        end_sym_idx = end_candle // cfg.sax_window_size

        # Track prediction statistics
        pred_count = 0
        pred_with_direction = 0
        pred_passed_threshold = 0
        risk_reject_reasons = {}  # reason -> count

        # Living Trie: track how many observations are recorded
        trie_observations_recorded = 0
        trie_new_nodes_created = 0
        trie_metadata_propagations = 0

        # Track current date for daily P&L reset
        current_date = None

        # Pre-extract arrays for fast intra-symbol access
        df_high = df['high'].values.astype(float)
        df_low = df['low'].values.astype(float)
        df_close = df['close'].values.astype(float)

        for sym_idx in range(start_sym_idx, end_sym_idx):
            # Candle range for this SAX symbol
            candle_start = sym_idx * cfg.sax_window_size
            candle_end = min((sym_idx + 1) * cfg.sax_window_size, len(df))
            last_candle_idx = candle_end - 1

            # Date check for daily P&L reset (use last candle of window)
            current_time = str(df.index[last_candle_idx]) if hasattr(df.index, 'strftime') else str(last_candle_idx)
            candle_date = current_time[:10] if len(current_time) >= 10 else None
            if candle_date and candle_date != current_date:
                if current_date is not None:
                    risk_mgr.reset_daily()
                current_date = candle_date

            # Current SAX pattern
            if sym_idx < cfg.pattern_length:
                continue
            current_symbols = all_sax_symbols[sym_idx - cfg.pattern_length:sym_idx]

            # ================================================================
            # v0.8.0: Regime detection at each SAX boundary
            # Detect market regime from recent prices and adjust position
            # sizing accordingly. Regime is updated once per SAX window
            # (not per candle) to match the trading decision cadence.
            # ================================================================
            if regime_detector is not None:
                # Use last 200 candles for regime detection (enough for lookback=50)
                regime_candle_start = max(0, last_candle_idx - 200)
                regime_prices = df_close[regime_candle_start:last_candle_idx + 1]
                if len(regime_prices) >= 50:
                    regime_info = regime_detector.detect_detailed(regime_prices)
                    current_regime = regime_info.regime
                    regime_stats[current_regime] = regime_stats.get(current_regime, 0) + 1

                    # v0.10.0: Update PPMT engine's regime for N4 matching (GAP-1)
                    if ppmt_engine is not None:
                        ppmt_engine.set_regime(current_regime)

            # ================================================================
            # PHASE 1: SL/TP checking
            # v0.2.10: SAX-boundary checking (like v0.2.8) with catastrophic
            # intra-window protection. v0.2.9 checked every candle with
            # HIGH/LOW which was too aggressive — it triggered on candle
            # wicks before the price actually closed there, cutting winners
            # short. Only 1 take_profit in 380 trades proved this was wrong.
            #
            # New approach:
            # 1. Check SL/TP at SAX window boundary (like v0.2.8) using
            #    close price — this lets trades breathe and reach TP
            # 2. Scan intra-window candles for catastrophic losses only
            #    (unrealized loss > catastrophic_loss_pct, default 5%)
            #    This prevents the -9.39% type losses from v0.2.8
            # ================================================================
            if current_position is not None:
                pos = risk_mgr._positions.get(cfg.symbol)
                if pos is None:
                    current_position = None
                    continue

                current_price = df_close[last_candle_idx]

                # === Step 1: Catastrophic intra-window protection ===
                # v0.3.0: DISABLED by default (catastrophic_loss_pct=0.0).
                # v0.2.10's catastrophic protection at 5% cut trades that
                # would have reached take_profit. The v0.2.8 baseline with
                # NO intra-window checking produced +1578% P&L.
                # Only enable if catastrophic_loss_pct > 0.
                catastrophic_close = False
                if cfg.catastrophic_loss_pct > 0:
                    for ci in range(candle_start, candle_end):
                        if ci >= len(df_close):
                            break
                        candle_c = df_close[ci]
                        if pos.direction == "LONG":
                            unrealized_loss_pct = (pos.entry_price - candle_c) / pos.entry_price * 100
                        else:
                            unrealized_loss_pct = (candle_c - pos.entry_price) / pos.entry_price * 100
                        if unrealized_loss_pct >= cfg.catastrophic_loss_pct:
                            # Catastrophic move — close immediately
                            cat_time = str(df.index[ci]) if hasattr(df.index, 'strftime') else str(ci)
                            _, pnl = risk_mgr.close_position(cfg.symbol, candle_c)
                            current_position.exit_price = candle_c
                            current_position.exit_time = cat_time
                            current_position.pnl = pnl
                            if current_position.direction == "LONG":
                                current_position.pnl_pct = (candle_c - current_position.entry_price) / current_position.entry_price * 100
                            else:
                                current_position.pnl_pct = (current_position.entry_price - candle_c) / current_position.entry_price * 100
                            current_position.actual_move_pct = current_position.pnl_pct
                            current_position.exit_reason = "catastrophic_stop"

                            if cfg.living_trie and current_position.matched_pattern:
                                next_sym = all_sax_symbols[sym_idx] if sym_idx < len(all_sax_symbols) else None
                                obs_result = _record_observation(
                                    trie, current_position, sym_idx, next_sym,
                                    fuzzy_matcher=fuzzy_matcher,
                                )
                                trie_observations_recorded += obs_result["observations"]
                                trie_new_nodes_created += obs_result["new_nodes"]

                            if current_position.pnl_pct <= 0:
                                last_losing_trade_sym_idx = sym_idx

                            result.trades.append(current_position)
                            trade_counter += 1
                            current_position = None
                            catastrophic_close = True
                            consecutive_breaks = 0

                            result.equity_curve.append(risk_mgr.capital)
                            result.capital_history.append(risk_mgr.capital)
                            if risk_mgr.capital > peak_capital:
                                peak_capital = risk_mgr.capital
                            break

                if catastrophic_close:
                    continue  # Skip to next SAX symbol

                # === Step 2: Trailing stop update (at SAX boundary) ===
                # Update trailing SL once per SAX window, using close price.
                # This is the same as v0.2.8 — gives trades room to breathe.
                if pos.tp_price is not None:
                    entry = pos.entry_price
                    if pos.direction == "LONG":
                        unrealized_pct = (current_price - entry) / entry * 100
                        tp_distance_pct = (pos.tp_price - entry) / entry * 100
                    else:
                        unrealized_pct = (entry - current_price) / entry * 100
                        tp_distance_pct = (entry - pos.tp_price) / entry * 100

                    # v0.3.0: Trailing stop activates at 75% of TP distance
                    # (v0.2.10's 50% was too aggressive — it triggered on normal
                    # retracements, locking in tiny gains instead of letting
                    # winners run to full TP). At 75%, we only trail when
                    # the trade is deep in profit and close to TP.
                    if not current_position.trailing_activated and tp_distance_pct > 0 and unrealized_pct >= tp_distance_pct * 0.75:
                        current_position.trailing_activated = True

                    if current_position.trailing_activated:
                        # v0.3.0: Use 1.5*ATR for trailing distance (wider)
                        # v0.2.10's 1*ATR was too tight — with avg ATR=0.84%,
                        # the trailing SL was only 0.84% away, getting hit
                        # by normal noise. 1.5*ATR = 1.26% gives breathing room.
                        current_atr = atr_pct[last_candle_idx] if last_candle_idx < len(atr_pct) else 2.0
                        trailing_distance = current_atr * 1.5
                        if pos.direction == "LONG":
                            new_sl = max(pos.sl_price, current_price * (1 - trailing_distance / 100))
                        else:
                            new_sl = min(pos.sl_price, current_price * (1 + trailing_distance / 100))
                        pos.sl_price = new_sl

                # === Step 3: Check SL/TP at SAX boundary (like v0.2.8) ===
                sl_hit = risk_mgr.check_stop_loss(cfg.symbol, current_price)
                tp_hit = risk_mgr.check_take_profit(cfg.symbol, current_price)

                if sl_hit:
                    # Close at stop loss (or trailing stop)
                    _, pnl = risk_mgr.close_position(cfg.symbol, current_price)
                    current_position.exit_price = current_price
                    current_position.exit_time = current_time
                    current_position.pnl = pnl
                    if current_position.direction == "LONG":
                        current_position.pnl_pct = (current_price - current_position.entry_price) / current_position.entry_price * 100
                    else:
                        current_position.pnl_pct = (current_position.entry_price - current_price) / current_position.entry_price * 100
                    current_position.actual_move_pct = current_position.pnl_pct
                    current_position.exit_reason = "trailing_stop" if current_position.trailing_activated else "stop_loss"

                    # Living Trie: record this trade outcome
                    if cfg.living_trie and current_position.matched_pattern:
                        next_sym = all_sax_symbols[sym_idx] if sym_idx < len(all_sax_symbols) else None
                        obs_result = _record_observation(
                            trie, current_position, sym_idx, next_sym,
                            fuzzy_matcher=fuzzy_matcher,
                        )
                        trie_observations_recorded += obs_result["observations"]
                        trie_new_nodes_created += obs_result["new_nodes"]

                    if current_position.pnl_pct <= 0:
                        last_losing_trade_sym_idx = sym_idx

                    result.trades.append(current_position)
                    trade_counter += 1
                    current_position = None
                    consecutive_breaks = 0

                    result.equity_curve.append(risk_mgr.capital)
                    result.capital_history.append(risk_mgr.capital)
                    if risk_mgr.capital > peak_capital:
                        peak_capital = risk_mgr.capital
                    continue

                elif tp_hit:
                    # Close at take profit
                    _, pnl = risk_mgr.close_position(cfg.symbol, current_price)
                    current_position.exit_price = current_price
                    current_position.exit_time = current_time
                    current_position.pnl = pnl
                    if current_position.direction == "LONG":
                        current_position.pnl_pct = (current_price - current_position.entry_price) / current_position.entry_price * 100
                    else:
                        current_position.pnl_pct = (current_position.entry_price - current_price) / current_position.entry_price * 100
                    current_position.actual_move_pct = current_position.pnl_pct
                    current_position.exit_reason = "take_profit"

                    # Living Trie: record this trade outcome
                    if cfg.living_trie and current_position.matched_pattern:
                        next_sym = all_sax_symbols[sym_idx] if sym_idx < len(all_sax_symbols) else None
                        obs_result = _record_observation(
                            trie, current_position, sym_idx, next_sym,
                            fuzzy_matcher=fuzzy_matcher,
                        )
                        trie_observations_recorded += obs_result["observations"]
                        trie_new_nodes_created += obs_result["new_nodes"]

                    result.trades.append(current_position)
                    trade_counter += 1
                    current_position = None
                    consecutive_breaks = 0

                    result.equity_curve.append(risk_mgr.capital)
                    result.capital_history.append(risk_mgr.capital)
                    if risk_mgr.capital > peak_capital:
                        peak_capital = risk_mgr.capital
                    continue

                # Update position unrealized P&L (at end of window)
                risk_mgr.update_position(cfg.symbol, current_price)

            # ================================================================
            # PHASE 2: Pattern break check with fuzzy matcher + grace period
            # v0.6.5: Uses FuzzyMatcher.check_continuation() instead of
            # trie.check_continuation() directly. This provides:
            #   - Fuzzy symbol matching (not just exact)
            #   - pattern_break_score for graduated exit decisions
            #   - All 4 trie levels checked (not just trie_n3)
            #
            # v0.2.9: Grace period — instead of closing on the FIRST pattern
            # break, we wait for N consecutive breaks (default 2). A single
            # break may be noise; two consecutive breaks confirm the pattern
            # has actually changed.
            # ================================================================
            if current_position is not None and len(current_symbols) >= 2:
                pattern_to_check = current_symbols[:-1]
                latest_symbol = current_symbols[-1]

                # v0.6.5: Check all 4 trie levels, pick best break score
                cont_results = []
                for cont_trie in [trie_n1, trie_n2, trie_n3, trie_n4]:
                    if cont_trie is not None:
                        cr = fuzzy_matcher.check_continuation(cont_trie, pattern_to_check, latest_symbol)
                        cont_results.append(cr)

                if cont_results:
                    best_cont = max(cont_results, key=lambda c: c.pattern_break_score)
                    continues = best_cont.matched
                    break_score = best_cont.pattern_break_score
                else:
                    # Fallback to exact match on main trie
                    continues, _ = trie.check_continuation(pattern_to_check, latest_symbol)
                    break_score = 0.0 if not continues else 1.0

                if not continues and current_position.confidence > 0:
                    consecutive_breaks += 1
                    # v0.6.5: Use break_score to modulate grace period
                    # If break_score is high (close match), give more grace
                    effective_grace = cfg.pattern_break_grace
                    if break_score >= 0.4:
                        # Pattern is weakening but not broken — add extra grace
                        effective_grace = cfg.pattern_break_grace + 1

                    if consecutive_breaks >= effective_grace:
                        # N consecutive breaks → close position
                        current_price = df_close[last_candle_idx]
                        _, pnl = risk_mgr.close_position(cfg.symbol, current_price)
                        current_position.exit_price = current_price
                        current_position.exit_time = current_time
                        current_position.pnl = pnl
                        if current_position.direction == "LONG":
                            current_position.pnl_pct = (current_price - current_position.entry_price) / current_position.entry_price * 100
                        else:
                            current_position.pnl_pct = (current_position.entry_price - current_price) / current_position.entry_price * 100
                        current_position.actual_move_pct = current_position.pnl_pct
                        current_position.exit_reason = "pattern_break"

                        # Living Trie: record AND create new child for the break symbol
                        if cfg.living_trie and current_position.matched_pattern:
                            obs_result = _record_observation(
                                trie, current_position, sym_idx,
                                latest_symbol,  # The symbol that broke the pattern
                                fuzzy_matcher=fuzzy_matcher,
                            )
                            trie_observations_recorded += obs_result["observations"]
                            trie_new_nodes_created += obs_result["new_nodes"]

                        if current_position.pnl_pct <= 0:
                            last_losing_trade_sym_idx = sym_idx

                        result.trades.append(current_position)
                        trade_counter += 1
                        current_position = None
                        consecutive_breaks = 0

                        result.equity_curve.append(risk_mgr.capital)
                        result.capital_history.append(risk_mgr.capital)
                        if risk_mgr.capital > peak_capital:
                            peak_capital = risk_mgr.capital
                        continue
                else:
                    # Pattern continues → reset break counter
                    consecutive_breaks = 0

            # ================================================================
            # PHASE 3: Entry signal generation
            # v0.2.10 parameters:
            # - min_confidence = 0.12 (compromise)
            # - min_quality_score = 0.05 (less strict than v0.2.9's 0.10)
            # - SHORT uses wider SL (ATR*2.0, min 2.5%) and lower TP (SL*1.5)
            # - Re-entry cooldown = 1 symbol step (reduced from 3)
            # ================================================================
            if current_position is None:
                # v0.2.9: Re-entry cooldown — skip if too soon after a losing trade
                if sym_idx - last_losing_trade_sym_idx < cfg.reentry_cooldown:
                    cooldown_filter_count += 1
                    continue

                # V0.6.2: Regime filter — skip entries in volatile regime.
                # Walk-forward OOS showed 16.7% WR in adverse periods.
                # Volatile regime = high uncertainty = don't enter new positions.
                # NOTE: Currently disabled — testing showed mixed results.
                # BTC got worse with the filter, ETH improved slightly.
                # More sophisticated regime-aware logic needed before enabling.
                # if current_regime == "volatile":
                #     continue

                pred_count += 1

                current_price = df_close[last_candle_idx]

                try:
                    prediction = pred_engine.predict(
                        current_symbols=current_symbols,
                        entry_price=current_price,
                        timeframe_hours=tf_hours,
                        symbol=cfg.symbol,
                        current_regime=current_regime,  # V4.1: regime-aware confidence
                    )
                except Exception:
                    continue

                if prediction.direction == "FLAT" or prediction.confidence <= 0:
                    continue

                pred_with_direction += 1

                # v0.10.0: Get weighted confidence from 4-level matching (GAP-1)
                # If multi-level is available, use PPMT.match_raw() to compute
                # confidence across N1/N2/N3/N4 with adaptive weights.
                # Otherwise, fall back to single-trie prediction confidence.
                weighted_confidence = prediction.confidence  # default: single-trie
                match_result = None
                best_trie_level = "n3"

                if ppmt_engine is not None:
                    ppmt_result = ppmt_engine.match_raw(
                        current_symbols=current_symbols,
                        current_price=current_price,
                    )
                    weighted_confidence = ppmt_result.weighted_confidence
                    match_result = ppmt_result

                    # Determine which level had the best match
                    level_confs = {
                        "n1": ppmt_result.n1_confidence,
                        "n2": ppmt_result.n2_confidence,
                        "n3": ppmt_result.n3_confidence,
                        "n4": ppmt_result.n4_confidence,
                    }
                    best_trie_level = max(level_confs, key=level_confs.get)

                    # Graceful degradation: if 4-level confidence is 0 but
                    # PredictionEngine found a direction, fall back to N3-only
                    if weighted_confidence <= 0 and prediction.confidence > 0:
                        weighted_confidence = prediction.confidence
                        best_trie_level = "n3"

                # Effective minimum confidence
                effective_min_conf = cfg.min_confidence

                # v0.6.1: REMOVED probability bonus that was undermining min_confidence.
                # The bonus lowered threshold from 15% to 7.5% when prob>50%, allowing
                # 10% confidence trades that had WR of only 32.6%. This defeated the
                # entire purpose of raising min_confidence.

                # SHORT signals require regime-aware confidence gating.
                # V4.3: Replaced the fixed 1.2x multiplier with a regime-aware gate.
                # Previous versions used a fixed SHORT penalty (1.2x or 1.5x), which
                # either eliminated all SHORTs (1.5x was too strict) or let bad SHORTs
                # through (1.2x was too lenient in trending_up). The new approach:
                #
                # - trending_down: SHORT is FAVORABLE → lower threshold (0.85x)
                # - ranging:       SHORT is NEUTRAL   → slight penalty (1.1x)
                # - trending_up:   SHORT is ADVERSE   → strict penalty (1.5x)
                # - volatile:      SHORT is DANGEROUS  → hard gate (1.8x)
                #
                # The floor of 0.20 always applies regardless of regime, ensuring
                # minimum quality. This replaces the tautological check that existed
                # in earlier versions (confidence < max(confidence * 1.2, 0.20) was
                # always false for conf >= 0.167).
                if prediction.direction == "SHORT":
                    # v0.6.4: TokenProfile SHORT gating
                    # If the token profile says short_allowed=False, skip SHORT entries.
                    # Meme tokens (DOGE, SHIB, PEPE) have short_allowed=False because
                    # their SHORT signals are unreliable in the validated data.
                    if token_profile is not None and not token_profile.short_allowed:
                        continue

                    short_regime_mult = {
                        "trending_down": 0.85,  # SHORTs favored in downtrend
                        "ranging": 1.1,         # slight caution
                        "trending_up": 1.5,     # fighting the trend — strict
                        "volatile": 1.8,        # high risk — very strict
                    }.get(current_regime, 1.2)   # default: moderate penalty
                    effective_min_conf = max(effective_min_conf * short_regime_mult, 0.20)

                    # v0.6.4: Apply TokenProfile's short_confidence_multiplier
                    # This makes SHORTs harder for tokens where SHORT WR is low.
                    # blue_chip: 1.5x, large_cap: 1.8x, defi: 2.0x, meme: 99x (disabled)
                    if token_profile is not None:
                        effective_min_conf = max(
                            effective_min_conf * token_profile.short_confidence_multiplier,
                            effective_min_conf  # don't lower below current
                        )

                # V4.1: Regime-aware confidence adjustment
                # If the current regime is unfavorable for this pattern (e.g.,
                # the pattern was observed in trending_up but current regime is
                # volatile), reduce confidence. This uses the matched node's
                # regime_match_score() to adjust. The prediction already has
                # regime-aware confidence from PredictionEngine, but we also
                # apply the regime effect to the THRESHOLD — making it harder
                # to enter trades in unfavorable regimes.
                regime_adjustment = 1.0  # neutral
                if cfg.regime_aware and current_regime and prediction.confidence > 0:
                    try:
                        matched_node = trie.search(current_symbols)
                        if matched_node and matched_node.metadata.regime_distribution:
                            regime_adjustment = matched_node.metadata.regime_match_score(current_regime)
                    except Exception:
                        pass
                # Adjust effective min_confidence inversely to regime match:
                # If regime is favorable (score > 1.0), LOWER the threshold (easier to enter)
                # If regime is unfavorable (score < 1.0), RAISE the threshold (harder to enter)
                # This is equivalent to adjusting confidence but via the threshold.
                if regime_adjustment > 0:
                    effective_min_conf = effective_min_conf / regime_adjustment

                # Entry conditions
                # v0.6.1: Reverted probability threshold from >0.25 back to >0.20.
                # The >0.25 threshold was too aggressive, cutting pass rate from 32.5%
                # to 13.8% and reducing trades by 32%.
                # v0.6.2: Lowered expected_total_move from >1.0% to >0.3% for alpha=3.
                # With alpha=3, cumulative predicted moves are typically 0.3-0.8%.
                # The 1.0% threshold blocked ALL entries with alpha=3.
                if (prediction.direction != "FLAT"
                    and weighted_confidence >= effective_min_conf
                    and abs(prediction.expected_total_move_pct) > 0.3
                    and prediction.overall_probability > 0.20):

                    pred_passed_threshold += 1

                    # Create signal
                    from ppmt.engine.signal import Signal, SignalType
                    from ppmt.core.metadata import BlockLifecycleMetadata

                    signal_type = (
                        SignalType.ENTRY_LONG if prediction.direction == "LONG"
                        else SignalType.ENTRY_SHORT
                    )

                    # === Direction-specific SL/TP ===
                    # v0.2.10: LONG SL floor raised to 2.0% (was 1.5% in v0.2.8/9).
                    # v0.2.9 had almost all SL exits at exactly -1.50%, proving
                    # the 1.5% floor was too tight. With avg ATR=0.84%,
                    # ATR*1.5 = 1.26% → floored to 1.5% → too many hits.
                    # Raising to 2.0% gives trades room to survive noise.
                    #
                    # LONG:
                    #   SL = max(ATR * 1.5, 2.0%), capped at 5%
                    #   TP = SL * 2.0 (R:R = 2.0)
                    #
                    # SHORT:
                    #   SL = max(ATR * 2.0, 2.5%), capped at 7%
                    #   TP = SL * 1.5 (R:R = 1.5)
                    #
                    current_atr_pct = atr_pct[last_candle_idx] if last_candle_idx < len(atr_pct) else 2.0

                    # V0.6.2 CRITICAL FIX: Prediction-Aware SL/TP
                    #
                    # Previous ATR-based SL/TP had fixed floors (1.5% SL, 3% TP
                    # for LONG). With alpha=3, the average expected move is only
                    # ~0.3-0.5%. TP at 3% = 11x expected move! Almost no trade
                    # ever reached TP, so they all hit SL or pattern break →
                    # guaranteed losing system despite 54% directional accuracy.
                    #
                    # OOS validation showed this was THE #1 cause of losses:
                    #   Before fix: BTC -28.98%, WR 29.1%, PF 0.67
                    #   After fix:  BTC -11.81%, WR 38.1%, PF 0.88
                    #   After fix:  ETH  -6.79%, WR 42.2%, PF 0.99
                    #
                    # New approach: Scale SL/TP to PREDICTED move, not ATR.
                    #   SL = 1.5x expected move (room for noise)
                    #   TP = 2.5x expected move (R:R = 1.67)
                    #   Floor: 0.5% SL, Cap: 5% SL
                    # This ensures TP is REACHABLE when prediction is correct.
                    expected_move_abs = abs(prediction.expected_total_move_pct)
                    
                    if prediction.direction == "LONG":
                        sl_distance_pct = max(min(expected_move_abs * 1.5, 5.0), 0.5)
                        tp_distance_pct = expected_move_abs * 2.5  # R:R = 1.67
                        # Ensure minimum R:R of 1.5
                        if tp_distance_pct < sl_distance_pct * 1.5:
                            tp_distance_pct = sl_distance_pct * 1.5
                    else:  # SHORT
                        sl_distance_pct = max(min(expected_move_abs * 1.5, 5.0), 0.5)
                        tp_distance_pct = expected_move_abs * 2.5
                        if tp_distance_pct < sl_distance_pct * 1.5:
                            tp_distance_pct = sl_distance_pct * 1.5

                    if prediction.direction == "LONG":
                        sl_price = current_price * (1 - sl_distance_pct / 100)
                        tp_price = current_price * (1 + tp_distance_pct / 100)
                    else:
                        sl_price = current_price * (1 + sl_distance_pct / 100)
                        tp_price = current_price * (1 - tp_distance_pct / 100)

                    # Risk:Reward ratio
                    sl_distance_pct = abs(current_price - sl_price) / current_price * 100
                    tp_distance_pct = abs(tp_price - current_price) / current_price * 100
                    risk_reward = tp_distance_pct / sl_distance_pct if sl_distance_pct > 0 else 0

                    # V4.3: Get actual historical_count from the matched Trie node
                    # BEFORE creating the Signal. The previous version created the
                    # Signal with hardcoded historical_count=100, then tried to fix
                    # it with a separate mock_meta — but the Signal's own
                    # compute_quality_score() and compute_sizing_multiplier() had
                    # already used the wrong count. This distorted sizing by making
                    # rarely-observed patterns appear more reliable than they are.
                    actual_historical_count = 10  # conservative default if no node found
                    matched_node_for_sizing = None
                    try:
                        matched_node_for_sizing = trie.search(current_symbols)
                        if matched_node_for_sizing and matched_node_for_sizing.metadata.historical_count > 0:
                            actual_historical_count = matched_node_for_sizing.metadata.historical_count
                    except Exception:
                        pass

                    signal = Signal(
                        signal_type=signal_type,
                        confidence=weighted_confidence,  # v0.10.0: 4-level weighted confidence (GAP-1)
                        symbol=cfg.symbol,
                        entry_price=current_price,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        expected_move_pct=prediction.expected_total_move_pct,
                        risk_reward_ratio=risk_reward,
                        win_rate=prediction.overall_probability,
                        historical_count=actual_historical_count,
                        matched_pattern=current_symbols,
                        trie_level=best_trie_level,  # v0.10.0: Which level won
                    )
                    signal.quality_score = signal.compute_quality_score()
                    signal.sizing_multiplier = signal.compute_sizing_multiplier()

                    # v0.3.0: Quality score filter only if min_quality_score > 0
                    # (default is 0.0 = disabled). The Living Trie metadata
                    # naturally handles quality filtering through confidence.
                    if cfg.min_quality_score > 0 and signal.quality_score < cfg.min_quality_score:
                        risk_reject_reasons["low_quality"] = risk_reject_reasons.get("low_quality", 0) + 1
                        continue

                    # Metadata sizing — use the real historical_count for sizing
                    # The Signal already has the correct count, but we create
                    # a dedicated BlockLifecycleMetadata for precise Bayesian
                    # sizing calculations (probability_of_success, sizing_signal).
                    # V4.3: No longer duplicates the historical_count fix — it's
                    # already correct in the Signal from creation.
                    mock_meta = BlockLifecycleMetadata(
                        win_rate=signal.win_rate,
                        expected_move_pct=signal.expected_move_pct,
                        max_drawdown_pct=-sl_distance_pct,
                        historical_count=actual_historical_count,
                    )
                    signal.probability_of_success = mock_meta.probability_of_success
                    signal.expected_profit_ahead = mock_meta.expected_profit_ahead
                    signal.metadata_sizing_signal = mock_meta.sizing_signal

                    # Risk check
                    can_open, reason = risk_mgr.can_open(signal, info.asset_class)
                    if can_open:
                        size = risk_mgr.calculate_position_size(signal)
                        position = risk_mgr.open_position(signal, size)

                        # V4: Use matched node's regime info when available
                        # If the matched Trie node has regime metadata, use it
                        # as the node-level regime instead of the global regime.
                        # This provides more granular regime awareness — a pattern
                        # that historically worked in trending_up should get a
                        # confidence boost when the current regime is trending_up.
                        node_regime = current_regime  # default to global
                        node_regime_conf = regime_info.confidence if regime_info else 0.0
                        try:
                            # v0.10.0: Use best-match node from 4-level matching if available
                            if match_result is not None:
                                best_match = None
                                for m in [match_result.n3_match, match_result.n2_match,
                                          match_result.n1_match, match_result.n4_match]:
                                    if m is not None and m.node is not None and m.node.metadata.dominant_regime:
                                        best_match = m
                                        break
                                if best_match and best_match.node:
                                    node_regime = best_match.node.metadata.dominant_regime
                                    node_regime_conf = best_match.node.metadata.regime_confidence
                            else:
                                # Fallback: search N3 trie directly (backward compatible)
                                matched_node = trie.search(current_symbols)
                                if matched_node and matched_node.metadata.dominant_regime:
                                    node_regime = matched_node.metadata.dominant_regime
                                    node_regime_conf = matched_node.metadata.regime_confidence
                        except Exception:
                            pass

                        current_position = PaperTrade(
                            trade_id=trade_counter + 1,
                            symbol=cfg.symbol,
                            direction=signal.direction or "LONG",
                            entry_price=current_price,
                            exit_price=0.0,
                            entry_time=current_time,
                            size=size,
                            confidence=signal.confidence,
                            quality_score=signal.quality_score,
                            sizing_multiplier=signal.sizing_multiplier,
                            win_rate=signal.win_rate,
                            risk_reward_ratio=signal.risk_reward_ratio,
                            expected_move_pct=signal.expected_move_pct,
                            matched_pattern=current_symbols,
                            sl_price=sl_price,
                            tp_price=tp_price,
                            entry_sym_idx=sym_idx,
                            regime=node_regime,
                            regime_confidence=node_regime_conf,
                        )
                    else:
                        risk_reject_reasons[reason] = risk_reject_reasons.get(reason, 0) + 1

            # Record equity curve periodically
            if sym_idx % 10 == 0:
                unrealized_capital = risk_mgr.capital
                result.equity_curve.append(unrealized_capital)
                result.capital_history.append(unrealized_capital)

            # Living Trie: re-propagate metadata every 200 symbol steps
            if cfg.living_trie and trie_observations_recorded > 0 and sym_idx % 200 == 0:
                trie.propagate_metadata()
                trie_metadata_propagations += 1

        # Close any open position at end of data
        if current_position is not None:
            last_price = float(df["close"].iloc[-1])
            _, pnl = risk_mgr.close_position(cfg.symbol, last_price)
            current_position.exit_price = last_price
            current_position.exit_time = "end_of_data"
            current_position.pnl = pnl
            if current_position.direction == "LONG":
                current_position.pnl_pct = (last_price - current_position.entry_price) / current_position.entry_price * 100
            else:
                current_position.pnl_pct = (current_position.entry_price - last_price) / current_position.entry_price * 100
            current_position.actual_move_pct = current_position.pnl_pct
            current_position.exit_reason = "end_of_data"

            # Living Trie: record the final trade outcome
            if cfg.living_trie and current_position.matched_pattern:
                obs_result = _record_observation(
                    trie, current_position, len(all_sax_symbols) - 1, None,
                    fuzzy_matcher=fuzzy_matcher,
                )
                trie_observations_recorded += obs_result["observations"]
                trie_new_nodes_created += obs_result["new_nodes"]

            result.trades.append(current_position)

        # Print prediction statistics
        console.print(f"\n[dim]Prediction stats: {pred_count} attempts, "
                      f"{pred_with_direction} with direction, "
                      f"{pred_passed_threshold} passed threshold[/dim]")
        if risk_reject_reasons:
            console.print(f"[dim]Risk rejections: {risk_reject_reasons}[/dim]")
        if cooldown_filter_count > 0:
            console.print(f"[dim]Re-entry cooldown blocks: {cooldown_filter_count}[/dim]")
        if cfg.regime_aware and regime_stats:
            total_regime_steps = sum(regime_stats.values())
            console.print(f"[dim]Regime distribution: "
                          f"up={regime_stats.get('trending_up', 0)} ({regime_stats.get('trending_up', 0)/max(total_regime_steps,1):.0%}) "
                          f"down={regime_stats.get('trending_down', 0)} ({regime_stats.get('trending_down', 0)/max(total_regime_steps,1):.0%}) "
                          f"range={regime_stats.get('ranging', 0)} ({regime_stats.get('ranging', 0)/max(total_regime_steps,1):.0%}) "
                          f"volatile={regime_stats.get('volatile', 0)} ({regime_stats.get('volatile', 0)/max(total_regime_steps,1):.0%})[/dim]")

        # Living Trie statistics and save
        if cfg.living_trie and trie_observations_recorded > 0:
            # Final metadata propagation
            trie.propagate_metadata()
            trie_metadata_propagations += 1

            console.print(f"[bold cyan]Living Trie:[/bold cyan] "
                          f"{trie_observations_recorded} observations recorded, "
                          f"{trie_new_nodes_created} new nodes created, "
                          f"{trie_metadata_propagations} metadata propagations")
            if initial_pattern_count > 0:
                growth = trie.pattern_count - initial_pattern_count
                console.print(f"[bold cyan]Trie growth:[/bold cyan] "
                              f"{initial_pattern_count} -> {trie.pattern_count} patterns "
                              f"(+{growth} new patterns discovered)")

            # Save updated Trie back to storage
            storage.save_trie(cfg.symbol, "n3", trie)
            console.print(f"  [green]Updated N3 Trie saved to storage[/green]")

            # v0.10.0: Save N1/N2/N4 if loaded (they weren't modified by Living Trie,
            # but propagation may have updated metadata)
            if has_multi_level:
                for level, t in [("n1", trie_n1), ("n2", trie_n2), ("n4", trie_n4)]:
                    t.propagate_metadata()
                    storage.save_trie(cfg.symbol, level, t)
                console.print(f"  [green]All 4 levels saved to storage[/green]")

        # Compute final results
        result.final_capital = risk_mgr.capital
        result.total_pnl = risk_mgr.capital - cfg.initial_capital
        result.total_pnl_pct = (risk_mgr.capital - cfg.initial_capital) / cfg.initial_capital * 100
        result.total_trades = len(result.trades)
        result.winning_trades = sum(1 for t in result.trades if t.pnl_pct > 0)
        result.losing_trades = sum(1 for t in result.trades if t.pnl_pct <= 0)
        result.win_rate = result.winning_trades / result.total_trades if result.total_trades > 0 else 0

        if result.trades:
            pnls = [t.pnl_pct for t in result.trades]
            result.avg_trade_pnl_pct = sum(pnls) / len(pnls)
            result.best_trade_pnl_pct = max(pnls)
            result.worst_trade_pnl_pct = min(pnls)
            result.avg_confidence = sum(t.confidence for t in result.trades) / len(result.trades)
            result.avg_quality_score = sum(t.quality_score for t in result.trades) / len(result.trades)

            # Profit factor
            gross_profit = sum(t.pnl_pct for t in result.trades if t.pnl_pct > 0)
            gross_loss = sum(abs(t.pnl_pct) for t in result.trades if t.pnl_pct < 0)
            result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

            # Max drawdown from equity curve
            if result.equity_curve:
                peak = result.equity_curve[0]
                max_dd = 0.0
                for eq in result.equity_curve:
                    if eq > peak:
                        peak = eq
                    dd = (peak - eq) / peak if peak > 0 else 0
                    if dd > max_dd:
                        max_dd = dd
                result.max_drawdown = max_dd

            # Sharpe ratio (uses module-level numpy import)
            returns = [t.pnl_pct / 100 for t in result.trades]
            if len(returns) >= 2:
                mean_ret = np.mean(returns)
                std_ret = np.std(returns, ddof=1)
                if std_ret > 0:
                    result.sharpe_ratio = (mean_ret / std_ret) * (252 ** 0.5)

        storage.close()
        return result
