/**
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║  Footprint Analytics Client — Structured Crypto Analytics               ║
 * ║  REST API with 30+ chains, no API key required for basic endpoints     ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 *
 * Footprint Analytics (https://www.footprint.network/):
 *   - 30+ chains supported with structured analytics data
 *   - Free tier: 1,000 queries/day, no credit card required
 *   - REST API: https://api.footprint.network/api/v1/
 *   - No API key required for basic endpoints (but recommended for higher limits)
 *   - Response time: 1-5 seconds
 *   - Rich token metrics, OHLCV, protocol data, and chain overviews
 *
 * Environment Variable:
 *   FOOTPRINT_API_KEY — optional, raises rate limits and unlocks premium endpoints
 */

import { RateLimiter } from './rate-limiter';
import { UnifiedCache } from './source-cache';

// ============================================================
// TYPES
// ============================================================

/** Supported OHLCV timeframe values */
export type FootprintTimeframe = '1m' | '5m' | '15m' | '1h' | '4h' | '1d';

/** Token price data from Footprint */
export interface FootprintTokenPrice {
  tokenAddress: string;
  chain: string;
  priceUsd: number;
  priceChange24h: number;
  priceChange7d: number;
  volume24h: number;
  marketCap: number;
  lastUpdated: string;
}

/** OHLCV candle data point */
export interface FootprintOHLCV {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** DeFi protocol entry */
export interface FootprintProtocol {
  slug: string;
  name: string;
  chain: string;
  category: string;
  tvl: number;
  tvlChange1d: number;
  tvlChange7d: number;
  logo: string | null;
  url: string | null;
  description: string | null;
}

/** Protocol TVL history data point */
export interface FootprintProtocolTVL {
  date: string;
  tvlUsd: number;
  tvlChange1d: number;
  tvlChange7d: number;
}

/** Comprehensive token metrics */
export interface FootprintTokenMetrics {
  tokenAddress: string;
  chain: string;
  name: string;
  symbol: string;
  priceUsd: number;
  priceChange24h: number;
  priceChange7d: number;
  priceChange30d: number;
  volume24h: number;
  volumeChange24h: number;
  marketCap: number;
  fdv: number;
  holders: number;
  holdersChange24h: number;
  totalSupply: string;
  circulatingSupply: string;
  maxSupply: string | null;
  decimals: number;
}

/** Chain-level overview metrics */
export interface FootprintChainOverview {
  chain: string;
  totalTvl: number;
  totalTvlChange1d: number;
  totalTvlChange7d: number;
  totalVolume24h: number;
  totalVolumeChange24h: number;
  totalTransactions24h: number;
  totalFees24h: number;
  protocolCount: number;
  uniqueAddresses: number;
}

/** Utility: delay for a given number of milliseconds */
function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ============================================================
// FOOTPRINT ANALYTICS CLIENT
// Free tier: 1,000 queries/day, no API key required
// ============================================================

export class FootprintClient {
  private apiKey: string;
  private baseUrl = 'https://api.footprint.network/api/v1';
  private limiter: RateLimiter;
  private priceCache: UnifiedCache;
  private dataCache: UnifiedCache;

  /** Maximum number of retries on 429 (rate-limited) responses */
  private static readonly MAX_RETRIES = 3;

  /** Base delay in ms for exponential backoff on 429 responses */
  private static readonly BACKOFF_BASE_MS = 2_000;

  constructor(apiKey?: string) {
    this.apiKey = apiKey || process.env.FOOTPRINT_API_KEY || '';
    this.limiter = new RateLimiter(10, 20); // 10 RPS — generous free tier
    this.priceCache = new UnifiedCache(5); // 5 min TTL for price data (volatile)
    this.dataCache = new UnifiedCache(30); // 30 min TTL for protocol/historical data
  }

  /**
   * Whether the client is operational.
   * Footprint Analytics works without an API key on the free tier,
   * so this is always true.
   */
  get isConfigured(): boolean {
    return true;
  }

  // ----------------------------------------------------------
  // Internal helpers
  // ----------------------------------------------------------

  /**
   * Build headers common to all Footprint API requests.
   * Includes the API-KEY header only when a key is configured.
   */
  private buildHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };
    if (this.apiKey) {
      headers['API-KEY'] = this.apiKey;
    }
    return headers;
  }

  /**
   * Normalise a human-readable chain name to Footprint's expected format.
   * Footprint uses lowercase chain identifiers.
   */
  private toFootprintChain(chain: string): string {
    const map: Record<string, string> = {
      'ethereum': 'ethereum',
      'eth': 'ethereum',
      'base': 'base',
      'arbitrum': 'arbitrum',
      'arb': 'arbitrum',
      'optimism': 'optimism',
      'op': 'optimism',
      'polygon': 'polygon',
      'matic': 'polygon',
      'bsc': 'bsc',
      'bnb': 'bsc',
      'binance': 'bsc',
      'avalanche': 'avalanche',
      'avax': 'avalanche',
      'fantom': 'fantom',
      'ftm': 'fantom',
      'solana': 'solana',
      'sol': 'solana',
      'cronos': 'cronos',
      'harmony': 'harmony',
      'aurora': 'aurora',
      'moonbeam': 'moonbeam',
      'celo': 'celo',
    };
    return map[chain.toLowerCase()] || chain.toLowerCase();
  }

  /**
   * Core fetch with rate limiting, caching, and retry on 429.
   *
   * @param endpoint - API path (e.g. '/token/price')
   * @param params - Query string parameters
   * @param cacheTtlMinutes - Custom cache TTL; undefined uses the default
   * @param usePriceCache - If true, uses the short-TTL price cache; otherwise the data cache
   */
  private async fetchApi<T>(
    endpoint: string,
    params: Record<string, string> = {},
    cacheTtlMinutes?: number,
    usePriceCache: boolean = false,
  ): Promise<T | null> {
    const cacheKey = `footprint:${endpoint}:${JSON.stringify(params)}`;
    const cache = usePriceCache ? this.priceCache : this.dataCache;

    const cached = cache.get<T>(cacheKey);
    if (cached) return cached;

    await this.limiter.acquire();

    let retries = 0;
    while (retries <= FootprintClient.MAX_RETRIES) {
      try {
        const url = new URL(`${this.baseUrl}${endpoint}`);
        Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));

        const res = await fetch(url.toString(), {
          headers: this.buildHeaders(),
        });

        // Handle rate limiting with exponential backoff
        if (res.status === 429) {
          const backoffMs = FootprintClient.BACKOFF_BASE_MS * Math.pow(2, retries);
          console.warn(`[Footprint] Rate limited (429) — backing off ${backoffMs}ms, attempt ${retries + 1}/${FootprintClient.MAX_RETRIES}`);
          await delay(backoffMs);
          retries++;
          continue;
        }

        if (!res.ok) {
          const errorBody = await res.text();
          console.warn(`[Footprint] API error: ${res.status} ${res.statusText} — ${errorBody.slice(0, 200)}`);
          return null;
        }

        const data = await res.json() as T;
        cache.set(cacheKey, data, cacheTtlMinutes);
        return data;
      } catch (err) {
        console.error('[Footprint] Fetch error:', err);
        return null;
      }
    }

    console.warn(`[Footprint] Max retries (${FootprintClient.MAX_RETRIES}) exceeded for ${endpoint}`);
    return null;
  }

  // ----------------------------------------------------------
  // Public typed methods
  // ----------------------------------------------------------

  /**
   * Get the current price of a token on a specific chain.
   *
   * Uses a short 5-minute cache since prices change frequently.
   *
   * @param tokenAddress - The token contract address
   * @param chain - Blockchain identifier (e.g. 'ethereum', 'base')
   */
  async getTokenPrice(
    tokenAddress: string,
    chain: string = 'ethereum',
  ): Promise<FootprintTokenPrice | null> {
    const fpChain = this.toFootprintChain(chain);

    const result = await this.fetchApi<{
      data?: {
        address?: string;
        chain?: string;
        price_usd?: number;
        price_change_24h?: number;
        price_change_7d?: number;
        volume_24h?: number;
        market_cap?: number;
        updated_at?: string;
      };
    }>(
      '/token/price',
      { chain: fpChain, token_address: tokenAddress },
      5, // 5-minute cache for price data
      true, // Use the price cache (short TTL)
    );

    const d = result?.data;
    if (!d) return null;

    return {
      tokenAddress: d.address || tokenAddress,
      chain: d.chain || fpChain,
      priceUsd: d.price_usd ?? 0,
      priceChange24h: d.price_change_24h ?? 0,
      priceChange7d: d.price_change_7d ?? 0,
      volume24h: d.volume_24h ?? 0,
      marketCap: d.market_cap ?? 0,
      lastUpdated: d.updated_at || new Date().toISOString(),
    };
  }

  /**
   * Get OHLCV (candlestick) data for a token on a specific chain.
   *
   * Supports timeframes: 1m, 5m, 15m, 1h, 4h, 1d.
   * Uses the data cache with a 30-minute TTL since OHLCV data
   * for timeframes >= 1h is relatively stable.
   *
   * @param tokenAddress - The token contract address
   * @param chain - Blockchain identifier
   * @param timeframe - Candle timeframe (1m, 5m, 15m, 1h, 4h, 1d)
   * @param limit - Maximum number of candles to return (default 500)
   */
  async getOHLCV(
    tokenAddress: string,
    chain: string = 'ethereum',
    timeframe: FootprintTimeframe = '1d',
    limit: number = 500,
  ): Promise<FootprintOHLCV[]> {
    const fpChain = this.toFootprintChain(chain);

    // Use shorter cache for sub-hour timeframes, longer for 1h+
    const cacheTtl = (timeframe === '1m' || timeframe === '5m' || timeframe === '15m') ? 5 : 30;

    const result = await this.fetchApi<{
      data?: Array<{
        timestamp?: number;
        open?: number;
        high?: number;
        low?: number;
        close?: number;
        volume?: number;
      }>;
    }>(
      '/token/ohlcv',
      {
        chain: fpChain,
        token_address: tokenAddress,
        timeframe,
        limit: String(limit),
      },
      cacheTtl,
      timeframe === '1m' || timeframe === '5m' || timeframe === '15m', // short-TTL cache for fast timeframes
    );

    const items = result?.data;
    if (!items || !Array.isArray(items)) return [];

    return items.map(item => ({
      timestamp: item.timestamp ?? 0,
      open: item.open ?? 0,
      high: item.high ?? 0,
      low: item.low ?? 0,
      close: item.close ?? 0,
      volume: item.volume ?? 0,
    }));
  }

  /**
   * Get the list of DeFi protocols on a specific chain.
   *
   * Returns protocol names, categories, TVL, and metadata.
   * Cached for 30 minutes since protocol lists change slowly.
   *
   * @param chain - Blockchain identifier
   */
  async getProtocolList(chain: string = 'ethereum'): Promise<FootprintProtocol[]> {
    const fpChain = this.toFootprintChain(chain);

    const result = await this.fetchApi<{
      data?: Array<{
        slug?: string;
        name?: string;
        chain?: string;
        category?: string;
        tvl?: number;
        tvl_change_1d?: number;
        tvl_change_7d?: number;
        logo?: string;
        url?: string;
        description?: string;
      }>;
    }>(
      '/protocol/list',
      { chain: fpChain },
      30,
    );

    const items = result?.data;
    if (!items || !Array.isArray(items)) return [];

    return items.map(item => ({
      slug: item.slug || '',
      name: item.name || '',
      chain: item.chain || fpChain,
      category: item.category || '',
      tvl: item.tvl ?? 0,
      tvlChange1d: item.tvl_change_1d ?? 0,
      tvlChange7d: item.tvl_change_7d ?? 0,
      logo: item.logo ?? null,
      url: item.url ?? null,
      description: item.description ?? null,
    }));
  }

  /**
   * Get protocol TVL history for a specific protocol on a chain.
   *
   * Returns daily TVL snapshots with day-over-day and week-over-week changes.
   * Cached for 30 minutes as historical TVL data is stable.
   *
   * @param protocolSlug - Protocol identifier slug (e.g. 'uniswap-v3', 'aave')
   * @param chain - Blockchain identifier
   */
  async getProtocolTVL(
    protocolSlug: string,
    chain: string = 'ethereum',
  ): Promise<FootprintProtocolTVL[]> {
    const fpChain = this.toFootprintChain(chain);

    const result = await this.fetchApi<{
      data?: Array<{
        date?: string;
        tvl_usd?: number;
        tvl_change_1d?: number;
        tvl_change_7d?: number;
      }>;
    }>(
      '/protocol/tvl',
      { chain: fpChain, slug: protocolSlug },
      30,
    );

    const items = result?.data;
    if (!items || !Array.isArray(items)) return [];

    return items.map(item => ({
      date: item.date || '',
      tvlUsd: item.tvl_usd ?? 0,
      tvlChange1d: item.tvl_change_1d ?? 0,
      tvlChange7d: item.tvl_change_7d ?? 0,
    }));
  }

  /**
   * Get comprehensive token metrics including price, volume, market cap,
   * holder count, supply information, and period-over-period changes.
   *
   * Cached for 10 minutes as a balance between freshness and rate limit conservation.
   *
   * @param tokenAddress - The token contract address
   * @param chain - Blockchain identifier
   */
  async getTokenMetrics(
    tokenAddress: string,
    chain: string = 'ethereum',
  ): Promise<FootprintTokenMetrics | null> {
    const fpChain = this.toFootprintChain(chain);

    const result = await this.fetchApi<{
      data?: {
        address?: string;
        chain?: string;
        name?: string;
        symbol?: string;
        price_usd?: number;
        price_change_24h?: number;
        price_change_7d?: number;
        price_change_30d?: number;
        volume_24h?: number;
        volume_change_24h?: number;
        market_cap?: number;
        fdv?: number;
        holders?: number;
        holders_change_24h?: number;
        total_supply?: string;
        circulating_supply?: string;
        max_supply?: string | null;
        decimals?: number;
      };
    }>(
      '/token/metrics',
      { chain: fpChain, token_address: tokenAddress },
      10, // 10-minute cache — metrics change moderately
    );

    const d = result?.data;
    if (!d) return null;

    return {
      tokenAddress: d.address || tokenAddress,
      chain: d.chain || fpChain,
      name: d.name || '',
      symbol: d.symbol || '',
      priceUsd: d.price_usd ?? 0,
      priceChange24h: d.price_change_24h ?? 0,
      priceChange7d: d.price_change_7d ?? 0,
      priceChange30d: d.price_change_30d ?? 0,
      volume24h: d.volume_24h ?? 0,
      volumeChange24h: d.volume_change_24h ?? 0,
      marketCap: d.market_cap ?? 0,
      fdv: d.fdv ?? 0,
      holders: d.holders ?? 0,
      holdersChange24h: d.holders_change_24h ?? 0,
      totalSupply: d.total_supply || '0',
      circulatingSupply: d.circulating_supply || '0',
      maxSupply: d.max_supply ?? null,
      decimals: d.decimals ?? 18,
    };
  }

  /**
   * Get chain-level overview metrics including total TVL, volume,
   * transaction count, fees, protocol count, and unique addresses.
   *
   * Cached for 15 minutes as chain-level metrics change moderately.
   *
   * @param chain - Blockchain identifier
   */
  async getChainOverview(chain: string = 'ethereum'): Promise<FootprintChainOverview | null> {
    const fpChain = this.toFootprintChain(chain);

    const result = await this.fetchApi<{
      data?: {
        chain?: string;
        total_tvl?: number;
        total_tvl_change_1d?: number;
        total_tvl_change_7d?: number;
        total_volume_24h?: number;
        total_volume_change_24h?: number;
        total_transactions_24h?: number;
        total_fees_24h?: number;
        protocol_count?: number;
        unique_addresses?: number;
      };
    }>(
      '/chain/overview',
      { chain: fpChain },
      15, // 15-minute cache for chain-level data
    );

    const d = result?.data;
    if (!d) return null;

    return {
      chain: d.chain || fpChain,
      totalTvl: d.total_tvl ?? 0,
      totalTvlChange1d: d.total_tvl_change_1d ?? 0,
      totalTvlChange7d: d.total_tvl_change_7d ?? 0,
      totalVolume24h: d.total_volume_24h ?? 0,
      totalVolumeChange24h: d.total_volume_change_24h ?? 0,
      totalTransactions24h: d.total_transactions_24h ?? 0,
      totalFees24h: d.total_fees_24h ?? 0,
      protocolCount: d.protocol_count ?? 0,
      uniqueAddresses: d.unique_addresses ?? 0,
    };
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const footprintClient = new FootprintClient();
