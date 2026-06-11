"""
PPMT CLI - Command Line Interface

Usage:
  ppmt init                          Initialize database and config
  ppmt ingest --symbol BTC/USDT      Fetch and store historical data
  ppmt build --symbol BTC/USDT       Build Trie from stored data
  ppmt predict --symbol BTC/USDT     Show prediction from current pattern
  ppmt run --symbol BTC/USDT         Real-time pattern matching
  ppmt run --symbol BTC/USDT --paper Run paper trading simulation
  ppmt validate --symbol BTC/USDT    Out-of-sample validation (70/30 split)
  ppmt walk-forward --symbol BTC/USDT Walk-forward analysis
  ppmt monte-carlo --symbol BTC/USDT Monte Carlo simulation
  ppmt validate-all --symbol BTC/USDT One-click validation suite (P0+P1+P2)
  ppmt stats --symbol BTC/USDT       Show pattern statistics
  ppmt list                          List tracked assets
"""

from __future__ import annotations

import os

import click
import numpy as np
import yaml  # noqa: F401  (used inside validate/walk-forward)
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
@click.version_option(version="0.11.0")
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
@click.option("--force", "-f", is_flag=True, default=False, help="Force rebuild (discard Living Trie data)")
@click.option("--bootstrap/--no-bootstrap", default=True, help="Run bootstrap paper trading after build (default: enabled)")
@click.option("--bootstrap-ratio", default=1.0, type=float, help="Fraction of data for bootstrap (default: 1.0 = 100%%)")
@click.option("--bootstrap-passes", default=2, type=int, help="Number of bootstrap passes (default: 2 — second pass uses improved trie from first)")
@click.option("--train-ratio", default=1.0, type=float,
              help="Training data ratio (default: 1.0 = use all data). "
                   "When set to e.g. 0.7, only the first 70%% of data is used "
                   "to build the trie, enabling out-of-sample validation.")
def build(symbol: str, timeframe: str, pattern_length: int, force: bool,
          bootstrap: bool, bootstrap_ratio: float, bootstrap_passes: int,
          train_ratio: float):
    """Build PPMT Trie from stored data.

    By default, preserves the existing N3 Living Trie (accumulated trading
    metadata) by merging the new build into it. Use --force to discard
    the Living Trie and rebuild from scratch.

    v0.5.0: After building the trie, automatically runs bootstrap paper
    trading passes on the data (--bootstrap-ratio, default 100%%)
    to accumulate trading observations in the N3 trie. By default, 2 passes
    are run (--bootstrap-passes) — the second pass uses the improved trie
    from the first pass, producing higher-quality observations.
    This gives fresh tries meaningful metadata from day one.
    Use --no-bootstrap to skip.

    v0.7.0: Added --train-ratio option for out-of-sample validation.
    When set to less than 1.0, only the first train_ratio fraction of data
    is used for building and bootstrap. PAA normalization stats (mean/std)
    from the training data are computed and stored in the engine state
    for use during OOS testing.
    """
    # Validate train_ratio
    if train_ratio <= 0 or train_ratio > 1.0:
        console.print("[red]--train-ratio must be between 0 (exclusive) and 1.0 (inclusive)[/red]")
        return

    config = load_config()
    storage = PPMTStorage()

    # Load data
    df = storage.load_ohlcv(symbol, timeframe)
    if df.empty:
        console.print(f"[red]No data found for {symbol}. Run 'ppmt ingest' first.[/red]")
        return

    # Split data if train_ratio < 1.0
    if train_ratio < 1.0:
        train_end = int(len(df) * train_ratio)
        df_train = df.iloc[:train_end]
        df_test = df.iloc[train_end:]
        console.print(f"[cyan]Building PPMT for {symbol} (train/test split: {train_ratio:.0%}/{1 - train_ratio:.0%})...[/cyan]")
        console.print(f"  Training: {len(df_train)} candles (0-{train_end})")
        console.print(f"  Testing:  {len(df_test)} candles ({train_end}-{len(df)})")
        df_build = df_train
    else:
        df_train = df
        df_build = df
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

    # Build Trie (on training data if train_ratio < 1.0, else all data)
    count = engine.build(df_build, pattern_length=pattern_length)

    # v0.4.0: Run bootstrap paper trading pass
    # This accumulates trading observations in the N3 trie,
    # giving fresh tries meaningful metadata from day one.
    # v0.7.0: Bootstrap also runs on training data only when train_ratio < 1.0
    bootstrap_stats = None
    if bootstrap:
        total_bootstrap_trades = 0
        total_bootstrap_obs = 0
        total_bootstrap_wins = 0
        for pass_num in range(1, bootstrap_passes + 1):
            pass_label = f" (pass {pass_num}/{bootstrap_passes})" if bootstrap_passes > 1 else ""
            console.print(f"[cyan]Running bootstrap paper trading (ratio={bootstrap_ratio:.0%}){pass_label}...[/cyan]")
            bootstrap_stats = engine.bootstrap(
                df=df_build,
                pattern_length=pattern_length,
                bootstrap_ratio=bootstrap_ratio,
                verbose=False,
            )
            total_bootstrap_trades += bootstrap_stats["trades"]
            total_bootstrap_obs += bootstrap_stats["observations_recorded"]
            total_bootstrap_wins += bootstrap_stats["winning_trades"]
            if bootstrap_stats["trades"] > 0:
                wr_color = "green" if bootstrap_stats["win_rate"] >= 0.5 else "yellow"
                console.print(
                    f"  [bold]Bootstrap{pass_label}:[/bold] {bootstrap_stats['trades']} trades simulated, "
                    f"WR [{wr_color}]{bootstrap_stats['win_rate']:.1%}[/{wr_color}], "
                    f"{bootstrap_stats['observations_recorded']} observations recorded"
                )
            else:
                console.print(f"  [yellow]Bootstrap{pass_label}: no trades generated (data may be insufficient)[/yellow]")
                break  # No point running more passes if no trades
        # Combine stats for display
        combined_wr = total_bootstrap_wins / total_bootstrap_trades if total_bootstrap_trades > 0 else 0.0
        bootstrap_stats = {
            "trades": total_bootstrap_trades,
            "winning_trades": total_bootstrap_wins,
            "win_rate": combined_wr,
            "observations_recorded": total_bootstrap_obs,
            "new_nodes_created": bootstrap_stats["new_nodes_created"] if bootstrap_stats else 0,
        }
    else:
        console.print(f"  [dim]Bootstrap: skipped (--no-bootstrap)[/dim]")

    # For N3: check if existing Living Trie should be preserved
    existing_n3 = storage.load_trie(symbol, "n3")
    n3_to_save = engine.trie_n3

    if existing_n3 is not None and not force:
        existing_count = existing_n3.pattern_count
        new_count = engine.trie_n3.pattern_count

        if existing_count >= new_count:
            # Existing trie has Living Trie growth — merge new build INTO it
            console.print(f"[bold cyan]Living Trie detected:[/bold cyan] existing N3 has {existing_count} patterns vs {new_count} new")
            console.print(f"[cyan]Merging new build into existing Living Trie (preserving {existing_count - new_count} discovered patterns)...[/cyan]")

            merge_stats = existing_n3.merge(engine.trie_n3)

            console.print(f"[green]Merge complete:[/green] "
                          f"{merge_stats['new_patterns']} new patterns added, "
                          f"{merge_stats['merged_patterns']} patterns merged, "
                          f"{merge_stats['total_observations_added']} observations added")
            console.print(f"[green]N3 Trie: {existing_count} -> {existing_n3.pattern_count} patterns[/green]")

            n3_to_save = existing_n3
        else:
            console.print(f"[yellow]Existing N3 ({existing_count} patterns) has fewer patterns than new build ({new_count})[/yellow]")
            console.print(f"[yellow]Replacing with new build (existing trie was likely from a different config)[/yellow]")

    # Save Tries
    for level, trie in [
        ("n1", engine.trie_n1),
        ("n2", engine.trie_n2),
        ("n3", n3_to_save),
        ("n4", engine.trie_n4),
    ]:
        storage.save_trie(symbol, level, trie)
        console.print(f"  N{level[-1]} Trie: {trie.pattern_count} patterns, max depth {trie.max_depth}")

    # Save engine state — include training stats if train_ratio < 1.0
    stats = engine.get_stats()
    if train_ratio < 1.0:
        # Compute PAA normalization stats from training data
        from ppmt.core.sax import SAXEncoder
        sax_encoder = SAXEncoder(
            alphabet_size=sax_config.get("alphabet_size", 8),
            window_size=sax_config.get("window_size", 10),
            strategy=sax_config.get("strategy", "ohlcv"),
        )
        _, paa_mean, paa_std = sax_encoder.encode_with_normalization(df_train)
        stats['train_ratio'] = train_ratio
        stats['paa_mean'] = paa_mean
        stats['paa_std'] = paa_std
        stats['train_candle_count'] = len(df_train)
        stats['total_candle_count'] = len(df)

    storage.save_engine_state(symbol, stats)

    console.print(f"[green]Built {count} patterns for {symbol}[/green]")

    # Show stats
    console.print(f"  Weights: {engine.weights}")

    # Show training normalization stats if applicable
    if train_ratio < 1.0:
        console.print(f"  [bold cyan]Training normalization stats:[/bold cyan]")
        console.print(f"    PAA mean: {stats['paa_mean']:.6f}")
        console.print(f"    PAA std:  {stats['paa_std']:.6f}")
        console.print(f"    Train candles: {stats['train_candle_count']}")
        console.print(f"    Total candles: {stats['total_candle_count']}")
        console.print(f"  [dim]Use these stats with --paa-mean and --paa-std in 'ppmt run --paper' for OOS testing[/dim]")

    # Show bootstrap results summary
    if bootstrap_stats and bootstrap_stats["trades"] > 0:
        passes_text = f" ({bootstrap_passes} passes)" if bootstrap_passes > 1 else ""
        console.print(
            f"  [bold cyan]Bootstrap result:{passes_text}[/bold cyan] "
            f"N3 trie now has {engine.trie_n3.trading_observations} trading observations "
            f"({bootstrap_stats['trades']} trades, "
            f"WR {bootstrap_stats['win_rate']:.1%})"
        )

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

        # Show training stats if present
        if 'train_ratio' in state:
            console.print(f"\n[bold cyan]Training Stats (OOS):[/bold cyan]")
            console.print(f"  Train Ratio: {state['train_ratio']:.0%}")
            console.print(f"  PAA Mean: {state.get('paa_mean', 'N/A')}")
            console.print(f"  PAA Std:  {state.get('paa_std', 'N/A')}")
            console.print(f"  Train Candles: {state.get('train_candle_count', 'N/A')}")
            console.print(f"  Total Candles: {state.get('total_candle_count', 'N/A')}")

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
@click.option("--min-confidence", default=0.20, type=float, help="Minimum signal confidence to enter (default: 0.20)")
@click.option("--start-offset", default=200, type=int, help="Start candle index (default: 200)")
@click.option("--end-offset", default=0, type=int, help="End candle index, 0=all (for OOS validation)")
@click.option("--paa-mean", default=None, type=float, help="SAX normalization mean from training (for OOS)")
@click.option("--paa-std", default=None, type=float, help="SAX normalization std from training (for OOS)")
@click.option("--regime-aware/--no-regime-aware", default=True, help="Enable regime-aware position sizing (default: enabled)")
def run(symbol: str, timeframe: str, paper: bool, capital: float, min_confidence: float,
        start_offset: int, end_offset: int, paa_mean: float, paa_std: float, regime_aware: bool):
    """Run real-time pattern matching (requires exchange connection).

    Use --paper to run a paper trading simulation on historical data
    without real money. This validates PPMT predictions before going live.

    Use --start-offset and --end-offset for out-of-sample validation:
    build on all data, then trade only on a specific portion.
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
            start_offset=start_offset,
            end_offset=end_offset,
            paa_mean=paa_mean,
            paa_std=paa_std,
            regime_aware=regime_aware,
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
    console.print("[yellow]Use 'ppmt replay' for replay mode or 'ppmt live' for exchange connection.[/yellow]")
    console.print("[dim]The 'ppmt run' command without --paper now redirects to 'ppmt replay'.[/dim]")

    # Default: run replay mode (same data, streaming pipeline)
    from ppmt.engine.realtime import RealtimeTrader, ReplayConfig

    config = load_config()
    sax_config = config.get("sax", {})

    replay_config = ReplayConfig(
        symbol=symbol,
        timeframe=timeframe,
        initial_capital=capital,
        sax_alphabet_size=sax_config.get("alphabet_size", 8),
        sax_window_size=sax_config.get("window_size", 10),
        sax_strategy=sax_config.get("strategy", "ohlcv"),
        min_confidence=min_confidence,
        start_offset=start_offset,
        regime_aware=regime_aware,
        speed=0,  # Maximum speed for CLI
    )

    trader = RealtimeTrader(config=replay_config)
    result = trader.run_replay()

    from ppmt.engine.realtime import format_realtime_result
    console.print()
    console.print(Panel(format_realtime_result(result), title="Replay Results", border_style="cyan"))


# ══════════════════════════════════════════════════════════════════════════════
# v0.9.0: Real-Time Trading Commands
# ══════════════════════════════════════════════════════════════════════════════

@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--capital", "-c", default=10000.0, type=float, help="Initial capital")
@click.option("--min-confidence", default=0.20, type=float, help="Minimum signal confidence to enter")
@click.option("--speed", default=0.0, type=float, help="Replay speed (0=max, 1=real-time, 10=10x)")
@click.option("--start-offset", default=200, type=int, help="Start candle index (default: 200)")
@click.option("--regime-aware/--no-regime-aware", default=True, help="Regime-aware position sizing")
def replay(symbol: str, timeframe: str, capital: float, min_confidence: float,
           speed: float, start_offset: int, regime_aware: bool):
    """Replay historical data through the streaming PPMT pipeline.

    Unlike 'ppmt run --paper' which batch-processes all data, replay
    processes candles one at a time through the incremental SAX encoder,
    exactly as the live trading engine would. This validates the streaming
    pipeline before connecting to an exchange.

    v0.9.0: New command for real-time pipeline validation.
    """
    from ppmt.engine.realtime import RealtimeTrader, ReplayConfig, format_realtime_result

    config = load_config()
    sax_config = config.get("sax", {})

    replay_config = ReplayConfig(
        symbol=symbol,
        timeframe=timeframe,
        initial_capital=capital,
        sax_alphabet_size=sax_config.get("alphabet_size", 8),
        sax_window_size=sax_config.get("window_size", 10),
        sax_strategy=sax_config.get("strategy", "ohlcv"),
        min_confidence=min_confidence,
        start_offset=start_offset,
        regime_aware=regime_aware,
        speed=speed,
    )

    trader = RealtimeTrader(config=replay_config)
    result = trader.run_replay()

    console.print()
    console.print(Panel(format_realtime_result(result), title="Replay Results", border_style="cyan"))

    if result.trades:
        console.print()
        table = Table(title=f"Replay Trades: {symbol}")
        table.add_column("#", justify="right", width=4)
        table.add_column("Dir", width=5)
        table.add_column("Entry", justify="right", width=12)
        table.add_column("Exit", justify="right", width=12)
        table.add_column("PnL%", justify="right", width=8)
        table.add_column("Conf", justify="right", width=6)
        table.add_column("Exit Reason", width=15)
        table.add_column("Regime", width=8)

        for t in result.trades:
            pnl_style = "green" if t.pnl_pct >= 0 else "red"
            dir_style = "green" if t.direction == "LONG" else "red"
            table.add_row(
                str(t.trade_id),
                f"[{dir_style}]{t.direction}[/{dir_style}]",
                f"${t.entry_price:,.2f}",
                f"${t.exit_price:,.2f}",
                f"[{pnl_style}]{t.pnl_pct:+.2f}%[/{pnl_style}]",
                f"{t.confidence:.0%}",
                t.exit_reason,
                t.regime or "-",
            )

        console.print(table)


@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--capital", "-c", default=10000.0, type=float, help="Initial capital")
@click.option("--exchange", "-e", default="binance", help="Exchange name")
@click.option("--api-key", default="", help="Exchange API key")
@click.option("--api-secret", default="", help="Exchange API secret")
@click.option("--min-confidence", default=0.20, type=float, help="Minimum signal confidence")
@click.option("--testnet/--mainnet", default=True, help="Use testnet (default: True)")
@click.option("--dry-run/--execute", default=True, help="Dry run mode (no real orders, default: True)")
@click.option("--regime-aware/--no-regime-aware", default=True, help="Regime-aware position sizing")
def live(symbol: str, timeframe: str, capital: float, exchange: str,
         api_key: str, api_secret: str, min_confidence: float,
         testnet: bool, dry_run: bool, regime_aware: bool):
    """Connect to an exchange and trade in real-time using PPMT.

    Requires ccxt (pip install ccxt>=4.0.0, Python 3.10+).

    By default runs in DRY RUN mode (--dry-run) which processes signals
    but does not execute real orders. Use --execute to enable real trading.

    Use --testnet (default) for exchange testnet/paper trading.

    v0.9.0: New command for live exchange trading.
    """
    from ppmt.engine.realtime import RealtimeTrader, LiveConfig

    config = load_config()
    sax_config = config.get("sax", {})

    live_config = LiveConfig(
        symbol=symbol,
        timeframe=timeframe,
        initial_capital=capital,
        exchange=exchange,
        api_key=api_key,
        api_secret=api_secret,
        sax_alphabet_size=sax_config.get("alphabet_size", 8),
        sax_window_size=sax_config.get("window_size", 10),
        sax_strategy=sax_config.get("strategy", "ohlcv"),
        min_confidence=min_confidence,
        testnet=testnet,
        dry_run=dry_run,
        regime_aware=regime_aware,
    )

    if not dry_run:
        console.print("[bold red]⚠ WARNING: LIVE TRADING MODE[/bold red]")
        console.print("[red]Real orders will be executed. This can result in actual financial loss.[/red]")
        console.print("[red]PPMT is experimental software. Use at your own risk.[/red]")
        console.print()
        click.confirm("Are you sure you want to trade with real money?", abort=True)

    trader = RealtimeTrader(config=live_config)

    import asyncio
    result = asyncio.run(trader.run_live())

    from ppmt.engine.realtime import format_realtime_result
    console.print()
    console.print(Panel(format_realtime_result(result), title="Live Trading Results", border_style="cyan"))


# ══════════════════════════════════════════════════════════════════════════════
# v0.7.0: Out-of-Sample Validation Commands
# ══════════════════════════════════════════════════════════════════════════════

@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--train-ratio", default=0.7, type=float,
              help="Training data ratio (default: 0.7 = 70/30 split)")
@click.option("--capital", "-c", default=10000.0, type=float,
              help="Initial capital for paper trading")
@click.option("--min-confidence", default=0.20, type=float,
              help="Minimum signal confidence to enter")
@click.option("--simulations", "-n", default=1000, type=int,
              help="Monte Carlo simulations on OOS trades")
@click.option("--seed", default=42, type=int, help="Random seed for reproducibility")
def validate(symbol: str, timeframe: str, train_ratio: float, capital: float,
             min_confidence: float, simulations: int, seed: int):
    """Out-of-sample validation with automated train/test split.

    v0.7.0: Automates the entire OOS workflow:
      1. Loads all data and splits into train/test
      2. Builds trie on training data ONLY (no look-ahead)
      3. Runs bootstrap on training data to populate trie
      4. Computes training normalization stats (PAA mean/std)
      5. Paper trades on test data using training trie and stats
      6. Runs Monte Carlo simulation on OOS trades
      7. Displays comprehensive IS vs OOS comparison

    This is the gold standard for validating trading systems —
    no future data leaks into pattern discovery.
    """
    from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig
    from ppmt.core.sax import SAXEncoder
    from ppmt.risk.monte_carlo import MonteCarloSimulator, MonteCarloConfig

    # Validate train_ratio
    if train_ratio <= 0 or train_ratio >= 1.0:
        console.print("[red]--train-ratio must be between 0 and 1.0 (exclusive)[/red]")
        return

    config = load_config()
    sax_config = config.get("sax", {})
    storage = PPMTStorage()

    # Save original trie to restore later
    original_trie = storage.load_trie(symbol, "n3")

    try:
        # Step 1: Load all data
        df = storage.load_ohlcv(symbol, timeframe)
        if df.empty:
            console.print(f"[red]No data found for {symbol}. Run 'ppmt ingest' first.[/red]")
            return

        total_candles = len(df)
        train_end = int(total_candles * train_ratio)
        df_train = df.iloc[:train_end]
        df_test = df.iloc[train_end:]

        if len(df_train) < 200:
            console.print(f"[red]Not enough training data ({len(df_train)} candles). Need at least 200.[/red]")
            return

        if len(df_test) < 100:
            console.print(f"[red]Not enough test data ({len(df_test)} candles). Need at least 100.[/red]")
            return

        # Step 2: Classify asset
        classifier = AssetClassifier()
        info = classifier.classify(symbol)

        # Step 3: Encode training data and get normalization stats
        sax_encoder = SAXEncoder(
            alphabet_size=sax_config.get("alphabet_size", 8),
            window_size=sax_config.get("window_size", 10),
            strategy=sax_config.get("strategy", "ohlcv"),
        )
        train_symbols, paa_mean, paa_std = sax_encoder.encode_with_normalization(df_train)

        # Step 4: Build trie on TRAINING data only (with pre-computed symbols)
        console.print(f"\n[bold cyan]Step 1/5: Building trie on training data...[/bold cyan]")
        engine = PPMT(
            symbol=symbol,
            asset_class=info.asset_class,
            sax_alphabet_size=sax_config.get("alphabet_size", 8),
            sax_window_size=sax_config.get("window_size", 10),
            sax_strategy=sax_config.get("strategy", "ohlcv"),
            weight_profile=info.weight_profile,
        )
        engine.build(df_train, pattern_length=5, symbols=train_symbols)

        # Step 5: Run bootstrap on training data (1 pass for OOS validation)
        console.print(f"[bold cyan]Step 2/5: Running bootstrap on training data...[/bold cyan]")
        bootstrap_result = engine.bootstrap(
            df_train, pattern_length=5, bootstrap_ratio=1.0, verbose=False
        )

        trie = engine.trie_n3
        trie.propagate_metadata()

        train_patterns = trie.pattern_count
        train_observations = trie.trading_observations
        is_trades = bootstrap_result["trades"]
        is_wins = bootstrap_result["winning_trades"]
        is_wr = bootstrap_result["win_rate"]

        console.print(f"  Trie: {train_patterns} patterns, {train_observations} trading observations")
        console.print(f"  Bootstrap: {is_trades} trades, WR {is_wr:.1%}")

        # Save trie for paper trader to load
        storage.save_trie(symbol, "n3", trie)
        # Also save N1, N2, N4 for completeness
        for level, t in [("n1", engine.trie_n1), ("n2", engine.trie_n2), ("n4", engine.trie_n4)]:
            storage.save_trie(symbol, level, t)

        # Step 6: Paper trade on test data with training normalization stats
        console.print(f"[bold cyan]Step 3/5: Paper trading on test data (OOS)...[/bold cyan]")
        pt_config = PaperTraderConfig(
            symbol=symbol,
            timeframe=timeframe,
            initial_capital=capital,
            sax_alphabet_size=sax_config.get("alphabet_size", 8),
            sax_window_size=sax_config.get("window_size", 10),
            sax_strategy=sax_config.get("strategy", "ohlcv"),
            min_confidence=min_confidence,
            catastrophic_loss_pct=8.0,
            living_trie=False,  # No trie updates during OOS
            start_offset=train_end,  # Start at test period
            end_offset=0,  # Use all remaining data
            paa_mean=paa_mean,  # Use training normalization
            paa_std=paa_std,
        )

        trader = PaperTrader(config=pt_config)
        oos_result = trader.run()

        oos_trades = len(oos_result.trades)
        oos_wr = oos_result.win_rate
        oos_pnl_pct = oos_result.total_pnl_pct
        oos_max_dd = oos_result.max_drawdown
        oos_sharpe = oos_result.sharpe_ratio

        if oos_trades == 0:
            console.print("[yellow]No OOS trades generated. The system may not find matching patterns in test data.[/yellow]")
            console.print("[dim]This could indicate overfitting to training data or insufficient test data.[/dim]")

        # Step 7: Monte Carlo on OOS trades
        console.print(f"[bold cyan]Step 4/5: Running Monte Carlo on OOS trades...[/bold cyan]")
        mc_result = None
        if oos_trades >= 5:
            trades_pnl_pct = [t.pnl_pct / 100.0 for t in oos_result.trades]
            mc_config = MonteCarloConfig(
                simulations=simulations,
                seed=seed,
                initial_capital=capital,
            )
            mc_simulator = MonteCarloSimulator()
            with console.status(f"[bold green]Running {simulations} Monte Carlo simulations..."):
                mc_result = mc_simulator.simulate(trades_pnl_pct, config=mc_config)
        else:
            console.print(f"  [dim]Skipped: need at least 5 OOS trades for Monte Carlo (got {oos_trades})[/dim]")

        # Step 8: Display comprehensive comparison
        console.print(f"[bold cyan]Step 5/5: Generating validation report...[/bold cyan]")

        # Compute IS P&L from bootstrap (approximate)
        if is_trades > 0:
            is_pnl_pct = 0.0  # Bootstrap doesn't track cumulative P&L
            # Estimate from win rate and typical trade size
            avg_win = 3.0  # Approximate
            avg_loss = -2.0  # Approximate
            is_pnl_pct = (is_wins * avg_win + (is_trades - is_wins) * avg_loss) / is_trades * is_trades
        else:
            is_pnl_pct = 0.0

        # Degradation analysis
        wr_drop = is_wr - oos_wr if is_trades > 0 and oos_trades > 0 else 0.0
        pnl_ratio = oos_pnl_pct / is_pnl_pct if is_pnl_pct != 0 and oos_pnl_pct != 0 else 0.0

        if oos_trades == 0:
            verdict = "NO EDGE — system produced zero OOS trades"
            verdict_style = "bold red"
        elif oos_wr >= is_wr * 0.85 and oos_pnl_pct > 0:
            verdict = "STRONG EDGE — OOS performance close to IS"
            verdict_style = "bold green"
        elif oos_wr >= is_wr * 0.6 and oos_pnl_pct > 0:
            verdict = "MODERATE DEGRADATION — system has edge but IS results are inflated"
            verdict_style = "bold yellow"
        elif oos_pnl_pct > 0:
            verdict = "SIGNIFICANT DEGRADATION — edge exists but much weaker than IS suggests"
            verdict_style = "yellow"
        else:
            verdict = "NO EDGE — OOS results are negative, IS results are overfit"
            verdict_style = "bold red"

        # Format the report
        separator = "═" * 54
        thin_sep = "─" * 54

        console.print()
        console.print(f"[bold cyan]{separator}[/bold cyan]")
        console.print(f"  [bold]OUT-OF-SAMPLE VALIDATION REPORT[/bold]")
        console.print(f"[bold cyan]{separator}[/bold cyan]")
        console.print(f"  Symbol: {symbol} ({timeframe})")
        console.print(f"  Train/Test Split: {train_ratio:.0%}/{1 - train_ratio:.0%}")
        console.print(f"  Training: candles 0-{train_end} ({train_end} candles)")
        console.print(f"  Testing:  candles {train_end}-{total_candles} ({total_candles - train_end} candles)")
        console.print(f"  PAA Stats: mean={paa_mean:.6f}, std={paa_std:.6f}")
        console.print(f"[bold cyan]{thin_sep}[/bold cyan]")

        # In-sample results
        if is_trades > 0:
            console.print(f"  [bold]IN-SAMPLE (Training Bootstrap)[/bold]")
            console.print(f"  Trades: {is_trades} | WR: {is_wr:.1%}")
            console.print(f"  Trie: {train_patterns} patterns, {train_observations} observations")
        else:
            console.print(f"  [bold]IN-SAMPLE (Training Bootstrap)[/bold]")
            console.print(f"  No bootstrap trades generated")

        console.print(f"[bold cyan]{thin_sep}[/bold cyan]")

        # Out-of-sample results
        console.print(f"  [bold]OUT-OF-SAMPLE (Test)[/bold]")
        if oos_trades > 0:
            pnl_color = "green" if oos_pnl_pct >= 0 else "red"
            pnl_sign = "+" if oos_pnl_pct >= 0 else ""
            console.print(f"  Trades: {oos_trades} | WR: {oos_wr:.1%} | P&L: [{pnl_color}]{pnl_sign}{oos_pnl_pct:.2f}%[/{pnl_color}]")
            console.print(f"  Max DD: {oos_max_dd:.1%} | Sharpe: {oos_sharpe:.2f}")
        else:
            console.print(f"  No OOS trades generated")

        console.print(f"[bold cyan]{thin_sep}[/bold cyan]")

        # Degradation analysis
        if is_trades > 0 and oos_trades > 0:
            console.print(f"  [bold]DEGRADATION ANALYSIS[/bold]")
            console.print(f"  WR drop: {wr_drop:+.1f}pp ({is_wr:.1%} → {oos_wr:.1%})")
            if is_pnl_pct != 0:
                console.print(f"  P&L ratio: {pnl_ratio:.2f}x (OOS/IS)")
        console.print(f"  Verdict: [{verdict_style}]{verdict}[/{verdict_style}]")

        console.print(f"[bold cyan]{separator}[/bold cyan]")

        # Monte Carlo results
        if mc_result is not None:
            console.print()
            mc_summary = MonteCarloSimulator().generate_summary(mc_result)
            console.print(mc_summary)

            # Show interpretation
            console.print()
            if mc_result.risk_of_ruin < 0.01:
                console.print("[bold green]OOS MC VERDICT: Excellent - Very low risk of ruin[/bold green]")
            elif mc_result.risk_of_ruin < 0.05:
                console.print("[green]OOS MC VERDICT: Good - Acceptable risk for most traders[/green]")
            elif mc_result.risk_of_ruin < 0.10:
                console.print("[yellow]OOS MC VERDICT: Marginal - Consider reducing position sizes[/yellow]")
            else:
                console.print("[bold red]OOS MC VERDICT: Dangerous - High risk of blow-up[/bold red]")

    finally:
        # Restore original trie
        if original_trie is not None:
            storage.save_trie(symbol, "n3", original_trie)
            console.print(f"\n[dim]Original trie restored[/dim]")
        storage.close()


@cli.command("walk-forward")
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--folds", default=5, type=int, help="Number of walk-forward folds")
@click.option("--min-confidence", default=0.20, type=float,
              help="Minimum signal confidence to enter")
@click.option("--capital", "-c", default=10000.0, type=float,
              help="Initial capital per fold")
def walk_forward(symbol: str, timeframe: str, folds: int,
                 min_confidence: float, capital: float):
    """Walk-forward analysis with expanding window.

    v0.7.0: Runs multiple OOS validation folds with an expanding
    training window. Each fold:
      - Trains on all data up to that point (expanding window)
      - Tests on the next segment
      - Uses a fresh trie and fresh capital

    This reveals whether the system's edge is consistent across
    different time periods, which is critical for live trading.

    Data is split into (folds + 1) roughly equal segments:
      Fold 0: Train on segment 0,        Test on segment 1
      Fold 1: Train on segments 0-1,      Test on segment 2
      Fold 2: Train on segments 0-2,      Test on segment 3
      ...

    Each fold uses a FRESH PaperTrader with fresh capital.
    """
    from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig
    from ppmt.core.sax import SAXEncoder

    if folds < 2:
        console.print("[red]--folds must be at least 2[/red]")
        return

    config = load_config()
    sax_config = config.get("sax", {})
    storage = PPMTStorage()

    # Save original trie to restore later
    original_trie = storage.load_trie(symbol, "n3")

    try:
        # Load all data
        df = storage.load_ohlcv(symbol, timeframe)
        if df.empty:
            console.print(f"[red]No data found for {symbol}. Run 'ppmt ingest' first.[/red]")
            return

        total_candles = len(df)

        # Split data into (folds + 1) roughly equal segments
        num_segments = folds + 1
        segment_size = total_candles // num_segments
        if segment_size < 200:
            console.print(f"[red]Not enough data for {folds} folds. "
                          f"Need at least {200 * num_segments} candles, have {total_candles}.[/red]")
            return

        # Compute segment boundaries
        segment_ends = [segment_size * (i + 1) for i in range(num_segments)]
        # Make sure the last segment includes all remaining data
        segment_ends[-1] = total_candles

        # Classify asset
        classifier = AssetClassifier()
        info = classifier.classify(symbol)

        console.print()
        separator = "═" * 54
        thin_sep = "─" * 54
        console.print(f"[bold cyan]{separator}[/bold cyan]")
        console.print(f"  [bold]WALK-FORWARD ANALYSIS[/bold]")
        console.print(f"[bold cyan]{separator}[/bold cyan]")
        console.print(f"  Symbol: {symbol} ({timeframe}) | Folds: {folds}")
        console.print(f"  Total candles: {total_candles}")
        console.print(f"  Segment size: ~{segment_size} candles")
        console.print(f"  Window type: Expanding (each fold adds more training data)")
        console.print(f"[bold cyan]{thin_sep}[/bold cyan]")

        # Run each fold
        fold_results = []

        for fold_idx in range(folds):
            train_end = segment_ends[fold_idx]  # Expanding: train up to this segment end
            test_start = segment_ends[fold_idx]
            test_end = segment_ends[fold_idx + 1] if fold_idx + 1 < num_segments else total_candles

            df_train = df.iloc[:train_end]

            console.print(
                f"\n[bold yellow]Fold {fold_idx + 1}/{folds}:[/bold yellow] "
                f"Train [0:{train_end}] ({len(df_train)} candles), "
                f"Test [{test_start}:{test_end}] ({test_end - test_start} candles)"
            )

            # Encode training data and get normalization stats
            sax_encoder = SAXEncoder(
                alphabet_size=sax_config.get("alphabet_size", 8),
                window_size=sax_config.get("window_size", 10),
                strategy=sax_config.get("strategy", "ohlcv"),
            )
            train_symbols, paa_mean, paa_std = sax_encoder.encode_with_normalization(df_train)

            # Build trie on TRAINING data only (with pre-computed symbols)
            engine = PPMT(
                symbol=symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=sax_config.get("alphabet_size", 8),
                sax_window_size=sax_config.get("window_size", 10),
                sax_strategy=sax_config.get("strategy", "ohlcv"),
                weight_profile=info.weight_profile,
            )
            engine.build(df_train, pattern_length=5, symbols=train_symbols)

            # Run bootstrap on training data (1 pass)
            engine.bootstrap(df_train, pattern_length=5, bootstrap_ratio=1.0, verbose=False)

            trie = engine.trie_n3
            trie.propagate_metadata()

            fold_patterns = trie.pattern_count
            fold_observations = trie.trading_observations

            # Save trie for paper trader to load
            storage.save_trie(symbol, "n3", trie)
            for level, t in [("n1", engine.trie_n1), ("n2", engine.trie_n2), ("n4", engine.trie_n4)]:
                storage.save_trie(symbol, level, t)

            # Paper trade on test data with training normalization stats
            pt_config = PaperTraderConfig(
                symbol=symbol,
                timeframe=timeframe,
                initial_capital=capital,
                sax_alphabet_size=sax_config.get("alphabet_size", 8),
                sax_window_size=sax_config.get("window_size", 10),
                sax_strategy=sax_config.get("strategy", "ohlcv"),
                min_confidence=min_confidence,
                catastrophic_loss_pct=8.0,
                living_trie=False,  # No trie updates during OOS
                start_offset=test_start,
                end_offset=test_end,
                paa_mean=paa_mean,  # Use training normalization
                paa_std=paa_std,
            )

            trader = PaperTrader(config=pt_config)
            result = trader.run()

            n_trades = len(result.trades)
            if n_trades == 0:
                console.print(f"  [yellow]No trades in test window[/yellow]")
                fold_results.append({
                    "fold": fold_idx + 1,
                    "train_end": train_end,
                    "test_start": test_start,
                    "test_end": test_end,
                    "train_candles": len(df_train),
                    "test_candles": test_end - test_start,
                    "trades": 0,
                    "win_rate": 0.0,
                    "pnl_pct": 0.0,
                    "sharpe": 0.0,
                    "max_dd": 0.0,
                    "patterns": fold_patterns,
                    "observations": fold_observations,
                })
                continue

            wins = sum(1 for t in result.trades if t.pnl_pct > 0)
            wr = result.win_rate
            pnl_pct = result.total_pnl_pct
            sharpe = result.sharpe_ratio
            max_dd = result.max_drawdown

            pnl_color = "green" if pnl_pct > 0 else "red"
            console.print(
                f"  Trades: {n_trades} | WR: {wr:.1%} | "
                f"P&L: [{pnl_color}]{pnl_pct:+.2f}%[/{pnl_color}] | "
                f"Sharpe: {sharpe:.2f}"
            )

            fold_results.append({
                "fold": fold_idx + 1,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "train_candles": len(df_train),
                "test_candles": test_end - test_start,
                "trades": n_trades,
                "win_rate": wr,
                "pnl_pct": pnl_pct,
                "sharpe": sharpe,
                "max_dd": max_dd,
                "patterns": fold_patterns,
                "observations": fold_observations,
            })

        # Aggregate results
        valid_folds = [f for f in fold_results if f["trades"] > 0]

        if not valid_folds:
            console.print("\n[red]No folds produced trades. Cannot generate summary.[/red]")
            return

        total_oos_trades = sum(f["trades"] for f in valid_folds)
        avg_wr = np.mean([f["win_rate"] for f in valid_folds])
        avg_pnl = np.mean([f["pnl_pct"] for f in valid_folds])
        profitable_folds = sum(1 for f in valid_folds if f["pnl_pct"] > 0)

        # Find best and worst folds
        best_fold = max(valid_folds, key=lambda f: f["pnl_pct"])
        worst_fold = min(valid_folds, key=lambda f: f["pnl_pct"])

        # Verdict
        if profitable_folds == len(valid_folds) and avg_pnl > 50:
            verdict = "CONSISTENT EDGE — profitable in all folds"
            verdict_style = "bold green"
        elif profitable_folds >= len(valid_folds) * 0.7 and avg_pnl > 0:
            verdict = "MODERATE EDGE — profitable in most folds"
            verdict_style = "bold yellow"
        elif avg_pnl > 0:
            verdict = "WEAK EDGE — inconsistent across folds"
            verdict_style = "yellow"
        else:
            verdict = "NO EDGE — OOS results are negative"
            verdict_style = "bold red"

        # Print summary report
        console.print()
        console.print(f"[bold cyan]{separator}[/bold cyan]")
        console.print(f"  [bold]WALK-FORWARD ANALYSIS REPORT[/bold]")
        console.print(f"[bold cyan]{separator}[/bold cyan]")
        console.print(f"  Symbol: {symbol} ({timeframe}) | Folds: {folds}")
        console.print(f"[bold cyan]{thin_sep}[/bold cyan]")

        for f in fold_results:
            fold_label = f"Fold {f['fold']}"
            if f["trades"] == 0:
                console.print(f"  {fold_label}: Train 0-{f['train_end']}   Test {f['test_start']}-{f['test_end']}")
                console.print(f"    [yellow]No trades generated[/yellow]")
            else:
                pnl_color = "green" if f["pnl_pct"] > 0 else "red"
                console.print(
                    f"  {fold_label}: Train 0-{f['train_end']}   "
                    f"Test {f['test_start']}-{f['test_end']}"
                )
                console.print(
                    f"    Trades: {f['trades']} | WR: {f['win_rate']:.1%} | "
                    f"P&L: [{pnl_color}]{f['pnl_pct']:+.2f}%[/{pnl_color}] | "
                    f"Sharpe: {f['sharpe']:.2f}"
                )

        console.print(f"[bold cyan]{thin_sep}[/bold cyan]")
        console.print(f"  [bold]AGGREGATE WALK-FORWARD RESULTS[/bold]")
        console.print(f"  Total OOS Trades: {total_oos_trades}")
        console.print(f"  Average WR: {avg_wr:.1%} | Average P&L per fold: {avg_pnl:+.2f}%")
        console.print(
            f"  WR Consistency: {profitable_folds}/{len(valid_folds)} folds profitable "
            f"({profitable_folds / len(valid_folds):.0%})"
        )
        if worst_fold["trades"] > 0:
            console.print(
                f"  Worst Fold: Fold {worst_fold['fold']} "
                f"({worst_fold['pnl_pct']:+.2f}%, WR {worst_fold['win_rate']:.1%})"
            )
        if best_fold["trades"] > 0:
            console.print(
                f"  Best Fold: Fold {best_fold['fold']} "
                f"({best_fold['pnl_pct']:+.2f}%, WR {best_fold['win_rate']:.1%})"
            )
        console.print(f"  Verdict: [{verdict_style}]{verdict}[/{verdict_style}]")
        console.print(f"[bold cyan]{separator}[/bold cyan]")

    finally:
        # Restore original trie
        if original_trie is not None:
            storage.save_trie(symbol, "n3", original_trie)
            console.print(f"\n[dim]Original trie restored[/dim]")
        storage.close()


@cli.command("monte-carlo")
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--simulations", "-n", default=1000, type=int, help="Number of Monte Carlo simulations")
@click.option("--capital", "-c", default=10000.0, type=float, help="Initial capital")
@click.option("--seed", default=42, type=int, help="Random seed for reproducibility")
@click.option("--ruin-threshold", default=0.5, type=float, help="Ruin threshold (fraction of capital)")
@click.option("--paper-first", is_flag=True, default=False, help="Run paper trading first, then Monte Carlo on results")
@click.option("--min-confidence", default=0.20, type=float, help="Min confidence for paper trading (with --paper-first)")
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




# ══════════════════════════════════════════════════════════════════════════════
# v0.7.0: One-Click Validation Suite (CLI + Dashboard)
# ══════════════════════════════════════════════════════════════════════════════

@cli.command("validate-all")
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--train-ratio", default=0.7, type=float,
              help="P0: Training data ratio (default: 0.7)")
@click.option("--mc-simulations", default=1000, type=int,
              help="P1: Monte Carlo simulations (default: 1000)")
@click.option("--wf-windows", default=5, type=int,
              help="P2: Walk-forward windows (default: 5)")
@click.option("--capital", "-c", default=10000.0, type=float,
              help="Initial capital for simulations")
@click.option("--pattern-length", "-p", default=5, type=int,
              help="SAX blocks per pattern")
@click.option("--seed", default=42, type=int, help="Random seed")
@click.option("--json-output", is_flag=True, default=False,
              help="Output as JSON (for API bridge)")
def validate_all(symbol: str, timeframe: str, train_ratio: float,
                 mc_simulations: int, wf_windows: int, capital: float,
                 pattern_length: int, seed: int, json_output: bool):
    """One-click validation suite: P0 (OOS) + P1 (MC) + P2 (Walk-Forward).

    v0.7.0: Runs the complete ValidationEngine to produce a composite
    ROBUST / MARGINAL / OVERFIT / INSUFFICIENT_DATA verdict.

    This is the one-click solution that combines all three validation
    approaches into a single score (0-100 points):
      P0 Out-of-Sample:  40 pts max
      P1 Monte Carlo:    30 pts max
      P2 Walk-Forward:   30 pts max
    """
    import json as json_mod
    from ppmt.engine.validator import ValidationEngine, ValidationConfig

    config = load_config()
    sax_config = config.get("sax", {})
    storage = PPMTStorage()

    df = storage.load_ohlcv(symbol, timeframe)
    storage.close()

    if df.empty:
        console.print(f"[red]No data found for {symbol}. Run 'ppmt ingest' first.[/red]")
        return

    val_config = ValidationConfig(
        symbol=symbol,
        train_ratio=train_ratio,
        mc_simulations=mc_simulations,
        wf_window_count=wf_windows,
        initial_capital=capital,
        pattern_length=pattern_length,
        sax_alphabet_size=sax_config.get("alphabet_size", 8),
        sax_window_size=sax_config.get("window_size", 10),
        sax_strategy=sax_config.get("strategy", "ohlcv"),
        seed=seed,
    )

    console.print(f"\n[bold cyan]╔══════════════════════════════════════════════╗[/bold cyan]")
    console.print(f"[bold cyan]║     PPMT VALIDATION SUITE — ONE CLICK        ║[/bold cyan]")
    console.print(f"[bold cyan]╚══════════════════════════════════════════════╝[/bold cyan]")
    console.print(f"  Symbol: {symbol} ({timeframe}) | {len(df)} candles")
    console.print(f"  P0: OOS {train_ratio:.0%}/{1 - train_ratio:.0%} | P1: MC {mc_simulations} sims | P2: WF {wf_windows} windows")
    console.print()

    engine = ValidationEngine(config=val_config)

    with console.status("[bold green]Running full validation suite..."):
        verdict = engine.run_full_validation(df)

    if json_output:
        # For API bridge - output pure JSON
        import json as _json

        class NumpyEncoder(_json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, (np.integer,)):
                    return int(obj)
                if isinstance(obj, (np.floating,)):
                    return float(obj)
                if isinstance(obj, (np.bool_,)):
                    return bool(obj)
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
                return super().default(obj)

        print(_json.dumps(verdict.to_dict(), cls=NumpyEncoder))
        return

    # Rich CLI output
    # Verdict banner
    verdict_colors = {
        "ROBUST": "bold green",
        "MARGINAL": "bold yellow",
        "OVERFIT": "bold red",
        "INSUFFICIENT_DATA": "bold gray",
    }
    vc = verdict_colors.get(verdict.recommendation, "bold white")

    console.print(Panel(
        f"[{vc}]{verdict.recommendation}[/{vc}]\n"
        f"Score: {verdict.confidence_score:.0f}/100 points\n"
        f"P0: {verdict.p0_score:.0f}/40 | P1: {verdict.p1_score:.0f}/30 | P2: {verdict.p2_score:.0f}/30",
        title="VERDICT",
        border_style="cyan",
    ))

    # P0 Summary
    oos = verdict.oos
    console.print(f"\n[bold]P0: Out-of-Sample[/bold]")
    console.print(f"  IS:  {oos.is_total_trades} trades, WR {oos.is_win_rate:.1%}, P&L {oos.is_total_pnl_pct:+.2f}%")
    console.print(f"  OOS: {oos.oos_total_trades} trades, WR {oos.oos_win_rate:.1%}, P&L {oos.oos_total_pnl_pct:+.2f}%")
    console.print(f"  Degradation: {oos.pnl_degradation_pct:.1f}% | OOS Ratio: {oos.oos_ratio:.3f}")

    # P1 Summary
    mc = verdict.mc
    if mc.n_trades_used > 0:
        console.print(f"\n[bold]P1: Monte Carlo[/bold]")
        console.print(f"  Risk of Ruin: {mc.risk_of_ruin_pct:.2f}% | Profit Prob: {mc.profit_probability_pct:.1f}%")
        console.print(f"  P95 Max DD: {mc.p95_max_drawdown_pct:.1f}% | Median Equity: ${mc.median_final_equity:,.2f}")

    # P2 Summary
    wf = verdict.wf
    if wf.total_windows > 0:
        console.print(f"\n[bold]P2: Walk-Forward[/bold]")
        console.print(f"  Aggregate WFE: {wf.aggregate_wfe:.1%} | Consistency: {wf.consistency_pct:.0f}%")
        console.print(f"  Profitable windows: {wf.profitable_windows}/{wf.total_windows}")
        console.print(f"  Overall degradation: {wf.overall_degradation:.1f}%")

    console.print(f"\n[dim]Elapsed: {verdict.elapsed_seconds:.1f}s | Summary: {verdict.summary}[/dim]")


if __name__ == "__main__":
    cli()
