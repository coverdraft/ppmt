#!/usr/bin/env python3
"""
v82b — RR FIX: Position management overhaul to break the RR<1.1 ceiling.

DIAGNOSIS (v81 baseline, 12 seeds × 8 profiles):
  - WR target (>64%): 6/8 profiles PASS ✅
  - RR target (>1.8): 0/8 profiles PASS ❌ (best is MEME at 1.076)
  - Root cause: structural cap on RR from position management:
    * TP at 1.2 ATR but SL at 1.5 ATR → TP is closer than SL → avg_win ≤ 0.8R
    * 3 early partials (5%@0.5R, 10%@1.0R, 15%@1.25R) cut winners short
      Total partial close = 30% before trailing even starts
    * Trail at 0.30 ATR is too tight → exits on first pullback, caps runners
    * Result: avg winner ~0.8R, avg loser ~1.0R → RR ~0.8 (matches observed 0.4-1.1)

v82b FIXES (position management only — strategy logic unchanged from v81):
  P1. REMOVE fixed TP (was 1.2 ATR). Trail is the only exit (besides SL/cat_SL/lock).
      → Eliminates the TP<SL structural cap. Winners can run to +2R, +3R, +5R.
  P2. REMOVE partial1 (was 5% @ +0.5R). Locking 5% at +0.5R drags avg winner down.
  P3. partial2 @ +1.5R close 25% (was @ +1.0R close 10%). First scale-out higher.
  P4. partial3 @ +3.0R close 25% (was @ +1.25R close 15%). Second scale-out much higher.
      Total partial close = 50% (was 30%), but at much higher R levels.
  P5. Trail 0.30 → 0.50 ATR. Wider trail lets trends run further before exit.
  P6. Trail activates after partial2 (was same). No trail before +1.5R.
  P7. Pyramid B +75% → +50%. Less aggressive adding (less risk if reversal).
  P8. Keep SL = 1.5 ATR, Cat SL = 2.5 ATR, Lock @ +0.5R → SL=entry+0.35R (proven).
  P9. Trend filter threshold = 0.05 (back to v81 baseline; ablation showed 0.10 is worse).

EXPECTED IMPACT (math):
  - Average winner: 0.25 × 1.5R + 0.25 × 3.0R + 0.50 × ~3.5R (trail exit) = 0.375 + 0.75 + 1.75 = ~2.875R
    (This is optimistic; if trail exits at +2.0R avg: 0.25 × 1.5 + 0.25 × 3.0 + 0.50 × 2.0 = 0.375 + 0.75 + 1.0 = 2.125R)
  - Average loser: -1.0R (SL) — but ~20% of "losers" become +0.35R via lock, so net avg loser ~ -0.73R
  - RR expected: 2.125 / 0.73 = ~2.9 (well above 1.8 target) ✅
  - WR may drop from 78% → ~65% (less partial locking), but still above 64% target ✅
  - If both hold: 6-8/8 profiles pass both targets (vs 0/8 in v81).

VALIDATION: 12 seeds × 8 profiles = 96 runs.
COMPARE vs v81 (RESULTS_FILE = /tmp/v81_universal.json).
GATE: WR ≥ 64% AND RR ≥ 1.8 in ≥ 6/8 profiles (was 0/8 in v81).
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


# ─────────────────────────────────────────────────────────────────────
# v82b ENGINE — v81 + position management overhaul for RR fix
# ─────────────────────────────────────────────────────────────────────

TREND_FILTER_THRESHOLD = 0.05  # v82b: back to v81 baseline (ablation proved 0.10 worse)


class EngineSimV82b(EngineSimV62):
    """v82b: v81 base + redesigned position management for RR fix.

    Overrides _check_stops with:
    - No fixed TP (trail is the exit)
    - 2 partials only (was 3): @ +1.5R close 25%, @ +3.0R close 25%
    - Trail 0.50 ATR (was 0.30), activates after partial2
    - Pyramid B +50% (was +75%)
    - Keep SL 1.5 ATR, Cat SL 2.5 ATR, Lock +0.5R → +0.35R
    """

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

    # ------------------------------------------------------------------
    # Direction-aware close (from v81, kept identical)
    # ------------------------------------------------------------------
    def _close_position(self, sym, exit_price_raw, reason, tick):
        pos = self.positions.get(sym)
        if not pos:
            return
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
            if pnl > 0:
                self.wins_long += 1
        else:
            pnl = (pos.entry_price - exit_price) * pos.qty
            self.pnl_short += pnl
            self.trades_short += 1
            self.pnl_short_strat[strat] += pnl
            self.trades_short_strat[strat] += 1
            if pnl > 0:
                self.wins_short += 1
        super()._close_position(sym, exit_price_raw, reason, tick)

    def _partial_close(self, sym, exit_price_raw, reason, tick, close_qty):
        pos = self.positions.get(sym)
        if not pos:
            return
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
            if pnl > 0:
                self.wins_long += 1
        else:
            pnl = (pos.entry_price - exit_price) * close_qty
            self.pnl_short += pnl
            self.trades_short += 1
            self.pnl_short_strat[strat] += pnl
            self.trades_short_strat[strat] += 1
            if pnl > 0:
                self.wins_short += 1
        super()._partial_close(sym, exit_price_raw, reason, tick, close_qty)

    # ------------------------------------------------------------------
    # Strategy A: unchanged from v62 (momentum)
    # ------------------------------------------------------------------
    def _try_strategy_a(self, sym, prices, tick):
        super()._try_strategy_a(sym, prices, tick)

    # ------------------------------------------------------------------
    # Strategy B: v81 + trend filter 0.05 + same logic
    # ------------------------------------------------------------------
    def _try_strategy_b(self, sym, prices, tick):
        cfg = self.config.get('B', {})
        if not cfg.get('enabled', True):
            return
        if tick - self.last_signal_tick.get('B', 0) < 20:
            return
        if self.strategy_pos_count.get('B', 0) >= cfg.get('max_pos', 1):
            return
        if len(prices) < 100:
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

        # F1 v82b: TREND FILTER with threshold 0.05 (back to v81 baseline)
        sma_slope = compute_sma_slope(prices, period=100, lookback=10)
        if direction == 'LONG' and sma_slope < -TREND_FILTER_THRESHOLD:
            self.atr_filter_skips = getattr(self, 'atr_filter_skips', 0) + 1
            return
        if direction == 'SHORT' and sma_slope > TREND_FILTER_THRESHOLD:
            self.atr_filter_skips = getattr(self, 'atr_filter_skips', 0) + 1
            return

        # F5: Regime-aware B size
        b_base_size = 0.15 if atr_pct > 1.2 else 0.30

        # F2: Extended tiered sizing
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
            # v82b P1: NO fixed TP (was atr * 1.2). Trail is the only exit.
            pos.current_tp = None
            pos.catastrophic_sl = pos.entry_price - atr * 2.5 if direction == 'LONG' else pos.entry_price + atr * 2.5
            pos.initial_atr = atr
            pos.initial_sl_distance = atr * 1.5
            pos.lock_done = False
            pos.partial_done = False
            pos.partial1_done = False  # v82b: kept for compat, but partial1 is disabled
            pos.partial2_done = False
            pos.partial3_done = False
            pos.pyramid_done = False
            pos.trail_active = False
            pos.max_favorable_price = pos.entry_price
            pos.trail_atr = atr

    def _try_strategy_d(self, sym, prices, tick):
        super()._try_strategy_d(sym, prices, tick)

    def _try_strategy_e(self, sym, prices, tick):
        return  # disabled

    # ------------------------------------------------------------------
    # v82b CORE: Overhauled _check_stops for RR fix
    # ------------------------------------------------------------------
    def _check_stops(self, sym, prices, tick):
        if sym not in self.positions:
            return
        pos = self.positions[sym]
        price = prices[-1]
        cfg = self.config[pos.strategy]
        is_long = pos.direction == 'LONG'

        if is_long:
            if price > pos.max_favorable_price:
                pos.max_favorable_price = price
        else:
            if price < pos.max_favorable_price:
                pos.max_favorable_price = price

        initial_sl_distance = pos.trail_atr * cfg['sl_mult']
        if is_long:
            r_multiple = (price - pos.entry_price) / initial_sl_distance
        else:
            r_multiple = (pos.entry_price - price) / initial_sl_distance

        # ── PYRAMID (v82b P7: +50% instead of +75%) ──
        pyramid_trigger = self.config.get('pyramid_trigger_r', None)
        pyramid_pct = 0.50  # v82b: was 0.75 in v81
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

        # ── LOCK profit at +0.5R → SL = entry + 0.35R (unchanged from v81) ──
        if not pos.lock_done and self.config.get('lock_trigger_r') is not None:
            if r_multiple >= self.config['lock_trigger_r']:
                lock_r = self.config.get('lock_offset_r', 0.2)
                if is_long:
                    new_sl = pos.entry_price + lock_r * initial_sl_distance
                    if new_sl > pos.current_sl:
                        pos.current_sl = new_sl
                else:
                    new_sl = pos.entry_price - lock_r * initial_sl_distance
                    if new_sl < pos.current_sl or pos.current_sl is None:
                        pos.current_sl = new_sl
                pos.lock_done = True

        # ── v82b PARTIALS: only 2 levels, at higher R ──
        # P2: NO partial1 (was 5% @ +0.5R)
        # P3: partial2 @ +1.5R close 25% (was @ +1.0R close 10%)
        # P4: partial3 @ +3.0R close 25% (was @ +1.25R close 15%)
        V82B_PARTIAL2_R = 1.5
        V82B_PARTIAL2_PCT = 0.25
        V82B_PARTIAL3_R = 3.0
        V82B_PARTIAL3_PCT = 0.25

        if not getattr(pos, 'partial2_done', False):
            if r_multiple >= V82B_PARTIAL2_R:
                close_qty = pos.qty * V82B_PARTIAL2_PCT
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP2_v82b', tick, close_qty)
                setattr(pos, 'partial2_done', True)
                # P6: Trail activates after partial2
                pos.trail_active = True
                trail_dist = pos.trail_atr * 0.50  # P5: 0.50 ATR (was 0.30)
                if is_long:
                    new_sl = price - trail_dist
                    if new_sl > pos.current_sl:
                        pos.current_sl = new_sl
                else:
                    new_sl = price + trail_dist
                    if new_sl < pos.current_sl:
                        pos.current_sl = new_sl

        if not getattr(pos, 'partial3_done', False):
            if r_multiple >= V82B_PARTIAL3_R:
                close_qty = pos.qty * V82B_PARTIAL3_PCT
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP3_v82b', tick, close_qty)
                setattr(pos, 'partial3_done', True)
                # Trail continues, may tighten
                trail_dist = pos.trail_atr * 0.50
                if is_long:
                    new_sl = price - trail_dist
                    if new_sl > pos.current_sl:
                        pos.current_sl = new_sl
                else:
                    new_sl = price + trail_dist
                    if new_sl < pos.current_sl:
                        pos.current_sl = new_sl

        # ── Trailing stop update (P5: 0.50 ATR) ──
        if pos.trail_active:
            trail_dist = pos.trail_atr * 0.50
            if is_long:
                new_sl = pos.max_favorable_price - trail_dist
                if new_sl > pos.current_sl:
                    pos.current_sl = new_sl
                pos.current_tp = None
            else:
                new_sl = pos.max_favorable_price + trail_dist
                if new_sl < pos.current_sl:
                    pos.current_sl = new_sl
                pos.current_tp = None

        # ── Time stop (unchanged) ──
        if tick - pos.entry_tick > cfg['time_stop']:
            self._close_position(sym, price, 'TIME', tick)
            cd_key = 'time_cooldown_min' if 'time_cooldown_min' in cfg else 'cooldown_min'
            self.cooldown_until[sym] = tick + int(cfg.get(cd_key, cfg['cooldown_min']) * 60 / v40.TICK_SECONDS)
            return

        # ── SL / CAT_SL (no TP — P1: TP is None) ──
        hit = False
        reason = ''
        if pos.current_sl is not None:
            if is_long and price <= pos.current_sl:
                hit = True
                reason = 'SL'
            elif not is_long and price >= pos.current_sl:
                hit = True
                reason = 'SL'
        if not hit and pos.catastrophic_sl is not None:
            if is_long and price <= pos.catastrophic_sl:
                hit = True
                reason = 'CAT_SL'
            elif not is_long and price >= pos.catastrophic_sl:
                hit = True
                reason = 'CAT_SL'
        if hit:
            self._close_position(sym, price, reason, tick)
            if reason == 'TP':
                cd_min = cfg.get('tp_cooldown_min', cfg['cooldown_min'])
            else:
                cd_min = cfg['cooldown_min']
            self.cooldown_until[sym] = tick + int(cd_min * 60 / v40.TICK_SECONDS)


def v82b_config():
    """v82b config: v81 base + RR-fix position management."""
    cfg = v80_config()
    # Override pyramid_pct to 0.50 (was 0.75 in v81)
    cfg['pyramid_pct'] = 0.50
    # Disable partial1 (v82b P2)
    cfg['partial1_trigger_r'] = None
    cfg['partial1_close_pct'] = 0
    # partial2 and partial3 are handled in _check_stops override (hardcoded for clarity)
    cfg['v82b_features'] = {
        'no_fixed_tp': True,                # P1
        'no_partial1': True,                # P2
        'partial2_r': 1.5, 'partial2_pct': 0.25,  # P3
        'partial3_r': 3.0, 'partial3_pct': 0.25,  # P4
        'trail_atr': 0.50,                  # P5
        'trail_after_partial2': True,       # P6
        'pyramid_pct': 0.50,                # P7
        'sl_mult': 1.5,                     # P8 (unchanged)
        'catsl_mult': 2.5,                  # P8 (unchanged)
        'lock_trigger_r': 0.5, 'lock_offset_r': 0.35,  # P8 (unchanged)
        'trend_filter_threshold': 0.05,     # P9 (back to v81 baseline)
    }
    return cfg


# ─────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────

RESULTS_FILE = '/tmp/v82b_universal.json'
V81_RESULTS = '/tmp/v81_universal.json'


def run_seed_profile(seed, profile, n_tokens=10):
    rng = random.Random(seed)
    base = 1.0 * (1 + rng.uniform(-0.3, 0.3))
    all_prices = {f"TOK{i:02d}": gen_profile_prices(v40.TOTAL_TICKS, base * (1 + rng.uniform(-0.2, 0.2)), rng, profile)
                  for i in range(n_tokens)}

    engine = EngineSimV82b(deepcopy(v82b_config()), f'v82b_{profile}')
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

    print("=" * 180)
    print(f"{'Profile':<10} {'Ver':<6} {'Trades':<8} {'WR%':<8} {'avgR':<8} {'P&L':<12} {'PF':<7} {'MaxDD%':<8} {'Profit%':<10} {'L/S':<10} {'L P&L':<12} {'S P&L':<12} {'L WR%':<7} {'S WR%':<7}")
    print("=" * 180)
    summary = []
    for profile in PROFILES:
        # v81 baseline
        v81_seeds = [r for r in v81_results.values() if profile in r]
        if v81_seeds:
            ms = [r[profile] for r in v81_seeds]
            v81_pnl = statistics.mean(m['pnl'] for m in ms)
            v81_profit = sum(1 for m in ms if m['pnl'] > 0) / len(ms) * 100
            v81_wr = statistics.mean(m['wr'] for m in ms)
            v81_avgr = statistics.mean(m['avg_r'] for m in ms)
            v81_maxdd = statistics.mean(m['max_dd'] for m in ms)
            v81_pf = statistics.mean(m['pf'] for m in ms)
            v81_trades = statistics.mean(m['trades'] for m in ms)
            v81_l_t = statistics.mean(m['trades_long'] for m in ms)
            v81_s_t = statistics.mean(m['trades_short'] for m in ms)
            v81_l_pnl = statistics.mean(m['pnl_long'] for m in ms)
            v81_s_pnl = statistics.mean(m['pnl_short'] for m in ms)
            v81_l_wr = statistics.mean(m['wr_long'] for m in ms)
            v81_s_wr = statistics.mean(m['wr_short'] for m in ms)
            print(f"{profile:<10} {'v81':<6} {v81_trades:<8.0f} {v81_wr:<8.1f} {v81_avgr:<8.3f} {v81_pnl:<+12.2f} {v81_pf:<7.2f} {v81_maxdd:<8.2f} {v81_profit:<10.0f} {int(v81_l_t)}/{int(v81_s_t):<9} {v81_l_pnl:<+12.2f} {v81_s_pnl:<+12.2f} {v81_l_wr:<7.1f} {v81_s_wr:<7.1f}")
        # v82b
        v82_seeds = [r for r in all_results.values() if profile in r]
        if v82_seeds:
            ms = [r[profile] for r in v82_seeds]
            v82_pnl = statistics.mean(m['pnl'] for m in ms)
            v82_profit = sum(1 for m in ms if m['pnl'] > 0) / len(ms) * 100
            v82_wr = statistics.mean(m['wr'] for m in ms)
            v82_avgr = statistics.mean(m['avg_r'] for m in ms)
            v82_maxdd = statistics.mean(m['max_dd'] for m in ms)
            v82_pf = statistics.mean(m['pf'] for m in ms)
            v82_trades = statistics.mean(m['trades'] for m in ms)
            v82_l_t = statistics.mean(m['trades_long'] for m in ms)
            v82_s_t = statistics.mean(m['trades_short'] for m in ms)
            v82_l_pnl = statistics.mean(m['pnl_long'] for m in ms)
            v82_s_pnl = statistics.mean(m['pnl_short'] for m in ms)
            v82_l_wr = statistics.mean(m['wr_long'] for m in ms)
            v82_s_wr = statistics.mean(m['wr_short'] for m in ms)
            delta_pnl = v82_pnl - v81_pnl if v81_seeds else 0
            delta_avgr = v82_avgr - v81_avgr if v81_seeds else 0
            marker = "✅" if v82_avgr > v81_avgr else "❌"
            print(f"{profile:<10} {'v82b':<6} {v82_trades:<8.0f} {v82_wr:<8.1f} {v82_avgr:<8.3f} {v82_pnl:<+12.2f} {v82_pf:<7.2f} {v82_maxdd:<8.2f} {v82_profit:<10.0f} {int(v82_l_t)}/{int(v82_s_t):<9} {v82_l_pnl:<+12.2f} {v82_s_pnl:<+12.2f} {v82_l_wr:<7.1f} {v82_s_wr:<7.1f}  {marker} ΔR{delta_avgr:+.3f} ΔP&L{delta_pnl:+.2f}")
            summary.append({
                'profile': profile,
                'v81_pnl': v81_pnl if v81_seeds else 0,
                'v82_pnl': v82_pnl,
                'delta_pnl': delta_pnl,
                'v81_profit': v81_profit if v81_seeds else 0,
                'v82_profit': v82_profit,
                'v81_wr': v81_wr if v81_seeds else 0,
                'v82_wr': v82_wr,
                'v81_avgr': v81_avgr if v81_seeds else 0,
                'v82_avgr': v82_avgr,
                'delta_avgr': delta_avgr,
                'v81_maxdd': v81_maxdd if v81_seeds else 0,
                'v82_maxdd': v82_maxdd,
            })
        print("-" * 180)

    # Verdict with quality gate
    print("\n" + "=" * 100)
    print("VEREDICTO v82b vs v81 + QUALITY GATE")
    print("=" * 100)
    print(f"{'Profile':<10} {'v81 WR/RR':<18} {'v82b WR/RR':<18} {'v82b PASS?':<15}")
    print("-" * 100)
    pass_count = 0
    for s in summary:
        v81_str = f"{s['v81_wr']:.1f}% / {s['v81_avgr']:.2f}"
        v82_str = f"{s['v82_wr']:.1f}% / {s['v82_avgr']:.2f}"
        wr_ok = s['v82_wr'] >= 64
        rr_ok = s['v82_avgr'] >= 1.8
        both = wr_ok and rr_ok
        if both:
            pass_count += 1
        verdict = "✅ PASS" if both else "❌ FAIL"
        print(f"{s['profile']:<10} {v81_str:<18} {v82_str:<18} {verdict}")
    print(f"\nQUALITY GATE: {pass_count}/8 profiles pass WR>64% AND RR>1.8")
    print(f"  Target: ≥ 6/8 to deploy v82b")
    print(f"  v81 baseline: 0/8 (control)")

    with open('/tmp/v82b_summary.json', 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved to /tmp/v82b_summary.json")


def run_one(seed, profile):
    print(f"  Running seed {seed} × {profile}...", flush=True)
    start = time.time()
    result = run_seed_profile(seed, profile)
    elapsed = time.time() - start
    print(f"    done in {elapsed:.1f}s — P&L {result['pnl']:+.2f}, trades {result['trades']}, avgR {result['avg_r']:.3f}, L/S={result['trades_long']}/{result['trades_short']}", flush=True)
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
        print("  python v82b_rr_fix.py all")
        print("  python v82b_rr_fix.py one <seed> <profile>")
        print("  python v82b_rr_fix.py aggregate")
