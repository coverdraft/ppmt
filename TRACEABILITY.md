# PPMT v0.6.2 — TRACEABILITY DOCUMENT

> Last updated: 2026-06-14
> Data source: Binance real data (BTC, ETH, SOL, BNB, XRP, ADA, LINK, UNI, ATOM, DOGE, SHIB, PEPE)
> Timeframes tested: 1h, 5m, 1m
> OHLCV composite: ADDITIVE formula (v0.6.2 fix applied)

---

## 1. CRITICAL DISCOVERY: OHLCV vs Close Overlap

### Problem Statement
The OHLCV SAX strategy produces **1.01x overlap** (each pattern is unique), while the "close" strategy produces **13.85x overlap** (patterns repeat ~14x on average). The question: Is the 13.85x overlap correct for PPMT, or is it a bug?

### Root Cause Analysis

| Metric | OHLCV (alpha=10) | Close (alpha=10) |
|--------|------------------|-------------------|
| Unique patterns | 1,980 | 144 |
| Overlap ratio | 1.01x | 13.85x |
| Mean observations/pattern | 1.0 | 13.9 |
| Patterns with count>=5 | 0 (0%) | 53 (36.8%) |
| Patterns with count=1 | 1,965 (99.2%) | 53 (36.8%) |
| Mean confidence | 0.159 | 0.223 |
| **OOS match rate** | **2.0%** | **95.9%** |
| **OOS exact break rate** | **98.0%** | **4.1%** |

### Key Finding: OHLCV with alpha=10 is BROKEN for Prediction

The OHLCV strategy with `alphabet_size=10` and `window_size=5` produces **near-zero overlap**, meaning each 5-symbol pattern is essentially unique. This causes:

1. **98% OOS miss rate** — When paper trading encounters a new pattern, it almost never matches anything in the trie
2. **Unreliable metadata** — 99.2% of patterns have only 1 observation, making win_rate/confidence meaningless (0% or 100%)
3. **No pattern breaks** — Since patterns never match, the pattern break detection mechanism is useless
4. **No predictive power** — The trie cannot predict because it never sees the same pattern twice

The "close" strategy works better for matching (95.9% OOS match) but **loses critical candlestick information** (body size, wicks, volume, direction strength).

### Solution: OHLCV with Smaller Alphabet

Tested OHLCV with different alphabet sizes using 8,000 candles for training, 2,000 for OOS testing:

| Config | Overlap | OOS Match% | Break% | Avg Confidence | Avg WR | Avg Hist Count |
|--------|---------|-----------|--------|---------------|--------|---------------|
| **ohlcv/a3** | **6.56x** | **100.0%** | **0.0%** | **0.268** | **51.7%** | **6.6** |
| ohlcv/a4 | 2.00x | 79.2% | 20.8% | 0.192 | 48.7% | 2.1 |
| ohlcv/a5 | 1.29x | 39.0% | 61.0% | 0.167 | 47.6% | 1.2 |
| ohlcv/a8 | 1.02x | 7.8% | 92.2% | 0.159 | 48.4% | 1.0 |
| ohlcv/a10 | 1.01x | 2.0% | 98.0% | 0.151 | 25.0% | 1.0 |
| close/a10 | 10.22x | 95.9% | 4.1% | 0.344 | 46.4% | 104.8 |

### RECOMMENDATION

**Change default `sax_alphabet_size` from 10 to 3 or 4 when using OHLCV strategy.**

- **ohlcv/a3**: Best overall — 100% match rate, 6.56x overlap, mean 6.6 observations per pattern, 51.7% WR (above random). Maps to 3 clear market states: bearish / neutral / bullish.
- **ohlcv/a4**: Good balance — 79.2% match rate, 2.0x overlap. Maps to: strong_bearish / weak_bearish / weak_bullish / strong_bullish.

The current default (ohlcv/a10) is effectively broken because 10 symbols with the OHLCV composite creates too much diversity for the trie to find repeating patterns.

### Why the 13.85x "Close" Overlap is NOT Correct Either

The close strategy's 13.85x overlap comes from **information loss**, not from correct pattern grouping. It collapses all candlestick information into a single close price, losing:
- Candle body size (open vs close gap)
- Wicks (high/low extremes)
- Volume (market participation)
- Direction strength (body center x direction)

This is why close/a10 has higher match rate but lower win rate (46.4%) compared to ohlcv/a3 (51.7%).

---

## 2. CRITICAL FIX: OHLCV Composite Formula (v0.6.2)

### Problem: Degenerate Multiplicative Composite

The original OHLCV composite formula was:
```python
composite = body_center * direction * (0.5 + 0.5 * vol_ratio)
```

This formula is **degenerate** because when `direction ≈ 0` (doji candle, which is very common), the entire composite collapses to ≈0 regardless of body_position or volume. This means:
- All doji-like candles map to the same SAX symbol
- The direction component dominates and suppresses other features
- Volume information is completely lost when direction is near zero

### Fix: Additive Composite

New formula preserves all features independently:
```python
body_position = body_center  # [0, 1]
vol_signal = (vol_ratio - 0.5) / 1.5  # centered [0, 1]
composite = body_position * 0.4 + direction * 0.35 + vol_signal * 0.25
```

Range: [-0.35, 1.0] — bounded, non-degenerate, preserves all three components.

### Impact on Results

With the additive composite, results are **more realistic** (lower PnL) than the old multiplicative formula which produced inflated results due to artificial overlap from the degeneracy. The additive formula produces genuinely independent features and the trie matches reflect true pattern similarity rather than coincidence from collapsed values.

- **File**: `ppmt/src/ppmt/core/sax.py` line 137-148
- **Status**: FIXED (2026-06-14)

---

## 3. Multi-Timeframe Validation Results (v0.6.2, Additive Composite)

### Test Configuration
- **Tokens**: BTC/USDT (blue_chip), SOL/USDT (large_cap), DOGE/USDT (meme)
- **Timeframes**: 1h (600 days), 5m (40 days), 1m (8 days)
- **SAX**: alpha=3, window=5, strategy=ohlcv (additive composite)
- **Train/Test split**: 80%/20%
- **Monte Carlo**: 300 simulations per test
- **Data**: Binance real candles ONLY

### Results Table

| Token | TF | Trades | Long/Short | Win Rate | PnL% | PF | MaxDD% | MC% |
|-------|-----|--------|------------|----------|------|------|--------|-----|
| BTC | 1h | 101 | 46/55 | 72.3% | +22.79% | 1.54 | 11.55% | 92% |
| SOL | 1h | 135 | 53/82 | 63.7% | -7.02% | 0.94 | 20.37% | 40% |
| DOGE | 1h | 112 | 45/67 | 66.1% | +14.71% | 1.17 | 24.64% | 70% |
| BTC | 5m | 106 | 36/70 | 49.1% | -4.98% | 0.79 | 7.66% | 20% |
| SOL | 5m | 108 | 37/71 | 62.0% | +12.72% | 1.49 | 6.62% | 94% |
| DOGE | 5m | 103 | 37/66 | 54.4% | +10.73% | 1.45 | 6.44% | 87% |
| BTC | 1m | 18 | 13/5 | 61.1% | +0.52% | 1.19 | 1.65% | 62% |
| SOL | 1m | 37 | 20/17 | 67.6% | +4.03% | 1.86 | 1.85% | 91% |
| DOGE | 1m | 47 | 25/22 | 48.9% | -2.82% | 0.75 | 6.75% | 22% |

### Timeframe Averages

| TF | Avg Trades | Avg Win Rate | Avg PnL% | Profitable Tokens |
|----|-----------|-------------|----------|-------------------|
| 1h | 116.0 | 67.4% | +10.16% | 2/3 |
| 5m | 105.7 | 55.2% | +6.15% | 2/3 |
| 1m | 34.0 | 59.2% | +0.58% | 2/3 |

### Key Findings

1. **1h remains the strongest timeframe** on average (+10.16% avg PnL), but SOL is negative (-7.02%) — suggesting the additive composite needs calibration adjustment for some tokens.

2. **5m shows promise** — SOL (+12.72%) and DOGE (+10.73%) are strong at 5m with excellent MC (94%/87%). However, BTC at 5m is negative (-4.98%), indicating blue chips may not suit 5m.

3. **1m has too few trades** — only 18-47 trades across tokens due to limited training data (8 days). SOL at 1m is promising (+4.03%, 91% MC) but needs more data to be conclusive.

4. **Trade count is consistent** — ~100-135 trades per token at 1h and 5m. At 1m, much fewer trades (18-47) because patterns repeat less in very short timeframes.

5. **Exit reasons differ by timeframe** — 1h has more stop_loss exits (60-86) vs 5m (70-79) vs 1m (8-30). 1h has more pattern_break exits (24-25) vs 5m (3) vs 1m (0-3). This suggests 1h patterns are more "breakable" (more diverse continuations) while 5m patterns are simpler (hit SL or TP).

6. **Short dominance at 5m** — BTC 5m has 36L/70S, SOL 5m has 37L/71S, DOGE 5m has 37L/66S. This suggests the system is finding more bearish patterns at 5m, which may reflect recent market conditions or a structural bias in the data period.

7. **Additive composite produces realistic results** — previous massive_validation with old multiplicative formula showed +511% avg PnL (inflated). New additive formula shows +10.16% at 1h, which is more realistic and trustworthy.

### Data Volume Notes

| TF | Days | Candles | SAX Blocks (train) | SAX Blocks (test) |
|----|------|---------|--------------------|--------------------|
| 1h | 600 | 14,400 | 2,304 | 576 |
| 5m | 40 | 11,520 | 1,843 | 460 |
| 1m | 8 | 11,520 | 1,843 | 460 |

1m with 8 days of data means the training set covers only ~6.4 days of market history. This is insufficient for reliable pattern discovery. To get equivalent training data to 1h (2,304 SAX blocks ≈ 11,520 candles), 1m would need ~8 days total, but the market regime in those 8 days may not be representative.

---

## 4. Node Block Matching: Exact vs Similar

### Current State
- `paper_trader.py` uses **exact matching** for pattern break detection: `trie.check_continuation()`
- `PPMT.match()` uses **FuzzyMatcher** for entry signals: `matcher.best_match()`
- This mismatch means entry signals can be fuzzy but exits are exact

### Fuzzy Continuation Impact (OOS)

| Strategy | Exact Break% | Fuzzy Break% | Reduction |
|----------|-------------|-------------|-----------|
| ohlcv/a10 | 98.0% | 93.4% | 4.7% |
| close/a10 | 4.1% | 4.1% | 0.0% |

Fuzzy matching provides minimal improvement when overlap is already very low. The real fix is increasing overlap through alphabet size, not relying on fuzzy matching alone.

### Recommendation
- Use `FuzzyMatcher.check_continuation()` in paper_trader instead of `trie.check_continuation()`
- This provides a safety net for similar patterns, even with better alphabet sizing
- Keep threshold at 0.85 (current default)

---

## 5. Trie Fixes Applied (v0.6.2)

### 5.1 Missing `trading_observations` attribute
- **File**: `src/ppmt/core/trie.py`
- **Fix**: Added `self.trading_observations: int = 0` to `__init__`
- **Status**: FIXED

### 5.2 Missing `propagate_metadata()` method
- **File**: `src/ppmt/core/trie.py`
- **Fix**: Added `propagate_metadata()` and `_propagate_node()` methods with recursive aggregation
- **Status**: FIXED

### 5.3 Missing serialization of `trading_observations`
- **File**: `src/ppmt/core/trie.py`
- **Fix**: Updated both `to_dict()` and `from_dict()` methods
- **Status**: FIXED

### 5.4 OHLCV composite degenerate formula
- **File**: `ppmt/src/ppmt/core/sax.py` line 137-148
- **Old**: `body_center * direction * (0.5 + 0.5 * vol_ratio)` — collapses when direction≈0
- **New**: `body_position * 0.4 + direction * 0.35 + vol_signal * 0.25` — additive, preserves all features
- **Status**: FIXED (2026-06-14)

---

## 6. Massive Multi-Token Validation (1h, OLD multiplicative composite)

> **WARNING**: These results used the OLD degenerative multiplicative composite formula.
> They are NOT directly comparable with Section 3 results (additive composite).
> These results should be re-run with the new formula.

### OOS Trading Results (12 tokens, alpha=3, window=5)

| Token | Asset Class | Trades | Win Rate | PnL% | PF | MaxDD% | MC% |
|-------|-------------|--------|----------|------|------|--------|-----|
| BTC | blue_chip | 150 | 86.7% | +291.1% | 6.44 | 6.7% | 100% |
| ETH | blue_chip | 106 | 84.0% | +172.9% | 3.38 | 12.5% | 100% |
| SOL | large_cap | 143 | 90.2% | +751.3% | 11.68 | 14.2% | 100% |
| BNB | large_cap | 120 | 92.5% | +313.7% | 11.47 | 7.0% | 100% |
| XRP | large_cap | 111 | 83.8% | +318.8% | 4.54 | 16.3% | 100% |
| ADA | large_cap | 132 | 85.6% | +541.8% | 6.55 | 7.6% | 100% |
| LINK | defi | 131 | 89.3% | +515.2% | 8.15 | 13.3% | 100% |
| UNI | defi | 131 | 87.8% | +488.3% | 6.21 | 12.8% | 100% |
| ATOM | defi | 143 | 88.8% | +626.6% | 8.61 | 12.1% | 100% |
| DOGE | meme | 163 | 90.2% | +774.2% | 11.69 | 9.9% | 100% |
| SHIB | meme | 143 | 90.2% | +686.4% | 11.00 | 12.3% | 100% |
| PEPE | meme | 134 | 85.8% | +654.6% | 6.37 | 28.6% | 100% |

### Asset Class Averages (OLD formula)

| Class | Avg PnL% | Avg Win Rate | Avg PF |
|-------|----------|-------------|--------|
| blue_chip | +232.0% | 85.3% | 4.91 |
| large_cap | +481.4% | 88.0% | 8.56 |
| defi | +543.4% | 88.6% | 7.66 |
| meme | +705.1% | 88.7% | 9.69 |

---

## 7. Known Issues NOT Yet Fixed

### 7.1 SOL negative at 1h with additive composite
- **Severity**: HIGH
- **Impact**: SOL/USDT is -7.02% at 1h with the new additive formula
- **Root cause**: The additive formula changes symbol distribution; SOL may need different alpha/window
- **Fix needed**: Per-token calibration with trading PnL in the metric
- **Status**: NOT FIXED

### 7.2 Calibration metric convergence (all tokens → alpha=3/window=5)
- **Severity**: MEDIUM
- **Impact**: Calibration doesn't differentiate tokens; SOL might be better at alpha=4/window=7
- **Root cause**: Metric's `repetition` component favors lower alpha
- **Fix needed**: Incorporate OOS trading PnL into calibration metric
- **Status**: NOT FIXED

### 7.3 regime.py in wrong location
- **File**: Only exists in `ppmt/ppmt/src/ppmt/core/regime.py` (nested duplicate)
- **Fix needed**: Copy to primary source tree `src/ppmt/core/regime.py`
- **Status**: NOT FIXED

### 7.4 BlockLifecycleMetadata lacks regime tracking
- **File**: `src/ppmt/core/metadata.py`
- **Fix needed**: Add `regime: str = ""` field
- **Status**: NOT FIXED

### 7.5 SHORT confidence gate is weak
- **File**: `src/ppmt/engine/paper_trader.py`
- **Current**: `effective_min_conf = max(effective_min_conf * 1.2, 0.10)`
- **Fix needed**: Make it regime-aware
- **Status**: NOT FIXED

### 7.6 catastrophic_loss_pct disabled
- **File**: `src/ppmt/engine/paper_trader.py`
- **Current**: `catastrophic_loss_pct: float = 0.0`
- **Fix needed**: Re-enable with 8% as safety net
- **Status**: NOT FIXED

### 7.7 Pattern break uses exact matching
- **File**: `src/ppmt/engine/paper_trader.py`
- **Current**: `trie.check_continuation()` — exact match
- **Fix needed**: Use `FuzzyMatcher.check_continuation()` for noise tolerance
- **Status**: NOT FIXED

### 7.8 1m timeframe insufficient data
- **Severity**: LOW (informational)
- **Impact**: Only 8 days of 1m data available → 18-47 trades → inconclusive
- **Fix needed**: Would need to accumulate more 1m data over time
- **Status**: NOT APPLICABLE (data limitation)

---

## 8. Data Source Verification

All analysis in this document uses **real Binance data**:

### 1h timeframe (600 days)
- Candles: 14,400 per token
- Period: 2024-10-21 to 2026-06-13
- Tokens: BTC, ETH, SOL, BNB, XRP, ADA, LINK, UNI, ATOM, DOGE, SHIB, PEPE
- Source: Binance API (klines endpoint)
- Storage: Local SQLite at `~/.ppmt/ppmt.db`
- No synthetic/mock data was used

### 5m timeframe (40 days)
- Candles: 11,520 per token
- Period: 2026-05-04 to 2026-06-13
- Tokens: BTC, SOL, DOGE

### 1m timeframe (8 days)
- Candles: 11,520 per token
- Period: 2026-06-05 to 2026-06-13
- Tokens: BTC, SOL, DOGE

---

## 9. Historical Decision Log

| Version | Decision | Rationale | Result |
|---------|----------|-----------|--------|
| v0.2.8 | No catastrophic protection | Let trades breathe | +1578% P&L |
| v0.2.9 | Pattern break grace=2 | Avoid noise exits | Improved stability |
| v0.2.10 | Catastrophic 5%, tight trailing | "Improve" risk management | P&L dropped to +371% |
| v0.3.0 | Reverted to v0.2.8 baseline | v0.2.10 was worse | P&L recovered |
| v0.4.1 | min_confidence=0.15 | Filter low-quality signals | P&L dropped from +3665% to +347% |
| v0.4.2 | Reverted min_confidence=0.10 | v0.4.1 was worse | P&L recovered |
| v0.5.2 | SHORT gate 1.2x (was 1.5x) | Balance LONG/SHORT distribution | More balanced |
| v0.6.2a | OHLCV alpha=3-4 recommended | alpha=10 has 98% miss rate | Applied |
| v0.6.2b | 12-token massive validation | Cross-token OOS proof | 12/12 profitable (OLD formula) |
| **v0.6.2c** | **Fix OHLCV composite to additive** | **Multiplicative degenerate when dir≈0** | **More realistic PnL (+10.16% avg vs +511%)** |
| **v0.6.2c** | **Multi-timeframe test (5m, 1m)** | **Check lower TF quality** | **1h best avg, 5m promising for non-BTC, 1m too few trades** |

**Key Pattern**: Every time we tried to "improve" filtering (higher thresholds, tighter stops), it made results worse. The system works best with LOW thresholds and HIGH data quality. The real fix is always better data/encoding, not stricter filters.

---

## 10. Test Infrastructure Status

- Integration tests exist for SAX, Trie, Matcher, Encoder
- OOS validation framework operational (multi-token, multi-timeframe)
- Walk-forward validation operational (12 tokens)
- Monte Carlo simulation operational (300-1000 sims)
- Cross-token validation operational (12 tokens, 4 asset classes)

---

## 11. Next Steps (Priority Order)

1. **Re-run massive_validation with additive composite** — The 12-token results in Section 6 used the OLD formula and need updating. Expect more realistic (lower) PnL numbers.

2. **Improve calibration metric** — Incorporate OOS trading PnL into the calibration metric so it doesn't always converge to alpha=3/window=5. SOL may benefit from different parameters.

3. **5m optimization for non-BTC tokens** — SOL and DOGE show strong 5m performance. Consider per-token per-timeframe calibration.

4. **Integrate TokenProfile into paper_trader.py** — Use profile for shorts, catastrophic loss, etc.

5. **Re-enable catastrophic_loss_pct** — 8% hard stop from TokenProfile.

6. **Fuzzy pattern break** — Replace exact matching with FuzzyMatcher.check_continuation().

7. **BlockLifecycleMetadata regime field** — Add `regime: str` for N4 trie support.

8. **Living recalibration** — Auto-re-calibrate every N new candles.

9. **1m data accumulation** — Store 1m data over time to build larger training sets for future testing.
