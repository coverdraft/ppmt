#!/usr/bin/env python3
"""
v57 — Refine around v56d champion (Profit 67%, MaxDD 0.17%, WR 79.4%).

v56d baseline: adaptive ATR sizing (0.5x if ATR<0.6%), all v53h params

Variants:
  v57a — ATR threshold 0.65 (more aggressive halving)
  v57b — ATR threshold 0.55 (less aggressive, only edge cases)
  v57c — Size mult 0.6 (less aggressive halving)
  v57d — Size mult 0.4 (more aggressive halving)
  v57e — Combined: ATR 0.65 + mult 0.5
  v57f — Combined: ATR 0.6 + mult 0.6
  v57g — Combined: ATR 0.6 + warmup 30min
  v57h — Tiered: 0.4x if ATR<0.6, 0.7x if ATR<0.8, 1.0x otherwise
  v57i — ATR 0.6 + B size 0.15 (push B with adaptive protection)
  v57j — ATR 0.6 + trail 0.25 (tighter trail with adaptive size)
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v51_push import make_strategies_v51
from v53_push import EngineSimV53, make_v53_config
from v56_push import EngineSimV56


def v56d_base(adapt_kwargs=None, strat_kwargs=None, **overrides):
    """v56d config with optional overrides."""
    strat_kwargs = strat_kwargs or {'b_pos_size': 0.125}
    cfg = make_v53_config(
        strat_kwargs=strat_kwargs,
        partial1_pct=0.05, partial2_pct=0.10,
        partial3_r=1.25, partial3_pct=0.15,
    )
    # Default v56d adaptive sizing
    cfg['adaptive_atr_threshold_pct'] = 0.6
    cfg['adaptive_atr_size_mult'] = 0.5
    if adapt_kwargs:
        cfg.update(adapt_kwargs)
    cfg.update(overrides)
    return cfg


# Tiered adaptive sizing requires custom engine
class EngineSimV57(EngineSimV56):
    """V57: supports tiered adaptive sizing."""
    def __init__(self, config, name):
        super().__init__(config, name)
        self.tiered_atr = self.config.get('tiered_atr', None)  # [(threshold, mult), ...]

    def _try_strategy_a(self, sym, prices, tick):
        if self._check_session_kill(tick): return
        cfg = self.config.get('A', {})
        if not cfg.get('enabled', True): return
        if tick - self.last_signal_tick['A'] < 10: return
        if self.strategy_pos_count['A'] >= cfg['max_pos']: return
        if len(prices) < 60: return
        if sym in self.positions: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return
        recent = prices[-30:]
        momentum = ((recent[-1] - recent[0]) / recent[0]) * 100
        if abs(momentum) < cfg['momentum_min']: return
        rsi = v40.v38.computeRSI(prices, 14)
        if rsi < cfg['rsi_min'] or rsi > cfg['rsi_max']: return
        atr = v40.v38.computeATR(prices, 60)
        atr_pct = atr / prices[-1] * 100
        if self.config.get('atr_floor_pct') is not None and atr_pct < self.config['atr_floor_pct']:
            self.atr_filter_skips += 1
            return
        direction = 'LONG' if momentum > 0 else 'SHORT'
        if not self._trend_allows(direction, prices): return
        eff_cfg = dict(cfg)
        # Tiered adaptive sizing
        if self.tiered_atr is not None:
            mult = 1.0
            for threshold, m in self.tiered_atr:
                if atr_pct < threshold:
                    mult = m
                    break
            eff_cfg['pos_size_pct'] = cfg['pos_size_pct'] * mult
        else:
            # Default adaptive ATR sizing
            if self.adaptive_atr_threshold_pct is not None and atr_pct < self.adaptive_atr_threshold_pct:
                eff_cfg['pos_size_pct'] = cfg['pos_size_pct'] * self.adaptive_atr_size_mult
        # Warmup
        if self.warmup_ticks is not None and tick < self.warmup_ticks:
            eff_cfg['pos_size_pct'] = eff_cfg.get('pos_size_pct', cfg['pos_size_pct']) * self.warmup_size_mult
        self._open_position_v40(sym, direction, 'A', prices[-1], atr, eff_cfg, tick)
        self.last_signal_tick['A'] = tick

    def _try_strategy_b(self, sym, prices, tick):
        if self._check_session_kill(tick): return
        cfg = self.config.get('B', {})
        if not cfg.get('enabled', True): return
        if tick - self.last_signal_tick['B'] < 20: return
        if self.strategy_pos_count['B'] >= cfg['max_pos']: return
        if len(prices) < 60: return
        if sym in self.positions: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return
        rsi = v40.v38.computeRSI(prices, 14)
        if cfg['rsi_lo'] <= rsi <= cfg['rsi_hi']: return
        atr = v40.v38.computeATR(prices, 60)
        atr_pct = atr / prices[-1] * 100
        if self.config.get('atr_floor_pct') is not None and atr_pct < self.config['atr_floor_pct']:
            self.atr_filter_skips += 1
            return
        direction = 'LONG' if rsi < 50 else 'SHORT'
        eff_cfg = dict(cfg)
        # Tiered adaptive sizing
        if self.tiered_atr is not None:
            mult = 1.0
            for threshold, m in self.tiered_atr:
                if atr_pct < threshold:
                    mult = m
                    break
            eff_cfg['pos_size_pct'] = cfg['pos_size_pct'] * mult
        else:
            if self.adaptive_atr_threshold_pct is not None and atr_pct < self.adaptive_atr_threshold_pct:
                eff_cfg['pos_size_pct'] = cfg['pos_size_pct'] * self.adaptive_atr_size_mult
        # RSI-scaled B size
        if (self.rsi_scaled_b_threshold_extreme is not None
                and self.rsi_scaled_b_size is not None
                and (rsi < self.rsi_scaled_b_threshold_extreme[0]
                     or rsi > self.rsi_scaled_b_threshold_extreme[1])):
            eff_cfg['pos_size_pct'] = self.rsi_scaled_b_size
        # Warmup
        if self.warmup_ticks is not None and tick < self.warmup_ticks:
            eff_cfg['pos_size_pct'] = eff_cfg.get('pos_size_pct', cfg['pos_size_pct']) * self.warmup_size_mult
        self._open_position_v40(sym, direction, 'B', prices[-1], atr, eff_cfg, tick)
        self.last_signal_tick['B'] = tick


CONFIGS = {
    # v56d baseline (control)
    'v56d_baseline': v56d_base(),
    # v57a — ATR threshold 0.65 (more aggressive halving)
    'v57a_atr_0.65': v56d_base(adapt_kwargs={'adaptive_atr_threshold_pct': 0.65}),
    # v57b — ATR threshold 0.55 (less aggressive)
    'v57b_atr_0.55': v56d_base(adapt_kwargs={'adaptive_atr_threshold_pct': 0.55}),
    # v57c — Size mult 0.6 (less aggressive halving)
    'v57c_mult_0.6': v56d_base(adapt_kwargs={'adaptive_atr_size_mult': 0.6}),
    # v57d — Size mult 0.4 (more aggressive halving)
    'v57d_mult_0.4': v56d_base(adapt_kwargs={'adaptive_atr_size_mult': 0.4}),
    # v57e — Combined: ATR 0.65 + mult 0.5
    'v57e_atr065_mult05': v56d_base(adapt_kwargs={'adaptive_atr_threshold_pct': 0.65, 'adaptive_atr_size_mult': 0.5}),
    # v57f — Combined: ATR 0.6 + mult 0.6
    'v57f_atr06_mult06': v56d_base(adapt_kwargs={'adaptive_atr_threshold_pct': 0.6, 'adaptive_atr_size_mult': 0.6}),
    # v57g — Combined: ATR 0.6 + warmup 30min
    'v57g_atr06_warmup': v56d_base(adapt_kwargs={'adaptive_atr_threshold_pct': 0.6}, warmup_ticks=1200, warmup_size_mult=0.5),
    # v57h — Tiered: 0.4x if ATR<0.6, 0.7x if ATR<0.8, 1.0x otherwise
    'v57h_tiered': v56d_base(tiered_atr=[(0.6, 0.4), (0.8, 0.7), (float('inf'), 1.0)]),
    # v57i — ATR 0.6 + B size 0.15
    'v57i_b_size_0.15': v56d_base(strat_kwargs={'b_pos_size': 0.15}),
    # v57j — ATR 0.6 + trail 0.25
    'v57j_trail_0.25': v56d_base(trail_atr=0.25),
}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV57(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
    for tick in range(v40.TOTAL_TICKS):
        for sym in all_prices:
            if tick + 1 < 60: continue
            prices_slice = all_prices[sym][max(0, tick-250):tick+1]
            for engine in engines:
                engine._try_strategy_a(sym, prices_slice, tick)
                engine._try_strategy_b(sym, prices_slice, tick)
                engine._try_strategy_d(sym, prices_slice, tick)
                engine._try_strategy_e(sym, prices_slice, tick)
                engine._check_stops(sym, prices_slice, tick)
        for engine in engines:
            engine.update_equity(all_prices, tick)
    return {engine.name: engine.get_metrics() for engine in engines}


SEEDS_ALL = [2024, 7, 42, 1337, 99, 555, 31337, 8, 1234, 7777, 2025, 314]
RESULTS_FILE = '/tmp/v57_seeds.json'

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
        baseline = 'v56d_baseline'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L ({len(seeds)} seeds):\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*160)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        print("WINNER SELECTION (12 seeds: target ≥75% profitable + WR≥75 + MaxDD<0.3 + AvgR>0)")
        print("=" * 80)
        candidates = [(name, m) for name, m in agg.items()
                      if m['profitable_seeds'] >= 75 and m['wr_mean'] >= 75
                      and m['max_dd_mean'] < 0.3 and m['avg_r_mean'] > 0]
        if candidates:
            candidates.sort(key=lambda x: (x[1]['profitable_seeds'], x[1]['pnl_mean'], x[1]['wr_mean']), reverse=True)
            w = candidates[0]
            print(f"\n🏆 WINNER (12-seed validated): {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f}  Profit {w[1]['profitable_seeds']:.0f}%  AvgR {w[1]['avg_r_mean']:+.3f}  MaxDD {w[1]['max_dd_mean']:.2f}%  PF {w[1]['pf_mean']:.2f}")
        else:
            print("\n  ⚠️ No config met 75%/WR75 criterion. Top 5:")
            ranked = sorted(agg.items(), key=lambda x: (x[1]['profitable_seeds'], x[1]['pnl_mean']), reverse=True)
            for i, (name, m) in enumerate(ranked[:5]):
                print(f"  #{i+1} {name:<32} WR {m['wr_mean']:.1f}%  P&L {m['pnl_mean']:+.2f}  Profit {m['profitable_seeds']:.0f}%  AvgR {m['avg_r_mean']:+.3f}")
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
