/**
 * CoinGecko Client - CryptoQuant Terminal
 *
 * FREE data source using the CoinGecko API (no API key required for basic endpoints).
 * Primary source for market data: prices, volumes, market caps, OHLCV candles,
 * trending tokens, and global market overview.
 *
 * Data routing:
 *   - Top tokens by market cap → /coins/markets
 *   - Token detail → /coins/{id}
 *   - OHLCV candles → /coins/{id}/ohlc
 *   - Global market data → /global
 *   - Search tokens → /search
 *   - Trending → /search/trending
 *   - Token by contract → /coins/{platform}/contract/{address}
 *
 * Rate limits:
 *   - Free tier: ~10-30 requests/minute
 *   - Caching via UnifiedCache to minimize API calls
 *   - Rate-limit awareness with backoff on 429 responses
 */

import { unifiedCache, cacheKey, cacheKeyWithChain } from '../unified-cache';
import type { DexScreenerToken } from './data-ingestion';

// ============================================================
// TYPES
// ============================================================

/** CoinGecko market data object from /coins/markets */
export interface CoinGeckoMarketCoin {
  id: string;
  symbol: string;
  name: string;
  image: string;
  current_price: number;
  market_cap: number;
  market_cap_rank: number;
  fully_diluted_valuation: number | null;
  total_volume: number;
  high_24h: number;
  low_24h: number;
  price_change_24h: number;
  price_change_percentage_24h: number;
  market_cap_change_24h: number;
  market_cap_change_percentage_24h: number;
  circulating_supply: number;
  total_supply: number | null;
  max_supply: number | null;
  ath: number;
  ath_change_percentage: number;
  ath_date: string;
  atl: number;
  atl_change_percentage: number;
  atl_date: string;
  last_updated: string;
  sparkline_in_7d?: { price: number[] };
  price_change_percentage_1h_in_currency?: number;
  price_change_percentage_24h_in_currency?: number;
  price_change_percentage_7d_in_currency?: number;
}

/** CoinGecko coin detail from /coins/{id} */
export interface CoinGeckoCoinDetail {
  id: string;
  symbol: string;
  name: string;
  image: { thumb: string; small: string; large: string };
  market_cap_rank: number;
  market_data: {
    current_price: { usd: number };
    market_cap: { usd: number };
    total_volume: { usd: number };
    high_24h: { usd: number };
    low_24h: { usd: number };
    price_change_24h: number;
    price_change_percentage_24h: number;
    price_change_percentage_1h: number;
    price_change_percentage_7d: number;
    price_change_percentage_14d: number;
    price_change_percentage_30d: number;
    circulating_supply: number;
    total_supply: number | null;
    max_supply: number | null;
    ath: { usd: number };
    ath_change_percentage: { usd: number };
    atl: { usd: number };
    atl_change_percentage: { usd: number };
  };
  detail_platforms: Record<string, { decimal_place: number; contract_address: string } | null>;
  platforms: Record<string, string>;
  categories: string[];
  links: {
    homepage: string[];
    blockchain_site: string[];
    subreddit_url: string;
    repos_url: { github: string[] };
  };
  sentiment_votes_up_percentage: number;
  sentiment_votes_down_percentage: number;
}

/** OHLCV candle from CoinGecko /coins/{id}/ohlc */
export interface CoinGeckoOHLCVCandle {
  timestamp: number; // Unix ms
  open: number;
  high: number;
  low: number;
  close: number;
  /** Volume in USD for this candle period (from /market_chart, not available from /ohlc) */
  volume?: number;
}

/** Market chart data from CoinGecko /coins/{id}/market_chart */
export interface CoinGeckoMarketChart {
  prices: Array<[number, number]>;       // [timestamp_ms, price_usd]
  market_caps: Array<[number, number]>; // [timestamp_ms, market_cap_usd]
  total_volumes: Array<[number, number]>; // [timestamp_ms, volume_usd]
}

/** Global market data from /global */
export interface CoinGeckoGlobalData {
  data: {
    active_cryptocurrencies: number;
    upcoming_icos: number;
    ongoing_icos: number;
    ended_icos: number;
    markets: number;
    total_market_cap: { usd: number };
    total_volume: { usd: number };
    market_cap_percentage: { btc: number; eth: number; usdt: number };
    market_cap_change_percentage_24h_usd: number;
    updated_at: number;
  };
}

/** Search result from /search */
export interface CoinGeckoSearchResult {
  coins: Array<{
    id: string;
    name: string;
    api_symbol: string;
    symbol: string;
    market_cap_rank: number | null;
    thumb: string;
    large: string;
  }>;
}

/** Trending coin from /search/trending */
export interface CoinGeckoTrendingCoin {
  item: {
    id: string;
    coin_id: number;
    name: string;
    symbol: string;
    market_cap_rank: number;
    thumb: string;
    small: string;
    large: string;
    slug: string;
    price_btc: number;
    score: number;
    data: {
      price: number;
      price_change_percentage_24h: { usd: number };
      market_cap: string;
      total_volume: string;
      sparkline: string;
    };
  };
}

/** CoinGecko trending response */
export interface CoinGeckoTrendingResponse {
  coins: CoinGeckoTrendingCoin[];
}

/** Mapped token data compatible with our internal Token model */
export interface CoinGeckoMappedToken {
  /** CoinGecko coin ID (e.g. "bitcoin", "ethereum") */
  coinId: string;
  /** Contract address for chain-specific tokens, or coinId for native coins */
  address: string;
  symbol: string;
  name: string;
  image: string;
  priceUsd: number;
  volume24h: number;
  marketCap: number;
  priceChange1h: number;
  priceChange24h: number;
  priceChange7d: number;
  high24h: number;
  low24h: number;
  ath: number;
  athChangePercentage: number;
  marketCapRank: number;
  circulatingSupply: number;
  totalSupply: number | null;
  maxSupply: number | null;
  /** Platform contract addresses (e.g. { ethereum: "0x...", "polygon-pos": "0x..." }) */
  platforms: Record<string, string>;
  lastUpdated: string;
}

// ============================================================
// CONSTANTS
// ============================================================

const COINGECKO_BASE_URL = 'https://api.coingecko.com/api/v3';
const SOURCE = 'coingecko';

/**
 * Map of well-known native coins to their CoinGecko IDs.
 * For these coins, we use the CoinGecko ID as the "address" field
 * since they don't have contract addresses.
 */
const NATIVE_COIN_IDS: Record<string, string> = {
  bitcoin: 'bitcoin',
  ethereum: 'ethereum',
  solana: 'solana',
  binancecoin: 'binancecoin',
  ripple: 'ripple',
  cardano: 'cardano',
  dogecoin: 'dogecoin',
  polkadot: 'polkadot',
  avalanche: 'avalanche-2',
  'usd-coin': 'usd-coin',
  tether: 'tether',
  tron: 'tron',
  chainlink: 'chainlink',
  polygon: 'matic-network',
  litecoin: 'litecoin',
  uniswap: 'uniswap',
  stellar: 'stellar',
  near: 'near',
  aptos: 'aptos',
  sui: 'sui',
  arbitrum: 'arbitrum',
  optimism: 'optimism',
  base: 'base',
};

/**
 * Map CoinGecko platform IDs to our internal chain IDs.
 */
const PLATFORM_TO_CHAIN: Record<string, string> = {
  ethereum: 'ETH',
  'polygon-pos': 'MATIC',
  'binance-smart-chain': 'BSC',
  avalanche: 'AVAX',
  arbitrum: 'ARB',
  'optimistic-ethereum': 'OP',
  solana: 'SOL',
  base: 'BASE',
  fantom: 'FTM',
};

/**
 * Map our internal chain IDs to CoinGecko platform IDs
 * for contract address lookups.
 */
const CHAIN_TO_PLATFORM: Record<string, string> = {
  SOL: 'solana',
  ETH: 'ethereum',
  BSC: 'binance-smart-chain',
  MATIC: 'polygon-pos',
  ARB: 'arbitrum',
  OP: 'optimistic-ethereum',
  AVAX: 'avalanche',
  BASE: 'base',
  FTM: 'fantom',
};

/**
 * Map CoinGecko OHLCV days parameter to internal timeframe.
 * CoinGecko only supports: 1, 7, 14, 30, 90, 180, 365, max
 * Candle granularity depends on days:
 *   1 day → 30 min candles
 *   7-14 days → 4 hour candles
 *   30+ days → 4 hour candles
 *   90+ days → 4 hour candles
 */
const DAYS_TO_TIMEFRAME: Record<number, string> = {
  1: '30m',
  7: '4h',
  14: '4h',
  30: '4h',
  90: '4h',
  180: '4h',
  365: '4h',
};

/** Delay between requests to respect rate limits (ms) */
const INTER_REQUEST_DELAY = 1500;

/** Cache TTLs for different data types */
const CACHE_TTLS = {
  markets: 60_000,         // 1 min (prices change fast)
  coinDetail: 120_000,     // 2 min
  ohlcv: 300_000,          // 5 min (historical data)
  global: 60_000,          // 1 min
  search: 600_000,         // 10 min (search results are stable)
  trending: 300_000,       // 5 min
  contractToken: 120_000,  // 2 min
} as const;

// ============================================================
// COINGECKO CLIENT CLASS
// ============================================================

export class CoinGeckoClient {
  private baseUrl: string;

  constructor(baseUrl: string = COINGECKO_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  // ----------------------------------------------------------
  // TOP TOKENS
  // ----------------------------------------------------------

  /**
   * Get top coins by market cap from /coins/markets.
   * This is the PRIMARY method for fetching market data.
   * Returns tokens with price, volume, market cap, and 24h change.
   */
  async getTopTokens(limit: number = 50): Promise<CoinGeckoMappedToken[]> {
    const key = cacheKey(SOURCE, 'markets', `top:${limit}`);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const params = new URLSearchParams({
          vs_currency: 'usd',
          order: 'market_cap_desc',
          per_page: String(Math.min(limit, 250)),
          page: '1',
          sparkline: 'false',
          price_change_percentage: '1h,24h,7d',
        });

        const data = await this.fetchApi<CoinGeckoMarketCoin[]>(
          `/coins/markets?${params}`
        );

        if (!Array.isArray(data)) return [];

        return data.map(coin => this.mapMarketCoinToToken(coin));
      },
      SOURCE,
      CACHE_TTLS.markets,
    );
  }

  /**
   * Get top tokens by 24h trading volume (useful for active trading).
   */
  async getTopTokensByVolume(limit: number = 50): Promise<CoinGeckoMappedToken[]> {
    const key = cacheKey(SOURCE, 'markets', `volume:${limit}`);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const params = new URLSearchParams({
          vs_currency: 'usd',
          order: 'volume_desc',
          per_page: String(Math.min(limit, 250)),
          page: '1',
          sparkline: 'false',
          price_change_percentage: '1h,24h,7d',
        });

        const data = await this.fetchApi<CoinGeckoMarketCoin[]>(
          `/coins/markets?${params}`
        );

        if (!Array.isArray(data)) return [];

        return data.map(coin => this.mapMarketCoinToToken(coin));
      },
      SOURCE,
      CACHE_TTLS.markets,
    );
  }


  /**
   * Get top tokens with PAGINATION - fetches multiple pages.
   * Each page returns up to 250 tokens. Use pages 1-N for full coverage.
   * Respects rate limits with 2s delay between pages.
   */
  async getTopTokensPaginated(totalLimit: number = 1250): Promise<CoinGeckoMappedToken[]> {
    const allTokens: CoinGeckoMappedToken[] = [];
    const perPage = 250;
    const maxPages = Math.ceil(totalLimit / perPage);

    for (let page = 1; page <= maxPages; page++) {
      const key = cacheKey(SOURCE, 'markets', `top:p${page}:${perPage}`);

      try {
        const pageTokens = await unifiedCache.getOrFetch(
          key,
          async () => {
            const params = new URLSearchParams({
              vs_currency: 'usd',
              order: 'market_cap_desc',
              per_page: String(perPage),
              page: String(page),
              sparkline: 'false',
              price_change_percentage: '1h,24h,7d',
            });

            const data = await this.fetchApi<CoinGeckoMarketCoin[]>(
              `/coins/markets?${params}`
            );

            if (!Array.isArray(data)) return [];
            return data.map(coin => this.mapMarketCoinToToken(coin));
          },
          SOURCE,
          CACHE_TTLS.markets,
        );

        if (pageTokens.length === 0) break; // No more results
        allTokens.push(...pageTokens);

        if (allTokens.length >= totalLimit) break;
        if (pageTokens.length < perPage) break; // Last page

        // Rate limit: wait between pages
        if (page < maxPages) {
          await this.delay(2000);
        }
      } catch (err) {
        console.warn(`[CoinGecko] Pagination page ${page} failed:`, err);
        break; // Stop on error
      }
    }

    return allTokens.slice(0, totalLimit);
  }

  /**
   * Get top tokens by volume with PAGINATION.
   * Discovers high-activity tokens that may not be top by market cap.
   */
  async getTopTokensByVolumePaginated(totalLimit: number = 500): Promise<CoinGeckoMappedToken[]> {
    const allTokens: CoinGeckoMappedToken[] = [];
    const perPage = 250;
    const maxPages = Math.ceil(totalLimit / perPage);

    for (let page = 1; page <= maxPages; page++) {
      const key = cacheKey(SOURCE, 'markets', `volume:p${page}:${perPage}`);

      try {
        const pageTokens = await unifiedCache.getOrFetch(
          key,
          async () => {
            const params = new URLSearchParams({
              vs_currency: 'usd',
              order: 'volume_desc',
              per_page: String(perPage),
              page: String(page),
              sparkline: 'false',
              price_change_percentage: '1h,24h,7d',
            });

            const data = await this.fetchApi<CoinGeckoMarketCoin[]>(
              `/coins/markets?${params}`
            );

            if (!Array.isArray(data)) return [];
            return data.map(coin => this.mapMarketCoinToToken(coin));
          },
          SOURCE,
          CACHE_TTLS.markets,
        );

        if (pageTokens.length === 0) break;
        allTokens.push(...pageTokens);

        if (allTokens.length >= totalLimit) break;
        if (pageTokens.length < perPage) break;

        if (page < maxPages) {
          await this.delay(2000);
        }
      } catch (err) {
        console.warn(`[CoinGecko] Volume pagination page ${page} failed:`, err);
        break;
      }
    }

    return allTokens.slice(0, totalLimit);
  }
  // ----------------------------------------------------------
  // TOKEN DETAIL
  // ----------------------------------------------------------

  /**
   * Get detailed information about a coin from /coins/{id}.
   * Includes contract addresses on various platforms.
   */
  async getTokenDetail(id: string): Promise<CoinGeckoCoinDetail | null> {
    const key = cacheKey(SOURCE, 'coin-detail', id);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const params = new URLSearchParams({
          localization: 'false',
          tickers: 'false',
          market_data: 'true',
          community_data: 'false',
          developer_data: 'false',
          sparkline: 'false',
        });

        const data = await this.fetchApi<CoinGeckoCoinDetail>(
          `/coins/${encodeURIComponent(id)}?${params}`
        );

        if (!data || !data.id) return null;

        return data;
      },
      SOURCE,
      CACHE_TTLS.coinDetail,
    );
  }

  /**
   * Get token by contract address on a specific platform.
   * Useful for looking up Solana/Ethereum tokens by their contract address.
   */
  async getTokenByContract(
    chain: string,
    contractAddress: string,
  ): Promise<CoinGeckoCoinDetail | null> {
    const platform = CHAIN_TO_PLATFORM[chain] ?? chain.toLowerCase();
    const key = cacheKeyWithChain(SOURCE, 'contract-token', platform, contractAddress);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const data = await this.fetchApi<CoinGeckoCoinDetail>(
          `/coins/${encodeURIComponent(platform)}/contract/${encodeURIComponent(contractAddress)}`
        );

        if (!data || !data.id) return null;

        return data;
      },
      SOURCE,
      CACHE_TTLS.contractToken,
    );
  }

  // ----------------------------------------------------------
  // OHLCV
  // ----------------------------------------------------------

  /**
   * Get OHLCV candles from /coins/{id}/ohlc.
   * CoinGecko returns: [timestamp_ms, open, high, low, close]
   *
   * Days parameter determines candle granularity:
   *   1 → 30 min candles
   *   7 → 4 hour candles
   *   14 → 4 hour candles
   *   30 → 4 hour candles
   *   90 → 4 hour candles
   *   180 → 4 hour candles
   *   365 → 4 hour candles
   *   max → 4 hour candles
   */
  async getOHLCV(
    coinId: string,
    days: number = 7,
  ): Promise<CoinGeckoOHLCVCandle[]> {
    const key = cacheKey(SOURCE, 'ohlcv', `${coinId}:${days}`);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const params = new URLSearchParams({
          vs_currency: 'usd',
          days: String(days),
        });

        const data = await this.fetchApi<Array<[number, number, number, number, number]>>(
          `/coins/${encodeURIComponent(coinId)}/ohlc?${params}`
        );

        if (!Array.isArray(data)) return [];

        return data.map(candle => ({
          timestamp: candle[0],
          open: candle[1],
          high: candle[2],
          low: candle[3],
          close: candle[4],
        }));
      },
      SOURCE,
      CACHE_TTLS.ohlcv,
    );
  }

  // ----------------------------------------------------------
  // MARKET CHART (Volume Data)
  // ----------------------------------------------------------

  /**
   * Get market chart data from /coins/{id}/market_chart.
   * Returns prices, market caps, and total volumes over time.
   * This is the ONLY CoinGecko endpoint that provides volume data alongside price.
   *
   * Days parameter determines granularity:
   *   1-2 days → ~5 min data points
   *   3-30 days → hourly data points
   *   31+ days → daily data points
   */
  async getMarketChart(
    coinId: string,
    days: number = 1,
  ): Promise<CoinGeckoMarketChart | null> {
    const key = cacheKey(SOURCE, 'market-chart', `${coinId}:${days}`);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const params = new URLSearchParams({
          vs_currency: 'usd',
          days: String(days),
        });

        const data = await this.fetchApi<CoinGeckoMarketChart>(
          `/coins/${encodeURIComponent(coinId)}/market_chart?${params}`
        );

        if (!data || !data.prices || !Array.isArray(data.prices)) return null;

        return data;
      },
      SOURCE,
      CACHE_TTLS.ohlcv, // Same TTL as OHLCV (5 min)
    );
  }

  /**
   * Get volume data for a specific time range using /coins/{id}/market_chart/range.
   * More precise control over the date range.
   */
  async getMarketChartRange(
    coinId: string,
    fromUnix: number,
    toUnix: number,
  ): Promise<CoinGeckoMarketChart | null> {
    const key = cacheKey(SOURCE, 'market-chart-range', `${coinId}:${fromUnix}-${toUnix}`);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const params = new URLSearchParams({
          vs_currency: 'usd',
          from: String(fromUnix),
          to: String(toUnix),
        });

        const data = await this.fetchApi<CoinGeckoMarketChart>(
          `/coins/${encodeURIComponent(coinId)}/market_chart/range?${params}`
        );

        if (!data || !data.prices || !Array.isArray(data.prices)) return null;

        return data;
      },
      SOURCE,
      CACHE_TTLS.ohlcv,
    );
  }

  /**
   * Build a map of timestamp → volume from market_chart total_volumes data.
   * Used to enrich OHLCV candles with volume data.
   * Rounds timestamps to the nearest candle period for matching.
   *
   * @param totalVolumes - Array of [timestamp_ms, volume_usd] pairs
   * @param candleMs - Candle period in ms (e.g., 1800000 for 30m, 14400000 for 4h)
   */
  buildVolumeMap(totalVolumes: Array<[number, number]>, candleMs: number): Map<number, number> {
    const volumeMap = new Map<number, number>();

    for (const [ts, vol] of totalVolumes) {
      // Round timestamp down to nearest candle period
      const candleTs = Math.floor(ts / candleMs) * candleMs;
      const existing = volumeMap.get(candleTs) || 0;
      volumeMap.set(candleTs, existing + vol);
    }

    return volumeMap;
  }

  // ----------------------------------------------------------
  // GLOBAL MARKET DATA
  // ----------------------------------------------------------

  /**
   * Get global cryptocurrency market data from /global.
   * Returns total market cap, volume, BTC dominance, etc.
   */
  async getMarketData(): Promise<CoinGeckoGlobalData['data'] | null> {
    const key = cacheKey(SOURCE, 'global', 'market');

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const data = await this.fetchApi<CoinGeckoGlobalData>('/global');

        if (!data || !data.data) return null;

        return data.data;
      },
      SOURCE,
      CACHE_TTLS.global,
    );
  }

  // ----------------------------------------------------------
  // SEARCH
  // ----------------------------------------------------------

  /**
   * Search for tokens from /search.
   * Returns matching coins with name, symbol, and market cap rank.
   */
  async searchTokens(query: string): Promise<CoinGeckoSearchResult['coins']> {
    const key = cacheKey(SOURCE, 'search', query);

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const data = await this.fetchApi<CoinGeckoSearchResult>(
          `/search?query=${encodeURIComponent(query)}`
        );

        if (!data || !data.coins) return [];

        return data.coins;
      },
      SOURCE,
      CACHE_TTLS.search,
    );
  }

  // ----------------------------------------------------------
  // TRENDING
  // ----------------------------------------------------------

  /**
   * Get trending coins from /search/trending.
   * Returns the top trending coins on CoinGecko.
   */
  async getTrending(): Promise<CoinGeckoTrendingCoin[]> {
    const key = cacheKey(SOURCE, 'trending', 'latest');

    return unifiedCache.getOrFetch(
      key,
      async () => {
        const data = await this.fetchApi<CoinGeckoTrendingResponse>(
          '/search/trending'
        );

        if (!data || !data.coins) return [];

        return data.coins;
      },
      SOURCE,
      CACHE_TTLS.trending,
    );
  }

  // ----------------------------------------------------------
  // MAPPING HELPERS
  // ----------------------------------------------------------

  /**
   * Resolve a CoinGecko coin ID to a contract address for a given chain.
   * For native coins (BTC, ETH, SOL), returns the coin ID itself.
   * For tokens, returns the platform-specific contract address.
   */
  async resolveAddress(coinId: string, chain: string = 'SOL'): Promise<string> {
    // Check if this is a well-known native coin
    if (NATIVE_COIN_IDS[coinId]) {
      return coinId;
    }

    // Try to get coin detail which includes platform contract addresses
    try {
      const detail = await this.getTokenDetail(coinId);
      if (detail) {
        const platform = CHAIN_TO_PLATFORM[chain] ?? chain.toLowerCase();

        // Check detail_platforms first (more reliable)
        const platformDetail = detail.detail_platforms?.[platform];
        if (platformDetail?.contract_address) {
          return platformDetail.contract_address;
        }

        // Check platforms object (simpler format)
        const contractFromPlatforms = detail.platforms?.[platform];
        if (contractFromPlatforms && contractFromPlatforms !== '') {
          return contractFromPlatforms;
        }
      }
    } catch {
      // Detail fetch failed, fall back to coin ID
    }

    // Fallback: use the CoinGecko ID as address
    return coinId;
  }

  /**
   * Map a CoinGecko market coin to our internal CoinGeckoMappedToken format.
   */
  private mapMarketCoinToToken(coin: CoinGeckoMarketCoin): CoinGeckoMappedToken {
    return {
      coinId: coin.id,
      address: coin.id, // Will be resolved later for contract tokens
      symbol: coin.symbol?.toUpperCase() || '',
      name: coin.name || '',
      image: coin.image || '',
      priceUsd: coin.current_price ?? 0,
      volume24h: coin.total_volume ?? 0,
      marketCap: coin.market_cap ?? 0,
      priceChange1h: coin.price_change_percentage_1h_in_currency ?? 0,
      priceChange24h: coin.price_change_percentage_24h ?? 0,
      priceChange7d: coin.price_change_percentage_7d_in_currency ?? 0,
      high24h: coin.high_24h ?? 0,
      low24h: coin.low_24h ?? 0,
      ath: coin.ath ?? 0,
      athChangePercentage: coin.ath_change_percentage ?? 0,
      marketCapRank: coin.market_cap_rank ?? 0,
      circulatingSupply: coin.circulating_supply ?? 0,
      totalSupply: coin.total_supply,
      maxSupply: coin.max_supply,
      platforms: {}, // Not available from markets endpoint; use getTokenDetail
      lastUpdated: coin.last_updated || new Date().toISOString(),
    };
  }

  /**
   * Convert a CoinGeckoMappedToken to a DexScreenerToken-compatible format.
   * This allows CoinGecko data to flow through the existing pipeline.
   */
  toDexScreenerToken(token: CoinGeckoMappedToken, chainId: string = 'solana'): DexScreenerToken {
    return {
      chainId,
      dexId: 'coingecko',
      pairAddress: '',
      baseToken: {
        address: token.address,
        symbol: token.symbol,
        name: token.name,
      },
      quoteToken: {
        address: chainId === 'solana' ? 'So11111111111111111111111111111111111111112' : '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
        symbol: chainId === 'solana' ? 'SOL' : 'WETH',
        name: chainId === 'solana' ? 'Solana' : 'Wrapped Ether',
      },
      priceNative: String(token.priceUsd),
      priceUsd: String(token.priceUsd),
      priceChange: {
        h24: token.priceChange24h,
        h6: 0,
        h1: token.priceChange1h,
        m5: 0,
      },
      txns: {
        h24: { buys: 0, sells: 0 },
        h6: { buys: 0, sells: 0 },
        h1: { buys: 0, sells: 0 },
      },
      volume: {
        h24: token.volume24h,
        h6: 0,
        h1: 0,
      },
      liquidity: { usd: 0, base: 0, quote: 0 },
      fdv: token.marketCap,
      marketCap: token.marketCap,
      pairCreatedAt: 0,
    };
  }

  /**
   * Get the CoinGecko coin ID for a given token address on a chain.
   * Uses the contract address endpoint to look up the coin ID.
   */
  async getCoinIdFromContract(chain: string, contractAddress: string): Promise<string | null> {
    try {
      const detail = await this.getTokenByContract(chain, contractAddress);
      return detail?.id ?? null;
    } catch {
      return null;
    }
  }

  /**
   * Map a CoinGecko platform ID to our internal chain ID.
   */
  platformToChain(platformId: string): string {
    return PLATFORM_TO_CHAIN[platformId] ?? platformId.toUpperCase();
  }

  /**
   * Map our internal chain ID to CoinGecko platform ID.
   */
  chainToPlatform(chainId: string): string {
    return CHAIN_TO_PLATFORM[chainId] ?? chainId.toLowerCase();
  }

  /**
   * Get the internal timeframe for a given number of days of OHLCV data.
   */
  getOHLCVTimeframe(days: number): string {
    return DAYS_TO_TIMEFRAME[days] ?? '4h';
  }

  // ----------------------------------------------------------
  // PRIVATE: API FETCH WITH RATE LIMIT HANDLING
  // ----------------------------------------------------------

  private lastRequestTime = 0;

  /**
   * Fetch from the CoinGecko API with rate-limit handling.
   * Implements:
   *  - Request throttling (min interval between requests)
   *  - 429 backoff via unifiedCache
   *  - Automatic retry on rate-limit
   */
  private async fetchApi<T>(path: string, retries = 2): Promise<T> {
    // Check if we're rate-limited
    if (unifiedCache.isRateLimited(SOURCE)) {
      const remaining = unifiedCache.getRateLimitRemaining(SOURCE);
      console.warn(`[CoinGecko] Rate limited, ${remaining}ms remaining - skipping ${path}`);
      throw new Error(`CoinGecko rate limited, retry in ${remaining}ms`);
    }

    // Throttle: ensure minimum interval between requests
    const elapsed = Date.now() - this.lastRequestTime;
    if (elapsed < INTER_REQUEST_DELAY) {
      await this.delay(INTER_REQUEST_DELAY - elapsed);
    }

    const url = `${this.baseUrl}${path}`;

    try {
      this.lastRequestTime = Date.now();
      const res = await fetch(url, {
        headers: {
          'Accept': 'application/json',
          'User-Agent': 'CryptoQuant-Terminal/1.0',
        },
      });

      // Handle rate limiting
      if (res.status === 429) {
        const retryAfter = parseInt(res.headers.get('Retry-After') ?? '60', 10) * 1000;
        unifiedCache.markRateLimited(SOURCE, Math.max(retryAfter, 60_000));

        if (retries > 0) {
          console.warn(`[CoinGecko] Rate limited (429), retrying after ${retryAfter}ms. Retries: ${retries}`);
          await this.delay(Math.max(retryAfter, 60_000));
          return this.fetchApi<T>(path, retries - 1);
        }

        throw new Error(`CoinGecko rate limited (429), retry after ${retryAfter}ms`);
      }

      if (res.status === 404) {
        return null as T;
      }

      if (!res.ok) {
        throw new Error(`CoinGecko API error: ${res.status} ${res.statusText} for ${path}`);
      }

      const data = await res.json();
      return data as T;
    } catch (error) {
      if (error instanceof TypeError && error.message.includes('fetch')) {
        console.error(`[CoinGecko] Network error for ${path}:`, error);
      }
      throw error;
    }
  }

  /**
   * Simple async delay utility.
   */
  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

// ============================================================
// CONVENIENCE FUNCTIONS
// ============================================================

/**
 * Get market summary (BTC price, ETH price, SOL price, total market cap, Fear & Greed).
 * This is a convenience function that aggregates data from CoinGecko.
 */
export async function getMarketSummary(): Promise<{
  btcPrice: number;
  ethPrice: number;
  solPrice: number;
  totalMarketCap: number;
  totalVolume24h: number;
  btcDominance: number;
  ethDominance: number;
  fearGreedIndex: number;
  lastUpdated: number;
}> {
  try {
    const globalData = await coinGeckoClient.getMarketData();
    const topTokens = await coinGeckoClient.getTopTokens(3);

    const btc = topTokens.find(t => t.coinId === 'bitcoin');
    const eth = topTokens.find(t => t.coinId === 'ethereum');
    const sol = topTokens.find(t => t.coinId === 'solana');

    return {
      btcPrice: btc?.priceUsd ?? 0,
      ethPrice: eth?.priceUsd ?? 0,
      solPrice: sol?.priceUsd ?? 0,
      totalMarketCap: globalData?.total_market_cap?.usd ?? 0,
      totalVolume24h: globalData?.total_volume?.usd ?? 0,
      btcDominance: globalData?.market_cap_percentage?.btc ?? 0,
      ethDominance: globalData?.market_cap_percentage?.eth ?? 0,
      fearGreedIndex: 50, // CoinGecko doesn't provide this; default neutral
      lastUpdated: Date.now(),
    };
  } catch (error) {
    console.warn('[CoinGecko] getMarketSummary failed:', error);
    return {
      btcPrice: 0,
      ethPrice: 0,
      solPrice: 0,
      totalMarketCap: 0,
      totalVolume24h: 0,
      btcDominance: 0,
      ethDominance: 0,
      fearGreedIndex: 0,
      lastUpdated: 0,
    };
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const coinGeckoClient = new CoinGeckoClient();
