"""PPMT v2.2.2 — Reverse Direction Test

Hypothesis: if the motor systematically produces WR < 50%, it is anti-
predictive. Inverting the predicted direction should produce WR > 50%.
This is a quick diagnostic to confirm the hypothesis before designing
a fix.

Also tests:
  - Filter only ultra-high-confidence trades (top 10% by edge)
  - Walk-forward: build on first 30d, test on next 30d, repeat
"""
import sys, os, json, time, copy, gc
from pathlib import Path
from dataclasses import asdict
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/scripts")
sys.path.insert(0, "/home/z/my-project/ppmt/src")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")
import logging
logging.basicConfig(level=logging.WARNING)
for n in ["ppmt","ppmt.engine","ppmt.core","ppmt.data"]:
    logging.getLogger(n).setLevel(logging.WARNING)

from ppmt.engine.ppmt import PPMT
from ppmt.engine.weights import AdaptiveWeights
from ppmt.core.sax import LEVEL_DUAL_ALPHA_CONFIG, LEVEL_DUAL_ALPHA_TF_OVERRIDES
from ppmt_grid_search import (
    load_ohlcv, build_engine, compute_metrics, monte_carlo, Trade,
    TOKENS, TF, IS_DAYS, OOS_DAYS,
)
from ppmt_v221_atr import atr, Config221, backtest_v221

def backtest_reverse(engine, oos_df, cfg, symbol, reverse=False):
    """Same as backtest_v221 but with optional direction inversion."""
    trades = []
    position = None
    buf_size = max(
        engine.sax_n1.window_size * engine.pl_n1,
        engine.sax_n2.window_size * engine.pl_n2,
        engine.sax_n3.window_size * engine.pl_n3,
        engine.sax_n4.window_size * engine.pl_n4,
    ) + 20
    engine.weights.n1_universal = cfg.weights[0]
    engine.weights.n2_asset_class = cfg.weights[1]
    engine.weights.n3_per_asset = cfg.weights[2]
    engine.weights.n4_per_asset_regime = cfg.weights[3]
    engine.weights.n5_btc_context = 0.0

    atr_series = atr(oos_df, period=14)
    spread_map = {"blue_chip":0.010,"large_cap":0.015,"mid_cap":0.020,
                  "meme":0.050,"defi":0.025,"new_launch":0.080,"default":0.020}
    asset_class = next((ac for s, ac in TOKENS if s == symbol), "default")
    spread_pct = spread_map.get(asset_class, 0.020)

    n = len(oos_df)
    for i in range(buf_size, n):
        window = oos_df.iloc[i-buf_size:i]
        if position is not None:
            candle = oos_df.iloc[i]
            exit_reason = None; exit_price = None
            if position.direction == "LONG":
                if candle["low"] <= position.sl_price:
                    exit_price = position.sl_price; exit_reason = "stop_loss"
                elif candle["high"] >= position.tp_price:
                    exit_price = position.tp_price; exit_reason = "take_profit"
            else:
                if candle["high"] >= position.sl_price:
                    exit_price = position.sl_price; exit_reason = "stop_loss"
                elif candle["low"] <= position.tp_price:
                    exit_price = position.tp_price; exit_reason = "take_profit"
            if exit_reason is None and i - position.entry_idx >= cfg.max_hold_bars:
                exit_price = candle["close"]; exit_reason = "max_hold"
            if exit_reason is None and i == n - 1:
                exit_price = candle["close"]; exit_reason = "end_of_data"
            if exit_reason is not None:
                if position.direction == "LONG":
                    gross_pct = (exit_price - position.entry_price) / position.entry_price * 100
                else:
                    gross_pct = (position.entry_price - exit_price) / position.entry_price * 100
                notional = getattr(position, "_notional", cfg.initial_capital * cfg.risk_pct / 0.01)
                fee_dollars = notional * 2 * cfg.fee_pct / 100
                gross_dollars = notional * gross_pct / 100
                net_dollars = gross_dollars - fee_dollars
                position.pnl_pct = net_dollars / cfg.initial_capital * 100
                position.exit_idx = i; position.exit_price = exit_price
                position.exit_reason = exit_reason
                trades.append(position); position = None
            continue

        try:
            result = engine.match_raw(current_symbols=[],
                current_price=oos_df.iloc[i]["close"],
                recent_candles=window)
        except: continue

        if result.direction == "FLAT": continue
        if result.weighted_confidence < cfg.min_confidence: continue

        # Reverse direction if requested
        direction = result.direction
        if reverse:
            direction = "SHORT" if direction == "LONG" else "LONG"

        best_match = None; best_conf = 0.0
        for lvl_match, lvl_w in [
            (result.n1_match, cfg.weights[0]),
            (result.n2_match, cfg.weights[1]),
            (result.n3_match, cfg.weights[2]),
            (result.n4_match, cfg.weights[3]),
        ]:
            if lvl_match and lvl_match.node and lvl_w > 0:
                c = lvl_match.node.metadata.confidence
                if c > best_conf:
                    best_conf = c; best_match = lvl_match
        if best_match is None or best_match.node is None: continue
        meta = best_match.node.metadata
        if meta.historical_count < cfg.min_node_count: continue

        long_e = meta.long_edge(); short_e = meta.short_edge()
        dir_edge = abs(long_e - short_e)
        if dir_edge < cfg.min_dir_edge: continue

        current_atr_pct = atr_series.iloc[i] / oos_df.iloc[i]["close"] * 100
        if not np.isfinite(current_atr_pct) or current_atr_pct <= 0: continue
        sl_dist_pct = current_atr_pct * cfg.sl_atr_mult
        tp_dist_pct = current_atr_pct * cfg.tp_atr_mult
        sl_dist_pct = min(sl_dist_pct, 1.5); sl_dist_pct = max(sl_dist_pct, 0.15)
        tp_dist_pct = min(tp_dist_pct, 3.0); tp_dist_pct = max(tp_dist_pct, 0.3)

        effective_move = meta.avg_move_long if direction == "LONG" else abs(meta.avg_move_short)
        if effective_move < cfg.hard_move_floor: continue
        net_favorable = effective_move - spread_pct
        if net_favorable <= 0: continue
        net_rr = min(net_favorable / max(sl_dist_pct, 0.01), 3.0)
        net_ev = result.weighted_confidence * net_rr
        if net_ev < cfg.ev_threshold: continue

        entry_price = oos_df.iloc[i]["close"]
        sl_dist = sl_dist_pct / 100.0; tp_dist = tp_dist_pct / 100.0
        if direction == "LONG":
            sl_price = entry_price * (1 - sl_dist); tp_price = entry_price * (1 + tp_dist)
        else:
            sl_price = entry_price * (1 + sl_dist); tp_price = entry_price * (1 - tp_dist)

        position_notional = cfg.initial_capital * cfg.risk_pct / sl_dist
        position_notional = min(position_notional, cfg.initial_capital)
        position = Trade(symbol=symbol, direction=direction,
            entry_idx=i, exit_idx=-1, entry_price=entry_price, exit_price=0.0,
            sl_price=sl_price, tp_price=tp_price, pnl_pct=0.0, exit_reason="",
            confidence=best_conf, weighted_confidence=result.weighted_confidence)
        position._notional = position_notional
    return trades

def main():
    t0 = time.time()
    print(f"=== PPMT v2.2.2 — Reverse Direction Diagnostic ===\n", flush=True)

    data = {}
    for sym, ac in TOKENS:
        df = load_ohlcv(sym, TF)
        is_df = df.iloc[:-OOS_DAYS*288]
        oos_df = df.iloc[-OOS_DAYS*288:].reset_index(drop=True)
        data[sym] = (ac, is_df, oos_df)

    # Build engines
    engines = {}
    for sym, (ac, is_df, _) in data.items():
        e = build_engine(sym, ac, is_df, alpha_n3n4=5)
        engines[sym] = e
    print(f"Built in {time.time()-t0:.1f}s\n", flush=True)

    # Use the balanced config p2
    cfg = Config221(
        name="diag",
        weights=(0.30, 0.20, 0.30, 0.20),
        ev_threshold=0.15, sl_atr_mult=1.5, tp_atr_mult=3.0,
        min_dir_edge=0.10, min_node_count=5,
        hard_move_floor=0.10, min_confidence=0.10,
    )

    print(f"Comparing NORMAL vs REVERSE direction\n", flush=True)
    print(f"{'Token':12s} | {'Mode':8s} | {'n':>5s} | {'WR':>6s} | {'PnL%':>8s} | {'PF':>5s} | {'shorts%':>8s}", flush=True)
    print("-" * 70, flush=True)

    all_normal = []; all_reverse = []
    for sym, engine in engines.items():
        ac, is_df, oos_df = data[sym]
        # Reset weights
        engine.weights.n1_universal = 0.10
        engine.weights.n2_asset_class = 0.00
        engine.weights.n3_per_asset = 0.90
        engine.weights.n4_per_asset_regime = 0.00
        trades_n = backtest_reverse(engine, oos_df, cfg, sym, reverse=False)
        engine.weights.n1_universal = 0.10
        engine.weights.n2_asset_class = 0.00
        engine.weights.n3_per_asset = 0.90
        engine.weights.n4_per_asset_regime = 0.00
        trades_r = backtest_reverse(engine, oos_df, cfg, sym, reverse=True)
        m_n = compute_metrics(trades_n); m_r = compute_metrics(trades_r)
        all_normal.extend(trades_n); all_reverse.extend(trades_r)
        print(f"{sym:12s} | {'NORMAL':8s} | {m_n['n_trades']:5d} | {m_n['wr']:5.1f}% | "
              f"{m_n['pnl_pct']:+7.1f}% | {m_n['pf']:4.2f} | {m_n['shorts_pct']:6.1f}%", flush=True)
        print(f"{'':12s} | {'REVERSE':8s} | {m_r['n_trades']:5d} | {m_r['wr']:5.1f}% | "
              f"{m_r['pnl_pct']:+7.1f}% | {m_r['pf']:4.2f} | {m_r['shorts_pct']:6.1f}%", flush=True)

    print("-" * 70, flush=True)
    mn = compute_metrics(all_normal); mr = compute_metrics(all_reverse)
    print(f"{'PORTFOLIO':12s} | {'NORMAL':8s} | {mn['n_trades']:5d} | {mn['wr']:5.1f}% | "
          f"{mn['pnl_pct']:+7.1f}% | {mn['pf']:4.2f} | {mn['shorts_pct']:6.1f}%", flush=True)
    print(f"{'PORTFOLIO':12s} | {'REVERSE':8s} | {mr['n_trades']:5d} | {mr['wr']:5.1f}% | "
          f"{mr['pnl_pct']:+7.1f}% | {mr['pf']:4.2f} | {mr['shorts_pct']:6.1f}%", flush=True)

    print(f"\nMC NORMAL:", monte_carlo(all_normal, n_sims=2000), flush=True)
    print(f"MC REVERSE:", monte_carlo(all_reverse, n_sims=2000), flush=True)

    print(f"\nTotal: {time.time()-t0:.1f}s", flush=True)

if __name__ == "__main__":
    main()
