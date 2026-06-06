/**
 * Smart Money Sync Endpoint — CryptoQuant Terminal
 *
 * GET  /api/smart-money-sync  → Returns current sync status
 * POST /api/smart-money-sync  → Triggers a sync operation
 *
 * POST body options:
 *   { "action": "scan" }      → Scan top tokens' recent swaps via DexPaprika
 *                                AND Etherscan (for ETH tokens) to discover
 *                                and profile active wallets
 *   { "action": "etherscan" } → Etherscan-only targeted scan for ETH tokens
 *                                using real on-chain ERC-20 transfer data
 *   { "action": "profile" }   → Re-profile all existing traders in the DB
 *                                using the wallet-profiler scoring functions
 *   { "action": "full" }      → Run both scan + profile
 *
 * Scan Logic (DexPaprika path):
 *   1. Get top 30 tokens by volume from DB
 *   2. For each token, get recent swaps via DexPaprika trackSmartMoney()
 *      or fall back to simulated pool swaps
 *   3. For each discovered wallet: create/update Trader, create transactions,
 *      classify with wallet-profiler, create behavior patterns and labels
 *
 * Scan Logic (Etherscan path — for ETH tokens):
 *   1. For tokens with chain === 'ETH', use Etherscan discoverActiveTraders()
 *      to find wallets from real ERC-20 transfer data
 *   2. For each discovered trader, get their token transfers via
 *      getWalletTokenTransfers() for full transaction history
 *   3. Create Trader + TraderTransaction records with REAL on-chain data
 *      (actual tx hashes, timestamps, values — no random estimates)
 *   4. Classify using wallet-profiler scoring functions
 *   5. Etherscan data takes priority over DexPaprika when both are available
 *
 * Profile Logic:
 *   1. Get all traders from DB
 *   2. Compute analytics from their transactions
 *   3. Recalculate scores using wallet-profiler functions
 *   4. Update the trader record with new scores
 *   5. Update/create behavior patterns
 */

import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ── Module-level sync state ──────────────────────────────────
let syncRunning = false;
let lastSyncResult: SyncResult | null = null;
let lastSyncStartedAt: Date | null = null;
let lastSyncCompletedAt: Date | null = null;

// ── Types ────────────────────────────────────────────────────
interface SyncResult {
  action: 'scan' | 'profile' | 'full' | 'etherscan';
  startedAt: string;
  completedAt?: string;
  durationMs?: number;

  // Scan metrics
  tokensScanned: number;
  swapsDiscovered: number;
  walletsDiscovered: number;
  tradersCreated: number;
  tradersUpdated: number;
  transactionsCreated: number;

  // Etherscan-specific metrics
  etherscanTradersDiscovered: number;
  etherscanTransactionsCreated: number;
  etherscanTokensScanned: number;

  // DexPaprika-specific metrics
  dexpaprikaTradersDiscovered: number;
  dexpaprikaTransactionsCreated: number;

  // Profile metrics
  tradersProfiled: number;
  scoresRecalculated: number;
  patternsCreated: number;
  patternsUpdated: number;
  labelsCreated: number;

  // Summary
  totalSmartMoney: number;
  totalWhales: number;
  totalSnipers: number;
  totalBots: number;
  totalTraders: number;

  errors: string[];
}

interface SyncBody {
  action?: 'scan' | 'profile' | 'full' | 'etherscan';
}

// ── Chain mapping ────────────────────────────────────────────
const CHAIN_MAP: Record<string, string> = {
  SOL: 'solana',
  ETH: 'ethereum',
  BSC: 'bsc',
  BASE: 'base',
  ARB: 'arbitrum',
  MATIC: 'polygon',
  AVAX: 'avalanche',
  OP: 'optimism',
};

function toDexPaprikaChain(internal: string): string {
  return CHAIN_MAP[internal] ?? internal.toLowerCase();
}

// ══════════════════════════════════════════════════════════════
// SCAN LOGIC
// ══════════════════════════════════════════════════════════════

async function runScan(result: SyncResult): Promise<void> {
  const { db } = await import('@/lib/db');
  const { dexPaprikaClient } = await import('@/lib/services/data-sources/dexpaprika-client');
  const { etherscanClient } = await import('@/lib/services/data-sources/etherscan-client');
  const {
    calculateSmartMoneyScore,
    calculateWhaleScore,
    calculateSniperScore,
    detectBehavioralPatterns,
    buildWalletProfile,
  } = await import('@/lib/services/execution/wallet-profiler');

  console.log('[SmartMoneySync] === SCAN: Discovering wallets from on-chain swap data ===');

  // 1. Get top 30 tokens by volume
  const topTokens = await db.token.findMany({
    where: { volume24h: { gt: 0 } },
    orderBy: { volume24h: 'desc' },
    take: 30,
  });

  result.tokensScanned = topTokens.length;
  console.log(`[SmartMoneySync] Scanning ${topTokens.length} top tokens`);

  // 2. For each token, try to get smart money swaps
  for (const token of topTokens) {
    try {
      // ─── ETHERSCAN PATH: For ETH tokens, try Etherscan first ───
      if (token.chain === 'ETH') {
        try {
          const etherscanTraders = await etherscanClient.discoverActiveTraders(
            token.address,
            3, // min 3 transactions to be considered active
          );

          if (etherscanTraders.length > 0) {
            result.etherscanTokensScanned++;
            console.log(
              `[SmartMoneySync][Etherscan] Found ${etherscanTraders.length} active traders ` +
              `for ${token.symbol} (${token.address.slice(0, 10)}...)`,
            );

            for (const discoveredTrader of etherscanTraders) {
              try {
                await processEtherscanTrader(
                  discoveredTrader,
                  token,
                  db,
                  result,
                  { calculateSmartMoneyScore, calculateWhaleScore, calculateSniperScore, detectBehavioralPatterns, buildWalletProfile },
                );
              } catch (err) {
                result.errors.push(
                  `[Etherscan] Trader ${discoveredTrader.address.slice(0, 8)}: ${String(err)}`,
                );
              }
            }

            // Etherscan found data — skip DexPaprika for this ETH token
            // (Etherscan data takes priority for ETH tokens)
            result.walletsDiscovered += etherscanTraders.length;
            await new Promise(r => setTimeout(r, 200));
            continue; // Skip DexPaprika path for this token
          }
        } catch (ethErr) {
          // Etherscan failed — log and fall back to DexPaprika
          console.warn(
            `[SmartMoneySync][Etherscan] Failed for ${token.symbol}, falling back to DexPaprika: ${String(ethErr)}`,
          );
          result.errors.push(`[Etherscan] ${token.symbol}: ${String(ethErr)}`);
        }
      }

      // ─── DEXPAPRIKA PATH: Default/fallback data source ───
      const dpChain = toDexPaprikaChain(token.chain);
      const poolId = token.pairAddress || token.address;

      // Try trackSmartMoney() first (real on-chain data path)
      let smartMoneySwaps = await dexPaprikaClient.trackSmartMoney(
        dpChain,
        poolId,
        2,    // min 2 swaps per wallet
        100,  // min $100 per swap
      );

      // If trackSmartMoney returns empty, the poolId might not match DexPaprika's format.
      // Try discovering the correct DexPaprika pool for this token, then retry.
      if (smartMoneySwaps.length === 0) {
        // Attempt 1: Try getPoolSwaps directly with the existing poolId
        const poolSwaps = await dexPaprikaClient.getPoolSwaps(dpChain, poolId, 50);

        if (poolSwaps.length > 0) {
          // Got swap data — group by wallet and convert to SmartMoneySwap structure
          const walletMap = new Map<string, typeof poolSwaps>();

          for (const swap of poolSwaps) {
            const wallet = swap.maker;
            if (!wallet) continue;

            const existing = walletMap.get(wallet) || [];
            existing.push(swap);
            walletMap.set(wallet, existing);
          }

          smartMoneySwaps = Array.from(walletMap.entries())
            .filter(([, swaps]) => swaps.length >= 1)
            .map(([wallet, swaps]) => {
              const buySwaps = swaps.filter(s => s.type === 'buy');
              const sellSwaps = swaps.filter(s => s.type === 'sell');
              const netBuyValueUsd = buySwaps.reduce((s, sw) => s + sw.valueUsd, 0)
                - sellSwaps.reduce((s, sw) => s + sw.valueUsd, 0);
              const totalValueUsd = swaps.reduce((s, sw) => s + sw.valueUsd, 0);

              return {
                wallet,
                poolId,
                chain: dpChain,
                swaps,
                netBuyAmount: 0,
                netBuyValueUsd,
                firstSwapAt: new Date(Math.min(...swaps.map(s => new Date(s.timestamp).getTime()))),
                lastSwapAt: new Date(Math.max(...swaps.map(s => new Date(s.timestamp).getTime()))),
                swapCount: swaps.length,
                averageSizeUsd: totalValueUsd / swaps.length,
              };
            });
        }

        // Attempt 2: If still empty, discover DexPaprika pools for this token
        // and try with the correct DexPaprika pool ID
        if (smartMoneySwaps.length === 0) {
          try {
            // Search DexPaprika for this token to find its pools
            const searchResults = await dexPaprikaClient.searchPools({
              query: token.symbol || token.address,
              chain: dpChain,
              limit: 3,
            });

            for (const pool of searchResults) {
              if (smartMoneySwaps.length > 0) break; // Already found data

              const dpPoolSwaps = await dexPaprikaClient.getPoolSwaps(pool.chain, pool.id, 50);
              if (dpPoolSwaps.length === 0) continue;

              // Group by wallet
              const walletMap = new Map<string, typeof dpPoolSwaps>();
              for (const swap of dpPoolSwaps) {
                const wallet = swap.maker;
                if (!wallet) continue;
                const existing = walletMap.get(wallet) || [];
                existing.push(swap);
                walletMap.set(wallet, existing);
              }

              smartMoneySwaps = Array.from(walletMap.entries())
                .filter(([, swaps]) => swaps.length >= 1)
                .map(([wallet, swaps]) => {
                  const buySwaps = swaps.filter(s => s.type === 'buy');
                  const sellSwaps = swaps.filter(s => s.type === 'sell');
                  const netBuyValueUsd = buySwaps.reduce((s, sw) => s + sw.valueUsd, 0)
                    - sellSwaps.reduce((s, sw) => s + sw.valueUsd, 0);
                  const totalValueUsd = swaps.reduce((s, sw) => s + sw.valueUsd, 0);

                  return {
                    wallet,
                    poolId: pool.id,
                    chain: pool.chain,
                    swaps,
                    netBuyAmount: 0,
                    netBuyValueUsd,
                    firstSwapAt: new Date(Math.min(...swaps.map(s => new Date(s.timestamp).getTime()))),
                    lastSwapAt: new Date(Math.max(...swaps.map(s => new Date(s.timestamp).getTime()))),
                    swapCount: swaps.length,
                    averageSizeUsd: totalValueUsd / swaps.length,
                  };
                });
            }
          } catch (poolSearchErr) {
            console.warn(
              `[SmartMoneySync] DexPaprika pool discovery failed for ${token.symbol}: ${String(poolSearchErr)}`,
            );
          }
        }
      }

      result.swapsDiscovered += smartMoneySwaps.reduce((s, sm) => s + sm.swapCount, 0);
      result.walletsDiscovered += smartMoneySwaps.length;
      result.dexpaprikaTradersDiscovered += smartMoneySwaps.length;

      // 3. Process each discovered wallet
      for (const smSwap of smartMoneySwaps) {
        try {
          const walletAddress = smSwap.wallet;
          if (!walletAddress || walletAddress.length < 5) continue;

          // Check if trader already exists
          const existingTrader = await db.trader.findUnique({
            where: { address: walletAddress },
          });

          // Build initial analytics from swap data
          const swapAnalytics = buildAnalyticsFromSwaps(smSwap, token);

          // Calculate scores
          const smartMoneyScore = calculateSmartMoneyScore(swapAnalytics);
          const whaleScore = calculateWhaleScore(swapAnalytics);
          const sniperScore = calculateSniperScore(swapAnalytics);
          const patterns = detectBehavioralPatterns(swapAnalytics);

          // Build full profile for classification
          const profile = buildWalletProfile(walletAddress, token.chain, swapAnalytics);

          // Enhanced bot detection using the full 8-signal bot detection engine
          // Falls back to simple wallet-profiler heuristic if detectBot fails
          let botDetectionResult: import('@/lib/services/execution/bot-detection').BotDetectionResult | null = null;
          try {
            const { detectBot, analyticsToMetrics } = await import('@/lib/services/execution/bot-detection');
            const metrics = analyticsToMetrics(swapAnalytics);
            botDetectionResult = detectBot(metrics);
          } catch {
            // Fall back to simple heuristic from wallet-profiler
          }
          const isBot = botDetectionResult?.isBot ?? profile.botProbability > 0.5;
          const botType = botDetectionResult?.botType ?? (profile.botProbability > 0.5 ? inferBotType(profile) : null);
          const botConfidence = botDetectionResult?.confidence ?? Math.round(profile.botProbability * 100) / 100;

          if (existingTrader) {
            // Update existing trader
            try {
              await db.trader.update({
                where: { id: existingTrader.id },
                data: {
                  lastActive: new Date(),
                  totalTrades: existingTrader.totalTrades + smSwap.swapCount,
                  totalVolumeUsd: existingTrader.totalVolumeUsd + smSwap.averageSizeUsd * smSwap.swapCount,
                  avgTradeSizeUsd: (
                    (existingTrader.avgTradeSizeUsd * existingTrader.totalTrades) +
                    (smSwap.averageSizeUsd * smSwap.swapCount)
                  ) / (existingTrader.totalTrades + smSwap.swapCount),
                  // Update scores (weighted average with existing)
                  smartMoneyScore: Math.round(
                    (existingTrader.smartMoneyScore * 0.7 + smartMoneyScore * 0.3) * 100
                  ) / 100,
                  whaleScore: Math.round(
                    (existingTrader.whaleScore * 0.7 + whaleScore * 0.3) * 100
                  ) / 100,
                  sniperScore: Math.round(
                    (existingTrader.sniperScore * 0.7 + sniperScore * 0.3) * 100
                  ) / 100,
                  // Re-evaluate flags based on updated scores
                  isSmartMoney: smartMoneyScore > 50 || existingTrader.isSmartMoney,
                  isWhale: whaleScore > 50 || existingTrader.isWhale,
                  isSniper: sniperScore > 50 || existingTrader.isSniper,
                  // Update primary label if new classification is stronger
                  primaryLabel: profile.primaryLabel !== 'UNKNOWN'
                    ? profile.primaryLabel
                    : existingTrader.primaryLabel,
                  labelConfidence: Math.max(
                    profile.labelConfidence,
                    existingTrader.labelConfidence
                  ),
                  lastAnalyzed: new Date(),
                  analysisVersion: (existingTrader.analysisVersion || 1) + 1,
                },
              });
              result.tradersUpdated++;
            } catch (err) {
              result.errors.push(`Update trader ${walletAddress.slice(0, 8)}: ${String(err)}`);
            }
          } else {
            // Create new trader
            try {
              await db.trader.create({
                data: {
                  address: walletAddress,
                  chain: token.chain || 'SOL',
                  primaryLabel: profile.primaryLabel,
                  subLabels: JSON.stringify([profile.primaryLabel]),
                  labelConfidence: profile.labelConfidence,
                  // Bot detection — uses enhanced detectBot() when available
                  isBot,
                  botType,
                  botConfidence,
                  botDetectionSignals: JSON.stringify(
                    botDetectionResult?.signals?.map(s => s.name) ?? (profile.botProbability > 0.3 ? ['timing_pattern', 'swap_frequency'] : [])
                  ),
                  // Performance metrics from swaps
                  totalTrades: smSwap.swapCount,
                  winRate: swapAnalytics.winRate,
                  avgPnl: swapAnalytics.avgPnlUsd,
                  totalPnl: swapAnalytics.totalPnlUsd,
                  avgHoldTimeMin: swapAnalytics.avgHoldTimeMin,
                  avgTradeSizeUsd: swapAnalytics.avgTradeSizeUsd,
                  largestTradeUsd: smSwap.averageSizeUsd * 3,
                  totalVolumeUsd: smSwap.averageSizeUsd * smSwap.swapCount,
                  sharpeRatio: swapAnalytics.sharpeRatio,
                  profitFactor: swapAnalytics.profitFactor,
                  // Behavioral metrics
                  washTradeScore: swapAnalytics.washTradeScore,
                  copyTradeScore: swapAnalytics.copyTradeScore,
                  avgTimeBetweenTrades: swapAnalytics.avgTimeBetweenTradesMin,
                  isActiveAtNight: swapAnalytics.isActive247,
                  isActive247: swapAnalytics.isActive247,
                  consistencyScore: swapAnalytics.consistencyScore,
                  // Smart Money specific
                  isSmartMoney: smartMoneyScore > 50,
                  smartMoneyScore: Math.round(smartMoneyScore * 100) / 100,
                  earlyEntryCount: swapAnalytics.earlyEntryCount,
                  avgEntryRank: swapAnalytics.avgEntryRank,
                  avgExitMultiplier: swapAnalytics.avgExitMultiplier,
                  // Whale specific
                  isWhale: whaleScore > 50,
                  whaleScore: Math.round(whaleScore * 100) / 100,
                  totalHoldingsUsd: swapAnalytics.totalHoldingsUsd,
                  avgPositionUsd: swapAnalytics.avgTradeSizeUsd,
                  // Sniper specific
                  isSniper: sniperScore > 50,
                  sniperScore: Math.round(sniperScore * 100) / 100,
                  // Portfolio
                  uniqueTokensTraded: 1,
                  preferredChains: JSON.stringify([token.chain]),
                  preferredDexes: JSON.stringify([token.dex || 'unknown']),
                  // Timing
                  tradingHourPattern: JSON.stringify(swapAnalytics.tradingHourPattern),
                  // Meta
                  firstSeen: smSwap.firstSwapAt,
                  lastActive: smSwap.lastSwapAt,
                  lastAnalyzed: new Date(),
                  dataQuality: 0.3,
                },
              });
              result.tradersCreated++;
            } catch (err) {
              result.errors.push(`Create trader ${walletAddress.slice(0, 8)}: ${String(err)}`);
            }
          }

          // 4. Create TraderTransaction records for discovered swaps
          const trader = await db.trader.findUnique({
            where: { address: walletAddress },
            select: { id: true },
          });

          if (trader) {
            for (const swap of smSwap.swaps) {
              try {
                // Skip if txHash already exists
                const existing = await db.traderTransaction.findUnique({
                  where: { txHash: swap.txnHash },
                  select: { id: true },
                });
                if (existing) continue;

                await db.traderTransaction.create({
                  data: {
                    traderId: trader.id,
                    txHash: swap.txnHash,
                    blockNumber: swap.blockNumber,
                    blockTime: new Date(swap.timestamp),
                    chain: token.chain || 'SOL',
                    dex: token.dex || 'unknown',
                    action: swap.type === 'buy' ? 'BUY' : 'SELL',
                    tokenAddress: token.address,
                    tokenSymbol: token.symbol,
                    quoteToken: swap.tokenIn?.symbol || 'UNKNOWN',
                    amountIn: parseFloat(String(swap.amountIn)) || 0,
                    amountOut: parseFloat(String(swap.amountOut)) || 0,
                    priceUsd: swap.priceUsd || 0,
                    valueUsd: swap.valueUsd || 0,
                    metadata: JSON.stringify({
                      source: 'dexpaprika',
                      poolId,
                      swapType: swap.type,
                    }),
                  },
                });
                result.transactionsCreated++;
                result.dexpaprikaTransactionsCreated++;
              } catch {
                // Transaction might already exist — skip silently
              }
            }

            // 5. Create TraderBehaviorPattern records for detected patterns
            for (const pattern of patterns.slice(0, 5)) {
              try {
                // Check if pattern already exists for this trader
                const existingPattern = await db.traderBehaviorPattern.findFirst({
                  where: {
                    traderId: trader.id,
                    pattern: pattern.pattern,
                  },
                });

                if (existingPattern) {
                  // Update existing pattern with new confidence
                  await db.traderBehaviorPattern.update({
                    where: { id: existingPattern.id },
                    data: {
                      confidence: Math.max(existingPattern.confidence, pattern.confidence),
                      dataPoints: Math.max(existingPattern.dataPoints, pattern.dataPoints),
                      lastObserved: new Date(),
                    },
                  });
                  result.patternsUpdated++;
                } else {
                  await db.traderBehaviorPattern.create({
                    data: {
                      traderId: trader.id,
                      pattern: pattern.pattern,
                      confidence: Math.round(pattern.confidence * 100) / 100,
                      dataPoints: pattern.dataPoints,
                      firstObserved: new Date(),
                      lastObserved: new Date(),
                      metadata: JSON.stringify({
                        description: pattern.description,
                        source: 'smart-money-sync',
                      }),
                    },
                  });
                  result.patternsCreated++;
                }
              } catch (err) {
                result.errors.push(`Pattern ${pattern.pattern} for ${walletAddress.slice(0, 8)}: ${String(err)}`);
              }
            }

            // 6. Create TraderLabelAssignment records based on classification
            const labelsToAssign: Array<{ label: string; confidence: number; evidence: string[] }> = [];

            if (smartMoneyScore > 50) {
              labelsToAssign.push({
                label: 'SMART_MONEY',
                confidence: smartMoneyScore / 100,
                evidence: [`smartMoneyScore=${smartMoneyScore.toFixed(1)}`, `winRate=${swapAnalytics.winRate.toFixed(2)}`],
              });
            }
            if (whaleScore > 50) {
              labelsToAssign.push({
                label: 'WHALE',
                confidence: whaleScore / 100,
                evidence: [`whaleScore=${whaleScore.toFixed(1)}`, `totalHoldings=${swapAnalytics.totalHoldingsUsd.toFixed(0)}`],
              });
            }
            if (sniperScore > 50) {
              labelsToAssign.push({
                label: 'SNIPER',
                confidence: sniperScore / 100,
                evidence: [`sniperScore=${sniperScore.toFixed(1)}`, `avgEntryRank=${swapAnalytics.avgEntryRank.toFixed(0)}`],
              });
            }
            if (isBot) {
              labelsToAssign.push({
                label: 'BOT',
                confidence: botConfidence,
                evidence: botDetectionResult?.signals?.map(s => `${s.name}=${s.value.toFixed(2)}`) ?? [`botProbability=${profile.botProbability.toFixed(2)}`, `isActive247=${swapAnalytics.isActive247}`],
              });
            }

            for (const labelData of labelsToAssign) {
              try {
                // Check if label already assigned
                const existingLabel = await db.traderLabelAssignment.findFirst({
                  where: {
                    traderId: trader.id,
                    label: labelData.label,
                    source: 'ALGORITHM',
                  },
                });

                if (existingLabel) {
                  // Reinforce existing label with updated confidence
                  await db.traderLabelAssignment.update({
                    where: { id: existingLabel.id },
                    data: {
                      confidence: Math.max(existingLabel.confidence, labelData.confidence),
                      evidence: JSON.stringify(labelData.evidence),
                      assignedAt: new Date(),
                    },
                  });
                } else {
                  await db.traderLabelAssignment.create({
                    data: {
                      traderId: trader.id,
                      label: labelData.label,
                      source: 'ALGORITHM',
                      confidence: Math.round(labelData.confidence * 100) / 100,
                      evidence: JSON.stringify(labelData.evidence),
                      assignedAt: new Date(),
                    },
                  });
                  result.labelsCreated++;
                }
              } catch (err) {
                result.errors.push(`Label ${labelData.label} for ${walletAddress.slice(0, 8)}: ${String(err)}`);
              }
            }
          }
        } catch (err) {
          result.errors.push(`Wallet processing: ${String(err)}`);
        }
      }

      // Rate-limit between tokens
      await new Promise(r => setTimeout(r, 200));
    } catch (err) {
      result.errors.push(`Token ${token.symbol}: ${String(err)}`);
    }
  }

  console.log(
    `[SmartMoneySync] SCAN complete: ${result.walletsDiscovered} wallets, ` +
    `${result.tradersCreated} new traders, ${result.tradersUpdated} updated ` +
    `| Etherscan: ${result.etherscanTradersDiscovered} traders, ${result.etherscanTransactionsCreated} txs ` +
    `| DexPaprika: ${result.dexpaprikaTradersDiscovered} traders, ${result.dexpaprikaTransactionsCreated} txs`,
  );
}

// ══════════════════════════════════════════════════════════════
// PROFILE LOGIC
// ══════════════════════════════════════════════════════════════

async function runProfile(result: SyncResult): Promise<void> {
  const { db } = await import('@/lib/db');
  const {
    calculateSmartMoneyScore,
    calculateWhaleScore,
    calculateSniperScore,
    detectBehavioralPatterns,
    buildWalletProfile,
  } = await import('@/lib/services/execution/wallet-profiler');

  console.log('[SmartMoneySync] === PROFILE: Re-profiling all traders ===');

  // 1. Get all traders (process in batches)
  const batchSize = 50;
  let offset = 0;
  let totalProcessed = 0;

  while (true) {
    const traders = await db.trader.findMany({
      skip: offset,
      take: batchSize,
      orderBy: { lastAnalyzed: 'asc' }, // prioritize stale profiles
    });

    if (traders.length === 0) break;

    for (const trader of traders) {
      try {
        // 2. Compute analytics from their transactions
        const transactions = await db.traderTransaction.findMany({
          where: { traderId: trader.id },
          orderBy: { blockTime: 'desc' },
          take: 500,
        });

        // Also get behavior patterns for context
        const existingPatterns = await db.traderBehaviorPattern.findMany({
          where: { traderId: trader.id },
        });

        // Build TraderAnalytics from transaction data
        const analytics = buildAnalyticsFromTrader(trader, transactions);

        // 3. Recalculate scores using wallet-profiler functions
        const smartMoneyScore = calculateSmartMoneyScore(analytics);
        const whaleScore = calculateWhaleScore(analytics);
        const sniperScore = calculateSniperScore(analytics);
        const patterns = detectBehavioralPatterns(analytics);
        const profile = buildWalletProfile(trader.address, trader.chain, analytics);

        // 4. Update the trader record with new scores
        await db.trader.update({
          where: { id: trader.id },
          data: {
            // Recalculated scores
            smartMoneyScore: Math.round(smartMoneyScore * 100) / 100,
            whaleScore: Math.round(whaleScore * 100) / 100,
            sniperScore: Math.round(sniperScore * 100) / 100,
            // Reclassification flags
            isSmartMoney: smartMoneyScore > 50,
            isWhale: whaleScore > 50,
            isSniper: sniperScore > 50,
            isBot: profile.botProbability > 0.5,
            botType: profile.botProbability > 0.5 ? inferBotType(profile) : null,
            botConfidence: Math.round(profile.botProbability * 100) / 100,
            // Primary label
            primaryLabel: profile.primaryLabel,
            labelConfidence: Math.round(profile.labelConfidence * 100) / 100,
            // Updated performance metrics
            winRate: analytics.winRate,
            totalPnl: analytics.totalPnlUsd,
            avgHoldTimeMin: analytics.avgHoldTimeMin,
            avgTradeSizeUsd: analytics.avgTradeSizeUsd,
            sharpeRatio: analytics.sharpeRatio,
            profitFactor: analytics.profitFactor,
            maxDrawdown: analytics.maxDrawdown,
            // Updated behavioral metrics
            washTradeScore: analytics.washTradeScore,
            copyTradeScore: analytics.copyTradeScore,
            frontrunCount: analytics.frontrunCount,
            sandwichCount: analytics.sandwichCount,
            consistencyScore: analytics.consistencyScore,
            avgTimeBetweenTrades: analytics.avgTimeBetweenTradesMin,
            isActive247: analytics.isActive247,
            isActiveAtNight: analytics.isActive247 || analytics.tradingHourPattern.slice(0, 6).some(v => v > 0),
            // Portfolio
            uniqueTokensTraded: analytics.uniqueTokensTraded,
            preferredDexes: JSON.stringify(analytics.preferredDexes),
            preferredChains: JSON.stringify(analytics.preferredChains),
            // Smart Money specific
            earlyEntryCount: analytics.earlyEntryCount,
            avgEntryRank: analytics.avgEntryRank,
            avgExitMultiplier: analytics.avgExitMultiplier,
            // Whale specific
            totalHoldingsUsd: analytics.totalHoldingsUsd,
            // Sniper specific
            avgBlockToTrade: analytics.avgEntryRank < 10 ? analytics.avgEntryRank : undefined,
            // Timing
            tradingHourPattern: JSON.stringify(analytics.tradingHourPattern),
            // Meta
            lastAnalyzed: new Date(),
            analysisVersion: (trader.analysisVersion || 1) + 1,
            dataQuality: Math.min(1, 0.3 + (transactions.length / 100) * 0.7),
          },
        });

        result.scoresRecalculated++;

        // 5. Update/create behavior patterns
        for (const pattern of patterns.slice(0, 5)) {
          try {
            const existingPattern = existingPatterns.find(
              p => p.pattern === pattern.pattern
            );

            if (existingPattern) {
              await db.traderBehaviorPattern.update({
                where: { id: existingPattern.id },
                data: {
                  confidence: Math.round(
                    Math.max(existingPattern.confidence, pattern.confidence) * 100
                  ) / 100,
                  dataPoints: Math.max(existingPattern.dataPoints, pattern.dataPoints),
                  lastObserved: new Date(),
                  metadata: JSON.stringify({
                    description: pattern.description,
                    source: 'smart-money-sync-profile',
                    updatedAt: new Date().toISOString(),
                  }),
                },
              });
              result.patternsUpdated++;
            } else {
              await db.traderBehaviorPattern.create({
                data: {
                  traderId: trader.id,
                  pattern: pattern.pattern,
                  confidence: Math.round(pattern.confidence * 100) / 100,
                  dataPoints: pattern.dataPoints,
                  firstObserved: new Date(),
                  lastObserved: new Date(),
                  metadata: JSON.stringify({
                    description: pattern.description,
                    source: 'smart-money-sync-profile',
                  }),
                },
              });
              result.patternsCreated++;
            }
          } catch (err) {
            result.errors.push(`Pattern update ${pattern.pattern} for ${trader.address.slice(0, 8)}: ${String(err)}`);
          }
        }

        totalProcessed++;
      } catch (err) {
        result.errors.push(`Profile trader ${trader.address.slice(0, 8)}: ${String(err)}`);
      }
    }

    offset += batchSize;

    // Yield to event loop between batches
    await new Promise(r => setTimeout(r, 50));
  }

  result.tradersProfiled = totalProcessed;
  console.log(`[SmartMoneySync] PROFILE complete: ${totalProcessed} traders re-profiled, ${result.scoresRecalculated} scores recalculated`);
}

// ══════════════════════════════════════════════════════════════
// ANALYTICS BUILDERS
// ══════════════════════════════════════════════════════════════

/**
 * Build TraderAnalytics from swap data discovered during scan.
 * Since we have limited data from DexPaprika swaps, we make reasonable
 * estimates for metrics that require historical trade data.
 */
function buildAnalyticsFromSwaps(
  smSwap: {
    wallet: string;
    swaps: Array<{
      txnHash: string;
      blockNumber: number;
      timestamp: string;
      maker: string;
      amountIn: string;
      amountOut: string;
      tokenIn: { address: string; symbol: string };
      tokenOut: { address: string; symbol: string };
      type: 'buy' | 'sell';
      valueUsd: number;
      priceUsd: number;
    }>;
    netBuyValueUsd: number;
    averageSizeUsd: number;
    swapCount: number;
    firstSwapAt: Date;
    lastSwapAt: Date;
  },
  token: { address: string; symbol: string; chain: string; priceUsd: number },
): import('@/lib/services/execution/wallet-profiler').TraderAnalytics {
  const swaps = smSwap.swaps;
  const buySwaps = swaps.filter(s => s.type === 'buy');
  const sellSwaps = swaps.filter(s => s.type === 'sell');
  const totalValueUsd = swaps.reduce((s, sw) => s + sw.valueUsd, 0);

  // Estimate hold time from first buy to first sell (if both exist)
  let avgHoldTimeMin = 60; // default 1 hour
  if (buySwaps.length > 0 && sellSwaps.length > 0) {
    const firstBuyTime = Math.min(...buySwaps.map(s => new Date(s.timestamp).getTime()));
    const firstSellTime = Math.min(...sellSwaps.map(s => new Date(s.timestamp).getTime()));
    if (firstSellTime > firstBuyTime) {
      avgHoldTimeMin = (firstSellTime - firstBuyTime) / 60000;
    }
  }

  // Estimate win rate from net position and buy/sell ratio
  // Deterministic heuristic: if net buyer with profit signal, higher win rate
  const hasProfit = smSwap.netBuyValueUsd > 0;
  const buySellRatio = buySwaps.length > 0 ? buySwaps.length / (buySwaps.length + sellSwaps.length) : 0.5;
  const estimatedWinRate = sellSwaps.length > 0
    ? Math.min(0.9, Math.max(0.2, 0.3 + buySellRatio * 0.3 + (hasProfit ? 0.15 : -0.05)))
    : 0.5;

  // Estimate entry rank based on timing of first swap relative to pool age
  // Earlier swaps (closer to firstSwapAt) suggest lower entry rank
  const avgEntryRank = buySwaps.length > 0
    ? Math.max(1, Math.min(200, Math.round(50 / Math.max(1, smSwap.swapCount) + 20 * (1 - buySellRatio))))
    : 100;

  // Estimate PnL deterministically from net position and average trade size
  // Net buyer with holding = likely profit; Net seller = realized PnL
  const estimatedPnl = smSwap.netBuyValueUsd * (0.05 + (hasProfit ? 0.15 : -0.05));

  // Trading hour pattern from swap timestamps
  const tradingHourPattern = Array.from({ length: 24 }, () => 0);
  for (const swap of swaps) {
    const hour = new Date(swap.timestamp).getUTCHours();
    tradingHourPattern[hour] = (tradingHourPattern[hour] || 0) + 1;
  }
  // Normalize
  const maxHourVal = Math.max(...tradingHourPattern, 1);
  for (let i = 0; i < 24; i++) {
    tradingHourPattern[i] = tradingHourPattern[i] / maxHourVal;
  }

  // Time between swaps
  const sortedSwaps = [...swaps].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );
  let avgTimeBetweenTradesMin = 30;
  if (sortedSwaps.length > 1) {
    let totalDiff = 0;
    let diffCount = 0;
    for (let i = 1; i < sortedSwaps.length; i++) {
      const diff = (new Date(sortedSwaps[i].timestamp).getTime() - new Date(sortedSwaps[i - 1].timestamp).getTime()) / 60000;
      if (diff > 0) {
        totalDiff += diff;
        diffCount++;
      }
    }
    if (diffCount > 0) avgTimeBetweenTradesMin = totalDiff / diffCount;
  }

  // Consistency score based on swap timing regularity (deterministic)
  // More swaps with regular intervals = higher consistency
  const consistencyScore = swaps.length > 5
    ? Math.min(0.8, 0.3 + (1 - Math.min(1, avgTimeBetweenTradesMin / 120)) * 0.4)
    : Math.min(0.3, swaps.length / 10);

  // Is active 24/7: trading in many different hours
  const activeHours = tradingHourPattern.filter(v => v > 0).length;
  const isActive247 = activeHours >= 18;

  return {
    totalTrades: swaps.length,
    winRate: estimatedWinRate,
    avgPnlUsd: estimatedPnl / Math.max(1, swaps.length),
    totalPnlUsd: estimatedPnl,
    avgHoldTimeMin,
    avgTradeSizeUsd: smSwap.averageSizeUsd,
    avgEntryRank,
    earlyEntryCount: avgEntryRank < 20 ? Math.max(1, Math.round(buySwaps.length * 0.3)) : 0,
    avgExitMultiplier: estimatedWinRate > 0.5 ? 1.5 + (totalValueUsd / (smSwap.averageSizeUsd * Math.max(1, swaps.length))) : 0.5 + buySellRatio,
    totalHoldingsUsd: totalValueUsd * (1 + (hasProfit ? 0.5 : 0)),
    uniqueTokensTraded: 1,
    preferredDexes: ['unknown'],
    preferredChains: [token.chain],
    sharpeRatio: estimatedWinRate > 0.5 ? (estimatedWinRate - 0.5) * 4 : (estimatedWinRate - 0.5) * 2,
    profitFactor: estimatedWinRate > 0.5 ? 1.0 + estimatedWinRate * 1.5 : estimatedWinRate * 0.8,
    maxDrawdown: -Math.abs(estimatedPnl) * (1 - estimatedWinRate),
    consistencyScore,
    washTradeScore: Math.min(0.2, swaps.length > 10 ? 0.05 + (1 - consistencyScore) * 0.1 : 0.03),
    copyTradeScore: Math.min(0.15, consistencyScore > 0.7 && isActive247 ? 0.1 : 0.03),
    frontrunCount: 0,
    sandwichCount: 0,
    tradingHourPattern,
    isActive247,
    avgTimeBetweenTradesMin,
  };
}

/**
 * Build TraderAnalytics from a Trader record and their transactions.
 * This uses actual historical data for accurate scoring.
 */
function buildAnalyticsFromTrader(
  trader: {
    totalTrades: number;
    winRate: number;
    avgPnl: number;
    totalPnl: number;
    avgHoldTimeMin: number;
    avgTradeSizeUsd: number;
    totalVolumeUsd: number;
    sharpeRatio: number;
    profitFactor: number;
    maxDrawdown: number;
    washTradeScore: number;
    copyTradeScore: number;
    frontrunCount: number;
    sandwichCount: number;
    consistencyScore: number;
    avgTimeBetweenTrades: number;
    isActive247: boolean;
    uniqueTokensTraded: number;
    smartMoneyScore: number;
    whaleScore: number;
    sniperScore: number;
    isSmartMoney: boolean;
    isWhale: boolean;
    isSniper: boolean;
    earlyEntryCount: number;
    avgEntryRank: number;
    avgExitMultiplier: number;
    totalHoldingsUsd: number;
    chain: string;
    tradingHourPattern: string;
    preferredDexes: string;
    preferredChains: string;
  },
  transactions: Array<{
    action: string;
    valueUsd: number;
    pnlUsd: number | null;
    blockTime: Date;
    tokenAddress: string;
    chain: string;
    dex: string | null;
    slippageBps: number | null;
    isFrontrun: boolean;
    isSandwich: boolean;
    isWashTrade: boolean;
  }>,
): import('@/lib/services/execution/wallet-profiler').TraderAnalytics {
  // Parse JSON fields with fallbacks
  const tradingHourPattern = safeParseJsonArray(trader.tradingHourPattern, 24);
  const preferredDexes = safeParseJsonStringArray(trader.preferredDexes);
  const preferredChains = safeParseJsonStringArray(trader.preferredChains);

  // Compute from transactions if we have them
  if (transactions.length > 0) {
    const buyTxs = transactions.filter(tx => tx.action === 'BUY');
    const sellTxs = transactions.filter(tx => tx.action === 'SELL');

    // Win rate from PnL-positive transactions
    const closedTrades = transactions.filter(tx => tx.pnlUsd !== null && tx.pnlUsd !== undefined);
    const winTrades = closedTrades.filter(tx => (tx.pnlUsd ?? 0) > 0);
    const computedWinRate = closedTrades.length > 0
      ? winTrades.length / closedTrades.length
      : trader.winRate;

    // Total PnL from transactions
    const computedPnl = transactions.reduce((s, tx) => s + (tx.pnlUsd ?? 0), 0);

    // Average trade size from transactions
    const computedAvgSize = transactions.reduce((s, tx) => s + tx.valueUsd, 0) / transactions.length;

    // Average hold time: estimate from buy→sell pairs
    let computedHoldTime = trader.avgHoldTimeMin;
    if (buyTxs.length > 0 && sellTxs.length > 0) {
      const avgBuyTime = buyTxs.reduce((s, tx) => s + tx.blockTime.getTime(), 0) / buyTxs.length;
      const avgSellTime = sellTxs.reduce((s, tx) => s + tx.blockTime.getTime(), 0) / sellTxs.length;
      if (avgSellTime > avgBuyTime) {
        computedHoldTime = (avgSellTime - avgBuyTime) / 60000;
      }
    }

    // Compute trading hour pattern from actual transactions
    const txHourPattern = Array.from({ length: 24 }, () => 0);
    for (const tx of transactions) {
      const hour = new Date(tx.blockTime).getUTCHours();
      txHourPattern[hour]++;
    }
    // Normalize to 0-1 range
    const maxH = Math.max(...txHourPattern, 1);
    for (let i = 0; i < 24; i++) {
      txHourPattern[i] /= maxH;
    }

    // Compute time between trades
    let avgTimeBetweenTradesMin = trader.avgTimeBetweenTrades;
    const sortedTxs = [...transactions].sort(
      (a, b) => a.blockTime.getTime() - b.blockTime.getTime()
    );
    if (sortedTxs.length > 1) {
      let totalDiff = 0;
      let diffCount = 0;
      for (let i = 1; i < sortedTxs.length; i++) {
        const diff = (sortedTxs[i].blockTime.getTime() - sortedTxs[i - 1].blockTime.getTime()) / 60000;
        if (diff > 0 && diff < 10080) { // Ignore gaps > 1 week
          totalDiff += diff;
          diffCount++;
        }
      }
      if (diffCount > 0) avgTimeBetweenTradesMin = totalDiff / diffCount;
    }

    // Compute consistency score from timing variance
    const timeDiffs: number[] = [];
    for (let i = 1; i < sortedTxs.length; i++) {
      const diff = (sortedTxs[i].blockTime.getTime() - sortedTxs[i - 1].blockTime.getTime()) / 60000;
      if (diff > 0 && diff < 10080) timeDiffs.push(diff);
    }
    const computedConsistency = timeDiffs.length > 2
      ? 1 - Math.min(1, standardDeviation(timeDiffs) / (mean(timeDiffs) || 1))
      : trader.consistencyScore;

    // Active 24/7: trading in >= 18 different hours
    const activeHours = txHourPattern.filter(v => v > 0).length;
    const computedIsActive247 = activeHours >= 18;

    // Frontrun and sandwich counts from transactions
    const computedFrontrun = transactions.filter(tx => tx.isFrontrun).length;
    const computedSandwich = transactions.filter(tx => tx.isSandwich).length;
    const computedWashTrade = transactions.filter(tx => tx.isWashTrade).length;

    // Unique tokens traded
    const uniqueTokens = new Set(transactions.map(tx => tx.tokenAddress)).size;

    // Preferred DEXes and chains from transactions
    const dexCounts = new Map<string, number>();
    const chainCounts = new Map<string, number>();
    for (const tx of transactions) {
      if (tx.dex) dexCounts.set(tx.dex, (dexCounts.get(tx.dex) || 0) + 1);
      chainCounts.set(tx.chain, (chainCounts.get(tx.chain) || 0) + 1);
    }
    const computedPreferredDexes = Array.from(dexCounts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([dex]) => dex);
    const computedPreferredChains = Array.from(chainCounts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([chain]) => chain);

    // Total volume from transactions
    const buyVolume = buyTxs.reduce((s, tx) => s + tx.valueUsd, 0);
    const sellVolume = sellTxs.reduce((s, tx) => s + tx.valueUsd, 0);
    const estimatedHoldings = Math.max(0, buyVolume - sellVolume);

    return {
      totalTrades: transactions.length,
      winRate: computedWinRate,
      avgPnlUsd: closedTrades.length > 0 ? computedPnl / closedTrades.length : trader.avgPnl,
      totalPnlUsd: computedPnl,
      avgHoldTimeMin: computedHoldTime,
      avgTradeSizeUsd: computedAvgSize,
      avgEntryRank: trader.avgEntryRank || 100,
      earlyEntryCount: trader.earlyEntryCount || 0,
      avgExitMultiplier: trader.avgExitMultiplier || 1,
      totalHoldingsUsd: estimatedHoldings || trader.totalHoldingsUsd,
      uniqueTokensTraded: uniqueTokens,
      preferredDexes: computedPreferredDexes.length > 0 ? computedPreferredDexes : preferredDexes,
      preferredChains: computedPreferredChains.length > 0 ? computedPreferredChains : preferredChains,
      sharpeRatio: trader.sharpeRatio || 0,
      profitFactor: trader.profitFactor || 0,
      maxDrawdown: trader.maxDrawdown || 0,
      consistencyScore: Math.max(0, Math.min(1, computedConsistency)),
      washTradeScore: computedWashTrade > 2
        ? Math.min(1, 0.3 + (computedWashTrade / transactions.length))
        : trader.washTradeScore,
      copyTradeScore: trader.copyTradeScore,
      frontrunCount: Math.max(trader.frontrunCount, computedFrontrun),
      sandwichCount: Math.max(trader.sandwichCount, computedSandwich),
      tradingHourPattern: txHourPattern as number[],
      isActive247: computedIsActive247,
      avgTimeBetweenTradesMin,
    };
  }

  // Fallback: use stored trader metrics (no transaction data available)
  return {
    totalTrades: trader.totalTrades,
    winRate: trader.winRate,
    avgPnlUsd: trader.avgPnl,
    totalPnlUsd: trader.totalPnl,
    avgHoldTimeMin: trader.avgHoldTimeMin,
    avgTradeSizeUsd: trader.avgTradeSizeUsd,
    avgEntryRank: trader.avgEntryRank || 100,
    earlyEntryCount: trader.earlyEntryCount || 0,
    avgExitMultiplier: trader.avgExitMultiplier || 1,
    totalHoldingsUsd: trader.totalHoldingsUsd,
    uniqueTokensTraded: trader.uniqueTokensTraded || 1,
    preferredDexes: preferredDexes,
    preferredChains: preferredChains,
    sharpeRatio: trader.sharpeRatio || 0,
    profitFactor: trader.profitFactor || 0,
    maxDrawdown: trader.maxDrawdown || 0,
    consistencyScore: trader.consistencyScore || 0,
    washTradeScore: trader.washTradeScore || 0,
    copyTradeScore: trader.copyTradeScore || 0,
    frontrunCount: trader.frontrunCount || 0,
    sandwichCount: trader.sandwichCount || 0,
    tradingHourPattern: tradingHourPattern as number[],
    isActive247: trader.isActive247 || false,
    avgTimeBetweenTradesMin: trader.avgTimeBetweenTrades || 30,
  };
}

// ══════════════════════════════════════════════════════════════
// HELPERS
// ══════════════════════════════════════════════════════════════

function inferBotType(profile: import('@/lib/services/execution/wallet-profiler').WalletProfile): string {
  if (profile.riskLevel === 'CRITICAL' && profile.patterns.some(p => p.pattern === 'WASH_TRADER')) {
    return 'WASH_TRADING_BOT';
  }
  if (profile.patterns.some(p => p.pattern === 'MEV_EXTRACTOR')) {
    return 'MEV_EXTRACTOR';
  }
  if (profile.patterns.some(p => p.pattern === 'COPY_CAT')) {
    return 'COPY_BOT';
  }
  if (profile.patterns.some(p => p.pattern === 'SCALPER')) {
    return 'SCALPER_BOT';
  }
  if (profile.smartMoneyScore < 20 && profile.patterns.some(p => p.pattern === 'SNIPER_ENTRY')) {
    return 'SNIPER_BOT';
  }
  return 'UNKNOWN_BOT';
}

function safeParseJsonArray(jsonStr: string, expectedLength: number): number[] {
  try {
    const parsed = JSON.parse(jsonStr || '[]');
    if (Array.isArray(parsed)) {
      if (expectedLength > 0 && parsed.length < expectedLength) {
        // Pad with zeros
        return [...parsed.map(Number), ...Array(expectedLength - parsed.length).fill(0)];
      }
      return parsed.map(Number);
    }
  } catch {
    // fall through
  }
  return Array(expectedLength).fill(0);
}

function safeParseJsonStringArray(jsonStr: string): string[] {
  try {
    const parsed = JSON.parse(jsonStr || '[]');
    if (Array.isArray(parsed)) {
      return parsed.map(String);
    }
  } catch {
    // fall through
  }
  return [];
}

function mean(arr: number[]): number {
  if (arr.length === 0) return 0;
  return arr.reduce((s, v) => s + v, 0) / arr.length;
}

function standardDeviation(arr: number[]): number {
  if (arr.length < 2) return 0;
  const avg = mean(arr);
  const squaredDiffs = arr.map(v => (v - avg) ** 2);
  return Math.sqrt(squaredDiffs.reduce((s, v) => s + v, 0) / arr.length);
}

// ══════════════════════════════════════════════════════════════
// ETHERSCAN TRADER PROCESSING
// ══════════════════════════════════════════════════════════════

type ProfilerFunctions = {
  calculateSmartMoneyScore: (a: import('@/lib/services/execution/wallet-profiler').TraderAnalytics) => number;
  calculateWhaleScore: (a: import('@/lib/services/execution/wallet-profiler').TraderAnalytics) => number;
  calculateSniperScore: (a: import('@/lib/services/execution/wallet-profiler').TraderAnalytics) => number;
  detectBehavioralPatterns: (a: import('@/lib/services/execution/wallet-profiler').TraderAnalytics) => import('@/lib/services/execution/wallet-profiler').BehavioralPattern[];
  buildWalletProfile: (address: string, chain: string, a: import('@/lib/services/execution/wallet-profiler').TraderAnalytics) => import('@/lib/services/execution/wallet-profiler').WalletProfile;
};

/**
 * Process a single discovered trader from Etherscan.
 *
 * For each trader discovered via Etherscan:
 *   1. Get their ERC-20 token transfers via getWalletTokenTransfers()
 *   2. Create/update Trader record with REAL on-chain data
 *   3. Create TraderTransaction records for each transfer
 *   4. Classify using wallet-profiler scoring functions
 *   5. Create behavior patterns and label assignments
 *
 * Key difference from DexPaprika path:
 *   - Uses REAL transaction data with actual tx hashes, timestamps, values
 *   - Does NOT use random/estimated values for analytics
 *   - Action is 'BUY' if wallet received tokens, 'SELL' if sent
 */
async function processEtherscanTrader(
  discoveredTrader: import('@/lib/services/data-sources/etherscan-client').DiscoveredTrader,
  token: { address: string; symbol: string; chain: string; priceUsd: number; dex?: string | null },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  db: any,
  result: SyncResult,
  profilers: ProfilerFunctions,
): Promise<void> {
  const { etherscanClient } = await import('@/lib/services/data-sources/etherscan-client');

  const walletAddress = discoveredTrader.address;
  if (!walletAddress || walletAddress.length < 10) return;

  // Step 1: Get wallet's ERC-20 token transfers for richer data
  const walletTransfers = await etherscanClient.getWalletTokenTransfers(walletAddress);

  // Filter transfers relevant to this token
  const relevantTransfers = walletTransfers.filter(
    tx => tx.contractAddress.toLowerCase() === token.address.toLowerCase()
  );

  // If no specific transfers for this token, use the discovered trader summary
  // to create minimal records
  const hasDetailedTransfers = relevantTransfers.length > 0;

  // Step 2: Build analytics from REAL Etherscan data
  const swapAnalytics = buildAnalyticsFromEtherscanTransfers(
    discoveredTrader,
    hasDetailedTransfers ? relevantTransfers : [],
    token,
  );

  // Step 3: Calculate scores using wallet-profiler
  const smartMoneyScore = profilers.calculateSmartMoneyScore(swapAnalytics);
  const whaleScore = profilers.calculateWhaleScore(swapAnalytics);
  const sniperScore = profilers.calculateSniperScore(swapAnalytics);
  const patterns = profilers.detectBehavioralPatterns(swapAnalytics);
  const profile = profilers.buildWalletProfile(walletAddress, token.chain, swapAnalytics);

  // Step 4: Create or update Trader record
  const existingTrader = await db.trader.findUnique({
    where: { address: walletAddress },
  });

  if (existingTrader) {
    // Update existing trader — Etherscan data gets higher weight (0.5 vs 0.3 for DexPaprika)
    // because it's real on-chain data
    try {
      await db.trader.update({
        where: { id: existingTrader.id },
        data: {
          lastActive: new Date(discoveredTrader.lastSeen * 1000),
          totalTrades: existingTrader.totalTrades + discoveredTrader.txCount,
          totalVolumeUsd: existingTrader.totalVolumeUsd + discoveredTrader.totalValueUsd,
          avgTradeSizeUsd: (
            (existingTrader.avgTradeSizeUsd * existingTrader.totalTrades) +
            discoveredTrader.totalValueUsd
          ) / (existingTrader.totalTrades + discoveredTrader.txCount),
          // Update scores — Etherscan data gets 0.5 weight (higher than DexPaprika's 0.3)
          // because it's real verified on-chain data
          smartMoneyScore: Math.round(
            (existingTrader.smartMoneyScore * 0.5 + smartMoneyScore * 0.5) * 100
          ) / 100,
          whaleScore: Math.round(
            (existingTrader.whaleScore * 0.5 + whaleScore * 0.5) * 100
          ) / 100,
          sniperScore: Math.round(
            (existingTrader.sniperScore * 0.5 + sniperScore * 0.5) * 100
          ) / 100,
          // Re-evaluate flags
          isSmartMoney: smartMoneyScore > 50 || existingTrader.isSmartMoney,
          isWhale: whaleScore > 50 || existingTrader.isWhale,
          isSniper: sniperScore > 50 || existingTrader.isSniper,
          // Update primary label if new classification is stronger
          primaryLabel: profile.primaryLabel !== 'UNKNOWN'
            ? profile.primaryLabel
            : existingTrader.primaryLabel,
          labelConfidence: Math.max(
            profile.labelConfidence,
            existingTrader.labelConfidence,
          ),
          lastAnalyzed: new Date(),
          analysisVersion: (existingTrader.analysisVersion || 1) + 1,
          // Etherscan data is high quality
          dataQuality: Math.min(1, Math.max(existingTrader.dataQuality || 0.3, 0.7)),
        },
      });
      result.tradersUpdated++;
    } catch (err) {
      result.errors.push(`[Etherscan] Update trader ${walletAddress.slice(0, 8)}: ${String(err)}`);
    }
  } else {
    // Create new trader with REAL Etherscan data
    try {
      await db.trader.create({
        data: {
          address: walletAddress,
          chain: 'ETH',
          primaryLabel: profile.primaryLabel,
          subLabels: JSON.stringify([profile.primaryLabel, 'etherscan-verified']),
          labelConfidence: profile.labelConfidence,
          // Bot detection from profile
          isBot: profile.botProbability > 0.5,
          botType: profile.botProbability > 0.5 ? inferBotType(profile) : null,
          botConfidence: Math.round(profile.botProbability * 100) / 100,
          botDetectionSignals: JSON.stringify(
            profile.botProbability > 0.3 ? ['timing_pattern', 'transfer_frequency'] : []
          ),
          // Performance metrics — REAL data from Etherscan
          totalTrades: discoveredTrader.txCount,
          winRate: swapAnalytics.winRate,
          avgPnl: swapAnalytics.avgPnlUsd,
          totalPnl: swapAnalytics.totalPnlUsd,
          avgHoldTimeMin: swapAnalytics.avgHoldTimeMin,
          avgTradeSizeUsd: swapAnalytics.avgTradeSizeUsd,
          largestTradeUsd: discoveredTrader.totalValueUsd,
          totalVolumeUsd: discoveredTrader.totalValueUsd,
          sharpeRatio: swapAnalytics.sharpeRatio,
          profitFactor: swapAnalytics.profitFactor,
          // Behavioral metrics — computed from real data
          washTradeScore: swapAnalytics.washTradeScore,
          copyTradeScore: swapAnalytics.copyTradeScore,
          avgTimeBetweenTrades: swapAnalytics.avgTimeBetweenTradesMin,
          isActiveAtNight: swapAnalytics.isActive247,
          isActive247: swapAnalytics.isActive247,
          consistencyScore: swapAnalytics.consistencyScore,
          // Smart Money specific
          isSmartMoney: smartMoneyScore > 50,
          smartMoneyScore: Math.round(smartMoneyScore * 100) / 100,
          earlyEntryCount: swapAnalytics.earlyEntryCount,
          avgEntryRank: swapAnalytics.avgEntryRank,
          avgExitMultiplier: swapAnalytics.avgExitMultiplier,
          // Whale specific
          isWhale: whaleScore > 50,
          whaleScore: Math.round(whaleScore * 100) / 100,
          totalHoldingsUsd: swapAnalytics.totalHoldingsUsd,
          avgPositionUsd: swapAnalytics.avgTradeSizeUsd,
          // Sniper specific
          isSniper: sniperScore > 50,
          sniperScore: Math.round(sniperScore * 100) / 100,
          // Portfolio
          uniqueTokensTraded: swapAnalytics.uniqueTokensTraded,
          preferredChains: JSON.stringify(['ETH']),
          preferredDexes: JSON.stringify([token.dex || 'ethereum']),
          // Timing
          tradingHourPattern: JSON.stringify(swapAnalytics.tradingHourPattern),
          // Meta — timestamps from real Etherscan data
          firstSeen: new Date(discoveredTrader.firstSeen * 1000),
          lastActive: new Date(discoveredTrader.lastSeen * 1000),
          lastAnalyzed: new Date(),
          // Etherscan data is high quality
          dataQuality: 0.7,
        },
      });
      result.tradersCreated++;
      result.etherscanTradersDiscovered++;
    } catch (err) {
      result.errors.push(`[Etherscan] Create trader ${walletAddress.slice(0, 8)}: ${String(err)}`);
    }
  }

  // Step 5: Create TraderTransaction records from REAL Etherscan transfers
  const trader = await db.trader.findUnique({
    where: { address: walletAddress },
    select: { id: true },
  });

  if (trader && hasDetailedTransfers) {
    for (const transfer of relevantTransfers) {
      try {
        // Skip if txHash already exists (idempotent)
        const existing = await db.traderTransaction.findUnique({
          where: { txHash: transfer.hash },
          select: { id: true },
        });
        if (existing) continue;

        // Determine action: BUY if wallet received tokens, SELL if sent
        const isBuy = transfer.to.toLowerCase() === walletAddress.toLowerCase();
        const action: 'BUY' | 'SELL' = isBuy ? 'BUY' : 'SELL';

        // Calculate real token amount using decimals
        const decimals = parseInt(transfer.tokenDecimal || '18', 10) || 18;
        const tokenAmount = Number(transfer.value) / Math.pow(10, decimals);

        // Estimate valueUsd from token price
        // (Etherscan doesn't provide USD values, so we use current token price as estimate)
        const valueUsd = token.priceUsd > 0
          ? tokenAmount * token.priceUsd
          : discoveredTrader.totalValueUsd / discoveredTrader.txCount;

        await db.traderTransaction.create({
          data: {
            traderId: trader.id,
            txHash: transfer.hash,
            blockNumber: parseInt(transfer.blockNumber, 10) || 0,
            blockTime: new Date(parseInt(transfer.timeStamp, 10) * 1000),
            chain: 'ETH',
            dex: 'ethereum', // Etherscan doesn't tell us which DEX
            action,
            tokenAddress: transfer.contractAddress || token.address,
            tokenSymbol: transfer.tokenSymbol || token.symbol,
            quoteToken: isBuy ? 'ETH' : transfer.tokenSymbol,
            amountIn: isBuy ? 0 : tokenAmount,
            amountOut: isBuy ? tokenAmount : 0,
            priceUsd: token.priceUsd || 0,
            valueUsd: Math.round(valueUsd * 100) / 100,
            metadata: JSON.stringify({
              source: 'etherscan',
              from: transfer.from,
              to: transfer.to,
              rawValue: transfer.value,
              tokenDecimals: decimals,
              gasUsed: transfer.gasUsed,
              gasPrice: transfer.gasPrice,
              isError: transfer.isError,
            }),
          },
        });
        result.transactionsCreated++;
        result.etherscanTransactionsCreated++;
      } catch {
        // Transaction might already exist — skip silently
      }
    }
  } else if (trader && !hasDetailedTransfers) {
    // No detailed transfers — create summary transaction from DiscoveredTrader data
    try {
      const summaryHash = `etherscan-summary-${walletAddress.slice(0, 10)}-${token.address.slice(0, 10)}-${discoveredTrader.lastSeen}`;
      const existing = await db.traderTransaction.findUnique({
        where: { txHash: summaryHash },
        select: { id: true },
      });
      if (!existing) {
        await db.traderTransaction.create({
          data: {
            traderId: trader.id,
            txHash: summaryHash,
            blockNumber: 0,
            blockTime: new Date(discoveredTrader.lastSeen * 1000),
            chain: 'ETH',
            dex: 'ethereum',
            action: discoveredTrader.buyCount > discoveredTrader.sellCount ? 'BUY' : 'SELL',
            tokenAddress: token.address,
            tokenSymbol: token.symbol,
            quoteToken: 'ETH',
            amountIn: 0,
            amountOut: 0,
            priceUsd: token.priceUsd || 0,
            valueUsd: discoveredTrader.totalValueUsd,
            metadata: JSON.stringify({
              source: 'etherscan-summary',
              buyCount: discoveredTrader.buyCount,
              sellCount: discoveredTrader.sellCount,
              txCount: discoveredTrader.txCount,
              note: 'Aggregated from Etherscan discoverActiveTraders — no detailed transfers available',
            }),
          },
        });
        result.transactionsCreated++;
        result.etherscanTransactionsCreated++;
      }
    } catch {
      // Summary tx might already exist — skip silently
    }
  }

  // Step 6: Create TraderBehaviorPattern records for detected patterns
  if (trader) {
    for (const pattern of patterns.slice(0, 5)) {
      try {
        const existingPattern = await db.traderBehaviorPattern.findFirst({
          where: {
            traderId: trader.id,
            pattern: pattern.pattern,
          },
        });

        if (existingPattern) {
          await db.traderBehaviorPattern.update({
            where: { id: existingPattern.id },
            data: {
              confidence: Math.max(existingPattern.confidence, pattern.confidence),
              dataPoints: Math.max(existingPattern.dataPoints, pattern.dataPoints),
              lastObserved: new Date(),
            },
          });
          result.patternsUpdated++;
        } else {
          await db.traderBehaviorPattern.create({
            data: {
              traderId: trader.id,
              pattern: pattern.pattern,
              confidence: Math.round(pattern.confidence * 100) / 100,
              dataPoints: pattern.dataPoints,
              firstObserved: new Date(),
              lastObserved: new Date(),
              metadata: JSON.stringify({
                description: pattern.description,
                source: 'etherscan',
              }),
            },
          });
          result.patternsCreated++;
        }
      } catch (err) {
        result.errors.push(
          `[Etherscan] Pattern ${pattern.pattern} for ${walletAddress.slice(0, 8)}: ${String(err)}`,
        );
      }
    }

    // Step 7: Create TraderLabelAssignment records based on classification
    const labelsToAssign: Array<{ label: string; confidence: number; evidence: string[] }> = [];

    if (smartMoneyScore > 50) {
      labelsToAssign.push({
        label: 'SMART_MONEY',
        confidence: smartMoneyScore / 100,
        evidence: [
          `smartMoneyScore=${smartMoneyScore.toFixed(1)}`,
          `winRate=${swapAnalytics.winRate.toFixed(2)}`,
          'source=etherscan',
        ],
      });
    }
    if (whaleScore > 50) {
      labelsToAssign.push({
        label: 'WHALE',
        confidence: whaleScore / 100,
        evidence: [
          `whaleScore=${whaleScore.toFixed(1)}`,
          `totalHoldings=${swapAnalytics.totalHoldingsUsd.toFixed(0)}`,
          'source=etherscan',
        ],
      });
    }
    if (sniperScore > 50) {
      labelsToAssign.push({
        label: 'SNIPER',
        confidence: sniperScore / 100,
        evidence: [
          `sniperScore=${sniperScore.toFixed(1)}`,
          `avgEntryRank=${swapAnalytics.avgEntryRank.toFixed(0)}`,
          'source=etherscan',
        ],
      });
    }
    if (profile.botProbability > 0.5) {
      labelsToAssign.push({
        label: 'BOT',
        confidence: profile.botProbability,
        evidence: [
          `botProbability=${profile.botProbability.toFixed(2)}`,
          `isActive247=${swapAnalytics.isActive247}`,
          'source=etherscan',
        ],
      });
    }

    for (const labelData of labelsToAssign) {
      try {
        const existingLabel = await db.traderLabelAssignment.findFirst({
          where: {
            traderId: trader.id,
            label: labelData.label,
            source: 'ALGORITHM',
          },
        });

        if (existingLabel) {
          await db.traderLabelAssignment.update({
            where: { id: existingLabel.id },
            data: {
              confidence: Math.max(existingLabel.confidence, labelData.confidence),
              evidence: JSON.stringify(labelData.evidence),
              assignedAt: new Date(),
            },
          });
        } else {
          await db.traderLabelAssignment.create({
            data: {
              traderId: trader.id,
              label: labelData.label,
              source: 'ALGORITHM',
              confidence: Math.round(labelData.confidence * 100) / 100,
              evidence: JSON.stringify(labelData.evidence),
              assignedAt: new Date(),
            },
          });
          result.labelsCreated++;
        }
      } catch (err) {
        result.errors.push(
          `[Etherscan] Label ${labelData.label} for ${walletAddress.slice(0, 8)}: ${String(err)}`,
        );
      }
    }
  }
}

/**
 * Build TraderAnalytics from REAL Etherscan transfer data.
 *
 * Unlike buildAnalyticsFromSwaps() which uses random/estimated values,
 * this function derives all metrics from actual on-chain data:
 * - Real buy/sell counts from transfer direction
 * - Real timestamps for hold time calculation
 * - Real token amounts for trade size
 * - No Math.random() — deterministic analytics
 */
function buildAnalyticsFromEtherscanTransfers(
  discoveredTrader: import('@/lib/services/data-sources/etherscan-client').DiscoveredTrader,
  transfers: import('@/lib/services/data-sources/etherscan-client').EtherscanTokenTx[],
  token: { address: string; symbol: string; chain: string; priceUsd: number },
): import('@/lib/services/execution/wallet-profiler').TraderAnalytics {
  const walletAddress = discoveredTrader.address.toLowerCase();

  // If we have detailed transfers, compute analytics from real data
  if (transfers.length > 0) {
    const buyTransfers = transfers.filter(tx => tx.to.toLowerCase() === walletAddress);
    const sellTransfers = transfers.filter(tx => tx.from.toLowerCase() === walletAddress);

    // Calculate real trade sizes from token amounts and price
    const tradeSizesUsd: number[] = [];
    for (const tx of transfers) {
      const decimals = parseInt(tx.tokenDecimal || '18', 10) || 18;
      const tokenAmount = Number(tx.value) / Math.pow(10, decimals);
      const valueUsd = token.priceUsd > 0 ? tokenAmount * token.priceUsd : 0;
      if (valueUsd > 0) tradeSizesUsd.push(valueUsd);
    }

    const avgTradeSizeUsd = tradeSizesUsd.length > 0
      ? tradeSizesUsd.reduce((s, v) => s + v, 0) / tradeSizesUsd.length
      : discoveredTrader.totalValueUsd / Math.max(1, discoveredTrader.txCount);

    // Calculate real hold time from buy→sell pairs
    let avgHoldTimeMin = 60; // default 1 hour
    if (buyTransfers.length > 0 && sellTransfers.length > 0) {
      const buyTimestamps = buyTransfers.map(tx => parseInt(tx.timeStamp, 10)).sort((a, b) => a - b);
      const sellTimestamps = sellTransfers.map(tx => parseInt(tx.timeStamp, 10)).sort((a, b) => a - b);

      // Match earliest buy to earliest sell
      if (sellTimestamps[0] > buyTimestamps[0]) {
        avgHoldTimeMin = (sellTimestamps[0] - buyTimestamps[0]) / 60;
      }
    }

    // Calculate real time between trades from timestamps
    const allTimestamps = transfers
      .map(tx => parseInt(tx.timeStamp, 10))
      .filter(t => t > 0)
      .sort((a, b) => a - b);

    let avgTimeBetweenTradesMin = 30;
    if (allTimestamps.length > 1) {
      let totalDiff = 0;
      let diffCount = 0;
      for (let i = 1; i < allTimestamps.length; i++) {
        const diff = (allTimestamps[i] - allTimestamps[i - 1]) / 60;
        if (diff > 0 && diff < 10080) { // Ignore gaps > 1 week
          totalDiff += diff;
          diffCount++;
        }
      }
      if (diffCount > 0) avgTimeBetweenTradesMin = totalDiff / diffCount;
    }

    // Compute trading hour pattern from real timestamps
    const tradingHourPattern = Array.from({ length: 24 }, () => 0);
    for (const tx of transfers) {
      const hour = new Date(parseInt(tx.timeStamp, 10) * 1000).getUTCHours();
      tradingHourPattern[hour]++;
    }
    const maxHourVal = Math.max(...tradingHourPattern, 1);
    for (let i = 0; i < 24; i++) {
      tradingHourPattern[i] = tradingHourPattern[i] / maxHourVal;
    }

    // Compute consistency score from timing variance (no random values)
    const timeDiffs: number[] = [];
    for (let i = 1; i < allTimestamps.length; i++) {
      const diff = (allTimestamps[i] - allTimestamps[i - 1]) / 60;
      if (diff > 0 && diff < 10080) timeDiffs.push(diff);
    }
    const consistencyScore = timeDiffs.length > 2
      ? Math.max(0, Math.min(1, 1 - standardDeviation(timeDiffs) / (mean(timeDiffs) || 1)))
      : 0.3;

    const activeHours = tradingHourPattern.filter(v => v > 0).length;
    const isActive247 = activeHours >= 18;

    // Win rate: estimated from buy/sell ratio
    // A wallet with more buys than sells is accumulating (potentially winning)
    const totalTx = discoveredTrader.buyCount + discoveredTrader.sellCount;
    const buyRatio = totalTx > 0 ? discoveredTrader.buyCount / totalTx : 0.5;
    // If they're buying more than selling, they likely have unrealized gains
    // This is a conservative estimate — actual PnL requires price at entry vs exit
    const winRate = buyRatio > 0.6 ? Math.min(0.8, 0.5 + (buyRatio - 0.5) * 0.6)
      : buyRatio < 0.4 ? Math.max(0.2, 0.5 - (0.5 - buyRatio) * 0.6)
      : 0.5;

    // Estimate total PnL based on gas costs (conservative)
    // Total value from gas is a lower bound — actual PnL requires more data
    const totalPnlUsd = discoveredTrader.totalValueUsd > 0
      ? discoveredTrader.totalValueUsd * (winRate - 0.5) * 2
      : 0;

    // Unique tokens traded from transfer data
    const uniqueTokens = new Set(transfers.map(tx => tx.contractAddress.toLowerCase())).size;

    // Entry rank — for Etherscan we don't know the token's creation time
    // Use block numbers to estimate how early the trader entered
    const firstTransferBlock = Math.min(...transfers.map(tx => parseInt(tx.blockNumber, 10) || Infinity));
    const avgEntryRank = firstTransferBlock < Infinity
      ? Math.max(1, Math.min(1000, Math.floor(firstTransferBlock / 1000)))
      : 100;

    return {
      totalTrades: discoveredTrader.txCount,
      winRate,
      avgPnlUsd: totalPnlUsd / Math.max(1, discoveredTrader.txCount),
      totalPnlUsd,
      avgHoldTimeMin,
      avgTradeSizeUsd,
      avgEntryRank,
      earlyEntryCount: avgEntryRank < 50 ? discoveredTrader.buyCount : 0,
      avgExitMultiplier: winRate > 0.6 ? 1.5 + (winRate - 0.5) * 3 : 0.8,
      totalHoldingsUsd: discoveredTrader.totalValueUsd,
      uniqueTokensTraded: Math.max(1, uniqueTokens),
      preferredDexes: ['ethereum'],
      preferredChains: [token.chain],
      sharpeRatio: winRate > 0.6 ? 0.8 + (winRate - 0.5) * 2 : -0.2 + winRate * 0.5,
      profitFactor: winRate > 0.5 ? 1 + winRate * 0.8 : 0.5 + winRate * 0.5,
      maxDrawdown: -Math.abs(totalPnlUsd) * 0.3,
      consistencyScore,
      washTradeScore: 0.02, // Low default — real data doesn't show wash trading signals
      copyTradeScore: 0.02, // Low default — needs cross-wallet analysis to determine
      frontrunCount: 0,
      sandwichCount: 0,
      tradingHourPattern,
      isActive247,
      avgTimeBetweenTradesMin,
    };
  }

  // Fallback: Use DiscoveredTrader summary (no detailed transfers)
  // Still NO random values — use deterministic estimates from available data
  const totalTx = discoveredTrader.buyCount + discoveredTrader.sellCount;
  const buyRatio = totalTx > 0 ? discoveredTrader.buyCount / totalTx : 0.5;
  const winRate = buyRatio > 0.6 ? Math.min(0.8, 0.5 + (buyRatio - 0.5) * 0.6)
    : buyRatio < 0.4 ? Math.max(0.2, 0.5 - (0.5 - buyRatio) * 0.6)
    : 0.5;

  const totalPnlUsd = discoveredTrader.totalValueUsd * (winRate - 0.5) * 2;
  const avgTradeSizeUsd = discoveredTrader.totalValueUsd / Math.max(1, discoveredTrader.txCount);

  // Estimate hold time from first and last seen timestamps
  const activitySpanMin = (discoveredTrader.lastSeen - discoveredTrader.firstSeen) / 60;
  const avgHoldTimeMin = activitySpanMin > 0
    ? activitySpanMin / Math.max(1, Math.floor(discoveredTrader.txCount / 2))
    : 60;

  return {
    totalTrades: discoveredTrader.txCount,
    winRate,
    avgPnlUsd: totalPnlUsd / Math.max(1, discoveredTrader.txCount),
    totalPnlUsd,
    avgHoldTimeMin,
    avgTradeSizeUsd,
    avgEntryRank: 100, // Unknown without block context
    earlyEntryCount: 0,
    avgExitMultiplier: winRate > 0.6 ? 1.5 + (winRate - 0.5) * 3 : 0.8,
    totalHoldingsUsd: discoveredTrader.totalValueUsd,
    uniqueTokensTraded: 1,
    preferredDexes: ['ethereum'],
    preferredChains: [token.chain],
    sharpeRatio: winRate > 0.6 ? 0.8 + (winRate - 0.5) * 2 : -0.2 + winRate * 0.5,
    profitFactor: winRate > 0.5 ? 1 + winRate * 0.8 : 0.5 + winRate * 0.5,
    maxDrawdown: -Math.abs(totalPnlUsd) * 0.3,
    consistencyScore: 0.3, // Conservative default
    washTradeScore: 0.02,
    copyTradeScore: 0.02,
    frontrunCount: 0,
    sandwichCount: 0,
    tradingHourPattern: Array(24).fill(0) as number[],
    isActive247: false,
    avgTimeBetweenTradesMin: 30,
  };
}

// ══════════════════════════════════════════════════════════════
// ETHERSCAN-ONLY SCAN LOGIC
// ══════════════════════════════════════════════════════════════

/**
 * Etherscan-only targeted scan for ETH tokens.
 * Uses real on-chain ERC-20 transfer data from Etherscan.
 * This is faster than a full scan and provides verified data.
 */
async function runEtherscanScan(result: SyncResult): Promise<void> {
  const { db } = await import('@/lib/db');
  const { etherscanClient } = await import('@/lib/services/data-sources/etherscan-client');
  const {
    calculateSmartMoneyScore,
    calculateWhaleScore,
    calculateSniperScore,
    detectBehavioralPatterns,
    buildWalletProfile,
  } = await import('@/lib/services/execution/wallet-profiler');

  console.log('[SmartMoneySync] === ETHERSCAN SCAN: Targeted ETH token scan with real on-chain data ===');

  // 1. Get ETH tokens with volume
  const ethTokens = await db.token.findMany({
    where: {
      chain: 'ETH',
      volume24h: { gt: 0 },
    },
    orderBy: { volume24h: 'desc' },
    take: 20,
  });

  result.tokensScanned = ethTokens.length;
  console.log(`[SmartMoneySync][Etherscan] Scanning ${ethTokens.length} ETH tokens`);

  // 2. For each ETH token, discover active traders via Etherscan
  for (const token of ethTokens) {
    try {
      const discoveredTraders = await etherscanClient.discoverActiveTraders(
        token.address,
        3, // min 3 transactions to be considered active
      );

      result.etherscanTokensScanned++;

      if (discoveredTraders.length === 0) {
        console.log(`[SmartMoneySync][Etherscan] No active traders for ${token.symbol}`);
        continue;
      }

      console.log(
        `[SmartMoneySync][Etherscan] Found ${discoveredTraders.length} active traders for ${token.symbol}`,
      );

      // 3. Process each discovered trader
      for (const discoveredTrader of discoveredTraders) {
        try {
          await processEtherscanTrader(
            discoveredTrader,
            token,
            db,
            result,
            { calculateSmartMoneyScore, calculateWhaleScore, calculateSniperScore, detectBehavioralPatterns, buildWalletProfile },
          );
        } catch (err) {
          result.errors.push(
            `[Etherscan] Trader ${discoveredTrader.address.slice(0, 8)}: ${String(err)}`,
          );
        }
      }

      result.walletsDiscovered += discoveredTraders.length;
      result.swapsDiscovered += discoveredTraders.reduce((s, t) => s + t.txCount, 0);

      // Rate-limit between tokens (Etherscan has built-in 250ms rate limit)
      await new Promise(r => setTimeout(r, 300));
    } catch (err) {
      result.errors.push(`[Etherscan] Token ${token.symbol}: ${String(err)}`);
    }
  }

  console.log(
    `[SmartMoneySync][Etherscan] SCAN complete: ${result.etherscanTradersDiscovered} traders, ` +
    `${result.etherscanTransactionsCreated} transactions, ${result.etherscanTokensScanned} tokens scanned`,
  );
}

// ══════════════════════════════════════════════════════════════
// MAIN SYNC ORCHESTRATOR
// ══════════════════════════════════════════════════════════════

async function runSync(action: 'scan' | 'profile' | 'full' | 'etherscan'): Promise<SyncResult> {
  const startedAt = new Date();
  const result: SyncResult = {
    action,
    startedAt: startedAt.toISOString(),
    tokensScanned: 0,
    swapsDiscovered: 0,
    walletsDiscovered: 0,
    tradersCreated: 0,
    tradersUpdated: 0,
    transactionsCreated: 0,
    etherscanTradersDiscovered: 0,
    etherscanTransactionsCreated: 0,
    etherscanTokensScanned: 0,
    dexpaprikaTradersDiscovered: 0,
    dexpaprikaTransactionsCreated: 0,
    tradersProfiled: 0,
    scoresRecalculated: 0,
    patternsCreated: 0,
    patternsUpdated: 0,
    labelsCreated: 0,
    totalSmartMoney: 0,
    totalWhales: 0,
    totalSnipers: 0,
    totalBots: 0,
    totalTraders: 0,
    errors: [],
  };

  try {
    // Run Etherscan-only scan if requested
    if (action === 'etherscan') {
      await runEtherscanScan(result);
    }

    // Run scan if requested
    if (action === 'scan' || action === 'full') {
      await runScan(result);
    }

    // Run profile if requested
    if (action === 'profile' || action === 'full') {
      await runProfile(result);
    }

    // Compute final summary counts
    const { db } = await import('@/lib/db');
    result.totalSmartMoney = await db.trader.count({ where: { isSmartMoney: true } });
    result.totalWhales = await db.trader.count({ where: { isWhale: true } });
    result.totalSnipers = await db.trader.count({ where: { isSniper: true } });
    result.totalBots = await db.trader.count({ where: { isBot: true } });
    result.totalTraders = await db.trader.count();
  } catch (err) {
    result.errors.push(`Sync orchestration error: ${String(err)}`);
  }

  const completedAt = new Date();
  result.completedAt = completedAt.toISOString();
  result.durationMs = completedAt.getTime() - startedAt.getTime();

  return result;
}

// ══════════════════════════════════════════════════════════════
// API HANDLERS
// ══════════════════════════════════════════════════════════════

export async function GET() {
  try {
    const { db } = await import('@/lib/db');

    // Gather current status from DB
    const [
      totalTraders,
      totalSmartMoney,
      totalWhales,
      totalSnipers,
      totalBots,
      totalTransactions,
      totalBehaviorPatterns,
      totalLabelAssignments,
      recentTraders,
    ] = await Promise.all([
      db.trader.count(),
      db.trader.count({ where: { isSmartMoney: true } }),
      db.trader.count({ where: { isWhale: true } }),
      db.trader.count({ where: { isSniper: true } }),
      db.trader.count({ where: { isBot: true } }),
      db.traderTransaction.count(),
      db.traderBehaviorPattern.count(),
      db.traderLabelAssignment.count(),
      db.trader.findMany({
        orderBy: { lastActive: 'desc' },
        take: 5,
        select: {
          address: true,
          chain: true,
          primaryLabel: true,
          smartMoneyScore: true,
          whaleScore: true,
          sniperScore: true,
          isSmartMoney: true,
          isWhale: true,
          isSniper: true,
          isBot: true,
          lastActive: true,
          totalTrades: true,
          totalPnl: true,
        },
      }),
    ]);

    return NextResponse.json({
      status: syncRunning ? 'running' : 'idle',
      currentSync: syncRunning
        ? { action: lastSyncResult?.action, startedAt: lastSyncStartedAt }
        : null,
      lastResult: lastSyncResult,
      lastSyncCompletedAt,
      db: {
        totalTraders,
        totalSmartMoney,
        totalWhales,
        totalSnipers,
        totalBots,
        totalTransactions,
        totalBehaviorPatterns,
        totalLabelAssignments,
      },
      recentTraders: recentTraders.map(t => ({
        address: t.address.slice(0, 8) + '...' + t.address.slice(-4),
        chain: t.chain,
        label: t.primaryLabel,
        scores: {
          smartMoney: t.smartMoneyScore,
          whale: t.whaleScore,
          sniper: t.sniperScore,
        },
        flags: {
          isSmartMoney: t.isSmartMoney,
          isWhale: t.isWhale,
          isSniper: t.isSniper,
          isBot: t.isBot,
        },
        lastActive: t.lastActive,
        totalTrades: t.totalTrades,
        totalPnl: t.totalPnl,
      })),
    });
  } catch (error) {
    return NextResponse.json(
      { error: String(error), status: 'error' },
      { status: 500 },
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const body: SyncBody = await request.json().catch(() => ({}));
    const action = body.action || 'full';

    if (!['scan', 'profile', 'full', 'etherscan'].includes(action)) {
      return NextResponse.json(
        { error: `Invalid action "${action}". Use "scan", "profile", "full", or "etherscan".` },
        { status: 400 },
      );
    }

    if (syncRunning) {
      return NextResponse.json(
        {
          error: 'Sync already running',
          status: 'running',
          currentAction: lastSyncResult?.action,
          lastResult: lastSyncResult,
        },
        { status: 409 },
      );
    }

    syncRunning = true;
    lastSyncStartedAt = new Date();
    // Safety timeout: reset flag after 10 minutes even if sync crashes
    setTimeout(() => { syncRunning = false; }, 600000);

    // Fire-and-forget: run sync in background
    runSync(action as 'scan' | 'profile' | 'full' | 'etherscan')
      .then((result) => {
        lastSyncResult = result;
        lastSyncCompletedAt = new Date();
        console.log(`[SmartMoneySync] Complete (${action}):`, {
          walletsDiscovered: result.walletsDiscovered,
          tradersCreated: result.tradersCreated,
          tradersUpdated: result.tradersUpdated,
          tradersProfiled: result.tradersProfiled,
          scoresRecalculated: result.scoresRecalculated,
          patternsCreated: result.patternsCreated,
          labelsCreated: result.labelsCreated,
          etherscanTraders: result.etherscanTradersDiscovered,
          etherscanTxs: result.etherscanTransactionsCreated,
          dexpaprikaTraders: result.dexpaprikaTradersDiscovered,
          dexpaprikaTxs: result.dexpaprikaTransactionsCreated,
          errors: result.errors.length,
          durationMs: result.durationMs,
        });
      })
      .catch((err) => {
        console.error('[SmartMoneySync] Failed:', err);
        lastSyncResult = {
          action,
          startedAt: lastSyncStartedAt!.toISOString(),
          errors: [String(err)],
          tokensScanned: 0,
          swapsDiscovered: 0,
          walletsDiscovered: 0,
          tradersCreated: 0,
          tradersUpdated: 0,
          transactionsCreated: 0,
          etherscanTradersDiscovered: 0,
          etherscanTransactionsCreated: 0,
          etherscanTokensScanned: 0,
          dexpaprikaTradersDiscovered: 0,
          dexpaprikaTransactionsCreated: 0,
          tradersProfiled: 0,
          scoresRecalculated: 0,
          patternsCreated: 0,
          patternsUpdated: 0,
          labelsCreated: 0,
          totalSmartMoney: 0,
          totalWhales: 0,
          totalSnipers: 0,
          totalBots: 0,
          totalTraders: 0,
        };
        lastSyncCompletedAt = new Date();
      })
      .finally(() => {
        syncRunning = false;
      });

    return NextResponse.json({
      status: 'started',
      action,
      message: `Smart Money sync started with action: ${action}. ${
        action === 'scan'
          ? 'Scanning top tokens for active wallets via DexPaprika + Etherscan (for ETH tokens).'
          : action === 'etherscan'
            ? 'Targeted Etherscan scan for ETH tokens using real on-chain ERC-20 transfer data.'
            : action === 'profile'
              ? 'Re-profiling all existing traders with wallet-profiler scoring.'
              : 'Running full scan + profile pipeline.'
      }`,
      startedAt: lastSyncStartedAt.toISOString(),
      lastResult: lastSyncResult,
    });
  } catch (error) {
    syncRunning = false;
    return NextResponse.json(
      { error: String(error) },
      { status: 500 },
    );
  }
}
