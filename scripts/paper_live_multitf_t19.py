#!/usr/bin/env python3
"""TAREA 19: Paper Live Multi-TF Launcher + Monitor.

Launches 7 WebSocket streams simultaneously and monitors
Net EV Gate statistics for 1 hour.

Streams:
  - BTC/USDT  5m  (blue_chip)
  - ETH/USDT  5m  (blue_chip)
  - SOL/USDT  5m  (large_cap)
  - SOL/USDT  15m (large_cap)
  - AVAX/USDT 5m  (large_cap)
  - LINK/USDT 5m  (large_cap)
  - LINK/USDT 15m (large_cap)
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import websockets

# ── Configuration ──
SERVER_URL = os.environ.get("PPMT_SERVER_URL", "ws://localhost:8420")
MONITOR_DURATION_SEC = 3600  # 1 hour

STREAMS = [
    ("BTC-USDT",  "5m"),
    ("ETH-USDT",  "5m"),
    ("SOL-USDT",  "5m"),
    ("SOL-USDT",  "15m"),
    ("AVAX-USDT", "5m"),
    ("LINK-USDT", "5m"),
    ("LINK-USDT", "15m"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("t19_monitor")


async def stream_worker(symbol: str, timeframe: str, stats: dict):
    """Connect to a single WS stream and track events."""
    ws_url = f"{SERVER_URL}/ws/paper-live/{symbol}/{timeframe}"
    display = f"{symbol.replace('-', '/')}/{timeframe}"
    logger.info(f"[{display}] Connecting to {ws_url}")

    try:
        async with websockets.connect(ws_url, close_timeout=5) as ws:
            logger.info(f"[{display}] Connected ✓")
            stats[display]["connected"] = True

            async for msg in ws:
                try:
                    data = json.loads(msg)
                    msg_type = data.get("type", "")

                    if msg_type == "candle":
                        stats[display]["candles"] += 1
                    elif msg_type == "brain_update":
                        brain = data.get("data", {})
                        wc = brain.get("weighted_confidence", 0)
                        if wc > 0:
                            stats[display]["brain_updates"] += 1
                            stats[display]["last_conf"] = wc
                    elif msg_type == "position_update":
                        pos = data.get("data", {})
                        status = pos.get("status", "")
                        if status in ("ACTIVE", "BREAK_EVEN_SECURED"):
                            stats[display]["positions_opened"] += 1
                            direction = pos.get("direction", "?")
                            entry = pos.get("entry_price", 0)
                            net_ev = pos.get("metadata", {}).get("net_ev_score", "N/A")
                            logger.info(
                                f"[{display}] POSITION OPENED: {direction} @ {entry:.6f} "
                                f"Net_EV={net_ev}"
                            )
                        elif "CLOSED" in status:
                            pnl = pos.get("pnl_pct", 0)
                            stats[display]["positions_closed"] += 1
                            if pnl > 0:
                                stats[display]["wins"] += 1
                            else:
                                stats[display]["losses"] += 1
                            logger.info(
                                f"[{display}] POSITION CLOSED: {status} PnL={pnl:+.2f}%"
                            )
                    elif msg_type == "error":
                        error_msg = data.get("data", {}).get("message", "")
                        logger.warning(f"[{display}] Error: {error_msg}")

                except json.JSONDecodeError:
                    pass

    except Exception as e:
        logger.error(f"[{display}] Connection error: {e}")
        stats[display]["connected"] = False


async def stats_reporter(stats: dict):
    """Print a summary report every 5 minutes."""
    start = time.time()
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        elapsed = (time.time() - start) / 60
        logger.info(f"\n{'=' * 70}")
        logger.info(f"MONITOR REPORT — {elapsed:.0f} min elapsed")
        logger.info(f"{'=' * 70}")

        for display, s in stats.items():
            if s["candles"] == 0 and not s["connected"]:
                continue
            logger.info(
                f"  {display:18s} candles={s['candles']:4d} "
                f"brain={s['brain_updates']:4d} "
                f"opened={s['positions_opened']} "
                f"closed={s['positions_closed']} "
                f"W/L={s['wins']}/{s['losses']} "
                f"last_conf={s.get('last_conf', 0):.3f}"
            )


async def fetch_net_ev_stats():
    """Fetch Net EV Gate stats from the server's REST endpoint."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{SERVER_URL.replace('ws', 'http')}/api/net-ev-stats", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception:
        pass
    return None


async def net_ev_reporter():
    """Report Net EV Gate stats every 10 minutes."""
    while True:
        await asyncio.sleep(600)  # Every 10 minutes
        ev_stats = await fetch_net_ev_stats()
        if ev_stats:
            s = ev_stats.get("stats", {})
            a = ev_stats.get("active_symbols", {})
            logger.info(f"\n{'─' * 50}")
            logger.info(f"NET EV GATE STATS:")
            logger.info(f"  Total raw signals:     {s.get('total_raw_signals', 0)}")
            logger.info(f"  Passed Net EV:         {s.get('passed_net_ev', 0)}")
            logger.info(f"  Rejected by spread:    {s.get('rejected_spread', 0)}")
            logger.info(f"  Rejected by EV score:  {s.get('rejected_ev_score', 0)}")
            logger.info(f"  Rejected by overlap:   {s.get('rejected_overlap', 0)}")
            if a:
                logger.info(f"  Active symbols:        {a}")
            logger.info(f"{'─' * 50}")


async def main():
    logger.info("=" * 70)
    logger.info("TAREA 19: Paper Live Multi-TF — Net EV Gate Monitor")
    logger.info("=" * 70)
    logger.info(f"Server: {SERVER_URL}")
    logger.info(f"Streams: {len(STREAMS)}")
    logger.info(f"Duration: {MONITOR_DURATION_SEC / 60:.0f} minutes")
    logger.info("")

    # Initialize stats
    stats = {}
    for symbol, tf in STREAMS:
        display = f"{symbol.replace('-', '/')}/{tf}"
        stats[display] = {
            "connected": False,
            "candles": 0,
            "brain_updates": 0,
            "positions_opened": 0,
            "positions_closed": 0,
            "wins": 0,
            "losses": 0,
            "last_conf": 0.0,
        }

    # Launch all stream workers + reporters
    tasks = []
    for symbol, tf in STREAMS:
        tasks.append(stream_worker(symbol, tf, stats))

    # Add periodic reporters
    tasks.append(stats_reporter(stats))
    tasks.append(net_ev_reporter())

    # Run for the specified duration
    logger.info("Starting all streams...")
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=MONITOR_DURATION_SEC)
    except asyncio.TimeoutError:
        logger.info("Monitor duration reached.")

    # ─── Final Report ───
    logger.info(f"\n{'=' * 70}")
    logger.info("FINAL REPORT — TAREA 19 Paper Live Multi-TF")
    logger.info(f"{'=' * 70}")

    total_candles = 0
    total_opened = 0
    total_closed = 0
    total_wins = 0
    total_losses = 0

    for display, s in stats.items():
        total_candles += s["candles"]
        total_opened += s["positions_opened"]
        total_closed += s["positions_closed"]
        total_wins += s["wins"]
        total_losses += s["losses"]
        logger.info(
            f"  {display:18s} candles={s['candles']:4d} "
            f"opened={s['positions_opened']} "
            f"closed={s['positions_closed']} "
            f"W/L={s['wins']}/{s['losses']}"
        )

    logger.info(f"\n  TOTALS: candles={total_candles} opened={total_opened} "
                f"closed={total_closed} W/L={total_wins}/{total_losses}")

    # Fetch final Net EV stats
    ev_stats = await fetch_net_ev_stats()
    if ev_stats:
        s = ev_stats.get("stats", {})
        logger.info(f"\n  NET EV GATE FINAL:")
        logger.info(f"    Total raw signals:     {s.get('total_raw_signals', 0)}")
        logger.info(f"    Passed Net EV:         {s.get('passed_net_ev', 0)}")
        logger.info(f"    Rejected by spread:    {s.get('rejected_spread', 0)}")
        logger.info(f"    Rejected by EV score:  {s.get('rejected_ev_score', 0)}")
        logger.info(f"    Rejected by overlap:   {s.get('rejected_overlap', 0)}")

    logger.info(f"\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
