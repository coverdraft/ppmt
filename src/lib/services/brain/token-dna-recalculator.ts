/**
 * Token DNA Recalculator - CryptoQuant Terminal
 *
 * Recalculates TokenDNA from trader intelligence data.
 * DNA is set once during token sync but should be updated as new
 * trader data becomes available from smart-money-sync and brain cycles.
 *
 * This module is called:
 *   1. After smart-money-sync completes (new traders discovered)
 *   2. Periodically by the brain cycle engine (every N cycles)
 *   3. On-demand via API
 *
 * All metrics are deterministic — computed from actual Trader records
 * and TraderTransactions. No Math.random().
 */

import { db } from '../../db';

// ============================================================
// TYPES
// ============================================================

export interface DNACalculationResult {
  tokenAddress: string;
  symbol: string;
  chain: string;
  updated: boolean;

  // Calculated scores
  botActivityScore: number;
  smartMoneyScore: number;
  retailScore: number;
  whaleScore: number;
  washTradeProb: number;
  sniperPct: number;
  mevPct: number;
  copyBotPct: number;

  // Composition breakdown
  traderComposition: Record<string, number>;
  topWallets: Array<{
    address: string;
    label: string;
    pnl: number;
    entryRank: number;
    holdTime: number;
  }>;

  // Metadata
  tradersAnalyzed: number;
  transactionsAnalyzed: number;
  calculatedAt: Date;
}

// ============================================================
// MAIN RECALCULATION FUNCTION
// ============================================================

/**
 * Recalculate DNA for a single token from its trader data.
 */
export async function recalculateTokenDna(
  tokenAddress: string,
): Promise<DNACalculationResult | null> {
  // Get token with existing DNA
  const token = await db.token.findUnique({
    where: { address: tokenAddress },
    include: { dna: true },
  });

  if (!token) return null;

  // Get all traders with transactions for this token
  // First find trader IDs who have transactions for this token
  const traderIds = await db.traderTransaction.findMany({
    where: { tokenAddress },
    distinct: ['traderId'],
    select: { traderId: true },
  });

  const traders = traderIds.length > 0
    ? await db.trader.findMany({
        where: { id: { in: traderIds.map(t => t.traderId) } },
        include: {
          transactions: {
            where: { tokenAddress },
            orderBy: { blockTime: 'desc' },
            take: 100,
          },
        },
      })
    : [];

  const totalTraders = traders.length;
  const totalTransactions = traders.reduce((sum, t) => sum + t.transactions.length, 0);

  // If no trader data, return default DNA
  if (totalTraders === 0) {
    return {
      tokenAddress,
      symbol: token.symbol,
      chain: token.chain,
      updated: false,
      botActivityScore: 0,
      smartMoneyScore: 0,
      retailScore: 50,
      whaleScore: 0,
      washTradeProb: 0,
      sniperPct: 0,
      mevPct: 0,
      copyBotPct: 0,
      traderComposition: { retail: 100 },
      topWallets: [],
      tradersAnalyzed: 0,
      transactionsAnalyzed: 0,
      calculatedAt: new Date(),
    };
  }

  // === CLASSIFY TRADERS ===
  let botCount = 0;
  let smartMoneyCount = 0;
  let whaleCount = 0;
  let sniperCount = 0;
  let retailCount = 0;
  let mevCount = 0;
  let copyBotCount = 0;

  // Volume-weighted classification
  let botVolume = 0;
  let smVolume = 0;
  let whaleVolume = 0;
  let sniperVolume = 0;
  let retailVolume = 0;
  let totalVolume = 0;

  // Top wallets by PnL
  const walletScores: Array<{
    address: string;
    label: string;
    pnl: number;
    entryRank: number;
    holdTime: number;
  }> = [];

  for (const trader of traders) {
    const traderVol = trader.totalVolumeUsd || 0;
    totalVolume += traderVol;

    // Classify by primary label and scores
    const isBot = trader.isBot || trader.botConfidence > 0.5;
    const isSmartMoney = trader.isSmartMoney || trader.smartMoneyScore > 50;
    const isWhale = trader.isWhale || trader.whaleScore > 50;
    const isSniper = trader.isSniper || trader.sniperScore > 50;

    if (isBot) {
      botCount++;
      botVolume += traderVol;
      // Sub-classify bots
      const botType = trader.botType?.toLowerCase() || '';
      if (botType.includes('mev') || botType.includes('sandwich') || botType.includes('frontrun')) {
        mevCount++;
      } else if (botType.includes('copy') || trader.copyTradeScore > 0.5) {
        copyBotCount++;
      }
    } else if (isSmartMoney) {
      smartMoneyCount++;
      smVolume += traderVol;
    } else if (isSniper) {
      sniperCount++;
      sniperVolume += traderVol;
    } else if (isWhale) {
      whaleCount++;
      whaleVolume += traderVol;
    } else {
      retailCount++;
      retailVolume += traderVol;
    }

    // Build wallet score for top wallets
    walletScores.push({
      address: trader.address,
      label: trader.primaryLabel || 'UNKNOWN',
      pnl: trader.totalPnl || 0,
      entryRank: trader.avgEntryRank || 100,
      holdTime: trader.avgHoldTimeMin || 0,
    });
  }

  // === CALCULATE SCORES (0-100) ===
  const botActivityScore = totalTraders > 0 ? (botCount / totalTraders) * 100 : 0;
  const smartMoneyScore = totalTraders > 0 ? (smartMoneyCount / totalTraders) * 100 : 0;
  const whaleScore = totalTraders > 0 ? (whaleCount / totalTraders) * 100 : 0;
  const retailScore = totalTraders > 0 ? (retailCount / totalTraders) * 100 : 50;
  const sniperPct = totalTraders > 0 ? (sniperCount / totalTraders) * 100 : 0;
  const mevPct = totalTraders > 0 ? (mevCount / totalTraders) * 100 : 0;
  const copyBotPct = totalTraders > 0 ? (copyBotCount / totalTraders) * 100 : 0;

  // Wash trade probability: based on wash trade scores of traders
  const avgWashTradeScore = traders.length > 0
    ? traders.reduce((sum, t) => sum + (t.washTradeScore || 0), 0) / traders.length
    : 0;
  const washTradeProb = Math.min(1, avgWashTradeScore);

  // Volume-weighted adjustments (if most volume comes from bots, increase bot score)
  const volumeWeightedBotScore = totalVolume > 0
    ? Math.max(botActivityScore, (botVolume / totalVolume) * 100)
    : botActivityScore;

  // Trader composition breakdown
  const traderComposition: Record<string, number> = {
    smartMoney: smartMoneyCount,
    whale: whaleCount,
    bot_mev: mevCount,
    bot_sniper: sniperCount,
    bot_copy: copyBotCount,
    retail: retailCount,
  };

  // Top wallets sorted by PnL (most profitable first)
  const topWallets = walletScores
    .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
    .slice(0, 10);

  // === PERSIST TO DB ===
  const result: DNACalculationResult = {
    tokenAddress,
    symbol: token.symbol,
    chain: token.chain,
    updated: true,
    botActivityScore: Math.round(volumeWeightedBotScore * 100) / 100,
    smartMoneyScore: Math.round(smartMoneyScore * 100) / 100,
    retailScore: Math.round(retailScore * 100) / 100,
    whaleScore: Math.round(whaleScore * 100) / 100,
    washTradeProb: Math.round(washTradeProb * 1000) / 1000,
    sniperPct: Math.round(sniperPct * 100) / 100,
    mevPct: Math.round(mevPct * 100) / 100,
    copyBotPct: Math.round(copyBotPct * 100) / 100,
    traderComposition,
    topWallets,
    tradersAnalyzed: totalTraders,
    transactionsAnalyzed: totalTransactions,
    calculatedAt: new Date(),
  };

  // Upsert DNA record
  if (token.dna) {
    await db.tokenDNA.update({
      where: { tokenId: token.id },
      data: {
        botActivityScore: result.botActivityScore,
        smartMoneyScore: result.smartMoneyScore,
        retailScore: result.retailScore,
        whaleScore: result.whaleScore,
        washTradeProb: result.washTradeProb,
        sniperPct: result.sniperPct,
        mevPct: result.mevPct,
        copyBotPct: result.copyBotPct,
        traderComposition: JSON.stringify(traderComposition),
        topWallets: JSON.stringify(topWallets),
      },
    });
  } else {
    await db.tokenDNA.create({
      data: {
        tokenId: token.id,
        botActivityScore: result.botActivityScore,
        smartMoneyScore: result.smartMoneyScore,
        retailScore: result.retailScore,
        whaleScore: result.whaleScore,
        washTradeProb: result.washTradeProb,
        sniperPct: result.sniperPct,
        mevPct: result.mevPct,
        copyBotPct: result.copyBotPct,
        traderComposition: JSON.stringify(traderComposition),
        topWallets: JSON.stringify(topWallets),
      },
    });
  }

  // Also update token-level bot/smart money percentages
  await db.token.update({
    where: { address: tokenAddress },
    data: {
      botActivityPct: result.botActivityScore,
      smartMoneyPct: result.smartMoneyScore,
    },
  });

  return result;
}

/**
 * Batch recalculate DNA for multiple tokens.
 * Processes sequentially to avoid DB overload.
 */
export async function batchRecalculateDna(
  tokenAddresses: string[],
): Promise<{
  total: number;
  updated: number;
  skipped: number;
  errors: string[];
}> {
  let updated = 0;
  let skipped = 0;
  const errors: string[] = [];

  for (const address of tokenAddresses) {
    try {
      const result = await recalculateTokenDna(address);
      if (result?.updated) {
        updated++;
      } else {
        skipped++;
      }
    } catch (error) {
      errors.push(`${address}: ${error instanceof Error ? error.message : String(error)}`);
      skipped++;
    }

    // Small delay between tokens to avoid DB pressure
    await new Promise(r => setTimeout(r, 50));
  }

  return { total: tokenAddresses.length, updated, skipped, errors };
}

/**
 * Recalculate DNA for all tokens that have trader data but stale DNA.
 * A token's DNA is considered stale if it has traders but DNA hasn't been
 * updated (TokenDNA has no updatedAt field, so we check if DNA exists at all
 * or if the token has more traders than when DNA was last calculated).
 */
export async function recalculateStaleDna(
  limit = 50,
): Promise<{
  total: number;
  updated: number;
  skipped: number;
  errors: string[];
}> {
  // Find tokens that have trader transactions but no DNA or potentially stale DNA
  // TraderTransaction is related to Trader, not Token directly, so we query
  // distinct tokenAddresses from TraderTransaction
  const tokenAddressesWithTraders = await db.traderTransaction.findMany({
    where: { tokenAddress: { not: '' } },
    distinct: ['tokenAddress'],
    select: { tokenAddress: true },
    take: limit,
  });

  if (tokenAddressesWithTraders.length === 0) {
    return { total: 0, updated: 0, skipped: 0, errors: [] };
  }

  return batchRecalculateDna(tokenAddressesWithTraders.map(t => t.tokenAddress));
}
