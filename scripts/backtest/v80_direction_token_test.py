#!/usr/bin/env python3
"""
v80 — DIRECTION + TOKEN PROFILE + HIGHVOL analysis of v67 champion.

User question:
  1. Does v67 work well in LONG and SHORT?
  2. Does v67 work on ANY token (meme vs bluechip)?
  3. Does v67 work in HIGH VOLATILITY moments?

This script answers all three by:
  - Running v67 (A 0.040 + B 0.30 + pyr +75%) on 12 seeds
  - Splitting P&L by direction (LONG vs SHORT)
  - Testing 4 token profiles: MEME (8% vol), ALT (3% vol), BLUE (1% vol), STABLE (0.2% vol)
  - Testing HIGHVOL regime specifically (1.5% per-tick vol)
  - Testing BEAR regime (negative drift -3%)
"""
import sys, os, json, random, statistics, math, time
from copy import deepcopy
from collections import defaultdict
sys.path.insert(0, '/home/z/my-project/scripts')
sys.path.insert(0, '/home/z/my-project/scripts/backtest')

# Import the v62 engine (which v67 inherits from) and v40 helpers
import v40_push as v40
from v62_push import EngineSimV62, v61b_base
import v38_push_v37e as v38


# ─────────────────────────────────────────────────────────────────────
# v67 CONFIGURATION — the current production champion
# ─────────────────────────────────────────────────────────────────────

def v67_config():
    """v67: A 0.040 + B 0.30 + pyr 75% @+1.0R + lock 0.35R"""
    cfg = v61b_base(
        pyramid_pct=0.75,
        strat_kwargs={'a_pos_size': 0.040, 'b_pos_size': 0.30},
        lock_offset_r=0.35,
    )
    cfg['pyramid_trigger_r'] = 1.0
    return cfg


# ─────────────────────────────────────────────────────────────────────
# DIRECTION TRACKING — extend Trade tracking to separate LONG/SHORT P&L
# ─────────────────────────────────────────────────────────────────────

class EngineSimV80(EngineSimV62):
    """v62 engine + LONG/SHORT P&L tracking."""

    def __init__(self, config, name, capital=12000):
        super().__init__(config, name)
        self.pnl_long = 0.0
        self.pnl_short = 0.0
        self.trades_long = 0
        self.trades_short = 0
        self.wins_long = 0
        self.wins_short = 0
        self.pnl_long_strat = defaultdict(float)  # by strategy
        self.pnl_short_strat = defaultdict(float)
        self.trades_long_strat = defaultdict(int)
        self.trades_short_strat = defaultdict(int)

    def _close_position(self, sym, exit_price_raw, reason, tick):
        """Override to capture direction split (full close)."""
        pos = self.positions.get(sym)
        if not pos:
            return
        # Compute pnl BEFORE parent closes (parent resets)
        is_long = pos.direction == 'LONG'
        strat = pos.strategy
        # Apply slippage to mirror parent's pnl computation
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
        # Call parent
        super()._close_position(sym, exit_price_raw, reason, tick)

    def _partial_close(self, sym, exit_price_raw, reason, tick, close_qty):
        """Override to capture direction split (partial close)."""
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

    def get_direction_metrics(self):
        return {
            'pnl_long': self.pnl_long,
            'pnl_short': self.pnl_short,
            'trades_long': self.trades_long,
            'trades_short': self.trades_short,
            'wins_long': self.wins_long,
            'wins_short': self.wins_short,
            'wr_long': (self.wins_long / self.trades_long * 100) if self.trades_long else 0,
            'wr_short': (self.wins_short / self.trades_short * 100) if self.trades_short else 0,
            'pnl_long_strat': dict(self.pnl_long_strat),
            'pnl_short_strat': dict(self.pnl_short_strat),
            'trades_long_strat': dict(self.trades_long_strat),
            'trades_short_strat': dict(self.trades_short_strat),
        }


# ─────────────────────────────────────────────────────────────────────
# TOKEN PROFILE GENERATORS — simulate MEME / ALT / BLUE / STABLE behavior
# ─────────────────────────────────────────────────────────────────────

def gen_profile_prices(n_ticks, base_price, rng, profile='MIXED'):
    """
    Generate prices for a specific token profile.

    profile options:
      - MEME:    3% drift, 1.5% per-tick vol, 8% regime jumps (high vol + jumps)
      - ALT:     1% drift, 0.8% per-tick vol, 3% regime jumps (medium vol)
      - BLUE:    0.3% drift, 0.3% per-tick vol, 1% regime jumps (low vol, BTC-like)
      - STABLE:  0% drift, 0.05% per-tick vol, 0.2% max jump (USDT-like)
      - MIXED:   original GBM + regime switching (v38 baseline)
      - HIGHVOL: 0% drift, 1.5% per-tick vol, 5% regime jumps (pure high vol)
      - BEAR:   -3% drift, 1% per-tick vol, 3% regime jumps (downtrend)
      - BULL:   +3% drift, 1% per-tick vol, 3% regime jumps (uptrend)
    """
    if profile == 'MIXED':
        return v38.gen_regime_prices(n_ticks, base_price, rng)

    # Configurable profiles
    PROFILE_CONFIG = {
        'MEME':    {'drift':  0.50, 'vol': 0.015, 'jump_pct': 0.08, 'jump_freq': 0.005, 'regimes': False},
        'ALT':     {'drift':  0.10, 'vol': 0.008, 'jump_pct': 0.03, 'jump_freq': 0.003, 'regimes': False},
        'BLUE':    {'drift':  0.03, 'vol': 0.003, 'jump_pct': 0.01, 'jump_freq': 0.001, 'regimes': False},
        'STABLE':  {'drift':  0.00, 'vol': 0.0005, 'jump_pct': 0.002, 'jump_freq': 0.0005, 'regimes': False},
        'HIGHVOL': {'drift':  0.00, 'vol': 0.015, 'jump_pct': 0.05, 'jump_freq': 0.005, 'regimes': False},
        'BEAR':    {'drift': -0.30, 'vol': 0.010, 'jump_pct': 0.03, 'jump_freq': 0.003, 'regimes': False},
        'BULL':    {'drift':  0.30, 'vol': 0.010, 'jump_pct': 0.03, 'jump_freq': 0.003, 'regimes': False},
    }
    c = PROFILE_CONFIG[profile]
    prices = [base_price]
    for i in range(1, n_ticks):
        # GBM core
        ret = c['drift'] / 100 + c['vol'] * rng.gauss(0, 1)
        # Jumps (fat tails — common in crypto, esp. MEME)
        if rng.random() < c['jump_freq']:
            jump = rng.uniform(-c['jump_pct'], c['jump_pct'])
            ret += jump
        new_price = prices[-1] * (1 + ret)
        # Floor at 0.01 to avoid negative prices
        new_price = max(0.01, new_price)
        prices.append(new_price)
    return prices


# ─────────────────────────────────────────────────────────────────────
# RUNNER — single seed × single profile
# ─────────────────────────────────────────────────────────────────────

def run_seed_profile(seed, profile, n_tokens=10):
    """Run v67 on 1 seed × 1 profile, return metrics + direction split."""
    rng = random.Random(seed)
    base = 1.0 * (1 + rng.uniform(-0.3, 0.3))
    all_prices = {f"TOK{i:02d}": gen_profile_prices(v40.TOTAL_TICKS, base * (1 + rng.uniform(-0.2, 0.2)), rng, profile)
                  for i in range(n_tokens)}

    engine = EngineSimV80(deepcopy(v67_config()), f'v67_{profile}')
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
    d = engine.get_direction_metrics()
    m.update(d)
    return m


SEEDS = [2024, 7, 42, 1337, 99, 555, 31337, 8, 1234, 7777, 2025, 314]
PROFILES = ['MIXED', 'BULL', 'BEAR', 'HIGHVOL', 'MEME', 'ALT', 'BLUE', 'STABLE']
RESULTS_FILE = '/tmp/v80_direction_token.json'


def aggregate():
    if not os.path.exists(RESULTS_FILE):
        print(f"No results at {RESULTS_FILE}")
        sys.exit(1)
    with open(RESULTS_FILE) as f:
        all_results = json.load(f)

    print("=" * 130)
    print(f"{'Profile':<10} {'Trades':<8} {'WR%':<10} {'P&L':<12} {'PF':<7} {'MaxDD%':<8} {'Profit%':<10} {'L trades':<10} {'S trades':<10} {'L P&L':<12} {'S P&L':<12} {'L WR%':<8} {'S WR%':<8}")
    print("=" * 130)
    for profile in PROFILES:
        seeds_data = [r for r in all_results.values() if profile in r]
        if not seeds_data:
            continue
        ms = [r[profile] for r in seeds_data]
        trades = statistics.mean(m['trades'] for m in ms)
        wr = statistics.mean(m['wr'] for m in ms)
        pnl = statistics.mean(m['pnl'] for m in ms)
        pf = statistics.mean(m['pf'] for m in ms)
        max_dd = statistics.mean(m['max_dd'] for m in ms)
        profit = sum(1 for m in ms if m['pnl'] > 0) / len(ms) * 100
        l_trades = statistics.mean(m['trades_long'] for m in ms)
        s_trades = statistics.mean(m['trades_short'] for m in ms)
        l_pnl = statistics.mean(m['pnl_long'] for m in ms)
        s_pnl = statistics.mean(m['pnl_short'] for m in ms)
        l_wr = statistics.mean(m['wr_long'] for m in ms)
        s_wr = statistics.mean(m['wr_short'] for m in ms)
        print(f"{profile:<10} {trades:<8.0f} {wr:<10.1f} {pnl:<+12.2f} {pf:<7.2f} {max_dd:<8.2f} {profit:<10.0f} {l_trades:<10.0f} {s_trades:<10.0f} {l_pnl:<+12.2f} {s_pnl:<+12.2f} {l_wr:<8.1f} {s_wr:<8.1f}")

    print("\n" + "=" * 130)
    print("DIRECTION P&L BY STRATEGY (sum across all seeds)")
    print("=" * 130)
    print(f"{'Profile':<10} {'Strat':<7} {'L trades':<10} {'S trades':<10} {'L P&L':<14} {'S P&L':<14} {'L WR%':<8} {'S WR%':<8}")
    print("-" * 130)
    for profile in PROFILES:
        seeds_data = [r for r in all_results.values() if profile in r]
        if not seeds_data:
            continue
        ms = [r[profile] for r in seeds_data]
        for strat in ['A', 'B', 'D', 'E']:
            l_t = sum(m['trades_long_strat'].get(strat, 0) for m in ms)
            s_t = sum(m['trades_short_strat'].get(strat, 0) for m in ms)
            l_p = sum(m['pnl_long_strat'].get(strat, 0) for m in ms)
            s_p = sum(m['pnl_short_strat'].get(strat, 0) for m in ms)
            if l_t + s_t == 0:
                continue
            # Approximate WR via P&L sign (not perfect — close enough for diagnostic)
            l_wr_strat = "—"
            s_wr_strat = "—"
            print(f"{profile:<10} {strat:<7} {l_t:<10} {s_t:<10} {l_p:<+14.2f} {s_p:<+14.2f} {l_wr_strat:<8} {s_wr_strat:<8}")

    print("\n" + "=" * 130)
    print("VEREDICTO v67 — LONG vs SHORT vs TOKEN PROFILE")
    print("=" * 130)
    for profile in PROFILES:
        seeds_data = [r for r in all_results.values() if profile in r]
        if not seeds_data:
            continue
        ms = [r[profile] for r in seeds_data]
        pnl = statistics.mean(m['pnl'] for m in ms)
        l_pnl = statistics.mean(m['pnl_long'] for m in ms)
        s_pnl = statistics.mean(m['pnl_short'] for m in ms)
        l_share = (abs(l_pnl) / (abs(l_pnl) + abs(s_pnl)) * 100) if (abs(l_pnl) + abs(s_pnl)) > 0 else 0
        s_share = 100 - l_share
        profit = sum(1 for m in ms if m['pnl'] > 0) / len(ms) * 100
        verdict_p = "✅" if profit >= 70 else ("⚠️" if profit >= 50 else "❌")
        verdict_dir = "✅ BALANCED" if (35 <= l_share <= 65) else ("⚠️ LONG-DOM" if l_share > 65 else "⚠️ SHORT-DOM")
        print(f"  {verdict_p} {profile:<10} P&L {pnl:+8.2f} | LONG {l_pnl:+8.2f} ({l_share:5.1f}%) | SHORT {s_pnl:+8.2f} ({s_share:5.1f}%) | Profit {profit:3.0f}% | {verdict_dir}")


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
        # Run all 12 seeds × 8 profiles = 96 runs (~30-40 min)
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
        print("  python v80_direction_token_test.py all        # 12 seeds × 8 profiles = 96 runs (~30-40 min)")
        print("  python v80_direction_token_test.py one <seed> <profile>")
        print("  python v80_direction_token_test.py aggregate")
