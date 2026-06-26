"""
runner.py — CLI entrypoint for the V12 paper trader.

Usage:
    # Check if models exist
    python -m scripts.v12.paper_trader --status --symbol SOL

    # Run single cycle (smoke test)
    python -m scripts.v12.paper_trader --symbol SOL --once

    # Run continuous paper trading
    python -m scripts.v12.paper_trader --symbol SOL

    # Run with conservative profile
    python -m scripts.v12.paper_trader --symbol SOL --profile conservative

    # Run all V12 symbols
    python -m scripts.v12.paper_trader --all

    # Background mode
    nohup python -m scripts.v12.paper_trader --symbol SOL > /tmp/v12_SOL.log 2>&1 &
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys

from .engine import Engine
from .model import V12_SYMBOL_CONFIG, DEFAULT_PROFILE

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
    p = argparse.ArgumentParser(description="PPMT V12 paper trader (1h microstructure)")
    p.add_argument("--symbol", default=None,
                   help="target symbol (e.g. SOL, DOGE, AVAX)")
    p.add_argument("--profile", default=DEFAULT_PROFILE,
                   choices=["balanced", "conservative"],
                   help="trading profile (balanced=more trades, conservative=higher WR)")
    p.add_argument("--all", action="store_true",
                   help="run all V12 validated symbols (SOL, DOGE, AVAX)")
    p.add_argument("--exchange", default="bybit",
                   choices=["bybit", "okx", "kraken", "coinbase"])
    p.add_argument("--warmup-bars", type=int, default=400,
                   help="number of 5m bars for feature warm-up")
    p.add_argument("--once", action="store_true",
                   help="run exactly one cycle then exit")
    p.add_argument("--status", action="store_true",
                   help="print engine status and exit")
    p.add_argument("--verbose", action="store_true")
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
                log.info("  model_loaded: %s", status["model_loaded"])

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

    # --- Run mode ---
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
