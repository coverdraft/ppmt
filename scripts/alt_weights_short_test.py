#!/usr/bin/env python3
"""
Alt Weights SHORT Test — N3=20% N4=65% vs baseline N3=55% N4=35%

Runs the 5m replay with the alternate weight profile to answer:
"If N3=20% and N4=65%, how many SHORT trades pass and what's their WR?"

Uses tries already built with 5m_a3 key. Only reports SHORT counts + WR.
No commits. Solo números.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
from ppmt.engine.weights import AdaptiveWeights, TIMEFRAME_WEIGHT_OVERRIDES
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
from ppmt.core.regime import RegimeDetector
from ppmt.core.thresholds import SignalThresholds

# ─── Configuration ──────────────────────────────────────────────

TOKENS = ["BTC/USDT", "SOL/USDT", "DOGE/USDT", "LINK/USDT"]
CAPITAL_USDT = 1000.0
RISK_PCT = 0.01
OOS_DAYS = 7
IS_DAYS = 83
TIMEFRAME = "5m"
TF_KEY = "5m_a3"
ALPHA_N3_N4 = 3
EV_THRESHOLD = 0.80

# Alt weights
ALT_N3 = 0.20
ALT_N4 = 0.65

# Baseline weights (current production)
BASE_N3 = 0.55
BASE_N4 = 0.35


@dataclass
class TradeRecord:
    direction: str = ""
    pnl_pct: float = 0.0
    won: bool = False


@dataclass
class ReplayResult:
    symbol: str = ""
    label: str = ""
    trades_long: int = 0
    trades_short: int = 0
    long_won: int = 0
    short_won: int = 0
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    total_pnl: float = 0.0
    regime_counts: dict = field(default_factory=lambda: {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0})
    n4_direction_counts: dict = field(default_factory=lambda: {"LONG": 0, "SHORT": 0, "FLAT": 0})


async def download_ohlcv(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Download OHLCV data from Binance."""
    exchange = _DirectPollExchange("binance")
    api_symbol = symbol.replace("/", "")
    all_data = []
    end_ms = int(time.time() * 1000)
    tf_ms = {"5m": 300000}
    candle_ms = tf_ms.get(timeframe, 300000)
    current_end = end_ms
    start_ms = end_ms - days * 86400 * 1000

    while current_end > start_ms:
        fetch_start = max(start_ms, current_end - 1000 * candle_ms)
        try:
            ohlcv = await exchange._exchange.fetch_ohlcv(
                api_symbol, timeframe, since=fetch_start, limit=1000
            )
        except Exception:
            break
        if not ohlcv:
            break
        for candle in ohlcv:
            all_data.append({
                "timestamp": pd.Timestamp(candle[0], unit="ms", tz="UTC"),
                "open": candle[1], "high": candle[2],
                "low": candle[3], "close": candle[4], "volume": candle[5],
            })
        if ohlcv:
            current_end = ohlcv[0][0] - 1
        else:
            break

    await exchange._exchange.close()

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    df = df.set_index("timestamp")
    return df


def run_replay_with_weights(
    symbol: str,
    oos_df: pd.DataFrame,
    storage: PPMTStorage,
    asset_class: str,
    weight_profile: str,
    n3_weight: float,
    n4_weight: float,
) -> ReplayResult:
    """Run OOS replay with specific N3/N4 weights."""
    result = ReplayResult(symbol=symbol)

    # Load tries
    n1_trie = storage.load_trie(UNIVERSAL_POOL_KEY, level="n1", timeframe=TF_KEY)
    class_pk = class_pool_key(asset_class)
    n2_trie = storage.load_trie(class_pk, level="n2", timeframe=TF_KEY)
    n3_trie = storage.load_trie(symbol, level="n3", timeframe=TF_KEY)
    n4_trie = storage.load_trie(symbol, level="n4", timeframe=TF_KEY)

    # Configure alpha overrides for engine init
    saved_dual_config = dict(LEVEL_DUAL_ALPHA_CONFIG)
    saved_tf_overrides = {
        k: dict(v) for k, v in LEVEL_DUAL_ALPHA_TF_OVERRIDES.items()
    }

    LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": ALPHA_N3_N4, "volume": 0}
    LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": ALPHA_N3_N4, "volume": 0}
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
            timeframe=TIMEFRAME,
        )

        # Override weights with custom N3/N4
        # N1 stays at 10%, N2 stays at 0%
        n1_w = 0.10
        n2_w = 0.00
        # Normalize to ensure sum = 1.0
        total = n1_w + n2_w + n3_weight + n4_weight
        engine.weights = AdaptiveWeights(
            n1_universal=n1_w / total,
            n2_asset_class=n2_w / total,
            n3_per_asset=n3_weight / total,
            n4_per_asset_regime=n4_weight / total,
            n5_btc_context=0.0,
            profile=weight_profile,
            timeframe=TIMEFRAME,
        )

        engine.set_tries(
            trie_n1=n1_trie if n1_trie else PPMTTrie(name="empty_n1"),
            trie_n2=n2_trie if n2_trie else PPMTTrie(name="empty_n2"),
            trie_n3=n3_trie if n3_trie else PPMTTrie(name="empty_n3"),
            trie_n4=n4_trie if n4_trie else engine.trie_n4,
        )

        # Signal thresholds (paper mode for replay)
        sig_thresholds = SignalThresholds.paper()

    finally:
        LEVEL_DUAL_ALPHA_CONFIG.clear()
        LEVEL_DUAL_ALPHA_CONFIG.update(saved_dual_config)
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_overrides)

    # Executor setup
    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None
    spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)

    # Regime detection
    _regime_detector = RegimeDetector()
    _regime_window: list[dict] = []
    _REGIME_WINDOW_SIZE = 10

    # Replay loop
    t0 = time.time()
    _last_engine_ts = 0
    active_trade: TradeRecord | None = None
    active_trade_entry_idx = 0
    active_sl = 0.0
    active_tp = 0.0
    active_cat_sl = 0.0
    active_direction = ""
    trade_records: list[TradeRecord] = []

    for idx in range(len(oos_df)):
        row_slice = oos_df.iloc[idx:idx + 1]
        row = row_slice
        current_price = float(row["close"].iloc[0])
        candle_high = float(row["high"].iloc[0])
        candle_low = float(row["low"].iloc[0])

        ts_sec = int(row.index[0].timestamp()) if hasattr(row.index[0], 'timestamp') else idx

        # Check SL/TP on active position
        if executor.is_in_position and active_trade is not None:
            pos = executor._position
            closed = None
            if active_direction == "LONG":
                if candle_low <= active_sl:
                    closed = executor._close_position_sync(active_sl, "CLOSED_BY_SL")
                elif candle_high >= active_tp:
                    closed = executor._close_position_sync(active_tp, "CLOSED_BY_TP")
                elif candle_low <= active_cat_sl:
                    closed = executor._close_position_sync(active_cat_sl, "CLOSED_CATASTROPHIC")
            else:  # SHORT
                if candle_high >= active_sl:
                    closed = executor._close_position_sync(active_sl, "CLOSED_BY_SL")
                elif candle_low <= active_tp:
                    closed = executor._close_position_sync(active_tp, "CLOSED_BY_TP")
                elif candle_high >= active_cat_sl:
                    closed = executor._close_position_sync(active_cat_sl, "CLOSED_CATASTROPHIC")

            if closed:
                pnl = closed.pnl_pct
                active_trade.won = pnl > 0
                active_trade.pnl_pct = pnl
                trade_records.append(active_trade)
                active_trade = None

        # Feed candle to engine
        ppmt_result: PPMTResult | None = None
        if ts_sec > _last_engine_ts:
            _last_engine_ts = ts_sec

            # Regime detection
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
                    result.regime_counts[_detected] += 1
                    engine.set_regime(_detected)
                except Exception:
                    result.regime_counts["ranging"] += 1
                    engine.set_regime("ranging")

            ppmt_result = engine.process_new_candle(
                candle_df=row,
                current_price=current_price,
                is_in_position=executor.is_in_position,
                entry_price=executor.position.entry_price if executor.position else None,
            )

        if ppmt_result is None:
            continue

        signal = ppmt_result.signal
        if signal is None or not signal.is_entry:
            continue

        # Track N4 direction on every result
        _n4_dir = ppmt_result.n4_direction if hasattr(ppmt_result, 'n4_direction') and ppmt_result.n4_direction else "FLAT"
        result.n4_direction_counts[_n4_dir] = result.n4_direction_counts.get(_n4_dir, 0) + 1

        # Direction from weighted vote (v2.1: overrides signal direction)
        direction = ppmt_result.direction
        if direction == "FLAT":
            continue

        # EV Gate — same formula as dual_test_alpha5_15m.py
        # Uses metadata from best matching node, not signal prices
        _best_node = None
        for _mr in [ppmt_result.n3_match, ppmt_result.n1_match, ppmt_result.n2_match, ppmt_result.n4_match]:
            if _mr and _mr.node and _mr.node.metadata and _mr.node.metadata.historical_count > 0:
                _best_node = _mr.node
                break

        favorable_pct = abs(_best_node.metadata.max_favorable_pct) if _best_node else 0.0
        drawdown_pct = abs(_best_node.metadata.max_drawdown_pct) if _best_node else 0.5

        if favorable_pct < 0.001:
            favorable_pct = abs(signal.expected_move_pct) if signal.expected_move_pct else 0.1
        if drawdown_pct < 0.001:
            drawdown_pct = 0.5

        net_favorable = favorable_pct - spread_pct
        if net_favorable <= 0:
            continue  # Spread gate

        net_rr = net_favorable / drawdown_pct
        net_rr_capped = min(net_rr, 3.0)
        net_ev = signal.confidence * net_rr_capped

        if net_ev < EV_THRESHOLD:
            continue  # EV gate

        # Hard move floor check (per-timeframe)
        hmf = sig_thresholds.hard_move_floor_for_timeframe(TIMEFRAME)
        exp_move = abs(signal.expected_move_pct) if signal.expected_move_pct else 0.0
        if exp_move < hmf:
            continue

        # Skip if already in position
        if executor.is_in_position:
            continue

        # Enter trade
        expected_move_pct = signal.expected_move_pct if signal.expected_move_pct else 1.0
        size_usdt = CAPITAL_USDT * RISK_PCT / (abs(expected_move_pct) * 0.012)
        size_usdt = min(size_usdt, CAPITAL_USDT)

        try:
            pos = executor.open_position_sync(
                symbol=symbol,
                direction=direction,
                entry_price=current_price,
                expected_move_pct=expected_move_pct,
                size_usdt=size_usdt,
            )
        except RuntimeError:
            continue  # Overlap

        if executor.position:
            # SL FIX: max(1.2×expected_move, drawdown_pct×1.1)
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

            active_direction = direction
            active_sl = pos.current_sl
            active_tp = pos.current_tp
            active_cat_sl = pos.catastrophic_sl
            active_trade = TradeRecord(direction=direction)
            active_trade_entry_idx = idx

    # Close any remaining position at end
    if executor.is_in_position and active_trade is not None:
        last_price = float(oos_df["close"].iloc[-1])
        pnl = (last_price - executor.position.entry_price) / executor.position.entry_price * 100
        if active_direction == "SHORT":
            pnl = -pnl
        active_trade.won = pnl > 0
        active_trade.pnl_pct = pnl
        trade_records.append(active_trade)

    # Aggregate
    for t in trade_records:
        if t.direction == "LONG":
            result.trades_long += 1
            if t.won:
                result.long_won += 1
            result.long_pnl += t.pnl_pct
        else:
            result.trades_short += 1
            if t.won:
                result.short_won += 1
            result.short_pnl += t.pnl_pct

    result.total_pnl = result.long_pnl + result.short_pnl
    return result


async def main():
    storage = PPMTStorage()
    classifier = AssetClassifier()

    # Try to load existing data first
    all_data = {}
    for symbol in TOKENS:
        info = classifier.classify(symbol)
        df = storage.load_ohlcv(symbol, TIMEFRAME)
        if df is not None and len(df) > 100:
            oos_start = df.index[-1] - pd.Timedelta(days=OOS_DAYS)
            is_df = df[df.index < oos_start]
            oos_df = df[df.index >= oos_start]
            if len(oos_df) >= 100:
                all_data[symbol] = (info, oos_df)
                print(f"  {symbol}: {len(oos_df)} OOS candles (loaded from storage)")
            else:
                print(f"  {symbol}: OOS too short ({len(oos_df)} candles), downloading...")
        else:
            print(f"  {symbol}: No stored data, downloading...")

    # Download missing data
    for symbol in TOKENS:
        if symbol not in all_data:
            info = classifier.classify(symbol)
            print(f"  Downloading {symbol} {TIMEFRAME}...")
            df = await download_ohlcv(symbol, TIMEFRAME, IS_DAYS + OOS_DAYS)
            if df is not None and len(df) > 100:
                oos_start = df.index[-1] - pd.Timedelta(days=OOS_DAYS)
                oos_df = df[df.index >= oos_start]
                if len(oos_df) >= 100:
                    all_data[symbol] = (info, oos_df)
                    print(f"    {symbol}: {len(oos_df)} OOS candles")
                else:
                    print(f"    {symbol}: OOS too short after download")
            else:
                print(f"    {symbol}: Download failed")

    # Run both weight profiles
    print(f"\n{'='*80}")
    print(f"  ALT WEIGHTS TEST: 5m α={ALPHA_N3_N4} + set_regime() + direction vote")
    print(f"  Baseline: N3=55% N4=35%  |  Alt: N3=20% N4=65%")
    print(f"{'='*80}")

    for label, n3_w, n4_w in [
        ("BASELINE (N3=55% N4=35%)", BASE_N3, BASE_N4),
        ("ALT (N3=20% N4=65%)", ALT_N3, ALT_N4),
    ]:
        print(f"\n{'─'*80}")
        print(f"  {label}")
        print(f"{'─'*80}")

        total_long = 0
        total_short = 0
        total_long_won = 0
        total_short_won = 0
        total_long_pnl = 0.0
        total_short_pnl = 0.0
        total_regime = {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0}

        for symbol in TOKENS:
            if symbol not in all_data:
                continue
            info, oos_df = all_data[symbol]
            r = run_replay_with_weights(
                symbol=symbol,
                oos_df=oos_df,
                storage=storage,
                asset_class=info.asset_class,
                weight_profile=info.weight_profile,
                n3_weight=n3_w,
                n4_weight=n4_w,
            )
            long_wr = (r.long_won / r.trades_long * 100) if r.trades_long > 0 else 0
            short_wr = (r.short_won / r.trades_short * 100) if r.trades_short > 0 else 0
            print(f"  {symbol:12s} LONG={r.trades_long:3d} (WR={long_wr:.0f}%)  SHORT={r.trades_short:3d} (WR={short_wr:.0f}%)  P&L L={r.long_pnl:+.2f}% S={r.short_pnl:+.2f}%  Total={r.total_pnl:+.2f}%")

            total_long += r.trades_long
            total_short += r.trades_short
            total_long_won += r.long_won
            total_short_won += r.short_won
            total_long_pnl += r.long_pnl
            total_short_pnl += r.short_pnl
            for k in total_regime:
                total_regime[k] += r.regime_counts.get(k, 0)

        total_all = total_long + total_short
        total_won = total_long_won + total_short_won
        total_pnl = total_long_pnl + total_short_pnl
        long_wr = (total_long_won / total_long * 100) if total_long > 0 else 0
        short_wr = (total_short_won / total_short * 100) if total_short > 0 else 0
        overall_wr = (total_won / total_all * 100) if total_all > 0 else 0

        print(f"\n  {'TOTAL':12s} LONG={total_long:3d} (WR={long_wr:.0f}%)  SHORT={total_short:3d} (WR={short_wr:.0f}%)  P&L L={total_long_pnl:+.2f}% S={total_short_pnl:+.2f}%  Total={total_pnl:+.2f}%")
        print(f"  Overall WR={overall_wr:.0f}%  ({total_won}/{total_all})")
        print(f"  Regime dist: {total_regime}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
