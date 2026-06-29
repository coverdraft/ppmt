#!/usr/bin/env python3
"""
v53 — 3-partial TP system + dynamic trail + v52b sizing baseline.

v52b baseline (B bigger 0.125): P&L +25.71, PF 1.99, MaxDD 0.28, WR 75.3%, Profit 58%

New engine features (EngineSimV53):
- 3-partial TP: partial1, partial2, partial3 + trailing on remainder
- Dynamic trail: trail distance shrinks as R-multiple grows
  (e.g., 0.30 at +1R, 0.25 at +1.5R, 0.20 at +2R)

Variants:
  v53a — v52b + 3-partial (5/10/15/70 trailing)
  v53b — v52b + 3-partial (5/15/20/60 trailing)
  v53c — v52b + 3-partial (10/15/20/55 trailing)
  v53d — v52b + dynamic trail (0.30→0.25→0.20 at 1R/1.5R/2R)
  v53e — v52b + dynamic trail (0.30→0.20→0.15 at 1R/1.5R/2R)
  v53f — v52b + 3-partial (5/10/15/70) + dynamic trail
  v53g — v52b + 3-partial (10/15/20/55) + dynamic trail
  v53h — v52b + 4-partial (5/5/10/15/65 trailing) — extreme granularity
  v53i — v52b + 3-partial (5/10/15/70) + dynamic trail aggressive (0.30→0.20→0.15)
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v51_push import make_strategies_v51
from v52_push import v51e_base


class EngineSimV53(v40.EngineSimV40):
    """V53 engine: adds 3-partial TP + dynamic trail on top of V40."""

    def _check_stops(self, sym, prices, tick):
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

        # LOCK profit at lock_trigger_r
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

        # MULTI-PARTIAL: support 2 or 3 levels
        multi_mode = self.config.get('multi_partial', False)
        if multi_mode:
            # Level 1
            if not getattr(pos, 'partial1_done', False) and self.config.get('partial1_trigger_r') is not None:
                if r_multiple >= self.config['partial1_trigger_r']:
                    pct1 = self.config.get('partial1_close_pct', 0.10)
                    close_qty = pos.qty * pct1
                    if close_qty > 0.001:
                        self._partial_close(sym, price, 'PARTIAL_TP1', tick, close_qty)
                    setattr(pos, 'partial1_done', True)
            # Level 2
            if not getattr(pos, 'partial2_done', False) and self.config.get('partial2_trigger_r') is not None:
                if r_multiple >= self.config['partial2_trigger_r']:
                    pct2 = self.config.get('partial2_close_pct', 0.20)
                    close_qty = pos.qty * pct2
                    if close_qty > 0.001:
                        self._partial_close(sym, price, 'PARTIAL_TP2', tick, close_qty)
                    setattr(pos, 'partial2_done', True)
                    # Enable trailing after partial2 (V53: defer if partial3 enabled)
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
            # Level 3 (V53 NEW)
            if not getattr(pos, 'partial3_done', False) and self.config.get('partial3_trigger_r') is not None:
                if r_multiple >= self.config['partial3_trigger_r']:
                    pct3 = self.config.get('partial3_close_pct', 0.15)
                    close_qty = pos.qty * pct3
                    if close_qty > 0.001:
                        self._partial_close(sym, price, 'PARTIAL_TP3', tick, close_qty)
                    setattr(pos, 'partial3_done', True)
                    # Enable trailing after partial3
                    if self.config.get('trail_after_partial', True):
                        pos.trail_active = True
                        trail_dist = pos.trail_atr * self._dynamic_trail_atr(r_multiple)
                        if is_long:
                            new_sl = price - trail_dist
                            if new_sl > pos.current_sl: pos.current_sl = new_sl
                        else:
                            new_sl = price + trail_dist
                            if new_sl < pos.current_sl: pos.current_sl = new_sl

        # Trailing stop update with dynamic distance
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
            cd_key = 'time_cooldown_min' if 'time_cooldown_min' in cfg else 'cooldown_min'
            self.cooldown_until[sym] = tick + int(cfg.get(cd_key, cfg['cooldown_min']) * 60 / v40.TICK_SECONDS)
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
            if reason == 'TP':
                cd_min = cfg.get('tp_cooldown_min', cfg['cooldown_min'])
            else:
                cd_min = cfg['cooldown_min']
            self.cooldown_until[sym] = tick + int(cd_min * 60 / v40.TICK_SECONDS)

    def _dynamic_trail_atr(self, r_multiple):
        """Compute trail_atr_mult based on R-multiple (tighter as R grows)."""
        base = self.config.get('trail_atr_mult', 0.30)
        # Dynamic trail: shrink trail as R grows
        # Format: list of (r_threshold, trail_mult)
        dyn = self.config.get('dynamic_trail', None)
        if dyn is None:
            return base
        # Find the highest threshold <= r_multiple
        result = base
        for r_thresh, mult in dyn:
            if r_multiple >= r_thresh:
                result = mult
        return result


def make_v53_config(strat_kwargs=None, lock_offset_r=0.35, trail_atr=0.30,
                    partial1_r=0.5, partial1_pct=0.10,
                    partial2_r=1.0, partial2_pct=0.20,
                    partial3_r=None, partial3_pct=None,
                    dynamic_trail=None):
    """Build v53 config with optional 3-partial and dynamic trail."""
    strat_kwargs = strat_kwargs or {}
    strat_kwargs.setdefault('sl_mult', 1.5)
    strat_kwargs.setdefault('tp_mult', 1.2)
    strat_kwargs.setdefault('momentum_min', 0.55)
    cfg = v40.make_config(
        strategies=make_strategies_v51(**strat_kwargs),
        lock_r=0.5, lock_offset_r=lock_offset_r,
        multi_partial=True,
        partial1_r=partial1_r, partial1_pct=partial1_pct,
        partial2_r=partial2_r, partial2_pct=partial2_pct,
        trail_atr=trail_atr, atr_floor_pct=0.58,
    )
    if partial3_r is not None:
        cfg['partial3_trigger_r'] = partial3_r
        cfg['partial3_close_pct'] = partial3_pct
    else:
        cfg['partial3_trigger_r'] = None
    if dynamic_trail is not None:
        cfg['dynamic_trail'] = dynamic_trail
    return cfg


# Dynamic trail schedules
DT_CONSERVATIVE = [(1.0, 0.30), (1.5, 0.25), (2.0, 0.20)]   # gentle tightening
DT_AGGRESSIVE = [(1.0, 0.30), (1.5, 0.20), (2.0, 0.15)]     # fast tightening


CONFIGS = {
    # v52b baseline (B bigger 0.125) — control
    'v52b_baseline': make_v53_config(strat_kwargs={'b_pos_size': 0.125}),
    # v53a — 3-partial (5/10/15/70)
    'v53a_3p_5_10_15_70': make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        partial1_pct=0.05, partial2_pct=0.10,
        partial3_r=1.5, partial3_pct=0.15,
    ),
    # v53b — 3-partial (5/15/20/60)
    'v53b_3p_5_15_20_60': make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        partial1_pct=0.05, partial2_pct=0.15,
        partial3_r=1.5, partial3_pct=0.20,
    ),
    # v53c — 3-partial (10/15/20/55)
    'v53c_3p_10_15_20_55': make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        partial1_pct=0.10, partial2_pct=0.15,
        partial3_r=1.5, partial3_pct=0.20,
    ),
    # v53d — dynamic trail conservative
    'v53d_dt_cons': make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        dynamic_trail=DT_CONSERVATIVE,
    ),
    # v53e — dynamic trail aggressive
    'v53e_dt_aggr': make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        dynamic_trail=DT_AGGRESSIVE,
    ),
    # v53f — 3-partial (5/10/15/70) + dynamic trail conservative
    'v53f_3p_dt_cons': make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        partial1_pct=0.05, partial2_pct=0.10,
        partial3_r=1.5, partial3_pct=0.15,
        dynamic_trail=DT_CONSERVATIVE,
    ),
    # v53g — 3-partial (10/15/20/55) + dynamic trail aggressive
    'v53g_3p_dt_aggr': make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        partial1_pct=0.10, partial2_pct=0.15,
        partial3_r=1.5, partial3_pct=0.20,
        dynamic_trail=DT_AGGRESSIVE,
    ),
    # v53h — 3-partial (5/10/15/70) + partial3 at 1.25R (faster)
    'v53h_3p_fast': make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        partial1_pct=0.05, partial2_pct=0.10,
        partial3_r=1.25, partial3_pct=0.15,
    ),
    # v53i — 3-partial (5/10/15/70) + dynamic trail aggressive
    'v53i_3p_dt_aggr': make_v53_config(
        strat_kwargs={'b_pos_size': 0.125},
        partial1_pct=0.05, partial2_pct=0.10,
        partial3_r=1.5, partial3_pct=0.15,
        dynamic_trail=DT_AGGRESSIVE,
    ),
}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV53(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
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
RESULTS_FILE = '/tmp/v53_seeds.json'

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
        baseline = 'v52b_baseline'
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
