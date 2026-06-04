/**
 * Shared service instances for API routes
 *
 * Single source of truth for all client singletons.
 * All clients use the global unifiedCache singleton for consistent caching
 * and rate-limit coordination.
 *
 * CANONICAL clients (DO NOT create new instances of these elsewhere):
 *   - CoinGecko → coingecko-client.ts (market data, OHLCV, trending)
 *   - DexScreener → dexscreener-client.ts (DEX pairs, liquidity, buy/sell)
 *   - DexPaprika → dexpaprika-client.ts (35 chains, pool data)
 *   - Binance → binance-client.ts (OHLCV, all timeframes, real volume)
 *
 * Specialty clients (require API keys):
 *   - Moralis → EVM+Solana wallet history
 *   - Helius → Solana wallet intelligence
 *   - Etherscan → EVM transaction backup
 *   - DefiLlama → Protocol analytics
 *   - CryptoDataDownload → Bulk CSV OHLCV
 *   - SQD, Dune, Footprint → (require paid API keys)
 */

import { CoinGeckoClient } from '@/lib/services/data-sources/coingecko-client';
import { DexPaprikaClient } from '@/lib/services/data-sources/dexpaprika-client';
import {
  HeliusClient,
  MoralisClient,
  DefiLlamaClient,
  EtherscanV2Client,
  CryptoDataDownloadClient,
} from './universal-data-extractor';
import { UnifiedCache } from '@/lib/services/data-sources/source-cache';

// Use a single shared cache instance for all clients that need the simple API
const sharedCache = new UnifiedCache(15);

// ============================================================
// CANONICAL SINGLETON CLIENTS
// These are the ONLY instances that should be used across the app.
// DO NOT create new instances of these classes elsewhere.
// ============================================================

/** Shared CoinGecko client — CANONICAL (market data, OHLCV, trending) */
export const coinGeckoClient = new CoinGeckoClient();

/** Shared DexPaprika client — CANONICAL (35 chains, pool data) */
export const dexPaprikaClient = new DexPaprikaClient();

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

/** Shared DefiLlama client for protocol analytics */
export const defiLlamaClient = new DefiLlamaClient(sharedCache);

/** Shared Etherscan V2 client for EVM TX backup */
export const etherscanV2Client = new EtherscanV2Client(
  process.env.ETHERSCAN_API_KEY || '',
  sharedCache,
);

/** Shared CryptoDataDownload client for bulk CSV OHLCV */
export const cryptoDataDownloadClient = new CryptoDataDownloadClient(sharedCache);

/** Shared cache for routes that need it directly */
export { sharedCache };

// Re-export canonical singletons from their own modules for convenience
export { dexScreenerClient } from '@/lib/services/data-sources/dexscreener-client';
export { binanceClient } from '@/lib/services/data-sources/binance-client';
