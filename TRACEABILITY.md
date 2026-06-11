# PPMT Traceability Document

> **Single source of truth** for the PPMT (Progressive Pattern Matching Trie) project.
> All versions, bugs, fixes, decisions, and benchmarks are tracked here.

---

## Current Status

| Item | Value |
|------|-------|
| **Version** | v0.10.0 |
| **Branch** | main |
| **Working Tree** | Modified (v0.10.0 regime-in-metadata changes) |
| **Last Commit** | `621498f` — v0.9.0: real-time trading engine |
| **Active Source** | `/ppmt/src/ppmt/` (development), `/ppmt/ppmt/src/ppmt/` (submodule mirror) |

---

## Architecture Overview

```
Pipeline: OHLCV → SAX Encoding → Living Trie Match → Prediction → Signal → Risk → Trade

Components:
  SAX Encoder    (core/sax.py)      — OHLCV → discrete symbols (a-h)
  Living Trie    (core/trie.py)     — Multi-level pattern storage (N1-N4)
  Metadata       (core/metadata.py) — Block Lifecycle per Trie node + regime context
  RegimeDetector (core/regime.py)   — Market regime detection (trending_up/down/ranging/volatile)
  PPMT Engine    (engine/ppmt.py)   — Orchestrator: build + predict + bootstrap
  Prediction     (engine/prediction.py) — Forward chain prediction + regime-aware confidence
  Paper Trader   (engine/paper_trader.py) — Simulated trading engine
  Risk Manager   (risk/manager.py)  — Position sizing, SL/TP, drawdown limits
  Monte Carlo    (risk/monte_carlo.py) — Resampling validation
  Validator      (engine/validator.py) — P0+P1+P2 composite scoring
  CLI            (cli/main.py)      — ppmt build/run/validate/monte-carlo
```

### Key Parameters (v0.6.2 baseline)

| Parameter | Value | Notes |
|-----------|-------|-------|
| SAX alphabet | 8 | Balanced granularity |
| SAX window | 10 | 10 candles per symbol |
| Pattern length | 5 | 5 SAX blocks per pattern |
| Min confidence | 0.20 (20%) | Raised from 15% in v0.6.2 |
| SHORT gate | max(conf*1.2, 0.20) | Relaxed from 1.5x in v0.6.2 |
| Catastrophic stop | 8.0% | Re-enabled in v0.6.2 |
| Pattern break grace | 2 consecutive | v0.2.9+ |
| Re-entry cooldown | 1 symbol step | v0.2.10+ |
| Trailing stop | 75% TP distance | 1.5*ATR trailing width |
| LONG SL | max(ATR*1.5, 1.5%) cap 5% | v0.2.8 baseline |
| SHORT SL | max(ATR*2.0, 2.0%) cap 7% | v0.2.8 baseline |
| AdaptiveWeights | N1=5%, N2=20%, N3=35%, N4=40% | Multi-level trie |

---

## Version History & Decisions

### v0.10.0 (2026-06-11) — Regime-in-Metadata Architecture
- **Added**: `regime` and `regime_distribution` fields to `BlockLifecycleMetadata`
  - Every trie node now knows which market regimes it was observed in
  - `regime_distribution` tracks count per regime (e.g., `{trending_up: 45, ranging: 30}`)
  - `regime` field stores the primary regime (most observations)
- **Added**: `regime_independence` property on metadata
  - Uses normalized Shannon entropy of regime distribution
  - Independent nodes (spread across regimes) → score near 1.0 (works anywhere)
  - Dependent nodes (concentrated in one regime) → score near 0.0 (needs specific regime)
- **Added**: `regime_match_score(current_regime)` method on metadata
  - Returns confidence multiplier (0.4 to 1.5) based on regime match
  - Current regime matches primary → boost (up to 1.2x)
  - Current regime has no observations → penalize (0.6x)
  - Independent nodes are less affected (flattened toward 1.0)
  - Dependent nodes are more affected (keep full base score)
- **Added**: N4 as dict of regime-specific tries
  - `trie_n4_regime: dict[str, PPMTTrie]` — separate trie per regime
  - `trie_n4_fallback` — generic trie containing ALL patterns
  - When matching, uses regime-specific trie; falls back if < 50 patterns
  - This makes N4 truly "Per-Asset+Regime" instead of a copy of N3
- **Changed**: `PPMT.build()` now detects regime at each pattern position
  - Uses `RegimeDetector.detect_series()` for efficiency
  - Passes `regime` parameter to all `insert_with_observations()` calls
  - N1, N2, N3 also receive regime context (stored in metadata)
  - N4 inserts into the appropriate regime-specific trie
- **Changed**: `PredictionEngine.predict()` accepts `current_regime` parameter
  - Confidence adjusted by `regime_match_score`
  - Patterns matching current regime get boosted confidence
  - Patterns in wrong regime get penalized confidence
- **Changed**: `_record_observation()` and `update_from_observation()` accept `regime`
  - Living Trie now accumulates regime distribution from trading observations
  - Regime distribution propagates upward via `propagate_metadata()`
- **Decision**: Independent vs dependent node behavior
  - Patterns observed across all regimes = "independent" = robust
  - Patterns observed only in one regime = "dependent" = conditional
  - This distinction enables regime-aware confidence without hard gating

### v0.9.0 (2026-06-11)
- **Added**: Real-time trading engine (`engine/realtime.py`)
  - Replay mode: stream historical data through incremental SAX pipeline
  - Live mode: connect to exchange via ccxt for real-time trading
  - Streaming pattern buffer for real-time matching
  - CLI `ppmt replay` and `ppmt live` commands
  - `ppmt run` (without --paper) now runs replay instead of showing TODO
- **Added**: Callbacks (`on_signal`, `on_trade`, `on_candle`) for integrations

### v0.8.1 (2026-06-11)
- **Bug Fixed**: Regime multiplier was NOT being applied to position sizing
  - Regime was detected and recorded in trades but never scaled the sizing_signal
  - Now `metadata_sizing_signal` and `sizing_multiplier` are multiplied by regime factor
  - trending_up: 1.2x, ranging: 1.0x, trending_down: 0.6x, volatile: 0.4x
- **Fixed**: pyproject.toml version synced 0.7.0 → 0.8.1
- **Fixed**: CLI --version synced to 0.8.1

### v0.7.1 (2026-06-11)
- **Decision**: Converted ppmt/ from git submodule to regular tracked directory
- **Fix**: numpy JSON serialization in `validate-all --json-output`
- Updated .gitignore with egg-info exclusion

### v0.7.0 (2026-06-11) — Phase 1+2+3: Validation Engine + API + Dashboard
- **Added**: `validator.py` — P0 (OOS), P1 (MC), P2 (Walk-Forward) scoring
- **Added**: Composite 100-point scoring (P0=40, P1=30, P2=30)
- **Added**: Verdict system: ROBUST (>=70), MARGINAL (>=45), OVERFIT (<45)
- **Added**: API bridge `/api/validation/run` for dashboard consumption
- **Added**: Dashboard validation suite panel with charts
- **Decision**: V7.9 normalization fix — training z-score stats propagate to test encoding

### v0.6.3 (2026-06-10) — OOS Normalization Fix
- **Added**: `encode_with_normalization()` in SAXEncoder
- **Added**: paa_mean/paa_std stored in engine state
- **Decision**: Strict OOS requires consistent z-score mapping between train/test
- **Bug Found & Fixed**: Z-score recalculation in test period caused look-ahead bias

### v0.6.2 (2026-06-10) — Cycle 5 Regression Fixes
- **Added**: `ppmt validate` CLI command (OOS validation)
- **Added**: `end_offset` parameter for OOS testing
- **Added**: Walk-forward analysis, Monte Carlo CLI
- **Fixed**: min_confidence raised 0.15 → 0.20 (Cycle 5 showed 15-19% conf had 38% WR)
- **Fixed**: SHORT gate relaxed from max(conf*1.5, 0.15) to max(conf*1.2, 0.20)
  - Old: 1.5x gate eliminated ALL SHORT trades (0/354)
  - New: 1.2x = 24% min SHORT, still stricter but allows quality SHORTs
- **Fixed**: Catastrophic loss protection re-enabled at 8% (was disabled at 5% in v0.3.0)
- **Performance**: Cycle 6a +155,150% P&L, 89.2% WR | Cycle 6b +45,035%, 90.2% WR

### v0.6.1 (2026-06-09) — Probability Bonus Removal
- **Fixed**: Removed probability bonus that undermined min_confidence
  - Bonus lowered threshold from 15% to 7.5% when prob>50%
  - 10% confidence trades had WR of only 32.6%
- **Kept**: min_confidence at 15% from v0.6.0

### v0.6.0 (2026-06-09) — REGRESSION
- **Added**: Probability bonus (REMOVED in v0.6.1)
- **Regressed**: P&L dropped from +1434% to +86.82%, WR from 50.5% to 44.6%
- **Root Cause**: Bonus allowed 10% confidence trades via loophole

### v0.5.0 (2026-06-08) — Bootstrap Living Trie
- **Added**: 2-pass bootstrap paper trading to populate Living Trie
- **Critical Finding**: Without bootstrap, system collapses to -18.77% P&L
- **Decision**: Fresh tries need meaningful metadata from day one

### v0.4.0 — Living Trie + Multi-Level
- **Added**: Living Trie concept (Trie learns from trading results)
- **Added**: N1-N4 multi-level with AdaptiveWeights
- **Added**: SAX encoding, pattern matching (exact/fuzzy/prefix)
- **Added**: Risk management with ATR-based SL/TP

---

## Bug Tracking

### Fixed Bugs

| ID | Version Found | Version Fixed | Description | Fix |
|----|---------------|---------------|-------------|-----|
| BUG-001 | v0.6.0 | v0.6.1 | Probability bonus undermined min_confidence | Removed bonus entirely |
| BUG-002 | v0.6.0 | v0.6.2 | SHORT gate tautology: max(conf*1.5, 0.15) eliminated all SHORTs | Changed to max(conf*1.2, 0.20) |
| BUG-003 | v0.6.3 | v0.6.3 | Z-score recalculation in OOS test caused look-ahead | Added encode_with_normalization() |
| BUG-004 | v0.5.0 | v0.6.2 | Catastrophic 5% was too tight for BTC volatility | Re-enabled at 8% with more breathing room |
| BUG-005 | Pre-v0.5 | v0.5.0 | Fresh tries had no metadata → system collapsed | 2-pass bootstrap paper trading |
| BUG-006 | v0.8.0 | v0.8.1 | Regime detected but not applied to position sizing | Added regime_mult to sizing_signal computation |

### Verified Clean (2026-06-11 session)

The following bugs from a previous session's analysis were verified as already fixed:
- ~~double `.append.append()` in SHORT SL block~~ — NOT present in current code
- ~~SHORT gate tautology `confidence < max(confidence * 1.2, 0.20)`~~ — Current code uses `effective_min_conf = max(effective_min_conf * 1.2, 0.20)` then checks `confidence >= effective_min_conf`, which is correct
- ~~print syntax `{len(wins)W}`~~ — NOT present in current code
- ~~sax.py breakpoints incomplete~~ — Current sax.py is complete (342 lines, all breakpoints defined)

---

## Performance Benchmarks

| Cycle | Version | P&L | Win Rate | Sharpe | Max DD | Trades |
|-------|---------|-----|----------|--------|--------|--------|
| 1-3 | v0.3.x | +1578% | ~50% | — | — | 380 |
| 4 | v0.5.0 | +1434% | 50.5% | — | — | 519 |
| 5 | v0.6.0 | +86.82% | 44.6% | — | 41.3% | 354 |
| 6a | v0.6.2 | +155,150% | 89.2% | 19.96 | 8.3% | — |
| 6b | v0.6.2 | +45,035% | 90.2% | 20.93 | 3.7% | — |

> Note: Cycles 6a/6b numbers are from paper trading with specific configurations. Real OOS validation (v0.7.0) showed +295% OOS P&L, 0% MC risk of ruin, 6/6 WF windows profitable.

---

## File Map

| File | Path | Lines | Status |
|------|------|-------|--------|
| SAX Encoder | `src/ppmt/core/sax.py` | 342 | Complete |
| Trie | `src/ppmt/core/trie.py` | 649 | Complete |
| Metadata | `src/ppmt/core/metadata.py` | — | Complete |
| Matcher | `src/ppmt/core/matcher.py` | — | Complete |
| PPMT Engine | `src/ppmt/engine/ppmt.py` | ~800 | Complete |
| Prediction | `src/ppmt/engine/prediction.py` | ~400 | Complete |
| Signal | `src/ppmt/engine/signal.py` | ~500 | Complete |
| Paper Trader | `src/ppmt/engine/paper_trader.py` | 1200 | Complete |
| Realtime Trader | `src/ppmt/engine/realtime.py` | 530 | Complete (v0.9.0) |
| Weights | `src/ppmt/engine/weights.py` | ~200 | Complete |
| Monte Carlo (engine) | `src/ppmt/engine/monte_carlo.py` | ~390 | Complete |
| Monte Carlo (risk) | `src/ppmt/risk/monte_carlo.py` | — | Complete |
| Validator | `src/ppmt/engine/validator.py` | ~1200 | Complete |
| Risk Manager | `src/ppmt/risk/manager.py` | — | Complete |
| Position Sizing | `src/ppmt/risk/position_sizing.py` | — | Complete |
| Storage | `src/ppmt/data/storage.py` | — | Complete |
| Collector | `src/ppmt/data/collector.py` | — | Complete |
| Classifier | `src/ppmt/data/classifier.py` | — | Complete |
| CLI | `src/ppmt/cli/main.py` | ~900+ | Complete |
| Dashboard | `src/ppmt/dashboard/app.py` | — | Complete |

---

## One-Click Analysis Functions

The CLI provides these single-command analysis workflows:

| Command | Description | Phase |
|---------|-------------|-------|
| `ppmt validate-all` | Full P0+P1+P2 validation suite (100-point score) | v0.7.0 |
| `ppmt validate` | Out-of-sample train/test validation | v0.7.0 |
| `ppmt walk-forward` | Walk-forward analysis with expanding window | v0.7.0 |
| `ppmt monte-carlo` | Monte Carlo resampling simulation | v0.6.2 |
| `ppmt build` | Build trie + 2-pass bootstrap (default) | v0.5.0 |
| `ppmt run --paper` | Paper trading simulation | v0.4.0 |
| `ppmt replay` | Stream historical data through real-time pipeline | v0.9.0 |
| `ppmt live` | Connect to exchange for live trading | v0.9.0 |

All commands support `--symbol`, `--timeframe`, and appropriate configuration flags.

---

## Known Issues & Next Steps

### Pending
- [ ] Sync ppmt/ppmt/ submodule with ppmt/src/ active development code
- [x] Real-time trading mode — v0.9.0: replay + live (ccxt) modes
- [ ] Live trading bridge (paper -> live transition)
- [ ] Multi-asset portfolio mode
- [x] Regime detection integration (v0.8.0 connected, v0.8.1 fixed sizing multiplier)
- [x] Regime in node metadata (v0.10.0: regime_distribution + regime_match_score)
- [x] N4 regime-specific tries (v0.10.0: separate tries per regime + fallback)
- [ ] Validate v0.10.0 with full paper trading run (regime-aware confidence)
- [ ] Update CHANGELOG.md with v0.10.0

### Architectural Debt
- Two source trees exist: `/ppmt/src/ppmt/` (active dev) and `/ppmt/ppmt/src/ppmt/` (submodule mirror)
  - Active dev version is more complete (56KB paper_trader.py vs 23KB)
  - Need to reconcile and ensure git tracks the correct version
- pyproject.toml version synced to 0.8.1

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-08 | Bootstrap 2-pass for fresh tries | Without it, -18.77% P&L collapse |
| 2026-06-09 | Remove probability bonus | 10% confidence trades had 32.6% WR |
| 2026-06-10 | Raise min_confidence to 20% | 15-19% confidence trades had 38% WR |
| 2026-06-10 | Relax SHORT gate to 1.2x | 1.5x eliminated ALL SHORT trades |
| 2026-06-10 | Re-enable catastrophic at 8% | 5% too tight; 8% = 3x avg ATR |
| 2026-06-10 | Training z-score propagation | OOS requires consistent symbol mapping |
| 2026-06-11 | Single traceability document | User preference: avoid scattered info |
| 2026-06-11 | Apply regime multiplier to sizing signal | v0.8.0 detected regime but never used it for sizing |
| 2026-06-11 | Regime in metadata + N4 regime-specific tries | N4 was described as "Per-Asset+Regime" but received same data as N3. Now each regime has its own N4 trie. Nodes carry regime_distribution so confidence can be adjusted by regime match. Independent vs dependent nodes: patterns working across all regimes are robust, regime-specific patterns are conditional. |
