#!/usr/bin/env python3
"""
v35 — Push v34b (ATR floor 0.5%) further
v34b: P&L -8.56, WR 59.4%, MaxDD 0.56%, Consistency 50%, Profitable 33% of seeds

GOAL: Cross 50% profitable seeds while keeping WR > 60% and MaxDD < 0.8%

Configs (all based on v34b = lock 0.4R + partial 30%@0.8R + trail 0.6 + ATR floor 0.5%):
  v35a: tighter trail 0.4 ATR (capture more R from winners)
  v35b: wider trail 0.8 ATR (give winners more room)
  v35c: larger partial 50%@0.8R (lock more profit earlier)
  v35d: lock at 0.6R instead of 0.4R (later lock, more room before SL tightens)
  v35e: wider TP 1.5 ATR (capture bigger moves)
  v35f: tighter SL 1.5 (less risk per trade)
  v35g: ATR floor 0.45 (slight relax — more trades)
  v35h: best combo guess (lock 0.5, partial 40%@0.7R, trail 0.5, TP 1.4)
"""
import random, statistics, math
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from copy import deepcopy

SEEDS = [2024, 7, 42, 1337, 99]  # 5 seeds

N_TOKENS = 10
SIM_HOURS = 4  # 4h for speed
TICK_SECONDS = 1.5
TOTAL_TICKS = int(SIM_HOURS * 3600 / TICK_SECONDS)
FEE_PCT = 0.10
SLIPPAGE_PCT = 0.05
TICKS_PER_HOUR = int(3600 / TICK_SECONDS)

REGIMES = [
    {'vol_pct': 0.30, 'drift_pct': 0.0, 'weight': 0.60},
    {'vol_pct': 0.60, 'drift_pct': 0.0, 'weight': 0.25},
    {'vol_pct': 1.20, 'drift_pct': 0.0, 'weight': 0.10},
    {'vol_pct': 0.50, 'drift_pct': 3.0, 'weight': 0.05},
]

def computeRSI(prices, period=14):
    if len(prices) < period + 1: return 50
    gains = losses = 0
    for i in range(1, period + 1):
        ch = prices[i] - prices[i-1]
        if ch >= 0: gains += ch
        else: losses -= ch
    avgGain = gains / period; avgLoss = losses / period
    for i in range(period + 1, len(prices)):
        ch = prices[i] - prices[i-1]
        g = ch if ch > 0 else 0; l = -ch if ch < 0 else 0
        avgGain = (avgGain * (period - 1) + g) / period
        avgLoss = (avgLoss * (period - 1) + l) / period
    if avgLoss == 0: return 100
    return 100 - (100 / (1 + avgGain / avgLoss))

def computeATR(prices, period=60):
    if len(prices) < 2: return 0
    start = max(1, len(prices) - period)
    diffs = [abs(prices[i] - prices[i-1]) for i in range(start, len(prices))]
    if not diffs: return 0
    return max(sum(diffs) / len(diffs), prices[-1] * 0.003)

def computeSMA(prices, period):
    if len(prices) < period: return prices[-1] if prices else 0
    return sum(prices[-period:]) / period

def computeBollinger(prices, period=50, mult=2):
    slice_ = prices[-period:]
    if len(slice_) < 5: return {'width': 0, 'upper': 0, 'lower': 0}
    mean = sum(slice_) / len(slice_)
    var = sum((p - mean) ** 2 for p in slice_) / len(slice_)
    std = var ** 0.5
    return {'width': (mult * 2 * std) / mean if mean else 0, 'upper': mean + mult * std, 'lower': mean - mult * std}

def gen_regime_prices(n, base=1.0, rng=None):
    if rng is None: rng = random
    prices = [base]
    regime_ticks_left = 1200
    regime = pick_regime(rng)
    vol = base * regime['vol_pct'] / 100
    drift = base * regime['drift_pct'] / 100 / n * 5
    for i in range(1, n):
        if regime_ticks_left <= 0:
            regime = pick_regime(rng)
            vol = prices[-1] * regime['vol_pct'] / 100
            drift = prices[-1] * regime['drift_pct'] / 100 / n * 5
            regime_ticks_left = 1200
        prices.append(max(0.0001, prices[-1] + rng.gauss(0, vol) + drift))
        regime_ticks_left -= 1
    return prices

def pick_regime(rng):
    r = rng.random(); cum = 0
    for regime in REGIMES:
        cum += regime['weight']
        if r <= cum: return regime
    return REGIMES[0]


@dataclass
class Position:
    symbol: str; direction: str; strategy: str
    entry_price: float; qty: float; size_usdt: float
    current_sl: Optional[float] = None
    current_tp: Optional[float] = None
    catastrophic_sl: Optional[float] = None
    entry_tick: int = 0
    initial_qty: float = 0.0
    partial_done: bool = False
    be_done: bool = False
    lock_done: bool = False
    trail_active: bool = False
    trail_atr: float = 0.0
    max_favorable_price: float = 0.0


@dataclass
class Trade:
    symbol: str; direction: str; strategy: str
    entry_price: float; exit_price: float; size_usdt: float
    pnl: float; close_reason: str; hold_ticks: int
    r_multiple: float = 0.0


def make_strategies(sl_mult=2.0, tp_mult=1.2, pos_size_A=0.025, pos_size_B=0.10, pos_size_D=0.05):
    return {
        'A': {'momentum_min': 0.40, 'max_pos': 1, 'sl_mult': sl_mult, 'tp_mult': tp_mult, 'catsl_mult': 4.0, 'cooldown_min': 45, 'rsi_min': 25, 'rsi_max': 75, 'time_stop': 2400, 'pos_size_pct': pos_size_A},
        'B': {'rsi_lo': 30, 'rsi_hi': 70, 'max_pos': 1, 'sl_mult': sl_mult, 'tp_mult': tp_mult, 'catsl_mult': 4.0, 'cooldown_min': 45, 'enabled': True, 'time_stop': 2400, 'pos_size_pct': pos_size_B},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': sl_mult * 0.75, 'tp_mult': tp_mult * 0.83, 'catsl_mult': 3.0, 'cooldown_min': 45, 'time_stop': 2400, 'pos_size_pct': pos_size_D},
        'E': {'enabled': False},
    }


def make_config(strategies=None, lock_r=0.4, partial_r=0.8, partial_pct=0.3,
                trail_after_partial=True, trail_atr=0.6, atr_floor_pct=0.5):
    if strategies is None: strategies = make_strategies()
    return {
        **strategies,
        'partial_trigger_r': partial_r, 'partial_close_pct': partial_pct,
        'be_trigger_r': None, 'lock_trigger_r': lock_r,
        'trail_after_partial': trail_after_partial, 'trail_atr_mult': trail_atr,
        'adaptive_size': False,
        'atr_floor_pct': atr_floor_pct,
        'dynamic_tp': False, 'dynamic_tp_min_pct': 0.5,
        'regime_size_scale': {},
    }


# v34b baseline
V34B = dict(strategies=make_strategies(), lock_r=0.4, partial_r=0.8, partial_pct=0.3,
            trail_after_partial=True, trail_atr=0.6, atr_floor_pct=0.5)

CONFIGS = {
    'v34b_baseline': make_config(**V34B),
    'v35a_tighter_trail_0.4': make_config(strategies=make_strategies(), lock_r=0.4, partial_r=0.8, partial_pct=0.3, trail_atr=0.4, atr_floor_pct=0.5),
    'v35b_wider_trail_0.8': make_config(strategies=make_strategies(), lock_r=0.4, partial_r=0.8, partial_pct=0.3, trail_atr=0.8, atr_floor_pct=0.5),
    'v35c_larger_partial_50': make_config(strategies=make_strategies(), lock_r=0.4, partial_r=0.8, partial_pct=0.5, trail_atr=0.6, atr_floor_pct=0.5),
    'v35d_lock_0.6': make_config(strategies=make_strategies(), lock_r=0.6, partial_r=0.8, partial_pct=0.3, trail_atr=0.6, atr_floor_pct=0.5),
    'v35e_wider_TP_1.5': make_config(strategies=make_strategies(tp_mult=1.5), lock_r=0.4, partial_r=0.8, partial_pct=0.3, trail_atr=0.6, atr_floor_pct=0.5),
    'v35f_tighter_SL_1.5': make_config(strategies=make_strategies(sl_mult=1.5), lock_r=0.4, partial_r=0.8, partial_pct=0.3, trail_atr=0.6, atr_floor_pct=0.5),
    'v35g_atr_floor_0.45': make_config(strategies=make_strategies(), lock_r=0.4, partial_r=0.8, partial_pct=0.3, trail_atr=0.6, atr_floor_pct=0.45),
    'v35h_combo': make_config(strategies=make_strategies(tp_mult=1.4), lock_r=0.5, partial_r=0.7, partial_pct=0.4, trail_atr=0.5, atr_floor_pct=0.45),
}


class EngineSim:
    def __init__(self, config, name, capital=12000):
        self.config = config; self.name = name
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.cooldown_until: Dict[str, int] = {}
        self.last_signal_tick: Dict[str, int] = {'A': 0, 'B': 0, 'D': 0, 'E': 0}
        self.cash = capital
        self.initial_capital = capital
        self.strategy_pos_count: Dict[str, int] = {'A': 0, 'B': 0, 'D': 0, 'E': 0}
        self.max_equity = capital; self.max_drawdown = 0.0
        self.hourly_realized_pnl: List[float] = [0.0] * SIM_HOURS
        self.equity_series: List[float] = []
        self.consec_losses: int = 0
        self.max_consec_losses: int = 0
        self.atr_filter_skips: int = 0

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
        rsi = computeRSI(prices, 14)
        if rsi < cfg['rsi_min'] or rsi > cfg['rsi_max']: return
        atr = computeATR(prices, 60)
        atr_pct = atr / prices[-1] * 100
        if self.config.get('atr_floor_pct') is not None and atr_pct < self.config['atr_floor_pct']:
            self.atr_filter_skips += 1
            return
        direction = 'LONG' if momentum > 0 else 'SHORT'
        self._open_position(sym, direction, 'A', prices[-1], atr, cfg, tick)
        self.last_signal_tick['A'] = tick

    def _try_strategy_b(self, sym, prices, tick):
        cfg = self.config.get('B', {})
        if not cfg.get('enabled', True): return
        if tick - self.last_signal_tick['B'] < 20: return
        if self.strategy_pos_count['B'] >= cfg['max_pos']: return
        if len(prices) < 60: return
        if sym in self.positions: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return
        rsi = computeRSI(prices, 14)
        if cfg['rsi_lo'] <= rsi <= cfg['rsi_hi']: return
        atr = computeATR(prices, 60)
        atr_pct = atr / prices[-1] * 100
        if self.config.get('atr_floor_pct') is not None and atr_pct < self.config['atr_floor_pct']:
            self.atr_filter_skips += 1
            return
        direction = 'LONG' if rsi < 50 else 'SHORT'
        self._open_position(sym, direction, 'B', prices[-1], atr, cfg, tick)
        self.last_signal_tick['B'] = tick

    def _try_strategy_d(self, sym, prices, tick):
        cfg = self.config.get('D', {})
        if not cfg.get('enabled', True): return
        if tick - self.last_signal_tick['D'] < 40: return
        if self.strategy_pos_count['D'] >= cfg['max_pos']: return
        if len(prices) < 55: return
        if sym in self.positions: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return
        bb = computeBollinger(prices, 50, 2)
        if bb['width'] <= 0 or bb['width'] > cfg['bb_width_max']: return
        current = prices[-1]
        if not (current > bb['upper'] or current < bb['lower']): return
        atr = computeATR(prices, 60)
        atr_pct = atr / prices[-1] * 100
        if self.config.get('atr_floor_pct') is not None and atr_pct < self.config['atr_floor_pct']:
            self.atr_filter_skips += 1
            return
        direction = 'LONG' if current > bb['upper'] else 'SHORT'
        self._open_position(sym, direction, 'D', prices[-1], atr, cfg, tick)
        self.last_signal_tick['D'] = tick

    def _try_strategy_e(self, sym, prices, tick): pass

    def _open_position(self, sym, direction, strategy, price, atr, cfg, tick):
        pos_size_pct = cfg.get('pos_size_pct', 0.05)
        regime_scale = self.config.get('regime_size_scale', {})
        if regime_scale:
            atr_pct = atr / price * 100
            closest_vol = min(regime_scale.keys(), key=lambda v: abs(v - atr_pct))
            pos_size_pct = pos_size_pct * regime_scale[closest_vol]
        size_usdt = min(self.cash * pos_size_pct, self.cash * 0.15)
        if size_usdt < 50: return
        slip = price * (SLIPPAGE_PCT / 100)
        entry_price = price + slip if direction == 'LONG' else price - slip
        fee = size_usdt * (FEE_PCT / 100)
        self.cash -= (size_usdt + fee)
        qty = size_usdt / entry_price
        pos = Position(symbol=sym, direction=direction, strategy=strategy,
                       entry_price=entry_price, qty=qty, size_usdt=size_usdt, entry_tick=tick,
                       initial_qty=qty)
        if direction == 'LONG':
            pos.current_sl = entry_price - atr * cfg['sl_mult']
            pos.current_tp = entry_price + atr * cfg['tp_mult']
            pos.catastrophic_sl = entry_price - atr * cfg['catsl_mult']
            pos.max_favorable_price = entry_price
        else:
            pos.current_sl = entry_price + atr * cfg['sl_mult']
            pos.current_tp = entry_price - atr * cfg['tp_mult']
            pos.catastrophic_sl = entry_price + atr * cfg['catsl_mult']
            pos.max_favorable_price = entry_price
        pos.trail_atr = atr
        self.positions[sym] = pos
        self.strategy_pos_count[strategy] += 1

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
        # Lock profit
        if not pos.lock_done and self.config.get('lock_trigger_r') is not None:
            if r_multiple >= self.config['lock_trigger_r']:
                lock_r = 0.2
                if is_long:
                    new_sl = pos.entry_price + lock_r * initial_sl_distance
                    if new_sl > pos.current_sl: pos.current_sl = new_sl
                else:
                    new_sl = pos.entry_price - lock_r * initial_sl_distance
                    if new_sl < pos.current_sl or pos.current_sl is None: pos.current_sl = new_sl
                pos.lock_done = True
        # Partial TP
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
        # Update trailing
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
            self.cooldown_until[sym] = tick + int(cfg['cooldown_min'] * 60 / TICK_SECONDS)
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
            if reason in ('SL', 'CAT_SL'):
                self.cooldown_until[sym] = tick + int(cfg['cooldown_min'] * 60 / TICK_SECONDS)

    def _partial_close(self, sym, exit_price_raw, reason, tick, close_qty):
        pos = self.positions[sym]
        slip = exit_price_raw * (SLIPPAGE_PCT / 100)
        exit_price = exit_price_raw - slip if pos.direction == 'LONG' else exit_price_raw + slip
        gross = (exit_price - pos.entry_price) * close_qty if pos.direction == 'LONG' else (pos.entry_price - exit_price) * close_qty
        exit_fee = exit_price * close_qty * (FEE_PCT / 100)
        net = gross - exit_fee
        partial_size = close_qty * pos.entry_price
        self.cash += partial_size + net
        pos.qty -= close_qty
        pos.size_usdt -= partial_size
        initial_sl_distance = pos.trail_atr * self.config[pos.strategy]['sl_mult']
        r_mult = ((exit_price - pos.entry_price) / initial_sl_distance) if pos.direction == 'LONG' else ((pos.entry_price - exit_price) / initial_sl_distance)
        self.trades.append(Trade(symbol=sym, direction=pos.direction, strategy=pos.strategy,
                                  entry_price=pos.entry_price, exit_price=exit_price,
                                  size_usdt=partial_size, pnl=net,
                                  close_reason=reason, hold_ticks=tick - pos.entry_tick,
                                  r_multiple=r_mult))
        hour_idx = tick // TICKS_PER_HOUR
        if 0 <= hour_idx < SIM_HOURS: self.hourly_realized_pnl[hour_idx] += net
        if net > 0: self.consec_losses = 0
        else:
            self.consec_losses += 1
            if self.consec_losses > self.max_consec_losses: self.max_consec_losses = self.consec_losses

    def _close_position(self, sym, exit_price_raw, reason, tick):
        pos = self.positions[sym]
        slip = exit_price_raw * (SLIPPAGE_PCT / 100)
        exit_price = exit_price_raw - slip if pos.direction == 'LONG' else exit_price_raw + slip
        gross = (exit_price - pos.entry_price) * pos.qty if pos.direction == 'LONG' else (pos.entry_price - exit_price) * pos.qty
        exit_fee = exit_price * pos.qty * (FEE_PCT / 100)
        net = gross - exit_fee
        self.cash += pos.size_usdt + net
        initial_sl_distance = pos.trail_atr * self.config[pos.strategy]['sl_mult']
        r_mult = ((exit_price - pos.entry_price) / initial_sl_distance) if pos.direction == 'LONG' else ((pos.entry_price - exit_price) / initial_sl_distance)
        self.trades.append(Trade(symbol=sym, direction=pos.direction, strategy=pos.strategy,
                                  entry_price=pos.entry_price, exit_price=exit_price,
                                  size_usdt=pos.size_usdt, pnl=net,
                                  close_reason=reason, hold_ticks=tick - pos.entry_tick,
                                  r_multiple=r_mult))
        self.strategy_pos_count[pos.strategy] -= 1
        del self.positions[sym]
        hour_idx = tick // TICKS_PER_HOUR
        if 0 <= hour_idx < SIM_HOURS: self.hourly_realized_pnl[hour_idx] += net
        if net > 0: self.consec_losses = 0
        else:
            self.consec_losses += 1
            if self.consec_losses > self.max_consec_losses: self.max_consec_losses = self.consec_losses

    def update_equity(self, all_prices, tick):
        equity = self.cash
        for sym, pos in self.positions.items():
            if sym in all_prices and tick < len(all_prices[sym]):
                price = all_prices[sym][tick]
                unreal = (price - pos.entry_price) * pos.qty if pos.direction == 'LONG' else (pos.entry_price - price) * pos.qty
                equity += pos.size_usdt + unreal
        if equity > self.max_equity: self.max_equity = equity
        dd = (self.max_equity - equity) / self.max_equity * 100
        if dd > self.max_drawdown: self.max_drawdown = dd
        if (tick + 1) % TICKS_PER_HOUR == 0:
            self.equity_series.append(equity)

    def get_metrics(self):
        closed = self.trades
        if not closed:
            return self._empty_metrics()
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        per_strat = {}
        for s in ['A', 'B', 'D', 'E']:
            s_trades = [t for t in closed if t.strategy == s]
            s_wins = [t for t in s_trades if t.pnl > 0]
            per_strat[s] = {'trades': len(s_trades), 'wr': len(s_wins) / len(s_trades) * 100 if s_trades else 0, 'pnl': sum(t.pnl for t in s_trades)}
        tp_count = sum(1 for t in closed if t.close_reason == 'TP')
        sl_count = sum(1 for t in closed if t.close_reason in ('SL', 'CAT_SL'))
        time_count = sum(1 for t in closed if t.close_reason == 'TIME')
        partial_count = sum(1 for t in closed if t.close_reason == 'PARTIAL_TP')
        avg_hold = sum(t.hold_ticks for t in closed) / len(closed) * TICK_SECONDS / 60
        if len(self.equity_series) >= 2:
            returns = []
            for i in range(1, len(self.equity_series)):
                if self.equity_series[i-1] > 0:
                    returns.append((self.equity_series[i] - self.equity_series[i-1]) / self.equity_series[i-1])
            if returns:
                mean_r = statistics.mean(returns)
                std_r = statistics.stdev(returns) if len(returns) > 1 else 0
                sharpe = (mean_r / std_r * math.sqrt(24 * 365)) if std_r > 0 else 0
                downside = [r for r in returns if r < 0]
                ds_std = (statistics.mean([r**2 for r in downside]) ** 0.5) if downside else 0
                sortino = (mean_r / ds_std * math.sqrt(24 * 365)) if ds_std > 0 else 0
            else: sharpe = 0; sortino = 0
        else: sharpe = 0; sortino = 0
        profitable_hours = sum(1 for p in self.hourly_realized_pnl if p > 0)
        consistency = profitable_hours / SIM_HOURS * 100
        hourly_std = statistics.stdev(self.hourly_realized_pnl) if len(self.hourly_realized_pnl) > 1 else 0
        recovery = abs(self.max_drawdown) > 0 and sum(t.pnl for t in closed) / max(self.max_drawdown, 0.01)
        avg_r = statistics.mean(t.r_multiple for t in closed) if closed else 0
        return {'trades': len(closed),
                'wr': len(wins) / len(closed) * 100, 'pnl': sum(t.pnl for t in closed),
                'pf': gross_win / gross_loss if gross_loss > 0 else float('inf'),
                'max_dd': self.max_drawdown, 'per_strat': per_strat,
                'avg_hold_min': avg_hold,
                'tp_pct': tp_count / len(closed) * 100,
                'sl_pct': sl_count / len(closed) * 100,
                'time_pct': time_count / len(closed) * 100,
                'partial_pct': partial_count / len(closed) * 100,
                'sharpe': sharpe, 'sortino': sortino,
                'max_consec_loss': self.max_consec_losses,
                'consistency': consistency,
                'hourly_std': hourly_std,
                'recovery': recovery,
                'avg_r': avg_r,
                'atr_skips': self.atr_filter_skips}

    def _empty_metrics(self):
        return {'trades': 0, 'wr': 0, 'pnl': 0, 'pf': 0, 'max_dd': 0, 'per_strat': {},
                'avg_hold_min': 0, 'tp_pct': 0, 'sl_pct': 0, 'time_pct': 0,
                'sharpe': 0, 'sortino': 0, 'max_consec_loss': 0, 'consistency': 0,
                'hourly_std': 0, 'recovery': 0, 'avg_r': 0, 'atr_skips': self.atr_filter_skips}


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": gen_regime_prices(TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(N_TOKENS)}
    engines = [EngineSim(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
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


def aggregate_results(all_seed_results):
    agg = {}
    for name in CONFIGS.keys():
        seed_metrics = [r[name] for r in all_seed_results if name in r]
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
    return agg


def print_results(agg, baseline_name='v34b_baseline'):
    print("\n" + "="*200)
    print(f"{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}")
    print("="*200)
    for name, m in agg.items():
        is_baseline = name == baseline_name
        marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
        print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
    print("="*200)

    print(f"\nvs BASELINE ({baseline_name}):")
    base = agg[baseline_name]
    for name, m in agg.items():
        if name == baseline_name: continue
        d_wr = m['wr_mean'] - base['wr_mean']
        d_pnl = m['pnl_mean'] - base['pnl_mean']
        d_sharpe = m['sharpe_mean'] - base['sharpe_mean']
        d_r = m['avg_r_mean'] - base['avg_r_mean']
        d_cons = m['consistency_mean'] - base['consistency_mean']
        d_cl = m['max_consec_loss_mean'] - base['max_consec_loss_mean']
        d_dd = m['max_dd_mean'] - base['max_dd_mean']
        d_profit = m['profitable_seeds'] - base['profitable_seeds']
        print(f"  {name:<32} ΔWR {d_wr:+.1f}  ΔP&L {d_pnl:+.1f}  ΔSharpe {d_sharpe:+.2f}  ΔRR {d_r:+.3f}  ΔConsist {d_cons:+.1f}  ΔMaxCL {d_cl:+.1f}  ΔMaxDD {d_dd:+.2f}  ΔProfit {d_profit:+.0f}%")

    print("\nPer-seed P&L:")
    print(f"  {'Config':<32} | " + " | ".join(f"S{s}" for s in SEEDS))
    print("  " + "-" * 100)
    for name, m in agg.items():
        pnl_str = " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed'])
        print(f"  {name:<32} | {pnl_str}")


def run():
    print(f"v35 PUSH v34b FURTHER")
    print(f"Configs: {len(CONFIGS)} | Seeds: {SEEDS} | Hours: {SIM_HOURS}h")
    all_seed_results = []
    for seed in SEEDS:
        print(f"\n  Running seed {seed}...")
        result = run_single_seed(seed)
        all_seed_results.append(result)
        for name, m in result.items():
            print(f"    {name:<32} WR {m['wr']:.1f}%  P&L {m['pnl']:+.1f}  Trades {m['trades']}")

    print("\n" + "#" * 200)
    print("# AGGREGATED RESULTS")
    print("#" * 200)
    agg = aggregate_results(all_seed_results)
    print_results(agg)

    print("\n" + "=" * 80)
    print("WINNER SELECTION (target: ≥50% profitable seeds + WR≥60 + MaxDD<0.8)")
    print("=" * 80)
    candidates = [(name, m) for name, m in agg.items()
                  if m['profitable_seeds'] >= 50 and m['wr_above_60_seeds'] >= 50 and m['max_dd_mean'] < 0.8]
    if candidates:
        candidates.sort(key=lambda x: (x[1]['pnl_mean'], x[1]['avg_r_mean']), reverse=True)
        winner = candidates[0]
        print(f"\n🏆 WINNER: {winner[0]}")
        print(f"   WR {winner[1]['wr_mean']:.1f}% (±{winner[1]['wr_std']:.1f})")
        print(f"   P&L {winner[1]['pnl_mean']:+.2f} (±{winner[1]['pnl_std']:.0f})")
        print(f"   Profitable in {winner[1]['profitable_seeds']:.0f}% of seeds")
        print(f"   Sharpe {winner[1]['sharpe_mean']:+.2f}")
        print(f"   Avg R {winner[1]['avg_r_mean']:+.3f}")
        print(f"   Max DD {winner[1]['max_dd_mean']:.2f}%")
        print(f"   Max consec losses {winner[1]['max_consec_loss_mean']:.1f}")
        print(f"   Profit consistency {winner[1]['consistency_mean']:.1f}%")
    else:
        print("\n  ⚠️ No config met all criteria. Listing best P&L:")
        ranked = sorted(agg.items(), key=lambda x: x[1]['pnl_mean'], reverse=True)
        for i, (name, m) in enumerate(ranked[:3]):
            print(f"  #{i+1} {name:<32} P&L {m['pnl_mean']:+.2f}  WR {m['wr_mean']:.1f}%  Profit {m['profitable_seeds']:.0f}%")


if __name__ == "__main__":
    run()
