#!/usr/bin/env python3
"""
v5_paper_dashboard.py — Live TUI dashboard for the v5 cb_v2 paper trader.

Reads `state/v5_cb_v2/paper_trader_state.json` (written every 30s by the
running paper trader) and renders a live, color-coded terminal dashboard.

This script is READ-ONLY — it never writes to the state file, so even if
it crashes the running trader is unaffected.

==============================================================================
  USAGE
==============================================================================

  # Make sure the trader is already running in another terminal:
  ./scripts/v5/run_paper.sh

  # Then launch the dashboard:
  python3 scripts/v5/v5_paper_dashboard.py

  # Custom refresh interval (default 2s):
  python3 scripts/v5/v5_paper_dashboard.py --refresh 5

  # Custom state file path:
  python3 scripts/v5/v5_paper_dashboard.py --state path/to/state.json

  # One-shot snapshot (no live refresh, just print and exit):
  python3 scripts/v5/v5_paper_dashboard.py --once

==============================================================================
  REQUIREMENTS
==============================================================================

  pip install rich

==============================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Resolve state path relative to repo root (matches paper trader's logic)
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
DEFAULT_STATE = Path(os.environ.get(
    "PPMT_STATE_PATH",
    str(_REPO_ROOT / "state" / "v5_cb_v2" / "paper_trader_state.json"),
))

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.text import Text
    from rich.align import Align
    from rich.columns import Columns
    from rich import box
except ImportError:
    print("ERROR: rich is not installed. Install with:")
    print("  pip install rich")
    sys.exit(1)

console = Console()


# ── STATE LOADING ───────────────────────────────────────────────────────────

def load_state(path: Path) -> dict[str, Any] | None:
    """Load the trader state JSON. Returns None if not found or invalid."""
    if not path.exists():
        return None
    try:
        # The trader writes via atomic rename, so reads are always consistent
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return {"_error": str(e)}


# ── FORMATTING HELPERS ─────────────────────────────────────────────────────

def fmt_money(x: float) -> str:
    sign = "+" if x >= 0 else ""
    return f"{sign}${x:,.2f}"


def fmt_pct(x: float) -> str:
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.2f}%"


def fmt_pnl_pct(x: float, color: bool = True) -> Text:
    """Color a PnL percentage: green if positive, red if negative."""
    s = fmt_pct(x)
    if not color:
        return Text(s)
    style = "bold green" if x >= 0 else "bold red"
    return Text(s, style=style)


def fmt_wr(wr: float) -> Text:
    """Color win rate: green ≥80, yellow 60-80, red <60."""
    s = f"{wr:.1f}%"
    if wr >= 80:
        style = "bold green"
    elif wr >= 60:
        style = "yellow"
    else:
        style = "bold red"
    return Text(s, style=style)


def fmt_account(account: float, starting: float) -> Text:
    delta = account - starting
    delta_pct = (delta / starting * 100) if starting > 0 else 0.0
    style = "bold green" if delta >= 0 else "bold red"
    s = f"${account:,.2f}  ({fmt_pct(delta_pct)})"
    return Text(s, style=style)


def fmt_age(started_at_iso: str) -> str:
    """Return human-readable uptime like '4h 12m' or '2d 4h 12m'."""
    try:
        # Parse ISO 8601 with timezone
        started = datetime.fromisoformat(started_at_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta_sec = int((now - started).total_seconds())
        if delta_sec < 0:
            return "just now"
        days, rem = divmod(delta_sec, 86400)
        hours, rem = divmod(rem, 3600)
        mins, _ = divmod(rem, 60)
        if days > 0:
            return f"{days}d {hours}h {mins}m"
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"
    except Exception:
        return "?"


def fmt_age_short(ts_sec: int) -> str:
    """Return short age like '12m ago' from a unix timestamp."""
    try:
        now = int(time.time())
        delta = now - int(ts_sec)
        if delta < 60:
            return f"{delta}s ago"
        if delta < 3600:
            return f"{delta // 60}m ago"
        if delta < 86400:
            return f"{delta // 3600}h ago"
        return f"{delta // 86400}d ago"
    except Exception:
        return "?"


# ── METRIC COMPUTATION ─────────────────────────────────────────────────────

def compute_metrics(closed_trades: list[dict]) -> dict:
    """Compute aggregate metrics from closed trades."""
    if not closed_trades:
        return {
            "n_closed": 0, "wins": 0, "losses": 0, "wr": 0.0,
            "total_pnl": 0.0, "avg_pnl_pct": 0.0, "pf": 0.0,
            "gross_win": 0.0, "gross_loss": 0.0,
        }
    wins = [t for t in closed_trades if t.get("net_pnl_usd", 0) > 0]
    losses = [t for t in closed_trades if t.get("net_pnl_usd", 0) <= 0]
    n = len(closed_trades)
    wr = len(wins) / n * 100
    total_pnl = sum(t.get("net_pnl_usd", 0) for t in closed_trades)
    avg_pnl_pct = sum(t.get("net_pnl_pct", 0) for t in closed_trades) / n
    gross_win = sum(t.get("net_pnl_pct", 0) for t in wins)
    gross_loss = -sum(t.get("net_pnl_pct", 0) for t in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    return {
        "n_closed": n, "wins": len(wins), "losses": len(losses),
        "wr": wr, "total_pnl": total_pnl, "avg_pnl_pct": avg_pnl_pct,
        "pf": pf, "gross_win": gross_win, "gross_loss": gross_loss,
    }


def compute_unrealized_pnl(open_pos: dict, current_price: float | None) -> float | None:
    """Compute unrealized PnL % on margin for an open position.

    current_price: latest price for the symbol (we'll fetch from Coinbase)
    Returns None if current_price is None.
    """
    if current_price is None:
        return None
    fill = open_pos["fill_price"]
    lev = open_pos["leverage"]
    return (current_price - fill) / fill * 100 * lev


# ── PRICE FETCHING (for unrealized PnL on open positions) ──────────────────

import urllib.request
import urllib.error

COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/{pair}/ticker"


def fetch_price(pair: str) -> float | None:
    """Fetch latest price from Coinbase public ticker. Returns None on error."""
    url = COINBASE_TICKER_URL.format(pair=pair)
    req = urllib.request.Request(url, headers={"User-Agent": "ppmt-dashboard/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
            return float(data.get("price", 0))
    except (urllib.error.URLError, ValueError, KeyError, OSError):
        return None


_price_cache: dict[str, tuple[float, float]] = {}  # pair -> (price, fetched_at_sec)
PRICE_CACHE_TTL = 30  # seconds


def get_cached_price(pair: str) -> float | None:
    now = time.time()
    cached = _price_cache.get(pair)
    if cached and (now - cached[1]) < PRICE_CACHE_TTL:
        return cached[0]
    price = fetch_price(pair)
    if price is not None:
        _price_cache[pair] = (price, now)
    return price


# ── RENDER COMPONENTS ──────────────────────────────────────────────────────

# Token pair lookup (binance_symbol → coinbase pair)
TOKEN_PAIRS = {
    "BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD", "SOLUSDT": "SOL-USD",
    "XRPUSDT": "XRP-USD", "ADAUSDT": "ADA-USD", "AVAXUSDT": "AVAX-USD",
    "LINKUSDT": "LINK-USD", "DOGEUSDT": "DOGE-USD", "SHIBUSDT": "SHIB-USD",
    "PEPEUSDT": "PEPE-USD", "WIFUSDT": "WIF-USD", "BONKUSDT": "BONK-USD",
}

STARTING_ACCOUNT = 10000.0  # default; will be overridden if config says otherwise


def render_header(state: dict, metrics: dict) -> Panel:
    """Top stats panel."""
    account = state.get("account_usd", STARTING_ACCOUNT)
    stats = state.get("stats", {})
    config = state.get("config", {})
    started_at = state.get("started_at", "")

    # Try to infer starting account from config
    starting = STARTING_ACCOUNT

    n_open = stats.get("n_open", 0)
    max_concurrent = config.get("max_concurrent", 3)
    n_signals = stats.get("n_signals_seen", 0)
    n_approved = stats.get("n_signals_approved", 0)
    n_closed = metrics["n_closed"]
    wr = metrics["wr"]
    pf = metrics["pf"]
    avg_pnl = metrics["avg_pnl_pct"]

    left_col = Table.grid(padding=(0, 2))
    left_col.add_column(style="cyan")
    left_col.add_column()
    left_col.add_row("Account:", fmt_account(account, starting))
    left_col.add_row("Signals:", Text(f"{n_signals}", style="white"))
    left_col.add_row("Approved:", Text(f"{n_approved}", style="green"))
    left_col.add_row("Closed:", Text(f"{n_closed}", style="white"))

    right_col = Table.grid(padding=(0, 2))
    right_col.add_column(style="cyan")
    right_col.add_column()
    right_col.add_row("Open:", Text(f"{n_open}/{max_concurrent}", style="magenta"))
    right_col.add_row("Win Rate:", fmt_wr(wr))
    pf_style = "green" if pf >= 3 else ("yellow" if pf >= 1 else "red")
    right_col.add_row("Profit Factor:", Text(f"{pf:.2f}" if pf != float("inf") else "inf", style=pf_style))
    pnl_style = "green" if avg_pnl >= 0 else "red"
    right_col.add_row("Avg PnL/trade:", Text(fmt_pct(avg_pnl), style=pnl_style))
    right_col.add_row("Uptime:", Text(fmt_age(started_at), style="white"))

    cols = Columns([left_col, right_col], expand=True, padding=(0, 4))
    title = f"PPMT Paper Trader — LIVE  [dim]({datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC)[/dim]"
    return Panel(cols, title=title, border_style="blue", box=box.DOUBLE)


def render_open_positions(state: dict) -> Panel:
    """Open positions table."""
    open_positions = state.get("open_positions", [])
    if not open_positions:
        return Panel(Text("No open positions", style="dim italic"), title="Open Positions", border_style="cyan")

    table = Table(box=box.SIMPLE, expand=True, show_lines=False)
    table.add_column("Symbol", style="cyan", no_wrap=True, width=10)
    table.add_column("Entry", justify="right", width=12)
    table.add_column("Now", justify="right", width=12)
    table.add_column("TP", justify="right", style="green", width=12)
    table.add_column("SL", justify="right", style="red", width=12)
    table.add_column("PnL%", justify="right", width=10)
    table.add_column("Age", justify="right", style="dim", width=10)
    table.add_column("Conf", justify="right", style="dim", width=8)

    for pos in open_positions:
        symbol = pos["symbol"]
        pair = TOKEN_PAIRS.get(symbol, symbol)
        entry = pos["fill_price"]
        now_price = get_cached_price(pair)
        tp = pos["tp_price"]
        sl = pos["sl_price"]
        lev = pos["leverage"]
        conf = pos.get("confidence", 0)
        entry_ts = pos.get("entry_ts", 0)

        # Smart price formatting: more decimals for low-priced assets
        def fmt_price(p: float) -> str:
            if p is None:
                return "—"
            if p >= 1000:
                return f"{p:,.2f}"
            if p >= 1:
                return f"{p:.4f}"
            return f"{p:.6f}"

        if now_price is not None:
            unreal_pct = (now_price - entry) / entry * 100 * lev
            now_str = fmt_price(now_price)
            pnl_str = fmt_pnl_pct(unreal_pct)
        else:
            now_str = "—"
            pnl_str = Text("—", style="dim")

        age = fmt_age_short(entry_ts) if entry_ts else "?"

        table.add_row(
            symbol,
            fmt_price(entry),
            now_str,
            fmt_price(tp),
            fmt_price(sl),
            pnl_str,
            age,
            f"{conf:.3f}",
        )

    return Panel(table, title=f"Open Positions ({len(open_positions)})", border_style="cyan")


def render_equity_curve(closed_trades: list[dict]) -> Panel:
    """ASCII equity curve from closed trades."""
    if not closed_trades:
        return Panel(Text("No closed trades yet", style="dim italic"), title="Equity Curve", border_style="green")

    # Build cumulative account value over time
    # Start from 10000 (default starting), apply each trade's net_pnl_usd
    starting = STARTING_ACCOUNT
    values = [starting]
    for t in closed_trades[-100:]:  # last 100 trades max
        values.append(values[-1] + t.get("net_pnl_usd", 0))

    # Render as ASCII sparkline
    if len(values) < 2:
        return Panel(Text("Waiting for more trades...", style="dim italic"), title="Equity Curve", border_style="green")

    v_min, v_max = min(values), max(values)
    if v_max == v_min:
        v_max = v_min + 1

    width = 60  # chars wide
    height = 5  # rows tall

    # Sample values down to `width` points
    n = len(values)
    if n > width:
        step = n / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values

    # Build grid
    grid = [[" " for _ in range(len(sampled))] for _ in range(height)]
    for i, v in enumerate(sampled):
        # Invert: row 0 is top (highest), row height-1 is bottom (lowest)
        norm = (v - v_min) / (v_max - v_min)
        row = int((1 - norm) * (height - 1))
        # Draw filled below
        for r in range(row, height):
            grid[r][i] = "█"

    lines = []
    for r in range(height):
        line = "".join(grid[r])
        # Y-axis label at left
        if r == 0:
            label = f"${v_max:,.0f}"
        elif r == height - 1:
            label = f"${v_min:,.0f}"
        else:
            label = " " * len(f"${v_max:,.0f}")
        lines.append(f"{label} ┤{line}")

    text = "\n".join(lines)
    final_value = values[-1]
    delta = final_value - starting
    delta_pct = (delta / starting * 100) if starting > 0 else 0
    color = "green" if delta >= 0 else "red"

    return Panel(
        Text(text + f"\n\nFinal: ${final_value:,.2f}  ({fmt_pct(delta_pct)})", style=color),
        title=f"Equity Curve (last {min(len(closed_trades), 100)} trades)",
        border_style="green",
    )


def render_recent_decisions(state: dict) -> Panel:
    """Last 5 decision events."""
    decisions = state.get("decisions_log", [])
    if not decisions:
        return Panel(Text("No decisions yet", style="dim italic"), title="Last Decisions", border_style="magenta")

    last_5 = decisions[-5:][::-1]  # most recent first

    table = Table(box=box.SIMPLE, expand=True, show_header=True)
    table.add_column("Time", style="dim", no_wrap=True)
    table.add_column("Action", no_wrap=True)
    table.add_column("Symbol", style="cyan", no_wrap=True)
    table.add_column("Proba", justify="right")
    table.add_column("Reason", style="dim")

    for d in last_5:
        ts = d.get("ts_utc", "")
        # Try to parse and format as HH:MM:SS
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            time_str = dt.strftime("%H:%M:%S")
        except Exception:
            time_str = ts[:8] if len(ts) >= 8 else ts

        action = d.get("action", "?")
        symbol = d.get("symbol", "?")
        proba = d.get("proba", 0)
        reason = d.get("reason", "")[:40]

        if action == "OPEN":
            action_text = Text("OPEN", style="bold green")
        elif action.startswith("SKIP"):
            action_text = Text(action.replace("SKIP_", "SKIP "), style="yellow")
        else:
            action_text = Text(action, style="white")

        table.add_row(time_str, action_text, symbol, f"{proba:.3f}", reason)

    return Panel(table, title="Last Decisions", border_style="magenta")


def render_per_symbol(closed_trades: list[dict]) -> Panel:
    """Per-symbol breakdown of closed trades."""
    if not closed_trades:
        return Panel(Text("No closed trades yet", style="dim italic"), title="Per-Symbol", border_style="yellow")

    by_sym: dict[str, list[dict]] = defaultdict(list)
    for t in closed_trades:
        by_sym[t.get("symbol", "?")].append(t)

    table = Table(box=box.SIMPLE, expand=True)
    table.add_column("Symbol", style="cyan", no_wrap=True)
    table.add_column("Trades", justify="right")
    table.add_column("WR", justify="right")
    table.add_column("PnL $", justify="right")
    table.add_column("Avg %", justify="right")

    rows = []
    for sym, trades in by_sym.items():
        n = len(trades)
        wins = sum(1 for t in trades if t.get("net_pnl_usd", 0) > 0)
        wr = wins / n * 100 if n else 0
        total = sum(t.get("net_pnl_usd", 0) for t in trades)
        avg = sum(t.get("net_pnl_pct", 0) for t in trades) / n if n else 0
        rows.append((sym, n, wr, total, avg))

    # Sort by total PnL desc
    rows.sort(key=lambda r: r[3], reverse=True)
    for sym, n, wr, total, avg in rows:
        wr_text = fmt_wr(wr)
        pnl_text = Text(fmt_money(total), style="green" if total >= 0 else "red")
        avg_text = Text(fmt_pct(avg), style="green" if avg >= 0 else "red")
        table.add_row(sym, str(n), wr_text, pnl_text, avg_text)

    return Panel(table, title=f"Per-Symbol ({len(by_sym)} symbols)", border_style="yellow")


def render_status_bar(state: dict) -> Text:
    """Bottom status bar with last save time + state file path."""
    last_saved = state.get("last_saved_at", "?")
    try:
        dt = datetime.fromisoformat(last_saved.replace("Z", "+00:00"))
        age = fmt_age_short(dt.timestamp())
    except Exception:
        age = "?"
    n_closed = len(state.get("closed_trades", []))
    n_decisions = len(state.get("decisions_log", []))
    return Text(
        f"State saved: {age}  │  Closed trades: {n_closed}  │  Decisions logged: {n_decisions}  │  Ctrl-C to exit",
        style="dim",
    )


# ── MAIN RENDER ────────────────────────────────────────────────────────────

def render_dashboard(state: dict, state_path: Path) -> Layout:
    """Render the full dashboard layout."""
    layout = Layout()

    # Check for error
    if state.get("_error"):
        layout.split_column(
            Panel(Text(f"ERROR reading state file:\n{state['_error']}", style="bold red"),
                  title="Dashboard Error", border_style="red"),
            render_status_bar(state),
        )
        return layout

    metrics = compute_metrics(state.get("closed_trades", []))

    # Build the body as a single column of panels
    body = Table.grid(expand=True, padding=(0, 0))
    body.add_column()
    body.add_row(render_header(state, metrics))
    body.add_row(render_open_positions(state))
    body.add_row(render_equity_curve(state.get("closed_trades", [])))
    cols = Table.grid(expand=True, padding=(0, 1))
    cols.add_column()
    cols.add_column()
    cols.add_row(
        render_recent_decisions(state),
        render_per_symbol(state.get("closed_trades", [])),
    )
    body.add_row(cols)
    body.add_row(render_status_bar(state))

    layout.update(body)
    return layout


# ── MAIN LOOP ──────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE,
                        help=f"Path to paper_trader_state.json (default: {DEFAULT_STATE})")
    parser.add_argument("--refresh", type=float, default=2.0,
                        help="Refresh interval in seconds (default: 2)")
    parser.add_argument("--once", action="store_true",
                        help="Print one snapshot and exit (no live refresh)")
    args = parser.parse_args()

    if args.once:
        state = load_state(args.state)
        if state is None:
            console.print(f"[bold red]State file not found:[/bold red] {args.state}")
            console.print(f"\nIs the paper trader running? Start it with:")
            console.print(f"  [cyan]./scripts/v5/run_paper.sh[/cyan]")
            return 1
        console.print(render_dashboard(state, args.state))
        return 0

    # Live mode
    console.print(f"[dim]Reading state from: {args.state}[/dim]")
    console.print(f"[dim]Refresh: every {args.refresh}s  │  Ctrl-C to exit[/dim]")
    console.print()

    try:
        with Live(render_dashboard({"_error": "loading..."}, args.state),
                  console=console, refresh_per_second=1 / args.refresh, screen=True) as live:
            while True:
                state = load_state(args.state)
                if state is None:
                    state = {"_error": f"File not found: {args.state}\nIs the trader running?"}
                live.update(render_dashboard(state, args.state))
                time.sleep(args.refresh)
    except KeyboardInterrupt:
        console.print("\n[bold]Dashboard stopped.[/bold] Trader keeps running in background.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
