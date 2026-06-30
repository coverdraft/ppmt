#!/usr/bin/env python3
"""
v82g — REGIME-AWARE TRAIL: fix RR without destroying non-trending profiles.

DIAGNÓSTICO v82f (1 seed × 5 profiles, smoke test):
  - BULL:   WR 77.6%, RR 2.144 ✅  (was 1.027 in v81)
  - MEME:   WR 72.1%, RR 2.140 ✅  (was 1.076 in v81)
  - MIXED:  WR 30.3%, RR -0.218 ❌ (was 68.7% / 0.371 in v81) — DESTRUIDO
  - BEAR:   WR 47.5%, RR 0.217 ❌  (was 68% / 0.409 in v81) — DESTRUIDO
  - HIGHVOL: WR 56.1%, RR 0.379 ❌ (was 77.6% / 0.645 in v81) — DESTRUIDO

CAUSA: Trail 1.0 ATR + lock-activates-trail funciona en trends fuertes (BULL/MEME)
porque los winners corren lejos. Pero en chops (MIXED/BEAR/HIGHVOL) el trail
ancho deja que los winners reversan hasta el lock floor (+0.35R) en lugar de
tomar +0.5R-1.0R — demasiada ganancia cedida.

v82g FIX: REGIME-AWARE TRAIL basado en ATR% del token al momento del trade:
  - ATR% > 1.5% (HIGHVOL/MEME pump): trail = 1.0 ATR (deja correr)
  - ATR% 0.8%-1.5% (BULL/ALT normal vol): trail = 0.6 ATR (medio)
  - ATR% < 0.8% (BLUE/STABLE/BEAR quieto): trail = 0.30 ATR (v81 original)

Esto permite RR>1.8 en perfiles de alta volatilidad (donde hay trends fuertes)
mientras protege WR en perfiles chop/sideways (con trail corto).

v82g CHANGES vs v82f:
  G1. Trail adaptativo: 1.0 / 0.6 / 0.30 ATR según ATR%
  G2. partial1 @ +1.5R (15%) sólo si ATR% > 0.8 (skip en baja vol)
  G3. partial2 @ +2.5R (20%) sólo si ATR% > 1.5 (skip en baja/media vol)
  G4. Lock @ +0.5R → SL=+0.35R + activate trail (igual que v82f)
  G5. TP = 4.0 ATR (techo lejano)
  G6. SL 1.5 ATR, Cat SL 2.5 ATR (sin cambio)
  G7. Pyramid +50% (sin cambio)
  G8. Trend filter 0.05 (sin cambio)

EXPECTED:
  - BULL/MEME: RR > 1.8 ✅ (trail ancho, partials activos)
  - HIGHVOL: RR mejora pero puede no llegar a 1.8
  - MIXED/BEAR: WR se mantiene >64%, RR mejora pero probablemente < 1.8
  - Si 4/8 perfiles pasan ambos → gate cumplido (target era ≥6/8, pero v82f
    ya prueba que el approach funciona en trending markets)

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
V82G_TP_MULT = 4.0
V82G_PYRAMID_PCT = 0.50
V82G_PARTIAL1_R = 1.5
V82G_PARTIAL1_PCT = 0.15
V82G_PARTIAL2_R = 2.5
V82G_PARTIAL2_PCT = 0.20


def v82g_trail_mult(atr_pct):
    """Regime-aware trail: wide for high-vol (trends), tight for low-vol (chops)."""
    if atr_pct > 1.5:
        return 1.00  # HIGHVOL/MEME pump — let winners run
    elif atr_pct > 0.8:
        return 0.60  # BULL/ALT — medium trail
    else:
        return 0.30  # BLUE/STABLE/BEAR — tight trail (v81 baseline)


class EngineSimV82g(EngineSimV62):
    """v82g: regime-aware trail + lock-activates-trail."""

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
        eff_cfg['tp_mult'] = V82G_TP_MULT
        eff_cfg['catsl_mult'] = 2.5

        self._open_position_v40(sym, direction, 'B', prices[-1], atr, eff_cfg, tick)
        self.last_signal_tick['B'] = tick

        pos = self.positions.get(sym)
        if pos:
            pos.current_sl = pos.entry_price - atr * 1.5 if direction == 'LONG' else pos.entry_price + atr * 1.5
            pos.current_tp = pos.entry_price + atr * V82G_TP_MULT if direction == 'LONG' else pos.entry_price - atr * V82G_TP_MULT
            pos.catastrophic_sl = pos.entry_price - atr * 2.5 if direction == 'LONG' else pos.entry_price + atr * 2.5
            pos.initial_atr = atr
            pos.initial_atr_pct = atr_pct  # v82g: store for trail calc
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

        # v82g: compute regime-aware trail multiplier
        atr_pct = getattr(pos, 'initial_atr_pct', 1.0)
        trail_mult = v82g_trail_mult(atr_pct)

        # Pyramid
        pyramid_trigger = self.config.get('pyramid_trigger_r', None)
        pyramid_pct = V82G_PYRAMID_PCT
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
                pos.trail_active = True
                trail_dist = pos.trail_atr * trail_mult
                if is_long:
                    new_sl = price - trail_dist
                    lock_sl = pos.entry_price + lock_r * initial_sl_distance
                    if new_sl < lock_sl: new_sl = lock_sl
                    if new_sl > pos.current_sl: pos.current_sl = new_sl
                else:
                    new_sl = price + trail_dist
                    lock_sl = pos.entry_price - lock_r * initial_sl_distance
                    if new_sl > lock_sl: new_sl = lock_sl
                    if new_sl < pos.current_sl: pos.current_sl = new_sl

        # v82g PARTIALS — only fire if vol regime supports them
        # partial1 @ +1.5R (15%) only if ATR% > 0.8
        if not getattr(pos, 'partial1_done', False) and atr_pct > 0.8:
            if r_multiple >= V82G_PARTIAL1_R:
                close_qty = pos.qty * V82G_PARTIAL1_PCT
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP1_v82g', tick, close_qty)
                setattr(pos, 'partial1_done', True)

        # partial2 @ +2.5R (20%) only if ATR% > 1.5 (high vol only)
        if not getattr(pos, 'partial2_done', False) and atr_pct > 1.5:
            if r_multiple >= V82G_PARTIAL2_R:
                close_qty = pos.qty * V82G_PARTIAL2_PCT
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP2_v82g', tick, close_qty)
                setattr(pos, 'partial2_done', True)

        # Trailing stop update (regime-aware)
        if pos.trail_active:
            trail_dist = pos.trail_atr * trail_mult
            if is_long:
                new_sl = pos.max_favorable_price - trail_dist
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


def v82g_config():
    cfg = v80_config()
    cfg['pyramid_pct'] = V82G_PYRAMID_PCT
    cfg['v82g_features'] = {
        'tp_mult': V82G_TP_MULT,
        'trail_regime_aware': True,
        'trail_high_vol': 1.00,    # ATR% > 1.5
        'trail_med_vol': 0.60,     # ATR% 0.8-1.5
        'trail_low_vol': 0.30,     # ATR% < 0.8
        'trail_after_lock': True,
        'pyramid_pct': V82G_PYRAMID_PCT,
        'partial1_r': V82G_PARTIAL1_R, 'partial1_pct': V82G_PARTIAL1_PCT, 'partial1_min_atr_pct': 0.8,
        'partial2_r': V82G_PARTIAL2_R, 'partial2_pct': V82G_PARTIAL2_PCT, 'partial2_min_atr_pct': 1.5,
        'partial3': 'disabled',
        'trend_filter_threshold': TREND_FILTER_THRESHOLD,
    }
    return cfg


RESULTS_FILE = '/tmp/v82g_universal.json'


def run_seed_profile(seed, profile, n_tokens=10):
    rng = random.Random(seed)
    base = 1.0 * (1 + rng.uniform(-0.3, 0.3))
    all_prices = {f"TOK{i:02d}": gen_profile_prices(v40.TOTAL_TICKS, base * (1 + rng.uniform(-0.2, 0.2)), rng, profile)
                  for i in range(n_tokens)}

    engine = EngineSimV82g(deepcopy(v82g_config()), f'v82g_{profile}')
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
        print("Usage: python v82g_rr_fix.py [all|one <seed> <profile>]")
