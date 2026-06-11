# PPMT Traceability Document

> Single source of truth for all PPMT development. This document tracks every change, decision, and rationale.

---

## Project: PPMT — Pattern Prediction Market Trader

**Current Version**: v0.9.0 (base) → V4.1 metadata enhancements  
**Branch**: main  
**Last Updated**: 2026-06-11  

---

## Version History & Changes

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

**Key Design Decisions**:

1. **RegimeStats as separate dataclass**: Keeps metadata clean and allows independent serialization
2. **Welford's algorithm for variance**: Numerically stable, O(1) memory, no raw data storage needed
3. **Pooled variance for propagation**: Combines within-group and between-group variance correctly
4. **regime_match_score range [0.5, 1.2]**: Conservative range prevents extreme confidence swings
5. **Backward compatibility**: Old serialized data without V4.1 fields loads with safe defaults

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
```

## Module Inventory

| Module | Path | Status | Tests |
|--------|------|--------|-------|
| BlockLifecycleMetadata | `ppmt/src/ppmt/core/metadata.py` | V4.1 ✅ | 23 V4.1 + 8 existing |
| RegimeStats | `ppmt/src/ppmt/core/metadata.py` | V4.1 ✅ | 4 |
| PPMTTrie | `ppmt/src/ppmt/core/trie.py` | V4.1 ✅ | 10 |
| SAXEncoder | `ppmt/src/ppmt/core/sax.py` | Complete ✅ | 10 |
| FuzzyMatcher | `ppmt/src/ppmt/core/matcher.py` | Complete ✅ | 8 |
| RegimeDetector | `ppmt/src/ppmt/core/regime.py` | Complete ✅ | — |
| PredictionEngine | `ppmt/src/ppmt/engine/prediction.py` | V4.1 ✅ | 2 integration |
| PPMT Engine | `ppmt/src/ppmt/engine/ppmt.py` | Complete ✅ | — |
| PaperTrader | `ppmt/src/ppmt/engine/paper_trader.py` | v0.9.0 ✅ | — |
| SignalGenerator | `ppmt/src/ppmt/engine/signal.py` | Complete ✅ | — |
| AdaptiveWeights | `ppmt/src/ppmt/engine/weights.py` | Complete ✅ | — |
| RiskManager | `ppmt/src/ppmt/risk/manager.py` | Complete ✅ | — |

## Known Issues (Current)

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `process_new_candle()` in ppmt.py returns None (streaming not implemented) | Medium | TODO |
| 2 | N4 Trie not used for regime-specific matching in paper_trader.py | Low | Deferred |
| 3 | No observation freshness/decay mechanism | Low | Future |
| 4 | No avg_holding_period (different from avg_duration) | Low | Future |

## Previous Bug Status

| Bug | Description | Status |
|-----|-------------|--------|
| Double append | `self.trades.append.append({` | ✅ Does NOT exist in v0.9.0 |
| SHORT gate tautology | `confidence < max(confidence * 1.2, 0.20)` always false | ✅ Fixed in v0.9.0 — now uses `effective_min_conf = max(effective_min_conf * 1.2, 0.20)` |
| Print syntax | `{len(wins)W}` should be `{len(wins)}W` | ✅ Does NOT exist in v0.9.0 |
| regime_match_score missing | Method called but not defined | ✅ Fixed in V4.1 |
