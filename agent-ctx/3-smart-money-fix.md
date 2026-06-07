# Task 3: Fix Smart Money Panel - Wallets Don't Load

## Summary
Fixed the TraderIntelligencePanel showing "No trader data available" by implementing real wallet discovery through the DexPaprika `/transactions` API endpoint.

## Key Discovery
DexPaprika API has an undocumented endpoint: `GET /networks/{chain}/pools/{poolId}/transactions`
- Returns real on-chain swap data with wallet addresses (sender/recipient fields)
- Works for: Solana, Base, Arbitrum, BSC (tested)
- Was NOT being used before — both `getPoolSwaps()` and `trackSmartMoney()` were stubs returning `[]`

## Changes Made

### 1. dexpaprika-client.ts
- **Added interfaces**: `DexPaprikaTransactionsResponse`, `DexPaprikaTransaction` — match DexPaprika API response format
- **Implemented `getPoolSwaps()`**: Calls `/networks/{chain}/pools/{poolId}/transactions?limit={limit}`, maps to `DexPaprikaSwap[]` via `mapDexPaprikaTransactionToSwap()`, 15s cache TTL
- **Implemented `getWalletSwaps()`**: Filters `getPoolSwaps()` results by wallet address
- **Added `mapDexPaprikaTransactionToSwap()`**: Converts DexPaprika transaction to `DexPaprikaSwap`:
  - Buy/sell detection from `amount_0` sign (negative = buy of base token)
  - Maps `sender` → `maker` (wallet address)
  - Computes `valueUsd` from volume × price_usd
- **Implemented `trackSmartMoney()`**: Groups swaps by wallet, filters by min count/value, computes buy/sell metrics, sorts by net value

### 2. trader-intelligence.tsx
- **Added auto-sync useEffect on mount**: Checks `/api/traders?limit=1` after 1.5s delay; if no traders exist, auto-triggers `handleSyncTraders()`
- Uses `useRef` guard to prevent multiple triggers
- Fail-open: if the check fails, syncs anyway

### 3. smart-money-sync/route.ts
- **Enhanced fallback logic** with 3 attempts:
  1. `trackSmartMoney()` with existing poolId
  2. `getPoolSwaps()` with existing poolId (DexScreener pair address)
  3. DexPaprika pool discovery: search by token symbol, then try `getPoolSwaps()` with discovered DexPaprika pool IDs

## Verification
- TypeScript: No new errors (verified via `tsc --noEmit`)
- ESLint: No new errors (verified via `bun run lint`)
- All pre-existing errors are in unrelated files
