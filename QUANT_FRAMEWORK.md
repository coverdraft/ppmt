# PPMT Quantitative Research Framework

> **Paradigm shift (v63+)**: We no longer optimize parameters to maximize backtest P&L.
> We build a quantitative engine that is **hard to break** across market regimes.
> Inspired by institutional quant research practices.

## The 10 Commandments (user directive, v63)

1. **ROBUSTNESS** — 50-100 seeds per candidate. Validate across bull / bear / sideways / high-vol / low-vol regimes. A strategy that only works in one regime is regime-specific, not robust.
2. **SENSITIVITY** — Sweep every parameter ±10-20%. If small changes destroy the strategy, it is fragile. We want sensitivity maps, not just the best point.
3. **GENERALIZATION** — Test on multiple assets. Universal parameters that work everywhere are preferred over parameters that are optimal on one asset.
4. **NEW STRATEGIES** — Independent alpha sources. Each must generate different operations, not duplicate existing signals.
5. **CORRELATION** — Calculate correlation between strategies. Eliminate duplicates. Build an uncorrelated portfolio.
6. **MONTE CARLO** — Bootstrap resampling. Compute worst-case scenario, drawdown distribution, monthly/yearly loss probability.
7. **REAL COSTS** — Variable spread, partial fills, slippage. Know how much survives in real conditions.
8. **PAPER TRADING** — Only at the end, once we know the best options.
9. **METRICS** — Optimize a composite score combining PF / Sharpe / Sortino / MaxDD / Calmar / Recovery / WR / AvgR / stability. Never optimize P&L alone.
10. **GOLDEN RULE** — Only accept an improvement if it is **statistically more robust** than v62a. Prefer a strategy that earns 10% less but survives any market over one that earns 10% more only in backtest.

---

## Current State (after v63 analysis)

### ❌ v62a REJECTED as production champion

The 12-seed validation that crowned v62a was **statistically underpowered** and gave false confidence.

| Metric | 12-seed (v62a claim) | 50-seed reality | Verdict |
|---|---|---|---|
| Composite score | (not measured) | **33/100** | ❌ Low |
| MIXED P&L | +48.56 | **-0.90** | ❌ Breakeven |
| MIXED profitable seeds | 67% | **33%** | ❌ Worse than coin flip |
| Regime stability | (not measured) | **17%** (1/6) | ❌ Fragile |
| BEAR MaxDD | (not measured) | **21.91%** | ❌ Catastrophic |
| HIGHVOL MaxDD | (not measured) | **47.06%** | ❌ Catastrophic |
| LOWVOL trades | (not measured) | **0** | ❌ No signals |
| MIXED MaxDD | 0.29% | **0.42%** | ❌ Over 0.35% limit |

### Root causes of fragility

1. **12-seed luck** — The 12 seeds happened to be favorable. With 50 seeds, the true distribution is revealed.
2. **Regime overfit** — v62a's parameters are tuned for v38's MIXED regime distribution (60% calm, 25% normal, 10% volatile, 5% trending). In pure regimes, the strategy fails.
3. **Pyramiding amplifies losses** — The +75% pyramid at +1R (v62a's headline feature) becomes a liability in BEAR/HIGHVOL where SL is hit frequently.
4. **ATR floor blocks calm markets** — The 0.58% ATR floor (sweet spot for MIXED) prevents ANY trades in LOWVOL (0.20% vol).
5. **Strategy A/B don't trigger in SIDE/LOWVOL** — Momentum (A) needs trend, RSI (B) needs volatility. Side and low-vol markets produce no signals.

---

## Framework Architecture (v63+)

```
┌─────────────────────────────────────────────────────────┐
│  v63_robustness.py  (FOUNDATION)                        │
│  ├─ Multi-regime price generator (6 regimes)            │
│  ├─ 50-seed validator                                   │
│  ├─ Extended metrics (Sortino, Calmar, Recovery)         │
│  ├─ Composite score (0-100, 10 components)              │
│  └─ Acceptance gate (5 criteria)                        │
└─────────────────────────────────────────────────────────┘
        │
        ├── v64_sensitivity.py — parameter ±10-20% sweep, fragility index
        ├── v65_strategies.py — 8 new independent alpha sources (solo-tested)
        ├── v66_correlation.py — trade-level correlation matrix
        ├── v67_montecarlo.py — bootstrap resampling, DD distribution
        ├── v68_realcosts.py — variable spread, partial fills
        └── v69_generalization.py — multi-asset profile testing
```

### Regimes tested

| Regime | Volatility | Drift | Description |
|---|---|---|---|
| BULL | 0.40% / tick | +3% / 4h | Sustained uptrend |
| BEAR | 0.50% / tick | -3% / 4h | Sustained downtrend (bears are more volatile) |
| SIDE | 0.30% / tick | 0% | Choppy mean-reversion market |
| HIGHVOL | 0.80% / tick | 0% | Storm market (realistic crypto storm) |
| LOWVOL | 0.20% / tick | 0% | Dead market |
| MIXED | v38 weights | v38 weights | Backward-compat with v38-v62 results |

### Composite score (0-100)

| Component | Weight | Cap |
|---|---|---|
| Profit Factor | 15% | 3.0 |
| Sharpe | 12% | 10 |
| Sortino | 10% | 15 |
| Calmar | 10% | 200 |
| Recovery Factor | 10% | 200 |
| Win Rate | 10% | 85% |
| AvgR | 10% | 1.0 |
| MaxDD penalty | 8% | 0.50% |
| Regime stability | 8% | 100% |
| Seed stability | 7% | 100% |

### Acceptance gate (all 5 must pass)

1. Composite score > baseline
2. No regime collapse (P&L > -10 in every regime)
3. Profitable seeds % >= baseline
4. MaxDD ≤ 0.35% in every regime
5. P&L std ≤ baseline × 1.20

---

## New strategies implemented (v65)

8 independent alpha sources, each tested SOLO first:

| Strategy | Description | Status |
|---|---|---|
| E_RSI595 | RSI extremes (15/85 mean reversion) | Needs tuning |
| E_VOLBREAK | Volatility breakout (2σ move) | Needs tuning |
| E_ORB | Opening range breakout (200-tick) | Needs tuning |
| E_PULLBACK | Pullback in trend (SMA50>SMA100) | Needs tuning |
| E_MEANREV | Mean reversion (2σ from VWAP) | Needs tuning |
| E_VWAP | VWAP bounce with momentum | Needs tuning |
| E_LIQUIDITY | Liquidity sweep (wick + reclaim) | Needs tuning |
| E_COMPRESS | Compression breakout (Bollinger) | ⚠️ Promising |

---

## Version stack (v63+)

```
v62a  → REJECTED (overfit to 12-seed MIXED)
v63   → Robustness framework + v62a baseline measurement (composite 33/100)
v64   → Sensitivity maps (pending)
v65   → 8 new strategies implemented (pending tuning)
v66   → Correlation matrix (pending)
v67   → Monte Carlo (pending)
v68   → Real costs (pending)
v69   → Generalization (pending)
```

## Next steps

1. **Tune the 8 new strategies** — Each needs to be profitable in MIXED with MaxDD ≤ 0.35%
2. **Run v64 sensitivity** — Identify which v62a parameters are fragile
3. **Build v66 correlation** — Eliminate duplicate strategies
4. **Build v67 Monte Carlo** — Compute loss probability distributions
5. **Build v70 robust engine** — A new engine designed from scratch for regime robustness
6. **Acceptance gate** — Only strategies that beat v62a's composite 33/100 AND pass all 5 criteria

---

## File map

```
scripts/quant/
├── v63_robustness.py       # Foundation: regimes + 50 seeds + composite
├── v63_parallel.py         # Multiprocessing runner (6 regimes in parallel)
├── v63_analyze.py          # Aggregate + print results
├── v63_smoke.py            # Quick 2-seed × 6-regime smoke test
├── v64_sensitivity.py      # Parameter ±10-20% sweep
├── v65_strategies.py       # 8 new independent strategies (solo-tested)
├── v65_smoke.py            # Quick 2-seed × 8-strategy smoke test
└── v62a_ROBUSTNESS_FINDINGS.txt  # The 50-seed analysis that rejected v62a
```
