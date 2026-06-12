# PPMT v0.6.2 — TRACEABILITY DOCUMENT

> Last updated: 2026-06-12
> Data source: Binance BTC/USDT 1h (10,000 real candles)

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

## 2. Node Block Matching: Exact vs Similar

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

## 3. Trie Fixes Applied (v0.6.2)

### 3.1 Missing `trading_observations` attribute
- **File**: `src/ppmt/core/trie.py`
- **Problem**: `paper_trader.py` references `trie.trading_observations` but `PPMTTrie.__init__` didn't have it
- **Fix**: Added `self.trading_observations: int = 0` to `__init__`
- **Status**: FIXED

### 3.2 Missing `propagate_metadata()` method
- **File**: `src/ppmt/core/trie.py`
- **Problem**: `paper_trader.py` calls `trie.propagate_metadata()` but `PPMTTrie` didn't have it
- **Fix**: Added `propagate_metadata()` and `_propagate_node()` methods with recursive aggregation
- **Aggregation logic**: Leaf nodes return own metadata; internal nodes aggregate children with weighted averages; own observations take precedence
- **Status**: FIXED

### 3.3 Missing serialization of `trading_observations`
- **File**: `src/ppmt/core/trie.py`
- **Problem**: `to_dict()` and `from_dict()` didn't handle `trading_observations`
- **Fix**: Updated both methods to include the field
- **Status**: FIXED

---

## 4. Known Issues NOT Yet Fixed

### 4.1 SAX alphabet_size mismatch with OHLCV strategy
- **Severity**: CRITICAL
- **Impact**: 98% OOS pattern miss rate with current defaults
- **Fix needed**: Change default `sax_alphabet_size` from 10 to 3-4 for OHLCV
- **Status**: NOT FIXED — awaiting user decision on alpha=3 vs alpha=4

### 4.2 regime.py in wrong location
- **File**: Only exists in `ppmt/ppmt/src/ppmt/core/regime.py` (nested duplicate)
- **Fix needed**: Copy to primary source tree `src/ppmt/core/regime.py`
- **Status**: NOT FIXED

### 4.3 BlockLifecycleMetadata lacks regime tracking
- **File**: `src/ppmt/core/metadata.py`
- **Problem**: No regime field (trending_up, ranging, trending_down, volatile)
- **Fix needed**: Add `regime: str = ""` field
- **Status**: NOT FIXED

### 4.4 SHORT confidence gate is weak
- **File**: `src/ppmt/engine/paper_trader.py` line 908-909
- **Current**: `effective_min_conf = max(effective_min_conf * 1.2, 0.10)`
- **Problem**: 1.2x multiplier barely filters; floor of 0.10 is too low
- **Fix needed**: Make it regime-aware (stricter in trending_up, looser in trending_down)
- **Status**: NOT FIXED

### 4.5 catastrophic_loss_pct disabled
- **File**: `src/ppmt/engine/paper_trader.py` line 277
- **Current**: `catastrophic_loss_pct: float = 0.0`
- **Problem**: No hard stop for extreme losses
- **Fix needed**: Re-enable with 8% as safety net
- **Status**: NOT FIXED

### 4.6 Pattern break uses exact matching
- **File**: `src/ppmt/engine/paper_trader.py` line 819
- **Current**: `continues, _ = trie.check_continuation(pattern_to_check, latest_symbol)` — exact match
- **Fix needed**: Use FuzzyMatcher.check_continuation() for noise tolerance
- **Status**: NOT FIXED

---

## 5. Data Source Verification

All analysis in this document uses **real Binance data**:
- Symbol: BTC/USDT
- Timeframe: 1h
- Candles: 10,000 (2025-04-21 to 2026-06-12)
- Price range: $59,396 - $126,011
- Source: Binance API (klines endpoint)
- Storage: Local SQLite at `~/.ppmt/ppmt.db`
- No synthetic/mock data was used

---

## 6. Historical Decision Log

| Version | Decision | Rationale | Result |
|---------|----------|-----------|--------|
| v0.2.8 | No catastrophic protection | Let trades breathe | +1578% P&L |
| v0.2.9 | Pattern break grace=2 | Avoid noise exits | Improved stability |
| v0.2.10 | Catastrophic 5%, tight trailing | "Improve" risk management | P&L dropped to +371% |
| v0.3.0 | Reverted to v0.2.8 baseline | v0.2.10 was worse | P&L recovered |
| v0.4.1 | min_confidence=0.15 | Filter low-quality signals | P&L dropped from +3665% to +347% |
| v0.4.2 | Reverted min_confidence=0.10 | v0.4.1 was worse | P&L recovered |
| v0.5.2 | SHORT gate 1.2x (was 1.5x) | Balance LONG/SHORT distribution | More balanced |
| **v0.6.2** | **OHLCV alpha=3-4 recommended** | **alpha=10 has 98% miss rate** | **Pending user decision** |

**Key Pattern**: Every time we tried to "improve" filtering (higher thresholds, tighter stops), it made results worse. The system works best with LOW thresholds and HIGH data quality. The real fix is always better data/encoding, not stricter filters.

---

## 7. Test Infrastructure Status

- **No tests exist yet** in the `tests/` directory for the PPMT engine
- All current analysis was done via ad-hoc diagnostic scripts
- Need: Integration tests using real Binance data
- Need: OOS validation framework (build on 80%, test on 20%)
- Need: Cross-token validation (BTC, ETH, SOL, etc.)
