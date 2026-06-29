#!/usr/bin/env python3
"""Run v35 seed-by-seed, save JSON, then aggregate."""
import sys, json, time, os
sys.path.insert(0, '/home/z/my-project/scripts')

SEEDS = [2024, 7, 42, 1337, 99]
RESULTS_FILE = '/tmp/v35_seeds.json'

def run_one_seed(seed):
    import v35_push_v34b as v35
    v35.SEEDS = [seed]
    # Reduce to just 5 most promising configs to speed up
    configs_to_test = [
        'v34b_baseline',
        'v35b_wider_trail_0.8',
        'v35c_larger_partial_50',
        'v35e_wider_TP_1.5',
        'v35f_tighter_SL_1.5',
        'v35g_atr_floor_0.45',
        'v35h_combo',
    ]
    v35.CONFIGS = {k: v35.CONFIGS[k] for k in configs_to_test}
    result = v35.run_single_seed(seed)
    # Convert per_strat dicts for JSON
    for name, m in result.items():
        m['per_strat'] = {k: v for k, v in m['per_strat'].items()}
    return result

if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if seed is not None:
        print(f"Running seed {seed}...")
        start = time.time()
        result = run_one_seed(seed)
        elapsed = time.time() - start
        # Load existing
        all_results = {}
        if os.path.exists(RESULTS_FILE):
            with open(RESULTS_FILE) as f:
                all_results = json.load(f)
        all_results[str(seed)] = result
        with open(RESULTS_FILE, 'w') as f:
            json.dump(all_results, f, indent=2)
        print(f"Seed {seed} done in {elapsed:.1f}s. P&L: " + ", ".join(f"{n}={m['pnl']:+.0f}" for n, m in result.items()))
    else:
        # Aggregate mode
        if not os.path.exists(RESULTS_FILE):
            print(f"No results file at {RESULTS_FILE}")
            sys.exit(1)
        with open(RESULTS_FILE) as f:
            all_results = json.load(f)
        print(f"Loaded {len(all_results)} seeds: {list(all_results.keys())}")
        # Aggregate using v35's function
        import v35_push_v34b as v35
        seed_results = []
        configs_to_test = [
            'v34b_baseline',
            'v35b_wider_trail_0.8',
            'v35c_larger_partial_50',
            'v35e_wider_TP_1.5',
            'v35f_tighter_SL_1.5',
            'v35g_atr_floor_0.45',
            'v35h_combo',
        ]
        v35.CONFIGS = {k: v35.CONFIGS[k] for k in configs_to_test}
        for seed_str, result in all_results.items():
            seed_results.append(result)
        v35.SEEDS = [int(s) for s in all_results.keys()]
        agg = v35.aggregate_results(seed_results)
        v35.print_results(agg)
