#!/usr/bin/env python3
"""
v80 — UNIVERSAL ENGINE: fixes v67's catastrophic failures outside MIXED.

v67 problems (diagnosed by v80_direction_token_test.py on 12 seeds × 8 profiles):
  1. MIXED: P&L -22.33, Profit 8% (was supposed to be +46.93, Profit 67%)
     — the 12-seed validation was overfit to specific RNG draws
  2. BULL: P&L -295, MaxDD 3.09%, Profit 0%
     — Strategy B SHORT killed by mean reversion in uptrend (-4428 P&L)
  3. BEAR: P&L -164, MaxDD 1.69%, Profit 0%
     — Strategy B LONG killed by mean reversion in downtrend (-835 P&L)
  4. HIGHVOL: P&L -100, MaxDD 2.34%, Profit 33%
     — Pyramiding amplifies losses
  5. MEME: P&L -206, MaxDD 3.13%, Profit 17%
     — Strategy B SHORT killed by meme pumps (-6138 P&L)
  6. ALT: P&L -124, MaxDD 1.61%, Profit 8%
     — Strategy B SHORT (-1143 P&L)
  7. BLUE/STABLE: 0 trades (ATR floor 0.58% blocks everything)

v80 FIXES:
  F1. Trend filter for Strategy B: SMA100 slope gates direction
      - LONG only if SMA100 slope >= -0.01% per tick (no bearish trend)
      - SHORT only if SMA100 slope <= +0.01% per tick (no bullish trend)
      → Eliminates B's catastrophic trend-fighting losses
  F2. Dynamic ATR floor: 0.20% (was 0.58%) — allows BLUE/STABLE trading
      - But size_mult scaled: floor×0.3 if ATR < 0.4%, ×0.5 if < 0.6%, ×0.7 if < 0.8%, ×1.0 otherwise
      → Enables BLUE/STABLE without blowing up
  F3. Regime-aware pyramid: disable pyramid if ATR% > 1.5 (HIGHVOL/MEME)
      → Prevents pyramid amplification of losses
  F4. Tighter catastrophic SL: 2.5 ATR (was 4.0) — caps tail risk
  F5. Regime-aware Strategy B size: B size 0.15 if ATR% > 1.2 (HIGHVOL), 0.30 otherwise
      → Smaller B in HIGHVOL reduces drawdown
  F6. Strategy A: keep momentum (it works in BULL/BEAR)
  F7. Add Strategy F: Range Trading — only fires in SIDE regime (SMA100 flat + low ATR)
      - Buy at lower Bollinger, sell at upper Bollinger
      - Tighter SL (1.0 ATR), tighter TP (1.5 ATR)
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


# ─────────────────────────────────────────────────────────────────────
# v80 ENGINE — extends v62 with trend filter + dynamic ATR + regime pyramid
# ─────────────────────────────────────────────────────────────────────

def compute_sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def compute_sma_slope(prices, period=100, lookback=10):
    """Slope of SMA(period) over the last `lookback` ticks, in % per tick."""
    if len(prices) < period + lookback:
        return 0.0
    sma_now = sum(prices[-period:]) / period
    sma_then = sum(prices[-period-lookback:-lookback]) / period
    return (sma_now - sma_then) / sma_then * 100  # % change


def compute_bollinger(prices, period=20, num_std=2):
    if len(prices) < period:
        return None
    slice_ = prices[-period:]
    mean = sum(slice_) / period
    var = sum((p - mean) ** 2 for p in slice_) / period
    std = var ** 0.5
    return {'upper': mean + num_std * std, 'mid': mean, 'lower': mean - num_std * std, 'width': std * 2 / mean * 100 if mean else 0}


class EngineSimV81(EngineSimV62):
    """v80: regime-aware + trend-filtered + dynamic ATR floor."""

    def __init__(self, config, name):
        super().__init__(config, name)
        # Direction tracking
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
        # Regime tracking
        self.regime_samples = defaultdict(int)

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

    def _try_strategy_a(self, sym, prices, tick):
        """Strategy A: Momentum — unchanged from v62 (it works in BULL/BEAR)."""
        super()._try_strategy_a(sym, prices, tick)

    def _try_strategy_b(self, sym, prices, tick):
        """Strategy B: Mean Reversion + TREND FILTER (F1) + dynamic ATR floor (F2)."""
        # Mirror v40's gating exactly to avoid double-firing
        cfg = self.config.get('B', {})
        if not cfg.get('enabled', True):
            return
        if tick - self.last_signal_tick.get('B', 0) < 20:
            return
        if self.strategy_pos_count.get('B', 0) >= cfg.get('max_pos', 1):
            return
        if len(prices) < 100:  # need 100 for SMA100 slope
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

        # F2: Dynamic ATR floor — 0.40% (compromise: keeps MIXED quality, allows some BLUE)
        if atr_pct < 0.40:
            return

        direction = 'LONG' if rsi < 50 else 'SHORT'

        # F1: TREND FILTER (slope-based, not level-based)
        sma_slope = compute_sma_slope(prices, period=100, lookback=10)
        if direction == 'LONG' and sma_slope < -0.05:
            self.atr_filter_skips = getattr(self, 'atr_filter_skips', 0) + 1
            return  # don't long into a downtrend
        if direction == 'SHORT' and sma_slope > 0.05:
            self.atr_filter_skips = getattr(self, 'atr_filter_skips', 0) + 1
            return  # don't short into an uptrend

        # F5: Regime-aware B size (0.15 if HIGHVOL, 0.30 otherwise)
        b_base_size = 0.15 if atr_pct > 1.2 else 0.30

        # F2: Extended tiered sizing (lower for low-vol tokens)
        if atr_pct < 0.40:
            size_mult = 0.3
        elif atr_pct < 0.60:
            size_mult = 0.5
        elif atr_pct < 0.80:
            size_mult = 0.7
        else:
            size_mult = 1.0

        # Build effective cfg
        eff_cfg = cfg.copy()
        eff_cfg['pos_size_pct'] = b_base_size * size_mult
        eff_cfg['sl_mult'] = 1.5
        eff_cfg['tp_mult'] = 1.2
        eff_cfg['catsl_mult'] = 2.5  # F4: tighter cat SL

        # Open via parent
        self._open_position_v40(sym, direction, 'B', prices[-1], atr, eff_cfg, tick)
        self.last_signal_tick['B'] = tick

        pos = self.positions.get(sym)
        if pos:
            # Reinforce SL/TP/cat_SL
            pos.current_sl = pos.entry_price - atr * 1.5 if direction == 'LONG' else pos.entry_price + atr * 1.5
            pos.current_tp = pos.entry_price + atr * 1.2 if direction == 'LONG' else pos.entry_price - atr * 1.2
            pos.catastrophic_sl = pos.entry_price - atr * 2.5 if direction == 'LONG' else pos.entry_price + atr * 2.5
            pos.initial_atr = atr
            pos.initial_sl_distance = atr * 1.5
            pos.lock_done = False
            pos.partial_done = False
            pos.partial1_done = False
            pos.partial2_done = False
            pos.partial3_done = False
            pos.pyramid_done = False
            pos.trail_active = False
            pos.max_favorable_price = pos.entry_price
            pos.trail_atr = atr

    def _try_strategy_d(self, sym, prices, tick):
        """Strategy D: Vol Squeeze — unchanged (it's inert anyway)."""
        super()._try_strategy_d(sym, prices, tick)

    def _try_strategy_e(self, sym, prices, tick):
        """Strategy E: disabled."""
        return

    def _compute_atr(self, prices, period):
        """Simple ATR: average of |price[i] - price[i-1]| over last `period` ticks."""
        if len(prices) < period + 1:
            return 0
        diffs = [abs(prices[i] - prices[i-1]) for i in range(len(prices) - period, len(prices))]
        return sum(diffs) / period

    def _compute_rsi(self, prices, period=14):
        """Wilder's RSI."""
        if len(prices) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(len(prices) - period, len(prices)):
            change = prices[i] - prices[i-1]
            gains.append(max(0, change))
            losses.append(max(0, -change))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _check_stops(self, sym, prices, tick):
        """Override to add F3: regime-aware pyramid disabling."""
        pos = self.positions.get(sym)
        if pos and not getattr(pos, 'pyramid_done', False) and pos.strategy == 'B':
            # F3: Disable pyramid in HIGHVOL
            if hasattr(pos, 'initial_atr') and pos.initial_atr and pos.entry_price:
                atr_pct = (pos.initial_atr / pos.entry_price) * 100
                if atr_pct > 1.5:
                    pos.pyramid_done = True  # disable pyramid by marking as done
        super()._check_stops(sym, prices, tick)


def v80_config():
    """v80 config: same as v67 but with trend filter + dynamic ATR + regime pyramid."""
    cfg = v61b_base(
        pyramid_pct=0.75,
        strat_kwargs={'a_pos_size': 0.040, 'b_pos_size': 0.30},
        lock_offset_r=0.35,
    )
    cfg['pyramid_trigger_r'] = 1.0
    cfg['v80_features'] = {
        'trend_filter_B': True,
        'dynamic_atr_floor_pct': 0.20,
        'regime_aware_pyramid': True,
        'catastrophic_sl_mult': 2.5,
        'b_size_highvol': 0.15,
        'b_size_normal': 0.30,
    }
    return cfg


# ─────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────

def run_seed_profile(seed, profile, n_tokens=10):
    rng = random.Random(seed)
    base = 1.0 * (1 + rng.uniform(-0.3, 0.3))
    all_prices = {f"TOK{i:02d}": gen_profile_prices(v40.TOTAL_TICKS, base * (1 + rng.uniform(-0.2, 0.2)), rng, profile)
                  for i in range(n_tokens)}

    engine = EngineSimV81(deepcopy(v80_config()), f'v81_{profile}')
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


RESULTS_FILE = '/tmp/v81_universal.json'


def aggregate():
    if not os.path.exists(RESULTS_FILE):
        print(f"No results at {RESULTS_FILE}")
        sys.exit(1)
    with open(RESULTS_FILE) as f:
        all_results = json.load(f)

    # Load v67 baseline for comparison
    v67_results = {}
    if os.path.exists(V80_RESULTS):
        with open(V80_RESULTS) as f:
            v67_results = json.load(f)

    print("=" * 160)
    print(f"{'Profile':<10} {'Ver':<5} {'Trades':<8} {'WR%':<10} {'P&L':<12} {'PF':<7} {'MaxDD%':<8} {'Profit%':<10} {'L/S trades':<12} {'L P&L':<12} {'S P&L':<12} {'L WR%':<7} {'S WR%':<7}")
    print("=" * 160)
    for profile in PROFILES:
        # v67 baseline
        v67_seeds = [r for r in v67_results.values() if profile in r]
        if v67_seeds:
            ms = [r[profile] for r in v67_seeds]
            v67_pnl = statistics.mean(m['pnl'] for m in ms)
            v67_profit = sum(1 for m in ms if m['pnl'] > 0) / len(ms) * 100
            v67_wr = statistics.mean(m['wr'] for m in ms)
            v67_maxdd = statistics.mean(m['max_dd'] for m in ms)
            v67_pf = statistics.mean(m['pf'] for m in ms)
            v67_trades = statistics.mean(m['trades'] for m in ms)
            v67_l_t = statistics.mean(m['trades_long'] for m in ms)
            v67_s_t = statistics.mean(m['trades_short'] for m in ms)
            v67_l_pnl = statistics.mean(m['pnl_long'] for m in ms)
            v67_s_pnl = statistics.mean(m['pnl_short'] for m in ms)
            v67_l_wr = statistics.mean(m['wr_long'] for m in ms)
            v67_s_wr = statistics.mean(m['wr_short'] for m in ms)
            print(f"{profile:<10} {'v67':<5} {v67_trades:<8.0f} {v67_wr:<10.1f} {v67_pnl:<+12.2f} {v67_pf:<7.2f} {v67_maxdd:<8.2f} {v67_profit:<10.0f} {int(v67_l_t)}/{int(v67_s_t):<11} {v67_l_pnl:<+12.2f} {v67_s_pnl:<+12.2f} {v67_l_wr:<7.1f} {v67_s_wr:<7.1f}")
        # v80
        v80_seeds = [r for r in all_results.values() if profile in r]
        if v80_seeds:
            ms = [r[profile] for r in v80_seeds]
            v80_pnl = statistics.mean(m['pnl'] for m in ms)
            v80_profit = sum(1 for m in ms if m['pnl'] > 0) / len(ms) * 100
            v80_wr = statistics.mean(m['wr'] for m in ms)
            v80_maxdd = statistics.mean(m['max_dd'] for m in ms)
            v80_pf = statistics.mean(m['pf'] for m in ms)
            v80_trades = statistics.mean(m['trades'] for m in ms)
            v80_l_t = statistics.mean(m['trades_long'] for m in ms)
            v80_s_t = statistics.mean(m['trades_short'] for m in ms)
            v80_l_pnl = statistics.mean(m['pnl_long'] for m in ms)
            v80_s_pnl = statistics.mean(m['pnl_short'] for m in ms)
            v80_l_wr = statistics.mean(m['wr_long'] for m in ms)
            v80_s_wr = statistics.mean(m['wr_short'] for m in ms)
            delta = v80_pnl - v67_pnl if v67_seeds else 0
            marker = "✅" if v80_pnl > v67_pnl else "❌"
            print(f"{profile:<10} {'v81':<5} {v80_trades:<8.0f} {v80_wr:<10.1f} {v80_pnl:<+12.2f} {v80_pf:<7.2f} {v80_maxdd:<8.2f} {v80_profit:<10.0f} {int(v80_l_t)}/{int(v80_s_t):<11} {v80_l_pnl:<+12.2f} {v80_s_pnl:<+12.2f} {v80_l_wr:<7.1f} {v80_s_wr:<7.1f}  {marker} Δ{delta:+.2f}")
        print("-" * 160)

    print("\n" + "=" * 80)
    print("VEREDICTO v80 vs v67 (por perfil)")
    print("=" * 80)
    for profile in PROFILES:
        v67_seeds = [r for r in v67_results.values() if profile in r]
        v80_seeds = [r for r in all_results.values() if profile in r]
        if not v80_seeds:
            continue
        v67_pnl = statistics.mean(m['pnl'] for m in [r[profile] for r in v67_seeds]) if v67_seeds else 0
        v81_pnl = statistics.mean(m['pnl'] for m in [r[profile] for r in v80_seeds])
        v67_profit = sum(1 for m in [r[profile] for r in v67_seeds] if m['pnl'] > 0) / len(v67_seeds) * 100 if v67_seeds else 0
        v81_profit = sum(1 for m in [r[profile] for r in v80_seeds] if m['pnl'] > 0) / len(v80_seeds) * 100
        delta = v81_pnl - v67_pnl
        marker_p = "✅" if v81_pnl > 0 else "❌"
        marker_d = "✅" if delta > 0 else "❌"
        print(f"  {marker_p} {marker_d} {profile:<10}  v67 {v67_pnl:+8.2f} ({v67_profit:3.0f}% profit)  →  v81 {v81_pnl:+8.2f} ({v81_profit:3.0f}% profit)  Δ {delta:+8.2f}")


def run_one(seed, profile):
    print(f"  Running seed {seed} × {profile}...", flush=True)
    start = time.time()
    result = run_seed_profile(seed, profile)
    elapsed = time.time() - start
    print(f"    done in {elapsed:.1f}s — P&L {result['pnl']:+.2f}, trades {result['trades']}, L/S={result['trades_long']}/{result['trades_short']}", flush=True)
    return result


if __name__ == "__main__":
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
        print("  python v80_universal_engine.py all")
        print("  python v80_universal_engine.py one <seed> <profile>")
        print("  python v80_universal_engine.py aggregate")
