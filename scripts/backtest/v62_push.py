#!/usr/bin/env python3
"""
v62 — Refine v61b's pyramiding. PUSH P&L HARDER with pyramid tuning.

v61b baseline: A 0.050 + B 0.20 + tiered 0.4/0.7/1.0 + PYRAMID B +50% @+1.0R
  WR 79.6%, P&L +46.02, MaxDD 0.29%, Profit 67%, PF 2.66, Sharpe +8.69

User: "mucha mas ganancia y en mas corto tiempo"

LEVERS:
  1) PYRAMID SIZE  → +50% was good, try +75% / +100% with B's tiered protection
  2) PYRAMID R     → +1.0R was good, try +0.8R (earlier) / +1.25R (later, more selective)
  3) MULTI-PYRAMID → allow 2nd pyramid at +2.0R (compound winners)
  4) PYRAMID B + A 0.055 → push A size alongside pyramid B

Variants:
  v62a — Pyramid +75% B @+1.0R (more aggressive size)
  v62b — Pyramid +100% B @+1.0R (double size on winners)
  v62c — Pyramid +50% B @+0.8R (earlier trigger)
  v62d — Pyramid +50% B @+1.25R (later, more selective)
  v62e — Multi-pyramid: +50% @+1.0R + +30% @+2.0R (compound)
  v62f — Pyramid +50% + A 0.055 (push A alongside)
  v62g — Pyramid +75% + A 0.055 (combined aggressive)
  v62h — Pyramid +50% + tighter trail 0.25 (lock pyramid profit faster)
  v62i — Pyramid +50% + lock_offset 0.40 (tighter BE post-pyramid)
  v62j — MAX: Pyramid +100% + A 0.055 + multi-pyramid + trail 0.25
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v51_push import make_strategies_v51
from v53_push import EngineSimV53, make_v53_config
from v57_push import EngineSimV57


def v61b_base(strat_kwargs=None, adapt_kwargs=None, **overrides):
    """v61b config: A 0.050 + B 0.20 + TIERED + PYRAMID B +50% @+1.0R."""
    strat_kwargs = strat_kwargs or {'b_pos_size': 0.20, 'a_pos_size': 0.050}
    cfg = make_v53_config(
        strat_kwargs=strat_kwargs,
        partial1_pct=0.05, partial2_pct=0.10,
        partial3_r=1.25, partial3_pct=0.15,
    )
    cfg['adaptive_atr_threshold_pct'] = 0.6
    cfg['adaptive_atr_size_mult'] = 0.5
    cfg['tiered_atr'] = [(0.6, 0.4), (0.8, 0.7), (float('inf'), 1.0)]
    cfg['pyramid_trigger_r'] = 1.0
    cfg['pyramid_pct'] = 0.50
    cfg['pyramid_strategies'] = ['B']
    if adapt_kwargs:
        cfg.update(adapt_kwargs)
    cfg.update(overrides)
    return cfg


class EngineSimV62(EngineSimV57):
    """V62: extends V61 with MULTI-PYRAMID support (2nd pyramid at higher R)."""

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

        # ── PYRAMID (V62: supports up to 2 pyramid levels) ──
        pyramid_trigger = self.config.get('pyramid_trigger_r', None)
        pyramid_pct = self.config.get('pyramid_pct', 0.50)
        pyramid_strategies = self.config.get('pyramid_strategies', ['B'])
        pyramid2_trigger = self.config.get('pyramid2_trigger_r', None)
        pyramid2_pct = self.config.get('pyramid2_pct', 0.30)

        pyramid_fired = False
        if (pyramid_trigger is not None
                and not getattr(pos, 'pyramid_done', False)
                and pos.strategy in pyramid_strategies
                and r_multiple >= pyramid_trigger):
            self._do_pyramid(pos, price, pyramid_pct, is_long, prices, cfg, sym, r_multiple, level=1)
            pyramid_fired = True

        if (pyramid2_trigger is not None
                and getattr(pos, 'pyramid_done', False)
                and not getattr(pos, 'pyramid2_done', False)
                and pos.strategy in pyramid_strategies
                and r_multiple >= pyramid2_trigger):
            self._do_pyramid(pos, price, pyramid2_pct, is_long, prices, cfg, sym, r_multiple, level=2)
            pyramid_fired = True

        # Recompute r_multiple after pyramid
        if pyramid_fired:
            if is_long:
                r_multiple = (price - pos.entry_price) / (pos.initial_sl_distance or 1)
            else:
                r_multiple = (pos.entry_price - price) / (pos.initial_sl_distance or 1)

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

        # MULTI-PARTIAL: 2 or 3 levels (V57 base)
        multi_mode = self.config.get('multi_partial', False)
        if multi_mode:
            if not getattr(pos, 'partial1_done', False) and self.config.get('partial1_trigger_r') is not None:
                if r_multiple >= self.config['partial1_trigger_r']:
                    pct1 = self.config.get('partial1_close_pct', 0.10)
                    close_qty = pos.qty * pct1
                    if close_qty > 0.001:
                        self._partial_close(sym, price, 'PARTIAL_TP1', tick, close_qty)
                    setattr(pos, 'partial1_done', True)
            if not getattr(pos, 'partial2_done', False) and self.config.get('partial2_trigger_r') is not None:
                if r_multiple >= self.config['partial2_trigger_r']:
                    pct2 = self.config.get('partial2_close_pct', 0.20)
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

        # Trailing stop update
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
            if is_long and price <= pos.current_sl: hit = True; reason = 'SL'
            elif not is_long and price >= pos.current_sl: hit = True; reason = 'SL'
        if not hit and pos.current_tp is not None:
            if is_long and price >= pos.current_tp: hit = True; reason = 'TP'
            elif not is_long and price <= pos.current_tp: hit = True; reason = 'TP'
        if not hit and pos.catastrophic_sl is not None:
            if is_long and price <= pos.catastrophic_sl: hit = True; reason = 'CAT_SL'
            elif not is_long and price >= pos.catastrophic_sl: hit = True; reason = 'CAT_SL'
        if hit:
            self._close_position(sym, price, reason, tick)
            if reason == 'TP':
                cd_min = cfg.get('tp_cooldown_min', cfg['cooldown_min'])
            else:
                cd_min = cfg['cooldown_min']
            self.cooldown_until[sym] = tick + int(cd_min * 60 / v40.TICK_SECONDS)

    def _do_pyramid(self, pos, price, pyramid_pct, is_long, prices, cfg, sym, r_multiple, level):
        """Apply pyramid: add pyramid_pct to pos.qty at current price."""
        add_qty = pos.qty * pyramid_pct
        old_qty = pos.qty
        old_entry = pos.entry_price
        new_qty = old_qty + add_qty
        new_entry = (old_qty * old_entry + add_qty * price) / new_qty
        pos.qty = new_qty
        pos.entry_price = new_entry
        new_atr = v40.v38.computeATR(prices, 60)
        if new_atr > 0:
            pos.initial_atr = new_atr
            pos.initial_sl_distance = new_atr * cfg['sl_mult']
            pos.current_sl = (new_entry - new_atr * cfg['sl_mult']) if is_long else (new_entry + new_atr * cfg['sl_mult'])
            pos.catastrophic_sl = (new_entry - new_atr * cfg['catsl_mult']) if is_long else (new_entry + new_atr * cfg['catsl_mult'])
            pos.current_tp = None
        # Reset partials + lock + trail so they re-fire on pyramided position
        pos.partial1_done = False
        pos.partial2_done = False
        pos.partial3_done = False
        pos.lock_done = False
        pos.trail_active = False
        pos.max_favorable_price = price
        if level == 1:
            pos.pyramid_done = True
        elif level == 2:
            pos.pyramid2_done = True


CONFIGS = {
    # v61b baseline (control)
    'v61b_baseline': v61b_base(),
    # ── PYRAMID SIZE ──
    'v62a_pyr75_B': v61b_base(pyramid_pct=0.75),
    'v62b_pyr100_B': v61b_base(pyramid_pct=1.00),
    # ── PYRAMID R ──
    'v62c_pyr50_08R_B': v61b_base(pyramid_trigger_r=0.8),
    'v62d_pyr50_125R_B': v61b_base(pyramid_trigger_r=1.25),
    # ── MULTI-PYRAMID ──
    'v62e_multi_pyr': v61b_base(pyramid2_trigger_r=2.0, pyramid2_pct=0.30),
    # ── PYRAMID + A push ──
    'v62f_pyr50_a055': v61b_base(strat_kwargs={'b_pos_size': 0.20, 'a_pos_size': 0.055}),
    'v62g_pyr75_a055': v61b_base(strat_kwargs={'b_pos_size': 0.20, 'a_pos_size': 0.055}, pyramid_pct=0.75),
    # ── PYRAMID + risk tuning ──
    'v62h_pyr50_trail025': v61b_base(trail_atr=0.25),
    'v62i_pyr50_lock040': v61b_base(lock_offset_r=0.40),
    # ── MAX ──
    'v62j_max': v61b_base(
        strat_kwargs={'b_pos_size': 0.20, 'a_pos_size': 0.055},
        pyramid_pct=1.00, pyramid2_trigger_r=2.0, pyramid2_pct=0.30,
        trail_atr=0.25, lock_offset_r=0.40),
}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV62(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
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
RESULTS_FILE = '/tmp/v62_seeds.json'

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
        baseline = 'v61b_baseline'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L ({len(seeds)} seeds):\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*160)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        print("WINNER SELECTION (target: P&L > baseline + MaxDD ≤ 0.30% + Profit ≥ 67%)")
        print("=" * 80)
        baseline_pnl = agg[baseline]['pnl_mean']
        candidates = [(name, m) for name, m in agg.items()
                      if name != baseline
                      and m['pnl_mean'] > baseline_pnl
                      and m['max_dd_mean'] <= 0.30
                      and m['profitable_seeds'] >= 67]
        if candidates:
            candidates.sort(key=lambda x: (x[1]['pnl_mean'], x[1]['profitable_seeds']), reverse=True)
            w = candidates[0]
            print(f"\n🏆 WINNER (12-seed validated): {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f} (vs base {baseline_pnl:+.2f})  Profit {w[1]['profitable_seeds']:.0f}%  AvgR {w[1]['avg_r_mean']:+.3f}  MaxDD {w[1]['max_dd_mean']:.2f}%  PF {w[1]['pf_mean']:.2f}")
        else:
            print("\n  ⚠️ No config beat baseline. Top 5 by P&L:")
            ranked = sorted(agg.items(), key=lambda x: x[1]['pnl_mean'], reverse=True)
            for i, (name, m) in enumerate(ranked[:5]):
                vs = m['pnl_mean'] - baseline_pnl
                print(f"  #{i+1} {name:<32} P&L {m['pnl_mean']:+.2f} ({vs:+.2f})  WR {m['wr_mean']:.1f}%  Profit {m['profitable_seeds']:.0f}%  MaxDD {m['max_dd_mean']:.2f}%")
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
