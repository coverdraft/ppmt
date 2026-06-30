#!/usr/bin/env python3
"""Analyze v62a robustness results — works with partial data."""
import sys, os, json
sys.path.insert(0, '/home/z/my-project/scripts')
import v63_robustness as v63

print(f"\n{'='*180}")
print(f"  v62a ROBUSTNESS BASELINE — multi-regime analysis")
print(f"{'='*180}")

all_agg = {}
for regime in v63.REGIMES_ALL:
    f = f'/tmp/v63_results/v62a_{regime}.json'
    if not os.path.exists(f):
        print(f"  {regime}: no data")
        continue
    with open(f) as fp: seed_results = json.load(fp)
    n = len(seed_results)
    agg = v63.aggregate_seeds(list(seed_results.values()))
    all_agg[regime] = agg

v63.print_regime_table("v62a (baseline)", all_agg)

# Compute composite
mixed = all_agg.get('MIXED', {})
if mixed:
    comp = v63.composite_for_aggregate(mixed, all_agg)
    regime_stability = sum(1 for r in all_agg.values() if r.get('pnl_mean', 0) > 0) / len(all_agg)
    print(f"\n  COMPOSITE SCORE (MIXED): {comp:.2f}/100")
    print(f"  REGIME STABILITY: {regime_stability*100:.0f}% regimes profitable")
    print(f"  SEED STABILITY: {mixed.get('profitable_seeds_pct', 0):.0f}% seeds profitable (MIXED)")

    # Verdict
    print(f"\n  VERDICT:")
    if mixed['pnl_mean'] > 0 and regime_stability >= 0.6:
        print(f"  ✅ v62a is ROBUST — profitable in {regime_stability*100:.0f}% of regimes")
    elif mixed['pnl_mean'] > 0:
        print(f"  ⚠️  v62a is FRAGILE — only profitable in {regime_stability*100:.0f}% of regimes")
        print(f"     Fails in: " + ", ".join(r for r, a in all_agg.items() if a.get('pnl_mean', 0) <= 0))
    else:
        print(f"  ❌ v62a is BROKEN — not profitable even in MIXED")

# Per-regime detail
print(f"\n\n{'='*180}")
print(f"  PER-REGIME DETAIL — where v62a wins and loses")
print(f"{'='*180}")
for regime in v63.REGIMES_ALL:
    a = all_agg.get(regime, {})
    if not a: continue
    n = a.get('n_seeds', 0)
    pnls = a.get('pnl_per_seed', [])
    print(f"\n  {regime} ({n} seeds):")
    print(f"    P&L:        {a['pnl_mean']:+.2f} ± {a['pnl_std']:.2f}  (range: {min(pnls):+.2f} to {max(pnls):+.2f})")
    print(f"    WR:         {a['wr_mean']:.1f}% ± {a['wr_std']:.1f}%")
    print(f"    MaxDD:      {a['max_dd_mean']:.2f}% ± {a['max_dd_std']:.2f}%  (worst: {a['max_dd_max']:.2f}%)")
    print(f"    PF:         {a['pf_mean']:.2f}")
    print(f"    Sharpe:     {a['sharpe_mean']:+.2f}")
    print(f"    Sortino:    {a['sortino_mean']:+.2f}")
    print(f"    Calmar:     {a['calmar_mean']:.1f}")
    print(f"    Recovery:   {a['recovery_mean']:.1f}")
    print(f"    AvgR:       {a['avg_r_mean']:+.3f}")
    print(f"    Trades:     {a['trades_mean']:.0f} ± {a['trades_std']:.0f}")
    print(f"    Profitable: {a['profitable_seeds_pct']:.0f}% of seeds")
    print(f"    MaxDD ≤0.35%: {a['maxdd_under_35_pct']:.0f}% of seeds")
