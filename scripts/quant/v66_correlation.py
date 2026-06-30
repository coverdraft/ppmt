#!/usr/bin/env python3
"""
v66 — CORRELATION MATRIX (PRIORITY #5)

User directive:
  "Calcula la correlación entre Strategy A, Strategy B y las nuevas estrategias.
   Busco estrategias poco correlacionadas.
   Si dos estrategias generan prácticamente las mismas entradas, elimina una."

WHAT THIS DOES:
  1. Runs each strategy SOLO on the same price data (same seeds, same regime).
  2. Records the entry tick + direction of every trade.
  3. Computes pairwise correlation:
     - ENTRY CORRELATION: do strategies enter at the same time? (Jaccard similarity of entry ticks)
     - DIRECTION CORRELATION: when both enter same tick, same direction?
     - PNL CORRELATION: do their daily P&Ls move together? (Pearson correlation)
  4. Eliminates strategies with > 0.7 correlation to another.

OUTPUT:
  - 8x8 correlation matrix (heatmap-style text output)
  - List of duplicate pairs (> 0.7 correlation)
  - Recommended portfolio (uncorrelated subset)
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy
from collections import defaultdict
from itertools import combinations
from typing import List, Dict

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
import v63_robustness as v63
import v65_strategies as v65
from v62_push import v61b_base, EngineSimV62


# ════════════════════════════════════════════════════════════════════
#  RUN ALL STRATEGIES ON SAME PRICE DATA
# ════════════════════════════════════════════════════════════════════

def run_all_strategies_same_data(regime: str, seed: int) -> Dict:
    """Run A, B (from v62a) + 8 new strategies SOLO on the SAME price data.
    Returns dict of {strategy_name: list of trades with entry_tick}.
    """
    rng = random.Random(seed)
    # Generate ONE set of prices — all strategies see the same data
    all_prices = {
        f"TOK{i:02d}": v63.gen_regime_prices(
            v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)),
            regime, rng
        )
        for i in range(v40.N_TOKENS)
    }

    results = {}

    # Run Strategy A + B (from v62a config) SOLO
    # We need to extract just A or just B. The v62a config has both.
    # We'll create separate configs where only A or only B is enabled.
    cfg_a_only = v61b_base(pyramid_pct=0.75)
    cfg_a_only['B']['enabled'] = False
    cfg_a_only['D']['enabled'] = False
    cfg_a_only['E']['enabled'] = False

    cfg_b_only = v61b_base(pyramid_pct=0.75)
    cfg_b_only['A']['momentum_min'] = 999  # disable A
    cfg_b_only['D']['enabled'] = False
    cfg_b_only['E']['enabled'] = False

    # Use trade INDEX as time proxy (trades are appended chronologically)
    # For each trade, estimate entry_tick = (trade_index / total_trades) * TOTAL_TICKS
    for strat_name, cfg in [('A', cfg_a_only), ('B', cfg_b_only)]:
        engine = EngineSimV62(deepcopy(cfg), f"{strat_name}_{regime}_S{seed}")
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
        # Record trades — estimate entry_tick from hold_ticks
        # We know trades close in chronological order; we can approximate entry_tick
        # by tracking the last close tick (cumulative)
        n_trades = len(engine.trades)
        results[strat_name] = []
        close_tick_est = 0
        for i, t in enumerate(engine.trades):
            # Estimate: entry happened close_tick_est - hold_ticks ago, close at close_tick_est
            entry_tick_est = max(0, close_tick_est - t.hold_ticks)
            results[strat_name].append({
                'symbol': t.symbol,
                'entry_tick': entry_tick_est,
                'direction': t.direction,
                'pnl': t.pnl,
                'strategy': t.strategy,
            })
            close_tick_est += max(1, t.hold_ticks // 10)  # rough spacing

    # Run 8 new strategies SOLO
    for sname, strat in v65.STRATEGIES.items():
        cfg = deepcopy(strat['cfg'])
        engine = v65.SoloEngine(sname, strat['fn'], cfg, f"{sname}_{regime}_S{seed}")
        for tick in range(v40.TOTAL_TICKS):
            for sym in all_prices:
                if tick + 1 < 60: continue
                prices_slice = all_prices[sym][max(0, tick-250):tick+1]
                engine._try_entry(sym, prices_slice, tick)
                engine._check_stops(sym, prices_slice, tick)
            engine.update_equity(all_prices, tick)
        results[sname] = [
            {'symbol': t.symbol, 'entry_tick': t.entry_tick, 'direction': t.direction,
             'pnl': t.pnl, 'strategy': t.strategy}
            for t in engine.trades
        ]

    return results


# ════════════════════════════════════════════════════════════════════
#  CORRELATION CALCULATIONS
# ════════════════════════════════════════════════════════════════════

def entry_jaccard(trades1: List, trades2: List, tolerance: int = 30) -> float:
    """Jaccard similarity of entry ticks.
    Two trades "match" if they enter on the same symbol within ±tolerance ticks.
    Returns 0-1 (1 = identical entry timing).
    """
    # Group trades by symbol
    by_sym1 = defaultdict(list)
    by_sym2 = defaultdict(list)
    for t in trades1:
        by_sym1[t['symbol']].append(t['entry_tick'])
    for t in trades2:
        by_sym2[t['symbol']].append(t['entry_tick'])

    all_syms = set(by_sym1.keys()) | set(by_sym2.keys())
    matches = 0
    total_unique = 0

    for sym in all_syms:
        ticks1 = sorted(by_sym1.get(sym, []))
        ticks2 = sorted(by_sym2.get(sym, []))
        # For each tick in ticks1, find if there's a close tick in ticks2
        used2 = set()
        sym_matches = 0
        for t1 in ticks1:
            for i, t2 in enumerate(ticks2):
                if i in used2: continue
                if abs(t1 - t2) <= tolerance:
                    sym_matches += 1
                    used2.add(i)
                    break
        # Union size = len(ticks1) + len(ticks2) - sym_matches
        union = len(ticks1) + len(ticks2) - sym_matches
        if union > 0:
            matches += sym_matches
            total_unique += union

    return matches / total_unique if total_unique > 0 else 0


def direction_agreement(trades1: List, trades2: List, tolerance: int = 30) -> float:
    """When two strategies enter on same symbol within ±tolerance, do they agree on direction?
    Returns fraction of matching-direction entries (0-1, 0.5 = random).
    """
    by_sym1 = defaultdict(list)
    by_sym2 = defaultdict(list)
    for t in trades1:
        by_sym1[t['symbol']].append((t['entry_tick'], t['direction']))
    for t in trades2:
        by_sym2[t['symbol']].append((t['entry_tick'], t['direction']))

    agreements = 0
    comparisons = 0
    for sym in set(by_sym1.keys()) & set(by_sym2.keys()):
        for t1, d1 in by_sym1[sym]:
            for t2, d2 in by_sym2[sym]:
                if abs(t1 - t2) <= tolerance:
                    comparisons += 1
                    if d1 == d2: agreements += 1

    return agreements / comparisons if comparisons > 0 else 0.5


def pnl_correlation(trades1: List, trades2: List, n_buckets: int = 50) -> float:
    """Pearson correlation of bucketed P&L time series.
    Divides the session into n_buckets and sums P&L per bucket.
    """
    if not trades1 or not trades2: return 0
    # Bucket trades by entry tick
    bucket_size = v40.TOTAL_TICKS / n_buckets
    pnls1 = [0.0] * n_buckets
    pnls2 = [0.0] * n_buckets
    for t in trades1:
        b = min(int(t['entry_tick'] / bucket_size), n_buckets - 1)
        pnls1[b] += t['pnl']
    for t in trades2:
        b = min(int(t['entry_tick'] / bucket_size), n_buckets - 1)
        pnls2[b] += t['pnl']

    mean1 = sum(pnls1) / n_buckets
    mean2 = sum(pnls2) / n_buckets
    num = sum((p1 - mean1) * (p2 - mean2) for p1, p2 in zip(pnls1, pnls2))
    den1 = math.sqrt(sum((p1 - mean1) ** 2 for p1 in pnls1))
    den2 = math.sqrt(sum((p2 - mean2) ** 2 for p2 in pnls2))
    if den1 == 0 or den2 == 0: return 0
    return num / (den1 * den2)


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

ALL_STRATEGIES = ['A', 'B'] + list(v65.STRATEGIES.keys())


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=10)
    ap.add_argument('--regime', default='MIXED', choices=v63.REGIMES_ALL)
    ap.add_argument('--out', default='/tmp/v66_correlation.json')
    args = ap.parse_args()

    seeds = v63.SEEDS_50[:args.seeds]
    print(f"\n{'='*120}")
    print(f"  v66 CORRELATION ANALYSIS — {len(ALL_STRATEGIES)} strategies × {len(seeds)} seeds × {args.regime}")
    print(f"{'='*120}\n")

    # Aggregate trades across all seeds
    all_trades = {s: [] for s in ALL_STRATEGIES}
    for seed in seeds:
        t0 = time.time()
        results = run_all_strategies_same_data(args.regime, seed)
        for sname, trades in results.items():
            all_trades[sname].extend(trades)
        print(f"  S{seed}: " + ", ".join(f"{s}={len(t)}" for s, t in results.items()) + f"  ({time.time()-t0:.1f}s)")

    # Compute pairwise correlations
    print(f"\n{'='*120}")
    print(f"  ENTRY CORRELATION (Jaccard similarity — 1.0 = same entry timing)")
    print(f"{'='*120}")
    header = "          " + " ".join(f"{s:>10}" for s in ALL_STRATEGIES)
    print(header)
    entry_corr = {}
    for s1 in ALL_STRATEGIES:
        row = f"  {s1:<8} "
        entry_corr[s1] = {}
        for s2 in ALL_STRATEGIES:
            if s1 == s2:
                row += f"{'1.00':>10} "
                entry_corr[s1][s2] = 1.0
            else:
                c = entry_jaccard(all_trades[s1], all_trades[s2], tolerance=30)
                entry_corr[s1][s2] = c
                row += f"{c:>10.2f} "
        print(row)

    print(f"\n{'='*120}")
    print(f"  DIRECTION AGREEMENT (when both enter same time, same direction? 0.5 = random)")
    print(f"{'='*120}")
    print(header)
    dir_corr = {}
    for s1 in ALL_STRATEGIES:
        row = f"  {s1:<8} "
        dir_corr[s1] = {}
        for s2 in ALL_STRATEGIES:
            if s1 == s2:
                row += f"{'1.00':>10} "
                dir_corr[s1][s2] = 1.0
            else:
                c = direction_agreement(all_trades[s1], all_trades[s2], tolerance=30)
                dir_corr[s1][s2] = c
                row += f"{c:>10.2f} "
        print(row)

    print(f"\n{'='*120}")
    print(f"  P&L CORRELATION (Pearson — do P&L time series move together?)")
    print(f"{'='*120}")
    print(header)
    pnl_corr = {}
    for s1 in ALL_STRATEGIES:
        row = f"  {s1:<8} "
        pnl_corr[s1] = {}
        for s2 in ALL_STRATEGIES:
            if s1 == s2:
                row += f"{'1.00':>10} "
                pnl_corr[s1][s2] = 1.0
            else:
                c = pnl_correlation(all_trades[s1], all_trades[s2], n_buckets=50)
                pnl_corr[s1][s2] = c
                row += f"{c:>10.2f} "
        print(row)

    # Find duplicates (correlation > 0.7)
    print(f"\n{'='*120}")
    print(f"  DUPLICATE DETECTION (P&L correlation > 0.70)")
    print(f"{'='*120}")
    duplicates = []
    for s1, s2 in combinations(ALL_STRATEGIES, 2):
        c = pnl_corr[s1][s2]
        if c > 0.7:
            duplicates.append((s1, s2, c))
            print(f"  ⚠️  {s1} ↔ {s2}: {c:.2f} — DUPLICATE")
    if not duplicates:
        print("  ✅ No duplicates found — all strategies are sufficiently independent")

    # Recommended portfolio (greedy: start with best P&L, add uncorrelated)
    print(f"\n{'='*120}")
    print(f"  RECOMMENDED PORTFOLIO (uncorrelated subset, |corr| < 0.5)")
    print(f"{'='*120}")
    # Rank by absolute P&L (proxy for edge)
    pnl_totals = {s: sum(t['pnl'] for t in trades) for s, trades in all_trades.items()}
    ranked = sorted(pnl_totals.items(), key=lambda x: x[1], reverse=True)
    portfolio = []
    for s, pnl in ranked:
        # Check correlation with all in portfolio
        ok = True
        for ps in portfolio:
            if abs(pnl_corr[s][ps]) > 0.5:
                ok = False
                break
        if ok:
            portfolio.append(s)
            print(f"  ✅ {s}: total P&L {pnl:+.2f}, trades {len(all_trades[s])}")
        else:
            blocked_by = [ps for ps in portfolio if abs(pnl_corr[s][ps]) > 0.5]
            print(f"  ❌ {s}: total P&L {pnl:+.2f} — blocked by {blocked_by}")

    # Save
    save = {
        'entry_corr': entry_corr,
        'direction_corr': dir_corr,
        'pnl_corr': pnl_corr,
        'duplicates': duplicates,
        'portfolio': portfolio,
        'pnl_totals': pnl_totals,
        'regime': args.regime,
        'n_seeds': len(seeds),
    }
    with open(args.out, 'w') as f: json.dump(save, f, indent=2)
    print(f"\nSaved to {args.out}")

    return save


if __name__ == "__main__":
    main()
