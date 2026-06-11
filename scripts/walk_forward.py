#!/usr/bin/env python3
"""Walk-Forward Validation for PPMT v0.6.2

Rolling window validation: uses the full trie (built on all data) but
paper-trades on different time windows to test robustness across regimes.

Note: The trie sees all data (partial look-ahead in pattern discovery),
but trading decisions are made fresh in each window with living_trie=False.
This tests whether the system can trade profitably across different
market conditions.

Usage:
    python walk_forward.py --symbol BTC/USDT --test-candles 5000 --step-candles 5000
"""

import sys
import argparse
import numpy as np
from dataclasses import dataclass

sys.path.insert(0, '/home/z/my-project/ppmt/src')

from ppmt.data.storage import PPMTStorage
from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig

from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class WalkForwardWindow:
    window_id: int
    test_start: int
    test_end: int
    test_candles: int
    trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    pnl_pct: float = 0.0
    sharpe: float = 0.0
    max_dd: float = 0.0
    profit_factor: float = 0.0
    avg_trade: float = 0.0


def main():
    parser = argparse.ArgumentParser(description='Walk-Forward Validation for PPMT')
    parser.add_argument('--symbol', '-s', default='BTC/USDT')
    parser.add_argument('--timeframe', '-t', default='1h')
    parser.add_argument('--test-candles', default=5000, type=int, help='Test window size')
    parser.add_argument('--step-candles', default=5000, type=int, help='Step size')
    parser.add_argument('--min-confidence', default=0.20, type=float)
    parser.add_argument('--max-windows', default=10, type=int)
    args = parser.parse_args()

    storage = PPMTStorage()
    df = storage.load_ohlcv(args.symbol, args.timeframe)
    if df.empty:
        console.print(f"[red]No data for {args.symbol}[/red]")
        return

    total = len(df)

    # Check trie exists
    trie = storage.load_trie(args.symbol, "n3")
    if trie is None:
        console.print(f"[red]No trie for {args.symbol}. Run 'ppmt build' first.[/red]")
        return

    console.print(f"\n[bold cyan]Walk-Forward Validation: {args.symbol}[/bold cyan]")
    console.print(f"  Total candles: {total}")
    console.print(f"  Trie: {trie.pattern_count} patterns, {trie.trading_observations} observations")
    console.print(f"  Test window: {args.test_candles} candles | Step: {args.step_candles} candles")
    console.print(f"  Min confidence: {args.min_confidence:.0%}")
    console.print(f"  [dim]Note: Trie built on all data (partial look-ahead), but trading decisions are fresh per window[/dim]")

    windows = []
    window_id = 0
    # Start from 30% of data (first 70% is "training")
    start = int(total * 0.3)

    while start + args.test_candles <= total and window_id < args.max_windows:
        test_start = start
        test_end = start + args.test_candles

        console.print(f"\n[yellow]Window {window_id + 1}:[/yellow] "
                      f"Test [{test_start}:{test_end}] ({args.test_candles} candles)")

        config = PaperTraderConfig(
            symbol=args.symbol,
            timeframe=args.timeframe,
            initial_capital=10000.0,
            min_confidence=args.min_confidence,
            catastrophic_loss_pct=8.0,
            living_trie=False,  # No trie updates during OOS
            start_offset=test_start,
            end_offset=test_end,
        )

        trader = PaperTrader(config=config)
        result = trader.run()

        n_trades = len(result.trades)
        if n_trades == 0:
            console.print(f"    [yellow]No trades in window[/yellow]")
            start += args.step_candles
            window_id += 1
            continue

        wins = sum(1 for t in result.trades if t.pnl_pct > 0)
        wf = WalkForwardWindow(
            window_id=window_id + 1,
            test_start=test_start, test_end=test_end,
            test_candles=args.test_candles,
            trades=n_trades, wins=wins,
            win_rate=result.win_rate, pnl_pct=result.total_pnl_pct,
            sharpe=result.sharpe_ratio, max_dd=result.max_drawdown * 100,
            profit_factor=result.profit_factor,
            avg_trade=result.avg_trade_pnl_pct,
        )
        windows.append(wf)

        console.print(f"    P&L: {wf.pnl_pct:+.2f}% | WR: {wf.win_rate:.1f}% | "
                      f"Sharpe: {wf.sharpe:.2f} | Max DD: {wf.max_dd:.1f}% | Trades: {n_trades}")

        start += args.step_candles
        window_id += 1

    if not windows:
        console.print("[red]No valid windows produced results.[/red]")
        return

    # Summary
    console.print(f"\n[bold green]Walk-Forward Summary ({len(windows)} windows)[/bold green]")

    table = Table(title="Walk-Forward Results")
    table.add_column("Win", justify="center")
    table.add_column("Candles", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("WR", justify="right")
    table.add_column("P&L%", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("Max DD%", justify="right")
    table.add_column("PF", justify="right")

    for w in windows:
        pnl_color = "green" if w.pnl_pct > 0 else "red"
        table.add_row(
            str(w.window_id), str(w.test_candles), str(w.trades),
            f"{w.win_rate:.1f}%", f"[{pnl_color}]{w.pnl_pct:+.2f}%[/{pnl_color}]",
            f"{w.sharpe:.2f}", f"{w.max_dd:.1f}%",
            f"{w.profit_factor:.2f}",
        )

    # Aggregate
    total_trades = sum(w.trades for w in windows)
    total_wins = sum(w.wins for w in windows)
    avg_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    profitable = sum(1 for w in windows if w.pnl_pct > 0)
    pnls = [w.pnl_pct for w in windows]

    table.add_row(
        "ALL", str(sum(w.test_candles for w in windows)), str(total_trades),
        f"{avg_wr:.1f}%", f"{np.mean(pnls):+.2f}%",
        f"{np.mean([w.sharpe for w in windows]):.2f}",
        f"{np.max([w.max_dd for w in windows]):.1f}%",
        f"{np.mean([w.profit_factor for w in windows]):.2f}",
    )

    console.print(table)

    console.print(f"\n[bold]Walk-Forward Key Metrics:[/bold]")
    console.print(f"  Profitable windows: {profitable}/{len(windows)} ({profitable/len(windows)*100:.0f}%)")
    console.print(f"  Average P&L per window: {np.mean(pnls):+.2f}%")
    console.print(f"  Worst window P&L: {min(pnls):+.2f}%")
    console.print(f"  Best window P&L: {max(pnls):+.2f}%")
    console.print(f"  Average Sharpe: {np.mean([w.sharpe for w in windows]):.2f}")
    console.print(f"  Worst Max DD: {max([w.max_dd for w in windows]):.1f}%")
    console.print(f"  Total trades: {total_trades}")
    console.print(f"  Overall Win Rate: {avg_wr:.1f}%")

    # Verdict
    if profitable == len(windows) and np.mean(pnls) > 50:
        console.print(f"\n[bold green]VERDICT: ROBUST — All windows profitable, avg P&L {np.mean(pnls):+.1f}%[/bold green]")
    elif profitable >= len(windows) * 0.7 and np.mean(pnls) > 0:
        console.print(f"\n[bold yellow]VERDICT: MODERATE — {profitable}/{len(windows)} profitable, avg P&L {np.mean(pnls):+.1f}%[/bold yellow]")
    else:
        console.print(f"\n[bold red]VERDICT: FRAGILE — Only {profitable}/{len(windows)} profitable, avg P&L {np.mean(pnls):+.1f}%[/bold red]")


if __name__ == "__main__":
    main()
