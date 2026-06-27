"""
runner.py — CLI entrypoint for the V12 paper trader.

Usage:
    # Check if models exist
    python -m scripts.v12.paper_trader --status --symbol SOL

    # Run continuous paper trading
    python -m scripts.v12.paper_trader --symbol SOL

    # Run single cycle
    python -m scripts.v12.paper_trader --symbol SOL --once

    # Run with conservative profile
    python -m scripts.v12.paper_trader --symbol SOL --profile conservative

    # Run all V12 symbols
    python -m scripts.v12.paper_trader --all

    # Performance report
    python -m scripts.v12.paper_trader --symbol SOL --report

    # Drift check
    python -m scripts.v12.paper_trader --symbol SOL --drift

    # Rolling retrain
    python -m scripts.v12.paper_trader --symbol SOL --retrain

    # Monitor (continuous)
    python -m scripts.v12.paper_trader --symbol SOL --monitor

    # Model history
    python -m scripts.v12.paper_trader --symbol SOL --model-history

    # Backfill prediction outcomes
    python -m scripts.v12.paper_trader --symbol SOL --backfill
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys

from .engine import Engine
from .model import V12_SYMBOL_CONFIG, DEFAULT_PROFILE
from .monitor import (
    show_status, show_report, show_drift, show_model_history,
    backfill_outcomes, watch,
)
from .rolling_retrain import main as retrain_main

# V12 validated symbols
V12_SYMBOLS = list(V12_SYMBOL_CONFIG.keys())  # SOL, DOGE, AVAX


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _run_engine(symbol: str, profile: str, warmup_bars: int,
                exchange: str, once: bool) -> int:
    eng = Engine(symbol=symbol, profile=profile,
                 warmup_5m_bars=warmup_bars, exchange=exchange)
    eng.ensure_model()

    if once:
        result = eng.run_once()
        return 0 if result else 1

    eng.run_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PPMT V12 paper trader — quantitative trading cycle")
    p.add_argument("--symbol", default=None,
                   help="target symbol (e.g. SOL, DOGE, AVAX)")
    p.add_argument("--profile", default=DEFAULT_PROFILE,
                   choices=["balanced", "conservative"],
                   help="trading profile")
    p.add_argument("--all", action="store_true",
                   help="run all V12 validated symbols (SOL, DOGE, AVAX)")
    p.add_argument("--exchange", default="bybit",
                   choices=["bybit", "okx", "kraken", "coinbase"])
    p.add_argument("--warmup-bars", type=int, default=400,
                   help="number of 5m bars for feature warm-up")

    # Run modes
    p.add_argument("--once", action="store_true",
                   help="run exactly one cycle then exit")
    p.add_argument("--status", action="store_true",
                   help="show engine status and exit")
    p.add_argument("--verbose", action="store_true")

    # New monitoring & analysis commands
    p.add_argument("--report", action="store_true",
                   help="show performance report")
    p.add_argument("--drift", action="store_true",
                   help="run drift detection check")
    p.add_argument("--retrain", action="store_true",
                   help="run rolling retrain pipeline")
    p.add_argument("--retrain-days", type=int, default=30,
                   help="retrain window in days (default: 30)")
    p.add_argument("--dry-run", action="store_true",
                   help="retrain dry-run (train but don't deploy)")
    p.add_argument("--monitor", action="store_true",
                   help="continuous monitoring dashboard")
    p.add_argument("--monitor-interval", type=int, default=30,
                   help="monitor refresh interval in seconds")
    p.add_argument("--model-history", action="store_true",
                   help="show model version history")
    p.add_argument("--backfill", action="store_true",
                   help="backfill prediction outcomes")

    args = p.parse_args(argv)

    setup_logging(args.verbose)
    log = logging.getLogger("v12_runner")

    # Determine symbols
    if args.all:
        symbols = V12_SYMBOLS
    elif args.symbol:
        symbols = [args.symbol]
    else:
        symbols = [V12_SYMBOLS[0]]  # default: SOL

    # --- Monitor mode ---
    if args.monitor:
        for sym in symbols:
            watch(sym, args.monitor_interval)
        return 0

    # --- Report mode ---
    if args.report:
        for sym in symbols:
            show_report(sym)
        return 0

    # --- Drift check mode ---
    if args.drift:
        for sym in symbols:
            show_drift(sym)
        return 0

    # --- Retrain mode ---
    if args.retrain:
        for sym in symbols:
            ec = retrain_main([
                "--symbol", sym.replace("/USDT", ""),
                "--days", str(args.retrain_days),
                "--exchange", args.exchange,
            ] + (["--dry-run"] if args.dry_run else []))
            if ec != 0:
                log.warning("Retrain for %s returned exit code %d", sym, ec)
        return 0

    # --- Model history mode ---
    if args.model_history:
        for sym in symbols:
            show_model_history(sym)
        return 0

    # --- Backfill mode ---
    if args.backfill:
        for sym in symbols:
            backfill_outcomes(sym)
        return 0

    # --- Status mode ---
    if args.status:
        for sym in symbols:
            try:
                eng = Engine(symbol=sym, profile=args.profile,
                             warmup_5m_bars=args.warmup_bars, exchange=args.exchange)
                status = eng.status()
                log.info("=== V12 STATUS %s (%s) ===", sym, args.profile)
                log.info("  config: Q%d/%d dir=%s trend=%s Win=%d Cost=%.2f%%",
                         status["config"]["q_long"], status["config"]["q_short"],
                         status["config"]["direction"], status["config"]["trend_filter"],
                         status["config"]["window_size"], status["config"]["cost_pct"])
                log.info("  model_loaded: %s  version: %s",
                         status["model_loaded"], status.get("model_version", ""))
                log.info("  db: %s", status.get("db_path", ""))

                state = status["state"]
                log.info("  equity: %.3f%% trades=%d wins=%d WR=%.1f%%",
                         state.get("equity_pct", 0),
                         state.get("n_trades", 0),
                         state.get("n_wins", 0),
                         (state.get("n_wins", 0) / max(state.get("n_trades", 1), 1)) * 100)
                pos = state.get("position")
                if pos:
                    log.info("  position: %s @ %.4f bars_held=%d",
                             pos["side"], pos["entry_price"], pos.get("bars_held", 0))
                else:
                    log.info("  position: none")

                # Walk-forward validation stats
                cfg = status["config"]
                if "wr_wf" in cfg:
                    log.info("  WF validation: WR=%.3f PF=%.2f Sharpe=%.3f consistency=%s",
                             cfg["wr_wf"], cfg.get("pf_wf", 0),
                             cfg.get("sharpe_wf", 0), cfg.get("consistency", "?"))
            except Exception as e:
                log.error("  %s: error loading status: %s", sym, e)
        return 0

    # --- Run mode (default) ---
    if len(symbols) == 1:
        return _run_engine(symbols[0], args.profile, args.warmup_bars,
                          args.exchange, args.once)

    # Multi-symbol — launch separate processes
    log.info("Launching %d V12 engines: %s", len(symbols), ", ".join(symbols))
    processes = []
    for sym in symbols:
        proc = multiprocessing.Process(
            target=_run_engine,
            args=(sym, args.profile, args.warmup_bars,
                  args.exchange, args.once),
            name=f"v12_pt_{sym}",
        )
        proc.start()
        processes.append(proc)
        log.info("Started V12 engine for %s (PID=%d)", sym, proc.pid)

    try:
        for proc in processes:
            proc.join()
    except KeyboardInterrupt:
        log.info("Interrupted — stopping all V12 engines")
        for proc in processes:
            proc.terminate()
        for proc in processes:
            proc.join(timeout=10)

    return 0


if __name__ == "__main__":
    sys.exit(main())
