#!/usr/bin/env python3
"""
v41 — Push v40b (multi-partial) further

v40b champion (8 seeds): WR 76.8%, P&L +30.89, Profit 88%, MaxDD 0.22%, AvgR +0.78, PF 1.89

Multi-partial logic: 30%@0.5R + 30%@1.0R + 40% trailing @0.5 ATR

NEW IDEAS to push P&L higher while keeping WR ≥70%:
1. v41a — Tighter trail 0.4 (lock more from trailing 40%)
2. v41b — Wider partial2 1.2R (let profits run more)
3. v41c — Tighter SL 1.3 (smaller losses)
4. v41d — Wider SL 1.5 (fewer stopped out)
5. v41e — ATR floor 0.60 (fewer calm-regime trades)
6. v41f — Trend filter SMA(100) on A and D
7. v41g — Larger first partial 40%@0.5R + 30%@1.0R + 30% trailing
8. v41h — Smaller first partial 20%@0.5R + 30%@1.0R + 50% trailing (let runner run)
"""
import random, statistics, math, sys, os, json, time
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from copy import deepcopy

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40


def make_strategies(sl_mult=1.4, tp_mult=1.2, rsi_lo=30, rsi_hi=70, momentum_min=0.40,
                    tp_cooldown_min=45, sl_cooldown_min=45):
    return {
        'A': {'momentum_min': momentum_min, 'max_pos': 1, 'sl_mult': sl_mult, 'tp_mult': tp_mult,
              'catsl_mult': 4.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'rsi_min': 25, 'rsi_max': 75, 'time_stop': 2400, 'pos_size_pct': 0.025},
        'B': {'rsi_lo': rsi_lo, 'rsi_hi': rsi_hi, 'max_pos': 1, 'sl_mult': sl_mult, 'tp_mult': tp_mult,
              'catsl_mult': 4.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'enabled': True, 'time_stop': 2400, 'pos_size_pct': 0.10},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': sl_mult * 0.75, 'tp_mult': tp_mult * 0.83,
              'catsl_mult': 3.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'time_stop': 2400, 'pos_size_pct': 0.05},
        'E': {'enabled': False},
    }


CONFIGS = {
    # v40b champion (control)
    'v40b_baseline': v40.make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.30,
        partial2_r=1.0, partial2_pct=0.30,
        trail_atr=0.5, atr_floor_pct=0.58,
    ),
    # v41a — Tighter trail 0.4
    'v41a_trail_0.4': v40.make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.30,
        partial2_r=1.0, partial2_pct=0.30,
        trail_atr=0.4, atr_floor_pct=0.58,
    ),
    # v41b — Wider partial2 1.2R
    'v41b_p2_1.2R': v40.make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.30,
        partial2_r=1.2, partial2_pct=0.30,
        trail_atr=0.5, atr_floor_pct=0.58,
    ),
    # v41c — Tighter SL 1.3
    'v41c_SL_1.3': v40.make_config(
        strategies=make_strategies(sl_mult=1.3, tp_mult=1.2),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.30,
        partial2_r=1.0, partial2_pct=0.30,
        trail_atr=0.5, atr_floor_pct=0.58,
    ),
    # v41d — Wider SL 1.5
    'v41d_SL_1.5': v40.make_config(
        strategies=make_strategies(sl_mult=1.5, tp_mult=1.2),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.30,
        partial2_r=1.0, partial2_pct=0.30,
        trail_atr=0.5, atr_floor_pct=0.58,
    ),
    # v41e — ATR floor 0.60
    'v41e_atr_0.60': v40.make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.30,
        partial2_r=1.0, partial2_pct=0.30,
        trail_atr=0.5, atr_floor_pct=0.60,
    ),
    # v41f — Trend filter SMA(100)
    'v41f_trend': v40.make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.30,
        partial2_r=1.0, partial2_pct=0.30,
        trail_atr=0.5, atr_floor_pct=0.58,
        trend_sma_period=100, trend_buffer_pct=0.1,
    ),
    # v41g — Larger first partial 40%@0.5R + 30%@1.0R + 30% trailing
    'v41g_p1_40pct': v40.make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.40,
        partial2_r=1.0, partial2_pct=0.30,
        trail_atr=0.5, atr_floor_pct=0.58,
    ),
    # v41h — Smaller first partial 20%@0.5R + 30%@1.0R + 50% trailing
    'v41h_p1_20pct_runner': v40.make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.20,
        partial2_r=1.0, partial2_pct=0.30,
        trail_atr=0.5, atr_floor_pct=0.58,
    ),
}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [v40.EngineSimV40(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
    for tick in range(v40.TOTAL_TICKS):
        for sym in all_prices:
            if tick + 1 < 60: continue
            prices_slice = all_prices[sym][max(0, tick-250):tick+1]
            for engine in engines:
                engine._try_strategy_a(sym, prices_slice, tick)
                engine._try_strategy_b(sym, prices_slice, tick)
                engine._try_strategy_d(sym, prices_slice, tick)
                engine._try_strategy_e(sym, prices_slice, tick)
                engine._check_stops(sym, prices_slice, tick)
        for engine in engines:
            engine.update_equity(all_prices, tick)
    return {engine.name: engine.get_metrics() for engine in engines}


SEEDS_ALL = [2024, 7, 42, 1337, 99, 555, 31337, 8]
RESULTS_FILE = '/tmp/v41_seeds.json'

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'aggregate':
        if not os.path.exists(RESULTS_FILE):
            print(f"No results at {RESULTS_FILE}"); sys.exit(1)
        with open(RESULTS_FILE) as f: all_results = json.load(f)
        seed_results = list(all_results.values())
        seeds = [int(s) for s in all_results.keys()]
        agg = {}
        for name in CONFIGS.keys():
            seed_metrics = [r[name] for r in seed_results if name in r]
            if not seed_metrics: continue
            agg[name] = {
                'wr_mean': statistics.mean(m['wr'] for m in seed_metrics),
                'wr_std': statistics.stdev(m['wr'] for m in seed_metrics) if len(seed_metrics) > 1 else 0,
                'pnl_mean': statistics.mean(m['pnl'] for m in seed_metrics),
                'pnl_std': statistics.stdev(m['pnl'] for m in seed_metrics) if len(seed_metrics) > 1 else 0,
                'pf_mean': statistics.mean(m['pf'] for m in seed_metrics),
                'sharpe_mean': statistics.mean(m['sharpe'] for m in seed_metrics),
                'max_consec_loss_mean': statistics.mean(m['max_consec_loss'] for m in seed_metrics),
                'consistency_mean': statistics.mean(m['consistency'] for m in seed_metrics),
                'avg_r_mean': statistics.mean(m['avg_r'] for m in seed_metrics),
                'trades_mean': statistics.mean(m['trades'] for m in seed_metrics),
                'max_dd_mean': statistics.mean(m['max_dd'] for m in seed_metrics),
                'profitable_seeds': sum(1 for m in seed_metrics if m['pnl'] > 0) / len(seed_metrics) * 100,
                'wr_above_60_seeds': sum(1 for m in seed_metrics if m['wr'] >= 60) / len(seed_metrics) * 100,
                'pnl_per_seed': [m['pnl'] for m in seed_metrics],
            }
        baseline = 'v40b_baseline'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L:\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*120)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        print("WINNER SELECTION (target: ≥75% profitable + WR≥70 + MaxDD<0.3 + AvgR>0)")
        print("=" * 80)
        candidates = [(name, m) for name, m in agg.items()
                      if m['profitable_seeds'] >= 75 and m['wr_above_60_seeds'] >= 75
                      and m['max_dd_mean'] < 0.3 and m['avg_r_mean'] > 0]
        if candidates:
            candidates.sort(key=lambda x: (x[1]['profitable_seeds'], x[1]['pnl_mean'], x[1]['wr_mean']), reverse=True)
            w = candidates[0]
            print(f"\n🏆 WINNER: {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f}  Profit {w[1]['profitable_seeds']:.0f}%  AvgR {w[1]['avg_r_mean']:+.3f}  MaxDD {w[1]['max_dd_mean']:.2f}%  PF {w[1]['pf_mean']:.2f}")
        else:
            print("\n  ⚠️ No config met all criteria. Top 5:")
            ranked = sorted(agg.items(), key=lambda x: (x[1]['profitable_seeds'], x[1]['pnl_mean']), reverse=True)
            for i, (name, m) in enumerate(ranked[:5]):
                print(f"  #{i+1} {name:<32} WR {m['wr_mean']:.1f}%  P&L {m['pnl_mean']:+.2f}  Profit {m['profitable_seeds']:.0f}%  AvgR {m['avg_r_mean']:+.3f}")
    else:
        seed = int(sys.argv[1])
        print(f"Running seed {seed}...", flush=True)
        start = time.time()
        result = run_single_seed(seed)
        elapsed = time.time() - start
        all_results = {}
        if os.path.exists(RESULTS_FILE):
            with open(RESULTS_FILE) as f: all_results = json.load(f)
        for name, m in result.items():
            m['per_strat'] = {k: v for k, v in m['per_strat'].items()}
        all_results[str(seed)] = result
        with open(RESULTS_FILE, 'w') as f: json.dump(all_results, f, indent=2)
        print(f"Seed {seed} done in {elapsed:.1f}s. P&L: " + ", ".join(f"{n}={m['pnl']:+.0f}" for n, m in result.items()), flush=True)
