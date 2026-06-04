/**
 * Operability Filter - Determines if a token is tradeable after fees & slippage
 */

import { db } from '@/lib/db';

export interface OperabilityInput {
  tokenAddress: string;
  chain: string;
  liquidityUsd: number;
  volume24h: number;
  priceUsd: number;
  marketCap?: number;
  positionSizeUsd: number;
}

export interface OperabilityResult {
  tokenAddress: string;
  chain: string;
  score: number;
  feeImpactPct: number;
  slippageImpactPct: number;
  liquidityUsd: number;
  maxPositionUsd: number;
  volume24h: number;
  spreadPct: number;
  isOperable: boolean;
  reason?: string;
}

const CHAIN_FEES: Record<string, number> = {
  solana: 0.00025,
  ethereum: 0.003,
  base: 0.003,
  arbitrum: 0.001,
  bsc: 0.002,
  polygon: 0.001,
};

const MIN_OPERABILITY_SCORE = 20;
const MIN_LIQUIDITY_USD = 0;
const MAX_POSITION_PCT_OF_LIQUIDITY = 0.05;

export function calculateOperability(input: OperabilityInput): OperabilityResult {
  const { tokenAddress, chain, liquidityUsd, volume24h, priceUsd, marketCap, positionSizeUsd } = input;

  const feeRate = CHAIN_FEES[chain.toLowerCase()] || 0.003;
  const roundTripFees = feeRate * 2;
  const feeImpactPct = roundTripFees * 100;

  let slippageImpactPct = 0;
  let maxPositionUsd = 0;

  if (liquidityUsd > 0) {
    const positionPctOfLiq = positionSizeUsd / liquidityUsd;
    slippageImpactPct = (2 * positionPctOfLiq / (1 - positionPctOfLiq)) * 100;
    maxPositionUsd = liquidityUsd * MAX_POSITION_PCT_OF_LIQUIDITY;
  } else if (volume24h > 0) {
    const estimatedLiquidity = volume24h * 0.05;
    slippageImpactPct = Math.min(2.0, (positionSizeUsd / estimatedLiquidity) * 100);
    maxPositionUsd = estimatedLiquidity * MAX_POSITION_PCT_OF_LIQUIDITY;
  } else {
    slippageImpactPct = 100;
    maxPositionUsd = 0;
  }

  const effectiveLiquidity = liquidityUsd > 0 ? liquidityUsd : volume24h * 0.05;
  const spreadPct = effectiveLiquidity > 100000 ? 0.1
    : effectiveLiquidity > 10000 ? 0.5
    : effectiveLiquidity > 1000 ? 2.0
    : 10.0;

  const volumeHealth = volume24h > 0 ? Math.min(1, volume24h / 10000) : 0;
  const totalCostPct = feeImpactPct + slippageImpactPct + spreadPct;
  const costReliability = 0.5 + (volumeHealth * 0.5);
  const adjustedCostPct = totalCostPct * costReliability;
  const score = Math.max(0, Math.min(100, 100 - adjustedCostPct));

  let isOperable = score >= MIN_OPERABILITY_SCORE;
  let reason: string | undefined;

  if (liquidityUsd > 0 && liquidityUsd < MIN_LIQUIDITY_USD) {
    isOperable = false;
    reason = `Insufficient liquidity: $${liquidityUsd.toFixed(0)} < $${MIN_LIQUIDITY_USD}`;
  } else if (liquidityUsd > 0 && positionSizeUsd > maxPositionUsd) {
    isOperable = false;
    reason = `Position too large: $${positionSizeUsd.toFixed(0)} > max $${maxPositionUsd.toFixed(0)}`;
  } else if (totalCostPct > 50) {
    isOperable = false;
    reason = `Total costs too high: ${totalCostPct.toFixed(1)}% (fees+slippage+spread)`;
  }

  return {
    tokenAddress,
    chain,
    score: Math.round(score * 100) / 100,
    feeImpactPct: Math.round(feeImpactPct * 100) / 100,
    slippageImpactPct: Math.round(slippageImpactPct * 100) / 100,
    liquidityUsd,
    maxPositionUsd: Math.round(maxPositionUsd * 100) / 100,
    volume24h,
    spreadPct,
    isOperable,
    reason,
  };
}

export function batchCalculateOperability(tokens: OperabilityInput[]): OperabilityResult[] {
  return tokens
    .map(t => calculateOperability(t))
    .filter(r => r.isOperable)
    .sort((a, b) => b.score - a.score);
}

export async function persistOperabilityScores(results: OperabilityResult[], cycleId?: string): Promise<number> {
  let persisted = 0;
  for (const result of results) {
    try {
      await db.operabilityScore.create({
        data: {
          tokenAddress: result.tokenAddress,
          chain: result.chain,
          score: result.score,
          feeImpactPct: result.feeImpactPct,
          slippageImpactPct: result.slippageImpactPct,
          liquidityUsd: result.liquidityUsd,
          maxPositionUsd: result.maxPositionUsd,
          volume24h: result.volume24h,
          spreadPct: result.spreadPct,
          isOperable: result.isOperable,
          reason: result.reason,
          cycleId,
        },
      });
      persisted++;
    } catch {
      // Skip duplicates/errors
    }
  }
  return persisted;
}
