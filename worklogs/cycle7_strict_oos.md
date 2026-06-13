# Cycle 7 Analysis — PPMT v0.6.3

**Date**: 2026-06-11
**Version**: v0.6.3
**Symbol**: BTC/USDT (1h)

## v0.6.3 Changes

1. **SAX z-score propagation fix** (V7.9 backport from V9/V10 codebase)
   - Added `encode_with_normalization()` to SAXEncoder
   - Added `symbols` parameter to `PPMT.build()` for pre-computed symbols
   - Added `paa_mean` / `paa_std` to PaperTraderConfig
   - Added `--paa-mean` / `--paa-std` CLI options
2. **Strict walk-forward script** — builds trie on training data ONLY, no look-ahead

## Critical Finding: Look-Ahead Bias Confirmed

### Previous Results (Partial OOS — trie built on ALL data)
- P0 OOS (70/30): +295% P&L, 91.1% WR, Sharpe 23.83
- P2 Walk-forward (6 windows): 6/6 profitable, avg +4.28%

### Strict OOS (trie built on TRAINING data ONLY)
- 40000 training candles, 5000 test candles, min_conf=15%
- **Result: -6.39% P&L, 31.2% WR, Sharpe -4.07, Max DD 6.4%**
- **16 trades, only 5 profitable**

### Why the Difference?

The trie built on ALL data (including test period) has patterns that encode
future price movements. When the paper trader encounters these patterns
during the test period, the trie's metadata already "knows" what happened
next — this is look-ahead bias.

With strict OOS (trie built on training data only), the trie has:
- Fewer observations (370 vs 805-1200)
- No knowledge of test-period patterns
- Prediction confidence too low → most signals rejected at 20% threshold
- When signals do pass (with 15% threshold), WR drops to 31.2%

### Root Cause Analysis

1. **Bootstrap needs ALL data to be effective**: The 2-pass bootstrap on
   full data generates 805 observations with 50.9% WR. On training-only
   data (40000 candles), it generates only 370 observations.

2. **SAX normalization mismatch is PARTIALLY solved**: The V7.9 fix
   propagates training z-score stats to test encoding, which produces
   consistent symbols. But the trie still has fewer patterns to match.

3. **The system is NOT genuinely predictive** — it relies on having seen
   similar patterns before. When it encounters truly novel patterns in
   the test period, it either can't match them or matches them poorly.

4. **The 90.2% WR was an artifact** of look-ahead bias in the trie.
   The system appears to be curve-fitted to the historical data.

## Honest Assessment

| Scenario | P&L | WR | Verdict |
|----------|-----|-----|---------|
| In-sample (all data) | +45,035% | 90.2% | Look-ahead bias |
| Partial OOS (trie=all, trade=30%) | +295% | 91.1% | Partial look-ahead |
| Walk-forward (trie=all, windows) | +4.28%/win | 67.9% | Partial look-ahead |
| **Strict OOS (trie=train only)** | **-6.39%** | **31.2%** | **No predictive power** |

## Implications

The PPMT system in its current form (v0.6.x) is NOT suitable for live
trading. The high win rates are an artifact of the trie having seen
future data during construction.

To make the system genuinely predictive, we need:
1. **Much larger training datasets** (100K+ candles) so the trie sees
   enough patterns to generalize
2. **Regime detection** — adapt trie matching to current market conditions
3. **Fuzzy matching** — allow approximate pattern matches instead of exact
4. **Online learning** — the Living Trie should be the PRIMARY mechanism,
   not the pre-built trie
5. **Lower confidence thresholds** — accept more trades with lower
   individual quality, relying on portfolio-level edge

## Next Steps (Revised)

### P0 — System Redesign Required
The current architecture (build trie on historical data → match patterns)
has a fundamental look-ahead bias problem. We need to redesign around:
1. **Live-only trie building** — start with empty trie, only add patterns
   from real-time observations (no historical backfill)
2. **Walk-forward optimization** — continuously rebuild trie on rolling
   window of most recent N candles
3. **Ensemble approach** — multiple tries with different window sizes

### P1 — If Keeping Current Architecture
1. **Lower min_confidence to 10%** for strict OOS to get more trades
2. **Increase training window** to 90% of data, test on 10%
3. **More bootstrap passes** (5-10) to increase observation density
4. **Fuzzy matching** — allow patterns within edit distance 1

### P2 — Alternative Approach
1. **Pure Living Trie mode** — build with bootstrap, then ONLY use
   Living Trie observations for predictions (ignore pre-built metadata)
2. **Confidence weighting** — weight recent Living Trie observations
   more heavily than old bootstrap observations
