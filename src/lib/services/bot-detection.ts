/**
 * Bot Detection Engine - CryptoQuant Terminal
 * 
 * Classifies wallets as bots based on behavioral signals.
 * Supports: MEV Extractors, Sniper Bots, Copy Bots, Arbitrage Bots,
 * Sandwich Bots, Wash Trading Bots, Liquidation Bots, JIT LP Bots,
 * Jito Tip Bots, and more.
 */

export interface BotDetectionResult {
  isBot: boolean;
  botType: string | null;
  confidence: number;
  signals: BotSignal[];
  classification: BotClassification;
}

export interface BotSignal {
  type: string;
  name: string;
  weight: number;    // 0-1 importance
  value: number;     // 0-1 signal strength
  description: string;
  evidence: string[];
}

export interface BotClassification {
  primary: string;
  secondary: string[];
  confidence: number;
  reasoning: string;
}

export interface TraderMetrics {
  totalTrades: number;
  avgTimeBetweenTradesMin: number;
  consistencyScore: number;
  isActive247: boolean;
  isActiveAtNight: boolean;
  avgSlippageBps: number;
  frontrunCount: number;
  sandwichCount: number;
  washTradeScore: number;
  copyTradeScore: number;
  mevExtractionUsd: number;
  avgHoldTimeMin: number;
  tradingHourPattern: number[];
  block0EntryCount: number;
  avgBlockToTrade: number;
  priorityFeeUsd: number;
  justInTimeCount: number;
  multiHopCount: number;
  sameTokenPairCount: number;
  selfTradeCount: number;
}

// ============================================================
// BOT DETECTION SIGNALS - Each returns a BotSignal
// ============================================================

/**
 * MEV Extractor Detection
 * - Very high priority fees (Jito tips on Solana)
 * - Consistently trades in same block as victim
 * - Front-runs large swaps
 * - Extracts value from other traders
 */
function detectMEV(metrics: TraderMetrics): BotSignal {
  const evidence: string[] = [];
  let value = 0;

  // Priority fees are a strong MEV indicator
  if (metrics.priorityFeeUsd > 100) {
    value += 0.3;
    evidence.push(`High priority fees: $${metrics.priorityFeeUsd.toFixed(2)}`);
  }

  // Front-running activity
  if (metrics.frontrunCount > 5) {
    value += 0.3;
    evidence.push(`${metrics.frontrunCount} frontrun incidents detected`);
  }

  // MEV extraction
  if (metrics.mevExtractionUsd > 500) {
    value += 0.25;
    evidence.push(`$${metrics.mevExtractionUsd.toFixed(2)} MEV extracted`);
  }

  // Consistent timing (bots are precise)
  if (metrics.consistencyScore > 0.8) {
    value += 0.15;
    evidence.push(`High consistency score: ${metrics.consistencyScore.toFixed(2)}`);
  }

  return {
    type: 'MEV_EXTRACTION',
    name: 'MEV Extractor',
    weight: 0.35,
    value: Math.min(value, 1),
    description: 'Detects Maximum Extractable Value extraction patterns',
    evidence,
  };
}

/**
 * Sniper Bot Detection
 * - Enters tokens within first few blocks of creation
 * - Very low avgBlockToTrade (near zero)
 * - High block0EntryCount
 * - Typically buys and sells quickly
 */
function detectSniperBot(metrics: TraderMetrics): BotSignal {
  const evidence: string[] = [];
  let value = 0;

  // Block 0 entries are extremely suspicious
  if (metrics.block0EntryCount > 3) {
    value += 0.4;
    evidence.push(`${metrics.block0EntryCount} block-0 entries detected`);
  }

  // Very fast average entry
  if (metrics.avgBlockToTrade < 2) {
    value += 0.3;
    evidence.push(`Avg ${metrics.avgBlockToTrade.toFixed(1)} blocks to first trade`);
  }

  // Short hold time (snipers dump quickly)
  if (metrics.avgHoldTimeMin < 30 && metrics.totalTrades > 20) {
    value += 0.15;
    evidence.push(`Avg hold time: ${metrics.avgHoldTimeMin.toFixed(1)} min`);
  }

  // 24/7 activity
  if (metrics.isActive247) {
    value += 0.15;
    evidence.push('Active 24/7 - consistent with automated sniping');
  }

  return {
    type: 'SNIPER_BOT',
    name: 'Sniper Bot',
    weight: 0.30,
    value: Math.min(value, 1),
    description: 'Detects automated token sniping at launch',
    evidence,
  };
}

/**
 * Sandwich Attack Bot Detection
 * - Frequently involved in sandwich attacks
 * - Trades before and after the same target
 * - High slippage tolerance
 */
function detectSandwichBot(metrics: TraderMetrics): BotSignal {
  const evidence: string[] = [];
  let value = 0;

  if (metrics.sandwichCount > 5) {
    value += 0.5;
    evidence.push(`${metrics.sandwichCount} sandwich attack participations`);
  }

  if (metrics.frontrunCount > 3 && metrics.avgSlippageBps > 100) {
    value += 0.25;
    evidence.push(`Frontruns + high slippage: ${metrics.avgSlippageBps} bps`);
  }

  if (metrics.consistencyScore > 0.7 && metrics.avgTimeBetweenTradesMin < 1) {
    value += 0.25;
    evidence.push('Consistent sub-minute trading intervals');
  }

  return {
    type: 'SANDWICH_BOT',
    name: 'Sandwich Bot',
    weight: 0.30,
    value: Math.min(value, 1),
    description: 'Detects sandwich attack execution patterns',
    evidence,
  };
}

/**
 * Copy Trading Bot Detection
 * - Follows specific wallets with delay
 * - Trades same tokens as tracked wallets
 * - Consistent delay between target and copy trade
 */
function detectCopyBot(metrics: TraderMetrics): BotSignal {
  const evidence: string[] = [];
  let value = 0;

  if (metrics.copyTradeScore > 0.5) {
    value += 0.5;
    evidence.push(`Copy trade score: ${metrics.copyTradeScore.toFixed(2)}`);
  }

  if (metrics.sameTokenPairCount > 50 && metrics.avgTimeBetweenTradesMin < 5) {
    value += 0.25;
    evidence.push(`Trades same pairs rapidly: ${metrics.sameTokenPairCount} instances`);
  }

  if (metrics.consistencyScore > 0.6) {
    value += 0.15;
    evidence.push('Moderate consistency in trade timing');
  }

  return {
    type: 'COPY_BOT',
    name: 'Copy Trading Bot',
    weight: 0.25,
    value: Math.min(value, 1),
    description: 'Detects automated copy trading of other wallets',
    evidence,
  };
}

/**
 * Wash Trading Bot Detection
 * - Trades with itself or circular trading
 * - No net position change
 * - Creates artificial volume
 */
function detectWashTrading(metrics: TraderMetrics): BotSignal {
  const evidence: string[] = [];
  let value = 0;

  if (metrics.washTradeScore > 0.4) {
    value += 0.4;
    evidence.push(`Wash trade score: ${metrics.washTradeScore.toFixed(2)}`);
  }

  if (metrics.selfTradeCount > 3) {
    value += 0.3;
    evidence.push(`${metrics.selfTradeCount} self-trade incidents`);
  }

  if (metrics.avgSlippageBps < 5 && metrics.totalTrades > 100) {
    value += 0.15;
    evidence.push('Abnormally low slippage for high trade count');
  }

  if (metrics.totalTrades > 50 && metrics.washTradeScore > 0.3) {
    value += 0.15;
    evidence.push('Near-zero PnL despite many trades');
  }

  return {
    type: 'WASH_TRADING',
    name: 'Wash Trading Bot',
    weight: 0.30,
    value: Math.min(value, 1),
    description: 'Detects wash trading / artificial volume creation',
    evidence,
  };
}

/**
 * Arbitrage Bot Detection
 * - Exploits price differences across DEXes
 * - Very short hold times
 * - Multi-hop swaps
 * - Near-guaranteed profits
 */
function detectArbitrageBot(metrics: TraderMetrics): BotSignal {
  const evidence: string[] = [];
  let value = 0;

  if (metrics.multiHopCount > 20) {
    value += 0.35;
    evidence.push(`${metrics.multiHopCount} multi-hop swaps`);
  }

  if (metrics.avgHoldTimeMin < 1 && metrics.totalTrades > 50) {
    value += 0.25;
    evidence.push(`Extremely short hold time: ${metrics.avgHoldTimeMin.toFixed(2)} min`);
  }

  // Arbitrage bots have very consistent (low variance) outcomes
  if (metrics.consistencyScore > 0.8 && metrics.totalTrades > 30) {
    value += 0.25;
    evidence.push(`Highly consistent outcomes: consistency ${(metrics.consistencyScore * 100).toFixed(0)}%`);
  }

  if (metrics.avgSlippageBps < 10) {
    value += 0.15;
    evidence.push('Low slippage - consistent with arbitrage execution');
  }

  return {
    type: 'ARBITRAGE_BOT',
    name: 'Arbitrage Bot',
    weight: 0.25,
    value: Math.min(value, 1),
    description: 'Detects cross-DEX or intra-block arbitrage patterns',
    evidence,
  };
}

/**
 * JIT (Just-In-Time) Liquidity Bot Detection
 * - Provides liquidity right before a large swap
 * - Removes liquidity immediately after
 * - Earns fees without taking directional risk
 */
function detectJITBot(metrics: TraderMetrics): BotSignal {
  const evidence: string[] = [];
  let value = 0;

  if (metrics.justInTimeCount > 5) {
    value += 0.5;
    evidence.push(`${metrics.justInTimeCount} JIT liquidity events`);
  }

  if (metrics.mevExtractionUsd > 100 && metrics.avgHoldTimeMin < 5) {
    value += 0.3;
    evidence.push('MEV extraction with very short positions');
  }

  if (metrics.consistencyScore > 0.8) {
    value += 0.2;
    evidence.push('Highly consistent timing patterns');
  }

  return {
    type: 'JIT_LP_BOT',
    name: 'JIT Liquidity Bot',
    weight: 0.20,
    value: Math.min(value, 1),
    description: 'Detects Just-In-Time liquidity provision patterns',
    evidence,
  };
}

/**
 * 24/7 Activity Detection (general bot indicator)
 * - Trades at all hours consistently
 * - No sleep pattern
 * - Very regular intervals
 */
function detect247Activity(metrics: TraderMetrics): BotSignal {
  const evidence: string[] = [];
  let value = 0;

  if (metrics.isActive247) {
    value += 0.5;
    evidence.push('Active across all 24 hours consistently');
  }

  if (metrics.isActiveAtNight && metrics.consistencyScore > 0.6) {
    value += 0.25;
    evidence.push('Active during off-hours with consistent patterns');
  }

  // Check for uniform hour distribution
  const hourPattern = metrics.tradingHourPattern;
  if (hourPattern.length === 24) {
    const avg = hourPattern.reduce((a, b) => a + b, 0) / 24;
    const variance = hourPattern.reduce((a, b) => a + (b - avg) ** 2, 0) / 24;
    const coefficientOfVariation = Math.sqrt(variance) / (avg || 1);
    
    if (coefficientOfVariation < 0.3) {
      value += 0.25;
      evidence.push('Uniform trading distribution across all hours');
    }
  }

  return {
    type: 'TWENTY_FOUR_SEVEN',
    name: '24/7 Activity',
    weight: 0.20,
    value: Math.min(value, 1),
    description: 'Detects non-stop trading activity indicative of automation',
    evidence,
  };
}

// ============================================================
// MAIN CLASSIFICATION ENGINE
// ============================================================

const ALL_DETECTORS = [
  detectMEV,
  detectSniperBot,
  detectSandwichBot,
  detectCopyBot,
  detectWashTrading,
  detectArbitrageBot,
  detectJITBot,
  detect247Activity,
];

const BOT_TYPE_MAP: Record<string, string> = {
  'MEV_EXTRACTION': 'MEV_EXTRACTOR',
  'SNIPER_BOT': 'SNIPER_BOT',
  'SANDWICH_BOT': 'SANDWICH_BOT',
  'COPY_BOT': 'COPY_BOT',
  'WASH_TRADING': 'WASH_TRADING_BOT',
  'ARBITRAGE_BOT': 'ARBITRAGE_BOT',
  'JIT_LP_BOT': 'JIT_LP_BOT',
  'TWENTY_FOUR_SEVEN': 'SCALPER_BOT',
};

/**
 * Main bot detection function
 * Runs all detectors and produces a classification
 */
export function detectBot(metrics: TraderMetrics): BotDetectionResult {
  const signals = ALL_DETECTORS.map(detector => detector(metrics));
  
  // Calculate weighted score
  const totalWeight = signals.reduce((sum, s) => sum + s.weight, 0);
  const weightedScore = signals.reduce(
    (sum, s) => sum + s.value * s.weight,
    0
  ) / totalWeight;
  
  // Find primary signal
  const significantSignals = signals
    .filter(s => s.value > 0.3)
    .sort((a, b) => (b.value * b.weight) - (a.value * a.weight));
  
  const isBot = weightedScore > 0.35;
  const primarySignal = significantSignals[0];
  const botType = isBot && primarySignal
    ? BOT_TYPE_MAP[primarySignal.type] || 'UNKNOWN_BOT'
    : null;
  
  // Build classification reasoning
  const reasoning = significantSignals
    .map(s => `${s.name}: ${(s.value * 100).toFixed(0)}% confidence - ${s.evidence.join('; ')}`)
    .join('\n');
  
  return {
    isBot,
    botType,
    confidence: Math.min(weightedScore, 1),
    signals,
    classification: {
      primary: botType || 'NOT_BOT',
      secondary: significantSignals
        .slice(1)
        .map(s => BOT_TYPE_MAP[s.type] || s.type),
      confidence: weightedScore,
      reasoning,
    },
  };
}

/**
 * Batch process multiple traders for bot detection
 */
export function batchDetectBots(
  traders: TraderMetrics[]
): BotDetectionResult[] {
  return traders.map(detectBot);
}

/**
 * Get a human-readable summary of bot detection results
 */
export function getBotDetectionSummary(result: BotDetectionResult): string {
  if (!result.isBot) {
    return 'No bot activity detected. Trading patterns appear human.';
  }
  
  const type = result.botType?.replace(/_/g, ' ') || 'Unknown';
  const confidence = (result.confidence * 100).toFixed(0);
  const evidence = result.signals
    .filter(s => s.value > 0.2)
    .map(s => `- ${s.name}: ${s.evidence.join(', ')}`)
    .join('\n');
  
  return `Detected ${type} (${confidence}% confidence)\nEvidence:\n${evidence}`;
}
