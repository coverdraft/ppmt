#!/usr/bin/env python3
"""
PPMT EV Gate Diagnostic — Why does the EV gate reject 99% of signals?

Captures per-signal EV breakdown (confidence, favorable_pct, drawdown_pct,
net_rr, net_rr_capped, net_ev) for ALL raw signals, then runs the same
replay with EV threshold = 0.40 to compare.

Reports:
  1. Average EV of rejected signals (near 0.80 or far?)
  2. Average net_rr_capped of rejected signals (capping issue?)
  3. Confidence distribution for DOGE's 211 raw signals
  4. Same replay with EV threshold = 0.40
  5. Context on the "6000 operations" claim

Usage:
    python scripts/ev_gate_diagnostic.py
    python scripts/ev_gate_diagnostic.py --tokens BTC/USDT DOGE/USDT
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# Ensure ppmt is importable
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_dir = os.path.join(_repo_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import numpy as np
import pandas as pd

from ppmt.data.classifier import AssetClassifier
from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.realtime import _DirectPollExchange
from ppmt.terminal.paper_executor import PaperExecutor
from ppmt.core.profiles import SPREAD_ESTIMATES
from ppmt.core.trie import PPMTTrie

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ev_diagnostic")
logger.setLevel(logging.INFO)

# ─── Configuration ──────────────────────────────────────────────

DEFAULT_TOKENS = ["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"]
TIMEFRAME = "5m"
CAPITAL_USDT = 1000.0
RISK_PCT = 0.01
OOS_DAYS = 7

EV_THRESHOLDS = [0.80, 0.40]  # Run both for comparison


# ─── Per-Signal Record ─────────────────────────────────────────

@dataclass
class SignalRecord:
    """Complete EV breakdown for a single raw signal."""
    symbol: str = ""
    confidence: float = 0.0
    favorable_pct: float = 0.0      # max_favorable_pct from metadata
    drawdown_pct: float = 0.0       # max_drawdown_pct from metadata
    spread_pct: float = 0.0
    net_favorable: float = 0.0
    net_rr: float = 0.0             # raw net R:R
    net_rr_capped: float = 0.0      # min(net_rr, 3.0)
    net_ev: float = 0.0             # confidence × net_rr_capped
    rejection_reason: str = ""      # "spread", "ev_score", "overlap", or "PASSED"
    best_node_level: str = ""       # Which trie level provided the node
    historical_count: int = 0
    expected_move_pct: float = 0.0
    signal_type: str = ""
    direction: str = ""
    # What confidence would be needed for this signal to pass EV=0.80
    required_confidence_08: float = 0.0
    # What net_rr would be needed for this signal to pass EV=0.80
    required_net_rr_08: float = 0.0


@dataclass
class ReplayResult:
    """Result of a single replay run."""
    symbol: str = ""
    ev_threshold: float = 0.80
    total_candles: int = 0
    signals: list[SignalRecord] = field(default_factory=list)
    trades_total: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    all_pnl: list[float] = field(default_factory=list)
    elapsed_seconds: float = 0.0


# ─── Data Download ──────────────────────────────────────────────

def download_data(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Download OHLCV from Binance via HTTP (synchronous)."""
    import requests
    api_symbol = symbol.replace("/", "")
    base_url = "https://api.binance.com"

    all_data = []
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 24 * 3600 * 1000)
    current_end = end_ms
    total_fetched = 0
    target = days * 288 + 500  # buffer

    while total_fetched < target:
        batch_size = 1000
        start_batch = current_end - (batch_size * 300000)

        url = f"{base_url}/api/v3/klines"
        params = {
            "symbol": api_symbol,
            "interval": timeframe,
            "limit": batch_size,
            "startTime": max(start_batch, start_ms),
            "endTime": current_end,
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            if not data:
                break
            parsed = [
                [int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])]
                for c in data
            ]
            all_data.extend(parsed)
            total_fetched += len(parsed)
            current_end = int(data[0][0]) - 1
            if len(parsed) < batch_size:
                break
        except Exception as e:
            logger.warning(f"Download error for {symbol}: {e}")
            break

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    df.sort_index(inplace=True)
    return df


# ─── Replay with Signal Capture ────────────────────────────────

def run_diagnostic_replay(
    symbol: str,
    oos_df: pd.DataFrame,
    storage: PPMTStorage,
    asset_class: str,
    weight_profile: str,
    timeframe: str,
    ev_threshold: float,
) -> ReplayResult:
    """
    Run OOS replay capturing per-signal EV breakdown.
    """
    result = ReplayResult(symbol=symbol, ev_threshold=ev_threshold)
    t0 = time.time()

    # Load tries
    tries = storage.load_all_tries(symbol, asset_class, timeframe=timeframe)
    n1_trie = tries.get("n1")
    n2_trie = tries.get("n2")
    n3_trie = tries.get("n3")
    n4_trie = tries.get("n4")

    logger.info(
        f"  {symbol} EV={ev_threshold}: Tries N1={n1_trie.pattern_count if n1_trie else 0} "
        f"N2={n2_trie.pattern_count if n2_trie else 0} "
        f"N3={n3_trie.pattern_count if n3_trie else 0} "
        f"N4={n4_trie.pattern_count if n4_trie else 0}"
    )

    if not n1_trie and not n2_trie and not n3_trie:
        logger.error(f"  No tries for {symbol}!")
        return result

    # Create engine
    engine = PPMT(
        symbol=symbol,
        asset_class=asset_class,
        weight_profile=weight_profile,
        dual_sax=True,
        min_confidence=0.08,
        timeframe=timeframe,
    )
    engine.set_tries(
        trie_n1=n1_trie if n1_trie else PPMTTrie(name="empty_n1"),
        trie_n2=n2_trie if n2_trie else PPMTTrie(name="empty_n2"),
        trie_n3=n3_trie if n3_trie else PPMTTrie(name="empty_n3"),
        trie_n4=n4_trie if n4_trie else engine.trie_n4,
    )

    # Create executor
    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None

    # Spread estimate
    spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)

    # Replay loop
    result.total_candles = len(oos_df)
    _last_engine_ts = 0

    for idx in range(len(oos_df)):
        row = oos_df.iloc[[idx]]
        current_price = float(row["close"].iloc[0])
        candle_high = float(row["high"].iloc[0])
        candle_low = float(row["low"].iloc[0])
        ts = oos_df.index[idx]
        ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)

        # Check SL/TP
        if executor.is_in_position:
            pos = executor.position
            closed = None
            if pos.direction == "LONG":
                if candle_low <= pos.catastrophic_sl:
                    closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                elif candle_low <= pos.current_sl:
                    closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                elif candle_high >= pos.current_tp:
                    closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
            else:
                if candle_high >= pos.catastrophic_sl:
                    closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                elif candle_high >= pos.current_sl:
                    closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                elif candle_low <= pos.current_tp:
                    closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
            if closed:
                pnl = closed.pnl_pct or 0.0
                result.all_pnl.append(pnl)
                result.trades_total += 1
                if pnl > 0.01:
                    result.trades_won += 1
                elif pnl < -0.01:
                    result.trades_lost += 1
                executor._position = None

        # Feed candle to engine
        engine_result: Optional[PPMTResult] = None
        if ts_sec > _last_engine_ts:
            _last_engine_ts = ts_sec
            engine_result = engine.process_new_candle(
                candle_df=row,
                current_price=current_price,
                is_in_position=executor.is_in_position,
                entry_price=executor.position.entry_price if executor.position else None,
            )

        if engine_result is None:
            continue

        sig = engine_result.signal if engine_result and engine_result.signal else None
        if sig is None or not sig.is_entry:
            continue
        if executor.is_in_position:
            continue

        # ─── Capture EV breakdown ──────────────────────────────
        _best_node = None
        _best_level = ""
        for _lvl, _mr in [("n3", engine_result.n3_match), ("n1", engine_result.n1_match),
                          ("n2", engine_result.n2_match), ("n4", engine_result.n4_match)]:
            if _mr and _mr.node and _mr.node.metadata and _mr.node.metadata.historical_count > 0:
                _best_node = _mr.node
                _best_level = _lvl
                break

        favorable_pct = abs(_best_node.metadata.max_favorable_pct) if _best_node else 0.0
        drawdown_pct = abs(_best_node.metadata.max_drawdown_pct) if _best_node else 0.5
        hist_count = _best_node.metadata.historical_count if _best_node else 0

        if favorable_pct < 0.001:
            favorable_pct = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.1
        if drawdown_pct < 0.001:
            drawdown_pct = 0.5

        net_favorable = favorable_pct - spread_pct

        # Compute net_rr even if spread-rejected (for diagnostic)
        net_rr = net_favorable / drawdown_pct if drawdown_pct > 0 else 0.0
        net_rr_capped = min(net_rr, 3.0)
        net_ev = sig.confidence * net_rr_capped

        # Required values to pass EV=0.80
        # net_ev = conf * net_rr_capped >= 0.80
        # If net_rr_capped > 0: required_conf = 0.80 / net_rr_capped
        # If confidence > 0: required_net_rr = 0.80 / confidence
        req_conf = (0.80 / net_rr_capped) if net_rr_capped > 0 else float('inf')
        req_rr = (0.80 / sig.confidence) if sig.confidence > 0 else float('inf')

        # Determine rejection reason
        rejection_reason = ""
        if net_favorable <= 0:
            rejection_reason = "spread"
        elif net_ev < ev_threshold:
            rejection_reason = "ev_score"
        else:
            rejection_reason = "PASSED"

        rec = SignalRecord(
            symbol=symbol,
            confidence=sig.confidence,
            favorable_pct=favorable_pct,
            drawdown_pct=drawdown_pct,
            spread_pct=spread_pct,
            net_favorable=net_favorable,
            net_rr=net_rr,
            net_rr_capped=net_rr_capped,
            net_ev=net_ev,
            rejection_reason=rejection_reason,
            best_node_level=_best_level,
            historical_count=hist_count,
            expected_move_pct=sig.expected_move_pct or 0.0,
            signal_type=sig.signal_type.value,
            direction=sig.direction or "",
            required_confidence_08=req_conf,
            required_net_rr_08=req_rr,
        )
        result.signals.append(rec)

        # ─── Apply EV Gate ────────────────────────────────────
        if net_favorable <= 0:
            continue  # spread rejected
        if net_ev < ev_threshold:
            continue  # EV rejected

        # PASSED — open position
        direction = sig.direction or "LONG"
        size_usdt = CAPITAL_USDT * RISK_PCT / (abs(sig.expected_move_pct or 1.0) * 0.012)
        size_usdt = min(size_usdt, CAPITAL_USDT)

        try:
            pos = executor.open_position_sync(
                symbol=symbol,
                direction=direction,
                entry_price=current_price,
                expected_move_pct=sig.expected_move_pct or 1.0,
                predicted_path_symbols=sig.predicted_path_symbols if sig.predicted_path else None,
                size_usdt=size_usdt,
            )
        except RuntimeError:
            continue

        # Check SL/TP on entry candle
        if executor.is_in_position:
            entry_closed = None
            if pos.direction == "LONG":
                if candle_low <= pos.catastrophic_sl:
                    entry_closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                elif candle_low <= pos.current_sl:
                    entry_closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                elif candle_high >= pos.current_tp:
                    entry_closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
            else:
                if candle_high >= pos.catastrophic_sl:
                    entry_closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                elif candle_high >= pos.current_sl:
                    entry_closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                elif candle_low <= pos.current_tp:
                    entry_closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
            if entry_closed:
                pnl = entry_closed.pnl_pct or 0.0
                result.all_pnl.append(pnl)
                result.trades_total += 1
                if pnl > 0.01:
                    result.trades_won += 1
                elif pnl < -0.01:
                    result.trades_lost += 1
                executor._position = None

        # Walk-forward check
        if engine_result and executor.is_in_position:
            current_sax = []
            buf = getattr(engine, '_streaming_buffer', None)
            if buf and buf._pattern_buffer:
                last_sym = buf._pattern_buffer[-1]
                if isinstance(last_sym, (tuple, list)):
                    current_sax = [str(s) for s in last_sym]
                else:
                    current_sax = [str(last_sym)]
            if current_sax:
                executor.check_walk_forward(current_sax, current_price)

    # Close remaining position
    if executor.is_in_position and executor._position:
        last_price = float(oos_df["close"].iloc[-1])
        closed = executor.force_close(last_price, "REPLAY_END")
        pnl = closed.pnl_pct or 0.0
        result.all_pnl.append(pnl)
        result.trades_total += 1
        if pnl > 0.01:
            result.trades_won += 1
        elif pnl < -0.01:
            result.trades_lost += 1

    result.elapsed_seconds = time.time() - t0
    return result


# ─── Analysis ───────────────────────────────────────────────────

def analyze_results(all_results: dict[tuple[str, float], ReplayResult]):
    """Print comprehensive EV gate diagnostic."""

    print("\n" + "=" * 90)
    print("  PPMT EV GATE DIAGNOSTIC — WHY 99% REJECTION RATE?")
    print("=" * 90)

    symbols = sorted(set(k[0] for k in all_results.keys()))

    # ═══════════════════════════════════════════════════════════
    # PART 1: EV=0.80 Analysis — Answer questions 1-3
    # ═══════════════════════════════════════════════════════════

    for symbol in symbols:
        key = (symbol, 0.80)
        res = all_results.get(key)
        if not res or not res.signals:
            print(f"\n  {symbol}: No signals captured — SKIP")
            continue

        sigs = res.signals
        rejected = [s for s in sigs if s.rejection_reason != "PASSED"]
        passed = [s for s in sigs if s.rejection_reason == "PASSED"]
        spread_rej = [s for s in sigs if s.rejection_reason == "spread"]
        ev_rej = [s for s in sigs if s.rejection_reason == "ev_score"]

        print(f"\n{'═'*90}")
        print(f"  {symbol} — EV Threshold = 0.80")
        print(f"{'═'*90}")
        print(f"  Raw signals: {len(sigs)} | Passed: {len(passed)} | Rejected: {len(rejected)} ({len(rejected)/len(sigs)*100:.1f}%)")
        print(f"  Rejection breakdown: Spread={len(spread_rej)} | EV score={len(ev_rej)}")

        # ─── Q1: Average EV of rejected signals ────────────
        if ev_rej:
            evs = [s.net_ev for s in ev_rej]
            print(f"\n  ┌── Q1: EV of rejected signals (EV score rejection only) ──┐")
            print(f"  │  Count:          {len(ev_rej)}")
            print(f"  │  Mean EV:        {np.mean(evs):.4f}")
            print(f"  │  Median EV:      {np.median(evs):.4f}")
            print(f"  │  Std EV:         {np.std(evs):.4f}")
            print(f"  │  Min EV:         {np.min(evs):.4f}")
            print(f"  │  Max EV:         {np.max(evs):.4f}")
            # How many are "close" (0.60-0.79)?
            close = sum(1 for e in evs if 0.60 <= e < 0.80)
            mid = sum(1 for e in evs if 0.30 <= e < 0.60)
            far = sum(1 for e in evs if e < 0.30)
            print(f"  │  EV in [0.60, 0.80): {close} ({close/len(ev_rej)*100:.1f}%)  ← near threshold")
            print(f"  │  EV in [0.30, 0.60): {mid} ({mid/len(ev_rej)*100:.1f}%)  ← mid-range")
            print(f"  │  EV in [0.00, 0.30): {far} ({far/len(ev_rej)*100:.1f}%)  ← far below")
            print(f"  └{'─'*60}┘")
        elif spread_rej:
            print(f"\n  Q1: All rejected signals were spread-rejected (net_favorable ≤ 0)")
            print(f"       EV was never even computed — the move doesn't cover spread!")

        # ─── Q2: Average net_rr_capped of rejected signals ──
        if rejected:
            rrs = [s.net_rr_capped for s in rejected]
            raw_rrs = [s.net_rr for s in rejected]
            print(f"\n  ┌── Q2: Net R:R of rejected signals ──────────────────────┐")
            print(f"  │  Mean net_rr_capped:  {np.mean(rrs):.4f}")
            print(f"  │  Median net_rr_capped: {np.median(rrs):.4f}")
            print(f"  │  Mean net_rr (raw):   {np.mean(raw_rrs):.4f}")
            print(f"  │  Median net_rr (raw): {np.median(raw_rrs):.4f}")
            capped_count = sum(1 for r in raw_rrs if r > 3.0)
            print(f"  │  Signals with raw RR > 3.0 (capped): {capped_count}/{len(rejected)}")
            # R:R buckets
            rr_low = sum(1 for r in rrs if r < 0.5)
            rr_mid = sum(1 for r in rrs if 0.5 <= r < 1.0)
            rr_ok = sum(1 for r in rrs if 1.0 <= r < 2.0)
            rr_good = sum(1 for r in rrs if r >= 2.0)
            print(f"  │  RR < 0.5:       {rr_low} ({rr_low/len(rejected)*100:.1f}%)  ← terrible")
            print(f"  │  RR [0.5, 1.0):  {rr_mid} ({rr_mid/len(rejected)*100:.1f}%)  ← unfavorable")
            print(f"  │  RR [1.0, 2.0):  {rr_ok} ({rr_ok/len(rejected)*100:.1f}%)  ← acceptable")
            print(f"  │  RR >= 2.0:      {rr_good} ({rr_good/len(rejected)*100:.1f}%)  ← good")
            print(f"  └{'─'*60}┘")

            # Decompose: what's the favorable and drawdown separately?
            favs = [s.favorable_pct for s in rejected]
            dd = [s.drawdown_pct for s in rejected]
            print(f"\n  ┌── Metadata breakdown (rejected signals) ───────────────┐")
            print(f"  │  Mean favorable_pct:  {np.mean(favs):.4f}%  (max favorable move)")
            print(f"  │  Mean drawdown_pct:   {np.mean(dd):.4f}%  (max drawdown)")
            print(f"  │  Mean spread_pct:     {rejected[0].spread_pct:.4f}%")
            print(f"  │  Mean net_favorable:  {np.mean([s.net_favorable for s in rejected]):.4f}%")
            print(f"  │  Ratio fav/dd:        {np.mean(favs)/np.mean(dd):.4f}  (needs > 1.0 for positive R:R)")
            print(f"  └{'─'*60}┘")

        # ─── Q3: Confidence distribution ────────────────────
        confs = [s.confidence for s in sigs]
        print(f"\n  ┌── Q3: Confidence distribution (ALL {len(sigs)} raw signals) ──┐")
        print(f"  │  Mean confidence:     {np.mean(confs):.4f}")
        print(f"  │  Median confidence:   {np.median(confs):.4f}")
        print(f"  │  Std confidence:      {np.std(confs):.4f}")
        print(f"  │  Min / Max:           [{np.min(confs):.4f} — {np.max(confs):.4f}]")
        # Histogram buckets
        buckets = [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5),
                   (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
        print(f"  │  Distribution:")
        for lo, hi in buckets:
            cnt = sum(1 for c in confs if lo <= c < hi)
            bar = "█" * int(cnt / max(len(confs) * 0.05, 1))
            label = f"[{lo:.1f}, {hi:.1f})"
            print(f"  │    {label:>12}: {cnt:>4} ({cnt/len(confs)*100:>5.1f}%) {bar}")
        print(f"  └{'─'*60}┘")

        # ─── What would each rejected signal need to pass? ────
        if ev_rej:
            print(f"\n  ┌── What would rejected signals need to pass EV=0.80? ──┐")
            avg_conf = np.mean([s.confidence for s in ev_rej])
            avg_rr = np.mean([s.net_rr_capped for s in ev_rej])
            avg_req_conf = np.mean([s.required_confidence_08 for s in ev_rej if s.required_confidence_08 < 10])
            avg_req_rr = np.mean([s.required_net_rr_08 for s in ev_rej if s.required_net_rr_08 < 10])
            print(f"  │  Average confidence:       {avg_conf:.4f}")
            print(f"  │  Required confidence:      {avg_req_conf:.4f}  (gap: {avg_req_conf - avg_conf:.4f})")
            print(f"  │  Average net_rr_capped:    {avg_rr:.4f}")
            print(f"  │  Required net_rr:          {avg_req_rr:.4f}  (gap: {avg_req_rr - avg_rr:.4f})")
            print(f"  └{'─'*60}┘")

    # ═══════════════════════════════════════════════════════════
    # PART 2: Compare EV=0.80 vs EV=0.40 — Answer Q4
    # ═══════════════════════════════════════════════════════════

    print(f"\n\n{'═'*90}")
    print(f"  Q4: EV=0.80 vs EV=0.40 COMPARISON")
    print(f"{'═'*90}")

    print(f"\n  {'Token':<14} {'EV Gate':>8} {'Raw Sig':>8} {'Passed':>8} {'Trades':>8} {'WR%':>7} {'P&L%':>9} {'Avg Conf':>9}")
    print(f"  {'─'*14} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*9} {'─'*9}")

    for symbol in symbols:
        for ev_thresh in [0.80, 0.40]:
            key = (symbol, ev_thresh)
            res = all_results.get(key)
            if not res:
                continue

            sigs = res.signals
            passed = [s for s in sigs if s.rejection_reason == "PASSED"]
            total_pnl = sum(res.all_pnl) if res.all_pnl else 0.0
            wr = (res.trades_won / res.trades_total * 100) if res.trades_total > 0 else 0.0
            avg_conf = np.mean([s.confidence for s in passed]) if passed else 0.0

            print(
                f"  {symbol:<14} "
                f"EV={ev_thresh:.2f} "
                f"{len(sigs):>8} "
                f"{len(passed):>8} "
                f"{res.trades_total:>8} "
                f"{wr:>6.1f}% "
                f"{total_pnl:>+8.2f}% "
                f"{avg_conf:>8.4f}"
            )
        print(f"  {'─'*14} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*9} {'─'*9}")

    # Detailed EV=0.40 analysis
    print(f"\n  ┌── EV=0.40 Detail ──────────────────────────────────────┐")
    for symbol in symbols:
        key = (symbol, 0.40)
        res = all_results.get(key)
        if not res or not res.signals:
            continue

        sigs = res.signals
        passed_040 = [s for s in sigs if s.rejection_reason == "PASSED"]
        rejected_040 = [s for s in sigs if s.rejection_reason != "PASSED"]

        if passed_040:
            evs = [s.net_ev for s in passed_040]
            print(f"  │  {symbol}: {len(passed_040)} passed, avg EV={np.mean(evs):.3f}, "
                  f"range [{np.min(evs):.3f}—{np.max(evs):.3f}]")
        else:
            # How close are they?
            ev_rej = [s for s in sigs if s.rejection_reason == "ev_score"]
            if ev_rej:
                evs = [s.net_ev for s in ev_rej]
                print(f"  │  {symbol}: 0 passed. Closest EV={np.max(evs):.3f}, avg={np.mean(evs):.3f}")
            else:
                print(f"  │  {symbol}: 0 passed. All spread-rejected.")
    print(f"  └{'─'*60}┘")

    # ═══════════════════════════════════════════════════════════
    # PART 3: Q5 — The 6000 operations context
    # ═══════════════════════════════════════════════════════════

    print(f"\n\n{'═'*90}")
    print(f"  Q5: THE '6000 OPERATIONS' CONTEXT")
    print(f"{'═'*90}")

    # Aggregate all signals across all tokens at EV=0.80
    all_sigs_080 = []
    for symbol in symbols:
        key = (symbol, 0.80)
        res = all_results.get(key)
        if res:
            all_sigs_080.extend(res.signals)

    if all_sigs_080:
        passed_080 = [s for s in all_sigs_080 if s.rejection_reason == "PASSED"]
        spread_080 = [s for s in all_sigs_080 if s.rejection_reason == "spread"]
        ev_rej_080 = [s for s in all_sigs_080 if s.rejection_reason == "ev_score"]

        print(f"\n  Current replay (4 tokens, 7 days OOS, EV=0.80):")
        print(f"    Total raw signals:     {len(all_sigs_080)}")
        print(f"    Passed EV gate:        {len(passed_080)} ({len(passed_080)/len(all_sigs_080)*100:.1f}%)")
        print(f"    Rejected by spread:    {len(spread_080)} ({len(spread_080)/len(all_sigs_080)*100:.1f}%)")
        print(f"    Rejected by EV score:  {len(ev_rej_080)} ({len(ev_rej_080)/len(all_sigs_080)*100:.1f}%)")

    # At EV=0.40
    all_sigs_040 = []
    for symbol in symbols:
        key = (symbol, 0.40)
        res = all_results.get(key)
        if res:
            all_sigs_040.extend(res.signals)

    if all_sigs_040:
        passed_040 = [s for s in all_sigs_040 if s.rejection_reason == "PASSED"]
        print(f"\n  Same replay with EV=0.40:")
        print(f"    Total raw signals:     {len(all_sigs_040)}")
        print(f"    Passed EV gate:        {len(passed_040)} ({len(passed_040)/len(all_sigs_040)*100:.1f}%)")

    print(f"\n  Note on '6000 operations':")
    print(f"    The 6000 figure likely comes from bulk_build across ALL ~173 tokens")
    print(f"    with ~90 days of data each. This replay only tests 4 tokens over 7 days OOS.")
    print(f"    173 tokens × 90 days × 288 candles/day ≈ 4.5M candles processed during build.")
    print(f"    The build counts PATTERN INSERTIONS into the trie, not trading signals.")
    print(f"    Pattern insertions ≠ tradeable signals — most patterns are too weak to pass EV gate.")

    # ═══════════════════════════════════════════════════════════
    # PART 4: Root Cause Diagnosis
    # ═══════════════════════════════════════════════════════════

    print(f"\n\n{'═'*90}")
    print(f"  ROOT CAUSE DIAGNOSIS")
    print(f"{'═'*90}")

    # Collect aggregate stats
    all_confs = [s.confidence for s in all_sigs_080] if all_sigs_080 else []
    all_rrs = [s.net_rr_capped for s in all_sigs_080] if all_sigs_080 else []
    all_favs = [s.favorable_pct for s in all_sigs_080] if all_sigs_080 else []
    all_dds = [s.drawdown_pct for s in all_sigs_080] if all_sigs_080 else []

    if all_confs:
        print(f"\n  A) CONFIDENCE (α-based discrimination):")
        print(f"     Mean: {np.mean(all_confs):.4f} | Median: {np.median(all_confs):.4f}")
        low_conf = sum(1 for c in all_confs if c < 0.3)
        print(f"     Signals with confidence < 0.30: {low_conf}/{len(all_confs)} ({low_conf/len(all_confs)*100:.1f}%)")
        if low_conf > len(all_confs) * 0.5:
            print(f"     → PROBLEM: Majority of signals have low confidence. α=3 doesn't discriminate.")
            print(f"       Consider: α=5 or α=7 for more SAX symbols → more specific patterns → higher confidence")
        else:
            print(f"     → Confidence is not the primary bottleneck.")

        print(f"\n  B) R:R (Metadata predictiveness):")
        print(f"     Mean favorable: {np.mean(all_favs):.4f}% | Mean drawdown: {np.mean(all_dds):.4f}%")
        print(f"     Mean net_rr_capped: {np.mean(all_rrs):.4f}")
        bad_rr = sum(1 for r in all_rrs if r < 1.0)
        print(f"     Signals with net_rr < 1.0: {bad_rr}/{len(all_rrs)} ({bad_rr/len(all_rrs)*100:.1f}%)")
        if bad_rr > len(all_rrs) * 0.5:
            print(f"     → PROBLEM: Most patterns have unfavorable R:R (drawdown exceeds favorable move).")
            print(f"       The metadata's max_favorable and max_drawdown don't produce tradeable R:R.")
        else:
            print(f"     → R:R is not the primary bottleneck.")

        print(f"\n  C) EV THRESHOLD (0.80 too strict?):")
        # What % would pass at different thresholds?
        for thresh in [0.20, 0.30, 0.40, 0.50, 0.60, 0.80]:
            count = sum(1 for s in all_sigs_080 if s.rejection_reason != "spread" and s.net_ev >= thresh)
            pct = count / len(all_sigs_080) * 100 if all_sigs_080 else 0
            print(f"     EV >= {thresh:.2f}: {count:>4} signals ({pct:.1f}%)")

        # Verdict
        print(f"\n  D) COMBINED VERDICT:")
        avg_ev_rejected = np.mean([s.net_ev for s in all_sigs_080 if s.rejection_reason == "ev_score"]) if ev_rej_080 else 0
        spread_pct_total = sum(1 for s in all_sigs_080 if s.rejection_reason == "spread") / len(all_sigs_080) * 100 if all_sigs_080 else 0

        if spread_pct_total > 30:
            print(f"     → PRIMARY: {spread_pct_total:.0f}% of signals are SPREAD-REJECTED")
            print(f"       (favorable move doesn't even cover trading costs)")
            print(f"       This means the metadata's max_favorable_pct is too small for real trading.")

        if avg_ev_rejected < 0.20:
            print(f"     → SECONDARY: Average EV of rejected signals = {avg_ev_rejected:.3f}")
            print(f"       Way below 0.80 — even lowering threshold won't help much.")
            print(f"       Root cause: confidence × net_rr is fundamentally low.")

        if np.mean(all_confs) < 0.3:
            print(f"     → CONTRIBUTING: Low average confidence ({np.mean(all_confs):.3f})")
            print(f"       α=3 SAX creates only 3 symbols → patterns are too generic")
            print(f"       All 3-symbol patterns look the same → confidence stays low")

    print(f"\n{'═'*90}\n")


# ─── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PPMT EV Gate Diagnostic")
    parser.add_argument("--tokens", nargs="+", default=DEFAULT_TOKENS)
    parser.add_argument("--oos-days", type=int, default=OOS_DAYS)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()

    storage = PPMTStorage()
    classifier = AssetClassifier()

    # Download data if needed
    data_cache: dict[str, pd.DataFrame] = {}

    for symbol in args.tokens:
        if args.skip_download:
            df = storage.load_ohlcv(symbol, TIMEFRAME)
        else:
            logger.info(f"Downloading {symbol}...")
            df = download_data(symbol, TIMEFRAME, args.oos_days + 83)
            if df is not None and len(df) > 100:
                storage.save_ohlcv(symbol, TIMEFRAME, df)

        if df is None or len(df) < 100:
            logger.error(f"Insufficient data for {symbol}")
            continue

        data_cache[symbol] = df
        logger.info(f"  {symbol}: {len(df)} candles loaded")

    # Run replays at both EV thresholds
    all_results: dict[tuple[str, float], ReplayResult] = {}

    for symbol in args.tokens:
        if symbol not in data_cache:
            continue

        info = classifier.classify(symbol)
        df = data_cache[symbol]

        # Split IS/OOS
        oos_start = df.index[-1] - pd.Timedelta(days=args.oos_days)
        oos_df = df[df.index >= oos_start]

        if len(oos_df) < 10:
            logger.warning(f"OOS data too short for {symbol}")
            continue

        logger.info(f"  {symbol}: OOS = {len(oos_df)} candles ({oos_df.index[0]} → {oos_df.index[-1]})")

        for ev_thresh in EV_THRESHOLDS:
            logger.info(f"  Running {symbol} with EV={ev_thresh}...")
            result = run_diagnostic_replay(
                symbol=symbol,
                oos_df=oos_df,
                storage=storage,
                asset_class=info.asset_class,
                weight_profile=info.weight_profile,
                timeframe=TIMEFRAME,
                ev_threshold=ev_thresh,
            )
            all_results[(symbol, ev_thresh)] = result

    # Analyze
    if all_results:
        analyze_results(all_results)
    else:
        print("\n  No results to analyze.")


if __name__ == "__main__":
    main()
