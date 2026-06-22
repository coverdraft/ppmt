#!/usr/bin/env python3
"""
PPMT Trade Autopsy — Per-Trade Detailed Analysis

Captures every detail of each trade from the replay:
  - Entry/exit prices, SL/TP levels
  - Expected move vs actual move (MFE/MAE)
  - Close reason, confidence, node metadata
  - Direction accuracy

Runs with BOTH:
  A) New weights (5m: N1=10%, N2=0%, N3=55%, N4=35%)
  B) Old weights (5m: base WEIGHT_PROFILES — meme N2=60%)

Compares the 1 old winning trade vs all new trades.

Usage:
    python scripts/trade_autopsy.py
    python scripts/trade_autopsy.py --skip-download
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

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
from ppmt.engine.weights import AdaptiveWeights, TIMEFRAME_WEIGHT_OVERRIDES, WEIGHT_PROFILES
from ppmt.terminal.paper_executor import PaperExecutor
from ppmt.execution.models import PositionState
from ppmt.core.profiles import SPREAD_ESTIMATES
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("trade_autopsy")

# ─── Configuration ──────────────────────────────────────────────

DEFAULT_TOKENS = ["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"]
TIMEFRAME = "5m"
CAPITAL_USDT = 1000.0
RISK_PCT = 0.01
EV_THRESHOLD = 0.80
OOS_DAYS = 7
IS_DAYS = 83


# ─── Trade Record ───────────────────────────────────────────────

@dataclass
class TradeRecord:
    """Detailed record of a single trade."""
    trade_id: int = 0
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    entry_time: str = ""

    # SL/TP levels
    sl_price: float = 0.0
    tp_price: float = 0.0
    cat_sl_price: float = 0.0

    # Expected move from signal
    expected_move_pct: float = 0.0
    expected_move_abs: float = 0.0

    # Actual outcome
    exit_price: float = 0.0
    exit_time: str = ""
    close_reason: str = ""
    pnl_pct: float = 0.0

    # Post-entry price excursions (computed from subsequent candles)
    max_favorable_excursion_pct: float = 0.0  # MFE: max move in our direction
    max_adverse_excursion_pct: float = 0.0     # MAE: max move against us
    candles_held: int = 0

    # Signal quality
    confidence: float = 0.0
    weighted_confidence: float = 0.0

    # Node metadata
    favorable_pct_node: float = 0.0
    drawdown_pct_node: float = 0.0
    historical_count_node: int = 0
    best_node_level: str = ""

    # EV computation
    net_favorable: float = 0.0
    net_rr: float = 0.0
    net_rr_capped: float = 0.0
    net_ev: float = 0.0

    # Per-level confidence breakdown
    n1_confidence: float = 0.0
    n2_confidence: float = 0.0
    n3_confidence: float = 0.0
    n4_confidence: float = 0.0

    # Per-level weights used
    w_n1: float = 0.0
    w_n2: float = 0.0
    w_n3: float = 0.0
    w_n4: float = 0.0

    # Weight profile name
    weight_profile: str = ""


# ─── Build Tries ────────────────────────────────────────────────

def build_tries_for_symbol(
    storage: PPMTStorage,
    symbol: str,
    asset_class: str,
    weight_profile: str,
    is_df: pd.DataFrame,
    timeframe: str,
) -> dict:
    """Build N1/N2/N3/N4 tries from in-sample data and save to DB."""
    engine = PPMT(
        symbol=symbol,
        asset_class=asset_class,
        weight_profile=weight_profile,
        dual_sax=True,
        timeframe=timeframe,
    )
    build_count = engine.build(is_df)
    logger.info(f"  {symbol}: built {build_count} patterns from {len(is_df)} IS candles")
    return {
        "n1": engine.trie_n1,
        "n2": engine.trie_n2,
        "n3": engine.trie_n3,
        "n4": engine.trie_n4,
    }


# ─── Autopsy Replay ─────────────────────────────────────────────

def run_autopsy_replay(
    symbol: str,
    oos_df: pd.DataFrame,
    storage: PPMTStorage,
    asset_class: str,
    weight_profile: str,
    timeframe: str,
    use_new_weights: bool = False,
) -> list[TradeRecord]:
    """
    Run OOS replay capturing every trade detail for autopsy.

    If use_new_weights=True, forces 5m timeframe override (N2=0%).
    If False, uses base WEIGHT_PROFILES (old meme N2=60%).
    """
    trades: list[TradeRecord] = []
    trade_counter = 0

    # Load tries
    tries = storage.load_all_tries(symbol, asset_class, timeframe=timeframe)
    n1_trie = tries.get("n1")
    n2_trie = tries.get("n2")
    n3_trie = tries.get("n3")
    n4_trie = tries.get("n4")

    if not n1_trie and not n2_trie and not n3_trie:
        logger.error(f"  No tries found for {symbol}! Skipping.")
        return trades

    # Create engine
    engine = PPMT(
        symbol=symbol,
        asset_class=asset_class,
        weight_profile=weight_profile,
        dual_sax=True,
        min_confidence=0.08,
        timeframe=timeframe,
    )

    # If using old weights, we need to BYPASS the 5m override.
    # We do this by temporarily removing the "5m" key from TIMEFRAME_WEIGHT_OVERRIDES
    # so the engine falls back to WEIGHT_PROFILES.
    # Then restore it after.
    global TIMEFRAME_WEIGHT_OVERRIDES
    saved_5m = None
    if not use_new_weights and "5m" in TIMEFRAME_WEIGHT_OVERRIDES:
        saved_5m = TIMEFRAME_WEIGHT_OVERRIDES["5m"]
        del TIMEFRAME_WEIGHT_OVERRIDES["5m"]

    # Force recreate weights on the engine
    engine.weights = AdaptiveWeights.from_profile(weight_profile, timeframe=timeframe)

    engine.set_tries(
        trie_n1=n1_trie if n1_trie else PPMTTrie(name="empty_n1"),
        trie_n2=n2_trie if n2_trie else PPMTTrie(name="empty_n2"),
        trie_n3=n3_trie if n3_trie else PPMTTrie(name="empty_n3"),
        trie_n4=n4_trie if n4_trie else engine.trie_n4,
    )

    # Restore 5m overrides if we removed them
    if saved_5m is not None:
        TIMEFRAME_WEIGHT_OVERRIDES["5m"] = saved_5m

    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None

    spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)

    # Track active trade for MFE/MAE computation
    active_trade: Optional[TradeRecord] = None
    active_trade_entry_idx: int = 0

    _last_engine_ts = 0

    for idx in range(len(oos_df)):
        row = oos_df.iloc[[idx]]
        current_price = float(row["close"].iloc[0])
        candle_high = float(row["high"].iloc[0])
        candle_low = float(row["low"].iloc[0])
        ts = oos_df.index[idx]
        ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)

        # ─── Update MFE/MAE for active trade ─────────────
        if active_trade is not None and executor.is_in_position:
            pos = executor.position
            if pos.direction == "LONG":
                favorable_move = (candle_high - pos.entry_price) / pos.entry_price * 100.0
                adverse_move = (pos.entry_price - candle_low) / pos.entry_price * 100.0
            else:
                favorable_move = (pos.entry_price - candle_low) / pos.entry_price * 100.0
                adverse_move = (candle_high - pos.entry_price) / pos.entry_price * 100.0

            active_trade.max_favorable_excursion_pct = max(
                active_trade.max_favorable_excursion_pct, favorable_move
            )
            active_trade.max_adverse_excursion_pct = max(
                active_trade.max_adverse_excursion_pct, adverse_move
            )
            active_trade.candles_held = idx - active_trade_entry_idx

        # ─── Check SL/TP on every candle ─────────────────
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
                if active_trade is not None:
                    active_trade.exit_price = closed.close_price or current_price
                    active_trade.exit_time = str(ts)
                    active_trade.close_reason = closed.close_reason or "UNKNOWN"
                    active_trade.pnl_pct = closed.pnl_pct or 0.0
                    # Final MFE/MAE update with close candle
                    if closed.direction == "LONG":
                        fm = (candle_high - closed.entry_price) / closed.entry_price * 100.0
                        am = (closed.entry_price - candle_low) / closed.entry_price * 100.0
                    else:
                        fm = (closed.entry_price - candle_low) / closed.entry_price * 100.0
                        am = (candle_high - closed.entry_price) / closed.entry_price * 100.0
                    active_trade.max_favorable_excursion_pct = max(active_trade.max_favorable_excursion_pct, fm)
                    active_trade.max_adverse_excursion_pct = max(active_trade.max_adverse_excursion_pct, am)
                    active_trade.candles_held = idx - active_trade_entry_idx
                    trades.append(active_trade)
                    active_trade = None
                executor._position = None

        # Feed candle to engine
        result: Optional[PPMTResult] = None
        if ts_sec > _last_engine_ts:
            _last_engine_ts = ts_sec
            result = engine.process_new_candle(
                candle_df=row,
                current_price=current_price,
                is_in_position=executor.is_in_position,
                entry_price=executor.position.entry_price if executor.position else None,
            )

        if result is None:
            continue

        sig = result.signal if result and result.signal else None
        if sig is None or not sig.is_entry:
            continue
        if executor.is_in_position:
            continue

        # ─── Net EV Gate ──────────────────────────────────
        _best_node = None
        _best_level = ""
        for _mr, _lvl in [(result.n3_match, "N3"), (result.n1_match, "N1"),
                           (result.n2_match, "N2"), (result.n4_match, "N4")]:
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
        if net_favorable <= 0:
            continue

        net_rr = net_favorable / drawdown_pct
        net_rr_capped = min(net_rr, 3.0)
        net_ev = sig.confidence * net_rr_capped

        if net_ev < EV_THRESHOLD:
            continue

        # ─── PASSED EV Gate — open trade ──────────────────
        trade_counter += 1
        direction = sig.direction or "LONG"
        expected_move_pct = sig.expected_move_pct or 1.0
        size_usdt = CAPITAL_USDT * RISK_PCT / (abs(expected_move_pct) * 0.012)
        size_usdt = min(size_usdt, CAPITAL_USDT)

        # Get per-level confidences
        n1_c = result.n1_confidence if result else 0.0
        n2_c = result.n2_confidence if result else 0.0
        n3_c = result.n3_confidence if result else 0.0
        n4_c = result.n4_confidence if result else 0.0

        # Get weights from engine
        w = engine.weights

        try:
            pos = executor.open_position_sync(
                symbol=symbol,
                direction=direction,
                entry_price=current_price,
                expected_move_pct=expected_move_pct,
                predicted_path_symbols=sig.predicted_path_symbols if sig.predicted_path else None,
                size_usdt=size_usdt,
            )
        except RuntimeError:
            continue

        # ─── SL FIX: Use max(1.2×expected_move, drawdown_pct×1.1) ──
        current_sl_distance_pct = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
        drawdown_sl_pct = drawdown_pct * 1.1  # 10% buffer over observed max drawdown

        if drawdown_sl_pct > current_sl_distance_pct:
            extra_distance = drawdown_sl_pct - current_sl_distance_pct
            if pos.direction == "LONG":
                pos.current_sl -= pos.entry_price * (extra_distance / 100.0)
                pos.catastrophic_sl -= pos.entry_price * (extra_distance / 100.0)
            else:
                pos.current_sl += pos.entry_price * (extra_distance / 100.0)
                pos.catastrophic_sl += pos.entry_price * (extra_distance / 100.0)

        # Record trade
        tr = TradeRecord(
            trade_id=trade_counter,
            symbol=symbol,
            direction=direction,
            entry_price=current_price,
            entry_time=str(ts),
            sl_price=pos.current_sl,
            tp_price=pos.current_tp,
            cat_sl_price=pos.catastrophic_sl,
            expected_move_pct=expected_move_pct,
            expected_move_abs=current_price * (expected_move_pct / 100.0),
            confidence=sig.confidence,
            weighted_confidence=sig.confidence,
            favorable_pct_node=favorable_pct,
            drawdown_pct_node=drawdown_pct,
            historical_count_node=hist_count,
            best_node_level=_best_level,
            net_favorable=net_favorable,
            net_rr=net_rr,
            net_rr_capped=net_rr_capped,
            net_ev=net_ev,
            n1_confidence=n1_c,
            n2_confidence=n2_c,
            n3_confidence=n3_c,
            n4_confidence=n4_c,
            w_n1=w.n1_universal,
            w_n2=w.n2_asset_class,
            w_n3=w.n3_per_asset,
            w_n4=w.n4_per_asset_regime,
            weight_profile="NEW (N2=0%)" if use_new_weights else "OLD (N2=60%)",
        )
        active_trade = tr
        active_trade_entry_idx = idx

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
                tr.exit_price = entry_closed.close_price or current_price
                tr.exit_time = str(ts)
                tr.close_reason = entry_closed.close_reason or "ENTRY_CANDLE"
                tr.pnl_pct = entry_closed.pnl_pct or 0.0
                tr.candles_held = 0
                trades.append(tr)
                active_trade = None
                executor._position = None
                continue

        # Walk-Forward check
        if result and executor.is_in_position:
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

    # Close remaining position at end
    if executor.is_in_position and executor._position:
        last_price = float(oos_df["close"].iloc[-1])
        closed = executor.force_close(last_price, "REPLAY_END")
        if active_trade is not None:
            active_trade.exit_price = closed.close_price or last_price
            active_trade.exit_time = str(oos_df.index[-1])
            active_trade.close_reason = closed.close_reason or "REPLAY_END"
            active_trade.pnl_pct = closed.pnl_pct or 0.0
            active_trade.candles_held = len(oos_df) - 1 - active_trade_entry_idx
            trades.append(active_trade)
            active_trade = None

    return trades


# ─── Report ─────────────────────────────────────────────────────

def print_trade_detail(trade: TradeRecord):
    """Print full detail of a single trade."""
    direction_str = "🟢 LONG" if trade.direction == "LONG" else "🔴 SHORT"
    result_str = "WIN" if trade.pnl_pct > 0.01 else ("LOSS" if trade.pnl_pct < -0.01 else "BE")

    print(f"\n  ┌─ Trade #{trade.trade_id}: {trade.symbol} {direction_str} ── {result_str}")
    print(f"  │  Entry:  ${trade.entry_price:.4f}  @ {trade.entry_time[:19]}")
    print(f"  │  Exit:   ${trade.exit_price:.4f}  @ {trade.exit_time[:19]}")
    print(f"  │  Reason: {trade.close_reason}")
    print(f"  │")
    print(f"  │  SL:  ${trade.sl_price:.4f}  ({'entry' if trade.direction == 'LONG' else 'entry'} - {abs(trade.entry_price - trade.sl_price):.4f})")
    print(f"  │  TP:  ${trade.tp_price:.4f}  ({'entry' if trade.direction == 'LONG' else 'entry'} + {abs(trade.tp_price - trade.entry_price):.4f})")
    print(f"  │  CatSL: ${trade.cat_sl_price:.4f}")
    print(f"  │")
    print(f"  │  Expected move: {trade.expected_move_pct:.3f}%  (${trade.expected_move_abs:.4f})")
    print(f"  │  Actual MFE:    {trade.max_favorable_excursion_pct:.3f}%  (max favorable)")
    print(f"  │  Actual MAE:    {trade.max_adverse_excursion_pct:.3f}%  (max adverse)")
    print(f"  │  MFE/Expected:  {trade.max_favorable_excursion_pct / trade.expected_move_pct * 100:.0f}% of expected")
    print(f"  │  Candles held:  {trade.candles_held}")
    print(f"  │")
    print(f"  │  P&L: {trade.pnl_pct:+.3f}%")
    print(f"  │")
    print(f"  │  ── Signal Quality ──")
    print(f"  │  Confidence:     {trade.confidence:.4f}")
    print(f"  │  Best node:      {trade.best_node_level} (hist_count={trade.historical_count_node})")
    print(f"  │  Node fav/dd:    {trade.favorable_pct_node:.3f}% / {trade.drawdown_pct_node:.3f}%")
    print(f"  │  Net favorable:  {trade.net_favorable:.3f}%")
    print(f"  │  Net R:R:        {trade.net_rr:.3f} (capped: {trade.net_rr_capped:.3f})")
    print(f"  │  Net EV:         {trade.net_ev:.4f}")
    print(f"  │")
    print(f"  │  ── Per-Level Confidence ──")
    print(f"  │  N1: {trade.n1_confidence:.4f} (w={trade.w_n1:.0%})")
    print(f"  │  N2: {trade.n2_confidence:.4f} (w={trade.w_n2:.0%})")
    print(f"  │  N3: {trade.n3_confidence:.4f} (w={trade.w_n3:.0%})")
    print(f"  │  N4: {trade.n4_confidence:.4f} (w={trade.w_n4:.0%})")
    print(f"  │  Profile: {trade.weight_profile}")
    print(f"  └─")


def print_autopsy_report(trades: list[TradeRecord], label: str):
    """Print comprehensive autopsy report."""
    print(f"\n{'='*90}")
    print(f"  TRADE AUTOPSY — {label}")
    print(f"{'='*90}")
    print(f"  Total trades: {len(trades)}")

    if not trades:
        print("  No trades to analyze.")
        return

    # Per-trade details
    for t in trades:
        print_trade_detail(t)

    # ─── Aggregate analysis ──────────────────────────────────
    print(f"\n{'─'*90}")
    print(f"  AGGREGATE ANALYSIS — {label}")
    print(f"{'─'*90}")

    # Close reason distribution
    close_reasons = {}
    for t in trades:
        r = t.close_reason
        close_reasons[r] = close_reasons.get(r, 0) + 1
    print(f"\n  Close Reason Distribution:")
    for r, c in sorted(close_reasons.items(), key=lambda x: -x[1]):
        print(f"    {r:30s}: {c:3d} ({c/len(trades)*100:.0f}%)")

    # Direction accuracy
    direction_correct = sum(
        1 for t in trades
        if (t.direction == "LONG" and t.max_favorable_excursion_pct > t.max_adverse_excursion_pct)
        or (t.direction == "SHORT" and t.max_favorable_excursion_pct > t.max_adverse_excursion_pct)
    )
    print(f"\n  Direction Accuracy: {direction_correct}/{len(trades)} ({direction_correct/len(trades)*100:.0f}%)")
    print(f"    = Price moved in signal direction MORE THAN against it")

    # Expected vs actual move
    avg_expected = np.mean([t.expected_move_pct for t in trades])
    avg_mfe = np.mean([t.max_favorable_excursion_pct for t in trades])
    avg_mae = np.mean([t.max_adverse_excursion_pct for t in trades])
    mfe_ratios = [t.max_favorable_excursion_pct / t.expected_move_pct for t in trades if t.expected_move_pct > 0]

    print(f"\n  Expected vs Actual Move:")
    print(f"    Avg expected move:     {avg_expected:.3f}%")
    print(f"    Avg MFE (favorable):   {avg_mfe:.3f}%  ({avg_mfe/avg_expected*100:.0f}% of expected)")
    print(f"    Avg MAE (adverse):     {avg_mae:.3f}%  ({avg_mae/avg_expected*100:.0f}% of expected)")
    print(f"    MFE/Expected ratio:    avg={np.mean(mfe_ratios):.2f}x, min={np.min(mfe_ratios):.2f}x, max={np.max(mfe_ratios):.2f}x")

    # SL distance vs MAE comparison
    sl_distances_pct = []
    for t in trades:
        if t.direction == "LONG":
            sl_dist = (t.entry_price - t.sl_price) / t.entry_price * 100.0
        else:
            sl_dist = (t.sl_price - t.entry_price) / t.entry_price * 100.0
        sl_distances_pct.append(sl_dist)

    print(f"\n  SL Distance vs MAE:")
    print(f"    Avg SL distance:  {np.mean(sl_distances_pct):.3f}%")
    print(f"    Avg MAE:          {avg_mae:.3f}%")
    if avg_mae > 0:
        print(f"    MAE/SL ratio:     {avg_mae / np.mean(sl_distances_pct):.2f}x")
        sl_too_tight = sum(1 for t in trades if t.max_adverse_excursion_pct > sl_distances_pct[trades.index(t)] * 0.8)
        print(f"    Trades where MAE > 80% of SL: {sl_too_tight}/{len(trades)}")

    # TP distance vs MFE comparison
    tp_distances_pct = []
    for t in trades:
        if t.direction == "LONG":
            tp_dist = (t.tp_price - t.entry_price) / t.entry_price * 100.0
        else:
            tp_dist = (t.entry_price - t.tp_price) / t.entry_price * 100.0
        tp_distances_pct.append(tp_dist)

    mfe_approached_tp = sum(1 for t in trades if t.max_favorable_excursion_pct > tp_distances_pct[trades.index(t)] * 0.5)

    print(f"\n  TP Distance vs MFE:")
    print(f"    Avg TP distance:  {np.mean(tp_distances_pct):.3f}%")
    print(f"    Avg MFE:          {avg_mfe:.3f}%")
    if avg_mfe > 0:
        print(f"    MFE/TP ratio:     {avg_mfe / np.mean(tp_distances_pct):.2f}x")
    print(f"    Trades where MFE > 50% of TP: {mfe_approached_tp}/{len(trades)}")

    # Confidence analysis
    confs = [t.confidence for t in trades]
    print(f"\n  Confidence:")
    print(f"    Avg:   {np.mean(confs):.4f}")
    print(f"    Range: [{np.min(confs):.4f} — {np.max(confs):.4f}]")

    # Key question: Did price move in the right direction but not enough?
    right_direction_no_tp = sum(
        1 for t in trades
        if t.max_favorable_excursion_pct > t.max_adverse_excursion_pct
        and t.close_reason == "CLOSED_BY_SL"
    )
    right_direction_hit_tp = sum(
        1 for t in trades
        if t.max_favorable_excursion_pct > t.max_adverse_excursion_pct
        and t.close_reason == "CLOSED_BY_TP"
    )
    wrong_direction = sum(
        1 for t in trades
        if t.max_favorable_excursion_pct <= t.max_adverse_excursion_pct
    )
    print(f"\n  Direction vs Outcome:")
    print(f"    Right direction, hit TP:    {right_direction_hit_tp}")
    print(f"    Right direction, hit SL:    {right_direction_no_tp}  ← TP too far / SL too tight?")
    print(f"    Wrong direction:            {wrong_direction}  ← Signal is wrong")

    # SL/TP ratio analysis
    print(f"\n  SL/TP Ratio (1.2×/2.5× expected_move):")
    print(f"    SL = 1.2 × expected_move → {np.mean(sl_distances_pct)/avg_expected:.1f}× avg expected")
    print(f"    TP = 2.5 × expected_move → {np.mean(tp_distances_pct)/avg_expected:.1f}× avg expected")
    print(f"    Effective R:R = TP/SL = {np.mean(tp_distances_pct)/np.mean(sl_distances_pct):.2f}")


# ─── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PPMT Trade Autopsy")
    parser.add_argument("--tokens", nargs="+", default=DEFAULT_TOKENS)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    storage = PPMTStorage()
    classifier = AssetClassifier()

    # ─── Phase 1: Download data ─────────────────────────
    if not args.skip_download:
        print(f"\n{'='*80}")
        print("  PHASE 1: Downloading OHLCV data")
        print(f"{'='*80}")

        exchange = _DirectPollExchange("binance")
        for symbol in args.tokens:
            api_symbol = symbol.replace("/", "")
            print(f"\n  Downloading {symbol} ({TIMEFRAME}, ~90 days)...")

            try:
                all_data = []
                end_ms = int(time.time() * 1000)
                current_end = end_ms
                total_fetched = 0
                target_candles = 26000

                while total_fetched < target_candles:
                    batch_size = 1000
                    start_ms = current_end - (batch_size * 300000)

                    import requests
                    url = f"{exchange.base_url}/api/v3/klines"
                    params = {
                        "symbol": api_symbol,
                        "interval": TIMEFRAME,
                        "limit": batch_size,
                        "startTime": start_ms,
                        "endTime": current_end,
                    }
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

                if all_data:
                    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                    df.set_index("timestamp", inplace=True)
                    df = df[~df.index.duplicated(keep="first")]
                    df.sort_index(inplace=True)
                    storage.save_ohlcv(symbol, TIMEFRAME, df)
                    print(f"    Saved {len(df):,} candles")
            except Exception as e:
                logger.error(f"  Download failed for {symbol}: {e}")

    # ─── Phase 2: Build tries ────────────────────────────
    if not args.skip_build:
        print(f"\n{'='*80}")
        print("  PHASE 2: Building tries")
        print(f"{'='*80}")

        for symbol in args.tokens:
            info = classifier.classify(symbol)
            df = storage.load_ohlcv(symbol, TIMEFRAME)
            if df is None or len(df) < 100:
                continue

            oos_start = df.index[-1] - pd.Timedelta(days=OOS_DAYS)
            is_df = df[df.index < oos_start]

            if len(is_df) < 200:
                continue

            print(f"  {symbol}: {len(is_df)} IS candles")
            build_tries_for_symbol(storage, symbol, info.asset_class, info.weight_profile, is_df, TIMEFRAME)

    # ─── Phase 3: Run BOTH autopsies ─────────────────────
    for use_new, label in [(True, "NEW WEIGHTS (5m: N2=0%, N3=55%, N4=35%)"),
                            (False, "OLD WEIGHTS (5m: base profiles, meme N2=60%)")]:
        print(f"\n{'='*90}")
        print(f"  RUNNING AUTOPSY: {label}")
        print(f"{'='*90}")

        all_trades = []

        for symbol in args.tokens:
            info = classifier.classify(symbol)
            df = storage.load_ohlcv(symbol, TIMEFRAME)
            if df is None or len(df) < 50:
                continue

            oos_start = df.index[-1] - pd.Timedelta(days=OOS_DAYS)
            oos_df = df[df.index >= oos_start]

            if len(oos_df) < 10:
                continue

            print(f"\n  Autopsying {symbol}...")
            trades = run_autopsy_replay(
                symbol, oos_df, storage, info.asset_class, info.weight_profile,
                TIMEFRAME, use_new_weights=use_new,
            )
            all_trades.extend(trades)
            print(f"    Found {len(trades)} trades")

        print_autopsy_report(all_trades, label)

    # ─── Phase 4: Comparative summary ─────────────────────
    print(f"\n\n{'='*90}")
    print(f"  COMPARATIVE SUMMARY")
    print(f"{'='*90}")
    print(f"  Run A: NEW weights (N2=0%, N3=55%, N4=35%)")
    print(f"  Run B: OLD weights (base profiles, meme N2=60%)")
    print(f"{'='*90}")


if __name__ == "__main__":
    main()
