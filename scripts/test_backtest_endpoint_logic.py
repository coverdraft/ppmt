#!/usr/bin/env python3
"""
Diagnostic: Reproduce the 0-trades bug in the backtest endpoint.
Uses cached OHLCV data from SQLite instead of downloading from Binance.
"""
from __future__ import annotations

import copy
import logging
import os
import sys
import time

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_dir = os.path.join(_repo_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import numpy as np
import pandas as pd

from ppmt.data.classifier import AssetClassifier
from ppmt.data.storage import PPMTStorage
from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.weights import AdaptiveWeights
from ppmt.terminal.paper_executor import PaperExecutor
from ppmt.core.regime import RegimeDetector
from ppmt.core.profiles import SPREAD_ESTIMATES
from ppmt.core.thresholds import TIMEFRAME_HARD_MOVE_FLOOR
from ppmt.core.sax import LEVEL_DUAL_ALPHA_CONFIG, LEVEL_DUAL_ALPHA_TF_OVERRIDES
from ppmt.core.trie import PPMTTrie

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_bt")

# ─── Config F parameters (same as endpoint) ─────────────────────
EV_THRESHOLD = 0.40
SL_MULT = 2.0
CONFIG_F_WEIGHTS = {"n1": 0.10, "n2": 0.00, "n3": 0.90, "n4": 0.00, "n5": 0.00}
CAPITAL_USDT = 1000.0
RISK_PCT = 0.01
SYMBOL = "SOL/USDT"
TIMEFRAME = "5m"


def load_data_from_db(storage, symbol, timeframe, days=None):
    """Load OHLCV from the DB instead of downloading."""
    df = storage.load_ohlcv(symbol, timeframe)
    if df is None or df.empty:
        raise ValueError(f"No OHLCV data in DB for {symbol} {timeframe}")
    if days:
        cutoff = len(df) - days * 288
        if cutoff > 0:
            df = df.iloc[cutoff:]
    return df


def apply_config_f_overrides(timeframe):
    """Save and override global state for Config F. Returns saved state."""
    saved_n3 = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
    saved_n4 = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
    saved_tf_overrides = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)
    saved_hmf = TIMEFRAME_HARD_MOVE_FLOOR.get(timeframe, 0.15)

    LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": 3, "volume": 0}
    LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": 3, "volume": 0}
    for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
        for lvl in ["n3", "n4"]:
            LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)
    TIMEFRAME_HARD_MOVE_FLOOR[timeframe] = 0.10

    return (saved_n3, saved_n4, saved_tf_overrides, saved_hmf)


def restore_globals(timeframe, saved):
    saved_n3, saved_n4, saved_tf_overrides, saved_hmf = saved
    LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3
    LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4
    LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
    LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_overrides)
    TIMEFRAME_HARD_MOVE_FLOOR[timeframe] = saved_hmf


def run_replay(oos_df, engine, executor, regime_detector, asset_class_str, timeframe,
               ev_threshold, sl_mult, capital_usdt, risk_pct, label=""):
    """Common replay loop — matches full_replay_v21.py exactly."""
    regime_window = []
    REGIME_WINDOW_SIZE = 10
    _last_engine_ts = 0

    trades = []
    wins = 0
    losses = 0
    total_pnl = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    total_signals = 0
    signals_rejected_spread = 0
    signals_rejected_ev = 0
    results_with_signal = 0
    results_none = 0
    results_no_entry = 0
    in_pos_skip = 0
    open_failed = 0
    regime_counts = {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0}
    spread_pct = SPREAD_ESTIMATES.get(asset_class_str, 0.050)

    for idx in range(len(oos_df)):
        row = oos_df.iloc[[idx]]
        current_price = float(row["close"].iloc[0])
        candle_high = float(row["high"].iloc[0])
        candle_low = float(row["low"].iloc[0])
        ts = oos_df.index[idx]
        ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)

        # Check SL/TP (script style — NO continue after close)
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
                trades.append({
                    "direction": pos.direction, "entry": pos.entry_price,
                    "exit": closed.close_price, "pnl_pct": round(pnl, 2),
                    "reason": closed.close_reason or "UNKNOWN"
                })
                if pnl > 0:
                    wins += 1; gross_profit += pnl
                else:
                    losses += 1; gross_loss += abs(pnl)
                total_pnl += pnl
                executor._position = None

        # Feed candle to engine (script style)
        result = None
        if ts_sec > _last_engine_ts:
            _last_engine_ts = ts_sec

            # Regime detection
            regime_window.append({
                "open": float(row["open"].iloc[0]),
                "high": candle_high, "low": candle_low,
                "close": current_price,
                "volume": float(row["volume"].iloc[0]),
            })
            if len(regime_window) > REGIME_WINDOW_SIZE:
                regime_window = regime_window[-REGIME_WINDOW_SIZE:]
            if len(regime_window) >= 2:
                try:
                    rw_df = pd.DataFrame(regime_window)
                    detected = regime_detector.detect_simple(rw_df, timeframe=timeframe)
                    regime_counts[detected] += 1
                    engine.set_regime(detected)
                except Exception:
                    regime_counts["ranging"] += 1
                    engine.set_regime("ranging")

            result = engine.process_new_candle(
                candle_df=row, current_price=current_price,
                is_in_position=executor.is_in_position,
                entry_price=executor.position.entry_price if executor.position else None,
            )

        if result is None:
            results_none += 1
            continue

        sig = result.signal if result and result.signal else None
        if sig is None or not sig.is_entry:
            results_no_entry += 1
            continue
        if executor.is_in_position:
            in_pos_skip += 1
            continue

        results_with_signal += 1
        total_signals += 1

        # Net EV Gate
        best_node = None
        for _mr in [result.n3_match, result.n1_match, result.n2_match, result.n4_match]:
            if _mr and _mr.node and _mr.node.metadata and _mr.node.metadata.historical_count > 0:
                best_node = _mr.node
                break

        favorable_pct = abs(best_node.metadata.max_favorable_pct) if best_node else 0.0
        drawdown_pct = abs(best_node.metadata.max_drawdown_pct) if best_node else 0.5

        if favorable_pct < 0.001:
            favorable_pct = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.1
        if drawdown_pct < 0.001:
            drawdown_pct = 0.5

        net_favorable = favorable_pct - spread_pct
        if net_favorable <= 0:
            signals_rejected_spread += 1
            continue

        net_rr = min(net_favorable / drawdown_pct, 3.0)
        net_ev = sig.confidence * net_rr

        if net_ev < ev_threshold:
            signals_rejected_ev += 1
            continue

        # Open position
        direction = sig.direction or "LONG"
        expected_move_pct = sig.expected_move_pct or 1.0
        size_usdt = capital_usdt * risk_pct / (abs(expected_move_pct) * 0.012)
        size_usdt = min(size_usdt, capital_usdt)

        try:
            pos = executor.open_position_sync(
                symbol=SYMBOL, direction=direction,
                entry_price=current_price,
                expected_move_pct=expected_move_pct,
                predicted_path_symbols=sig.predicted_path_symbols if sig.predicted_path else None,
                size_usdt=size_usdt,
            )
        except RuntimeError as e:
            open_failed += 1
            continue

        # SL adjustment
        sl_dist_pct = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
        dd_sl_pct = drawdown_pct * sl_mult
        if dd_sl_pct > sl_dist_pct:
            extra = dd_sl_pct - sl_dist_pct
            if pos.direction == "LONG":
                pos.current_sl -= pos.entry_price * (extra / 100.0)
                pos.catastrophic_sl -= pos.entry_price * (extra / 100.0)
            else:
                pos.current_sl += pos.entry_price * (extra / 100.0)
                pos.catastrophic_sl += pos.entry_price * (extra / 100.0)

        # Entry candle SL/TP check
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
                trades.append({
                    "direction": pos.direction, "entry": pos.entry_price,
                    "exit": entry_closed.close_price, "pnl_pct": round(pnl, 2),
                    "reason": entry_closed.close_reason or "ENTRY_CANDLE"
                })
                if pnl > 0:
                    wins += 1; gross_profit += pnl
                else:
                    losses += 1; gross_loss += abs(pnl)
                total_pnl += pnl
                executor._position = None

    # Force-close remaining
    if executor.is_in_position and executor._position:
        last_price = float(oos_df["close"].iloc[-1])
        closed = executor.force_close(last_price, "REPLAY_END")
        pnl = closed.pnl_pct or 0.0
        trades.append({
            "direction": closed.direction, "entry": closed.entry_price,
            "exit": last_price, "pnl_pct": round(pnl, 2), "reason": "REPLAY_END"
        })
        if pnl > 0:
            wins += 1; gross_profit += pnl
        else:
            losses += 1; gross_loss += abs(pnl)
        total_pnl += pnl

    total_trades = wins + losses
    wr = round((wins / total_trades * 100), 1) if total_trades > 0 else 0
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.99

    logger.info(f"  [{label}] OOS={len(oos_df)} candles | result=None: {results_none} | no_entry: {results_no_entry} | has_entry: {results_with_signal}")
    logger.info(f"  [{label}] raw_signals={total_signals} rej_spread={signals_rejected_spread} rej_ev={signals_rejected_ev} in_pos_skip={in_pos_skip} open_fail={open_failed}")
    logger.info(f"  [{label}] trades={total_trades} WR={wr}% PnL={round(total_pnl,2)}% PF={pf}")
    logger.info(f"  [{label}] regimes={regime_counts}")
    for t in trades[:10]:
        logger.info(f"    trade: {t}")
    if len(trades) > 10:
        logger.info(f"    ... and {len(trades)-10} more trades")

    return total_trades


def test1_endpoint_logic():
    """Replicate current endpoint: warmup=500, tries from '5m'."""
    logger.info("=" * 60)
    logger.info("TEST 1: Endpoint logic (warmup=500, tries from '5m')")
    logger.info("=" * 60)

    storage = PPMTStorage()
    classifier = AssetClassifier()
    info = classifier.classify(SYMBOL)
    asset_class_str = info.asset_class
    weight_profile = info.weight_profile

    # Load data
    df = storage.load_ohlcv(SYMBOL, TIMEFRAME)
    if df is None or df.empty:
        logger.error("No OHLCV data")
        return 0
    logger.info(f"DB data: {len(df)} candles, {df.index[0]} → {df.index[-1]}")

    # Use last ~7 days as the "requested" period (plus 500 warmup)
    oos_candles = 7 * 288
    warmup_df = df.iloc[-(oos_candles + 500):-oos_candles]
    oos_df = df.iloc[-oos_candles:]
    logger.info(f"Warmup: {len(warmup_df)} OOS: {len(oos_df)}")

    # Load tries from "5m" (same as endpoint)
    tries = storage.load_all_tries(SYMBOL, asset_class_str, timeframe=TIMEFRAME)
    trie_counts = {}
    for lvl in ("n1", "n2", "n3", "n4"):
        t = tries.get(lvl)
        trie_counts[lvl] = t.pattern_count if t else 0
    logger.info(f"Tries (key='5m'): N1={trie_counts['n1']} N2={trie_counts['n2']} N3={trie_counts['n3']} N4={trie_counts['n4']}")

    if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
        logger.error("No tries! Cannot run test 1")
        return 0

    # Override and create engine
    saved = apply_config_f_overrides(TIMEFRAME)
    try:
        engine = PPMT(symbol=SYMBOL, asset_class=asset_class_str, weight_profile=weight_profile,
                      dual_sax=True, min_confidence=0.08, timeframe=TIMEFRAME)
        engine.weights = AdaptiveWeights(
            n1_universal=CONFIG_F_WEIGHTS["n1"], n2_asset_class=CONFIG_F_WEIGHTS["n2"],
            n3_per_asset=CONFIG_F_WEIGHTS["n3"], n4_per_asset_regime=CONFIG_F_WEIGHTS["n4"],
            n5_btc_context=CONFIG_F_WEIGHTS["n5"],
        )
        engine.set_tries(
            trie_n1=tries["n1"] if tries["n1"] else PPMTTrie(name="empty_n1"),
            trie_n2=tries["n2"] if tries["n2"] else PPMTTrie(name="empty_n2"),
            trie_n3=tries["n3"] if tries["n3"] else PPMTTrie(name="empty_n3"),
            trie_n4=tries["n4"] if tries["n4"] is not None else engine.trie_n4,
        )
    finally:
        restore_globals(TIMEFRAME, saved)

    # Warmup (same as endpoint)
    t0 = time.time()
    for idx, row in warmup_df.iterrows():
        candle_df = pd.DataFrame(
            {"open": [row["open"]], "high": [row["high"]], "low": [row["low"]],
             "close": [row["close"]], "volume": [row["volume"]]},
            index=pd.DatetimeIndex([idx]),
        )
        engine.process_new_candle(candle_df=candle_df, current_price=float(row["close"]),
                                  is_in_position=False, entry_price=None)
    logger.info(f"Warmup done ({len(warmup_df)} candles, {time.time()-t0:.1f}s)")

    # Replay
    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None
    regime_detector = RegimeDetector()

    return run_replay(oos_df, engine, executor, regime_detector, asset_class_str, TIMEFRAME,
                      EV_THRESHOLD, SL_MULT, CAPITAL_USDT, RISK_PCT, label="TEST1")


def test2_script_logic():
    """Replicate script: no warmup, tries from '5m_a3', IS/OOS split."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 2: Script logic (no warmup, tries from '5m_a3')")
    logger.info("=" * 60)

    storage = PPMTStorage()
    classifier = AssetClassifier()
    info = classifier.classify(SYMBOL)
    asset_class_str = info.asset_class
    weight_profile = info.weight_profile

    # Load data
    df = storage.load_ohlcv(SYMBOL, TIMEFRAME)
    if df is None or df.empty:
        logger.error("No OHLCV data")
        return 0
    logger.info(f"DB data: {len(df)} candles, {df.index[0]} → {df.index[-1]}")

    # IS/OOS split (script style: 60/7 ratio)
    IS_DAYS = 60
    OOS_DAYS = 7
    total_candles = len(df)
    is_cutoff = int(total_candles * IS_DAYS / (IS_DAYS + OOS_DAYS))
    is_df = df.iloc[:is_cutoff]
    oos_df = df.iloc[is_cutoff:]
    logger.info(f"IS: {len(is_df)} OOS: {len(oos_df)}")

    # Load tries from "5m_a3" (same key as script)
    tf_key = f"{TIMEFRAME}_a3"
    tries = storage.load_all_tries(SYMBOL, asset_class_str, timeframe=tf_key)
    trie_counts = {}
    for lvl in ("n1", "n2", "n3", "n4"):
        t = tries.get(lvl)
        trie_counts[lvl] = t.pattern_count if t else 0
    logger.info(f"Tries (key='{tf_key}'): N1={trie_counts['n1']} N2={trie_counts['n2']} N3={trie_counts['n3']} N4={trie_counts['n4']}")

    if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
        logger.warning(f"No tries under '{tf_key}'! Building from IS data...")

        # Build tries (same as script)
        saved = apply_config_f_overrides(TIMEFRAME)
        try:
            engine = PPMT(symbol=SYMBOL, asset_class=asset_class_str, weight_profile=weight_profile,
                          dual_sax=True, min_confidence=0.08, timeframe=TIMEFRAME)
            build_count = engine.build(is_df)
            logger.info(f"Built {build_count} patterns from {len(is_df)} IS candles")

            from ppmt.data.storage import UNIVERSAL_POOL_KEY, class_pool_key
            if engine.trie_n1 and engine.trie_n1.pattern_count > 0:
                storage.save_trie(UNIVERSAL_POOL_KEY, "n1", engine.trie_n1, timeframe=tf_key)
            if engine.trie_n2 and engine.trie_n2.pattern_count > 0:
                storage.save_trie(class_pool_key(asset_class_str), "n2", engine.trie_n2, timeframe=tf_key)
            if engine.trie_n3 and engine.trie_n3.pattern_count > 0:
                storage.save_trie(SYMBOL, "n3", engine.trie_n3, timeframe=tf_key)
            if engine.trie_n4 and engine.trie_n4.pattern_count > 0:
                storage.save_trie(SYMBOL, "n4", engine.trie_n4, timeframe=tf_key)
        finally:
            restore_globals(TIMEFRAME, saved)

        # Reload
        tries = storage.load_all_tries(SYMBOL, asset_class_str, timeframe=tf_key)
        trie_counts = {}
        for lvl in ("n1", "n2", "n3", "n4"):
            t = tries.get(lvl)
            trie_counts[lvl] = t.pattern_count if t else 0
        logger.info(f"Rebuilt tries: N1={trie_counts['n1']} N2={trie_counts['n2']} N3={trie_counts['n3']} N4={trie_counts['n4']}")

    # Create engine for replay
    saved = apply_config_f_overrides(TIMEFRAME)
    try:
        engine = PPMT(symbol=SYMBOL, asset_class=asset_class_str, weight_profile=weight_profile,
                      dual_sax=True, min_confidence=0.08, timeframe=TIMEFRAME)
        engine.weights = AdaptiveWeights(
            n1_universal=CONFIG_F_WEIGHTS["n1"], n2_asset_class=CONFIG_F_WEIGHTS["n2"],
            n3_per_asset=CONFIG_F_WEIGHTS["n3"], n4_per_asset_regime=CONFIG_F_WEIGHTS["n4"],
            n5_btc_context=CONFIG_F_WEIGHTS["n5"],
        )
        engine.set_tries(
            trie_n1=tries["n1"] if tries["n1"] else PPMTTrie(name="empty_n1"),
            trie_n2=tries["n2"] if tries["n2"] else PPMTTrie(name="empty_n2"),
            trie_n3=tries["n3"] if tries["n3"] else PPMTTrie(name="empty_n3"),
            trie_n4=tries["n4"] if tries["n4"] else engine.trie_n4,
        )
    finally:
        restore_globals(TIMEFRAME, saved)

    # NO warmup (script style)
    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None
    regime_detector = RegimeDetector()

    return run_replay(oos_df, engine, executor, regime_detector, asset_class_str, TIMEFRAME,
                      EV_THRESHOLD, SL_MULT, CAPITAL_USDT, RISK_PCT, label="TEST2")


def test3_endpoint_with_correct_key():
    """Endpoint logic but with tries from '5m_a3' instead of '5m'."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 3: Endpoint logic but tries from '5m_a3' (no warmup)")
    logger.info("=" * 60)

    storage = PPMTStorage()
    classifier = AssetClassifier()
    info = classifier.classify(SYMBOL)
    asset_class_str = info.asset_class
    weight_profile = info.weight_profile

    # Load data
    df = storage.load_ohlcv(SYMBOL, TIMEFRAME)
    if df is None or df.empty:
        logger.error("No OHLCV data")
        return 0

    # Just use last 7 days as OOS (no IS/warmup)
    oos_candles = 7 * 288
    oos_df = df.iloc[-oos_candles:]
    logger.info(f"OOS: {len(oos_df)} candles")

    # Load tries from "5m_a3"
    tf_key = f"{TIMEFRAME}_a3"
    tries = storage.load_all_tries(SYMBOL, asset_class_str, timeframe=tf_key)
    trie_counts = {}
    for lvl in ("n1", "n2", "n3", "n4"):
        t = tries.get(lvl)
        trie_counts[lvl] = t.pattern_count if t else 0
    logger.info(f"Tries (key='{tf_key}'): N1={trie_counts['n1']} N2={trie_counts['n2']} N3={trie_counts['n3']} N4={trie_counts['n4']}")

    # Create engine
    saved = apply_config_f_overrides(TIMEFRAME)
    try:
        engine = PPMT(symbol=SYMBOL, asset_class=asset_class_str, weight_profile=weight_profile,
                      dual_sax=True, min_confidence=0.08, timeframe=TIMEFRAME)
        engine.weights = AdaptiveWeights(
            n1_universal=CONFIG_F_WEIGHTS["n1"], n2_asset_class=CONFIG_F_WEIGHTS["n2"],
            n3_per_asset=CONFIG_F_WEIGHTS["n3"], n4_per_asset_regime=CONFIG_F_WEIGHTS["n4"],
            n5_btc_context=CONFIG_F_WEIGHTS["n5"],
        )
        engine.set_tries(
            trie_n1=tries["n1"] if tries["n1"] else PPMTTrie(name="empty_n1"),
            trie_n2=tries["n2"] if tries["n2"] else PPMTTrie(name="empty_n2"),
            trie_n3=tries["n3"] if tries["n3"] else PPMTTrie(name="empty_n3"),
            trie_n4=tries["n4"] if tries["n4"] else engine.trie_n4,
        )
    finally:
        restore_globals(TIMEFRAME, saved)

    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None
    regime_detector = RegimeDetector()

    return run_replay(oos_df, engine, executor, regime_detector, asset_class_str, TIMEFRAME,
                      EV_THRESHOLD, SL_MULT, CAPITAL_USDT, RISK_PCT, label="TEST3")


if __name__ == "__main__":
    t1 = test1_endpoint_logic()
    t2 = test2_script_logic()
    t3 = test3_endpoint_with_correct_key()

    print(f"\n{'='*60}")
    print(f"COMPARISON:")
    print(f"  TEST 1: Endpoint logic (warmup=500, tries '5m'):   {t1} trades")
    print(f"  TEST 2: Script logic (no warmup, tries '5m_a3'):    {t2} trades")
    print(f"  TEST 3: No warmup, tries '5m_a3', last 7d OOS:     {t3} trades")
    print(f"{'='*60}")
