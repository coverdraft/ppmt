#!/usr/bin/env python3
"""
v63 runner — parallel execution of 6 regimes × 50 seeds using multiprocessing.
Runs all 6 regimes CONCURRENTLY (one process per regime), each does 50 seeds serially.
Total wall time: ~6 min instead of ~35 min.
"""
import sys, os, json, time, multiprocessing as mp
from functools import partial

sys.path.insert(0, '/home/z/my-project/scripts')


def run_regime_worker(regime_name, seeds, cfg_name='v62a', out_dir='/tmp/v63_results'):
    """Worker process: run all seeds for one regime."""
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f'{cfg_name}_{regime_name}.json')

    # Import inside worker to avoid pickling issues
    import v63_robustness as v63

    # Pick config
    if cfg_name == 'v62a':
        cfg = v63.V62A_CONFIG
    else:
        from v62_push import v61b_base
        cfg = v61b_base()

    # Load existing
    results = {}
    if os.path.exists(out_file):
        try:
            with open(out_file) as f: results = json.load(f)
        except Exception:
            results = {}

    for i, seed in enumerate(seeds):
        if str(seed) in results:
            continue
        t0 = time.time()
        try:
            m = v63.run_one_regime(cfg, regime_name, seed)
            m_dump = {k: v for k, v in m.items() if k not in ('equity_curve', 'trades_list')}
            m_dump = v63._sanitize_json(m_dump)
            results[str(seed)] = m_dump
            # Save every 5 seeds
            if i % 5 == 4:
                with open(out_file, 'w') as f: json.dump(results, f, indent=2, default=v63._json_default)
            elapsed = time.time() - t0
            print(f"[{cfg_name}|{regime_name}|S{seed}] P&L {m['pnl']:+.2f} WR {m['wr']:.1f}% DD {m['max_dd']:.2f}% "
                  f"trades {m['trades']} ({elapsed:.1f}s)", flush=True)
        except Exception as e:
            print(f"[{cfg_name}|{regime_name}|S{seed}] ERROR: {e}", flush=True)
            import traceback; traceback.print_exc()

    # Final save
    with open(out_file, 'w') as f: json.dump(results, f, indent=2, default=v63._json_default)
    return regime_name, len(results)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=50)
    ap.add_argument('--config', default='v62a', choices=['v62a', 'v61b'])
    ap.add_argument('--out-dir', default='/tmp/v63_results')
    ap.add_argument('--regimes', default='ALL', help='comma-separated or ALL')
    ap.add_argument('--workers', type=int, default=6)
    args = ap.parse_args()

    import v63_robustness as v63
    seeds = v63.SEEDS_50[:args.seeds]
    regimes = v63.REGIMES_ALL if args.regimes == 'ALL' else args.regimes.split(',')

    os.makedirs(args.out_dir, exist_ok=True)

    print(f"\n{'='*120}")
    print(f"  v63 PARALLEL RUNNER — {args.config} | {len(regimes)} regimes × {len(seeds)} seeds = {len(regimes)*len(seeds)} runs")
    print(f"  Workers: {args.workers} (one per regime)")
    print(f"{'='*120}\n", flush=True)

    t0 = time.time()
    # Run regimes in parallel
    worker_fn = partial(run_regime_worker, seeds=seeds, cfg_name=args.config, out_dir=args.out_dir)
    with mp.Pool(processes=min(args.workers, len(regimes))) as pool:
        results = pool.map(worker_fn, regimes)

    elapsed = time.time() - t0
    print(f"\n{'='*120}")
    print(f"  All {len(regimes)} regimes completed in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"{'='*120}\n")

    # Aggregate and print
    all_agg = {}
    for regime in regimes:
        out_file = os.path.join(args.out_dir, f'{args.config}_{regime}.json')
        if os.path.exists(out_file):
            with open(out_file) as f: seed_results = json.load(f)
            all_agg[regime] = v63.aggregate_seeds(list(seed_results.values()))

    v63.print_regime_table(f"{args.config} ({len(seeds)} seeds)", all_agg)

    # Save aggregated
    agg_file = os.path.join(args.out_dir, f'{args.config}_aggregated.json')
    # Strip non-serializable
    agg_save = {}
    for r, a in all_agg.items():
        agg_save[r] = v63._sanitize_json(a)
    with open(agg_file, 'w') as f: json.dump(agg_save, f, indent=2, default=v63._json_default)
    print(f"\nAggregated saved to: {agg_file}")

    # Print composite
    mixed = all_agg.get('MIXED', {})
    if mixed:
        comp = v63.composite_for_aggregate(mixed, all_agg)
        print(f"Composite score (MIXED): {comp:.2f}/100")
        regime_stability = sum(1 for r in all_agg.values() if r.get('pnl_mean', 0) > 0) / len(all_agg)
        print(f"Regime stability: {regime_stability*100:.0f}% regimes profitable")

    return all_agg


if __name__ == "__main__":
    mp.set_start_method('spawn', force=True)
    main()
