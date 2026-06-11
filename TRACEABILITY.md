# PPMT Traceability Document

> Single source of truth for all PPMT development. This document tracks every change, decision, and rationale.

---

## Project: PPMT — Pattern Prediction Market Trader

**Current Version**: v0.10.0 (base) → V4.3 robustness fixes & SHORT gate redesign
**Branch**: main
**Last Updated**: 2026-06-11  

---

## Version History & Changes

### V4.3 — SHORT Gate Redesign & Robustness Fixes (2026-06-11)

**Problem**: Two critical issues found in paper_trader.py:
1. `historical_count=100` was STILL hardcoded in Signal creation at line 1097, despite the V4.2 attempt to fix it. The V4.2 fix only corrected the `mock_meta` for sizing, but `Signal.compute_quality_score()` and `Signal.compute_sizing_multiplier()` had already been called with the wrong count=100. This meant rare patterns (3 observations) got the same quality score as well-observed patterns (100+ observations), distorting position sizing.
2. SHORT gate used a fixed 1.2x multiplier regardless of market regime. In trending_up markets, SHORTs fighting the trend need stricter gating; in trending_down, SHORTs are favorable and the fixed 1.2x was unnecessarily restrictive.

**Changes Made**:

| File | Change | Rationale |
|------|--------|-----------|
| `ppmt/src/ppmt/engine/paper_trader.py` | Move Trie node lookup BEFORE Signal creation | Historical count is now correct from the moment Signal is created, so `compute_quality_score()` uses the real count. Default changed from 100 to 10 (conservative). |
| `ppmt/src/ppmt/engine/paper_trader.py` | Replace fixed SHORT 1.2x multiplier with regime-aware gate | trending_down: 0.85x (favored), ranging: 1.1x (cautious), trending_up: 1.5x (fighting trend), volatile: 1.8x (dangerous). Floor of 0.20 always applies. |
| `ppmt/tests/test_v43_robust.py` | Created 49 robust, non-distorting tests | 10 test classes covering: SHORT gate, historical count, regime propagation, node classification, confidence invariants, regime match score, move variance, SAX consistency, full pipeline, and anti-distortion checks. |

**SHORT Gate Design**:
```
Regime           Multiplier  Effect (with min_conf=0.20)
trending_down    0.85x       0.17 → floored to 0.20 (easier SHORT entry)
ranging          1.1x        0.22 (slight caution)
trending_up      1.5x        0.30 (strict — fighting the trend)
volatile         1.8x        0.36 (very strict — dangerous)
```

**Key Design Decisions**:
1. **Conservative default count (10, not 100)**: When no Trie node is found, a count of 10 produces moderate Bayesian shrinkage, preventing overconfident sizing on unknown patterns.
2. **Regime-aware SHORT gate**: Different regimes have fundamentally different risk profiles for SHORT trades. A fixed multiplier was either too strict (eliminating all SHORTs in downtrends) or too lenient (allowing SHORTs in strong uptrends).
3. **Anti-distortion tests**: New test class specifically checks that the system doesn't produce misleading results — random data shouldn't produce high confidence, single trades shouldn't produce high confidence, and propagation shouldn't inflate counts.

**Tests**: 49 new V4.3 tests, ALL PASSING. Total: 171+ tests across all test files.

**Metadata Audit Results**:

The V4.3 metadata audit confirmed that node metadata is NOT corrupt. The current structure is comprehensive:

| Metadata Field | Version | Purpose |
|----------------|---------|---------|
| `trigger_candle`, `remaining_candles` | V3 | Entry/exit timing |
| `expected_move_pct`, `max_drawdown_pct`, `max_favorable_pct` | V3 | Price prediction & risk |
| `win_rate`, `avg_duration`, `historical_count` | V3 | Historical statistics |
| `sl_price`, `tp_price` | V3 | Dynamic SL/TP |
| `continuation_nodes`, `break_nodes` | V3 | Forward/backward navigation |
| `confidence`, `probability_of_success`, `sizing_signal` | V3 | Computed properties for RiskManager |
| `regime`, `regime_confidence`, `dominant_regime` | V4 | Regime awareness |
| `regime_distribution` | V4 | Regime frequency histogram |
| `regime_stats` (RegimeStats) | V4.1 | Per-regime win_rate and expected_move |
| `move_variance`, `move_mean_for_variance` | V4.1 | Welford's online variance |
| `move_std`, `move_coefficient_of_variation` | V4.1 | Pattern reliability metrics |
| `node_type` (independent/dependent) | V4 | Node classification with confidence penalty |
| `min_independent_count` | V4 | Threshold for independent classification |
| `last_observation_time` | V4.2 | Freshness tracking |
| `observation_timespan` | V4.2 | Robustness metric |
| `freshness_decay` | V4.2 | Time-based decay (7-day half-life) |
| `observation_density` | V4.2 | Observations per day |

**Recommendation for future metadata additions**:
- Direction-specific stats (LONG win_rate vs SHORT win_rate per node) — useful for assets that trend strongly
- Regime transition tracking (what regime follows what) — for predictive regime shifts
- Time-of-day patterns — for intraday strategies

---

### V4.2 — Metadata Audit Fixes & Observation Freshness (2026-06-11)

**Problem**: Full audit of node metadata revealed 6 critical issues:
1. `current_regime` was NOT passed to `pred_engine.predict()` in paper_trader.py — regime detection had zero effect on actual trading decisions
2. N4 Trie was a duplicate of N3 — it received all patterns regardless of regime, making it useless for regime-specific matching
3. No observation freshness/decay mechanism — patterns from 5000 candles ago had the same weight as recent ones
4. `historical_count=100` was hardcoded in signal sizing — inflating confidence for rarely-observed patterns
5. `max_drawdown_pct` inconsistency — stored as negative but used with abs() inconsistently (not fixed, documented)
6. No `last_observation_time` — impossible to distinguish active from stale patterns

**Changes Made**:

| File | Change | Rationale |
|------|--------|-----------|
| `ppmt/src/ppmt/engine/paper_trader.py` | Pass `current_regime` to `pred_engine.predict()` | PredictionEngine had regime-aware confidence since V4.1, but paper_trader never used it. Now prediction confidence is regime-adjusted. |
| `ppmt/src/ppmt/engine/paper_trader.py` | Add regime-aware threshold adjustment via `regime_match_score()` | Favorable regimes lower the entry threshold (easier to enter), unfavorable regimes raise it (harder to enter). This makes regime detection actually affect trading decisions. |
| `ppmt/src/ppmt/engine/paper_trader.py` | Replace hardcoded `historical_count=100` with real node count | The Bayesian shrinkage in `probability_of_success` and `sizing_signal` now uses the actual sample size, preventing overconfident sizing on rarely-observed patterns. |
| `ppmt/src/ppmt/engine/ppmt.py` | Apply `regime_match_score` to N4 confidence in `match()` | N4 confidence is now multiplied by the regime match score — matching the current regime boosts N4, mismatching penalizes it. |
| `ppmt/src/ppmt/engine/ppmt.py` | Separate N1-N3 loop from N4 insertion with documentation | Clarified that N4 receives ALL patterns (with regime tags) but confidence is filtered at query time via regime_match_score. |
| `ppmt/src/ppmt/core/metadata.py` | Add `last_observation_time` field | Tracks when the most recent observation was recorded. Enables freshness tracking. |
| `ppmt/src/ppmt/core/metadata.py` | Add `observation_timespan` field | Measures time spread between first and last observation. Longer timespan = more robust pattern. |
| `ppmt/src/ppmt/core/metadata.py` | Add `freshness_decay` property | Exponential decay with 7-day half-life. Returns [0,1] multiplier — fresh observations = 1.0, stale = near 0. |
| `ppmt/src/ppmt/core/metadata.py` | Add `observation_density` property | Observations per day. High density = potentially overfit. Low density = robust across conditions. |
| `ppmt/src/ppmt/core/metadata.py` | Update `update_from_observation()` to track time | Auto-sets `last_observation_time` and `observation_timespan` on each observation. |
| `ppmt/src/ppmt/core/metadata.py` | Update `to_dict()` / `from_dict()` for new fields | Full serialization support with backward compatibility. |
| `ppmt/tests/test_metadata_v42.py` | Created 21 new tests | Tests for freshness, real count, regime threshold, N4 filtering, and backward compatibility. |

**Key Design Decisions**:

1. **Regime adjustment via threshold (not confidence)**: Instead of multiplying confidence by regime_match_score (which could be double-counted since PredictionEngine already does it), we adjust the THRESHOLD inversely. This is equivalent but avoids compounding the adjustment.
2. **Freshness with 7-day half-life**: Conservative enough that patterns aren't prematurely expired, but aggressive enough that stale patterns lose influence within a month.
3. **observation_timespan tracks robustness**: A pattern observed 100 times over 30 days is more trustworthy than one observed 100 times in 1 hour. The density metric makes this quantifiable.
4. **N4 receives all patterns, filters at query time**: Rather than building separate N4 tries per regime (which would fragment data), we store regime tags on each node and filter via `regime_match_score` at matching time. This preserves sample sizes while still being regime-aware.
5. **Backward compatibility**: V4.1 serialized data without freshness fields loads with `last_observation_time=0.0` (which gives `freshness_decay=1.0`, neutral).

**Tests**: 122 total (101 existing + 21 new V4.2), ALL PASSING

---

### V4.1 — Metadata Enhancement (2026-06-11)

**Problem**: Node metadata was insufficient for regime-aware trading decisions. The `regime_distribution` only counted observations per regime but did not track win_rate or expected_move per regime. The `regime_match_score()` method was referenced in `prediction.py` but did not exist, causing potential runtime crashes.

**Changes Made**:

| File | Change | Rationale |
|------|--------|-----------|
| `ppmt/src/ppmt/core/metadata.py` | Added `RegimeStats` dataclass | Per-regime tracking of count, wins, total_move_pct enables saying "this pattern wins 62% in trending_up but 33% in volatile" |
| `ppmt/src/ppmt/core/metadata.py` | Added `regime_stats` field to `BlockLifecycleMetadata` | Makes regime_distribution actionable with performance data |
| `ppmt/src/ppmt/core/metadata.py` | Added `move_variance` and `move_mean_for_variance` fields | Welford's online algorithm tracks dispersion of observed moves without storing raw data |
| `ppmt/src/ppmt/core/metadata.py` | Implemented `regime_match_score()` method | Fixes critical bug where `prediction.py` called non-existent method. Returns confidence multiplier [0.5, 1.2] based on regime match |
| `ppmt/src/ppmt/core/metadata.py` | Added `move_std` and `move_coefficient_of_variation` properties | Reliability metrics: CV < 0.5 = reliable, CV > 1.0 = unreliable pattern |
| `ppmt/src/ppmt/core/metadata.py` | Updated `update_from_observation()` to track regime_stats and move_variance | Incremental tracking without raw data storage |
| `ppmt/src/ppmt/core/metadata.py` | Updated `to_dict()` / `from_dict()` for new fields | Full serialization support including backward compatibility |
| `ppmt/src/ppmt/core/trie.py` | Updated `_propagate_node()` to merge regime_stats and compute pooled move_variance | Intermediate nodes now aggregate per-regime stats and variance from children |
| `ppmt/tests/test_metadata_v41.py` | Created 23 new tests | Sane tests that verify behavior, invariants, backward compatibility, and integration |

**Tests**: 101 total (78 existing + 23 new V4.1), ALL PASSING

---

### V4.0 — Regime-Aware Nodes (prior session)

- Added `regime`, `regime_confidence`, `dominant_regime`, `regime_distribution` to metadata
- Added `node_type` (independent/dependent) classification
- RegimeDetector integration in PPMT engine build
- Regime-aware paper trading with position sizing multipliers

### V3 — Block Lifecycle Metadata

- 12 metadata fields per Trie node
- `confidence`, `probability_of_success`, `expected_profit_ahead`, `sizing_signal`
- Direct PPMT → RiskManager integration via metadata-driven sizing

---

## Architecture Map

```
Data Flow:
  OHLCV → SAX Encoder → Trie (N1-N4) → Prediction Engine → Signal Generator → Risk Manager → Paper Trader
                                    ↕                                ↑
                              Living Trie ←—————— _record_observation ←┘

Metadata Flow:
  Observation → update_from_observation() → propagate_metadata() → confidence/computed properties
                                                                  → regime_match_score() → prediction confidence
                                                                  → regime_stats → regime-specific decisions
                                                                  → freshness_decay → observation freshness weighting
                                                                  → observation_density → robustness metric
```

## Module Inventory

| Module | Path | Status | Tests |
|--------|------|--------|-------|
| BlockLifecycleMetadata | `ppmt/src/ppmt/core/metadata.py` | V4.3 ✅ | 21 V4.2 + 23 V4.1 + 8 existing + 49 V4.3 |
| RegimeStats | `ppmt/src/ppmt/core/metadata.py` | V4.1 ✅ | 4 |
| PPMTTrie | `ppmt/src/ppmt/core/trie.py` | V4.1 ✅ | 10 + 12 V4.3 |
| SAXEncoder | `ppmt/src/ppmt/core/sax.py` | Complete ✅ | 10 + 6 V4.3 |
| FuzzyMatcher | `ppmt/src/ppmt/core/matcher.py` | Complete ✅ | 8 |
| RegimeDetector | `ppmt/src/ppmt/core/regime.py` | Complete ✅ | 2 V4.3 |
| PredictionEngine | `ppmt/src/ppmt/engine/prediction.py` | V4.2 ✅ | 2 + 3 V4.3 |
| PPMT Engine | `ppmt/src/ppmt/engine/ppmt.py` | V4.2 ✅ | 1 |
| PaperTrader | `ppmt/src/ppmt/engine/paper_trader.py` | V4.3 ✅ | 7 V4.3 (SHORT gate + historical count) |
| SignalGenerator | `ppmt/src/ppmt/engine/signal.py` | Complete ✅ | — |
| AdaptiveWeights | `ppmt/src/ppmt/engine/weights.py` | Complete ✅ | — |
| RiskManager | `ppmt/src/ppmt/risk/manager.py` | Complete ✅ | — |

## Known Issues (Current)

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `process_new_candle()` in ppmt.py returns None (streaming not implemented) | Medium | TODO |
| 2 | `max_drawdown_pct` stored as negative, used with abs() inconsistently | Low | Documented — works but confusing |
| 3 | No avg_holding_period (different from avg_duration) | Low | Future |
| 4 | `freshness_decay` not yet used in confidence computation | Low | Ready to integrate in V4.4 |
| 5 | No direction-specific win_rate per node (LONG vs SHORT) | Low | Future — useful for strongly-trending assets |
| 6 | No regime transition tracking | Low | Future — for predictive regime shifts |

## Previous Bug Status

| Bug | Description | Status |
|-----|-------------|--------|
| Double append | `self.trades.append.append({` | ✅ Does NOT exist in v0.9.0 |
| SHORT gate tautology | `confidence < max(confidence * 1.2, 0.20)` always false | ✅ Fixed in v0.9.0 — now uses `effective_min_conf = max(effective_min_conf * 1.2, 0.20)` |
| Print syntax | `{len(wins)W}` should be `{len(wins)}W` | ✅ Does NOT exist in v0.9.0 |
| regime_match_score missing | Method called but not defined | ✅ Fixed in V4.1 |
| Regime not passed to predict() | paper_trader.py never passed current_regime | ✅ Fixed in V4.2 |
| N4 redundant with N3 | N4 received all patterns, never filtered by regime | ✅ Fixed in V4.2 — regime_match_score applied at query time |
| hardcoded historical_count=100 | Signal sizing used fake count of 100 | ✅ Fixed in V4.2 (mock_meta), then V4.3 (moved BEFORE Signal creation so quality_score uses real count) |
| SHORT gate fixed multiplier | 1.2x regardless of regime — too strict in downtrend, too lenient in uptrend | ✅ Fixed in V4.3 — regime-aware gate (0.85x-1.8x based on regime) |
| No observation freshness | Stale patterns had same weight as fresh | ✅ Fixed in V4.2 — freshness_decay + observation_timespan |
