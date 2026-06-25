"""
runner.py — CLI entrypoint for the v7.5 paper trader.

Usage:
    # 1. Train model (or re-train) on ~30d of historical 5m data
    python -m scripts.v7.paper_trader.runner --train --symbol SOL/USDT

    # 2. Start paper trading loop (one cycle per 5m candle close)
    python -m scripts.v7.paper_trader.runner --symbol SOL/USDT

    # 3. Single-cycle mode (for cron / smoke test)
    python -m scripts.v7.paper_trader.runner --symbol SOL/USDT --once

    # 4. Status report
    python -m scripts.v7.paper_trader.runner --status --symbol SOL/USDT

    # 5. Train multiple symbols in batch
    python -m scripts.v7.paper_trader.runner --train --symbols BTC/USDT,ETH/USDT,SOL/USDT

Recommended symbols for paper trading (per PPMT_v7_MASTER_PLAN.md §6):
    BTC/USDT, ETH/USDT, SOL/USDT, ADA/USDT, XRP/USDT, DOGE/USDT

By default we use Bybit (Binance IP-banned in some regions). Override with
`--exchange bybit|okx|kraken`.
"""
from __future__ import annotations

import argparse
import logging
import sys

from .engine import Engine


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PPMT v7.5 paper trader")
    p.add_argument("--symbol", default="SOL/USDT",
                   help="target trading symbol (default SOL/USDT)")
    p.add_argument("--symbols", default=None,
                   help="comma-separated list for batch train/status (overrides --symbol)")
    p.add_argument("--exchange", default="bybit",
                   choices=["bybit", "okx", "kraken", "coinbase"])
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--warmup-bars", type=int, default=200,
                   help="number of historical bars fetched each cycle for feature warm-up")
    p.add_argument("--bootstrap-bars", type=int, default=4000,
                   help="number of historical bars used for one-shot bootstrap training")
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

    symbols = args.symbols.split(",") if args.symbols else [args.symbol]

    # --- Train mode ---
    if args.train:
        for sym in symbols:
            log.info("=== TRAIN %s ===", sym)
            eng = Engine(symbol=sym, timeframe=args.timeframe,
                         warmup_bars=args.warmup_bars, exchange=args.exchange,
                         bootstrap_bars=args.bootstrap_bars, auto_train=True)
            eng._bootstrap_train()
            log.info("=== TRAIN %s done: %s ===", sym, eng.meta)
        return 0

    # --- Status mode ---
    if args.status:
        for sym in symbols:
            eng = Engine(symbol=sym, timeframe=args.timeframe,
                         warmup_bars=args.warmup_bars, exchange=args.exchange,
                         bootstrap_bars=args.bootstrap_bars, auto_train=False)
            log.info("=== STATUS %s ===", sym)
            log.info("state: %s", eng.state)
        return 0

    # --- Run mode ---
    if len(symbols) > 1:
        log.error("running multiple symbols in parallel not supported in this version; "
                  "launch separate processes per symbol")
        return 1

    eng = Engine(symbol=symbols[0], timeframe=args.timeframe,
                 warmup_bars=args.warmup_bars, exchange=args.exchange,
                 bootstrap_bars=args.bootstrap_bars, auto_train=True)
    eng.ensure_model()

    if args.once:
        result = eng.run_once()
        log.info("once result: %s", result)
        return 0 if result else 1

    eng.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
