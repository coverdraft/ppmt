#!/usr/bin/env python3
"""
v70 — ROBUST ENGINE ATTEMPT #1

User directive:
  "Piensa como un quantitative researcher de un hedge fund.
   No busques el mejor backtest. Busca la estrategia más difícil de romper."

GOAL: Build an engine that PASSES the acceptance gate:
  1. Composite score > 31 (v62a baseline)
  2. No regime collapse (P&L > -10 in EVERY regime)
  3. Profitable seeds % > 34% (v62a baseline)
  4. MaxDD ≤ 0.35% in EVERY regime
  5. P&L std ≤ v62a's std × 1.20

DESIGN PRINCIPLES (learned from v62a's failures):
  1. NO PYRAMIDING — pyramiding amplifies losses in BEAR/HIGHVOL
  2. REGIME-AWARE ENTRY — detect regime and only trade when conditions favor the strategy
  3. DYNAMIC ATR FLOOR — lower the floor in LOWVOL so we still trade (but with smaller size)
  4. STRICTER RISK PER TRADE — smaller position size (1.5% vs 5%)
  5. SHORTER HOLD TIME — 30 min max (vs 60 min) to reduce exposure
  6. MULTI-STRATEGY PORTFOLIO — combine uncorrelated strategies
  7. NO A↔B DUPLICATE — use only ONE of momentum OR mean reversion per signal

STRATEGY MIX:
  - Momentum (improved A): trend-following with regime filter
  - Mean Reversion (improved B): only in SIDE/LOWVOL regimes
  - Compression Breakout: from v65 (showed promise)
  - Pullback in Trend: from v65

REGIME DETECTION:
  - Use ATR(60) and SMA(50) slope to classify current regime
  - TRENDING_UP: SMA slope > 0.1% per 50 ticks
  - TRENDING_DOWN: SMA slope < -0.1% per 50 ticks
  - VOLATILE: ATR > 0.8% (storm)
  - CALM: ATR < 0.3% (dead)
  - NORMAL: everything else

  Each strategy only fires in its favored regime:
  - Momentum: TRENDING_UP or TRENDING_DOWN
  - Mean Reversion: CALM or NORMAL (not VOLATILE, not trending)
  - Compression Breakout: NORMAL or VOLATILE (after compression)
  - Pullback: TRENDING_UP or TRENDING_DOWN
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from collections import defaultdict

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
import v63_robustness as v63


# ════════════════════════════════════════════════════════════════════
#  INDICATORS
# ════════════════════════════════════════════════════════════════════

def sma(prices, period):
    if len(prices) < period: return None
    return sum(prices[-period:]) / period


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


def atr(prices, period=60):
    if len(prices) < 2: return 0
    start = max(1, len(prices) - period)
    diffs = [abs(prices[i] - prices[i-1]) for i in range(start, len(prices))]
    if not diffs: return 0
    return sum(diffs) / len(diffs)


def sma_slope(prices, period=50):
    """Slope of SMA over last `period` ticks, as % per tick."""
    if len(prices) < period * 2: return 0
    sma_now = sma(prices, period)
    sma_past = sma(prices[:-period], period) if len(prices) > period * 2 else sma_now
    if sma_past is None or sma_past == 0: return 0
    return (sma_now - sma_past) / sma_past / period * 100  # % per tick


def bollinger_width(prices, period=50, mult=2):
    if len(prices) < period: return 0
    slice_ = prices[-period:]
    mean = sum(slice_) / len(slice_)
    var = sum((p - mean) ** 2 for p in slice_) / len(slice_)
    std = var ** 0.5
    return (mult * 2 * std) / mean if mean else 0


def detect_regime(prices, lookback=100):
    """Classify current market regime.
    Returns one of: TRENDING_UP, TRENDING_DOWN, VOLATILE, CALM, NORMAL
    """
    if len(prices) < lookback: return 'NORMAL'
    atr_val = atr(prices, 60)
    atr_pct = atr_val / prices[-1] * 100
    slope = sma_slope(prices, 50)

    # Volatile: ATR > 0.7% (storm — avoid)
    if atr_pct > 0.7:
        return 'VOLATILE'
    # Trending: SMA slope > 0.15% per tick (STRONG trend only)
    if slope > 0.15:
        return 'TRENDING_UP'
    if slope < -0.15:
        return 'TRENDING_DOWN'
    # Calm: ATR < 0.25%
    if atr_pct < 0.25:
        return 'CALM'
    return 'NORMAL'


# ════════════════════════════════════════════════════════════════════
#  ROBUST ENGINE
# ════════════════════════════════════════════════════════════════════

@dataclass
class RobustPosition:
    symbol: str; direction: str; strategy: str
    entry_price: float; qty: float; size_usdt: float
    current_sl: Optional[float] = None
    current_tp: Optional[float] = None
    entry_tick: int = 0
    initial_atr: float = 0.0
    initial_sl_distance: float = 0.0
    max_favorable_price: float = 0.0
    trail_active: bool = False
    lock_done: bool = False
    partial1_done: bool = False
    partial2_done: bool = False
    entry_regime: str = 'NORMAL'


@dataclass
class RobustTrade:
    symbol: str; direction: str; strategy: str
    entry_price: float; exit_price: float; size_usdt: float
    pnl: float; close_reason: str; hold_ticks: int
    r_multiple: float = 0.0
    entry_tick: int = 0
    entry_regime: str = 'NORMAL'


class RobustEngine:
    """v70 Robust Engine — regime-aware, multi-strategy, no pyramiding."""

    def __init__(self, config: Dict, name: str, capital=12000):
        self.config = config  # global config
        self.name = name
        self.capital = capital
        self.initial_capital = capital
        self.positions: Dict[str, RobustPosition] = {}  # symbol → position
        self.trades: List[RobustTrade] = []
        self.equity_series: List[float] = []
        self.cooldown_until: Dict[str, int] = {}
        self.equity_peak = capital
        self.max_dd = 0.0
        self.regime_counts = defaultdict(int)

    def _try_strategies(self, sym, prices, tick):
        """Try all enabled strategies on this symbol."""
        if sym in self.positions: return  # already in position
        if sym in self.cooldown_until and tick < self.cooldown_until[sym]: return
        if len(prices) < 100: return

        regime = detect_regime(prices)
        self.regime_counts[regime] += 1
        atr_val = max(atr(prices, 60), prices[-1] * 0.003)
        atr_pct = atr_val / prices[-1] * 100

        # DYNAMIC ATR FLOOR — lower in calm markets, higher in volatile
        # This allows trading in LOWVOL (was blocked in v62a)
        atr_floor = self.config.get('atr_floor_pct', 0.20)  # default 0.20% (was 0.58%)
        if atr_pct < atr_floor: return

        # Try each strategy in priority order
        for strat_name in ['MOMENTUM', 'MEANREV', 'COMPRESS', 'PULLBACK']:
            if not self.config.get(f'{strat_name}_enabled', True): continue
            direction = self._check_entry(strat_name, prices, atr_val, regime)
            if direction in ('LONG', 'SHORT'):
                self._open_position(sym, direction, strat_name, prices, tick, atr_val, regime)
                return  # one position per symbol

    def _check_entry(self, strat_name: str, prices: List[float], atr_val: float, regime: str) -> Optional[str]:
        """Check entry conditions for a strategy. Returns 'LONG', 'SHORT', or None."""
        price = prices[-1]

        if strat_name == 'MOMENTUM':
            # Only trade in STRONG trending regimes
            if regime not in ('TRENDING_UP', 'TRENDING_DOWN'): return None
            r = rsi(prices, 14)
            # Stricter RSI band — only enter with momentum confirmation
            if regime == 'TRENDING_UP' and 60 < r < 80: return 'LONG'
            if regime == 'TRENDING_DOWN' and 20 < r < 40: return 'SHORT'

        elif strat_name == 'MEANREV':
            # Only trade in calm/normal regimes (avoid volatile and trending)
            if regime not in ('CALM', 'NORMAL'): return None
            r = rsi(prices, 14)
            # Stricter — only extreme RSI
            if r < 20: return 'LONG'
            if r > 80: return 'SHORT'

        elif strat_name == 'COMPRESS':
            # Only trade in NORMAL (not VOLATILE — too risky)
            if regime != 'NORMAL': return None
            current_width = bollinger_width(prices, 50)
            # Find min width in last 50 ticks
            min_width = float('inf')
            for i in range(max(50, len(prices) - 50), len(prices) - 5):
                w = bollinger_width(prices[:i+1], 50)
                if w > 0 and w < min_width: min_width = w
            if min_width == float('inf'): return None
            # Stricter expansion threshold
            if current_width < min_width * 1.8: return None
            # Direction: break of last 20-tick range
            slice_ = prices[-20:]
            lo, hi = min(slice_), max(slice_)
            if price > hi * 0.999 and prices[-1] > prices[-2]: return 'LONG'
            if price < lo * 1.001 and prices[-1] < prices[-2]: return 'SHORT'

        elif strat_name == 'PULLBACK':
            # Only trade in STRONG trending regimes
            if regime not in ('TRENDING_UP', 'TRENDING_DOWN'): return None
            sma20 = sma(prices, 20)
            sma50 = sma(prices, 50)
            if sma20 is None or sma50 is None: return None
            # Stricter pullback — price must be within 0.3 ATR of SMA20
            if regime == 'TRENDING_UP':
                if abs(price - sma20) < atr_val * 0.3 and price > sma50: return 'LONG'
            if regime == 'TRENDING_DOWN':
                if abs(price - sma20) < atr_val * 0.3 and price < sma50: return 'SHORT'

        return None

    def _open_position(self, sym, direction, strategy, prices, tick, atr_val, regime):
        """Open a position with regime-aware sizing."""
        price = prices[-1]
        atr_pct = atr_val / price * 100

        # REGIME-AWARE POSITION SIZING
        # Smaller in volatile, normal in trending, smaller in calm
        base_size = self.config.get(f'{strategy}_size', 0.015)  # 1.5% default
        if regime == 'VOLATILE':
            size_mult = 0.4  # 40% size in volatile
        elif regime == 'CALM':
            size_mult = 0.5  # 50% size in calm
        elif regime in ('TRENDING_UP', 'TRENDING_DOWN'):
            size_mult = 1.0  # full size in trending
        else:  # NORMAL
            size_mult = 0.7

        sl_mult = self.config.get(f'{strategy}_sl_mult', 1.4)
        tp_mult = self.config.get(f'{strategy}_tp_mult', 1.5)

        sl_distance = atr_val * sl_mult
        if direction == 'LONG':
            sl = price - sl_distance
            tp = price + atr_val * tp_mult
        else:
            sl = price + sl_distance
            tp = price - atr_val * tp_mult

        size_usdt = self.capital * base_size * size_mult
        qty = size_usdt / price

        self.positions[sym] = RobustPosition(
            symbol=sym, direction=direction, strategy=strategy,
            entry_price=price, qty=qty, size_usdt=size_usdt,
            current_sl=sl, current_tp=tp, entry_tick=tick,
            initial_atr=atr_val, initial_sl_distance=sl_distance,
            max_favorable_price=price, entry_regime=regime,
        )

    def _check_stops(self, sym, prices, tick):
        if sym not in self.positions: return
        pos = self.positions[sym]
        price = prices[-1]
        is_long = pos.direction == 'LONG'
        if is_long:
            if price > pos.max_favorable_price: pos.max_favorable_price = price
        else:
            if price < pos.max_favorable_price: pos.max_favorable_price = price

        r_dist = (price - pos.entry_price) / pos.initial_sl_distance if is_long else (pos.entry_price - price) / pos.initial_sl_distance

        # LOCK at +0.5R → SL to entry + 0.2R
        if not pos.lock_done and r_dist >= 0.5:
            lock_r = 0.2
            if is_long:
                new_sl = pos.entry_price + lock_r * pos.initial_sl_distance
                if new_sl > pos.current_sl: pos.current_sl = new_sl
            else:
                new_sl = pos.entry_price - lock_r * pos.initial_sl_distance
                if new_sl < pos.current_sl: pos.current_sl = new_sl
            pos.lock_done = True

        # PARTIAL TP1 at +0.5R → close 15%
        if not pos.partial1_done and r_dist >= 0.5:
            close_qty = pos.qty * 0.15
            if close_qty > 0.0001:
                self._partial_close(sym, price, 'PARTIAL1', tick, close_qty)
            pos.partial1_done = True

        # PARTIAL TP2 at +1.0R → close 25%, activate trailing
        if not pos.partial2_done and r_dist >= 1.0:
            close_qty = pos.qty * 0.25
            if close_qty > 0.0001:
                self._partial_close(sym, price, 'PARTIAL2', tick, close_qty)
            pos.partial2_done = True
            pos.trail_active = True

        # Trailing stop (0.30 ATR)
        trail_atr = self.config.get('trail_atr', 0.30)
        if pos.trail_active:
            trail_dist = pos.initial_atr * trail_atr
            if is_long:
                new_sl = pos.max_favorable_price - trail_dist
                if new_sl > pos.current_sl: pos.current_sl = new_sl
                pos.current_tp = None
            else:
                new_sl = pos.max_favorable_price + trail_dist
                if new_sl < pos.current_sl: pos.current_sl = new_sl
                pos.current_tp = None

        # Time stop (SHORTER — 30 min = 1200 ticks)
        time_stop = self.config.get('time_stop', 1200)
        if tick - pos.entry_tick > time_stop:
            self._close_position(sym, price, 'TIME', tick)
            return

        # SL / TP
        if is_long and price <= pos.current_sl:
            self._close_position(sym, price, 'SL', tick); return
        if not is_long and price >= pos.current_sl:
            self._close_position(sym, price, 'SL', tick); return
        if pos.current_tp is not None:
            if is_long and price >= pos.current_tp:
                self._close_position(sym, price, 'TP', tick); return
            if not is_long and price <= pos.current_tp:
                self._close_position(sym, price, 'TP', tick); return

    def _partial_close(self, sym, price, reason, tick, close_qty):
        pos = self.positions[sym]
        if pos is None or close_qty <= 0: return
        fee_pct = v40.FEE_PCT + v40.SLIPPAGE_PCT
        if pos.direction == 'LONG':
            gross = (price - pos.entry_price) * close_qty
        else:
            gross = (pos.entry_price - price) * close_qty
        partial_size = close_qty * pos.entry_price
        fees = partial_size * fee_pct / 100 * 2
        pnl = gross - fees
        pos.qty -= close_qty
        r_mult = ((price - pos.entry_price) / pos.initial_sl_distance) if pos.direction == 'LONG' else ((pos.entry_price - price) / pos.initial_sl_distance)
        self.trades.append(RobustTrade(
            symbol=sym, direction=pos.direction, strategy=pos.strategy,
            entry_price=pos.entry_price, exit_price=price, size_usdt=partial_size,
            pnl=pnl, close_reason=reason, hold_ticks=tick - pos.entry_tick,
            r_multiple=r_mult, entry_tick=pos.entry_tick, entry_regime=pos.entry_regime,
        ))
        self.capital += pnl

    def _close_position(self, sym, price, reason, tick):
        pos = self.positions.get(sym)
        if pos is None: return
        fee_pct = v40.FEE_PCT + v40.SLIPPAGE_PCT
        if pos.direction == 'LONG':
            gross = (price - pos.entry_price) * pos.qty
        else:
            gross = (pos.entry_price - price) * pos.qty
        fees = pos.size_usdt * fee_pct / 100 * 2
        pnl = gross - fees
        r_mult = ((price - pos.entry_price) / pos.initial_sl_distance) if pos.direction == 'LONG' else ((pos.entry_price - price) / pos.initial_sl_distance)
        self.trades.append(RobustTrade(
            symbol=sym, direction=pos.direction, strategy=pos.strategy,
            entry_price=pos.entry_price, exit_price=price, size_usdt=pos.size_usdt,
            pnl=pnl, close_reason=reason, hold_ticks=tick - pos.entry_tick,
            r_multiple=r_mult, entry_tick=pos.entry_tick, entry_regime=pos.entry_regime,
        ))
        self.capital += pnl
        cooldown_min = self.config.get('cooldown_min', 45)
        self.cooldown_until[sym] = tick + int(cooldown_min * 60 / v40.TICK_SECONDS)
        del self.positions[sym]

    def update_equity(self, all_prices, tick):
        equity = self.capital
        for sym, pos in self.positions.items():
            if sym in all_prices:
                price = all_prices[sym][-1]
                if pos.direction == 'LONG':
                    equity += (price - pos.entry_price) * pos.qty
                else:
                    equity += (pos.entry_price - price) * pos.qty
        self.equity_series.append(equity)
        if equity > self.equity_peak: self.equity_peak = equity
        dd = (self.equity_peak - equity) / self.equity_peak * 100 if self.equity_peak > 0 else 0
        if dd > self.max_dd: self.max_dd = dd

    def get_metrics(self):
        trades = self.trades
        n = len(trades)
        n_wins = sum(1 for t in trades if t.pnl > 0)
        wr = n_wins / n * 100 if n else 0
        pnl = sum(t.pnl for t in trades)
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
        pf = gross_profit / gross_loss if gross_loss > 0 else 0
        avg_r = statistics.mean([t.r_multiple for t in trades]) if trades else 0
        pnls = [t.pnl for t in trades]
        if len(pnls) > 1:
            mean_p = statistics.mean(pnls); std_p = statistics.stdev(pnls)
            sharpe = mean_p / std_p * math.sqrt(n) if std_p > 0 else 0
        else:
            sharpe = 0
        max_cl = 0; cur = 0
        for t in trades:
            if t.pnl < 0: cur += 1; max_cl = max(max_cl, cur)
            else: cur = 0
        # Per-strategy breakdown
        per_strat = defaultdict(lambda: {'trades': 0, 'pnl': 0, 'wins': 0})
        for t in trades:
            per_strat[t.strategy]['trades'] += 1
            per_strat[t.strategy]['pnl'] += t.pnl
            if t.pnl > 0: per_strat[t.strategy]['wins'] += 1
        return {
            'trades': n, 'wr': wr, 'pnl': pnl, 'pf': pf, 'sharpe': sharpe,
            'max_dd': self.max_dd, 'avg_r': avg_r, 'max_consec_loss': max_cl,
            'consistency': 0, 'per_strat': dict(per_strat),
            'regime_counts': dict(self.regime_counts),
        }


# ════════════════════════════════════════════════════════════════════
#  v70 CONFIG
# ════════════════════════════════════════════════════════════════════

def v70_config():
    """v70 robust engine config — regime-aware, no pyramiding, smaller sizes."""
    return {
        # Global
        'atr_floor_pct': 0.20,  # lower floor (was 0.58 in v62a) — allows LOWVOL trades
        'trail_atr': 0.30,
        'cooldown_min': 45,
        'time_stop': 1200,  # 30 min (was 60 min in v62a) — shorter exposure
        # Strategy enables
        'MOMENTUM_enabled': True,
        'MEANREV_enabled': True,
        'COMPRESS_enabled': True,
        'PULLBACK_enabled': True,
        # Strategy sizes (SMALLER than v62a — was 5%/20%)
        'MOMENTUM_size': 0.020,    # 2% per trade
        'MEANREV_size': 0.015,     # 1.5% per trade
        'COMPRESS_size': 0.020,    # 2% per trade
        'PULLBACK_size': 0.020,    # 2% per trade
        # Strategy SL/TP
        'MOMENTUM_sl_mult': 1.4,
        'MOMENTUM_tp_mult': 1.5,
        'MEANREV_sl_mult': 1.3,
        'MEANREV_tp_mult': 1.4,
        'COMPRESS_sl_mult': 1.5,
        'COMPRESS_tp_mult': 1.5,
        'PULLBACK_sl_mult': 1.3,
        'PULLBACK_tp_mult': 1.5,
    }


# ════════════════════════════════════════════════════════════════════
#  RUNNER
# ════════════════════════════════════════════════════════════════════

def run_v70_regime(cfg, regime_name, seed):
    """Run v70 on one regime with one seed."""
    rng = random.Random(seed)
    all_prices = {
        f"TOK{i:02d}": v63.gen_regime_prices(
            v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)),
            regime_name, rng
        )
        for i in range(v40.N_TOKENS)
    }
    engine = RobustEngine(deepcopy(cfg), f"v70_{regime_name}_S{seed}")
    for tick in range(v40.TOTAL_TICKS):
        for sym in all_prices:
            if tick + 1 < 60: continue
            prices_slice = all_prices[sym][max(0, tick-250):tick+1]
            engine._try_strategies(sym, prices_slice, tick)
            engine._check_stops(sym, prices_slice, tick)
        engine.update_equity(all_prices, tick)

    base = engine.get_metrics()
    trades = [
        {'pnl': t.pnl, 'r_multiple': t.r_multiple, 'hold_ticks': t.hold_ticks,
         'close_reason': t.close_reason, 'strategy': t.strategy,
         'entry_regime': t.entry_regime}
        for t in engine.trades
    ]
    ext = v63.extended_metrics(trades, engine.equity_series, base['pnl'], base['max_dd'])
    ext['regime'] = regime_name
    ext['seed'] = seed
    ext['per_strat'] = base['per_strat']
    ext['regime_counts'] = base['regime_counts']
    return ext


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=12)
    ap.add_argument('--regimes', default='ALL')
    ap.add_argument('--out', default='/tmp/v70_results.json')
    args = ap.parse_args()

    seeds = v63.SEEDS_50[:args.seeds]
    regimes = v63.REGIMES_ALL if args.regimes == 'ALL' else args.regimes.split(',')
    cfg = v70_config()

    print(f"\n{'='*120}")
    print(f"  v70 ROBUST ENGINE — {len(regimes)} regimes × {len(seeds)} seeds")
    print(f"  Design: regime-aware, no pyramiding, smaller sizes, shorter holds")
    print(f"{'='*120}\n")

    all_results = {}
    for regime in regimes:
        print(f"\n→ {regime}...", flush=True)
        t0 = time.time()
        per_seed = []
        for seed in seeds:
            m = run_v70_regime(cfg, regime, seed)
            per_seed.append(m)
            all_results.setdefault(regime, []).append(m)
        agg = v63.aggregate_seeds(per_seed)
        print(f"   P&L {agg['pnl_mean']:+.2f}±{agg['pnl_std']:.0f}, WR {agg['wr_mean']:.1f}%, "
              f"DD {agg['max_dd_mean']:.2f}%, PF {agg['pf_mean']:.2f}, Sharpe {agg['sharpe_mean']:+.2f}, "
              f"trades {agg['trades_mean']:.0f}, profitable {agg['profitable_seeds_pct']:.0f}%  ({time.time()-t0:.1f}s)",
              flush=True)
        # Save progressively
        save = {r: [{k: v for k, v in m.items() if k not in ('equity_curve', 'trades_list')} for m in results]
                for r, results in all_results.items()}
        with open(args.out, 'w') as f: json.dump(save, f, indent=2, default=v63._json_default)

    # Summary
    print(f"\n\n{'='*180}")
    print(f"  v70 ROBUST ENGINE — multi-regime performance ({len(seeds)} seeds)")
    print(f"{'='*180}")
    all_agg = {}
    for regime in regimes:
        results = all_results.get(regime, [])
        if results:
            all_agg[regime] = v63.aggregate_seeds(results)

    v63.print_regime_table("v70 (robust engine)", all_agg)

    # Acceptance gate vs v62a
    v62a_composite = 31.04  # measured baseline
    v70_mixed = all_agg.get('MIXED', {})
    if v70_mixed:
        v70_comp = v63.composite_for_aggregate(v70_mixed, all_agg)
        print(f"\n  COMPOSITE COMPARISON:")
        print(f"    v62a baseline: {v62a_composite:.2f}/100")
        print(f"    v70:           {v70_comp:.2f}/100")
        print(f"    Delta:         {v70_comp - v62a_composite:+.2f}")

        # Check acceptance criteria
        print(f"\n  ACCEPTANCE GATE:")
        c1 = v70_comp > v62a_composite
        c2 = all(r.get('pnl_mean', -999) > -10 for r in all_agg.values())
        c3 = v70_mixed.get('profitable_seeds_pct', 0) > 34
        c4 = all(r.get('max_dd_mean', 999) <= 0.35 for r in all_agg.values())
        v62a_std = 71  # from v62a analysis
        c5 = v70_mixed.get('pnl_std', 999) <= v62a_std * 1.20
        print(f"    1. Composite > v62a ({v62a_composite:.1f}): {'✅' if c1 else '❌'} ({v70_comp:.1f})")
        print(f"    2. No regime collapse (P&L > -10): {'✅' if c2 else '❌'}")
        fails = [r for r, a in all_agg.items() if a.get('pnl_mean', -999) <= -10]
        if fails: print(f"       Fails: {fails}")
        print(f"    3. Profitable seeds > 34%: {'✅' if c3 else '❌'} ({v70_mixed.get('profitable_seeds_pct', 0):.0f}%)")
        print(f"    4. MaxDD ≤0.35% in all regimes: {'✅' if c4 else '❌'}")
        dd_fails = [(r, a['max_dd_mean']) for r, a in all_agg.items() if a.get('max_dd_mean', 0) > 0.35]
        if dd_fails: print(f"       Fails: {dd_fails}")
        print(f"    5. P&L std ≤ {v62a_std*1.20:.0f}: {'✅' if c5 else '❌'} ({v70_mixed.get('pnl_std', 0):.0f})")

        verdict = 'ACCEPTED ✅' if all([c1, c2, c3, c4, c5]) else 'REJECTED ❌'
        print(f"\n  VERDICT: {verdict}")

    return all_agg


if __name__ == "__main__":
    main()
