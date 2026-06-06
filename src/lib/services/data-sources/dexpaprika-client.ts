/**
 * DexPaprika Client - CryptoQuant Terminal
 *
 * Hybrid data client that combines:
 *   - DexPaprika API: Token search across 35+ chains (FREE, no API key)
 *   - DexScreener API: Pool/pair data with buy/sell txn breakdowns
 *   - CoinGecko API: OHLCV candle data (free, no API key)
 *
 * Data routing:
 *   - Token search → DexPaprika /search endpoint
 *   - Pool search → DexScreener /latest/dex/search
 *   - Pool detail → DexScreener /latest/dex/pairs/{chain}/{pair}
 *   - Top pools → DexScreener token pair lookup
 *   - Buy/sell pressure → DexScreener txn data (h24/h6/h1 buys/sells)
 *   - Pool swaps → Simulated from DexScreener txn counts
 *   - OHLCV → CoinGecko API (free, no API key)
 *   - Smart money → Requires on-chain data (returns empty)
 *   - Cross-chain search → DexPaprika search + DexScreener pool lookup
 *
 * Integrated with UnifiedCache for TTL caching,
 * request deduplication, and rate-limit handling.
 */

import { unifiedCache, cacheKey, cacheKeyWithChain } from '../../unified-cache';
import { dexScreenerClient, type DexScreenerPair as CanonicalDexScreenerPair } from './dexscreener-client';

// ============================================================
// TYPES (backward-compatible)
// ============================================================

export interface DexPaprikaChain {
  id: string;
  name: string;
  type: string;
}

export interface DexPaprikaPool {
  id: string;
  chain: string;
  dexId: string;
  baseToken: {
    address: string;
    name: string;
    symbol: string;
  };
  quoteToken: {
    address: string;
    name: string;
    symbol: string;
  };
  priceUsd: string;
  volume: {
    h24: number;
    h6: number;
    h1: number;
  };
  txns: {
    h24: { buys: number; sells: number };
    h6: { buys: number; sells: number };
    h1: { buys: number; sells: number };
  };
  liquidity: {
    usd: number;
  };
  fdv: number;
  marketCap: number;
  pairCreatedAt: number;
  buyRatio24h: number;
  buyRatio6h: number;
  buyRatio1h: number;
}

export interface DexPaprikaPoolDetail extends DexPaprikaPool {
  info?: {
    imageUrl?: string;
    websites?: { url: string; label?: string }[];
    socials?: { type: string; url: string }[];
  };
}

export interface DexPaprikaSwap {
  txnHash: string;
  blockNumber: number;
  timestamp: string;
  maker: string;          // Wallet address
  amountIn: string;
  amountOut: string;
  tokenIn: {
    address: string;
    symbol: string;
  };
  tokenOut: {
    address: string;
    symbol: string;
  };
  type: 'buy' | 'sell';
  valueUsd: number;
  priceUsd: number;
}

export interface DexPaprikaToken {
  id: string;
  name: string;
  symbol: string;
  chain: string;
  priceUsd: number;
  priceChange24h: number;
  volume24h: number;
  marketCap: number;
  liquidity: number;
  fdv: number;
}

export interface DexPaprikaSearchParams {
  query: string;
  chain?: string;
  limit?: number;
}

export interface DexPaprikaTokenScreenParams {
  chain: string;
  minLiquidity?: number;
  minVolume24h?: number;
  minMarketCap?: number;
  maxMarketCap?: number;
  orderBy?: 'volume24h' | 'marketCap' | 'priceChange24h' | 'liquidity';
  orderDir?: 'asc' | 'desc';
  limit?: number;
  cursor?: string;
}

/** Network/chain info from DexPaprika */
export interface DexPaprikaNetwork {
  id: string;
  display_name: string;
  volume_usd_24h: number;
  txns_24h: number;
  pools_count: number;
}

/** Detailed token info from DexPaprika */
export interface DexPaprikaTokenDetail {
  id: string;
  name: string;
  symbol: string;
  chain: string;
  price_usd: number;
  volume_usd_24h: number;
  liquidity_usd: number;
  market_cap: number;
  summary?: Record<string, {
    buys: number;
    sells: number;
    buy_usd?: number;
    sell_usd?: number;
  }>;
  ath_price?: number;
  liquidity_usd_value?: number;
  pool_count?: number;
}

/** Screen options for multi-chain screening */
export interface DexPaprikaScreenOptions {
  minVolumeUsd?: number;
  minLiquidityUsd?: number;
  minBuySellRatio?: number;
  maxBuySellRatio?: number;
  limit?: number;
}

export interface BuySellPressure {
  poolId: string;
  chain: string;
  /** Ratio of buys to total transactions (0-1) */
  buyRatio24h: number;
  buyRatio6h: number;
  buyRatio1h: number;
  /** Absolute counts */
  buys24h: number;
  sells24h: number;
  buys6h: number;
  sells6h: number;
  buys1h: number;
  sells1h: number;
  /** Direction assessment */
  pressure24h: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  pressure6h: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  pressure1h: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  /** Acceleration: 1h vs 6h vs 24h trend */
  acceleration: 'INCREASING_BUY' | 'INCREASING_SELL' | 'STABLE' | 'MIXED';
}

export interface SmartMoneySwap {
  wallet: string;
  poolId: string;
  chain: string;
  swaps: DexPaprikaSwap[];
  netBuyAmount: number;
  netBuyValueUsd: number;
  firstSwapAt: Date;
  lastSwapAt: Date;
  swapCount: number;
  averageSizeUsd: number;
}

export interface DexPaprikaOHLCV {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// ============================================================
// INTERNAL TYPES for DexPaprika search response
// ============================================================

/** DexPaprika /search response shape */
interface DexPaprikaSearchResponse {
  tokens: DexPaprikaSearchToken[];
}

/** Individual token from DexPaprika /search */
interface DexPaprikaSearchToken {
  id: string;
  name: string;
  symbol: string;
  chain: string;
  type: string;
  status: string;
  decimals: number;
  total_supply: number | string | null;
  description: string | null;
  website: string | null;
  explorer: string | null;
  price_usd: number | string | null;
  liquidity_usd: number | string | null;
  volume_usd: number | string | null;
  price_usd_change: number | string | null;
}

/** DexPaprika /transactions response shape */
interface DexPaprikaTransactionsResponse {
  transactions: DexPaprikaTransaction[];
}

/** Individual transaction from DexPaprika /transactions */
interface DexPaprikaTransaction {
  id: string;
  log_index: number;
  transaction_index: number;
  factory_id: string;
  pool_id: string;
  chain: string;
  sender: string;
  recipient: string;
  token_0: string;
  token_0_symbol: string;
  token_1: string;
  token_1_symbol: string;
  amount_0: number;
  amount_1: number;
  volume_0: number;
  volume_1: number;
  price_0: number;
  price_1: number;
  price_0_usd: number;
  price_1_usd: number;
  created_at_block_number: number;
  created_at_block_hash?: string;
  created_at: string;
  canonical_chain?: boolean;
}

/** DexScreener pair type — re-exported from canonical client */
export type { DexScreenerPair } from './dexscreener-client';

// ============================================================
// CONSTANTS
// ============================================================

const DEXPAPRIKA_BASE_URL = 'https://api.dexpaprika.com';
const SOURCE = 'dexpaprika';
const SOURCE_CG = 'coingecko';

/** Map internal timeframes to CoinGecko days parameter */
const COINGECKO_TF_DAYS: Record<string, number> = {
  '30m': 1,
  '1h': 1,
  '4h': 7,
  '1d': 30,
  '1w': 90,
};

/** Map internal timeframes to duration in seconds */
const TIMEFRAME_SECONDS: Record<string, number> = {
  '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
  '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600,
  '12h': 43200, '1d': 86400, '1w': 604800,
};

/** Chains supported by DexPaprika search */
const SUPPORTED_CHAINS = [
  'ethereum', 'solana', 'bsc', 'polygon', 'arbitrum', 'optimism',
  'avalanche', 'fantom', 'base', 'linea', 'zksync', 'cronos',
  'celo', 'moonbeam', 'moonriver', 'harmony', 'aurora', 'kava',
  'metis', 'polygon_zkevm', 'mantle', 'scroll', 'zora', 'blast',
  'mode', 'taiko', 'sei', 'sui', 'aptos', 'near', 'cosmos',
  'osmosis', 'injective', 'celestia', 'starknet',
] as const;

export type DexPaprikaChainId = typeof SUPPORTED_CHAINS[number];

/** Map our internal chain IDs to DexPaprika/DexScreener chain IDs */
const CHAIN_ID_MAP: Record<string, DexPaprikaChainId | string> = {
  'SOL': 'solana',
  'ETH': 'ethereum',
  'BSC': 'bsc',
  'MATIC': 'polygon',
  'ARB': 'arbitrum',
  'OP': 'optimism',
  'AVAX': 'avalanche',
  'FTM': 'fantom',
  'BASE': 'base',
  'LINEA': 'linea',
  'ZKSYNC': 'zksync',
  'CRO': 'cronos',
  'CELO': 'celo',
  'GLMR': 'moonbeam',
  'MOVR': 'moonriver',
  'ONE': 'harmony',
  'AURORA': 'aurora',
  'KAVA': 'kava',
  'METIS': 'metis',
  'MANTLE': 'mantle',
  'SCROLL': 'scroll',
  'SEI': 'sei',
  'SUI': 'sui',
  'APT': 'aptos',
  'NEAR': 'near',
  // Passthrough for already-correct names
  'solana': 'solana',
  'ethereum': 'ethereum',
  'bsc': 'bsc',
  'polygon': 'polygon',
  'arbitrum': 'arbitrum',
  'optimism': 'optimism',
  'avalanche': 'avalanche',
  'fantom': 'fantom',
  'base': 'base',
};

// ============================================================
// DEXPAPRIKA CLIENT CLASS
// ============================================================

export class DexPaprikaClient {
  private dexpaprikaBaseUrl: string;

  constructor(
    dexpaprikaBaseUrl: string = DEXPAPRIKA_BASE_URL,
  ) {
    this.dexpaprikaBaseUrl = dexpaprikaBaseUrl;
  }

  // ----------------------------------------------------------
  // CHAINS
  // ----------------------------------------------------------

  /**
   * Get all supported chains.
   * Returns a static list (DexPaprika /chains endpoint is not available).
   * Cached for 24h (chains rarely change).
   */
  async getChains(): Promise<DexPaprikaChain[]> {
    return unifiedCache.getOrFetch(
      cacheKey(SOURCE, 'chains', 'all'),
      () => Promise.resolve(
        SUPPORTED_CHAINS.map(id => ({
          id,
          name: id.charAt(0).toUpperCase() + id.slice(1).replace(/_/g, ' '),
          type: 'EVM',
        }))
      ),
      SOURCE,
      86400_000, // 24h
    );
  }

  // ----------------------------------------------------------
  // POOLS (via DexScreener)
  // ----------------------------------------------------------

  /**
   * Get top pools for a chain.
   * Uses DexScreener search since DexPaprika doesn't have a pools endpoint.
   * Each pool includes buy/sell ratios from DexScreener txn data.
   */
  async getPools(
    chain: string,
    limit = 50,
    cursor?: string,
  ): Promise<{ pools: DexPaprikaPool[]; cursor?: string }> {
    const dpChain = this.toDexPaprikaChain(chain);
    const key = cacheKeyWithChain(SOURCE, 'pools', dpChain, `top:${limit}:${cursor ?? ''}`);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        // Use DexScreener search with chain name to find popular pairs
        const pairs = await this.fetchDexScreenerSearch(dpChain);
        const pools = pairs
          .filter(p => p.chainId === dpChain)
          .slice(0, limit)
          .map(p => this.mapDexScreenerPairToPool(p));

        // Enrich with buy ratios
        for (const pool of pools) {
          pool.buyRatio24h = this.calculateBuyRatio(pool.txns?.h24);
          pool.buyRatio6h = this.calculateBuyRatio(pool.txns?.h6);
          pool.buyRatio1h = this.calculateBuyRatio(pool.txns?.h1);
        }

        return { pools, cursor: undefined };
      },
      SOURCE,
      30_000, // 30s TTL for pools
    );
  }

  /**
   * Get detailed pool information.
   * Uses DexScreener pair endpoint.
   */
  async getPoolDetail(
    chain: string,
    poolId: string,
  ): Promise<DexPaprikaPoolDetail | null> {
    const dpChain = this.toDexPaprikaChain(chain);
    const key = cacheKeyWithChain(SOURCE, 'pool-detail', dpChain, poolId);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const pair = await this.fetchDexScreenerPair(dpChain, poolId);
        if (!pair) return null;
        return this.mapDexScreenerPairToPoolDetail(pair);
      },
      SOURCE,
      60_000, // 1 min
    );
  }

  /**
   * Search for pools across all or specific chains.
   * Uses DexScreener search (returns pairs with pool data).
   */
  async searchPools(params: DexPaprikaSearchParams): Promise<DexPaprikaPool[]> {
    const { query, chain, limit = 20 } = params;
    const dpChain = chain ? this.toDexPaprikaChain(chain) : undefined;
    const key = cacheKey(SOURCE, 'search-pools', `${query}:${dpChain ?? 'all'}:${limit}`);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const pairs = await this.fetchDexScreenerSearch(query);
        let filtered = pairs;

        if (dpChain) {
          filtered = pairs.filter(p => p.chainId === dpChain);
        }

        const pools = filtered
          .slice(0, limit)
          .map(p => this.mapDexScreenerPairToPool(p));

        // Enrich with buy ratios
        for (const pool of pools) {
          pool.buyRatio24h = this.calculateBuyRatio(pool.txns?.h24);
          pool.buyRatio6h = this.calculateBuyRatio(pool.txns?.h6);
          pool.buyRatio1h = this.calculateBuyRatio(pool.txns?.h1);
        }

        return pools;
      },
      SOURCE,
      300_000, // 5 min
    );
  }

  // ----------------------------------------------------------
  // TOKEN SEARCH (via DexPaprika)
  // ----------------------------------------------------------

  /**
   * Search for tokens using DexPaprika's /search endpoint.
   * Returns tokens with price, volume, and liquidity data.
   */
  async searchTokens(params: DexPaprikaSearchParams): Promise<DexPaprikaToken[]> {
    const { query, chain, limit = 20 } = params;
    const dpChain = chain ? this.toDexPaprikaChain(chain) : undefined;
    const key = cacheKey(SOURCE, 'search-tokens', `${query}:${dpChain ?? 'all'}:${limit}`);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const searchParams = new URLSearchParams({ query });
        if (dpChain) searchParams.set('chain', dpChain);

        const data = await this.fetchDexPaprika<DexPaprikaSearchResponse>(
          `/search?${searchParams}`
        );

        if (!data || !data.tokens) return [];

        return data.tokens
          .slice(0, limit)
          .map(t => this.mapDexPaprikaSearchToken(t));
      },
      SOURCE,
      300_000, // 5 min
    );
  }

  /**
   * Get top tokens for a chain using DexPaprika search.
   * Searches by chain name to find popular tokens on that chain.
   */
  async getTopTokens(chain: string, limit = 20): Promise<DexPaprikaToken[]> {
    const dpChain = this.toDexPaprikaChain(chain);
    const key = cacheKeyWithChain(SOURCE, 'top-tokens', dpChain, String(limit));

    return unifiedCache.getOrFetch(
      key,
      async () => {
        // Search with chain name to discover popular tokens
        const searchParams = new URLSearchParams({ query: dpChain, chain: dpChain });
        const data = await this.fetchDexPaprika<DexPaprikaSearchResponse>(
          `/search?${searchParams}`
        );

        if (!data || !data.tokens) return [];

        return data.tokens
          .slice(0, limit)
          .map(t => this.mapDexPaprikaSearchToken(t));
      },
      SOURCE,
      120_000, // 2 min
    );
  }

  // ----------------------------------------------------------
  // SWAPS (from DexPaprika /transactions API)
  // ----------------------------------------------------------

  /**
   * Get recent swaps for a pool.
   * Uses DexPaprika's /networks/{chain}/pools/{poolId}/transactions endpoint
   * which returns real on-chain swap data with wallet addresses.
   *
   * Supports: Solana, Base, Arbitrum, BSC, Ethereum, and other DexPaprika networks.
   */
  async getPoolSwaps(
    chain: string,
    poolId: string,
    limit = 50,
  ): Promise<DexPaprikaSwap[]> {
    const dpChain = this.toDexPaprikaChain(chain);
    const key = cacheKeyWithChain(SOURCE, 'pool-swaps', dpChain, `${poolId}:${limit}`);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        try {
          const data = await this.fetchDexPaprika<DexPaprikaTransactionsResponse>(
            `/networks/${dpChain}/pools/${poolId}/transactions?limit=${limit}`
          );

          if (!data || !data.transactions || data.transactions.length === 0) return [];

          return data.transactions
            .filter(tx => tx.sender && tx.id)
            .map(tx => this.mapDexPaprikaTransactionToSwap(tx));
        } catch (error) {
          // Pool transactions may not be available for all pools
          console.warn(`[DexPaprika] getPoolSwaps failed for ${dpChain}/${poolId}:`, error);
          return [];
        }
      },
      SOURCE,
      15_000, // 15s TTL for swaps (fresh data needed)
    );
  }

  /**
   * Get swaps for a specific wallet in a pool.
   * Filters the pool's transaction list for swaps involving the given wallet.
   */
  async getWalletSwaps(
    chain: string,
    poolId: string,
    walletAddress: string,
    limit = 50,
  ): Promise<DexPaprikaSwap[]> {
    const allSwaps = await this.getPoolSwaps(chain, poolId, Math.min(limit * 3, 200));
    const walletLower = walletAddress.toLowerCase();
    return allSwaps
      .filter(s => s.maker.toLowerCase() === walletLower)
      .slice(0, limit);
  }

  // ----------------------------------------------------------
  // BUY/SELL PRESSURE (from DexScreener txn data)
  // ----------------------------------------------------------

  /**
   * Calculate buy/sell pressure for a pool.
   * Uses DexScreener's txn breakdown (h24/h6/h1).
   */
  async getBuySellPressure(
    chain: string,
    poolId: string,
  ): Promise<BuySellPressure> {
    const dpChain = this.toDexPaprikaChain(chain);
    const key = cacheKeyWithChain(SOURCE, 'buy-sell-ratio', dpChain, poolId);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const pair = await this.fetchDexScreenerPair(dpChain, poolId);
        if (!pair || !pair.txns) {
          return this.emptyPressure(poolId, chain);
        }

        const h24 = pair.txns.h24 ?? { buys: 0, sells: 0 };
        const h6 = pair.txns.h6 ?? { buys: 0, sells: 0 };
        const h1 = pair.txns.h1 ?? { buys: 0, sells: 0 };

        const buyRatio24h = this.calculateBuyRatio(h24);
        const buyRatio6h = this.calculateBuyRatio(h6);
        const buyRatio1h = this.calculateBuyRatio(h1);

        const acceleration = this.determineAcceleration(buyRatio1h, buyRatio6h, buyRatio24h);

        return {
          poolId,
          chain: dpChain,
          buyRatio24h,
          buyRatio6h,
          buyRatio1h,
          buys24h: h24.buys,
          sells24h: h24.sells,
          buys6h: h6.buys,
          sells6h: h6.sells,
          buys1h: h1.buys,
          sells1h: h1.sells,
          pressure24h: this.assessPressure(buyRatio24h),
          pressure6h: this.assessPressure(buyRatio6h),
          pressure1h: this.assessPressure(buyRatio1h),
          acceleration,
        };
      },
      SOURCE,
      30_000, // 30s
    );
  }

  /**
   * Batch buy/sell pressure for multiple pools.
   * Optimized: if pools already have txn data (from getPools/searchPools),
   * uses that directly instead of fetching each pool individually.
   */
  async batchBuySellPressure(
    chain: string,
    poolIds: string[],
  ): Promise<BuySellPressure[]> {
    // Limit to 5 concurrent requests to avoid server crashes
    const batchLimit = 5;
    const limitedIds = poolIds.slice(0, batchLimit);
    const results = await Promise.allSettled(
      limitedIds.map(poolId => this.getBuySellPressure(chain, poolId))
    );
    return results
      .filter((r): r is PromiseFulfilledResult<BuySellPressure> => r.status === 'fulfilled')
      .map(r => r.value);
  }

  /**
   * Compute buy/sell pressure from pool objects that already have txn data.
   * No additional API calls needed - uses data already in DexPaprikaPool.
   */
  computePressureFromPools(pools: DexPaprikaPool[]): BuySellPressure[] {
    return pools.map(pool => {
      const h24 = pool.txns?.h24 ?? { buys: 0, sells: 0 };
      const h6 = pool.txns?.h6 ?? { buys: 0, sells: 0 };
      const h1 = pool.txns?.h1 ?? { buys: 0, sells: 0 };

      const buyRatio24h = this.calculateBuyRatio(h24);
      const buyRatio6h = this.calculateBuyRatio(h6);
      const buyRatio1h = this.calculateBuyRatio(h1);

      return {
        poolId: pool.id,
        chain: pool.chain,
        buyRatio24h,
        buyRatio6h,
        buyRatio1h,
        buys24h: h24.buys,
        sells24h: h24.sells,
        buys6h: h6.buys,
        sells6h: h6.sells,
        buys1h: h1.buys,
        sells1h: h1.sells,
        pressure24h: this.assessPressure(buyRatio24h),
        pressure6h: this.assessPressure(buyRatio6h),
        pressure1h: this.assessPressure(buyRatio1h),
        acceleration: this.determineAcceleration(buyRatio1h, buyRatio6h, buyRatio24h),
      };
    });
  }

  // ----------------------------------------------------------
  // TOKENS & SCREENING
  // ----------------------------------------------------------

  /**
   * Get token information.
   * Uses DexPaprika search to find the token, then DexScreener for pool data.
   */
  async getToken(
    chain: string,
    tokenAddress: string,
  ): Promise<DexPaprikaToken | null> {
    const dpChain = this.toDexPaprikaChain(chain);
    const key = cacheKeyWithChain(SOURCE, 'tokens', dpChain, tokenAddress);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        // First try DexPaprika search by address
        const tokens = await this.searchTokens({
          query: tokenAddress,
          chain: dpChain,
          limit: 1,
        });

        if (tokens.length > 0) return tokens[0];

        // Fallback: try DexScreener token endpoint
        try {
          const pairs = await this.fetchDexScreenerTokenPairs(tokenAddress);
          const matchingPair = pairs.find(p => p.chainId === dpChain) ?? pairs[0];
          if (!matchingPair) return null;

          return {
            id: matchingPair.baseToken.address,
            name: matchingPair.baseToken.name,
            symbol: matchingPair.baseToken.symbol,
            chain: matchingPair.chainId,
            priceUsd: parseFloat(matchingPair.priceUsd || '0'),
            priceChange24h: matchingPair.priceChange?.h24 ?? 0,
            volume24h: matchingPair.volume?.h24 ?? 0,
            marketCap: matchingPair.marketCap ?? 0,
            liquidity: matchingPair.liquidity?.usd ?? 0,
            fdv: matchingPair.fdv ?? 0,
          };
        } catch {
          return null;
        }
      },
      SOURCE,
      60_000, // 1 min
    );
  }

  /**
   * Screen tokens with filters.
   * Uses DexPaprika search + client-side filtering since
   * DexPaprika doesn't have a dedicated screening endpoint.
   */
  async screenTokens(params: DexPaprikaTokenScreenParams): Promise<{
    tokens: DexPaprikaToken[];
    cursor?: string;
  }> {
    const dpChain = this.toDexPaprikaChain(params.chain);
    const key = cacheKeyWithChain(
      SOURCE, 'token-screen', dpChain,
      `${params.minLiquidity ?? 0}:${params.minVolume24h ?? 0}:${params.minMarketCap ?? 0}:${params.maxMarketCap ?? 0}:${params.limit ?? 20}`
    );

    return unifiedCache.getOrFetch(
      key,
      async () => {
        // Use DexPaprika search to get tokens, then filter client-side
        const searchParams = new URLSearchParams({ query: dpChain, chain: dpChain });
        const data = await this.fetchDexPaprika<DexPaprikaSearchResponse>(
          `/search?${searchParams}`
        );

        if (!data || !data.tokens) return { tokens: [], cursor: undefined };

        let tokens = data.tokens.map(t => this.mapDexPaprikaSearchToken(t));

        // Apply filters
        if (params.minLiquidity) tokens = tokens.filter(t => t.liquidity >= params.minLiquidity!);
        if (params.minVolume24h) tokens = tokens.filter(t => t.volume24h >= params.minVolume24h!);
        if (params.minMarketCap) tokens = tokens.filter(t => t.marketCap >= params.minMarketCap!);
        if (params.maxMarketCap) tokens = tokens.filter(t => t.marketCap <= params.maxMarketCap!);

        // Apply ordering
        if (params.orderBy) {
          const dir = params.orderDir === 'asc' ? 1 : -1;
          tokens.sort((a, b) => {
            const aVal = a[params.orderBy!] ?? 0;
            const bVal = b[params.orderBy!] ?? 0;
            return (aVal - bVal) * dir;
          });
        }

        const limit = params.limit ?? 20;
        return {
          tokens: tokens.slice(0, limit),
          cursor: undefined,
        };
      },
      SOURCE,
      120_000, // 2 min
    );
  }

  // ----------------------------------------------------------
  // OHLCV (via CoinGecko)
  // ----------------------------------------------------------

  /**
   * Get OHLCV candles for a pool.
   * Neither DexPaprika nor DexScreener provides OHLCV data,
   * so this uses CoinGecko's free API (no API key needed).
   * Resolves the token from the pool to a CoinGecko coin ID,
   * then fetches OHLCV data.
   */
  async getOHLCV(
    chain: string,
    poolId: string,
    timeframe: string = '1h',
    _limit = 200,
  ): Promise<DexPaprikaOHLCV[]> {
    const dpChain = this.toDexPaprikaChain(chain);
    const key = cacheKeyWithChain(
      SOURCE_CG, 'ohlcv', dpChain,
      `${poolId}:${timeframe}`
    );

    return unifiedCache.getOrFetch(
      key,
      async () => {
        try {
          // Get pool detail to find the base token symbol/name
          const pair = await this.fetchDexScreenerPair(dpChain, poolId);
          if (!pair) return [];

          const tokenSymbol = pair.baseToken.symbol;
          const tokenName = pair.baseToken.name;

          // Resolve to CoinGecko coin ID via search
          const { coinGeckoClient } = await import('./coingecko-client');
          const searchResults = await coinGeckoClient.searchTokens(tokenSymbol);
          if (!searchResults || searchResults.length === 0) return [];

          // Find best match by symbol
          const match = searchResults.find(
            c => c.symbol?.toLowerCase() === tokenSymbol.toLowerCase() ||
                 c.name?.toLowerCase() === tokenName.toLowerCase()
          ) ?? searchResults[0];

          const coinId = match.id;

          // Determine CoinGecko days parameter from timeframe
          const days = COINGECKO_TF_DAYS[timeframe] ?? 7;

          // Fetch OHLCV from CoinGecko
          const cgCandles = await coinGeckoClient.getOHLCV(coinId, days);

          // Convert CoinGecko format to DexPaprikaOHLCV format
          return cgCandles.map(c => ({
            timestamp: Math.floor(c.timestamp / 1000), // Convert ms to seconds
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: 0, // CoinGecko OHLCV doesn't include volume
          }));
        } catch {
          // OHLCV may not be available for all pools
          return [];
        }
      },
      SOURCE_CG,
      60_000, // 1 min
    );
  }

  // ----------------------------------------------------------
  // CROSS-CHAIN SEARCH (via DexPaprika search)
  // ----------------------------------------------------------

  /**
   * Search for a token across ALL chains simultaneously.
   * Uses DexPaprika search (no chain filter) to find the token
   * on every chain, then enriches with DexScreener pool data.
   */
  async crossChainSearch(tokenAddress: string): Promise<DexPaprikaPool[]> {
    const key = cacheKey(SOURCE, 'cross-chain-search', tokenAddress);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        // Step 1: Search DexPaprika for the token across all chains
        const tokens = await this.searchTokens({ query: tokenAddress, limit: 10 });
        if (tokens.length === 0) return [];

        // Step 2: For each token found, get pool data from DexScreener
        const poolPromises = tokens.map(async (token) => {
          try {
            // Search DexScreener for pools containing this token
            const pairs = await this.fetchDexScreenerTokenPairs(token.id);
            return pairs
              .filter(p => p.chainId === token.chain)
              .slice(0, 3)
              .map(p => this.mapDexScreenerPairToPool(p));
          } catch {
            return [] as DexPaprikaPool[];
          }
        });

        const poolResults = await Promise.allSettled(poolPromises);
        const allPools: DexPaprikaPool[] = [];

        for (const result of poolResults) {
          if (result.status === 'fulfilled') {
            allPools.push(...result.value);
          }
        }

        // Enrich with buy ratios
        for (const pool of allPools) {
          pool.buyRatio24h = this.calculateBuyRatio(pool.txns?.h24);
          pool.buyRatio6h = this.calculateBuyRatio(pool.txns?.h6);
          pool.buyRatio1h = this.calculateBuyRatio(pool.txns?.h1);
        }

        return allPools;
      },
      SOURCE,
      120_000, // 2 min
    );
  }

  // ----------------------------------------------------------
  // SMART MONEY TRACKING (using DexPaprika /transactions API)
  // ----------------------------------------------------------

  /**
   * Track smart money activity in a pool.
   * Uses DexPaprika's /transactions endpoint to get real swap data with
   * wallet addresses, then groups by wallet to identify active traders.
   *
   * Smart money criteria:
   *   - Minimum swap count (default 2)
   *   - Minimum net value in USD (default $100)
   *
   * Results sorted by absolute net buy value (descending).
   */
  async trackSmartMoney(
    chain: string,
    poolId: string,
    minSwapCount = 2,
    minValueUsd = 100,
  ): Promise<SmartMoneySwap[]> {
    try {
      // Get recent swaps from the pool using the /transactions endpoint
      const swaps = await this.getPoolSwaps(chain, poolId, 50);
      if (!swaps || swaps.length === 0) return [];

      // Group swaps by wallet address (maker field)
      const walletMap = new Map<string, {
        address: string;
        buyCount: number;
        sellCount: number;
        totalBuyUsd: number;
        totalSellUsd: number;
        swaps: DexPaprikaSwap[];
      }>();

      for (const swap of swaps) {
        const addr = swap.maker;
        if (!addr) continue;

        if (!walletMap.has(addr)) {
          walletMap.set(addr, {
            address: addr,
            buyCount: 0,
            sellCount: 0,
            totalBuyUsd: 0,
            totalSellUsd: 0,
            swaps: [],
          });
        }
        const w = walletMap.get(addr)!;
        w.swaps.push(swap);

        const isBuy = swap.type === 'buy';
        const valueUsd = swap.valueUsd || 0;

        if (isBuy) {
          w.buyCount++;
          w.totalBuyUsd += valueUsd;
        } else {
          w.sellCount++;
          w.totalSellUsd += valueUsd;
        }
      }

      // Filter wallets that meet minimum criteria and build results
      const results: SmartMoneySwap[] = [];
      for (const [_, wallet] of walletMap) {
        const totalSwaps = wallet.buyCount + wallet.sellCount;
        if (totalSwaps < minSwapCount) continue;

        const netBuyValueUsd = wallet.totalBuyUsd - wallet.totalSellUsd;
        if (Math.abs(netBuyValueUsd) < minValueUsd) continue;

        const netBuyAmount = 0; // Cannot determine exact token amount from swap summary
        const totalValueUsd = wallet.totalBuyUsd + wallet.totalSellUsd;
        const timestamps = wallet.swaps.map(s => new Date(s.timestamp).getTime()).filter(t => !isNaN(t));

        results.push({
          wallet: wallet.address,
          poolId,
          chain,
          swaps: wallet.swaps,
          netBuyAmount,
          netBuyValueUsd,
          firstSwapAt: new Date(timestamps.length > 0 ? Math.min(...timestamps) : Date.now()),
          lastSwapAt: new Date(timestamps.length > 0 ? Math.max(...timestamps) : Date.now()),
          swapCount: totalSwaps,
          averageSizeUsd: totalSwaps > 0 ? totalValueUsd / totalSwaps : 0,
        });
      }

      // Sort by absolute net value descending
      results.sort((a, b) => Math.abs(b.netBuyValueUsd) - Math.abs(a.netBuyValueUsd));
      return results;
    } catch (error) {
      console.error('[DexPaprika] trackSmartMoney error:', error);
      return [];
    }
  }

  // ----------------------------------------------------------
  // SSE STREAMING (not available)
  // ----------------------------------------------------------

  /**
   * Create an SSE connection for real-time pool updates.
   * NOTE: DexPaprika SSE streaming is not available.
   * Returns null. For real-time data, use:
   *   - DexScreener webhook API
   *   - Direct RPC WebSocket subscriptions
   */
  createPoolStream(_chain: string, _poolId: string): EventSource | null {
    // DexPaprika SSE endpoint is not available
    return null;
  }

  // ----------------------------------------------------------
  // UTILITY
  // ----------------------------------------------------------

  /**
   * Get the DexPaprika chain ID for our internal chain ID.
   */
  toDexPaprikaChain(chain: string): string {
    return CHAIN_ID_MAP[chain] ?? chain.toLowerCase();
  }

  /**
   * Get all supported DexPaprika chain IDs.
   */
  getSupportedChains(): readonly string[] {
    return SUPPORTED_CHAINS;
  }

  /**
   * Check if a chain is supported by DexPaprika.
   */
  isChainSupported(chain: string): boolean {
    const dpChain = this.toDexPaprikaChain(chain);
    return (SUPPORTED_CHAINS as readonly string[]).includes(dpChain);
  }

  // ----------------------------------------------------------
  // PRIVATE: DexPaprika API
  // ----------------------------------------------------------

  private async fetchDexPaprika<T>(path: string): Promise<T> {
    const url = `${this.dexpaprikaBaseUrl}${path}`;

    if (unifiedCache.isRateLimited(SOURCE)) {
      const remaining = unifiedCache.getRateLimitRemaining(SOURCE);
      console.warn(`[DexPaprika] Rate limited, ${remaining}ms remaining`);
      throw new Error(`DexPaprika rate limited, retry in ${remaining}ms`);
    }

    try {
      const res = await fetch(url, {
        headers: {
          'Accept': 'application/json',
          'User-Agent': 'CryptoQuant-Terminal/1.0',
        },
      });

      if (res.status === 429) {
        const retryAfter = parseInt(res.headers.get('Retry-After') ?? '60', 10) * 1000;
        unifiedCache.markRateLimited(SOURCE, retryAfter);
        throw new Error(`DexPaprika rate limited, retry after ${retryAfter}ms`);
      }

      if (res.status === 404) {
        return null as T;
      }

      if (!res.ok) {
        throw new Error(`DexPaprika API error: ${res.status} ${res.statusText}`);
      }

      const data = await res.json();
      return data as T;
    } catch (error) {
      if (error instanceof TypeError && error.message.includes('fetch')) {
        console.error(`[DexPaprika] Network error for ${path}:`, error);
      }
      throw error;
    }
  }

  // ----------------------------------------------------------
  // PRIVATE: DexScreener API (delegates to canonical client)
  // ----------------------------------------------------------

  private async fetchDexScreenerSearch(query: string): Promise<CanonicalDexScreenerPair[]> {
    return dexScreenerClient.searchTokenByName(query);
  }

  private async fetchDexScreenerPair(
    chainId: string,
    pairAddress: string,
  ): Promise<CanonicalDexScreenerPair | null> {
    return dexScreenerClient.getPairData(chainId, pairAddress);
  }

  private async fetchDexScreenerTokenPairs(
    tokenAddress: string,
  ): Promise<CanonicalDexScreenerPair[]> {
    return dexScreenerClient.searchTokenPairs(tokenAddress);
  }

  // ----------------------------------------------------------
  // PRIVATE: Data mapping helpers
  // ----------------------------------------------------------

  /** Map a DexScreener pair to our DexPaprikaPool type */
  private mapDexScreenerPairToPool(pair: CanonicalDexScreenerPair): DexPaprikaPool {
    const txns = {
      h24: pair.txns?.h24 ?? { buys: 0, sells: 0 },
      h6: pair.txns?.h6 ?? { buys: 0, sells: 0 },
      h1: pair.txns?.h1 ?? { buys: 0, sells: 0 },
    };

    return {
      id: pair.pairAddress,
      chain: pair.chainId,
      dexId: pair.dexId,
      baseToken: {
        address: pair.baseToken.address,
        name: pair.baseToken.name,
        symbol: pair.baseToken.symbol,
      },
      quoteToken: {
        address: pair.quoteToken.address,
        name: pair.quoteToken.name,
        symbol: pair.quoteToken.symbol,
      },
      priceUsd: pair.priceUsd || '0',
      volume: {
        h24: pair.volume?.h24 ?? 0,
        h6: pair.volume?.h6 ?? 0,
        h1: pair.volume?.h1 ?? 0,
      },
      txns,
      liquidity: {
        usd: pair.liquidity?.usd ?? 0,
      },
      fdv: pair.fdv ?? 0,
      marketCap: pair.marketCap ?? 0,
      pairCreatedAt: pair.pairCreatedAt ?? 0,
      buyRatio24h: this.calculateBuyRatio(txns.h24),
      buyRatio6h: this.calculateBuyRatio(txns.h6),
      buyRatio1h: this.calculateBuyRatio(txns.h1),
    };
  }

  /** Map a DexScreener pair to our DexPaprikaPoolDetail type */
  private mapDexScreenerPairToPoolDetail(pair: CanonicalDexScreenerPair): DexPaprikaPoolDetail {
    const pool = this.mapDexScreenerPairToPool(pair);

    let info: DexPaprikaPoolDetail['info'];
    if (pair.info) {
      info = {
        imageUrl: pair.info.imageUrl,
        websites: pair.info.websites?.map(w => ({ url: w.url, label: w.label })),
        socials: pair.info.socials?.map(s => ({ type: s.type, url: s.url })),
      };
    }

    return { ...pool, info };
  }

  /** Map a DexPaprika search token to our DexPaprikaToken type */
  private mapDexPaprikaSearchToken(t: DexPaprikaSearchToken): DexPaprikaToken {
    return {
      id: t.id,
      name: t.name || '',
      symbol: t.symbol || '',
      chain: t.chain || '',
      priceUsd: typeof t.price_usd === 'number' ? t.price_usd : parseFloat(String(t.price_usd ?? '0')) || 0,
      priceChange24h: typeof t.price_usd_change === 'number' ? t.price_usd_change : parseFloat(String(t.price_usd_change ?? '0')) || 0,
      volume24h: typeof t.volume_usd === 'number' ? t.volume_usd : parseFloat(String(t.volume_usd ?? '0')) || 0,
      marketCap: 0, // Not provided by DexPaprika search
      liquidity: typeof t.liquidity_usd === 'number' ? t.liquidity_usd : parseFloat(String(t.liquidity_usd ?? '0')) || 0,
      fdv: 0, // Not provided by DexPaprika search
    };
  }

  /** Map a DexPaprika /transactions response to our DexPaprikaSwap type */
  private mapDexPaprikaTransactionToSwap(tx: DexPaprikaTransaction): DexPaprikaSwap {
    // Determine buy/sell based on amount signs:
    // In DexPaprika, amount_0 > 0 means the pool received token_0 (someone sold token_0)
    // amount_0 < 0 means the pool sent token_0 (someone bought token_0)
    // We consider token_0 as the "base" token.
    // If the user (sender) is buying the base token: they send quote (token_1) and receive base (token_0)
    // From the pool's perspective: amount_0 < 0 (pool sends base), amount_1 > 0 (pool receives quote)
    // So: amount_0 < 0 → buy of token_0 by the sender
    const isBuy = tx.amount_0 < 0;

    // Determine which token the sender is sending in and receiving out
    const tokenIn = isBuy
      ? { address: tx.token_1, symbol: tx.token_1_symbol }
      : { address: tx.token_0, symbol: tx.token_0_symbol };
    const tokenOut = isBuy
      ? { address: tx.token_0, symbol: tx.token_0_symbol }
      : { address: tx.token_1, symbol: tx.token_1_symbol };

    // Amount in = the amount the sender sends (absolute value)
    const amountIn = isBuy
      ? String(Math.abs(tx.volume_1))
      : String(Math.abs(tx.volume_0));
    const amountOut = isBuy
      ? String(Math.abs(tx.volume_0))
      : String(Math.abs(tx.volume_1));

    // Value in USD: use the USD price of the sent token times volume
    const valueUsd = isBuy
      ? Math.abs(tx.volume_1) * (tx.price_1_usd || 0)
      : Math.abs(tx.volume_0) * (tx.price_0_usd || 0);

    // Price per unit of the token being acquired
    const priceUsd = isBuy
      ? (tx.price_0_usd || 0)
      : (tx.price_1_usd || 0);

    return {
      txnHash: tx.id,
      blockNumber: tx.created_at_block_number,
      timestamp: tx.created_at,
      maker: tx.sender,
      amountIn,
      amountOut,
      tokenIn,
      tokenOut,
      type: isBuy ? 'buy' : 'sell',
      valueUsd,
      priceUsd,
    };
  }

  // ----------------------------------------------------------
  // PRIVATE: Calculation helpers
  // ----------------------------------------------------------

  private calculateBuyRatio(txns: { buys: number; sells: number }): number {
    const total = txns.buys + txns.sells;
    if (total === 0) return 0.5; // Neutral
    return txns.buys / total;
  }

  private assessPressure(buyRatio: number): 'BULLISH' | 'BEARISH' | 'NEUTRAL' {
    if (buyRatio > 0.6) return 'BULLISH';
    if (buyRatio < 0.4) return 'BEARISH';
    return 'NEUTRAL';
  }

  private determineAcceleration(
    ratio1h: number,
    ratio6h: number,
    ratio24h: number,
  ): 'INCREASING_BUY' | 'INCREASING_SELL' | 'STABLE' | 'MIXED' {
    const shortTermTrend = ratio1h - ratio6h;
    const longTermTrend = ratio6h - ratio24h;

    if (shortTermTrend > 0.05 && longTermTrend > 0.05) return 'INCREASING_BUY';
    if (shortTermTrend < -0.05 && longTermTrend < -0.05) return 'INCREASING_SELL';
    if (Math.abs(shortTermTrend) < 0.05 && Math.abs(longTermTrend) < 0.05) return 'STABLE';
    return 'MIXED';
  }

  private emptyPressure(poolId: string, chain: string): BuySellPressure {
    return {
      poolId,
      chain,
      buyRatio24h: 0.5, buyRatio6h: 0.5, buyRatio1h: 0.5,
      buys24h: 0, sells24h: 0, buys6h: 0, sells6h: 0, buys1h: 0, sells1h: 0,
      pressure24h: 'NEUTRAL', pressure6h: 'NEUTRAL', pressure1h: 'NEUTRAL',
      acceleration: 'STABLE',
    };
  }

  // ----------------------------------------------------------
  // MULTI-CHAIN SCREENER STUBS
  // ----------------------------------------------------------

  /**
   * Get top pools by volume for a given chain.
   * Uses DexScreener search since DexPaprika doesn't have a volume-sorted endpoint.
   */
  async getTopPoolsByVolume(networkId: string, maxPools: number = 10): Promise<DexPaprikaPool[]> {
    try {
      const result = await this.getPools(networkId, maxPools);
      return result.pools;
    } catch {
      return [];
    }
  }

  /**
   * Get detailed token info.
   * Uses DexScreener token lookup as a proxy.
   */
  async getTokenDetail(networkId: string, tokenId: string): Promise<DexPaprikaTokenDetail | null> {
    try {
      const pairs = await this.fetchDexScreenerTokenPairs(tokenId);
      const pair = pairs[0];
      if (!pair) return null;
      return {
        id: tokenId,
        name: pair.baseToken.name,
        symbol: pair.baseToken.symbol,
        chain: pair.chainId,
        price_usd: parseFloat(pair.priceUsd || '0'),
        volume_usd_24h: pair.volume?.h24 ?? 0,
        liquidity_usd: pair.liquidity?.usd ?? 0,
        market_cap: pair.marketCap ?? pair.fdv ?? 0,
      };
    } catch {
      return null;
    }
  }

  /**
   * Get all supported networks/chains.
   * Returns a static list with basic stats.
   */
  async getNetworks(): Promise<DexPaprikaNetwork[]> {
    return SUPPORTED_CHAINS.map(id => ({
      id,
      display_name: id.charAt(0).toUpperCase() + id.slice(1).replace(/_/g, ' '),
      volume_usd_24h: 0,
      txns_24h: 0,
      pools_count: 0,
    }));
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const dexPaprikaClient = new DexPaprikaClient();
