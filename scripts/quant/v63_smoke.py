#!/usr/bin/env python3
"""Smoke test v63 framework — 2 seeds × 6 regimes, ~3 min."""
import sys, time
sys.path.insert(0, '/home/z/my-project/scripts')
import v63_robustness as v63

t0 = time.time()
agg_by_regime = {}
for regime in v63.REGIMES_ALL:
    print(f"\n--- {regime} ---")
    seed_results = []
    for seed in [42, 1337]:
        t1 = time.time()
        m = v63.run_one_regime(v63.V62A_CONFIG, regime, seed)
        seed_results.append(m)
        print(f"  S{seed}: P&L {m['pnl']:+.2f}, WR {m['wr']:.1f}%, DD {m['max_dd']:.2f}%, "
              f"PF {m['pf']:.2f}, Sharpe {m['sharpe']:+.2f}, Sortino {m['sortino']:+.2f}, "
              f"trades {m['trades']}, avgR {m['avg_r']:+.2f}  ({time.time()-t1:.1f}s)")
    agg = v63.aggregate_seeds(seed_results)
    agg_by_regime[regime] = agg

print(f"\nTotal time: {time.time()-t0:.1f}s")
v63.print_regime_table("v62a (smoke test 2 seeds)", agg_by_regime)

# Test composite
mixed = agg_by_regime.get('MIXED', {})
comp = v63.composite_for_aggregate(mixed, agg_by_regime)
print(f"\nComposite (MIXED, 2 seeds): {comp:.2f}/100")
print(f"Per-regime profitable: " + ", ".join(
    f"{r}={'Y' if a['pnl_mean']>0 else 'N'}" for r,a in agg_by_regime.items()
))
