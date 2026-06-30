#!/usr/bin/env python3
"""Smoke test v65 strategies — 8 strategies × MIXED × 2 seeds."""
import sys, time
sys.path.insert(0, '/home/z/my-project/scripts')
import v65_strategies as v65

t0 = time.time()
results = {}
for sname in v65.STRATEGIES:
    print(f"\n→ {sname} | MIXED | 2 seeds...")
    t1 = time.time()
    per_seed = []
    for seed in [42, 1337]:
        m = v65.run_solo_strategy(sname, 'MIXED', seed)
        per_seed.append(m)
        print(f"  S{seed}: P&L {m['pnl']:+.2f}, WR {m['wr']:.1f}%, DD {m['max_dd']:.2f}%, trades {m['trades']}, avgR {m['avg_r']:+.2f}")
    import v63_robustness as v63
    agg = v63.aggregate_seeds(per_seed)
    results[sname] = agg
    print(f"  → mean: P&L {agg['pnl_mean']:+.2f}, WR {agg['wr_mean']:.1f}%, profitable {agg['profitable_seeds_pct']:.0f}%  ({time.time()-t1:.1f}s)")

print(f"\nTotal: {time.time()-t0:.1f}s")
print(f"\n{'='*120}")
print(f"  SMOKE TEST — 8 new strategies on MIXED regime (2 seeds)")
print(f"{'='*120}")
print(f"{'Strategy':<15} {'Trades':<8} {'WR%':<10} {'P&L':<12} {'MaxDD%':<8} {'PF':<7} {'Verdict':<15}")
for sname, a in results.items():
    verdict = '✅' if a['pnl_mean'] > 0 and a['max_dd_mean'] <= 0.40 else ('⚠️' if a['pnl_mean'] > 0 else '❌')
    print(f"{sname:<15} {a['trades_mean']:<8.0f} {a['wr_mean']:<10.1f} {a['pnl_mean']:+.2f}{'':>5} {a['max_dd_mean']:<8.2f} {a['pf_mean']:<7.2f} {verdict}")
