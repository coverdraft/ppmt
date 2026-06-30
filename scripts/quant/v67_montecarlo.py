#!/usr/bin/env python3
"""
v67 — MONTE CARLO SIMULATION (PRIORITY #6)

User directive:
  "Ejecuta simulaciones Monte Carlo. Quiero conocer:
   - peor escenario esperado
   - drawdown esperado
   - distribución del beneficio
   - probabilidad de pérdida mensual
   - probabilidad de pérdida anual"

WHAT THIS DOES:
  1. Takes the trade list from v62a (and any candidate) on MIXED regime.
  2. Bootstrap resampling: randomly sample trades with replacement to create
     10,000 alternative trade sequences of the same length.
  3. For each resampled sequence, compute:
     - Final P&L
     - Max Drawdown
     - Monthly P&L (assuming ~360 trades/month at 12 trades/day × 30 days)
     - Yearly P&L (extrapolated)
  4. Output distribution statistics:
     - 5th, 25th, 50th, 75th, 95th percentile of P&L
     - 5th percentile of MaxDD (worst-case)
     - P(monthly loss) = % of bootstrap samples where monthly P&L < 0
     - P(yearly loss) = % of bootstrap samples where yearly P&L < 0

  This tells us the PROBABILITY of losing money over a month / year, which
  is the true measure of strategy risk (not just average P&L).
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy
from collections import defaultdict
from typing import List, Dict

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
import v63_robustness as v63
from v62_push import v61b_base, EngineSimV62


# ════════════════════════════════════════════════════════════════════
#  COLLECT TRADE LIST FROM v62a (or any candidate)
# ════════════════════════════════════════════════════════════════════

def collect_trades(cfg, regime: str, n_seeds: int = 20) -> List[Dict]:
    """Run the engine on multiple seeds and collect ALL trades into one pool."""
    all_trades = []
    seeds = v63.SEEDS_50[:n_seeds]
    for seed in seeds:
        rng = random.Random(seed)
        all_prices = {
            f"TOK{i:02d}": v63.gen_regime_prices(
                v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)),
                regime, rng
            )
            for i in range(v40.N_TOKENS)
        }
        engine = EngineSimV62(deepcopy(cfg), f"mc_{regime}_S{seed}")
        for tick in range(v40.TOTAL_TICKS):
            for sym in all_prices:
                if tick + 1 < 60: continue
                prices_slice = all_prices[sym][max(0, tick-250):tick+1]
                engine._try_strategy_a(sym, prices_slice, tick)
                engine._try_strategy_b(sym, prices_slice, tick)
                engine._try_strategy_d(sym, prices_slice, tick)
                engine._try_strategy_e(sym, prices_slice, tick)
                engine._check_stops(sym, prices_slice, tick)
            engine.update_equity(all_prices, tick)
        for t in engine.trades:
            all_trades.append({
                'pnl': t.pnl,
                'r_multiple': t.r_multiple,
                'hold_ticks': t.hold_ticks,
                'close_reason': t.close_reason,
                'strategy': t.strategy,
                'seed': seed,
            })
    return all_trades


# ════════════════════════════════════════════════════════════════════
#  BOOTSTRAP MONTE CARLO
# ════════════════════════════════════════════════════════════════════

def bootstrap_monte_carlo(trades: List[Dict], n_simulations: int = 10000,
                          trades_per_month: int = 360, trades_per_year: int = 4320,
                          rng: random.Random = None) -> Dict:
    """Bootstrap resample the trade list n_simulations times.

    For each simulation:
      1. Sample `trades_per_year` trades with replacement (1 year of trading)
      2. Compute final P&L
      3. Compute Max Drawdown (running peak-to-trough on cumulative P&L)
      4. Compute monthly P&L (sum of first trades_per_month trades)
      5. Compute yearly P&L (sum of all trades_per_year trades)

    Returns distribution statistics.
    """
    if rng is None: rng = random.Random(42)
    if not trades:
        return {'error': 'no trades'}

    pnls = [t['pnl'] for t in trades]
    n_pool = len(pnls)

    final_pnls = []
    max_dds = []
    monthly_pnls = []
    yearly_pnls = []

    for sim in range(n_simulations):
        # Sample trades_per_year trades with replacement
        sample = [pnls[rng.randint(0, n_pool - 1)] for _ in range(trades_per_year)]

        # Cumulative P&L
        cum = [0]
        for p in sample:
            cum.append(cum[-1] + p)
        final_pnl = cum[-1]
        final_pnls.append(final_pnl)

        # Max Drawdown
        peak = cum[0]
        max_dd = 0
        for c in cum:
            if c > peak: peak = c
            dd = peak - c
            if dd > max_dd: max_dd = dd
        max_dds.append(max_dd)

        # Monthly P&L (first month)
        monthly_pnl = sum(sample[:trades_per_month])
        monthly_pnls.append(monthly_pnl)

        # Yearly P&L
        yearly_pnls.append(final_pnl)

    # Compute distribution statistics
    def percentile(sorted_list, p):
        if not sorted_list: return 0
        idx = int(len(sorted_list) * p / 100)
        idx = min(idx, len(sorted_list) - 1)
        return sorted_list[idx]

    final_sorted = sorted(final_pnls)
    dd_sorted = sorted(max_dds)
    monthly_sorted = sorted(monthly_pnls)

    return {
        'n_simulations': n_simulations,
        'n_pool_trades': n_pool,
        'final_pnl': {
            'mean': statistics.mean(final_pnls),
            'std': statistics.stdev(final_pnls),
            'p5': percentile(final_sorted, 5),
            'p25': percentile(final_sorted, 25),
            'p50': percentile(final_sorted, 50),
            'p75': percentile(final_sorted, 75),
            'p95': percentile(final_sorted, 95),
            'min': min(final_pnls),
            'max': max(final_pnls),
        },
        'max_dd': {
            'mean': statistics.mean(max_dds),
            'std': statistics.stdev(max_dds),
            'p5': percentile(dd_sorted, 5),    # best-case (low DD)
            'p50': percentile(dd_sorted, 50),
            'p95': percentile(dd_sorted, 95),  # worst-case (high DD)
            'max': max(max_dds),
        },
        'monthly_pnl': {
            'mean': statistics.mean(monthly_pnls),
            'p5': percentile(monthly_sorted, 5),
            'p50': percentile(monthly_sorted, 50),
            'p95': percentile(monthly_sorted, 95),
            'p_loss': sum(1 for p in monthly_pnls if p < 0) / len(monthly_pnls) * 100,
        },
        'yearly_pnl': {
            'mean': statistics.mean(yearly_pnls),
            'p5': percentile(sorted(yearly_pnls), 5),
            'p50': percentile(sorted(yearly_pnls), 50),
            'p95': percentile(sorted(yearly_pnls), 95),
            'p_loss': sum(1 for p in yearly_pnls if p < 0) / len(yearly_pnls) * 100,
        },
    }


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=20, help='Seeds to collect trades from')
    ap.add_argument('--regime', default='MIXED', choices=v63.REGIMES_ALL)
    ap.add_argument('--sims', type=int, default=10000, help='Monte Carlo simulations')
    ap.add_argument('--out', default='/tmp/v67_montecarlo.json')
    ap.add_argument('--config', default='v62a', choices=['v62a', 'v61b'])
    args = ap.parse_args()

    print(f"\n{'='*120}")
    print(f"  v67 MONTE CARLO — {args.config} | {args.regime} | {args.seeds} seeds → {args.sims} bootstrap simulations")
    print(f"{'='*120}\n")

    # Config
    if args.config == 'v62a':
        cfg = v61b_base(pyramid_pct=0.75)
    else:
        cfg = v61b_base()

    # Step 1: Collect trades
    print(f"Step 1: Collecting trades from {args.seeds} seeds on {args.regime}...")
    t0 = time.time()
    trades = collect_trades(cfg, args.regime, n_seeds=args.seeds)
    print(f"  Collected {len(trades)} trades in {time.time()-t0:.1f}s")
    if trades:
        pnls = [t['pnl'] for t in trades]
        print(f"  Trade P&L: mean {statistics.mean(pnls):+.2f}, std {statistics.stdev(pnls):.2f}")
        print(f"  Win rate: {sum(1 for p in pnls if p > 0)/len(pnls)*100:.1f}%")
        print(f"  Total P&L: {sum(pnls):+.2f}")

    # Step 2: Bootstrap Monte Carlo
    print(f"\nStep 2: Running {args.sims} bootstrap simulations...")
    t0 = time.time()
    # Calibration: 4h session produces ~50 trades, so 1 day (24h) = 300 trades
    # 1 month (30 days) = 9000 trades, 1 year (365 days) = 109500 trades
    # But we use a more conservative estimate: 12 trades/day = 360/month = 4320/year
    results = bootstrap_monte_carlo(trades, n_simulations=args.sims,
                                     trades_per_month=360, trades_per_year=4320)
    print(f"  Done in {time.time()-t0:.1f}s")

    # Step 3: Print results
    print(f"\n{'='*120}")
    print(f"  MONTE CARLO RESULTS — {args.config} on {args.regime} ({args.sims} simulations)")
    print(f"{'='*120}")

    print(f"\n  FINAL P&L DISTRIBUTION (1 year of trading):")
    print(f"    Mean:   {results['final_pnl']['mean']:+.2f}")
    print(f"    Std:    {results['final_pnl']['std']:.2f}")
    print(f"    Min:    {results['final_pnl']['min']:+.2f}")
    print(f"    P5:     {results['final_pnl']['p5']:+.2f}  (worst-case 5%)")
    print(f"    P25:    {results['final_pnl']['p25']:+.2f}")
    print(f"    P50:    {results['final_pnl']['p50']:+.2f}  (median)")
    print(f"    P75:    {results['final_pnl']['p75']:+.2f}")
    print(f"    P95:    {results['final_pnl']['p95']:+.2f}  (best-case 5%)")
    print(f"    Max:    {results['final_pnl']['max']:+.2f}")

    print(f"\n  MAX DRAWDOWN DISTRIBUTION:")
    print(f"    Mean:   {results['max_dd']['mean']:.2f}")
    print(f"    P5:     {results['max_dd']['p5']:.2f}  (best-case 5%)")
    print(f"    P50:    {results['max_dd']['p50']:.2f}  (median)")
    print(f"    P95:    {results['max_dd']['p95']:.2f}  (worst-case 5%)")
    print(f"    Max:    {results['max_dd']['max']:.2f}  (worst ever)")

    print(f"\n  MONTHLY P&L:")
    print(f"    Mean:        {results['monthly_pnl']['mean']:+.2f}")
    print(f"    P5:          {results['monthly_pnl']['p5']:+.2f}")
    print(f"    P50:         {results['monthly_pnl']['p50']:+.2f}")
    print(f"    P95:         {results['monthly_pnl']['p95']:+.2f}")
    print(f"    P(monthly loss): {results['monthly_pnl']['p_loss']:.1f}%")

    print(f"\n  YEARLY P&L:")
    print(f"    Mean:        {results['yearly_pnl']['mean']:+.2f}")
    print(f"    P5:          {results['yearly_pnl']['p5']:+.2f}")
    print(f"    P50:         {results['yearly_pnl']['p50']:+.2f}")
    print(f"    P95:         {results['yearly_pnl']['p95']:+.2f}")
    print(f"    P(yearly loss):  {results['yearly_pnl']['p_loss']:.1f}%")

    # Verdict
    print(f"\n{'='*120}")
    print(f"  VERDICT")
    print(f"{'='*120}")
    p_monthly_loss = results['monthly_pnl']['p_loss']
    p_yearly_loss = results['yearly_pnl']['p_loss']
    worst_dd = results['max_dd']['p95']
    if p_yearly_loss < 5 and worst_dd < 5:
        print(f"  ✅ ROBUST — P(yearly loss) = {p_yearly_loss:.1f}%, worst-case DD = {worst_dd:.2f}")
    elif p_yearly_loss < 25:
        print(f"  ⚠️  ACCEPTABLE — P(yearly loss) = {p_yearly_loss:.1f}%, worst-case DD = {worst_dd:.2f}")
    else:
        print(f"  ❌ FRAGILE — P(yearly loss) = {p_yearly_loss:.1f}%, worst-case DD = {worst_dd:.2f}")

    # Save
    save = {'config': args.config, 'regime': args.regime, **results}
    with open(args.out, 'w') as f: json.dump(save, f, indent=2)
    print(f"\nSaved to {args.out}")

    return results


if __name__ == "__main__":
    main()
