"""
monitor.py — Real-time monitoring dashboard for V12 paper trading.

Usage:
  # Show current status
  python -m scripts.v12.paper_trader.monitor --symbol SOL

  # Show performance report
  python -m scripts.v12.paper_trader.monitor --symbol SOL --report

  # Check for drift
  python -m scripts.v12.paper_trader.monitor --symbol SOL --drift-check

  # Continuous monitoring (refresh every 30s)
  python -m scripts.v12.paper_trader.monitor --symbol SOL --watch

  # Show model history
  python -m scripts.v12.paper_trader.monitor --symbol SOL --model-history

  # Backfill prediction outcomes
  python -m scripts.v12.paper_trader.monitor --symbol SOL --backfill
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
import datetime as dt
from pathlib import Path

from .database import TradeDB, DB_DIR
from .metrics import compute_trade_metrics, compute_equity_metrics, generate_report, format_report
from .drift import run_drift_check, should_retrain
from .model import get_symbol_config, V12_SYMBOL_CONFIG

LOG = logging.getLogger("v12_monitor")


def _ts_to_iso(ts_ms: int) -> str:
    return dt.datetime.utcfromtimestamp(ts_ms / 1000).isoformat()


def _bar(pct: float, width: int = 20, marker: str = "#") -> str:
    """Create a simple text bar for visualization."""
    filled = int(max(0, min(width, pct / 100 * width)))
    return f"[{marker * filled}{'-' * (width - filled)}] {pct:+.2f}%"


def show_status(symbol: str) -> None:
    """Show current trading status."""
    sym = symbol.replace("/USDT", "")
    db = TradeDB(sym)

    # Trade stats
    stats = db.get_trade_stats()
    eq = compute_equity_metrics(db)

    # Active model version
    model_version = db.get_active_model_version()

    # Open trade
    open_trade = db.get_open_trade()

    # Recent drift events
    drift_events = db.get_drift_events(limit=3)

    # Config
    cfg = get_symbol_config(sym)

    print(f"\n{'='*60}")
    print(f"  V12 STATUS — {sym}")
    print(f"{'='*60}")
    print(f"  Config: Q{cfg['q_long']}/{cfg['q_short']} dir={cfg.get('direction','both')} "
          f"trend={cfg.get('trend_filter','none')} Win={cfg.get('window_size',200)}")
    print(f"  Model version:  {model_version or '(none)'}")
    print(f"")
    print(f"  Equity:         {_bar(eq['current_equity'])}")
    print(f"  Peak equity:    {eq['peak_equity']:+.3f}%")
    print(f"  Drawdown:       {eq['current_drawdown']:.3f}% (max: {eq['max_drawdown']:.3f}%)")
    print(f"")
    print(f"  Trades:         {stats['n_trades']}")
    print(f"  Win Rate:       {stats['win_rate']:.1%}")
    print(f"  Long: {stats['n_long']} (WR {stats['wr_long']:.1%})  "
          f"Short: {stats['n_short']} (WR {stats['wr_short']:.1%})")
    print(f"  Profit Factor:  {stats['profit_factor']:.2f}")
    print(f"  Sharpe (ann.):  {stats['sharpe']:.2f}")
    print(f"  Total PnL:      {stats['total_pnl']:+.3f}%")

    if open_trade:
        print(f"\n  Open position:  {open_trade['side']} @ {open_trade['entry_price']:.4f}")
        print(f"  Entry time:     {_ts_to_iso(open_trade['entry_ts'])}")
    else:
        print(f"\n  Open position:  none")

    if drift_events:
        print(f"\n  Recent drift events:")
        for e in drift_events[:3]:
            print(f"    {e['severity'].upper()}: {e['drift_type']}/{e['metric_name']} "
                  f"current={e['current_value']:.4f} baseline={e['baseline_value']:.4f}")

    # Walk-forward baseline
    if "wr_wf" in cfg:
        print(f"\n  Walk-forward baseline: WR={cfg['wr_wf']:.3f} "
              f"Sharpe={cfg.get('sharpe_wf',0):.3f} PF={cfg.get('pf_wf',0):.2f}")

    print(f"{'='*60}\n")
    db.close()


def show_report(symbol: str) -> None:
    """Show comprehensive performance report."""
    sym = symbol.replace("/USDT", "")
    db = TradeDB(sym)
    report = generate_report(db)
    print(format_report(report))
    db.close()


def show_drift(symbol: str) -> None:
    """Run drift check and display results."""
    sym = symbol.replace("/USDT", "")
    db = TradeDB(sym)
    events = run_drift_check(db)

    print(f"\n{'='*60}")
    print(f"  V12 DRIFT CHECK — {sym}")
    print(f"{'='*60}")

    if not events:
        print(f"  No drift detected. Model is performing within baseline.")
    else:
        critical = [e for e in events if e["severity"] == "critical"]
        warnings = [e for e in events if e["severity"] == "warning"]

        if critical:
            print(f"\n  CRITICAL ({len(critical)}):")
            for e in critical:
                print(f"    {e['drift_type']}/{e['metric_name']}: "
                      f"current={e['current_value']:.4f} baseline={e['baseline_value']:.4f} "
                      f"delta={e['delta']:+.4f} → {e['recommendation']}")

        if warnings:
            print(f"\n  WARNINGS ({len(warnings)}):")
            for e in warnings:
                print(f"    {e['drift_type']}/{e['metric_name']}: "
                      f"current={e['current_value']:.4f} baseline={e['baseline_value']:.4f} "
                      f"delta={e['delta']:+.4f} → {e['recommendation']}")

    retrain_needed, reason = should_retrain(db)
    print(f"\n  Retrain recommended: {'YES' if retrain_needed else 'NO'}")
    if retrain_needed:
        print(f"  Reason: {reason}")

    print(f"{'='*60}\n")
    db.close()


def show_model_history(symbol: str) -> None:
    """Show model version history."""
    sym = symbol.replace("/USDT", "")
    db = TradeDB(sym)
    history = db.get_model_history(limit=10)

    print(f"\n{'='*60}")
    print(f"  MODEL HISTORY — {sym}")
    print(f"{'='*60}")

    if not history:
        print("  No model versions registered yet.")
    else:
        for m in history:
            active = " (ACTIVE)" if m["is_active"] else ""
            print(f"\n  Version: {m['version']}{active}")
            print(f"    Deployed: {_ts_to_iso(m['deployed_at'])}")
            print(f"    Decision: {m['acceptance_decision']}  delta_auc={m.get('delta_auc', 0):+.3f}")
            print(f"    AUC val: {m.get('auc_val', 'N/A')}  dir_acc: {m.get('dir_acc_val', 'N/A')}")
            if m.get("wf_win_rate"):
                print(f"    WF: WR={m['wf_win_rate']:.3f} Sharpe={m.get('wf_sharpe', 0):.3f} "
                      f"PnL={m.get('wf_pnl_pct', 0):+.3f}%")
            if m.get("retired_at"):
                print(f"    Retired: {_ts_to_iso(m['retired_at'])}")

    print(f"{'='*60}\n")
    db.close()


def backfill_outcomes(symbol: str) -> None:
    """Backfill prediction outcomes."""
    sym = symbol.replace("/USDT", "")
    db = TradeDB(sym)
    filled = db.backfill_outcomes(horizon_bars=12)
    print(f"Backfilled {filled} prediction outcomes for {sym}")
    db.close()


def watch(symbol: str, interval: int = 30) -> None:
    """Continuous monitoring mode."""
    sym = symbol.replace("/USDT", "")
    print(f"Watching {sym} — press Ctrl+C to stop\n")

    try:
        while True:
            # Clear screen (basic)
            print(f"\033[2J\033[H", end="")  # ANSI clear screen
            show_status(sym)

            # Run lightweight drift check
            db = TradeDB(sym)
            events = run_drift_check(db)
            if events:
                critical = [e for e in events if e["severity"] == "critical"]
                if critical:
                    print("  ⚠️  CRITICAL DRIFT DETECTED — consider retraining")
            db.close()

            print(f"  Refreshing in {interval}s... (Ctrl+C to stop)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped watching.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="V12 paper trader monitor")
    p.add_argument("--symbol", default="SOL", help="Symbol to monitor")
    p.add_argument("--status", action="store_true", help="Show current status")
    p.add_argument("--report", action="store_true", help="Show performance report")
    p.add_argument("--drift-check", action="store_true", help="Run drift detection")
    p.add_argument("--model-history", action="store_true", help="Show model versions")
    p.add_argument("--backfill", action="store_true", help="Backfill prediction outcomes")
    p.add_argument("--watch", action="store_true", help="Continuous monitoring")
    p.add_argument("--interval", type=int, default=30, help="Watch interval in seconds")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING,  # Quiet for monitor output
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Default: show status
    if not any([args.status, args.report, args.drift_check,
                args.model_history, args.backfill, args.watch]):
        args.status = True

    if args.watch:
        watch(args.symbol, args.interval)
    elif args.status:
        show_status(args.symbol)
    elif args.report:
        show_report(args.symbol)
    elif args.drift_check:
        show_drift(args.symbol)
    elif args.model_history:
        show_model_history(args.symbol)
    elif args.backfill:
        backfill_outcomes(args.symbol)

    return 0


if __name__ == "__main__":
    sys.exit(main())
