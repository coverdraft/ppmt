"""
PPMT Terminal CLI - Command Line Interface

Usage:
  ppmt init                          Initialize database and config
  ppmt ingest --symbol BTC/USDT      Fetch and store historical data
  ppmt build --symbol BTC/USDT       Build Trie from stored data
  ppmt predict --symbol BTC/USDT     Show prediction from current pattern
  ppmt run --symbol BTC/USDT         Real-time pattern matching + dashboard
  ppmt terminal                      Launch web dashboard
  ppmt scan                          Scan and analyze assets
  ppmt stats --symbol BTC/USDT       Show pattern statistics
  ppmt list                          List tracked assets
  ppmt portfolio                     Portfolio and money management
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from ppmt.data.storage import PPMTStorage
from ppmt.data.collector import DataCollector
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.ppmt import PPMT
from ppmt.engine.prediction import PredictionEngine
from ppmt.engine.realtime import format_realtime_result

console = Console()

CONFIG_DIR = os.path.expanduser("~/.ppmt")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.yaml")


def load_config() -> dict:
    """Load configuration from file."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    return {}


@click.group()
@click.version_option(version="0.39.4")
def cli():
    """PPMT Terminal - Autonomous Pattern-Based Trading Terminal"""
    pass


@cli.command()
def init():
    """Initialize PPMT database and configuration."""
    os.makedirs(CONFIG_DIR, exist_ok=True)

    # Create default config if not exists
    if not os.path.exists(CONFIG_FILE):
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "config",
            "default.yaml",
        )
        if os.path.exists(config_path):
            import shutil
            shutil.copy(config_path, CONFIG_FILE)
            console.print(f"[green]Created config at {CONFIG_FILE}[/green]")
        else:
            # Write minimal config
            with open(CONFIG_FILE, "w") as f:
                yaml.dump({"sax": {"alphabet_size": 8, "window_size": 10}}, f)
            console.print(f"[green]Created minimal config at {CONFIG_FILE}[/green]")
    else:
        console.print(f"[yellow]Config already exists at {CONFIG_FILE}[/yellow]")

    # Initialize database
    storage = PPMTStorage()
    storage.close()

    console.print("[green]PPMT initialized successfully![/green]")
    console.print(f"  Config: {CONFIG_FILE}")
    console.print(f"  Database: {os.path.expanduser('~/.ppmt/ppmt.db')}")


@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair (e.g., BTC/USDT)")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--days", "-d", default=365, type=int, help="Days of history to fetch")
@click.option("--exchange", "-e", default="binance", help="Exchange name")
@click.option("--csv", "csv_path", default=None, help="Import from CSV file instead of exchange")
def ingest(symbol: str, timeframe: str, days: int, exchange: str, csv_path: str):
    """Fetch and store historical OHLCV data."""
    config = load_config()
    storage = PPMTStorage()

    collector = DataCollector(exchange=exchange, storage=storage)

    try:
        if csv_path:
            # Import from CSV (works without ccxt)
            console.print(f"[cyan]Importing {symbol} ({timeframe}) from CSV...[/cyan]")
            df = collector.import_csv(symbol, timeframe, csv_path)
        else:
            # Fetch from exchange (requires ccxt / Python 3.10+)
            console.print(f"[cyan]Fetching {symbol} ({timeframe}) from {exchange}...[/cyan]")
            df = collector.fetch_and_save(symbol, timeframe, days)

        if df.empty:
            console.print("[red]No data fetched. Check symbol and exchange.[/red]")
            return

        # Classify asset
        classifier = AssetClassifier()
        info = classifier.classify(symbol)
        storage.register_asset(symbol, info.asset_class)

        console.print(f"[green]Fetched {len(df)} candles[/green]")
        console.print(f"  Symbol: {symbol}")
        console.print(f"  Asset Class: {info.asset_class}")
        console.print(f"  Weight Profile: {info.weight_profile}")
        if not df.empty and hasattr(df, 'index') and len(df.index) > 0:
            console.print(f"  Date Range: {df.index[0]} → {df.index[-1]}")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        collector.close()


@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--pattern-length", "-p", default=5, type=int, help="SAX blocks per pattern")
def build(symbol: str, timeframe: str, pattern_length: int):
    """Build PPMT Trie from stored data."""
    config = load_config()
    storage = PPMTStorage()

    # Load data
    df = storage.load_ohlcv(symbol, timeframe)
    if df.empty:
        console.print(f"[red]No data found for {symbol}. Run 'ppmt ingest' first.[/red]")
        return

    console.print(f"[cyan]Building PPMT for {symbol} ({len(df)} candles)...[/cyan]")

    # Classify asset
    classifier = AssetClassifier()
    info = classifier.classify(symbol)

    # Create engine
    sax_config = config.get("sax", {})
    engine = PPMT(
        symbol=symbol,
        asset_class=info.asset_class,
        sax_alphabet_size=sax_config.get("alphabet_size", 8),
        sax_window_size=sax_config.get("window_size", 10),
        sax_strategy=sax_config.get("strategy", "ohlcv"),
        weight_profile=info.weight_profile,
    )

    # Build Trie
    count = engine.build(df, pattern_length=pattern_length)

    # Save Tries
    for level, trie in [
        ("n1", engine.trie_n1),
        ("n2", engine.trie_n2),
        ("n3", engine.trie_n3),
        ("n4", engine.trie_n4),
    ]:
        storage.save_trie(symbol, level, trie)
        console.print(f"  N{level[-1]} Trie: {trie.pattern_count} patterns, max depth {trie.max_depth}")

    # Save engine state
    storage.save_engine_state(symbol, engine.get_stats())

    console.print(f"[green]Built {count} patterns for {symbol}[/green]")

    # Show stats
    stats = engine.get_stats()
    console.print(f"  Weights: {engine.weights}")

    storage.close()


@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
def stats(symbol: str, timeframe: str):
    """Show PPMT statistics for an asset."""
    storage = PPMTStorage()

    # Load engine state
    state = storage.load_engine_state(symbol)
    if state is None:
        console.print(f"[red]No engine state for {symbol}. Run 'ppmt build' first.[/red]")
        return

    # Load Tries
    table = Table(title=f"PPMT Stats: {symbol}")
    table.add_column("Level", style="cyan")
    table.add_column("Patterns", justify="right")
    table.add_column("Max Depth", justify="right")

    for level in ["n1", "n2", "n3", "n4"]:
        trie = storage.load_trie(symbol, level)
        if trie:
            table.add_row(
                f"N{level[-1]} ({trie.name})",
                str(trie.pattern_count),
                str(trie.max_depth),
            )
        else:
            table.add_row(f"N{level[-1]}", "0", "0")

    console.print(table)

    # Show weights
    if state:
        console.print(f"\nWeights: {state.get('weight_profile', 'default')}")
        console.print(f"Asset Class: {state.get('asset_class', 'unknown')}")
        console.print(f"Total Patterns Built: {state.get('total_patterns_built', 0)}")

    # Show stored candles
    df = storage.load_ohlcv(symbol, timeframe)
    console.print(f"Stored Candles: {len(df)}")

    storage.close()


@cli.command("list")
def list_assets():
    """List all tracked assets."""
    storage = PPMTStorage()
    assets = storage.get_assets()

    if not assets:
        console.print("[yellow]No assets tracked. Run 'ppmt ingest' to add one.[/yellow]")
        return

    table = Table(title="Tracked Assets")
    table.add_column("Symbol", style="cyan")
    table.add_column("Asset Class")
    table.add_column("Candles", justify="right")

    for asset in assets:
        table.add_row(
            asset["symbol"],
            asset["asset_class"],
            str(asset["candle_count"]),
        )

    console.print(table)
    storage.close()


@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--depth", "-d", default=5, type=int, help="Prediction depth (SAX blocks)")
@click.option("--price", "-p", default=None, type=float, help="Current price for price levels")
def predict(symbol: str, timeframe: str, depth: int, price: float):
    """Show PPMT prediction for the current pattern state."""
    config = load_config()
    storage = PPMTStorage()

    # Load data and find the most recent SAX symbols
    df = storage.load_ohlcv(symbol, timeframe)
    if df.empty:
        console.print(f"[red]No data found for {symbol}. Run 'ppmt ingest' first.[/red]")
        return

    # Load the per-asset Trie (N3)
    trie = storage.load_trie(symbol, "n3")
    if trie is None:
        console.print(f"[red]No Trie built for {symbol}. Run 'ppmt build' first.[/red]")
        return

    # Encode recent data to SAX symbols
    sax_config = config.get("sax", {})
    from ppmt.core.sax import SAXEncoder
    encoder = SAXEncoder(
        alphabet_size=sax_config.get("alphabet_size", 8),
        window_size=sax_config.get("window_size", 10),
        strategy=sax_config.get("strategy", "ohlcv"),
    )

    # Use last N candles to get recent SAX symbols
    recent_df = df.tail(100)
    symbols = encoder.encode(recent_df)

    if not symbols:
        console.print("[red]Could not encode recent data.[/red]")
        return

    # Use last 5 SAX blocks as current pattern
    current_symbols = symbols[-5:] if len(symbols) >= 5 else symbols

    # Get current price
    current_price = price or float(df["close"].iloc[-1])

    # Timeframe to hours
    tf_hours = {"1m": 1/60, "5m": 5/60, "15m": 15/60, "1h": 1, "4h": 4, "1d": 24}.get(timeframe, 1)

    # Generate prediction
    pred_engine = PredictionEngine(trie, prediction_depth=depth)
    prediction = pred_engine.predict(
        current_symbols=current_symbols,
        entry_price=current_price,
        timeframe_hours=tf_hours,
        symbol=symbol,
    )

    # Display
    console.print(prediction.format_summary(timeframe_hours=tf_hours))

    # Also show sizing recommendation
    if prediction.overall_probability > 0:
        from ppmt.engine.signal import Signal, SignalType
        mock_signal = Signal(
            signal_type=SignalType.ENTRY_LONG if prediction.direction == "LONG" else SignalType.ENTRY_SHORT,
            confidence=prediction.confidence,
            win_rate=prediction.predicted_path[-1].win_rate if prediction.predicted_path else 0,
            risk_reward_ratio=abs(prediction.expected_total_move_pct / prediction.pattern_break_probability) if prediction.pattern_break_probability > 0 else 0,
            historical_count=100,
        )
        mock_signal.quality_score = mock_signal.compute_quality_score()
        mock_signal.sizing_multiplier = mock_signal.compute_sizing_multiplier()

        # V3: Show metadata-driven sizing signal
        from ppmt.core.metadata import BlockLifecycleMetadata
        mock_meta = BlockLifecycleMetadata(
            win_rate=mock_signal.win_rate,
            expected_move_pct=prediction.expected_total_move_pct,
            max_drawdown_pct=prediction.pattern_break_probability * 2 if prediction.pattern_break_probability > 0 else -1.0,
            historical_count=100,
        )
        mock_signal.probability_of_success = mock_meta.probability_of_success
        mock_signal.expected_profit_ahead = mock_meta.expected_profit_ahead
        mock_signal.metadata_sizing_signal = mock_meta.sizing_signal

        console.print(f"\n[bold]Position Sizing (Metadata-Driven):[/bold]")
        console.print(f"  Quality Score: {mock_signal.quality_score:.2f}")
        console.print(f"  Probability of Success: {mock_signal.probability_of_success:.1%}")
        console.print(f"  Expected Profit Ahead: {mock_signal.expected_profit_ahead:+.2f}%")
        console.print(f"  [bold cyan]Sizing Signal: {mock_signal.metadata_sizing_signal:.2f}[/bold cyan]")

        # Sizing interpretation
        if mock_signal.metadata_sizing_signal >= 1.5:
            console.print(f"  → [bold green]2.0x base position (HIGH CONVICTION)[/bold green]")
        elif mock_signal.metadata_sizing_signal >= 1.0:
            console.print(f"  → [green]1.0x base position (NORMAL)[/green]")
        elif mock_signal.metadata_sizing_signal >= 0.5:
            console.print(f"  → [yellow]0.5x base position (LOW CONVICTION)[/yellow]")
        else:
            console.print(f"  → [red]0.25x base position or REJECT[/red]")

        # Forward prediction chain
        if prediction.predicted_path:
            console.print(f"\n[bold]Forward Prediction Chain:[/bold]")
            total_hours = 0
            for step in prediction.predicted_path:
                step_hours = step.estimated_candles * tf_hours
                total_hours += step_hours
                marker = "[green]✓[/green]" if step.is_continuation else "[red]✗[/red]"
                console.print(
                    f"  {marker} Block [{step.symbol}] "
                    f"prob={step.probability:.0%} "
                    f"move={step.cumulative_move_pct:>+5.2f}% "
                    f"candles={step.estimated_candles} "
                    f"~{step_hours:.1f}h "
                    f"wr={step.win_rate:.0%}"
                )
            console.print(f"  [bold]Total: {len(prediction.predicted_path)} blocks, ~{total_hours:.1f}h ahead[/bold]")

    storage.close()


@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--exchange", "-e", default="binance", help="Exchange (binance/bybit/mexc)")
@click.option("--capital", "-c", default=10000.0, type=float, help="Initial capital")
@click.option("--dry-run", is_flag=True, default=True, help="Paper trading (no real orders)")
@click.option("--live", "dry_run_false", is_flag=True, help="Execute REAL orders on exchange")
@click.option("--testnet", is_flag=True, default=False, help="Use exchange testnet (for order execution only)")
@click.option("--mainnet", "testnet_false", is_flag=True, help="Use exchange MAINNET (real money)")
@click.option("--api-key", envvar="PPMT_API_KEY", default="", help="Exchange API key")
@click.option("--api-secret", envvar="PPMT_API_SECRET", default="", help="Exchange API secret")
@click.option("--replay", is_flag=True, help="Replay historical data instead of live")
@click.option("--speed", default=0.0, type=float, help="Replay speed (0=max, 1=realtime, 10=10x)")
@click.option("--pattern-length", "-p", default=5, type=int, help="SAX blocks per pattern")
@click.option("--min-confidence", default=0.08, type=float, help="Minimum signal confidence (v0.21.0: lowered for Bayesian shrinkage)")
@click.option("--auto-calibrate", is_flag=True, default=True, help="Auto-calibrate SAX parameters")
@click.option("--no-calibrate", "no_calibrate", is_flag=True, help="Skip auto-calibration")
@click.option("--regime-aware", is_flag=True, default=True, help="Enable regime detection")
@click.option("--multi-level", is_flag=True, default=True, help="Enable 4-level matching")
@click.option("--leverage", "-l", default=1, type=int, help="Leverage (1=spot, 2-125=futures)")
@click.option("--auto/--manual", default=True, help="Auto mode (execute signals) or manual (display only)")
@click.option("--max-positions", default=5, type=int, help="Max simultaneous open positions")
@click.option("--max-exposure", default=0.80, type=float, help="Max portfolio exposure (0-1)")
@click.option("--kill-switch", default=0.95, type=float, help="Exposure % to trigger kill switch (0-1)")
@click.option("--daily-loss", default=0.05, type=float, help="Max daily loss (0-1)")
@click.option("--kelly", is_flag=True, default=True, help="Use Kelly Criterion sizing")
@click.option("--no-kelly", "no_kelly", is_flag=True, help="Disable Kelly Criterion sizing")
def run(
    symbol: str, timeframe: str, exchange: str, capital: float,
    dry_run: bool, dry_run_false: bool, testnet: bool, testnet_false: bool,
    api_key: str, api_secret: str,
    replay: bool, speed: float,
    pattern_length: int, min_confidence: float,
    auto_calibrate: bool, no_calibrate: bool,
    regime_aware: bool, multi_level: bool,
    leverage: int, auto: bool,
    max_positions: int, max_exposure: float,
    kill_switch: float, daily_loss: float,
    kelly: bool, no_kelly: bool,
):
    """Run real-time pattern matching and trading.

    v0.20.0: Supports Binance, Bybit, MEXC exchanges.
    WebSocket streaming, REST polling, and replay modes.

    Modes:
      --replay          Replay historical data (for testing)
      (default)         Live WebSocket streaming from exchange

    Examples:
      ppmt run -s BTC/USDT                      # Dry run with Binance WebSocket
      ppmt run -s BTC/USDT --replay             # Replay stored data
      ppmt run -s ETH/USDT -e bybit             # Bybit WebSocket
      ppmt run -s BTC/USDT -t 5m -e mexc        # MEXC 5-minute WebSocket
      ppmt run -s BTC/USDT --live --mainnet     # REAL trading on Binance
      ppmt run -s BTC/USDT -e mexc --live       # REAL trading on MEXC
    """
    # Resolve flag conflicts
    actual_dry_run = not dry_run_false  # --live flag disables dry-run
    actual_testnet = testnet and not testnet_false  # Testnet only if explicitly requested; --mainnet overrides
    # v0.29.0: For paper trading (dry_run), always use mainnet data feed.
    # Testnet is only for order execution routing, NOT for market data.
    if actual_dry_run and actual_testnet:
        actual_testnet = False
        console.print("[dim]Note: Using mainnet data feed (testnet only affects order execution)[/dim]")

    if no_calibrate:
        auto_calibrate = False

    if replay:
        # === REPLAY MODE ===
        from ppmt.engine.realtime import RealtimeTrader, ReplayConfig

        config = ReplayConfig(
            symbol=symbol,
            timeframe=timeframe,
            initial_capital=capital,
            speed=speed,
            pattern_length=pattern_length,
            min_confidence=min_confidence,
            auto_calibrate=auto_calibrate,
            regime_aware=regime_aware,
            use_multi_level=multi_level,
            use_token_profile=True,
        )

        trader = RealtimeTrader(config=config)
        result = trader.run_replay()

        console.print(format_realtime_result(result))

    else:
        # === LIVE MODE (WebSocket or REST) ===
        import asyncio
        from ppmt.engine.realtime import RealtimeTrader, LiveConfig

        if not actual_dry_run:
            if not api_key or not api_secret:
                console.print("[bold red]WARNING: Live trading requires API credentials![/bold red]")
                console.print("[dim]Set PPMT_API_KEY and PPMT_API_SECRET environment variables[/dim]")
                console.print("[dim]Or use --api-key and --api-secret options[/dim]")
                if not click.confirm("Continue without credentials?"):
                    return

        config = LiveConfig(
            symbol=symbol,
            timeframe=timeframe,
            initial_capital=capital,
            exchange=exchange,
            api_key=api_key,
            api_secret=api_secret,
            pattern_length=pattern_length,
            min_confidence=min_confidence,
            auto_calibrate=auto_calibrate,
            regime_aware=regime_aware,
            use_multi_level=multi_level,
            use_token_profile=True,
            testnet=actual_testnet,
            dry_run=actual_dry_run,
            leverage=leverage,
            auto_mode=auto,
            max_open_positions=max_positions,
            max_portfolio_exposure_pct=max_exposure,
            kill_switch_pct=kill_switch,
            daily_loss_limit_pct=daily_loss,
            use_kelly_sizing=kelly and not no_kelly,
        )

        trader = RealtimeTrader(config=config)

        try:
            result = asyncio.run(trader.run_live())
            console.print(format_realtime_result(result))
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped by user.[/yellow]")


if __name__ == "__main__":
    cli()


# ============================================================
# v0.13.0: BACKTEST & MONTE CARLO COMMANDS
# ============================================================

@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--capital", "-c", default=10000.0, type=float, help="Initial capital")
@click.option("--pattern-length", "-p", default=5, type=int, help="SAX blocks per pattern")
@click.option("--min-confidence", default=0.08, type=float, help="Minimum signal confidence (v0.21.0: lowered for Bayesian shrinkage)")
@click.option("--start-offset", default=200, type=int, help="Candles to skip (warm-up)")
@click.option("--auto-calibrate", is_flag=True, default=True, help="Auto-calibrate SAX parameters")
@click.option("--no-calibrate", "no_calibrate", is_flag=True, help="Skip auto-calibration")
@click.option("--regime-aware", is_flag=True, default=True, help="Enable regime detection")
@click.option("--multi-level", is_flag=True, default=True, help="Enable 4-level matching")
@click.option("--output", "-o", default=None, help="Save results to JSON file")
def backtest(
    symbol: str, timeframe: str, capital: float,
    pattern_length: int, min_confidence: float, start_offset: int,
    auto_calibrate: bool, no_calibrate: bool,
    regime_aware: bool, multi_level: bool, output: str,
):
    """Run a full backtest on stored historical data.

    Uses the RealtimeTrader in replay mode to simulate streaming through
    historical candles, generating signals, and tracking P&L with
    the full PPMT pipeline (SAX → Trie → Match → Signal → Risk → Trade).

    This is the primary tool for validating the trading engine before
    deploying to live markets.

    Examples:
      ppmt backtest -s BTC/USDT                    # Default backtest
      ppmt backtest -s ETH/USDT -t 5m -c 50000     # 5-minute with 50k capital
      ppmt backtest -s BTC/USDT -o results.json     # Save results
    """
    if no_calibrate:
        auto_calibrate = False

    from ppmt.engine.realtime import RealtimeTrader, ReplayConfig

    config = ReplayConfig(
        symbol=symbol,
        timeframe=timeframe,
        initial_capital=capital,
        speed=0,  # Maximum speed for backtest
        pattern_length=pattern_length,
        min_confidence=min_confidence,
        start_offset=start_offset,
        auto_calibrate=auto_calibrate,
        regime_aware=regime_aware,
        use_multi_level=multi_level,
        use_token_profile=True,
        verbose=True,
    )

    console.print(f"[bold cyan]Running Backtest: {symbol} ({timeframe})[/bold cyan]")
    console.print(f"  Capital: ${capital:,.2f}")
    console.print(f"  Pattern Length: {pattern_length}")
    console.print(f"  Min Confidence: {min_confidence:.0%}")
    console.print(f"  Auto-calibrate: {'ON' if auto_calibrate else 'OFF'}")
    console.print(f"  Regime-aware: {'ON' if regime_aware else 'OFF'}")
    console.print(f"  Multi-level: {'ON' if multi_level else 'OFF'}")
    console.print()

    trader = RealtimeTrader(config=config)
    result = trader.run_replay()

    console.print(format_realtime_result(result))

    # Show trade details if any
    if result.trades:
        console.print(f"\n[bold]Trade Details:[/bold]")
        trade_table = Table(title="Backtest Trades")
        trade_table.add_column("#", justify="right", style="cyan")
        trade_table.add_column("Dir")
        trade_table.add_column("Entry", justify="right")
        trade_table.add_column("Exit", justify="right")
        trade_table.add_column("P&L%", justify="right")
        trade_table.add_column("Exit Reason")
        trade_table.add_column("Regime")

        for t in result.trades:
            pnl_color = "green" if t.pnl_pct >= 0 else "red"
            trade_table.add_row(
                str(t.trade_id),
                t.direction,
                f"${t.entry_price:,.2f}",
                f"${t.exit_price:,.2f}",
                f"[{pnl_color}]{t.pnl_pct:+.2f}%[/{pnl_color}]",
                t.exit_reason,
                t.regime,
            )

        console.print(trade_table)

    # Save to JSON if requested
    if output:
        import json
        from pathlib import Path
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path

        result_dict = {
            "symbol": result.symbol,
            "timeframe": result.timeframe,
            "mode": result.mode,
            "initial_capital": result.initial_capital,
            "final_capital": result.final_capital,
            "total_pnl": result.total_pnl,
            "total_pnl_pct": result.total_pnl_pct,
            "total_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "losing_trades": result.losing_trades,
            "win_rate": result.win_rate,
            "max_drawdown": result.max_drawdown,
            "candles_processed": result.candles_processed,
            "sax_symbols_produced": result.sax_symbols_produced,
            "signals_generated": result.signals_generated,
            "duration_seconds": result.duration_seconds,
            "trades": [
                {
                    "trade_id": t.trade_id,
                    "direction": t.direction,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "exit_reason": t.exit_reason,
                    "regime": t.regime,
                    "confidence": t.confidence,
                }
                for t in result.trades
            ],
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result_dict, f, indent=2, default=str)
        console.print(f"\n[green]Results saved to {output_path}[/green]")


@cli.command("monte-carlo")
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--capital", "-c", default=10000.0, type=float, help="Initial capital")
@click.option("--simulations", "-n", default=1000, type=int, help="Number of simulations")
@click.option("--pattern-length", "-p", default=5, type=int, help="SAX blocks per pattern")
@click.option("--min-confidence", default=0.08, type=float, help="Minimum signal confidence (v0.21.0: lowered for Bayesian shrinkage)")
@click.option("--auto-calibrate", is_flag=True, default=True, help="Auto-calibrate SAX parameters")
@click.option("--regime-aware", is_flag=True, default=True, help="Enable regime detection")
@click.option("--confidence-level", default=0.95, type=float, help="Confidence level (0-1)")
@click.option("--output", "-o", default=None, help="Save results to JSON file")
def monte_carlo(
    symbol: str, timeframe: str, capital: float,
    simulations: int, pattern_length: int, min_confidence: float,
    auto_calibrate: bool, regime_aware: bool,
    confidence_level: float, output: str,
):
    """Run Monte Carlo simulation on backtest results.

    First runs a backtest to collect trade P&Ls, then resamples
    them thousands of times to build confidence intervals for:
      - Risk of Ruin
      - Probability of Profit
      - Max Drawdown distribution
      - Sharpe Ratio distribution

    This provides statistical validation of the trading strategy.

    Examples:
      ppmt monte-carlo -s BTC/USDT                     # 1000 simulations
      ppmt monte-carlo -s ETH/USDT -n 5000             # 5000 simulations
      ppmt monte-carlo -s BTC/USDT -o mc_results.json   # Save results
    """
    from ppmt.engine.realtime import RealtimeTrader, ReplayConfig
    from ppmt.risk.monte_carlo import MonteCarloSimulator, MonteCarloConfig

    # Step 1: Run backtest to collect trades
    console.print(f"[bold cyan]Step 1/2: Running Backtest for {symbol}...[/bold cyan]")
    config = ReplayConfig(
        symbol=symbol,
        timeframe=timeframe,
        initial_capital=capital,
        speed=0,
        pattern_length=pattern_length,
        min_confidence=min_confidence,
        auto_calibrate=auto_calibrate,
        regime_aware=regime_aware,
        use_multi_level=True,
        use_token_profile=True,
        verbose=False,  # Quiet mode for backtest
    )

    trader = RealtimeTrader(config=config)
    result = trader.run_replay()

    if result.total_trades == 0:
        console.print("[yellow]No trades generated. Cannot run Monte Carlo.[/yellow]")
        console.print("[dim]Try lowering --min-confidence or ingesting more data.[/dim]")
        return

    console.print(f"  Backtest: {result.total_trades} trades, "
                  f"WR={result.win_rate:.1%}, "
                  f"P&L={result.total_pnl_pct:+.2f}%")

    # Step 2: Run Monte Carlo
    console.print(f"\n[bold cyan]Step 2/2: Running {simulations} Monte Carlo simulations...[/bold cyan]")

    # Extract trade PnL percentages as fractions (e.g., 0.05 = +5%)
    trade_pnls_pct = [t.pnl_pct / 100.0 for t in result.trades]

    mc_config = MonteCarloConfig(
        simulations=simulations,
        initial_capital=capital,
    )
    mc = MonteCarloSimulator()
    mc_results = mc.simulate(trades_pnl_pct=trade_pnls_pct, config=mc_config)

    # Display results
    console.print(f"\n[bold]Monte Carlo Simulation Results: {symbol} ({timeframe})[/bold]")
    console.print(f"  Base Trades:       {result.total_trades}")
    console.print(f"  Simulations:       {simulations}")
    console.print("")

    # Risk metrics
    risk_of_ruin = mc_results.risk_of_ruin
    prob_profit = mc_results.probability_of_profit
    mean_final = mc_results.mean_final_equity
    median_final = mc_results.median_final_equity

    console.print(f"  [bold]Risk of Ruin:[/bold]          {risk_of_ruin:.1%}")
    console.print(f"  [bold]Probability of Profit:[/bold]  {prob_profit:.1%}")
    console.print(f"  Mean Final Capital:    ${mean_final:,.2f}")
    console.print(f"  Median Final Capital:  ${median_final:,.2f}")

    # Confidence intervals for equity
    if mc_results.equity_percentiles:
        console.print(f"\n  [bold]Equity Confidence Intervals:[/bold]")
        for ci in mc_results.equity_percentiles:
            console.print(f"    P{ci.level:>2}: ${ci.value:>12,.2f}")

    # Drawdown intervals
    if mc_results.drawdown_percentiles:
        console.print(f"\n  [bold]Max Drawdown Confidence Intervals:[/bold]")
        for ci in mc_results.drawdown_percentiles:
            console.print(f"    P{ci.level:>2}: {ci.value * 100:>8.2f}%")

    # P95 max drawdown
    console.print(f"\n  P95 Max Drawdown:     {mc_results.p95_max_drawdown:.1%}")

    # Original path metrics
    if mc_results.original_metrics:
        om = mc_results.original_metrics
        console.print(f"\n  [bold]Original (Unshuffled) Path:[/bold]")
        console.print(f"    Final Equity:  ${om.final_equity:>12,.2f}")
        console.print(f"    Max Drawdown:  {om.max_drawdown:.1%}")
        console.print(f"    Sharpe Ratio:  {om.sharpe_ratio:.3f}")
        console.print(f"    Win Rate:      {om.win_rate:.1%}")
        pf_str = "INF" if om.profit_factor == float('inf') else f"{om.profit_factor:.2f}"
        console.print(f"    Profit Factor: {pf_str}")

    # Verdict — gate live trading (v0.24.0: comprehensive multi-factor verdict)
    # The old logic only checked risk_of_ruin, which meant a strategy with
    # 0% probability of profit but low ruin (slow bleed) was marked "LOW RISK".
    # Now we also check probability_of_profit, profit_factor, and win_rate.

    profit_factor = mc_results.original_metrics.profit_factor if mc_results.original_metrics else 0.0
    win_rate = mc_results.original_metrics.win_rate if mc_results.original_metrics else 0.0

    # Build a composite risk score (0-100, lower = safer)
    risk_score = 0

    # Factor 1: Risk of Ruin (0-40 points)
    if risk_of_ruin > 0.20:
        risk_score += 40
    elif risk_of_ruin > 0.10:
        risk_score += 30
    elif risk_of_ruin > 0.05:
        risk_score += 15
    else:
        risk_score += 0

    # Factor 2: Probability of Profit (0-30 points, inverted)
    if prob_profit < 0.10:
        risk_score += 30  # Almost never profitable
    elif prob_profit < 0.30:
        risk_score += 22
    elif prob_profit < 0.50:
        risk_score += 12
    elif prob_profit < 0.70:
        risk_score += 5
    else:
        risk_score += 0

    # Factor 3: Profit Factor (0-15 points)
    if profit_factor < 0.8:
        risk_score += 15  # Losing money consistently
    elif profit_factor < 1.0:
        risk_score += 10  # Marginal loser
    elif profit_factor < 1.2:
        risk_score += 5   # Marginal winner
    else:
        risk_score += 0

    # Factor 4: P95 Max Drawdown (0-15 points)
    if mc_results.p95_max_drawdown > 0.30:
        risk_score += 15
    elif mc_results.p95_max_drawdown > 0.20:
        risk_score += 8
    elif mc_results.p95_max_drawdown > 0.10:
        risk_score += 3
    else:
        risk_score += 0

    # Render verdict based on composite score
    if risk_score >= 50:
        console.print(f"\n  [bold red]VERDICT: HIGH RISK (score: {risk_score}/100)[/bold red]")
        if prob_profit < 0.10:
            console.print(f"  [red]Probability of profit is {prob_profit:.0%} — this strategy LOSES money.[/red]")
        if profit_factor < 1.0:
            console.print(f"  [red]Profit factor {profit_factor:.2f} < 1.0 — losses exceed gains.[/red]")
        console.print(f"  [red]DO NOT deploy this strategy with real money.[/red]")
        console.print(f"  [dim]Suggestions: improve signal quality, widen SL, reduce position size, or tune parameters.[/dim]")
    elif risk_score >= 30:
        console.print(f"\n  [bold yellow]VERDICT: MODERATE RISK (score: {risk_score}/100)[/bold yellow]")
        console.print(f"  [yellow]Risk of ruin: {risk_of_ruin:.0%} | Profit prob: {prob_profit:.0%} | PF: {profit_factor:.2f}[/yellow]")
        console.print(f"  [yellow]Consider reducing position size or improving signal filters before live trading.[/yellow]")
    else:
        console.print(f"\n  [bold green]VERDICT: LOW RISK (score: {risk_score}/100)[/bold green]")
        console.print(f"  [green]Risk of ruin: {risk_of_ruin:.0%} | Profit prob: {prob_profit:.0%} | PF: {profit_factor:.2f}[/green]")
        console.print(f"  [green]Strategy passed Monte Carlo validation. Safe to deploy.[/green]")

    # Save to JSON
    if output:
        import json as json_mod
        from pathlib import Path
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path

        # Build summary using the simulator's generate_summary method
        mc_dict = {
            "symbol": symbol,
            "timeframe": timeframe,
            "base_trades": result.total_trades,
            "base_win_rate": result.win_rate,
            "base_pnl_pct": result.total_pnl_pct,
            "simulations": simulations,
            "risk_of_ruin": risk_of_ruin,
            "probability_of_profit": prob_profit,
            "mean_final_equity": mean_final,
            "median_final_equity": median_final,
            "p95_max_drawdown": mc_results.p95_max_drawdown,
            "equity_percentiles": [
                {"level": ci.level, "value": ci.value}
                for ci in mc_results.equity_percentiles
            ],
            "drawdown_percentiles": [
                {"level": ci.level, "value": ci.value}
                for ci in mc_results.drawdown_percentiles
            ],
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json_mod.dump(mc_dict, f, indent=2, default=str)
        console.print(f"\n[green]Results saved to {output_path}[/green]")


@cli.command("validate")
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--pattern-length", "-p", default=5, type=int, help="SAX blocks per pattern")
@click.option("--output", "-o", default=None, help="Save validation report to JSON")
def validate(symbol: str, timeframe: str, pattern_length: int, output: str):
    """Run the full validation suite (OOS + Monte Carlo + Walk-Forward).

    Performs three statistical tests to determine if the strategy
    is robust, overfit, or marginal:

      P0 — Out-of-Sample Test: Train on 70%, test on 30%
      P1 — Monte Carlo Permutation: Randomize trade order 1000x
      P2 — Walk-Forward Analysis: Rolling window validation

    Composite verdict: ROBUST / MARGINAL / OVERFIT / INSUFFICIENT_DATA

    Examples:
      ppmt validate -s BTC/USDT
      ppmt validate -s ETH/USDT -t 5m -o validation.json
    """
    from ppmt.engine.validator import ValidationSuite

    console.print(f"[bold cyan]Running Validation Suite: {symbol} ({timeframe})[/bold cyan]")

    storage = PPMTStorage()
    df = storage.load_ohlcv(symbol, timeframe)
    if df.empty:
        console.print(f"[red]No data for {symbol}. Run 'ppmt ingest' first.[/red]")
        storage.close()
        return

    config = load_config()
    classifier = AssetClassifier()
    info = classifier.classify(symbol)

    sax_config = config.get("sax", {})

    suite = ValidationSuite(
        symbol=symbol,
        timeframe=timeframe,
        pattern_length=pattern_length,
        sax_alphabet_size=sax_config.get("alphabet_size", 8),
        sax_window_size=sax_config.get("window_size", 10),
        asset_class=info.asset_class,
    )

    results = suite.run(df)

    # Display results
    console.print(f"\n[bold]Validation Results: {symbol} ({timeframe})[/bold]")
    console.print(f"  Data: {len(df)} candles")

    for test_name, test_result in results.items():
        if isinstance(test_result, dict):
            status = test_result.get("verdict", "N/A")
            color = {"ROBUST": "green", "MARGINAL": "yellow", "OVERFIT": "red", "INSUFFICIENT_DATA": "dim"}.get(status, "white")
            console.print(f"  {test_name}: [{color}]{status}[/{color}]")
            for key, val in test_result.items():
                if key != "verdict" and isinstance(val, (int, float, str)):
                    console.print(f"    {key}: {val}")

    composite = results.get("composite_verdict", "UNKNOWN")
    composite_color = {"ROBUST": "bold green", "MARGINAL": "bold yellow", "OVERFIT": "bold red"}.get(composite, "white")
    console.print(f"\n  [bold]Composite Verdict:[/bold] [{composite_color}]{composite}[/{composite_color}]")

    # Save to JSON
    if output:
        import json
        from pathlib import Path
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert results for JSON serialization
        json_results = {}
        for key, val in results.items():
            if isinstance(val, dict):
                json_results[key] = {k: str(v) if not isinstance(v, (int, float, bool)) else v
                                     for k, v in val.items()}
            else:
                json_results[key] = str(val)

        json_results["symbol"] = symbol
        json_results["timeframe"] = timeframe
        json_results["data_candles"] = len(df)

        with open(output_path, "w") as f:
            json.dump(json_results, f, indent=2, default=str)
        console.print(f"\n[green]Validation report saved to {output_path}[/green]")

    storage.close()


# ============================================================
# v0.14.0: TERMINAL DASHBOARD & PORTFOLIO COMMANDS
# ============================================================

@cli.command()
@click.option("--host", "-h", default="localhost", help="Dashboard host")
@click.option("--port", "-p", default=8420, type=int, help="Dashboard port (default 8420)")
@click.option("--open-browser", is_flag=True, default=False, help="Open browser automatically")
def terminal(host: str, port: int, open_browser: bool):
    """Launch the PPMT Terminal web dashboard.

    v0.29.0: FastAPI dashboard with real-time candlestick chart,
    entry/exit markers, Money Management, Node Management, Backtest,
    and live trading controls with start/stop from dashboard.
    Runs on port 8420 by default.

    Examples:
      ppmt terminal                      # Start dashboard on port 8420
      ppmt terminal -p 9000             # Custom port
      ppmt terminal --open-browser      # Auto-open in browser
    """
    console.print("[bold cyan]PPMT Terminal Dashboard v0.39.4[/bold cyan]")

    if open_browser:
        import webbrowser
        import threading

    fastapi_port = port

    # Kill any existing process on the target port
    try:
        import subprocess as _sp
        check = _sp.run(
            ["lsof", "-ti", f":{fastapi_port}"],
            capture_output=True, text=True, timeout=5,
        )
        if check.returncode == 0 and check.stdout.strip():
            pids = check.stdout.strip().split('\n')
            console.print(f"  [yellow]Port {fastapi_port} in use. Killing...[/yellow]")
            for pid in pids:
                try:
                    _sp.run(["kill", "-9", pid], capture_output=True, timeout=5)
                except Exception:
                    pass
            import time as _time
            _time.sleep(1)
    except Exception:
        pass

    console.print(f"  Starting PPMT Terminal Dashboard on http://localhost:{fastapi_port}")

    if open_browser:
        url = f"http://localhost:{fastapi_port}"
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
        console.print(f"  Opening browser at {url}")

    console.print("[dim]Press Ctrl+C to stop[/dim]")
    console.print("[dim]Dashboard features: Portfolio, Money Management, Node Control, Backtest, Real-time[/dim]\n")

    try:
        from ppmt.terminal.server import run_server
        run_server(host=host, port=fastapi_port)
    except ImportError as e:
        console.print(f"[red]Terminal dashboard not available: {e}[/red]")
        console.print("[dim]Install dependencies: pip3 install fastapi uvicorn --break-system-packages[/dim]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")


@cli.command()
@click.option("--exchange", "-e", default="binance", help="Exchange to scan")
@click.option("--quote", "-q", default="USDT", help="Quote currency")
@click.option("--top", default=20, type=int, help="Number of top assets to show")
@click.option("--sort-by", type=click.Choice(["volume", "volatility", "change"]), default="volume", help="Sort criterion")
def scan(exchange: str, quote: str, top: int, sort_by: str):
    """Scan and analyze available trading assets.

    Fetches market data from the exchange and ranks assets by
    the selected criterion. Useful for finding high-activity
    or high-volatility assets to trade.

    Examples:
      ppmt scan                           # Top 20 USDT pairs by volume on Binance
      ppmt scan -e bybit -q USDC         # USDC pairs on Bybit
      ppmt scan --sort-by volatility      # Most volatile assets
    """
    console.print(f"[bold cyan]Scanning {exchange} markets ({quote} pairs)...[/bold cyan]")

    try:
        storage = PPMTStorage()
        collector = DataCollector(exchange=exchange, storage=storage)

        # Get markets via ccxt (v0.20.0: now implemented)
        markets = collector.get_markets()
        if not markets:
            console.print("[red]No markets found. Check exchange connection.[/red]")
            collector.close()
            storage.close()
            return

        # Filter by quote currency
        pairs = [m for m in markets if m.endswith(f"/{quote}")]
        if not pairs:
            console.print(f"[red]No {quote} pairs found on {exchange}[/red]")
            collector.close()
            storage.close()
            return

        # Fetch tickers for ranking (v0.20.0: now implemented)
        console.print(f"  Found {len(pairs)} {quote} pairs. Fetching tickers...")

        tickers = collector.get_tickers(pairs[:100])  # Limit to avoid rate limits

        # Build ranking
        rankings = []
        for symbol, ticker in tickers.items():
            volume = ticker.get("quoteVolume", 0) or 0
            change = ticker.get("percentage", 0) or 0
            high = ticker.get("high", 0) or 0
            low = ticker.get("low", 0) or 0
            last = ticker.get("last", 0) or 0

            volatility = ((high - low) / last * 100) if last > 0 else 0

            rankings.append({
                "symbol": symbol,
                "price": last,
                "volume_24h": volume,
                "change_24h": change,
                "volatility": volatility,
                "high": high,
                "low": low,
            })

        # Sort
        sort_key = {"volume": "volume_24h", "volatility": "volatility", "change": "change_24h"}[sort_by]
        rankings.sort(key=lambda x: x[sort_key], reverse=True)

        # Display table
        table = Table(title=f"Top {top} {quote} Pairs on {exchange} (by {sort_by})")
        table.add_column("#", style="dim", width=4)
        table.add_column("Symbol", style="bold")
        table.add_column("Price", justify="right")
        table.add_column("24h Volume", justify="right")
        table.add_column("24h Change", justify="right")
        table.add_column("Volatility", justify="right")

        for i, r in enumerate(rankings[:top], 1):
            change_color = "green" if r["change_24h"] >= 0 else "red"
            change_sign = "+" if r["change_24h"] >= 0 else ""

            vol_str = f"${r['volume_24h']:,.0f}" if r['volume_24h'] >= 1e6 else f"${r['volume_24h']:,.0f}"
            price_str = f"${r['price']:,.2f}" if r['price'] >= 1 else f"${r['price']:,.6f}"

            table.add_row(
                str(i),
                r["symbol"],
                price_str,
                vol_str,
                f"[{change_color}]{change_sign}{r['change_24h']:.1f}%[/{change_color}]",
                f"{r['volatility']:.1f}%",
            )

        console.print(table)
        console.print(f"\n[dim]To ingest data: ppmt ingest -s <SYMBOL> -e {exchange}[/dim]")

        collector.close()
        storage.close()

    except ImportError:
        console.print("[red]ccxt is required for market scanning. Install with: pip install ccxt>=4.0.0[/red]")
    except Exception as e:
        console.print(f"[red]Scan failed: {e}[/red]")


@cli.command()
@click.option("--symbol", "-s", default=None, help="Filter by symbol")
@click.option("--capital", "-c", default=None, type=float, help="Set initial capital")
@click.option("--tokens", "-t", default=None, help="Comma-separated token list (e.g. BTC/USDT,ETH/USDT)")
@click.option("--method", "-m", default="REGIME_AWARE", help="Allocation method: EQUAL_WEIGHT, RISK_PARITY, REGIME_AWARE, QUALITY_WEIGHTED")
@click.option("--correlation", is_flag=True, help="Show cross-token correlation matrix")
@click.option("--rebalance", is_flag=True, help="Trigger portfolio rebalance")
@click.option("--serve-api", is_flag=True, help="Start Portfolio API server")
@click.option("--api-port", default=8430, type=int, help="API server port")
def portfolio(symbol: str, capital: float, tokens: str, method: str, correlation: bool, rebalance: bool, serve_api: bool, api_port: int):
    """Show portfolio and money management status.

    Displays current portfolio value, open positions, exposure,
    circuit breaker status, and risk metrics.

    v0.16.0: Now supports multi-token portfolio management with
    cross-token correlation, regime-aware allocation, and
    a REST API bridge for the dashboard.

    Examples:
      ppmt portfolio                                    # Show portfolio overview
      ppmt portfolio -s BTC/USDT                        # Filter by symbol
      ppmt portfolio -c 50000                           # Set initial capital
      ppmt portfolio -t BTC/USDT,ETH/USDT,SOL/USDT      # Multi-token portfolio
      ppmt portfolio -m RISK_PARITY                     # Use risk parity allocation
      ppmt portfolio --correlation                      # Show correlation matrix
      ppmt portfolio --rebalance                        # Trigger rebalance
      ppmt portfolio --serve-api --api-port 8430         # Start API server
    """
    from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig
    from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine
    from ppmt.risk.regime_allocator import RegimeAwareAllocator

    # Start API server if requested
    if serve_api:
        from ppmt.risk.portfolio_api import serve as portfolio_serve
        portfolio_serve(port=api_port)
        return

    # Parse token list
    token_list = None
    if tokens:
        token_list = [t.strip() for t in tokens.split(",")]

    # Build portfolio config
    config_kwargs = {}
    if capital is not None:
        config_kwargs["initial_capital"] = capital
    if token_list:
        config_kwargs["tokens"] = token_list
    config_kwargs["allocation_method"] = method

    pm_config = PortfolioConfig(**config_kwargs)

    # Try to load saved state
    state_file = os.path.join(CONFIG_DIR, "portfolio_state.json")
    pm_config.state_file = state_file

    pm = PortfolioManager(config=pm_config)

    # Load state if exists
    if os.path.exists(state_file):
        try:
            pm.load_state(state_file)
        except Exception:
            pass

    if capital is not None:
        console.print(f"[yellow]Setting capital to ${capital:,.2f}[/yellow]")

    # Trigger rebalance if requested
    if rebalance:
        result = pm.rebalance(reason="cli_request")
        console.print(f"\n[bold green]Portfolio rebalanced![/bold green]")
        if result.capital_moves:
            for move in result.capital_moves:
                delta_color = "green" if move["move"] >= 0 else "red"
                console.print(
                    f"  {move['symbol']}: ${move['from']:,.0f} -> ${move['to']:,.0f} "
                    f"[{delta_color}]{move['move']:+,.0f}[/{delta_color}]"
                )
        else:
            console.print("  No capital moves needed")
        pm.save_state()
        return

    # Show correlation matrix if requested
    if correlation:
        corr_engine = CrossTokenCorrelationEngine(
            tokens=list(pm._slots.keys()),
        )
        result = corr_engine.compute_matrix()
        corr_engine.display_matrix(result)

        # Show diversification score
        div = corr_engine.compute_diversification_score()
        console.print(f"\n  Diversification Score: [bold]{div['score']:.2f}[/bold] ({div['rating']})")
        console.print(f"  Effective Positions: {div['effective_positions']:.1f}")
        console.print(f"  Correlation Clusters: {div['clusters']}")
        return

    # Display portfolio summary using the PortfolioManager's rich display
    pm.display_summary()

    # Save state
    try:
        pm.save_state()
    except Exception:
        pass


@cli.command("nodes")
@click.option("--capital", "-c", default=10000.0, type=float, help="Total capital for parent node")
@click.option("--add", "-a", default=None, help="Add child node: SYMBOL:TIMEFRAME:ALLOC_PCT:LEVERAGE (e.g. BTC/USDT:1h:30:1)")
@click.option("--remove", "-r", default=None, help="Remove child node by node_id")
@click.option("--leverage", "-l", default=None, help="Set leverage for a child node: NODE_ID:LEVERAGE (e.g. btc_5m:5)")
@click.option("--auto/--manual", default=None, help="Set child node to auto or manual mode: NODE_ID")
@click.option("--kill", is_flag=True, help="Activate global kill switch (close all positions)")
@click.option("--unkill", is_flag=True, help="Deactivate global kill switch")
@click.option("--redistribute", "-R", default=None, help="Redistribute capital: NODE_ID:NEW_PCT,NODE_ID:NEW_PCT")
def nodes(capital: float, add: str, remove: str, leverage: str, auto: bool, kill: bool, unkill: bool, redistribute: str):
    """Manage parent-child node architecture for multi-strategy capital distribution.

    v0.23.0: Parent-Child Node Architecture with Leverage Control.

    The parent node manages a pool of capital and distributes it among
    child nodes, each running an independent PPMT strategy with its own
    leverage and auto/manual mode.

    Examples:
      ppmt nodes -c 50000                                 # Initialize with $50k capital
      ppmt nodes -a BTC/USDT:1h:30:1                      # Add child: BTC 1h, 30% capital, 1x leverage
      ppmt nodes -a BTC/USDT:5m:20:3                      # Add child: BTC 5m, 20% capital, 3x leverage
      ppmt nodes -a ETH/USDT:1h:25:1                      # Add child: ETH 1h, 25% capital, 1x leverage
      ppmt nodes -r btc_1h                                # Remove child node
      ppmt nodes -l btc_5m:5                               # Set btc_5m to 5x leverage
      ppmt nodes --manual btc_1h                          # Set btc_1h to manual mode
      ppmt nodes --kill                                   # Emergency: close all positions
      ppmt nodes --unkill                                 # Deactivate kill switch
      ppmt nodes -R btc_1h:40,btc_5m:15                   # Redistribute capital
    """
    from ppmt.risk.money_manager import ParentNodeManager, ChildNodeConfig

    # Load or create parent node state
    state_file = os.path.join(CONFIG_DIR, "parent_node_state.json")
    parent = ParentNodeManager(total_capital=capital)

    # Load existing state
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                saved = yaml.safe_load(f) or {}
            parent.total_capital = saved.get("total_capital", capital)
            for child_data in saved.get("children", []):
                cfg = ChildNodeConfig(**child_data)
                parent.register_child(cfg)
            if parent._children:
                parent.distribute_capital()
        except Exception:
            pass

    # Add child node
    if add:
        try:
            parts = add.split(":")
            if len(parts) < 4:
                console.print("[red]Format: SYMBOL:TIMEFRAME:ALLOC_PCT:LEVERAGE[/red]")
                console.print("[dim]Example: BTC/USDT:1h:30:1[/dim]")
                return

            symbol = parts[0]
            timeframe = parts[1]
            alloc_pct = float(parts[2]) / 100.0
            leverage_val = int(parts[3])

            # Generate node_id
            node_id = f"{symbol.split('/')[0].lower()}_{timeframe}"

            cfg = ChildNodeConfig(
                node_id=node_id,
                symbol=symbol,
                timeframe=timeframe,
                capital_allocation_pct=alloc_pct,
                leverage=leverage_val,
            )

            parent.register_child(cfg)
            parent.distribute_capital()

            console.print(f"[green]Added child node: {node_id}[/green]")
            console.print(f"  Symbol: {symbol}")
            console.print(f"  Timeframe: {timeframe}")
            console.print(f"  Allocation: {alloc_pct:.0%}")
            console.print(f"  Leverage: {leverage_val}x")
            console.print(f"  Capital: ${parent.get_child_capital(node_id):,.2f}")

        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            return

    # Remove child node
    if remove:
        try:
            parent.unregister_child(remove)
            console.print(f"[green]Removed child node: {remove}[/green]")
        except (ValueError, RuntimeError) as e:
            console.print(f"[red]Error: {e}[/red]")
            return

    # Set leverage
    if leverage:
        try:
            parts = leverage.split(":")
            if len(parts) != 2:
                console.print("[red]Format: NODE_ID:LEVERAGE[/red]")
                return
            parent.set_child_leverage(parts[0], int(parts[1]))
            console.print(f"[green]Set {parts[0]} leverage to {parts[1]}x[/green]")
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            return

    # Set auto/manual mode
    if auto is not None:
        # Find the node_id - the last argument should be the node_id
        # But with click, auto is True/False. We need to handle this differently.
        # For now, just show status
        pass

    # Kill switch
    if kill:
        parent.activate_global_kill_switch()
        console.print("[bold red]GLOBAL KILL SWITCH ACTIVATED[/bold red]")
        console.print("[dim]All child nodes disabled. Use --unkill to reactivate.[/dim]")

    if unkill:
        parent.deactivate_global_kill_switch()
        console.print("[bold green]Kill switch deactivated. All nodes re-enabled.[/bold green]")

    # Redistribute capital
    if redistribute:
        try:
            allocs = {}
            for pair in redistribute.split(","):
                node_id, pct = pair.split(":")
                allocs[node_id] = float(pct) / 100.0
            parent.redistribute_capital(allocs)
            console.print(f"[green]Capital redistributed:[/green]")
            for node_id, pct in allocs.items():
                console.print(f"  {node_id}: {pct:.0%} → ${parent.get_child_capital(node_id):,.2f}")
        except (ValueError, RuntimeError) as e:
            console.print(f"[red]Error: {e}[/red]")
            return

    # Display status
    parent.print_status()

    # Save state
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        state = {
            "total_capital": parent.total_capital,
            "children": [
                {
                    "node_id": cfg.node_id,
                    "symbol": cfg.symbol,
                    "timeframe": cfg.timeframe,
                    "capital_allocation_pct": cfg.capital_allocation_pct,
                    "leverage": cfg.leverage,
                    "auto_mode": cfg.auto_mode,
                    "max_position_pct": cfg.max_position_pct,
                    "enabled": cfg.enabled,
                }
                for cfg in parent._children.values()
            ],
        }
        with open(state_file, "w") as f:
            yaml.dump(state, f, default_flow_style=False)
    except Exception:
        pass
