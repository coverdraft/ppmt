"""Pure directional edge test: hold for N bars, exit at close, no SL/TP.

If this doesn't show edge, the trie's directional signal has no real predictive power.
If it does show edge, we can layer SL/TP on top.
"""
import sys, os, time, copy, gc, logging
sys.path.insert(0, "/home/z/my-project/ppmt/src")
sys.path.insert(0, "/home/z/my-project/scripts")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")
import logging
logging.basicConfig(level=logging.WARNING)
for name in ["ppmt", "ppmt.engine", "ppmt.core", "ppmt.data"]:
    logging.getLogger(name).setLevel(logging.WARNING)

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict
from scipy.stats import chi2 as chi2_dist

from ppmt.engine.ppmt import PPMT
from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt_grid_search import compute_metrics, Trade
from ppmt_v24_adaptive import (
    load_ohlcv_multiwindow, build_engine_alpha, pattern_stats,
    atr, TF_5M, CANDLES_PER_DAY_5M,
)


@dataclass
class PureCfg:
    is_days: int
    reverse: bool
    hold_bars: int
    chi2_p_threshold: float = 0.40
    min_node_count: int = 5
    min_dir_edge: float = 0.15
    moderate_wr_threshold: float = 0.60
    hard_move_floor: float = 0.04
    weights: Tuple[float, float, float, float] = (0.30, 0.10, 0.50, 0.10)
    alphas: Tuple[int, ...] = (5, 7)
    min_alpha_agreement: int = 2
    min_confidence: float = 0.05
    risk_pct: float = 0.02
    initial_capital: float = 1000.0
    fee_pct: float = 0.04


def backtest_pure(cfg: PureCfg, full_5m, atr_series_5m, oos_start, oos_end,
                  symbol="BTC/USDT", asset_class="blue_chip"):
    spread_pct = {"blue_chip":0.010,"large_cap":0.015,"mid_cap":0.020,
                  "meme":0.050}.get(asset_class, 0.020)
    trades = []
    position = None
    buf_size = 200

    is_size = cfg.is_days * CANDLES_PER_DAY_5M
    is_start = max(0, oos_start - is_size)
    is_df = full_5m.iloc[is_start:oos_start].reset_index(drop=True)

    engines = {}
    for alpha in cfg.alphas:
        try:
            e = build_engine_alpha(symbol, asset_class, is_df, alpha, TF_5M)
            engines[alpha] = e
        except Exception:
            pass
    if not engines:
        return trades, 0

    n_entries = 0
    for i in range(oos_start, oos_end):
        # Exit check
        if position is not None:
            if i - position.entry_idx >= cfg.hold_bars or i == oos_end - 1:
                exit_price = full_5m.iloc[i]["close"]
                if position.direction == "LONG":
                    gross_pct = (exit_price - position.entry_price) / position.entry_price * 100
                else:
                    gross_pct = (position.entry_price - exit_price) / position.entry_price * 100
                notional = getattr(position, "_notional", cfg.initial_capital * cfg.risk_pct / 0.01)
                fee_dollars = notional * 2 * cfg.fee_pct / 100
                gross_dollars = notional * gross_pct / 100
                net_dollars = gross_dollars - fee_dollars
                position.pnl_pct = net_dollars / cfg.initial_capital * 100
                position.exit_idx = i
                position.exit_price = exit_price
                position.exit_reason = "time_exit"
                trades.append(position)
                position = None
            continue  # don't enter on same candle as exit

        # Entry
        if not engines:
            continue

        window_5m = full_5m.iloc[max(0, i-buf_size):i]
        if len(window_5m) < buf_size:
            continue

        current_price = full_5m.iloc[i]["close"]

        votes = []
        for alpha, engine in engines.items():
            engine.weights.n1_universal = cfg.weights[0]
            engine.weights.n2_asset_class = cfg.weights[1]
            engine.weights.n3_per_asset = cfg.weights[2]
            engine.weights.n4_per_asset_regime = cfg.weights[3]
            engine.weights.n5_btc_context = 0.0
            try:
                result = engine.match_raw(
                    current_symbols=[],
                    current_price=current_price,
                    recent_candles=window_5m,
                )
            except Exception:
                continue
            if result.direction == "FLAT":
                continue
            if result.weighted_confidence < cfg.min_confidence:
                continue
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
            if best_match is None or best_match.node is None:
                continue
            meta = best_match.node.metadata
            votes.append((alpha, result.direction, result.weighted_confidence, meta))

        if not votes:
            continue

        long_votes = sum(1 for v in votes if v[1] == "LONG")
        short_votes = sum(1 for v in votes if v[1] == "SHORT")
        if max(long_votes, short_votes) < cfg.min_alpha_agreement:
            continue
        if long_votes > short_votes:
            engine_direction = "LONG"
        elif short_votes > long_votes:
            engine_direction = "SHORT"
        else:
            continue

        agreeing_votes = [v for v in votes if v[1] == engine_direction]
        agreeing_votes.sort(key=lambda v: v[2], reverse=True)
        if not agreeing_votes:
            continue
        chosen_meta = agreeing_votes[0][3]
        chosen_confidence = agreeing_votes[0][2]

        is_pred, p_val, dir_edge, long_wr, short_wr = pattern_stats(
            chosen_meta, cfg.chi2_p_threshold, cfg.min_node_count
        )
        if not is_pred:
            continue
        if dir_edge < cfg.min_dir_edge:
            continue

        max_wr = max(long_wr, short_wr)
        if max_wr < cfg.moderate_wr_threshold:
            continue

        avg_move_raw = chosen_meta.long_stats.avg_move_pct
        effective_move = abs(avg_move_raw)
        if effective_move < cfg.hard_move_floor:
            continue

        if cfg.reverse:
            trade_direction = "SHORT" if engine_direction == "LONG" else "LONG"
        else:
            trade_direction = engine_direction

        # Notional: use $1000 (full capital) for pure directional test
        notional = cfg.initial_capital
        # Fixed $1000 notional → fee is 0.04% × 2 × $1000 = $0.80 per trade
        entry_price = current_price
        position = Trade(
            symbol=symbol, direction=trade_direction,
            entry_idx=i, exit_idx=-1,
            entry_price=entry_price, exit_price=0.0,
            sl_price=0.0, tp_price=0.0,
            pnl_pct=0.0, exit_reason="",
            confidence=chosen_confidence,
            weighted_confidence=chosen_confidence,
        )
        position._notional = notional
        n_entries += 1

    return trades, n_entries


def main():
    t0 = time.time()
    print("=== PURE DIRECTIONAL EDGE TEST (no SL/TP, fixed-time exit) ===\n", flush=True)
    print("Tests if trie's directional signal has any edge at all.\n", flush=True)

    df_5m_all = load_ohlcv_multiwindow("BTC/USDT", TF_5M, windows=["RECENT_2026"])
    full_5m = df_5m_all.reset_index(drop=True)
    print(f"RECENT_2026 5m: {len(full_5m)} candles", flush=True)

    oos_size = 30 * CANDLES_PER_DAY_5M
    oos_end = len(full_5m)
    oos_start = oos_end - oos_size

    atr_series_5m = atr(full_5m, period=14)

    # Test matrix: hold_bars × direction × is_days
    hold_bars_list = [3, 6, 12, 24, 48]  # 15m, 30m, 1h, 2h, 4h on 5m
    is_days_list = [7, 14, 30, 60]

    print(f"\nIS=30d, varying hold_bars and direction:")
    print(f"{'hold':>5s} {'dir':>8s} {'n':>4s} {'WR':>6s} {'PnL':>7s} {'PF':>5s} {'avg':>6s} {'sh%':>5s}")
    print("-" * 60)
    results = []
    for hold in hold_bars_list:
        for rev in [True, False]:
            cfg = PureCfg(is_days=30, reverse=rev, hold_bars=hold)
            t_c = time.time()
            trades, n_entries = backtest_pure(cfg, full_5m, atr_series_5m, oos_start, oos_end)
            m = compute_metrics(trades, cfg.initial_capital)
            results.append({"hold": hold, "reverse": rev, "is_d": 30, "metrics": m})
            dir_label = "REVERSE" if rev else "FOLLOW"
            print(f"{hold:5d} {dir_label:>8s} {m['n_trades']:4d} {m['wr']:5.1f}% "
                  f"{m['pnl_pct']:+6.1f}% {m['pf']:5.2f} {m['avg_pnl']:+5.2f} "
                  f"{m['shorts_pct']:4.1f}%  ({time.time()-t_c:.0f}s)", flush=True)
            gc.collect()

    print(f"\nVarying IS_days, hold=12 bars:")
    print(f"{'IS_d':>4s} {'dir':>8s} {'n':>4s} {'WR':>6s} {'PnL':>7s} {'PF':>5s} {'avg':>6s} {'sh%':>5s}")
    print("-" * 60)
    for is_d in is_days_list:
        for rev in [True, False]:
            cfg = PureCfg(is_days=is_d, reverse=rev, hold_bars=12)
            t_c = time.time()
            trades, n_entries = backtest_pure(cfg, full_5m, atr_series_5m, oos_start, oos_end)
            m = compute_metrics(trades, cfg.initial_capital)
            results.append({"hold": 12, "reverse": rev, "is_d": is_d, "metrics": m})
            dir_label = "REVERSE" if rev else "FOLLOW"
            print(f"{is_d:4d} {dir_label:>8s} {m['n_trades']:4d} {m['wr']:5.1f}% "
                  f"{m['pnl_pct']:+6.1f}% {m['pf']:5.2f} {m['avg_pnl']:+5.2f} "
                  f"{m['shorts_pct']:4.1f}%  ({time.time()-t_c:.0f}s)", flush=True)
            gc.collect()

    # Also test: NO FILTERS (raw signal)
    print(f"\nNO FILTERS (raw directional signal), IS=30d, hold=12:")
    print(f"{'dir':>8s} {'n':>4s} {'WR':>6s} {'PnL':>7s} {'PF':>5s} {'avg':>6s}")
    print("-" * 40)
    for rev in [True, False]:
        cfg = PureCfg(is_days=30, reverse=rev, hold_bars=12,
                      chi2_p_threshold=1.0, min_node_count=0,
                      min_dir_edge=0.0, moderate_wr_threshold=0.0,
                      hard_move_floor=0.0, min_confidence=0.0,
                      min_alpha_agreement=1)
        t_c = time.time()
        trades, n_entries = backtest_pure(cfg, full_5m, atr_series_5m, oos_start, oos_end)
        m = compute_metrics(trades, cfg.initial_capital)
        dir_label = "REVERSE" if rev else "FOLLOW"
        print(f"{dir_label:>8s} {m['n_trades']:4d} {m['wr']:5.1f}% "
              f"{m['pnl_pct']:+6.1f}% {m['pf']:5.2f} {m['avg_pnl']:+5.2f}  "
              f"({time.time()-t_c:.0f}s)", flush=True)

    print(f"\nTotal time: {time.time()-t0:.1f}s", flush=True)

    print("\n=== TOP 5 by PnL ===")
    results.sort(key=lambda r: r["metrics"]["pnl_pct"], reverse=True)
    for r in results[:5]:
        m = r["metrics"]
        dir_label = "REVERSE" if r["reverse"] else "FOLLOW"
        print(f"  IS={r['is_d']:3d}d hold={r['hold']:3d} {dir_label} "
              f"n={m['n_trades']:3d} WR={m['wr']:.1f}% PnL={m['pnl_pct']:+.1f}% "
              f"PF={m['pf']:.2f} avg={m['avg_pnl']:+.2f}%")


if __name__ == "__main__":
    main()
