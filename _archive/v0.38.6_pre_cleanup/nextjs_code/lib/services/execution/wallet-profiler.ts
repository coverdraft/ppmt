/**
 * Wallet Profiler Engine - CryptoQuant Terminal
 * 
 * Builds comprehensive behavioral profiles for each wallet/trader.
 * Calculates Smart Money Score, Whale Score, Sniper Score,
 * behavioral patterns, and cross-chain correlations.
 */

export interface WalletProfile {
  address: string;
  chain: string;
  
  // Core Classification
  primaryLabel: string;
  labelConfidence: number;
  
  // Scores (0-100)
  smartMoneyScore: number;
  whaleScore: number;
  sniperScore: number;
  botProbability: number;
  
  // Behavioral Archetypes
  patterns: BehavioralPattern[];
  
  // Risk Assessment
  riskLevel: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  riskFactors: string[];
  
  // Trading Profile Summary
  profileSummary: string;
}

export interface BehavioralPattern {
  pattern: string;
  confidence: number;
  description: string;
  dataPoints: number;
}

export interface TraderAnalytics {
  // Trade history metrics
  totalTrades: number;
  winRate: number;
  avgPnlUsd: number;
  totalPnlUsd: number;
  avgHoldTimeMin: number;
  avgTradeSizeUsd: number;
  
  // Entry timing metrics
  avgEntryRank: number;
  earlyEntryCount: number;
  avgExitMultiplier: number;
  
  // Wallet characteristics
  totalHoldingsUsd: number;
  uniqueTokensTraded: number;
  preferredDexes: string[];
  preferredChains: string[];
  
  // Advanced metrics
  sharpeRatio: number;
  profitFactor: number;
  maxDrawdown: number;
  consistencyScore: number;
  
  // Behavioral indicators
  washTradeScore: number;
  copyTradeScore: number;
  frontrunCount: number;
  sandwichCount: number;
  
  // Timing
  tradingHourPattern: number[];
  isActive247: boolean;
  avgTimeBetweenTradesMin: number;
}

// ============================================================
// SMART MONEY SCORING ENGINE
// ============================================================

/**
 * Calculate Smart Money Score (0-100)
 * 
 * A wallet is considered "Smart Money" if it:
 * 1. Consistently enters early (low avgEntryRank)
 * 2. Has high win rate (>60%)
 * 3. Exits at significant multipliers
 * 4. Has strong risk-adjusted returns (Sharpe > 1)
 * 5. Makes large, conviction-sized trades
 * 6. Is NOT a bot (verified human-like patterns)
 */
export function calculateSmartMoneyScore(analytics: TraderAnalytics): number {
  let score = 0;
  
  // Win rate component (0-25 points)
  if (analytics.winRate > 0.7) score += 25;
  else if (analytics.winRate > 0.6) score += 20;
  else if (analytics.winRate > 0.5) score += 10;
  else if (analytics.winRate > 0.4) score += 5;
  
  // Entry timing (0-25 points) - early entry is crucial
  if (analytics.avgEntryRank < 10) score += 25;
  else if (analytics.avgEntryRank < 50) score += 20;
  else if (analytics.avgEntryRank < 200) score += 10;
  else if (analytics.avgEntryRank < 1000) score += 5;
  
  // Risk-adjusted returns (0-20 points)
  if (analytics.sharpeRatio > 2) score += 20;
  else if (analytics.sharpeRatio > 1.5) score += 15;
  else if (analytics.sharpeRatio > 1) score += 10;
  else if (analytics.sharpeRatio > 0.5) score += 5;
  
  // Exit efficiency (0-15 points)
  if (analytics.avgExitMultiplier > 5) score += 15;
  else if (analytics.avgExitMultiplier > 3) score += 12;
  else if (analytics.avgExitMultiplier > 2) score += 8;
  else if (analytics.avgExitMultiplier > 1.5) score += 4;
  
  // Profit factor (0-10 points)
  if (analytics.profitFactor > 2) score += 10;
  else if (analytics.profitFactor > 1.5) score += 7;
  else if (analytics.profitFactor > 1.2) score += 4;
  else if (analytics.profitFactor > 1) score += 2;
  
  // Consistency (0-5 points)
  if (analytics.consistencyScore > 0.7) score += 5;
  else if (analytics.consistencyScore > 0.5) score += 3;
  else if (analytics.consistencyScore > 0.3) score += 1;
  
  // Penalize bot-like behavior
  if (analytics.washTradeScore > 0.5) score = Math.max(0, score - 30);
  if (analytics.copyTradeScore > 0.7) score = Math.max(0, score - 20);
  if (analytics.isActive247 && analytics.avgTimeBetweenTradesMin < 1) score = Math.max(0, score - 15);
  
  return Math.min(100, Math.max(0, score));
}

// ============================================================
// WHALE SCORING ENGINE
// ============================================================

/**
 * Calculate Whale Score (0-100)
 * 
 * A wallet is a "Whale" if it:
 * 1. Has large total holdings
 * 2. Makes large individual trades
 * 3. Causes significant price impact
 * 4. Holds concentrated positions
 */
export function calculateWhaleScore(analytics: TraderAnalytics): number {
  let score = 0;
  
  // Total holdings (0-40 points)
  if (analytics.totalHoldingsUsd > 10_000_000) score += 40;
  else if (analytics.totalHoldingsUsd > 1_000_000) score += 30;
  else if (analytics.totalHoldingsUsd > 500_000) score += 20;
  else if (analytics.totalHoldingsUsd > 100_000) score += 10;
  else if (analytics.totalHoldingsUsd > 10_000) score += 5;
  
  // Average trade size (0-30 points)
  if (analytics.avgTradeSizeUsd > 100_000) score += 30;
  else if (analytics.avgTradeSizeUsd > 50_000) score += 22;
  else if (analytics.avgTradeSizeUsd > 10_000) score += 15;
  else if (analytics.avgTradeSizeUsd > 5_000) score += 8;
  else if (analytics.avgTradeSizeUsd > 1_000) score += 3;
  
  // Profit factor (0-15 points) - whales tend to be profitable
  if (analytics.profitFactor > 2) score += 15;
  else if (analytics.profitFactor > 1.5) score += 10;
  else if (analytics.profitFactor > 1) score += 5;
  
  // Total PnL (0-15 points)
  if (analytics.totalPnlUsd > 1_000_000) score += 15;
  else if (analytics.totalPnlUsd > 100_000) score += 10;
  else if (analytics.totalPnlUsd > 10_000) score += 5;
  
  return Math.min(100, Math.max(0, score));
}

// ============================================================
// SNIPER SCORING ENGINE
// ============================================================

/**
 * Calculate Sniper Score (0-100)
 * 
 * A wallet is a "Sniper" if it:
 * 1. Enters tokens within first few blocks
 * 2. Has very low avgEntryRank
 * 3. Dumps quickly after entry
 * 4. Has many early entries
 * 5. May be bot-assisted
 */
export function calculateSniperScore(analytics: TraderAnalytics): number {
  let score = 0;
  
  // Average entry rank (0-35 points)
  if (analytics.avgEntryRank < 5) score += 35;
  else if (analytics.avgEntryRank < 20) score += 25;
  else if (analytics.avgEntryRank < 50) score += 15;
  else if (analytics.avgEntryRank < 100) score += 8;
  
  // Early entry count (0-25 points)
  if (analytics.earlyEntryCount > 20) score += 25;
  else if (analytics.earlyEntryCount > 10) score += 18;
  else if (analytics.earlyEntryCount > 5) score += 10;
  else if (analytics.earlyEntryCount > 2) score += 5;
  
  // Short hold time (0-20 points) - snipers dump fast
  if (analytics.avgHoldTimeMin < 5) score += 20;
  else if (analytics.avgHoldTimeMin < 30) score += 12;
  else if (analytics.avgHoldTimeMin < 120) score += 5;
  
  // 24/7 activity (0-10 points) - automated sniping
  if (analytics.isActive247) score += 10;
  else if (analytics.avgTimeBetweenTradesMin < 2) score += 5;
  
  // High trade volume (0-10 points)
  if (analytics.totalTrades > 500) score += 10;
  else if (analytics.totalTrades > 200) score += 6;
  else if (analytics.totalTrades > 50) score += 3;
  
  return Math.min(100, Math.max(0, score));
}

// ============================================================
// BEHAVIORAL PATTERN DETECTION
// ============================================================

export function detectBehavioralPatterns(analytics: TraderAnalytics): BehavioralPattern[] {
  const patterns: BehavioralPattern[] = [];
  
  // Accumulator: buys slowly over time, high hold time
  if (analytics.avgHoldTimeMin > 1440 && analytics.winRate > 0.5 && analytics.avgTradeSizeUsd > 1000) {
    patterns.push({
      pattern: 'ACCUMULATOR',
      confidence: Math.min(0.95, analytics.avgHoldTimeMin / 10080),
      description: 'Systematically accumulates positions over extended periods',
      dataPoints: analytics.totalTrades,
    });
  }
  
  // Dumper: sells quickly after entry, usually at loss
  if (analytics.avgHoldTimeMin < 30 && analytics.winRate < 0.4 && analytics.totalTrades > 20) {
    patterns.push({
      pattern: 'DUMPER',
      confidence: Math.min(0.9, (1 - analytics.winRate) * (30 / (analytics.avgHoldTimeMin || 1))),
      description: 'Panic sells quickly, usually realizing losses',
      dataPoints: analytics.totalTrades,
    });
  }
  
  // Scalper: very short holds, high frequency, variable win rate
  if (analytics.avgHoldTimeMin < 15 && analytics.totalTrades > 100) {
    patterns.push({
      pattern: 'SCALPER',
      confidence: Math.min(0.9, analytics.totalTrades / 500),
      description: 'Extremely short-term trades capturing small price movements',
      dataPoints: analytics.totalTrades,
    });
  }
  
  // Swing Trader: moderate holds, decent win rate
  if (analytics.avgHoldTimeMin > 60 && analytics.avgHoldTimeMin < 4320 && analytics.winRate > 0.45) {
    patterns.push({
      pattern: 'SWING_TRADER',
      confidence: 0.7,
      description: 'Holds positions for hours to days, capturing swing moves',
      dataPoints: analytics.totalTrades,
    });
  }
  
  // Diamond Hands: very long holds
  if (analytics.avgHoldTimeMin > 4320 && analytics.totalTrades > 10) {
    patterns.push({
      pattern: 'DIAMOND_HANDS',
      confidence: Math.min(0.9, analytics.avgHoldTimeMin / 21600),
      description: 'Long-term holder, rarely sells regardless of volatility',
      dataPoints: analytics.totalTrades,
    });
  }
  
  // Momentum Rider: follows price trends
  if (analytics.profitFactor > 1.3 && analytics.sharpeRatio > 0.8 && analytics.avgHoldTimeMin < 1440) {
    patterns.push({
      pattern: 'MOMENTUM_RIDER',
      confidence: Math.min(0.85, analytics.sharpeRatio / 3),
      description: 'Rides momentum trends with disciplined entries and exits',
      dataPoints: analytics.totalTrades,
    });
  }
  
  // Contrarian: buys dips, sells rips
  if (analytics.winRate > 0.55 && analytics.profitFactor > 1.5 && analytics.avgHoldTimeMin > 120) {
    patterns.push({
      pattern: 'CONTRARIAN',
      confidence: 0.65,
      description: 'Buys during fear, sells during greed - counter-trend trader',
      dataPoints: analytics.totalTrades,
    });
  }
  
  // Wash Trader: circular trading
  if (analytics.washTradeScore > 0.4) {
    patterns.push({
      pattern: 'WASH_TRADER',
      confidence: Math.min(0.95, analytics.washTradeScore),
      description: 'Likely creating artificial volume through circular trading',
      dataPoints: analytics.totalTrades,
    });
  }
  
  // Copy Cat: follows other wallets
  if (analytics.copyTradeScore > 0.5) {
    patterns.push({
      pattern: 'COPY_CAT',
      confidence: Math.min(0.9, analytics.copyTradeScore),
      description: 'Follows trades of other wallets, likely using copy trading tools',
      dataPoints: analytics.totalTrades,
    });
  }
  
  // MEV Extractor
  if (analytics.frontrunCount > 5 || analytics.sandwichCount > 3) {
    patterns.push({
      pattern: 'MEV_EXTRACTOR',
      confidence: Math.min(0.95, (analytics.frontrunCount + analytics.sandwichCount * 2) / 20),
      description: 'Extracts value through frontrunning and sandwich attacks',
      dataPoints: analytics.frontrunCount + analytics.sandwichCount,
    });
  }
  
  // Bridge Hopper: trades across multiple chains
  if (analytics.preferredChains.length > 2) {
    patterns.push({
      pattern: 'BRIDGE_HOPPER',
      confidence: Math.min(0.8, analytics.preferredChains.length / 5),
      description: 'Actively trades across multiple chains via bridges',
      dataPoints: analytics.totalTrades,
    });
  }
  
  // Yield Farmer
  if (analytics.preferredDexes.length > 3 && analytics.avgHoldTimeMin > 10080) {
    patterns.push({
      pattern: 'YIELD_FARMER',
      confidence: 0.6,
      description: 'Primarily farms yield across DeFi protocols',
      dataPoints: analytics.totalTrades,
    });
  }
  
  return patterns.sort((a, b) => b.confidence - a.confidence);
}

// ============================================================
// RISK ASSESSMENT
// ============================================================

export function assessWalletRisk(
  analytics: TraderAnalytics,
  smartMoneyScore: number,
  whaleScore: number,
  sniperScore: number
): { level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'; factors: string[] } {
  const factors: string[] = [];
  let riskScore = 0;
  
  // Bot indicators add risk
  if (analytics.isActive247) {
    riskScore += 25;
    factors.push('24/7 automated trading detected');
  }
  
  if (analytics.washTradeScore > 0.5) {
    riskScore += 30;
    factors.push('High wash trading probability');
  }
  
  if (analytics.copyTradeScore > 0.7) {
    riskScore += 15;
    factors.push('High copy trading behavior');
  }
  
  if (analytics.frontrunCount > 5) {
    riskScore += 20;
    factors.push('Multiple frontrunning incidents');
  }
  
  if (analytics.sandwichCount > 3) {
    riskScore += 20;
    factors.push('Sandwich attack involvement');
  }
  
  // Sniper behavior adds risk for followers
  if (sniperScore > 60) {
    riskScore += 15;
    factors.push('Aggressive sniper behavior');
  }
  
  // Very low win rate
  if (analytics.winRate < 0.3 && analytics.totalTrades > 20) {
    riskScore += 10;
    factors.push('Consistently losing trader');
  }
  
  // Negative PnL
  if (analytics.totalPnlUsd < -10000) {
    riskScore += 10;
    factors.push('Significant cumulative losses');
  }
  
  let level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  if (riskScore >= 60) level = 'CRITICAL';
  else if (riskScore >= 40) level = 'HIGH';
  else if (riskScore >= 20) level = 'MEDIUM';
  else level = 'LOW';
  
  return { level, factors };
}

// ============================================================
// FULL PROFILE BUILDER
// ============================================================

export function buildWalletProfile(
  address: string,
  chain: string,
  analytics: TraderAnalytics
): WalletProfile {
  // Calculate all scores
  const smartMoneyScore = calculateSmartMoneyScore(analytics);
  const whaleScore = calculateWhaleScore(analytics);
  const sniperScore = calculateSniperScore(analytics);
  
  // Determine primary label based on highest score
  const scores = [
    { label: 'SMART_MONEY', score: smartMoneyScore },
    { label: 'WHALE', score: whaleScore },
    { label: 'SNIPER', score: sniperScore },
  ].sort((a, b) => b.score - a.score);
  
  // Check for bot indicators
  const botProbability = analytics.isActive247 ? 0.7
    : analytics.washTradeScore > 0.5 ? 0.6
    : analytics.copyTradeScore > 0.7 ? 0.5
    : analytics.avgTimeBetweenTradesMin < 0.5 ? 0.4
    : 0;
  
  // Determine primary label
  let primaryLabel = scores[0].label;
  if (botProbability > 0.5) {
    primaryLabel = analytics.washTradeScore > 0.5 ? 'BOT_WASH'
      : analytics.copyTradeScore > 0.5 ? 'BOT_COPY'
      : 'BOT_MEV';
  }
  
  if (analytics.avgHoldTimeMin < 5 && analytics.totalTrades > 100 && botProbability < 0.5) {
    primaryLabel = 'RETAIL';
  }
  
  const labelConfidence = Math.max(scores[0].score / 100, botProbability);
  
  // Detect patterns
  const patterns = detectBehavioralPatterns(analytics);
  
  // Risk assessment
  const { level: riskLevel, factors: riskFactors } = assessWalletRisk(
    analytics,
    smartMoneyScore,
    whaleScore,
    sniperScore
  );
  
  // Build summary
  const profileSummary = generateProfileSummary(
    address,
    primaryLabel,
    smartMoneyScore,
    whaleScore,
    sniperScore,
    botProbability,
    analytics,
    patterns
  );
  
  return {
    address,
    chain,
    primaryLabel,
    labelConfidence,
    smartMoneyScore,
    whaleScore,
    sniperScore,
    botProbability,
    patterns,
    riskLevel,
    riskFactors,
    profileSummary,
  };
}

function generateProfileSummary(
  address: string,
  label: string,
  smScore: number,
  whaleScore: number,
  sniperScore: number,
  botProb: number,
  analytics: TraderAnalytics,
  patterns: BehavioralPattern[]
): string {
  const shortAddr = `${address.slice(0, 6)}...${address.slice(-4)}`;
  const topPattern = patterns[0]?.pattern || 'UNKNOWN';
  const winPct = (analytics.winRate * 100).toFixed(0);
  const pnl = analytics.totalPnlUsd >= 0
    ? `+$${analytics.totalPnlUsd.toFixed(0)}`
    : `-$${Math.abs(analytics.totalPnlUsd).toFixed(0)}`;
  
  return `${shortAddr} | ${label} | SM:${smScore} WH:${whaleScore} SN:${sniperScore} | Bot:${(botProb * 100).toFixed(0)}% | ${topPattern} | WR:${winPct}% | PnL:${pnl} | ${analytics.totalTrades} trades | Hold:${analytics.avgHoldTimeMin.toFixed(0)}min`;
}

// ============================================================
// CROSS-CHAIN CORRELATION
// ============================================================

export interface CrossChainCorrelation {
  primaryAddress: string;
  primaryChain: string;
  linkedAddress: string;
  linkedChain: string;
  linkType: string;
  confidence: number;
  evidence: string[];
}

/**
 * Detect cross-chain wallet correlations
 * Based on: bridge transactions, timing patterns, similar trading behavior
 */
export function detectCrossChainCorrelation(
  trader1: TraderAnalytics & { address: string; chain: string },
  trader2: TraderAnalytics & { address: string; chain: string }
): CrossChainCorrelation | null {
  if (trader1.chain === trader2.chain) return null; // Same chain, not cross-chain
  
  const evidence: string[] = [];
  let confidence = 0;
  
  // Similar trading hour patterns
  const hourCorrelation = calculatePatternCorrelation(
    trader1.tradingHourPattern,
    trader2.tradingHourPattern
  );
  if (hourCorrelation > 0.8) {
    confidence += 0.25;
    evidence.push(`Very similar trading hours (correlation: ${hourCorrelation.toFixed(2)})`);
  } else if (hourCorrelation > 0.6) {
    confidence += 0.1;
    evidence.push(`Similar trading hours (correlation: ${hourCorrelation.toFixed(2)})`);
  }
  
  // Similar win rate
  if (Math.abs(trader1.winRate - trader2.winRate) < 0.05) {
    confidence += 0.15;
    evidence.push(`Nearly identical win rates: ${(trader1.winRate * 100).toFixed(1)}% vs ${(trader2.winRate * 100).toFixed(1)}%`);
  }
  
  // Similar average hold time
  if (Math.abs(trader1.avgHoldTimeMin - trader2.avgHoldTimeMin) / (trader1.avgHoldTimeMin || 1) < 0.2) {
    confidence += 0.1;
    evidence.push('Similar hold time patterns');
  }
  
  // Same preferred DEXes (cross-chain)
  const sharedDexes = trader1.preferredDexes.filter(d => trader2.preferredDexes.includes(d));
  if (sharedDexes.length > 0) {
    confidence += 0.1;
    evidence.push(`Shared DEXes: ${sharedDexes.join(', ')}`);
  }
  
  // Same consistency score (bots have similar patterns across chains)
  if (Math.abs(trader1.consistencyScore - trader2.consistencyScore) < 0.1 && trader1.consistencyScore > 0.7) {
    confidence += 0.15;
    evidence.push('Very similar consistency scores (automated behavior)');
  }
  
  if (confidence < 0.3) return null; // Not enough evidence
  
  return {
    primaryAddress: trader1.address,
    primaryChain: trader1.chain,
    linkedAddress: trader2.address,
    linkedChain: trader2.chain,
    linkType: confidence > 0.6 ? 'SAME_ENTITY' : 'LIKELY_LINKED',
    confidence,
    evidence,
  };
}

function calculatePatternCorrelation(a: number[], b: number[]): number {
  if (a.length !== b.length || a.length === 0) return 0;
  
  const n = a.length;
  const meanA = a.reduce((s, v) => s + v, 0) / n;
  const meanB = b.reduce((s, v) => s + v, 0) / n;
  
  let numerator = 0;
  let denomA = 0;
  let denomB = 0;
  
  for (let i = 0; i < n; i++) {
    const diffA = a[i] - meanA;
    const diffB = b[i] - meanB;
    numerator += diffA * diffB;
    denomA += diffA * diffA;
    denomB += diffB * diffB;
  }
  
  const denominator = Math.sqrt(denomA * denomB);
  return denominator === 0 ? 0 : numerator / denominator;
}
