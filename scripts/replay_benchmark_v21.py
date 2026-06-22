#!/usr/bin/env python3
"""
PPMT Replay Benchmark — TERMINAL v2.1 + Weighted Direction Vote

Fix: hard_move_floor per timeframe + short_allowed=True for memes
New: Weighted Direction Vote (v0.44.0)

Parameters:
  - α=3 (default, unchanged)
  - Weight profile: N2=0%, N3=55%, N4=35% (5m micro-structure-first)
  - SL fix: max(1.2×expected_move, drawdown_pct×1.1)
  - hard_move_floor per timeframe: 5m=0.15, 15m=0.20, 1m=0.10
  - EV threshold: 0.80
  - Weighted Direction Vote: each level votes direction_edge = ±1 × confidence
  - 4 tokens (BTC, SOL, DOGE, LINK), 7d OOS
  - short_allowed=True for ALL tokens (including DOGE/memes)

Reports:
  - Raw signals: LONG vs SHORT vs FLAT (by weighted direction vote)
  - LONG vs SHORT after all filters
  - Trades: WR by direction (WR LONG, WR SHORT)
  - P&L by direction
  - Comparison vs without direction vote
"""

from __future__ import annotations

import argparse
import asyncio
import copy
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
from ppmt.core.sax import (
    LEVEL_DUAL_ALPHA_CONFIG,
    LEVEL_DUAL_ALPHA_TF_OVERRIDES,
    LEVEL_WINDOW_CONFIG,
    LEVEL_PATTERN_CONFIG,
    get_dual_alpha_for_level,
)
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
from ppmt.core.thresholds import (
    SignalThresholds,
    get_hard_move_floor,
    get_ranging_move_floor,
    get_volatile_move_floor,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("replay_v21")

# ─── Configuration ──────────────────────────────────────────────

DEFAULT_TOKENS = ["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"]
CAPITAL_USDT = 1000.0
RISK_PCT = 0.01
OOS_DAYS = 7
IS_DAYS = 83

TIMEFRAME = "5m"
EV_THRESHOLD = 0.80

# Per-timeframe hard_move_floor (the fix)
HARD_MOVE_FLOOR = {
    "1m": 0.10,
    "5m": 0.15,
    "15m": 0.20,
}


# ─── Data Classes ───────────────────────────────────────────────

@dataclass
class TradeRecord:
    """Detailed record of a single trade for analysis."""
    trade_id: int = 0
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    entry_time: str = ""
    sl_price: float = 0.0
    tp_price: float = 0.0
    cat_sl_price: float = 0.0
    expected_move_pct: float = 0.0
    exit_price: float = 0.0
    exit_time: str = ""
    close_reason: str = ""
    pnl_pct: float = 0.0
    max_favorable_excursion_pct: float = 0.0
    max_adverse_excursion_pct: float = 0.0
    candles_held: int = 0
    confidence: float = 0.0
    favorable_pct_node: float = 0.0
    drawdown_pct_node: float = 0.0
    net_ev: float = 0.0
    sl_distance_pct: float = 0.0
    drawdown_sl_pct: float = 0.0
    sl_used_pct: float = 0.0
    direction_score: float = 0.0
    n1_direction: str = ""
    n3_direction: str = ""
    n4_direction: str = ""


@dataclass
class ReplayStats:
    """Statistics for a single token replay — with direction breakdown."""
    symbol: str = ""
    timeframe: str = ""
    total_candles: int = 0

    # Raw signals (before ANY filter) — by direction
    raw_signals_long: int = 0
    raw_signals_short: int = 0
    raw_signals_flat: int = 0

    # Weighted direction vote breakdown
    raw_vote_agrees: int = 0     # Vote agrees with best-match P7
    raw_vote_disagrees: int = 0  # Vote disagrees with best-match P7
    raw_vote_n1_long: int = 0
    raw_vote_n1_short: int = 0
    raw_vote_n3_long: int = 0
    raw_vote_n3_short: int = 0
    raw_vote_n4_long: int = 0
    raw_vote_n4_short: int = 0

    # After all engine filters (hard_move_floor, regime gates, etc.)
    filtered_signals_long: int = 0
    filtered_signals_short: int = 0

    # After EV gate
    signals_passed_ev: int = 0
    signals_passed_ev_long: int = 0
    signals_passed_ev_short: int = 0
    signals_rejected_spread: int = 0
    signals_rejected_ev_score: int = 0
    signals_rejected_overlap: int = 0

    # Trades by direction
    trades_long: int = 0
    trades_short: int = 0
    trades_long_won: int = 0
    trades_short_won: int = 0
    trades_long_lost: int = 0
    trades_short_lost: int = 0

    # P&L by direction
    pnl_long: list[float] = field(default_factory=list)
    pnl_short: list[float] = field(default_factory=list)
    all_pnl: list[float] = field(default_factory=list)

    passed_confidences: list[float] = field(default_factory=list)
    all_trades: list[TradeRecord] = field(default_factory=list)
    trie_n1_patterns: int = 0
    trie_n2_patterns: int = 0
    trie_n3_patterns: int = 0
    trie_n4_patterns: int = 0
    elapsed_seconds: float = 0.0

    @property
    def total_signals_raw(self) -> int:
        return self.raw_signals_long + self.raw_signals_short + self.raw_signals_flat

    @property
    def trades_total(self) -> int:
        return self.trades_long + self.trades_short

    @property
    def trades_won(self) -> int:
        return self.trades_long_won + self.trades_short_won

    @property
    def trades_lost(self) -> int:
        return self.trades_long_lost + self.trades_short_lost

    @property
    def win_rate(self) -> float:
        return (self.trades_won / self.trades_total * 100) if self.trades_total > 0 else 0.0

    @property
    def win_rate_long(self) -> float:
        return (self.trades_long_won / self.trades_long * 100) if self.trades_long > 0 else 0.0

    @property
    def win_rate_short(self) -> float:
        return (self.trades_short_won / self.trades_short * 100) if self.trades_short > 0 else 0.0

    @property
    def total_pnl_pct(self) -> float:
        return sum(self.all_pnl) if self.all_pnl else 0.0

    @property
    def pnl_long_total(self) -> float:
        return sum(self.pnl_long) if self.pnl_long else 0.0

    @property
    def pnl_short_total(self) -> float:
        return sum(self.pnl_short) if self.pnl_short else 0.0

    @property
    def avg_confidence(self) -> float:
        return np.mean(self.passed_confidences) if self.passed_confidences else 0.0

    @property
    def direction_accuracy(self) -> float:
        if not self.all_trades:
            return 0.0
        correct = sum(1 for t in self.all_trades if t.max_favorable_excursion_pct > t.max_adverse_excursion_pct)
        return correct / len(self.all_trades) * 100.0


# ─── Data Download ──────────────────────────────────────────────

async def download_ohlcv(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Download OHLCV data from Binance."""
    exchange = _DirectPollExchange("binance")
    api_symbol = symbol.replace("/", "")

    all_data = []
    end_ms = int(time.time() * 1000)

    tf_ms = {
        "1m": 60000, "5m": 300000, "15m": 900000,
        "30m": 1800000, "1h": 3600000, "4h": 14400000, "1d": 86400000,
    }
    candle_ms = tf_ms.get(timeframe, 300000)

    current_end = end_ms
    total_fetched = 0
    target_candles = (days * 24 * 3600 * 1000) // candle_ms

    while total_fetched < target_candles:
        batch_size = 1000
        start_ms = current_end - (batch_size * candle_ms)

        import requests
        url = f"{exchange.base_url}/api/v3/klines"
        params = {
            "symbol": api_symbol,
            "interval": timeframe,
            "limit": batch_size,
            "startTime": start_ms,
            "endTime": current_end,
        }

        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"HTTP {resp.status_code} for {symbol}")
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

            print(f"    Fetched {total_fetched:,} candles ({timeframe})...")

        except Exception as e:
            logger.warning(f"Download error for {symbol}: {e}")
            break

    try:
        await exchange.close()
    except Exception:
        pass

    if not all_data:
        logger.error(f"No data downloaded for {symbol}")
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    df.sort_index(inplace=True)

    return df


# ─── Trie Building ──────────────────────────────────────────────

def build_tries_for_test(
    storage: PPMTStorage,
    symbol: str,
    asset_class: str,
    weight_profile: str,
    is_df: pd.DataFrame,
    timeframe: str,
) -> dict:
    """Build N1/N2/N3/N4 tries from in-sample data using α=3 (default)."""

    engine = PPMT(
        symbol=symbol,
        asset_class=asset_class,
        weight_profile=weight_profile,
        dual_sax=True,
        min_confidence=0.08,
        timeframe=timeframe,
    )
    build_count = engine.build(is_df)
    logger.info(f"  {symbol}: built {build_count} patterns from {len(is_df)} IS candles")

    if engine.trie_n1 and engine.trie_n1.pattern_count > 0:
        storage.save_trie(UNIVERSAL_POOL_KEY, "n1", engine.trie_n1, timeframe=timeframe)
    if engine.trie_n2 and engine.trie_n2.pattern_count > 0:
        storage.save_trie(class_pool_key(asset_class), "n2", engine.trie_n2, timeframe=timeframe)
    if engine.trie_n3 and engine.trie_n3.pattern_count > 0:
        storage.save_trie(symbol, "n3", engine.trie_n3, timeframe=timeframe)
    if engine.trie_n4 and engine.trie_n4.pattern_count > 0:
        storage.save_trie(symbol, "n4", engine.trie_n4, timeframe=timeframe)

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
    ev_threshold: float,
) -> ReplayStats:
    """
    Run OOS replay for a single token with direction tracking.

    Includes:
      - SL fix: max(1.2×expected_move, drawdown_pct×1.1)
      - Per-timeframe hard_move_floor
      - Direction tracking (LONG vs SHORT) at every filter stage
    """
    stats = ReplayStats(symbol=symbol, timeframe=timeframe)
    t0 = time.time()

    # Load tries
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

    engine = PPMT(
        symbol=symbol,
        asset_class=asset_class,
        weight_profile=weight_profile,
        dual_sax=True,
        min_confidence=0.08,
        timeframe=timeframe,
    )

    # Ensure 5m override weights are active (N2=0%, N3=55%, N4=35%)
    engine.weights = AdaptiveWeights.from_profile(weight_profile, timeframe=timeframe)

    engine.set_tries(
        trie_n1=n1_trie if n1_trie else PPMTTrie(name="empty_n1"),
        trie_n2=n2_trie if n2_trie else PPMTTrie(name="empty_n2"),
        trie_n3=n3_trie if n3_trie else PPMTTrie(name="empty_n3"),
        trie_n4=n4_trie if n4_trie else engine.trie_n4,
    )

    # Get per-timeframe move floors
    hmf = HARD_MOVE_FLOOR.get(timeframe, 0.15)
    logger.info(f"  hard_move_floor for {timeframe} = {hmf}%")

    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None

    spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)

    # Track active trade for MFE/MAE
    active_trade: Optional[TradeRecord] = None
    active_trade_entry_idx: int = 0
    trade_counter = 0

    stats.total_candles = len(oos_df)
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
                pnl = closed.pnl_pct or 0.0
                stats.all_pnl.append(pnl)

                # Direction tracking
                if closed.direction == "LONG":
                    stats.trades_long += 1
                    stats.pnl_long.append(pnl)
                    if pnl > 0.01:
                        stats.trades_long_won += 1
                    elif pnl < -0.01:
                        stats.trades_long_lost += 1
                else:
                    stats.trades_short += 1
                    stats.pnl_short.append(pnl)
                    if pnl > 0.01:
                        stats.trades_short_won += 1
                    elif pnl < -0.01:
                        stats.trades_short_lost += 1

                if active_trade is not None:
                    active_trade.exit_price = closed.close_price or current_price
                    active_trade.exit_time = str(ts)
                    active_trade.close_reason = closed.close_reason or "UNKNOWN"
                    active_trade.pnl_pct = pnl
                    if closed.direction == "LONG":
                        fm = (candle_high - closed.entry_price) / closed.entry_price * 100.0
                        am = (closed.entry_price - candle_low) / closed.entry_price * 100.0
                    else:
                        fm = (closed.entry_price - candle_low) / closed.entry_price * 100.0
                        am = (candle_high - closed.entry_price) / closed.entry_price * 100.0
                    active_trade.max_favorable_excursion_pct = max(active_trade.max_favorable_excursion_pct, fm)
                    active_trade.max_adverse_excursion_pct = max(active_trade.max_adverse_excursion_pct, am)
                    active_trade.candles_held = idx - active_trade_entry_idx
                    stats.all_trades.append(active_trade)
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

        # ─── v0.44.0: Use Weighted Direction Vote ──────────
        # result.direction comes from the weighted vote across N1/N2/N3/N4.
        # Each level votes direction_edge = ±1 × confidence, weighted by profile.
        # This replaces the single-level direction from sig.direction.
        direction = result.direction if result.direction and result.direction != "FLAT" else (sig.direction or "LONG")

        # Track vote agreement with single-level P7
        sig_direction = sig.direction or "LONG"
        if direction == sig_direction:
            stats.raw_vote_agrees += 1
        else:
            stats.raw_vote_disagrees += 1

        # Track per-level direction from the vote
        if result.n1_direction == "LONG":
            stats.raw_vote_n1_long += 1
        elif result.n1_direction == "SHORT":
            stats.raw_vote_n1_short += 1
        if result.n3_direction == "LONG":
            stats.raw_vote_n3_long += 1
        elif result.n3_direction == "SHORT":
            stats.raw_vote_n3_short += 1
        if result.n4_direction == "LONG":
            stats.raw_vote_n4_long += 1
        elif result.n4_direction == "SHORT":
            stats.raw_vote_n4_short += 1
        if direction == "LONG":
            stats.raw_signals_long += 1
        elif direction == "SHORT":
            stats.raw_signals_short += 1
        else:
            stats.raw_signals_flat += 1

        # ─── Per-timeframe hard_move_floor filter ─────────
        # This is the v2.1 fix — use per-timeframe floor instead of flat 0.05/0.5
        expected_move_abs = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.0
        if expected_move_abs < hmf:
            # Signal rejected by move floor — already counted as raw
            continue

        # Track signals that passed engine-level filters
        if direction == "LONG":
            stats.filtered_signals_long += 1
        elif direction == "SHORT":
            stats.filtered_signals_short += 1

        # ─── Net EV Gate ──────────────────────────────────
        _best_node = None
        for _mr, _lvl in [(result.n3_match, "N3"), (result.n1_match, "N1"),
                           (result.n2_match, "N2"), (result.n4_match, "N4")]:
            if _mr and _mr.node and _mr.node.metadata and _mr.node.metadata.historical_count > 0:
                _best_node = _mr.node
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
            stats.signals_rejected_spread += 1
            continue

        net_rr = net_favorable / drawdown_pct
        net_rr_capped = min(net_rr, 3.0)
        net_ev = sig.confidence * net_rr_capped

        if net_ev < ev_threshold:
            stats.signals_rejected_ev_score += 1
            continue

        # ─── PASSED EV Gate ───────────────────────────────
        stats.signals_passed_ev += 1
        if direction == "LONG":
            stats.signals_passed_ev_long += 1
        else:
            stats.signals_passed_ev_short += 1
        stats.passed_confidences.append(sig.confidence)

        expected_move_pct = sig.expected_move_pct or 1.0
        size_usdt = CAPITAL_USDT * RISK_PCT / (abs(expected_move_pct) * 0.012)
        size_usdt = min(size_usdt, CAPITAL_USDT)

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
            stats.signals_rejected_overlap += 1
            continue

        # ─── SL FIX: max(1.2×expected_move, drawdown_pct×1.1) ──
        current_sl_distance_pct = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
        drawdown_sl_pct = drawdown_pct * 1.1

        sl_used_pct = current_sl_distance_pct

        if drawdown_sl_pct > current_sl_distance_pct:
            extra_distance = drawdown_sl_pct - current_sl_distance_pct
            if pos.direction == "LONG":
                pos.current_sl -= pos.entry_price * (extra_distance / 100.0)
                pos.catastrophic_sl -= pos.entry_price * (extra_distance / 100.0)
            else:
                pos.current_sl += pos.entry_price * (extra_distance / 100.0)
                pos.catastrophic_sl += pos.entry_price * (extra_distance / 100.0)
            sl_used_pct = drawdown_sl_pct

        # Record trade
        trade_counter += 1
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
            confidence=sig.confidence,
            favorable_pct_node=favorable_pct,
            drawdown_pct_node=drawdown_pct,
            net_ev=net_ev,
            sl_distance_pct=current_sl_distance_pct,
            drawdown_sl_pct=drawdown_sl_pct,
            sl_used_pct=sl_used_pct,
            direction_score=result.direction_score if result else 0.0,
            n1_direction=result.n1_direction if result else "",
            n3_direction=result.n3_direction if result else "",
            n4_direction=result.n4_direction if result else "",
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
                pnl = entry_closed.pnl_pct or 0.0
                stats.all_pnl.append(pnl)

                if direction == "LONG":
                    stats.trades_long += 1
                    stats.pnl_long.append(pnl)
                    if pnl > 0.01:
                        stats.trades_long_won += 1
                    elif pnl < -0.01:
                        stats.trades_long_lost += 1
                else:
                    stats.trades_short += 1
                    stats.pnl_short.append(pnl)
                    if pnl > 0.01:
                        stats.trades_short_won += 1
                    elif pnl < -0.01:
                        stats.trades_short_lost += 1

                tr.exit_price = entry_closed.close_price or current_price
                tr.exit_time = str(ts)
                tr.close_reason = entry_closed.close_reason or "ENTRY_CANDLE"
                tr.pnl_pct = pnl
                tr.candles_held = 0
                stats.all_trades.append(tr)
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
        pnl = closed.pnl_pct or 0.0
        stats.all_pnl.append(pnl)

        direction = closed.direction or "LONG"
        if direction == "LONG":
            stats.trades_long += 1
            stats.pnl_long.append(pnl)
            if pnl > 0.01:
                stats.trades_long_won += 1
            elif pnl < -0.01:
                stats.trades_long_lost += 1
        else:
            stats.trades_short += 1
            stats.pnl_short.append(pnl)
            if pnl > 0.01:
                stats.trades_short_won += 1
            elif pnl < -0.01:
                stats.trades_short_lost += 1

        if active_trade is not None:
            active_trade.exit_price = closed.close_price or last_price
            active_trade.exit_time = str(oos_df.index[-1])
            active_trade.close_reason = "REPLAY_END"
            active_trade.pnl_pct = pnl
            active_trade.candles_held = len(oos_df) - 1 - active_trade_entry_idx
            stats.all_trades.append(active_trade)
            active_trade = None

    stats.elapsed_seconds = time.time() - t0
    return stats


# ─── Report ─────────────────────────────────────────────────────

def print_report(all_stats: list[ReplayStats], ev_threshold: float, tf: str):
    """Print comprehensive report with direction breakdown."""
    hmf = HARD_MOVE_FLOOR.get(tf, 0.15)

    print(f"\n{'='*100}")
    print(f"  TERMINAL v2.1 REPLAY BENCHMARK + WEIGHTED DIRECTION VOTE")
    print(f"  α=3 | N1=10% N2=0% N3=55% N4=35% | SL fix=max(1.2×EM, DD×1.1) | hard_move_floor={hmf}% ({tf}) | EV={ev_threshold}")
    print(f"  short_allowed=True for ALL tokens (including memes)")
    print(f"  Direction: Weighted Vote (N1×0.10 + N3×0.55 + N4×0.35 × direction_edge)")
    print(f"{'='*100}")

    for s in all_stats:
        print(f"\n{'─'*100}")
        print(f"  {s.symbol} ({s.timeframe})")
        print(f"{'─'*100}")

        print(f"\n  TRIE DATA:")
        print(f"    N1 (Universal):  {s.trie_n1_patterns:>6} patterns")
        print(f"    N2 (Class):      {s.trie_n2_patterns:>6} patterns")
        print(f"    N3 (Symbol):     {s.trie_n3_patterns:>6} patterns")
        print(f"    N4 (Regime):     {s.trie_n4_patterns:>6} patterns")

        print(f"\n  RAW SIGNALS (before any filter — WEIGHTED DIRECTION VOTE):")
        print(f"    LONG:  {s.raw_signals_long:>6}")
        print(f"    SHORT: {s.raw_signals_short:>6}")
        print(f"    FLAT:  {s.raw_signals_flat:>6}")
        print(f"    Total: {s.total_signals_raw:>6}")
        print(f"    Vote agrees with single-level P7:     {s.raw_vote_agrees:>6}")
        print(f"    Vote disagrees with single-level P7:  {s.raw_vote_disagrees:>6}")
        print(f"\n  PER-LEVEL DIRECTION (raw signals):")
        print(f"    N1: LONG={s.raw_vote_n1_long} SHORT={s.raw_vote_n1_short}")
        print(f"    N3: LONG={s.raw_vote_n3_long} SHORT={s.raw_vote_n3_short}")
        print(f"    N4: LONG={s.raw_vote_n4_long} SHORT={s.raw_vote_n4_short}")

        print(f"\n  AFTER ENGINE FILTERS (hard_move_floor={hmf}%):")
        print(f"    LONG:  {s.filtered_signals_long:>6}")
        print(f"    SHORT: {s.filtered_signals_short:>6}")

        print(f"\n  AFTER EV GATE (EV >= {ev_threshold}):")
        print(f"    Passed LONG:  {s.signals_passed_ev_long:>6}")
        print(f"    Passed SHORT: {s.signals_passed_ev_short:>6}")
        print(f"    Rejected (spread):    {s.signals_rejected_spread:>6}")
        print(f"    Rejected (EV):        {s.signals_rejected_ev_score:>6}")
        print(f"    Rejected (overlap):   {s.signals_rejected_overlap:>6}")

        print(f"\n  TRADES BY DIRECTION:")
        print(f"    LONG:  {s.trades_long:>4} trades | WR {s.win_rate_long:>5.1f}% | P&L {s.pnl_long_total:>+7.2f}%")
        print(f"    SHORT: {s.trades_short:>4} trades | WR {s.win_rate_short:>5.1f}% | P&L {s.pnl_short_total:>+7.2f}%")
        print(f"    Total: {s.trades_total:>4} trades | WR {s.win_rate:>5.1f}% | P&L {s.total_pnl_pct:>+7.2f}%")
        print(f"    Direction accuracy: {s.direction_accuracy:.1f}% (MFE>MAE)")

        if s.passed_confidences:
            print(f"\n  CONFIDENCE:")
            print(f"    Avg:   {s.avg_confidence:.4f}")
            print(f"    Range: [{min(s.passed_confidences):.4f} — {max(s.passed_confidences):.4f}]")

        # Per-trade detail
        if s.all_trades:
            print(f"\n  PER-TRADE DETAIL:")
            for t in s.all_trades:
                dir_str = t.direction
                correct = "OK" if t.max_favorable_excursion_pct > t.max_adverse_excursion_pct else "WRONG"
                result_str = "WIN" if t.pnl_pct > 0.01 else ("LOSS" if t.pnl_pct < -0.01 else "BE")
                sl_type = "DD" if t.sl_used_pct == t.drawdown_sl_pct else "EM"
                print(
                    f"    #{t.trade_id:2d} {t.symbol:<12} {dir_str:<5} "
                    f"Entry={t.entry_price:>10.4f} "
                    f"EM={t.expected_move_pct:>5.2f}% "
                    f"SL={t.sl_used_pct:>5.2f}%({sl_type}) "
                    f"MFE={t.max_favorable_excursion_pct:>5.2f}% "
                    f"MAE={t.max_adverse_excursion_pct:>5.2f}% "
                    f"{correct:<5} {result_str:<4} "
                    f"PnL={t.pnl_pct:>+6.2f}% "
                    f"Conf={t.confidence:.3f} "
                    f"EV={t.net_ev:.3f} "
                    f"DS={t.direction_score:>+.3f} "
                    f"N1={t.n1_direction or '-':<5} N3={t.n3_direction or '-':<5} N4={t.n4_direction or '-':<5} "
                    f"{t.close_reason}"
                )

    # ─── Summary table ─────────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  SUMMARY — TERMINAL v2.1 + WEIGHTED DIRECTION VOTE")
    print(f"{'='*100}")
    print(
        f"  {'Token':<14} {'RawL':>5} {'RawS':>5} {'FiltL':>5} {'FiltS':>5} "
        f"{'TrL':>4} {'TrS':>4} {'WRL%':>6} {'WRS%':>6} {'PnLL%':>8} {'PnLS%':>8} {'PnL%':>8}"
    )
    print(f"  {'─'*14} {'─'*5} {'─'*5} {'─'*5} {'─'*5} {'─'*4} {'─'*4} {'─'*6} {'─'*6} {'─'*8} {'─'*8} {'─'*8}")

    for s in all_stats:
        print(
            f"  {s.symbol:<14} "
            f"{s.raw_signals_long:>5} "
            f"{s.raw_signals_short:>5} "
            f"{s.filtered_signals_long:>5} "
            f"{s.filtered_signals_short:>5} "
            f"{s.trades_long:>4} "
            f"{s.trades_short:>4} "
            f"{s.win_rate_long:>5.1f}% "
            f"{s.win_rate_short:>5.1f}% "
            f"{s.pnl_long_total:>+7.2f}% "
            f"{s.pnl_short_total:>+7.2f}% "
            f"{s.total_pnl_pct:>+7.2f}%"
        )

    # Aggregate
    agg_raw_l = sum(s.raw_signals_long for s in all_stats)
    agg_raw_s = sum(s.raw_signals_short for s in all_stats)
    agg_filt_l = sum(s.filtered_signals_long for s in all_stats)
    agg_filt_s = sum(s.filtered_signals_short for s in all_stats)
    agg_tr_l = sum(s.trades_long for s in all_stats)
    agg_tr_s = sum(s.trades_short for s in all_stats)
    agg_won_l = sum(s.trades_long_won for s in all_stats)
    agg_won_s = sum(s.trades_short_won for s in all_stats)
    agg_pnl_l = sum(s.pnl_long_total for s in all_stats)
    agg_pnl_s = sum(s.pnl_short_total for s in all_stats)
    agg_wr_l = (agg_won_l / agg_tr_l * 100) if agg_tr_l > 0 else 0.0
    agg_wr_s = (agg_won_s / agg_tr_s * 100) if agg_tr_s > 0 else 0.0
    agg_pnl = sum(s.total_pnl_pct for s in all_stats)

    print(f"  {'─'*14} {'─'*5} {'─'*5} {'─'*5} {'─'*5} {'─'*4} {'─'*4} {'─'*6} {'─'*6} {'─'*8} {'─'*8} {'─'*8}")
    print(
        f"  {'AGGREGATE':<14} "
        f"{agg_raw_l:>5} "
        f"{agg_raw_s:>5} "
        f"{agg_filt_l:>5} "
        f"{agg_filt_s:>5} "
        f"{agg_tr_l:>4} "
        f"{agg_tr_s:>4} "
        f"{agg_wr_l:>5.1f}% "
        f"{agg_wr_s:>5.1f}% "
        f"{agg_pnl_l:>+7.2f}% "
        f"{agg_pnl_s:>+7.2f}% "
        f"{agg_pnl:>+7.2f}%"
    )
    print(f"{'='*100}\n")

    return {
        "raw_long": agg_raw_l,
        "raw_short": agg_raw_s,
        "filtered_long": agg_filt_l,
        "filtered_short": agg_filt_s,
        "trades_long": agg_tr_l,
        "trades_short": agg_tr_s,
        "wr_long": agg_wr_l,
        "wr_short": agg_wr_s,
        "pnl_long": agg_pnl_l,
        "pnl_short": agg_pnl_s,
        "pnl_total": agg_pnl,
    }


# ─── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PPMT TERMINAL v2.1 Replay Benchmark")
    parser.add_argument("--tokens", nargs="+", default=DEFAULT_TOKENS, help="Tokens to test")
    parser.add_argument("--skip-download", action="store_true", help="Skip data download")
    parser.add_argument("--skip-build", action="store_true", help="Skip trie building")
    parser.add_argument("--timeframe", default=TIMEFRAME, help="Timeframe (default: 5m)")
    parser.add_argument("--ev", type=float, default=EV_THRESHOLD, help="EV threshold (default: 0.80)")
    args = parser.parse_args()

    tf = args.timeframe
    ev = args.ev

    storage = PPMTStorage()
    classifier = AssetClassifier()

    # ─── Phase 1: Download data ───────────────────────────
    if not args.skip_download:
        print(f"\n{'='*80}")
        print(f"  DOWNLOADING: {tf} data from Binance")
        print(f"{'='*80}")

        for symbol in args.tokens:
            print(f"\n  Downloading {symbol} ({tf}, ~90 days)...")
            df = asyncio.run(download_ohlcv(symbol, tf, 90))
            if len(df) > 0:
                storage.save_ohlcv(symbol, tf, df)
                print(f"    Saved {len(df):,} candles ({df.index[0]} → {df.index[-1]})")
            else:
                print(f"    No data for {symbol}!")

    # ─── Phase 2: Build tries ─────────────────────────────
    if not args.skip_build:
        print(f"\n{'='*80}")
        print(f"  BUILDING TRIES: {tf} with α=3 (default)")
        print(f"{'='*80}")

        for symbol in args.tokens:
            info = classifier.classify(symbol)
            print(f"\n  {symbol}: class={info.asset_class}, profile={info.weight_profile}")

            df = storage.load_ohlcv(symbol, tf)
            if df is None or len(df) < 100:
                print(f"    SKIP: insufficient data")
                continue

            oos_start = df.index[-1] - pd.Timedelta(days=OOS_DAYS)
            is_df = df[df.index < oos_start]

            print(f"    IS: {len(is_df)} candles")

            if len(is_df) < 200:
                print(f"    SKIP: IS too short")
                continue

            counts = build_tries_for_test(
                storage=storage,
                symbol=symbol,
                asset_class=info.asset_class,
                weight_profile=info.weight_profile,
                is_df=is_df,
                timeframe=tf,
            )
            print(f"    Tries: N1={counts['n1']} N2={counts['n2']} N3={counts['n3']} N4={counts['n4']}")

    # ─── Phase 3: Run replay ──────────────────────────────
    print(f"\n{'='*80}")
    print(f"  REPLAY: {tf} α=3 + SL fix + hard_move_floor={HARD_MOVE_FLOOR.get(tf, 0.15)}% + EV={ev}")
    print(f"{'='*80}")

    all_stats = []
    for symbol in args.tokens:
        info = classifier.classify(symbol)
        print(f"\n  Replaying {symbol} ({tf})...")

        df = storage.load_ohlcv(symbol, tf)
        if df is None or len(df) < 50:
            print(f"    SKIP: insufficient data")
            continue

        oos_start = df.index[-1] - pd.Timedelta(days=OOS_DAYS)
        oos_df = df[df.index >= oos_start]

        if len(oos_df) < 10:
            print(f"    SKIP: OOS too short ({len(oos_df)} candles)")
            continue

        print(f"    OOS: {len(oos_df)} candles from {oos_df.index[0]} to {oos_df.index[-1]}")

        stats = run_replay(
            symbol=symbol,
            oos_df=oos_df,
            storage=storage,
            asset_class=info.asset_class,
            weight_profile=info.weight_profile,
            timeframe=tf,
            ev_threshold=ev,
        )
        all_stats.append(stats)

    # ─── Phase 4: Report ──────────────────────────────────
    if all_stats:
        summary = print_report(all_stats, ev, tf)


if __name__ == "__main__":
    main()
