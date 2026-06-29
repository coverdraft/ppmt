#!/usr/bin/env python3
"""
v21 — SCALPING MODE para WR alto
Objetivo: WR > 61% (ideal 71%)

Filosofía: TP apretado + SL ancho = alta probabilidad de hit
- TP 0.8 ATR (apretado, se ejecuta rápido)
- SL 3.0 ATR (ancho, tolera ruido)
- Filtros fuertes (momentum 0.4%+, RSI extremos, BB tight)
- Time stop 1h (si no se mueve, salir)
- R:R 1:0.27 → necesita WR 79%+ para ser profitable
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
    if len(slice_) < 5: return {'width': 0, 'upper': 0, 'lower': 0, 'mean': last}
    mean = sum(slice_) / len(slice_)
    var = sum((p - mean) ** 2 for p in slice_) / len(slice_)
    std = var ** 0.5
    return {'width': (mult * 2 * std) / mean if mean else 0, 'upper': mean + mult * std, 'lower': mean - mult * std, 'mean': mean}

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

@dataclass
class Trade:
    symbol: str; direction: str; strategy: str
    entry_price: float; exit_price: float; size_usdt: float
    pnl: float; close_reason: str; hold_ticks: int

CONFIGS = {
    'v15_baseline': {
        'A': {'momentum_min': 0.15, 'max_pos': 2, 'sl_mult': 2.0, 'tp_mult': 2.5, 'catsl_mult': 5.0, 'cooldown_min': 45, 'rsi_min': 0, 'rsi_max': 100, 'time_stop': 4800},
        'B': {'rsi_lo': 40, 'rsi_hi': 60, 'max_pos': 2, 'sl_mult': 2.0, 'tp_mult': 2.5, 'catsl_mult': 4.0, 'cooldown_min': 45, 'enabled': True, 'time_stop': 4800},
        'D': {'bb_width_max': 0.015, 'max_pos': 1, 'sl_mult': 1.5, 'tp_mult': 3.0, 'catsl_mult': 3.5, 'cooldown_min': 45, 'time_stop': 4800},
    },
    'v21a_scalp_tightTP': {  # TP 0.8 ATR, SL 3.0 ATR
        'A': {'momentum_min': 0.30, 'max_pos': 1, 'sl_mult': 3.0, 'tp_mult': 0.8, 'catsl_mult': 6.0, 'cooldown_min': 30, 'rsi_min': 30, 'rsi_max': 70, 'time_stop': 2400},
        'B': {'rsi_lo': 35, 'rsi_hi': 65, 'max_pos': 1, 'sl_mult': 3.0, 'tp_mult': 0.8, 'catsl_mult': 5.0, 'cooldown_min': 30, 'enabled': True, 'time_stop': 2400},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': 2.5, 'tp_mult': 0.7, 'catsl_mult': 4.5, 'cooldown_min': 30, 'time_stop': 2400},
    },
    'v21b_scalp_ultraTight': {  # TP 0.5 ATR, SL 2.5 ATR — ultra scalping
        'A': {'momentum_min': 0.40, 'max_pos': 1, 'sl_mult': 2.5, 'tp_mult': 0.5, 'catsl_mult': 5.0, 'cooldown_min': 30, 'rsi_min': 25, 'rsi_max': 75, 'time_stop': 1800},
        'B': {'rsi_lo': 30, 'rsi_hi': 70, 'max_pos': 1, 'sl_mult': 2.5, 'tp_mult': 0.5, 'catsl_mult': 4.5, 'cooldown_min': 30, 'enabled': True, 'time_stop': 1800},
        'D': {'bb_width_max': 0.010, 'max_pos': 1, 'sl_mult': 2.0, 'tp_mult': 0.4, 'catsl_mult': 4.0, 'cooldown_min': 30, 'time_stop': 1800},
    },
    'v21c_scalp_wideSL': {  # TP 1.0 ATR, SL 4.0 ATR — más room para SL
        'A': {'momentum_min': 0.35, 'max_pos': 1, 'sl_mult': 4.0, 'tp_mult': 1.0, 'catsl_mult': 7.0, 'cooldown_min': 45, 'rsi_min': 30, 'rsi_max': 70, 'time_stop': 3000},
        'B': {'rsi_lo': 35, 'rsi_hi': 65, 'max_pos': 1, 'sl_mult': 4.0, 'tp_mult': 1.0, 'catsl_mult': 6.0, 'cooldown_min': 45, 'enabled': True, 'time_stop': 3000},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': 3.5, 'tp_mult': 0.9, 'catsl_mult': 5.5, 'cooldown_min': 45, 'time_stop': 3000},
    },
    'v21d_scalp_filtered': {  # TP 1.0 ATR, SL 3.0 ATR + filtros muy estrictos
        'A': {'momentum_min': 0.50, 'max_pos': 1, 'sl_mult': 3.0, 'tp_mult': 1.0, 'catsl_mult': 6.0, 'cooldown_min': 60, 'rsi_min': 25, 'rsi_max': 75, 'time_stop': 2400},
        'B': {'rsi_lo': 25, 'rsi_hi': 75, 'max_pos': 1, 'sl_mult': 3.0, 'tp_mult': 1.0, 'catsl_mult': 5.0, 'cooldown_min': 60, 'enabled': True, 'time_stop': 2400},
        'D': {'bb_width_max': 0.010, 'max_pos': 1, 'sl_mult': 2.5, 'tp_mult': 0.9, 'catsl_mult': 4.5, 'cooldown_min': 60, 'time_stop': 2400},
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

    def _try_strategy_a(self, sym, prices, tick):
        cfg = self.config['A']
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
        cfg = self.config['D']
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

    def _open_position(self, sym, direction, strategy, price, atr, cfg, tick):
        size_usdt = min(self.cash * POSITION_SIZE_PCT, self.cash * 0.10)
        if size_usdt < 50: return
        slip = price * (SLIPPAGE_PCT / 100)
        entry_price = price + slip if direction == 'LONG' else price - slip
        fee = size_usdt * (FEE_PCT / 100)
        self.cash -= (size_usdt + fee)
        qty = size_usdt / entry_price
        pos = Position(symbol=sym, direction=direction, strategy=strategy,
                       entry_price=entry_price, qty=qty, size_usdt=size_usdt, entry_tick=tick)
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

    def _check_stops(self, sym, prices, tick):
        if sym not in self.positions: return
        pos = self.positions[sym]
        price = prices[-1]
        cfg = self.config[pos.strategy]
        if tick - pos.entry_tick > cfg['time_stop']:
            self._close_position(sym, price, 'TIME', tick)
            self.cooldown_until[sym] = tick + int(cfg['cooldown_min'] * 60 / TICK_SECONDS)
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
        gross = (exit_price - pos.entry_price) * pos.qty if pos.direction == 'LONG' else (pos.entry_price - exit_price) * pos.qty
        exit_fee = exit_price * pos.qty * (FEE_PCT / 100)
        net = gross - exit_fee
        self.cash += pos.size_usdt + net
        self.trades.append(Trade(symbol=sym, direction=pos.direction, strategy=pos.strategy,
                                  entry_price=pos.entry_price, exit_price=exit_price,
                                  size_usdt=pos.size_usdt, pnl=net,
                                  close_reason=reason, hold_ticks=tick - pos.entry_tick))
        self.strategy_pos_count[pos.strategy] -= 1
        del self.positions[sym]

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

    def get_metrics(self):
        closed = self.trades
        if not closed:
            return {'trades': 0, 'wr': 0, 'pnl': 0, 'pf': 0, 'max_dd': 0, 'per_strat': {}, 'avg_hold_min': 0, 'tp_pct': 0, 'sl_pct': 0}
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        per_strat = {}
        for s in ['A', 'B', 'D']:
            s_trades = [t for t in closed if t.strategy == s]
            s_wins = [t for t in s_trades if t.pnl > 0]
            per_strat[s] = {'trades': len(s_trades), 'wr': len(s_wins) / len(s_trades) * 100 if s_trades else 0, 'pnl': sum(t.pnl for t in s_trades)}
        tp_count = sum(1 for t in closed if t.close_reason == 'TP')
        sl_count = sum(1 for t in closed if t.close_reason in ('SL', 'CAT_SL'))
        time_count = sum(1 for t in closed if t.close_reason == 'TIME')
        avg_hold = sum(t.hold_ticks for t in closed) / len(closed) * TICK_SECONDS / 60
        return {'trades': len(closed),
                'wr': len(wins) / len(closed) * 100, 'pnl': sum(t.pnl for t in closed),
                'pf': gross_win / gross_loss if gross_loss > 0 else float('inf'),
                'max_dd': self.max_drawdown, 'per_strat': per_strat,
                'avg_hold_min': avg_hold,
                'tp_pct': tp_count / len(closed) * 100,
                'sl_pct': sl_count / len(closed) * 100,
                'time_pct': time_count / len(closed) * 100}


def run():
    print(f"Generando {N_TOKENS} tokens × {TOTAL_TICKS} ticks ({SIM_HOURS}h)...")
    all_prices = {f"TOK{i:02d}": gen_regime_prices(TOTAL_TICKS, 1.0 * (1 + random.uniform(-0.3, 0.3)))
                  for i in range(N_TOKENS)}

    engines = [EngineSim(cfg, name) for name, cfg in CONFIGS.items()]
    print(f"Simulando {SIM_HOURS}h con {len(engines)} configs...\n")

    for tick in range(TOTAL_TICKS):
        for sym in all_prices:
            if tick + 1 < 60: continue
            prices_slice = all_prices[sym][max(0, tick-80):tick+1]
            for engine in engines:
                engine._try_strategy_a(sym, prices_slice, tick)
                engine._try_strategy_b(sym, prices_slice, tick)
                engine._try_strategy_d(sym, prices_slice, tick)
                engine._check_stops(sym, prices_slice, tick)
        for engine in engines:
            engine.update_equity(all_prices, tick)

    print("="*130)
    print(f"{'Config':<26} {'Trades':<8} {'WR':<8} {'P&L':<11} {'PF':<7} {'MaxDD':<7} {'AvgHold':<9} {'TP%':<7} {'SL%':<7} {'TIME%':<7} {'Strat A':<20} {'Strat B':<20} {'Strat D':<20}")
    print("="*130)
    best = None; best_wr = 0
    for engine in engines:
        m = engine.get_metrics()
        ps = m['per_strat']
        def strat_str(s):
            x = ps[s]
            return f"{x['trades']}t/{x['wr']:.0f}%/{x['pnl']:+.1f}"
        print(f"{engine.name:<26} {m['trades']:<8} {m['wr']:.1f}%{'':>2} {m['pnl']:+.2f}{'':>3} {m['pf']:.2f}{'':>3} {m['max_dd']:.2f}%{'':>2} {m['avg_hold_min']:.1f}m{'':>3} {m['tp_pct']:.0f}%{'':>4} {m['sl_pct']:.0f}%{'':>4} {m['time_pct']:.0f}%{'':>4} {strat_str('A'):<20} {strat_str('B'):<20} {strat_str('D'):<20}")
        if m['wr'] > best_wr and m['trades'] >= 30:
            best_wr = m['wr']; best = engine.name

    print("\n" + "="*130)
    print("ANÁLISIS:")
    print("="*130)
    base = engines[0].get_metrics()
    for engine in engines[1:]:
        m = engine.get_metrics()
        delta_pnl = m['pnl'] - base['pnl']
        delta_wr = m['wr'] - base['wr']
        verdict = "✅ MEJOR" if (delta_pnl > 0 and delta_wr > 0) else "⚠️ MIXTO" if delta_pnl * delta_wr > 0 else "❌ PEOR"
        print(f"  {verdict}  {engine.name}: ΔWR {delta_wr:+.1f}pp, ΔP&L {delta_pnl:+.2f}")
    if best:
        print(f"\n🏆 Mejor WR: {best} ({best_wr:.1f}%)")


if __name__ == "__main__":
    run()
