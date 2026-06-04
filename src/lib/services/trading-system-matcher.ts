/**
 * Trading System Matcher - CryptoQuant Terminal
 * 
 * Given a token's lifecycle phase + operability + market context,
 * select the OPTIMAL trading system and configure it for the specific token.
 * 
 * This is where Big Data "thinking" becomes ACTION:
 * - Phase → System selection
 * - Operability → Position sizing
 * - Behavior → Risk parameters
 * - Regime → Confidence adjustment
 * 
 * The matcher can also EVOLVE new systems by combining successful elements
 * from existing systems when no single system fits the current conditions.
 */

import type { TokenAnalysis, UnifiedPhase } from './brain-orchestrator';
import type { OperabilityLevel } from './operability-score';

// ============================================================
// TYPES
// ============================================================

export interface SystemRecommendation {
  tokenAddress: string;
  symbol: string;
  chain: string;
  
  // Which system(s) to use
  primarySystem: string;
  secondarySystems: string[];
  
  // Custom configuration for this specific token
  config: {
    positionSizeUsd: number;
    stopLossPct: number;
    takeProfitPct: number;
    trailingStopPct: number;
    maxPositionPct: number;
    confidence: number;
    
    // Entry conditions (from brain analysis)
    entryConditions: string[];
    
    // Risk adjustments
    riskMultiplier: number;
    allocationMethod: string;
  };
  
  // Why this system was chosen
  reasoning: string[];
  
  // Expected performance estimate
  estimatedWinRate: number;
  estimatedGainPct: number;
  estimatedLossPct: number;
  
  // Should we trade this at all?
  shouldTrade: boolean;
  urgencyLevel: 'IMMEDIATE' | 'HIGH' | 'MEDIUM' | 'LOW' | 'WAIT';
}

export interface SystemEvolutionSuggestion {
  parentSystem: string;
  suggestedName: string;
  targetPhase: UnifiedPhase;
  modifications: {
    stopLossPct: number;
    takeProfitPct: number;
    positionSizeMultiplier: number;
    confidenceThreshold: number;
    timeframe: string;
  };
  reasoning: string;
  expectedImprovement: string;
}

// ============================================================
// SYSTEM-PHASE MATRIX
// ============================================================

/**
 * Which system works best in which phase?
 * Score 0-100: how well the system performs in this phase.
 */
const SYSTEM_PHASE_SCORES: Record<string, Record<UnifiedPhase, number>> = {
  'alpha-hunter': {
    GENESIS: 90, INCIPIENT: 85, GROWTH: 40, FOMO: 15, DECLINE: 5, LEGACY: 5,
  },
  'smart-money': {
    GENESIS: 30, INCIPIENT: 70, GROWTH: 85, FOMO: 60, DECLINE: 30, LEGACY: 20,
  },
  'technical': {
    GENESIS: 20, INCIPIENT: 40, GROWTH: 75, FOMO: 50, DECLINE: 60, LEGACY: 70,
  },
  'defensive': {
    GENESIS: 10, INCIPIENT: 20, GROWTH: 30, FOMO: 50, DECLINE: 85, LEGACY: 80,
  },
  'bot-aware': {
    GENESIS: 70, INCIPIENT: 80, GROWTH: 60, FOMO: 75, DECLINE: 40, LEGACY: 30,
  },
  'deep-research': {
    GENESIS: 50, INCIPIENT: 60, GROWTH: 55, FOMO: 40, DECLINE: 70, LEGACY: 75,
  },
  'micro-cap': {
    GENESIS: 80, INCIPIENT: 75, GROWTH: 50, FOMO: 20, DECLINE: 10, LEGACY: 5,
  },
  'adaptive': {
    GENESIS: 40, INCIPIENT: 50, GROWTH: 70, FOMO: 60, DECLINE: 55, LEGACY: 50,
  },
};

/**
 * How does regime affect system performance?
 * Multiplier: <1 = worse, 1 = neutral, >1 = better
 */
const SYSTEM_REGIME_MULTIPLIER: Record<string, Record<string, number>> = {
  'alpha-hunter': { BULL: 1.3, BEAR: 0.6, SIDEWAYS: 0.8, TRANSITION: 0.9 },
  'smart-money':  { BULL: 1.2, BEAR: 0.7, SIDEWAYS: 1.0, TRANSITION: 0.8 },
  'technical':    { BULL: 1.0, BEAR: 1.0, SIDEWAYS: 1.2, TRANSITION: 0.7 },
  'defensive':    { BULL: 0.8, BEAR: 1.4, SIDEWAYS: 1.1, TRANSITION: 1.2 },
  'bot-aware':    { BULL: 1.0, BEAR: 0.9, SIDEWAYS: 1.0, TRANSITION: 1.1 },
  'deep-research':{ BULL: 1.0, BEAR: 1.2, SIDEWAYS: 1.1, TRANSITION: 1.0 },
  'micro-cap':    { BULL: 1.4, BEAR: 0.5, SIDEWAYS: 0.7, TRANSITION: 0.6 },
  'adaptive':     { BULL: 1.1, BEAR: 1.1, SIDEWAYS: 1.1, TRANSITION: 1.0 },
};

/**
 * Default risk parameters per phase
 */
const PHASE_RISK_DEFAULTS: Record<UnifiedPhase, {
  stopLossPct: number;
  takeProfitPct: number;
  trailingStopPct: number;
  riskMultiplier: number;
}> = {
  GENESIS:   { stopLossPct: 30, takeProfitPct: 200, trailingStopPct: 20, riskMultiplier: 0.3 },
  INCIPIENT: { stopLossPct: 20, takeProfitPct: 100, trailingStopPct: 15, riskMultiplier: 0.5 },
  GROWTH:    { stopLossPct: 12, takeProfitPct: 40,  trailingStopPct: 10, riskMultiplier: 0.8 },
  FOMO:      { stopLossPct: 8,  takeProfitPct: 20,  trailingStopPct: 8,  riskMultiplier: 0.4 },
  DECLINE:   { stopLossPct: 5,  takeProfitPct: 10,  trailingStopPct: 5,  riskMultiplier: 0.2 },
  LEGACY:    { stopLossPct: 5,  takeProfitPct: 8,   trailingStopPct: 3,  riskMultiplier: 0.1 },
};

/**
 * Operability adjustments to risk
 */
const OPERABILITY_RISK_ADJUST: Record<OperabilityLevel, number> = {
  PREMIUM: 1.0,
  GOOD: 0.9,
  MARGINAL: 0.6,
  RISKY: 0.3,
  UNOPERABLE: 0,
};

// ============================================================
// MAIN MATCHER
// ============================================================

/**
 * Match the best trading system for a token based on full brain analysis.
 */
export function matchSystem(analysis: TokenAnalysis): SystemRecommendation {
  const reasoning: string[] = [];
  
  // 1. Score all systems for this token
  const systemScores: Record<string, number> = {};
  
  for (const [system, phaseScores] of Object.entries(SYSTEM_PHASE_SCORES)) {
    // Base score from phase fit
    const phaseScore = phaseScores[analysis.lifecyclePhase] || 0;
    
    // Regime multiplier
    const regimeMult = (SYSTEM_REGIME_MULTIPLIER[system] || {})[analysis.regime] || 1.0;
    
    // Operability adjustment
    const operabilityMult = OPERABILITY_RISK_ADJUST[analysis.operabilityLevel as OperabilityLevel] || 0.5;
    
    // Bot swarm penalty (reduce confidence when bots dominate)
    const botPenalty = analysis.botSwarmLevel === 'CRITICAL' ? 0.4
      : analysis.botSwarmLevel === 'HIGH' ? 0.7
      : 1.0;
    
    // Whale alignment bonus (if whales accumulating and we want to buy)
    const whaleBonus = analysis.whaleDirection === 'ACCUMULATING' && analysis.netBehaviorFlow === 'BULLISH' ? 1.15
      : analysis.whaleDirection === 'DISTRIBUTING' && analysis.netBehaviorFlow === 'BEARISH' ? 1.1
      : 1.0;
    
    // Behavior confidence bonus
    const behaviorBonus = 1 + (analysis.behaviorConfidence * 0.1);
    
    const finalScore = phaseScore * regimeMult * operabilityMult * botPenalty * whaleBonus * behaviorBonus;
    systemScores[system] = Math.round(finalScore);
  }
  
  // 2. Sort systems by score
  const rankedSystems = Object.entries(systemScores)
    .sort(([, a], [, b]) => b - a);
  
  const primarySystem = rankedSystems[0]?.[0] || 'technical';
  const primaryScore = rankedSystems[0]?.[1] || 0;
  const secondarySystems = rankedSystems.slice(1, 3).map(([name]) => name);
  
  reasoning.push(`Primary system: ${primarySystem} (score: ${primaryScore})`);
  reasoning.push(`Phase: ${analysis.lifecyclePhase} | Regime: ${analysis.regime} | Operability: ${analysis.operabilityLevel}`);
  reasoning.push(`Bot swarm: ${analysis.botSwarmLevel} | Whale: ${analysis.whaleDirection} | Behavior: ${analysis.netBehaviorFlow}`);
  
  // 3. Determine position size
  const phaseDefaults = PHASE_RISK_DEFAULTS[analysis.lifecyclePhase];
  const operabilityMult = OPERABILITY_RISK_ADJUST[analysis.operabilityLevel as OperabilityLevel] || 0.5;
  
  const positionSizeUsd = Math.min(
    analysis.recommendedPositionUsd,
    analysis.recommendedPositionUsd * operabilityMult * phaseDefaults.riskMultiplier
  );
  
  // 4. Determine risk parameters
  const riskMultiplier = phaseDefaults.riskMultiplier * operabilityMult;
  const stopLossPct = phaseDefaults.stopLossPct;
  const takeProfitPct = phaseDefaults.takeProfitPct;
  const trailingStopPct = phaseDefaults.trailingStopPct;
  
  // 5. Entry conditions (from analysis)
  const entryConditions: string[] = [];
  
  if (analysis.regime === 'BULL') entryConditions.push('Bull regime confirmed');
  if (analysis.whaleDirection === 'ACCUMULATING') entryConditions.push('Whales accumulating');
  if (analysis.smartMoneyFlow === 'INFLOW') entryConditions.push('Smart money inflow');
  if (analysis.netBehaviorFlow === 'BULLISH') entryConditions.push('Behavioral bullish');
  if (analysis.meanReversionZone) entryConditions.push(`Near mean reversion zone (${(analysis.meanReversionZone.probabilityOfReversion * 100).toFixed(0)}% probability)`);
  if (analysis.lifecyclePhase === 'GROWTH') entryConditions.push('Growth phase - momentum');
  if (analysis.botSwarmLevel === 'NONE' || analysis.botSwarmLevel === 'LOW') entryConditions.push('Low bot activity - safe for retail');
  
  // 6. Estimated performance
  let estimatedWinRate = 0.5; // Base
  if (primaryScore > 70) estimatedWinRate += 0.1;
  else if (primaryScore > 50) estimatedWinRate += 0.05;
  else if (primaryScore < 30) estimatedWinRate -= 0.1;
  
  if (analysis.regime === 'BULL') estimatedWinRate += 0.05;
  if (analysis.behaviorAnomaly) estimatedWinRate -= 0.05;
  if (analysis.botSwarmLevel === 'CRITICAL') estimatedWinRate -= 0.15;
  
  estimatedWinRate = Math.max(0.2, Math.min(0.8, estimatedWinRate));
  
  const estimatedGainPct = takeProfitPct * estimatedWinRate;
  const estimatedLossPct = stopLossPct * (1 - estimatedWinRate);
  
  // 7. Confidence
  const confidence = Math.min(0.95, Math.max(0.1,
    (primaryScore / 100) * 0.5 +
    analysis.lifecycleConfidence * 0.2 +
    analysis.regimeConfidence * 0.15 +
    analysis.behaviorConfidence * 0.15
  ));
  
  // 8. Should we trade?
  const shouldTrade = analysis.isOperable 
    && primaryScore >= 25 
    && analysis.botSwarmLevel !== 'CRITICAL'
    && confidence >= 0.3;
  
  // 9. Urgency
  let urgencyLevel: SystemRecommendation['urgencyLevel'] = 'MEDIUM';
  if (analysis.isTransitioning && analysis.netBehaviorFlow === 'BULLISH') urgencyLevel = 'IMMEDIATE';
  else if (primaryScore > 70 && analysis.regime === 'BULL') urgencyLevel = 'HIGH';
  else if (primaryScore < 40 || analysis.operabilityLevel === 'MARGINAL') urgencyLevel = 'LOW';
  else if (!shouldTrade) urgencyLevel = 'WAIT';
  
  // 10. Allocation method
  const allocationMethod = positionSizeUsd < 50 ? 'FIXED_AMOUNT' 
    : analysis.regime === 'BULL' ? 'KELLY_MODIFIED'
    : analysis.regime === 'BEAR' ? 'MAX_DRAWDOWN_CONTROL'
    : 'RISK_PARITY';
  
  return {
    tokenAddress: analysis.tokenAddress,
    symbol: analysis.symbol,
    chain: analysis.chain,
    primarySystem,
    secondarySystems,
    config: {
      positionSizeUsd: Math.round(positionSizeUsd * 100) / 100,
      stopLossPct,
      takeProfitPct,
      trailingStopPct,
      maxPositionPct: Math.round(riskMultiplier * 100) / 100,
      confidence,
      entryConditions,
      riskMultiplier,
      allocationMethod,
    },
    reasoning,
    estimatedWinRate: Math.round(estimatedWinRate * 1000) / 1000,
    estimatedGainPct: Math.round(estimatedGainPct * 10) / 10,
    estimatedLossPct: Math.round(estimatedLossPct * 10) / 10,
    shouldTrade,
    urgencyLevel,
  };
}

/**
 * Batch match systems for multiple tokens
 */
export function batchMatchSystems(analyses: TokenAnalysis[]): SystemRecommendation[] {
  return analyses
    .map(matchSystem)
    .sort((a, b) => {
      // Sort by: shouldTrade > urgency > confidence
      if (a.shouldTrade !== b.shouldTrade) return a.shouldTrade ? -1 : 1;
      const urgencyOrder = { IMMEDIATE: 0, HIGH: 1, MEDIUM: 2, LOW: 3, WAIT: 4 };
      const urgencyDiff = (urgencyOrder[a.urgencyLevel] || 3) - (urgencyOrder[b.urgencyLevel] || 3);
      if (urgencyDiff !== 0) return urgencyDiff;
      return b.config.confidence - a.config.confidence;
    });
}

/**
 * Suggest system evolutions based on performance gaps.
 * This is where the brain "thinks" about creating new, better systems.
 */
export function suggestEvolutions(
  analysis: TokenAnalysis,
  recentBacktestWinRates: Record<string, number> = {}
): SystemEvolutionSuggestion[] {
  const suggestions: SystemEvolutionSuggestion[] = [];
  
  // If no system scores well for this phase, suggest a specialized one
  const bestScore = Math.max(
    ...Object.values(SYSTEM_PHASE_SCORES).map(scores => scores[analysis.lifecyclePhase] || 0)
  );
  
  if (bestScore < 60) {
    // No system is great for this phase - suggest a specialized one
    const bestSystem = Object.entries(SYSTEM_PHASE_SCORES)
      .sort(([, a], [, b]) => (b[analysis.lifecyclePhase] || 0) - (a[analysis.lifecyclePhase] || 0))[0]?.[0] || 'adaptive';
    
    const phaseDefaults = PHASE_RISK_DEFAULTS[analysis.lifecyclePhase];
    
    suggestions.push({
      parentSystem: bestSystem,
      suggestedName: `${bestSystem}-${analysis.lifecyclePhase.toLowerCase()}-specialist`,
      targetPhase: analysis.lifecyclePhase,
      modifications: {
        stopLossPct: phaseDefaults.stopLossPct,
        takeProfitPct: phaseDefaults.takeProfitPct,
        positionSizeMultiplier: phaseDefaults.riskMultiplier,
        confidenceThreshold: 0.5,
        timeframe: analysis.lifecyclePhase === 'GENESIS' ? '5m' : analysis.lifecyclePhase === 'FOMO' ? '15m' : '1h',
      },
      reasoning: `No existing system scores >60 for ${analysis.lifecyclePhase} phase. Best is ${bestSystem} at ${bestScore}. Creating specialized variant.`,
      expectedImprovement: `Expected +15-25% phase-specific performance improvement`,
    });
  }
  
  // If a system has poor win rate, suggest parameter adjustments
  for (const [system, winRate] of Object.entries(recentBacktestWinRates)) {
    if (winRate < 0.4) {
      suggestions.push({
        parentSystem: system,
        suggestedName: `${system}-conservative`,
        targetPhase: analysis.lifecyclePhase,
        modifications: {
          stopLossPct: 8,
          takeProfitPct: 25,
          positionSizeMultiplier: 0.3,
          confidenceThreshold: 0.7,
          timeframe: '4h',
        },
        reasoning: `System ${system} has ${((winRate) * 100).toFixed(0)}% win rate (<40%). Creating conservative variant with tighter stops and higher confidence threshold.`,
        expectedImprovement: `Expected +10% win rate through stricter entry filtering`,
      });
    }
  }
  
  // If behavioral analysis shows a pattern, suggest a behavioral overlay
  if (analysis.dominantArchetype === 'SMART_MONEY' && analysis.whaleDirection === 'ACCUMULATING') {
    suggestions.push({
      parentSystem: 'smart-money',
      suggestedName: 'smart-money-whale-tail',
      targetPhase: analysis.lifecyclePhase,
      modifications: {
        stopLossPct: 15,
        takeProfitPct: 80,
        positionSizeMultiplier: 1.2,
        confidenceThreshold: 0.4,
        timeframe: '1h',
      },
      reasoning: `Smart money + whale accumulation detected. Creating aggressive follow variant that rides SM coattails with larger position sizing.`,
      expectedImprovement: `Expected +20% profit factor from whale-aligned entries`,
    });
  }
  
  return suggestions;
}
