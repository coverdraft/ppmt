# PPMT Traceability Document

> Single source of truth for PPMT project architecture, decisions, and evolution.

## Current Version: v0.12.0 (V4 Regime-Aware Enhancement)

---

## Architecture Overview

### Core Pipeline
```
OHLCV Data → SAX Encode → 4-Level Trie (N1-N4) → Adaptive Weight Merge → Signal Generator → Trade
```

### 4-Level Trie Architecture
| Level | Name | Scope | Default Weight |
|-------|------|-------|----------------|
| N1 | Universal | All assets, all regimes | 10% |
| N2 | Asset Class | Blue Chip, Large Cap, Mid Cap, DeFi, Meme, New Launch | 30% |
| N3 | Per-Asset | Single trading pair (e.g., BTC/USDT) | 30% |
| N4 | Per-Asset+Regime | Single pair + market regime | 30% |

### Key Components
| File | Purpose |
|------|---------|
| `core/sax.py` | SAX encoder (8 alphabet, window=10, ohlcv strategy) |
| `core/trie.py` | PPMT Trie with Block Lifecycle Metadata + Living Trie |
| `core/metadata.py` | V4 Regime-Aware Node Metadata (independent/dependent nodes) |
| `core/regime.py` | RegimeDetector (trending_up, trending_down, ranging, volatile) |
| `core/matcher.py` | Fuzzy matching engine (exact → prefix → 1-edit → best) |
| `core/encoder.py` | Delta encoder for Trie compression |
| `engine/ppmt.py` | Main PPMT engine (build, match, bootstrap) |
| `engine/prediction.py` | Forward-looking prediction from Trie |
| `engine/paper_trader.py` | Paper trading simulation engine |
| `engine/weights.py` | Adaptive weight management |
| `engine/signal.py` | Signal types and generation |
| `risk/manager.py` | Position sizing and risk management |

---

## V4 Enhancement: Regime-Aware Node Metadata

### Problem Solved
Before V4, RegimeDetector detected market regimes at runtime but **never stored them in Trie nodes**. This meant:
- Patterns observed under "trending_up" had no memory of that regime
- N4 Trie (per_asset_regime) couldn't actually segment by regime
- No regime propagation from parents to children
- No concept of node reliability (independent vs dependent)

### V4 Changes

#### BlockLifecycleMetadata New Fields
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `regime` | str | "" | Regime at first observation (trending_up, trending_down, ranging, volatile) |
| `regime_confidence` | float | 0.0 | Confidence of regime detection at observation time |
| `dominant_regime` | str | "" | Most common regime across all observations |
| `regime_distribution` | dict[str, int] | {} | Histogram of regimes (e.g., {'trending_up': 45, 'ranging': 30}) |
| `node_type` | str | "dependent" | "independent" or "dependent" |
| `min_independent_count` | int | 10 | Minimum observations for independent classification |

#### Independent vs Dependent Nodes
- **Independent**: `historical_count >= min_independent_count` (default 10). Metadata is self-sufficient and reliable. Confidence used at full strength.
- **Dependent**: `historical_count < min_independent_count`. Metadata is inherited/aggregated from children. Confidence is scaled down by a dependency penalty factor (0.5 to 1.0).

#### Regime Propagation
During `propagate_metadata()` (bottom-up traversal):
1. Terminal nodes: regime is set directly from observations
2. Intermediate nodes: regime_distribution is merged from all children
3. dominant_regime is set to the regime with highest count in the distribution
4. regime_confidence is computed as weighted average of children

#### Paper Trader Integration
- At entry time, the matched Trie node's `dominant_regime` is used for the trade's regime field
- Living Trie observations pass the trade's regime back to `update_from_observation()`
- This creates a feedback loop: regime info flows both into and out of the Trie

---

## Version History

| Version | Date | Key Changes |
|---------|------|-------------|
| v0.1.0 | Initial | Basic SAX + Trie + Paper Trader |
| v0.2.8 | - | Baseline SL/TP (max P&L: +1578%) |
| v0.2.9 | - | Pattern break grace period, re-entry cooldown |
| v0.2.10 | - | Direction-specific SL/TP, catastrophic protection (regression) |
| v0.3.0 | - | Revert to v0.2.8 SL/TP baseline |
| v0.3.3 | - | Trade-simulation "won" classification during build |
| v0.4.0 | - | Bootstrap paper trading pass |
| v0.5.0 | - | SAX window=10, alphabet=8, 2-pass bootstrap |
| v0.6.0 | - | Probability bonus (regression: +86.82% P&L) |
| v0.6.1 | - | Removed probability bonus |
| v0.6.2 | - | Raised min_confidence to 0.20, catastrophic 8%, SHORT gate 1.2x |
| v0.6.3 | - | Training normalization for OOS validation |
| v0.8.0 | - | Regime-aware position sizing (runtime only) |
| v0.10.0 | - | Regime-in-metadata architecture |
| v0.11.0 | - | Fix corrupted node metadata |
| **v0.12.0** | **2026-06-11** | **V4: Regime stored in nodes, independent/dependent classification, regime propagation** |

---

## Known Issues & Design Decisions

### Design Decisions
1. **min_independent_count = 10**: Below 10 observations, node statistics are too noisy. Penalty scales from 0.5 (0 obs) to 1.0 (at threshold).
2. **Regime detected during build**: Each pattern's entry candle position is used for RegimeDetector, providing per-pattern regime labels.
3. **dominant_regime over regime**: For intermediate/propagated nodes, dominant_regime (from distribution) is more reliable than regime (from first observation).
4. **Living Trie passes regime back**: When recording trade outcomes, the trade's regime is passed to update_from_observation(), maintaining regime continuity.

### Not Yet Implemented
- Regime-specific win_rate calculation (per-regime performance breakdown)
- Regime transition tracking (which regimes follow which)
- N4 Trie actual regime-based segmentation (currently stores same data as N3)
- Walk-forward validation with regime stability checks

---

## Test Results Archive

| Cycle | Version | P&L | WR | Trades | Notes |
|-------|---------|-----|-----|--------|-------|
| Baseline | v0.2.8 | +1578% | - | - | Best historical result |
| Cycle 5 | v0.6.0 | +86.82% | - | 354 | Probability bonus regression |
| Cycle 4 | v0.5.0 | +1434% | 50.5% | 519 | Stable baseline |

---

## File Structure
```
ppmt/
├── src/ppmt/
│   ├── core/           # SAX, Trie, Metadata, Regime, Matcher, Encoder
│   ├── engine/         # PPMT, Prediction, PaperTrader, Weights, Signal
│   ├── data/           # Storage, Collector, Classifier
│   ├── risk/           # RiskManager, PositionSizing, MonteCarlo
│   ├── cli/            # CLI (build, run, monte-carlo)
│   └── dashboard/      # Dashboard (if enabled)
├── tests/              # Unit tests
├── scripts/            # Bulk ingest, utilities
├── CHANGELOG.md
├── TRACEABILITY.md     # This file
└── pyproject.toml
```
