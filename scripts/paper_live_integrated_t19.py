#!/usr/bin/env python3
"""TAREA 19: Integrated Paper Live Multi-TF Test.

Runs the v2 server + 7 WebSocket clients in a single process.
Monitors for 1 hour and reports Net EV Gate statistics.

Since the server can't persist as a background process in this
environment, we run everything in-process using asyncio.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
import pandas as pd
import uvicorn

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.ppmt import PPMT
from ppmt.core.trie import PPMTTrie
from ppmt.core.profiles import SPREAD_ESTIMATES
from ppmt.engine.realtime import _DirectPollExchange

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("t19")

# ── Configuration ──
STREAMS = [
    ("BTC/USDT",  "5m",  "blue_chip"),
    ("ETH/USDT",  "5m",  "blue_chip"),
    ("SOL/USDT",  "5m",  "large_cap"),
    ("SOL/USDT",  "15m", "large_cap"),
    ("AVAX/USDT", "5m",  "large_cap"),
    ("LINK/USDT", "5m",  "large_cap"),
    ("LINK/USDT", "15m", "large_cap"),
]

NET_EV_GATE_THRESHOLD = 0.80
EV_GATE_STATS = {
    "total_raw_signals": 0,
    "passed_net_ev": 0,
    "rejected_spread": 0,
    "rejected_ev_score": 0,
    "rejected_overlap": 0,
}
ACTIVE_SYMBOLS: dict[str, str] = {}  # {"SOL/USDT": "5m", ...}

# Per-stream tracking
STREAM_STATS = {}


async def run_single_stream(symbol: str, timeframe: str, asset_class: str):
    """Run a single paper-live stream: init engine, poll, apply Net EV Gate."""
    display = f"{symbol}/{timeframe}"
    STREAM_STATS[display] = {
        "candles": 0,
        "raw_signals": 0,
        "passed_net_ev": 0,
        "rejected_spread": 0,
        "rejected_ev_score": 0,
        "rejected_overlap": 0,
        "positions_opened": 0,
        "positions_closed": 0,
        "wins": 0,
        "losses": 0,
        "net_ev_scores": [],
    }

    logger.info(f"[{display}] Initializing...")

    # ─── Init Engine ───
    try:
        classifier = AssetClassifier()
        info = classifier.classify(symbol)
        storage = PPMTStorage()

        engine = PPMT(
            symbol=symbol,
            asset_class=info.asset_class,
            weight_profile=info.weight_profile,
            dual_sax=True,
            min_confidence=0.08,
            timeframe=timeframe,
        )

        tries = storage.load_all_tries(symbol, asset_class=info.asset_class, timeframe=timeframe)
        n1 = tries.get("n1")
        n2 = tries.get("n2")
        n3 = tries.get("n3")
        n4 = tries.get("n4")

        n1_count = n1.pattern_count if n1 else 0
        n2_count = n2.pattern_count if n2 else 0
        n3_count = n3.pattern_count if n3 else 0
        n4_count = n4.pattern_count if hasattr(n4, 'pattern_count') and n4 else 0
        logger.info(f"[{display}] Tries: N1={n1_count} N2={n2_count} N3={n3_count} N4={n4_count}")

        if n1 or n2 or n3:
            engine.set_tries(
                trie_n1=n1 if n1 is not None else PPMTTrie(name="n1_empty"),
                trie_n2=n2 if n2 is not None else PPMTTrie(name="n2_empty"),
                trie_n3=n3 or PPMTTrie(name="n3_empty"),
                trie_n4=n4 if n4 is not None else engine.trie_n4,
            )
        else:
            logger.warning(f"[{display}] No tries found — skipping")
            storage.close()
            return

    except Exception as e:
        logger.error(f"[{display}] Init failed: {e}")
        return

    # ─── Warmup ───
    exchange = _DirectPollExchange("binance")
    api_symbol = symbol.replace("/", "")
    is_in_position = False
    position_direction = None
    position_entry_price = 0.0

    try:
        ohlcv_raw = await exchange.fetch_ohlcv(api_symbol, timeframe, limit=500)
        if ohlcv_raw:
            df = pd.DataFrame(ohlcv_raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            for i in range(len(df) - 1):
                row = df.iloc[[i]]
                engine.process_new_candle(
                    candle_df=row,
                    current_price=float(row["close"].iloc[0]),
                )
            logger.info(f"[{display}] Warmup: {len(df)} candles")
    except Exception as e:
        logger.warning(f"[{display}] Warmup failed: {e}")

    # ─── Poll loop ───
    tf_seconds = {"1m": 5, "5m": 10, "15m": 15, "1h": 30}
    poll_interval = tf_seconds.get(timeframe, 10)
    last_candle_ts = 0
    start_time = time.time()

    try:
        while time.time() - start_time < 3600:  # 1 hour max
            try:
                ohlcv_raw = await exchange.fetch_ohlcv(api_symbol, timeframe, limit=2)
                if not ohlcv_raw:
                    await asyncio.sleep(poll_interval)
                    continue

                latest = ohlcv_raw[-1]
                ts_ms, o, h, l, c, v = latest
                ts_sec = int(ts_ms / 1000)

                if ts_sec <= last_candle_ts:
                    await asyncio.sleep(poll_interval)
                    continue

                last_candle_ts = ts_sec
                current_price = float(c)
                STREAM_STATS[display]["candles"] += 1

                # Feed to engine
                candle_df = pd.DataFrame(
                    {"open": [o], "high": [h], "low": [l], "close": [c], "volume": [v]},
                    index=pd.DatetimeIndex([datetime.fromtimestamp(ts_sec, tz=timezone.utc)]),
                )

                result = engine.process_new_candle(
                    candle_df=candle_df,
                    current_price=current_price,
                    is_in_position=is_in_position,
                    entry_price=position_entry_price if is_in_position else None,
                )

                if result is None or not result.signal or not result.signal.is_entry:
                    # Also check SL/TP for existing position
                    if is_in_position:
                        if position_direction == "LONG":
                            if current_price < position_entry_price * 0.97:  # -3% SL
                                is_in_position = False
                                pnl = (current_price - position_entry_price) / position_entry_price * 100
                                STREAM_STATS[display]["positions_closed"] += 1
                                if pnl > 0:
                                    STREAM_STATS[display]["wins"] += 1
                                else:
                                    STREAM_STATS[display]["losses"] += 1
                                ACTIVE_SYMBOLS.pop(symbol, None)
                                logger.info(f"[{display}] CLOSED SL: PnL={pnl:+.2f}%")
                            elif current_price > position_entry_price * 1.05:  # +5% TP
                                is_in_position = False
                                pnl = (current_price - position_entry_price) / position_entry_price * 100
                                STREAM_STATS[display]["positions_closed"] += 1
                                if pnl > 0:
                                    STREAM_STATS[display]["wins"] += 1
                                else:
                                    STREAM_STATS[display]["losses"] += 1
                                ACTIVE_SYMBOLS.pop(symbol, None)
                                logger.info(f"[{display}] CLOSED TP: PnL={pnl:+.2f}%")
                        elif position_direction == "SHORT":
                            if current_price > position_entry_price * 1.03:  # +3% SL
                                is_in_position = False
                                pnl = (position_entry_price - current_price) / position_entry_price * 100
                                STREAM_STATS[display]["positions_closed"] += 1
                                if pnl > 0:
                                    STREAM_STATS[display]["wins"] += 1
                                else:
                                    STREAM_STATS[display]["losses"] += 1
                                ACTIVE_SYMBOLS.pop(symbol, None)
                                logger.info(f"[{display}] CLOSED SL: PnL={pnl:+.2f}%")
                            elif current_price < position_entry_price * 0.95:  # -5% TP
                                is_in_position = False
                                pnl = (position_entry_price - current_price) / position_entry_price * 100
                                STREAM_STATS[display]["positions_closed"] += 1
                                if pnl > 0:
                                    STREAM_STATS[display]["wins"] += 1
                                else:
                                    STREAM_STATS[display]["losses"] += 1
                                ACTIVE_SYMBOLS.pop(symbol, None)
                                logger.info(f"[{display}] CLOSED TP: PnL={pnl:+.2f}%")
                    await asyncio.sleep(poll_interval)
                    continue

                if is_in_position:
                    await asyncio.sleep(poll_interval)
                    continue

                # ─── Net EV Gate ───
                sig = result.signal
                EV_GATE_STATS["total_raw_signals"] += 1
                STREAM_STATS[display]["raw_signals"] += 1

                # Anti-overlap check
                if symbol in ACTIVE_SYMBOLS:
                    existing_tf = ACTIVE_SYMBOLS[symbol]
                    EV_GATE_STATS["rejected_overlap"] += 1
                    STREAM_STATS[display]["rejected_overlap"] += 1
                    logger.info(
                        f"[NET EV GATE] OVERLAP REJECTED: {display} — "
                        f"already active in {existing_tf}"
                    )
                    await asyncio.sleep(poll_interval)
                    continue

                # Net EV calculation
                expected_move_pct = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.0
                spread_pct = SPREAD_ESTIMATES.get(info.asset_class, 0.050)
                net_move = expected_move_pct - spread_pct

                if net_move <= 0:
                    EV_GATE_STATS["rejected_spread"] += 1
                    STREAM_STATS[display]["rejected_spread"] += 1
                    logger.info(
                        f"[NET EV GATE] SPREAD REJECTED: {display} "
                        f"move={expected_move_pct:.3f}% spread={spread_pct:.3f}% "
                        f"net={net_move:.3f}%"
                    )
                    await asyncio.sleep(poll_interval)
                    continue

                sl_pct = abs(sig.sl_price - current_price) / current_price * 100 if sig.sl_price and current_price > 0 else 0.5
                if sl_pct < 0.01:
                    sl_pct = 0.5
                net_rr = net_move / sl_pct
                net_rr_capped = min(net_rr, 3.0)
                net_ev = sig.confidence * net_rr_capped

                if net_ev < NET_EV_GATE_THRESHOLD:
                    EV_GATE_STATS["rejected_ev_score"] += 1
                    STREAM_STATS[display]["rejected_ev_score"] += 1
                    logger.info(
                        f"[NET EV GATE] EV REJECTED: {display} "
                        f"conf={sig.confidence:.3f} net_R:R={net_rr_capped:.2f} "
                        f"Net_EV={net_ev:.3f}"
                    )
                    await asyncio.sleep(poll_interval)
                    continue

                # ─── PASSED ───
                EV_GATE_STATS["passed_net_ev"] += 1
                STREAM_STATS[display]["passed_net_ev"] += 1
                STREAM_STATS[display]["net_ev_scores"].append(net_ev)
                ACTIVE_SYMBOLS[symbol] = timeframe
                is_in_position = True
                position_direction = sig.direction or "LONG"
                position_entry_price = current_price
                STREAM_STATS[display]["positions_opened"] += 1

                logger.info(
                    f"[NET EV GATE] PASSED: {display} {sig.signal_type.value} @ {current_price:.6f} "
                    f"conf={sig.confidence:.3f} net_R:R={net_rr_capped:.2f} "
                    f"Net_EV={net_ev:.3f} spread={spread_pct:.3f}%"
                )

            except Exception as e:
                logger.error(f"[{display}] Poll error: {e}")

            await asyncio.sleep(poll_interval)

    except asyncio.CancelledError:
        pass
    finally:
        # Clean up
        if symbol in ACTIVE_SYMBOLS and ACTIVE_SYMBOLS.get(symbol) == timeframe:
            ACTIVE_SYMBOLS.pop(symbol, None)
        try:
            await exchange.close()
        except Exception:
            pass
        storage.close()
        logger.info(f"[{display}] Session ended")


async def periodic_reporter():
    """Print summary every 5 minutes."""
    start = time.time()
    while True:
        await asyncio.sleep(300)  # 5 minutes
        elapsed = (time.time() - start) / 60
        logger.info(f"\n{'=' * 70}")
        logger.info(f"T19 MONITOR — {elapsed:.0f} min elapsed")
        logger.info(f"{'=' * 70}")
        logger.info(f"  NET EV GATE GLOBAL:")
        logger.info(f"    Raw signals:      {EV_GATE_STATS['total_raw_signals']}")
        logger.info(f"    Passed Net EV:    {EV_GATE_STATS['passed_net_ev']}")
        logger.info(f"    Rejected spread:  {EV_GATE_STATS['rejected_spread']}")
        logger.info(f"    Rejected EV:      {EV_GATE_STATS['rejected_ev_score']}")
        logger.info(f"    Rejected overlap: {EV_GATE_STATS['rejected_overlap']}")
        logger.info(f"  ACTIVE SYMBOLS: {dict(ACTIVE_SYMBOLS)}")

        for display, s in STREAM_STATS.items():
            if s["candles"] == 0:
                continue
            logger.info(
                f"  {display:18s} candles={s['candles']:4d} "
                f"raw={s['raw_signals']} passed={s['passed_net_ev']} "
                f"spread_rej={s['rejected_spread']} ev_rej={s['rejected_ev_score']} "
                f"overlap_rej={s['rejected_overlap']} "
                f"opened={s['positions_opened']} closed={s['positions_closed']} "
                f"W/L={s['wins']}/{s['losses']}"
            )


async def main():
    logger.info("=" * 70)
    logger.info("TAREA 19: Net EV Gate + Multi-TF Paper Live")
    logger.info("=" * 70)
    logger.info(f"Streams: {len(STREAMS)}")
    logger.info(f"Duration: 60 minutes")
    logger.info(f"Net EV Gate threshold: {NET_EV_GATE_THRESHOLD}")
    logger.info(f"SPREAD_ESTIMATES: {SPREAD_ESTIMATES}")
    logger.info("")

    # Launch all streams + reporter
    tasks = []
    for symbol, tf, ac in STREAMS:
        tasks.append(run_single_stream(symbol, tf, ac))
    tasks.append(periodic_reporter())

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"Main error: {e}")

    # ─── Final Report ───
    logger.info(f"\n{'=' * 70}")
    logger.info("TAREA 19: FINAL REPORT — Net EV Gate + Multi-TF")
    logger.info(f"{'=' * 70}")

    logger.info(f"\n  NET EV GATE SUMMARY:")
    logger.info(f"    Total raw signals:     {EV_GATE_STATS['total_raw_signals']}")
    logger.info(f"    Passed Net EV:         {EV_GATE_STATS['passed_net_ev']}")
    logger.info(f"    Rejected by spread:    {EV_GATE_STATS['rejected_spread']}")
    logger.info(f"    Rejected by EV score:  {EV_GATE_STATS['rejected_ev_score']}")
    logger.info(f"    Rejected by overlap:   {EV_GATE_STATS['rejected_overlap']}")

    logger.info(f"\n  PER-STREAM DETAILS:")
    logger.info(f"  {'Stream':18s} {'Candles':>8} {'Raw':>5} {'Pass':>5} {'SprdRj':>6} {'EVRj':>4} {'OvlpRj':>6} {'Open':>4} {'Clsd':>4} {'W/L':>5}")
    logger.info(f"  {'─'*18} {'─'*8} {'─'*5} {'─'*5} {'─'*6} {'─'*4} {'─'*6} {'─'*4} {'─'*4} {'─'*5}")

    for display, s in STREAM_STATS.items():
        logger.info(
            f"  {display:18s} {s['candles']:>8d} {s['raw_signals']:>5d} "
            f"{s['passed_net_ev']:>5d} {s['rejected_spread']:>6d} "
            f"{s['rejected_ev_score']:>4d} {s['rejected_overlap']:>6d} "
            f"{s['positions_opened']:>4d} {s['positions_closed']:>4d} "
            f"{s['wins']}/{s['losses']}"
        )

    # Net EV score distribution for passed signals
    all_net_evs = []
    for display, s in STREAM_STATS.items():
        all_net_evs.extend(s["net_ev_scores"])

    if all_net_evs:
        ev_arr = np.array(all_net_evs)
        logger.info(f"\n  NET EV SCORE DISTRIBUTION (passed signals):")
        logger.info(f"    Count: {len(ev_arr)}")
        logger.info(f"    Mean:  {np.mean(ev_arr):.3f}")
        logger.info(f"    Min:   {np.min(ev_arr):.3f}")
        logger.info(f"    Max:   {np.max(ev_arr):.3f}")
        for t in [0.80, 1.00, 1.20, 1.50, 2.00]:
            cnt = (ev_arr >= t).sum()
            logger.info(f"    Net_EV >= {t:.2f}: {cnt}")

    logger.info(f"\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
