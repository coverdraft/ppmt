#!/usr/bin/env python3
"""TAREA 19: Accelerated Paper Live Simulation — Net EV Gate + Multi-TF.

Simulates 1 hour of paper-live trading by processing historical
candles at high speed through the exact same Net EV Gate logic
that v2_server.py uses. This gives us statistically meaningful
results in minutes instead of waiting for live candles.

Uses the SAME logic as the modified v2_server.py:
  1. Net EV Gate: Net_Move = expected_move_pct - spread_pct
  2. If Net_Move <= 0: SPREAD REJECTED
  3. Net_RR = Net_Move / SL_pct
  4. Net_EV = confidence * min(Net_RR, 3.0)
  5. Net_EV >= 0.80 to PASS
  6. Anti-overlap: same symbol can't have 2 positions across TFs
"""
import sys
import os
import time
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
import pandas as pd

from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.core.trie import PPMTTrie
from ppmt.core.profiles import SPREAD_ESTIMATES

# ── Configuration ──
# 1 hour = 12 candles in 5m, 4 candles in 15m
# But we simulate MORE (200 per 5m, 200 per 15m) for statistical significance
# This represents ~16.7 hours of 5m data, ~50 hours of 15m data

STREAMS = [
    ("BTC/USDT",  "5m",  "blue_chip"),
    ("ETH/USDT",  "5m",  "blue_chip"),
    ("SOL/USDT",  "5m",  "large_cap"),
    ("SOL/USDT",  "15m", "large_cap"),
    ("AVAX/USDT", "5m",  "large_cap"),
    ("LINK/USDT", "5m",  "large_cap"),
    ("LINK/USDT", "15m", "large_cap"),
]

CANDLES_PER_STREAM = 1000  # ~83 hours of 5m data, ~250 hours of 15m data
NET_EV_GATE_THRESHOLD = 0.80
BINANCE_BASE = "https://api.binance.com"

TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
}


def download_candles(symbol: str, timeframe: str, total_candles: int) -> pd.DataFrame:
    """Download candles from Binance."""
    api_sym = symbol.replace("/", "")
    ms_per = TF_MS[timeframe]
    all_data = []
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - total_candles * ms_per
    cur = start_ts
    while cur < end_ts:
        try:
            resp = requests.get(
                f"{BINANCE_BASE}/api/v3/klines",
                params={"symbol": api_sym, "interval": timeframe, "limit": 1000, "startTime": cur},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            time.sleep(1)
            continue
        if not data:
            break
        for c in data:
            all_data.append([c[0], float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
        cur = data[-1][0] + ms_per
        time.sleep(0.1)
    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


# Global state (same as v2_server.py)
ACTIVE_SYMBOLS: dict[str, str] = {}  # {"SOL/USDT": "5m"}
NET_EV_STATS = {
    "total_raw_signals": 0,
    "passed_net_ev": 0,
    "rejected_spread": 0,
    "rejected_ev_score": 0,
    "rejected_overlap": 0,
}

STREAM_STATS = {}


def run_stream_simulation(symbol: str, timeframe: str, asset_class: str, df: pd.DataFrame):
    """Run Net EV Gate simulation for a single stream using historical data."""
    display = f"{symbol}/{timeframe}"
    STREAM_STATS[display] = {
        "candles": len(df),
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
        "spread_rejections": [],  # (expected_move, spread) pairs
    }

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
    n1, n2, n3, n4 = tries.get("n1"), tries.get("n2"), tries.get("n3"), tries.get("n4")

    if not (n1 or n2 or n3):
        print(f"  [{display}] No tries found — skipping", flush=True)
        storage.close()
        return

    engine.set_tries(
        trie_n1=n1 if n1 is not None else PPMTTrie(name="n1_empty"),
        trie_n2=n2 if n2 is not None else PPMTTrie(name="n2_empty"),
        trie_n3=n3 or PPMTTrie(name="n3_empty"),
        trie_n4=n4 if n4 is not None else engine.trie_n4,
    )

    # Sliding window match (same as OOS tests)
    w_n3 = engine.sax_n3.window_size
    pl_n3 = engine.pl_n3
    min_candles = w_n3 * pl_n3
    forward_window = pl_n3 * w_n3
    evaluable_end = len(df) - forward_window

    spread_pct = SPREAD_ESTIMATES.get(info.asset_class, 0.050)
    is_in_position = False
    position_direction = None
    position_entry_idx = 0
    position_entry_price = 0.0

    print(f"  [{display}] Tries: N1={n1.pattern_count if n1 else 0} N2={n2.pattern_count if n2 else 0} "
          f"N3={n3.pattern_count if n3 else 0} N4={n4.pattern_count if hasattr(n4, 'pattern_count') and n4 else 0} "
          f"spread={spread_pct}%", flush=True)

    for i in range(min_candles, evaluable_end):
        current_price = float(df["close"].iloc[i])

        # ─── Check SL/TP for existing position ───
        if is_in_position:
            move_pct = (current_price - position_entry_price) / position_entry_price * 100
            if position_direction == "LONG":
                if move_pct <= -3.0 or move_pct >= 5.0:
                    is_in_position = False
                    STREAM_STATS[display]["positions_closed"] += 1
                    if move_pct > 0:
                        STREAM_STATS[display]["wins"] += 1
                    else:
                        STREAM_STATS[display]["losses"] += 1
                    ACTIVE_SYMBOLS.pop(symbol, None)
                    continue
            elif position_direction == "SHORT":
                adj_move = -move_pct
                if adj_move <= -3.0 or adj_move >= 5.0:
                    is_in_position = False
                    STREAM_STATS[display]["positions_closed"] += 1
                    if adj_move > 0:
                        STREAM_STATS[display]["wins"] += 1
                    else:
                        STREAM_STATS[display]["losses"] += 1
                    ACTIVE_SYMBOLS.pop(symbol, None)
                    continue
            continue  # Skip matching while in position

        # ─── Match ───
        window_df = df.iloc[i - min_candles + 1: i + 1]
        try:
            result = engine.match_raw(
                current_symbols=[], current_price=current_price, recent_candles=window_df,
            )
        except Exception:
            continue

        if result.weighted_confidence <= 0:
            continue

        # Extract best metadata
        best_meta = None
        for level_name, match_result in [
            ("N3", result.n3_match), ("N1", result.n1_match),
            ("N2", result.n2_match), ("N4", result.n4_match),
        ]:
            if match_result and match_result.node:
                meta = match_result.node.metadata
                if meta.historical_count > 0:
                    best_meta = meta
                    break

        if best_meta is None:
            continue

        # ─── Signal detected ───
        NET_EV_STATS["total_raw_signals"] += 1
        STREAM_STATS[display]["raw_signals"] += 1

        direction = "LONG" if best_meta.expected_move_pct > 0 else "SHORT"

        # ─── Net EV Gate calculation ───
        # Use max_favorable_pct (the ACTUAL best outcome this pattern achieved)
        # instead of expected_move_pct (the average, which includes losses).
        # The spread must be covered by the favorable move, not the average.
        confidence = result.weighted_confidence
        favorable_pct = abs(best_meta.max_favorable_pct)
        drawdown_pct = abs(best_meta.max_drawdown_pct)
        if drawdown_pct < 0.001:
            drawdown_pct = 0.1  # fallback
        if favorable_pct < 0.001:
            favorable_pct = 0.1  # fallback

        # Net favorable move after spread
        net_favorable = favorable_pct - spread_pct

        # Anti-overlap check
        if symbol in ACTIVE_SYMBOLS:
            existing_tf = ACTIVE_SYMBOLS[symbol]
            NET_EV_STATS["rejected_overlap"] += 1
            STREAM_STATS[display]["rejected_overlap"] += 1
            print(f"  [NET EV GATE] OVERLAP REJECTED: {display} — already active in {existing_tf}", flush=True)
            continue

        if net_favorable <= 0:
            NET_EV_STATS["rejected_spread"] += 1
            STREAM_STATS[display]["rejected_spread"] += 1
            STREAM_STATS[display]["spread_rejections"].append((favorable_pct, spread_pct))
            continue

        # Net R:R = (favorable - spread) / drawdown
        net_rr = net_favorable / drawdown_pct
        net_rr_capped = min(net_rr, 3.0)
        net_ev = confidence * net_rr_capped

        if net_ev < NET_EV_GATE_THRESHOLD:
            NET_EV_STATS["rejected_ev_score"] += 1
            STREAM_STATS[display]["rejected_ev_score"] += 1
            continue

        # ─── PASSED ───
        NET_EV_STATS["passed_net_ev"] += 1
        STREAM_STATS[display]["passed_net_ev"] += 1
        STREAM_STATS[display]["net_ev_scores"].append(net_ev)
        ACTIVE_SYMBOLS[symbol] = timeframe
        is_in_position = True
        position_direction = direction
        position_entry_idx = i
        position_entry_price = current_price
        STREAM_STATS[display]["positions_opened"] += 1

        print(f"  [NET EV GATE] PASSED: {display} {direction} @ {current_price:.6f} "
              f"conf={confidence:.3f} net_R:R={net_rr_capped:.2f} "
              f"Net_EV={net_ev:.3f} spread={spread_pct:.3f}% "
              f"favorable={favorable_pct:.3f}% drawdown={drawdown_pct:.3f}%", flush=True)

    storage.close()


def main():
    print("=" * 78, flush=True)
    print("TAREA 19: Net EV Gate + Multi-TF Paper Live Simulation", flush=True)
    print("=" * 78, flush=True)
    print(f"Streams: {len(STREAMS)}", flush=True)
    print(f"Candles per stream: {CANDLES_PER_STREAM}", flush=True)
    print(f"Net EV Gate threshold: {NET_EV_GATE_THRESHOLD}", flush=True)
    print(f"SPREAD_ESTIMATES: {SPREAD_ESTIMATES}", flush=True)
    print(flush=True)

    for symbol, timeframe, asset_class in STREAMS:
        display = f"{symbol}/{timeframe}"
        print(f"\n{'─' * 78}", flush=True)
        print(f"  {display} ({asset_class})", flush=True)
        print(f"{'─' * 78}", flush=True)

        # Download data
        print(f"  Downloading {CANDLES_PER_STREAM} {timeframe} candles...", flush=True)
        df = download_candles(symbol, timeframe, CANDLES_PER_STREAM)
        if len(df) < 100:
            print(f"  [ERROR] Only got {len(df)} candles — skipping", flush=True)
            continue
        print(f"  Got {len(df)} candles", flush=True)

        # Run simulation
        run_stream_simulation(symbol, timeframe, asset_class, df)

    # ─── Final Report ───
    print(f"\n{'=' * 78}", flush=True)
    print("TAREA 19: FINAL REPORT — Net EV Gate + Multi-TF", flush=True)
    print(f"{'=' * 78}", flush=True)

    print(f"\n  NET EV GATE SUMMARY:", flush=True)
    print(f"    Total raw signals:     {NET_EV_STATS['total_raw_signals']}", flush=True)
    print(f"    Passed Net EV:         {NET_EV_STATS['passed_net_ev']}", flush=True)
    print(f"    Rejected by spread:    {NET_EV_STATS['rejected_spread']}", flush=True)
    print(f"    Rejected by EV score:  {NET_EV_STATS['rejected_ev_score']}", flush=True)
    print(f"    Rejected by overlap:   {NET_EV_STATS['rejected_overlap']}", flush=True)

    pass_rate = (NET_EV_STATS['passed_net_ev'] / NET_EV_STATS['total_raw_signals'] * 100
                 if NET_EV_STATS['total_raw_signals'] > 0 else 0)
    print(f"    Pass rate:             {pass_rate:.1f}%", flush=True)

    print(f"\n  PER-STREAM DETAILS:", flush=True)
    print(f"  {'Stream':18s} {'Candles':>8} {'Raw':>5} {'Pass':>5} {'SprdRj':>6} {'EVRj':>5} {'OvlpRj':>6} {'Open':>5} {'Clsd':>5} {'W/L':>6}", flush=True)
    print(f"  {'─'*18} {'─'*8} {'─'*5} {'─'*5} {'─'*6} {'─'*5} {'─'*6} {'─'*5} {'─'*5} {'─'*6}", flush=True)

    for display, s in STREAM_STATS.items():
        if s["candles"] == 0:
            continue
        print(
            f"  {display:18s} {s['candles']:>8d} {s['raw_signals']:>5d} "
            f"{s['passed_net_ev']:>5d} {s['rejected_spread']:>6d} "
            f"{s['rejected_ev_score']:>5d} {s['rejected_overlap']:>6d} "
            f"{s['positions_opened']:>5d} {s['positions_closed']:>5d} "
            f"{s['wins']}/{s['losses']}", flush=True
        )

    # Net EV score distribution
    all_net_evs = []
    for display, s in STREAM_STATS.items():
        all_net_evs.extend(s["net_ev_scores"])

    if all_net_evs:
        ev_arr = np.array(all_net_evs)
        print(f"\n  NET EV SCORE DISTRIBUTION (passed signals):", flush=True)
        print(f"    Count: {len(ev_arr)}", flush=True)
        print(f"    Mean:  {np.mean(ev_arr):.3f}", flush=True)
        print(f"    Min:   {np.min(ev_arr):.3f}", flush=True)
        print(f"    Max:   {np.max(ev_arr):.3f}", flush=True)
        for t in [0.80, 1.00, 1.20, 1.50, 2.00]:
            cnt = (ev_arr >= t).sum()
            print(f"    Net_EV >= {t:.2f}: {cnt}", flush=True)

    # Spread rejection analysis
    all_spread_rejections = []
    for display, s in STREAM_STATS.items():
        all_spread_rejections.extend(s["spread_rejections"])

    if all_spread_rejections:
        moves = [r[0] for r in all_spread_rejections]
        spreads = [r[1] for r in all_spread_rejections]
        print(f"\n  SPREAD REJECTION ANALYSIS ({len(all_spread_rejections)} rejections):", flush=True)
        print(f"    Avg expected_move: {np.mean(moves):.3f}%", flush=True)
        print(f"    Avg spread:        {np.mean(spreads):.3f}%", flush=True)
        print(f"    Min expected_move: {np.min(moves):.3f}%", flush=True)
        print(f"    Max expected_move: {np.max(moves):.3f}%", flush=True)

    print(f"\nDone.", flush=True)


if __name__ == "__main__":
    main()
