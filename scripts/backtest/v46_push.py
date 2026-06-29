#!/usr/bin/env python3
"""
v46 — Push profitability stability from 67% to 75%+ of seeds

v43a champion (12 seeds): WR 72.5%, P&L +13.92, Profit 67%, AvgR +0.61, MaxDD 0.29%, PF 1.54
PROBLEM: 4 of 12 seeds lose money (314, 1234, 2025, 99)

NEW IDEAS to stabilize profitability:
1. v46a — Max concurrent positions = 3 (was unlimited ~6) — reduce correlated risk
2. v46b — Daily loss limit: stop trading if cumulative P&L < -50 USDT in session
3. v46c — Cooldown escalation: 45→90→180 min for consecutive losses (was 45 fixed)
4. v46d — Re-entry lockout: after TP, wait 5min before re-entering same symbol (cool profits)
5. v46e — Combo: max 3 positions + daily loss limit + cooldown escalation
6. v46f — Volatility regime adaptive: SL 1.6 in calm, 1.4 in normal, 1.2 in volatile
7. v46g — Spread entries: require 2min between any two new positions (avoid clustering)
8. v46h — Max 2 strategies active simultaneously (was 3 — A, B, D)
"""
import random, statistics, math, sys, os, json, time
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from copy import deepcopy

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40


def make_strategies(sl_mult=1.4, tp_mult=1.2, rsi_lo=30, rsi_hi=70, momentum_min=0.40,
                    tp_cooldown_min=45, sl_cooldown_min=45, time_stop=2400):
    return {
        'A': {'momentum_min': momentum_min, 'max_pos': 1, 'sl_mult': sl_mult, 'tp_mult': tp_mult,
              'catsl_mult': 4.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'rsi_min': 25, 'rsi_max': 75, 'time_stop': time_stop, 'pos_size_pct': 0.025},
        'B': {'rsi_lo': rsi_lo, 'rsi_hi': rsi_hi, 'max_pos': 1, 'sl_mult': sl_mult, 'tp_mult': tp_mult,
              'catsl_mult': 4.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'enabled': True, 'time_stop': time_stop, 'pos_size_pct': 0.10},
        'D': {'bb_width_max': 0.012, 'max_pos': 1, 'sl_mult': sl_mult * 0.75, 'tp_mult': tp_mult * 0.83,
              'catsl_mult': 3.0, 'cooldown_min': sl_cooldown_min, 'tp_cooldown_min': tp_cooldown_min,
              'time_stop': time_stop, 'pos_size_pct': 0.05},
        'E': {'enabled': False},
    }


def make_config_v46(strategies=None, lock_r=0.5, multi_partial=True,
                    partial1_r=0.5, partial1_pct=0.15,
                    partial2_r=1.0, partial2_pct=0.25,
                    trail_atr=0.4, atr_floor_pct=0.58,
                    max_concurrent=None, daily_loss_limit=None,
                    cooldown_escalation=False, reentry_lockout_min=0,
                    spread_entries_min=0, max_strategies_active=None):
    if strategies is None: strategies = make_strategies()
    cfg = v40.make_config(
        strategies=strategies,
        lock_r=lock_r, multi_partial=multi_partial,
        partial1_r=partial1_r, partial1_pct=partial1_pct,
        partial2_r=partial2_r, partial2_pct=partial2_pct,
        trail_atr=trail_atr, atr_floor_pct=atr_floor_pct,
    )
    cfg.update({
        'max_concurrent': max_concurrent,
        'daily_loss_limit': daily_loss_limit,
        'cooldown_escalation': cooldown_escalation,
        'reentry_lockout_ticks': int(reentry_lockout_min * 60 / 1.5) if reentry_lockout_min else 0,
        'spread_entries_ticks': int(spread_entries_min * 60 / 1.5) if spread_entries_min else 0,
        'max_strategies_active': max_strategies_active,
    })
    return cfg


CONFIGS = {
    # v43a champion (control)
    'v43a_baseline': make_config_v46(),
    # v46a — Max 3 concurrent positions
    'v46a_max3pos': make_config_v46(max_concurrent=3),
    # v46b — Daily loss limit -50 USDT
    'v46b_dailyloss_50': make_config_v46(daily_loss_limit=-50),
    # v46c — Cooldown escalation (45→90→180 min for consecutive losses)
    'v46c_cd_escalation': make_config_v46(cooldown_escalation=True),
    # v46d — Re-entry lockout 5min after TP
    'v46d_reentry_5min': make_config_v46(reentry_lockout_min=5),
    # v46e — Combo: max3 + dailyloss + cd_escalation
    'v46e_combo': make_config_v46(max_concurrent=3, daily_loss_limit=-50, cooldown_escalation=True),
    # v46f — Spread entries 2min
    'v46f_spread_2min': make_config_v46(spread_entries_min=2),
    # v46g — Max 2 strategies active simultaneously
    'v46g_max2strat': make_config_v46(max_strategies_active=2),
    # v46h — Full combo: max3 + cd_esc + reentry 5min + spread 2min
    'v46h_full_combo': make_config_v46(max_concurrent=3, cooldown_escalation=True,
                                        reentry_lockout_min=5, spread_entries_min=2),
}


class EngineSimV46(v40.EngineSimV40):
    """V46 engine with risk management extensions."""

    def __init__(self, config, name, capital=12000):
        super().__init__(config, name, capital)
        self.session_start_cash = capital
        self.consec_sl_count = 0  # for cooldown escalation
        self.last_entry_tick = -10000  # for spread entries
        self.last_tp_tick_by_sym = {}  # for re-entry lockout
        self.daily_loss_triggered = False
        self.skipped_max_concurrent = 0
        self.skipped_spread = 0
        self.skipped_reentry = 0
        self.skipped_max_strat = 0
        self.skipped_daily_loss = 0

    def _risk_allows_entry(self, sym, strategy, tick):
        """Check all v46 risk gates. Returns (allowed, reason)."""
        # Daily loss limit
        if self.config.get('daily_loss_limit') is not None and self.daily_loss_triggered:
            self.skipped_daily_loss += 1
            return False, 'daily_loss'
        # Max concurrent positions
        max_c = self.config.get('max_concurrent')
        if max_c is not None and len(self.positions) >= max_c:
            self.skipped_max_concurrent += 1
            return False, 'max_concurrent'
        # Max strategies active
        max_s = self.config.get('max_strategies_active')
        if max_s is not None:
            active_strats = set(pos.strategy for pos in self.positions.values())
            if strategy not in active_strats and len(active_strats) >= max_s:
                self.skipped_max_strat += 1
                return False, 'max_strategies'
        # Spread entries
        spread_ticks = self.config.get('spread_entries_ticks', 0)
        if spread_ticks > 0 and tick - self.last_entry_tick < spread_ticks:
            self.skipped_spread += 1
            return False, 'spread'
        # Re-entry lockout after TP
        reentry_ticks = self.config.get('reentry_lockout_ticks', 0)
        if reentry_ticks > 0 and sym in self.last_tp_tick_by_sym:
            if tick - self.last_tp_tick_by_sym[sym] < reentry_ticks:
                self.skipped_reentry += 1
                return False, 'reentry_lockout'
        return True, None

    def _try_strategy_a(self, sym, prices, tick):
        allowed, _ = self._risk_allows_entry(sym, 'A', tick)
        if not allowed: return
        super()._try_strategy_a(sym, prices, tick)
        if sym in self.positions and self.positions[sym].strategy == 'A' and self.positions[sym].entry_tick == tick:
            self.last_entry_tick = tick

    def _try_strategy_b(self, sym, prices, tick):
        allowed, _ = self._risk_allows_entry(sym, 'B', tick)
        if not allowed: return
        super()._try_strategy_b(sym, prices, tick)
        if sym in self.positions and self.positions[sym].strategy == 'B' and self.positions[sym].entry_tick == tick:
            self.last_entry_tick = tick

    def _try_strategy_d(self, sym, prices, tick):
        allowed, _ = self._risk_allows_entry(sym, 'D', tick)
        if not allowed: return
        super()._try_strategy_d(sym, prices, tick)
        if sym in self.positions and self.positions[sym].strategy == 'D' and self.positions[sym].entry_tick == tick:
            self.last_entry_tick = tick

    def _close_position(self, sym, exit_price_raw, reason, tick):
        # Track TP tick for re-entry lockout
        if reason == 'TP':
            self.last_tp_tick_by_sym[sym] = tick
        # Cooldown escalation for SL
        cfg = self.config[self.positions[sym].strategy] if sym in self.positions else {}
        if reason in ('SL', 'CAT_SL') and self.config.get('cooldown_escalation', False):
            self.consec_sl_count += 1
            # Override cooldown: 45 → 90 → 180 min based on consec SL count
            base_cd = cfg.get('cooldown_min', 45)
            if self.consec_sl_count >= 4:
                cd_min = base_cd * 4  # 180
            elif self.consec_sl_count >= 3:
                cd_min = base_cd * 2  # 90
            else:
                cd_min = base_cd
            # Call parent then override cooldown
            super()._close_position(sym, exit_price_raw, reason, tick)
            self.cooldown_until[sym] = tick + int(cd_min * 60 / 1.5)
            # Check daily loss limit
            self._check_daily_loss()
            return
        super()._close_position(sym, exit_price_raw, reason, tick)
        # Reset consec SL count on TP or TIME (winning or neutral exit)
        if reason in ('TP', 'TIME'):
            self.consec_sl_count = 0
        # Check daily loss limit
        self._check_daily_loss()

    def _partial_close(self, sym, exit_price_raw, reason, tick, close_qty):
        super()._partial_close(sym, exit_price_raw, reason, tick, close_qty)
        # Partial TPs don't reset consec SL (they're partial wins, not full TP)
        # but they also don't increment it
        self._check_daily_loss()

    def _check_daily_loss(self):
        """Trigger daily loss limit if cumulative P&L < threshold."""
        if self.config.get('daily_loss_limit') is None: return
        cum_pnl = self.cash - self.session_start_cash
        if cum_pnl < self.config['daily_loss_limit']:
            self.daily_loss_triggered = True

    def get_metrics(self):
        m = super().get_metrics()
        m['skipped_max_concurrent'] = self.skipped_max_concurrent
        m['skipped_spread'] = self.skipped_spread
        m['skipped_reentry'] = self.skipped_reentry
        m['skipped_max_strat'] = self.skipped_max_strat
        m['skipped_daily_loss'] = self.skipped_daily_loss
        m['daily_loss_triggered'] = self.daily_loss_triggered
        return m


def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV46(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
    for tick in range(v40.TOTAL_TICKS):
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


SEEDS_ALL = [2024, 7, 42, 1337, 99, 555, 31337, 8, 1234, 7777, 2025, 314]
RESULTS_FILE = '/tmp/v46_seeds.json'

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
        baseline = 'v43a_baseline'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L ({len(seeds)} seeds):\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*160)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        print("WINNER SELECTION (12 seeds: target ≥75% profitable + WR≥70 + MaxDD<0.3 + AvgR>0)")
        print("=" * 80)
        candidates = [(name, m) for name, m in agg.items()
                      if m['profitable_seeds'] >= 75 and m['wr_above_60_seeds'] >= 75
                      and m['max_dd_mean'] < 0.3 and m['avg_r_mean'] > 0]
        if candidates:
            candidates.sort(key=lambda x: (x[1]['profitable_seeds'], x[1]['pnl_mean'], x[1]['wr_mean']), reverse=True)
            w = candidates[0]
            print(f"\n🏆 WINNER (12-seed validated): {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f}  Profit {w[1]['profitable_seeds']:.0f}%  AvgR {w[1]['avg_r_mean']:+.3f}  MaxDD {w[1]['max_dd_mean']:.2f}%  PF {w[1]['pf_mean']:.2f}")
        else:
            print("\n  ⚠️ No config met 75% profit criterion. Top 5:")
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
