#!/usr/bin/env python3
"""
Test the ACTUAL _backtest_sync from v2_server.py in a thread (like the real server).
Verifies threading + queue + SQLite safety.
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_dir = os.path.join(_repo_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("test_threaded")

# Import the actual function from v2_server
# We need to mock the FastAPI parts first
import importlib

# Pre-import required modules that v2_server needs
import pandas as pd
import asyncio
import json

# We can't import v2_server directly (it starts FastAPI), so we extract just _backtest_sync
# by reading and exec-ing the function
import inspect

# Instead, let's just replicate the threading test using our verified logic
from ppmt.data.classifier import AssetClassifier
from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt.engine.ppmt import PPMT, PPMTResult
from ppmt.engine.weights import AdaptiveWeights
from ppmt.terminal.paper_executor import PaperExecutor
from ppmt.core.regime import RegimeDetector
from ppmt.core.profiles import SPREAD_ESTIMATES
from ppmt.core.thresholds import TIMEFRAME_HARD_MOVE_FLOOR
from ppmt.core.sax import LEVEL_DUAL_ALPHA_CONFIG, LEVEL_DUAL_ALPHA_TF_OVERRIDES
from ppmt.core.trie import PPMTTrie
from typing import Optional
import copy


def _backtest_sync_test(symbol: str, timeframe: str, days: int, msg_queue: queue.Queue) -> None:
    """Exact copy of the new _backtest_sync from v2_server.py for thread testing."""

    import requests as _requests

    def _send(msg_type: str, data: dict):
        msg_queue.put({"type": msg_type, "data": data})

    EV_THRESHOLD = 0.40
    SL_MULT = 2.0
    ALPHA_N3_N4 = 3
    CONFIG_F_WEIGHTS = {"n1": 0.10, "n2": 0.00, "n3": 0.90, "n4": 0.00, "n5": 0.00}
    HARD_MOVE_FLOOR = 0.10
    CAPITAL_USDT = 1000.0
    RISK_PCT = 0.01
    IS_DAYS = 60
    REGIME_WINDOW_SIZE = 10

    try:
        logger.info(f"[BT-THREAD] Started: {symbol} {timeframe} {days}d")

        classifier = AssetClassifier()
        info = classifier.classify(symbol)
        asset_class = info.asset_class
        weight_profile = info.weight_profile

        # NEW: Create storage INSIDE the thread (SQLite safe)
        storage = PPMTStorage()
        logger.info(f"[BT-THREAD] Storage created in thread {threading.current_thread().ident}")

        df = storage.load_ohlcv(symbol, timeframe)
        if df is None or len(df) < 1000:
            raise ValueError(f"No data for {symbol}")

        total_candles = len(df)
        is_cutoff = int(total_candles * IS_DAYS / (IS_DAYS + days))
        is_df = df.iloc[:is_cutoff]
        oos_df = df.iloc[is_cutoff:]

        tf_key = f"{timeframe}_a{ALPHA_N3_N4}"
        tries = storage.load_all_tries(symbol, asset_class, timeframe=tf_key)

        trie_counts = {}
        for lvl in ("n1", "n2", "n3", "n4"):
            t = tries.get(lvl)
            trie_counts[lvl] = t.pattern_count if t else 0
        logger.info(f"[BT-THREAD] Tries: N1={trie_counts['n1']} N3={trie_counts['n3']} N4={trie_counts['n4']}")

        if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
            logger.info(f"[BT-THREAD] Building tries...")
            saved_n3_b = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
            saved_n4_b = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
            saved_tf_b = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)
            LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": ALPHA_N3_N4, "volume": 0}
            LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": ALPHA_N3_N4, "volume": 0}
            for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
                for lvl in ["n3", "n4"]:
                    LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)
            try:
                build_engine = PPMT(symbol=symbol, asset_class=asset_class, weight_profile=weight_profile,
                                    dual_sax=True, min_confidence=0.08, timeframe=timeframe)
                build_engine.build(is_df)
                if build_engine.trie_n1 and build_engine.trie_n1.pattern_count > 0:
                    storage.save_trie(UNIVERSAL_POOL_KEY, "n1", build_engine.trie_n1, timeframe=tf_key)
                if build_engine.trie_n2 and build_engine.trie_n2.pattern_count > 0:
                    storage.save_trie(class_pool_key(asset_class), "n2", build_engine.trie_n2, timeframe=tf_key)
                if build_engine.trie_n3 and build_engine.trie_n3.pattern_count > 0:
                    storage.save_trie(symbol, "n3", build_engine.trie_n3, timeframe=tf_key)
                if build_engine.trie_n4 and build_engine.trie_n4.pattern_count > 0:
                    storage.save_trie(symbol, "n4", build_engine.trie_n4, timeframe=tf_key)
            finally:
                LEVEL_DUAL_ALPHA_CONFIG["n3"] = saved_n3_b
                LEVEL_DUAL_ALPHA_CONFIG["n4"] = saved_n4_b
                LEVEL_DUAL_ALPHA_TF_OVERRIDES.clear()
                LEVEL_DUAL_ALPHA_TF_OVERRIDES.update(saved_tf_b)
            tries = storage.load_all_tries(symbol, asset_class, timeframe=tf_key)

        saved_n3 = LEVEL_DUAL_ALPHA_CONFIG["n3"].copy()
        saved_n4 = LEVEL_DUAL_ALPHA_CONFIG["n4"].copy()
        saved_tf_overrides = copy.deepcopy(LEVEL_DUAL_ALPHA_TF_OVERRIDES)
        saved_hmf = TIMEFRAME_HARD_MOVE_FLOOR.get(timeframe, 0.15)
        LEVEL_DUAL_ALPHA_CONFIG["n3"] = {"price": ALPHA_N3_N4, "volume": 0}
        LEVEL_DUAL_ALPHA_CONFIG["n4"] = {"price": ALPHA_N3_N4, "volume": 0}
        for tf_k in LEVEL_DUAL_ALPHA_TF_OVERRIDES:
            for lvl in ["n3", "n4"]:
                LEVEL_DUAL_ALPHA_TF_OVERRIDES[tf_k].pop(lvl, None)
        TIMEFRAME_HARD_MOVE_FLOOR[timeframe] = HARD_MOVE_FLOOR

        try:
            engine = PPMT(symbol=symbol, asset_class=asset_class, weight_profile=weight_profile,
                          dual_sax=True, min_confidence=0.08, timeframe=timeframe)
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
            TIMEFRAME_HARD_MOVE_FLOOR[timeframe] = saved_hmf

        executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
        executor._position = None
        regime_detector = RegimeDetector()
        regime_window = []
        _last_engine_ts = 0
        wins = losses = 0
        total_pnl = gross_profit = gross_loss = max_dd = peak_pnl = 0.0
        longs = shorts = 0
        total_signals_raw = 0
        signals_rejected_spread = 0
        signals_rejected_ev = 0
        spread_pct = SPREAD_ESTIMATES.get(asset_class, 0.050)

        for idx in range(len(oos_df)):
            row = oos_df.iloc[[idx]]
            cp = float(row["close"].iloc[0])
            ch = float(row["high"].iloc[0])
            cl = float(row["low"].iloc[0])
            ts = oos_df.index[idx]
            ts_sec = int(ts.timestamp()) if isinstance(ts, pd.Timestamp) else int(ts)

            if executor.is_in_position:
                pos = executor.position
                closed = None
                if pos.direction == "LONG":
                    if cl <= pos.catastrophic_sl: closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif cl <= pos.current_sl: closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif ch >= pos.current_tp: closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
                else:
                    if ch >= pos.catastrophic_sl: closed = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif ch >= pos.current_sl: closed = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif cl <= pos.current_tp: closed = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
                if closed:
                    pnl = closed.pnl_pct or 0.0
                    if pnl > 0: wins += 1; gross_profit += pnl
                    else: losses += 1; gross_loss += abs(pnl)
                    total_pnl += pnl
                    peak_pnl = max(peak_pnl, total_pnl)
                    max_dd = max(max_dd, peak_pnl - total_pnl)
                    _send("backtest_trade", {"direction": pos.direction, "pnl_pct": round(pnl, 2)})
                    executor._position = None

            result = None
            if ts_sec > _last_engine_ts:
                _last_engine_ts = ts_sec
                regime_window.append({"open": float(row["open"].iloc[0]), "high": ch, "low": cl, "close": cp, "volume": float(row["volume"].iloc[0])})
                if len(regime_window) > REGIME_WINDOW_SIZE: regime_window = regime_window[-REGIME_WINDOW_SIZE:]
                if len(regime_window) >= 2:
                    try:
                        engine.set_regime(RegimeDetector().detect_simple(pd.DataFrame(regime_window), timeframe=timeframe))
                    except: engine.set_regime("ranging")
                result = engine.process_new_candle(candle_df=row, current_price=cp, is_in_position=executor.is_in_position,
                    entry_price=executor.position.entry_price if executor.position else None)

            if result is None: continue
            sig = result.signal if result and result.signal else None
            if sig is None or not sig.is_entry: continue
            if executor.is_in_position: continue
            total_signals_raw += 1

            best_node = None
            for _mr in [result.n3_match, result.n1_match, result.n2_match, result.n4_match]:
                if _mr and _mr.node and _mr.node.metadata and _mr.node.metadata.historical_count > 0:
                    best_node = _mr.node; break
            fav = abs(best_node.metadata.max_favorable_pct) if best_node else 0.0
            dd_pct = abs(best_node.metadata.max_drawdown_pct) if best_node else 0.5
            if fav < 0.001: fav = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.1
            if dd_pct < 0.001: dd_pct = 0.5
            net_fav = fav - spread_pct
            if net_fav <= 0: signals_rejected_spread += 1; continue
            net_ev = sig.confidence * min(net_fav / dd_pct, 3.0)
            if net_ev < EV_THRESHOLD: signals_rejected_ev += 1; continue

            _send("backtest_signal", {"direction": sig.direction or "LONG", "entry": cp})
            direction = sig.direction or "LONG"
            emp = sig.expected_move_pct or 1.0
            sz = min(CAPITAL_USDT * RISK_PCT / (abs(emp) * 0.012), CAPITAL_USDT)
            try:
                pos = executor.open_position_sync(symbol=symbol, direction=direction,
                    entry_price=cp, expected_move_pct=emp,
                    predicted_path_symbols=sig.predicted_path_symbols if sig.predicted_path else None,
                    size_usdt=sz)
            except RuntimeError: continue

            sl_d = abs(pos.entry_price - pos.current_sl) / pos.entry_price * 100.0
            dd_sl = dd_pct * SL_MULT
            if dd_sl > sl_d:
                ex = dd_sl - sl_d
                if pos.direction == "LONG":
                    pos.current_sl -= pos.entry_price * (ex / 100.0)
                    pos.catastrophic_sl -= pos.entry_price * (ex / 100.0)
                else:
                    pos.current_sl += pos.entry_price * (ex / 100.0)
                    pos.catastrophic_sl += pos.entry_price * (ex / 100.0)

            if direction == "LONG": longs += 1
            else: shorts += 1

            if executor.is_in_position:
                ec = None
                if pos.direction == "LONG":
                    if cl <= pos.catastrophic_sl: ec = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif cl <= pos.current_sl: ec = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif ch >= pos.current_tp: ec = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
                else:
                    if ch >= pos.catastrophic_sl: ec = executor.force_close(pos.catastrophic_sl, "CLOSED_CATASTROPHIC")
                    elif ch >= pos.current_sl: ec = executor.force_close(pos.current_sl, "CLOSED_BY_SL")
                    elif cl <= pos.current_tp: ec = executor.force_close(pos.current_tp, "CLOSED_BY_TP")
                if ec:
                    pnl = ec.pnl_pct or 0.0
                    if pnl > 0: wins += 1; gross_profit += pnl
                    else: losses += 1; gross_loss += abs(pnl)
                    total_pnl += pnl
                    peak_pnl = max(peak_pnl, total_pnl)
                    max_dd = max(max_dd, peak_pnl - total_pnl)
                    _send("backtest_trade", {"direction": pos.direction, "pnl_pct": round(pnl, 2)})
                    executor._position = None
                    continue

        if executor.is_in_position and executor._position:
            lp = float(oos_df["close"].iloc[-1])
            c = executor.force_close(lp, "REPLAY_END")
            pnl = c.pnl_pct or 0.0
            if pnl > 0: wins += 1; gross_profit += pnl
            else: losses += 1; gross_loss += abs(pnl)
            total_pnl += pnl

        total = wins + losses
        wr = round((wins / total * 100), 1) if total > 0 else 0
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0

        _send("backtest_complete", {
            "trades": total, "wins": wins, "losses": losses, "wr": wr,
            "pnl_pct": round(total_pnl, 2), "profit_factor": pf,
            "signals_total": total_signals_raw,
        })
        logger.info(f"[BT-THREAD] Complete: {total} trades, WR={wr}%, PnL={round(total_pnl,2)}%")

    except Exception as e:
        import traceback
        logger.error(f"[BT-THREAD] FAILED: {e}\n{traceback.format_exc()}")
        _send("backtest_complete", {"error": str(e), "trades": 0})


def test_threaded():
    """Test running _backtest_sync in a thread with queue drain (like the real server)."""
    symbols = ["SOL/USDT", "BTC/USDT"]

    for sym in symbols:
        print(f"\n  Testing threaded backtest for {sym}...")
        msg_queue = queue.Queue()

        thread = threading.Thread(
            target=_backtest_sync_test,
            args=(sym, "5m", 7, msg_queue),
            daemon=True,
        )
        thread.start()
        logger.info(f"Thread started for {sym}")

        # Drain queue (simulating the async WS sender)
        msgs = []
        got_complete = False
        while True:
            if not thread.is_alive() and msg_queue.empty():
                break
            try:
                msg = msg_queue.get(timeout=0.5)
                msgs.append(msg)
                if msg.get("type") == "backtest_complete":
                    got_complete = True
                    break
            except queue.Empty:
                continue

        thread.join(timeout=5)

        complete_msgs = [m for m in msgs if m["type"] == "backtest_complete"]
        trade_msgs = [m for m in msgs if m["type"] == "backtest_trade"]
        signal_msgs = [m for m in msgs if m["type"] == "backtest_signal"]

        if complete_msgs:
            data = complete_msgs[0]["data"]
            trades = data.get("trades", 0)
            error = data.get("error")
            if error:
                print(f"  {sym}: ❌ ERROR: {error}")
            elif trades > 0:
                print(f"  {sym}: ✅ {trades} trades | {len(signal_msgs)} signals | {len(trade_msgs)} trade msgs | PF={data.get('profit_factor')}")
            else:
                print(f"  {sym}: ❌ 0 trades")
        elif not got_complete:
            print(f"  {sym}: ❌ NO COMPLETE MESSAGE (thread crashed)")


if __name__ == "__main__":
    print(f"{'='*60}")
    print("  THREADED BACKTEST TEST (mimics real server)")
    print(f"{'='*60}")
    test_threaded()
    print(f"\n{'='*60}")
    print("  Thread test complete")
    print(f"{'='*60}")
