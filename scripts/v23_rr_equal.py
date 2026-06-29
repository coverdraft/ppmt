#!/usr/bin/env python3
"""
v23 — R:R 1:1 (TP=SL) para reducir fee impact
Objetivo: WR > 61% Y P&L positivo

Math: con TP=SL, breakeven WR = 53% (con fees 0.30%)
Si alcanzamos 60%+ WR, debe ser profitable.

Configs:
- v23a: TP=SL=1.5 (R:R 1:1)
- v23b: TP=SL=2.0 (R:R 1:1, menos fee impact)
- v23c: solo B (mejor performer) con TP=SL=1.5
- v23d: TP=SL=1.5 + partial + breakeven
- v23e: TP=SL=2.0 + partial + breakeven + trend filter
"""
import random
import statistics
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from copy import deepcopy

random.seed(2024)

N_TOKENS = 10
SIM_HOURS = 6
TICK_SECONDS = 1.5
TOTAL_TICKS = int(SIM_HOURS * 3600 / TICK_SECONDS)
FEE_PCT = 0.10
SLIPPAGE_PCT = 0.05
POSITION_SIZE_PCT = 0.05

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
    last = prices[-1] if prices else 0
    slice_ = prices[-period:]
    if len(slice_) < 5: return {'width': 0, 'upper': 0, 'lower': 0}
    mean = sum(slice_) / len(slice_)
    var = sum((p - mean) ** 2 for p in slice_) / len(slice_)
    std = var ** 0.5
    return {'width': (mult * 2 * std) / mean if mean else 0, 'upper': mean + mult * std, 'lower': mean - mult * std}

def gen_regime_prices(n, base=1.0):
    prices = [base]
    regime_ticks_left = 1200
    regime = pick_regime()
    vol = base * regime['vol_pct'] / 100
    drift = base * regime['drift_pct'] / 100 / n * 5
    for i in range(1, n):
        if regime_ticks_left <= 0:
            regime = pick_regime()
            vol = prices[-1] * regime['vol_pct'] / 100
            drift = prices[-1] * regime['drift_pct'] / 100 / n * 5
            regime_ticks_left = 1200
        prices.append(max(0.0001, prices[-1] + random.gauss(0, vol) + drift))
        regime_ticks_left -= 1
    return prices

def pick_regime():
    r = random.random(); cum = 0
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
    initial_atr: float = 0
    breakeven_moved: bool = False
    partial_taken: bool = False
    remaining_qty: float = 0
    initial_qty: float = 0

@dataclass
class Trade:
    symbol: str; direction: str; strategy: str
    entry_price: float; exit_price: float; size_usdt: float
    pnl: float; close_reason: str; hold_ticks: int
    is_partial: bool = False

CONFIGS = {
    'v15_baseline': {
        'A': {'momentum_min': 0.15, 'max_pos': 2, 'sl_mult': 2.0, 'tp_mult': 2.5, 'catsl_mult': 5.0, 'cooldown_min': 45, 'rsi_min': 0, 'rsi_max': 100, 'time_stop': 4800, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False, 'multi_tf': False},
        'B': {'rsi_lo': 40, 'rsi_hi': 60, 'max_pos': 2, 'sl_mult': 2.0, 'tp_mult': 2.5, 'catsl_mult': 4.0, 'cooldown_min': 45, 'enabled': True, 'time_stop': 4800, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False},
        'D': {'bb_width_max': 0.015, 'max_pos': 1, 'sl_mult': 1.5, 'tp_mult': 3.0, 'catsl_mult': 3.5, 'cooldown_min': 45, 'time_stop': 4800, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False},
    },
    'v23a_RR1_TP1.5': {
        'A': {'momentum_min': 0.40, 'max_pos': 1, 'sl_mult': 1.5, 'tp_mult': 1.5, 'catsl_mult': 3.0, 'cooldown_min': 45, 'rsi_min': 25, 'rsi_max': 75, 'time_stop': 2400, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False, 'multi_tf': False},
        'B': {'rsi_lo': 30, 'rsi_hi': 70, 'max_pos': 1, 'sl_mult': 1.5, 'tp_mult': 1.5, 'catsl_mult': 3.0, 'cooldown_min': 45, 'enabled': True, 'time_stop': 2400, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': 1.2, 'tp_mult': 1.2, 'catsl_mult': 2.5, 'cooldown_min': 45, 'time_stop': 2400, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False},
    },
    'v23b_RR1_TP2.0': {
        'A': {'momentum_min': 0.40, 'max_pos': 1, 'sl_mult': 2.0, 'tp_mult': 2.0, 'catsl_mult': 4.0, 'cooldown_min': 45, 'rsi_min': 25, 'rsi_max': 75, 'time_stop': 2400, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False, 'multi_tf': False},
        'B': {'rsi_lo': 30, 'rsi_hi': 70, 'max_pos': 1, 'sl_mult': 2.0, 'tp_mult': 2.0, 'catsl_mult': 4.0, 'cooldown_min': 45, 'enabled': True, 'time_stop': 2400, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': 1.5, 'tp_mult': 1.5, 'catsl_mult': 3.0, 'cooldown_min': 45, 'time_stop': 2400, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False},
    },
    'v23c_onlyB_TP1.5': {  # Solo Strategy B (mejor performer)
        'A': {'momentum_min': 0.40, 'max_pos': 1, 'sl_mult': 1.5, 'tp_mult': 1.5, 'catsl_mult': 3.0, 'cooldown_min': 45, 'rsi_min': 25, 'rsi_max': 75, 'time_stop': 2400, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False, 'multi_tf': False, 'enabled': False},
        'B': {'rsi_lo': 30, 'rsi_hi': 70, 'max_pos': 2, 'sl_mult': 1.5, 'tp_mult': 1.5, 'catsl_mult': 3.0, 'cooldown_min': 45, 'enabled': True, 'time_stop': 2400, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': 1.2, 'tp_mult': 1.2, 'catsl_mult': 2.5, 'cooldown_min': 45, 'time_stop': 2400, 'partial_r': None, 'partial_pct': 0, 'breakeven_r': None, 'trend_filter': False, 'enabled': False},
    },
    'v23d_RR1_partial': {  # TP=SL=1.5 + partial + breakeven
        'A': {'momentum_min': 0.40, 'max_pos': 1, 'sl_mult': 1.5, 'tp_mult': 1.5, 'catsl_mult': 3.0, 'cooldown_min': 45, 'rsi_min': 25, 'rsi_max': 75, 'time_stop': 2400, 'partial_r': 0.7, 'partial_pct': 0.5, 'breakeven_r': 0.7, 'trend_filter': False, 'multi_tf': False},
        'B': {'rsi_lo': 30, 'rsi_hi': 70, 'max_pos': 1, 'sl_mult': 1.5, 'tp_mult': 1.5, 'catsl_mult': 3.0, 'cooldown_min': 45, 'enabled': True, 'time_stop': 2400, 'partial_r': 0.7, 'partial_pct': 0.5, 'breakeven_r': 0.7, 'trend_filter': False},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': 1.2, 'tp_mult': 1.2, 'catsl_mult': 2.5, 'cooldown_min': 45, 'time_stop': 2400, 'partial_r': 0.7, 'partial_pct': 0.5, 'breakeven_r': 0.7, 'trend_filter': False},
    },
    'v23e_RR2_partial_trend': {  # TP=SL=2.0 + partial + breakeven + trend filter
        'A': {'momentum_min': 0.40, 'max_pos': 1, 'sl_mult': 2.0, 'tp_mult': 2.0, 'catsl_mult': 4.0, 'cooldown_min': 60, 'rsi_min': 25, 'rsi_max': 75, 'time_stop': 2400, 'partial_r': 0.7, 'partial_pct': 0.5, 'breakeven_r': 0.7, 'trend_filter': True, 'multi_tf': True},
        'B': {'rsi_lo': 30, 'rsi_hi': 70, 'max_pos': 1, 'sl_mult': 2.0, 'tp_mult': 2.0, 'catsl_mult': 4.0, 'cooldown_min': 60, 'enabled': True, 'time_stop': 2400, 'partial_r': 0.7, 'partial_pct': 0.5, 'breakeven_r': 0.7, 'trend_filter': True},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': 1.5, 'tp_mult': 1.5, 'catsl_mult': 3.0, 'cooldown_min': 60, 'time_stop': 2400, 'partial_r': 0.7, 'partial_pct': 0.5, 'breakeven_r': 0.7, 'trend_filter': True},
    },
}

class EngineSim:
    def __init__(self, config, name, capital=12000):
        self.config = config; self.name = name
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.cooldown_until: Dict[str, int] = {}
        self.last_signal_tick: Dict[str, int] = {'A': 0, 'B': 0, 'D': 0}
        self.cash = capital
        self.strategy_pos_count: Dict[str, int] = {'A': 0, 'B': 0, 'D': 0}
        self.max_equity = capital; self.max_drawdown = 0.0

    def _trend_filter_pass(self, prices, direction):
        if len(prices) < 200: return True
        sma200 = computeSMA(prices, 200)
        if direction == 'LONG' and prices[-1] < sma200: return False
        if direction == 'SHORT' and prices[-1] > sma200: return False
        return True

    def _try_strategy_a(self, sym, prices, tick):
        cfg = self.config['A']
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
        direction = 'LONG' if momentum > 0 else 'SHORT'
        if cfg.get('multi_tf', False) and len(prices) >= 60:
            long_window = prices[-60:]
            momentum_long = ((long_window[-1] - long_window[0]) / long_window[0]) * 100
            if direction == 'LONG' and momentum_long < 0: return
            if direction == 'SHORT' and momentum_long > 0: return
        if cfg.get('trend_filter', False) and not self._trend_filter_pass(prices, direction):
            return
        atr = computeATR(prices, 60)
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
        direction = 'LONG' if rsi < 50 else 'SHORT'
        if cfg.get('trend_filter', False) and len(prices) >= 200:
            sma200 = computeSMA(prices, 200)
            sma50 = computeSMA(prices, 50)
            trend_strength = abs(sma50 - sma200) / sma200 if sma200 > 0 else 0
            if trend_strength > 0.01: return
        atr = computeATR(prices, 60)
        self._open_position(sym, direction, 'B', prices[-1], atr, cfg, tick)
        self.last_signal_tick['B'] = tick

    def _try_strategy_d(self, sym, prices, tick):
        cfg = self.config['D']
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
        direction = 'LONG' if current > bb['upper'] else 'SHORT'
        if cfg.get('trend_filter', False) and not self._trend_filter_pass(prices, direction):
            return
        atr = computeATR(prices, 60)
        self._open_position(sym, direction, 'D', prices[-1], atr, cfg, tick)
        self.last_signal_tick['D'] = tick

    def _open_position(self, sym, direction, strategy, price, atr, cfg, tick):
        size_usdt = min(self.cash * POSITION_SIZE_PCT, self.cash * 0.10)
        if size_usdt < 50: return
        slip = price * (SLIPPAGE_PCT / 100)
        entry_price = price + slip if direction == 'LONG' else price - slip
        fee = size_usdt * (FEE_PCT / 100)
        self.cash -= (size_usdt + fee)
        qty = size_usdt / entry_price
        pos = Position(symbol=sym, direction=direction, strategy=strategy,
                       entry_price=entry_price, qty=qty, size_usdt=size_usdt,
                       initial_atr=atr, entry_tick=tick, initial_qty=qty, remaining_qty=qty)
        if direction == 'LONG':
            pos.current_sl = entry_price - atr * cfg['sl_mult']
            pos.current_tp = entry_price + atr * cfg['tp_mult']
            pos.catastrophic_sl = entry_price - atr * cfg['catsl_mult']
        else:
            pos.current_sl = entry_price + atr * cfg['sl_mult']
            pos.current_tp = entry_price - atr * cfg['tp_mult']
            pos.catastrophic_sl = entry_price + atr * cfg['catsl_mult']
        self.positions[sym] = pos
        self.strategy_pos_count[strategy] += 1

    def _compute_r(self, pos, price):
        if pos.initial_atr == 0: return 0
        sl_distance = pos.initial_atr * self.config[pos.strategy]['sl_mult']
        if sl_distance == 0: return 0
        if pos.direction == 'LONG':
            favorable = price - pos.entry_price
        else:
            favorable = pos.entry_price - price
        return favorable / sl_distance

    def _update_position(self, pos, price):
        cfg = self.config[pos.strategy]
        r = self._compute_r(pos, price)
        be_r = cfg.get('breakeven_r')
        if be_r is not None and not pos.breakeven_moved and r >= be_r:
            pos.current_sl = pos.entry_price
            pos.breakeven_moved = True
        partial_r = cfg.get('partial_r')
        if partial_r is not None and not pos.partial_taken and r >= partial_r and pos.remaining_qty > 0:
            partial_pct = cfg.get('partial_pct', 0.5)
            partial_qty = pos.remaining_qty * partial_pct
            if partial_qty > 0:
                self._close_partial(pos, price, partial_qty, 'PARTIAL_TP')
            pos.partial_taken = True

    def _close_partial(self, pos, exit_price_raw, qty, reason):
        slip = exit_price_raw * (SLIPPAGE_PCT / 100)
        exit_price = exit_price_raw - slip if pos.direction == 'LONG' else exit_price_raw + slip
        gross = (exit_price - pos.entry_price) * qty if pos.direction == 'LONG' else (pos.entry_price - exit_price) * qty
        exit_fee = exit_price * qty * (FEE_PCT / 100)
        net = gross - exit_fee
        partial_size_usdt = qty * pos.entry_price
        self.cash += partial_size_usdt + net
        self.trades.append(Trade(symbol=pos.symbol, direction=pos.direction, strategy=pos.strategy,
                                  entry_price=pos.entry_price, exit_price=exit_price,
                                  size_usdt=partial_size_usdt, pnl=net,
                                  close_reason=reason, hold_ticks=0, is_partial=True))
        pos.remaining_qty -= qty
        pos.qty = pos.remaining_qty

    def _check_stops(self, sym, prices, tick):
        if sym not in self.positions: return
        pos = self.positions[sym]
        price = prices[-1]
        cfg = self.config[pos.strategy]
        self._update_position(pos, price)
        if tick - pos.entry_tick > cfg['time_stop']:
            self._close_position(sym, price, 'TIME', tick)
            self.cooldown_until[sym] = tick + int(cfg['cooldown_min'] * 60 / TICK_SECONDS)
            return
        if pos.remaining_qty <= 0:
            del self.positions[sym]
            self.strategy_pos_count[pos.strategy] -= 1
            return
        is_long = pos.direction == 'LONG'
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

    def _close_position(self, sym, exit_price_raw, reason, tick):
        pos = self.positions[sym]
        slip = exit_price_raw * (SLIPPAGE_PCT / 100)
        exit_price = exit_price_raw - slip if pos.direction == 'LONG' else exit_price_raw + slip
        qty = pos.remaining_qty if pos.remaining_qty > 0 else pos.qty
        gross = (exit_price - pos.entry_price) * qty if pos.direction == 'LONG' else (pos.entry_price - exit_price) * qty
        exit_fee = exit_price * qty * (FEE_PCT / 100)
        net = gross - exit_fee
        remaining_size = qty * pos.entry_price
        self.cash += remaining_size + net
        self.trades.append(Trade(symbol=sym, direction=pos.direction, strategy=pos.strategy,
                                  entry_price=pos.entry_price, exit_price=exit_price,
                                  size_usdt=remaining_size, pnl=net,
                                  close_reason=reason, hold_ticks=tick - pos.entry_tick, is_partial=False))
        self.strategy_pos_count[pos.strategy] -= 1
        del self.positions[sym]

    def update_equity(self, all_prices, tick):
        equity = self.cash
        for sym, pos in self.positions.items():
            if sym in all_prices and tick < len(all_prices[sym]):
                price = all_prices[sym][tick]
                qty = pos.remaining_qty if pos.remaining_qty > 0 else pos.qty
                unreal = (price - pos.entry_price) * qty if pos.direction == 'LONG' else (pos.entry_price - price) * qty
                equity += qty * pos.entry_price + unreal
        if equity > self.max_equity: self.max_equity = equity
        dd = (self.max_equity - equity) / self.max_equity * 100
        if dd > self.max_drawdown: self.max_drawdown = dd

    def get_metrics(self):
        closed = [t for t in self.trades if not t.is_partial]
        partials = [t for t in self.trades if t.is_partial]
        if not closed:
            return {'trades': 0, 'wr': 0, 'pnl': 0, 'pf': 0, 'max_dd': 0, 'per_strat': {}, 'avg_hold_min': 0, 'tp_pct': 0, 'sl_pct': 0, 'time_pct': 0, 'partials': 0}
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        gross_win = sum(t.pnl for t in wins) + sum(t.pnl for t in partials)
        gross_loss = abs(sum(t.pnl for t in losses))
        per_strat = {}
        for s in ['A', 'B', 'D']:
            s_trades = [t for t in closed if t.strategy == s]
            s_wins = [t for t in s_trades if t.pnl > 0]
            s_partials = [t for t in partials if t.strategy == s]
            s_partial_pnl = sum(t.pnl for t in s_partials)
            per_strat[s] = {'trades': len(s_trades), 'wr': len(s_wins) / len(s_trades) * 100 if s_trades else 0,
                            'pnl': sum(t.pnl for t in s_trades) + s_partial_pnl}
        tp_count = sum(1 for t in closed if t.close_reason == 'TP')
        sl_count = sum(1 for t in closed if t.close_reason in ('SL', 'CAT_SL'))
        time_count = sum(1 for t in closed if t.close_reason == 'TIME')
        avg_hold = sum(t.hold_ticks for t in closed) / len(closed) * TICK_SECONDS / 60
        partial_pnl_total = sum(t.pnl for t in partials)
        total_pnl = sum(t.pnl for t in closed) + partial_pnl_total
        return {'trades': len(closed),
                'wr': len(wins) / len(closed) * 100, 'pnl': total_pnl,
                'pf': gross_win / gross_loss if gross_loss > 0 else float('inf'),
                'max_dd': self.max_drawdown, 'per_strat': per_strat,
                'avg_hold_min': avg_hold,
                'tp_pct': tp_count / len(closed) * 100,
                'sl_pct': sl_count / len(closed) * 100,
                'time_pct': time_count / len(closed) * 100,
                'partials': len(partials), 'partial_pnl': partial_pnl_total}


def run():
    print(f"Generando {N_TOKENS} tokens × {TOTAL_TICKS} ticks ({SIM_HOURS}h)...")
    all_prices = {f"TOK{i:02d}": gen_regime_prices(TOTAL_TICKS, 1.0 * (1 + random.uniform(-0.3, 0.3)))
                  for i in range(N_TOKENS)}

    engines = [EngineSim(cfg, name) for name, cfg in CONFIGS.items()]
    print(f"Simulando {SIM_HOURS}h con {len(engines)} configs...\n")

    for tick in range(TOTAL_TICKS):
        for sym in all_prices:
            if tick + 1 < 60: continue
            prices_slice = all_prices[sym][max(0, tick-220):tick+1]
            for engine in engines:
                engine._try_strategy_a(sym, prices_slice, tick)
                engine._try_strategy_b(sym, prices_slice, tick)
                engine._try_strategy_d(sym, prices_slice, tick)
                engine._check_stops(sym, prices_slice, tick)
        for engine in engines:
            engine.update_equity(all_prices, tick)

    print("="*160)
    print(f"{'Config':<28} {'Trades':<8} {'WR':<8} {'P&L':<11} {'PF':<7} {'MaxDD':<7} {'AvgHold':<9} {'TP%':<6} {'SL%':<6} {'TIME%':<6} {'Part':<6} {'Strat A':<20} {'Strat B':<20} {'Strat D':<20}")
    print("="*160)
    best = None; best_score = -999
    for engine in engines:
        m = engine.get_metrics()
        ps = m['per_strat']
        def strat_str(s):
            x = ps[s]
            return f"{x['trades']}t/{x['wr']:.0f}%/{x['pnl']:+.1f}"
        part_str = f"{m['partials']}"
        print(f"{engine.name:<28} {m['trades']:<8} {m['wr']:.1f}%{'':>2} {m['pnl']:+.2f}{'':>3} {m['pf']:.2f}{'':>3} {m['max_dd']:.2f}%{'':>2} {m['avg_hold_min']:.1f}m{'':>3} {m['tp_pct']:.0f}%{'':>3} {m['sl_pct']:.0f}%{'':>3} {m['time_pct']:.0f}%{'':>3} {part_str:<6} {strat_str('A'):<20} {strat_str('B'):<20} {strat_str('D'):<20}")
        score = m['wr'] if m['pnl'] > 0 else -100 + m['wr']
        if score > best_score and m['trades'] >= 30:
            best_score = score; best = engine.name

    print("\n" + "="*160)
    print("ANÁLISIS: WR > 61% Y P&L > 0")
    print("="*160)
    base = engines[0].get_metrics()
    for engine in engines[1:]:
        m = engine.get_metrics()
        wr_pass = "✅WR>61" if m['wr'] > 61 else "❌WR<61"
        pnl_pass = "✅P&L>0" if m['pnl'] > 0 else "❌P&L<0"
        target_pass = "🎯TARGET" if (m['wr'] > 61 and m['pnl'] > 0) else "       "
        print(f"  {target_pass} {wr_pass} {pnl_pass}  {engine.name}: WR {m['wr']:.1f}%, P&L {m['pnl']:+.2f}, PF {m['pf']:.2f}")
    if best:
        print(f"\n🏆 Mejor (WR + P&L positivo): {best}")


if __name__ == "__main__":
    run()
