/**
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║  Multi-Chain Screener — Discover tradeable tokens across 35 chains     ║
 * ║  Powered by DexPaprika: FREE, no API key, 26M+ tokens                  ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 *
 * The key advantage: we can now discover tradeable tokens on 35 chains,
 * not just Solana. This service screens top pools across multiple priority
 * chains and produces scored ScreenedToken results for the brain.
 *
 * Scoring System:
 *   - buyPressure (0-100): Derived from buy/sell ratio across 1h/6h/24h
 *   - volumeHealth (0-100): Based on volume trends and consistency
 *   - chainActivity (0-100): Based on chain's overall activity level
 *
 * Usage:
 *   const screener = new MultiChainScreener(dexPaprikaClient);
 *   const tokens = await screener.screenAllChains();
 *   const ethTokens = await screener.screenChain('ethereum', { minVolumeUsd: 100000 });
 *   const health = await screener.getChainHealth();
 */

import {
  DexPaprikaClient,
  DexPaprikaNetwork,
  DexPaprikaPool,
  DexPaprikaTokenDetail,
  DexPaprikaScreenOptions,
} from './dexpaprika-client';
import { UnifiedCache } from './source-cache';

// ============================================================
// TYPES & INTERFACES
// ============================================================

/** Priority-ordered chain list for screening */
const PRIORITY_CHAINS = [
  'ethereum',
  'bsc',
  'polygon',
  'solana',
  'base',
  'arbitrum',
] as const;

/** Additional chains for extended screening */
const EXTENDED_CHAINS = [
  'optimism',
  'avalanche',
  'fantom',
  'cronos',
  'linea',
  'zksync_era',
  'mantle',
  'scroll',
  'polygon_zkevm',
  'celo',
] as const;

/** All chains we screen by default */
const DEFAULT_CHAINS = [...PRIORITY_CHAINS, ...EXTENDED_CHAINS] as const;

/** Scored token result for the brain */
export interface ScreenedToken {
  // Identity
  tokenId: string;
  tokenName: string;
  tokenSymbol: string;
  networkId: string;
  chain: string;

  // Pool info
  poolId: string;
  dexId: string;
  dexName: string;

  // Price data
  priceUsd: number;
  priceChange5m: number;
  priceChange1h: number;
  priceChange24h: number;

  // Volume & liquidity
  volumeUsd: number;
  liquidityUsd: number;
  fdv: number;
  marketCap: number;

  // Buy/sell ratios from DexPaprika
  buySellRatio1h: number;
  buySellRatio6h: number;
  buySellRatio24h: number;
  buyVolumeUsd: number;
  sellVolumeUsd: number;
  buyCount1h: number;
  sellCount1h: number;
  buyCount24h: number;
  sellCount24h: number;

  // ATH
  athPrice: number;

  // Pool count
  poolCount: number;

  // === COMPOSITE SCORES (0-100) ===

  /** Buy pressure score (0-100): Derived from buy/sell ratios */
  buyPressure: number;

  /** Volume health score (0-100): Based on volume trends and consistency */
  volumeHealth: number;

  /** Chain activity score (0-100): Based on chain's overall activity level */
  chainActivity: number;

  /** Overall screening score (0-100): Weighted composite */
  overallScore: number;

  // Metadata
  screenedAt: number;
}

/** Chain health snapshot */
export interface ChainHealth {
  networkId: string;
  displayName: string;
  volume24h: number;
  txns24h: number;
  poolsCount: number;
  activityScore: number; // 0-100
  isActive: boolean;
}

/** Options for single-chain screening */
export interface ChainScreenOptions extends DexPaprikaScreenOptions {
  /** Maximum number of pools to evaluate per chain */
  maxPools?: number;
  /** Whether to enrich with token detail (buy/sell ratios) */
  enrichWithTokenDetail?: boolean;
  /** Minimum volume in USD */
  minVolumeUsd?: number;
  /** Minimum liquidity in USD */
  minLiquidityUsd?: number;
  /** Minimum buy/sell ratio */
  minBuySellRatio?: number;
  /** Maximum buy/sell ratio */
  maxBuySellRatio?: number;
  /** Maximum tokens to return */
  limit?: number;
}

/** Options for all-chains screening */
export interface AllChainsScreenOptions extends ChainScreenOptions {
  /** Specific chains to screen (defaults to PRIORITY_CHAINS) */
  chains?: string[];
  /** Whether to include extended chains */
  includeExtended?: boolean;
}

// ============================================================
// SCORING FUNCTIONS
// ============================================================

/**
 * Calculate buy pressure score (0-100) from buy/sell ratios.
 *
 * Logic:
 *   - ratio < 0.5 → heavy selling (0-20)
 *   - ratio 0.5-0.9 → moderate selling (20-40)
 *   - ratio 0.9-1.1 → balanced (40-60)
 *   - ratio 1.1-2.0 → moderate buying (60-80)
 *   - ratio > 2.0 → heavy buying (80-100)
 *
 * Weighted: 1h=50%, 6h=30%, 24h=20% (recency bias)
 */
function calculateBuyPressure(
  ratio1h: number,
  ratio6h: number,
  ratio24h: number,
): number {
  const scoreFromRatio = (ratio: number): number => {
    if (ratio <= 0) return 0;
    if (ratio < 0.5) return ratio / 0.5 * 20;
    if (ratio < 0.9) return 20 + (ratio - 0.5) / 0.4 * 20;
    if (ratio < 1.1) return 40 + (ratio - 0.9) / 0.2 * 20;
    if (ratio < 2.0) return 60 + (ratio - 1.1) / 0.9 * 20;
    return Math.min(100, 80 + (ratio - 2.0) / 3.0 * 20);
  };

  const s1h = scoreFromRatio(ratio1h);
  const s6h = scoreFromRatio(ratio6h);
  const s24h = scoreFromRatio(ratio24h);

  return Math.round(s1h * 0.5 + s6h * 0.3 + s24h * 0.2);
}

/**
 * Calculate volume health score (0-100) based on volume and pool metrics.
 *
 * Considers:
 *   - Absolute volume (log-scaled)
 *   - Transaction count
 *   - Pool count (diversity of liquidity venues)
 *   - Volume consistency (1h vs 24h ratio)
 */
function calculateVolumeHealth(
  volume24h: number,
  txns24h: number,
  poolCount: number,
  volume1h: number,
): number {
  // Volume score (log-scaled, 10K-1B range)
  const logVol = Math.log10(Math.max(1, volume24h));
  const volScore = Math.min(100, Math.max(0, (logVol - 4) / 5 * 100)); // 10K=0, 1B=100

  // Transaction score (log-scaled, 10-100K range)
  const logTxns = Math.log10(Math.max(1, txns24h));
  const txnScore = Math.min(100, Math.max(0, (logTxns - 1) / 4 * 100)); // 10=0, 100K=100

  // Pool diversity score (1=0, 50=100)
  const poolScore = Math.min(100, Math.max(0, poolCount / 50 * 100));

  // Volume consistency: 1h volume should be roughly 1/24 of 24h volume
  const expected1h = volume24h / 24;
  const consistencyRatio = expected1h > 0 ? volume1h / expected1h : 0;
  const consistencyScore = Math.min(100, Math.max(0,
    consistencyRatio > 0.5 && consistencyRatio < 2.0
      ? 100 - Math.abs(1 - consistencyRatio) * 50
      : Math.max(0, 50 - Math.abs(1 - consistencyRatio) * 25)
  ));

  return Math.round(volScore * 0.4 + txnScore * 0.25 + poolScore * 0.15 + consistencyScore * 0.2);
}

/**
 * Calculate chain activity score (0-100) based on chain-wide metrics.
 *
 * Considers:
 *   - 24h volume on chain
 *   - Transaction count
 *   - Pool count
 */
function calculateChainActivity(network: DexPaprikaNetwork): number {
  // Volume score (log-scaled, 100K-10B range)
  const logVol = Math.log10(Math.max(1, network.volume_usd_24h));
  const volScore = Math.min(100, Math.max(0, (logVol - 5) / 5 * 100)); // 100K=0, 10B=100

  // Transaction score (log-scaled, 100-1M range)
  const logTxns = Math.log10(Math.max(1, network.txns_24h));
  const txnScore = Math.min(100, Math.max(0, (logTxns - 2) / 4 * 100)); // 100=0, 1M=100

  // Pool count score (100=0, 50K=100)
  const poolScore = Math.min(100, Math.max(0, network.pools_count / 50000 * 100));

  return Math.round(volScore * 0.5 + txnScore * 0.3 + poolScore * 0.2);
}

/**
 * Calculate overall screening score (0-100).
 *
 * Weighted: buyPressure=40%, volumeHealth=35%, chainActivity=25%
 */
function calculateOverallScore(
  buyPressure: number,
  volumeHealth: number,
  chainActivity: number,
): number {
  return Math.round(buyPressure * 0.4 + volumeHealth * 0.35 + chainActivity * 0.25);
}

// ============================================================
// MULTI-CHAIN SCREENER
// ============================================================

export class MultiChainScreener {
  private client: DexPaprikaClient;
  private cache: UnifiedCache;

  constructor(client: DexPaprikaClient, cache?: UnifiedCache) {
    this.client = client;
    this.cache = cache || new UnifiedCache(5);
  }

  /**
   * Screen top tokens across multiple priority chains.
   * The primary method for the brain's cross-chain discovery.
   *
   * Screens each chain's top pools, enriches with buy/sell data,
   * and returns scored results sorted by overall score.
   */
  async screenAllChains(
    options: AllChainsScreenOptions = {},
  ): Promise<ScreenedToken[]> {
    const {
      chains,
      includeExtended = false,
      maxPools = 10,
      enrichWithTokenDetail = true,
      ...screenOpts
    } = options;

    // Determine which chains to screen
    const targetChains = chains || [
      ...PRIORITY_CHAINS,
      ...(includeExtended ? EXTENDED_CHAINS : []),
    ];

    // Get chain health data for scoring
    const chainHealthMap = await this.getChainHealthMap();

    // Screen each chain in parallel (but with rate limiting from the client)
    const allTokens: ScreenedToken[] = [];

    const screenPromises = targetChains.map(async (chainId) => {
      try {
        const chainTokens = await this.screenChain(chainId, {
          ...screenOpts,
          maxPools,
          enrichWithTokenDetail,
        });
        return chainTokens;
      } catch (err) {
        console.error(`[MultiChainScreener] Error screening ${chainId}:`, err);
        return [];
      }
    });

    const results = await Promise.all(screenPromises);
    for (const tokens of results) {
      allTokens.push(...tokens);
    }

    // Apply chain activity scores
    for (const token of allTokens) {
      const health = chainHealthMap[token.networkId];
      token.chainActivity = health?.activityScore ?? 50;
      token.overallScore = calculateOverallScore(
        token.buyPressure,
        token.volumeHealth,
        token.chainActivity,
      );
    }

    // Sort by overall score descending
    allTokens.sort((a, b) => b.overallScore - a.overallScore);

    return allTokens;
  }

  /**
   * Screen a single chain for tradeable tokens.
   * Returns scored tokens with buy/sell ratios and composite scores.
   */
  async screenChain(
    networkId: string,
    options: ChainScreenOptions = {},
  ): Promise<ScreenedToken[]> {
    const {
      maxPools = 10,
      enrichWithTokenDetail = true,
      minVolumeUsd = 0,
      minLiquidityUsd = 0,
      minBuySellRatio = 0,
      maxBuySellRatio = Infinity,
      limit = 20,
    } = options;

    // Fetch top pools for this chain
    const pools = await this.client.getTopPoolsByVolume(networkId, maxPools);

    // Filter by minimum volume (DexPaprikaPool uses volume.h24)
    const filteredPools = pools.filter(p => (p.volume?.h24 ?? 0) >= minVolumeUsd);

    const screenedTokens: ScreenedToken[] = [];
    const now = Date.now();

    for (const pool of filteredPools) {
      if (screenedTokens.length >= limit) break;

      const targetToken = pool.baseToken; // Primary token in the pair
      if (!targetToken) continue;

      // Enrich with token detail if requested
      let tokenDetail: DexPaprikaTokenDetail | null = null;
      let buySellRatio1h = 0;
      let buySellRatio6h = 0;
      let buySellRatio24h = 0;
      let buyVolumeUsd = 0;
      let sellVolumeUsd = 0;
      let buyCount1h = 0;
      let sellCount1h = 0;
      let buyCount24h = 0;
      let sellCount24h = 0;
      let athPrice = 0;
      let liquidityUsd = pool.liquidity?.usd ?? 0;
      let poolCount = 0;

      if (enrichWithTokenDetail) {
        tokenDetail = await this.client.getTokenDetail(networkId, targetToken.address);

        if (tokenDetail?.summary) {
          const s = tokenDetail.summary;
          buySellRatio1h = s['1h']?.sells ? s['1h'].buys / s['1h'].sells : 0;
          buySellRatio6h = s['6h']?.sells ? s['6h'].buys / s['6h'].sells : 0;
          buySellRatio24h = s['24h']?.sells ? s['24h'].buys / s['24h'].sells : 0;
          buyVolumeUsd = s['24h']?.buy_usd || 0;
          sellVolumeUsd = s['24h']?.sell_usd || 0;
          buyCount1h = s['1h']?.buys || 0;
          sellCount1h = s['1h']?.sells || 0;
          buyCount24h = s['24h']?.buys || 0;
          sellCount24h = s['24h']?.sells || 0;
          liquidityUsd = tokenDetail.liquidity_usd_value ?? liquidityUsd;
          poolCount = tokenDetail.pool_count ?? 0;
        }

        if (tokenDetail?.ath_price) {
          athPrice = tokenDetail.ath_price;
        }
      }

      // Apply filters
      if (minLiquidityUsd > 0 && liquidityUsd < minLiquidityUsd) continue;
      if (buySellRatio24h < minBuySellRatio) continue;
      if (buySellRatio24h > maxBuySellRatio) continue;

      // Calculate scores
      const buyPressure = calculateBuyPressure(buySellRatio1h, buySellRatio6h, buySellRatio24h);
      const volumeHealth = calculateVolumeHealth(
        pool.volume?.h24 ?? 0,
        (pool.txns?.h24?.buys ?? 0) + (pool.txns?.h24?.sells ?? 0),
        poolCount,
        pool.volume?.h1 ?? 0,
      );

      screenedTokens.push({
        tokenId: targetToken.address,
        tokenName: targetToken.name,
        tokenSymbol: targetToken.symbol,
        networkId,
        chain: pool.chain,

        poolId: pool.id,
        dexId: pool.dexId,
        dexName: pool.dexId, // Use dexId as name

        priceUsd: parseFloat(pool.priceUsd || '0'),
        priceChange5m: 0, // Not available in DexPaprikaPool
        priceChange1h: 0, // Derived from buy ratios
        priceChange24h: 0,

        volumeUsd: pool.volume?.h24 ?? 0,
        liquidityUsd,
        fdv: pool.fdv ?? 0,
        marketCap: pool.marketCap ?? pool.fdv ?? 0,

        buySellRatio1h,
        buySellRatio6h,
        buySellRatio24h,
        buyVolumeUsd,
        sellVolumeUsd,
        buyCount1h,
        sellCount1h,
        buyCount24h,
        sellCount24h,

        athPrice,
        poolCount,

        buyPressure,
        volumeHealth,
        chainActivity: 0, // Will be populated by screenAllChains
        overallScore: 0, // Will be calculated after chain activity scoring

        screenedAt: now,
      });
    }

    // Calculate initial overall scores (without chain activity)
    for (const token of screenedTokens) {
      token.overallScore = calculateOverallScore(
        token.buyPressure,
        token.volumeHealth,
        50, // Default chain activity until populated
      );
    }

    // Sort by overall score
    screenedTokens.sort((a, b) => b.overallScore - a.overallScore);

    return screenedTokens;
  }

  /**
   * Get health/activity data for all chains.
   * Useful for deciding which chains are most active and worth screening.
   *
   * Returns chain health sorted by activity score (highest first).
   */
  async getChainHealth(): Promise<ChainHealth[]> {
    const cacheKey = 'multichain-screener:chain-health';
    const cached = this.cache.get<ChainHealth[]>(cacheKey);
    if (cached) return cached;

    const networks = await this.client.getNetworks();

    const healthData: ChainHealth[] = networks.map(network => {
      const activityScore = calculateChainActivity(network);
      return {
        networkId: network.id,
        displayName: network.display_name,
        volume24h: network.volume_usd_24h,
        txns24h: network.txns_24h,
        poolsCount: network.pools_count,
        activityScore,
        isActive: activityScore >= 30, // Threshold for "active"
      };
    });

    // Sort by activity score descending
    healthData.sort((a, b) => b.activityScore - a.activityScore);

    this.cache.set(cacheKey, healthData, 10); // Cache for 10 minutes
    return healthData;
  }

  /**
   * Internal helper: get chain health as a map for quick lookups.
   */
  private async getChainHealthMap(): Promise<Record<string, ChainHealth>> {
    const healthData = await this.getChainHealth();
    const map: Record<string, ChainHealth> = {};
    for (const h of healthData) {
      map[h.networkId] = h;
    }
    return map;
  }

  /**
   * Get the list of priority chains for quick screening.
   */
  getPriorityChains(): readonly string[] {
    return PRIORITY_CHAINS;
  }

  /**
   * Get the list of extended chains for deeper screening.
   */
  getExtendedChains(): readonly string[] {
    return EXTENDED_CHAINS;
  }

  /**
   * Get all default chains.
   */
  getDefaultChains(): readonly string[] {
    return DEFAULT_CHAINS;
  }
}
