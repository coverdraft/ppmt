# Task 2-c: StrategyStateManager Enforcement

## Agent: Subagent C
## Status: COMPLETED

## Summary

Implemented state enforcement in the Paper Trading Engine so that strategies in PAUSED or ERROR state cannot open new positions.

## Changes Made

### File: `/home/z/cryptoquant-terminal/src/lib/services/execution/paper-trading-engine.ts`

1. **Added `isStrategyAllowedToTrade()` private method** (lines ~1716-1776)
   - Resolves TradingSystem by name to get systemId
   - Queries latest StrategyStateHistory for that systemId
   - If no history: derives status from TradingSystem fields (mirrors StrategyStateManager.deriveStatus)
   - Returns `false` if PAUSED or ERROR, `true` otherwise
   - Fail-open: on any error, returns `true`
   - All decisions logged with `[PaperTrading:StateEnforcement]` prefix

2. **Guard in `runSingleScan()`** (line ~527)
   - Checks state before scan loop begins
   - Skips entire scan if strategy is PAUSED/ERROR

3. **Guard in `activateStrategy()`** (line ~770)
   - Checks state before AI Manager position opening
   - Returns `{ success: false, message }` with reason if blocked

4. **Guard in `openPosition()`** (line ~1099)
   - Defense-in-depth check before position creation
   - Returns `null` if blocked

## Design Decisions

- **Fail-open**: If state cannot be determined (no DB record, query error), trading is allowed. This prevents a state-check bug from accidentally blocking all trading.
- **No modification to StrategyStateManager**: Only consumes its state data.
- **Dynamic import pattern**: Uses `await import('@/lib/db')` consistent with existing codebase.
- **Defense in depth**: State is checked at both entry points (runSingleScan, activateStrategy) AND at the core openPosition method.
