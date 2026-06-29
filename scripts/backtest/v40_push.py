#!/usr/bin/env python3
"""
v40 — Push v38g further toward 71% WR with stable profitability

v38g champion (8 seeds): WR 66.7%, P&L +30.97, Profit 88%, MaxDD 0.26%, AvgR +0.60, PF 1.85

NEW IDEAS (none tested before):
1. v40a — Trend filter SMA(100): only LONG if price>SMA100, only SHORT if price<SMA100
2. v40b — Multi-partial TP: 30%@0.5R + 30%@1.0R + 40% trailing (was 40%@0.7R + 60% trail)
3. v40c — Quick re-entry: cooldown 20min after TP, 45min after SL (was 45/45)
4. v40d — Tighter trail 0.4 ATR (was 0.5): lock more profit
5. v40e — Combo: trend filter + multi-partial + quick re-entry + tighter trail
6. v40f — Lock earlier 0.4R (was 0.5) + tighter trail 0.4
7. v40g — Wider partial 50%@0.7R + tighter trail 0.4
8. v40h — Trend filter + adaptive SL (1.6 ATR if momentum strong, else 1.4)
"""
import random, statistics, math, sys, os, json, time
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from copy import deepcopy

sys.path.insert(0, '/home/z/my-project/scripts')
import v38_push_v37e as v38

N_TOKENS = v38.N_TOKENS
SIM_HOURS = v38.SIM_HOURS
TOTAL_TICKS = v38.TOTAL_TICKS
TICK_SECONDS = v38.TICK_SECONDS
FEE_PCT = v38.FEE_PCT
SLIPPAGE_PCT = v38.SLIPPAGE_PCT
TICKS_PER_HOUR = v38.TICKS_PER_HOUR


def computeSMA(prices, period=100):
    if len(prices) < period: return None
    slice_ = prices[-period:]
    return sum(slice_) / len(slice_)


class EngineSimV40(v38.EngineSim):
    """V40 engine with trend filter, multi-partial, adaptive SL support."""

    def __init__(self, config, name, capital=12000):
        super().__init__(config, name, capital)
        self.trend_filter_skips = 0
        self.adaptive_sl_widens = 0

    def _trend_allows(self, direction, prices):
        """Return True if trend filter allows this direction."""
        sma_period = self.config.get('trend_sma_period')
        if sma_period is None:
            return True
        sma = computeSMA(prices, sma_period)
        if sma is None:
            return True  # not enough data, allow
        current = prices[-1]
        if direction == 'LONG' and current < sma * (1 - self.config.get('trend_buffer_pct', 0) / 100):
            self.trend_filter_skips += 1
            return False
        if direction == 'SHORT' and current > sma * (1 + self.config.get('trend_buffer_pct', 0) / 100):
            self.trend_filter_skips += 1
            return False
        return True

    def _try_strategy_a(self, sym, prices, tick):
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
        rsi = v38.computeRSI(prices, 14)
        if rsi < cfg['rsi_min'] or rsi > cfg['rsi_max']: return
        atr = v38.computeATR(prices, 60)
        atr_pct = atr / prices[-1] * 100
        if self.config.get('atr_floor_pct') is not None and atr_pct < self.config['atr_floor_pct']:
            self.atr_filter_skips += 1
            return
        direction = 'LONG' if momentum > 0 else 'SHORT'
        # Trend filter
        if not self._trend_allows(direction, prices): return
        # Adaptive SL: wider if momentum strong
        eff_cfg = dict(cfg)
        if self.config.get('adaptive_sl', False) and abs(momentum) > 0.6:
            eff_cfg['sl_mult'] = cfg['sl_mult'] * 1.15
            self.adaptive_sl_widens += 1
        self._open_position_v40(sym, direction, 'A', prices[-1], atr, eff_cfg, tick)
        self.last_signal_tick['A'] = tick

    def _try_strategy_b(self, sym, prices, tick):
        cfg = self.config.get('B', {})
        if not cfg.get('enabled', True): return
        if tick - self.last_signal_tick['B'] < 20: return
        if self.strategy_pos_count['B'] >= cfg['max_pos']: return
        if len(prices) < 60: return
        if sym in self.positions: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return
        rsi = v38.computeRSI(prices, 14)
        if cfg['rsi_lo'] <= rsi <= cfg['rsi_hi']: return
        atr = v38.computeATR(prices, 60)
        atr_pct = atr / prices[-1] * 100
        if self.config.get('atr_floor_pct') is not None and atr_pct < self.config['atr_floor_pct']:
            self.atr_filter_skips += 1
            return
        direction = 'LONG' if rsi < 50 else 'SHORT'
        # Trend filter (mean reversion: invert direction - allow if price is FAR from SMA, not aligned)
        # For mean reversion, we want price to be extended away from SMA, so trend filter is opposite
        sma_period = self.config.get('trend_sma_period_b')
        if sma_period is not None:
            sma = computeSMA(prices, sma_period)
            if sma is not None:
                # For LONG (oversold), price should be below SMA (extended down)
                # For SHORT (overbought), price should be above SMA (extended up)
                if direction == 'LONG' and prices[-1] > sma:
                    self.trend_filter_skips += 1
                    return
                if direction == 'SHORT' and prices[-1] < sma:
                    self.trend_filter_skips += 1
                    return
        self._open_position_v40(sym, direction, 'B', prices[-1], atr, cfg, tick)
        self.last_signal_tick['B'] = tick

    def _try_strategy_d(self, sym, prices, tick):
        # Vol squeeze breakout — no trend filter (breakout can be in any direction)
        cfg = self.config.get('D', {})
        if not cfg.get('enabled', True): return
        if tick - self.last_signal_tick['D'] < 40: return
        if self.strategy_pos_count['D'] >= cfg['max_pos']: return
        if len(prices) < 55: return
        if sym in self.positions: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return
        bb = v38.computeBollinger(prices, 50, 2)
        if bb['width'] <= 0 or bb['width'] > cfg['bb_width_max']: return
        current = prices[-1]
        if not (current > bb['upper'] or current < bb['lower']): return
        atr = v38.computeATR(prices, 60)
        atr_pct = atr / prices[-1] * 100
        if self.config.get('atr_floor_pct') is not None and atr_pct < self.config['atr_floor_pct']:
            self.atr_filter_skips += 1
            return
        direction = 'LONG' if current > bb['upper'] else 'SHORT'
        if not self._trend_allows(direction, prices): return
        self._open_position_v40(sym, direction, 'D', prices[-1], atr, cfg, tick)
        self.last_signal_tick['D'] = tick

    def _open_position_v40(self, sym, direction, strategy, price, atr, cfg, tick):
        """Wrapper that uses parent _open_position."""
        # Inject eff_cfg into config temporarily — easier: just call parent
        # We need to pass eff_cfg, but parent uses self.config[strategy]. We'll save/restore.
        original_cfg = self.config[strategy]
        self.config[strategy] = cfg
        try:
            self._open_position(sym, direction, strategy, price, atr, cfg, tick)
        finally:
            self.config[strategy] = original_cfg

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

        # LOCK profit at lock_trigger_r (move SL to entry + lock_offset_r * sl_distance)
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

        # MULTI-PARTIAL: support 2 levels (partial1_trigger_r + partial2_trigger_r)
        multi_mode = self.config.get('multi_partial', False)
        if multi_mode:
            # Level 1
            if not getattr(pos, 'partial1_done', False) and self.config.get('partial1_trigger_r') is not None:
                if r_multiple >= self.config['partial1_trigger_r']:
                    pct1 = self.config.get('partial1_close_pct', 0.30)
                    close_qty = pos.qty * pct1
                    if close_qty > 0.001:
                        self._partial_close(sym, price, 'PARTIAL_TP1', tick, close_qty)
                    setattr(pos, 'partial1_done', True)
            # Level 2
            if not getattr(pos, 'partial2_done', False) and self.config.get('partial2_trigger_r') is not None:
                if r_multiple >= self.config['partial2_trigger_r']:
                    pct2 = self.config.get('partial2_close_pct', 0.30)
                    close_qty = pos.qty * pct2
                    if close_qty > 0.001:
                        self._partial_close(sym, price, 'PARTIAL_TP2', tick, close_qty)
                    setattr(pos, 'partial2_done', True)
                    # Enable trailing after final partial
                    if self.config.get('trail_after_partial', False):
                        pos.trail_active = True
                        trail_dist = pos.trail_atr * self.config.get('trail_atr_mult', 0.5)
                        if is_long:
                            new_sl = price - trail_dist
                            if new_sl > pos.current_sl: pos.current_sl = new_sl
                        else:
                            new_sl = price + trail_dist
                            if new_sl < pos.current_sl: pos.current_sl = new_sl
        else:
            # Single partial (v38g behavior)
            if not pos.partial_done and self.config.get('partial_trigger_r') is not None:
                if r_multiple >= self.config['partial_trigger_r']:
                    partial_pct = self.config.get('partial_close_pct', 0.5)
                    close_qty = pos.qty * partial_pct
                    if close_qty > 0.001:
                        self._partial_close(sym, price, 'PARTIAL_TP', tick, close_qty)
                    pos.partial_done = True
                    if self.config.get('trail_after_partial', False):
                        pos.trail_active = True
                        trail_dist = pos.trail_atr * self.config.get('trail_atr_mult', 0.5)
                        if is_long:
                            new_sl = price - trail_dist
                            if new_sl > pos.current_sl: pos.current_sl = new_sl
                        else:
                            new_sl = price + trail_dist
                            if new_sl < pos.current_sl: pos.current_sl = new_sl

        # Trailing stop update
        if pos.trail_active:
            trail_dist = pos.trail_atr * self.config.get('trail_atr_mult', 0.5)
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
            # Quick re-entry: shorter cooldown after TIME
            cd_key = 'time_cooldown_min' if 'time_cooldown_min' in cfg else 'cooldown_min'
            self.cooldown_until[sym] = tick + int(cfg.get(cd_key, cfg['cooldown_min']) * 60 / TICK_SECONDS)
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
            # Quick re-entry: shorter cooldown after TP
            if reason == 'TP':
                cd_min = cfg.get('tp_cooldown_min', cfg['cooldown_min'])
            else:
                cd_min = cfg['cooldown_min']
            self.cooldown_until[sym] = tick + int(cd_min * 60 / TICK_SECONDS)


def make_strategies(sl_mult=1.4, tp_mult=1.2, rsi_lo=30, rsi_hi=70, momentum_min=0.40,
                    tp_cooldown_min=45, sl_cooldown_min=45):
    return {
        'A': {'momentum_min': momentum_min, 'max_pos': 1, 'sl_mult': sl_mult, 'tp_mult': tp_mult,
              'catsl_mult': 4.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'rsi_min': 25, 'rsi_max': 75, 'time_stop': 2400, 'pos_size_pct': 0.025},
        'B': {'rsi_lo': rsi_lo, 'rsi_hi': rsi_hi, 'max_pos': 1, 'sl_mult': sl_mult, 'tp_mult': tp_mult,
              'catsl_mult': 4.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'enabled': True, 'time_stop': 2400, 'pos_size_pct': 0.10},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': sl_mult * 0.75, 'tp_mult': tp_mult * 0.83,
              'catsl_mult': 3.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'time_stop': 2400, 'pos_size_pct': 0.05},
        'E': {'enabled': False},
    }


def make_config(strategies=None, lock_r=0.5, lock_offset_r=0.2,
                partial_r=0.7, partial_pct=0.4,
                partial1_r=None, partial1_pct=None, partial2_r=None, partial2_pct=None,
                multi_partial=False,
                trail_after_partial=True, trail_atr=0.5, atr_floor_pct=0.58,
                trend_sma_period=None, trend_sma_period_b=None, trend_buffer_pct=0.0,
                adaptive_sl=False):
    if strategies is None: strategies = make_strategies()
    cfg = {
        **strategies,
        'partial_trigger_r': partial_r, 'partial_close_pct': partial_pct,
        'be_trigger_r': None, 'lock_trigger_r': lock_r, 'lock_offset_r': lock_offset_r,
        'trail_after_partial': trail_after_partial, 'trail_atr_mult': trail_atr,
        'adaptive_size': False,
        'atr_floor_pct': atr_floor_pct,
        'multi_partial': multi_partial,
        'partial1_trigger_r': partial1_r, 'partial1_close_pct': partial1_pct,
        'partial2_trigger_r': partial2_r, 'partial2_close_pct': partial2_pct,
        'trend_sma_period': trend_sma_period,
        'trend_sma_period_b': trend_sma_period_b,
        'trend_buffer_pct': trend_buffer_pct,
        'adaptive_sl': adaptive_sl,
    }
    return cfg


# v38g champion as baseline + 8 new variants
CONFIGS = {
    # v38g champion (control group)
    'v38g_baseline': make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, partial_r=0.7, partial_pct=0.4, trail_atr=0.5, atr_floor_pct=0.58,
    ),
    # v40a — Trend filter SMA(100) on A and D
    'v40a_trend_sma100': make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, partial_r=0.7, partial_pct=0.4, trail_atr=0.5, atr_floor_pct=0.58,
        trend_sma_period=100, trend_buffer_pct=0.1,
    ),
    # v40b — Multi-partial TP: 30%@0.5R + 30%@1.0R + 40% trailing
    'v40b_multi_partial': make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.30,
        partial2_r=1.0, partial2_pct=0.30,
        trail_atr=0.5, atr_floor_pct=0.58,
    ),
    # v40c — Quick re-entry: cooldown 20min after TP, 45min after SL
    'v40c_quick_reentry': make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2, tp_cooldown_min=20, sl_cooldown_min=45),
        lock_r=0.5, partial_r=0.7, partial_pct=0.4, trail_atr=0.5, atr_floor_pct=0.58,
    ),
    # v40d — Tighter trail 0.4 ATR (lock more profit)
    'v40d_tight_trail_0.4': make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, partial_r=0.7, partial_pct=0.4, trail_atr=0.4, atr_floor_pct=0.58,
    ),
    # v40e — Combo: trend filter + multi-partial + quick re-entry + tight trail
    'v40e_combo_all': make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2, tp_cooldown_min=20, sl_cooldown_min=45),
        lock_r=0.5, multi_partial=True,
        partial1_r=0.5, partial1_pct=0.30,
        partial2_r=1.0, partial2_pct=0.30,
        trail_atr=0.4, atr_floor_pct=0.58,
        trend_sma_period=100, trend_buffer_pct=0.1,
    ),
    # v40f — Lock earlier 0.4R + tighter trail 0.4
    'v40f_lock_0.4_trail_0.4': make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.4, partial_r=0.7, partial_pct=0.4, trail_atr=0.4, atr_floor_pct=0.58,
    ),
    # v40g — Wider partial 50%@0.7R + tighter trail 0.4
    'v40g_partial_50_trail_0.4': make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, partial_r=0.7, partial_pct=0.5, trail_atr=0.4, atr_floor_pct=0.58,
    ),
    # v40h — Trend filter + adaptive SL (wider in strong momentum)
    'v40h_trend_adapt_sl': make_config(
        strategies=make_strategies(sl_mult=1.4, tp_mult=1.2),
        lock_r=0.5, partial_r=0.7, partial_pct=0.4, trail_atr=0.5, atr_floor_pct=0.58,
        trend_sma_period=100, trend_buffer_pct=0.1,
        adaptive_sl=True,
    ),
}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v38.gen_regime_prices(TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(N_TOKENS)}
    engines = [EngineSimV40(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
    for tick in range(TOTAL_TICKS):
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


SEEDS_ALL = [2024, 7, 42, 1337, 99, 555, 31337, 8]
RESULTS_FILE = '/tmp/v40_seeds.json'

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
        baseline = 'v38g_baseline'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L:\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*120)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        print("WINNER SELECTION (target: ≥75% profitable + WR≥62 + MaxDD<0.3 + AvgR>0)")
        print("=" * 80)
        candidates = [(name, m) for name, m in agg.items()
                      if m['profitable_seeds'] >= 75 and m['wr_above_60_seeds'] >= 50
                      and m['max_dd_mean'] < 0.3 and m['avg_r_mean'] > 0]
        if candidates:
            candidates.sort(key=lambda x: (x[1]['profitable_seeds'], x[1]['wr_mean'], x[1]['pnl_mean']), reverse=True)
            w = candidates[0]
            print(f"\n🏆 WINNER: {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f}  Profit {w[1]['profitable_seeds']:.0f}%  AvgR {w[1]['avg_r_mean']:+.3f}  MaxDD {w[1]['max_dd_mean']:.2f}%  PF {w[1]['pf_mean']:.2f}")
        else:
            print("\n  ⚠️ No config met all criteria. Top 5:")
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
