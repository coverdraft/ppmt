#!/usr/bin/env python3
"""
v66 — FINE TUNE around v65 sweet spot.

v65 12-seed results showed:
  - A=0.050: P&L +48.56, MaxDD 0.29%, PF 2.72, Sharpe 9.82  (CHAMPION v62a)
  - A=0.040: P&L +42.45, MaxDD 0.25%, PF 2.85, Sharpe 10.02  (close, better robustness)
  - A=0.030: P&L +36.36, MaxDD 0.21%, PF 3.07, Sharpe 10.31  (sweet spot?)
  - A=0.020: P&L +30.27, MaxDD 0.18%, PF 3.49, Sharpe 11.11  (more robust, less P&L)

Goal: find config that beats v62a on MULTIPLE metrics simultaneously.
  - Match v62a P&L (>+45) AND improve MaxDD (<0.29) AND improve PF (>2.72)

Strategy: keep A around 0.035-0.045 (where P&L is close to v62a) and push B
size up slightly to compensate. Also test if pyramiding harder on B recovers P&L.
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v62_push import EngineSimV62, v61b_base


class EngineSimV66(EngineSimV62):
    pass


def v66_base(a_size=0.050, b_size=0.20, pyramid_pct=0.75, **overrides):
    cfg = v61b_base(
        pyramid_pct=pyramid_pct,
        strat_kwargs={'a_pos_size': a_size, 'b_pos_size': b_size},
    )
    cfg.update(overrides)
    return cfg


CONFIGS = {
    # Control
    'v62a_control':             v66_base(a_size=0.050, b_size=0.20, pyramid_pct=0.75),

    # Sweet spot search — A around 0.040-0.045, B slightly bigger
    'v66_a_045_b_020':          v66_base(a_size=0.045, b_size=0.20, pyramid_pct=0.75),
    'v66_a_040_b_020':          v66_base(a_size=0.040, b_size=0.20, pyramid_pct=0.75),
    'v66_a_045_b_022':          v66_base(a_size=0.045, b_size=0.22, pyramid_pct=0.75),
    'v66_a_040_b_022':          v66_base(a_size=0.040, b_size=0.22, pyramid_pct=0.75),
    'v66_a_045_b_024':          v66_base(a_size=0.045, b_size=0.24, pyramid_pct=0.75),
    'v66_a_040_b_024':          v66_base(a_size=0.040, b_size=0.24, pyramid_pct=0.75),

    # Pyramid harder to compensate A reduction
    'v66_a_040_pyr100':         v66_base(a_size=0.040, b_size=0.20, pyramid_pct=1.00),
    'v66_a_045_pyr100':         v66_base(a_size=0.045, b_size=0.20, pyramid_pct=1.00),
    'v66_a_045_b_022_pyr100':   v66_base(a_size=0.045, b_size=0.22, pyramid_pct=1.00),

    # Pyramid with multi-level (2nd at +2.0R)
    'v66_a_045_b_022_mp':       v66_base(a_size=0.045, b_size=0.22, pyramid_pct=0.75,
                                          pyramid2_trigger_r=2.0, pyramid2_pct=0.30),

    # Tighter lock to protect pyramided gains
    'v66_a_045_b_022_lock04':   v66_base(a_size=0.045, b_size=0.22, pyramid_pct=0.75,
                                          lock_offset_r=0.40),
}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV66(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
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
RESULTS_FILE = '/tmp/v66_seeds.json'

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
                'pnl_per_seed': [m['pnl'] for m in seed_metrics],
            }
        baseline = 'v62a_control'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < agg[baseline]['pnl_mean'] else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L ({len(seeds)} seeds):\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*160)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        baseline_pnl = agg[baseline]['pnl_mean']
        baseline_dd = agg[baseline]['max_dd_mean']
        baseline_pf = agg[baseline]['pf_mean']
        baseline_profit = agg[baseline]['profitable_seeds']
        # Strict winner: must match P&L (within 5%) AND improve MaxDD AND improve PF
        candidates_strict = [(name, m) for name, m in agg.items()
                             if name != baseline
                             and m['pnl_mean'] >= baseline_pnl * 0.95
                             and m['max_dd_mean'] < baseline_dd
                             and m['pf_mean'] > baseline_pf
                             and m['profitable_seeds'] >= baseline_profit]
        # Loose winner: any improvement in P&L with MaxDD not worse
        candidates_loose = [(name, m) for name, m in agg.items()
                            if name != baseline
                            and m['pnl_mean'] > baseline_pnl
                            and m['max_dd_mean'] <= baseline_dd + 0.02]
        if candidates_strict:
            candidates_strict.sort(key=lambda x: (-x[1]['pnl_mean'], x[1]['max_dd_mean']))
            w = candidates_strict[0]
            print(f"\n🏆 STRICT WINNER (P&L≥95% baseline + MaxDD↓ + PF↑ + Profit≥baseline): {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f} (vs {baseline_pnl:+.2f})  Profit {w[1]['profitable_seeds']:.0f}%  MaxDD {w[1]['max_dd_mean']:.2f}% (vs {baseline_dd:.2f}%)  PF {w[1]['pf_mean']:.2f} (vs {baseline_pf:.2f})  Sharpe {w[1]['sharpe_mean']:+.2f}")
        elif candidates_loose:
            candidates_loose.sort(key=lambda x: (-x[1]['pnl_mean'], x[1]['max_dd_mean']))
            w = candidates_loose[0]
            print(f"\n🥈 LOOSE WINNER (P&L↑ + MaxDD≤baseline+0.02): {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f} (vs {baseline_pnl:+.2f})  Profit {w[1]['profitable_seeds']:.0f}%  MaxDD {w[1]['max_dd_mean']:.2f}% (vs {baseline_dd:.2f}%)  PF {w[1]['pf_mean']:.2f}  Sharpe {w[1]['sharpe_mean']:+.2f}")
        else:
            print("\n  ⚠️ No winner. Top 5 by composite (P&L + Sharpe + MaxDD inverted):")
            ranked = sorted(agg.items(),
                            key=lambda x: x[1]['pnl_mean'] + x[1]['sharpe_mean']*3 - x[1]['max_dd_mean']*50,
                            reverse=True)
            for i, (name, m) in enumerate(ranked[:5]):
                vs_pnl = m['pnl_mean'] - baseline_pnl
                vs_dd = m['max_dd_mean'] - baseline_dd
                vs_pf = m['pf_mean'] - baseline_pf
                print(f"  #{i+1} {name:<32} P&L {m['pnl_mean']:+.2f} ({vs_pnl:+.2f})  MaxDD {m['max_dd_mean']:.2f}% ({vs_dd:+.2f})  PF {m['pf_mean']:.2f} ({vs_pf:+.2f})  Sharpe {m['sharpe_mean']:+.2f}")
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
