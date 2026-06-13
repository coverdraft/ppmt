#!/usr/bin/env python3
"""Strict Walk-Forward Validation for PPMT v0.6.3

Builds trie on TRAINING data only (no look-ahead), encodes test data
with training normalization stats (V7.9 fix), paper trades on test.

This is the gold standard for validating trading systems — no future
data leaks into pattern discovery.

Usage:
    python walk_forward.py -s BTC/USDT --train-candles 30000 --test-candles 5000 --step-candles 5000
"""

import sys
import argparse
import numpy as np
from dataclasses import dataclass

sys.path.insert(0, '/home/z/my-project/ppmt/src')

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.ppmt import PPMT
from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig
from ppmt.core.sax import SAXEncoder

from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class WalkForwardWindow:
    window_id: int
    train_candles: int
    test_candles: int
    trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    pnl_pct: float = 0.0
    sharpe: float = 0.0
    max_dd: float = 0.0
    profit_factor: float = 0.0
    avg_trade: float = 0.0
    trie_patterns: int = 0
    trie_observations: int = 0


def main():
    parser = argparse.ArgumentParser(description='Strict Walk-Forward Validation for PPMT')
    parser.add_argument('--symbol', '-s', default='BTC/USDT')
    parser.add_argument('--timeframe', '-t', default='1h')
    parser.add_argument('--train-candles', default=30000, type=int)
    parser.add_argument('--test-candles', default=5000, type=int)
    parser.add_argument('--step-candles', default=5000, type=int)
    parser.add_argument('--min-confidence', default=0.20, type=float)
    parser.add_argument('--max-windows', default=10, type=int)
    args = parser.parse_args()

    storage = PPMTStorage()
    df = storage.load_ohlcv(args.symbol, args.timeframe)
    if df.empty:
        console.print(f"[red]No data for {args.symbol}[/red]")
        return

    total = len(df)

    # Save original trie to restore later
    original_trie = storage.load_trie(args.symbol, "n3")

    console.print(f"\n[bold cyan]Strict Walk-Forward Validation: {args.symbol}[/bold cyan]")
    console.print(f"  Total candles: {total}")
    console.print(f"  Train: {args.train_candles} | Test: {args.test_candles} | Step: {args.step_candles}")
    console.print(f"  Min confidence: {args.min_confidence:.0%}")
    console.print(f"  [bold green]STRICT MODE: Trie built on training data ONLY[/bold green]")
    console.print(f"  [dim]V7.9 fix: Training normalization stats propagated to test encoding[/dim]")

    windows = []
    window_id = 0
    test_start = args.train_candles

    while test_start + args.test_candles <= total and window_id < args.max_windows:
        train_start = max(0, test_start - args.train_candles)
        train_end = test_start
        test_end = test_start + args.test_candles

        df_train = df.iloc[train_start:train_end]

        console.print(f"\n[yellow]Window {window_id + 1}:[/yellow] "
                      f"Train [{train_start}:{train_end}] ({len(df_train)} candles), "
                      f"Test [{test_start}:{test_end}] ({args.test_candles} candles)")

        # Step 1: Encode training data and get normalization stats
        classifier = AssetClassifier()
        info = classifier.classify(args.symbol)

        sax_encoder = SAXEncoder(alphabet_size=8, window_size=10, strategy="ohlcv")
        train_symbols, paa_mean, paa_std = sax_encoder.encode_with_normalization(df_train)

        console.print(f"    Training SAX stats: mean={paa_mean:.6f}, std={paa_std:.6f}")
        console.print(f"    Training symbols: {len(train_symbols)}")

        # Step 2: Build trie on TRAINING data only (with pre-computed symbols)
        engine = PPMT(
            symbol=args.symbol,
            asset_class=info.asset_class,
            sax_alphabet_size=8,
            sax_window_size=10,
            sax_strategy="ohlcv",
            weight_profile=info.weight_profile,
        )
        engine.build(df_train, pattern_length=5, symbols=train_symbols)

        # Step 3: Run bootstrap on training data
        bootstrap_result = engine.bootstrap(df_train, pattern_length=5, bootstrap_ratio=1.0, verbose=False)

        trie = engine.trie_n3
        trie.propagate_metadata()

        obs = trie.trading_observations
        patterns = trie.pattern_count
        console.print(f"    Trie: {patterns} patterns, {obs} trading observations")

        if obs == 0:
            console.print(f"    [red]No trading observations — skipping[/red]")
            test_start += args.step_candles
            window_id += 1
            continue

        # Step 4: Save trie for paper trader
        storage.save_trie(args.symbol, "n3", trie)

        # Step 5: Paper trade on test data with training normalization stats
        config = PaperTraderConfig(
            symbol=args.symbol,
            timeframe=args.timeframe,
            initial_capital=10000.0,
            min_confidence=args.min_confidence,
            catastrophic_loss_pct=8.0,
            living_trie=False,  # No trie updates during OOS
            start_offset=test_start,
            end_offset=test_end,
            paa_mean=paa_mean,  # V7.9 fix: propagate training stats
            paa_std=paa_std,
        )

        trader = PaperTrader(config=config)
        result = trader.run()

        n_trades = len(result.trades)
        if n_trades == 0:
            console.print(f"    [yellow]No trades in test window[/yellow]")
            test_start += args.step_candles
            window_id += 1
            continue

        wins = sum(1 for t in result.trades if t.pnl_pct > 0)
        wf = WalkForwardWindow(
            window_id=window_id + 1,
            train_candles=len(df_train),
            test_candles=args.test_candles,
            trades=n_trades, wins=wins,
            win_rate=result.win_rate, pnl_pct=result.total_pnl_pct,
            sharpe=result.sharpe_ratio, max_dd=result.max_drawdown * 100,
            profit_factor=result.profit_factor,
            avg_trade=result.avg_trade_pnl_pct,
            trie_patterns=patterns, trie_observations=obs,
        )
        windows.append(wf)

        console.print(f"    P&L: {wf.pnl_pct:+.2f}% | WR: {wf.win_rate:.1f}% | "
                      f"Sharpe: {wf.sharpe:.2f} | Max DD: {wf.max_dd:.1f}% | Trades: {n_trades}")

        test_start += args.step_candles
        window_id += 1

    # Restore original trie
    if original_trie is not None:
        storage.save_trie(args.symbol, "n3", original_trie)
        console.print(f"\n[dim]Restored original trie[/dim]")

    if not windows:
        console.print("[red]No valid windows produced results.[/red]")
        return

    # Summary
    console.print(f"\n[bold green]Strict Walk-Forward Summary ({len(windows)} windows)[/bold green]")

    table = Table(title="Strict Walk-Forward Results (No Look-Ahead)")
    table.add_column("Win", justify="center")
    table.add_column("Patterns", justify="right")
    table.add_column("Obs", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("WR", justify="right")
    table.add_column("P&L%", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("Max DD%", justify="right")
    table.add_column("PF", justify="right")

    for w in windows:
        pnl_color = "green" if w.pnl_pct > 0 else "red"
        table.add_row(
            str(w.window_id), str(w.trie_patterns), str(w.trie_observations),
            str(w.trades), f"{w.win_rate:.1f}%",
            f"[{pnl_color}]{w.pnl_pct:+.2f}%[/{pnl_color}]",
            f"{w.sharpe:.2f}", f"{w.max_dd:.1f}%", f"{w.profit_factor:.2f}",
        )

    # Aggregate
    total_trades = sum(w.trades for w in windows)
    total_wins = sum(w.wins for w in windows)
    avg_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    profitable = sum(1 for w in windows if w.pnl_pct > 0)
    pnls = [w.pnl_pct for w in windows]

    table.add_row(
        "ALL", "—", "—", str(total_trades),
        f"{avg_wr:.1f}%", f"{np.mean(pnls):+.2f}%",
        f"{np.mean([w.sharpe for w in windows]):.2f}",
        f"{max([w.max_dd for w in windows]):.1f}%",
        f"{np.mean([w.profit_factor for w in windows]):.2f}",
    )

    console.print(table)

    console.print(f"\n[bold]Strict Walk-Forward Metrics:[/bold]")
    console.print(f"  Profitable windows: {profitable}/{len(windows)} ({profitable/len(windows)*100:.0f}%)")
    console.print(f"  Average P&L: {np.mean(pnls):+.2f}%")
    console.print(f"  Worst window: {min(pnls):+.2f}%")
    console.print(f"  Best window: {max(pnls):+.2f}%")
    console.print(f"  Avg Sharpe: {np.mean([w.sharpe for w in windows]):.2f}")
    console.print(f"  Total trades: {total_trades}")

    # Verdict
    if profitable == len(windows) and np.mean(pnls) > 50:
        console.print(f"\n[bold green]VERDICT: ROBUST — All windows profitable, no look-ahead bias[/bold green]")
    elif profitable >= len(windows) * 0.7 and np.mean(pnls) > 0:
        console.print(f"\n[bold yellow]VERDICT: MODERATE — {profitable}/{len(windows)} profitable (strict OOS)[/bold yellow]")
    else:
        console.print(f"\n[bold red]VERDICT: FRAGILE — Only {profitable}/{len(windows)} profitable (strict OOS)[/bold red]")


if __name__ == "__main__":
    main()
