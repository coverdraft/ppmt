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

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.core.sax import SAXEncoder
from ppmt.engine.ppmt import PPMT
from ppmt.engine.prediction import PredictionEngine
from ppmt.engine.signal import SignalType, Signal
from ppmt.risk.manager import RiskManager, RiskConfig


console = Console()


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

    min_confidence: float = 0.60
    """Minimum confidence to generate entry signal."""

    min_risk_reward: float = 1.5
    """Minimum risk:reward ratio."""

    start_offset: int = 200
    """Number of initial candles to skip (warm-up for SAX encoding)."""

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
    """Why the trade closed: 'take_profit', 'stop_loss', 'pattern_break', 'end_of_data'"""


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
            base_position_size_pct=0.02,
            max_position_size_pct=0.06,
            min_position_size_pct=0.005,
        )

    def run(self) -> PaperTraderResult:
        """
        Run paper trading simulation on stored historical data.

        Steps:
        1. Load OHLCV data from storage
        2. Build PPMT engine from stored Tries (or build from data)
        3. Step through candles from warm-up offset
        4. At each candle:
           a. Encode recent data to SAX
           b. Match pattern in Trie
           c. Generate prediction
           d. If no position and signal is strong → enter
           e. If in position → check SL/TP/pattern break
        5. Track all trades and equity curve
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
            console.print(f"[green]Loaded N3 Trie for {cfg.symbol} ({trie.pattern_count} patterns)[/green]")

        # Create SAX encoder
        sax_encoder = SAXEncoder(
            alphabet_size=cfg.sax_alphabet_size,
            window_size=cfg.sax_window_size,
            strategy=cfg.sax_strategy,
        )

        # Create engines
        pred_engine = PredictionEngine(trie, prediction_depth=cfg.pattern_length)
        risk_mgr = RiskManager(capital=cfg.initial_capital, config=self.risk_config)

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

        # Start from warm-up offset
        start = cfg.start_offset
        if start >= len(df):
            console.print(f"[red]Not enough data. Need at least {start} candles, have {len(df)}.[/red]")
            return result

        console.print(f"\n[bold cyan]Starting Paper Trading: {cfg.symbol} ({cfg.timeframe})[/bold cyan]")
        console.print(f"  Capital: ${cfg.initial_capital:,.2f}")
        console.print(f"  Data: {len(df)} candles, starting from index {start}")
        console.print(f"  Trie: {trie.pattern_count} patterns\n")

        for i in range(start, len(df)):
            # Get data up to current candle
            window_df = df.iloc[max(0, i - 100):i + 1]
            current_price = float(df["close"].iloc[i])
            current_time = str(df.index[i]) if hasattr(df.index, 'strftime') else str(i)

            # Check SL/TP for open position
            if current_position is not None:
                sl_hit = risk_mgr.check_stop_loss(cfg.symbol, current_price)
                tp_hit = risk_mgr.check_take_profit(cfg.symbol, current_price)

                if sl_hit:
                    # Close at stop loss
                    _, pnl = risk_mgr.close_position(cfg.symbol, current_price)
                    current_position.exit_price = current_price
                    current_position.exit_time = current_time
                    current_position.pnl = pnl
                    if current_position.direction == "LONG":
                        current_position.pnl_pct = (current_price - current_position.entry_price) / current_position.entry_price * 100
                    else:
                        current_position.pnl_pct = (current_position.entry_price - current_price) / current_position.entry_price * 100
                    current_position.actual_move_pct = current_position.pnl_pct
                    current_position.exit_reason = "stop_loss"

                    result.trades.append(current_position)
                    trade_counter += 1
                    current_position = None

                    # Update equity curve
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

                    result.trades.append(current_position)
                    trade_counter += 1
                    current_position = None

                    result.equity_curve.append(risk_mgr.capital)
                    result.capital_history.append(risk_mgr.capital)
                    if risk_mgr.capital > peak_capital:
                        peak_capital = risk_mgr.capital
                    continue

                # Update position unrealized P&L
                risk_mgr.update_position(cfg.symbol, current_price)

            # Encode recent data to SAX
            try:
                sax_symbols = sax_encoder.encode(window_df)
            except Exception:
                continue

            if not sax_symbols or len(sax_symbols) < 3:
                continue

            # Use last N symbols as current pattern
            current_symbols = sax_symbols[-cfg.pattern_length:] if len(sax_symbols) >= cfg.pattern_length else sax_symbols

            # If in position, check for pattern break
            if current_position is not None and len(current_symbols) >= 2:
                # Check if the latest symbol continues the pattern
                pattern_to_check = current_symbols[:-1]
                latest_symbol = current_symbols[-1]
                continues, _ = trie.check_continuation(pattern_to_check, latest_symbol)

                if not continues and current_position.confidence > 0:
                    # Pattern break - close position
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

                    result.trades.append(current_position)
                    trade_counter += 1
                    current_position = None

                    result.equity_curve.append(risk_mgr.capital)
                    result.capital_history.append(risk_mgr.capital)
                    if risk_mgr.capital > peak_capital:
                        peak_capital = risk_mgr.capital
                    continue

            # If no position, try to generate entry signal
            if current_position is None:
                prediction = None

                # Try progressively shorter patterns for a match
                for pat_len in range(len(current_symbols), 1, -1):
                    try:
                        candidate = current_symbols[-pat_len:]
                        pred = pred_engine.predict(
                            current_symbols=candidate,
                            entry_price=current_price,
                            timeframe_hours=tf_hours,
                            symbol=cfg.symbol,
                        )
                        if pred.direction != "FLAT" and pred.confidence > 0:
                            prediction = pred
                            break
                    except Exception:
                        continue

                if prediction is None:
                    continue

                # Check if prediction is strong enough for entry
                # Use min_confidence but allow lower if probability is high
                effective_min_conf = cfg.min_confidence
                if prediction.overall_probability > 0.5:
                    effective_min_conf = max(cfg.min_confidence * 0.5, 0.10)

                if (prediction.direction != "FLAT"
                    and prediction.confidence >= effective_min_conf
                    and abs(prediction.expected_total_move_pct) > 0.3
                    and prediction.overall_probability > 0.2):

                    # Create signal
                    from ppmt.engine.signal import Signal, SignalType
                    from ppmt.core.metadata import BlockLifecycleMetadata

                    signal_type = (
                        SignalType.ENTRY_LONG if prediction.direction == "LONG"
                        else SignalType.ENTRY_SHORT
                    )

                    # Compute SL/TP from prediction
                    sl_distance = abs(prediction.pattern_break_probability) * 2 + 0.01
                    if prediction.direction == "LONG":
                        sl_price = current_price * (1 - sl_distance)
                        tp_price = prediction.predicted_target or current_price * (1 + abs(prediction.expected_total_move_pct) / 100)
                    else:
                        sl_price = current_price * (1 + sl_distance)
                        tp_price = prediction.predicted_target or current_price * (1 - abs(prediction.expected_total_move_pct) / 100)

                    signal = Signal(
                        signal_type=signal_type,
                        confidence=prediction.confidence,
                        symbol=cfg.symbol,
                        entry_price=current_price,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        expected_move_pct=prediction.expected_total_move_pct,
                        risk_reward_ratio=abs(prediction.expected_total_move_pct / (sl_distance * 100)) if sl_distance > 0 else 0,
                        win_rate=prediction.overall_probability,
                        historical_count=100,
                        matched_pattern=current_symbols,
                    )
                    signal.quality_score = signal.compute_quality_score()
                    signal.sizing_multiplier = signal.compute_sizing_multiplier()

                    # Metadata sizing
                    mock_meta = BlockLifecycleMetadata(
                        win_rate=signal.win_rate,
                        expected_move_pct=signal.expected_move_pct,
                        max_drawdown_pct=-sl_distance * 100,
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
                        )

            # Record equity curve periodically
            if i % 10 == 0:
                unrealized_capital = risk_mgr.capital
                result.equity_curve.append(unrealized_capital)
                result.capital_history.append(unrealized_capital)

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
            result.trades.append(current_position)

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

            # Sharpe ratio
            returns = [t.pnl_pct / 100 for t in result.trades]
            if len(returns) >= 2:
                import numpy as np
                mean_ret = np.mean(returns)
                std_ret = np.std(returns, ddof=1)
                if std_ret > 0:
                    result.sharpe_ratio = (mean_ret / std_ret) * (252 ** 0.5)

        storage.close()
        return result
