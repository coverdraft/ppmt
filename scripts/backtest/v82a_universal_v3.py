#!/usr/bin/env python3
"""
v82a — UNIVERSAL ENGINE v3: tune trend filter 0.05 → 0.10 + RSI filter validation.

Builds on v81 (5/8 perfiles rentables). Two changes:

CHANGE 1 (simulador): Trend filter threshold 0.05 → 0.10
  - v81 F1 used ±0.05% slope gate on SMA100. Too tight: blocked good trades in MIXED.
  - MIXED regressed -22 → -91 from v67 → v81 (mostly because of this filter).
  - v82a loosens to ±0.10%. Should recover MIXED P&L without breaking BULL/MEME.

CHANGE 2 (TypeScript engine, validated separately): Fix RSI bug in Strategy A
  - paper-trading-engine.ts line 1130: computeRSI(prices, 14) used undefined `prices`.
  - Filter was inert for ~30 versions in the TS engine.
  - Fix: computeRSI(hist.map(h => h.price), 14).
  - NOTE: The Python simulator (v40_push.py) already had the RSI filter active, so this
  - change cannot be validated by this backtest. It will be validated when the TS engine
  - runs in the browser with real data.
  - Expected impact on TS engine: MIXED/HIGHVOL improve (filter blocks bad momentum entries).

Validation: 12 seeds × 8 profiles = 96 runs.
Compare vs v81 (RESULTS_FILE = /tmp/v81_universal.json).
"""
import sys, os, json, random, statistics, math, time
from copy import deepcopy
from collections import defaultdict
sys.path.insert(0, '/home/z/my-project/scripts')
sys.path.insert(0, '/home/z/my-project/scripts/backtest')

import v40_push as v40
from v62_push import EngineSimV62, v61b_base
import v38_push_v37e as v38
from v80_direction_token_test import (
    gen_profile_prices, PROFILES, SEEDS, RESULTS_FILE as V80_RESULTS
)
from v81_universal_v2 import EngineSimV81, v80_config, compute_sma_slope


# ─────────────────────────────────────────────────────────────────────
# v82a ENGINE — v81 + trend filter threshold 0.10
# ─────────────────────────────────────────────────────────────────────

# Tunable threshold for ablation testing
TREND_FILTER_THRESHOLD = 0.10  # v82a: was 0.05 in v81


class EngineSimV82a(EngineSimV81):
    """v82a: v81 + trend filter threshold 0.10 (was 0.05)."""

    def _try_strategy_b(self, sym, prices, tick):
        """Strategy B: Mean Reversion + TREND FILTER (F1, v82a tuned) + dynamic ATR floor (F2)."""
        cfg = self.config.get('B', {})
        if not cfg.get('enabled', True):
            return
        if tick - self.last_signal_tick.get('B', 0) < 20:
            return
        if self.strategy_pos_count.get('B', 0) >= cfg.get('max_pos', 1):
            return
        if len(prices) < 100:  # need 100 for SMA100 slope
            return
        if sym in self.positions:
            return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]:
            return

        rsi = v38.computeRSI(prices, 14)
        if cfg.get('rsi_lo', 30) <= rsi <= cfg.get('rsi_hi', 70):
            return

        atr = v38.computeATR(prices, 60)
        if atr <= 0:
            return
        atr_pct = atr / prices[-1] * 100

        # F2: Dynamic ATR floor — 0.40% (same as v81)
        if atr_pct < 0.40:
            return

        direction = 'LONG' if rsi < 50 else 'SHORT'

        # F1 v82a: TREND FILTER with threshold 0.10 (was 0.05 in v81)
        sma_slope = compute_sma_slope(prices, period=100, lookback=10)
        if direction == 'LONG' and sma_slope < -TREND_FILTER_THRESHOLD:
            self.atr_filter_skips = getattr(self, 'atr_filter_skips', 0) + 1
            return  # don't long into a downtrend
        if direction == 'SHORT' and sma_slope > TREND_FILTER_THRESHOLD:
            self.atr_filter_skips = getattr(self, 'atr_filter_skips', 0) + 1
            return  # don't short into an uptrend

        # F5: Regime-aware B size (same as v81)
        b_base_size = 0.15 if atr_pct > 1.2 else 0.30

        # F2: Extended tiered sizing (same as v81)
        if atr_pct < 0.40:
            size_mult = 0.3
        elif atr_pct < 0.60:
            size_mult = 0.5
        elif atr_pct < 0.80:
            size_mult = 0.7
        else:
            size_mult = 1.0

        eff_cfg = cfg.copy()
        eff_cfg['pos_size_pct'] = b_base_size * size_mult
        eff_cfg['sl_mult'] = 1.5
        eff_cfg['tp_mult'] = 1.2
        eff_cfg['catsl_mult'] = 2.5

        self._open_position_v40(sym, direction, 'B', prices[-1], atr, eff_cfg, tick)
        self.last_signal_tick['B'] = tick

        pos = self.positions.get(sym)
        if pos:
            pos.current_sl = pos.entry_price - atr * 1.5 if direction == 'LONG' else pos.entry_price + atr * 1.5
            pos.current_tp = pos.entry_price + atr * 1.2 if direction == 'LONG' else pos.entry_price - atr * 1.2
            pos.catastrophic_sl = pos.entry_price - atr * 2.5 if direction == 'LONG' else pos.entry_price + atr * 2.5
            pos.initial_atr = atr
            pos.initial_sl_distance = atr * 1.5
            pos.lock_done = False
            pos.partial_done = False
            pos.partial1_done = False
            pos.partial2_done = False
            pos.partial3_done = False
            pos.pyramid_done = False
            pos.trail_active = False
            pos.max_favorable_price = pos.entry_price
            pos.trail_atr = atr


def v82a_config():
    """v82a config: same as v81 but trend filter threshold = 0.10."""
    cfg = v80_config()
    cfg['v82a_features'] = {
        'trend_filter_threshold': TREND_FILTER_THRESHOLD,
        'rsi_filter_a_active': True,  # always was in Python sim
        'note': 'trend filter 0.05 → 0.10 to recover MIXED P&L',
    }
    return cfg


# ─────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────

RESULTS_FILE = '/tmp/v82a_universal.json'
V81_RESULTS = '/tmp/v81_universal.json'


def run_seed_profile(seed, profile, n_tokens=10):
    rng = random.Random(seed)
    base = 1.0 * (1 + rng.uniform(-0.3, 0.3))
    all_prices = {f"TOK{i:02d}": gen_profile_prices(v40.TOTAL_TICKS, base * (1 + rng.uniform(-0.2, 0.2)), rng, profile)
                  for i in range(n_tokens)}

    engine = EngineSimV82a(deepcopy(v82a_config()), f'v82a_{profile}')
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
    m['trades_long'] = engine.trades_long
    m['trades_short'] = engine.trades_short
    m['wins_long'] = engine.wins_long
    m['wins_short'] = engine.wins_short
    m['wr_long'] = (engine.wins_long / engine.trades_long * 100) if engine.trades_long else 0
    m['wr_short'] = (engine.wins_short / engine.trades_short * 100) if engine.trades_short else 0
    m['pnl_long_strat'] = dict(engine.pnl_long_strat)
    m['pnl_short_strat'] = dict(engine.pnl_short_strat)
    m['trades_long_strat'] = dict(engine.trades_long_strat)
    m['trades_short_strat'] = dict(engine.trades_short_strat)
    return m


def aggregate():
    if not os.path.exists(RESULTS_FILE):
        print(f"No results at {RESULTS_FILE}")
        sys.exit(1)
    with open(RESULTS_FILE) as f:
        all_results = json.load(f)

    v81_results = {}
    if os.path.exists(V81_RESULTS):
        with open(V81_RESULTS) as f:
            v81_results = json.load(f)

    print("=" * 170)
    print(f"{'Profile':<10} {'Ver':<6} {'Trades':<8} {'WR%':<8} {'P&L':<12} {'PF':<7} {'MaxDD%':<8} {'Profit%':<10} {'L/S':<10} {'L P&L':<12} {'S P&L':<12} {'L WR%':<7} {'S WR%':<7}")
    print("=" * 170)
    summary = []
    for profile in PROFILES:
        # v81 baseline
        v81_seeds = [r for r in v81_results.values() if profile in r]
        if v81_seeds:
            ms = [r[profile] for r in v81_seeds]
            v81_pnl = statistics.mean(m['pnl'] for m in ms)
            v81_profit = sum(1 for m in ms if m['pnl'] > 0) / len(ms) * 100
            v81_wr = statistics.mean(m['wr'] for m in ms)
            v81_maxdd = statistics.mean(m['max_dd'] for m in ms)
            v81_pf = statistics.mean(m['pf'] for m in ms)
            v81_trades = statistics.mean(m['trades'] for m in ms)
            v81_l_t = statistics.mean(m['trades_long'] for m in ms)
            v81_s_t = statistics.mean(m['trades_short'] for m in ms)
            v81_l_pnl = statistics.mean(m['pnl_long'] for m in ms)
            v81_s_pnl = statistics.mean(m['pnl_short'] for m in ms)
            v81_l_wr = statistics.mean(m['wr_long'] for m in ms)
            v81_s_wr = statistics.mean(m['wr_short'] for m in ms)
            print(f"{profile:<10} {'v81':<6} {v81_trades:<8.0f} {v81_wr:<8.1f} {v81_pnl:<+12.2f} {v81_pf:<7.2f} {v81_maxdd:<8.2f} {v81_profit:<10.0f} {int(v81_l_t)}/{int(v81_s_t):<9} {v81_l_pnl:<+12.2f} {v81_s_pnl:<+12.2f} {v81_l_wr:<7.1f} {v81_s_wr:<7.1f}")
        # v82a
        v82_seeds = [r for r in all_results.values() if profile in r]
        if v82_seeds:
            ms = [r[profile] for r in v82_seeds]
            v82_pnl = statistics.mean(m['pnl'] for m in ms)
            v82_profit = sum(1 for m in ms if m['pnl'] > 0) / len(ms) * 100
            v82_wr = statistics.mean(m['wr'] for m in ms)
            v82_maxdd = statistics.mean(m['max_dd'] for m in ms)
            v82_pf = statistics.mean(m['pf'] for m in ms)
            v82_trades = statistics.mean(m['trades'] for m in ms)
            v82_l_t = statistics.mean(m['trades_long'] for m in ms)
            v82_s_t = statistics.mean(m['trades_short'] for m in ms)
            v82_l_pnl = statistics.mean(m['pnl_long'] for m in ms)
            v82_s_pnl = statistics.mean(m['pnl_short'] for m in ms)
            v82_l_wr = statistics.mean(m['wr_long'] for m in ms)
            v82_s_wr = statistics.mean(m['wr_short'] for m in ms)
            delta = v82_pnl - v81_pnl if v81_seeds else 0
            marker = "✅" if v82_pnl > v81_pnl else "❌"
            print(f"{profile:<10} {'v82a':<6} {v82_trades:<8.0f} {v82_wr:<8.1f} {v82_pnl:<+12.2f} {v82_pf:<7.2f} {v82_maxdd:<8.2f} {v82_profit:<10.0f} {int(v82_l_t)}/{int(v82_s_t):<9} {v82_l_pnl:<+12.2f} {v82_s_pnl:<+12.2f} {v82_l_wr:<7.1f} {v82_s_wr:<7.1f}  {marker} Δ{delta:+.2f}")
            summary.append({
                'profile': profile,
                'v81_pnl': v81_pnl if v81_seeds else 0,
                'v82_pnl': v82_pnl,
                'delta': delta,
                'v81_profit': v81_profit if v81_seeds else 0,
                'v82_profit': v82_profit,
                'v81_wr': v81_wr if v81_seeds else 0,
                'v82_wr': v82_wr,
                'v81_maxdd': v81_maxdd if v81_seeds else 0,
                'v82_maxdd': v82_maxdd,
            })
        print("-" * 170)

    # Verdict
    print("\n" + "=" * 100)
    print("VEREDICTO v82a vs v81 (por perfil)")
    print("=" * 100)
    improved = 0
    regressed = 0
    for s in summary:
        marker_p = "✅" if s['v82_pnl'] > 0 else "❌"
        marker_d = "✅" if s['delta'] > 0 else "❌"
        if s['delta'] > 0:
            improved += 1
        elif s['delta'] < -10:  # significant regression
            regressed += 1
        print(f"  {marker_p} {marker_d} {s['profile']:<10}  v81 {s['v81_pnl']:+8.2f} ({s['v81_profit']:3.0f}% profit)  →  v82a {s['v82_pnl']:+8.2f} ({s['v82_profit']:3.0f}% profit)  Δ {s['delta']:+8.2f}")

    print(f"\nResumen: {improved}/8 mejoraron, {regressed}/8 regresaron significativamente")

    # Save summary
    with open('/tmp/v82a_summary.json', 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved to /tmp/v82a_summary.json")


def run_one(seed, profile):
    print(f"  Running seed {seed} × {profile}...", flush=True)
    start = time.time()
    result = run_seed_profile(seed, profile)
    elapsed = time.time() - start
    print(f"    done in {elapsed:.1f}s — P&L {result['pnl']:+.2f}, trades {result['trades']}, L/S={result['trades_long']}/{result['trades_short']}", flush=True)
    return result


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'aggregate':
        aggregate()
    elif len(sys.argv) > 2 and sys.argv[1] == 'one':
        seed = int(sys.argv[2])
        profile = sys.argv[3]
        result = run_one(seed, profile)
        all_results = {}
        if os.path.exists(RESULTS_FILE):
            with open(RESULTS_FILE) as f:
                all_results = json.load(f)
        all_results.setdefault(str(seed), {})[profile] = result
        with open(RESULTS_FILE, 'w') as f:
            json.dump(all_results, f, indent=2)
        print(f"  Saved seed {seed} × {profile}")
    elif len(sys.argv) > 1 and sys.argv[1] == 'all':
        all_results = {}
        if os.path.exists(RESULTS_FILE):
            with open(RESULTS_FILE) as f:
                all_results = json.load(f)
        for seed in SEEDS:
            for profile in PROFILES:
                key = str(seed)
                if key in all_results and profile in all_results[key]:
                    print(f"  Skipping seed {seed} × {profile} (already done)")
                    continue
                result = run_one(seed, profile)
                all_results.setdefault(key, {})[profile] = result
                with open(RESULTS_FILE, 'w') as f:
                    json.dump(all_results, f, indent=2)
        print("\n\n=== ALL DONE — aggregating ===\n")
        aggregate()
    else:
        print("Usage:")
        print("  python v82a_universal_v3.py all")
        print("  python v82a_universal_v3.py one <seed> <profile>")
        print("  python v82a_universal_v3.py aggregate")
