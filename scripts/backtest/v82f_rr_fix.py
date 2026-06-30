#!/usr/bin/env python3
"""
v82f — RR FIX v5: Trail AFTER LOCK (no esperar a partial2).

DIAGNÓSTICO v82e:
  - MIXED WR 29% (vs 68% en v81) — DESTRUIDO
  - BULL WR 66%, RR 0.601 (peor que v82d)
  - MEME WR 76%, RR 0.899 (mejor que v82d en RR, peor en WR)
  - BEAR WR 48% — DESTRUIDO

CAUSA: El trail se activa sólo tras partial2 @ +2.5R. Si el trade va a +1.5R
(partial1 fired) y reversa, no hay trail → se sale por SL en +0.35R (lock).
Resultado: partial1 captura +1.5R en 15% del tamaño, pero el 85% restante se
cierra en +0.35R (lock) — RR promedio bajo.

v82f FIX: Activar trail INMEDIATAMENTE después del lock (+0.5R). Así:
  - Trade va a +0.5R → lock fires, SL moves to +0.35R, trail ACTIVATES
  - Si sigue a +1.5R → partial1 fires (15%), trail sigue activo
  - Si reversa desde +1.5R → sale por trail a ~+1.0R (no por lock)
  - Si llega a +2.5R → partial2 fires (20%), trail sigue
  - Si llega a +4.0R → TP hit (remainder)
  - Trail 1.0 ATR protege ganancias acumuladas

v82f CHANGES vs v82e:
  F1. Trail activates after LOCK (not after partial2)
  F2. Keep trail at 1.0 ATR (wide)
  F3. Keep partials at +1.5R (15%) and +2.5R (20%)
  F4. Keep TP at 4.0 ATR
  F5. SL stays 1.5 ATR, lock stays +0.5R → SL=+0.35R

EXPECTED MATH (con trail post-lock):
  - 30% trades son losers @ -1.0R (SL)
  - 20% trades van a +0.5R, lock fires, luego reversan → exit @ +0.35R (lock)
  - 30% trades van a +1.5R, partial1 15% @ +1.5R, trail exit @ ~+0.8R promedio
  - 15% trades van a +2.5R, partial2 20% @ +2.5R, trail exit @ ~+1.5R promedio
  - 5% trades van a +4.0R, TP hit @ +4.0R

  avgR = 0.30 × (-1.0) + 0.20 × 0.35 + 0.30 × (0.15×1.5 + 0.85×0.8) +
         0.15 × (0.20×2.5 + 0.80×1.5) + 0.05 × (0.20×2.5 + 0.15×1.5 + 0.65×4.0)
       = -0.30 + 0.07 + 0.30 × (0.225 + 0.68) + 0.15 × (0.5 + 1.2) + 0.05 × (0.5 + 0.225 + 2.6)
       = -0.30 + 0.07 + 0.30 × 0.905 + 0.15 × 1.7 + 0.05 × 3.325
       = -0.30 + 0.07 + 0.272 + 0.255 + 0.166
       = +0.463R

  WR esperada ~70% (lock_save ayuda mucho)

  Para RR>1.8 necesito avgR > 1.8. Mi estimación da 0.46R — sigue muy bajo.
  El problema es que el avgR en el engine es P&L / (entry_size × initial_SL_distance)
  que NO es lo mismo que avg_win/avg_loss.

  Si WR=70% y avgR=0.46, significa avg_win = (0.46 + 0.30)/0.70 = 1.09R y
  avg_loss = 1.0R. RR=1.09. Aún lejos de 1.8.

  Para que RR>1.8 con WR=70%:
    avg_win - 0.30/0.70 × avg_loss = 1.8
    avg_win = 1.8 + 0.4286 × 1.0 = 2.23R
  Necesito avg_win > 2.23R. Eso requiere que la mayoría de winners lleguen a +2R+.

  Hipótesis v82f: trail 1.0 ATR permite que winners que llegan a +1R sigan a +2R-3R
  antes de reversar. Si ~40% de winners llegan a +2.5R (partial2), el avg_win sube.

VALIDACIÓN: 12 seeds × 8 perfiles = 96 runs.
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
from v81_universal_v2 import v80_config, compute_sma_slope


TREND_FILTER_THRESHOLD = 0.05
V82F_TP_MULT = 4.0
V82F_TRAIL_ATR = 1.00
V82F_PYRAMID_PCT = 0.50
V82F_PARTIAL1_R = 1.5
V82F_PARTIAL1_PCT = 0.15
V82F_PARTIAL2_R = 2.5
V82F_PARTIAL2_PCT = 0.20


class EngineSimV82f(EngineSimV62):
    """v82f: trail activates after lock (not after partial2)."""

    def __init__(self, config, name):
        super().__init__(config, name)
        self.pnl_long = 0.0
        self.pnl_short = 0.0
        self.trades_long = 0
        self.trades_short = 0
        self.wins_long = 0
        self.wins_short = 0
        self.pnl_long_strat = defaultdict(float)
        self.pnl_short_strat = defaultdict(float)
        self.trades_long_strat = defaultdict(int)
        self.trades_short_strat = defaultdict(int)
        self.regime_samples = defaultdict(int)

    def _close_position(self, sym, exit_price_raw, reason, tick):
        pos = self.positions.get(sym)
        if not pos: return
        is_long = pos.direction == 'LONG'
        strat = pos.strategy
        slip = exit_price_raw * (v38.SLIPPAGE_PCT / 100)
        exit_price = exit_price_raw - slip if is_long else exit_price_raw + slip
        if is_long:
            pnl = (exit_price - pos.entry_price) * pos.qty
            self.pnl_long += pnl
            self.trades_long += 1
            self.pnl_long_strat[strat] += pnl
            self.trades_long_strat[strat] += 1
            if pnl > 0: self.wins_long += 1
        else:
            pnl = (pos.entry_price - exit_price) * pos.qty
            self.pnl_short += pnl
            self.trades_short += 1
            self.pnl_short_strat[strat] += pnl
            self.trades_short_strat[strat] += 1
            if pnl > 0: self.wins_short += 1
        super()._close_position(sym, exit_price_raw, reason, tick)

    def _partial_close(self, sym, exit_price_raw, reason, tick, close_qty):
        pos = self.positions.get(sym)
        if not pos: return
        is_long = pos.direction == 'LONG'
        strat = pos.strategy
        slip = exit_price_raw * (v38.SLIPPAGE_PCT / 100)
        exit_price = exit_price_raw - slip if is_long else exit_price_raw + slip
        if is_long:
            pnl = (exit_price - pos.entry_price) * close_qty
            self.pnl_long += pnl
            self.trades_long += 1
            self.pnl_long_strat[strat] += pnl
            self.trades_long_strat[strat] += 1
            if pnl > 0: self.wins_long += 1
        else:
            pnl = (pos.entry_price - exit_price) * close_qty
            self.pnl_short += pnl
            self.trades_short += 1
            self.pnl_short_strat[strat] += pnl
            self.trades_short_strat[strat] += 1
            if pnl > 0: self.wins_short += 1
        super()._partial_close(sym, exit_price_raw, reason, tick, close_qty)

    def _try_strategy_a(self, sym, prices, tick):
        super()._try_strategy_a(sym, prices, tick)

    def _try_strategy_b(self, sym, prices, tick):
        cfg = self.config.get('B', {})
        if not cfg.get('enabled', True): return
        if tick - self.last_signal_tick.get('B', 0) < 20: return
        if self.strategy_pos_count.get('B', 0) >= cfg.get('max_pos', 1): return
        if len(prices) < 100: return
        if sym in self.positions: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return

        rsi = v38.computeRSI(prices, 14)
        if cfg.get('rsi_lo', 30) <= rsi <= cfg.get('rsi_hi', 70): return

        atr = v38.computeATR(prices, 60)
        if atr <= 0: return
        atr_pct = atr / prices[-1] * 100
        if atr_pct < 0.40: return

        direction = 'LONG' if rsi < 50 else 'SHORT'

        sma_slope = compute_sma_slope(prices, period=100, lookback=10)
        if direction == 'LONG' and sma_slope < -TREND_FILTER_THRESHOLD:
            self.atr_filter_skips = getattr(self, 'atr_filter_skips', 0) + 1
            return
        if direction == 'SHORT' and sma_slope > TREND_FILTER_THRESHOLD:
            self.atr_filter_skips = getattr(self, 'atr_filter_skips', 0) + 1
            return

        b_base_size = 0.15 if atr_pct > 1.2 else 0.30
        if atr_pct < 0.40: size_mult = 0.3
        elif atr_pct < 0.60: size_mult = 0.5
        elif atr_pct < 0.80: size_mult = 0.7
        else: size_mult = 1.0

        eff_cfg = cfg.copy()
        eff_cfg['pos_size_pct'] = b_base_size * size_mult
        eff_cfg['sl_mult'] = 1.5
        eff_cfg['tp_mult'] = V82F_TP_MULT
        eff_cfg['catsl_mult'] = 2.5

        self._open_position_v40(sym, direction, 'B', prices[-1], atr, eff_cfg, tick)
        self.last_signal_tick['B'] = tick

        pos = self.positions.get(sym)
        if pos:
            pos.current_sl = pos.entry_price - atr * 1.5 if direction == 'LONG' else pos.entry_price + atr * 1.5
            pos.current_tp = pos.entry_price + atr * V82F_TP_MULT if direction == 'LONG' else pos.entry_price - atr * V82F_TP_MULT
            pos.catastrophic_sl = pos.entry_price - atr * 2.5 if direction == 'LONG' else pos.entry_price + atr * 2.5
            pos.initial_atr = atr
            pos.initial_sl_distance = atr * 1.5
            pos.lock_done = False
            pos.partial_done = False
            pos.partial1_done = False
            pos.partial2_done = False
            pos.partial3_done = True
            pos.pyramid_done = False
            pos.trail_active = False
            pos.max_favorable_price = pos.entry_price
            pos.trail_atr = atr

    def _try_strategy_d(self, sym, prices, tick):
        super()._try_strategy_d(sym, prices, tick)

    def _try_strategy_e(self, sym, prices, tick):
        return

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

        # Pyramid
        pyramid_trigger = self.config.get('pyramid_trigger_r', None)
        pyramid_pct = V82F_PYRAMID_PCT
        pyramid_strategies = self.config.get('pyramid_strategies', ['B'])

        pyramid_fired = False
        if (pyramid_trigger is not None
                and not getattr(pos, 'pyramid_done', False)
                and pos.strategy in pyramid_strategies
                and r_multiple >= pyramid_trigger):
            self._do_pyramid(pos, price, pyramid_pct, is_long, prices, cfg, sym, r_multiple, level=1)
            pyramid_fired = True

        if pyramid_fired:
            if is_long:
                r_multiple = (price - pos.entry_price) / (pos.initial_sl_distance or 1)
            else:
                r_multiple = (pos.entry_price - price) / (pos.initial_sl_distance or 1)

        # LOCK @ +0.5R → SL = entry + 0.35R + ACTIVATE TRAIL (F1)
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
                # F1: ACTIVATE TRAIL IMMEDIATELY AFTER LOCK
                pos.trail_active = True
                trail_dist = pos.trail_atr * V82F_TRAIL_ATR
                if is_long:
                    new_sl = price - trail_dist
                    # Don't lower SL below the lock level
                    lock_sl = pos.entry_price + lock_r * initial_sl_distance
                    if new_sl < lock_sl: new_sl = lock_sl
                    if new_sl > pos.current_sl: pos.current_sl = new_sl
                else:
                    new_sl = price + trail_dist
                    lock_sl = pos.entry_price - lock_r * initial_sl_distance
                    if new_sl > lock_sl: new_sl = lock_sl
                    if new_sl < pos.current_sl: pos.current_sl = new_sl

        # v82f PARTIALS (same as v82e but trail already active from lock)
        if not getattr(pos, 'partial1_done', False):
            if r_multiple >= V82F_PARTIAL1_R:
                close_qty = pos.qty * V82F_PARTIAL1_PCT
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP1_v82f', tick, close_qty)
                setattr(pos, 'partial1_done', True)

        if not getattr(pos, 'partial2_done', False):
            if r_multiple >= V82F_PARTIAL2_R:
                close_qty = pos.qty * V82F_PARTIAL2_PCT
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP2_v82f', tick, close_qty)
                setattr(pos, 'partial2_done', True)

        # Trailing stop update (1.0 ATR, active from lock onward)
        if pos.trail_active:
            trail_dist = pos.trail_atr * V82F_TRAIL_ATR
            if is_long:
                new_sl = pos.max_favorable_price - trail_dist
                # Never trail below entry once locked (lock floor)
                if pos.lock_done:
                    lock_r = self.config.get('lock_offset_r', 0.2)
                    lock_floor = pos.entry_price + lock_r * initial_sl_distance
                    if new_sl < lock_floor: new_sl = lock_floor
                if new_sl > pos.current_sl: pos.current_sl = new_sl
                pos.current_tp = None
            else:
                new_sl = pos.max_favorable_price + trail_dist
                if pos.lock_done:
                    lock_r = self.config.get('lock_offset_r', 0.2)
                    lock_floor = pos.entry_price - lock_r * initial_sl_distance
                    if new_sl > lock_floor: new_sl = lock_floor
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


def v82f_config():
    cfg = v80_config()
    cfg['pyramid_pct'] = V82F_PYRAMID_PCT
    cfg['v82f_features'] = {
        'tp_mult': V82F_TP_MULT,
        'trail_atr': V82F_TRAIL_ATR,
        'trail_after_lock': True,  # F1: key change
        'pyramid_pct': V82F_PYRAMID_PCT,
        'partial1_r': V82F_PARTIAL1_R, 'partial1_pct': V82F_PARTIAL1_PCT,
        'partial2_r': V82F_PARTIAL2_R, 'partial2_pct': V82F_PARTIAL2_PCT,
        'partial3': 'disabled',
        'trend_filter_threshold': TREND_FILTER_THRESHOLD,
    }
    return cfg


RESULTS_FILE = '/tmp/v82f_universal.json'


def run_seed_profile(seed, profile, n_tokens=10):
    rng = random.Random(seed)
    base = 1.0 * (1 + rng.uniform(-0.3, 0.3))
    all_prices = {f"TOK{i:02d}": gen_profile_prices(v40.TOTAL_TICKS, base * (1 + rng.uniform(-0.2, 0.2)), rng, profile)
                  for i in range(n_tokens)}

    engine = EngineSimV82f(deepcopy(v82f_config()), f'v82f_{profile}')
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


def run_one(seed, profile):
    print(f"  Running seed {seed} × {profile}...", flush=True)
    start = time.time()
    result = run_seed_profile(seed, profile)
    elapsed = time.time() - start
    print(f"    done in {elapsed:.1f}s — P&L {result['pnl']:+.2f}, WR {result['wr']:.1f}%, avgR {result['avg_r']:.3f}, trades {result['trades']}, L/S={result['trades_long']}/{result['trades_short']}", flush=True)
    return result


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == 'one':
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
                    print(f"  Skipping seed {seed} × {profile}")
                    continue
                result = run_one(seed, profile)
                all_results.setdefault(key, {})[profile] = result
                with open(RESULTS_FILE, 'w') as f:
                    json.dump(all_results, f, indent=2)
        print("\n=== ALL DONE ===")
    else:
        print("Usage: python v82f_rr_fix.py [all|one <seed> <profile>]")
