#!/usr/bin/env python3
"""
PPMT Replay Benchmark — 5m Tries Performance Test

Runs an out-of-sample replay on 4 representative tokens using the real
PPMT engine + tries from the DB. Mirrors the terminal's exact logic:
  - process_new_candle() for incremental SAX encoding
  - PaperExecutor for position tracking
  - Net EV Gate with spread/friction awareness
  - Walk-Forward + Divergence checks

Usage:
    # Full benchmark (download data + build tries + replay)
    python scripts/replay_benchmark_5m.py

    # Skip download/trie build if already done
    python scripts/replay_benchmark_5m.py --skip-build

    # Specific tokens only
    python scripts/replay_benchmark_5m.py --tokens BTC/USDT SOL/USDT

Commit: [TERMINAL-v2.1] Add replay benchmark script for 5m tries
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from ppmt.execution.models import PositionState
from ppmt.core.profiles import SPREAD_ESTIMATES
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("replay_benchmark")

# ─── Configuration ──────────────────────────────────────────────

DEFAULT_TOKENS = ["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"]
TIMEFRAME = "5m"
CAPITAL_USDT = 1000.0
RISK_PCT = 0.01           # 1% risk per trade
EV_THRESHOLD = 0.80       # Same as terminal
OOS_DAYS = 7              # Out-of-sample period
IS_DAYS = 83              # In-sample period (90 total - 7 OOS)
POLL_INTERVAL = 0         # No delay in replay (full speed)

# ─── Data Classes ───────────────────────────────────────────────

@dataclass
class ReplayStats:
    """Statistics for a single token replay."""
    symbol: str = ""
    timeframe: str = "5m"
    total_candles: int = 0
    is_candles: int = 0
    oos_candles: int = 0
    total_signals_raw: int = 0
    signals_passed_ev: int = 0
    signals_rejected_spread: int = 0
    signals_rejected_ev_score: int = 0
    signals_rejected_overlap: int = 0
    signals_no_direction: int = 0
    trades_total: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    trades_be: int = 0  # break-even
    wins: list[float] = field(default_factory=list)
    losses: list[float] = field(default_factory=list)
    all_pnl: list[float] = field(default_factory=list)
    passed_confidences: list[float] = field(default_factory=list)
    trie_n1_patterns: int = 0
    trie_n2_patterns: int = 0
    trie_n3_patterns: int = 0
    trie_n4_patterns: int = 0
    elapsed_seconds: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.trades_won / self.trades_total * 100) if self.trades_total > 0 else 0.0

    @property
    def total_pnl_pct(self) -> float:
        return sum(self.all_pnl) if self.all_pnl else 0.0

    @property
    def avg_confidence(self) -> float:
        return np.mean(self.passed_confidences) if self.passed_confidences else 0.0

    @property
    def pnl_in_r(self) -> float:
        """P&L expressed in R-multiples (1R = risk_per_trade)."""
        if not self.all_pnl:
            return 0.0
        # Each trade risks RISK_PCT of capital, so 1R = RISK_PCT * 100
        r_value = RISK_PCT * 100  # e.g. 1.0 for 1% risk
        total_r = sum(p / r_value for p in self.all_pnl)
        return total_r

    @property
    def rejection_rate(self) -> float:
        rejected = self.signals_rejected_spread + self.signals_rejected_ev_score + self.signals_rejected_overlap
        total = self.total_signals_raw
        return (rejected / total * 100) if total > 0 else 0.0


# ─── Data Download ──────────────────────────────────────────────

async def download_ohlcv(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Download OHLCV data from Binance for the given period."""
    exchange = _DirectPollExchange("binance")
    api_symbol = symbol.replace("/", "")

    # Binance klines max limit = 1000 per request
    # 5m candles: 288/day, 90 days = 25,920 candles
    total_needed = days * 288
    all_data = []

    # Fetch in batches of 1000
    # Calculate start timestamp
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - (days * 24 * 3600 * 1000)

    current_start = start_ts
    while current_start < end_ts:
        try:
            batch = await exchange.fetch_ohlcv(api_symbol, timeframe, limit=1000)
            if not batch:
                break
            all_data.extend(batch)
            # Move start to after last candle
            last_ts = batch[-1][0]
            if last_ts <= current_start:
                break
            current_start = last_ts + 1
            # For simplicity, just do one big request with limit=1000
            # which gives us the most recent 1000 candles
            break
        except Exception as e:
            logger.warning(f"Download error for {symbol}: {e}")
            break

    await exchange.close()

    if not all_data:
        logger.error(f"No data downloaded for {symbol}")
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    df.sort_index(inplace=True)

    return df


def download_ohlcv_sync(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Synchronous wrapper for download."""
    return asyncio.run(download_ohlcv(symbol, timeframe, days))


# ─── Trie Building ──────────────────────────────────────────────

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

    # Build engine on IS data
    build_count = engine.build(is_df)
    logger.info(f"  {symbol}: built {build_count} patterns from {len(is_df)} IS candles")

    # Save per-symbol tries (N3, N4)
    if engine.trie_n3 and engine.trie_n3.pattern_count > 0:
        storage.save_trie(symbol, "n3", engine.trie_n3, timeframe=timeframe)
    if engine.trie_n4 and engine.trie_n4.pattern_count > 0:
        storage.save_trie(symbol, "n4", engine.trie_n4, timeframe=timeframe)

    # Save shared pools (N1 universal, N2 class)
    if engine.trie_n1 and engine.trie_n1.pattern_count > 0:
        storage.save_trie(UNIVERSAL_POOL_KEY, "n1", engine.trie_n1, timeframe=timeframe)
    if engine.trie_n2 and engine.trie_n2.pattern_count > 0:
        storage.save_trie(class_pool_key(asset_class), "n2", engine.trie_n2, timeframe=timeframe)

    return {
        "n1": engine.trie_n1.pattern_count if engine.trie_n1 else 0,
        "n2": engine.trie_n2.pattern_count if engine.trie_n2 else 0,
        "n3": engine.trie_n3.pattern_count if engine.trie_n3 else 0,
        "n4": engine.trie_n4.pattern_count if engine.trie_n4 else 0,
    }


# ─── Replay Engine ──────────────────────────────────────────────

def run_replay(
    symbol: str,
    oos_df: pd.DataFrame,
    storage: PPMTStorage,
    asset_class: str,
    weight_profile: str,
    timeframe: str,
) -> ReplayStats:
    """
    Run OOS replay for a single token using the PPMT engine
    with pre-built tries from the DB.

    Mirrors v2_server.py logic: process_new_candle() → signal → EV gate → PaperExecutor.
    """
    stats = ReplayStats(symbol=symbol, timeframe=timeframe)
    t0 = time.time()

    # ─── Load tries from DB ─────────────────────────────────
    tries = storage.load_all_tries(symbol, asset_class, timeframe=timeframe)
    n1_trie = tries.get("n1")
    n2_trie = tries.get("n2")
    n3_trie = tries.get("n3")
    n4_trie = tries.get("n4")

    stats.trie_n1_patterns = n1_trie.pattern_count if n1_trie else 0
    stats.trie_n2_patterns = n2_trie.pattern_count if n2_trie else 0
    stats.trie_n3_patterns = n3_trie.pattern_count if n3_trie else 0
    stats.trie_n4_patterns = n4_trie.pattern_count if n4_trie else 0

    logger.info(
        f"  Tries loaded: N1={stats.trie_n1_patterns} N2={stats.trie_n2_patterns} "
        f"N3={stats.trie_n3_patterns} N4={stats.trie_n4_patterns}"
    )

    if not n1_trie and not n2_trie and not n3_trie:
        logger.error(f"  No tries found for {symbol}! Skipping.")
        return stats

    # ─── Create engine ──────────────────────────────────────
    engine = PPMT(
        symbol=symbol,
        asset_class=asset_class,
        weight_profile=weight_profile,
        dual_sax=True,
        min_confidence=0.08,
        timeframe=timeframe,
    )

    # Set tries — use empty trie for missing levels
    engine.set_tries(
        trie_n1=n1_trie if n1_trie else PPMTTrie(name="empty_n1"),
        trie_n2=n2_trie if n2_trie else PPMTTrie(name="empty_n2"),
        trie_n3=n3_trie if n3_trie else PPMTTrie(name="empty_n3"),
        trie_n4=n4_trie if n4_trie else engine.trie_n4,
    )

    # ─── Create executor ────────────────────────────────────
    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None

    # ─── Spread estimate ────────────────────────────────────
    spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)

    # ─── Replay loop ────────────────────────────────────────
    # v2.1 FIX: Check SL/TP using HIGH/LOW (realistic intra-candle fills)
    # not just close. Otherwise trades with wide SL/TP never close.
    stats.oos_candles = len(oos_df)
    _last_engine_ts = 0

    for idx in range(len(oos_df)):
        row = oos_df.iloc[[idx]]
        current_price = float(row["close"].iloc[0])
        candle_high = float(row["high"].iloc[0])
        candle_low = float(row["low"].iloc[0])
        ts = oos_df.index[idx]
        ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)

        # ─── Check SL/TP on every candle (HIGH/LOW) ────────
        # This is more realistic than close-only checking.
        # A stop-loss fills when price moves THROUGH the level
        # during the candle, even if close is elsewhere.
        if executor.is_in_position:
            pos = executor.position
            closed = None
            if pos.direction == "LONG":
                # Check catastrophic SL and normal SL with LOW
                if candle_low <= pos.catastrophic_sl:
                    closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                elif candle_low <= pos.current_sl:
                    closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                # Check TP with HIGH
                elif candle_high >= pos.current_tp:
                    closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
            else:  # SHORT
                if candle_high >= pos.catastrophic_sl:
                    closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                elif candle_high >= pos.current_sl:
                    closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                elif candle_low <= pos.current_tp:
                    closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")

            if closed:
                pnl = closed.pnl_pct or 0.0
                stats.all_pnl.append(pnl)
                stats.trades_total += 1
                if pnl > 0.01:
                    stats.trades_won += 1
                    stats.wins.append(pnl)
                elif pnl < -0.01:
                    stats.trades_lost += 1
                    stats.losses.append(pnl)
                else:
                    stats.trades_be += 1
                executor._position = None  # Reset for next trade

        # Feed candle to engine (every candle in replay is "closed")
        result: Optional[PPMTResult] = None
        if ts_sec > _last_engine_ts:
            _last_engine_ts = ts_sec
            result = engine.process_new_candle(
                candle_df=row,
                current_price=current_price,
                is_in_position=executor.is_in_position,
                entry_price=executor.position.entry_price if executor.position else None,
            )

        # ─── Process signal ─────────────────────────────────
        if result is None:
            continue

        # Even without a result, check for quick match on buffer state
        # (mirror terminal logic)
        sig = result.signal if result and result.signal else None

        if sig is None or not sig.is_entry:
            continue

        if executor.is_in_position:
            continue

        stats.total_signals_raw += 1

        # ─── Net EV Gate (same as v2_server.py) ────────────
        # Get favorable/drawdown from best matched node
        _best_node = None
        for _mr in [result.n3_match, result.n1_match, result.n2_match, result.n4_match]:
            if _mr and _mr.node and _mr.node.metadata and _mr.node.metadata.historical_count > 0:
                _best_node = _mr.node
                break

        favorable_pct = abs(_best_node.metadata.max_favorable_pct) if _best_node else 0.0
        drawdown_pct = abs(_best_node.metadata.max_drawdown_pct) if _best_node else 0.5

        if favorable_pct < 0.001:
            favorable_pct = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.1
        if drawdown_pct < 0.001:
            drawdown_pct = 0.5

        net_favorable = favorable_pct - spread_pct

        if net_favorable <= 0:
            stats.signals_rejected_spread += 1
            continue

        net_rr = net_favorable / drawdown_pct
        net_rr_capped = min(net_rr, 3.0)
        net_ev = sig.confidence * net_rr_capped

        if net_ev < EV_THRESHOLD:
            stats.signals_rejected_ev_score += 1
            continue

        # ─── PASSED EV Gate ────────────────────────────────
        stats.signals_passed_ev += 1
        stats.passed_confidences.append(sig.confidence)

        # Open position
        direction = sig.direction or "LONG"
        size_usdt = CAPITAL_USDT * RISK_PCT / (abs(sig.expected_move_pct or 1.0) * 0.012)
        size_usdt = min(size_usdt, CAPITAL_USDT)  # Cap at total capital

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
            # Already in position
            stats.signals_rejected_overlap += 1
            continue

        # ─── SL FIX: Use max(1.2×expected_move, drawdown_pct×1.1) ──
        # The node's drawdown_pct reflects the max IS drawdown for this pattern.
        # If that's larger than the default 1.2×expected_move SL, use it.
        current_sl_distance_pct = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
        drawdown_sl_pct = drawdown_pct * 1.1  # 10% buffer over observed max drawdown

        if drawdown_sl_pct > current_sl_distance_pct:
            # Widen SL to drawdown-based level
            extra_distance = drawdown_sl_pct - current_sl_distance_pct
            if pos.direction == "LONG":
                pos.current_sl -= pos.entry_price * (extra_distance / 100.0)
                pos.catastrophic_sl -= pos.entry_price * (extra_distance / 100.0)
            else:
                pos.current_sl += pos.entry_price * (extra_distance / 100.0)
                pos.catastrophic_sl += pos.entry_price * (extra_distance / 100.0)

        # ─── Check SL/TP on ENTRY candle ──────────────────
        # The entry candle's high/low may already hit SL/TP.
        # This is realistic: you enter, then the same candle
        # can take out your stop or hit your target.
        if executor.is_in_position:
            entry_closed = None
            if pos.direction == "LONG":
                if candle_low <= pos.catastrophic_sl:
                    entry_closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                elif candle_low <= pos.current_sl:
                    entry_closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                elif candle_high >= pos.current_tp:
                    entry_closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
            else:  # SHORT
                if candle_high >= pos.catastrophic_sl:
                    entry_closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                elif candle_high >= pos.current_sl:
                    entry_closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                elif candle_low <= pos.current_tp:
                    entry_closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
            if entry_closed:
                pnl = entry_closed.pnl_pct or 0.0
                stats.all_pnl.append(pnl)
                stats.trades_total += 1
                if pnl > 0.01:
                    stats.trades_won += 1
                    stats.wins.append(pnl)
                elif pnl < -0.01:
                    stats.trades_lost += 1
                    stats.losses.append(pnl)
                else:
                    stats.trades_be += 1
                executor._position = None
                continue  # Skip walk-forward and divergence checks

        # ─── Walk-Forward check ─────────────────────────────
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
                updated = executor.check_walk_forward(current_sax, current_price)

        # ─── Divergence check ──────────────────────────────
        # NOTE: pattern_break_score is always 0.0 in current engine
        # (not yet implemented). A score of 0.0 triggers immediate
        # divergence close (score < 0.3 = unknown block), which gives
        # PnL ≈ 0% on the entry candle. Skip divergence check until
        # pattern_break_score is properly implemented.
        # if executor.is_in_position and result is not None:
        #     _break_score = 1.0
        #     ...

    # ─── Close any remaining position at end ────────────────
    if executor.is_in_position and executor._position:
        last_price = float(oos_df["close"].iloc[-1])
        closed = executor.force_close(last_price, "REPLAY_END")
        pnl = closed.pnl_pct or 0.0
        stats.all_pnl.append(pnl)
        stats.trades_total += 1
        if pnl > 0.01:
            stats.trades_won += 1
        elif pnl < -0.01:
            stats.trades_lost += 1
        else:
            stats.trades_be += 1

    stats.total_candles = stats.oos_candles
    stats.elapsed_seconds = time.time() - t0
    return stats


# ─── Report ─────────────────────────────────────────────────────

def print_report(all_stats: list[ReplayStats]):
    """Print comprehensive benchmark report."""
    print("\n" + "=" * 80)
    print("  PPMT REPLAY BENCHMARK — 5m OUT-OF-SAMPLE (7 DAYS)")
    print("=" * 80)
    print(f"  Capital: ${CAPITAL_USDT:.0f}/token | Risk: {RISK_PCT*100:.0f}%/trade | EV Gate: {EV_THRESHOLD}")
    print(f"  Timeframe: {TIMEFRAME} | OOS: {OOS_DAYS} days")
    print("=" * 80)

    for s in all_stats:
        print(f"\n{'─'*80}")
        print(f"  {s.symbol} ({s.timeframe})")
        print(f"{'─'*80}")

        print(f"\n  TRIE DATA:")
        print(f"    N1 (Universal):  {s.trie_n1_patterns:>6} patterns")
        print(f"    N2 (Class):      {s.trie_n2_patterns:>6} patterns")
        print(f"    N3 (Symbol):     {s.trie_n3_patterns:>6} patterns")
        print(f"    N4 (Regime):     {s.trie_n4_patterns:>6} patterns")

        print(f"\n  1. Velas procesadas:     {s.total_candles:>6}")
        print(f"  2. Señales raw:          {s.total_signals_raw:>6}")
        print(f"  3. Señales pasaron EV:   {s.signals_passed_ev:>6}")
        print(f"  4. Trades ejecutados:    {s.trades_total:>6}")
        print(f"  5. Wins / Losses / BE:   {s.trades_won} / {s.trades_lost} / {s.trades_be}")
        print(f"  6. Win Rate:             {s.win_rate:>6.1f}%")
        print(f"  7. P&L total:            {s.total_pnl_pct:>+6.2f}%")
        print(f"  8. P&L en R:             {s.pnl_in_r:>+6.2f}R")

        if s.passed_confidences:
            print(f"  9. Confidence promedio:  {s.avg_confidence:>6.3f}")
            print(f"     Confidence rango:     [{min(s.passed_confidences):.3f} — {max(s.passed_confidences):.3f}]")
        else:
            print(f"  9. Confidence promedio:  N/A (0 trades)")

        print(f"\n  10. RAZÓN DE RECHAZO:")
        total_rejected = s.signals_rejected_spread + s.signals_rejected_ev_score + s.signals_rejected_overlap
        print(f"      Total rechazadas:    {total_rejected} / {s.total_signals_raw} ({s.rejection_rate:.1f}%)")
        print(f"      - Spread insuf.:     {s.signals_rejected_spread}")
        print(f"      - EV bajo:           {s.signals_rejected_ev_score}")
        print(f"      - Overlap:           {s.signals_rejected_overlap}")

        if s.trades_total > 0:
            print(f"\n  TRADE DETAIL:")
            if s.wins:
                print(f"      Avg win:  +{np.mean(s.wins):.2f}%  (best: +{max(s.wins):.2f}%)")
            if s.losses:
                print(f"      Avg loss: {np.mean(s.losses):.2f}%  (worst: {min(s.losses):.2f}%)")
            if s.wins and s.losses:
                print(f"      Avg W/L ratio: {abs(np.mean(s.wins)/np.mean(s.losses)):.2f}:1")

        print(f"\n  Tiempo: {s.elapsed_seconds:.1f}s ({s.total_candles/s.elapsed_seconds:.0f} candles/s)")

    # ─── Summary table ─────────────────────────────────────
    print(f"\n{'='*80}")
    print("  RESUMEN COMPARATIVO")
    print(f"{'='*80}")
    print(f"  {'Token':<14} {'Señales':>8} {'Pasaron':>8} {'Trades':>8} {'WR%':>7} {'P&L%':>8} {'R':>7} {'Conf':>6}")
    print(f"  {'─'*14} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*8} {'─'*7} {'─'*6}")
    for s in all_stats:
        print(
            f"  {s.symbol:<14} "
            f"{s.total_signals_raw:>8} "
            f"{s.signals_passed_ev:>8} "
            f"{s.trades_total:>8} "
            f"{s.win_rate:>6.1f}% "
            f"{s.total_pnl_pct:>+7.2f}% "
            f"{s.pnl_in_r:>+6.2f}R "
            f"{s.avg_confidence:>5.3f}"
        )

    # Aggregate
    tot_sig = sum(s.total_signals_raw for s in all_stats)
    tot_pass = sum(s.signals_passed_ev for s in all_stats)
    tot_trades = sum(s.trades_total for s in all_stats)
    tot_wins = sum(s.trades_won for s in all_stats)
    agg_wr = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0.0
    agg_pnl = sum(s.total_pnl_pct for s in all_stats)
    agg_r = sum(s.pnl_in_r for s in all_stats)
    all_confs = [c for s in all_stats for c in s.passed_confidences]
    agg_conf = np.mean(all_confs) if all_confs else 0.0

    print(f"  {'─'*14} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*8} {'─'*7} {'─'*6}")
    print(
        f"  {'AGGREGATE':<14} "
        f"{tot_sig:>8} "
        f"{tot_pass:>8} "
        f"{tot_trades:>8} "
        f"{agg_wr:>6.1f}% "
        f"{agg_pnl:>+7.2f}% "
        f"{agg_r:>+6.2f}R "
        f"{agg_conf:>5.3f}"
    )
    print(f"{'='*80}\n")


# ─── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PPMT Replay Benchmark 5m")
    parser.add_argument("--tokens", nargs="+", default=DEFAULT_TOKENS, help="Tokens to benchmark")
    parser.add_argument("--skip-build", action="store_true", help="Skip trie building (use existing DB)")
    parser.add_argument("--skip-download", action="store_true", help="Skip data download (use existing DB)")
    parser.add_argument("--oos-days", type=int, default=OOS_DAYS, help="Out-of-sample days")
    args = parser.parse_args()

    storage = PPMTStorage()
    classifier = AssetClassifier()

    # ─── Phase 1: Download data ─────────────────────────────
    if not args.skip_download:
        print(f"\n{'='*80}")
        print("  PHASE 1: Downloading OHLCV data from Binance")
        print(f"{'='*80}")

        exchange = _DirectPollExchange("binance")

        for symbol in args.tokens:
            api_symbol = symbol.replace("/", "")
            print(f"\n  Downloading {symbol} ({TIMEFRAME}, ~90 days)...")

            try:
                # Fetch 1000 candles (most recent) — ~3.5 days for 5m
                # Need more: do multiple requests
                all_data = []
                # Start from 90 days ago
                end_ms = int(time.time() * 1000)
                # 5m = 300,000 ms per candle. 90 days = 90*24*12 = 25,920 candles
                # We'll fetch in batches going backwards
                current_end = end_ms
                total_fetched = 0
                target_candles = 26000  # ~90 days of 5m

                while total_fetched < target_candles:
                    # Calculate start time for this batch
                    batch_size = 1000
                    start_ms = current_end - (batch_size * 300000)  # 5m in ms

                    # Use fetch_ohlcv with startTime
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
                        logger.warning(f"  HTTP {resp.status_code} for {symbol}")
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

                    # Move end time to before this batch
                    current_end = int(data[0][0]) - 1

                    if len(parsed) < batch_size:
                        break  # No more data

                    print(f"    Fetched {total_fetched:,} candles...")

                if all_data:
                    # Remove duplicates and sort
                    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                    df.set_index("timestamp", inplace=True)
                    df = df[~df.index.duplicated(keep="first")]
                    df.sort_index(inplace=True)
                    print(f"    Total: {len(df):,} candles ({df.index[0]} → {df.index[-1]})")

                    # Save to DB
                    storage.save_ohlcv(symbol, TIMEFRAME, df)
                    print(f"    Saved to DB ✓")
                else:
                    print(f"    No data for {symbol}!")

            except Exception as e:
                logger.error(f"  Download failed for {symbol}: {e}")

        try:
            asyncio.run(exchange.close())
        except Exception:
            pass

    # ─── Phase 2: Build tries ───────────────────────────────
    if not args.skip_build:
        print(f"\n{'='*80}")
        print("  PHASE 2: Building tries (in-sample only)")
        print(f"{'='*80}")

        for symbol in args.tokens:
            info = classifier.classify(symbol)
            print(f"\n  {symbol}: class={info.asset_class}, profile={info.weight_profile}")

            # Load OHLCV
            df = storage.load_ohlcv(symbol, TIMEFRAME)
            if df is None or len(df) < 100:
                print(f"    SKIP: insufficient data ({len(df) if df is not None else 0} candles)")
                continue

            # Split: IS = all but last 7 days, OOS = last 7 days
            oos_start = df.index[-1] - pd.Timedelta(days=args.oos_days)
            is_df = df[df.index < oos_start]
            oos_df = df[df.index >= oos_start]

            print(f"    IS: {len(is_df)} candles | OOS: {len(oos_df)} candles")

            if len(is_df) < 200:
                print(f"    SKIP: IS data too short ({len(is_df)} candles)")
                continue

            # Build tries
            counts = build_tries_for_symbol(storage, symbol, info.asset_class, info.weight_profile, is_df, TIMEFRAME)
            print(f"    Tries: N1={counts['n1']} N2={counts['n2']} N3={counts['n3']} N4={counts['n4']}")

    # ─── Phase 3: Run replay ────────────────────────────────
    print(f"\n{'='*80}")
    print("  PHASE 3: Running OOS replay")
    print(f"{'='*80}")

    all_stats = []

    for symbol in args.tokens:
        info = classifier.classify(symbol)
        print(f"\n  Replaying {symbol}...")

        # Load OOS data
        df = storage.load_ohlcv(symbol, TIMEFRAME)
        if df is None or len(df) < 50:
            print(f"    SKIP: insufficient data")
            continue

        # Split IS/OOS
        oos_start = df.index[-1] - pd.Timedelta(days=args.oos_days)
        oos_df = df[df.index >= oos_start]

        if len(oos_df) < 10:
            print(f"    SKIP: OOS data too short ({len(oos_df)} candles)")
            continue

        print(f"    OOS: {len(oos_df)} candles from {oos_df.index[0]} to {oos_df.index[-1]}")

        # Run replay
        stats = run_replay(symbol, oos_df, storage, info.asset_class, info.weight_profile, TIMEFRAME)
        all_stats.append(stats)

    # ─── Phase 4: Report ────────────────────────────────────
    if all_stats:
        print_report(all_stats)
    else:
        print("\n  No tokens had enough data for replay.")


if __name__ == "__main__":
    main()
