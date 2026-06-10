"""
PPMT CLI - Command Line Interface V9.0

Usage:
  ppmt init                          Initialize database and config
  ppmt ingest --symbol BTC/USDT      Fetch and store historical data
  ppmt build --symbol BTC/USDT       Build Trie from stored data
  ppmt predict --symbol BTC/USDT     Show prediction from current pattern
  ppmt run --symbol BTC/USDT         Real-time pattern matching
  ppmt stats --symbol BTC/USDT       Show pattern statistics
  ppmt list                          List tracked assets
  ppmt backtest --symbol BTC/USDT    Walk-forward backtest (static split)
  ppmt rolling-backtest --symbol BTC/USDT  Rolling walk-forward backtest
  ppmt dashboard                     Launch web dashboard (V9.0)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
import numpy as np
import pandas as pd
import yaml
from rich.console import Console
from rich.table import Table

from ppmt.data.storage import PPMTStorage
from ppmt.data.collector import DataCollector
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.ppmt import PPMT
from ppmt.engine.prediction import PredictionEngine
from ppmt.core.sax import SAXEncoder
from ppmt.core.matcher import FuzzyMatcher

console = Console()

CONFIG_DIR = os.path.expanduser("~/.ppmt")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.yaml")

VERSION = "V10.0"


def load_config() -> dict:
    """Load configuration from file."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    return {}


# ---------------------------------------------------------------------------
# Helper: build a fresh PPMT engine from config
# ---------------------------------------------------------------------------
def _make_engine(symbol: str, config: dict) -> PPMT:
    classifier = AssetClassifier()
    info = classifier.classify(symbol)
    sax_config = config.get("sax", {})
    return PPMT(
        symbol=symbol,
        asset_class=info.asset_class,
        sax_alphabet_size=sax_config.get("alphabet_size", 8),
        sax_window_size=sax_config.get("window_size", 10),
        sax_strategy=sax_config.get("strategy", "ohlcv"),
        weight_profile=info.weight_profile,
    )


# ---------------------------------------------------------------------------
# Helper: load OHLCV data (from CSV or SQLite DB)
# ---------------------------------------------------------------------------
def _load_data(symbol: str, csv_path: str | None, timeframe: str = "1h") -> pd.DataFrame:
    """Load OHLCV data from CSV file or SQLite database."""
    if csv_path:
        return _load_csv_data(csv_path)

    # Try loading from SQLite database
    storage = PPMTStorage()
    df = storage.load_ohlcv(symbol, timeframe)
    storage.close()

    if df.empty:
        raise ValueError(
            f"No data found for {symbol} ({timeframe}) in database. "
            f"Run 'ppmt ingest -s {symbol}' first, or use --csv to specify a file."
        )
    return df


# ---------------------------------------------------------------------------
# Helper: load OHLCV from CSV (standalone, no DB needed)
# ---------------------------------------------------------------------------
def _load_csv_data(csv_path: str) -> pd.DataFrame:
    """Load OHLCV data from a CSV file."""
    df = pd.read_csv(csv_path)
    # Normalize column names
    col_map = {}
    for col in df.columns:
        lower = col.strip().lower()
        if lower in ("open", "high", "low", "close", "volume"):
            col_map[col] = lower
        elif lower in ("date", "datetime", "timestamp", "time"):
            col_map[col] = "timestamp"
    df = df.rename(columns=col_map)

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")

    # Ensure required columns
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    if "volume" not in df.columns:
        df["volume"] = 0

    df = df.sort_index()
    return df


# ---------------------------------------------------------------------------
# Helper: run a single backtest window (train → test)
# ---------------------------------------------------------------------------
def _run_backtest_window(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    all_df: pd.DataFrame,
    symbol: str,
    config: dict,
    pattern_length: int = 5,
    forward_window: int = 5,
    train_ratio: float = 0.7,
    won_rr_threshold: Optional[float] = None,
    min_dir_count: int = 0,
    position_size_pct: float = 1.0,
) -> dict:
    """
    Run a single walk-forward backtest window.

    1. Encode training data to get normalization stats (V7.9 fix)
    2. Encode ALL data with training stats
    3. Build trie on training portion
    4. Walk through test portion, generating signals
    5. Compute P&L

    Returns a dict with trade list and summary stats.
    """
    encoder = SAXEncoder(
        alphabet_size=config.get("sax", {}).get("alphabet_size", 8),
        window_size=config.get("sax", {}).get("window_size", 10),
        strategy=config.get("sax", {}).get("strategy", "ohlcv"),
    )

    # V7.9: Encode training data first to establish normalization stats
    train_symbols, paa_mean, paa_std = encoder.encode_with_normalization(train_df)

    # Encode ALL data using training normalization stats
    all_symbols, _, _ = encoder.encode_with_normalization(all_df, paa_mean=paa_mean, paa_std=paa_std)

    # Verify training portion matches
    mismatch_count = sum(
        1 for a, b in zip(train_symbols, all_symbols[:len(train_symbols)]) if a != b
    )

    # Build engine with training symbols
    engine = _make_engine(symbol, config)
    train_count = engine.build(
        train_df,
        pattern_length=pattern_length,
        forward_window=forward_window,
        won_rr_threshold=won_rr_threshold,
        symbols=train_symbols,
    )

    if train_count == 0:
        return {
            "trades": [],
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl_pct": 0.0,
            "long_trades": 0,
            "short_trades": 0,
            "long_wr": 0.0,
            "short_wr": 0.0,
            "avg_rr": 0.0,
            "patterns_trained": 0,
            "norm_mismatch": mismatch_count,
        }

    # Walk through test data and generate signals
    test_start_idx = len(train_df)
    trades = []

    # We walk through SAX blocks in the test region
    n_train_symbols = len(train_symbols)
    window_size = encoder.window_size

    # Use the engine's fuzzy matcher for noise-tolerant matching
    fuzzy_matcher = FuzzyMatcher(sax_encoder=encoder, threshold=0.85)

    for i in range(n_train_symbols, len(all_symbols) - pattern_length):
        # Only consider symbols that map to the test portion
        candle_idx = i * window_size
        if candle_idx < test_start_idx:
            continue
        if candle_idx + (pattern_length + forward_window) * window_size > len(all_df):
            break

        current_pattern = all_symbols[i:i + pattern_length]

        # Search ALL trie levels: N3 (per-asset) first, then N2 (asset class), then N1 (universal)
        # Also try fuzzy matching for noise tolerance
        best_node = None
        best_level = None

        for trie, level_name in [
            (engine.trie_n3, "N3"),
            (engine.trie_n2, "N2"),
            (engine.trie_n1, "N1"),
        ]:
            # Try exact match first
            node = trie.search(current_pattern)
            if node is not None and node.metadata.historical_count >= max(1, min_dir_count):
                best_node = node
                best_level = level_name
                break

            # Try fuzzy match
            match_result = fuzzy_matcher.best_match(trie, current_pattern)
            if (match_result.node is not None and
                match_result.node.metadata.historical_count >= max(1, min_dir_count)):
                best_node = match_result.node
                best_level = f"{level_name}(fuzzy)"
                break

        # Also try shorter patterns (prefix search) if no match at full length
        if best_node is None and pattern_length > 3:
            for shorter_len in range(pattern_length - 1, 2, -1):
                short_pattern = all_symbols[i:i + shorter_len]
                for trie, level_name in [
                    (engine.trie_n3, "N3"),
                    (engine.trie_n2, "N2"),
                    (engine.trie_n1, "N1"),
                ]:
                    node, matched_depth = trie.search_prefix(short_pattern)
                    if (node is not None and matched_depth >= shorter_len and
                        node.metadata.historical_count >= max(1, min_dir_count)):
                        best_node = node
                        best_level = f"{level_name}(prefix-{matched_depth})"
                        break
                if best_node is not None:
                    break

        if best_node is None:
            continue

        meta = best_node.metadata

        # Determine direction from expected_move
        if abs(meta.expected_move_pct) < 0.001:
            continue

        direction = "LONG" if meta.expected_move_pct > 0 else "SHORT"

        # Compute actual outcome from price data
        entry_candle = i * window_size
        exit_candle = (i + pattern_length + forward_window) * window_size
        if exit_candle > len(all_df):
            continue

        entry_price = all_df["close"].iloc[entry_candle]
        exit_price = all_df["close"].iloc[exit_candle - 1]

        if direction == "LONG":
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0 * position_size_pct
        else:
            pnl_pct = ((entry_price - exit_price) / entry_price) * 100.0 * position_size_pct

        won = pnl_pct > 0

        # Compute actual R:R from this trade
        window_df = all_df.iloc[entry_candle:exit_candle]
        high = window_df["high"].max()
        low = window_df["low"].min()

        if direction == "LONG":
            favorable = ((high - entry_price) / entry_price) * 100.0
            drawdown = ((low - entry_price) / entry_price) * 100.0
        else:
            favorable = ((entry_price - low) / entry_price) * 100.0
            drawdown = ((entry_price - high) / entry_price) * 100.0

        rr = favorable / abs(drawdown) if abs(drawdown) > 1e-10 else 0

        # Sizing multiplier from metadata
        sizing = meta.sizing_signal

        trade = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "won": won,
            "rr": rr,
            "historical_count": meta.historical_count,
            "win_rate_historical": meta.win_rate,
            "expected_move": meta.expected_move_pct,
            "sizing_signal": sizing,
            "pattern": "".join(current_pattern),
            "match_level": best_level,
            "candle_idx": entry_candle,
        }
        trades.append(trade)

    # Compute summary
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "trades": [],
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl_pct": 0.0,
            "long_trades": 0,
            "short_trades": 0,
            "long_wr": 0.0,
            "short_wr": 0.0,
            "avg_rr": 0.0,
            "patterns_trained": train_count,
            "norm_mismatch": mismatch_count,
        }

    wins = sum(1 for t in trades if t["won"])
    long_trades = [t for t in trades if t["direction"] == "LONG"]
    short_trades = [t for t in trades if t["direction"] == "SHORT"]
    long_wins = sum(1 for t in long_trades if t["won"])
    short_wins = sum(1 for t in short_trades if t["won"])
    total_pnl = sum(t["pnl_pct"] for t in trades)
    avg_rr = np.mean([t["rr"] for t in trades]) if trades else 0

    return {
        "trades": trades,
        "total_trades": total_trades,
        "win_rate": wins / total_trades if total_trades > 0 else 0,
        "total_pnl_pct": total_pnl,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_wr": long_wins / len(long_trades) if long_trades else 0,
        "short_wr": short_wins / len(short_trades) if short_trades else 0,
        "avg_rr": avg_rr,
        "patterns_trained": train_count,
        "norm_mismatch": mismatch_count,
    }


# ===================================================================
# CLI Commands
# ===================================================================

@click.group()
@click.version_option(version="10.0.0")
def cli():
    """PPMT - Progressive Pattern Matching Trie Engine"""
    pass


@cli.command()
def init():
    """Initialize PPMT database and configuration."""
    os.makedirs(CONFIG_DIR, exist_ok=True)

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
            with open(CONFIG_FILE, "w") as f:
                yaml.dump({"sax": {"alphabet_size": 8, "window_size": 10}}, f)
            console.print(f"[green]Created minimal config at {CONFIG_FILE}[/green]")
    else:
        console.print(f"[yellow]Config already exists at {CONFIG_FILE}[/yellow]")

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
            console.print(f"[cyan]Importing {symbol} ({timeframe}) from CSV...[/cyan]")
            df = collector.import_csv(symbol, timeframe, csv_path)
        else:
            console.print(f"[cyan]Fetching {symbol} ({timeframe}) from {exchange}...[/cyan]")
            df = collector.fetch_and_save(symbol, timeframe, days)

        if df.empty:
            console.print("[red]No data fetched. Check symbol and exchange.[/red]")
            return

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

    df = storage.load_ohlcv(symbol, timeframe)
    if df.empty:
        console.print(f"[red]No data found for {symbol}. Run 'ppmt ingest' first.[/red]")
        return

    console.print(f"[cyan]Building PPMT for {symbol} ({len(df)} candles)...[/cyan]")

    classifier = AssetClassifier()
    info = classifier.classify(symbol)

    sax_config = config.get("sax", {})
    engine = PPMT(
        symbol=symbol,
        asset_class=info.asset_class,
        sax_alphabet_size=sax_config.get("alphabet_size", 8),
        sax_window_size=sax_config.get("window_size", 10),
        sax_strategy=sax_config.get("strategy", "ohlcv"),
        weight_profile=info.weight_profile,
    )

    count = engine.build(df, pattern_length=pattern_length)

    for level, trie in [
        ("n1", engine.trie_n1),
        ("n2", engine.trie_n2),
        ("n3", engine.trie_n3),
        ("n4", engine.trie_n4),
    ]:
        storage.save_trie(symbol, level, trie)
        console.print(f"  N{level[-1]} Trie: {trie.pattern_count} patterns, max depth {trie.max_depth}")

    storage.save_engine_state(symbol, engine.get_stats())
    console.print(f"[green]Built {count} patterns for {symbol}[/green]")
    stats = engine.get_stats()
    console.print(f"  Weights: {engine.weights}")

    storage.close()


@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
def stats(symbol: str, timeframe: str):
    """Show PPMT statistics for an asset."""
    storage = PPMTStorage()

    state = storage.load_engine_state(symbol)
    if state is None:
        console.print(f"[red]No engine state for {symbol}. Run 'ppmt build' first.[/red]")
        return

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

    if state:
        console.print(f"\nWeights: {state.get('weight_profile', 'default')}")
        console.print(f"Asset Class: {state.get('asset_class', 'unknown')}")
        console.print(f"Total Patterns Built: {state.get('total_patterns_built', 0)}")

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

    df = storage.load_ohlcv(symbol, timeframe)
    if df.empty:
        console.print(f"[red]No data found for {symbol}. Run 'ppmt ingest' first.[/red]")
        return

    trie = storage.load_trie(symbol, "n3")
    if trie is None:
        console.print(f"[red]No Trie built for {symbol}. Run 'ppmt build' first.[/red]")
        return

    sax_config = config.get("sax", {})
    encoder = SAXEncoder(
        alphabet_size=sax_config.get("alphabet_size", 8),
        window_size=sax_config.get("window_size", 10),
        strategy=sax_config.get("strategy", "ohlcv"),
    )

    recent_df = df.tail(100)
    symbols = encoder.encode(recent_df)

    if not symbols:
        console.print("[red]Could not encode recent data.[/red]")
        return

    current_symbols = symbols[-5:] if len(symbols) >= 5 else symbols
    current_price = price or float(df["close"].iloc[-1])

    tf_hours = {"1m": 1/60, "5m": 5/60, "15m": 15/60, "1h": 1, "4h": 4, "1d": 24}.get(timeframe, 1)

    pred_engine = PredictionEngine(trie, prediction_depth=depth)
    prediction = pred_engine.predict(
        current_symbols=current_symbols,
        entry_price=current_price,
        timeframe_hours=tf_hours,
        symbol=symbol,
    )

    console.print(prediction.format_summary(timeframe_hours=tf_hours))

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

        if mock_signal.metadata_sizing_signal >= 1.5:
            console.print(f"  → [bold green]2.0x base position (HIGH CONVICTION)[/bold green]")
        elif mock_signal.metadata_sizing_signal >= 1.0:
            console.print(f"  → [green]1.0x base position (NORMAL)[/green]")
        elif mock_signal.metadata_sizing_signal >= 0.5:
            console.print(f"  → [yellow]0.5x base position (LOW CONVICTION)[/yellow]")
        else:
            console.print(f"  → [red]0.25x base position or REJECT[/red]")

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
@click.option("--paper", is_flag=True, default=True, help="Paper trading mode (no real money)")
@click.option("--capital", default=10000.0, type=float, help="Initial capital")
@click.option("--position-size", default=0.02, type=float, help="Position size as fraction of equity")
def run(symbol: str, timeframe: str, paper: bool, capital: float, position_size: float):
    """Run real-time pattern matching (paper mode by default).

    Use --paper for safe simulation without real money (default).
    Examples:
      ppmt run -s BTC/USDT --paper
      ppmt run -s ETH/USDT --capital 5000 --paper
    """
    mode = "PAPER" if paper else "LIVE"
    console.print(f"[cyan]Starting PPMT {mode} trading for {symbol}...[/cyan]")
    console.print(f"  Mode: {mode}")
    console.print(f"  Capital: ${capital:,.2f}")
    console.print(f"  Position Size: {position_size * 100:.1f}%")
    if paper:
        console.print("[yellow]Paper trading: no real money at risk.[/yellow]")
    else:
        console.print("[red]LIVE mode: real money at risk![/red]")
    console.print("[yellow]Exchange API connection required for live data.[/yellow]")
    console.print("Streaming engine ready — connect to exchange for real-time execution.")


# ===================================================================
# V7.9: Static Walk-Forward Backtest
# ===================================================================
@cli.command()
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--csv", "csv_path", default=None, help="Path to OHLCV CSV file (optional, uses DB if not specified)")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe (for DB lookup)")
@click.option("--train-ratio", default=0.7, type=float, help="Training data ratio (default: 0.7)")
@click.option("--pattern-length", "-p", default=5, type=int, help="SAX blocks per pattern")
@click.option("--forward-window", "-f", default=5, type=int, help="Forward window in SAX blocks")
@click.option("--position-size", default=1.0, type=float, help="Position size multiplier")
@click.option("--min-dir-count", default=0, type=int, help="Min directional count for signal (0=disabled)")
def backtest(
    symbol: str,
    csv_path: str | None,
    timeframe: str,
    train_ratio: float,
    pattern_length: int,
    forward_window: int,
    position_size: float,
    min_dir_count: int,
):
    """
    Walk-forward backtest with static train/test split (V7.9).

    Uses training z-score stats for both train and test encoding
    to ensure SAX symbol consistency across regimes.

    Data source: use --csv for a CSV file, or omit to load from
    the SQLite database (requires 'ppmt ingest' first).
    """
    config = load_config()

    console.print(f"\n[bold cyan]PPMT Walk-Forward Backtest {VERSION}[/bold cyan]")
    console.print(f"  Symbol: {symbol}")
    console.print(f"  Data source: {'CSV: ' + csv_path if csv_path else 'SQLite DB'}")

    # Load data
    try:
        df = _load_data(symbol, csv_path, timeframe)
    except Exception as e:
        console.print(f"[red]Error loading data: {e}[/red]")
        return

    console.print(f"  Candles: {len(df)}")
    console.print(f"  Train ratio: {train_ratio:.0%}")
    console.print(f"  Pattern length: {pattern_length}")
    console.print(f"  Forward window: {forward_window}")

    # Split
    split_idx = int(len(df) * train_ratio)
    train_df = df.iloc[:split_idx]
    test_df = df.iloc[split_idx:]

    console.print(f"  Train: {len(train_df)} candles | Test: {len(test_df)} candles")

    # Run backtest
    result = _run_backtest_window(
        train_df=train_df,
        test_df=test_df,
        all_df=df,
        symbol=symbol,
        config=config,
        pattern_length=pattern_length,
        forward_window=forward_window,
        train_ratio=train_ratio,
        min_dir_count=min_dir_count,
        position_size_pct=position_size,
    )

    # Display results
    console.print(f"\n[bold]─── Backtest Results ───[/bold]")
    console.print(f"  Patterns trained: {result['patterns_trained']}")
    console.print(f"  Norm mismatches: {result['norm_mismatch']}")
    console.print(f"  Total trades: {result['total_trades']}")

    if result["total_trades"] > 0:
        console.print(f"  Win Rate: {result['win_rate']:.1%}")
        console.print(f"  Total P&L: {result['total_pnl_pct']:+.2f}%")
        console.print(f"  Avg R:R: {result['avg_rr']:.2f}")
        console.print(f"  LONG:  {result['long_trades']} trades, {result['long_wr']:.1%} WR")
        console.print(f"  SHORT: {result['short_trades']} trades, {result['short_wr']:.1%} WR")

        # Per-trade details
        if result["trades"]:
            table = Table(title="Trade Log (last 20)")
            table.add_column("#", justify="right", style="cyan")
            table.add_column("Dir")
            table.add_column("Entry", justify="right")
            table.add_column("Exit", justify="right")
            table.add_column("P&L%", justify="right")
            table.add_column("WR_hist", justify="right")
            table.add_column("Pattern")

            for idx, t in enumerate(result["trades"][-20:], 1):
                pnl_color = "green" if t["won"] else "red"
                table.add_row(
                    str(idx),
                    t["direction"],
                    f"{t['entry_price']:.2f}",
                    f"{t['exit_price']:.2f}",
                    f"[{pnl_color}]{t['pnl_pct']:+.2f}%[/{pnl_color}]",
                    f"{t['win_rate_historical']:.0%}",
                    t["pattern"],
                )
            console.print(table)
    else:
        console.print("[yellow]No trades generated in backtest.[/yellow]")

    # Save results to JSON
    output_dir = os.path.dirname(csv_path) if csv_path else os.path.expanduser("~/.ppmt/backtest_results")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(output_dir, f"backtest_{symbol.replace('/', '_')}_{timestamp}.json")

    # Convert trades for JSON serialization
    json_result = {k: v for k, v in result.items() if k != "trades"}
    json_result["version"] = VERSION
    json_result["symbol"] = symbol
    json_result["trades"] = [
        {k: v for k, v in t.items() if k != "won"} for t in result["trades"]
    ]
    # Convert numpy types
    json_result = json.loads(json.dumps(json_result, default=str))

    with open(output_file, "w") as f:
        json.dump(json_result, f, indent=2, default=str)
    console.print(f"\n[green]Results saved to {output_file}[/green]")


# ===================================================================
# V8.0: Rolling Walk-Forward Backtest
# ===================================================================
@cli.command("rolling-backtest")
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--csv", "csv_path", default=None, help="Path to OHLCV CSV file (optional, uses DB if not specified)")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe (for DB lookup)")
@click.option("--train-candles", default=4000, type=int,
              help="Training window size in candles (default: 4000)")
@click.option("--test-candles", default=1000, type=int,
              help="Test window size in candles (default: 1000)")
@click.option("--step-candles", default=500, type=int,
              help="Step size for sliding window in candles (default: 500)")
@click.option("--pattern-length", "-p", default=5, type=int, help="SAX blocks per pattern")
@click.option("--forward-window", "-f", default=5, type=int, help="Forward window in SAX blocks")
@click.option("--position-size", default=1.0, type=float, help="Position size multiplier")
@click.option("--min-dir-count", default=0, type=int, help="Min directional count (0=disabled)")
@click.option("--save-trades", is_flag=True, help="Save individual trade details to JSON")
def rolling_backtest(
    symbol: str,
    csv_path: str | None,
    timeframe: str,
    train_candles: int,
    test_candles: int,
    step_candles: int,
    pattern_length: int,
    forward_window: int,
    position_size: float,
    min_dir_count: int,
    save_trades: bool,
):
    """
    Rolling Walk-Forward Backtest (V8.0).

    Trains on a sliding window of `train_candles`, tests on the next
    `test_candles`, then slides forward by `step_candles`. Repeats
    until the entire dataset is consumed.

    This is the most robust validation method: it shows whether the
    system is consistently profitable across different market regimes,
    not just a single static split.

    Data source: use --csv for a CSV file, or omit to load from
    the SQLite database (requires 'ppmt ingest' first).

    Examples:
      ppmt rolling-backtest -s BTC/USDT
      ppmt rolling-backtest -s BTC/USDT --csv data.csv --train-candles 4000
    """
    config = load_config()

    console.print(f"\n[bold cyan]PPMT Rolling Walk-Forward Backtest {VERSION}[/bold cyan]")
    console.print(f"  Symbol: {symbol}")
    console.print(f"  Data source: {'CSV: ' + csv_path if csv_path else 'SQLite DB'}")

    # Load data
    try:
        df = _load_data(symbol, csv_path, timeframe)
    except Exception as e:
        console.print(f"[red]Error loading data: {e}[/red]")
        return

    total_candles = len(df)
    console.print(f"  Total candles: {total_candles}")
    console.print(f"  Train window: {train_candles} candles")
    console.print(f"  Test window: {test_candles} candles")
    console.print(f"  Step size: {step_candles} candles")
    console.print(f"  Pattern length: {pattern_length}")
    console.print(f"  Forward window: {forward_window}")

    # Calculate number of windows
    n_windows = 0
    start = 0
    while start + train_candles + test_candles <= total_candles:
        n_windows += 1
        start += step_candles

    if n_windows == 0:
        console.print(f"[red]Dataset too small for rolling backtest. Need at least "
                      f"{train_candles + test_candles} candles, got {total_candles}.[/red]")
        return

    console.print(f"  Windows: {n_windows}")
    console.print()

    # Run rolling windows
    all_trades = []
    window_results = []
    start = 0
    window_idx = 0

    for window_start in range(0, total_candles - train_candles - test_candles + 1, step_candles):
        window_idx += 1
        train_start = window_start
        train_end = window_start + train_candles
        test_end = min(train_end + test_candles, total_candles)

        if test_end <= train_end:
            break

        train_df = df.iloc[train_start:train_end]
        test_df = df.iloc[train_end:test_end]
        window_df = df.iloc[train_start:test_end]

        # Run backtest for this window
        result = _run_backtest_window(
            train_df=train_df,
            test_df=test_df,
            all_df=window_df,
            symbol=symbol,
            config=config,
            pattern_length=pattern_length,
            forward_window=forward_window,
            min_dir_count=min_dir_count,
            position_size_pct=position_size,
        )

        # Collect results
        wr = result["win_rate"]
        pnl = result["total_pnl_pct"]
        n_trades = result["total_trades"]

        # Window marker
        if n_trades > 0:
            marker = "[green]✓[/green]" if pnl > 0 else "[red]✗[/red]"
        else:
            marker = "[yellow]·[/yellow]"

        console.print(
            f"  Window {window_idx:2d}/{n_windows} | "
            f"Candles {train_start:5d}-{test_end:5d} | "
            f"Trades: {n_trades:3d} | "
            f"WR: {wr:5.1%} | "
            f"P&L: {pnl:+7.2f}% | "
            f"{marker}"
        )

        window_results.append({
            "window": window_idx,
            "train_start": train_start,
            "train_end": train_end,
            "test_end": test_end,
            "trades": n_trades,
            "win_rate": wr,
            "pnl_pct": pnl,
            "long_trades": result["long_trades"],
            "short_trades": result["short_trades"],
            "long_wr": result["long_wr"],
            "short_wr": result["short_wr"],
            "avg_rr": result["avg_rr"],
            "patterns_trained": result["patterns_trained"],
        })

        all_trades.extend(result["trades"])

    # ===================================================================
    # Aggregate Results
    # ===================================================================
    console.print(f"\n[bold]═══ Rolling Walk-Forward Aggregate Results ═══[/bold]")

    total_trades = len(all_trades)
    if total_trades == 0:
        console.print("[yellow]No trades generated across any window.[/yellow]")
        return

    total_wins = sum(1 for t in all_trades if t["won"])
    total_pnl = sum(t["pnl_pct"] for t in all_trades)
    long_trades = [t for t in all_trades if t["direction"] == "LONG"]
    short_trades = [t for t in all_trades if t["direction"] == "SHORT"]
    long_wins = sum(1 for t in long_trades if t["won"])
    short_wins = sum(1 for t in short_trades if t["won"])
    avg_rr = np.mean([t["rr"] for t in all_trades])

    # Per-window consistency
    profitable_windows = sum(1 for w in window_results if w["pnl_pct"] > 0)
    total_windows_with_trades = sum(1 for w in window_results if w["trades"] > 0)

    # Compute cumulative P&L curve
    cumulative_pnl = 0.0
    max_drawdown = 0.0
    peak = 0.0
    for t in all_trades:
        cumulative_pnl += t["pnl_pct"]
        peak = max(peak, cumulative_pnl)
        dd = cumulative_pnl - peak
        max_drawdown = min(max_drawdown, dd)

    console.print(f"  Total trades: {total_trades}")
    console.print(f"  Overall Win Rate: {total_wins / total_trades:.1%}")
    console.print(f"  Total P&L: {total_pnl:+.2f}%")
    console.print(f"  Avg R:R: {avg_rr:.2f}")
    console.print(f"  Max Drawdown: {max_drawdown:.2f}%")
    console.print()
    console.print(f"  LONG:  {len(long_trades)} trades, {long_wins / len(long_trades):.1%} WR" if long_trades else "  LONG:  0 trades")
    console.print(f"  SHORT: {len(short_trades)} trades, {short_wins / len(short_trades):.1%} WR" if short_trades else "  SHORT: 0 trades")
    console.print()
    console.print(f"  Profitable windows: {profitable_windows}/{total_windows_with_trades} "
                  f"({profitable_windows / total_windows_with_trades:.0%})" if total_windows_with_trades > 0 else "")
    console.print(f"  Total windows: {len(window_results)} ({total_windows_with_trades} with trades)")

    # Regime consistency table
    if len(window_results) > 1:
        console.print(f"\n[bold]Per-Window Breakdown:[/bold]")
        table = Table()
        table.add_column("Window", justify="right", style="cyan")
        table.add_column("Trades", justify="right")
        table.add_column("WR", justify="right")
        table.add_column("P&L%", justify="right")
        table.add_column("LONG", justify="right")
        table.add_column("SHORT", justify="right")

        for w in window_results:
            pnl_color = "green" if w["pnl_pct"] > 0 else "red"
            table.add_row(
                str(w["window"]),
                str(w["trades"]),
                f"{w['win_rate']:.0%}",
                f"[{pnl_color}]{w['pnl_pct']:+.2f}%[/{pnl_color}]",
                f"{w['long_wr']:.0%}" if w["long_trades"] > 0 else "-",
                f"{w['short_wr']:.0%}" if w["short_trades"] > 0 else "-",
            )
        console.print(table)

    # Save results
    output_dir = os.path.dirname(csv_path) if csv_path else os.path.expanduser("~/.ppmt/backtest_results")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(
        output_dir,
        f"rolling_backtest_{symbol.replace('/', '_')}_{timestamp}.json"
    )

    json_result = {
        "version": VERSION,
        "symbol": symbol,
        "config": {
            "train_candles": train_candles,
            "test_candles": test_candles,
            "step_candles": step_candles,
            "pattern_length": pattern_length,
            "forward_window": forward_window,
            "position_size": position_size,
            "min_dir_count": min_dir_count,
        },
        "summary": {
            "total_trades": total_trades,
            "win_rate": total_wins / total_trades,
            "total_pnl_pct": total_pnl,
            "avg_rr": float(avg_rr),
            "max_drawdown_pct": float(max_drawdown),
            "long_trades": len(long_trades),
            "long_wr": long_wins / len(long_trades) if long_trades else 0,
            "short_trades": len(short_trades),
            "short_wr": short_wins / len(short_trades) if short_trades else 0,
            "profitable_windows": profitable_windows,
            "total_windows": len(window_results),
            "windows_with_trades": total_windows_with_trades,
        },
        "windows": window_results,
    }

    if save_trades:
        json_result["trades"] = [
            {k: v for k, v in t.items() if k != "won"} for t in all_trades
        ]

    json_result = json.loads(json.dumps(json_result, default=str))

    with open(output_file, "w") as f:
        json.dump(json_result, f, indent=2, default=str)
    console.print(f"\n[green]Results saved to {output_file}[/green]")


# ===================================================================
# V9.0: Web Dashboard
# ===================================================================
@cli.command()
@click.option("--port", default=5000, type=int, help="Port to run the dashboard on (default: 5000)")
@click.option("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
def dashboard(port: int, host: str, no_browser: bool):
    """
    Launch the PPMT web dashboard (V9.0).

    Opens an interactive web dashboard with equity curves, drawdown
    charts, per-window analysis, trade distribution, and symbol
    comparison. Uses Chart.js for visualization and reads backtest
    results from ~/.ppmt/backtest_results/.

    Examples:
      ppmt dashboard
      ppmt dashboard --port 8080
      ppmt dashboard --no-browser
    """
    try:
        from ppmt.dashboard.server import start_dashboard
    except ImportError:
        console.print("[red]Dashboard requires Flask. Install with: pip install flask[/red]")
        console.print("[yellow]  pip install flask[/yellow]")
        return

    console.print(f"\n[bold cyan]PPMT Dashboard {VERSION}[/bold cyan]")
    console.print(f"  Starting on http://{host}:{port}")
    console.print(f"  Press Ctrl+C to stop\n")

    start_dashboard(
        port=port,
        host=host,
        open_browser=not no_browser,
    )


if __name__ == "__main__":
    cli()

# ===================================================================
# V10.0: Monte Carlo Simulation
# ===================================================================
@cli.command("monte-carlo")
@click.option("--symbol", "-s", required=True, help="Symbol (e.g., BTC/USDT, ETH/USDT)")
@click.option("--simulations", "-n", default=1000, type=int, help="Number of Monte Carlo simulations")
@click.option("--capital", default=10000.0, type=float, help="Initial capital for simulations")
@click.option("--ruin-threshold", default=0.5, type=float, help="Ruin threshold (fraction of initial capital)")
@click.option("--position-size", default=0.02, type=float, help="Position size as fraction of equity")
@click.option("--csv", "csv_path", default=None, help="Path to OHLCV CSV file")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--seed", default=None, type=int, help="Random seed for reproducibility")
@click.option("--json-output", is_flag=True, help="Output results as JSON")
def monte_carlo(symbol, simulations, capital, ruin_threshold, position_size,
                csv_path, timeframe, seed, json_output):
    """
    Run Monte Carlo simulation on a symbol.

    Resamples from historical backtest trade results to simulate thousands of
    equity curves, calculating risk of ruin, confidence intervals, and
    distribution of outcomes.

    Examples:
      ppmt monte-carlo -s BTC/USDT --simulations 1000
      ppmt monte-carlo -s ETH/USDT -n 5000 --capital 10000 --ruin-threshold 0.5
    """
    from ppmt.engine.monte_carlo import MonteCarloEngine
    from rich.panel import Panel

    console.print(Panel(
        f"[cyan]Symbol:[/cyan] {symbol}\n"
        f"[cyan]Simulations:[/cyan] {simulations:,}\n"
        f"[cyan]Capital:[/cyan] ${capital:,.2f}\n"
        f"[cyan]Ruin Threshold:[/cyan] {ruin_threshold}\n"
        f"[cyan]Position Size:[/cyan] {position_size * 100:.1f}%",
        title="Monte Carlo Simulation",
        border_style="blue",
    ))

    # Load data and run backtest first
    config = load_config()
    try:
        df = _load_data(symbol, csv_path, timeframe)
    except Exception as e:
        console.print(f"[red]Error loading data: {e}[/red]")
        return

    console.print(f"[cyan]Running backtest to collect trade results...[/cyan]")

    # Run rolling backtest to get trade PnLs
    train_candles = min(4000, int(len(df) * 0.7))
    test_candles = min(1000, len(df) - train_candles)
    if test_candles <= 0:
        console.print("[red]Insufficient data for backtest.[/red]")
        return

    split_idx = train_candles
    train_df = df.iloc[:split_idx]
    test_df = df.iloc[split_idx:]
    
    bt_result = _run_backtest_window(
        train_df=train_df,
        test_df=test_df,
        all_df=df,
        symbol=symbol,
        config=config,
    )

    if not bt_result["trades"]:
        console.print("[red]Backtest produced no trades. Cannot run Monte Carlo.[/red]")
        return

    trade_pnl_pcts = np.array([t["pnl_pct"] for t in bt_result["trades"]])
    console.print(f"[green]Backtest: {bt_result['total_trades']} trades, "
                  f"WR={bt_result['win_rate']:.1%}, P&L={bt_result['total_pnl_pct']:+.2f}%[/green]")
    console.print(f"[cyan]Running {simulations:,} Monte Carlo simulations...[/cyan]")

    # Run Monte Carlo
    engine = MonteCarloEngine(seed=seed)
    mc_result = engine.simulate_from_trades(
        trade_pnls=np.array([t["pnl_pct"] for t in bt_result["trades"]]),
        trade_pnl_pcts=trade_pnl_pcts,
        symbol=symbol,
        initial_capital=capital,
        n_simulations=simulations,
        ruin_threshold=ruin_threshold,
        position_size_pct=position_size,
    )

    # Add backtest info
    mc_result.stats["backtest_total_trades"] = bt_result["total_trades"]
    mc_result.stats["backtest_win_rate"] = bt_result["win_rate"]
    mc_result.stats["backtest_pnl_pct"] = bt_result["total_pnl_pct"]

    if json_output:
        console.print_json(json.dumps(mc_result.to_dict(), indent=2, default=str))
    else:
        console.print(mc_result.summary_text())


# ===================================================================
# V10.0: Enhanced `run` with --paper option
# ===================================================================
# The existing `run` command has been enhanced with --paper flag
# We override it by adding the option to the existing command definition
# Note: Click doesn't allow re-defining commands, so we add a new `run-paper` alias
# ===================================================================
@cli.command("run-paper")
@click.option("--symbol", "-s", required=True, help="Trading pair")
@click.option("--timeframe", "-t", default="1h", help="Candle timeframe")
@click.option("--capital", default=10000.0, type=float, help="Initial capital")
@click.option("--position-size", default=0.02, type=float, help="Position size as fraction of equity")
def run_paper(symbol, timeframe, capital, position_size):
    """Run paper trading mode (no real money at risk)."""
    from ppmt.engine.streaming import LiveTradeEngine
    from rich.panel import Panel

    console.print(Panel(
        f"[cyan]Symbol:[/cyan] {symbol}\n"
        f"[cyan]Mode:[/cyan] [yellow]PAPER[/yellow]\n"
        f"[cyan]Capital:[/cyan] ${capital:,.2f}\n"
        f"[cyan]Position Size:[/cyan] {position_size * 100:.1f}%",
        title="PPMT Paper Trading",
        border_style="yellow",
    ))
    console.print("[yellow]Paper trading engine ready. Connect to exchange for live data.[/yellow]")
    console.print("[cyan]Use: ppmt run -s SYMBOL --paper  (when --paper flag is available)[/cyan]")
