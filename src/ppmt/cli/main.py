"""
PPMT CLI - Command Line Interface

Usage:
  ppmt init                          Initialize database and config
  ppmt ingest --symbol BTC/USDT      Fetch and store historical data
  ppmt build --symbol BTC/USDT       Build Trie from stored data
  ppmt predict --symbol BTC/USDT     Show prediction from current pattern
  ppmt run --symbol BTC/USDT         Real-time pattern matching
  ppmt run --symbol BTC/USDT --paper Run paper trading simulation
  ppmt monte-carlo --symbol BTC/USDT Monte Carlo simulation
  ppmt stats --symbol BTC/USDT       Show pattern statistics
  ppmt list                          List tracked assets
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from ppmt.data.storage import PPMTStorage
from ppmt.data.collector import DataCollector
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.ppmt import PPMT
from ppmt.engine.prediction import PredictionEngine

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
@click.version_option(version="0.2.7")
def cli():
    """PPMT - Progressive Pattern Matching Trie Engine"""
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
            console.print(f"  Date Range: {df.index[0]} -> {df.index[-1]}")

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

    # Propagate metadata so intermediate nodes have statistics
    # (old stored Tries may not have propagated metadata)
    trie.propagate_metadata()

    # Encode the FULL DataFrame to SAX symbols (same context as build)
    sax_config = config.get("sax", {})
    from ppmt.core.sax import SAXEncoder
    encoder = SAXEncoder(
        alphabet_size=sax_config.get("alphabet_size", 8),
        window_size=sax_config.get("window_size", 10),
        strategy=sax_config.get("strategy", "ohlcv"),
    )

    # Must encode full DataFrame to get same z-score context as during build
    symbols = encoder.encode(df)

    if not symbols:
        console.print("[red]Could not encode data.[/red]")
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
            console.print(f"  -> [bold green]2.0x base position (HIGH CONVICTION)[/bold green]")
        elif mock_signal.metadata_sizing_signal >= 1.0:
            console.print(f"  -> [green]1.0x base position (NORMAL)[/green]")
        elif mock_signal.metadata_sizing_signal >= 0.5:
            console.print(f"  -> [yellow]0.5x base position (LOW CONVICTION)[/yellow]")
        else:
            console.print(f"  -> [red]0.25x base position or REJECT[/red]")

        # Forward prediction chain
        if prediction.predicted_path:
            console.print(f"\n[bold]Forward Prediction Chain:[/bold]")
            total_hours = 0
            for step in prediction.predicted_path:
                step_hours = step.estimated_candles * tf_hours
                total_hours += step_hours
                marker = "[green]ok[/green]" if step.is_continuation else "[red]X[/red]"
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
@click.option("--paper", is_flag=True, default=False, help="Run in paper trading mode (simulated)")
@click.option("--capital", "-c", default=10000.0, type=float, help="Initial capital for paper trading")
@click.option("--min-confidence", default=0.10, type=float, help="Minimum signal confidence to enter (default: 0.10)")
def run(symbol: str, timeframe: str, paper: bool, capital: float, min_confidence: float):
    """Run real-time pattern matching (requires exchange connection).

    Use --paper to run a paper trading simulation on historical data
    without real money. This validates PPMT predictions before going live.
    """
    if paper:
        # Paper trading mode
        from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig

        config = load_config()
        sax_config = config.get("sax", {})

        pt_config = PaperTraderConfig(
            symbol=symbol,
            timeframe=timeframe,
            initial_capital=capital,
            sax_alphabet_size=sax_config.get("alphabet_size", 8),
            sax_window_size=sax_config.get("window_size", 10),
            sax_strategy=sax_config.get("strategy", "ohlcv"),
            min_confidence=min_confidence,
        )

        trader = PaperTrader(config=pt_config)
        result = trader.run()

        # Display results
        console.print()
        console.print(Panel(result.format_summary(), title="Paper Trading Results", border_style="cyan"))

        if result.trades:
            console.print()
            console.print(result.format_trades_table())

            # Offer Monte Carlo on paper trading results
            trades_pnl = [t.pnl_pct / 100.0 for t in result.trades]
            if len(trades_pnl) >= 5:
                console.print(f"\n[dim]Tip: Run 'ppmt monte-carlo --symbol {symbol}' for risk analysis[/dim]")
        else:
            console.print("\n[yellow]No trades generated. Try adjusting --min-confidence or ensure data is available.[/yellow]")

        return

    # Real-time mode (requires exchange connection)
    console.print(f"[cyan]Starting PPMT real-time matching for {symbol}...[/cyan]")
    console.print("[yellow]Real-time mode requires exchange API connection.[/yellow]")
    console.print("[yellow]Use --paper to run a paper trading simulation instead.[/yellow]")

    # TODO: Implement real-time loop with WebSocket
    # 1. Load engine state and Tries from storage
    # 2. Connect to exchange WebSocket
    # 3. Process each new candle through SAX -> match -> signal
    # 4. Pass signals to RiskManager
    # 5. Execute trades if risk allows

    console.print("Real-time engine will be implemented in the next phase.")


@cli.command("monte-carlo")
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--simulations", "-n", default=1000, type=int, help="Number of Monte Carlo simulations")
@click.option("--capital", "-c", default=10000.0, type=float, help="Initial capital")
@click.option("--seed", default=42, type=int, help="Random seed for reproducibility")
@click.option("--ruin-threshold", default=0.5, type=float, help="Ruin threshold (fraction of capital)")
@click.option("--paper-first", is_flag=True, default=False, help="Run paper trading first, then Monte Carlo on results")
@click.option("--min-confidence", default=0.10, type=float, help="Min confidence for paper trading (with --paper-first)")
def monte_carlo(
    symbol: str,
    timeframe: str,
    simulations: int,
    capital: float,
    seed: int,
    ruin_threshold: float,
    paper_first: bool,
    min_confidence: float,
):
    """Run Monte Carlo simulation to assess trading system robustness.

    Reshuffles trade order many times to estimate the distribution of
    possible outcomes and derive confidence intervals for key risk metrics
    including Risk of Ruin, P95 Max Drawdown, and Probability of Profit.

    Two modes:
      1. With --paper-first: Runs paper trading first, then Monte Carlo on results
      2. Without --paper-first: Runs Monte Carlo on stored trade history (if any)
    """
    from ppmt.risk.monte_carlo import MonteCarloSimulator, MonteCarloConfig

    config = load_config()
    storage = PPMTStorage()

    trades_pnl_pct = []

    if paper_first:
        # Run paper trading first to generate trade history
        from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig

        sax_config = config.get("sax", {})

        console.print(f"[cyan]Step 1: Running paper trading for {symbol}...[/cyan]\n")
        pt_config = PaperTraderConfig(
            symbol=symbol,
            timeframe=timeframe,
            initial_capital=capital,
            sax_alphabet_size=sax_config.get("alphabet_size", 8),
            sax_window_size=sax_config.get("window_size", 10),
            sax_strategy=sax_config.get("strategy", "ohlcv"),
            min_confidence=min_confidence,
        )

        trader = PaperTrader(config=pt_config)
        pt_result = trader.run()

        # Show paper trading summary
        console.print(Panel(pt_result.format_summary(), title="Paper Trading Results", border_style="cyan"))

        if not pt_result.trades:
            console.print("[red]No trades generated. Cannot run Monte Carlo.[/red]")
            console.print("[yellow]Try adjusting --min-confidence or ensure data is available.[/yellow]")
            storage.close()
            return

        trades_pnl_pct = [t.pnl_pct / 100.0 for t in pt_result.trades]
        console.print(f"\n[cyan]Step 2: Running Monte Carlo on {len(trades_pnl_pct)} trades...[/cyan]\n")

    else:
        # Try to load trade history from storage
        # For now, we need paper trading results
        console.print(f"[cyan]Loading trade history for {symbol}...[/cyan]")

        # Try paper trading as source
        df = storage.load_ohlcv(symbol, timeframe)
        if df.empty:
            console.print(f"[red]No data found for {symbol}. Run 'ppmt ingest' first.[/red]")
            storage.close()
            return

        # Run paper trading to generate trades
        console.print(f"[yellow]No stored trade history found. Running paper trading first...[/yellow]")
        console.print(f"[dim](Use --paper-first explicitly to control paper trading parameters)[/dim]\n")

        from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig

        sax_config = config.get("sax", {})
        pt_config = PaperTraderConfig(
            symbol=symbol,
            timeframe=timeframe,
            initial_capital=capital,
            sax_alphabet_size=sax_config.get("alphabet_size", 8),
            sax_window_size=sax_config.get("window_size", 10),
            sax_strategy=sax_config.get("strategy", "ohlcv"),
            min_confidence=min_confidence,
        )

        trader = PaperTrader(config=pt_config)
        pt_result = trader.run()

        if not pt_result.trades:
            console.print("[red]No trades generated. Cannot run Monte Carlo.[/red]")
            storage.close()
            return

        trades_pnl_pct = [t.pnl_pct / 100.0 for t in pt_result.trades]
        console.print(f"\n[cyan]Running Monte Carlo on {len(trades_pnl_pct)} trades...[/cyan]\n")

    # Run Monte Carlo simulation
    mc_config = MonteCarloConfig(
        simulations=simulations,
        seed=seed,
        initial_capital=capital,
        ruin_threshold=ruin_threshold,
    )

    simulator = MonteCarloSimulator()

    with console.status(f"[bold green]Running {simulations} Monte Carlo simulations...") as status:
        result = simulator.simulate(trades_pnl_pct, config=mc_config)

    # Display results
    summary = simulator.generate_summary(result)
    console.print(summary)

    # Show interpretation
    console.print()
    if result.risk_of_ruin < 0.01:
        console.print("[bold green]VERDICT: Excellent - Very low risk of ruin[/bold green]")
    elif result.risk_of_ruin < 0.05:
        console.print("[green]VERDICT: Good - Acceptable risk for most traders[/green]")
    elif result.risk_of_ruin < 0.10:
        console.print("[yellow]VERDICT: Marginal - Consider reducing position sizes[/yellow]")
    else:
        console.print("[bold red]VERDICT: Dangerous - High risk of blow-up, reduce exposure[/bold red]")

    # Show equity curve percentiles in a table
    table = Table(title="Equity Curve Percentiles")
    table.add_column("Percentile", style="cyan")
    table.add_column("Final Equity", justify="right")
    table.add_column("vs Initial", justify="right")

    for ci in result.equity_percentiles:
        pct_change = (ci.value - capital) / capital * 100
        style = "green" if pct_change >= 0 else "red"
        table.add_row(
            f"P{ci.level}",
            f"${ci.value:,.2f}",
            f"[{style}]{pct_change:+.1f}%[/{style}]",
        )

    console.print(table)

    # Drawdown percentiles
    dd_table = Table(title="Max Drawdown Percentiles")
    dd_table.add_column("Percentile", style="cyan")
    dd_table.add_column("Max Drawdown", justify="right")
    dd_table.add_column("Risk Level", justify="right")

    for ci in result.drawdown_percentiles:
        dd_pct = ci.value * 100
        if dd_pct < 10:
            risk = "[green]Low[/green]"
        elif dd_pct < 25:
            risk = "[yellow]Moderate[/yellow]"
        elif dd_pct < 40:
            risk = "[red]High[/red]"
        else:
            risk = "[bold red]Extreme[/bold red]"
        dd_table.add_row(f"P{ci.level}", f"{dd_pct:.1f}%", risk)

    console.print(dd_table)

    storage.close()


if __name__ == "__main__":
    cli()
