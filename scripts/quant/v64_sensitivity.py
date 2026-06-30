#!/usr/bin/env python3
"""
v64 — SENSITIVITY MAPS (PRIORITY #2)

User directive:
  "Haz pruebas modificando cada parámetro ±10-20%.
   Si pequeños cambios destruyen la estrategia, el sistema es frágil.
   Quiero mapas de sensibilidad, no únicamente el mejor punto."

WHAT THIS DOES:
  For each parameter in v62a, sweep ±10%, ±20% (and sometimes more)
  while keeping all others fixed. Run on MIXED regime with 12 seeds
  (12 seeds is enough for sensitivity — 50 seeds is for final validation).

  Output: sensitivity table per parameter showing:
    - P&L mean ± std
    - WR mean
    - MaxDD mean
    - Composite score
    - DELTA vs baseline (how much P&L changes per % parameter change)

  Parameters swept:
    1. ATR floor (0.0058 base)  — 0.0040 to 0.0080
    2. SL mult   (1.5 base)      — 1.2 to 1.8
    3. TP mult   (1.2 base)      — 0.9 to 1.5  (TP is rarely hit, partial-based)
    4. Trail ATR (0.30 base)     — 0.20 to 0.40
    5. A pos size (0.050 base)   — 0.030 to 0.070
    6. B pos size (0.20 base)    — 0.15 to 0.25
    7. Pyramid pct (0.75 base)   — 0.50 to 1.00
    8. Pyramid trigger R (1.0)   — 0.7 to 1.3
    9. Lock offset R (0.20)      — 0.10 to 0.30
   10. Cooldown min (45)         — 25 to 65

  KEY OUTPUT:
    - FRAGILITY INDEX = sum of |ΔP&L| / |Δparam| across all params
      (high = fragile, low = robust)
    - WORST PARAMETER = the one that breaks the strategy fastest
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy
from collections import defaultdict

sys.path.insert(0, '/home/z/my-project/scripts')
import v63_robustness as v63
from v62_push import v61b_base, EngineSimV62


# ════════════════════════════════════════════════════════════════════
#  PARAMETER SWEEP DEFINITIONS
# ════════════════════════════════════════════════════════════════════

def v62a_baseline():
    """v62a config — exact production baseline."""
    return v61b_base(pyramid_pct=0.75)


# Each parameter: (label, getter, setter, sweep_values)
# sweep_values is list of (value, label) — baseline MUST be in the list
SWEEPS = [
    {
        'name': 'atr_floor_pct',
        'desc': 'ATR floor (minimum ATR as % of price)',
        'baseline': 0.58,
        'values': [(0.40, '-31%'), (0.50, '-14%'), (0.58, 'BASE'), (0.66, '+14%'), (0.80, '+38%')],
        'setter': lambda cfg, v: cfg.update({'atr_floor_pct': v / 100}),  # cfg expects fractional
    },
    {
        'name': 'sl_mult',
        'desc': 'Stop-loss multiplier (× ATR)',
        'baseline': 1.5,
        'values': [(1.2, '-20%'), (1.35, '-10%'), (1.5, 'BASE'), (1.65, '+10%'), (1.8, '+20%')],
        'setter': lambda cfg, v: _set_strat(cfg, 'sl_mult', v),
    },
    {
        'name': 'tp_mult',
        'desc': 'Take-profit multiplier (× ATR) — rarely hit due to partials',
        'baseline': 1.2,
        'values': [(0.9, '-25%'), (1.05, '-12%'), (1.2, 'BASE'), (1.35, '+12%'), (1.5, '+25%')],
        'setter': lambda cfg, v: _set_strat(cfg, 'tp_mult', v),
    },
    {
        'name': 'trail_atr',
        'desc': 'Trailing stop distance (× ATR)',
        'baseline': 0.30,
        'values': [(0.20, '-33%'), (0.25, '-17%'), (0.30, 'BASE'), (0.35, '+17%'), (0.40, '+33%')],
        'setter': lambda cfg, v: cfg.update({'trail_atr': v}),
    },
    {
        'name': 'a_pos_size',
        'desc': 'Strategy A position size (fraction of capital)',
        'baseline': 0.050,
        'values': [(0.030, '-40%'), (0.040, '-20%'), (0.050, 'BASE'), (0.060, '+20%'), (0.070, '+40%')],
        'setter': lambda cfg, v: _set_strat(cfg, 'pos_size_pct', v, strategy='A'),
    },
    {
        'name': 'b_pos_size',
        'desc': 'Strategy B position size (fraction of capital)',
        'baseline': 0.20,
        'values': [(0.15, '-25%'), (0.175, '-12%'), (0.20, 'BASE'), (0.225, '+12%'), (0.25, '+25%')],
        'setter': lambda cfg, v: _set_strat(cfg, 'pos_size_pct', v, strategy='B'),
    },
    {
        'name': 'pyramid_pct',
        'desc': 'Pyramid size addition (% of original)',
        'baseline': 0.75,
        'values': [(0.50, '-33%'), (0.625, '-17%'), (0.75, 'BASE'), (0.875, '+17%'), (1.00, '+33%')],
        'setter': lambda cfg, v: cfg.update({'pyramid_pct': v}),
    },
    {
        'name': 'pyramid_trigger_r',
        'desc': 'R-multiple at which pyramid triggers',
        'baseline': 1.0,
        'values': [(0.7, '-30%'), (0.85, '-15%'), (1.0, 'BASE'), (1.15, '+15%'), (1.3, '+30%')],
        'setter': lambda cfg, v: cfg.update({'pyramid_trigger_r': v}),
    },
    {
        'name': 'lock_offset_r',
        'desc': 'Lock profit offset (R above entry)',
        'baseline': 0.20,
        'values': [(0.10, '-50%'), (0.15, '-25%'), (0.20, 'BASE'), (0.25, '+25%'), (0.30, '+50%')],
        'setter': lambda cfg, v: cfg.update({'lock_offset_r': v}),
    },
    {
        'name': 'cooldown_min',
        'desc': 'Cooldown between trades (minutes)',
        'baseline': 45,
        'values': [(25, '-44%'), (35, '-22%'), (45, 'BASE'), (55, '+22%'), (65, '+44%')],
        'setter': lambda cfg, v: _set_strat(cfg, 'cooldown_min', v),
    },
]


def _set_strat(cfg, key, value, strategy=None):
    """Helper: set a key in cfg['A'] and/or cfg['B'] (etc.)."""
    if strategy:
        cfg[strategy][key] = value
    else:
        # Apply to all strategies that have this key
        for s in ['A', 'B', 'D']:
            if s in cfg and key in cfg[s]:
                cfg[s][key] = value


# ════════════════════════════════════════════════════════════════════
#  SENSITIVITY RUNNER
# ════════════════════════════════════════════════════════════════════

SEEDS_SENSITIVITY = v63.SEEDS_50[:12]  # 12 seeds is enough for sensitivity


def run_sensitivity_one_param(param_def: Dict, seeds: List[int] = None) -> List[Dict]:
    """Sweep one parameter across its values. Returns list of result dicts."""
    if seeds is None:
        seeds = SEEDS_SENSITIVITY

    results = []
    for value, label in param_def['values']:
        cfg = v62a_baseline()
        param_def['setter'](cfg, value)
        per_seed = []
        for seed in seeds:
            m = v63.run_one_regime(cfg, 'MIXED', seed)
            per_seed.append(m)
        agg = v63.aggregate_seeds(per_seed)
        agg['param_name'] = param_def['name']
        agg['param_desc'] = param_def['desc']
        agg['param_value'] = value
        agg['param_label'] = label
        agg['composite'] = v63.composite_for_aggregate(agg, {'MIXED': agg})
        results.append(agg)
        print(f"  {param_def['name']:<22} {label:<8} val={value:>6} → P&L {agg['pnl_mean']:+.2f}±{agg['pnl_std']:.0f}, "
              f"WR {agg['wr_mean']:.1f}%, DD {agg['max_dd_mean']:.2f}%, comp {agg['composite']:.1f}", flush=True)
    return results


def compute_fragility(all_results: Dict[str, List[Dict]]) -> Dict:
    """Compute fragility index per parameter.

    Fragility = (max P&L - min P&L) / |baseline P&L| across the sweep range.
    Higher = more fragile.
    """
    fragility = {}
    for param_name, results in all_results.items():
        pnls = [r['pnl_mean'] for r in results]
        baseline_pnl = next((r['pnl_mean'] for r in results if r['param_label'] == 'BASE'), pnls[0])
        pnl_range = max(pnls) - min(pnls)
        fragility[param_name] = {
            'pnl_range': pnl_range,
            'pnl_min': min(pnls),
            'pnl_max': max(pnls),
            'pnl_baseline': baseline_pnl,
            'fragility_index': pnl_range / abs(baseline_pnl) if abs(baseline_pnl) > 1e-9 else float('inf'),
            'composite_range': max(r['composite'] for r in results) - min(r['composite'] for r in results),
        }
    return fragility


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=12)
    ap.add_argument('--out', default='/tmp/v64_sensitivity.json')
    ap.add_argument('--only', help='Only run one parameter (name)')
    args = ap.parse_args()

    seeds = v63.SEEDS_50[:args.seeds]
    sweeps = SWEEPS if not args.only else [s for s in SWEEPS if s['name'] == args.only]

    all_results = {}
    print(f"\n{'='*120}")
    print(f"  v64 SENSITIVITY ANALYSIS — v62a baseline, {len(seeds)} seeds, MIXED regime")
    print(f"{'='*120}\n")

    for sweep in sweeps:
        print(f"\n→ Sweeping {sweep['name']} — {sweep['desc']}")
        print(f"  Baseline: {sweep['baseline']}, values: {[v for v,_ in sweep['values']]}")
        t0 = time.time()
        results = run_sensitivity_one_param(sweep, seeds)
        all_results[sweep['name']] = results
        print(f"  ({time.time()-t0:.1f}s)")

    # Save raw results
    save = {name: [{k: v for k, v in r.items() if k != 'pnl_per_seed'} for r in results]
            for name, results in all_results.items()}
    with open(args.out, 'w') as f: json.dump(save, f, indent=2)

    # Print summary
    print(f"\n\n{'='*150}")
    print(f"  SENSITIVITY SUMMARY — v62a baseline fragility map")
    print(f"{'='*150}")
    print(f"{'Parameter':<22} {'Description':<48} {'Base P&L':<10} {'Min P&L':<10} {'Max P&L':<10} {'Range':<10} {'Fragility':<12} {'Comp Range':<12}")
    print('-' * 150)
    fragility = compute_fragility(all_results)
    for param_name in [s['name'] for s in sweeps]:
        f = fragility[param_name]
        desc = next(s['desc'] for s in sweeps if s['name'] == param_name)
        print(f"{param_name:<22} {desc[:46]:<48} {f['pnl_baseline']:+.2f}{'':>3} {f['pnl_min']:+.2f}{'':>3} "
              f"{f['pnl_max']:+.2f}{'':>3} {f['pnl_range']:.2f}{'':>5} {f['fragility_index']:.2f}x{'':>6} {f['composite_range']:.2f}")

    # Rank by fragility
    print(f"\n  FRAGILITY RANKING (most fragile → least):")
    ranked = sorted(fragility.items(), key=lambda x: x[1]['fragility_index'], reverse=True)
    for i, (name, f) in enumerate(ranked):
        print(f"  #{i+1}  {name:<22}  fragility {f['fragility_index']:.2f}x  (P&L range {f['pnl_min']:+.2f} to {f['pnl_max']:+.2f})")

    return all_results, fragility


if __name__ == "__main__":
    main()
