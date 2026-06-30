#!/usr/bin/env python3
"""
v82a ABLATION — Probar 4 valores de trend filter threshold para encontrar el óptimo.

Thresholds a probar:
  - 0.02 (muy estricto, bloquea casi todo)
  - 0.05 (v81 baseline)
  - 0.10 (v82a hipótesis —.Resultó peor)
  - 0.15 (muy laxo, casi sin filtro)
  - 0.00 (sin filtro, control)

Para cada threshold, correr 12 seeds × 8 perfiles = 96 runs.
Comparar P&L, Profit%, WR%, MaxDD% por perfil.
"""
import sys, os, json, random, statistics, time
from copy import deepcopy
sys.path.insert(0, '/home/z/my-project/scripts')
sys.path.insert(0, '/home/z/my-project/scripts/backtest')

import v40_push as v40
from v62_push import EngineSimV62, v61b_base
import v38_push_v37e as v38
from v80_direction_token_test import gen_profile_prices, PROFILES, SEEDS
from v81_universal_v2 import v80_config, compute_sma_slope
from v82a_universal_v3 import EngineSimV82a


def run_ablation(threshold, seeds, profiles, label):
    """Correr 12 seeds × 8 perfiles con un threshold dado."""
    print(f"\n{'='*60}\n  ABLATION: trend filter = {threshold}%  ({label})\n{'='*60}", flush=True)
    results = {}
    for seed in seeds:
        for profile in profiles:
            rng = random.Random(seed)
            base = 1.0 * (1 + rng.uniform(-0.3, 0.3))
            all_prices = {f"TOK{i:02d}": gen_profile_prices(v40.TOTAL_TICKS, base * (1 + rng.uniform(-0.2, 0.2)), rng, profile)
                          for i in range(10)}

            # Patch the threshold at runtime
            import v82a_universal_v3 as v82a_mod
            old_thr = v82a_mod.TREND_FILTER_THRESHOLD
            v82a_mod.TREND_FILTER_THRESHOLD = threshold

            engine = v82a_mod.EngineSimV82a(deepcopy(v82a_mod.v82a_config()), f'v82a_{profile}')
            for tick in range(v40.TOTAL_TICKS):
                for sym in all_prices:
                    if tick + 1 < 60:
                        continue
                    prices_slice = all_prices[sym][max(0, tick-250):tick+1]
                    engine._try_strategy_a(sym, prices_slice, tick)
                    engine._try_strategy_b(sym, prices_slice, tick)
                    engine._try_strategy_d(sym, prices_slice, tick)
                    engine._try_strategy_e(sym, prices_slice, tick)
                    engine._check_stops(sym, prices_slice, tick)
                engine.update_equity(all_prices, tick)

            m = engine.get_metrics()
            m['pnl_long'] = engine.pnl_long
            m['pnl_short'] = engine.pnl_short
            results.setdefault(seed, {})[profile] = m
            v82a_mod.TREND_FILTER_THRESHOLD = old_thr
        print(f"  seed {seed} done", flush=True)
    return results


def summarize(results, label):
    print(f"\n--- {label} ---")
    print(f"{'Profile':<10} {'Trades':<8} {'WR%':<8} {'P&L':<12} {'MaxDD%':<8} {'Profit%':<10} {'L P&L':<10} {'S P&L':<10}")
    summary = {}
    for profile in PROFILES:
        ms = [r[profile] for r in results.values() if profile in r]
        if not ms:
            continue
        pnl = statistics.mean(m['pnl'] for m in ms)
        profit = sum(1 for m in ms if m['pnl'] > 0) / len(ms) * 100
        wr = statistics.mean(m['wr'] for m in ms)
        maxdd = statistics.mean(m['max_dd'] for m in ms)
        trades = statistics.mean(m['trades'] for m in ms)
        l_pnl = statistics.mean(m['pnl_long'] for m in ms)
        s_pnl = statistics.mean(m['pnl_short'] for m in ms)
        print(f"{profile:<10} {trades:<8.0f} {wr:<8.1f} {pnl:<+12.2f} {maxdd:<8.2f} {profit:<10.0f} {l_pnl:<+10.2f} {s_pnl:<+10.2f}")
        summary[profile] = {'pnl': pnl, 'profit': profit, 'wr': wr, 'maxdd': maxdd, 'trades': trades}
    return summary


if __name__ == '__main__':
    thresholds = [
        (0.00, 'NO filter (control)'),
        (0.02, 'very strict'),
        (0.05, 'v81 baseline'),
        (0.10, 'v82a hypothesis'),
        (0.15, 'very loose'),
    ]

    all_summaries = {}
    for thr, label in thresholds:
        # Para ablation rápido: 4 seeds × 8 perfiles = 32 runs (no 96, tiempo limitado)
        # Si el óptimo es claro, luego validamos con 12 seeds
        ablation_seeds = [42, 1337, 7, 2025]  # 4 seeds representativos
        results = run_ablation(thr, ablation_seeds, PROFILES, label)
        all_summaries[thr] = summarize(results, f"threshold={thr}% ({label})")

    # Comparativa final
    print("\n\n" + "=" * 110)
    print("COMPARATIVA FINAL — P&L por perfil (4 seeds × 8 perfiles = 32 runs)")
    print("=" * 110)
    print(f"{'Profile':<10}", end='')
    for thr, _ in thresholds:
        print(f" {thr:.2f}%".rjust(12), end='')
    print()
    print("-" * 110)
    for profile in PROFILES:
        print(f"{profile:<10}", end='')
        for thr, _ in thresholds:
            val = all_summaries[thr].get(profile, {}).get('pnl', 0)
            marker = "✅" if val > 0 else "❌"
            print(f" {marker}{val:+9.2f}".rjust(12), end='')
        print()

    print("\n" + "=" * 110)
    print("COMPARATIVA FINAL — Profit% por perfil")
    print("=" * 110)
    print(f"{'Profile':<10}", end='')
    for thr, _ in thresholds:
        print(f" {thr:.2f}%".rjust(12), end='')
    print()
    print("-" * 110)
    for profile in PROFILES:
        print(f"{profile:<10}", end='')
        for thr, _ in thresholds:
            val = all_summaries[thr].get(profile, {}).get('profit', 0)
            print(f" {val:>10.0f}%", end='')
        print()

    # Suma total
    print("\n" + "=" * 110)
    print(f"{'TOTAL P&L':<10}", end='')
    for thr, _ in thresholds:
        total = sum(s.get('pnl', 0) for s in all_summaries[thr].values())
        print(f" {total:+11.2f}".rjust(12), end='')
    print()
    profitable_counts = {}
    for thr, _ in thresholds:
        cnt = sum(1 for s in all_summaries[thr].values() if s.get('pnl', 0) > 0)
        profitable_counts[thr] = cnt
    print(f"{'PROFITABLE':<10}", end='')
    for thr, _ in thresholds:
        print(f" {profitable_counts[thr]:>10}/8", end='')
    print()

    # Save
    with open('/tmp/v82a_ablation.json', 'w') as f:
        json.dump(all_summaries, f, indent=2)
    print("\nSaved /tmp/v82a_ablation.json")
