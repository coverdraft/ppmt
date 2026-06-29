#!/usr/bin/env python3
"""
v55 — Risk management: correlation filter, drawdown cooldown, re-entry logic.

v53h baseline: WR 79.4%, P&L +27.00, Profit 58% — 4 seeds (314, 1234, 99, 2025) still lose
Hypothesis: Risk management can cut losses on bad seeds without hurting winners.

New engine features (EngineSimV55):
- max_concurrent_positions: cap total open positions across all strategies
- drawdown_cooldown: after SL hit, extend cooldown if recent P&L is negative
- reentry_after_tp: if signal persists, allow re-entry with shorter cooldown after TP
- min_cooldown_between_seeds: per-symbol cooldown extension when 3+ SL hits in window

Variants:
  v55a — max_concurrent=3 (cap total positions)
  v55b — max_concurrent=2 (tighter cap)
  v55c — drawdown_cooldown: 90min if recent P&L negative (was 45min)
  v55d — drawdown_cooldown: 120min if recent P&L negative
  v55e — reentry_after_tp: 15min cooldown after TP (was 45min)
  v55f — reentry_after_tp: 20min cooldown after TP
  v55g — combined: max_concurrent=3 + reentry_after_tp 20min
  v55h — combined: max_concurrent=3 + drawdown_cooldown 90min
  v55i — per-symbol SL streak: 3 SLs in 2h → 90min cooldown
  v55j — combined: max_concurrent=3 + reentry 20min + drawdown_cd 90min
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy
from collections import defaultdict, deque

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v51_push import make_strategies_v51
from v53_push import EngineSimV53, make_v53_config


class EngineSimV55(EngineSimV53):
    """V55 engine: adds risk management on top of V53."""

    def __init__(self, config, name):
        super().__init__(config, name)
        # Track recent SL hits per symbol for streak detection
        self.sl_history = defaultdict(deque)  # sym → deque of tick when SL hit
        # Track recent P&L for drawdown cooldown
        self.recent_pnl = deque(maxlen=20)  # last 20 closed positions
        # Track open positions count for max_concurrent
        self.max_concurrent_cap = self.config.get('max_concurrent_positions', None)
        self.drawdown_cd_min = self.config.get('drawdown_cooldown_min', None)
        self.reentry_tp_cd_min = self.config.get('reentry_tp_cooldown_min', None)
        self.sl_streak_threshold = self.config.get('sl_streak_threshold', None)
        self.sl_streak_cd_min = self.config.get('sl_streak_cd_min', None)

    def _get_total_open_positions(self):
        return sum(1 for _ in self.positions.values())

    def _check_max_concurrent(self):
        """Return True if we can open new positions."""
        if self.max_concurrent_cap is None:
            return True
        return self._get_total_open_positions() < self.max_concurrent_cap

    def _try_strategy_a(self, sym, prices, tick):
        if not self._check_max_concurrent(): return
        super()._try_strategy_a(sym, prices, tick)

    def _try_strategy_b(self, sym, prices, tick):
        if not self._check_max_concurrent(): return
        super()._try_strategy_b(sym, prices, tick)

    def _try_strategy_d(self, sym, prices, tick):
        if not self._check_max_concurrent(): return
        super()._try_strategy_d(sym, prices, tick)

    def _close_position(self, sym, price, reason, tick):
        """Override to track P&L and SL streaks."""
        if sym in self.positions:
            pos = self.positions[sym]
            # Compute P&L for this position
            if pos.direction == 'LONG':
                pnl = (price - pos.entry_price) * pos.qty
            else:
                pnl = (pos.entry_price - price) * pos.qty
            self.recent_pnl.append(pnl)
            # Track SL streaks
            if reason == 'SL':
                self.sl_history[sym].append(tick)
                # Clean old entries (> 2h = 4800 ticks)
                while self.sl_history[sym] and self.sl_history[sym][0] < tick - 4800:
                    self.sl_history[sym].popleft()
        super()._close_position(sym, price, reason, tick)

    def _apply_cooldown(self, sym, reason, tick, cfg):
        """Apply cooldown with optional extensions."""
        cd_min = cfg['cooldown_min']
        if reason == 'TP':
            cd_min = cfg.get('tp_cooldown_min', cfg['cooldown_min'])
            # Re-entry: shorter cooldown after TP if enabled
            if self.reentry_tp_cd_min is not None:
                cd_min = self.reentry_tp_cd_min
        elif reason == 'SL':
            # Drawdown cooldown: extend if recent P&L is negative
            if self.drawdown_cd_min is not None and len(self.recent_pnl) >= 3:
                recent_sum = sum(self.recent_pnl)
                if recent_sum < 0:
                    cd_min = self.drawdown_cd_min
            # SL streak cooldown: extend if 3+ SLs in 2h
            if self.sl_streak_threshold is not None and self.sl_streak_cd_min is not None:
                if len(self.sl_history[sym]) >= self.sl_streak_threshold:
                    cd_min = self.sl_streak_cd_min
        self.cooldown_until[sym] = tick + int(cd_min * 60 / v40.TICK_SECONDS)

    def _check_stops(self, sym, prices, tick):
        """Override to use _apply_cooldown."""
        if sym not in self.positions: return
        pos = self.positions[sym]
        price = prices[-1]
        cfg = self.config[pos.strategy]
        is_long = pos.direction == 'LONG'
        if is_long:
            if price > pos.max_favorable_price: pos.max_favorable_price = price
        else:
            if price < pos.max_favorable_price: pos.max_favorable_price = price
        initial_sl_distance = pos.trail_atr * cfg['sl_mult']
        if is_long:
            r_multiple = (price - pos.entry_price) / initial_sl_distance
        else:
            r_multiple = (pos.entry_price - price) / initial_sl_distance

        # LOCK profit
        if not pos.lock_done and self.config.get('lock_trigger_r') is not None:
            if r_multiple >= self.config['lock_trigger_r']:
                lock_r = self.config.get('lock_offset_r', 0.2)
                if is_long:
                    new_sl = pos.entry_price + lock_r * initial_sl_distance
                    if new_sl > pos.current_sl: pos.current_sl = new_sl
                else:
                    new_sl = pos.entry_price - lock_r * initial_sl_distance
                    if new_sl < pos.current_sl or pos.current_sl is None: pos.current_sl = new_sl
                pos.lock_done = True

        # MULTI-PARTIAL: 2 or 3 levels
        multi_mode = self.config.get('multi_partial', False)
        if multi_mode:
            if not getattr(pos, 'partial1_done', False) and self.config.get('partial1_trigger_r') is not None:
                if r_multiple >= self.config['partial1_trigger_r']:
                    pct1 = self.config.get('partial1_close_pct', 0.05)
                    close_qty = pos.qty * pct1
                    if close_qty > 0.001:
                        self._partial_close(sym, price, 'PARTIAL_TP1', tick, close_qty)
                    setattr(pos, 'partial1_done', True)
            if not getattr(pos, 'partial2_done', False) and self.config.get('partial2_trigger_r') is not None:
                if r_multiple >= self.config['partial2_trigger_r']:
                    pct2 = self.config.get('partial2_close_pct', 0.10)
                    close_qty = pos.qty * pct2
                    if close_qty > 0.001:
                        self._partial_close(sym, price, 'PARTIAL_TP2', tick, close_qty)
                    setattr(pos, 'partial2_done', True)
                    if not self.config.get('partial3_trigger_r'):
                        if self.config.get('trail_after_partial', True):
                            pos.trail_active = True
                            trail_dist = pos.trail_atr * self._dynamic_trail_atr(r_multiple)
                            if is_long:
                                new_sl = price - trail_dist
                                if new_sl > pos.current_sl: pos.current_sl = new_sl
                            else:
                                new_sl = price + trail_dist
                                if new_sl < pos.current_sl: pos.current_sl = new_sl
            if not getattr(pos, 'partial3_done', False) and self.config.get('partial3_trigger_r') is not None:
                if r_multiple >= self.config['partial3_trigger_r']:
                    pct3 = self.config.get('partial3_close_pct', 0.15)
                    close_qty = pos.qty * pct3
                    if close_qty > 0.001:
                        self._partial_close(sym, price, 'PARTIAL_TP3', tick, close_qty)
                    setattr(pos, 'partial3_done', True)
                    if self.config.get('trail_after_partial', True):
                        pos.trail_active = True
                        trail_dist = pos.trail_atr * self._dynamic_trail_atr(r_multiple)
                        if is_long:
                            new_sl = price - trail_dist
                            if new_sl > pos.current_sl: pos.current_sl = new_sl
                        else:
                            new_sl = price + trail_dist
                            if new_sl < pos.current_sl: pos.current_sl = new_sl

        # Trailing stop
        if pos.trail_active:
            trail_dist = pos.trail_atr * self._dynamic_trail_atr(r_multiple)
            if is_long:
                new_sl = pos.max_favorable_price - trail_dist
                if new_sl > pos.current_sl: pos.current_sl = new_sl
                pos.current_tp = None
            else:
                new_sl = pos.max_favorable_price + trail_dist
                if new_sl < pos.current_sl: pos.current_sl = new_sl
                pos.current_tp = None

        # Time stop
        if tick - pos.entry_tick > cfg['time_stop']:
            self._close_position(sym, price, 'TIME', tick)
            self._apply_cooldown(sym, 'TIME', tick, cfg)
            return

        # SL / TP / CAT_SL
        hit = False; reason = ''
        if pos.current_sl is not None:
            if is_long and price <= pos.current_sl:
                hit = True; reason = 'SL'
            elif not is_long and price >= pos.current_sl:
                hit = True; reason = 'SL'
        if not hit and pos.current_tp is not None:
            if is_long and price >= pos.current_tp:
                hit = True; reason = 'TP'
            elif not is_long and price <= pos.current_tp:
                hit = True; reason = 'TP'
        if not hit and pos.catastrophic_sl is not None:
            if is_long and price <= pos.catastrophic_sl:
                hit = True; reason = 'CAT_SL'
            elif not is_long and price >= pos.catastrophic_sl:
                hit = True; reason = 'CAT_SL'
        if hit:
            self._close_position(sym, price, reason, tick)
            self._apply_cooldown(sym, reason, tick, cfg)


def v53h_base_risk(**risk_overrides):
    """v53h config with optional risk management overrides."""
    cfg = make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        partial1_pct=0.05, partial2_pct=0.10,
        partial3_r=1.25, partial3_pct=0.15,
    )
    cfg.update(risk_overrides)
    return cfg


CONFIGS = {
    # v53h baseline (control)
    'v53h_baseline': v53h_base_risk(),
    # v55a — max_concurrent=3
    'v55a_max3': v53h_base_risk(max_concurrent_positions=3),
    # v55b — max_concurrent=2 (tighter)
    'v55b_max2': v53h_base_risk(max_concurrent_positions=2),
    # v55c — drawdown_cooldown 90min
    'v55c_dd_cd90': v53h_base_risk(drawdown_cooldown_min=90),
    # v55d — drawdown_cooldown 120min
    'v55d_dd_cd120': v53h_base_risk(drawdown_cooldown_min=120),
    # v55e — reentry_after_tp 15min
    'v55e_reentry15': v53h_base_risk(reentry_tp_cooldown_min=15),
    # v55f — reentry_after_tp 20min
    'v55f_reentry20': v53h_base_risk(reentry_tp_cooldown_min=20),
    # v55g — max3 + reentry 20min
    'v55g_max3_reentry20': v53h_base_risk(max_concurrent_positions=3, reentry_tp_cooldown_min=20),
    # v55h — max3 + drawdown_cd 90min
    'v55h_max3_dd90': v53h_base_risk(max_concurrent_positions=3, drawdown_cooldown_min=90),
    # v55i — SL streak: 3 SLs in 2h → 90min cooldown
    'v55i_sl_streak': v53h_base_risk(sl_streak_threshold=3, sl_streak_cd_min=90),
    # v55j — combined: max3 + reentry20 + dd90
    'v55j_combined': v53h_base_risk(max_concurrent_positions=3, reentry_tp_cooldown_min=20, drawdown_cooldown_min=90),
}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV55(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
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
RESULTS_FILE = '/tmp/v55_seeds.json'

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
