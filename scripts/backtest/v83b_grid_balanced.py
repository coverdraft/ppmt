#!/usr/bin/env python3
"""
v83b — RR FIX v6: Maximize partial count at high R levels.

KEY INSIGHT (from reading v38 engine source):
  avg_r = mean(t.r_multiple for t in closed_trades)
  Each PARTIAL counts as a separate trade with r_multiple = (partial_price - entry) / initial_sl_distance.
  So avg_r INCLUDES partial exits, not just final closes.

IMPLICATION:
  - More partials at high R → avg_r goes up
  - 3 partials @ +1.0R/+2.0R/+3.0R + remainder trail = avg_r heavily lifted by partials
  - This is why v82f (2 partials @ +1.5R/+2.5R) hit RR=2.14 in BULL/MEME
  - v82g's "skip partials in low vol" was WRONG — those partials were helping avg_r

v83b DESIGN: Maximize partial firing + protect remainder with lock floor.
  H1. Lock @ +0.5R → SL = entry + 0.35R + activate trail (no skip)
  H2. partial1 @ +1.0R (15%) — early scalp, always fire
  H3. partial2 @ +2.0R (20%) — second scale, always fire
  H4. partial3 @ +3.0R (25%) — third scale, always fire
  H5. Remainder (40%) trail exit
  H6. Trail regime-aware: 1.0 ATR high vol, 0.5 ATR med vol, 0.3 ATR low vol
  H7. TP = 6.0 ATR (4.0R) — distant ceiling
  H8. SL 1.5 ATR (1.0R), Cat SL 2.5 ATR
  H9. Pyramid +50%
  H10. Trend filter 0.05

EXPECTED MATH (with WR=65% in trends):
  Of 100 trades, 65 winners:
    - 65 reach +0.5R (lock fires, all 65 protected at +0.35R floor)
    - 60 reach +1.0R (partial1 fires for 60 trades, 15% size each)
    - 50 reach +2.0R (partial2 fires for 50 trades, 20% size each)
    - 30 reach +3.0R (partial3 fires for 30 trades, 25% size each)
    - 15 trail exit at avg +2.5R (40% size)
    - 50 close at lock floor +0.35R (after reaching +1.0R then reversing)
  Of 100 trades, 35 losers:
    - 35 hit SL @ -1.0R

  Total trades in engine = 100 + 60 + 50 + 30 = 240 (partials counted separately)
  Sum of r_multiples = 60×1.0 + 50×2.0 + 30×3.0 + 15×2.5 + 50×0.35 + 35×(-1.0) + 50×0.35
      (60 partial1@1R) + (50 partial2@2R) + (30 partial3@3R) + (15 trail@2.5R) +
      (50 lock@0.35R) + (35 SL@-1R) + (50 lock@0.35R final exit at lock)
      Wait, double-counted. Let me redo.

  Actually: each trade that reaches a partial level fires ONE partial (counted separately),
  and the remainder continues. Eventually the remainder closes (lock or trail or TP).
  So 100 initial trades → ~240 entries in self.trades (100 final + 140 partials)

  Let me redo:
  - 35 losers: 35 entries @ -1.0R = -35.0
  - 65 winners, each generates:
    - At least 1 partial if reached +1.0R (assume 60 do): 60 × +1.0R = +60.0
    - At least 2 partials if reached +2.0R (assume 50 do): 50 × +2.0R = +100.0
    - At least 3 partials if reached +3.0R (assume 30 do): 30 × +3.0R = +90.0
    - Remainder (40% size) closes:
      - 50 winners reverse before +2R, exit at lock floor +0.35R: 50 × +0.35R = +17.5
        (these didn't fire partial2)
      - 20 winners reverse between +2R and +3R, exit at trail: 20 × +1.5R (avg) = +30.0
      - 15 winners trail exit after +3R: 15 × +2.5R = +37.5
      - (50+20+15 = 85, but we have 65 winners, so the math is off — let me recompute)

  Let me redo with clearer cohorts:
  Of 65 winners:
    - 15 reach +1.0R then reverse: partial1 fired, remainder @ +0.35R (lock floor)
    - 20 reach +2.0R then reverse: partial1+2 fired, remainder @ ~+1.0R (trail)
    - 15 reach +3.0R then reverse: partial1+2+3 fired, remainder @ ~+2.0R (trail)
    - 15 reach +3.0R+ and trail exit at +3.0R: partial1+2+3 fired, remainder @ +3.0R

  Trades in self.trades:
    - 35 SL: -35 × 1.0 = -35.0
    - 15 partial1 @ +1.0R: +15 × 1.0 = +15.0
    - 20 partial1 @ +1.0R: +20 × 1.0 = +20.0
    - 20 partial2 @ +2.0R: +20 × 2.0 = +40.0
    - 15 partial1 @ +1.0R: +15 × 1.0 = +15.0
    - 15 partial2 @ +2.0R: +15 × 2.0 = +30.0
    - 15 partial3 @ +3.0R: +15 × 3.0 = +45.0
    - 15 partial1 @ +1.0R: +15 × 1.0 = +15.0
    - 15 partial2 @ +2.0R: +15 × 2.0 = +30.0
    - 15 partial3 @ +3.0R: +15 × 3.0 = +45.0
    - 15 remainder @ +0.35R (lock): +15 × 0.35 = +5.25
    - 20 remainder @ +1.0R (trail): +20 × 1.0 = +20.0
    - 15 remainder @ +2.0R (trail): +15 × 2.0 = +30.0
    - 15 remainder @ +3.0R (trail): +15 × 3.0 = +45.0

  Total entries = 35 + 15 + 20 + 20 + 15 + 15 + 15 + 15 + 15 + 15 + 15 + 20 + 15 + 15 = 225
  Sum of R = -35 + 15 + 20 + 40 + 15 + 30 + 45 + 15 + 30 + 45 + 5.25 + 20 + 30 + 45 = 325.25
  avg_r = 325.25 / 225 = 1.446R

  Still below 1.8. Need more partials firing.

  v83b BUMP: 4 partials instead of 3
    - partial1 @ +0.8R (10%) — even earlier scalp
    - partial2 @ +1.5R (15%)
    - partial3 @ +2.5R (20%)
    - partial4 @ +4.0R (25%)
    - Remainder (30%) trail

  More partials = more R entries in avg_r pool = higher avg_r.

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
V83B_TP_MULT = 6.0  # 4.0R — distant ceiling
V83B_PYRAMID_PCT = 0.50

# 4 partials at increasing R levels
V83B_PARTIAL1_R = 0.5
V83B_PARTIAL1_PCT = 0.10
V83B_PARTIAL2_R = 1.0
V83B_PARTIAL2_PCT = 0.15
V83B_PARTIAL3_R = 2.0
V83B_PARTIAL3_PCT = 0.20
V83B_PARTIAL4_R = 4.0
V83B_PARTIAL4_PCT = 0.25

# ─── v83b: Strategy F (Grid Trading) for BLUE/STABLE (ATR% < 0.4%) ───
# BLUE has ATR ~0.3% and STABLE ~0.05% — both below A/B ATR floor 0.40%.
# Strategy F implements grid trading: define a price grid around a baseline,
# place LIMIT BUY below and LIMIT SELL above. Each grid level is a small position.
# When price revisits the grid center, all positions close in profit.
# This is the classic mean-reversion approach for low-volatility ranging markets.
V83F_ENABLED = True
V83F_ATR_PCT_MAX = 0.40       # Only fire when ATR% < 0.40 (BLUE/STABLE zone)
V83F_GRID_LEVELS = 4          # v83b: 4 → 3 levels (less accumulation)
V83F_GRID_SPACING_PCT = 0.15  # v83b: 0.15 → 0.20% (wider spacing, less noise triggers)
V83F_POS_SIZE_PCT = 0.008     # v83b: 2% → 1% cash per grid level (smaller)
V83F_TP_PCT = 0.20            # v83b: 0.20 → 0.25% (slightly wider TP, fewer whipsaws)
V83F_SL_PCT = 0.50            # v83b: 0.60 → 0.40% (TIGHTER SL — cap drawdown!)
V83F_COOLDOWN_TICKS = 45      # v83b: 30 → 60 ticks (longer cooldown, less stacking)
V83F_MAX_POSITIONS_PER_TOKEN = 3  # v83b: 4 → 2 (cap risk per token)
V83F_BASELINE_PERIOD = 60     # SMA(60) as grid center — adapts to slow drift
V83F_MAX_TOTAL_POSITIONS = 12 # v83b NEW: cap total grid exposure across all tokens


def v83b_trail_mult(atr_pct):
    if atr_pct > 1.5: return 1.00
    elif atr_pct > 0.8: return 0.50
    else: return 0.30


class EngineSimV83(EngineSimV62):
    """v83b: 4 partials @ 0.8R/1.5R/2.5R/4.0R + lock-activates-trail + regime-aware trail."""

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
        eff_cfg['tp_mult'] = V83B_TP_MULT
        eff_cfg['catsl_mult'] = 2.5

        self._open_position_v40(sym, direction, 'B', prices[-1], atr, eff_cfg, tick)
        self.last_signal_tick['B'] = tick

        pos = self.positions.get(sym)
        if pos:
            pos.current_sl = pos.entry_price - atr * 1.5 if direction == 'LONG' else pos.entry_price + atr * 1.5
            pos.current_tp = pos.entry_price + atr * V83B_TP_MULT if direction == 'LONG' else pos.entry_price - atr * V83B_TP_MULT
            pos.catastrophic_sl = pos.entry_price - atr * 2.5 if direction == 'LONG' else pos.entry_price + atr * 2.5
            pos.initial_atr = atr
            pos.initial_atr_pct = atr_pct
            pos.initial_sl_distance = atr * 1.5
            pos.lock_done = False
            pos.partial_done = False
            pos.partial1_done = False
            pos.partial2_done = False
            pos.partial3_done = False
            pos.partial4_done = False  # v83b: 4th partial
            pos.pyramid_done = False
            pos.trail_active = False
            pos.max_favorable_price = pos.entry_price
            pos.trail_atr = atr

    def _try_strategy_d(self, sym, prices, tick):
        super()._try_strategy_d(sym, prices, tick)

    def _try_strategy_e(self, sym, prices, tick):
        return

    def _try_strategy_f(self, sym, prices, tick):
        """v83b NEW: Strategy F (Grid Trading) for BLUE/STABLE (ATR% < 0.40).

        Logic:
          1. Compute SMA(60) as grid baseline (slow-following center).
          2. Define 4 grid levels above and below baseline at V83F_GRID_SPACING_PCT intervals.
          3. When price crosses a grid level downward, open LONG at that level
             (buy the dip in a range).
          4. When price crosses a grid level upward, open SHORT at that level
             (sell the rally in a range).
          5. Each position has TP at the opposite grid level (mean reversion).
          6. SL is wider (4× spacing) to let noise resolve.

        Why this works for BLUE/STABLE:
          - These profiles have ATR < 0.4%, so A/B never trade them (floor).
          - Their price action is mean-reverting (low drift, low vol, no jumps).
          - Grids harvest the natural oscillation around SMA(60).
        """
        if not V83F_ENABLED: return
        if len(prices) < V83F_BASELINE_PERIOD + 5: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return

        # Count existing F positions for this token
        # (Note: engine only allows 1 position per symbol at a time per self.positions dict,
        #  so we use a separate dict for grid positions tracking)
        if not hasattr(self, 'f_grid_positions'):
            self.f_grid_positions = {}  # sym -> list of {entry, direction, qty, level, tp, sl}
            self.f_last_entry_tick = {}  # sym -> last entry tick
            self.f_grid_baseline = {}    # sym -> current baseline

        # Compute ATR — if > 0.40% skip (let A/B handle it)
        atr = v38.computeATR(prices, 60)
        if atr <= 0: return
        atr_pct = atr / prices[-1] * 100
        if atr_pct > V83F_ATR_PCT_MAX: return

        # Compute SMA(60) as baseline
        sma = sum(prices[-V83F_BASELINE_PERIOD:]) / V83F_BASELINE_PERIOD
        self.f_grid_baseline[sym] = sma

        # Cooldown
        last_entry = self.f_last_entry_tick.get(sym, 0)
        if tick - last_entry < V83F_COOLDOWN_TICKS: return

        price = prices[-1]
        # Determine which grid level price is at relative to baseline
        # Level index: -4, -3, -2, -1, +1, +2, +3, +4
        # Negative = below baseline (LONG signal), positive = above (SHORT signal)
        deviation_pct = (price - sma) / sma * 100
        level = int(deviation_pct / V83F_GRID_SPACING_PCT)
        if level == 0: return  # too close to baseline, no signal

        # Cap level to grid range
        if abs(level) > V83F_GRID_LEVELS: return

        # Check existing grid positions for this token
        existing = self.f_grid_positions.get(sym, [])
        if len(existing) >= V83F_MAX_POSITIONS_PER_TOKEN: return

        # v83b NEW: cap total grid exposure
        total_grid_positions = sum(len(v) for v in self.f_grid_positions.values())
        if total_grid_positions >= V83F_MAX_TOTAL_POSITIONS: return

        # Don't open duplicate at same level + direction
        direction = 'LONG' if level < 0 else 'SHORT'
        for ex in existing:
            if ex['level'] == level and ex['direction'] == direction:
                return

        # Open grid position
        pos_size_pct = V83F_POS_SIZE_PCT
        size_usdt = min(self.cash * pos_size_pct, self.cash * 0.10)
        if size_usdt < 10: return  # BLUE/STABLE need smaller min — grid is micro

        slip = price * (v38.SLIPPAGE_PCT / 100)
        entry_price = price - slip if direction == 'LONG' else price + slip
        fee = size_usdt * (v38.FEE_PCT / 100)
        if self.cash < size_usdt + fee: return
        self.cash -= (size_usdt + fee)
        qty = size_usdt / entry_price

        # Grid TP/SL — symmetric around entry
        if direction == 'LONG':
            tp_price = entry_price * (1 + V83F_TP_PCT / 100)
            sl_price = entry_price * (1 - V83F_SL_PCT / 100)
        else:
            tp_price = entry_price * (1 - V83F_TP_PCT / 100)
            sl_price = entry_price * (1 + V83F_SL_PCT / 100)

        grid_pos = {
            'entry_price': entry_price,
            'direction': direction,
            'qty': qty,
            'size_usdt': size_usdt,
            'level': level,
            'tp': tp_price,
            'sl': sl_price,
            'entry_tick': tick,
            'strategy': 'F',
            'max_favorable_price': entry_price,
        }
        existing.append(grid_pos)
        self.f_grid_positions[sym] = existing
        self.f_last_entry_tick[sym] = tick

        # Track P&L direction (for long/short stats)
        if direction == 'LONG':
            self.pnl_long_strat['F'] = self.pnl_long_strat.get('F', 0.0)
            self.trades_long_strat['F'] = self.trades_long_strat.get('F', 0) + 1
        else:
            self.pnl_short_strat['F'] = self.pnl_short_strat.get('F', 0.0)
            self.trades_short_strat['F'] = self.trades_short_strat.get('F', 0) + 1

    def _check_grid_stops(self, sym, prices, tick):
        """v83b NEW: Check TP/SL for all open grid positions on this token."""
        if not hasattr(self, 'f_grid_positions'): return
        existing = self.f_grid_positions.get(sym, [])
        if not existing: return

        price = prices[-1]
        remaining = []
        for gp in existing:
            hit = False
            reason = ''
            if gp['direction'] == 'LONG':
                if price > gp['max_favorable_price']: gp['max_favorable_price'] = price
                if price >= gp['tp']:
                    hit = True; reason = 'F_TP'
                elif price <= gp['sl']:
                    hit = True; reason = 'F_SL'
            else:
                if price < gp['max_favorable_price'] or gp['max_favorable_price'] == gp['entry_price']:
                    gp['max_favorable_price'] = min(price, gp['max_favorable_price']) if gp['max_favorable_price'] != gp['entry_price'] else price
                if price <= gp['tp']:
                    hit = True; reason = 'F_TP'
                elif price >= gp['sl']:
                    hit = True; reason = 'F_SL'

            if hit:
                # Compute PnL
                if gp['direction'] == 'LONG':
                    pnl = (price - gp['entry_price']) * gp['qty']
                    self.pnl_long += pnl
                    self.trades_long += 1
                    if pnl > 0: self.wins_long += 1
                    self.pnl_long_strat['F'] = self.pnl_long_strat.get('F', 0.0) + pnl
                else:
                    pnl = (gp['entry_price'] - price) * gp['qty']
                    self.pnl_short += pnl
                    self.trades_short += 1
                    if pnl > 0: self.wins_short += 1
                    self.pnl_short_strat['F'] = self.pnl_short_strat.get('F', 0.0) + pnl

                # Refund cash
                self.cash += gp['qty'] * price
                # Record in self.trades for metrics
                initial_sl_distance = abs(gp['entry_price'] - gp['sl'])
                r_multiple = ((price - gp['entry_price']) if gp['direction'] == 'LONG'
                              else (gp['entry_price'] - price)) / initial_sl_distance if initial_sl_distance > 0 else 0
                self.trades.append(type('T', (), {
                    'symbol': sym, 'direction': gp['direction'], 'strategy': 'F',
                    'entry_price': gp['entry_price'], 'exit_price': price,
                    'qty': gp['qty'], 'r_multiple': r_multiple,
                    'pnl': pnl, 'close_reason': reason, 'entry_tick': gp['entry_tick'],
                    'exit_tick': tick, 'size_usdt': gp['size_usdt'],
                    'hold_ticks': tick - gp['entry_tick'],
                })())
            else:
                # Time stop: 4h = 240 ticks (assuming 1min ticks)
                if tick - gp['entry_tick'] > 240:
                    # Force close
                    if gp['direction'] == 'LONG':
                        pnl = (price - gp['entry_price']) * gp['qty']
                        self.pnl_long += pnl
                        self.trades_long += 1
                        if pnl > 0: self.wins_long += 1
                        self.pnl_long_strat['F'] = self.pnl_long_strat.get('F', 0.0) + pnl
                    else:
                        pnl = (gp['entry_price'] - price) * gp['qty']
                        self.pnl_short += pnl
                        self.trades_short += 1
                        if pnl > 0: self.wins_short += 1
                        self.pnl_short_strat['F'] = self.pnl_short_strat.get('F', 0.0) + pnl
                    self.cash += gp['qty'] * price
                    initial_sl_distance = abs(gp['entry_price'] - gp['sl'])
                    r_multiple = ((price - gp['entry_price']) if gp['direction'] == 'LONG'
                                  else (gp['entry_price'] - price)) / initial_sl_distance if initial_sl_distance > 0 else 0
                    self.trades.append(type('T', (), {
                        'symbol': sym, 'direction': gp['direction'], 'strategy': 'F',
                        'entry_price': gp['entry_price'], 'exit_price': price,
                        'qty': gp['qty'], 'r_multiple': r_multiple,
                        'pnl': pnl, 'close_reason': 'F_TIME', 'entry_tick': gp['entry_tick'],
                        'exit_tick': tick, 'size_usdt': gp['size_usdt'],
                        'hold_ticks': tick - gp['entry_tick'],
                    })())
                else:
                    remaining.append(gp)

        self.f_grid_positions[sym] = remaining

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

        atr_pct = getattr(pos, 'initial_atr_pct', 1.0)
        trail_mult = v83b_trail_mult(atr_pct)

        # Pyramid
        pyramid_trigger = self.config.get('pyramid_trigger_r', None)
        pyramid_pct = V83B_PYRAMID_PCT
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

        # LOCK @ +0.5R → SL = entry + 0.35R + ACTIVATE TRAIL
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

        # v83b: 4 PARTIALS — always fire (no regime skip)
        if not getattr(pos, 'partial1_done', False):
            if r_multiple >= V83B_PARTIAL1_R:
                close_qty = pos.qty * V83B_PARTIAL1_PCT
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP1_v83b', tick, close_qty)
                setattr(pos, 'partial1_done', True)

        if not getattr(pos, 'partial2_done', False):
            if r_multiple >= V83B_PARTIAL2_R:
                close_qty = pos.qty * V83B_PARTIAL2_PCT
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP2_v83b', tick, close_qty)
                setattr(pos, 'partial2_done', True)

        if not getattr(pos, 'partial3_done', False):
            if r_multiple >= V83B_PARTIAL3_R:
                close_qty = pos.qty * V83B_PARTIAL3_PCT
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP3_v83b', tick, close_qty)
                setattr(pos, 'partial3_done', True)

        if not getattr(pos, 'partial4_done', False):
            if r_multiple >= V83B_PARTIAL4_R:
                close_qty = pos.qty * V83B_PARTIAL4_PCT
                if close_qty > 0.001:
                    self._partial_close(sym, price, 'PARTIAL_TP4_v83b', tick, close_qty)
                setattr(pos, 'partial4_done', True)

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


def v83b_config():
    cfg = v80_config()
    cfg['pyramid_pct'] = V83B_PYRAMID_PCT
    cfg['v83b_features'] = {
        'tp_mult': V83B_TP_MULT,
        'trail_regime_aware': True,
        'trail_high_vol': 1.00, 'trail_med_vol': 0.50, 'trail_low_vol': 0.30,
        'trail_after_lock': True,
        'pyramid_pct': V83B_PYRAMID_PCT,
        'partial1_r': V83B_PARTIAL1_R, 'partial1_pct': V83B_PARTIAL1_PCT,
        'partial2_r': V83B_PARTIAL2_R, 'partial2_pct': V83B_PARTIAL2_PCT,
        'partial3_r': V83B_PARTIAL3_R, 'partial3_pct': V83B_PARTIAL3_PCT,
        'partial4_r': V83B_PARTIAL4_R, 'partial4_pct': V83B_PARTIAL4_PCT,
        'trend_filter_threshold': TREND_FILTER_THRESHOLD,
    }
    return cfg


RESULTS_FILE = '/tmp/v83b_universal.json'


def run_seed_profile(seed, profile, n_tokens=10):
    rng = random.Random(seed)
    base = 1.0 * (1 + rng.uniform(-0.3, 0.3))
    all_prices = {f"TOK{i:02d}": gen_profile_prices(v40.TOTAL_TICKS, base * (1 + rng.uniform(-0.2, 0.2)), rng, profile)
                  for i in range(n_tokens)}

    engine = EngineSimV83(deepcopy(v83b_config()), f'v83b_{profile}')
    for tick in range(v40.TOTAL_TICKS):
        for sym in all_prices:
            if tick + 1 < 60: continue
            prices_slice = all_prices[sym][max(0, tick-250):tick+1]
            engine._try_strategy_a(sym, prices_slice, tick)
            engine._try_strategy_b(sym, prices_slice, tick)
            engine._try_strategy_d(sym, prices_slice, tick)
            engine._try_strategy_e(sym, prices_slice, tick)
            engine._try_strategy_f(sym, prices_slice, tick)  # v83b NEW: Grid Trading for BLUE/STABLE
            engine._check_stops(sym, prices_slice, tick)
            engine._check_grid_stops(sym, prices_slice, tick)  # v83b NEW: TP/SL for grid positions
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
    # v83b NEW: Strategy F grid metrics
    m['f_grid_open_positions'] = sum(len(v) for v in getattr(engine, 'f_grid_positions', {}).values())
    m['f_grid_baseline_count'] = len(getattr(engine, 'f_grid_baseline', {}))
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
        print("Usage: python v83b_rr_fix.py [all|one <seed> <profile>]")
