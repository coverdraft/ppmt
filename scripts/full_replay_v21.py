#!/usr/bin/env python3
"""
PPMT V2.1 Full Replay — Complete Fix Validation

Tests ALL v2.1 fixes together on 4 tokens, 5m, 30 days OOS:
  - hard_move_floor=0.15% for 5m
  - short_allowed=True for all asset classes
  - SL fix: max(1.2×expected_move, drawdown_pct×SL_MULT)
  - set_regime() called on every candle
  - Weighted direction vote connected
  - Weight profile: N2=0%, N3=55%, N4=35%

Then tests alternative configs (A/B/C/D) if baseline is not profitable.

Usage:
    python scripts/full_replay_v21.py
    python scripts/full_replay_v21.py --skip-download
    python scripts/full_replay_v21.py --config baseline
    python scripts/full_replay_v21.py --config A
    python scripts/full_replay_v21.py --config all
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
    get_dual_alpha_for_level,
)
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
from ppmt.core.thresholds import TIMEFRAME_HARD_MOVE_FLOOR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("full_replay")

# ─── Token Configuration ────────────────────────────────────────

TOKENS = ["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"]
CAPITAL_USDT = 1000.0
RISK_PCT = 0.01
OOS_DAYS = 30
IS_DAYS = 60
TIMEFRAME = "5m"

# ─── Config Presets ─────────────────────────────────────────────

@dataclass
class ReplayConfig:
    name: str
    alpha_n3_n4: int = 3
    ev_threshold: float = 0.80
    sl_mult: float = 1.1  # multiplier on drawdown_pct for SL
    weight_n1: float = 0.10
    weight_n2: float = 0.00
    weight_n3: float = 0.55
    weight_n4: float = 0.35
    weight_n5: float = 0.00
    hard_move_floor_5m: float = 0.15
    short_allowed_all: bool = True
    label: str = ""

CONFIGS = {
    "baseline": ReplayConfig(
        name="baseline",
        alpha_n3_n4=3,
        ev_threshold=0.80,
        sl_mult=1.1,
        weight_n1=0.10, weight_n2=0.00, weight_n3=0.55, weight_n4=0.35,
        label="Baseline: α=3, EV≥0.80, SL=1.1×DD, N3=55%/N4=35%",
    ),
    "A": ReplayConfig(
        name="A",
        alpha_n3_n4=5,
        ev_threshold=0.60,
        sl_mult=2.0,
        weight_n1=0.10, weight_n2=0.00, weight_n3=0.55, weight_n4=0.35,
        label="Config A: α=5, EV≥0.60, SL=2.0×DD, N3=55%/N4=35%",
    ),
    "B": ReplayConfig(
        name="B",
        alpha_n3_n4=3,
        ev_threshold=0.50,
        sl_mult=2.0,
        weight_n1=0.10, weight_n2=0.00, weight_n3=0.70, weight_n4=0.20,
        label="Config B: α=3, EV≥0.50, SL=2.0×DD, N3=70%/N4=20%",
    ),
    "C": ReplayConfig(
        name="C",
        alpha_n3_n4=3,
        ev_threshold=0.40,
        sl_mult=1.5,
        weight_n1=0.10, weight_n2=0.00, weight_n3=0.80, weight_n4=0.10,
        label="Config C: α=3, EV≥0.40, SL=1.5×DD, N3=80%/N4=10%",
    ),
    "D": ReplayConfig(
        name="D",
        alpha_n3_n4=3,
        ev_threshold=0.60,
        sl_mult=1.5,
        weight_n1=0.10, weight_n2=0.00, weight_n3=0.65, weight_n4=0.25,
        label="Config D: α=3, EV≥0.60, SL=1.5×DD, N3=65%/N4=25%",
    ),
    "E": ReplayConfig(
        name="E",
        alpha_n3_n4=5,
        ev_threshold=0.50,
        sl_mult=2.5,
        weight_n1=0.10, weight_n2=0.00, weight_n3=0.60, weight_n4=0.30,
        hard_move_floor_5m=0.10,
        label="Config E: α=5, EV≥0.50, SL=2.5×DD, N3=60%/N4=30%, floor=0.10%",
    ),
    "F": ReplayConfig(
        name="F",
        alpha_n3_n4=3,
        ev_threshold=0.40,
        sl_mult=2.0,
        weight_n1=0.10, weight_n2=0.00, weight_n3=0.90, weight_n4=0.00,
        hard_move_floor_5m=0.10,
        label="Config F: α=3, EV≥0.40, SL=2.0×DD, N3=90%/N4=0%, floor=0.10%",
    ),
}

# ─── Data Classes ────────────────────────────────────────────────

@dataclass
class TradeRecord:
    trade_id: int = 0
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    entry_time: str = ""
    sl_price: float = 0.0
    tp_price: float = 0.0
    expected_move_pct: float = 0.0
    exit_price: float = 0.0
    exit_time: str = ""
    close_reason: str = ""
    pnl_pct: float = 0.0
    confidence: float = 0.0
    net_ev: float = 0.0
    n1_confidence: float = 0.0
    n2_confidence: float = 0.0
    n3_confidence: float = 0.0
    n4_confidence: float = 0.0


@dataclass
class ReplayStats:
    symbol: str = ""
    config_name: str = ""
    total_candles: int = 0
    total_signals_raw: int = 0
    signals_passed_ev: int = 0
    signals_rejected_spread: int = 0
    signals_rejected_ev: int = 0
    trades_total: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    trades_long: int = 0
    trades_short: int = 0
    trades_long_won: int = 0
    trades_short_won: int = 0
    all_pnl: list[float] = field(default_factory=list)
    pnl_long: list[float] = field(default_factory=list)
    pnl_short: list[float] = field(default_factory=list)
    all_trades: list[TradeRecord] = field(default_factory=list)
    trie_n1: int = 0
    trie_n2: int = 0
    trie_n3: int = 0
    trie_n4: int = 0
    elapsed: float = 0.0
    regime_counts: dict = field(default_factory=lambda: {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0})

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
    def total_pnl(self) -> float:
        return sum(self.all_pnl) if self.all_pnl else 0.0

    @property
    def pnl_long_total(self) -> float:
        return sum(self.pnl_long) if self.pnl_long else 0.0

    @property
    def pnl_short_total(self) -> float:
        return sum(self.pnl_short) if self.pnl_short else 0.0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(p for p in self.all_pnl if p > 0)
        gross_loss = abs(sum(p for p in self.all_pnl if p < 0))
        return gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0.0

    @property
    def max_drawdown(self) -> float:
        if not self.all_pnl:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in self.all_pnl:
            cumulative += p
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)
        return max_dd


# ─── Data Download ──────────────────────────────────────────────

async def download_ohlcv(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Download OHLCV data from Binance."""
    exchange = _DirectPollExchange("binance")
    api_symbol = symbol.replace("/", "")

    all_data = []
    end_ms = int(time.time() * 1000)
    tf_ms = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000}
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
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    df.sort_index(inplace=True)
    return df


# ─── Trie Building ──────────────────────────────────────────────

def build_tries(storage: PPMTStorage, symbol: str, asset_class,
                is_df: pd.DataFrame, alpha_n3_n4: int = 3,
                storage_tf_key: str = "5m") -> dict:
    """Build N1-N4 tries from in-sample data."""
    # Extract string from AssetInfo if needed
    asset_class_str = asset_class.asset_class if hasattr(asset_class, 'asset_class') else str(asset_class)
    weight_profile = asset_class.weight_profile if hasattr(asset_class, 'weight_profile') else 'default'
    saved_n3 = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
    saved_n4 = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
    saved_tf = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)

    LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": alpha_n3_n4, "volume": 0}
    LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": alpha_n3_n4, "volume": 0}
    for tf_key in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
        for lvl in ["n3", "n4"]:
            LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_key].pop(lvl, None)

    try:
        engine = PPMT(
            symbol=symbol,
            asset_class=asset_class_str,
            weight_profile=weight_profile,
            dual_sax=True,
            min_confidence=0.08,
            timeframe=TIMEFRAME,
        )
        build_count = engine.build(is_df)
        logger.info(f"  {symbol}: built {build_count} patterns from {len(is_df)} IS candles (α={alpha_n3_n4})")

        tf_key = storage_tf_key
        if engine.trie_n1 and engine.trie_n1.pattern_count > 0:
            storage.save_trie(UNIVERSAL_POOL_KEY, "n1", engine.trie_n1, timeframe=tf_key)
        if engine.trie_n2 and engine.trie_n2.pattern_count > 0:
            storage.save_trie(class_pool_key(asset_class_str), "n2", engine.trie_n2, timeframe=tf_key)
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
        LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf)


# ─── Replay Engine ──────────────────────────────────────────────

def run_replay(
    symbol: str,
    oos_df: pd.DataFrame,
    storage: PPMTStorage,
    asset_class,
    config: ReplayConfig,
    storage_tf_key: str = "5m",
) -> ReplayStats:
    """Run OOS replay for a single token with a specific config."""
    # Extract string from AssetInfo if needed
    asset_class_str = asset_class.asset_class if hasattr(asset_class, 'asset_class') else str(asset_class)
    weight_profile = asset_class.weight_profile if hasattr(asset_class, 'weight_profile') else 'default'
    stats = ReplayStats(symbol=symbol, config_name=config.name)
    t0 = time.time()

    tf_key = storage_tf_key
    tries = storage.load_all_tries(symbol, asset_class_str, timeframe=tf_key)
    n1_trie = tries.get("n1")
    n2_trie = tries.get("n2")
    n3_trie = tries.get("n3")
    n4_trie = tries.get("n4")

    stats.trie_n1 = n1_trie.pattern_count if n1_trie else 0
    stats.trie_n2 = n2_trie.pattern_count if n2_trie else 0
    stats.trie_n3 = n3_trie.pattern_count if n3_trie else 0
    stats.trie_n4 = n4_trie.pattern_count if n4_trie else 0

    logger.info(f"  Tries: N1={stats.trie_n1} N2={stats.trie_n2} N3={stats.trie_n3} N4={stats.trie_n4}")

    if not n1_trie and not n2_trie and not n3_trie:
        logger.error(f"  No tries for {symbol}! Skipping.")
        return stats

    # Override alpha for engine creation
    saved_n3 = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
    saved_n4 = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
    saved_tf = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)
    saved_hmf = TIMEFRAME_HARD_MOVE_FLOOR.get(TIMEFRAME, 0.15)

    LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": config.alpha_n3_n4, "volume": 0}
    LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": config.alpha_n3_n4, "volume": 0}
    for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
        for lvl in ["n3", "n4"]:
            LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)

    # Override hard_move_floor if config specifies a different value
    TIMEFRAME_HARD_MOVE_FLOOR[TIMEFRAME] = config.hard_move_floor_5m

    try:
        engine = PPMT(
            symbol=symbol,
            asset_class=asset_class_str,
            weight_profile=weight_profile,
            dual_sax=True,
            min_confidence=0.08,
            timeframe=TIMEFRAME,
        )

        # Apply custom weights
        engine.weights = AdaptiveWeights(
            n1_universal=config.weight_n1,
            n2_asset_class=config.weight_n2,
            n3_per_asset=config.weight_n3,
            n4_per_asset_regime=config.weight_n4,
            n5_btc_context=config.weight_n5,
        )

        engine.set_tries(
            trie_n1=n1_trie if n1_trie else PPMTTrie(name="empty_n1"),
            trie_n2=n2_trie if n2_trie else PPMTTrie(name="empty_n2"),
            trie_n3=n3_trie if n3_trie else PPMTTrie(name="empty_n3"),
            trie_n4=n4_trie if n4_trie else engine.trie_n4,
        )
    finally:
        LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf)
        TIMEFRAME_HARD_MOVE_FLOOR[TIMEFRAME] = saved_hmf

    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None

    # Extract string from AssetInfo if needed
    asset_class_str = asset_class.asset_class if hasattr(asset_class, 'asset_class') else str(asset_class)
    spread_pct = SPREAD_ESTIMATES.get(asset_class_str, 0.050)

    # v2.1 FIX: Regime detection
    from ppmt.core.regime import RegimeDetector
    _regime_detector = RegimeDetector()
    _regime_window: list[dict] = []
    _REGIME_WINDOW_SIZE = 10
    _regime_counts = {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0}

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
                stats.all_pnl.append(pnl)
                stats.trades_total += 1
                if pnl > 0.01:
                    stats.trades_won += 1
                elif pnl < -0.01:
                    stats.trades_lost += 1

                if closed.direction == "LONG":
                    stats.trades_long += 1
                    stats.pnl_long.append(pnl)
                    if pnl > 0.01:
                        stats.trades_long_won += 1
                else:
                    stats.trades_short += 1
                    stats.pnl_short.append(pnl)
                    if pnl > 0.01:
                        stats.trades_short_won += 1

                if active_trade is not None:
                    active_trade.exit_price = closed.close_price or current_price
                    active_trade.exit_time = str(ts)
                    active_trade.close_reason = closed.close_reason or "UNKNOWN"
                    active_trade.pnl_pct = pnl
                    stats.all_trades.append(active_trade)
                    active_trade = None

                executor._position = None

        # Feed candle to engine
        result: Optional[PPMTResult] = None
        if ts_sec > _last_engine_ts:
            _last_engine_ts = ts_sec

            # v2.1 FIX: Regime detection
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
                    _detected = _regime_detector.detect_simple(_rw_df, timeframe=TIMEFRAME)
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

        sig = result.signal if result and result.signal else None
        if sig is None or not sig.is_entry:
            continue
        if executor.is_in_position:
            continue

        stats.total_signals_raw += 1

        # Net EV Gate
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

        if net_ev < config.ev_threshold:
            stats.signals_rejected_ev += 1
            continue

        stats.signals_passed_ev += 1

        direction = sig.direction or "LONG"
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
            continue

        # v2.1 FIX: SL = max(default 1.2×EM, drawdown_pct × SL_MULT)
        current_sl_distance_pct = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
        drawdown_sl_pct = drawdown_pct * config.sl_mult

        if drawdown_sl_pct > current_sl_distance_pct:
            extra_distance = drawdown_sl_pct - current_sl_distance_pct
            if pos.direction == "LONG":
                pos.current_sl -= pos.entry_price * (extra_distance / 100.0)
                pos.catastrophic_sl -= pos.entry_price * (extra_distance / 100.0)
            else:
                pos.current_sl += pos.entry_price * (extra_distance / 100.0)
                pos.catastrophic_sl += pos.entry_price * (extra_distance / 100.0)

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
            expected_move_pct=expected_move_pct,
            confidence=sig.confidence,
            net_ev=net_ev,
            n1_confidence=result.n1_confidence if result else 0.0,
            n2_confidence=result.n2_confidence if result else 0.0,
            n3_confidence=result.n3_confidence if result else 0.0,
            n4_confidence=result.n4_confidence if result else 0.0,
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
                elif pnl < -0.01:
                    stats.trades_lost += 1

                if entry_closed.direction == "LONG":
                    stats.trades_long += 1
                    stats.pnl_long.append(pnl)
                    if pnl > 0.01:
                        stats.trades_long_won += 1
                else:
                    stats.trades_short += 1
                    stats.pnl_short.append(pnl)
                    if pnl > 0.01:
                        stats.trades_short_won += 1

                tr.exit_price = entry_closed.close_price or current_price
                tr.exit_time = str(ts)
                tr.close_reason = entry_closed.close_reason or "ENTRY_CANDLE"
                tr.pnl_pct = pnl
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

        if closed.direction == "LONG":
            stats.trades_long += 1
            stats.pnl_long.append(pnl)
            if pnl > 0.01:
                stats.trades_long_won += 1
        else:
            stats.trades_short += 1
            stats.pnl_short.append(pnl)
            if pnl > 0.01:
                stats.trades_short_won += 1

        if active_trade is not None:
            active_trade.exit_price = closed.close_price or last_price
            active_trade.exit_time = str(oos_df.index[-1])
            active_trade.close_reason = "REPLAY_END"
            active_trade.pnl_pct = pnl
            stats.all_trades.append(active_trade)
            active_trade = None

    stats.elapsed = time.time() - t0
    stats.regime_counts = dict(_regime_counts)
    return stats


# ─── Report ─────────────────────────────────────────────────────

def print_config_report(all_stats: list[ReplayStats], config: ReplayConfig):
    """Print comprehensive report for a config."""
    print(f"\n{'='*80}")
    print(f"  CONFIG: {config.label}")
    print(f"  Timeframe: {TIMEFRAME} | OOS: {OOS_DAYS} days | IS: {IS_DAYS} days")
    print(f"  Tokens: {', '.join(TOKENS)}")
    print(f"{'='*80}")

    # Aggregate
    total_trades = sum(s.trades_total for s in all_stats)
    total_won = sum(s.trades_won for s in all_stats)
    total_lost = sum(s.trades_lost for s in all_stats)
    total_long = sum(s.trades_long for s in all_stats)
    total_short = sum(s.trades_short for s in all_stats)
    total_long_won = sum(s.trades_long_won for s in all_stats)
    total_short_won = sum(s.trades_short_won for s in all_stats)
    all_pnl = [p for s in all_stats for p in s.all_pnl]
    pnl_long = [p for s in all_stats for p in s.pnl_long]
    pnl_short = [p for s in all_stats for p in s.pnl_short]

    wr = (total_won / total_trades * 100) if total_trades > 0 else 0.0
    wr_long = (total_long_won / total_long * 100) if total_long > 0 else 0.0
    wr_short = (total_short_won / total_short * 100) if total_short > 0 else 0.0
    total_pnl = sum(all_pnl) if all_pnl else 0.0
    pnl_long_total = sum(pnl_long) if pnl_long else 0.0
    pnl_short_total = sum(pnl_short) if pnl_short else 0.0

    # Profit factor
    gross_profit = sum(p for p in all_pnl if p > 0)
    gross_loss = abs(sum(p for p in all_pnl if p < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0.0

    # Max drawdown
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in all_pnl:
        cumulative += p
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    print(f"\n  AGGREGATE RESULTS:")
    print(f"    Total Trades:    {total_trades}")
    print(f"    LONG / SHORT:    {total_long} / {total_short}")
    print(f"    Win Rate:        {wr:.1f}%")
    print(f"    WR LONG:         {wr_long:.1f}% ({total_long_won}/{total_long})")
    print(f"    WR SHORT:        {wr_short:.1f}% ({total_short_won}/{total_short})")
    print(f"    P&L Total:       {total_pnl:+.2f}%")
    print(f"    P&L LONG:        {pnl_long_total:+.2f}%")
    print(f"    P&L SHORT:       {pnl_short_total:+.2f}%")
    print(f"    Profit Factor:   {pf:.2f}")
    print(f"    Max Drawdown:    {max_dd:.2f}%")

    # Per-token breakdown
    for s in all_stats:
        print(f"\n  {s.symbol}:")
        print(f"    Trades: {s.trades_total} (L:{s.trades_long} S:{s.trades_short})  "
              f"WR: {s.win_rate:.1f}%  P&L: {s.total_pnl:+.2f}%  "
              f"Tries: N1={s.trie_n1} N2={s.trie_n2} N3={s.trie_n3} N4={s.trie_n4}")
        rc = s.regime_counts
        rc_total = sum(rc.values()) or 1
        print(f"    Regimes: UP={rc.get('trending_up',0)/rc_total*100:.0f}% "
              f"DOWN={rc.get('trending_down',0)/rc_total*100:.0f}% "
              f"RANGING={rc.get('ranging',0)/rc_total*100:.0f}% "
              f"VOLATILE={rc.get('volatile',0)/rc_total*100:.0f}%")

    # Verdict
    profitable = total_pnl > 0 and pf > 1.0
    print(f"\n  VERDICT: {'PROFITABLE' if profitable else 'NOT PROFITABLE'} "
          f"(P&L={total_pnl:+.2f}%, PF={pf:.2f})")

    return profitable, {
        "total_trades": total_trades,
        "long": total_long,
        "short": total_short,
        "wr": wr,
        "wr_long": wr_long,
        "wr_short": wr_short,
        "pnl": total_pnl,
        "pnl_long": pnl_long_total,
        "pnl_short": pnl_short_total,
        "pf": pf,
        "max_dd": max_dd,
        "profitable": profitable,
    }


# ─── Main ───────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="PPMT V2.1 Full Replay")
    parser.add_argument("--skip-download", action="store_true", help="Skip data download (use cached)")
    parser.add_argument("--config", default="baseline", help="Config to test: baseline, A, B, C, D, all")
    args = parser.parse_args()

    storage = PPMTStorage()
    classifier = AssetClassifier()

    # Determine which configs to test
    if args.config == "all":
        config_names = ["baseline", "A", "B", "C", "D"]
    else:
        config_names = [args.config]

    # Download data
    data_cache: dict[str, pd.DataFrame] = {}

    if not args.skip_download:
        print(f"\n{'='*80}")
        print(f"  PHASE 1: Downloading {IS_DAYS + OOS_DAYS} days of data...")
        print(f"{'='*80}")

        for symbol in TOKENS:
            print(f"\n  Downloading {symbol}...")
            df = await download_ohlcv(symbol, TIMEFRAME, IS_DAYS + OOS_DAYS)
            if df.empty:
                logger.error(f"Failed to download {symbol}")
                continue
            data_cache[symbol] = df
            print(f"    Got {len(df)} candles ({df.index[0]} → {df.index[-1]})")
    else:
        print("  Skipping download (--skip-download) — downloading anyway (no cache support yet)")
        for symbol in TOKENS:
            print(f"\n  Downloading {symbol}...")
            df = await download_ohlcv(symbol, TIMEFRAME, IS_DAYS + OOS_DAYS)
            if df.empty:
                logger.error(f"Failed to download {symbol}")
                continue
            data_cache[symbol] = df
            print(f"    Got {len(df)} candles ({df.index[0]} → {df.index[-1]})")

    if not data_cache:
        logger.error("No data available. Exiting.")
        return

    # Build tries (only need to do once — all configs share the same tries)
    print(f"\n{'='*80}")
    print(f"  PHASE 2: Building tries...")
    print(f"{'='*80}")

    # Use baseline alpha for trie building (all configs use α=3 except A which uses α=5)
    # Build with α=3 first (baseline, B, C, D)
    # Then build with α=5 separately for config A if needed
    for alpha in [3, 5]:
        need_alpha = any(CONFIGS[cn].alpha_n3_n4 == alpha for cn in config_names)
        if not need_alpha:
            continue

        tf_key = f"5m_a{alpha}"
        print(f"\n  Building tries with α={alpha} (key={tf_key})...")

        for symbol, df in data_cache.items():
            asset_class = classifier.classify(symbol)
            total_candles = len(df)
            is_cutoff = int(total_candles * IS_DAYS / (IS_DAYS + OOS_DAYS))
            is_df = df.iloc[:is_cutoff]

            print(f"\n  {symbol} ({asset_class}): {len(is_df)} IS candles")
            counts = build_tries(storage, symbol, asset_class, is_df,
                               alpha_n3_n4=alpha, storage_tf_key=tf_key)
            print(f"    N1={counts['n1']} N2={counts['n2']} N3={counts['n3']} N4={counts['n4']}")

    # Run replays for each config
    print(f"\n{'='*80}")
    print(f"  PHASE 3: Running OOS replays...")
    print(f"{'='*80}")

    results = {}

    for config_name in config_names:
        config = CONFIGS[config_name]
        tf_key = f"5m_a{config.alpha_n3_n4}"

        print(f"\n  Config: {config.label}")
        all_stats = []

        for symbol, df in data_cache.items():
            asset_class = classifier.classify(symbol)
            total_candles = len(df)
            is_cutoff = int(total_candles * IS_DAYS / (IS_DAYS + OOS_DAYS))
            oos_df = df.iloc[is_cutoff:]

            print(f"\n  Replaying {symbol} ({len(oos_df)} OOS candles)...")
            stats = run_replay(symbol, oos_df, storage, asset_class, config,
                             storage_tf_key=tf_key)
            all_stats.append(stats)
            print(f"    Done: {stats.trades_total} trades, P&L={stats.total_pnl:+.2f}%, "
                  f"WR={stats.win_rate:.1f}% ({stats.elapsed:.1f}s)")

        profitable, summary = print_config_report(all_stats, config)
        results[config_name] = summary

    # Final summary across configs
    if len(results) > 1:
        print(f"\n{'='*80}")
        print(f"  CONFIG COMPARISON")
        print(f"{'='*80}")
        print(f"  {'Config':<12} {'Trades':>6} {'L/S':>10} {'WR%':>6} {'WR_L%':>6} {'WR_S%':>6} {'P&L%':>8} {'PF':>5} {'MaxDD%':>7} {'Result':>12}")
        print(f"  {'-'*82}")
        for cn, r in results.items():
            ls = f"{r['long']}/{r['short']}"
            verdict = "PROFIT" if r['profitable'] else "LOSS"
            print(f"  {cn:<12} {r['total_trades']:>6} {ls:>10} {r['wr']:>6.1f} {r['wr_long']:>6.1f} {r['wr_short']:>6.1f} {r['pnl']:>+8.2f} {r['pf']:>5.2f} {r['max_dd']:>7.2f} {verdict:>12}")

        # Find best config
        profitable_configs = {k: v for k, v in results.items() if v['profitable']}
        if profitable_configs:
            best = max(profitable_configs.items(), key=lambda x: x[1]['pnl'])
            print(f"\n  BEST CONFIG: {best[0]} — P&L={best[1]['pnl']:+.2f}%, PF={best[1]['pf']:.2f}, WR={best[1]['wr']:.1f}%")
            print(f"  Trades/day: {best[1]['total_trades'] / (OOS_DAYS * len(TOKENS)):.1f}")
        else:
            best_pnl = max(results.items(), key=lambda x: x[1]['pnl'])
            print(f"\n  NO PROFITABLE CONFIG. Best: {best_pnl[0]} — P&L={best_pnl[1]['pnl']:+.2f}%")
    else:
        cn = list(results.keys())[0]
        r = results[cn]
        if r['profitable']:
            print(f"\n  USE CONFIG {cn}: P&L={r['pnl']:+.2f}%, PF={r['pf']:.2f}, WR={r['wr']:.1f}%")
            print(f"  Expected trades/day: {r['total_trades'] / (OOS_DAYS * len(TOKENS)):.1f}")
        else:
            print(f"\n  Config {cn} NOT profitable (P&L={r['pnl']:+.2f}%). Run with --config all to test alternatives.")


if __name__ == "__main__":
    asyncio.run(main())
