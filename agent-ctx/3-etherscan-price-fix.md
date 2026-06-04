# Task 3: Fix Etherscan Client Hardcoded Prices

## Summary

Fixed three bugs in `/home/z/my-project/cryptoquant-terminal/src/lib/services/etherscan-client.ts` where hardcoded prices caused PnL calculations to return incorrect values.

## Changes Made

### 1. Added CoinGecko client import (line 28)
```typescript
import { coinGeckoClient } from './coingecko-client';
```

### 2. Added price cache and TTL constant (lines 155-156)
```typescript
private priceCache = new Map<string, { price: number; timestamp: number }>();
private static PRICE_CACHE_TTL = 5 * 60 * 1000; // 5 minutes
```

### 3. Added `getTokenPrice()` private helper method (lines 813-832)
- Takes token address and symbol
- Uses `coinGeckoClient.getTokenByContract('ETH', tokenAddress)` to fetch price
- Caches result for 5 minutes
- Falls back to $0 (instead of hardcoded $1) if price unavailable

### 4. Added `getEthPrice()` private helper method (lines 839-857)
- Uses `coinGeckoClient.getTokenDetail('ethereum')` to fetch ETH price
- Caches result for 5 minutes
- Falls back to $3000 only if API call fails

### 5. Replaced hardcoded $1 token price (was line 648)
**Before:**
```typescript
const avgBuyPrice = token.totalReceived > 0 ? 1 : 0; // Placeholder
const valueUsd = balance * avgBuyPrice;
```
**After:**
```typescript
const priceUsd = await this.getTokenPrice(token.address, token.symbol);
const valueUsd = balance * (priceUsd > 0 ? priceUsd : 0);
```

### 6. Replaced hardcoded $3000 ETH price (was line 534)
**Before:**
```typescript
const totalValueUsd = gasEth * 3000;
```
**After:**
```typescript
const ethPrice = await this.getEthPrice();
const totalValueUsd = gasEth * ethPrice;
```

### 7. Fixed `getTopTokenHolders` return type (line 411)
**Before:** `Promise<never[]>`
**After:** `Promise<DiscoveredTrader[]>`

## Verification

- `npx tsc --noEmit` confirms no type errors in etherscan-client.ts
- Pre-existing errors in other files are unrelated to this change
