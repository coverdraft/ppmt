#!/usr/bin/env python3
"""
Run the new backtest logic 3 times for 2 symbols to verify consistency.
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

logging.basicConfig(level=logging.WARNING)  # Less noise for multiple runs
logger = logging.getLogger("verify")
logger.setLevel(logging.INFO)

EV_THRESHOLD = 0.40
SL_MULT = 2.0
ALPHA_N3_N4 = 3
CONFIG_F_WEIGHTS = {"n1": 0.10, "n2": 0.00, "n3": 0.90, "n4": 0.00, "n5": 0.00}
HARD_MOVE_FLOOR = 0.10
CAPITAL_USDT = 1000.0
RISK_PCT = 0.01
IS_DAYS = 60
REGIME_WINDOW_SIZE = 10
TIMEFRAME = "5m"
DAYS = 7

def run_backtest(symbol):
    classifier = AssetClassifier()
    info = classifier.classify(symbol)
    asset_class = info.asset_class
    weight_profile = info.weight_profile

    storage = PPMTStorage()
    df = storage.load_ohlcv(symbol, TIMEFRAME)
    if df is None or len(df) < 1000:
        return -1

    total_candles = len(df)
    is_cutoff = int(total_candles * IS_DAYS / (IS_DAYS + DAYS))
    is_df = df.iloc[:is_cutoff]
    oos_df = df.iloc[is_cutoff:]

    tf_key = f"{TIMEFRAME}_a{ALPHA_N3_N4}"
    tries = storage.load_all_tries(symbol, asset_class, timeframe=tf_key)

    # Build if needed
    if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
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
                                dual_sax=True, min_confidence=0.08, timeframe=TIMEFRAME)
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

    if not tries.get("n1") and not tries.get("n2") and not tries.get("n3"):
        return -1

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
        engine = PPMT(symbol=symbol, asset_class=asset_class, weight_profile=weight_profile,
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

    executor = PaperExecutor(capital_usdt=CAPITAL_USDT)
    executor._position = None
    regime_detector = RegimeDetector()
    regime_window = []
    _last_engine_ts = 0
    wins = losses = 0
    total_pnl = gross_profit = gross_loss = max_dd = peak_pnl = 0.0
    longs = shorts = 0
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
                executor._position = None

        result = None
        if ts_sec > _last_engine_ts:
            _last_engine_ts = ts_sec
            regime_window.append({"open": float(row["open"].iloc[0]), "high": ch, "low": cl, "close": cp, "volume": float(row["volume"].iloc[0])})
            if len(regime_window) > REGIME_WINDOW_SIZE: regime_window = regime_window[-REGIME_WINDOW_SIZE:]
            if len(regime_window) >= 2:
                try:
                    rw_df = pd.DataFrame(regime_window)
                    engine.set_regime(regime_detector.detect_simple(rw_df, timeframe=TIMEFRAME))
                except: engine.set_regime("ranging")
            result = engine.process_new_candle(candle_df=row, current_price=cp, is_in_position=executor.is_in_position,
                entry_price=executor.position.entry_price if executor.position else None)

        if result is None: continue
        sig = result.signal if result and result.signal else None
        if sig is None or not sig.is_entry: continue
        if executor.is_in_position: continue

        best_node = None
        for _mr in [result.n3_match, result.n1_match, result.n2_match, result.n4_match]:
            if _mr and _mr.node and _mr.node.metadata and _mr.node.metadata.historical_count > 0:
                best_node = _mr.node; break
        fav = abs(best_node.metadata.max_favorable_pct) if best_node else 0.0
        dd_pct = abs(best_node.metadata.max_drawdown_pct) if best_node else 0.5
        if fav < 0.001: fav = abs(sig.expected_move_pct) if sig.expected_move_pct else 0.1
        if dd_pct < 0.001: dd_pct = 0.5
        net_fav = fav - spread_pct
        if net_fav <= 0: continue
        net_ev = sig.confidence * min(net_fav / dd_pct, 3.0)
        if net_ev < EV_THRESHOLD: continue

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
                executor._position = None
                continue

        if result and executor.is_in_position:
            buf = getattr(engine, '_streaming_buffer', None)
            if buf and buf._pattern_buffer:
                ls = buf._pattern_buffer[-1]
                csax = [str(s) for s in ls] if isinstance(ls, (tuple, list)) else [str(ls)]
                if csax: executor.check_walk_forward(csax, cp)

    if executor.is_in_position and executor._position:
        lp = float(oos_df["close"].iloc[-1])
        c = executor.force_close(lp, "REPLAY_END")
        pnl = c.pnl_pct or 0.0
        if pnl > 0: wins += 1; gross_profit += pnl
        else: losses += 1; gross_loss += abs(pnl)
        total_pnl += pnl

    total = wins + losses
    wr = round((wins / total * 100), 1) if total > 0 else 0
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.99 if gross_profit > 0 else 0.0)
    return total, wr, round(total_pnl, 2), pf, longs, shorts


if __name__ == "__main__":
    symbols = ["SOL/USDT", "BTC/USDT", "DOGE/USDT", "LINK/USDT"]
    all_ok = True

    for run in range(1, 4):
        print(f"\n{'='*60}")
        print(f"  VERIFICATION RUN {run}/3")
        print(f"{'='*60}")
        for sym in symbols:
            result = run_backtest(sym)
            if result == -1:
                print(f"  {sym}: ❌ NO DATA")
                all_ok = False
            else:
                trades, wr, pnl, pf, l, s = result
                status = "✅" if trades > 0 else "❌"
                print(f"  {sym}: {status} {trades} trades | WR={wr}% | PnL={pnl}% | PF={pf} | L={l} S={s}")
                if trades == 0:
                    all_ok = False

    print(f"\n{'='*60}")
    if all_ok:
        print("  ✅ ALL 3 RUNS PASSED — backtest produces trades consistently")
    else:
        print("  ❌ SOME RUNS FAILED — check logs above")
    print(f"{'='*60}")
