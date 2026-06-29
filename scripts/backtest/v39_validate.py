#!/usr/bin/env python3
"""v39 — Validate v37e across 8 seeds + try 3 WR-focused variants"""
import sys, os, json, time
sys.path.insert(0, '/home/z/my-project/scripts')
import v38_push_v37e as v38

# Override CONFIGS to focus on key variants
v38.CONFIGS = {
    'v37e_baseline': v38.make_config(strategies=v38.make_strategies(sl_mult=1.4, tp_mult=1.2), lock_r=0.4, partial_r=0.8, partial_pct=0.3, trail_atr=0.6, atr_floor_pct=0.55),
    'v38g_combo_65wr': v38.make_config(strategies=v38.make_strategies(sl_mult=1.4, tp_mult=1.2), lock_r=0.5, partial_r=0.7, partial_pct=0.4, trail_atr=0.5, atr_floor_pct=0.58),
    'v39a_combo_TP_1.0': v38.make_config(strategies=v38.make_strategies(sl_mult=1.4, tp_mult=1.0), lock_r=0.5, partial_r=0.7, partial_pct=0.4, trail_atr=0.5, atr_floor_pct=0.58),
    'v39b_combo_SL_1.3': v38.make_config(strategies=v38.make_strategies(sl_mult=1.3, tp_mult=1.2), lock_r=0.5, partial_r=0.7, partial_pct=0.4, trail_atr=0.5, atr_floor_pct=0.58),
    'v39c_combo_tighter_time': v38.make_config(strategies=v38.make_strategies(sl_mult=1.4, tp_mult=1.2), lock_r=0.5, partial_r=0.7, partial_pct=0.4, trail_atr=0.5, atr_floor_pct=0.58),
    'v39d_baseline_partial_40': v38.make_config(strategies=v38.make_strategies(sl_mult=1.4, tp_mult=1.2), lock_r=0.4, partial_r=0.8, partial_pct=0.4, trail_atr=0.6, atr_floor_pct=0.55),
    'v39e_baseline_lock_0.5': v38.make_config(strategies=v38.make_strategies(sl_mult=1.4, tp_mult=1.2), lock_r=0.5, partial_r=0.8, partial_pct=0.3, trail_atr=0.6, atr_floor_pct=0.55),
    'v39f_baseline_trail_0.5': v38.make_config(strategies=v38.make_strategies(sl_mult=1.4, tp_mult=1.2), lock_r=0.4, partial_r=0.8, partial_pct=0.3, trail_atr=0.5, atr_floor_pct=0.55),
}

# Override time_stop for v39c (tighter)
v39c_key = 'v39c_combo_tighter_time'
for s in ['A', 'B', 'D']:
    v38.CONFIGS[v39c_key][s]['time_stop'] = 1500  # 37.5 min instead of 60

# Use a fresh results file
v38.results_file = '/tmp/v39_seeds.json' if hasattr(v38, 'results_file') else None

# We need to patch the v38 module's results_file reference
# Actually v38 uses local var results_file in __main__, let me write a runner
RESULTS_FILE = '/tmp/v39_seeds.json'

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'aggregate':
        if not os.path.exists(RESULTS_FILE):
            print(f"No results at {RESULTS_FILE}"); sys.exit(1)
        with open(RESULTS_FILE) as f: all_results = json.load(f)
        seed_results = list(all_results.values())
        seeds = [int(s) for s in all_results.keys()]
        agg = {}
        for name in v38.CONFIGS.keys():
            seed_metrics = [r[name] for r in seed_results if name in r]
            if not seed_metrics: continue
            agg[name] = {
                'wr_mean': sum(m['wr'] for m in seed_metrics) / len(seed_metrics),
                'wr_std': __import__('statistics').stdev(m['wr'] for m in seed_metrics) if len(seed_metrics) > 1 else 0,
                'pnl_mean': sum(m['pnl'] for m in seed_metrics) / len(seed_metrics),
                'pnl_std': __import__('statistics').stdev(m['pnl'] for m in seed_metrics) if len(seed_metrics) > 1 else 0,
                'pf_mean': sum(m['pf'] for m in seed_metrics) / len(seed_metrics),
                'sharpe_mean': sum(m['sharpe'] for m in seed_metrics) / len(seed_metrics),
                'max_consec_loss_mean': sum(m['max_consec_loss'] for m in seed_metrics) / len(seed_metrics),
                'consistency_mean': sum(m['consistency'] for m in seed_metrics) / len(seed_metrics),
                'avg_r_mean': sum(m['avg_r'] for m in seed_metrics) / len(seed_metrics),
                'trades_mean': sum(m['trades'] for m in seed_metrics) / len(seed_metrics),
                'max_dd_mean': sum(m['max_dd'] for m in seed_metrics) / len(seed_metrics),
                'profitable_seeds': sum(1 for m in seed_metrics if m['pnl'] > 0) / len(seed_metrics) * 100,
                'wr_above_60_seeds': sum(1 for m in seed_metrics if m['wr'] >= 60) / len(seed_metrics) * 100,
                'pnl_per_seed': [m['pnl'] for m in seed_metrics],
            }
        import statistics as st
        baseline = 'v37e_baseline'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L:\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*120)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        print("WINNER (target: ≥75% profitable + WR≥62 + MaxDD<0.3)")
        print("=" * 80)
        candidates = [(name, m) for name, m in agg.items()
                      if m['profitable_seeds'] >= 75 and m['wr_above_60_seeds'] >= 50 and m['max_dd_mean'] < 0.3]
        if candidates:
            candidates.sort(key=lambda x: (x[1]['profitable_seeds'], x[1]['pnl_mean'], x[1]['avg_r_mean']), reverse=True)
            w = candidates[0]
            print(f"\n🏆 WINNER: {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f}  Profit {w[1]['profitable_seeds']:.0f}%  AvgR {w[1]['avg_r_mean']:+.3f}  MaxDD {w[1]['max_dd_mean']:.2f}%  PF {w[1]['pf_mean']:.2f}")
        else:
            print("\n  ⚠️ No config met criteria. Top 5:")
            ranked = sorted(agg.items(), key=lambda x: (x[1]['profitable_seeds'], x[1]['pnl_mean']), reverse=True)
            for i, (name, m) in enumerate(ranked[:5]):
                print(f"  #{i+1} {name:<32} P&L {m['pnl_mean']:+.2f}  WR {m['wr_mean']:.1f}%  Profit {m['profitable_seeds']:.0f}%")
    else:
        seed = int(sys.argv[1])
        print(f"Running seed {seed}...", flush=True)
        start = time.time()
        result = v38.run_single_seed(seed)
        elapsed = time.time() - start
        all_results = {}
        if os.path.exists(RESULTS_FILE):
            with open(RESULTS_FILE) as f: all_results = json.load(f)
        for name, m in result.items(): m['per_strat'] = {k: v for k, v in m['per_strat'].items()}
        all_results[str(seed)] = result
        with open(RESULTS_FILE, 'w') as f: json.dump(all_results, f, indent=2)
        print(f"Seed {seed} done in {elapsed:.1f}s. P&L: " + ", ".join(f"{n}={m['pnl']:+.0f}" for n, m in result.items()), flush=True)
