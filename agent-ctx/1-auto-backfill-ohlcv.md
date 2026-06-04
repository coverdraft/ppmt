# Task 1: Auto-Backfill OHLCV with Rate Limiting

**Agent:** Task-1 Agent
**Date:** 2025-03-05

## Summary

Implemented auto-backfill OHLCV with rate limiting in the backtest run route. When a backtest runs and there are no PriceCandle records in the DB, the system now automatically triggers an OHLCV backfill from CoinGecko before proceeding with the backtest. Also added a rate limiter class for CoinGecko API protection and a data quality validation function.

## Files Modified

1. **`/src/app/api/backtest/[id]/run/route.ts`** — Added auto-backfill logic
2. **`/src/lib/services/ohlcv-pipeline.ts`** — Added CoinGecko rate limiter
3. **`/src/lib/services/backtest-data-bridge.ts`** — Added data quality validation

## Changes Detail

### route.ts
- Changed `tokenData` from `const` to `let`
- Added import of `ohlcvPipeline` via dynamic import
- Added auto-backfill block: queries top 10 tokens from DB, falls back to well-known CoinGecko IDs, calls `backfillToken()` for each with 200ms delays, retries data loading, validates quality
- Updated error messages and response to include `autoBackfillAttempted: true`

### ohlcv-pipeline.ts
- Added `CoinGeckoRateLimiter` class (25 calls/min, sliding window)
- Created `coinGeckoRateLimiter` singleton
- Integrated `acquire()` calls before every CoinGecko API interaction in `fetchCoinGeckoOHLCV()`

### backtest-data-bridge.ts
- Added `validateTokenData()` method: rejects < 10 bars, <= 0 prices, high < low, sorts timestamps

## TypeScript Check
- No new errors introduced in modified files
- Pre-existing errors are unrelated (missing next/server types, etc.)
