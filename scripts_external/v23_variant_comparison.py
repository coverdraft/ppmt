"""Quick comparison: 4 variants on SOL/USDT 14d to identify which component
helps or hurts. Variants:
  A. v23 default (all 4 strategies combined)
  B. v23 no multi-TF (just walk-forward + stat filter + alpha ensemble)
  C. v23 no stat filter (just walk-forward + alpha ensemble + multi-TF)
  D. v23 reversed direction (test if engine is anti-predicting)

14d OOS = 4032 5m candles. Should run in <2 min total.
"""
import sys, os, time, copy
sys.path.insert(0, "/home/z/my-project/scripts")
sys.path.insert(0, "/home/z/my-project/ppmt/src")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")

import logging
logging.basicConfig(level=logging.WARNING)
for name in ["ppmt", "ppmt.engine", "ppmt.core", "ppmt.data"]:
    logging.getLogger(name).setLevel(logging.WARNING)

import ppmt_v23_combined as v23
from ppmt_v23_combined import (
    ConfigV23, walk_forward_backtest, pattern_is_predictive,
    atr, build_engine_alpha, get_15m_consensus,
    IS_DAYS, CANDLES_PER_DAY_5M, CANDLES_PER_DAY_15M,
)
from ppmt_grid_search import load_ohlcv, compute_metrics, Trade
import numpy as np
import pandas as pd
from dataclasses import asdict

# Reduce OOS for speed
v23.OOS_DAYS = 14


def make_config(name: str, **kwargs) -> ConfigV23:
    base = dict(
        name=name,
        weights=(0.40, 0.20, 0.20, 0.20),
        chi2_p_threshold=0.30,
        min_node_count=8,
        use_alpha_ensemble=True,
        alphas=(5, 7),
        min_alpha_agreement=2,
        sl_atr_mult=1.0,
        tp_atr_mult=2.0,
        min_confidence=0.05,
        hard_move_floor=0.05,
        min_dir_edge=0.10,
        use_multi_tf=True,
        risk_pct=0.02,
        max_hold_bars=24,
    )
    base.update(kwargs)
    return ConfigV23(**base)


def main():
    t0 = time.time()
    print("=== v2.3 VARIANT COMPARISON — SOL/USDT 14d OOS ===\n", flush=True)

    sym = "SOL/USDT"
    ac = "large_cap"
    df_5m = load_ohlcv(sym, "5m")
    df_15m = load_ohlcv(sym, "15m")
    print(f"  5m: {len(df_5m)} | 15m: {len(df_15m)}\n", flush=True)

    variants = [
        make_config("A_default"),
        make_config("B_no_mtf", use_multi_tf=False),
        make_config("C_no_stat", chi2_p_threshold=1.0, min_dir_edge=0.0),
        # D: reversed direction handled separately below
    ]

    results = []
    for cfg in variants:
        t1 = time.time()
        trades = walk_forward_backtest(sym, ac, df_5m, df_15m, cfg)
        m = compute_metrics(trades, cfg.initial_capital)
        results.append((cfg.name, m, len(trades)))
        print(f"  {cfg.name:20s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
              f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
              f"shorts={m['shorts_pct']:4.1f}% ({time.time()-t1:.0f}s)", flush=True)

    # Variant D: reversed direction
    print(f"\n  D_reversed (swap LONG<->SHORT in entry)...", flush=True)
    t1 = time.time()
    cfg_d = make_config("D_reversed")
    # Patch: we'll modify the walk_forward_backtest call to swap direction
    # Easier: run normal backtest, then swap each trade's direction PnL sign
    trades = walk_forward_backtest(sym, ac, df_5m, df_15m, cfg_d)
    # Swap PnL sign for each trade (simulating reverse direction)
    for t in trades:
        t.pnl_pct = -t.pnl_pct
    m = compute_metrics(trades, cfg_d.initial_capital)
    results.append(("D_reversed", m, len(trades)))
    print(f"  {'D_reversed':20s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
          f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
          f"shorts={m['shorts_pct']:4.1f}% ({time.time()-t1:.0f}s)", flush=True)

    # Summary
    print(f"\n=== SUMMARY ===", flush=True)
    for name, m, n in results:
        marker = "✓" if m["pnl_pct"] > 0 else "✗"
        print(f"  {marker} {name:20s} n={n:3d} WR={m['wr']:5.1f}% "
              f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f}", flush=True)

    print(f"\nTotal time: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
