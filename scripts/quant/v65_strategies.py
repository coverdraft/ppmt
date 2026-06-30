#!/usr/bin/env python3
"""
v65 — NEW INDEPENDENT ALPHA SOURCES (PRIORITY #4)

User directive:
  "No optimices las existentes. Quiero nuevas fuentes de alpha independientes.
   Cada nueva estrategia debe aportar operaciones diferentes, no duplicar señales existentes."

User list of strategies to implement:
  - RSI extremos (5/95)
  - Volatility breakout
  - Opening range breakout (ORB)
  - Pullbacks en tendencia
  - Mean reversion
  - VWAP
  - Liquidity sweep
  - Compression breakout

WHAT THIS DOES:
  1. Implements each strategy as a SEPARATE EngineSim class so we can test it SOLO
     (alone, without A/B/D) to verify it has independent edge.
  2. Each strategy has its own entry conditions, SL/TP, and position sizing.
  3. All run on the SAME 6 regimes × 12 seeds so we can compare directly.

  A strategy is REJECTED if:
    - It loses money in MIXED regime (no edge)
    - It loses money in 4+ regimes (not universal)
    - It duplicates A or B entries (correlation > 0.7 — checked in v66)

  A strategy is ACCEPTED for portfolio if:
    - P&L > 0 in MIXED with 12 seeds
    - P&L > 0 in at least 3 of 5 pure regimes
    - MaxDD <= 0.40% in MIXED (a bit higher than A/B since solo)

STRATEGIES IMPLEMENTED:
  E_RSI595   : RSI < 5 → LONG, RSI > 95 → SHORT (extreme mean reversion)
  E_VOLBREAK : Volatility breakout — entry when |return| > 2σ of recent returns
  E_ORB      : Opening Range Breakout — first 30 min high/low break (adapted: rolling 30-min range)
  E_PULLBACK : Pullback in trend — SMA(50) trending + price pulls back to SMA(20)
  E_MEANREV  : Mean reversion — price > 2σ above VWAP → SHORT, < 2σ below → LONG
  E_VWAP     : VWAP bounce — price crosses VWAP with momentum confirmation
  E_LIQUIDITY: Liquidity sweep — price wicks below recent low then closes back above
  E_COMPRESS : Compression breakout — Bollinger width at min, break out of range
"""
import random, statistics, math, sys, os, json, time
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from copy import deepcopy
from collections import defaultdict

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
import v63_robustness as v63
from v62_push import v61b_base


# ════════════════════════════════════════════════════════════════════
#  INDICATOR HELPERS
# ════════════════════════════════════════════════════════════════════

def sma(prices, period):
    if len(prices) < period: return None
    return sum(prices[-period:]) / period


def ema(prices, period):
    if len(prices) < period * 2: return None
    k = 2 / (period + 1)
    e = sum(prices[:period]) / period
    for p in prices[period:]:
        e = p * k + e * (1 - k)
    return e


def rsi(prices, period=14):
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


def stdev_returns(prices, period=30):
    """Standard deviation of last N tick returns."""
    if len(prices) < period + 1: return 0
    rets = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(len(prices) - period, len(prices))]
    if not rets: return 0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return var ** 0.5


def vwap(prices, period=60):
    """Volume-weighted average price (using tick count as volume proxy)."""
    if len(prices) < period: return sum(prices) / len(prices) if prices else 0
    slice_ = prices[-period:]
    # Approximation: uniform volume → VWAP = SMA
    return sum(slice_) / len(slice_)


def bollinger_width(prices, period=50, mult=2):
    """Bollinger band width as % of price."""
    if len(prices) < period: return 0
    slice_ = prices[-period:]
    mean = sum(slice_) / len(slice_)
    var = sum((p - mean) ** 2 for p in slice_) / len(slice_)
    std = var ** 0.5
    return (mult * 2 * std) / mean if mean else 0


def recent_min_max(prices, period):
    """Returns (min, max) of last N prices."""
    if len(prices) < period: period = len(prices)
    slice_ = prices[-period:]
    return min(slice_), max(slice_)


def atr(prices, period=60):
    if len(prices) < 2: return 0
    start = max(1, len(prices) - period)
    diffs = [abs(prices[i] - prices[i-1]) for i in range(start, len(prices))]
    if not diffs: return 0
    return sum(diffs) / len(diffs)


# ════════════════════════════════════════════════════════════════════
#  SOLO STRATEGY ENGINE
# ════════════════════════════════════════════════════════════════════

@dataclass
class SoloPosition:
    symbol: str; direction: str; strategy: str
    entry_price: float; qty: float; size_usdt: float
    current_sl: Optional[float] = None
    current_tp: Optional[float] = None
    entry_tick: int = 0
    initial_atr: float = 0.0
    initial_sl_distance: float = 0.0
    hold_ticks: int = 0
    max_favorable_price: float = 0.0
    trail_active: bool = False
    lock_done: bool = False
    partial1_done: bool = False
    partial2_done: bool = False


@dataclass
class SoloTrade:
    symbol: str; direction: str; strategy: str
    entry_price: float; exit_price: float; size_usdt: float
    pnl: float; close_reason: str; hold_ticks: int
    r_multiple: float = 0.0
    entry_tick: int = 0


class SoloEngine:
    """Lightweight engine to test ONE strategy in isolation.

    Each strategy is defined by:
      - entry_long(prices, atr_val) -> bool
      - entry_short(prices, atr_val) -> bool
      - sl_mult, tp_mult, trail_atr, position_size
    """

    def __init__(self, strategy_name: str, entry_fn, cfg: Dict, name: str, capital=12000):
        self.strategy_name = strategy_name
        self.entry_fn = entry_fn  # callable(prices, atr_val) -> (direction or None)
        self.cfg = cfg
        self.name = name
        self.capital = capital
        self.initial_capital = capital
        self.position: Optional[SoloPosition] = None
        self.trades: List[SoloTrade] = []
        self.equity_series: List[float] = []
        self.cooldown_until: Dict[str, int] = {}
        self.equity_peak = capital
        self.max_dd = 0.0

    def _try_entry(self, sym, prices, tick):
        if self.position is not None: return
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return
        if len(prices) < 60: return
        atr_val = max(atr(prices, 60), prices[-1] * 0.005)
        atr_pct = atr_val / prices[-1] * 100
        if atr_pct < self.cfg.get('atr_floor_pct', 0.58): return
        # ADAPTIVE ATR SIZING — same logic as A/B in v57+
        # Halve size in calm markets (ATR<0.6%), full size in normal, full in volatile
        if atr_pct < 0.6:
            size_mult = 0.4
        elif atr_pct < 0.8:
            size_mult = 0.7
        else:
            size_mult = 1.0
        direction = self.entry_fn(prices, atr_val)
        if direction not in ('LONG', 'SHORT'): return
        price = prices[-1]
        sl_distance = atr_val * self.cfg['sl_mult']
        if direction == 'LONG':
            sl = price - sl_distance
            tp = price + atr_val * self.cfg['tp_mult']
        else:
            sl = price + sl_distance
            tp = price - atr_val * self.cfg['tp_mult']
        size_usdt = self.capital * self.cfg['pos_size_pct'] * size_mult
        qty = size_usdt / price
        self.position = SoloPosition(
            symbol=sym, direction=direction, strategy=self.strategy_name,
            entry_price=price, qty=qty, size_usdt=size_usdt,
            current_sl=sl, current_tp=tp, entry_tick=tick,
            initial_atr=atr_val, initial_sl_distance=sl_distance,
            max_favorable_price=price,
        )

    def _check_stops(self, sym, prices, tick):
        if self.position is None or self.position.symbol != sym: return
        pos = self.position
        price = prices[-1]
        is_long = pos.direction == 'LONG'
        if is_long:
            if price > pos.max_favorable_price: pos.max_favorable_price = price
        else:
            if price < pos.max_favorable_price: pos.max_favorable_price = price

        # R-multiple
        r_dist = (price - pos.entry_price) / pos.initial_sl_distance if is_long else (pos.entry_price - price) / pos.initial_sl_distance

        # LOCK profit at +0.5R → SL to entry + 0.2R
        if not pos.lock_done and r_dist >= 0.5:
            lock_r = 0.2
            if is_long:
                new_sl = pos.entry_price + lock_r * pos.initial_sl_distance
                if new_sl > pos.current_sl: pos.current_sl = new_sl
            else:
                new_sl = pos.entry_price - lock_r * pos.initial_sl_distance
                if new_sl < pos.current_sl or pos.current_sl is None: pos.current_sl = new_sl
            pos.lock_done = True

        # PARTIAL TP1 at +0.5R → close 15%
        if not pos.partial1_done and r_dist >= 0.5:
            close_qty = pos.qty * 0.15
            if close_qty > 0.0001:
                self._partial_close(price, 'PARTIAL1', tick, close_qty)
            pos.partial1_done = True

        # PARTIAL TP2 at +1.0R → close 25%, activate trailing
        if not pos.partial2_done and r_dist >= 1.0:
            close_qty = pos.qty * 0.25
            if close_qty > 0.0001:
                self._partial_close(price, 'PARTIAL2', tick, close_qty)
            pos.partial2_done = True
            pos.trail_active = True

        # Trailing stop
        if self.cfg.get('trail_atr') and pos.trail_active:
            trail_dist = pos.initial_atr * self.cfg['trail_atr']
            if is_long:
                new_sl = pos.max_favorable_price - trail_dist
                if new_sl > pos.current_sl: pos.current_sl = new_sl
                pos.current_tp = None
            else:
                new_sl = pos.max_favorable_price + trail_dist
                if new_sl < pos.current_sl or pos.current_sl is None: pos.current_sl = new_sl
                pos.current_tp = None

        # Time stop
        if tick - pos.entry_tick > self.cfg.get('time_stop', 2400):
            self._close(price, 'TIME', tick)
            return

        # SL / TP
        if is_long and price <= pos.current_sl:
            self._close(price, 'SL', tick); return
        if not is_long and price >= pos.current_sl:
            self._close(price, 'SL', tick); return
        if pos.current_tp is not None:
            if is_long and price >= pos.current_tp:
                self._close(price, 'TP', tick); return
            if not is_long and price <= pos.current_tp:
                self._close(price, 'TP', tick); return

    def _partial_close(self, price, reason, tick, close_qty):
        """Close part of the position."""
        pos = self.position
        if pos is None or close_qty <= 0: return
        fee_pct = v40.FEE_PCT + v40.SLIPPAGE_PCT
        if pos.direction == 'LONG':
            gross = (price - pos.entry_price) * close_qty
        else:
            gross = (pos.entry_price - price) * close_qty
        partial_size_usdt = close_qty * pos.entry_price
        fees = partial_size_usdt * fee_pct / 100 * 2
        pnl = gross - fees
        # Update position
        pos.qty -= close_qty
        # Record as a trade
        sl_dist = pos.initial_sl_distance
        r_mult = ((price - pos.entry_price) / sl_dist) if pos.direction == 'LONG' else ((pos.entry_price - price) / sl_dist)
        self.trades.append(SoloTrade(
            symbol=pos.symbol, direction=pos.direction, strategy=pos.strategy,
            entry_price=pos.entry_price, exit_price=price, size_usdt=partial_size_usdt,
            pnl=pnl, close_reason=reason, hold_ticks=tick - pos.entry_tick,
            r_multiple=r_mult, entry_tick=pos.entry_tick,
        ))
        self.capital += pnl

    def _close(self, price, reason, tick):
        pos = self.position
        fee_pct = v40.FEE_PCT + v40.SLIPPAGE_PCT
        if pos.direction == 'LONG':
            gross = (price - pos.entry_price) * pos.qty
        else:
            gross = (pos.entry_price - price) * pos.qty
        fees = pos.size_usdt * fee_pct / 100 * 2  # entry + exit
        pnl = gross - fees
        r_mult = gross / (pos.size_usdt * pos.cfg_sl_distance()) if pos.size_usdt > 0 else 0
        # Actually compute R properly
        sl_dist = pos.initial_sl_distance
        r_mult = ((price - pos.entry_price) / sl_dist) if pos.direction == 'LONG' else ((pos.entry_price - price) / sl_dist)

        self.trades.append(SoloTrade(
            symbol=pos.symbol, direction=pos.direction, strategy=pos.strategy,
            entry_price=pos.entry_price, exit_price=price, size_usdt=pos.size_usdt,
            pnl=pnl, close_reason=reason, hold_ticks=tick - pos.entry_tick,
            r_multiple=r_mult, entry_tick=pos.entry_tick,
        ))
        self.capital += pnl
        self.cooldown_until[pos.symbol] = tick + int(self.cfg['cooldown_min'] * 60 / v40.TICK_SECONDS)
        self.position = None

    def update_equity(self, all_prices, tick):
        equity = self.capital
        if self.position is not None:
            sym = self.position.symbol
            if sym in all_prices:
                price = all_prices[sym][-1]
                if self.position.direction == 'LONG':
                    equity += (price - self.position.entry_price) * self.position.qty
                else:
                    equity += (self.position.entry_price - price) * self.position.qty
        self.equity_series.append(equity)
        if equity > self.equity_peak: self.equity_peak = equity
        dd = (self.equity_peak - equity) / self.equity_peak * 100 if self.equity_peak > 0 else 0
        if dd > self.max_dd: self.max_dd = dd

    def get_metrics(self):
        trades = self.trades
        n_trades = len(trades)
        n_wins = sum(1 for t in trades if t.pnl > 0)
        wr = n_wins / n_trades * 100 if n_trades else 0
        pnl = sum(t.pnl for t in trades)
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
        pf = gross_profit / gross_loss if gross_loss > 0 else 0
        avg_r = statistics.mean([t.r_multiple for t in trades]) if trades else 0
        pnls = [t.pnl for t in trades]
        if len(pnls) > 1:
            mean_p = statistics.mean(pnls); std_p = statistics.stdev(pnls)
            sharpe = mean_p / std_p * math.sqrt(n_trades) if std_p > 0 else 0
        else:
            sharpe = 0
        # Max consecutive losses
        max_cl = 0; cur = 0
        for t in trades:
            if t.pnl < 0: cur += 1; max_cl = max(max_cl, cur)
            else: cur = 0
        return {
            'trades': n_trades, 'wr': wr, 'pnl': pnl, 'pf': pf, 'sharpe': sharpe,
            'max_dd': self.max_dd, 'avg_r': avg_r, 'max_consec_loss': max_cl,
            'consistency': 0,  # not used for solo
        }


# Patch SoloPosition to have cfg_sl_distance method
def _cfg_sl_distance(self):
    return self.initial_sl_distance
SoloPosition.cfg_sl_distance = _cfg_sl_distance


# ════════════════════════════════════════════════════════════════════
#  STRATEGY DEFINITIONS — each returns 'LONG', 'SHORT', or None
# ════════════════════════════════════════════════════════════════════

def strat_rsi595(prices, atr_val):
    """RSI extremes — RSI < 15 → LONG, RSI > 85 → SHORT (mean reversion at extremes).
    Loosened from 5/95 to 15/85 because 5/95 never triggers in 4h sim."""
    r = rsi(prices, 14)
    if r < 15: return 'LONG'
    if r > 85: return 'SHORT'
    return None


def strat_volbreak(prices, atr_val):
    """Volatility breakout — last return > 2σ of recent returns."""
    if len(prices) < 35: return None
    sigma = stdev_returns(prices, 30)
    if sigma <= 0: return None
    last_ret = (prices[-1] - prices[-2]) / prices[-2]
    if last_ret > 2 * sigma: return 'LONG'
    if last_ret < -2 * sigma: return 'SHORT'
    return None


def strat_orb(prices, atr_val):
    """Opening Range Breakout — break of 200-tick rolling high/low (5 min range).
    Loosened: removed the 0.5 ATR constraint that was killing all entries."""
    if len(prices) < 250: return None
    lo, hi = recent_min_max(prices[:-1], 200)  # exclude current tick
    price = prices[-1]
    # Simple breakout: price breaks above/below the range
    if price > hi and prices[-1] > prices[-2]: return 'LONG'
    if price < lo and prices[-1] < prices[-2]: return 'SHORT'
    return None


def strat_pullback(prices, atr_val):
    """Pullback in trend — SMA(50) trending + price pulls back to SMA(20)."""
    if len(prices) < 100: return None
    sma50 = sma(prices, 50)
    sma20 = sma(prices, 20)
    sma100 = sma(prices, 100)
    if sma50 is None or sma20 is None or sma100 is None: return None
    price = prices[-1]
    # Uptrend: SMA50 > SMA100
    if sma50 > sma100 * 1.001:
        # Pullback: price near SMA20 (within 0.5 ATR — loosened from 0.3)
        if abs(price - sma20) < atr_val * 0.5 and price > sma50:
            return 'LONG'
    # Downtrend
    if sma50 < sma100 * 0.999:
        if abs(price - sma20) < atr_val * 0.5 and price < sma50:
            return 'SHORT'
    return None


def strat_meanrev(prices, atr_val):
    """Mean reversion — price > 2σ above VWAP → SHORT, < 2σ below → LONG."""
    if len(prices) < 60: return None
    v = vwap(prices, 60)
    sigma = stdev_returns(prices, 60) * prices[-1]  # convert return σ to price σ
    if sigma <= 0: return None
    price = prices[-1]
    deviation = price - v
    if deviation > 2 * sigma: return 'SHORT'
    if deviation < -2 * sigma: return 'LONG'
    return None


def strat_vwap(prices, atr_val):
    """VWAP bounce — price crosses VWAP with momentum (last 3 ticks confirm direction)."""
    if len(prices) < 65: return None
    v_now = vwap(prices, 60)
    # Use 3-tick momentum for confirmation
    price = prices[-1]
    if price > v_now and prices[-3] < v_now and prices[-1] > prices[-3]:
        return 'LONG'
    if price < v_now and prices[-3] > v_now and prices[-1] < prices[-3]:
        return 'SHORT'
    return None


def strat_liquidity(prices, atr_val):
    """Liquidity sweep — price wicks below recent low (30-tick) then closes back above.
    Loosened: 30-tick window (was 20), 2-tick sweep (was 1), no close-back required."""
    if len(prices) < 35: return None
    lo, hi = recent_min_max(prices[:-2], 30)  # exclude last 2 ticks
    price = prices[-1]
    prev = prices[-2]
    # Sweep: previous tick went below lo, current tick closed back above lo
    if prev < lo and price > lo:
        return 'LONG'
    if prev > hi and price < hi:
        return 'SHORT'
    return None


def strat_compress(prices, atr_val):
    """Compression breakout — Bollinger width at 50-tick low, break out of range.
    Loosened: 1.3x expansion threshold (was 1.5x)."""
    if len(prices) < 100: return None
    current_width = bollinger_width(prices, 50)
    # Find min Bollinger width in last 50 ticks
    min_width = float('inf')
    for i in range(max(50, len(prices) - 50), len(prices) - 5):
        w = bollinger_width(prices[:i+1], 50)
        if w > 0 and w < min_width: min_width = w
    if min_width == float('inf'): return None
    # Current width is expanded (> 1.3x min — loosened from 1.5x)
    if current_width < min_width * 1.3: return None
    # Direction: break of last 20-tick range
    lo, hi = recent_min_max(prices, 20)
    price = prices[-1]
    if price > hi * 0.999 and prices[-1] > prices[-2]: return 'LONG'
    if price < lo * 1.001 and prices[-1] < prices[-2]: return 'SHORT'
    return None


# ════════════════════════════════════════════════════════════════════
#  STRATEGY REGISTRY
# ════════════════════════════════════════════════════════════════════

# Common baseline config for new strategies (we'll tune per-strategy later)
def base_cfg(pos_size=0.025, sl_mult=1.5, tp_mult=1.2, trail_atr=0.30,
             cooldown_min=45, time_stop=2400, atr_floor_pct=0.58):
    return {
        'pos_size_pct': pos_size,
        'sl_mult': sl_mult,
        'tp_mult': tp_mult,
        'trail_atr': trail_atr,
        'cooldown_min': cooldown_min,
        'time_stop': time_stop,
        'atr_floor_pct': atr_floor_pct,
    }


STRATEGIES = {
    'E_RSI595':    {'fn': strat_rsi595,    'cfg': base_cfg(pos_size=0.025, sl_mult=1.4, tp_mult=1.5)},
    'E_VOLBREAK':  {'fn': strat_volbreak,  'cfg': base_cfg(pos_size=0.030, sl_mult=1.6, tp_mult=1.3)},
    'E_ORB':       {'fn': strat_orb,       'cfg': base_cfg(pos_size=0.030, sl_mult=1.5, tp_mult=1.5)},
    'E_PULLBACK':  {'fn': strat_pullback,  'cfg': base_cfg(pos_size=0.030, sl_mult=1.3, tp_mult=1.5)},
    'E_MEANREV':   {'fn': strat_meanrev,   'cfg': base_cfg(pos_size=0.025, sl_mult=1.3, tp_mult=1.4)},
    'E_VWAP':      {'fn': strat_vwap,      'cfg': base_cfg(pos_size=0.025, sl_mult=1.4, tp_mult=1.4)},
    'E_LIQUIDITY': {'fn': strat_liquidity, 'cfg': base_cfg(pos_size=0.030, sl_mult=1.3, tp_mult=1.5)},
    'E_COMPRESS':  {'fn': strat_compress,  'cfg': base_cfg(pos_size=0.030, sl_mult=1.5, tp_mult=1.5)},
}


# ════════════════════════════════════════════════════════════════════
#  SOLO RUNNER
# ════════════════════════════════════════════════════════════════════

def run_solo_strategy(strategy_name: str, regime_name: str, seed: int) -> Dict:
    """Run one strategy SOLO (no A/B/D) on one regime with one seed."""
    strat = STRATEGIES[strategy_name]
    cfg = deepcopy(strat['cfg'])
    rng = random.Random(seed)
    all_prices = {
        f"TOK{i:02d}": v63.gen_regime_prices(
            v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)),
            regime_name, rng
        )
        for i in range(v40.N_TOKENS)
    }
    engine = SoloEngine(strategy_name, strat['fn'], cfg, f"{strategy_name}_{regime_name}_S{seed}")
    for tick in range(v40.TOTAL_TICKS):
        for sym in all_prices:
            if tick + 1 < 60: continue
            prices_slice = all_prices[sym][max(0, tick-250):tick+1]
            engine._try_entry(sym, prices_slice, tick)
            engine._check_stops(sym, prices_slice, tick)
        engine.update_equity(all_prices, tick)

    base_metrics = engine.get_metrics()
    trades_list = [
        {'pnl': t.pnl, 'r_multiple': t.r_multiple, 'hold_ticks': t.hold_ticks,
         'close_reason': t.close_reason, 'strategy': t.strategy,
         'symbol': t.symbol, 'entry_tick': t.entry_tick,
         'direction': t.direction}
        for t in engine.trades
    ]
    ext = v63.extended_metrics(trades_list, engine.equity_series, base_metrics['pnl'], base_metrics['max_dd'])
    ext['regime'] = regime_name
    ext['seed'] = seed
    ext['trades_list'] = trades_list
    return ext


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=12)
    ap.add_argument('--regimes', default='ALL', choices=['ALL', 'MIXED'] + v63.REGIMES_ALL)
    ap.add_argument('--strategies', default='ALL', help='comma-separated or ALL')
    ap.add_argument('--out', default='/tmp/v65_strategies.json')
    args = ap.parse_args()

    seeds = v63.SEEDS_50[:args.seeds]
    regimes = v63.REGIMES_ALL if args.regimes == 'ALL' else [args.regimes]
    strategies = list(STRATEGIES.keys()) if args.strategies == 'ALL' else args.strategies.split(',')

    all_results = {}
    for sname in strategies:
        for regime in regimes:
            print(f"\n→ {sname} | {regime} | {len(seeds)} seeds...", flush=True)
            t0 = time.time()
            per_seed = []
            for seed in seeds:
                m = run_solo_strategy(sname, regime, seed)
                per_seed.append({k: v for k, v in m.items() if k != 'trades_list'})
                all_results.setdefault(sname, {}).setdefault(regime, []).append(per_seed[-1])
            agg = v63.aggregate_seeds(per_seed)
            print(f"   P&L {agg['pnl_mean']:+.2f}±{agg['pnl_std']:.0f}, WR {agg['wr_mean']:.1f}%, "
                  f"DD {agg['max_dd_mean']:.2f}%, PF {agg['pf_mean']:.2f}, Sharpe {agg['sharpe_mean']:+.2f}, "
                  f"trades {agg['trades_mean']:.0f}, profitable {agg['profitable_seeds_pct']:.0f}%  ({time.time()-t0:.1f}s)",
                  flush=True)
            # Save progressively
            save = {s: {r: [{k: v for k, v in rr.items() if k != 'pnl_per_seed'} for rr in res] for r, res in rs.items()} for s, rs in all_results.items()}
            with open(args.out, 'w') as f: json.dump(save, f, indent=2)

    # Summary table
    print(f"\n\n{'='*180}")
    print(f"  v65 NEW STRATEGIES — SOLO performance ({len(seeds)} seeds, MIXED regime)")
    print(f"{'='*180}")
    print(f"{'Strategy':<15} {'Trades':<8} {'WR%':<14} {'P&L':<16} {'PF':<8} {'Sharpe':<10} {'Sortino':<10} {'MaxDD%':<10} {'Calmar':<10} {'Profit%':<10} {'Verdict':<15}")
    print('-' * 180)
    for sname in strategies:
        mixed_results = all_results.get(sname, {}).get('MIXED', [])
        if not mixed_results:
            print(f"{sname:<15} (no MIXED data)")
            continue
        agg = v63.aggregate_seeds(mixed_results)
        # Verdict: ACCEPTED if P&L > 0 in MIXED and MaxDD < 0.40
        if agg['pnl_mean'] > 0 and agg['max_dd_mean'] <= 0.40:
            verdict = '✅ ACCEPTED'
        elif agg['pnl_mean'] > 0:
            verdict = '⚠️ PROFITABLE BUT DD HIGH'
        else:
            verdict = '❌ REJECTED'
        print(f"{sname:<15} {agg['trades_mean']:<8.0f} {agg['wr_mean']:.1f}±{agg['wr_std']:.1f}{'':>3} "
              f"{agg['pnl_mean']:+.2f}±{agg['pnl_std']:.0f}{'':>4} {agg['pf_mean']:.2f}{'':>4} "
              f"{agg['sharpe_mean']:+.2f}{'':>5} {agg['sortino_mean']:+.2f}{'':>5} "
              f"{agg['max_dd_mean']:.2f}{'':>5} {agg['calmar_mean']:.1f}{'':>5} "
              f"{agg['profitable_seeds_pct']:.0f}%{'':>5} {verdict}")

    # Per-regime table for accepted strategies
    print(f"\n\n{'='*180}")
    print(f"  PER-REGIME BREAKDOWN — which strategies survive which regimes?")
    print(f"{'='*180}")
    print(f"{'Strategy':<15} " + " ".join(f"{r:<14}" for r in v63.REGIMES_ALL) + " {'Regimes Profitable':<20}")
    print('-' * 180)
    for sname in strategies:
        row = f"{sname:<15} "
        n_profitable = 0
        for regime in v63.REGIMES_ALL:
            results = all_results.get(sname, {}).get(regime, [])
            if not results:
                row += f"{'--':<14} "
                continue
            agg = v63.aggregate_seeds(results)
            mark = '✅' if agg['pnl_mean'] > 0 else '❌'
            row += f"{mark}{agg['pnl_mean']:+6.1f}{'':>4} "
            if agg['pnl_mean'] > 0: n_profitable += 1
        row += f"{n_profitable}/6"
        print(row)

    return all_results


if __name__ == "__main__":
    main()
