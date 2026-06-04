# Task 2: Add OHLCV Backfill to /api/seed Endpoint

**Agent:** Task-2 Agent  
**Date:** 2026-03-05

## Summary
Modified the `/api/seed` endpoint to fetch OHLCV candle data for top tokens via `ohlcvPipeline.backfillToken()`, ensuring backtests work immediately after seeding. Also fixed chain/address mismatch where 0x Ethereum addresses were incorrectly assigned chain="SOL".

## Changes Made

### File: `/src/app/api/seed/route.ts`

1. **Added `ohlcvPipeline` import** — `import { ohlcvPipeline } from '@/lib/services/ohlcv-pipeline';`

2. **Added `ohlcvBackfill` field to `SeedResult`** — New nested object: `{ tokensProcessed, totalCandlesStored, failedTokens }`

3. **Added `inferChainFromAddress()` helper** — Returns 'ETH' for 0x-prefixed addresses, otherwise returns the fallback chain

4. **Fixed Step 5 (CoinGecko) chain assignment** — Added cross-check with `inferChainFromAddress(tokenAddress, chain)` after platform-based chain detection

5. **Added chain correction in Step 6** — Queries for tokens with 0x addresses incorrectly set to chain="SOL" and updates them to "ETH"

6. **Added Step 9b: OHLCV Pipeline Backfill** — After existing Step 9:
   - Takes top 20 tokens by volume24h
   - Calls `ohlcvPipeline.backfillToken(tokenAddress, chain, ['1h', '4h', '1d'])` for each
   - 250ms delay between tokens for rate limiting
   - Logs progress per token
   - Tracks tokens processed, total candles stored, and failed tokens
   - Continues on individual token failure

## Verification
- TypeScript: No errors in seed/route.ts
- Lint: Only pre-existing `any` type warnings (no new issues)
