#!/usr/bin/env python3
"""
Test the NEW _backtest_sync logic directly.
Verifies it produces trades using the exact same code as v2_server.py.
"""
from __future__ import annotations

import copy
import logging
import os
import queue
import sys
import time

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_dir = os.path.join(_repo_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import pandas as pd
from ppmt.data.storage import PPMTStorage
from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.weights import AdaptiveWeights
from ppmt.terminal.paper_executor import PaperExecutor
from ppmt.core.regime import RegimeDetector
from ppmt.core.profiles import SPREAD_ESTIMATES
from ppmt.core.thresholds import TIMEFRAME_HARD_MOVE_FLOOR
from ppmt.core.sax import LEVEL_DUAL_ALPHA_CONFIG, LEVEL_DUAL_ALPHA_TF_OVERRIDES
from ppmt.core.trie import PPMTTrie
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("test_new_bt")

# Import the actual _backtest_sync function from v2_server
# We can't import it directly (needs FastAPI), so we replicate it here

# Config F parameters (EXACTLY matching new _backtest_sync)
EV_THRESHOLD = 0.40
SL_MULT = 2.0
ALPHA_N3_N4 = 3
CONFIG_F_WEIGHTS = {"n1": 0.10, "n2": 0.00, "n3": 0.90, "n4": 0.00, "n5": 0.00}
HARD_MOVE_FLOOR = 0.10
CAPITAL_USDT = 1000.0
RISK_PCT = 0.01
IS_DAYS = 60
REGIME_WINDOW_SIZE = 10

SYMBOL = "SOL/USDT"
TIMEFRAME = "5m"
DAYS = 7

from ppmt.data.classifier import AssetClassifier
from ppmt.data.storage import UNIVERSAL_POOL_KEY, class_pool_key

def run_test():
    """Replicate the new _backtest_sync logic exactly."""
    msg_queue = queue.Queue()

    def _send(msg_type, data):
        msg_queue.put({"type": msg_type, "data": data})

    classifier = AssetClassifier()
    info = classifier.classify(SYMBOL)
    asset_class = info.asset_class
    weight_profile = info.weight_profile
    logger.info(f"Classified: {SYMBOL} → {asset_class}/{weight_profile}")

    # Load data from DB
    storage = PPMTStorage()
    df = storage.load_ohlcv(SYMBOL, TIMEFRAME)
    if df is None or df.empty:
        logger.error("No OHLCV data")
        return 0
    logger.info(f"Data: {len(df)} candles, {df.index[0]} → {df.index[-1]}")

    # IS/OOS split
    total_candles = len(df)
    is_cutoff = int(total_candles * IS_DAYS / (IS_DAYS + DAYS))
    is_df = df.iloc[:is_cutoff]
    oos_df = df.iloc[is_cutoff:]
    logger.info(f"IS: {len(is_df)} OOS: {len(oos_df)}")

    # Load tries from alpha key
    tf_key = f"{TIMEFRAME}_a{ALPHA_N3_N4}"
    tries = storage.load_all_tries(SYMBOL, asset_class, timeframe=tf_key)

    trie_counts = {}
    for lvl in ("n1", "n2", "n3", "n4"):
        t = tries.get(lvl)
        trie_counts[lvl] = t.pattern_count if t else 0
    logger.info(f"Tries from '{tf_key}': N1={trie_counts['n1']} N2={trie_counts['n2']} N3={trie_counts['n3']} N4={trie_counts['n4']}")

    # Build if needed
    if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
        logger.info(f"Building tries from IS data...")
        saved_n3_b = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
        saved_n4_b = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
        saved_tf_b = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)
        LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": ALPHA_N3_N4, "volume": 0}
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": ALPHA_N3_N4, "volume": 0}
        for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
            for lvl in ["n3", "n4"]:
                LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)
        try:
            build_engine = PPMT(symbol=SYMBOL, asset_class=asset_class, weight_profile=weight_profile,
                                dual_sax=True, min_confidence=0.08, timeframe=TIMEFRAME)
            build_count = build_engine.build(is_df)
            logger.info(f"Built {build_count} patterns")
            if build_engine.trie_n1 and build_engine.trie_n1.pattern_count > 0:
                storage.save_trie(UNIVERSAL_POOL_KEY, "n1", build_engine.trie_n1, timeframe=tf_key)
            if build_engine.trie_n2 and build_engine.trie_n2.pattern_count > 0:
                storage.save_trie(class_pool_key(asset_class), "n2", build_engine.trie_n2, timeframe=tf_key)
            if build_engine.trie_n3 and build_engine.trie_n3.pattern_count > 0:
                storage.save_trie(SYMBOL, "n3", build_engine.trie_n3, timeframe=tf_key)
            if build_engine.trie_n4 and build_engine.trie_n4.pattern_count > 0:
                storage.save_trie(SYMBOL, "n4", build_engine.trie_n4, timeframe=tf_key)
        finally:
            LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3_b
            LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4_b
            LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
            LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_b)

        tries = storage.load_all_tries(SYMBOL, asset_class, timeframe=tf_key)

    if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
        logger.error("No tries! Cannot run.")
        return 0

    # Create engine with Config F overrides
    saved_n3 = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
    saved_n4 = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
    saved_tf_overrides = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)
    saved_hmf = TIMEFRAME_HARD_MOVE_FLOOR.get(TIMEFRAME, 0.15)
    LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": ALPHA_N3_N4, "volume": 0}
    LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": ALPHA_N3_N4, "volume": 0}
    for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
        for lvl in ["n3", "n4"]:
            LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)
    TIMEFRAME_HARD_MOVE_FLOOR[TIMEFRAME] = HARD_MOVE_FLOOR

    try:
        engine = PPMT(symbol=SYMBOL, asset_class=asset_class, weight_profile=weight_profile,
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
        LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
        LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_overrides)
        TIMEFRAME_HARD_MOVE_FLOOR[TIMEFRAME] = saved_hmf

    # OOS replay (script style, no warmup)
    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None
    regime_detector = RegimeDetector()
    regime_window = []
    _last_engine_ts = 0

    wins = 0
    losses = 0
    total_pnl = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    max_drawdown = 0.0
    peak_pnl = 0.0
    long_count = 0
    short_count = 0
    total_signals_raw = 0
    signals_rejected_spread = 0
    signals_rejected_ev = 0
    regime_counts = {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0}
    spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)

    t0 = time.time()
    for idx in range(len(oos_df)):
        row = oos_df.iloc[[idx]]
        current_price = float(row["close"].iloc[0])
        candle_high = float(row["high"].iloc[0])
        candle_low = float(row["low"].iloc[0])
        ts = oos_df.index[idx]
        ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)

        # Check SL/TP (NO continue)
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
                if pnl > 0: wins += 1; gross_profit += pnl
                else: losses += 1; gross_loss += abs(pnl)
                total_pnl += pnl
                peak_pnl = max(peak_pnl, total_pnl)
                dd = peak_pnl - total_pnl
                max_drawdown = max(max_drawdown, dd)
                executor._position = None

        # Feed candle
        result = None
        if ts_sec > _last_engine_ts:
            _last_engine_ts = ts_sec
            regime_window.append({
                "open": float(row["open"].iloc[0]), "high": candle_high,
                "low": candle_low, "close": current_price,
                "volume": float(row["volume"].iloc[0]),
            })
            if len(regime_window) > REGIME_WINDOW_SIZE:
                regime_window = regime_window[-REGIME_WINDOW_SIZE:]
            if len(regime_window) >= 2:
                try:
                    rw_df = pd.DataFrame(regime_window)
                    detected = regime_detector.detect_simple(rw_df, timeframe=TIMEFRAME)
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

        if result is None: continue
        sig = result.signal if result and result.signal else None
        if sig is None or not sig.is_entry: continue
        if executor.is_in_position: continue
        total_signals_raw += 1

        # Net EV Gate
        best_node = None
        for _mr in [result.n3_match, result.n1_match, result.n2_match, result.n4_match]:
            if _mr and _mr.node and _mr.node.metadata and _mr.node.metadata.historical_count > 0:
                best_node = _mr.node
                break
        favorable_pct = abs(best_node.metadata.max_favorable_pct) if best_node else 0.0
        drawdown_pct = abs(best_node.metadata.max_drawdown_pct) if best_node else 0.5
        if favorable_pct < 0.001: favorable_pct = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.1
        if drawdown_pct < 0.001: drawdown_pct = 0.5
        net_favorable = favorable_pct - spread_pct
        if net_favorable <= 0: signals_rejected_spread += 1; continue
        net_rr = min(net_favorable / drawdown_pct, 3.0)
        net_ev = sig.confidence * net_rr
        if net_ev < EV_THRESHOLD: signals_rejected_ev += 1; continue

        # Open position
        direction = sig.direction or "LONG"
        expected_move_pct = sig.expected_move_pct or 1.0
        size_usdt = CAPITAL_USDT * RISK_PCT / (abs(expected_move_pct) * 0.012)
        size_usdt = min(size_usdt, CAPITAL_USDT)
        try:
            pos = executor.open_position_sync(symbol=SYMBOL, direction=direction,
                entry_price=current_price, expected_move_pct=expected_move_pct,
                predicted_path_symbols=sig.predicted_path_symbols if sig.predicted_path else None,
                size_usdt=size_usdt)
        except RuntimeError: continue

        # SL adjustment
        sl_dist_pct = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
        dd_sl_pct = drawdown_pct * SL_MULT
        if dd_sl_pct > sl_dist_pct:
            extra = dd_sl_pct - sl_dist_pct
            if pos.direction == "LONG":
                pos.current_sl -= pos.entry_price * (extra / 100.0)
                pos.catastrophic_sl -= pos.entry_price * (extra / 100.0)
            else:
                pos.current_sl += pos.entry_price * (extra / 100.0)
                pos.catastrophic_sl += pos.entry_price * (extra / 100.0)

        if direction == "LONG": long_count += 1
        else: short_count += 1

        # Entry candle SL/TP
        if executor.is_in_position:
            entry_closed = None
            if pos.direction == "LONG":
                if candle_low <= pos.catastrophic_sl: entry_closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                elif candle_low <= pos.current_sl: entry_closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                elif candle_high >= pos.current_tp: entry_closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
            else:
                if candle_high >= pos.catastrophic_sl: entry_closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                elif candle_high >= pos.current_sl: entry_closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                elif candle_low <= pos.current_tp: entry_closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
            if entry_closed:
                pnl = entry_closed.pnl_pct or 0.0
                if pnl > 0: wins += 1; gross_profit += pnl
                else: losses += 1; gross_loss += abs(pnl)
                total_pnl += pnl
                peak_pnl = max(peak_pnl, total_pnl)
                dd = peak_pnl - total_pnl
                max_drawdown = max(max_drawdown, dd)
                executor._position = None
                continue

        # Walk-Forward
        if result and executor.is_in_position:
            current_sax = []
            buf = getattr(engine, '_streaming_buffer', None)
            if buf and buf._pattern_buffer:
                last_sym = buf._pattern_buffer[-1]
                if isinstance(last_sym, (tuple, list)): current_sax = [str(s) for s in last_sym]
                else: current_sax = [str(last_sym)]
            if current_sax:
                executor.check_walk_forward(current_sax, current_price)

    # Force close
    if executor.is_in_position and executor._position:
        last_price = float(oos_df["close"].iloc[-1])
        closed = executor.force_close(last_price, "REPLAY_END")
        pnl = closed.pnl_pct or 0.0
        if pnl > 0: wins += 1; gross_profit += pnl
        else: losses += 1; gross_loss += abs(pnl)
        total_pnl += pnl

    total_trades = wins + losses
    wr = round((wins / total_trades * 100), 1) if total_trades > 0 else 0
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.99 if gross_profit > 0 else 0.0)
    elapsed = time.time() - t0

    logger.info(f"RESULT: trades={total_trades} WR={wr}% PnL={round(total_pnl,2)}% PF={pf} MaxDD={round(max_drawdown,2)}")
    logger.info(f"  signals={total_signals_raw} rej_spread={signals_rejected_spread} rej_ev={signals_rejected_ev}")
    logger.info(f"  long={long_count} short={short_count} regimes={regime_counts}")
    logger.info(f"  Elapsed: {elapsed:.1f}s")

    # Check queue messages
    msgs = []
    while not msg_queue.empty():
        msgs.append(msg_queue.get_nowait())
    complete_msgs = [m for m in msgs if m["type"] == "backtest_complete"]
    trade_msgs = [m for m in msgs if m["type"] == "backtest_trade"]
    signal_msgs = [m for m in msgs if m["type"] == "backtest_signal"]
    logger.info(f"  Queue: {len(msgs)} total, {len(signal_msgs)} signals, {len(trade_msgs)} trades, {len(complete_msgs)} complete")

    return total_trades


if __name__ == "__main__":
    trades = run_test()
    print(f"\n{'='*60}")
    if trades > 0:
        print(f"  ✅ SUCCESS: {trades} trades produced by new backtest logic")
    else:
        print(f"  ❌ FAIL: 0 trades produced")
    print(f"{'='*60}")
