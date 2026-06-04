/**
 * Shared service instances for API routes
 *
 * Provides singleton clients from various data sources
 * so that API route handlers don't need to construct their own
 * cache/client instances on every request.
 *
 * Data sources (use CoinGecko for price/OHLCV data):
 *   - CoinGecko (PRIMARY - market data, prices, volumes, OHLCV) [FREE, no API key]
 *   - DexScreener (multi-chain token data, DEX pairs/pools) [FREE]
 *   - DexPaprika (35 chains, pool swaps, buy/sell ratios) [FREE]
 */

import {
  DexScreenerClient,
  HeliusClient,
  MoralisClient,
} from './universal-data-extractor';
import { UnifiedCache } from './source-cache';
import { CoinGeckoClient } from './coingecko-client';
import { DexPaprikaClient } from './dexpaprika-client';

// Shared cache instance (15-minute TTL)
const sharedCache = new UnifiedCache(15);

/** Shared DexScreener client for API routes */
export const dexScreenerClient = new DexScreenerClient(sharedCache);

/** Shared Helius client for Solana wallet intelligence */
export const heliusClient = new HeliusClient(
  process.env.HELIUS_API_KEY || '',
  sharedCache,
);

/** Shared Moralis client for EVM wallet history */
export const moralisClient = new MoralisClient(
  process.env.MORALIS_API_KEY || '',
  sharedCache,
);

/** Shared CoinGecko client for price/OHLCV data */
export const coinGeckoClient = new CoinGeckoClient();

/** Shared DexPaprika client for multi-chain DEX data (FREE, no API key) */
export const dexPaprikaClient = new DexPaprikaClient();

/** Shared cache for routes that need it directly */
export { sharedCache };
