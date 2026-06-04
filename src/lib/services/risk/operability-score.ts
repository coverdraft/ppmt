/**
 * Operability Score Engine - CryptoQuant Terminal
 * 
 * Determines if a token is TRADEABLE based on:
 * - Liquidity depth (can we enter/exit without destroying the price?)
 * - Fee impact (will fees eat our profits?)
 * - Slippage estimation (what's the real execution price?)
 * - Minimum margin requirement (expected gain must exceed costs by 3x)
 * - Chain economics (Solana fees vs Ethereum fees)
 * 
 * This is the FIRST filter the brain applies. If a token isn't operable,
 * no trading system should be applied to it.
 */

// ============================================================
// TYPES
// ============================================================

export type OperabilityLevel = 'PREMIUM' | 'GOOD' | 'MARGINAL' | 'RISKY' | 'UNOPERABLE';

export interface OperabilityInput {
  tokenAddress: string;
  symbol: string;
  chain: 'SOL' | 'ETH' | 'BASE' | 'ARB' | string;
  
  // Market data (from DexScreener)
  priceUsd: number;
  liquidityUsd: number;
  volume24h: number;
  marketCap: number;
  
  // Trading parameters
  positionSizeUsd: number;        // How much we want to trade
  expectedGainPct: number;        // Expected profit % from signal
  
  // Token health indicators
  botActivityPct: number;         // % of volume from bots (0-100)
  holderCount: number;
  priceChange24h: number;         // To assess volatility
  
  // Optional: DexScreener specific
  dexId?: string;                 // Which DEX
  pairCreatedAt?: number;         // When pair was created (timestamp)
  buyTxns24h?: number;
  sellTxns24h?: number;
}

export interface FeeEstimate {
  gasFeeUsd: number;
  swapFeePct: number;             // DEX fee (e.g., 0.3% for Uniswap)
  swapFeeUsd: number;
  slippagePct: number;            // Estimated price impact
  slippageUsd: number;
  totalCostUsd: number;
  totalCostPct: number;           // Total cost as % of position
}

export interface OperabilityResult {
  tokenAddress: string;
  symbol: string;
  chain: string;
  
  // Core scores (0-100)
  overallScore: number;
  liquidityScore: number;
  feeScore: number;
  slippageScore: number;
  healthScore: number;
  marginScore: number;
  
  // Fee breakdown
  feeEstimate: FeeEstimate;
  
  // Operability classification
  level: OperabilityLevel;
  
  // What position size is safe?
  maxSafePositionUsd: number;
  recommendedPositionUsd: number;
  
  // Is it worth trading?
  isOperable: boolean;
  reason: string;
  
  // Minimum expected gain to be profitable after costs
  minimumGainPct: number;
  
  // Warnings
  warnings: string[];
}

// ============================================================
// CHAIN ECONOMICS - Fee models per chain
// ============================================================

interface ChainEconomics {
  avgGasFeeUsd: number;
  swapFeePct: number;           // Typical DEX fee
  priorityFeeUsd: number;       // For MEV protection
  confirmationTimeSec: number;  // How fast trades execute
  minLiquidityUsd: number;      // Minimum liquidity to be operable
}

const CHAIN_ECONOMICS: Record<string, ChainEconomics> = {
  'SOL': {
    avgGasFeeUsd: 0.001,        // ~$0.001 per tx
    swapFeePct: 0.003,           // 0.3% Jupiter/Raydium
    priorityFeeUsd: 0.01,        // Jito tip for MEV protection
    confirmationTimeSec: 2,
    minLiquidityUsd: 5000,
  },
  'ETH': {
    avgGasFeeUsd: 2.0,          // $2-10 per tx depending on gas
    swapFeePct: 0.003,           // 0.3% Uniswap
    priorityFeeUsd: 0.5,         // MEV protection
    confirmationTimeSec: 12,
    minLiquidityUsd: 50000,
  },
  'BASE': {
    avgGasFeeUsd: 0.05,
    swapFeePct: 0.003,
    priorityFeeUsd: 0.01,
    confirmationTimeSec: 2,
    minLiquidityUsd: 20000,
  },
  'ARB': {
    avgGasFeeUsd: 0.15,
    swapFeePct: 0.003,
    priorityFeeUsd: 0.05,
    confirmationTimeSec: 2,
    minLiquidityUsd: 20000,
  },
};

// ============================================================
// LIQUIDITY SCORING
// ============================================================

/**
 * Score liquidity depth based on:
 * 1. Absolute liquidity (is there enough?)
 * 2. Liquidity vs position size (can we enter/exit?)
 * 3. Volume/liquidity ratio (is liquidity real or stale?)
 */
function scoreLiquidity(input: OperabilityInput): { score: number; maxSafePosition: number; warnings: string[] } {
  const warnings: string[] = [];
  const chainEcon = CHAIN_ECONOMICS[input.chain] || CHAIN_ECONOMICS['SOL'];
  let score = 0;
  
  // 1. Absolute liquidity (0-40 points)
  if (input.liquidityUsd >= 1_000_000) score += 40;
  else if (input.liquidityUsd >= 500_000) score += 35;
  else if (input.liquidityUsd >= 100_000) score += 30;
  else if (input.liquidityUsd >= 50_000) score += 25;
  else if (input.liquidityUsd >= 10_000) score += 15;
  else if (input.liquidityUsd >= chainEcon.minLiquidityUsd) score += 8;
  else {
    score += 0;
    warnings.push(`Liquidity $${input.liquidityUsd.toFixed(0)} below minimum $${chainEcon.minLiquidityUsd}`);
  }
  
  // 2. Liquidity vs position size (0-35 points)
  // Rule: position should be <2% of liquidity to avoid significant impact
  const positionRatio = input.positionSizeUsd / (input.liquidityUsd || 1);
  if (positionRatio < 0.005) score += 35;       // <0.5% - excellent
  else if (positionRatio < 0.01) score += 30;   // <1% - very good
  else if (positionRatio < 0.02) score += 25;   // <2% - good
  else if (positionRatio < 0.05) score += 15;   // <5% - marginal
  else if (positionRatio < 0.10) {
    score += 5;
    warnings.push(`Position is ${((positionRatio) * 100).toFixed(1)}% of liquidity - high impact`);
  } else {
    score += 0;
    warnings.push(`Position is ${((positionRatio) * 100).toFixed(1)}% of liquidity - would move price significantly`);
  }
  
  // 3. Volume/liquidity ratio (0-25 points) - healthy ratio is 0.5-3x
  const volLiqRatio = input.volume24h / (input.liquidityUsd || 1);
  if (volLiqRatio >= 0.5 && volLiqRatio <= 3) score += 25;   // Healthy
  else if (volLiqRatio >= 0.2 && volLiqRatio <= 5) score += 15;
  else if (volLiqRatio > 5) {
    score += 5;
    warnings.push('Volume/Liquidity ratio >5x - liquidity may be thin');
  } else {
    score += 5;
    warnings.push('Volume/Liquidity ratio <0.2x - low activity');
  }
  
  // Calculate max safe position: 2% of liquidity
  const maxSafePosition = input.liquidityUsd * 0.02;
  
  return { score, maxSafePosition, warnings };
}

// ============================================================
// FEE ESTIMATION
// ============================================================

/**
 * Estimate total fees for a round-trip trade (buy + sell)
 * 
 * This is CRITICAL: with $10 capital, every cent matters.
 * We must know the exact cost of a trade before entering.
 */
export function estimateFees(input: OperabilityInput): FeeEstimate {
  const chainEcon = CHAIN_ECONOMICS[input.chain] || CHAIN_ECONOMICS['SOL'];
  
  // Gas fees (round trip: buy tx + sell tx)
  const gasFeeUsd = chainEcon.avgGasFeeUsd * 2 + chainEcon.priorityFeeUsd * 2;
  
  // Swap fees (DEX fee on entry + exit)
  const entrySwapFee = input.positionSizeUsd * chainEcon.swapFeePct;
  const exitSwapFee = input.positionSizeUsd * (1 + input.expectedGainPct / 100) * chainEcon.swapFeePct;
  const swapFeeUsd = entrySwapFee + exitSwapFee;
  const swapFeePct = chainEcon.swapFeePct * 2; // Two swaps
  
  // Slippage estimation
  // Model: slippage ≈ positionSize / (2 * sqrt(liquidity)) * volatilityFactor
  const positionRatio = input.positionSizeUsd / (input.liquidityUsd || 1);
  const volatilityFactor = Math.abs(input.priceChange24h) > 20 ? 2.0
    : Math.abs(input.priceChange24h) > 10 ? 1.5
    : 1.0;
  
  // Entry slippage + exit slippage
  const slippagePct = positionRatio * 50 * volatilityFactor; // Empirical approximation
  const slippageUsd = input.positionSizeUsd * (slippagePct / 100) * 2; // Round trip
  
  // Total
  const totalCostUsd = gasFeeUsd + swapFeeUsd + slippageUsd;
  const totalCostPct = (totalCostUsd / (input.positionSizeUsd || 1)) * 100;
  
  return {
    gasFeeUsd: Math.round(gasFeeUsd * 1000) / 1000,
    swapFeePct: Math.round(swapFeePct * 10000) / 10000,
    swapFeeUsd: Math.round(swapFeeUsd * 100) / 100,
    slippagePct: Math.round(slippagePct * 1000) / 1000,
    slippageUsd: Math.round(slippageUsd * 100) / 100,
    totalCostUsd: Math.round(totalCostUsd * 100) / 100,
    totalCostPct: Math.round(totalCostPct * 1000) / 1000,
  };
}

// ============================================================
// FEE SCORING
// ============================================================

function scoreFees(fees: FeeEstimate, positionSizeUsd: number): { score: number; warnings: string[] } {
  const warnings: string[] = [];
  let score = 0;
  
  // Score based on total cost as % of position
  if (fees.totalCostPct < 0.5) score += 50;       // <0.5% total cost - excellent
  else if (fees.totalCostPct < 1.0) score += 40;
  else if (fees.totalCostPct < 2.0) score += 25;
  else if (fees.totalCostPct < 3.0) score += 15;
  else if (fees.totalCostPct < 5.0) {
    score += 5;
    warnings.push(`Total fees ${fees.totalCostPct.toFixed(1)}% - eating into margins`);
  } else {
    score += 0;
    warnings.push(`Total fees ${fees.totalCostPct.toFixed(1)}% - likely unprofitable`);
  }
  
  // Additional check: absolute fee vs small capital
  if (positionSizeUsd <= 20 && fees.totalCostUsd > 0.5) {
    warnings.push(`$${fees.totalCostUsd.toFixed(2)} in fees on $${positionSizeUsd.toFixed(0)} position is significant`);
    score = Math.max(0, score - 10);
  }
  
  // Slippage warning
  if (fees.slippagePct > 1.0) {
    warnings.push(`Estimated slippage ${fees.slippagePct.toFixed(1)}% - high price impact`);
    score = Math.max(0, score - 10);
  }
  
  return { score, warnings };
}

// ============================================================
// HEALTH SCORING
// ============================================================

function scoreHealth(input: OperabilityInput): { score: number; warnings: string[] } {
  const warnings: string[] = [];
  let score = 0;
  
  // Bot activity (0-25 points) - less bots = better for retail
  if (input.botActivityPct < 20) score += 25;
  else if (input.botActivityPct < 40) score += 20;
  else if (input.botActivityPct < 60) score += 10;
  else {
    score += 0;
    warnings.push(`High bot activity: ${input.botActivityPct.toFixed(0)}% of volume`);
  }
  
  // Holder count (0-25 points)
  if (input.holderCount > 1000) score += 25;
  else if (input.holderCount > 500) score += 20;
  else if (input.holderCount > 100) score += 15;
  else if (input.holderCount > 50) score += 8;
  else {
    score += 0;
    warnings.push(`Very few holders: ${input.holderCount} - concentration risk`);
  }
  
  // Buy/sell balance (0-25 points)
  if (input.buyTxns24h !== undefined && input.sellTxns24h !== undefined) {
    const total = input.buyTxns24h + input.sellTxns24h || 1;
    const buyPct = input.buyTxns24h / total;
    if (buyPct > 0.4 && buyPct < 0.65) score += 25;     // Balanced
    else if (buyPct > 0.3 && buyPct < 0.75) score += 15; // Acceptable
    else {
      score += 5;
      warnings.push(`Imbalanced buy/sell ratio: ${((buyPct) * 100).toFixed(0)}% buys`);
    }
  } else {
    score += 12; // Neutral if no data
  }
  
  // Age (0-25 points) - too new = rug risk, too old = stagnant
  if (input.pairCreatedAt) {
    const ageHours = (Date.now() - input.pairCreatedAt) / 3600000;
    if (ageHours < 1) {
      score += 2;
      warnings.push('Token pair created less than 1 hour ago - extreme risk');
    } else if (ageHours < 6) {
      score += 8;
      warnings.push('Token pair less than 6 hours old - high risk');
    } else if (ageHours < 24) {
      score += 12;
    } else if (ageHours < 168) { // 1 week
      score += 20;
    } else {
      score += 25; // Established
    }
  } else {
    score += 15; // Neutral if unknown
  }
  
  return { score, warnings };
}

// ============================================================
// MARGIN SCORING
// ============================================================

function scoreMargin(expectedGainPct: number, totalCostPct: number): { score: number; minimumGainPct: number; warnings: string[] } {
  const warnings: string[] = [];
  
  // Minimum gain: costs * 3 (we want 3x margin of safety)
  const minimumGainPct = totalCostPct * 3;
  
  let score = 0;
  
  // Net gain after costs
  const netGainPct = expectedGainPct - totalCostPct;
  
  if (netGainPct > minimumGainPct) score += 50;       // Excellent margin
  else if (netGainPct > minimumGainPct * 0.5) score += 35;
  else if (netGainPct > 0) {
    score += 15;
    warnings.push(`Net gain only ${netGainPct.toFixed(1)}% after costs - thin margin`);
  } else {
    score += 0;
    warnings.push(`Expected gain ${expectedGainPct.toFixed(1)}% doesn't cover costs ${totalCostPct.toFixed(1)}%`);
  }
  
  // Cost/gain ratio
  if (totalCostPct > 0 && expectedGainPct > 0) {
    const costGainRatio = totalCostPct / expectedGainPct;
    if (costGainRatio < 0.2) score += 30;      // Costs < 20% of expected gain
    else if (costGainRatio < 0.4) score += 20;
    else if (costGainRatio < 0.6) score += 10;
    else {
      score += 0;
      if (costGainRatio >= 1) {
        warnings.push('Costs exceed expected gain - DO NOT TRADE');
      }
    }
  }
  
  return { score, minimumGainPct, warnings };
}

// ============================================================
// MAIN OPERABILITY ENGINE
// ============================================================

/**
 * Calculate the full operability score for a token.
 * This is the FIRST filter applied before any trading system.
 * 
 * Returns detailed breakdown of whether this token is worth trading,
 * including fee estimates, safe position sizes, and warnings.
 */
export function calculateOperabilityScore(input: OperabilityInput): OperabilityResult {
  const allWarnings: string[] = [];
  
  // 1. Liquidity scoring (weight: 30%)
  const { score: liquidityScore, maxSafePosition, warnings: liqWarnings } = scoreLiquidity(input);
  allWarnings.push(...liqWarnings);
  
  // 2. Fee estimation & scoring (weight: 25%)
  const feeEstimate = estimateFees(input);
  const { score: feeScore, warnings: feeWarnings } = scoreFees(feeEstimate, input.positionSizeUsd);
  allWarnings.push(...feeWarnings);
  
  // 3. Health scoring (weight: 20%)
  const { score: healthScore, warnings: healthWarnings } = scoreHealth(input);
  allWarnings.push(...healthWarnings);
  
  // 4. Margin scoring (weight: 25%)
  const { score: marginScore, minimumGainPct, warnings: marginWarnings } = scoreMargin(
    input.expectedGainPct, feeEstimate.totalCostPct
  );
  allWarnings.push(...marginWarnings);
  
  // 5. Slippage scoring (derived from fee estimate)
  let slippageScore = 50;
  if (feeEstimate.slippagePct < 0.1) slippageScore = 95;
  else if (feeEstimate.slippagePct < 0.5) slippageScore = 80;
  else if (feeEstimate.slippagePct < 1.0) slippageScore = 60;
  else if (feeEstimate.slippagePct < 2.0) slippageScore = 35;
  else if (feeEstimate.slippagePct < 5.0) slippageScore = 15;
  else slippageScore = 0;
  
  // 6. Overall score (weighted average)
  const overallScore = Math.round(
    liquidityScore * 0.30 +
    feeScore * 0.25 +
    slippageScore * 0.10 +
    healthScore * 0.15 +
    marginScore * 0.20
  );
  
  // 7. Determine operability level
  let level: OperabilityLevel;
  if (overallScore >= 80) level = 'PREMIUM';
  else if (overallScore >= 60) level = 'GOOD';
  else if (overallScore >= 40) level = 'MARGINAL';
  else if (overallScore >= 20) level = 'RISKY';
  else level = 'UNOPERABLE';
  
  // 8. Recommended position size
  const recommendedPositionUsd = Math.min(
    input.positionSizeUsd,
    maxSafePosition,
    input.liquidityUsd * 0.01 // Never more than 1% of liquidity
  );
  
  // 9. Is it operable?
  const isOperable = overallScore >= 30 && 
    feeEstimate.totalCostPct < input.expectedGainPct &&
    input.liquidityUsd >= (CHAIN_ECONOMICS[input.chain] || CHAIN_ECONOMICS['SOL']).minLiquidityUsd;
  
  // 10. Reason
  let reason: string;
  if (level === 'PREMIUM') reason = `Excellent operability: deep liquidity, low fees (${feeEstimate.totalCostPct.toFixed(1)}% round-trip)`;
  else if (level === 'GOOD') reason = `Good operability: adequate liquidity, reasonable fees (${feeEstimate.totalCostPct.toFixed(1)}% round-trip)`;
  else if (level === 'MARGINAL') reason = `Marginal operability: fees are ${feeEstimate.totalCostPct.toFixed(1)}%, need ${minimumGainPct.toFixed(1)}%+ gain`;
  else if (level === 'RISKY') reason = `Risky operability: thin liquidity or high costs (${feeEstimate.totalCostPct.toFixed(1)}%), easy to lose money`;
  else reason = `Unoperable: insufficient liquidity or costs exceed expected gains`;
  
  return {
    tokenAddress: input.tokenAddress,
    symbol: input.symbol,
    chain: input.chain,
    overallScore,
    liquidityScore,
    feeScore,
    slippageScore,
    healthScore,
    marginScore,
    feeEstimate,
    level,
    maxSafePositionUsd: Math.round(maxSafePosition * 100) / 100,
    recommendedPositionUsd: Math.round(recommendedPositionUsd * 100) / 100,
    isOperable,
    reason,
    minimumGainPct: Math.round(minimumGainPct * 100) / 100,
    warnings: allWarnings,
  };
}

/**
 * Batch operability scoring for multiple tokens
 */
export function batchOperabilityScore(inputs: OperabilityInput[]): OperabilityResult[] {
  return inputs
    .map(calculateOperabilityScore)
    .sort((a, b) => b.overallScore - a.overallScore);
}

/**
 * Filter tokens by minimum operability level
 */
export function filterOperable(
  inputs: OperabilityInput[],
  minLevel: OperabilityLevel = 'MARGINAL'
): OperabilityResult[] {
  const levelOrder: OperabilityLevel[] = ['UNOPERABLE', 'RISKY', 'MARGINAL', 'GOOD', 'PREMIUM'];
  const minIndex = levelOrder.indexOf(minLevel);
  
  return batchOperabilityScore(inputs)
    .filter(r => levelOrder.indexOf(r.level) >= minIndex);
}

/**
 * Quick check: is a token operable at a glance?
 */
export function quickOperabilityCheck(
  liquidityUsd: number,
  positionSizeUsd: number,
  chain: string = 'SOL'
): { operable: boolean; reason: string } {
  const chainEcon = CHAIN_ECONOMICS[chain] || CHAIN_ECONOMICS['SOL'];
  
  if (liquidityUsd < chainEcon.minLiquidityUsd) {
    return { operable: false, reason: `Liquidity $${liquidityUsd.toFixed(0)} < min $${chainEcon.minLiquidityUsd}` };
  }
  
  if (positionSizeUsd > liquidityUsd * 0.02) {
    return { operable: false, reason: `Position $${positionSizeUsd} > 2% of liquidity` };
  }
  
  const estFees = estimateFees({
    tokenAddress: '',
    symbol: '',
    chain,
    priceUsd: 0,
    liquidityUsd,
    volume24h: liquidityUsd * 0.5,
    marketCap: 0,
    positionSizeUsd,
    expectedGainPct: 5,
    botActivityPct: 30,
    holderCount: 100,
    priceChange24h: 0,
  });
  
  if (estFees.totalCostPct > 5) {
    return { operable: false, reason: `Fees ${estFees.totalCostPct.toFixed(1)}% too high` };
  }
  
  return { operable: true, reason: `Fees: ${estFees.totalCostPct.toFixed(1)}% round-trip` };
}
