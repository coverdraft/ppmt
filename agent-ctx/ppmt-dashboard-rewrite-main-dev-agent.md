# Task: PPMT Command Center Dashboard Rewrite

## Agent: Main Dev Agent

## Summary
Completely rewrote `/home/z/my-project/src/app/page.tsx` to create a comprehensive PPMT Command Center dashboard with a 3-tab layout.

## Changes Made

### Architecture
- Single file component (`page.tsx`) with ~1500 lines
- 3-tab layout: Dashboard, Command Center, Backtesting Lab
- Dark theme: bg-[#0a0e17], borders bg-[#1e293b], monospace font throughout
- All existing API endpoints integrated with @tanstack/react-query hooks

### Tab 1: Dashboard (default)
- Top bar with PPMT logo, status indicator, tab navigation, clock
- Stat cards row: Total Assets, Total Candles, Total Patterns, Active Signals
- Left sidebar: Asset list with search filter, add symbol input, quick actions (Ingest/Build/Predict)
- Center: Candlestick chart (lightweight-charts v5) with timeframe buttons, signal markers
- Right panel: Prediction panel, 4-Level Trie visualization (N1-N4), Pattern Quality metrics
- Bottom: Signals feed (horizontal scrollable cards)

### Tab 2: Command Center
- Data Sufficiency Summary (green/amber/red indicators)
- Data Inventory Grid showing all 12 assets with completeness bars, trie level checkmarks
- Ingestion Controls: Add new symbol, Bulk ingest all missing timeframes
- Build Controls: Build trie for all assets, Refresh status
- Database Stats: DB size, total records, patterns, signal count

### Tab 3: Backtesting Lab
- Single Asset Backtest: Symbol/timeframe selectors, equity curve chart, stats grid, trades table
- Monte Carlo Simulation: Symbol/timeframe/sim count inputs, equity paths chart (20 sample paths), distribution histogram, VaR/CVaR stats
- Multi-Asset Comparison: Visual return bars, ranked results table

### API Hooks Created
- `usePPMTStatus()` - refetch every 30s
- `usePPMTSignals(symbol?)` - refetch every 15s
- `usePPMTPrediction(symbol)` - refetch every 60s
- `useOHLCV(symbol, timeframe)` - refetch every 60s
- `useMonteCarlo()` - mutation
- `useMultiBacktest()` - mutation
- `useBuildAsset()` - mutation
- `useIngestAsset()` - mutation

### Chart Components
- `CandlestickChart` - main price chart with volume, OHLCV tooltip, signal markers, responsive resize
- `EquityCurveChart` - line chart for backtest equity
- `MCEquityPathsChart` - multiple line series for MC simulation paths

### Types Added
- `MCResult`, `MCResponse`, `MultiAssetResult`, `MultiBacktestResponse`
- Extended `BacktestStats` with avg_pnl, best_trade, worst_trade, profit_factor

### Utility Functions
- `formatNumber`, `formatDate`, `formatTime`
- `getDataCompleteness` - 0-100 score with candle/tf/trie weighting
- `getCompletenessColor`, `getCompletenessBg`, `getCompletenessBarBg`
- `getDataSufficiency` - sufficient/partial/insufficient classification
- `getSignalTypeIcon`, `getSignalTypeColor`

## Lint Status
- Zero lint errors in src/app/page.tsx
- Dev server running on port 3000, serving pages correctly
