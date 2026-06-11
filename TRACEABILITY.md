# PPMT - Traceability Document

**Version:** v0.6.2 (engine core), V4.3 (metadata/regime)  
**Last Updated:** 2026-06-11  
**Status:** Active Development  

---

## 1. Project Overview

PPMT (Pattern Prediction Market Trader) is a crypto trading system that uses a **Living Trie** with SAX (Symbolic Aggregate approXimation) discretization to detect and trade price patterns autonomously. The system is designed to be fully autonomous — all entry/exit/SL/TP decisions emerge from the Trie's metadata without external indicators.

### Architecture

```
OHLCV Data → SAX Encoder → 4-Level Trie (N1-N4) → Adaptive Weight Merge → Signal → Risk Manager → Trade
```

### 4-Level Trie Architecture

| Level | Name | Scope | Default Weight |
|-------|------|-------|----------------|
| N1 | Universal | All tokens, all regimes | 5% (new_launch: 40%) |
| N2 | Asset Class | Tokens of same class (blue_chip, meme, etc.) | 20% (new_launch: 30%) |
| N3 | Per-Asset | Single token (e.g., BTC/USDT) | 35% (new_launch: 20%) |
| N4 | Per-Asset+Regime | Token in specific market regime | 40% (new_launch: 10%) |

### Asset Classification (6 classes)

| Class | Examples | Weight Profile | Risk Profile |
|-------|----------|---------------|-------------|
| blue_chip | BTC, ETH | blue_chip | Conservative |
| large_cap | BNB, SOL, XRP | default | Standard |
| mid_cap | LINK, AVAX, DOT | default | Standard |
| defi | UNI, AAVE, CRV | default | Sector-specific |
| meme | DOGE, SHIB, PEPE | meme | Aggressive stops |
| new_launch | Recently listed | new_launch | Heavy N1/N2 reliance |

---

## 2. Source File Map

### Core Engine (`src/ppmt/core/`)

| File | Purpose | Version | Status |
|------|---------|---------|--------|
| `sax.py` | SAX encoding (3 strategies: close, typical_price, ohlcv) | V7.9 backport | Complete |
| `trie.py` | PPMTTrie with metadata propagation, merge, regime-aware | V4 | Complete |
| `metadata.py` | BlockLifecycleMetadata, RegimeStats, freshness tracking | V4.2 | Complete |
| `regime.py` | RegimeDetector (Hurst exponent, volatility, trend) | V0.8.0 | Complete |
| `matcher.py` | FuzzyMatcher (exact, prefix, 1-edit, best_match) | V0.2.7 | Complete |
| `encoder.py` | Additional encoding utilities | V0.1 | Present |

### Trading Engine (`src/ppmt/engine/`)

| File | Purpose | Version | Status |
|------|---------|---------|--------|
| `ppmt.py` | Main PPMT engine: build, match (4-level), bootstrap | V0.6.3 | Complete |
| `paper_trader.py` | Paper trading simulation with full SL/TP/trailing | V4.3 | Complete |
| `prediction.py` | Forward path prediction, regime-aware confidence | V0.10.0 | Complete |
| `signal.py` | Signal generation (entry/exit/continuation) | V0.2.10 | Present |
| `weights.py` | AdaptiveWeights with profiles (default, blue_chip, meme, new_launch) | V0.4 | Present |
| `monte_carlo.py` | Monte Carlo resampling validation | V0.2 | Present |
| `validator.py` | Validation utilities | V0.3 | Present |
| `realtime.py` | Real-time trading interface | V0.1 | Present |

### Data Layer (`src/ppmt/data/`)

| File | Purpose | Status |
|------|---------|--------|
| `storage.py` | SQLite-backed OHLCV and trie storage | Complete |
| `collector.py` | OHLCV data collection from exchanges | Complete |
| `classifier.py` | AssetClassifier with 6 classes and heuristic fallback | Complete |

### Risk Management (`src/ppmt/risk/`)

| File | Purpose | Status |
|------|---------|--------|
| `manager.py` | RiskManager with position sizing, SL/TP checking | Complete |
| `monte_carlo.py` | Risk-specific Monte Carlo | Present |
| `position_sizing.py` | Position sizing calculations | Present |

### CLI (`src/ppmt/cli/`)

| File | Purpose | Status |
|------|---------|--------|
| `main.py` | CLI with build, run, monte-carlo subcommands | Complete |

### Diagnostic Scripts (`scripts/`)

| File | Purpose | Status |
|------|---------|--------|
| `cross_token_diagnostic.py` | Cross-token OOS validation (4 tests) | Complete |
| `walk_forward.py` | Walk-forward analysis | Present |
| `e2e_validation.py` | End-to-end validation | Present |
| `bulk_ingest.py` | Bulk data ingestion | Present |

---

## 3. Bug Status

### Previously Reported Bugs — ALL RESOLVED

| Bug ID | Description | Status | Resolution |
|--------|-------------|--------|------------|
| BUG-1 | `self.trades.append.append({` (double append) | RESOLVED | Current code uses `result.trades.append(current_position)` correctly everywhere |
| BUG-2 | SHORT gate tautology: `confidence < max(confidence * 1.2, 0.20)` | RESOLVED | Replaced by V4.3 regime-aware SHORT gate (lines 1007-1014 paper_trader.py) |
| BUG-3 | Print syntax: `{len(wins)W}` | NOT PRESENT | Not found in current codebase |

### V4.3 SHORT Gate Logic (Current)

```python
if prediction.direction == "SHORT":
    short_regime_mult = {
        "trending_down": 0.85,  # SHORTs favored in downtrend
        "ranging": 1.1,         # slight caution
        "trending_up": 1.5,     # fighting the trend — strict
        "volatile": 1.8,        # high risk — very strict
    }.get(current_regime, 1.2)   # default: moderate penalty
    effective_min_conf = max(effective_min_conf * short_regime_mult, 0.20)
```

This correctly adjusts the SHORT entry threshold based on the current market regime instead of the old tautological check.

---

## 4. Metadata Architecture (V4/V4.1/V4.2)

### BlockLifecycleMetadata Fields

| Field | Type | Purpose | Version |
|-------|------|---------|---------|
| `trigger_candle` | int | When the pattern activates | V3 |
| `remaining_candles` | int | Predicted duration | V3 |
| `expected_move_pct` | float | Expected price move (+/-) | V3 |
| `max_drawdown_pct` | float | Worst observed drawdown | V3 |
| `max_favorable_pct` | float | Best observed favorable excursion | V3 |
| `win_rate` | float | Historical win rate | V3 |
| `avg_duration` | int | Average pattern duration | V3 |
| `historical_count` | int | Number of observations | V3 |
| `sl_price` | float | Dynamic stop loss | V3 |
| `tp_price` | float | Dynamic take profit | V3 |
| `continuation_nodes` | list[str] | Known continuation symbols | V3 |
| `break_nodes` | list[str] | Known break symbols | V3 |
| `regime` | str | Market regime when observed | V4 |
| `regime_confidence` | float | Regime detection confidence | V4 |
| `dominant_regime` | str | Most common regime across observations | V4 |
| `regime_distribution` | dict[str,int] | Regime observation counts | V4 |
| `regime_stats` | dict[str,RegimeStats] | Per-regime win_rate and expected_move | V4.1 |
| `move_variance` | float | Variance of moves (Welford's) | V4.1 |
| `move_mean_for_variance` | float | Welford's running mean | V4.1 |
| `node_type` | str | "independent" or "dependent" | V4 |
| `min_independent_count` | int | Threshold for independence (default: 10) | V4 |
| `last_observation_time` | float | Timestamp of latest observation | V4.2 |
| `observation_timespan` | float | Time between first and last observation | V4.2 |

### Computed Properties

| Property | Formula | Purpose |
|----------|---------|---------|
| `confidence` | Bayesian-adjusted WR × count_bonus × dependency_penalty | Entry quality score |
| `probability_of_success` | Bayesian-adjusted WR (no count bonus) | Pure probability |
| `expected_profit_ahead` | WR × expected_move + (1-WR) × max_drawdown | Expected value |
| `sizing_signal` | 0.4×prob + 0.35×profit_score + 0.25×RR, scaled to 0-2 | Position sizing |
| `move_std` | sqrt(move_variance / (n-1)) | Pattern reliability |
| `move_coefficient_of_variation` | move_std / |expected_move| | Normalized dispersion |
| `freshness_decay` | exp(-0.693 × age / 604800) | Observation freshness [0,1] |
| `observation_density` | historical_count / timespan_days | Concentration measure |
| `regime_match_score(current)` | [0.5, 1.2] based on regime match + regime_stats | Regime-aware confidence |

### Independent vs Dependent Nodes

- **Independent**: `historical_count >= min_independent_count (10)` — metadata is reliable on its own, full confidence
- **Dependent**: `historical_count < 10` — metadata inherited from parent/children, confidence scaled down by `0.5 + 0.5 × (count / min_count)`

### Metadata Propagation (bottom-up)

`trie.propagate_metadata()` walks bottom-up and:
1. Leaf nodes: classified as independent/dependent
2. Intermediate nodes: aggregate children's statistics (weighted average by count)
3. Merges regime_distribution and regime_stats from children
4. Sets dominant_regime from merged distribution
5. Computes pooled move_variance (within-group + between-group)

---

## 5. Paper Trader Configuration (V4.3)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `symbol` | BTC/USDT | Trading pair |
| `timeframe` | 1h | Candle interval |
| `initial_capital` | $10,000 | Starting capital |
| `pattern_length` | 5 | SAX blocks per pattern |
| `min_confidence` | 0.20 | Minimum confidence for entry |
| `catastrophic_loss_pct` | 8.0% | Hard stop for intra-window |
| `living_trie` | True | Record observations during trading |
| `pattern_break_grace` | 2 | Consecutive breaks before exit |
| `reentry_cooldown` | 1 | Symbol steps after loss |
| `regime_aware` | True | Enable regime-aware sizing |
| `end_offset` | 0 | Candle limit for OOS testing |
| `paa_mean` / `paa_std` | None | Training SAX normalization stats |

### SL/TP Parameters

| Direction | SL Formula | TP Formula | Cap |
|-----------|-----------|-----------|-----|
| LONG | max(ATR×1.5, 1.5%) | SL×2.0 (R:R=2.0) | 5% |
| SHORT | max(ATR×2.0, 2.0%) | SL×1.5 (R:R=1.5) | 7% |

---

## 6. Out-of-Sample (OOS) Validation

### Current OOS Support

1. **`end_offset` parameter**: PaperTraderConfig supports trading up to a specific candle index
2. **`paa_mean`/`paa_std`**: SAX normalization propagation from training to test (V7.9/V0.6.3)
3. **`cross_token_diagnostic.py`**: 4-test diagnostic suite (single-token OOS, cross-token, Living Trie ON/OFF, random baseline)

### Cross-Token Diagnostic Tests

| Test | Purpose | Method |
|------|---------|--------|
| Test 1 | Single-token OOS baseline | Build on 70%, trade on 30% |
| Test 2 | Cross-token generalization | Build on source, trade on target |
| Test 3 | Living Trie ON vs OFF | Compare with/without learning |
| Test 4 | Random baseline | Random trades with same SL/TP |

### Critical GAP Identified

**PaperTrader only uses N3 (per-asset trie) via PredictionEngine. N1, N2, N4 are NOT used during paper trading.**

This means:
- A token never seen before CANNOT benefit from universal (N1) or class-level (N2) patterns
- The `PPMT.match()` method does 4-level matching, but PaperTrader bypasses it
- For cross-token validation to work properly, PaperTrader needs to use `PPMT.match()` instead of just `PredictionEngine(trie_n3)`

### Recommended Fix Priority

1. **HIGH**: Connect `PPMT.match()` (4-level) to PaperTrader for cross-token support
2. **HIGH**: Implement proper walk-forward OOS with strict train/test separation
3. **MEDIUM**: Add regime-specific N4 filtering during matching
4. **LOW**: Implement streaming SAX buffer for real-time operation

---

## 7. Living Trie Mechanism

The Living Trie is the self-learning core:

```
Trie predicts → Trade executes → Outcome observed → Trie updated
```

### How It Works

1. When a trade closes, `_record_observation()` is called
2. The entry node's metadata is updated with the actual outcome (win/loss, move, duration, regime)
3. If the exit symbol is not already a child, a new node is created (the Trie literally grows)
4. Every 200 symbol steps, `trie.propagate_metadata()` re-aggregates intermediate nodes
5. At end of trading session, the updated trie is saved to storage

### In-Sample vs OOS Concern

The Living Trie learning from its own trades during simulation creates an **in-sample inflation** problem:
- During backtesting, the Trie improves as it trades → later trades benefit from earlier observations
- This inflates in-sample performance vs what would happen in real trading
- **The proper OOS test disables Living Trie during the test period** (only learns during training)

---

## 8. Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | - | Initial SAX + Trie + basic matching |
| 0.2.7 | - | Prediction: expected-value path (not frequency) |
| 0.2.8 | - | SL/TP at SAX boundaries, +1578% P&L baseline |
| 0.2.9 | - | Pattern break grace period, re-entry cooldown |
| 0.2.10 | - | Catastrophic protection, tighter SL (reduced P&L) |
| 0.3.0 | - | Revert to v0.2.8 baseline (removed harmful "improvements") |
| 0.3.3 | - | Trade-simulation "won" during build for better metadata |
| 0.4.0 | - | Bootstrap 2-pass (build then simulate) |
| 0.6.0 | - | Probability bonus (loophole: 10% confidence trades) |
| 0.6.1 | - | Removed probability bonus |
| 0.6.2 | - | min_confidence raised to 20%, catastrophic re-enabled at 8%, end_offset for OOS |
| 0.6.3 | - | SAX normalization propagation (encode_with_normalization) |
| V4 | - | Regime-aware metadata, independent/dependent nodes, regime_distribution |
| V4.1 | - | Per-regime stats (RegimeStats), move variance (Welford's) |
| V4.2 | - | Observation freshness (freshness_decay, observation_density) |
| V4.3 | - | Regime-aware SHORT gate, historical_count fix in Signal |
| V0.8.0 | - | Regime detection in paper trading, regime-aware position sizing |
| V0.10.0 | - | Regime match score in PredictionEngine confidence |

---

## 9. Testing Status

### Unit Tests (`tests/`)

| Test File | Scope | Status |
|-----------|-------|--------|
| `test_sax.py` | SAX encoding | Present |
| `test_trie.py` | Trie insertion/search | Present |
| `test_encoder.py` | Encoder utilities | Present |
| `test_matcher.py` | FuzzyMatcher | Present |
| `test_metadata_v41.py` | Metadata V4.1 | Present |
| `test_metadata_v42.py` | Metadata V4.2 | Present |
| `test_v43_robust.py` | V4.3 robustness | Present |
| `test_data_and_v3.py` | Data + V3 features | Present |
| `engine.test.ts` | TypeScript engine tests | Present |
| `sax.test.ts` | TypeScript SAX tests | Present |
| `trie.test.ts` | TypeScript trie tests | Present |
| `multiLevel.test.ts` | Multi-level trie | Present |

### Integration Tests

| Test | Method | Status |
|------|--------|--------|
| Single-token OOS | 70/30 split, SAX normalization propagated | Available (cross_token_diagnostic.py) |
| Cross-token OOS | Build source → test target | Available (cross_token_diagnostic.py) |
| Random baseline | Random trades with same SL/TP | Available (cross_token_diagnostic.py) |
| Walk-forward | Sliding window | Available (walk_forward.py) |

---

## 10. Open Issues & Recommendations

### Critical

1. **PaperTrader uses only N3 trie** — Must integrate PPMT.match() (4-level) for cross-token support
2. **Cross-token SAX normalization** — When using source N3 on target data, SAX symbols may not align due to different price distributions. The `encode_with_normalization()` fix (V0.6.3) must be used.

### High Priority

3. **Walk-forward OOS** — Implement proper rolling window walk-forward with strict train/test separation
4. **Living Trie inflation measurement** — Quantify how much Living Trie learning inflates in-sample results vs OOS

### Medium Priority

5. **N4 regime filtering** — During PPMT.match(), filter N4 results by current_regime for more precise regime-specific matching
6. **Streaming operation** — process_new_candle() returns None (TODO in ppmt.py)
7. **Cross-token_diagnostic.py duplicate function** — `_simplified_match_and_trade` defined twice (lines 294 and 596)

### Low Priority

8. **Observation freshness in confidence** — freshness_decay is computed but not yet used in the confidence formula
9. **Break_nodes tracking** — break_nodes list exists but is never populated with actual break symbols during trading
10. **Signal quality_score** — Uses a separate computation from metadata.sizing_signal; could be unified
