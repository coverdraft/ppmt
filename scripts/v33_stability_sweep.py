#!/usr/bin/env python3
"""
v33 — Push v32g further (multi-seed validated winner)
Base: v32g (lock@0.6R + partial 30%@0.8R + trail 0.6 ATR)
  P&L -42.65 (vs v31b -121.42), MaxDD 1.06% (vs 2.00%), Profitable seeds 40% (vs 20%)

GOAL: Push profitability above 60% of seeds while keeping MaxDD < 1.5% and WR > 60%

APPROACH:
  - More aggressive partial (capture more R early)
  - Earlier lock (smaller initial risk)
  - Tighter trailing (capture more R when winners extend)
  - Test 8 variants across 8 seeds for robustness
"""
import random, statistics, math
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from copy import deepcopy

SEEDS = [2024, 7, 42, 1337, 99, 555, 31337, 8]

N_TOKENS = 10
SIM_HOURS = 6
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


def base_strategies():
    return {
        'A': {'momentum_min': 0.40, 'max_pos': 1, 'sl_mult': 2.0, 'tp_mult': 1.2, 'catsl_mult': 4.0, 'cooldown_min': 45, 'rsi_min': 25, 'rsi_max': 75, 'time_stop': 2400, 'pos_size_pct': 0.025},
        'B': {'rsi_lo': 30, 'rsi_hi': 70, 'max_pos': 1, 'sl_mult': 2.0, 'tp_mult': 1.2, 'catsl_mult': 4.0, 'cooldown_min': 45, 'enabled': True, 'time_stop': 2400, 'pos_size_pct': 0.10},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': 1.5, 'tp_mult': 1.0, 'catsl_mult': 3.0, 'cooldown_min': 45, 'time_stop': 2400, 'pos_size_pct': 0.05},
        'E': {'enabled': False},
    }


def make_config(lock_r=None, partial_r=None, partial_pct=0.0,
                trail_after_partial=False, trail_atr=0.0,
                tp_mult_override=None, sl_mult_override=None,
                pos_size_A=0.025, pos_size_B=0.10, pos_size_D=0.05,
                time_stop_override=None):
    cfg = base_strategies()
    if tp_mult_override is not None:
        for s in ['A', 'B', 'D']: cfg[s]['tp_mult'] = tp_mult_override
    if sl_mult_override is not None:
        for s in ['A', 'B', 'D']: cfg[s]['sl_mult'] = sl_mult_override
    if time_stop_override is not None:
        for s in ['A', 'B', 'D']: cfg[s]['time_stop'] = time_stop_override
    cfg['A']['pos_size_pct'] = pos_size_A
    cfg['B']['pos_size_pct'] = pos_size_B
    cfg['D']['pos_size_pct'] = pos_size_D
    cfg.update({
        'partial_trigger_r': partial_r, 'partial_close_pct': partial_pct,
        'be_trigger_r': None, 'lock_trigger_r': lock_r,
        'trail_after_partial': trail_after_partial, 'trail_atr_mult': trail_atr,
        'adaptive_size': False,
    })
    return cfg


CONFIGS = {
    'v32g_baseline': make_config(lock_r=0.6, partial_r=0.8, partial_pct=0.3,
                                  trail_after_partial=True, trail_atr=0.6),
    'v33a_more_partial_50': make_config(lock_r=0.6, partial_r=0.8, partial_pct=0.5,
                                         trail_after_partial=True, trail_atr=0.6),
    'v33b_more_partial_70': make_config(lock_r=0.6, partial_r=0.8, partial_pct=0.7,
                                         trail_after_partial=True, trail_atr=0.6),
    'v33c_earlier_lock_0.4': make_config(lock_r=0.4, partial_r=0.8, partial_pct=0.3,
                                          trail_after_partial=True, trail_atr=0.6),
    'v33d_earlier_partial_0.6': make_config(lock_r=0.6, partial_r=0.6, partial_pct=0.5,
                                              trail_after_partial=True, trail_atr=0.6),
    'v33e_tighter_trail_0.4': make_config(lock_r=0.6, partial_r=0.8, partial_pct=0.3,
                                            trail_after_partial=True, trail_atr=0.4),
    'v33f_wider_trail_0.8': make_config(lock_r=0.6, partial_r=0.8, partial_pct=0.3,
                                          trail_after_partial=True, trail_atr=0.8),
    'v33g_wider_TP_1.5': make_config(lock_r=0.6, partial_r=0.8, partial_pct=0.3,
                                       trail_after_partial=True, trail_atr=0.6,
                                       tp_mult_override=1.5),
    'v33h_no_partial_lock_only': make_config(lock_r=0.4, partial_r=None, partial_pct=0.0,
                                               trail_after_partial=False, trail_atr=0.0),
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
        direction = 'LONG' if momentum > 0 else 'SHORT'
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
        atr = computeATR(prices, 60)
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
        direction = 'LONG' if current > bb['upper'] else 'SHORT'
        atr = computeATR(prices, 60)
        self._open_position(sym, direction, 'D', prices[-1], atr, cfg, tick)
        self.last_signal_tick['D'] = tick

    def _try_strategy_e(self, sym, prices, tick): pass

    def _open_position(self, sym, direction, strategy, price, atr, cfg, tick):
        pos_size_pct = cfg.get('pos_size_pct', 0.05)
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
            return {'trades': 0, 'wr': 0, 'pnl': 0, 'pf': 0, 'max_dd': 0, 'per_strat': {},
                    'avg_hold_min': 0, 'tp_pct': 0, 'sl_pct': 0, 'time_pct': 0,
                    'sharpe': 0, 'sortino': 0, 'max_consec_loss': 0, 'consistency': 0,
                    'hourly_std': 0, 'recovery': 0, 'avg_r': 0, 'partial_pct': 0}
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
                'avg_r': avg_r}


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
            'sortino_mean': statistics.mean(m['sortino'] for m in seed_metrics),
            'max_consec_loss_mean': statistics.mean(m['max_consec_loss'] for m in seed_metrics),
            'consistency_mean': statistics.mean(m['consistency'] for m in seed_metrics),
            'hourly_std_mean': statistics.mean(m['hourly_std'] for m in seed_metrics),
            'recovery_mean': statistics.mean(m['recovery'] for m in seed_metrics),
            'avg_r_mean': statistics.mean(m['avg_r'] for m in seed_metrics),
            'trades_mean': statistics.mean(m['trades'] for m in seed_metrics),
            'max_dd_mean': statistics.mean(m['max_dd'] for m in seed_metrics),
            'partial_pct_mean': statistics.mean(m['partial_pct'] for m in seed_metrics),
            'profitable_seeds': sum(1 for m in seed_metrics if m['pnl'] > 0) / len(seed_metrics) * 100,
            'wr_above_60_seeds': sum(1 for m in seed_metrics if m['wr'] >= 60) / len(seed_metrics) * 100,
            'pnl_per_seed': [m['pnl'] for m in seed_metrics],
        }
    return agg


def print_results(agg, baseline_name='v32g_baseline'):
    print("\n" + "="*220)
    print(f"{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'HourlyStd':<10} {'Recovery':<9} {'AvgR':<7} {'Partial%':<9} {'Stab%':<7}")
    print("="*220)
    for name, m in agg.items():
        is_baseline = name == baseline_name
        marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
        print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['hourly_std_mean']:.1f}{'':>6} {m['recovery_mean']:+.2f}{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['partial_pct_mean']:.0f}%{'':>5} {m['profitable_seeds']:.0f}%")
    print("="*220)

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
        print(f"  {name:<32} ΔWR {d_wr:+.1f}  ΔP&L {d_pnl:+.1f}  ΔSharpe {d_sharpe:+.2f}  ΔRR {d_r:+.3f}  ΔConsist {d_cons:+.1f}  ΔMaxCL {d_cl:+.1f}  ΔMaxDD {d_dd:+.2f}  ΔProfitSeeds {d_profit:+.0f}%")

    print("\nPer-seed P&L:")
    print(f"  {'Config':<32} | " + " | ".join(f"S{s}" for s in SEEDS))
    print("  " + "-" * 100)
    for name, m in agg.items():
        pnl_str = " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed'])
        print(f"  {name:<32} | {pnl_str}")


def run():
    print(f"v33 STABILITY SWEEP")
    print(f"Configs: {len(CONFIGS)} | Seeds: {SEEDS} | Hours per seed: {SIM_HOURS} | Tokens: {N_TOKENS}")
    all_seed_results = []
    for seed in SEEDS:
        print(f"\n  Running seed {seed}...")
        result = run_single_seed(seed)
        all_seed_results.append(result)
        for name, m in result.items():
            print(f"    {name:<32} WR {m['wr']:.1f}%  P&L {m['pnl']:+.1f}  RR {m['avg_r']:+.2f}  MaxDD {m['max_dd']:.2f}%")

    print("\n" + "#" * 220)
    print("# AGGREGATED RESULTS (mean ± std across 8 seeds)")
    print("#" * 220)
    agg = aggregate_results(all_seed_results)
    print_results(agg)

    print("\n" + "=" * 80)
    print("WINNER SELECTION (target: ≥50% profitable seeds + WR≥60 + MaxDD<1.5)")
    print("=" * 80)
    candidates = [(name, m) for name, m in agg.items()
                  if m['profitable_seeds'] >= 50 and m['wr_above_60_seeds'] >= 50 and m['max_dd_mean'] < 1.5]
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
        print("\n  ⚠️ No config met 50% profitable + 50% WR>60. Listing best P&L:")
        ranked = sorted(agg.items(), key=lambda x: x[1]['pnl_mean'], reverse=True)
        for i, (name, m) in enumerate(ranked[:3]):
            print(f"  #{i+1} {name:<32} P&L {m['pnl_mean']:+.2f}  WR {m['wr_mean']:.1f}%  Profit {m['profitable_seeds']:.0f}%")


if __name__ == "__main__":
    run()
