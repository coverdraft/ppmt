#!/usr/bin/env python3
"""
v63 — QUANTITATIVE ROBUSTNESS FRAMEWORK (paradigm shift)

User directive:
  "No aceptes una mejora porque gane más dinero.
   Solo acepta una mejora si demuestra estadísticamente que es más robusta que v62a.
   Piensa como un quantitative researcher de un hedge fund.
   No busques el mejor backtest. Busca la estrategia más difícil de romper."

This module implements PRIORITY #1 (ROBUSTNESS), #9 (METRICS), #10 (GOLDEN RULE).

WHAT'S NEW vs v38-v62:
  1. MULTI-REGIME price generator: explicit bull/bear/sideways/high-vol/low-vol/mixed
     regimes, each isolated so we can measure performance PER regime.
  2. 50-SEED validator (up from 12). Statistical power to detect real differences.
  3. COMPOSITE SCORE combining: PF, Sharpe, Sortino, MaxDD, Calmar, Recovery,
     WR, AvgR, seed stability, regime stability.
  4. ACCEPTANCE GATE: a candidate is only accepted if it beats v62a on composite
     score AND shows no regime collapse AND has tight seed std-dev.

REGIME DESIGN (5 pure + 1 mixed = 6 regimes):
  - BULL     : drift +0.15%/tick, vol 0.40%   (sustained uptrend)
  - BEAR     : drift -0.15%/tick, vol 0.40%   (sustained downtrend)
  - SIDE     : drift 0.00%,      vol 0.30%    (choppy mean-revert)
  - HIGHVOL  : drift 0.00%,      vol 1.20%    (storm)
  - LOWVOL   : drift 0.00%,      vol 0.15%    (dead market)
  - MIXED    : regime switches every 1200 ticks with v38 weights
               (this is the v38 baseline for backward comparability)

Each candidate runs on ALL 6 regimes × 50 seeds = 300 runs.
A robust strategy must survive all regimes without collapse.
"""
import random, statistics, math, sys, os, json, time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from copy import deepcopy
from collections import defaultdict

# ── Reuse the proven engine scaffolding ──
sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v51_push import make_strategies_v51
from v53_push import EngineSimV53, make_v53_config
from v57_push import EngineSimV57
from v62_push import EngineSimV62, v61b_base


# ════════════════════════════════════════════════════════════════════
#  1. MULTI-REGIME PRICE GENERATOR
# ════════════════════════════════════════════════════════════════════

REGIMES = {
    # 5 pure regimes — each isolates one market condition
    # Calibrated to be realistic (1.5s/tick crypto market):
    #   - BULL: sustained uptrend (drift +3% over 4h, vol 0.40%)
    #   - BEAR: sustained downtrend (drift -3% over 4h, vol 0.50% — bears are more volatile)
    #   - SIDE: choppy mean-reversion market (drift 0, vol 0.30%)
    #   - HIGHVOL: storm market (drift 0, vol 0.80% — realistic crypto storm)
    #   - LOWVOL: dead market (drift 0, vol 0.20% — still tradeable, ATR floor handles it)
    'BULL':    {'vol_pct': 0.40, 'drift_pct': +3.0, 'weight': 1.0, 'switch_every': None},
    'BEAR':    {'vol_pct': 0.50, 'drift_pct': -3.0, 'weight': 1.0, 'switch_every': None},
    'SIDE':    {'vol_pct': 0.30, 'drift_pct':  0.0, 'weight': 1.0, 'switch_every': None},
    'HIGHVOL': {'vol_pct': 0.80, 'drift_pct':  0.0, 'weight': 1.0, 'switch_every': None},
    'LOWVOL':  {'vol_pct': 0.20, 'drift_pct':  0.0, 'weight': 1.0, 'switch_every': None},
    # 1 mixed regime — replicates v38 baseline (used for backward-compat comparison)
    'MIXED':   {'vol_pct': None,  'drift_pct': None,  'weight': None, 'switch_every': 1200},
}

# v38 mixed-regime weights (kept identical for backward comparability)
MIXED_REGIME_POOL = [
    {'vol_pct': 0.30, 'drift_pct': 0.0, 'weight': 0.60},
    {'vol_pct': 0.60, 'drift_pct': 0.0, 'weight': 0.25},
    {'vol_pct': 1.20, 'drift_pct': 0.0, 'weight': 0.10},
    {'vol_pct': 0.50, 'drift_pct': 3.0, 'weight': 0.05},
]


def gen_pure_regime_prices(n: int, base: float, regime_name: str, rng: random.Random) -> List[float]:
    """Generate prices for a PURE regime (no switching)."""
    r = REGIMES[regime_name]
    vol = base * r['vol_pct'] / 100
    drift = base * r['drift_pct'] / 100 / n * 5
    prices = [base]
    for i in range(1, n):
        prices.append(max(0.0001, prices[-1] + rng.gauss(0, vol) + drift))
    return prices


def gen_mixed_regime_prices(n: int, base: float, rng: random.Random) -> List[float]:
    """v38-style regime switching — for backward comparability with all v38-v62 results."""
    prices = [base]
    regime_ticks_left = 1200
    regime = _pick_mixed_regime(rng)
    vol = base * regime['vol_pct'] / 100
    drift = base * regime['drift_pct'] / 100 / n * 5
    for i in range(1, n):
        if regime_ticks_left <= 0:
            regime = _pick_mixed_regime(rng)
            vol = prices[-1] * regime['vol_pct'] / 100
            drift = prices[-1] * regime['drift_pct'] / 100 / n * 5
            regime_ticks_left = 1200
        prices.append(max(0.0001, prices[-1] + rng.gauss(0, vol) + drift))
        regime_ticks_left -= 1
    return prices


def _pick_mixed_regime(rng):
    r = rng.random(); cum = 0
    for regime in MIXED_REGIME_POOL:
        cum += regime['weight']
        if r <= cum: return regime
    return MIXED_REGIME_POOL[0]


def gen_regime_prices(n: int, base: float, regime_name: str, rng: random.Random) -> List[float]:
    """Dispatch: pure regime or mixed."""
    if regime_name == 'MIXED':
        return gen_mixed_regime_prices(n, base, rng)
    return gen_pure_regime_prices(n, base, regime_name, rng)


# ════════════════════════════════════════════════════════════════════
#  2. EXTENDED METRICS (Sortino, Calmar, Recovery Factor)
# ════════════════════════════════════════════════════════════════════

def extended_metrics(trades: List[Dict], equity_curve: List[float],
                     pnl: float, max_dd: float, n_seeds_per_run: int = 1) -> Dict:
    """Compute extended metrics for a single run.

    trades: list of trade dicts (each must have pnl, r_multiple, hold_ticks, close_reason)
    equity_curve: list of equity values per tick
    """
    if not trades:
        return _empty_metrics()

    pnls = [t['pnl'] for t in trades]
    n_trades = len(trades)
    n_wins = sum(1 for p in pnls if p > 0)
    n_losses = sum(1 for p in pnls if p < 0)
    wr = n_wins / n_trades * 100 if n_trades else 0
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    pf = gross_profit / gross_loss if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0)
    avg_r = statistics.mean(t['r_multiple'] for t in trades)

    # Sharpe (per-trade, annualized to 4h session)
    if len(pnls) > 1:
        mean_pnl = statistics.mean(pnls)
        std_pnl = statistics.stdev(pnls)
        sharpe = (mean_pnl / std_pnl * math.sqrt(n_trades)) if std_pnl > 0 else 0
    else:
        sharpe = 0

    # Sortino — only penalize downside volatility
    downside = [p for p in pnls if p < 0]
    if len(downside) > 1:
        downside_std = math.sqrt(sum(p**2 for p in downside) / len(downside))
        sortino = (statistics.mean(pnls) / downside_std * math.sqrt(n_trades)) if downside_std > 0 else 0
    else:
        sortino = float('inf') if pnl > 0 else 0

    # Calmar = annualized return / max drawdown (we use total P&L / max_dd %)
    calmar = pnl / max_dd if max_dd > 0 else (float('inf') if pnl > 0 else 0)

    # Recovery Factor = net profit / max drawdown (in same units)
    recovery = pnl / max_dd if max_dd > 0 else (float('inf') if pnl > 0 else 0)

    # Max consecutive losses
    max_consec_loss = 0; cur = 0
    for p in pnls:
        if p < 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        else:
            cur = 0

    # Avg hold time (in seconds)
    avg_hold_s = statistics.mean(t['hold_ticks'] for t in trades) * v40.TICK_SECONDS

    return {
        'trades': n_trades,
        'wr': wr,
        'pnl': pnl,
        'pf': pf,
        'sharpe': sharpe,
        'sortino': sortino,
        'max_dd': max_dd,
        'calmar': calmar,
        'recovery': recovery,
        'avg_r': avg_r,
        'max_consec_loss': max_consec_loss,
        'avg_hold_s': avg_hold_s,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'n_wins': n_wins,
        'n_losses': n_losses,
    }


def _empty_metrics():
    return {
        'trades': 0, 'wr': 0, 'pnl': 0, 'pf': 0, 'sharpe': 0, 'sortino': 0,
        'max_dd': 0, 'calmar': 0, 'recovery': 0, 'avg_r': 0, 'max_consec_loss': 0,
        'avg_hold_s': 0, 'gross_profit': 0, 'gross_loss': 0, 'n_wins': 0, 'n_losses': 0,
    }


# ════════════════════════════════════════════════════════════════════
#  3. COMPOSITE SCORE  (PRIORITY #9)
# ════════════════════════════════════════════════════════════════════

def composite_score(metrics: Dict, regime_stability: float = 1.0,
                    seed_stability: float = 1.0) -> float:
    """Composite score combining all key metrics.

    Normalizes each component to [0, 1] using realistic caps, then weights:
      - Profit Factor (cap 3.0):           15%
      - Sharpe (cap 10):                    12%
      - Sortino (cap 15):                   10%
      - Calmar (cap 200):                   10%
      - Recovery Factor (cap 200):          10%
      - Win Rate (cap 85%):                 10%
      - AvgR (cap 1.0):                     10%
      - MaxDD penalty (cap 0.50%):           8%  (inverted: lower DD = higher score)
      - Regime stability (%):                8%
      - Seed stability (%):                  7%

    Higher = better. v62a baseline expected ~0.65-0.75.
    """
    def cap(v, c):
        return max(0, min(1, v / c))

    pf_s = cap(metrics.get('pf', 0), 3.0)
    sh_s = cap(metrics.get('sharpe', 0), 10.0)
    so_s = cap(metrics.get('sortino', 0), 15.0)
    cl_s = cap(metrics.get('calmar', 0), 200.0)
    rc_s = cap(metrics.get('recovery', 0), 200.0)
    wr_s = cap(metrics.get('wr', 0), 85.0)
    ar_s = cap(max(0, metrics.get('avg_r', 0)), 1.0)
    dd_s = cap(1 - metrics.get('max_dd', 0.5) / 0.50, 1.0)  # 0% DD → 1.0, 0.5% DD → 0
    rs_s = cap(regime_stability, 1.0)
    ss_s = cap(seed_stability, 1.0)

    score = (
        0.15 * pf_s +
        0.12 * sh_s +
        0.10 * so_s +
        0.10 * cl_s +
        0.10 * rc_s +
        0.10 * wr_s +
        0.10 * ar_s +
        0.08 * dd_s +
        0.08 * rs_s +
        0.07 * ss_s
    )
    return score * 100  # scale to 0-100 for readability


# ════════════════════════════════════════════════════════════════════
#  4. MULTI-REGIME ENGINE RUNNER
# ════════════════════════════════════════════════════════════════════

def run_one_regime(cfg: Dict, regime_name: str, seed: int,
                   engine_class=EngineSimV62) -> Dict:
    """Run one config on one regime with one seed. Returns extended metrics."""
    rng = random.Random(seed)
    # Generate 10 tokens, each with a slightly different base price (as in v38-v62)
    all_prices = {
        f"TOK{i:02d}": gen_regime_prices(
            v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)),
            regime_name, rng
        )
        for i in range(v40.N_TOKENS)
    }
    engine = engine_class(deepcopy(cfg), f"{regime_name}_S{seed}")
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
    # Augment with extended metrics
    trades = [
        {'pnl': t.pnl, 'r_multiple': t.r_multiple, 'hold_ticks': t.hold_ticks,
         'close_reason': t.close_reason, 'strategy': t.strategy}
        for t in engine.trades
    ]
    ext = extended_metrics(trades, engine.equity_series, base_metrics['pnl'], base_metrics['max_dd'])
    ext['regime'] = regime_name
    ext['seed'] = seed
    ext['equity_curve'] = engine.equity_series  # keep for monte carlo later
    ext['trades_list'] = trades
    return ext


# ════════════════════════════════════════════════════════════════════
#  5. 50-SEED MULTI-REGIME VALIDATOR
# ════════════════════════════════════════════════════════════════════

# 50 seeds (deterministic, no overlaps with v38-v62's 12-seed set)
SEEDS_50 = [
    2024, 7, 42, 1337, 99, 555, 31337, 8, 1234, 7777, 2025, 314,   # original 12
    1, 2, 3, 5, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43,            # small primes
    47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97, 101, 103,          # more primes
    109, 113, 127, 131, 137, 139, 149, 151, 157, 163, 167, 173     # yet more primes — drop 2 extras below
][:50]
assert len(SEEDS_50) == 50, f"Need 50 seeds, got {len(SEEDS_50)}"

REGIMES_ALL = ['BULL', 'BEAR', 'SIDE', 'HIGHVOL', 'LOWVOL', 'MIXED']


def aggregate_seeds(per_seed_metrics: List[Dict]) -> Dict:
    """Aggregate metrics across seeds for ONE regime (or one config).

    Computes mean, std, min, max, and stability indicators.
    Handles inf values (Sortino can be inf when no downside).
    """
    if not per_seed_metrics:
        return {}

    def _safe(vals, fn):
        clean = [v if math.isfinite(v) else (1e6 if v > 0 else -1e6) for v in vals]
        try:
            return fn(clean)
        except (statistics.StatisticsError, ValueError):
            return 0

    keys = ['trades', 'wr', 'pnl', 'pf', 'sharpe', 'sortino', 'max_dd',
            'calmar', 'recovery', 'avg_r', 'max_consec_loss', 'avg_hold_s']
    agg = {}
    for k in keys:
        vals = [m[k] for m in per_seed_metrics]
        # For mean, keep inf as a large finite for safety
        clean_vals = [v if math.isfinite(v) else (1e6 if v > 0 else -1e6) for v in vals]
        agg[f'{k}_mean'] = statistics.mean(clean_vals)
        agg[f'{k}_std'] = _safe(vals, lambda c: statistics.stdev(c)) if len(vals) > 1 else 0
        agg[f'{k}_min'] = min(clean_vals)
        agg[f'{k}_max'] = max(clean_vals)
        # Coefficient of variation — measures stability (lower is better)
        mean_v = agg[f'{k}_mean']
        agg[f'{k}_cv'] = abs(agg[f'{k}_std'] / mean_v) if abs(mean_v) > 1e-9 else 0

    # Profitable seeds %
    agg['profitable_seeds_pct'] = sum(1 for m in per_seed_metrics if m['pnl'] > 0) / len(per_seed_metrics) * 100
    # Seeds with WR >= 60%
    agg['wr_above_60_pct'] = sum(1 for m in per_seed_metrics if m['wr'] >= 60) / len(per_seed_metrics) * 100
    # Seeds with MaxDD <= 0.35% (user's hard limit)
    agg['maxdd_under_35_pct'] = sum(1 for m in per_seed_metrics if m['max_dd'] <= 0.35) / len(per_seed_metrics) * 100
    # Worst seed P&L (used for risk assessment)
    agg['worst_seed_pnl'] = min(m['pnl'] for m in per_seed_metrics)
    agg['best_seed_pnl'] = max(m['pnl'] for m in per_seed_metrics)
    # PnL std as % of mean PnL — stability indicator
    if abs(agg['pnl_mean']) > 1e-9:
        agg['pnl_stability'] = 1 - min(1, abs(agg['pnl_std'] / agg['pnl_mean']))
    else:
        agg['pnl_stability'] = 0

    # Per-seed P&L list (for traceability)
    agg['pnl_per_seed'] = [m['pnl'] for m in per_seed_metrics]
    agg['n_seeds'] = len(per_seed_metrics)

    return agg


def composite_for_aggregate(agg: Dict, regime_aggs: Dict = None) -> float:
    """Compute composite score from aggregated metrics.

    regime_aggs: dict of {regime_name: agg} — if provided, regime_stability
                 is computed as the fraction of regimes with P&L > 0.
    """
    metrics = {
        'pf': agg.get('pf_mean', 0),
        'sharpe': agg.get('sharpe_mean', 0),
        'sortino': agg.get('sortino_mean', 0),
        'calmar': agg.get('calmar_mean', 0),
        'recovery': agg.get('recovery_mean', 0),
        'wr': agg.get('wr_mean', 0),
        'avg_r': agg.get('avg_r_mean', 0),
        'max_dd': agg.get('max_dd_mean', 0),
    }
    # Seed stability: % of seeds profitable (0-1)
    seed_stability = agg.get('profitable_seeds_pct', 0) / 100
    # Regime stability: % of regimes profitable (0-1)
    if regime_aggs:
        regime_stability = sum(1 for r in regime_aggs.values() if r.get('pnl_mean', 0) > 0) / len(regime_aggs)
    else:
        regime_stability = 1.0  # default if no multi-regime data

    return composite_score(metrics, regime_stability, seed_stability)


# ════════════════════════════════════════════════════════════════════
#  6. ACCEPTANCE GATE (PRIORITY #10 — GOLDEN RULE)
# ════════════════════════════════════════════════════════════════════

def acceptance_gate(candidate_agg_by_regime: Dict, baseline_agg_by_regime: Dict,
                    candidate_name: str, baseline_name: str = 'v62a') -> Dict:
    """Determine whether a candidate is ACCEPTED vs baseline.

    CRITERIA (all must be met):
      1. Composite score (MIXED regime) > baseline composite score
      2. No regime collapse: candidate P&L > -10 in EVERY regime (baseline floor)
      3. Profitable seeds % >= baseline's profitable seeds %
      4. MaxDD mean <= 0.35% (user's hard limit) in EVERY regime
      5. P&L std (MIXED) <= baseline's P&L std * 1.20 (no fragility increase)

    Returns dict with verdict + breakdown.
    """
    c_mixed = candidate_agg_by_regime.get('MIXED', {})
    b_mixed = baseline_agg_by_regime.get('MIXED', {})

    c_comp = composite_for_aggregate(c_mixed, candidate_agg_by_regime)
    b_comp = composite_for_aggregate(b_mixed, baseline_agg_by_regime)

    checks = {}

    # 1. Composite score
    checks['composite_score'] = {
        'candidate': c_comp, 'baseline': b_comp,
        'pass': c_comp > b_comp,
        'delta': c_comp - b_comp,
    }

    # 2. No regime collapse
    collapsed_regimes = []
    for r, agg in candidate_agg_by_regime.items():
        if agg.get('pnl_mean', 0) < -10:
            collapsed_regimes.append((r, agg['pnl_mean']))
    checks['no_collapse'] = {
        'collapsed_regimes': collapsed_regimes,
        'pass': len(collapsed_regimes) == 0,
    }

    # 3. Profitable seeds %
    c_prof = c_mixed.get('profitable_seeds_pct', 0)
    b_prof = b_mixed.get('profitable_seeds_pct', 0)
    checks['profitable_seeds'] = {
        'candidate': c_prof, 'baseline': b_prof,
        'pass': c_prof >= b_prof,
    }

    # 4. MaxDD <= 0.35% in EVERY regime
    dd_violations = []
    for r, agg in candidate_agg_by_regime.items():
        if agg.get('max_dd_mean', 0) > 0.35:
            dd_violations.append((r, agg['max_dd_mean']))
    checks['maxdd_limit'] = {
        'violations': dd_violations,
        'pass': len(dd_violations) == 0,
    }

    # 5. P&L std (MIXED) <= baseline * 1.20
    c_std = c_mixed.get('pnl_std', 0)
    b_std = b_mixed.get('pnl_std', 0)
    checks['stability'] = {
        'candidate_std': c_std, 'baseline_std': b_std,
        'pass': c_std <= b_std * 1.20,
    }

    all_pass = all(c['pass'] for c in checks.values())

    return {
        'candidate': candidate_name,
        'baseline': baseline_name,
        'verdict': 'ACCEPTED' if all_pass else 'REJECTED',
        'composite_delta': c_comp - b_comp,
        'checks': checks,
    }


# ════════════════════════════════════════════════════════════════════
#  7. PRINT FORMATTERS
# ════════════════════════════════════════════════════════════════════

def print_regime_table(name: str, agg_by_regime: Dict):
    """Print a per-regime summary table for one config."""
    print(f"\n{'='*150}")
    print(f"  {name} — per-regime performance (50 seeds × 6 regimes = 300 runs)")
    print(f"{'='*150}")
    print(f"{'Regime':<10} {'Trades':<8} {'WR%':<14} {'P&L':<16} {'PF':<8} {'Sharpe':<10} {'Sortino':<10} {'MaxDD%':<10} {'Calmar':<10} {'Recovery':<10} {'Profit%':<10}")
    print('-' * 150)
    for r in REGIMES_ALL:
        a = agg_by_regime.get(r, {})
        if not a:
            print(f"{r:<10}  (no data)")
            continue
        print(f"{r:<10} {a['trades_mean']:<8.0f} {a['wr_mean']:.1f}±{a['wr_std']:.1f}{'':>3} "
              f"{a['pnl_mean']:+.2f}±{a['pnl_std']:.0f}{'':>4} {a['pf_mean']:.2f}{'':>4} "
              f"{a['sharpe_mean']:+.2f}{'':>5} {a['sortino_mean']:+.2f}{'':>5} "
              f"{a['max_dd_mean']:.2f}{'':>5} {a['calmar_mean']:.1f}{'':>5} "
              f"{a['recovery_mean']:.1f}{'':>5} {a['profitable_seeds_pct']:.0f}%")
    # Compute composite on MIXED
    comp = composite_for_aggregate(agg_by_regime.get('MIXED', {}), agg_by_regime)
    regime_stability = sum(1 for r in agg_by_regime.values() if r.get('pnl_mean', 0) > 0) / len(agg_by_regime)
    print(f"\n  Composite (MIXED): {comp:.2f}/100   Regime stability: {regime_stability*100:.0f}% regimes profitable")


def print_per_seed_pnl(name: str, agg_by_regime: Dict, n_show: int = 12):
    """Print per-seed P&L breakdown for the MIXED regime."""
    mixed = agg_by_regime.get('MIXED', {})
    if not mixed or 'pnl_per_seed' not in mixed: return
    pnls = mixed['pnl_per_seed'][:n_show]
    print(f"  {name} MIXED per-seed P&L: " + " ".join(f"{p:+6.1f}" for p in pnls))


# ════════════════════════════════════════════════════════════════════
#  8. MAIN — Run v62a baseline across all regimes
# ════════════════════════════════════════════════════════════════════


def _sanitize_json(obj):
    """Recursively convert inf/nan to safe values for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, float):
        if math.isinf(obj):
            return 1e9 if obj > 0 else -1e9
        if math.isnan(obj):
            return 0
        return obj
    return obj


def _json_default(obj):
    """Fallback JSON encoder for non-serializable types."""
    if isinstance(obj, float):
        if math.isinf(obj): return 1e9 if obj > 0 else -1e9
        if math.isnan(obj): return 0
        return obj
    try:
        return float(obj)
    except Exception:
        return str(obj)

V62A_CONFIG = v61b_base(pyramid_pct=0.75)  # current production champion


def main():
    """Run v62a across 6 regimes × N seeds to establish robustness baseline."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('regime', choices=REGIMES_ALL + ['ALL'], default='ALL', nargs='?')
    ap.add_argument('--seeds', type=int, default=50, help='Number of seeds (default 50)')
    ap.add_argument('--config', default='v62a', choices=['v62a', 'v61b'])
    ap.add_argument('--out', default='/tmp/v62a_robustness.json')
    ap.add_argument('--aggregate-only', action='store_true')
    args = ap.parse_args()

    if args.aggregate_only:
        if not os.path.exists(args.out):
            print(f"No results at {args.out}"); sys.exit(1)
        with open(args.out) as f: all_results = json.load(f)
        agg_by_regime = {}
        for regime, seed_results in all_results.items():
            agg_by_regime[regime] = aggregate_seeds(seed_results)
        print_regime_table(f"v62a (baseline)", agg_by_regime)
        return agg_by_regime

    # Pick config
    if args.config == 'v62a':
        cfg = V62A_CONFIG; cfg_name = 'v62a'
    else:
        cfg = v61b_base(); cfg_name = 'v61b'

    seeds = SEEDS_50[:args.seeds]
    regimes = REGIMES_ALL if args.regime == 'ALL' else [args.regime]

    # Load existing results if present (incremental)
    all_results = {}
    if os.path.exists(args.out):
        with open(args.out) as f: all_results = json.load(f)

    for regime in regimes:
        if regime not in all_results: all_results[regime] = {}
        for seed in seeds:
            if str(seed) in all_results[regime]:
                continue  # skip already-done
            print(f"[{time.strftime('%H:%M:%S')}] {cfg_name} | {regime} | seed {seed}...", flush=True)
            t0 = time.time()
            try:
                m = run_one_regime(cfg, regime, seed)
                # Strip non-serializable parts for the JSON dump
                m_dump = {k: v for k, v in m.items() if k not in ('equity_curve', 'trades_list')}
                # Sanitize inf/nan for JSON (Sortino can be inf)
                m_dump = _sanitize_json(m_dump)
                all_results[regime][str(seed)] = m_dump
                # Periodic save
                with open(args.out, 'w') as f: json.dump(all_results, f, indent=2, default=_json_default)
                print(f"   done in {time.time()-t0:.1f}s — P&L {m['pnl']:+.2f}, WR {m['wr']:.1f}%, DD {m['max_dd']:.2f}%, "
                      f"PF {m['pf']:.2f}, Sharpe {m['sharpe']:+.2f}, Sortino {m['sortino']:+.2f}, "
                      f"trades {m['trades']}, avgR {m['avg_r']:+.2f}, hold {m['avg_hold_s']/60:.1f}min", flush=True)
            except Exception as e:
                print(f"   ERROR: {e}", flush=True)
                import traceback; traceback.print_exc()

    # Aggregate and print
    agg_by_regime = {}
    for regime, seed_results in all_results.items():
        if seed_results:
            agg_by_regime[regime] = aggregate_seeds(list(seed_results.values()))
    print_regime_table(f"{cfg_name} (baseline)", agg_by_regime)
    for _name, _agg in [(cfg_name, agg_by_regime)]:
        print_per_seed_pnl(_name, _agg)

    return agg_by_regime


if __name__ == "__main__":
    main()
