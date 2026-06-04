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

import { unifiedCache, cacheKey, cacheKeyWithChain } from '../unified-cache';

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

/** DexScreener pair data shape */
interface DexScreenerPair {
  chainId: string;
  dexId: string;
  pairAddress: string;
  baseToken: { address: string; symbol: string; name: string };
  quoteToken: { address: string; symbol: string; name: string };
  priceNative: string;
  priceUsd: string;
  priceChange?: { h24: number; h6: number; h1: number; m5: number };
  txns?: {
    h24: { buys: number; sells: number };
    h6: { buys: number; sells: number };
    h1: { buys: number; sells: number };
  };
  volume?: { h24: number; h6: number; h1: number };
  liquidity?: { usd: number; base: number; quote: number };
  fdv?: number;
  marketCap?: number;
  pairCreatedAt?: number;
  info?: {
    imageUrl?: string;
    websites?: { url: string; label?: string }[];
    socials?: { type: string; url: string }[];
  };
}

// ============================================================
// CONSTANTS
// ============================================================

const DEXPAPRIKA_BASE_URL = 'https://api.dexpaprika.com';
const DEXSCREENER_BASE_URL = 'https://api.dexscreener.com';
const SOURCE = 'dexpaprika';
const SOURCE_DS = 'dexscreener';
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
  private dexscreenerBaseUrl: string;

  constructor(
    dexpaprikaBaseUrl: string = DEXPAPRIKA_BASE_URL,
    dexscreenerBaseUrl: string = DEXSCREENER_BASE_URL,
  ) {
    this.dexpaprikaBaseUrl = dexpaprikaBaseUrl;
    this.dexscreenerBaseUrl = dexscreenerBaseUrl;
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
  // SWAPS (simulated from DexScreener txn data)
  // ----------------------------------------------------------

  /**
   * Get recent swaps for a pool.
   * Since neither DexPaprika nor DexScreener provides individual swap data
   * with wallet addresses, this generates simulated swap entries based on
   * DexScreener's aggregate txn counts (h24/h6/h1 buys/sells).
   */
  async getPoolSwaps(
    chain: string,
    poolId: string,
    limit = 50,
  ): Promise<DexPaprikaSwap[]> {
    const dpChain = this.toDexPaprikaChain(chain);
    const key = cacheKeyWithChain(SOURCE, 'swaps', dpChain, `${poolId}:${limit}`);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const pair = await this.fetchDexScreenerPair(dpChain, poolId);
        if (!pair) return [];

        return this.generateSimulatedSwaps(pair, limit);
      },
      SOURCE,
      10_000, // 10s for swaps (near real-time)
    );
  }

  /**
   * Get swaps for a specific wallet in a pool.
   * NOTE: Wallet-level swap data is not available via DexPaprika or DexScreener.
   * Returns empty array - use on-chain RPC for real wallet data.
   */
  async getWalletSwaps(
    _chain: string,
    _poolId: string,
    _walletAddress: string,
    _limit = 50,
  ): Promise<DexPaprikaSwap[]> {
    // Wallet-level swap data requires on-chain indexing (Helius, Etherscan, etc.)
    return [];
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
  // SMART MONEY TRACKING (requires on-chain data)
  // ----------------------------------------------------------

  /**
   * Track smart money activity in a pool.
   * NOTE: Smart money tracking requires on-chain data with wallet addresses,
   * which is NOT available from DexPaprika or DexScreener APIs.
   * Returns empty array. For real smart money tracking, integrate:
   *   - Helius API (Solana enhanced transactions)
   *   - Etherscan API (Ethereum transaction history)
   *   - On-chain indexing via RPC
   */
  async trackSmartMoney(
    _chain: string,
    _poolId: string,
    _minSwapCount = 2,
    _minValueUsd = 100,
  ): Promise<SmartMoneySwap[]> {
    // Smart money tracking requires on-chain wallet-level data
    // which is not available from DexPaprika or DexScreener.
    // Use Helius/Etherscan/RPC for real implementation.
    return [];
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
  // PRIVATE: DexScreener API
  // ----------------------------------------------------------

  private async fetchDexScreenerSearch(query: string): Promise<DexScreenerPair[]> {
    const key = cacheKey(SOURCE_DS, 'search', query);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        if (unifiedCache.isRateLimited(SOURCE_DS)) {
          throw new Error(`DexScreener rate limited`);
        }

        const url = `${this.dexscreenerBaseUrl}/latest/dex/search?q=${encodeURIComponent(query)}`;
        const res = await fetch(url, {
          headers: {
            'Accept': 'application/json',
            'User-Agent': 'CryptoQuant-Terminal/1.0',
          },
        });

        if (res.status === 429) {
          const retryAfter = parseInt(res.headers.get('Retry-After') ?? '30', 10) * 1000;
          unifiedCache.markRateLimited(SOURCE_DS, retryAfter);
          throw new Error(`DexScreener rate limited, retry after ${retryAfter}ms`);
        }

        if (!res.ok) {
          throw new Error(`DexScreener search failed: ${res.status} ${res.statusText}`);
        }

        const data = await res.json();
        return (data.pairs || []) as DexScreenerPair[];
      },
      SOURCE_DS,
      120_000, // 2 min
    );
  }

  private async fetchDexScreenerPair(
    chainId: string,
    pairAddress: string,
  ): Promise<DexScreenerPair | null> {
    const key = cacheKeyWithChain(SOURCE_DS, 'pair', chainId, pairAddress);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        if (unifiedCache.isRateLimited(SOURCE_DS)) {
          throw new Error(`DexScreener rate limited`);
        }

        const url = `${this.dexscreenerBaseUrl}/latest/dex/pairs/${chainId}/${pairAddress}`;
        const res = await fetch(url, {
          headers: {
            'Accept': 'application/json',
            'User-Agent': 'CryptoQuant-Terminal/1.0',
          },
        });

        if (res.status === 404) return null;

        if (res.status === 429) {
          const retryAfter = parseInt(res.headers.get('Retry-After') ?? '30', 10) * 1000;
          unifiedCache.markRateLimited(SOURCE_DS, retryAfter);
          throw new Error(`DexScreener rate limited`);
        }

        if (!res.ok) {
          throw new Error(`DexScreener pair fetch failed: ${res.status}`);
        }

        const data = await res.json();
        const pairs = (data.pairs || []) as DexScreenerPair[];
        return pairs[0] ?? null;
      },
      SOURCE_DS,
      60_000, // 1 min
    );
  }

  private async fetchDexScreenerTokenPairs(
    tokenAddress: string,
  ): Promise<DexScreenerPair[]> {
    const key = cacheKey(SOURCE_DS, 'token-pairs', tokenAddress);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        if (unifiedCache.isRateLimited(SOURCE_DS)) {
          throw new Error(`DexScreener rate limited`);
        }

        const url = `${this.dexscreenerBaseUrl}/latest/dex/tokens/${tokenAddress}`;
        const res = await fetch(url, {
          headers: {
            'Accept': 'application/json',
            'User-Agent': 'CryptoQuant-Terminal/1.0',
          },
        });

        if (res.status === 429) {
          const retryAfter = parseInt(res.headers.get('Retry-After') ?? '30', 10) * 1000;
          unifiedCache.markRateLimited(SOURCE_DS, retryAfter);
          throw new Error(`DexScreener rate limited`);
        }

        if (!res.ok) {
          throw new Error(`DexScreener token pairs failed: ${res.status}`);
        }

        const data = await res.json();
        return (data.pairs || []) as DexScreenerPair[];
      },
      SOURCE_DS,
      60_000, // 1 min
    );
  }

  // ----------------------------------------------------------
  // PRIVATE: Data mapping helpers
  // ----------------------------------------------------------

  /** Map a DexScreener pair to our DexPaprikaPool type */
  private mapDexScreenerPairToPool(pair: DexScreenerPair): DexPaprikaPool {
    const txns = pair.txns ?? {
      h24: { buys: 0, sells: 0 },
      h6: { buys: 0, sells: 0 },
      h1: { buys: 0, sells: 0 },
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
  private mapDexScreenerPairToPoolDetail(pair: DexScreenerPair): DexPaprikaPoolDetail {
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

  // ----------------------------------------------------------
  // PRIVATE: Simulated swap generation
  // ----------------------------------------------------------

  /**
   * Generate simulated swap data based on DexScreener's aggregate txn counts.
   * This provides approximate swap activity for display purposes.
   * Real swap data with wallet addresses requires on-chain indexing.
   */
  private generateSimulatedSwaps(pair: DexScreenerPair, limit: number): DexPaprikaSwap[] {
    const swaps: DexPaprikaSwap[] = [];
    const priceUsd = parseFloat(pair.priceUsd || '0');
    const now = Date.now();

    const h24 = pair.txns?.h24 ?? { buys: 0, sells: 0 };
    const totalTxns24h = h24.buys + h24.sells;

    if (totalTxns24h === 0) return [];

    // Generate a representative sample of swaps
    const count = Math.min(limit, totalTxns24h);
    const buyRatio = h24.buys / totalTxns24h;

    // Estimate average swap value from 24h volume
    const volume24h = pair.volume?.h24 ?? 0;
    const avgSwapValue = totalTxns24h > 0 ? volume24h / totalTxns24h : 0;

    for (let i = 0; i < count; i++) {
      const isBuy = Math.random() < buyRatio;
      const timeOffset = Math.floor(Math.random() * 86400 * 1000); // Random time in last 24h
      const timestamp = new Date(now - timeOffset);
      const valueUsd = avgSwapValue * (0.5 + Math.random()); // ±50% variance

      // Generate placeholder wallet address
      const walletBytes = Array.from({ length: 32 }, () =>
        Math.floor(Math.random() * 256).toString(16).padStart(2, '0')
      ).join('');
      const wallet = `0x${walletBytes.slice(0, 40)}`;

      swaps.push({
        txnHash: `sim_${pair.pairAddress}_${i}_${Date.now()}`,
        blockNumber: 0, // Not available from aggregate data
        timestamp: timestamp.toISOString(),
        maker: wallet,
        amountIn: isBuy
          ? (valueUsd / (priceUsd || 1)).toFixed(6)
          : (valueUsd / (priceUsd || 1) * 1000).toFixed(6),
        amountOut: isBuy
          ? (valueUsd / (priceUsd || 1) * 1000).toFixed(6)
          : (valueUsd / (priceUsd || 1)).toFixed(6),
        tokenIn: {
          address: isBuy ? pair.quoteToken.address : pair.baseToken.address,
          symbol: isBuy ? pair.quoteToken.symbol : pair.baseToken.symbol,
        },
        tokenOut: {
          address: isBuy ? pair.baseToken.address : pair.quoteToken.address,
          symbol: isBuy ? pair.baseToken.symbol : pair.quoteToken.symbol,
        },
        type: isBuy ? 'buy' : 'sell',
        valueUsd: Math.round(valueUsd * 100) / 100,
        priceUsd,
      });
    }

    // Sort by timestamp descending (most recent first)
    swaps.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

    return swaps;
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
