#!/usr/bin/env python3
"""
v59 — AGGRESSIVE PUSH for "more profit in less time" per user request.

v58d baseline: A 0.030, B 0.15, adaptive ATR 0.6/0.5, partial3 1.25R
  WR 79.4%, P&L +32.12, MaxDD 0.21%, Profit 67%, PF 2.85

User: "tenemos que tener mucha mas ganancia y en mas corto tiempo"

3 LEVERS:
  1) FREQUENCY  → more trades/hour (lower ATR floor, shorter cooldown)
  2) SIZE       → bigger entries with tiered adaptive protection
  3) SPEED      → faster TP capture (partial3 1.15R, partial2 0.8R)

Variants:
  v59a — ATR floor 0.50 (more trades in calm markets)
  v59b — ATR floor 0.55
  v59c — cooldown 30min (was 45)
  v59d — cooldown 25min
  v59e — A 0.035 + B 0.175 + tiered 0.4/0.7/1.0 (push both)
  v59f — A 0.040 + B 0.175 + tiered (push A)
  v59g — A 0.035 + B 0.20 + tiered (push B)
  v59h — A 0.030 + B 0.175 + tiered (modest B)
  v59i — partial3 at 1.15R (faster p3)
  v59j — partial2 at +0.8R (faster p2)
  v59k — COMBO: A 0.035 + B 0.175 tiered + ATR floor 0.55 + p3 1.15R + cd 30min
  v59l — MAX: A 0.040 + B 0.20 tiered + ATR floor 0.50 + p3 1.10R + cd 25min
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v51_push import make_strategies_v51
from v53_push import EngineSimV53, make_v53_config
from v57_push import EngineSimV57


def v58d_base(strat_kwargs=None, adapt_kwargs=None, **overrides):
    """v58d config: A 0.030 + B 0.15 + adaptive ATR 0.6/0.5 (current champion)."""
    strat_kwargs = strat_kwargs or {'b_pos_size': 0.15, 'a_pos_size': 0.030}
    cfg = make_v53_config(
        strat_kwargs=strat_kwargs,
        partial1_pct=0.05, partial2_pct=0.10,
        partial3_r=1.25, partial3_pct=0.15,
    )
    cfg['adaptive_atr_threshold_pct'] = 0.6
    cfg['adaptive_atr_size_mult'] = 0.5
    if adapt_kwargs:
        cfg.update(adapt_kwargs)
    cfg.update(overrides)
    return cfg


TIERED = [(0.6, 0.4), (0.8, 0.7), (float('inf'), 1.0)]

CONFIGS = {
    # v58d baseline (control)
    'v58d_baseline': v58d_base(),
    # ── FREQUENCY: more trades/hour ──
    'v59a_atr_floor_0.50': v58d_base(atr_floor_pct=0.50),
    'v59b_atr_floor_0.55': v58d_base(atr_floor_pct=0.55),
    'v59c_cd_30min': v58d_base(strat_kwargs={'b_pos_size': 0.15, 'a_pos_size': 0.030,
                                              'sl_cooldown_min': 30, 'tp_cooldown_min': 30}),
    'v59d_cd_25min': v58d_base(strat_kwargs={'b_pos_size': 0.15, 'a_pos_size': 0.030,
                                              'sl_cooldown_min': 25, 'tp_cooldown_min': 25}),
    # ── SIZE: bigger entries w/ tiered protection ──
    'v59e_a035_b175_tiered': v58d_base(strat_kwargs={'b_pos_size': 0.175, 'a_pos_size': 0.035},
                                        tiered_atr=TIERED),
    'v59f_a040_b175_tiered': v58d_base(strat_kwargs={'b_pos_size': 0.175, 'a_pos_size': 0.040},
                                        tiered_atr=TIERED),
    'v59g_a035_b020_tiered': v58d_base(strat_kwargs={'b_pos_size': 0.20, 'a_pos_size': 0.035},
                                        tiered_atr=TIERED),
    'v59h_a030_b175_tiered': v58d_base(strat_kwargs={'b_pos_size': 0.175, 'a_pos_size': 0.030},
                                        tiered_atr=TIERED),
    # ── SPEED: faster TP capture ──
    'v59i_p3_1.15R': v58d_base(partial3_r=1.15),
    'v59j_p2_0.8R': v58d_base(partial2_r=0.8),
    # ── COMBO: aggressive all-in ──
    'v59k_combo_aggr': v58d_base(
        strat_kwargs={'b_pos_size': 0.175, 'a_pos_size': 0.035,
                      'sl_cooldown_min': 30, 'tp_cooldown_min': 30},
        tiered_atr=TIERED, atr_floor_pct=0.55, partial3_r=1.15),
    'v59l_max_aggr': v58d_base(
        strat_kwargs={'b_pos_size': 0.20, 'a_pos_size': 0.040,
                      'sl_cooldown_min': 25, 'tp_cooldown_min': 25},
        tiered_atr=TIERED, atr_floor_pct=0.50, partial3_r=1.10),
}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV57(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
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


SEEDS_ALL = [2024, 7, 42, 1337, 99, 555, 31337, 8, 1234, 7777, 2025, 314]
RESULTS_FILE = '/tmp/v59_seeds.json'

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
        baseline = 'v58d_baseline'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L ({len(seeds)} seeds):\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*160)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        print("WINNER SELECTION (target: P&L > baseline + MaxDD ≤ 0.30% + Profit ≥ 67%)")
        print("=" * 80)
        baseline_pnl = agg[baseline]['pnl_mean']
        baseline_dd = agg[baseline]['max_dd_mean']
        candidates = [(name, m) for name, m in agg.items()
                      if name != baseline
                      and m['pnl_mean'] > baseline_pnl
                      and m['max_dd_mean'] <= 0.30
                      and m['profitable_seeds'] >= 67]
        if candidates:
            candidates.sort(key=lambda x: (x[1]['pnl_mean'], x[1]['profitable_seeds']), reverse=True)
            w = candidates[0]
            print(f"\n🏆 WINNER (12-seed validated): {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f} (vs base {baseline_pnl:+.2f})  Profit {w[1]['profitable_seeds']:.0f}%  AvgR {w[1]['avg_r_mean']:+.3f}  MaxDD {w[1]['max_dd_mean']:.2f}%  PF {w[1]['pf_mean']:.2f}")
        else:
            print("\n  ⚠️ No config beat baseline. Top 5 by P&L:")
            ranked = sorted(agg.items(), key=lambda x: x[1]['pnl_mean'], reverse=True)
            for i, (name, m) in enumerate(ranked[:5]):
                vs = m['pnl_mean'] - baseline_pnl
                print(f"  #{i+1} {name:<32} P&L {m['pnl_mean']:+.2f} ({vs:+.2f})  WR {m['wr_mean']:.1f}%  Profit {m['profitable_seeds']:.0f}%  MaxDD {m['max_dd_mean']:.2f}%")
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
