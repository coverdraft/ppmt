# TRACEABILITY.md — PPMT Project Central Log

> Single source of truth for all changes, decisions, and status.
> Last updated: 2026-06-11

---

## Current Version: v0.11.0 (V4 Regime-Aware Enhanced)

---

## Version History

### v0.11.0 — V4 Regime-Aware Enhancement (2026-06-11)

**Problem**: Node metadata was corrupted because patterns observed under different market regimes (trending_up, ranging, trending_down, volatile) were mixed together in the same TrieNode. A pattern `[a, d, b, h]` observed in trending_up (+5% expected move) and the same pattern in ranging (+0.5% expected move) got averaged into the same node, producing misleading metadata.

**Solution**: Added detailed regime metrics to `BlockLifecycleMetadata` and integrated `RegimeDetector` throughout the pipeline.

**Changes**:

| File | Change |
|------|--------|
| `core/metadata.py` | Added 7 new regime fields: `regime_confidence`, `trend_strength`, `volatility_regime`, `hurst_exponent`, `regime_transitions`, `is_regime_dependent`, `suggested_direction` + `regime_aligned` property |
| `core/trie.py` | V4 regime propagation: `add_child()` inherits parent regime; `insert_with_observations()` accepts V4 params and propagates regime to intermediate nodes; `search()` accepts optional `regime` filter |
| `engine/ppmt.py` | `build()` now calls `regime_detector.detect_detailed()` per pattern window and passes all V4 regime metrics to `insert_with_observations()` for N1-N4 tries |
| `ppmt/__init__.py` | Version bump to 0.11.0 |

**New Node Behavior (Independent vs Dependent)**:

- **Independent nodes** (N1/N2): `is_regime_dependent=False`, no regime metrics. Work across all regimes. `suggested_direction` uses `expected_move_pct` alone.
- **Dependent nodes** (N3/N4): `is_regime_dependent=True`, carry full regime context. `suggested_direction` combines regime + expected_move for optimal direction.
  - `trending_up + bullish` → LONG (regime confirms)
  - `trending_down + bearish` → SHORT (regime confirms)
  - `volatile` → AVOID (too chaotic)
  - `ranging + small move` → FLAT (no edge)

**Regime Propagation**: When a node has regime context, all children automatically inherit it via `add_child()`. This ensures the entire pattern branch maintains consistent regime context.

---

### v0.10.0 — Regime Distribution & N4 Dict (Previous Session)

- Added `regime` and `regime_distribution` to `BlockLifecycleMetadata`
- Changed N4 from single trie to `dict[str, PPMTTrie]` with one trie per regime
- Added `trie_n4_fallback` for regimes with insufficient data
- Added `regime_independence` (entropy-based) and `regime_match_score` properties
- `build()` detects regime per pattern window via `RegimeDetector.detect_series()`
- `propagate_metadata()` aggregates `regime_distribution` from children to parents
- Trade-simulation "won" classification using ATR-based SL/TP

---

### Earlier Versions (v0.1.0 - v0.9.0)

See README.md experiment log for detailed history.

---

## Known Issues

| ID | Status | Description |
|----|--------|-------------|
| BUG-001 | OPEN | `risk/__init__.py` imports `Position` and `RiskConfig` which don't exist in `manager.py` — needs separate `Position` dataclass |
| BUG-002 | FIXED | `metadata.py` docstring had `"` instead of `"""` on `is_regime_dependent` field |
| TODO-001 | OPEN | `process_new_candle()` returns None — streaming pattern buffer not implemented |
| TODO-002 | OPEN | `paper_trader.py` uses `risk_mgr.can_open()`, `risk_mgr.open_position()`, `risk_mgr.close_position()` which don't exist in current `RiskManager` |
| TODO-003 | OPEN | Bootstrap method in `ppmt.py` imports `compute_atr_pct` and `_record_observation` from `paper_trader` — may cause circular imports |

---

## Architecture Quick Reference

```
4-Level Trie Architecture:
  N1 (10%) - Universal, regime-agnostic (independent nodes)
  N2 (30%) - Asset Class, regime-agnostic (independent nodes)
  N3 (30%) - Per-Asset, regime-dependent (dependent nodes)
  N4 (30%) - Per-Asset+Regime, regime-specific (dependent nodes)
             → dict[str, PPMTTrie] with separate tries per regime
             → Falls back to combined trie if regime has < 50 patterns

Node Metadata Fields (v0.11.0):
  Entry/Exit: trigger_candle, remaining_candles
  Price: expected_move_pct, max_drawdown_pct, max_favorable_pct
  Stats: win_rate, avg_duration, historical_count
  Risk: sl_price, tp_price
  Navigation: continuation_nodes, break_nodes
  Regime v0.10: regime, regime_distribution
  Regime v0.11: regime_confidence, trend_strength, volatility_regime,
                hurst_exponent, regime_transitions, is_regime_dependent
  Computed: confidence, probability_of_success, expected_profit_ahead,
            sizing_signal, risk_reward_ratio, regime_independence,
            regime_match_score, regime_aligned, suggested_direction
```

---

## File Map

```
/home/z/my-project/ppmt/
├── src/ppmt/
│   ├── core/          # Core data structures
│   │   ├── metadata.py    # BlockLifecycleMetadata (V4 regime-aware)
│   │   ├── trie.py        # PPMTTrie + TrieNode (V4 regime propagation)
│   │   ├── sax.py         # SAX encoder (complete)
│   │   ├── regime.py      # RegimeDetector + RegimeInfo
│   │   ├── matcher.py     # FuzzyMatcher
│   │   └── encoder.py     # DeltaEncoder
│   ├── engine/        # Trading engine
│   │   ├── ppmt.py        # PPMT engine (V4 regime integration)
│   │   ├── signal.py      # Signal generator
│   │   ├── prediction.py  # Prediction engine
│   │   ├── weights.py     # AdaptiveWeights
│   │   ├── paper_trader.py # Paper trading
│   │   └── monte_carlo.py # MC simulation
│   ├── risk/          # Risk management
│   │   ├── manager.py     # RiskManager
│   │   ├── position_sizing.py # AdvancedPositionSizer
│   │   └── monte_carlo.py # MC risk simulator
│   ├── data/          # Data layer
│   ├── cli/           # CLI
│   └── dashboard/     # Web dashboard
├── config/default.yaml
├── tests/
└── pyproject.toml
```
