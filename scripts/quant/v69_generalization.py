#!/usr/bin/env python3
"""
v69 — GENERALIZATION ACROSS ASSETS (PRIORITY #3)

User directive:
  "Prueba el motor en varios activos. Si solo funciona en uno, no me interesa.
   Busca parámetros que funcionen bien en la mayoría de activos aunque el
   beneficio absoluto sea ligeramente inferior."

WHAT THIS DOES:
  Tests v62a on multiple ASSET PROFILES (different volatility / trend / liquidity).
  Each profile generates prices with different characteristics:
    - BTC-like: low-medium vol (0.4%), occasional trends, deep liquidity
    - ALT-coin: medium-high vol (0.7%), frequent trends, medium liquidity
    - STABLE-pair: very low vol (0.15%), mean-reverting
    - MEME-coin: very high vol (1.5%), extreme moves, low liquidity
    - DEFI-token: high vol (1.0%), trending, gap risk
    - LARGE-cap: low vol (0.3%), slow trends, deep liquidity

  For each profile, run 12 seeds and measure:
    - P&L, WR, MaxDD, PF, composite score
    - Is v62a profitable on this asset?
    - Does MaxDD stay under 0.35%?

  A robust strategy works on MOST profiles, not just one.
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy
from typing import List, Dict

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
import v63_robustness as v63
from v62_push import v61b_base, EngineSimV62


# ════════════════════════════════════════════════════════════════════
#  ASSET PROFILES
# ════════════════════════════════════════════════════════════════════

ASSET_PROFILES = {
    'BTC': {
        'desc': 'Bitcoin-like — low-medium vol, occasional trends, deep liquidity',
        'vol_pct': 0.40,
        'drift_pct': 0.5,  # slight upward drift (BTC historical)
        'trend_freq': 0.10,  # 10% of time in trending regime
        'gap_freq': 0.0,     # no gaps (24/7 market, deep liquidity)
    },
    'ALT': {
        'desc': 'Altcoin — medium-high vol, frequent trends, medium liquidity',
        'vol_pct': 0.70,
        'drift_pct': 0.0,
        'trend_freq': 0.15,
        'gap_freq': 0.02,  # occasional gaps
    },
    'STABLE': {
        'desc': 'Stablecoin pair — very low vol, mean-reverting',
        'vol_pct': 0.15,
        'drift_pct': 0.0,
        'trend_freq': 0.0,
        'gap_freq': 0.0,
    },
    'MEME': {
        'desc': 'Meme coin — very high vol, extreme moves, low liquidity',
        'vol_pct': 1.50,
        'drift_pct': 0.0,
        'trend_freq': 0.20,
        'gap_freq': 0.05,  # frequent gaps
    },
    'DEFI': {
        'desc': 'DeFi token — high vol, trending, gap risk',
        'vol_pct': 1.00,
        'drift_pct': -0.2,  # slight downward drift (many DEFI tokens trend down)
        'trend_freq': 0.18,
        'gap_freq': 0.03,
    },
    'LARGE': {
        'desc': 'Large cap (ETH-like) — low vol, slow trends, deep liquidity',
        'vol_pct': 0.30,
        'drift_pct': 0.3,
        'trend_freq': 0.08,
        'gap_freq': 0.0,
    },
}


def gen_asset_prices(n: int, base: float, profile_name: str, rng: random.Random) -> List[float]:
    """Generate prices for a specific asset profile.
    Includes regime switching between normal and trending, plus occasional gaps.
    """
    p = ASSET_PROFILES[profile_name]
    prices = [base]
    regime_ticks_left = 1200
    # Start in normal regime
    in_trend = False
    trend_dir = 1
    vol = base * p['vol_pct'] / 100
    drift = base * p['drift_pct'] / 100 / n * 5

    for i in range(1, n):
        if regime_ticks_left <= 0:
            # Switch regime
            in_trend = rng.random() < p['trend_freq']
            if in_trend:
                trend_dir = rng.choice([-1, 1])
                vol = prices[-1] * p['vol_pct'] / 100 * 1.5  # higher vol in trend
                drift = prices[-1] * 2.0 / 100 / n * 5 * trend_dir  # trend drift
            else:
                vol = prices[-1] * p['vol_pct'] / 100
                drift = prices[-1] * p['drift_pct'] / 100 / n * 5
            regime_ticks_left = 1200

        # Gap risk
        gap = 0
        if p['gap_freq'] > 0 and rng.random() < p['gap_freq'] / 100:
            # Random gap: 2-5% move
            gap = prices[-1] * rng.uniform(-0.05, 0.05)

        prices.append(max(0.0001, prices[-1] + rng.gauss(0, vol) + drift + gap))
        regime_ticks_left -= 1

    return prices


# ════════════════════════════════════════════════════════════════════
#  RUNNER
# ════════════════════════════════════════════════════════════════════

def run_on_asset(cfg, profile_name: str, seed: int) -> Dict:
    """Run one config on one asset profile with one seed."""
    rng = random.Random(seed)
    all_prices = {
        f"TOK{i:02d}": gen_asset_prices(
            v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)),
            profile_name, rng
        )
        for i in range(v40.N_TOKENS)
    }
    engine = EngineSimV62(deepcopy(cfg), f"{profile_name}_S{seed}")
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

    base_metrics = engine.get_metrics()
    trades = [
        {'pnl': t.pnl, 'r_multiple': t.r_multiple, 'hold_ticks': t.hold_ticks,
         'close_reason': t.close_reason, 'strategy': t.strategy}
        for t in engine.trades
    ]
    ext = v63.extended_metrics(trades, engine.equity_series, base_metrics['pnl'], base_metrics['max_dd'])
    ext['profile'] = profile_name
    ext['seed'] = seed
    return ext


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=12)
    ap.add_argument('--profiles', default='ALL', help='comma-separated or ALL')
    ap.add_argument('--out', default='/tmp/v69_generalization.json')
    args = ap.parse_args()

    seeds = v63.SEEDS_50[:args.seeds]
    profiles = list(ASSET_PROFILES.keys()) if args.profiles == 'ALL' else args.profiles.split(',')
    cfg = v61b_base(pyramid_pct=0.75)

    print(f"\n{'='*120}")
    print(f"  v69 GENERALIZATION — v62a on {len(profiles)} asset profiles × {len(seeds)} seeds")
    print(f"{'='*120}\n")

    all_results = {}
    for profile in profiles:
        print(f"\n→ {profile} — {ASSET_PROFILES[profile]['desc']}", flush=True)
        t0 = time.time()
        per_seed = []
        for seed in seeds:
            m = run_on_asset(cfg, profile, seed)
            per_seed.append(m)
            all_results.setdefault(profile, []).append(m)
        agg = v63.aggregate_seeds(per_seed)
        print(f"   P&L {agg['pnl_mean']:+.2f}±{agg['pnl_std']:.0f}, WR {agg['wr_mean']:.1f}%, "
              f"DD {agg['max_dd_mean']:.2f}%, PF {agg['pf_mean']:.2f}, "
              f"trades {agg['trades_mean']:.0f}, profitable {agg['profitable_seeds_pct']:.0f}%  ({time.time()-t0:.1f}s)",
              flush=True)
        # Save progressively
        save = {p: [{k: v for k, v in m.items() if k not in ('equity_curve', 'trades_list')} for m in results]
                for p, results in all_results.items()}
        with open(args.out, 'w') as f: json.dump(save, f, indent=2, default=v63._json_default)

    # Summary
    print(f"\n\n{'='*180}")
    print(f"  GENERALIZATION SUMMARY — v62a across {len(profiles)} asset profiles ({len(seeds)} seeds each)")
    print(f"{'='*180}")
    print(f"{'Profile':<10} {'Description':<55} {'Trades':<8} {'WR%':<14} {'P&L':<16} {'PF':<8} {'Sharpe':<10} {'MaxDD%':<10} {'Calmar':<10} {'Profit%':<10} {'Verdict':<15}")
    print('-' * 180)
    n_profitable = 0
    n_maxdd_ok = 0
    for profile in profiles:
        results = all_results.get(profile, [])
        if not results: continue
        agg = v63.aggregate_seeds(results)
        if agg['pnl_mean'] > 0: n_profitable += 1
        if agg['max_dd_mean'] <= 0.35: n_maxdd_ok += 1
        if agg['pnl_mean'] > 0 and agg['max_dd_mean'] <= 0.35:
            verdict = '✅ GOOD'
        elif agg['pnl_mean'] > 0:
            verdict = '⚠️ PROFITABLE, DD HIGH'
        else:
            verdict = '❌ LOSES'
        print(f"{profile:<10} {ASSET_PROFILES[profile]['desc'][:53]:<55} {agg['trades_mean']:<8.0f} "
              f"{agg['wr_mean']:.1f}±{agg['wr_std']:.1f}{'':>3} {agg['pnl_mean']:+.2f}±{agg['pnl_std']:.0f}{'':>4} "
              f"{agg['pf_mean']:.2f}{'':>4} {agg['sharpe_mean']:+.2f}{'':>5} "
              f"{agg['max_dd_mean']:.2f}{'':>5} {agg['calmar_mean']:.1f}{'':>5} "
              f"{agg['profitable_seeds_pct']:.0f}%{'':>5} {verdict}")

    print(f"\n  GENERALIZATION SCORE:")
    print(f"    Profitable on {n_profitable}/{len(profiles)} profiles ({n_profitable/len(profiles)*100:.0f}%)")
    print(f"    MaxDD ≤0.35% on {n_maxdd_ok}/{len(profiles)} profiles ({n_maxdd_ok/len(profiles)*100:.0f}%)")

    print(f"\n  VERDICT:")
    if n_profitable == len(profiles) and n_maxdd_ok == len(profiles):
        print(f"  ✅ v62a GENERALIZES — profitable AND safe on all {len(profiles)} profiles")
    elif n_profitable >= len(profiles) * 0.7:
        print(f"  ⚠️  v62a PARTIAL GENERALIZATION — profitable on {n_profitable}/{len(profiles)} profiles")
        fails = [p for p in profiles if v63.aggregate_seeds(all_results[p])['pnl_mean'] <= 0]
        print(f"     Fails on: {', '.join(fails)}")
    else:
        print(f"  ❌ v62a DOES NOT GENERALIZE — only {n_profitable}/{len(profiles)} profiles profitable")

    # Save
    save = {p: [{k: v for k, v in m.items() if k not in ('equity_curve', 'trades_list')} for m in results]
            for p, results in all_results.items()}
    with open(args.out, 'w') as f: json.dump(save, f, indent=2, default=v63._json_default)
    print(f"\nSaved to {args.out}")

    return all_results


if __name__ == "__main__":
    main()
