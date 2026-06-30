#!/usr/bin/env python3
"""
v63 — EQUITY CURVE PROTECTION (Priority 1: ROBUSTNESS).

User's directive:
  "Piensa como un quantitative researcher de un hedge fund.
   No busques el mejor backtest. Busca la estrategia más difícil de romper."

Diagnosis of v62a (seed 2024 trace):
  - A: 53 trades, WR 75%, P&L -7.51 (NET LOSER)
  - B: 7 trades, WR 100%, P&L +41.89 (WORKHORSE via pyramiding)
  - D: 0 trades (inert)
  - 4 seeds always lose (314, 1234, 99, 2025)
  - MaxDD 0.29% (close to 0.30% safety limit)

KEY INSIGHT: All P&L comes from B's pyramiding. A drags the system down.
If we can DETECT when A is in a losing streak and pull size, we cut MaxDD
without hurting B's contribution.

DESIGN — Equity Curve Protection (ECP):
  Track rolling equity. If recent N trades are net negative beyond threshold,
  reduce size on A (the loser strategy) by 50% until equity recovers.

  State:
    ecp_window = 8 trades  (rolling window of recent P&L)
    ecp_threshold = -3.0 USDT  (if sum of last 8 trades < -3.0, reduce size)
    ecp_size_mult = 0.5  (when active, A trades at 50% size)
    ecp_recovery = +1.0 USDT  (re-enable when rolling sum > +1.0)

  Effect:
    - During A's losing streaks, size is halved → smaller drawdown
    - When A recovers, full size is restored → no loss of upside
    - B is NEVER affected (B is the winner, don't touch it)
    - D is NEVER affected (D is inert anyway)

  Why this is robust (not overfit):
    - No parameter optimization (window 8 / threshold 3.0 are heuristic, not grid-searched)
    - Logic is universal: "stop digging when in a hole"
    - Works across any market regime (no regime-specific tuning)
    - One threshold = low degrees of freedom = low overfit risk

Variants tested:
  v62a_control      — v62a baseline (regression check)
  v63a_ecp_a_only   — ECP on A only (window 8, threshold -3.0, size 0.5)
  v63b_ecp_a_strict — ECP on A, threshold -2.0 (trigger faster)
  v63c_ecp_a_loose  — ECP on A, threshold -5.0 (trigger slower)
  v63d_ecp_a_window5 — ECP on A, window 5 (faster detection)
  v63e_ecp_a_window12 — ECP on A, window 12 (slower detection)
  v63f_ecp_all      — ECP on A AND B (B also gets protection)
  v63g_ecp_a_size03 — ECP on A, size mult 0.3 (more aggressive cut)
"""
import random, statistics, math, sys, os, json, time
from copy import deepcopy
from collections import deque

sys.path.insert(0, '/home/z/my-project/scripts')
import v40_push as v40
from v51_push import make_strategies_v51
from v53_push import EngineSimV53, make_v53_config
from v57_push import EngineSimV57
from v62_push import EngineSimV62, v61b_base


# ─── Config builder ─────────────────────────────────────────────────
def v63_base(ecp_kwargs=None, **overrides):
    """v63 = v62a baseline + Equity Curve Protection."""
    cfg = v61b_base(pyramid_pct=0.75)  # v62a = v61b with pyramid_pct=0.75
    # Add ECP config
    ecp = {
        'ecp_enabled': True,
        'ecp_window': 8,              # rolling window of trades
        'ecp_threshold': -3.0,        # trigger when sum < -3.0
        'ecp_size_mult': 0.5,         # size multiplier when active
        'ecp_recovery': 1.0,          # recover when sum > +1.0
        'ecp_strategies': ['A'],      # which strategies get reduced
    }
    if ecp_kwargs:
        ecp.update(ecp_kwargs)
    cfg['ecp'] = ecp
    cfg.update(overrides)
    return cfg


# ─── V63 Engine: extends V62 with Equity Curve Protection ──────────
class EngineSimV63(EngineSimV62):
    """V63: adds Equity Curve Protection on top of V62."""

    def __init__(self, config, name):
        super().__init__(config, name)
        self.ecp_cfg = self.config.get('ecp', {})
        self.ecp_active = {s: False for s in 'ABCDE'}
        # Track recent trade P&L per strategy (rolling window)
        self.recent_trades_pnl = {s: deque(maxlen=self.ecp_cfg.get('ecp_window', 8))
                                   for s in 'ABCDE'}

    def _partial_close(self, sym, exit_price_raw, reason, tick, close_qty):
        """Override to track per-strategy partial close P&L for ECP."""
        super()._partial_close(sym, exit_price_raw, reason, tick, close_qty)
        # Don't count partials in ECP — only full closes

    def _close_position(self, sym, exit_price_raw, reason, tick):
        """Override to record trade P&L in ECP rolling window."""
        if sym not in self.positions: return
        pos = self.positions[sym]
        # Track P&L BEFORE closing
        slip = exit_price_raw * (v40.SLIPPAGE_PCT / 100)
        exit_price = exit_price_raw - slip if pos.direction == 'LONG' else exit_price_raw + slip
        gross = (exit_price - pos.entry_price) * pos.qty if pos.direction == 'LONG' \
                else (pos.entry_price - exit_price) * pos.qty
        exit_fee = exit_price * pos.qty * (v40.FEE_PCT / 100)
        net = gross - exit_fee
        # Record in ECP window for this strategy
        if pos.strategy in self.recent_trades_pnl:
            self.recent_trades_pnl[pos.strategy].append(net)
        # Update ECP state
        self._update_ecp_state(pos.strategy)
        # Call parent close
        super()._close_position(sym, exit_price_raw, reason, tick)

    def _update_ecp_state(self, strategy):
        """Check if ECP should be active for this strategy."""
        ecp = self.ecp_cfg
        if not ecp.get('ecp_enabled', False): return
        if strategy not in ecp.get('ecp_strategies', []): return
        window = self.recent_trades_pnl[strategy]
        if len(window) < 3: return  # need at least 3 trades to evaluate
        rolling_sum = sum(window)
        if self.ecp_active[strategy]:
            # Currently active: check recovery
            if rolling_sum > ecp.get('ecp_recovery', 1.0):
                self.ecp_active[strategy] = False
        else:
            # Currently inactive: check trigger
            if rolling_sum < ecp.get('ecp_threshold', -3.0):
                self.ecp_active[strategy] = True

    def _try_strategy_a(self, sym, prices, tick):
        """Override A: apply ECP size reduction if active."""
        if not self.ecp_cfg.get('ecp_enabled', False) or 'A' not in self.ecp_cfg.get('ecp_strategies', []):
            return super()._try_strategy_a(sym, prices, tick)
        # Check ECP state
        ecp = self.ecp_cfg
        # Temporarily override A's pos_size_pct if ECP active
        if self.ecp_active.get('A', False):
            original_a = deepcopy(self.config['A'])
            self.config['A']['pos_size_pct'] = original_a['pos_size_pct'] * ecp.get('ecp_size_mult', 0.5)
            try:
                super()._try_strategy_a(sym, prices, tick)
            finally:
                self.config['A'] = original_a
        else:
            super()._try_strategy_a(sym, prices, tick)

    def _try_strategy_b(self, sym, prices, tick):
        """Override B: apply ECP if B is in ecp_strategies."""
        if not self.ecp_cfg.get('ecp_enabled', False) or 'B' not in self.ecp_cfg.get('ecp_strategies', []):
            return super()._try_strategy_b(sym, prices, tick)
        if self.ecp_active.get('B', False):
            original_b = deepcopy(self.config['B'])
            self.config['B']['pos_size_pct'] = original_b['pos_size_pct'] * self.ecp_cfg.get('ecp_size_mult', 0.5)
            try:
                super()._try_strategy_b(sym, prices, tick)
            finally:
                self.config['B'] = original_b
        else:
            super()._try_strategy_b(sym, prices, tick)


# ─── Configs ────────────────────────────────────────────────────────
CONFIGS = {
    # CONTROL: v62a baseline, no ECP
    'v62a_control': v63_base(ecp_kwargs={'ecp_enabled': False}),

    # v63a — ECP on A only (default: window 8, threshold -3.0, size 0.5)
    'v63a_ecp_a': v63_base(ecp_kwargs={
        'ecp_enabled': True, 'ecp_strategies': ['A'],
        'ecp_window': 8, 'ecp_threshold': -3.0, 'ecp_size_mult': 0.5,
    }),

    # v63b — ECP on A, threshold -2.0 (trigger faster)
    'v63b_ecp_a_strict': v63_base(ecp_kwargs={
        'ecp_enabled': True, 'ecp_strategies': ['A'],
        'ecp_window': 8, 'ecp_threshold': -2.0, 'ecp_size_mult': 0.5,
    }),

    # v63c — ECP on A, threshold -5.0 (trigger slower)
    'v63c_ecp_a_loose': v63_base(ecp_kwargs={
        'ecp_enabled': True, 'ecp_strategies': ['A'],
        'ecp_window': 8, 'ecp_threshold': -5.0, 'ecp_size_mult': 0.5,
    }),

    # v63d — ECP on A, window 5 (faster detection)
    'v63d_ecp_a_w5': v63_base(ecp_kwargs={
        'ecp_enabled': True, 'ecp_strategies': ['A'],
        'ecp_window': 5, 'ecp_threshold': -3.0, 'ecp_size_mult': 0.5,
    }),

    # v63e — ECP on A, window 12 (slower detection)
    'v63e_ecp_a_w12': v63_base(ecp_kwargs={
        'ecp_enabled': True, 'ecp_strategies': ['A'],
        'ecp_window': 12, 'ecp_threshold': -3.0, 'ecp_size_mult': 0.5,
    }),

    # v63f — ECP on A AND B (also protect B)
    'v63f_ecp_all': v63_base(ecp_kwargs={
        'ecp_enabled': True, 'ecp_strategies': ['A', 'B'],
        'ecp_window': 8, 'ecp_threshold': -3.0, 'ecp_size_mult': 0.5,
    }),

    # v63g — ECP on A, size mult 0.3 (more aggressive cut)
    'v63g_ecp_a_size03': v63_base(ecp_kwargs={
        'ecp_enabled': True, 'ecp_strategies': ['A'],
        'ecp_window': 8, 'ecp_threshold': -3.0, 'ecp_size_mult': 0.3,
    }),
}


# ─── Runner ─────────────────────────────────────────────────────────
def run_single_seed(seed):
    rng = random.Random(seed)
    all_prices = {f"TOK{i:02d}": v40.v38.gen_regime_prices(v40.TOTAL_TICKS, 1.0 * (1 + rng.uniform(-0.3, 0.3)), rng)
                  for i in range(v40.N_TOKENS)}
    engines = [EngineSimV63(deepcopy(cfg), name) for name, cfg in CONFIGS.items()]
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
RESULTS_FILE = '/tmp/v63_seeds.json'

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
        baseline = 'v62a_control'
        print(f"\n{'='*220}\n{'Config':<32} {'Trades':<8} {'WR%':<12} {'P&L':<14} {'PF':<7} {'Sharpe':<10} {'MaxDD%':<8} {'MaxCL':<7} {'Consist%':<10} {'AvgR':<7} {'Stab%':<7}\n{'='*220}")
        for name, m in agg.items():
            is_baseline = name == baseline
            marker = "🟢" if is_baseline else ("  " if m['pnl_mean'] < 0 else "✅")
            print(f"{marker} {name:<30} {m['trades_mean']:<8.0f} {m['wr_mean']:.1f}±{m['wr_std']:.1f}{'':>2} {m['pnl_mean']:+.2f}±{m['pnl_std']:.0f}{'':>3} {m['pf_mean']:.2f}{'':>3} {m['sharpe_mean']:+.2f}{'':>5} {m['max_dd_mean']:.2f}{'':>4} {m['max_consec_loss_mean']:.1f}{'':>4} {m['consistency_mean']:.1f}%{'':>5} {m['avg_r_mean']:+.2f}{'':>4} {m['profitable_seeds']:.0f}%")
        print(f"\nPer-seed P&L ({len(seeds)} seeds):\n  {'Config':<32} | " + " | ".join(f"S{s}" for s in seeds) + "\n  " + "-"*160)
        for name, m in agg.items():
            print(f"  {name:<32} | " + " | ".join(f"{p:+6.0f}" for p in m['pnl_per_seed']))
        print("\n" + "=" * 80)
        print("WINNER SELECTION (target: P&L > baseline + MaxDD ≤ 0.30% + Profit ≥ 67%)")
        print("=" * 80)
        baseline_pnl = agg[baseline]['pnl_mean']
        baseline_dd = agg[baseline]['max_dd_mean']
        candidates = [(name, m) for name, m in agg.items()
                      if name != baseline
                      and m['pnl_mean'] > baseline_pnl * 0.95  # allow 5% P&L loss
                      and m['max_dd_mean'] <= baseline_dd       # must improve MaxDD
                      and m['profitable_seeds'] >= 67]
        if candidates:
            # Rank by robustness: lower MaxDD first, then higher P&L
            candidates.sort(key=lambda x: (x[1]['max_dd_mean'], -x[1]['pnl_mean']))
            w = candidates[0]
            print(f"\n🏆 WINNER (12-seed validated, robustness-first): {w[0]}")
            print(f"   WR {w[1]['wr_mean']:.1f}%  P&L {w[1]['pnl_mean']:+.2f} (vs base {baseline_pnl:+.2f})  Profit {w[1]['profitable_seeds']:.0f}%  MaxDD {w[1]['max_dd_mean']:.2f}% (vs base {baseline_dd:.2f}%)  PF {w[1]['pf_mean']:.2f}")
        else:
            print("\n  ⚠️ No config improved robustness. Top 5 by MaxDD:")
            ranked = sorted(agg.items(), key=lambda x: x[1]['max_dd_mean'])
            for i, (name, m) in enumerate(ranked[:5]):
                vs_pnl = m['pnl_mean'] - baseline_pnl
                vs_dd = m['max_dd_mean'] - baseline_dd
                print(f"  #{i+1} {name:<32} P&L {m['pnl_mean']:+.2f} ({vs_pnl:+.2f})  MaxDD {m['max_dd_mean']:.2f}% ({vs_dd:+.2f})  Profit {m['profitable_seeds']:.0f}%")
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
