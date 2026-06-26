"""
runner.py — CLI entrypoint for the v7 paper trader.

Supports multi-token paper trading with per-symbol config from SYMBOL_CONFIG.

Usage:
    # 1. Train model (or re-train) on ~180d of historical 5m data
    python -m scripts.v7.paper_trader.runner --train --symbol DOGE/USDT

    # 2. Train all core tokens (DOGE, AVAX, SOL, ETH)
    python -m scripts.v7.paper_trader.runner --train --all

    # 3. Start paper trading loop (one symbol)
    python -m scripts.v7.paper_trader.runner --symbol DOGE/USDT

    # 4. Start multi-token paper trading (separate processes)
    python -m scripts.v7.paper_trader.runner --all

    # 5. Single-cycle mode (for cron / smoke test)
    python -m scripts.v7.paper_trader.runner --symbol DOGE/USDT --once

    # 6. Status report
    python -m scripts.v7.paper_trader.runner --status --symbol DOGE/USDT

Recommended symbols for paper trading (per deep optimization 180d):
    DOGE/USDT (4/4, +41.6%), AVAX/USDT (4/4, +44.8%),
    SOL/USDT (4/4, +41.5%), ETH/USDT (3/4, +36.6%)

By default we use Bybit (Binance IP-banned in some regions). Override with
--exchange bybit|okx|kraken.

IMPORTANT: Use LIMIT ORDERS to achieve maker fees (0.04% round-trip).
Taker fees (0.14%) significantly reduce profitability.
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys

from .engine import Engine
from .model import SYMBOL_CONFIG


# Core tokens with 180d deep optimization (4/4 or 3/4 consistency)
CORE_TOKENS = ["DOGE/USDT", "AVAX/USDT", "SOL/USDT", "ETH/USDT"]

# All tokens with any validated config
ALL_TOKENS = list(SYMBOL_CONFIG.keys())


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _run_engine(symbol: str, timeframe: str, warmup_bars: int,
                exchange: str, bootstrap_bars: int, once: bool) -> int:
    """Run engine for one symbol. Used for both single and multi-token."""
    eng = Engine(symbol=symbol, timeframe=timeframe,
                 warmup_bars=warmup_bars, exchange=exchange,
                 bootstrap_bars=bootstrap_bars, auto_train=True)
    eng.ensure_model()

    if once:
        result = eng.run_once()
        return 0 if result else 1

    eng.run_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PPMT v7 paper trader")
    p.add_argument("--symbol", default=None,
                   help="target trading symbol (e.g. DOGE/USDT)")
    p.add_argument("--symbols", default=None,
                   help="comma-separated list for batch train/status")
    p.add_argument("--all", action="store_true",
                   help="use all core tokens (DOGE, AVAX, SOL, ETH)")
    p.add_argument("--exchange", default="bybit",
                   choices=["bybit", "okx", "kraken", "coinbase"])
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--warmup-bars", type=int, default=400,
                   help="number of historical bars fetched each cycle for feature warm-up")
    p.add_argument("--bootstrap-bars", type=int, default=52000,
                   help="number of historical bars used for bootstrap training (~180d)")
    p.add_argument("--train", action="store_true",
                   help="train (or re-train) the model and exit")
    p.add_argument("--once", action="store_true",
                   help="run exactly one cycle (process one new candle close) then exit")
    p.add_argument("--status", action="store_true",
                   help="print engine status and exit")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    setup_logging(args.verbose)
    log = logging.getLogger("pt_runner")

    # Determine symbols
    if args.all:
        symbols = CORE_TOKENS
    elif args.symbols:
        symbols = args.symbols.split(",")
    elif args.symbol:
        symbols = [args.symbol]
    else:
        symbols = [CORE_TOKENS[0]]  # default: DOGE/USDT

    # --- Train mode ---
    if args.train:
        for sym in symbols:
            log.info("=== TRAIN %s ===", sym)
            eng = Engine(symbol=sym, timeframe=args.timeframe,
                         warmup_bars=args.warmup_bars, exchange=args.exchange,
                         bootstrap_bars=args.bootstrap_bars, auto_train=True)
            eng._bootstrap_train()
            log.info("=== TRAIN %s done: n_rounds=%d auc_train=%.3f auc_test=%.3f ===",
                     sym, eng.meta.get("num_boost_round", 0),
                     eng.meta.get("auc_train", 0),
                     eng.meta.get("auc_test", 0))
        return 0

    # --- Status mode ---
    if args.status:
        for sym in symbols:
            eng = Engine(symbol=sym, timeframe=args.timeframe,
                         warmup_bars=args.warmup_bars, exchange=args.exchange,
                         bootstrap_bars=args.bootstrap_bars, auto_train=False)
            status = eng.status()
            log.info("=== STATUS %s ===", sym)
            log.info("  config: Q%d/%d Win=%d Cost=%.2f%% HP=%s",
                     status["config"]["q_long"], status["config"]["q_short"],
                     status["config"]["window_size"], status["config"]["cost_pct"],
                     status["config"]["hp"])
            log.info("  model_loaded: %s", status["model_loaded"])
            log.info("  equity: %.3f%% trades=%d wins=%d WR=%.1f%%",
                     status["state"].get("equity_pct", 0),
                     status["state"].get("n_trades", 0),
                     status["state"].get("n_wins", 0),
                     (status["state"].get("n_wins", 0) / max(status["state"].get("n_trades", 1), 1)) * 100)
            pos = status["state"].get("position")
            if pos:
                log.info("  position: %s @ %.4f bars_held=%d",
                         pos["side"], pos["entry_price"], pos.get("bars_held", 0))
            else:
                log.info("  position: none")
        return 0

    # --- Run mode ---
    if len(symbols) == 1:
        # Single symbol — run in this process
        return _run_engine(symbols[0], args.timeframe, args.warmup_bars,
                          args.exchange, args.bootstrap_bars, args.once)

    # Multi-symbol — launch separate processes
    log.info("Launching %d engines: %s", len(symbols), ", ".join(symbols))
    processes = []
    for sym in symbols:
        proc = multiprocessing.Process(
            target=_run_engine,
            args=(sym, args.timeframe, args.warmup_bars,
                  args.exchange, args.bootstrap_bars, args.once),
            name=f"pt_{sym.replace('/', '_')}",
        )
        proc.start()
        processes.append(proc)
        log.info("Started engine for %s (PID=%d)", sym, proc.pid)

    # Wait for all to finish (they run forever unless --once)
    try:
        for proc in processes:
            proc.join()
    except KeyboardInterrupt:
        log.info("Interrupted — stopping all engines")
        for proc in processes:
            proc.terminate()
        for proc in processes:
            proc.join(timeout=10)

    return 0


if __name__ == "__main__":
    sys.exit(main())
