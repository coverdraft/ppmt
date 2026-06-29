#!/usr/bin/env python3
"""
v60 — Push v59f's tiered approach further. MORE PROFIT, SAME PROTECTION.

v59f baseline: A 0.040 + B 0.175 + tiered 0.4/0.7/1.0
  WR 79.4%, P&L +36.03, MaxDD 0.23%, Profit 67%, PF 2.63

User: "mucha mas ganancia y en mas corto tiempo"

3 NEW LEVERS (parameter-only, no engine changes):
  1) SIZE     → push A and B even harder with tiered protection
  2) SL/LOCK  → wider SL (1.6/1.7) lets trades breathe; tighter lock captures more
  3) TRAIL    → tighter trail (0.25) locks more profit on runners

Variants:
  v60a — A 0.045 + B 0.20 tiered (push both)
  v60b — A 0.050 + B 0.20 tiered (push A more)
  v60c — A 0.040 + B 0.20 tiered (push B more)
  v60d — A 0.045 + B 0.225 tiered (push B even more)
  v60e — A 0.050 + B 0.225 tiered (push both max)
  v60f — SL 1.6 + A 0.040 + B 0.175 tiered (wider SL)
  v60g — SL 1.7 + A 0.040 + B 0.175 tiered (wider SL more)
  v60h — lock_offset 0.40 + A 0.040 + B 0.175 tiered (tighter BE)
  v60i — trail 0.25 + A 0.040 + B 0.175 tiered (tighter trail)
  v60j — COMBO: A 0.050 + B 0.225 + SL 1.6 + lock 0.40 + trail 0.25 tiered
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v51_push import make_strategies_v51
from v53_push import EngineSimV53, make_v53_config
from v57_push import EngineSimV57


def v59f_base(strat_kwargs=None, adapt_kwargs=None, **overrides):
    """v59f config: A 0.040 + B 0.175 + TIERED adaptive 0.4/0.7/1.0."""
    strat_kwargs = strat_kwargs or {'b_pos_size': 0.175, 'a_pos_size': 0.040}
    cfg = make_v53_config(
        strat_kwargs=strat_kwargs,
        partial1_pct=0.05, partial2_pct=0.10,
        partial3_r=1.25, partial3_pct=0.15,
    )
    cfg['adaptive_atr_threshold_pct'] = 0.6
    cfg['adaptive_atr_size_mult'] = 0.5
    cfg['tiered_atr'] = [(0.6, 0.4), (0.8, 0.7), (float('inf'), 1.0)]
    if adapt_kwargs:
        cfg.update(adapt_kwargs)
    cfg.update(overrides)
    return cfg


CONFIGS = {
    # v59f baseline (control)
    'v59f_baseline': v59f_base(),
    # ── SIZE: push harder ──
    'v60a_a045_b020_tiered': v59f_base(strat_kwargs={'b_pos_size': 0.20, 'a_pos_size': 0.045}),
    'v60b_a050_b020_tiered': v59f_base(strat_kwargs={'b_pos_size': 0.20, 'a_pos_size': 0.050}),
    'v60c_a040_b020_tiered': v59f_base(strat_kwargs={'b_pos_size': 0.20, 'a_pos_size': 0.040}),
    'v60d_a045_b225_tiered': v59f_base(strat_kwargs={'b_pos_size': 0.225, 'a_pos_size': 0.045}),
    'v60e_a050_b225_tiered': v59f_base(strat_kwargs={'b_pos_size': 0.225, 'a_pos_size': 0.050}),
    # ── SL/LOCK/TRAIL: tighter risk management ──
    'v60f_sl16': v59f_base(strat_kwargs={'b_pos_size': 0.175, 'a_pos_size': 0.040, 'sl_mult': 1.6}),
    'v60g_sl17': v59f_base(strat_kwargs={'b_pos_size': 0.175, 'a_pos_size': 0.040, 'sl_mult': 1.7}),
    'v60h_lock040': v59f_base(lock_offset_r=0.40),
    'v60i_trail025': v59f_base(trail_atr=0.25),
    # ── COMBO: max aggressive ──
    'v60j_combo_max': v59f_base(
        strat_kwargs={'b_pos_size': 0.225, 'a_pos_size': 0.050, 'sl_mult': 1.6},
        lock_offset_r=0.40, trail_atr=0.25),
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
RESULTS_FILE = '/tmp/v60_seeds.json'

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
        baseline = 'v59f_baseline'
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
