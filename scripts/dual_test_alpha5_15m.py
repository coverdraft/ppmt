#!/usr/bin/env python3
"""
PPMT Dual Test: α=5 (5m) vs α=3 (15m)

TEST 1: 5m with α=5 for N3/N4 + SL fix + EV=0.50
  - Rebuilds tries with α=5 for N3/N4 in 5m
  - Uses SL = max(1.2×expected_move, drawdown_pct×1.1)
  - EV threshold = 0.50 (lowered to compensate for lower confidence with α=5)
  - Weight profile: N2=0%, N3=55%, N4=35%

TEST 2: 15m with α=3 (default α=4) + SL fix + EV=0.80
  - Builds tries for 15m timeframe (W=12/12/6/6, P=5/5/3/3)
  - Uses SL fix (max drawdown)
  - EV threshold = 0.80
  - Standard weight profiles

Usage:
    python scripts/dual_test_alpha5_15m.py
    python scripts/dual_test_alpha5_15m.py --skip-download
    python scripts/dual_test_alpha5_15m.py --test1-only
    python scripts/dual_test_alpha5_15m.py --test2-only

No commits. Solo números.
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dual_test")

# ─── Configuration ──────────────────────────────────────────────

DEFAULT_TOKENS = ["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"]
CAPITAL_USDT = 1000.0
RISK_PCT = 0.01
OOS_DAYS = 7
IS_DAYS = 83

# Test 1 config
TEST1_TIMEFRAME = "5m"
TEST1_EV_THRESHOLD = 0.80
TEST1_ALPHA_N3_N4 = 3  # α=3 for N3/N4 (was α=5)

# Test 2 config
TEST2_TIMEFRAME = "15m"
TEST2_EV_THRESHOLD = 0.80

# Storage keys for α=3 tries
# α=3 is the default for 5m, so we can use the standard "5m" key
# But keep separate key to avoid overwriting existing α=3 tries during build
TEST1_TF_KEY = "5m_a3"


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
    n1_confidence: float = 0.0
    n2_confidence: float = 0.0
    n3_confidence: float = 0.0
    n4_confidence: float = 0.0
    w_n1: float = 0.0
    w_n2: float = 0.0
    w_n3: float = 0.0
    w_n4: float = 0.0
    sl_distance_pct: float = 0.0
    drawdown_sl_pct: float = 0.0
    sl_used_pct: float = 0.0  # Which SL was actually used


@dataclass
class ReplayStats:
    """Statistics for a single token replay."""
    symbol: str = ""
    timeframe: str = ""
    test_label: str = ""
    total_candles: int = 0
    total_signals_raw: int = 0
    signals_passed_ev: int = 0
    signals_rejected_spread: int = 0
    signals_rejected_ev_score: int = 0
    signals_rejected_overlap: int = 0
    trades_total: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    trades_be: int = 0
    wins: list[float] = field(default_factory=list)
    losses: list[float] = field(default_factory=list)
    all_pnl: list[float] = field(default_factory=list)
    passed_confidences: list[float] = field(default_factory=list)
    all_trades: list[TradeRecord] = field(default_factory=list)
    trie_n1_patterns: int = 0
    trie_n2_patterns: int = 0
    trie_n3_patterns: int = 0
    trie_n4_patterns: int = 0
    elapsed_seconds: float = 0.0
    # v2.1 FIX: Regime distribution and N4 direction tracking
    regime_counts: dict = field(default_factory=lambda: {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0})
    n4_direction_counts: dict = field(default_factory=lambda: {"LONG": 0, "SHORT": 0, "FLAT": 0})

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
    def direction_accuracy(self) -> float:
        """% of trades where MFE > MAE (price moved more in our direction than against)."""
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

    # Calculate candle duration in ms
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
    storage_tf_key: str = "",
    alpha_override_n3_n4: int | None = None,
) -> dict:
    """
    Build N1/N2/N3/N4 tries from in-sample data.

    Args:
        alpha_override_n3_n4: If set, overrides N3/N4 price alpha to this value.
        storage_tf_key: Key for storing tries (defaults to timeframe).
    """
    # If α override requested, temporarily modify the config
    saved_n3_config = None
    saved_n4_config = None
    saved_tf_overrides = None

    if alpha_override_n3_n4 is not None:
        # Save current config
        saved_n3_config = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
        saved_n4_config = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
        saved_tf_overrides = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)

        # Override N3/N4 price alpha
        LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": alpha_override_n3_n4, "volume": 0}
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": alpha_override_n3_n4, "volume": 0}

        # Clear TF overrides that might conflict
        for tf_key in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
            for lvl in ["n3", "n4"]:
                LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_key].pop(lvl, None)

        logger.info(f"  α override: N3/N4 price_alpha={alpha_override_n3_n4}")

    try:
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

        # Store tries with custom key
        tf_key = storage_tf_key or timeframe

        if engine.trie_n1 and engine.trie_n1.pattern_count > 0:
            storage.save_trie(UNIVERSAL_POOL_KEY, "n1", engine.trie_n1, timeframe=tf_key)
        if engine.trie_n2 and engine.trie_n2.pattern_count > 0:
            storage.save_trie(class_pool_key(asset_class), "n2", engine.trie_n2, timeframe=tf_key)
        if engine.trie_n3 and engine.trie_n3.pattern_count > 0:
            storage.save_trie(symbol, "n3", engine.trie_n3, timeframe=tf_key)
        if engine.trie_n4 and engine.trie_n4.pattern_count > 0:
            storage.save_trie(symbol, "n4", engine.trie_n4, timeframe=tf_key)

        return {
            "n1": engine.trie_n1.pattern_count if engine.trie_n1 else 0,
            "n2": engine.trie_n2.pattern_count if engine.trie_n2 else 0,
            "n3": engine.trie_n3.pattern_count if engine.trie_n3 else 0,
            "n4": engine.trie_n4.pattern_count if engine.trie_n4 else 0,
        }

    finally:
        # Restore original config
        if saved_n3_config is not None:
            LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3_config
            LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4_config
        if saved_tf_overrides is not None:
            LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
            LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_overrides)


# ─── Replay Engine ──────────────────────────────────────────────

def run_replay(
    symbol: str,
    oos_df: pd.DataFrame,
    storage: PPMTStorage,
    asset_class: str,
    weight_profile: str,
    timeframe: str,
    ev_threshold: float,
    storage_tf_key: str = "",
    alpha_override_n3_n4: int | None = None,
    test_label: str = "",
) -> ReplayStats:
    """
    Run OOS replay for a single token.

    Includes:
      - SL fix: max(1.2×expected_move, drawdown_pct×1.1)
      - MFE/MAE tracking per trade
      - Direction accuracy measurement
    """
    stats = ReplayStats(
        symbol=symbol,
        timeframe=timeframe,
        test_label=test_label,
    )
    t0 = time.time()

    # Load tries
    tf_key = storage_tf_key or timeframe
    tries = storage.load_all_tries(symbol, asset_class, timeframe=tf_key)
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
        logger.error(f"  No tries found for {symbol} (tf_key={tf_key})! Skipping.")
        return stats

    # If α override, temporarily modify config for engine creation
    saved_n3_config = None
    saved_n4_config = None
    saved_tf_overrides = None

    if alpha_override_n3_n4 is not None:
        saved_n3_config = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
        saved_n4_config = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
        saved_tf_overrides = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)

        LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": alpha_override_n3_n4, "volume": 0}
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": alpha_override_n3_n4, "volume": 0}

        for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
            for lvl in ["n3", "n4"]:
                LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)

    try:
        engine = PPMT(
            symbol=symbol,
            asset_class=asset_class,
            weight_profile=weight_profile,
            dual_sax=True,
            min_confidence=0.08,
            timeframe=timeframe,
        )

        # Ensure 5m override weights are active
        engine.weights = AdaptiveWeights.from_profile(weight_profile, timeframe=timeframe)

        engine.set_tries(
            trie_n1=n1_trie if n1_trie else PPMTTrie(name="empty_n1"),
            trie_n2=n2_trie if n2_trie else PPMTTrie(name="empty_n2"),
            trie_n3=n3_trie if n3_trie else PPMTTrie(name="empty_n3"),
            trie_n4=n4_trie if n4_trie else engine.trie_n4,
        )

    finally:
        # Restore config
        if saved_n3_config is not None:
            LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3_config
            LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4_config
        if saved_tf_overrides is not None:
            LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
            LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_overrides)

    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None

    spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)

    # ── v2.1 FIX: Regime detection for N4 ─────────────────────────
    # BUG: Without set_regime(), N4's RegimePartitionedTrie defaults
    # to _current_regime="trending_up" → 100% LONG bias.
    # Fix: detect regime from rolling window of recent candles.
    from ppmt.core.regime import RegimeDetector
    _regime_detector = RegimeDetector()
    _regime_window: list[dict] = []
    _REGIME_WINDOW_SIZE = 10  # same as trie build uses
    _regime_counts = {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0}
    _n4_direction_counts = {"LONG": 0, "SHORT": 0, "FLAT": 0}

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
                stats.trades_total += 1
                if pnl > 0.01:
                    stats.trades_won += 1
                    stats.wins.append(pnl)
                elif pnl < -0.01:
                    stats.trades_lost += 1
                    stats.losses.append(pnl)
                else:
                    stats.trades_be += 1

                if active_trade is not None:
                    active_trade.exit_price = closed.close_price or current_price
                    active_trade.exit_time = str(ts)
                    active_trade.close_reason = closed.close_reason or "UNKNOWN"
                    active_trade.pnl_pct = pnl
                    # Final MFE/MAE update
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

            # ── v2.1 FIX: Update regime for N4 before processing ──
            _regime_window.append({
                "open": float(row["open"].iloc[0]),
                "high": float(row["high"].iloc[0]),
                "low": float(row["low"].iloc[0]),
                "close": float(row["close"].iloc[0]),
                "volume": float(row["volume"].iloc[0]),
            })
            if len(_regime_window) > _REGIME_WINDOW_SIZE:
                _regime_window = _regime_window[-_REGIME_WINDOW_SIZE:]
            if len(_regime_window) >= 2:
                try:
                    _rw_df = pd.DataFrame(_regime_window)
                    _detected = _regime_detector.detect_simple(_rw_df, timeframe=timeframe)
                    _regime_counts[_detected] += 1
                    engine.set_regime(_detected)
                except Exception:
                    _regime_counts["ranging"] += 1
                    engine.set_regime("ranging")

            result = engine.process_new_candle(
                candle_df=row,
                current_price=current_price,
                is_in_position=executor.is_in_position,
                entry_price=executor.position.entry_price if executor.position else None,
            )

        if result is None:
            continue

        # ── v2.1: Track N4 direction on every result ──
        _n4_dir = result.n4_direction if hasattr(result, 'n4_direction') and result.n4_direction else "FLAT"
        _n4_direction_counts[_n4_dir] = _n4_direction_counts.get(_n4_dir, 0) + 1

        sig = result.signal if result and result.signal else None
        if sig is None or not sig.is_entry:
            continue
        if executor.is_in_position:
            continue

        stats.total_signals_raw += 1

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
        stats.passed_confidences.append(sig.confidence)

        direction = sig.direction or "LONG"
        expected_move_pct = sig.expected_move_pct or 1.0
        size_usdt = CAPITAL_USDT * RISK_PCT / (abs(expected_move_pct) * 0.012)
        size_usdt = min(size_usdt, CAPITAL_USDT)

        n1_c = result.n1_confidence if result else 0.0
        n2_c = result.n2_confidence if result else 0.0
        n3_c = result.n3_confidence if result else 0.0
        n4_c = result.n4_confidence if result else 0.0

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
        drawdown_sl_pct = drawdown_pct * 1.1  # 10% buffer over observed max drawdown

        sl_used_pct = current_sl_distance_pct  # Default: 1.2×expected_move

        if drawdown_sl_pct > current_sl_distance_pct:
            extra_distance = drawdown_sl_pct - current_sl_distance_pct
            if pos.direction == "LONG":
                pos.current_sl -= pos.entry_price * (extra_distance / 100.0)
                pos.catastrophic_sl -= pos.entry_price * (extra_distance / 100.0)
            else:
                pos.current_sl += pos.entry_price * (extra_distance / 100.0)
                pos.catastrophic_sl += pos.entry_price * (extra_distance / 100.0)
            sl_used_pct = drawdown_sl_pct  # Drawdown-based SL used

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
            n1_confidence=n1_c,
            n2_confidence=n2_c,
            n3_confidence=n3_c,
            n4_confidence=n4_c,
            w_n1=engine.weights.n1_universal,
            w_n2=engine.weights.n2_asset_class,
            w_n3=engine.weights.n3_per_asset,
            w_n4=engine.weights.n4_per_asset_regime,
            sl_distance_pct=current_sl_distance_pct,
            drawdown_sl_pct=drawdown_sl_pct,
            sl_used_pct=sl_used_pct,
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
                stats.trades_total += 1
                if pnl > 0.01:
                    stats.trades_won += 1
                    stats.wins.append(pnl)
                elif pnl < -0.01:
                    stats.trades_lost += 1
                    stats.losses.append(pnl)
                else:
                    stats.trades_be += 1

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
        stats.trades_total += 1
        if pnl > 0.01:
            stats.trades_won += 1
        elif pnl < -0.01:
            stats.trades_lost += 1
        else:
            stats.trades_be += 1

        if active_trade is not None:
            active_trade.exit_price = closed.close_price or last_price
            active_trade.exit_time = str(oos_df.index[-1])
            active_trade.close_reason = "REPLAY_END"
            active_trade.pnl_pct = pnl
            active_trade.candles_held = len(oos_df) - 1 - active_trade_entry_idx
            stats.all_trades.append(active_trade)
            active_trade = None

    stats.elapsed_seconds = time.time() - t0
    # v2.1: Transfer regime and N4 direction counts
    stats.regime_counts = dict(_regime_counts)
    stats.n4_direction_counts = dict(_n4_direction_counts)
    return stats


# ─── Report ─────────────────────────────────────────────────────

def print_test_report(all_stats: list[ReplayStats], test_label: str, ev_threshold: float):
    """Print comprehensive test report."""
    print(f"\n{'='*90}")
    print(f"  {test_label}")
    print(f"  EV Threshold: {ev_threshold} | SL Fix: max(1.2×EM, DD×1.1)")
    print(f"{'='*90}")

    for s in all_stats:
        print(f"\n{'─'*90}")
        print(f"  {s.symbol} ({s.timeframe})")
        print(f"{'─'*90}")

        print(f"\n  TRIE DATA:")
        print(f"    N1 (Universal):  {s.trie_n1_patterns:>6} patterns")
        print(f"    N2 (Class):      {s.trie_n2_patterns:>6} patterns")
        print(f"    N3 (Symbol):     {s.trie_n3_patterns:>6} patterns")
        print(f"    N4 (Regime):     {s.trie_n4_patterns:>6} patterns")

        # v2.1: Regime distribution
        rc = s.regime_counts or {}
        rc_total = sum(rc.values()) or 1
        print(f"\n  REGIME DISTRIBUTION:")
        for regime in ["trending_up", "trending_down", "ranging", "volatile"]:
            cnt = rc.get(regime, 0)
            pct = cnt / rc_total * 100
            print(f"    {regime:20s}: {cnt:>6} ({pct:5.1f}%)")

        # v2.1: N4 direction counts
        n4d = s.n4_direction_counts or {}
        n4d_total = sum(n4d.values()) or 1
        print(f"\n  N4 DIRECTION:")
        for d in ["LONG", "SHORT", "FLAT"]:
            cnt = n4d.get(d, 0)
            pct = cnt / n4d_total * 100
            print(f"    {d:20s}: {cnt:>6} ({pct:5.1f}%)")

        print(f"\n  SIGNALS:")
        print(f"    Raw signals:          {s.total_signals_raw:>6}")
        print(f"    Passed EV gate:       {s.signals_passed_ev:>6}")
        print(f"    Rejected (spread):    {s.signals_rejected_spread:>6}")
        print(f"    Rejected (EV):        {s.signals_rejected_ev_score:>6}")
        print(f"    Rejected (overlap):   {s.signals_rejected_overlap:>6}")

        print(f"\n  TRADES:")
        print(f"    Total:       {s.trades_total:>6}")
        print(f"    Won/Lost/BE: {s.trades_won}/{s.trades_lost}/{s.trades_be}")
        print(f"    Win Rate:    {s.win_rate:>6.1f}%")
        print(f"    P&L total:   {s.total_pnl_pct:>+6.2f}%")
        print(f"    Direction:   {s.direction_accuracy:>6.1f}% (MFE>MAE)")

        # v2.1: LONG vs SHORT breakdown
        if s.all_trades:
            long_trades = [t for t in s.all_trades if t.direction == "LONG"]
            short_trades = [t for t in s.all_trades if t.direction == "SHORT"]
            long_wins = sum(1 for t in long_trades if t.pnl_pct > 0.01)
            short_wins = sum(1 for t in short_trades if t.pnl_pct > 0.01)
            long_pnl = sum(t.pnl_pct for t in long_trades)
            short_pnl = sum(t.pnl_pct for t in short_trades)
            print(f"\n  LONG vs SHORT:")
            print(f"    LONG:  {len(long_trades):>4} trades | WR={long_wins/max(len(long_trades),1)*100:.1f}% | P&L={long_pnl:>+.2f}%")
            print(f"    SHORT: {len(short_trades):>4} trades | WR={short_wins/max(len(short_trades),1)*100:.1f}% | P&L={short_pnl:>+.2f}%")

        if s.passed_confidences:
            print(f"\n  CONFIDENCE:")
            print(f"    Avg:   {s.avg_confidence:.4f}")
            print(f"    Range: [{min(s.passed_confidences):.4f} — {max(s.passed_confidences):.4f}]")

        # Per-trade detail for direction analysis
        if s.all_trades:
            print(f"\n  PER-TRADE DETAIL:")
            for t in s.all_trades:
                dir_str = "LONG" if t.direction == "LONG" else "SHORT"
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
                    f"{t.close_reason}"
                )

    # ─── Summary table ─────────────────────────────────────
    print(f"\n{'='*90}")
    print(f"  SUMMARY — {test_label}")
    print(f"{'='*90}")
    print(
        f"  {'Token':<14} {'RawSig':>7} {'Passed':>7} {'Trades':>7} "
        f"{'WR%':>6} {'P&L%':>8} {'Dir%':>6} {'Conf':>6}"
    )
    print(f"  {'─'*14} {'─'*7} {'─'*7} {'─'*7} {'─'*6} {'─'*8} {'─'*6} {'─'*6}")

    for s in all_stats:
        print(
            f"  {s.symbol:<14} "
            f"{s.total_signals_raw:>7} "
            f"{s.signals_passed_ev:>7} "
            f"{s.trades_total:>7} "
            f"{s.win_rate:>5.1f}% "
            f"{s.total_pnl_pct:>+7.2f}% "
            f"{s.direction_accuracy:>5.1f}% "
            f"{s.avg_confidence:>5.3f}"
        )

    # Aggregate
    tot_sig = sum(s.total_signals_raw for s in all_stats)
    tot_pass = sum(s.signals_passed_ev for s in all_stats)
    tot_trades = sum(s.trades_total for s in all_stats)
    tot_wins = sum(s.trades_won for s in all_stats)
    agg_wr = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0.0
    agg_pnl = sum(s.total_pnl_pct for s in all_stats)
    agg_dir = np.mean([s.direction_accuracy for s in all_stats if s.all_trades]) if any(s.all_trades for s in all_stats) else 0.0
    all_confs = [c for s in all_stats for c in s.passed_confidences]
    agg_conf = np.mean(all_confs) if all_confs else 0.0

    print(f"  {'─'*14} {'─'*7} {'─'*7} {'─'*7} {'─'*6} {'─'*8} {'─'*6} {'─'*6}")
    print(
        f"  {'AGGREGATE':<14} "
        f"{tot_sig:>7} "
        f"{tot_pass:>7} "
        f"{tot_trades:>7} "
        f"{agg_wr:>5.1f}% "
        f"{agg_pnl:>+7.2f}% "
        f"{agg_dir:>5.1f}% "
        f"{agg_conf:>5.3f}"
    )
    print(f"{'='*90}\n")

    return {
        "raw_signals": tot_sig,
        "passed": tot_pass,
        "trades": tot_trades,
        "win_rate": agg_wr,
        "pnl": agg_pnl,
        "direction_accuracy": agg_dir,
        "avg_confidence": agg_conf,
    }


def print_comparison(test1_summary: dict, test2_summary: dict):
    """Print side-by-side comparison."""
    print(f"\n{'='*90}")
    print(f"  COMPARISON: 5m α=5 vs 15m α=3")
    print(f"{'='*90}")
    print(
        f"  {'Metric':<25} {'5m α=5 EV=0.50':>20} {'15m α=3 EV=0.80':>20} {'Better':>12}"
    )
    print(f"  {'─'*25} {'─'*20} {'─'*20} {'─'*12}")

    metrics = [
        ("Raw signals", "raw_signals", False),
        ("Passed EV", "passed", False),
        ("Trades", "trades", False),
        ("Win Rate %", "win_rate", True),
        ("P&L %", "pnl", True),
        ("Direction Accuracy %", "direction_accuracy", True),
        ("Avg Confidence", "avg_confidence", True),
    ]

    for label, key, higher_better in metrics:
        v1 = test1_summary.get(key, 0)
        v2 = test2_summary.get(key, 0)
        if higher_better:
            better = "5m" if v1 > v2 else ("15m" if v2 > v1 else "TIE")
        else:
            better = "—"

        if isinstance(v1, float):
            print(f"  {label:<25} {v1:>20.2f} {v2:>20.2f} {better:>12}")
        else:
            print(f"  {label:<25} {v1:>20} {v2:>20} {better:>12}")

    print(f"{'='*90}\n")


# ─── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PPMT Dual Test: α=5 (5m) vs 15m")
    parser.add_argument("--tokens", nargs="+", default=DEFAULT_TOKENS, help="Tokens to test")
    parser.add_argument("--skip-download", action="store_true", help="Skip data download")
    parser.add_argument("--skip-build", action="store_true", help="Skip trie building")
    parser.add_argument("--test1-only", action="store_true", help="Only run 5m α=5 test")
    parser.add_argument("--test2-only", action="store_true", help="Only run 15m test")
    args = parser.parse_args()

    storage = PPMTStorage()
    classifier = AssetClassifier()

    # ─── Phase 1: Download data for BOTH timeframes ──────────
    if not args.skip_download:
        for tf in [TEST1_TIMEFRAME, TEST2_TIMEFRAME]:
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

    # ─── Phase 2A: Build α=5 tries for 5m ────────────────────
    test1_stats = []

    if not args.test2_only:
        if not args.skip_build:
            print(f"\n{'='*80}")
            print(f"  BUILDING TRIES: 5m with α=5 for N3/N4")
            print(f"{'='*80}")

            for symbol in args.tokens:
                info = classifier.classify(symbol)
                print(f"\n  {symbol}: class={info.asset_class}, profile={info.weight_profile}")

                df = storage.load_ohlcv(symbol, TEST1_TIMEFRAME)
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
                    timeframe=TEST1_TIMEFRAME,
                    storage_tf_key=TEST1_TF_KEY,
                    alpha_override_n3_n4=TEST1_ALPHA_N3_N4,
                )
                print(f"    Tries (α=5): N1={counts['n1']} N2={counts['n2']} N3={counts['n3']} N4={counts['n4']}")

        # ─── Phase 3A: Run 5m α=5 replay ────────────────────
        print(f"\n{'='*80}")
        print(f"  REPLAY: 5m α=5 + SL fix + EV={TEST1_EV_THRESHOLD}")
        print(f"{'='*80}")

        for symbol in args.tokens:
            info = classifier.classify(symbol)
            print(f"\n  Replaying {symbol} (5m α=5)...")

            df = storage.load_ohlcv(symbol, TEST1_TIMEFRAME)
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
                timeframe=TEST1_TIMEFRAME,
                ev_threshold=TEST1_EV_THRESHOLD,
                storage_tf_key=TEST1_TF_KEY,
                alpha_override_n3_n4=TEST1_ALPHA_N3_N4,
                test_label=f"5m α=5 EV={TEST1_EV_THRESHOLD}",
            )
            test1_stats.append(stats)

    # ─── Phase 2B: Build tries for 15m ──────────────────────
    test2_stats = []

    if not args.test1_only:
        if not args.skip_build:
            print(f"\n{'='*80}")
            print(f"  BUILDING TRIES: 15m (default α=4)")
            print(f"{'='*80}")

            for symbol in args.tokens:
                info = classifier.classify(symbol)
                print(f"\n  {symbol}: class={info.asset_class}, profile={info.weight_profile}")

                df = storage.load_ohlcv(symbol, TEST2_TIMEFRAME)
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
                    timeframe=TEST2_TIMEFRAME,
                    storage_tf_key=TEST2_TIMEFRAME,  # Standard key for 15m
                    alpha_override_n3_n4=None,  # Default α
                )
                print(f"    Tries: N1={counts['n1']} N2={counts['n2']} N3={counts['n3']} N4={counts['n4']}")

        # ─── Phase 3B: Run 15m replay ──────────────────────
        print(f"\n{'='*80}")
        print(f"  REPLAY: 15m α=3 + SL fix + EV={TEST2_EV_THRESHOLD}")
        print(f"{'='*80}")

        for symbol in args.tokens:
            info = classifier.classify(symbol)
            print(f"\n  Replaying {symbol} (15m)...")

            df = storage.load_ohlcv(symbol, TEST2_TIMEFRAME)
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
                timeframe=TEST2_TIMEFRAME,
                ev_threshold=TEST2_EV_THRESHOLD,
                storage_tf_key=TEST2_TIMEFRAME,
                alpha_override_n3_n4=None,
                test_label=f"15m EV={TEST2_EV_THRESHOLD}",
            )
            test2_stats.append(stats)

    # ─── Phase 4: Reports ────────────────────────────────────
    test1_summary = {"raw_signals": 0, "passed": 0, "trades": 0, "win_rate": 0, "pnl": 0, "direction_accuracy": 0, "avg_confidence": 0}
    test2_summary = {"raw_signals": 0, "passed": 0, "trades": 0, "win_rate": 0, "pnl": 0, "direction_accuracy": 0, "avg_confidence": 0}

    if test1_stats:
        test1_summary = print_test_report(
            test1_stats,
            f"TEST 1: 5m α={TEST1_ALPHA_N3_N4} + SL fix + EV={TEST1_EV_THRESHOLD} + N2=0%",
            TEST1_EV_THRESHOLD,
        )

    if test2_stats:
        test2_summary = print_test_report(
            test2_stats,
            f"TEST 2: 15m α=3 (default) + SL fix + EV={TEST2_EV_THRESHOLD}",
            TEST2_EV_THRESHOLD,
        )

    # ─── Phase 5: Comparison ─────────────────────────────────
    if test1_stats and test2_stats:
        print_comparison(test1_summary, test2_summary)


if __name__ == "__main__":
    main()
