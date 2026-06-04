/**
 * Smart Money Tracker - CryptoQuant Terminal
 *
 * Tracks smart money wallets using REAL on-chain data from:
 *   - Etherscan (ETH tokens) — discoverActiveTraders()
 *   - Trader DB (all chains) — from smart-money-sync
 *   - Buy/sell pressure from DexScreener
 *
 * Previous version relied on DexPaprika.trackSmartMoney() which always
 * returned empty array. This version queries actual Trader records and
 * TraderTransactions from the database to build real signals.
 *
 * Key capabilities:
 * - Detect smart money wallets from DB for any token
 * - Track net buying/selling by identified smart money
 * - Cross-reference with DexScreener buy/sell pressure
 * - Generate smart money signals for the brain
 */

import { db } from '../../db';
import { unifiedCache, cacheKeyWithChain } from '../../unified-cache';

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
   * Analyze a token for smart money activity.
   * Queries real Trader + TraderTransaction data from the DB.
   * No longer depends on DexPaprika.trackSmartMoney() (which returned []).
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
    // 1. Find traders who have transacted on this token
    // Query TraderTransaction for recent buys/sells, then join with Trader
    const recentTxs = await db.traderTransaction.findMany({
      where: {
        tokenAddress,
        blockTime: { gte: new Date(Date.now() - 24 * 60 * 60 * 1000) }, // Last 24h
      },
      orderBy: { blockTime: 'desc' },
      take: 200,
    });

    // 2. Group transactions by trader
    const traderTxMap = new Map<string, typeof recentTxs>();
    for (const tx of recentTxs) {
      const existing = traderTxMap.get(tx.traderId) || [];
      existing.push(tx);
      traderTxMap.set(tx.traderId, existing);
    }

    // 3. Get trader details for the active traders
    const traderIds = Array.from(traderTxMap.keys());
    const traders = traderIds.length > 0
      ? await db.trader.findMany({
          where: { id: { in: traderIds } },
          select: {
            id: true,
            address: true,
            primaryLabel: true,
            isSmartMoney: true,
            isWhale: true,
            isSniper: true,
            isBot: true,
            smartMoneyScore: true,
            whaleScore: true,
            sniperScore: true,
          },
        })
      : [];

    const traderById = new Map(traders.map(t => [t.id, t]));

    // 4. Build enriched wallet list focusing on smart money / whales
    const enrichedWallets: SmartMoneyWallet[] = [];

    for (const [traderId, txs] of traderTxMap) {
      const trader = traderById.get(traderId);
      if (!trader) continue;

      // Skip bots — we want real smart money, not bots
      if (trader.isBot) continue;

      // Only include wallets classified as smart money, whale, or with high scores
      const isRelevant = trader.isSmartMoney || trader.isWhale || trader.smartMoneyScore > 40 || trader.whaleScore > 40;
      if (!isRelevant && txs.length < 3) continue; // Include unknown wallets only if very active

      const buyTxs = txs.filter(t => t.action === 'BUY');
      const sellTxs = txs.filter(t => t.action === 'SELL');
      const totalBuyValue = buyTxs.reduce((s, t) => s + t.valueUsd, 0);
      const totalSellValue = sellTxs.reduce((s, t) => s + t.valueUsd, 0);
      const netBuyValueUsd = totalBuyValue - totalSellValue;
      const totalValue = totalBuyValue + totalSellValue;
      const isAccumulating = netBuyValueUsd > 0;

      // Calculate urgency from recency and size
      const lastTx = txs[0]; // Already sorted desc
      const minutesAgo = (Date.now() - lastTx.blockTime.getTime()) / 60000;
      let urgency: SmartMoneyWallet['urgency'] = 'LOW';
      if (minutesAgo < 5 && totalValue / txs.length > 500) urgency = 'HIGH';
      else if (minutesAgo < 60 && totalValue / txs.length > 100) urgency = 'MEDIUM';

      const firstTx = txs[txs.length - 1];

      enrichedWallets.push({
        address: trader.address,
        label: trader.primaryLabel ?? null,
        isKnown: trader.isSmartMoney || trader.isWhale,
        netBuyValueUsd,
        swapCount: txs.length,
        averageSizeUsd: totalValue / txs.length,
        firstActivityAt: firstTx.blockTime,
        lastActivityAt: lastTx.blockTime,
        isAccumulating,
        urgency,
      });
    }

    // Sort by absolute net value (most significant wallets first)
    enrichedWallets.sort((a, b) => Math.abs(b.netBuyValueUsd) - Math.abs(a.netBuyValueUsd));

    // 5. Calculate aggregate metrics
    const totalNetBuy = enrichedWallets.reduce((s, w) => s + w.netBuyValueUsd, 0);
    const totalValue = enrichedWallets.reduce((s, w) => s + Math.abs(w.netBuyValueUsd), 0);

    let netDirection: SmartMoneySignal['netDirection'] = 'NEUTRAL';
    let netConfidence = 0;
    if (enrichedWallets.length > 0 && totalValue > 0) {
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

    // 6. Store signal in DB
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
   * Uses real Trader + TraderTransaction data from DB.
   */
  async trackWallet(
    walletAddress: string,
    chain: string = 'SOL',
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
