#!/usr/bin/env python3
"""
v64 — KILL THE LOSER + NEW ALPHA SOURCES.

Diagnosis of v62a (seed 2024, per-strategy):
  - A (Momentum): 53 trades, WR 75%, P&L -7.51  ← NET LOSER
  - B (Mean Rev):  7 trades, WR 100%, P&L +41.89 ← WORKHORSE via pyramiding
  - D (Squeeze):   0 trades (inert)

The system's P&L is ENTIRELY from B's pyramiding. A drags it down.

THREE PARALLEL PATHS in this experiment:

PATH 1 — KILL A (most aggressive, simplest):
  Disable A entirely. Redistribute A's 30% allocation to B (B goes 25% → 55%).
  B's size stays at 0.20 (already big). Hypothesis: removing A's -7.51 net loss
  should add ~+7.51 to total P&L per session.

PATH 2 — KILL A + SCALE B (compound the win):
  Disable A. Keep B at 0.20 base but with TIERED protection. The 30% extra
  capital just sits in cash (acts as drawdown buffer).

PATH 3 — KILL A + NEW STRATEGY F (Volatility Breakout):
  Disable A. Add Strategy F: enter on ATR spike >2x baseline (volatility
  expansion breakout). Direction = direction of first strong move.
  This is a NEW alpha source orthogonal to B's mean reversion.

PATH 4 — KILL A + Strategy B MEAN REVERSION on SHORTER RSI:
  Current B uses RSI 30/70. Try RSI 25/75 (slightly more selective).
  Bigger size 0.25 (since A's capital is freed).

PATH 5 — KEEP A but reduce size by 50%:
  Don't kill A entirely. A_pos_size 0.050 → 0.025. Test if A still contributes
  negatively at half size (if yes, kill it; if no, scale was the issue).

PATH 6 — CONTROL: v62a untouched (regression check).

EXPECTED OUTCOME:
  If PATH 1 or 2 wins → simplest fix, adopt immediately
  If PATH 3 wins → new alpha source, adopt and develop further
  If PATH 4 wins → B tuning is the lever
  If PATH 5 wins → A was just over-sized, not broken
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy
from collections import deque

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v51_push import make_strategies_v51
from v53_push import EngineSimV53, make_v53_config
from v57_push import EngineSimV57
from v62_push import EngineSimV62, v61b_base


# ─── PATH configs ───────────────────────────────────────────────────
def v64_path1_kill_a():
    """Kill A, redistribute to B (B cash 25% → 55%)."""
    cfg = v61b_base(pyramid_pct=0.75)
    # Disable A
    cfg['A']['enabled'] = False
    # Increase B's effective cash by giving it bigger pos_size
    cfg['B']['pos_size_pct'] = 0.30  # was 0.20 — push B harder with freed capital
    return cfg


def v64_path2_kill_a_cash_buffer():
    """Kill A, B stays at 0.20, freed capital acts as drawdown buffer."""
    cfg = v61b_base(pyramid_pct=0.75)
    cfg['A']['enabled'] = False
    # B unchanged
    return cfg


def v64_path3_kill_a_add_F():
    """Kill A, add Strategy F (Volatility Breakout)."""
    cfg = v61b_base(pyramid_pct=0.75)
    cfg['A']['enabled'] = False
    # Add F config
    cfg['F'] = {
        'enabled': True,
        'atr_spike_mult': 2.0,      # trigger when current ATR > 2x baseline
        'atr_baseline_period': 120, # baseline = avg ATR over 120 ticks
        'momentum_min': 0.3,        # need directional move to enter
        'max_pos': 2,
        'sl_mult': 1.5, 'tp_mult': 1.2,
        'catsl_mult': 4.0,
        'cooldown_min': 45, 'tp_cooldown_min': 45,
        'time_stop': 5400,
        'pos_size_pct': 0.10,
    }
    return cfg


def v64_path4_kill_a_b_rsi25_75():
    """Kill A, B uses RSI 25/75 (more selective) + bigger size 0.25."""
    cfg = v61b_base(pyramid_pct=0.75)
    cfg['A']['enabled'] = False
    cfg['B']['rsi_lo'] = 25
    cfg['B']['rsi_hi'] = 75
    cfg['B']['pos_size_pct'] = 0.25
    return cfg


def v64_path5_a_half_size():
    """Keep A but reduce size 0.050 → 0.025."""
    cfg = v61b_base(pyramid_pct=0.75)
    cfg['A']['pos_size_pct'] = 0.025
    return cfg


def v64_control():
    """v62a baseline (untouched)."""
    return v61b_base(pyramid_pct=0.75)


# ─── V64 Engine: adds Strategy F (Volatility Breakout) ─────────────
class EngineSimV64(EngineSimV62):
    """V64: extends V62 with Strategy F (Volatility Breakout)."""

    def __init__(self, config, name):
        super().__init__(config, name)
        self.last_signal_tick.setdefault('F', 0)
        self.strategy_pos_count.setdefault('F', 0)

    def _try_strategy_f(self, sym, prices, tick):
        """Volatility Breakout: enter when current ATR spikes above baseline."""
        cfg = self.config.get('F', {})
        if not cfg.get('enabled', False): return
        if tick - self.last_signal_tick.get('F', 0) < 30: return  # throttle
        if self.strategy_pos_count.get('F', 0) >= cfg['max_pos']: return
        if len(prices) < cfg['atr_baseline_period'] + 10: return
        if sym in self.positions: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return

        # Current ATR (last 60 ticks)
        current_atr = v40.v38.computeATR(prices, 60)
        if current_atr <= 0: return

        # Baseline ATR (over 120 ticks, excluding last 30 to avoid overlap)
        baseline_end = len(prices) - 30
        baseline_prices = prices[:baseline_end]
        if len(baseline_prices) < cfg['atr_baseline_period']: return
        baseline_atr = v40.v38.computeATR(baseline_prices, cfg['atr_baseline_period'])
        if baseline_atr <= 0: return

        # Spike check: current ATR > mult × baseline
        if current_atr < cfg['atr_spike_mult'] * baseline_atr:
            return

        # Direction: recent momentum
        recent = prices[-30:]
        momentum = ((recent[-1] - recent[0]) / recent[0]) * 100
        if abs(momentum) < cfg['momentum_min']:
            return

        # ATR floor
        atr_pct = current_atr / prices[-1] * 100
        if self.config.get('atr_floor_pct') is not None and atr_pct < self.config['atr_floor_pct']:
            self.atr_filter_skips += 1
            return

        direction = 'LONG' if momentum > 0 else 'SHORT'

        # Apply tiered adaptive sizing (same as A/B)
        eff_cfg = dict(cfg)
        if self.tiered_atr is not None:
            mult = 1.0
            for threshold, m in self.tiered_atr:
                if atr_pct < threshold:
                    mult = m
                    break
            eff_cfg['pos_size_pct'] = cfg['pos_size_pct'] * mult

        self._open_position_v40(sym, direction, 'F', prices[-1], current_atr, eff_cfg, tick)
        self.last_signal_tick['F'] = tick

    def _check_stops(self, sym, prices, tick):
        """Delegate to V62 base for F (uses A/B/C/D-style management)."""
        # For F, treat it like A (same SL/TP/partial logic)
        # Just call super — it uses pos.strategy to look up cfg, F has same keys
        return super()._check_stops(sym, prices, tick)


# ─── Configs ────────────────────────────────────────────────────────
CONFIGS = {
    'v62a_control':           v64_control(),
    'v64_p1_kill_a_b30':      v64_path1_kill_a(),
    'v64_p2_kill_a_buffer':   v64_path2_kill_a_cash_buffer(),
    'v64_p3_kill_a_add_F':    v64_path3_kill_a_add_F(),
    'v64_p4_kill_a_b_rsi25':  v64_path4_kill_a_b_rsi25_75(),
    'v64_p5_a_half_size':     v64_path5_a_half_size(),
}


# ─── Runner ─────────────────────────────────────────────────────────
def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV64(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
    for tick in range(v40.TOTAL_TICKS):
        for sym in all_prices:
            if tick + 1 < 60: continue
            prices_slice = all_prices[sym][max(0, tick-250):tick+1]
            for engine in engines:
                engine._try_strategy_a(sym, prices_slice, tick)
                engine._try_strategy_b(sym, prices_slice, tick)
                engine._try_strategy_d(sym, prices_slice, tick)
                engine._try_strategy_e(sym, prices_slice, tick)
                engine._try_strategy_f(sym, prices_slice, tick)
                engine._check_stops(sym, prices_slice, tick)
        for engine in engines:
            engine.update_equity(all_prices, tick)
    return {engine.name: engine.get_metrics() for engine in engines}


SEEDS_ALL = [2024, 7, 42, 1337, 99, 555, 31337, 8, 1234, 7777, 2025, 314]
RESULTS_FILE = '/tmp/v64_seeds.json'

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'aggregate':
        if not os.path.exists(RESULTS_FILE):
            print(f"No results at {RESULTS_FILE}"); sys.exit(1)
        with open(RESULTS_FILE) as f: all_results = json.load(f)
        seed_results = list(all_results.values())
        seeds = [int(s) for s in all_results.keys()]
        agg = {}
        for name in CONFIGS.keys():
            seed_metrics = [r[name] for r in seed_results if name in r]
            if not seed_metrics: continue
            agg[name] = {
                'wr_mean': statistics.mean(m['wr'] for m in seed_metrics),
                'wr_std': statistics.stdev(m['wr'] for m in seed_metrics) if len(seed_metrics) > 1 else 0,
                'pnl_mean': statistics.mean(m['pnl'] for m in seed_metrics),
                'pnl_std': statistics.stdev(m['pnl'] for m in seed_metrics) if len(seed_metrics) > 1 else 0,
                'pf_mean': statistics.mean(m['pf'] for m in seed_metrics),
                'sharpe_mean': statistics.mean(m['sharpe'] for m in seed_metrics),
                'max_consec_loss_mean': statistics.mean(m['max_consec_loss'] for m in seed_metrics),
                'consistency_mean': statistics.mean(m['consistency'] for m in seed_metrics),
                'avg_r_mean': statistics.mean(m['avg_r'] for m in seed_metrics),
                'trades_mean': statistics.mean(m['trades'] for m in seed_metrics),
                'max_dd_mean': statistics.mean(m['max_dd'] for m in seed_metrics),
                'profitable_seeds': sum(1 for m in seed_metrics if m['pnl'] > 0) / len(seed_metrics) * 100,
                'wr_above_60_seeds': sum(1 for m in seed_metrics if m['wr'] >= 60) / len(seed_metrics) * 100,
                'pnl_per_seed': [m['pnl'] for m in seed_metrics],
            }
        baseline = 'v62a_control'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L ({len(seeds)} seeds):\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*160)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        print("WINNER SELECTION (robustness-first: Profit ≥ 67% + MaxDD ≤ baseline + P&L ≥ baseline)")
        print("=" * 80)
        baseline_pnl = agg[baseline]['pnl_mean']
        baseline_dd = agg[baseline]['max_dd_mean']
        baseline_profit = agg[baseline]['profitable_seeds']
        candidates = [(name, m) for name, m in agg.items()
                      if name != baseline
                      and m['pnl_mean'] >= baseline_pnl  # must match or beat P&L
                      and m['max_dd_mean'] <= baseline_dd + 0.02  # allow MaxDD within 0.02
                      and m['profitable_seeds'] >= baseline_profit]  # must match or beat Profit%
        if candidates:
            candidates.sort(key=lambda x: (x[1]['profitable_seeds'], -x[1]['max_dd_mean'], x[1]['pnl_mean']), reverse=True)
            w = candidates[0]
            print(f"\n🏆 WINNER (12-seed validated, strict robustness): {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f} (vs base {baseline_pnl:+.2f})  Profit {w[1]['profitable_seeds']:.0f}% (vs base {baseline_profit:.0f}%)  MaxDD {w[1]['max_dd_mean']:.2f}% (vs base {baseline_dd:.2f}%)  PF {w[1]['pf_mean']:.2f}")
        else:
            print("\n  ⚠️ No strict winner. Top 5 by P&L:")
            ranked = sorted(agg.items(), key=lambda x: x[1]['pnl_mean'], reverse=True)
            for i, (name, m) in enumerate(ranked[:5]):
                vs_pnl = m['pnl_mean'] - baseline_pnl
                vs_dd = m['max_dd_mean'] - baseline_dd
                vs_profit = m['profitable_seeds'] - baseline_profit
                print(f"  #{i+1} {name:<32} P&L {m['pnl_mean']:+.2f} ({vs_pnl:+.2f})  MaxDD {m['max_dd_mean']:.2f}% ({vs_dd:+.2f})  Profit {m['profitable_seeds']:.0f}% ({vs_profit:+.0f})  Sharpe {m['sharpe_mean']:+.2f}")
    else:
        seed = int(sys.argv[1])
        print(f"Running seed {seed}...", flush=True)
        start = time.time()
        result = run_single_seed(seed)
        elapsed = time.time() - start
        all_results = {}
        if os.path.exists(RESULTS_FILE):
            with open(RESULTS_FILE) as f: all_results = json.load(f)
        for name, m in result.items():
            m['per_strat'] = {k: v for k, v in m['per_strat'].items()}
        all_results[str(seed)] = result
        with open(RESULTS_FILE, 'w') as f: json.dump(all_results, f, indent=2)
        print(f"Seed {seed} done in {elapsed:.1f}s. P&L: " + ", ".join(f"{n}={m['pnl']:+.0f}" for n, m in result.items()), flush=True)
