/**
 * Project-System Matcher - Assigns the optimal trading system to each token
 * 
 * Key insight: Different tokens need different strategies.
 * - Small-cap meme coin → Alpha Hunter or Micro-Structure
 * - High-liquidity blue chip → Technical or Smart Money
 * - Bot-dominated token → Bot-Aware system
 * - Token in FOMO phase → Defensive + Contrarian
 * - Token in decline → Defensive or Short-focused
 */

import { db } from '@/lib/db';

export interface TokenProfile {
  tokenAddress: string;
  chain: string;
  symbol: string;
  priceUsd: number;
  volume24h: number;
  liquidityUsd: number;
  marketCap: number;
  priceChange1h: number;
  priceChange24h: number;
  botActivityPct: number;
  smartMoneyPct: number;
  operabilityScore: number;
}

export interface SystemMatch {
  tokenAddress: string;
  primarySystem: string;
  secondarySystem?: string;
  multiStrategy: boolean;
  confidence: number;
  reasoning: string[];
  allocationPct: number; // % of capital to allocate
}

// Trading system categories with their dbKey mapping
const SYSTEM_CATEGORIES = {
  ALPHA_HUNTER: { riskLevel: 'high', minLiquidity: 500, maxMarketCap: 1000000, speciality: 'early-stage discovery' },
  SMART_MONEY: { riskLevel: 'medium', minLiquidity: 5000, maxMarketCap: Infinity, speciality: 'whale tracking' },
  TECHNICAL: { riskLevel: 'medium', minLiquidity: 50000, maxMarketCap: Infinity, speciality: 'chart patterns' },
  DEFENSIVE: { riskLevel: 'low', minLiquidity: 10000, maxMarketCap: Infinity, speciality: 'capital preservation' },
  BOT_AWARE: { riskLevel: 'medium', minLiquidity: 1000, maxMarketCap: Infinity, speciality: 'bot-dominated markets' },
  DEEP_ANALYSIS: { riskLevel: 'low', minLiquidity: 100000, maxMarketCap: Infinity, speciality: 'fundamental analysis' },
  MICRO_STRUCTURE: { riskLevel: 'high', minLiquidity: 100, maxMarketCap: 500000, speciality: 'micro-cap trading' },
  ADAPTIVE: { riskLevel: 'variable', minLiquidity: 1000, maxMarketCap: Infinity, speciality: 'market-adaptive' },
} as const;

type SystemKey = keyof typeof SYSTEM_CATEGORIES;

/**
 * Detect token lifecycle phase based on price and volume patterns.
 */
function detectLifecyclePhase(token: TokenProfile): 'incipient' | 'growth' | 'fomo' | 'peak' | 'decline' {
  const { priceChange1h, priceChange24h, volume24h, liquidityUsd, marketCap } = token;
  
  // Incipient: very small, just launched
  if (marketCap < 5000 && liquidityUsd < 500) return 'incipient';
  
  // FOMO: rapid price increase with high volume
  if (priceChange1h > 20 && priceChange24h > 50) return 'fomo';
  
  // Growth: steady positive trend
  if (priceChange1h > 0 && priceChange24h > 5 && priceChange24h < 50) return 'growth';
  
  // Peak: price up but momentum slowing
  if (priceChange24h > 10 && priceChange1h < 2) return 'peak';
  
  // Decline: negative trends
  if (priceChange1h < -5 || priceChange24h < -10) return 'decline';
  
  return 'growth'; // default
}

/**
 * Calculate position allocation based on operability score and risk profile.
 * Higher score + lower risk = larger allocation.
 */
function calculateAllocation(
  operabilityScore: number,
  riskLevel: string,
  totalCapitalUsd: number,
): number {
  // Base allocation: 1-10% of total capital
  let basePct = operabilityScore / 100 * 10; // max 10% for score=100
  
  // Adjust for risk
  if (riskLevel === 'high') basePct *= 0.5;
  if (riskLevel === 'low') basePct *= 1.2;
  
  // Cap at 10% per position
  basePct = Math.min(basePct, 10);
  
  // Minimum 0.5%
  basePct = Math.max(basePct, 0.5);
  
  return Math.round(basePct * 100) / 100;
}

/**
 * Match the best trading system to a token based on its profile.
 */
export function matchSystem(
  token: TokenProfile,
  totalCapitalUsd: number,
): SystemMatch {
  const phase = detectLifecyclePhase(token);
  const reasoning: string[] = [];
  let primarySystem: SystemKey = 'ADAPTIVE';
  let secondarySystem: SystemKey | undefined;
  let multiStrategy = false;

  // Decision logic based on token characteristics
  
  // High bot activity → Bot-Aware system
  if (token.botActivityPct > 50) {
    primarySystem = 'BOT_AWARE';
    reasoning.push(`High bot activity (${token.botActivityPct.toFixed(0)}%) → Bot-Aware system`);
    
    // If also has smart money, add secondary
    if (token.smartMoneyPct > 10) {
      secondarySystem = 'SMART_MONEY';
      multiStrategy = true;
      reasoning.push(`Also detected smart money (${token.smartMoneyPct.toFixed(0)}%) → Multi-strategy`);
    }
  }
  // Smart money present → Smart Money system
  else if (token.smartMoneyPct > 15) {
    primarySystem = 'SMART_MONEY';
    reasoning.push(`Smart money detected (${token.smartMoneyPct.toFixed(0)}%) → Smart Money system`);
  }
  // Very small cap → Alpha Hunter or Micro-Structure
  else if (token.marketCap < 500000 && token.liquidityUsd < 5000) {
    if (token.liquidityUsd < 500) {
      primarySystem = 'ALPHA_HUNTER';
      reasoning.push(`Micro-cap ($${token.marketCap.toFixed(0)} mcap) → Alpha Hunter`);
    } else {
      primarySystem = 'MICRO_STRUCTURE';
      reasoning.push(`Small-cap ($${token.marketCap.toFixed(0)} mcap) → Micro-Structure`);
    }
  }
  // High liquidity → Technical or Deep Analysis
  else if (token.liquidityUsd > 100000) {
    primarySystem = 'TECHNICAL';
    reasoning.push(`High liquidity ($${token.liquidityUsd.toFixed(0)}) → Technical analysis`);
    
    if (token.marketCap > 10000000) {
      secondarySystem = 'DEEP_ANALYSIS';
      multiStrategy = true;
      reasoning.push(`Large cap → adding Deep Analysis`);
    }
  }
  
  // Phase-based adjustments
  if (phase === 'fomo') {
    secondarySystem = 'DEFENSIVE';
    multiStrategy = true;
    reasoning.push(`FOMO phase detected → adding Defensive overlay`);
  } else if (phase === 'decline') {
    // In decline phase, switch to DEFENSIVE as primary (moves current primary to secondary)
    secondarySystem = primarySystem;
    primarySystem = 'DEFENSIVE';
    reasoning.push(`Decline phase → Defensive as primary system`);
  } else if (phase === 'incipient') {
    if (primarySystem !== 'ALPHA_HUNTER') {
      primarySystem = 'ALPHA_HUNTER';
      reasoning.push(`Incipient phase → Alpha Hunter for early discovery`);
    }
  }

  // Calculate confidence based on how well the token fits the system
  const systemProfile = SYSTEM_CATEGORIES[primarySystem];
  let confidence = 0.5;
  
  if (token.liquidityUsd >= systemProfile.minLiquidity) confidence += 0.15;
  if (token.marketCap <= systemProfile.maxMarketCap) confidence += 0.1;
  if (token.operabilityScore > 60) confidence += 0.1;
  if (token.volume24h > 1000) confidence += 0.1;
  if (multiStrategy && secondarySystem) confidence += 0.05;
  
  confidence = Math.min(0.95, confidence);

  const riskLevel = systemProfile.riskLevel;
  const allocationPct = calculateAllocation(token.operabilityScore, riskLevel, totalCapitalUsd);

  reasoning.push(`Lifecycle phase: ${phase}`);
  reasoning.push(`Allocation: ${allocationPct}% of capital ($${(totalCapitalUsd * allocationPct / 100).toFixed(2)})`);

  return {
    tokenAddress: token.tokenAddress,
    primarySystem,
    secondarySystem,
    multiStrategy,
    confidence: Math.round(confidence * 100) / 100,
    reasoning,
    allocationPct,
  };
}

/**
 * Batch match systems for multiple tokens.
 * Returns results sorted by confidence * operability score.
 */
export function batchMatchSystems(
  tokens: TokenProfile[],
  totalCapitalUsd: number,
): SystemMatch[] {
  const tokenScoreMap = new Map(tokens.map(t => [t.tokenAddress, t.operabilityScore]));
  
  return tokens
    .map(t => matchSystem(t, totalCapitalUsd))
    .sort((a, b) => {
      const scoreA = a.confidence * (tokenScoreMap.get(a.tokenAddress) || 0);
      const scoreB = b.confidence * (tokenScoreMap.get(b.tokenAddress) || 0);
      return scoreB - scoreA;
    });
}
