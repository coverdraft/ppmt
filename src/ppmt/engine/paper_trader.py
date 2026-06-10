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
) -> dict:
    """
    Living Trie: Record a trade's outcome back into the Trie.

    This is the core of the "Living Trie" concept — the Trie learns from
    its own trading results. Every closed trade becomes a new observation
    that updates the node's metadata, creating a feedback loop:

      Trie predicts → Trade executes → Outcome observed → Trie updated

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

    Returns:
        Dict with 'observations' and 'new_nodes' counts
    """
    from ppmt.core.metadata import BlockLifecycleMetadata

    observations = 0
    new_nodes = 0

    if not trade.matched_pattern:
        return {"observations": 0, "new_nodes": 0}

    # 1. Find the Trie node for the matched entry pattern
    #    Try exact match first, then progressively shorter prefixes
    node = trie.search(trade.matched_pattern)
    if node is None:
        # Try shorter prefixes (the prediction may have used a prefix match)
        for prefix_len in range(len(trade.matched_pattern) - 1, 0, -1):
            node = trie.search(trade.matched_pattern[:prefix_len])
            if node is not None:
                break

    if node is None:
        # Pattern not in Trie at all — create the entry as a new pattern
        trie.insert_with_observations(
            symbols=trade.matched_pattern,
            move_pct=trade.actual_move_pct,
            drawdown_pct=min(trade.actual_move_pct, 0) if trade.actual_move_pct < 0 else 0,
            favorable_pct=max(trade.actual_move_pct, 0) if trade.actual_move_pct > 0 else 0,
            duration=max(1, exit_sym_idx - trade.entry_sym_idx) if trade.entry_sym_idx > 0 else 1,
            won=trade.pnl_pct > 0,
            next_symbol=next_symbol,
        )
        new_nodes += 1
        observations += 1
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
    )
    observations += 1

    # 3. If next_symbol is NOT already a child, add it — the Trie GROWS
    if next_symbol and not node.has_child(next_symbol):
        extended_pattern = trade.matched_pattern + [next_symbol]
        child_node = trie.insert(extended_pattern)
        child_node.metadata.update_from_observation(
            move_pct=trade.actual_move_pct * 0.5,
            drawdown_pct=max_dd_pct,
            favorable_pct=max_fav_pct,
            duration=duration,
            won=won,
            next_symbol=None,
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

    sax_alphabet_size: int = 8
    """SAX alphabet size."""

    sax_window_size: int = 10
    """SAX window size."""

    sax_strategy: str = "ohlcv"
    """SAX encoding strategy."""

    min_confidence: float = 0.10
    """Minimum confidence to generate entry signal.
    v0.3.1: Kept at 0.10 (v0.2.8/v0.3.0 value). The Living Trie's
    accumulated metadata naturally produces higher confidence for
    validated patterns, making this threshold self-adjusting.
    With fresh tries (low avg confidence), the adaptive scaling
    in run() raises the effective threshold automatically."""

    min_quality_score: float = 0.0
    """Minimum quality score to enter a trade.
    v0.3.0: set to 0.0 — quality filtering is handled by the Living
    Trie metadata and min_confidence. Explicit quality thresholds
    have historically caused more harm than good by filtering
    valid entries."""

    min_risk_reward: float = 1.0
    """Minimum risk:reward ratio for entry.
    Kept at 1.0 to allow SHORT trades with R:R=1.5."""

    start_offset: int = 200
    """Number of initial candles to skip (warm-up for SAX encoding)."""

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
    """Catastrophic loss threshold (percentage). v0.3.0: DISABLED (0.0).
    v0.2.10's catastrophic protection cut winners short — trades that
    temporarily exceeded -5% unrealized loss often reversed to reach
    take_profit. The v0.2.8 baseline (no catastrophic protection)
    produced +1578% P&L precisely because it let trades breathe.
    Set to a non-zero value (e.g., 8.0) to re-enable as a safety net."""

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
            min_confidence=0.0,           # Checked in paper_trader, not RiskManager
        )

    def run(self) -> PaperTraderResult:
        """
        Run paper trading simulation on stored historical data.

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

        # Try to load existing Tries, or build new ones
        trie = storage.load_trie(cfg.symbol, "n3")
        initial_pattern_count = 0
        if trie is None:
            console.print(f"[yellow]No Trie for {cfg.symbol}. Building from data...[/yellow]")
            engine = PPMT(
                symbol=cfg.symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=cfg.sax_alphabet_size,
                sax_window_size=cfg.sax_window_size,
                sax_strategy=cfg.sax_strategy,
                weight_profile=info.weight_profile,
            )
            engine.build(df, pattern_length=cfg.pattern_length)
            trie = engine.trie_n3
        else:
            initial_pattern_count = trie.pattern_count
            console.print(f"[green]Loaded N3 Trie for {cfg.symbol} ({trie.pattern_count} patterns)[/green]")

        # CRITICAL: Propagate metadata so intermediate nodes have statistics
        trie.propagate_metadata()
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

        # Encode the FULL DataFrame once (same z-score context as during build)
        all_sax_symbols = sax_encoder.encode(df)
        if not all_sax_symbols:
            console.print(f"[red]Could not SAX encode data for {cfg.symbol}.[/red]")
            return PaperTraderResult(symbol=cfg.symbol, timeframe=cfg.timeframe)

        console.print(f"  SAX symbols: {len(all_sax_symbols)} (from {len(df)} candles)")

        # Create engines
        pred_engine = PredictionEngine(trie, prediction_depth=cfg.pattern_length)
        risk_mgr = RiskManager(capital=cfg.initial_capital, config=self.risk_config)

        # v0.3.1: Adaptive confidence scaling for fresh tries
        # When the trie has sparse metadata (low avg confidence), many predictions
        # barely pass the 0.10 threshold, leading to low-quality trades (46.1% WR
        # with fresh trie vs 66.1% with rich trie). We detect this by sampling
        # the trie's root metadata — if the average confidence is low, we scale
        # up the min_confidence threshold proportionally.
        root_meta = trie.root.metadata
        if root_meta.historical_count > 0:
            avg_node_confidence = root_meta.confidence
            if avg_node_confidence < 0.15:
                # Fresh trie with sparse metadata — raise threshold
                # Scale: at avg_conf=0.10, multiply by 1.5; at 0.15, multiply by 1.0
                adaptive_scale = 0.15 / max(avg_node_confidence, 0.05)
                adaptive_scale = min(adaptive_scale, 2.0)  # Cap at 2x
                cfg.min_confidence = min(cfg.min_confidence * adaptive_scale, 0.20)
                console.print(f"  [yellow]Adaptive confidence: trie avg={avg_node_confidence:.1%}, "
                              f"min_confidence scaled to {cfg.min_confidence:.0%}[/yellow]")
            else:
                console.print(f"  [green]Trie metadata quality: good (avg confidence={avg_node_confidence:.1%})[/green]")

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

        living_trie_status = "ON" if cfg.living_trie else "OFF"
        console.print(f"\n[bold cyan]Starting Paper Trading: {cfg.symbol} ({cfg.timeframe})[/bold cyan]")
        console.print(f"  Capital: ${cfg.initial_capital:,.2f}")
        console.print(f"  Data: {len(df)} candles, starting from index {start_candle}")
        console.print(f"  Trie: {trie.pattern_count} patterns")
        console.print(f"  Min confidence: {cfg.min_confidence:.0%} | Min quality: {cfg.min_quality_score:.2f}")
        console.print(f"  Entry: move > 1.0%, probability > 20%, ATR-based SL/TP")
        console.print(f"  LONG SL: max(ATR*1.5, 1.5%) cap 5% | SHORT SL: max(ATR*2.0, 2.0%) cap 7%")
        cat_status = f"{cfg.catastrophic_loss_pct:.0f}%" if cfg.catastrophic_loss_pct > 0 else "OFF"
        console.print(f"  Catastrophic protection: {cat_status}")
        console.print(f"  Trailing stop: activates at 75% of TP distance")
        console.print(f"  Pattern break grace: {cfg.pattern_break_grace} consecutive")
        console.print(f"  Re-entry cooldown: {cfg.reentry_cooldown} symbols after loss")
        console.print(f"  Living Trie: [bold]{living_trie_status}[/bold]\n")

        # We iterate over SAX symbol positions
        start_sym_idx = start_candle // cfg.sax_window_size
        if start_sym_idx < cfg.pattern_length:
            start_sym_idx = cfg.pattern_length

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

        for sym_idx in range(start_sym_idx, len(all_sax_symbols)):
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
                                    trie, current_position, sym_idx, next_sym
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
                            trie, current_position, sym_idx, next_sym
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
                            trie, current_position, sym_idx, next_sym
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
            # PHASE 2: Pattern break check with grace period
            # v0.2.9: Instead of closing on the FIRST pattern break, we
            # wait for N consecutive breaks (default 2). A single break
            # may be noise; two consecutive breaks confirm the pattern
            # has actually changed.
            # ================================================================
            if current_position is not None and len(current_symbols) >= 2:
                pattern_to_check = current_symbols[:-1]
                latest_symbol = current_symbols[-1]
                continues, _ = trie.check_continuation(pattern_to_check, latest_symbol)

                if not continues and current_position.confidence > 0:
                    consecutive_breaks += 1
                    if consecutive_breaks >= cfg.pattern_break_grace:
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

                pred_count += 1

                current_price = df_close[last_candle_idx]

                try:
                    prediction = pred_engine.predict(
                        current_symbols=current_symbols,
                        entry_price=current_price,
                        timeframe_hours=tf_hours,
                        symbol=cfg.symbol,
                    )
                except Exception:
                    continue

                if prediction.direction == "FLAT" or prediction.confidence <= 0:
                    continue

                pred_with_direction += 1

                # Effective minimum confidence
                effective_min_conf = cfg.min_confidence
                if prediction.overall_probability > 0.5:
                    effective_min_conf = max(cfg.min_confidence * 0.5, 0.05)

                # SHORT signals require higher confidence (BTC trends up)
                # v0.3.1: max(conf * 1.5, 0.15) — less strict than v0.2.10's max(conf*2, 0.20).
                # The v0.2.10 threshold was too restrictive — only 1 SHORT trade in 283
                # with the rich trie. Lowering to 1.5x/0.15 allows more SHORT entries
                # while still filtering the weakest signals.
                if prediction.direction == "SHORT":
                    effective_min_conf = max(effective_min_conf * 1.5, 0.15)

                # Entry conditions
                if (prediction.direction != "FLAT"
                    and prediction.confidence >= effective_min_conf
                    and abs(prediction.expected_total_move_pct) > 1.0
                    and prediction.overall_probability > 0.2):

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

                    # v0.3.0: Reverted to v0.2.8 SL/TP parameters
                    # LONG: SL = max(ATR*1.5, 1.5%), cap 5% → TP = SL*2.0
                    # SHORT: SL = max(ATR*2.0, 2.0%), cap 7% → TP = SL*1.5
                    # v0.2.10's LONG SL floor of 2.0% was wider but the
                    # trailing stop + catastrophic protection combo negated
                    # the benefit. v0.2.8's 1.5% with simple SL/TP works.
                    if prediction.direction == "LONG":
                        sl_distance_pct = min(max(current_atr_pct * 1.5, 1.5), 5.0)
                        tp_distance_pct = sl_distance_pct * 2.0  # R:R = 2.0
                    else:  # SHORT
                        sl_distance_pct = min(max(current_atr_pct * 2.0, 2.0), 7.0)
                        tp_distance_pct = sl_distance_pct * 1.5  # R:R = 1.5

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

                    signal = Signal(
                        signal_type=signal_type,
                        confidence=prediction.confidence,
                        symbol=cfg.symbol,
                        entry_price=current_price,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        expected_move_pct=prediction.expected_total_move_pct,
                        risk_reward_ratio=risk_reward,
                        win_rate=prediction.overall_probability,
                        historical_count=100,
                        matched_pattern=current_symbols,
                    )
                    signal.quality_score = signal.compute_quality_score()
                    signal.sizing_multiplier = signal.compute_sizing_multiplier()

                    # v0.3.0: Quality score filter only if min_quality_score > 0
                    # (default is 0.0 = disabled). The Living Trie metadata
                    # naturally handles quality filtering through confidence.
                    if cfg.min_quality_score > 0 and signal.quality_score < cfg.min_quality_score:
                        risk_reject_reasons["low_quality"] = risk_reject_reasons.get("low_quality", 0) + 1
                        continue

                    # Metadata sizing
                    mock_meta = BlockLifecycleMetadata(
                        win_rate=signal.win_rate,
                        expected_move_pct=signal.expected_move_pct,
                        max_drawdown_pct=-sl_distance_pct,
                        historical_count=100,
                    )
                    signal.probability_of_success = mock_meta.probability_of_success
                    signal.expected_profit_ahead = mock_meta.expected_profit_ahead
                    signal.metadata_sizing_signal = mock_meta.sizing_signal

                    # Risk check
                    can_open, reason = risk_mgr.can_open(signal, info.asset_class)
                    if can_open:
                        size = risk_mgr.calculate_position_size(signal)
                        position = risk_mgr.open_position(signal, size)

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
                    trie, current_position, len(all_sax_symbols) - 1, None
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
            console.print(f"  [green]Updated Trie saved to storage[/green]")

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
