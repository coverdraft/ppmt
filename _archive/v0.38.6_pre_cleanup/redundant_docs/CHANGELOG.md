# PPMT Changelog

All notable changes to the Progressive Pattern Matching Trie (PPMT) project.

## [v0.10.0] - 2026-06-11

### Added - Regime-in-Metadata Architecture
- **BlockLifecycleMetadata**: `regime` and `regime_distribution` fields
  - Every trie node now knows which market regimes it was observed in
  - `regime` = primary regime (most observations)
  - `regime_distribution` = count per regime (e.g., `{trending_up: 45, ranging: 30}`)
- **`regime_independence` property**: Normalized Shannon entropy of regime distribution
  - Independent nodes (spread across regimes) ~ 1.0 = works anywhere
  - Dependent nodes (concentrated in one regime) ~ 0.0 = needs specific regime
- **`regime_match_score(current_regime)` method**: Confidence multiplier (0.4-1.5)
  - Current regime matches primary -> boost (up to 1.2x)
  - Current regime has no observations -> penalize (0.6x)
  - Independent nodes less affected (flattened toward 1.0)
  - Dependent nodes more affected (keep full base score)
- **N4 regime-specific tries**: `trie_n4_regime: dict[str, PPMTTrie]`
  - Separate trie per regime: trending_up, trending_down, ranging, volatile
  - `trie_n4_fallback` for when regime-specific trie has < 50 patterns
  - Makes N4 truly "Per-Asset+Regime" instead of a copy of N3
- **PPMT.build()**: Detects regime at each pattern position via `RegimeDetector.detect_series()`
  - Passes `regime` parameter to all `insert_with_observations()` calls
  - N1, N2, N3 also receive regime context (stored in metadata)
  - N4 inserts into the appropriate regime-specific trie
- **PredictionEngine.predict()**: Accepts `current_regime` parameter
  - Confidence adjusted by `regime_match_score`
- **_record_observation()**: Accepts `regime` parameter
  - Living Trie accumulates regime distribution from trading observations
- **propagate_metadata()**: Aggregates regime distribution from children
  - Intermediate nodes inherit regime context from their descendants

### Changed
- N4 changed from single `PPMTTrie` to `dict[str, PPMTTrie]` + fallback
- `update_from_observation()` accepts optional `regime` parameter
- `insert_with_observations()` accepts optional `regime` parameter
- `_record_observation()` accepts optional `regime` parameter

## [v0.9.0] - 2026-06-11

### Added - Real-Time Trading Engine
- **engine/realtime.py**: Complete real-time trading engine with incremental SAX
  - `ReplayConfig`: Replay historical data through streaming pipeline
  - `LiveConfig`: Connect to exchange via ccxt for live trading
  - `RealtimeTrader`: Processes candles one at a time through incremental SAX encoder
  - Streaming pattern buffer for real-time pattern matching
  - Regime-aware position sizing applied in real-time
  - Callbacks: `on_signal`, `on_trade`, `on_candle` for external integrations
  - Speed control: 0=max speed, 1=real-time, 10=10x playback
- **CLI `ppmt replay`**: Replay historical data through streaming pipeline
  - Validates the incremental SAX + real-time signal pipeline
  - `--speed` for configurable playback speed
  - `--regime-aware` for regime-based position sizing
- **CLI `ppmt live`**: Connect to exchange and trade in real-time
  - Requires ccxt (`pip install ccxt>=4.0.0`, Python 3.10+)
  - `--testnet` (default) for exchange paper trading
  - `--dry-run` (default) processes signals without executing orders
  - `--execute` with confirmation prompt for real trading
  - 30-second polling interval for new candles
- **`ppmt run` (without --paper)**: Now redirects to replay mode
  - Previously showed a "TODO" message
  - Now actually runs the streaming pipeline

### Fixed
- BUG-006 (v0.8.1): Regime multiplier applied to sizing signal

## [v0.8.1] - 2026-06-11

### Fixed
- **Critical**: Regime multiplier was NOT being applied to position sizing
  - Regime was detected at SAX boundaries and recorded in trades but never scaled the sizing signal
  - Now `metadata_sizing_signal` and `sizing_multiplier` are multiplied by regime factor
  - trending_up: 1.2x, ranging: 1.0x, trending_down: 0.6x, volatile: 0.4x
- pyproject.toml version synced 0.7.0 → 0.8.1
- CLI --version synced to 0.8.1

## [v0.8.0] - 2026-06-11

### Added - Regime-Aware Position Sizing
- **RegimeDetector** now connected to PaperTrader pipeline (was standalone, never called)
- Market regime detected at each SAX boundary: trending_up, trending_down, ranging, volatile
- Regime feeds into position sizing multipliers:
  - trending_up: 1.2x (favorable, increase exposure)
  - ranging: 1.0x (neutral, base sizing)
  - trending_down: 0.6x (unfavorable, reduce exposure)
  - volatile: 0.4x (dangerous, minimal exposure)
- `PaperTrade.regime` and `PaperTrade.regime_confidence` fields track regime at entry
- Regime column added to trades table output
- CLI flag: `--regime-aware/--no-regime-aware` (default: enabled)
- Regime distribution statistics printed after simulation
- `RegimeDetector` and `RegimeInfo` exported from `ppmt.core.__init__`

### Changed
- Regime detection uses last 200 candles with 50-candle lookback
- Hurst exponent + volatility + trend strength combined for classification
- `AdvancedPositionSizer` already had regime support — now actually receives regime data

### Sync & Maintenance
- Synced active dev code (`ppmt/src/ppmt/`) to submodule mirror (`ppmt/ppmt/src/ppmt/`)
- Created `TRACEABILITY.md` as single source of truth document
- Updated `pyproject.toml` version from 0.3.0 to 0.7.1
- Verified all previously reported bugs already fixed in current code

## [v0.7.1] - 2026-06-11

### Changed
- Converted ppmt/ from git submodule to regular tracked directory
- Updated .gitignore with egg-info exclusion
- Force-pushed consolidated project to GitHub

## [v0.7.0] - 2026-06-11

### Added - Phase 1: Python Validation Engine
- **validator.py**: `ValidationEngine` with P0 (OOS), P1 (Monte Carlo), P2 (Walk-Forward)
- Composite 100-point scoring system: P0=40pts, P1=30pts, P2=30pts
- Verdict system: ROBUST (≥70), MARGINAL (≥45), OVERFIT (<45), INSUFFICIENT_DATA
- `MonteCarloEngine`: numpy-based resampling with `simulate_from_trades()` and `simulate_from_params()`
- CLI command `ppmt validate-all --json-output` for API bridge integration
- V7.9 normalization fix: training z-score stats propagate to test encoding for consistent SAX symbol mapping

### Added - Phase 2: API Bridge
- `/api/validation/run` route calling `ppmt validate-all` via subprocess
- 5-minute timeout for full validation suite
- Clean JSON response pipeline for dashboard consumption

### Added - Phase 3: Dashboard
- `validation-suite-panel.tsx`: Full one-click validation UI
- `VerdictBanner` with animated score bar and color-coded recommendation
- `ScoreBreakdown` cards (P0/P1/P2)
- P0: OOS comparison table (IS vs OOS metrics) + equity curve SVG chart
- P1: MC metrics cards (profit prob, risk of ruin, P95 DD, median equity) + confidence intervals table
- P2: WFE trend chart SVG + window-by-window table with degradation metrics
- Configuration panel with symbol, train ratio, MC simulations, WF windows, seed controls
- Collapsible config, loading state, empty state with explanatory cards

### Scoring Details
- P0 Out-of-Sample (40pts max):
  - OOS profitability: 20pts
  - OOS win rate: 10pts
  - OOS ratio (WFE): 10pts
- P1 Monte Carlo (30pts max):
  - Profit probability: 15pts
  - Risk of ruin: 10pts
  - P95 drawdown: 5pts
- P2 Walk-Forward (30pts max):
  - Aggregate WFE: 10pts
  - Consistency: 10pts
  - Low degradation: 10pts

## [v0.6.3] - 2026-06-10

### Added
- SAX z-score propagation: `encode_with_normalization()` method
- Training stats (paa_mean, paa_std) stored in engine state
- Strict OOS validation reveals look-ahead bias from z-score recalculation

### Changed
- PaperTrader: added `paa_mean` and `paa_std` config options
- Build CLI: `--train-ratio` computes and stores PAA normalization stats

## [v0.6.2] - 2026-06-10

### Added
- Out-of-sample validation via `ppmt validate` CLI command
- `end_offset` parameter in PaperTraderConfig for OOS testing
- Walk-forward analysis via `ppmt walk-forward` CLI command
- Monte Carlo simulation via `ppmt monte-carlo` CLI command
- Catastrophic loss protection re-enabled at 8% threshold

### Changed
- `min_confidence` raised from 0.15 to 0.20 (Cycle 5 regression analysis)
- SHORT gate relaxed from 1.5x to 1.2x: `max(conf*1.2, 0.20)`
- Validation results: OOS +295%, MC 0% ruin, WF 6/6 profitable

### Performance
| Cycle | P&L | WR | Sharpe | Max DD |
|-------|-----|-----|--------|--------|
| 6a | +155,150% | 89.2% | 19.96 | 8.3% |
| 6b | +45,035% | 90.2% | 20.93 | 3.7% |

## [v0.6.1] - 2026-06-09

### Fixed
- Removed probability bonus that caused v0.6.0 regression
- Reverted SHORT gate to original
- Kept min_confidence at 15% from v0.6.0

## [v0.6.0] - 2026-06-09

### Added
- Probability bonus feature (later removed in v0.6.1 due to regression)
- min_confidence raised from 10% to 15%

### Regressed
- P&L dropped from +1434% (Cycle 4) to +86.82% (Cycle 5)
- Win rate dropped from 50.5% to 44.6%

## [v0.5.0] - 2026-06-08

### Added
- Bootstrap paper trading: 2-pass simulation to populate Living Trie metadata
- Fresh tries now have meaningful trading observations from day one
- Without bootstrap: system collapses to -18.77% P&L

## [v0.4.0] - Earlier

### Added
- Living Trie concept: Trie learns from own trading results
- Multi-level trie: N1-N4 with AdaptiveWeights (N1=5%, N2=20%, N3=35%, N4=40%)
- SAX encoding for K-line to discrete symbol conversion
- Pattern matching: exact → fuzzy → prefix fallback
- Risk management: ATR-based SL/TP, trailing stops, position sizing
