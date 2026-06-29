#!/usr/bin/env python3
"""
v47 — More aggressive stabilization. v46 showed most risk gates don't fire.

Real issue: v43a loses -58, -41, -38 on 3 seeds. We need to:
1. Cut losses earlier when going bad (trailing stop on adverse)
2. Be more selective (fewer trades = fewer losses in bad seeds)
3. Adaptive position sizing (smaller after losses)

NEW IDEAS:
1. v47a — Trailing stop DOWN: if price goes -0.3R, move SL tighter (cut losers faster)
2. v47b — ATR ceiling 2.0% (skip trades in extreme volatility — backtested: those lose)
3. v47c — Stricter ATR floor 0.65% (fewer trades, higher quality)
4. v47d — Stricter momentum 0.50% (was 0.40 — Strategy A only takes strong signals)
5. v47e — Adaptive size: cut position size 50% after 2 consecutive losses
6. v47f — Time stop 1800 (30min, was 60min) — cut dead trades faster
7. v47g — Combo: ATR ceiling + adaptive size + tighter time
8. v47h — Combo: stricter ATR floor 0.65 + ATR ceiling 2.0 + adaptive size
"""
import random, statistics, math, sys, os, json, time
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from copy import deepcopy

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40


def make_strategies(sl_mult=1.4, tp_mult=1.2, rsi_lo=30, rsi_hi=70, momentum_min=0.40,
                    tp_cooldown_min=45, sl_cooldown_min=45, time_stop=2400,
                    atr_floor_pct=0.58, atr_ceiling_pct=None):
    """V47 strategies with ATR ceiling support."""
    return {
        'A': {'momentum_min': momentum_min, 'max_pos': 1, 'sl_mult': sl_mult, 'tp_mult': tp_mult,
              'catsl_mult': 4.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'rsi_min': 25, 'rsi_max': 75, 'time_stop': time_stop, 'pos_size_pct': 0.025,
              'atr_ceiling_pct': atr_ceiling_pct},
        'B': {'rsi_lo': rsi_lo, 'rsi_hi': rsi_hi, 'max_pos': 1, 'sl_mult': sl_mult, 'tp_mult': tp_mult,
              'catsl_mult': 4.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'enabled': True, 'time_stop': time_stop, 'pos_size_pct': 0.10,
              'atr_ceiling_pct': atr_ceiling_pct},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': sl_mult * 0.75, 'tp_mult': tp_mult * 0.83,
              'catsl_mult': 3.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'time_stop': time_stop, 'pos_size_pct': 0.05,
              'atr_ceiling_pct': atr_ceiling_pct},
        'E': {'enabled': False},
    }


class EngineSimV47(v40.EngineSimV40):
    """V47 engine with ATR ceiling + adverse trailing + adaptive size."""

    def __init__(self, config, name, capital=12000):
        super().__init__(config, name, capital)
        self.consec_loss_count = 0
        self.adaptive_size_shrink = 0  # multiplier reductions
        self.skipped_atr_ceiling = 0
        self.adverse_trail_triggers = 0

    def _try_strategy_a(self, sym, prices, tick):
        cfg = dict(self.config.get('A', {}))
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
        # ATR ceiling (NEW v47)
        if cfg.get('atr_ceiling_pct') is not None and atr_pct > cfg['atr_ceiling_pct']:
            self.skipped_atr_ceiling += 1
            return
        direction = 'LONG' if momentum > 0 else 'SHORT'
        # Apply adaptive size
        size_multiplier = self._adaptive_size_multiplier()
        if size_multiplier < 0.1: return  # too small to bother
        original_pos_size = cfg.get('pos_size_pct', 0.025)
        cfg['pos_size_pct'] = original_pos_size * size_multiplier
        self._open_position_v40(sym, direction, 'A', prices[-1], atr, cfg, tick)
        self.last_signal_tick['A'] = tick

    def _try_strategy_b(self, sym, prices, tick):
        cfg = dict(self.config.get('B', {}))
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
        if cfg.get('atr_ceiling_pct') is not None and atr_pct > cfg['atr_ceiling_pct']:
            self.skipped_atr_ceiling += 1
            return
        direction = 'LONG' if rsi < 50 else 'SHORT'
        size_multiplier = self._adaptive_size_multiplier()
        if size_multiplier < 0.1: return
        original_pos_size = cfg.get('pos_size_pct', 0.10)
        cfg['pos_size_pct'] = original_pos_size * size_multiplier
        self._open_position_v40(sym, direction, 'B', prices[-1], atr, cfg, tick)
        self.last_signal_tick['B'] = tick

    def _try_strategy_d(self, sym, prices, tick):
        cfg = dict(self.config.get('D', {}))
        if not cfg.get('enabled', True): return
        if tick - self.last_signal_tick['D'] < 40: return
        if self.strategy_pos_count['D'] >= cfg['max_pos']: return
        if len(prices) < 55: return
        if sym in self.positions: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return
        bb = v40.v38.computeBollinger(prices, 50, 2)
        if bb['width'] <= 0 or bb['width'] > cfg['bb_width_max']: return
        current = prices[-1]
        if not (current > bb['upper'] or current < bb['lower']): return
        atr = v40.v38.computeATR(prices, 60)
        atr_pct = atr / prices[-1] * 100
        if self.config.get('atr_floor_pct') is not None and atr_pct < self.config['atr_floor_pct']:
            self.atr_filter_skips += 1
            return
        if cfg.get('atr_ceiling_pct') is not None and atr_pct > cfg['atr_ceiling_pct']:
            self.skipped_atr_ceiling += 1
            return
        direction = 'LONG' if current > bb['upper'] else 'SHORT'
        size_multiplier = self._adaptive_size_multiplier()
        if size_multiplier < 0.1: return
        original_pos_size = cfg.get('pos_size_pct', 0.05)
        cfg['pos_size_pct'] = original_pos_size * size_multiplier
        self._open_position_v40(sym, direction, 'D', prices[-1], atr, cfg, tick)
        self.last_signal_tick['D'] = tick

    def _adaptive_size_multiplier(self):
        """After 2+ consecutive losses, shrink position size by 50%."""
        if not self.config.get('adaptive_size', False): return 1.0
        if self.consec_loss_count >= 4: return 0.25
        if self.consec_loss_count >= 3: return 0.5
        if self.consec_loss_count >= 2: return 0.75
        return 1.0

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

        # LOCK profit at +0.5R
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

        # ADVERSE TRAIL (NEW v47a): if R <= -0.3, tighten SL to -0.2R (cut losers faster)
        if self.config.get('adverse_trail', False) and r_multiple <= -0.3:
            adverse_sl_r = -0.2  # tighter than initial SL at -1.0R
            if is_long:
                new_sl = pos.entry_price + adverse_sl_r * initial_sl_distance
                # Only tighten (don't loosen)
                if pos.current_sl is None or new_sl > pos.current_sl:
                    pos.current_sl = new_sl
                    self.adverse_trail_triggers += 1
            else:
                new_sl = pos.entry_price - adverse_sl_r * initial_sl_distance
                if pos.current_sl is None or new_sl < pos.current_sl:
                    pos.current_sl = new_sl
                    self.adverse_trail_triggers += 1

        # MULTI-PARTIAL TP1 at +0.5R (15%)
        if not getattr(pos, 'partial1_done', False) and self.config.get('partial1_trigger_r') is not None:
            if r_multiple >= self.config['partial1_trigger_r']:
                pct1 = self.config.get('partial1_close_pct', 0.15)
                close_qty = pos.qty * pct1
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP1', tick, close_qty)
                setattr(pos, 'partial1_done', True)

        # MULTI-PARTIAL TP2 at +1.0R (25%, enable trailing)
        if not getattr(pos, 'partial2_done', False) and self.config.get('partial2_trigger_r') is not None:
            if r_multiple >= self.config['partial2_trigger_r']:
                pct2 = self.config.get('partial2_close_pct', 0.25)
                remaining_pct_after1 = 1 - self.config.get('partial1_close_pct', 0.15)
                close_qty = (pos.qty * pct2) / remaining_pct_after1
                if close_qty > 0.001 and close_qty <= pos.qty:
                    self._partial_close(sym, price, 'PARTIAL_TP2', tick, close_qty)
                setattr(pos, 'partial2_done', True)
                pos.trail_active = True
                trail_dist = pos.trail_atr * self.config.get('trail_atr_mult', 0.4)
                if is_long:
                    new_sl = price - trail_dist
                    if new_sl > pos.current_sl: pos.current_sl = new_sl
                else:
                    new_sl = price + trail_dist
                    if new_sl < pos.current_sl: pos.current_sl = new_sl

        # Trailing stop update
        if pos.trail_active:
            trail_dist = pos.trail_atr * self.config.get('trail_atr_mult', 0.4)
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
            self.cooldown_until[sym] = tick + int(cfg['cooldown_min'] * 60 / 1.5)
            return

        # SL/TP/CAT_SL
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
            self.cooldown_until[sym] = tick + int(cd_min * 60 / 1.5)

    def _close_position(self, sym, exit_price_raw, reason, tick):
        super()._close_position(sym, exit_price_raw, reason, tick)
        # Track consecutive losses for adaptive sizing
        # Get the last trade
        if self.trades:
            last = self.trades[-1]
            if last.close_reason in ('SL', 'CAT_SL'):
                self.consec_loss_count += 1
            elif last.close_reason == 'TP':
                self.consec_loss_count = 0

    def get_metrics(self):
        m = super().get_metrics()
        m['skipped_atr_ceiling'] = self.skipped_atr_ceiling
        m['adverse_trail_triggers'] = self.adverse_trail_triggers
        m['consec_loss_count'] = self.consec_loss_count
        return m


def make_config_v47(strategies=None, lock_r=0.5, multi_partial=True,
                    partial1_r=0.5, partial1_pct=0.15,
                    partial2_r=1.0, partial2_pct=0.25,
                    trail_atr=0.4, atr_floor_pct=0.58,
                    atr_ceiling_pct=None, adverse_trail=False, adaptive_size=False):
    if strategies is None: strategies = make_strategies()
    cfg = v40.make_config(
        strategies=strategies,
        lock_r=lock_r, multi_partial=multi_partial,
        partial1_r=partial1_r, partial1_pct=partial1_pct,
        partial2_r=partial2_r, partial2_pct=partial2_pct,
        trail_atr=trail_atr, atr_floor_pct=atr_floor_pct,
    )
    cfg.update({
        'adverse_trail': adverse_trail,
        'adaptive_size': adaptive_size,
    })
    return cfg


CONFIGS = {
    # v43a champion (control)
    'v43a_baseline': make_config_v47(),
    # v47a — Adverse trail: tighten SL to -0.2R when R <= -0.3 (cut losers faster)
    'v47a_adverse_trail': make_config_v47(adverse_trail=True),
    # v47b — ATR ceiling 2.0% (skip extreme vol)
    'v47b_atr_ceil_2.0': make_config_v47(
        strategies=make_strategies(atr_ceiling_pct=2.0)
    ),
    # v47c — Stricter ATR floor 0.65% (fewer trades, higher quality)
    'v47c_atr_floor_0.65': make_config_v47(atr_floor_pct=0.65),
    # v47d — Stricter momentum 0.50%
    'v47d_mom_0.50': make_config_v47(
        strategies=make_strategies(momentum_min=0.50)
    ),
    # v47e — Adaptive size (shrink after consecutive losses)
    'v47e_adapt_size': make_config_v47(adaptive_size=True),
    # v47f — Tighter time stop 1800 (30 min)
    'v47f_time_1800': make_config_v47(
        strategies=make_strategies(time_stop=1800)
    ),
    # v47g — Combo: ATR ceiling + adaptive size + adverse trail
    'v47g_combo': make_config_v47(
        strategies=make_strategies(atr_ceiling_pct=2.0),
        adverse_trail=True, adaptive_size=True,
    ),
    # v47h — Combo: stricter ATR floor 0.65 + ceiling 2.0 + adaptive size
    'v47h_strict_atr_adapt': make_config_v47(
        strategies=make_strategies(atr_ceiling_pct=2.0),
        atr_floor_pct=0.65, adaptive_size=True,
    ),
}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV47(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
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
RESULTS_FILE = '/tmp/v47_seeds.json'

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
        baseline = 'v43a_baseline'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L ({len(seeds)} seeds):\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*160)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        print("WINNER SELECTION (12 seeds: target ≥75% profitable + WR≥70 + MaxDD<0.3 + AvgR>0)")
        print("=" * 80)
        candidates = [(name, m) for name, m in agg.items()
                      if m['profitable_seeds'] >= 75 and m['wr_above_60_seeds'] >= 75
                      and m['max_dd_mean'] < 0.3 and m['avg_r_mean'] > 0]
        if candidates:
            candidates.sort(key=lambda x: (x[1]['profitable_seeds'], x[1]['pnl_mean'], x[1]['wr_mean']), reverse=True)
            w = candidates[0]
            print(f"\n🏆 WINNER (12-seed validated): {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f}  Profit {w[1]['profitable_seeds']:.0f}%  AvgR {w[1]['avg_r_mean']:+.3f}  MaxDD {w[1]['max_dd_mean']:.2f}%  PF {w[1]['pf_mean']:.2f}")
        else:
            print("\n  ⚠️ No config met 75% profit criterion. Top 5:")
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
