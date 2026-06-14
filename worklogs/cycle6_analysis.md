# Cycle 6 Analysis — PPMT v0.6.2

**Date**: 2026-06-11
**Version**: v0.6.2
**Command**: `ppmt build --force -s BTC/USDT && ppmt run --symbol BTC/USDT --paper`
**Symbol**: BTC/USDT (1h)
**Data**: 47981 candles (2020-12-19 to 2026-06-11)

## v0.6.2 Changes

1. **min_confidence**: 0.15 → 0.20 (PaperTraderConfig + CLI defaults)
2. **SHORT gate**: `max(conf*1.5, 0.15)` → `max(conf*1.2, 0.20)` — allows SHORT diversification
3. **Catastrophic protection**: 0.0% → 8.0% — safety net against extreme losses
4. **Bootstrap SHORT gate**: `max(conf*1.5, 0.15)` → `max(conf*1.2, 0.15)` — consistent
5. **Bootstrap probability bonus**: REMOVED (was lowering threshold from 0.10 to 0.05)
6. **CLI --min-confidence defaults**: Updated from 0.15/0.10 to 0.20

## Root Cause of Cycle 5 Regression

Cycle 5 (+86.82%) was a massive regression from Cycle 4 (+1434%). Root cause analysis:

1. **v0.6.0 probability bonus** lowered effective min_confidence from 15% to 7.5% when probability >50%
2. This allowed 10% confidence trades with WR of only 32.6%
3. v0.6.1 removed the bonus but the user was running v0.6.0 (pip install -e not properly applied)
4. SHORT gate (1.5x) eliminated ALL SHORT trades — no bearish diversification
5. Bootstrap probability bonus was also lowering thresholds, producing worse trie quality

## Results Comparison

### Cycle 6a (min_conf=15%, accidental due to CLI override)

| Metric | Value |
|--------|-------|
| P&L | +155,150% |
| Win Rate | 89.2% (190W/23L) |
| Sharpe | 19.96 |
| Max DD | 8.3% |
| Profit Factor | 13.59 |
| Trades | 213 |
| SHORT trades | 3 |
| Avg Confidence | 19.0% |

### Cycle 6b (min_conf=20%, correct v0.6.2 settings)

| Metric | Value |
|--------|-------|
| P&L | +45,035% |
| Win Rate | 90.2% (156W/17L) |
| Sharpe | 20.93 |
| Max DD | 3.7% |
| Profit Factor | 15.88 |
| Trades | 173 |
| SHORT trades | 2 |
| Avg Confidence | 22.2% |

### Cross-Cycle Comparison

| Cycle | P&L | WR | Sharpe | Max DD | Bootstrap | Notes |
|-------|-----|-----|--------|--------|-----------|-------|
| 1 | +4401% | 54.0% | — | — | Yes | Baseline v0.5.0 |
| 2 | +465% | 47.6% | — | — | Yes | After improvements |
| 3 | -18.77% | ~33% | — | — | No | No bootstrap = collapse |
| 4 | +1434% | 50.5% | 2.27 | — | Yes | Bootstrap confirmed critical |
| 5 | +86.82% | 44.6% | 1.18 | 41.3% | Yes | v0.6.0 prob bonus bug |
| **6a** | **+155,150%** | **89.2%** | **19.96** | **8.3%** | Yes | v0.6.2, min_conf=15% |
| **6b** | **+45,035%** | **90.2%** | **20.93** | **3.7%** | Yes | v0.6.2, min_conf=20% |

## Key Findings

1. **Removing probability bonus was the biggest win** — it eliminated low-confidence trades that had 32.6% WR
2. **Bootstrap improvement (805 vs 391 observations)** — removing bootstrap prob bonus led to better trie quality
3. **SHORT diversification works** — 2-3 SHORT trades with positive expected value
4. **Catastrophic protection at 8%** — triggered once (trade #27: -8.27%), preventing a larger loss
5. **Min confidence 20% > 15%** — better risk-adjusted returns (Sharpe 20.93 vs 19.96, Max DD 3.7% vs 8.3%)

## Caveats & Concerns

1. **In-sample testing** — trie is built on the same data it trades on, inflating results
2. **Compounding exaggeration** — 173+ trades with 90% WR compounds dramatically
3. **Need out-of-sample validation** — build on 70% of data, trade on 30%
4. **Few SHORT trades** — only 2-3 per run, need more data to validate SHORT edge

## Risk Parameters (v0.6.2)

- LONG SL: max(ATR*1.5, 1.5%) cap 5%
- SHORT SL: max(ATR*2.0, 2.0%) cap 7%
- Trailing: 75% TP
- Pattern break grace: 2
- Re-entry cooldown: 1
- Catastrophic protection: 8.0%
- Min confidence: 20%
- Probability threshold: >20%

## Living Trie Configuration

- AdaptiveWeights: N1=5%, N2=20%, N3=35%, N4=40%
- Profile: blue_chip
- Entry: Min confidence 20%, probability >20%, move >1.0%
- SHORT effective min confidence: max(0.20*1.2, 0.20) = 0.24 (24%)

## Recommended Next Steps (Post-Cycle 6)

### P0 — Critical
- **Out-of-sample validation**: Build on 70% of data, paper trade on 30%
- **Monte Carlo simulation**: Run `ppmt monte-carlo` to validate robustness

### P1 — High Priority
- **Walk-forward analysis**: Rolling window validation
- **More bootstrap passes**: Try 3-4 passes to see if trie quality plateaus

### P2 — Medium Priority
- **Adaptive min_confidence**: Dynamically adjust based on recent WR
- **SHORT gate tuning**: More data needed to determine optimal multiplier
- **Multi-symbol validation**: Test on ETH/USDT, SOL/USDT
