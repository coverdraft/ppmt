/**
 * Smart Money Tracker - CryptoQuant Terminal
 *
 * Tracks smart money wallets using DexPaprika's unique swap-level
 * wallet data. Identifies wallets with consistent profitability,
 * tracks their positions across pools, and feeds signals to the brain.
 *
 * Key capabilities:
 * - Detect wallets with above-average performance in a pool
 * - Track net buying/selling by identified smart money
 * - Cross-reference with existing Trader database
 * - Generate smart money signals for the brain
 */

import { dexPaprikaClient, type SmartMoneySwap, type DexPaprikaSwap } from './dexpaprika-client';
import { db } from '../db';
import { unifiedCache, cacheKey, cacheKeyWithChain } from '../unified-cache';

// ============================================================
// TYPES
// ============================================================

export interface SmartMoneySignal {
  poolId: string;
  chain: string;
  tokenAddress: string;
  tokenSymbol: string;

  /** Smart money wallets currently active */
  activeWallets: SmartMoneyWallet[];

  /** Aggregate net direction */
  netDirection: 'ACCUMULATING' | 'DISTRIBUTING' | 'NEUTRAL';
  netConfidence: number;

  /** Total smart money value in play */
  totalValueUsd: number;

  /** Signal strength (0-100) */
  signalStrength: number;

  detectedAt: Date;
}

export interface SmartMoneyWallet {
  address: string;
  /** Known from our Trader DB if available */
  label: string | null;
  isKnown: boolean;

  /** Recent activity summary */
  netBuyValueUsd: number;
  swapCount: number;
  averageSizeUsd: number;
  firstActivityAt: Date;
  lastActivityAt: Date;

  /** Performance indicators */
  isAccumulating: boolean;
  urgency: 'LOW' | 'MEDIUM' | 'HIGH';
}

export interface WalletActivityProfile {
  address: string;
  pools: {
    poolId: string;
    chain: string;
    tokenSymbol: string;
    netBuyValueUsd: number;
    swapCount: number;
    lastActiveAt: Date;
  }[];
  totalPoolsActive: number;
  totalValueUsd: number;
  dominantDirection: 'BUY' | 'SELL' | 'MIXED';
  isSmartMoney: boolean;
  smartMoneyScore: number;
}

// ============================================================
// SMART MONEY TRACKER CLASS
// ============================================================

class SmartMoneyTracker {
  private readonly SOURCE = 'smart-money';

  /**
   * Analyze a pool for smart money activity.
   * Gets recent swaps, identifies significant wallets, and
   * cross-references with our Trader database.
   */
  async analyzePool(
    chain: string,
    poolId: string,
    tokenAddress: string,
    tokenSymbol: string = '',
  ): Promise<SmartMoneySignal> {
    const cacheKeyStr = cacheKeyWithChain(this.SOURCE, 'pool-analysis', chain, poolId);

    return unifiedCache.getOrFetch(
      cacheKeyStr,
      () => this._analyzePool(chain, poolId, tokenAddress, tokenSymbol),
      this.SOURCE,
      30_000, // 30s cache
    );
  }

  private async _analyzePool(
    chain: string,
    poolId: string,
    tokenAddress: string,
    tokenSymbol: string,
  ): Promise<SmartMoneySignal> {
    // 1. Get smart money swaps from DexPaprika
    const smartMoneySwaps = await dexPaprikaClient.trackSmartMoney(
      chain, poolId,
      2,   // min 2 swaps
      50,  // min $50 value
    );

    // 2. Enrich with our Trader DB data
    const enrichedWallets: SmartMoneyWallet[] = [];

    for (const sm of smartMoneySwaps) {
      const knownTrader = await this.getKnownTrader(sm.wallet);
      const isAccumulating = sm.netBuyValueUsd > 0;

      // Calculate urgency based on recency and size
      const minutesAgo = (Date.now() - sm.lastSwapAt.getTime()) / 60000;
      let urgency: SmartMoneyWallet['urgency'] = 'LOW';
      if (minutesAgo < 5 && sm.averageSizeUsd > 500) urgency = 'HIGH';
      else if (minutesAgo < 30 && sm.averageSizeUsd > 100) urgency = 'MEDIUM';

      enrichedWallets.push({
        address: sm.wallet,
        label: knownTrader?.primaryLabel ?? null,
        isKnown: knownTrader !== null,
        netBuyValueUsd: sm.netBuyValueUsd,
        swapCount: sm.swapCount,
        averageSizeUsd: sm.averageSizeUsd,
        firstActivityAt: sm.firstSwapAt,
        lastActivityAt: sm.lastSwapAt,
        isAccumulating,
        urgency,
      });
    }

    // 3. Calculate aggregate metrics
    const totalNetBuy = enrichedWallets.reduce((s, w) => s + w.netBuyValueUsd, 0);
    const totalValue = enrichedWallets.reduce((s, w) => s + Math.abs(w.netBuyValueUsd), 0);

    let netDirection: SmartMoneySignal['netDirection'] = 'NEUTRAL';
    let netConfidence = 0;
    if (totalValue > 0) {
      const buyRatio = enrichedWallets.filter(w => w.isAccumulating).length / enrichedWallets.length;
      if (buyRatio > 0.6) {
        netDirection = 'ACCUMULATING';
        netConfidence = buyRatio;
      } else if (buyRatio < 0.4) {
        netDirection = 'DISTRIBUTING';
        netConfidence = 1 - buyRatio;
      }
    }

    // Signal strength based on: number of SM wallets, total value, direction agreement
    const walletScore = Math.min(30, enrichedWallets.length * 5);
    const valueScore = Math.min(40, totalValue / 1000);
    const directionScore = netConfidence * 30;
    const signalStrength = Math.min(100, walletScore + valueScore + directionScore);

    // 4. Store signal in DB
    try {
      await db.signal.create({
        data: {
          type: 'SMART_MONEY',
          tokenId: tokenAddress,
          confidence: Math.round(signalStrength),
          direction: netDirection === 'ACCUMULATING' ? 'LONG' : netDirection === 'DISTRIBUTING' ? 'SHORT' : 'NEUTRAL',
          description: `${enrichedWallets.length} SM wallets ${netDirection.toLowerCase()} in ${tokenSymbol || tokenAddress.slice(0, 8)}`,
          metadata: JSON.stringify({
            poolId,
            chain,
            netDirection,
            netConfidence,
            totalValueUsd: totalValue,
            walletCount: enrichedWallets.length,
            topWallets: enrichedWallets.slice(0, 5).map(w => ({
              address: w.address,
              netBuyValueUsd: w.netBuyValueUsd,
              isKnown: w.isKnown,
              label: w.label,
            })),
          }),
        },
      });
    } catch {
      // Signal storage is best-effort
    }

    return {
      poolId,
      chain,
      tokenAddress,
      tokenSymbol,
      activeWallets: enrichedWallets,
      netDirection,
      netConfidence,
      totalValueUsd: totalValue,
      signalStrength,
      detectedAt: new Date(),
    };
  }

  /**
   * Track a specific wallet's activity across pools.
   */
  async trackWallet(
    walletAddress: string,
    chain: string = 'solana',
  ): Promise<WalletActivityProfile> {
    // Check if we know this wallet
    const knownTrader = await this.getKnownTrader(walletAddress);

    // Get recent transactions from DB
    let recentTransactions: { tokenAddress: string; action: string; valueUsd: number; blockTime: Date }[] = [];
    try {
      const trader = await db.trader.findUnique({
        where: { address: walletAddress },
        include: {
          transactions: {
            orderBy: { blockTime: 'desc' },
            take: 50,
          },
        },
      });
      if (trader) {
        recentTransactions = trader.transactions.map(tx => ({
          tokenAddress: tx.tokenAddress,
          action: tx.action,
          valueUsd: tx.valueUsd,
          blockTime: tx.blockTime,
        }));
      }
    } catch {
      // DB access is best-effort
    }

    // Build activity profile
    const poolActivity = new Map<string, WalletActivityProfile['pools'][0]>();
    for (const tx of recentTransactions) {
      const key = `${chain}:${tx.tokenAddress}`;
      const existing = poolActivity.get(key);
      const netBuy = existing?.netBuyValueUsd ?? 0;

      poolActivity.set(key, {
        poolId: tx.tokenAddress, // Token address as pool proxy
        chain,
        tokenSymbol: '',
        netBuyValueUsd: netBuy + (tx.action === 'BUY' ? tx.valueUsd : -tx.valueUsd),
        swapCount: (existing?.swapCount ?? 0) + 1,
        lastActiveAt: tx.blockTime > (existing?.lastActiveAt ?? new Date(0))
          ? tx.blockTime : existing?.lastActiveAt ?? new Date(),
      });
    }

    const totalValue = Array.from(poolActivity.values())
      .reduce((s, p) => s + Math.abs(p.netBuyValueUsd), 0);
    const buyPools = Array.from(poolActivity.values())
      .filter(p => p.netBuyValueUsd > 0).length;

    const isSmartMoney = knownTrader?.isSmartMoney ?? false;
    const smartMoneyScore = knownTrader?.smartMoneyScore ?? 0;

    return {
      address: walletAddress,
      pools: Array.from(poolActivity.values()),
      totalPoolsActive: poolActivity.size,
      totalValueUsd: totalValue,
      dominantDirection: buyPools > poolActivity.size / 2 ? 'BUY'
        : buyPools < poolActivity.size / 2 ? 'SELL' : 'MIXED',
      isSmartMoney,
      smartMoneyScore,
    };
  }

  /**
   * Batch analyze pools for smart money activity.
   */
  async batchAnalyzePools(
    chain: string,
    poolIds: string[],
    tokenAddresses: string[],
    tokenSymbols: string[] = [],
  ): Promise<SmartMoneySignal[]> {
    const results = await Promise.allSettled(
      poolIds.map((poolId, i) =>
        this.analyzePool(chain, poolId, tokenAddresses[i], tokenSymbols[i])
      )
    );

    return results
      .filter((r): r is PromiseFulfilledResult<SmartMoneySignal> => r.status === 'fulfilled')
      .map(r => r.value);
  }

  // ----------------------------------------------------------
  // PRIVATE HELPERS
  // ----------------------------------------------------------

  private async getKnownTrader(address: string): Promise<{
    primaryLabel: string;
    isSmartMoney: boolean;
    smartMoneyScore: number;
  } | null> {
    try {
      const trader = await db.trader.findUnique({
        where: { address },
        select: {
          primaryLabel: true,
          isSmartMoney: true,
          smartMoneyScore: true,
        },
      });
      return trader;
    } catch {
      return null;
    }
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const smartMoneyTracker = new SmartMoneyTracker();
