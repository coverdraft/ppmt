#!/usr/bin/env python3
"""
v56 — Regime-adaptive sizing + session kill switch + new ideas.

v53h baseline: WR 79.4%, P&L +27.00, Profit 58% — 4 seeds still lose
Hypothesis: The 4 losing seeds have unfavorable regime; adapt size or cut losses.

New engine features (EngineSimV56):
- session_kill_switch: stop trading if session P&L < threshold after X ticks
- adaptive_atr_size: scale position size by ATR regime (smaller in calm)
- momentum_scaled_a: bigger A size when momentum is very strong
- rsi_scaled_b: bigger B size when RSI is extreme
- r_based_trail: trail based on R-multiple instead of ATR

Variants:
  v56a — session kill: stop if P&L < -30 after 2h (4800 ticks)
  v56b — session kill: stop if P&L < -50 after 3h (7200 ticks)
  v56c — adaptive_atr_size: 0.5x size if ATR < 0.7%
  v56d — adaptive_atr_size: 0.5x size if ATR < 0.6%
  v56e — momentum_scaled_a: 0.040 size if mom > 0.8% (else 0.025)
  v56f — rsi_scaled_b: 0.150 size if RSI extreme (else 0.125)
  v56g — combined: kill switch + adaptive_atr_size
  v56h — R-based trail: 0.5R trail instead of 0.30 ATR
  v56i — R-based trail: 0.7R trail (looser)
  v56j — adaptive_size_warmup: 0.5x size first 30min, then full
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy
from collections import defaultdict, deque

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v51_push import make_strategies_v51
from v53_push import EngineSimV53, make_v53_config


class EngineSimV56(EngineSimV53):
    """V56 engine: regime-adaptive sizing + session kill switch."""

    def __init__(self, config, name):
        super().__init__(config, name)
        self.session_kill_threshold = self.config.get('session_kill_threshold', None)
        self.session_kill_after_tick = self.config.get('session_kill_after_tick', None)
        self.session_killed = False
        self.adaptive_atr_threshold_pct = self.config.get('adaptive_atr_threshold_pct', None)
        self.adaptive_atr_size_mult = self.config.get('adaptive_atr_size_mult', 0.5)
        self.momentum_scaled_a_threshold = self.config.get('momentum_scaled_a_threshold', None)
        self.momentum_scaled_a_size = self.config.get('momentum_scaled_a_size', None)
        self.rsi_scaled_b_threshold_extreme = self.config.get('rsi_scaled_b_threshold_extreme', None)
        self.rsi_scaled_b_size = self.config.get('rsi_scaled_b_size', None)
        self.r_based_trail = self.config.get('r_based_trail', None)  # e.g., 0.5 = 0.5R trail
        self.warmup_ticks = self.config.get('warmup_ticks', None)
        self.warmup_size_mult = self.config.get('warmup_size_mult', 0.5)

    def _check_session_kill(self, tick):
        if self.session_killed: return True
        if (self.session_kill_threshold is not None
                and self.session_kill_after_tick is not None
                and tick >= self.session_kill_after_tick):
            # Compute current session P&L = current cash - initial capital
            session_pnl = self.cash - self.initial_capital
            if session_pnl < self.session_kill_threshold:
                self.session_killed = True
                return True
        return False

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
        # Adaptive size
        eff_cfg = dict(cfg)
        # Adaptive ATR size
        if self.adaptive_atr_threshold_pct is not None and atr_pct < self.adaptive_atr_threshold_pct:
            eff_cfg['pos_size_pct'] = cfg['pos_size_pct'] * self.adaptive_atr_size_mult
        # Momentum-scaled A size
        if (self.momentum_scaled_a_threshold is not None
                and self.momentum_scaled_a_size is not None
                and abs(momentum) > self.momentum_scaled_a_threshold):
            eff_cfg['pos_size_pct'] = self.momentum_scaled_a_size
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
        # Adaptive ATR size
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

    def _dynamic_trail_atr(self, r_multiple):
        """R-based trail if enabled, else use parent logic."""
        if self.r_based_trail is not None:
            # R-based trail: trail distance = r_based_trail * initial_sl_distance
            # But _dynamic_trail_atr returns a multiplier on pos.trail_atr
            # trail_dist = trail_atr * mult, and we want trail_dist = r_based_trail * initial_sl_distance
            # initial_sl_distance = trail_atr * sl_mult, so:
            # trail_atr * mult = r_based_trail * trail_atr * sl_mult
            # mult = r_based_trail * sl_mult
            sl_mult = self.config.get('A', {}).get('sl_mult', 1.5)
            return self.r_based_trail * sl_mult
        return super()._dynamic_trail_atr(r_multiple)


def v53h_base_adaptive(**adapt_overrides):
    """v53h config with optional adaptive overrides."""
    cfg = make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        partial1_pct=0.05, partial2_pct=0.10,
        partial3_r=1.25, partial3_pct=0.15,
    )
    cfg.update(adapt_overrides)
    return cfg


CONFIGS = {
    # v53h baseline (control)
    'v53h_baseline': v53h_base_adaptive(),
    # v56a — session kill: -30 after 2h
    'v56a_kill_30_2h': v53h_base_adaptive(session_kill_threshold=-30, session_kill_after_tick=4800),
    # v56b — session kill: -50 after 3h
    'v56b_kill_50_3h': v53h_base_adaptive(session_kill_threshold=-50, session_kill_after_tick=7200),
    # v56c — adaptive ATR size: 0.5x if ATR < 0.7%
    'v56c_atr_size_0.7': v53h_base_adaptive(adaptive_atr_threshold_pct=0.7, adaptive_atr_size_mult=0.5),
    # v56d — adaptive ATR size: 0.5x if ATR < 0.6%
    'v56d_atr_size_0.6': v53h_base_adaptive(adaptive_atr_threshold_pct=0.6, adaptive_atr_size_mult=0.5),
    # v56e — momentum-scaled A: 0.040 if mom > 0.8%
    'v56e_mom_a_0.040': v53h_base_adaptive(momentum_scaled_a_threshold=0.8, momentum_scaled_a_size=0.040),
    # v56f — RSI-scaled B: 0.150 if RSI extreme (< 25 or > 75)
    'v56f_rsi_b_0.150': v53h_base_adaptive(rsi_scaled_b_threshold_extreme=(25, 75), rsi_scaled_b_size=0.150),
    # v56g — combined: kill + adaptive ATR
    'v56g_kill_atr': v53h_base_adaptive(session_kill_threshold=-30, session_kill_after_tick=4800,
                                          adaptive_atr_threshold_pct=0.7, adaptive_atr_size_mult=0.5),
    # v56h — R-based trail: 0.5R (tighter than 0.30 ATR which is ~0.45R with SL 1.5)
    'v56h_r_trail_0.5': v53h_base_adaptive(r_based_trail=0.5),
    # v56i — R-based trail: 0.7R (looser)
    'v56i_r_trail_0.7': v53h_base_adaptive(r_based_trail=0.7),
    # v56j — warmup: 0.5x size first 30min (1200 ticks)
    'v56j_warmup_30min': v53h_base_adaptive(warmup_ticks=1200, warmup_size_mult=0.5),
}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV56(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
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
RESULTS_FILE = '/tmp/v56_seeds.json'

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
        baseline = 'v53h_baseline'
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
