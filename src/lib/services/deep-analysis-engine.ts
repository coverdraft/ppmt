/**
 * Deep Analysis Engine - CryptoQuant Terminal
 * Motor de Análisis Profundo con LLM (z-ai-sdk) + Fallback Rule-Based
 *
 * Three genuinely different analysis depths:
 *   QUICK: Simple rule-based scan, 3 factors, no scenarios, basic recommendation
 *   STANDARD: Weighted analysis, 6 factors, 3 scenarios, multi-factor recommendation
 *   DEEP: Full bayesian risk, 12 factors, 5 scenarios + stress test, detailed entry/exit,
 *         phase transitions, whale narratives, bot impact, invalidation levels
 */

import { db } from '../db';
import { type PatternScanResult } from './candlestick-pattern-engine';
import { type BehavioralPrediction } from './behavioral-model-engine';
import { type TokenAnalysis } from './brain-orchestrator';
import { TokenPhase } from './token-lifecycle-engine';

// ============================================================
// TYPES
// ============================================================

export type AnalysisDepth = 'QUICK' | 'STANDARD' | 'DEEP';
export type ThinkingDepth = AnalysisDepth; // Alias used by pipeline and UI
export type RiskLevel = 'VERY_LOW' | 'LOW' | 'MEDIUM' | 'HIGH' | 'VERY_HIGH' | 'EXTREME';
export type ActionRecommendation = 'STRONG_BUY' | 'BUY' | 'HOLD' | 'REDUCE' | 'SELL' | 'AVOID' | 'STRONG_SELL' | 'WAIT';

/** Extended input type used by the brain-analysis-pipeline */
export interface AnalysisInput {
  tokenAddress: string;
  symbol: string;
  chain: string;
  currentPrice: number;
  priceChange24h: number;
  regime: string;
  regimeConfidence: number;
  lifecyclePhase: string;
  lifecycleConfidence: number;
  netBehaviorFlow: string;
  botSwarmLevel: string;
  whaleDirection: string;
  operabilityScore: number;
  patternScan?: PatternScanResult;
  crossCorrelation?: Record<string, unknown>;
  dataReliability: {
    sampleSufficiency: string;
    totalCorrelationSamples: number;
    reliableCombinations: number;
  };
  candles1h: number;
  candles5m: number;
  tradersAnalyzed: number;
  signalsGenerated: number;
}

/** Rich deep analysis type used by the UI (deep-analysis-panel) */
export interface DeepAnalysis {
  tokenAddress: string;
  symbol: string;
  chain: string;
  depth: ThinkingDepth;
  analyzedAt: Date;

  // Phase assessment
  phaseAssessment: {
    phase: string;
    confidence: number;
    timeInPhase: string;
    narrative: string;
  };

  // Pattern assessment
  patternAssessment: {
    dominantPattern: string | null;
    patternSentiment: string;
    multiTfConfirmed: boolean;
    narrative: string;
  };

  // Trader assessment
  traderAssessment: {
    dominantArchetype: string;
    behaviorFlow: string;
    riskFromBots: string;
    riskFromWhales: string;
    narrative: string;
  };

  // Verdict
  verdict: {
    action: string;
    confidence: number;
    reasoning: string;
    summary?: string;
    criticalNote?: string;
  };

  // Risk assessment
  riskAssessment: {
    overallRisk: string;
    keyRisks: string[];
    mitigatingFactors: string[];
    blackSwanRisk: string;
  };

  // Strategy recommendation
  strategyRecommendation: {
    strategy: string;
    direction: string;
    confidenceLevel: number;
    positionSizeRecommendation: string;
    stopLossRecommendation: string;
    takeProfitRecommendation: string;
    entryConditions: string[];
    exitConditions: string[];
  };

  // Evidence matrix
  pros: Array<{ factor: string; weight: number; explanation: string }>;
  cons: Array<{ factor: string; weight: number; explanation: string }>;
  neutrals: Array<{ factor: string; weight: number; explanation: string }>;

  // Reasoning
  reasoningChain: string[];

  // Timestamp (from DeepAnalysisResult)
  timestamp?: Date;
}

export interface ExtendedScenario {
  name: string;
  probability: number;
  targetPct: number;
  description: string;
}

export interface DeepAnalysisResult {
  tokenAddress: string;
  symbol: string;
  chain: string;
  analyzedAt: Date;
  depth: AnalysisDepth;
  source: 'LLM' | 'RULE_BASED' | 'HYBRID';

  // Narrative
  summary: string;
  riskAssessment: string;
  recommendation: ActionRecommendation;
  recommendationConfidence: number; // 0-1
  justification: string[];

  // Key factors
  bullishFactors: string[];
  bearishFactors: string[];
  neutralFactors: string[];
  keyMonitorPoints: string[];

  // Scenarios (3 for STANDARD, 5 for DEEP, 0 for QUICK)
  scenarios: {
    bull: { probability: number; targetPct: number; description: string };
    base: { probability: number; targetPct: number; description: string };
    bear: { probability: number; targetPct: number; description: string };
  };

  // Extended scenarios (DEEP only: 5 scenarios + stress test)
  extendedScenarios?: ExtendedScenario[];
  stressTestResults?: {
    blackSwanPct: number;
    liquidityCrashPct: number;
    flashCrashRecoveryHours: number;
    maxDrawdownEstimate: number;
  };

  // Risk
  riskLevel: RiskLevel;
  riskScore: number; // 0-100
  maxRecommendedPositionPct: number;

  // DEEP-only fields
  detailedAnalysis?: {
    phaseTransitionProbability: number;
    whaleAccumulationNarrative: string;
    botSwarmImpactAnalysis: string;
    invalidationLevels: string[];
    keyAssumptions: string[];
    riskAdjustedPositionSizing: {
      conservativePct: number;
      moderatePct: number;
      aggressivePct: number;
      kellyFraction: number;
    };
  };

  // Timing
  suggestedTimeHorizon: string;
  urgencyLevel: 'IMMEDIATE' | 'HIGH' | 'MEDIUM' | 'LOW';

  // LLM raw output (if available)
  llmRawAnalysis?: string;
}

export interface DeepAnalysisInput {
  tokenAddress: string;
  symbol: string;
  chain: string;
  brainAnalysis: TokenAnalysis;
  patternScan?: PatternScanResult;
  behavioralPrediction?: BehavioralPrediction;
  depth?: AnalysisDepth;
}

// ============================================================
// RISK SCORING CONSTANTS
// ============================================================

const PHASE_RISK: Record<string, number> = {
  GENESIS: 85, INCIPIENT: 70, GROWTH: 40, FOMO: 60, DECLINE: 75, LEGACY: 50,
};

const REGIME_RISK: Record<string, number> = {
  BULL: 25, SIDEWAYS: 45, TRANSITION: 60, BEAR: 80,
};

const BOT_SWARM_RISK: Record<string, number> = {
  NONE: 0, LOW: 15, MEDIUM: 35, HIGH: 55, CRITICAL: 80,
};

// Weighted risk component weights per depth
const QUICK_WEIGHTS = { phase: 0.33, regime: 0.33, bot: 0.34 };
const STANDARD_WEIGHTS = { phase: 0.25, regime: 0.25, bot: 0.15, operability: 0.15, whale: 0.10, smartMoney: 0.10 };
const DEEP_WEIGHTS = { phase: 0.15, regime: 0.15, bot: 0.10, operability: 0.10, whale: 0.10, smartMoney: 0.10, anomaly: 0.10, transition: 0.10, volatility: 0.05, correlation: 0.05 };

// ============================================================
// QUICK MODE: Simple average risk, basic threshold recommendation
// ============================================================

function quickAnalysis(input: DeepAnalysisInput): DeepAnalysisResult {
  const { brainAnalysis: brain, patternScan, symbol, chain, tokenAddress } = input;

  const bullishFactors: string[] = [];
  const bearishFactors: string[] = [];
  const neutralFactors: string[] = [];
  const justification: string[] = [];

  const phaseRisk = PHASE_RISK[brain.lifecyclePhase] ?? 50;
  const regimeRisk = REGIME_RISK[brain.regime] ?? 50;
  const botRisk = BOT_SWARM_RISK[brain.botSwarmLevel] ?? 0;

  // Simple average risk scoring
  const riskScore = Math.max(0, Math.min(100, (phaseRisk + regimeRisk + botRisk) / 3));

  // Quick factor collection — max 3 each
  if (['GROWTH', 'LEGACY'].includes(brain.lifecyclePhase)) {
    bullishFactors.push(`${brain.lifecyclePhase} lifecycle phase`);
  } else if (['GENESIS', 'DECLINE'].includes(brain.lifecyclePhase)) {
    bearishFactors.push(`${brain.lifecyclePhase} lifecycle phase`);
  }

  if (brain.regime === 'BULL') bullishFactors.push('Bull market regime');
  else if (brain.regime === 'BEAR') bearishFactors.push('Bear market regime');

  if (brain.botSwarmLevel === 'CRITICAL' || brain.botSwarmLevel === 'HIGH') {
    bearishFactors.push(`${brain.botSwarmLevel} bot activity`);
  }

  if (brain.whaleDirection === 'ACCUMULATING') bullishFactors.push('Whale accumulation');
  else if (brain.whaleDirection === 'DISTRIBUTING') bearishFactors.push('Whale distribution');

  if (brain.smartMoneyFlow === 'INFLOW') bullishFactors.push('Smart money inflow');
  else if (brain.smartMoneyFlow === 'OUTFLOW') bearishFactors.push('Smart money outflow');

  if (brain.operabilityLevel === 'PREMIUM' || brain.operabilityLevel === 'GOOD') {
    bullishFactors.push(`Good operability (${brain.operabilityScore}/100)`);
  } else if (brain.operabilityLevel === 'RISKY' || brain.operabilityLevel === 'UNOPERABLE') {
    bearishFactors.push(`Poor operability (${brain.operabilityScore}/100)`);
  }

  // Simple threshold recommendation
  const netFactors = bullishFactors.length - bearishFactors.length;
  let recommendation: ActionRecommendation;
  let recommendationConfidence: number;

  if (riskScore <= 30 && netFactors >= 2) {
    recommendation = 'BUY';
    recommendationConfidence = 0.65;
  } else if (riskScore <= 50 && netFactors >= 1) {
    recommendation = 'HOLD';
    recommendationConfidence = 0.5;
  } else if (riskScore > 70) {
    recommendation = 'AVOID';
    recommendationConfidence = 0.75;
  } else if (netFactors <= -1) {
    recommendation = 'REDUCE';
    recommendationConfidence = 0.55;
  } else {
    recommendation = 'HOLD';
    recommendationConfidence = 0.45;
  }

  const riskLevel = riskScore <= 20 ? 'VERY_LOW' : riskScore <= 40 ? 'LOW' : riskScore <= 60 ? 'MEDIUM' : riskScore <= 80 ? 'HIGH' : 'VERY_HIGH';
  const maxPosition = riskScore <= 30 ? 8 : riskScore <= 50 ? 5 : riskScore <= 70 ? 3 : 1;

  justification.push(`Risk: ${riskLevel} (${riskScore.toFixed(0)}/100) | ${bullishFactors.length} bull / ${bearishFactors.length} bear`);

  const summary = `${symbol || tokenAddress.slice(0,8)}: ${recommendation} (risk: ${riskLevel}, confidence: ${(recommendationConfidence * 100).toFixed(0)}%). `
    + `${bullishFactors.length} bull / ${bearishFactors.length} bear factors.`;

  const riskAssessment = `Risk: ${riskLevel} (${riskScore.toFixed(0)}/100). Max position: ${maxPosition}%.`;

  const timeHorizon = brain.lifecyclePhase === 'GENESIS' ? '1-4 hours'
    : brain.lifecyclePhase === 'GROWTH' ? '1-3 days'
    : brain.lifecyclePhase === 'FOMO' ? '2-8 hours'
    : '1-7 days';

  const urgency: DeepAnalysisResult['urgencyLevel'] = brain.isTransitioning ? 'HIGH' : netFactors >= 2 || netFactors <= -2 ? 'MEDIUM' : 'LOW';

  return {
    tokenAddress,
    symbol: symbol || '',
    chain,
    analyzedAt: new Date(),
    depth: 'QUICK',
    source: 'RULE_BASED',
    summary,
    riskAssessment,
    recommendation,
    recommendationConfidence,
    justification: justification.slice(0, 2),
    bullishFactors: bullishFactors.slice(0, 3),
    bearishFactors: bearishFactors.slice(0, 3),
    neutralFactors: neutralFactors.slice(0, 1),
    keyMonitorPoints: [],
    scenarios: {
      bull: { probability: 0, targetPct: 0, description: '' },
      base: { probability: 0, targetPct: 0, description: '' },
      bear: { probability: 0, targetPct: 0, description: '' },
    },
    riskLevel,
    riskScore,
    maxRecommendedPositionPct: maxPosition,
    suggestedTimeHorizon: timeHorizon,
    urgencyLevel: urgency,
  };
}

// ============================================================
// STANDARD MODE: Weighted risk, 3 scenarios, multi-factor recommendation
// ============================================================

function standardAnalysis(input: DeepAnalysisInput): DeepAnalysisResult {
  const { brainAnalysis: brain, patternScan, behavioralPrediction: behavior, symbol, chain, tokenAddress } = input;

  const bullishFactors: string[] = [];
  const bearishFactors: string[] = [];
  const neutralFactors: string[] = [];
  const justification: string[] = [];
  const keyMonitorPoints: string[] = [];

  const phaseRisk = PHASE_RISK[brain.lifecyclePhase] ?? 50;
  const regimeRisk = REGIME_RISK[brain.regime] ?? 50;
  const botRisk = BOT_SWARM_RISK[brain.botSwarmLevel] ?? 0;
  const operabilityRisk = brain.operabilityScore >= 60 ? 10 : brain.operabilityScore >= 40 ? 30 : brain.operabilityScore >= 20 ? 55 : 80;
  const whaleRisk = brain.whaleDirection === 'DISTRIBUTING' ? 65 : brain.whaleDirection === 'ACCUMULATING' ? 20 : 40;
  const smartMoneyRisk = brain.smartMoneyFlow === 'OUTFLOW' ? 60 : brain.smartMoneyFlow === 'INFLOW' ? 25 : 40;

  // Weighted risk scoring
  const riskScore = Math.max(0, Math.min(100,
    phaseRisk * STANDARD_WEIGHTS.phase +
    regimeRisk * STANDARD_WEIGHTS.regime +
    botRisk * STANDARD_WEIGHTS.bot +
    operabilityRisk * STANDARD_WEIGHTS.operability +
    whaleRisk * STANDARD_WEIGHTS.whale +
    smartMoneyRisk * STANDARD_WEIGHTS.smartMoney
  ));

  // Factor collection — max 6 each
  if (['GROWTH', 'LEGACY'].includes(brain.lifecyclePhase)) {
    bullishFactors.push(`${brain.lifecyclePhase} lifecycle phase (favorable for upside)`);
  } else if (['GENESIS', 'DECLINE'].includes(brain.lifecyclePhase)) {
    bearishFactors.push(`${brain.lifecyclePhase} lifecycle phase (higher risk/uncertainty)`);
  } else {
    neutralFactors.push(`${brain.lifecyclePhase} phase - neutral positioning`);
  }

  if (brain.regime === 'BULL') {
    bullishFactors.push(`Bull market regime (${(brain.regimeConfidence * 100).toFixed(0)}% confidence)`);
  } else if (brain.regime === 'BEAR') {
    bearishFactors.push(`Bear market regime (${(brain.regimeConfidence * 100).toFixed(0)}% confidence)`);
  }

  // Pattern analysis from candlestick-pattern-engine
  if (patternScan) {
    if (patternScan.overallSignal === 'BULLISH') {
      bullishFactors.push(`Candlestick patterns: ${patternScan.bullishPatterns.length} bullish (score: ${patternScan.overallScore.toFixed(2)})`);
      if (patternScan.confluences.length > 0) {
        bullishFactors.push(`Pattern confluences: ${patternScan.confluences.map(c => c.pattern).join(', ')}`);
      }
    } else if (patternScan.overallSignal === 'BEARISH') {
      bearishFactors.push(`Candlestick patterns: ${patternScan.bearishPatterns.length} bearish (score: ${patternScan.overallScore.toFixed(2)})`);
    } else {
      neutralFactors.push(`Candlestick patterns: mixed/neutral (${patternScan.patterns.length} total)`);
    }
    if (patternScan.dominantPattern) {
      keyMonitorPoints.push(`Watch ${patternScan.dominantPattern} on ${patternScan.dominantTimeframe}`);
    }
  }

  // Behavioral prediction integration
  if (behavior) {
    if (behavior.netFlowDirection === 'BULLISH') {
      bullishFactors.push(`Trader behavior: net bullish flow (score: ${behavior.netFlowScore.toFixed(2)})`);
    } else if (behavior.netFlowDirection === 'BEARISH') {
      bearishFactors.push(`Trader behavior: net bearish flow (score: ${behavior.netFlowScore.toFixed(2)})`);
    }
    if (behavior.archetypeBreakdown.length > 0) {
      const topArch = behavior.archetypeBreakdown[0];
      keyMonitorPoints.push(`Top archetype: ${topArch.archetype} (${topArch.dominantAction})`);
    }
  }

  // Whale & smart money
  if (brain.whaleDirection === 'ACCUMULATING') {
    bullishFactors.push(`Whales accumulating (${(brain.whaleConfidence * 100).toFixed(0)}% confidence)`);
  } else if (brain.whaleDirection === 'DISTRIBUTING') {
    bearishFactors.push(`Whales distributing (${(brain.whaleConfidence * 100).toFixed(0)}% confidence)`);
  }
  if (brain.smartMoneyFlow === 'INFLOW') bullishFactors.push('Smart money inflow detected');
  else if (brain.smartMoneyFlow === 'OUTFLOW') bearishFactors.push('Smart money outflow detected');

  // Bot swarm
  if (brain.botSwarmLevel === 'CRITICAL' || brain.botSwarmLevel === 'HIGH') {
    bearishFactors.push(`${brain.botSwarmLevel} bot swarm activity`);
    keyMonitorPoints.push('Monitor bot activity - increases slippage risk');
  }

  // Operability
  if (brain.operabilityLevel === 'PREMIUM' || brain.operabilityLevel === 'GOOD') {
    bullishFactors.push(`Operability: ${brain.operabilityLevel} (${brain.operabilityScore}/100)`);
  } else if (brain.operabilityLevel === 'RISKY' || brain.operabilityLevel === 'UNOPERABLE') {
    bearishFactors.push(`Low operability: ${brain.operabilityLevel}`);
  }

  // Multi-factor recommendation
  const bullCount = bullishFactors.length;
  const bearCount = bearishFactors.length;
  const netFactors = bullCount - bearCount;
  let recommendation: ActionRecommendation;
  let recommendationConfidence: number;

  if (riskScore <= 30 && netFactors >= 2) {
    recommendation = 'STRONG_BUY';
    recommendationConfidence = 0.75;
  } else if (riskScore <= 40 && netFactors >= 1) {
    recommendation = 'BUY';
    recommendationConfidence = 0.65;
  } else if (riskScore <= 55 && Math.abs(netFactors) <= 1) {
    recommendation = 'HOLD';
    recommendationConfidence = 0.5;
  } else if (riskScore <= 70 && netFactors <= -1) {
    recommendation = 'REDUCE';
    recommendationConfidence = 0.6;
  } else if (riskScore <= 85 && netFactors <= -2) {
    recommendation = 'SELL';
    recommendationConfidence = 0.7;
  } else if (riskScore > 85) {
    recommendation = 'AVOID';
    recommendationConfidence = 0.8;
  } else {
    recommendation = 'HOLD';
    recommendationConfidence = 0.45;
  }

  // Confidence adjustments based on data quality
  if (patternScan) recommendationConfidence = Math.min(1, recommendationConfidence + 0.08);
  if (behavior) recommendationConfidence = Math.min(1, recommendationConfidence + 0.08);
  if (brain.regimeConfidence > 0.7) recommendationConfidence = Math.min(1, recommendationConfidence + 0.05);

  const riskLevel = riskScore <= 20 ? 'VERY_LOW' : riskScore <= 40 ? 'LOW' : riskScore <= 60 ? 'MEDIUM' : riskScore <= 80 ? 'HIGH' : 'VERY_HIGH';
  const maxPosition = riskScore <= 20 ? 10 : riskScore <= 40 ? 7 : riskScore <= 60 ? 5 : riskScore <= 80 ? 3 : 1;

  justification.push(`Risk: ${riskLevel} (${riskScore.toFixed(0)}/100) | Bull: ${bullCount} | Bear: ${bearCount}`);
  justification.push(`Phase: ${brain.lifecyclePhase} | Regime: ${brain.regime} | Bots: ${brain.botSwarmLevel}`);

  // 3 Scenarios with probabilities
  const bullProb = Math.max(0.05, Math.min(0.7, 0.3 + netFactors * 0.06 - riskScore * 0.003));
  const bearProb = Math.max(0.05, Math.min(0.7, 0.3 - netFactors * 0.06 + riskScore * 0.003));
  const baseProb = Math.max(0.1, 1 - bullProb - bearProb);

  const bullTarget = brain.lifecyclePhase === 'GROWTH' ? 25 : brain.lifecyclePhase === 'FOMO' ? 40 : 15;
  const bearTarget = brain.regime === 'BEAR' ? -30 : brain.lifecyclePhase === 'DECLINE' ? -25 : -15;
  const baseTarget = bullTarget * 0.2 + bearTarget * 0.2;

  const timeHorizon = brain.lifecyclePhase === 'GENESIS' ? '1-4 hours'
    : brain.lifecyclePhase === 'INCIPIENT' ? '4-12 hours'
    : brain.lifecyclePhase === 'GROWTH' ? '1-3 days'
    : brain.lifecyclePhase === 'FOMO' ? '2-8 hours'
    : brain.lifecyclePhase === 'DECLINE' ? '1-7 days'
    : '1-14 days';

  const urgency: DeepAnalysisResult['urgencyLevel'] =
    (patternScan?.confluences.length ?? 0) > 0 ? 'IMMEDIATE'
    : brain.isTransitioning ? 'HIGH'
    : brain.anomalyDetected ? 'HIGH'
    : netFactors >= 2 || netFactors <= -2 ? 'MEDIUM' : 'LOW';

  const summary = `${symbol || tokenAddress.slice(0,8)} is in ${brain.lifecyclePhase} phase within a ${brain.regime} regime. `
    + `${bullCount} bullish factors vs ${bearCount} bearish factors. `
    + `Overall risk: ${riskLevel}. Recommendation: ${recommendation}.`
    + (patternScan ? ` Candlestick patterns suggest ${patternScan.overallSignal} bias.` : '')
    + (behavior ? ` Trader behavior: ${behavior.netFlowDirection}.` : '');

  const riskAssessment = `Risk level: ${riskLevel} (${riskScore.toFixed(0)}/100). `
    + `Phase risk: ${phaseRisk.toFixed(0)}/100, Regime risk: ${regimeRisk.toFixed(0)}/100, Bot risk: ${botRisk.toFixed(0)}/100. `
    + `Operability: ${brain.operabilityLevel} (${brain.operabilityScore}/100). `
    + `Maximum recommended position: ${maxPosition}% of capital.`;

  return {
    tokenAddress,
    symbol: symbol || '',
    chain,
    analyzedAt: new Date(),
    depth: 'STANDARD',
    source: 'RULE_BASED',
    summary,
    riskAssessment,
    recommendation,
    recommendationConfidence,
    justification: justification.slice(0, 5),
    bullishFactors: bullishFactors.slice(0, 6),
    bearishFactors: bearishFactors.slice(0, 6),
    neutralFactors: neutralFactors.slice(0, 3),
    keyMonitorPoints: keyMonitorPoints.slice(0, 4),
    scenarios: {
      bull: { probability: bullProb, targetPct: bullTarget, description: `Strong momentum continues, ${bullTarget}%+ upside` },
      base: { probability: baseProb, targetPct: baseTarget, description: `Sideways consolidation, ${baseTarget > 0 ? '+' : ''}${baseTarget}% move` },
      bear: { probability: bearProb, targetPct: bearTarget, description: `Deterioration, ${bearTarget}% decline` },
    },
    riskLevel,
    riskScore,
    maxRecommendedPositionPct: maxPosition,
    suggestedTimeHorizon: timeHorizon,
    urgencyLevel: urgency,
  };
}

// ============================================================
// DEEP MODE: Full bayesian risk, 5 scenarios + stress test, 12 factors
// ============================================================

function deepAnalysis(input: DeepAnalysisInput): DeepAnalysisResult {
  const { brainAnalysis: brain, patternScan, behavioralPrediction: behavior, symbol, chain, tokenAddress } = input;

  const bullishFactors: string[] = [];
  const bearishFactors: string[] = [];
  const neutralFactors: string[] = [];
  const justification: string[] = [];
  const keyMonitorPoints: string[] = [];

  const phaseRisk = PHASE_RISK[brain.lifecyclePhase] ?? 50;
  const regimeRisk = REGIME_RISK[brain.regime] ?? 50;
  const botRisk = BOT_SWARM_RISK[brain.botSwarmLevel] ?? 0;
  const operabilityRisk = brain.operabilityScore >= 60 ? 10 : brain.operabilityScore >= 40 ? 30 : brain.operabilityScore >= 20 ? 55 : 80;
  const whaleRisk = brain.whaleDirection === 'DISTRIBUTING' ? 65 : brain.whaleDirection === 'ACCUMULATING' ? 20 : 40;
  const smartMoneyRisk = brain.smartMoneyFlow === 'OUTFLOW' ? 60 : brain.smartMoneyFlow === 'INFLOW' ? 25 : 40;
  const anomalyRisk = brain.anomalyDetected ? 55 + brain.anomalyScore * 20 : 20;
  const transitionRisk = brain.isTransitioning ? 65 : 15;
  const volatilityRisk = (brain as any).volatilityRegime === 'HIGH' ? 70 : (brain as any).volatilityRegime === 'EXTREME' ? 85 : 30;

  // Full bayesian-inspired risk scoring with prior adjustments
  // Prior: start from a base of 40 (moderate), then adjust with evidence
  const priorRisk = 40;
  const evidenceLikelihood = (
    phaseRisk * DEEP_WEIGHTS.phase +
    regimeRisk * DEEP_WEIGHTS.regime +
    botRisk * DEEP_WEIGHTS.bot +
    operabilityRisk * DEEP_WEIGHTS.operability +
    whaleRisk * DEEP_WEIGHTS.whale +
    smartMoneyRisk * DEEP_WEIGHTS.smartMoney +
    anomalyRisk * DEEP_WEIGHTS.anomaly +
    transitionRisk * DEEP_WEIGHTS.transition +
    volatilityRisk * DEEP_WEIGHTS.volatility
  );
  // Bayesian update: posterior = (prior * 0.3) + (evidence * 0.7) — evidence dominates
  let riskScore = Math.max(0, Math.min(100, priorRisk * 0.3 + evidenceLikelihood * 0.7));

  // Prior adjustment: if data reliability is low, increase uncertainty (push toward 50)
  const dataPenalty = (brain as any).dataReliability?.sampleSufficiency === 'INSUFFICIENT' ? 8 : 0;
  riskScore = riskScore + (50 - riskScore) * (dataPenalty / 100) * 2; // pull toward 50

  // Factor collection — up to 12 each with detailed explanations
  // Phase factors
  if (['GROWTH', 'LEGACY'].includes(brain.lifecyclePhase)) {
    bullishFactors.push(`${brain.lifecyclePhase} lifecycle phase (favorable for upside)`);
    bullishFactors.push(`Phase momentum: ${brain.lifecyclePhase} tokens historically show ${brain.lifecyclePhase === 'GROWTH' ? 'sustained uptrend' : 'stability'}`);
  } else if (['GENESIS', 'DECLINE'].includes(brain.lifecyclePhase)) {
    bearishFactors.push(`${brain.lifecyclePhase} lifecycle phase (higher risk/uncertainty)`);
    bearishFactors.push(`${brain.lifecyclePhase} phase tokens typically experience ${brain.lifecyclePhase === 'GENESIS' ? 'extreme volatility in first hours' : 'prolonged downtrend pressure'}`);
  } else {
    neutralFactors.push(`${brain.lifecyclePhase} phase - neutral positioning`);
    neutralFactors.push(`INCIPIENT/FOMO phases often precede significant moves; direction depends on volume confirmation`);
  }

  // Regime factors
  if (brain.regime === 'BULL') {
    bullishFactors.push(`Bull market regime with ${(brain.regimeConfidence * 100).toFixed(0)}% confidence`);
    bullishFactors.push(`Bull regime tailwind: historical data shows ${brain.regimeConfidence > 0.7 ? 'strong' : 'moderate'} upside bias in this regime`);
  } else if (brain.regime === 'BEAR') {
    bearishFactors.push(`Bear market regime with ${(brain.regimeConfidence * 100).toFixed(0)}% confidence`);
    bearishFactors.push(`Bear regime headwind: defensive positioning recommended; ${brain.regimeConfidence > 0.7 ? 'high' : 'moderate'} conviction of continued downside`);
  } else if (brain.regime === 'TRANSITION') {
    neutralFactors.push('Transition regime detected - market direction uncertain, reduced position sizing advisable');
  }

  // Full pattern analysis from candlestick-pattern-engine
  if (patternScan) {
    if (patternScan.overallSignal === 'BULLISH') {
      bullishFactors.push(`Candlestick patterns: ${patternScan.bullishPatterns.length} bullish signals (score: ${patternScan.overallScore.toFixed(2)})`);
      if (patternScan.confluences.length > 0) {
        bullishFactors.push(`Pattern confluences: ${patternScan.confluences.map(c => c.pattern).join(', ')}`);
      }
      for (const bp of patternScan.bullishPatterns.slice(0, 3)) {
        bullishFactors.push(`Bullish pattern: ${bp.pattern} on ${bp.timeframe} (confidence: ${(bp.confidence * 100).toFixed(0)}%)`);
      }
    } else if (patternScan.overallSignal === 'BEARISH') {
      bearishFactors.push(`Candlestick patterns: ${patternScan.bearishPatterns.length} bearish signals (score: ${patternScan.overallScore.toFixed(2)})`);
      for (const bp of patternScan.bearishPatterns.slice(0, 3)) {
        bearishFactors.push(`Bearish pattern: ${bp.pattern} on ${bp.timeframe} (confidence: ${(bp.confidence * 100).toFixed(0)}%)`);
      }
    } else {
      neutralFactors.push(`Candlestick patterns: mixed/neutral (${patternScan.patterns.length} total)`);
    }
    if (patternScan.dominantPattern) {
      keyMonitorPoints.push(`Watch for ${patternScan.dominantPattern} pattern continuation or invalidation on ${patternScan.dominantTimeframe}`);
      keyMonitorPoints.push(`Pattern invalidation level: if price breaks ${patternScan.overallSignal === 'BULLISH' ? 'below support' : 'above resistance'}, pattern fails`);
    }
  }

  // Behavioral prediction integration
  if (behavior) {
    if (behavior.netFlowDirection === 'BULLISH') {
      bullishFactors.push(`Trader behavior: net bullish flow (score: ${behavior.netFlowScore.toFixed(2)})`);
      bullishFactors.push(`Flow momentum: ${behavior.netFlowScore > 0.7 ? 'strong' : 'moderate'} bullish conviction with ${(behavior.confidence * 100).toFixed(0)}% prediction reliability`);
    } else if (behavior.netFlowDirection === 'BEARISH') {
      bearishFactors.push(`Trader behavior: net bearish flow (score: ${behavior.netFlowScore.toFixed(2)})`);
      bearishFactors.push(`Flow momentum: ${behavior.netFlowScore < -0.7 ? 'strong' : 'moderate'} bearish conviction with ${(behavior.confidence * 100).toFixed(0)}% prediction reliability`);
    }
    if (behavior.archetypeBreakdown.length > 0) {
      const topArch = behavior.archetypeBreakdown[0];
      keyMonitorPoints.push(`Top trader archetype: ${topArch.archetype} (${topArch.dominantAction}, ${topArch.volumeShare.toFixed(0)}% volume)`);
      if (behavior.archetypeBreakdown.length > 1) {
        const secondArch = behavior.archetypeBreakdown[1];
        keyMonitorPoints.push(`Secondary archetype: ${secondArch.archetype} (${secondArch.dominantAction}, ${secondArch.volumeShare.toFixed(0)}% volume)`);
      }
    }
    if (behavior.confidence > 0.7) {
      justification.push(`High behavioral prediction confidence (${(behavior.confidence * 100).toFixed(0)}%)`);
    }
    for (const arch of behavior.archetypeBreakdown.slice(0, 3)) {
      justification.push(`[${arch.archetype}] ${arch.dominantAction} - ${(arch.volumeShare * 100).toFixed(1)}% volume share`);
    }
  }

  // Whale & smart money — with accumulation narratives
  if (brain.whaleDirection === 'ACCUMULATING') {
    bullishFactors.push(`Whales accumulating (${(brain.whaleConfidence * 100).toFixed(0)}% confidence)`);
    bullishFactors.push(`Whale accumulation pattern: ${brain.whaleConfidence > 0.7 ? 'Large wallets consistently adding positions' : 'Moderate whale buying activity observed'}`);
  } else if (brain.whaleDirection === 'DISTRIBUTING') {
    bearishFactors.push(`Whales distributing (${(brain.whaleConfidence * 100).toFixed(0)}% confidence)`);
    bearishFactors.push(`Whale distribution alert: ${brain.whaleConfidence > 0.7 ? 'Large wallets actively selling into strength' : 'Some large wallet reduction observed'}`);
  }
  if (brain.smartMoneyFlow === 'INFLOW') {
    bullishFactors.push('Smart money inflow detected');
    bullishFactors.push('Smart money entry typically precedes significant price moves - monitor for acceleration');
  } else if (brain.smartMoneyFlow === 'OUTFLOW') {
    bearishFactors.push('Smart money outflow detected');
    bearishFactors.push('Smart money exit often signals impending correction - consider reducing exposure');
  }

  // Bot swarm — with impact analysis
  if (brain.botSwarmLevel === 'CRITICAL' || brain.botSwarmLevel === 'HIGH') {
    bearishFactors.push(`${brain.botSwarmLevel} bot swarm activity - retail front-running risk`);
    bearishFactors.push(`Bot swarm impact: ${brain.botSwarmLevel === 'CRITICAL' ? 'Extreme MEV extraction risk, avoid market orders' : 'Elevated sandwich attack probability, use limit orders'}`);
    keyMonitorPoints.push('Monitor bot activity for changes - high bot presence increases slippage risk');
    keyMonitorPoints.push('Bot behavior can shift rapidly - set alerts for bot activity level changes');
  } else if (brain.botSwarmLevel === 'MEDIUM') {
    neutralFactors.push('Moderate bot activity - exercise caution with order execution');
  }

  // Operability
  if (brain.operabilityLevel === 'PREMIUM' || brain.operabilityLevel === 'GOOD') {
    bullishFactors.push(`Operability: ${brain.operabilityLevel} (score: ${brain.operabilityScore}/100)`);
    bullishFactors.push(`High operability means lower slippage and better fill quality - favorable for both entry and exit execution`);
  } else if (brain.operabilityLevel === 'RISKY' || brain.operabilityLevel === 'UNOPERABLE') {
    bearishFactors.push(`Low operability: ${brain.operabilityLevel} (fees/slippage erode gains)`);
    bearishFactors.push(`Operability score ${brain.operabilityScore}/100 indicates ${brain.operabilityLevel === 'UNOPERABLE' ? 'severe' : 'significant'} execution risk - avoid large positions`);
  }

  // Anomaly
  if (brain.anomalyDetected) {
    neutralFactors.push(`Volume anomaly detected (score: ${brain.anomalyScore.toFixed(2)})`);
    neutralFactors.push(`Anomaly score ${brain.anomalyScore.toFixed(2)} suggests ${brain.anomalyScore > 0.8 ? 'high probability of significant event' : 'moderate unusual activity'}`);
    keyMonitorPoints.push('Volume anomaly - could indicate catalyst event or manipulation');
    keyMonitorPoints.push('Anomalous volume patterns often precede major moves - prepare for increased volatility');
  }

  // Transition
  if (brain.isTransitioning) {
    neutralFactors.push('Token is transitioning between lifecycle phases');
    neutralFactors.push('Phase transitions are high-uncertainty periods - trend direction may reverse or accelerate sharply');
    keyMonitorPoints.push('Phase transition in progress - direction uncertainty elevated');
    keyMonitorPoints.push('Monitor on-chain metrics closely during transition for early direction signals');
  }

  // Full decision tree recommendation with confidence intervals
  const bullCount = bullishFactors.length;
  const bearCount = bearishFactors.length;
  const netFactors = bullCount - bearCount;
  let recommendation: ActionRecommendation;
  let recommendationConfidence: number;

  if (riskScore <= 25 && netFactors >= 3) {
    recommendation = 'STRONG_BUY';
    recommendationConfidence = 0.85;
  } else if (riskScore <= 35 && netFactors >= 2) {
    recommendation = 'STRONG_BUY';
    recommendationConfidence = 0.75;
  } else if (riskScore <= 40 && netFactors >= 1) {
    recommendation = 'BUY';
    recommendationConfidence = 0.65;
  } else if (riskScore <= 55 && Math.abs(netFactors) <= 1) {
    recommendation = 'HOLD';
    recommendationConfidence = 0.5;
  } else if (riskScore <= 65 && netFactors <= -1) {
    recommendation = 'REDUCE';
    recommendationConfidence = 0.55;
  } else if (riskScore <= 75 && netFactors <= -2) {
    recommendation = 'SELL';
    recommendationConfidence = 0.65;
  } else if (riskScore <= 85 && netFactors <= -3) {
    recommendation = 'STRONG_SELL';
    recommendationConfidence = 0.75;
  } else if (riskScore > 85) {
    recommendation = 'AVOID';
    recommendationConfidence = 0.85;
  } else {
    recommendation = 'HOLD';
    recommendationConfidence = 0.45;
  }

  // Confidence adjustments
  if (patternScan) recommendationConfidence = Math.min(1, recommendationConfidence + 0.1);
  if (behavior) recommendationConfidence = Math.min(1, recommendationConfidence + 0.1);
  if (brain.regimeConfidence > 0.7) recommendationConfidence = Math.min(1, recommendationConfidence + 0.05);
  if (brain.anomalyDetected) recommendationConfidence = Math.max(0.2, recommendationConfidence - 0.05);

  const riskLevel = riskScore <= 20 ? 'VERY_LOW' : riskScore <= 40 ? 'LOW' : riskScore <= 60 ? 'MEDIUM' : riskScore <= 80 ? 'HIGH' : riskScore <= 90 ? 'VERY_HIGH' : 'EXTREME';
  const maxPosition = riskScore <= 20 ? 10 : riskScore <= 40 ? 7 : riskScore <= 60 ? 5 : riskScore <= 80 ? 3 : 1;

  justification.push(`Risk: ${riskLevel} (${riskScore.toFixed(0)}/100) | Bull factors: ${bullCount} | Bear factors: ${bearCount}`);
  justification.push(`[DEEP] Phase: ${brain.lifecyclePhase} (risk contribution: ${phaseRisk.toFixed(0)}/100)`);
  justification.push(`[DEEP] Regime: ${brain.regime} (risk contribution: ${regimeRisk.toFixed(0)}/100)`);
  justification.push(`[DEEP] Bot risk: ${brain.botSwarmLevel} (risk contribution: ${botRisk.toFixed(0)}/100)`);
  justification.push(`[DEEP] Operability: ${brain.operabilityLevel} (${brain.operabilityScore}/100)`);
  if (patternScan) justification.push(`[DEEP] Pattern signal: ${patternScan.overallSignal} (${patternScan.patterns.length} patterns, score: ${patternScan.overallScore.toFixed(2)})`);
  if (behavior) justification.push(`[DEEP] Trader flow: ${behavior.netFlowDirection} (score: ${behavior.netFlowScore.toFixed(2)}, confidence: ${(behavior.confidence * 100).toFixed(0)}%)`);

  // 5 Scenarios (bull, base-bull, base, base-bear, bear) + stress test
  const bullProb = Math.max(0.05, Math.min(0.5, 0.2 + netFactors * 0.05 - riskScore * 0.002));
  const bearProb = Math.max(0.05, Math.min(0.5, 0.2 - netFactors * 0.05 + riskScore * 0.002));
  const baseBullProb = Math.max(0.05, Math.min(0.25, 0.15 + netFactors * 0.02));
  const baseBearProb = Math.max(0.05, Math.min(0.25, 0.15 - netFactors * 0.02));
  const baseProb = Math.max(0.1, 1 - bullProb - bearProb - baseBullProb - baseBearProb);

  const bullTarget = brain.lifecyclePhase === 'GROWTH' ? 35 : brain.lifecyclePhase === 'FOMO' ? 50 : 20;
  const baseBullTarget = bullTarget * 0.5;
  const bearTarget = brain.regime === 'BEAR' ? -40 : brain.lifecyclePhase === 'DECLINE' ? -30 : -20;
  const baseBearTarget = bearTarget * 0.5;
  const baseTarget = bullTarget * 0.15 + bearTarget * 0.15;

  const extendedScenarios: ExtendedScenario[] = [
    { name: 'BULL', probability: bullProb, targetPct: bullTarget, description: `Strong momentum continues with ${brain.regime === 'BULL' ? 'regime tailwind' : 'improving conditions'}. ${bullTarget}%+ upside. Key catalyst: ${bullishFactors[0] || 'market dynamics'}.` },
    { name: 'BASE-BULL', probability: baseBullProb, targetPct: baseBullTarget, description: `Mild upside scenario. Moderate gains of ${baseBullTarget}% driven by sector rotation or short covering.` },
    { name: 'BASE', probability: baseProb, targetPct: baseTarget, description: `Sideways consolidation within ${baseTarget > 0 ? '+' : ''}${baseTarget.toFixed(1)}% range. Market awaiting direction catalyst. ${neutralFactors.length > 0 ? `Neutral factors: ${neutralFactors[0]}.` : ''}` },
    { name: 'BASE-BEAR', probability: baseBearProb, targetPct: baseBearTarget, description: `Mild downside scenario. ${baseBearTarget}% decline on profit-taking or fading momentum.` },
    { name: 'BEAR', probability: bearProb, targetPct: bearTarget, description: `Deterioration likely if ${bearishFactors[0] || 'risk factors materialize'}. ${bearTarget}% decline. ${brain.botSwarmLevel === 'HIGH' || brain.botSwarmLevel === 'CRITICAL' ? 'Bot activity amplifies downside risk.' : ''}` },
  ];

  // Stress test
  const stressTestResults = {
    blackSwanPct: -(40 + riskScore * 0.3),
    liquidityCrashPct: -(25 + (brain.operabilityScore < 40 ? 20 : 5)),
    flashCrashRecoveryHours: brain.operabilityScore >= 60 ? 4 : brain.operabilityScore >= 40 ? 12 : 48,
    maxDrawdownEstimate: -(15 + riskScore * 0.25),
  };

  // Phase transition probability
  const phaseTransitionProb = brain.isTransitioning ? 0.7 : brain.lifecyclePhase === 'FOMO' ? 0.5 : brain.lifecyclePhase === 'INCIPIENT' ? 0.4 : 0.15;

  // Whale accumulation narrative
  const whaleNarrative = brain.whaleDirection === 'ACCUMULATING'
    ? `Large wallets are actively accumulating at current levels (${(brain.whaleConfidence * 100).toFixed(0)}% confidence). ${brain.whaleConfidence > 0.7 ? 'This is a strong bullish signal - whales typically have superior information and their accumulation precedes major moves by 12-48 hours.' : 'Moderate accumulation observed - may indicate early positioning but conviction is not yet strong.'}`
    : brain.whaleDirection === 'DISTRIBUTING'
    ? `Whales are distributing (${(brain.whaleConfidence * 100).toFixed(0)}% confidence). ${brain.whaleConfidence > 0.7 ? 'This is a strong bearish signal - large holders exiting often signals the top of a cycle. Consider reducing exposure.' : 'Some distribution observed - monitor for acceleration.'}`
    : `Whale activity is neutral - no clear accumulation or distribution pattern detected.`;

  // Bot swarm impact analysis
  const botImpactAnalysis = brain.botSwarmLevel === 'CRITICAL'
    ? `CRITICAL bot swarm detected. MEV extraction risk is extreme - sandwich attacks and front-running are highly probable. Use only limit orders with wide slippage tolerance. Expected additional slippage: 2-5% above normal. Consider delaying execution until bot activity subsides.`
    : brain.botSwarmLevel === 'HIGH'
    ? `HIGH bot activity present. Elevated risk of sandwich attacks and front-running. Use limit orders and avoid market orders. Expected additional slippage: 1-3%. Set tighter slippage tolerance and use TWAP execution.`
    : brain.botSwarmLevel === 'MEDIUM'
    ? `Moderate bot activity detected. Some front-running risk exists but is manageable with proper execution strategy. Use limit orders for larger positions.`
    : `Bot activity is low - execution conditions are favorable with minimal MEV risk.`;

  // Invalidation levels
  const invalidationLevels = [
    patternScan?.overallSignal === 'BULLISH' ? `Bearish invalidation: Price breaks below key support (pattern fails)` : `Bullish invalidation: Price fails to break above resistance`,
    `Volume invalidation: 24h volume drops below 50% of current level`,
    `Whale invalidation: ${brain.whaleDirection === 'ACCUMULATING' ? 'Whale selling begins (distribution replaces accumulation)' : 'Whale accumulation fails to materialize'}`,
    `Regime invalidation: Market regime shifts from ${brain.regime} to adverse direction`,
  ];

  // Key assumptions
  const keyAssumptions = [
    `Current ${brain.regime} regime persists over suggested time horizon`,
    `Whale activity patterns remain consistent in near term`,
    `Bot swarm level does not escalate beyond ${brain.botSwarmLevel}`,
    `No external black swan events (regulatory, exchange failure, etc.)`,
    `Operability conditions remain stable (score: ${brain.operabilityScore}/100)`,
    brain.anomalyDetected ? `Volume anomaly resolves without extreme price impact` : `No sudden volume anomalies emerge`,
  ];

  // Risk-adjusted position sizing (Kelly-based)
  const winProb = recommendation.includes('BUY') ? recommendationConfidence : 1 - recommendationConfidence;
  const avgWin = bullTarget / 100;
  const avgLoss = Math.abs(bearTarget) / 100;
  const kellyFraction = avgLoss > 0 ? Math.max(0, (winProb * avgWin - (1 - winProb) * avgLoss) / avgLoss) : 0;

  const riskAdjustedPositionSizing = {
    conservativePct: Math.min(maxPosition, Math.round(kellyFraction * 25 * 10) / 10),
    moderatePct: Math.min(maxPosition, Math.round(kellyFraction * 50 * 10) / 10),
    aggressivePct: Math.min(maxPosition, Math.round(kellyFraction * 75 * 10) / 10),
    kellyFraction: Math.round(kellyFraction * 1000) / 1000,
  };

  const timeHorizon = brain.lifecyclePhase === 'GENESIS' ? '1-4 hours'
    : brain.lifecyclePhase === 'INCIPIENT' ? '4-12 hours'
    : brain.lifecyclePhase === 'GROWTH' ? '1-3 days'
    : brain.lifecyclePhase === 'FOMO' ? '2-8 hours'
    : brain.lifecyclePhase === 'DECLINE' ? '1-7 days'
    : '1-14 days';

  const urgency: DeepAnalysisResult['urgencyLevel'] =
    (patternScan?.confluences.length ?? 0) > 0 ? 'IMMEDIATE'
    : brain.isTransitioning ? 'HIGH'
    : brain.anomalyDetected ? 'HIGH'
    : netFactors >= 2 || netFactors <= -2 ? 'MEDIUM' : 'LOW';

  const summary = `${symbol || tokenAddress.slice(0,8)} is in ${brain.lifecyclePhase} phase within a ${brain.regime} regime (${(brain.regimeConfidence * 100).toFixed(0)}% confidence). `
    + `Comprehensive analysis identifies ${bullCount} bullish factors vs ${bearCount} bearish factors (${neutralFactors.length} neutral). `
    + `Overall risk: ${riskLevel} (${riskScore.toFixed(0)}/100). Recommendation: ${recommendation} with ${(recommendationConfidence * 100).toFixed(0)}% confidence.`
    + (patternScan ? ` Candlestick analysis: ${patternScan.overallSignal} bias with ${patternScan.patterns.length} patterns detected (score: ${patternScan.overallScore.toFixed(2)}). ${patternScan.confluences.length > 0 ? `${patternScan.confluences.length} confluences confirmed across timeframes.` : 'No multi-timeframe confluences.'}` : '')
    + (behavior ? ` Trader behavior: ${behavior.netFlowDirection} flow (${behavior.netFlowScore.toFixed(2)} score, ${behavior.archetypeBreakdown.length} archetypes identified).` : '')
    + ` Maximum recommended position: ${maxPosition}% of capital. Suggested horizon: ${timeHorizon}.`;

  const riskAssessment = `Risk level: ${riskLevel} (${riskScore.toFixed(0)}/100). `
    + `Phase risk: ${phaseRisk.toFixed(0)}/100, Regime risk: ${regimeRisk.toFixed(0)}/100, Bot risk: ${botRisk.toFixed(0)}/100. `
    + `Operability: ${brain.operabilityLevel} (${brain.operabilityScore}/100). `
    + `Maximum recommended position: ${maxPosition}% of capital. `
    + `Whale risk: ${brain.whaleDirection} (${(brain.whaleConfidence * 100).toFixed(0)}%). `
    + `Smart money: ${brain.smartMoneyFlow}. `
    + `Volatility regime: ${(brain as any).volatilityRegime || 'NORMAL'}. `
    + `Anomaly: ${brain.anomalyDetected ? `Detected (${brain.anomalyScore.toFixed(2)})` : 'None'}. `
    + `Transition: ${brain.isTransitioning ? 'Yes - elevated uncertainty' : 'No'}.`;

  return {
    tokenAddress,
    symbol: symbol || '',
    chain,
    analyzedAt: new Date(),
    depth: 'DEEP',
    source: 'RULE_BASED',
    summary,
    riskAssessment,
    recommendation,
    recommendationConfidence,
    justification: justification.slice(0, 10),
    bullishFactors: bullishFactors.slice(0, 12),
    bearishFactors: bearishFactors.slice(0, 12),
    neutralFactors: neutralFactors.slice(0, 6),
    keyMonitorPoints: keyMonitorPoints.slice(0, 8),
    scenarios: {
      bull: { probability: bullProb, targetPct: bullTarget, description: extendedScenarios[0].description },
      base: { probability: baseProb, targetPct: baseTarget, description: extendedScenarios[2].description },
      bear: { probability: bearProb, targetPct: bearTarget, description: extendedScenarios[4].description },
    },
    extendedScenarios,
    stressTestResults,
    riskLevel,
    riskScore,
    maxRecommendedPositionPct: maxPosition,
    detailedAnalysis: {
      phaseTransitionProbability: phaseTransitionProb,
      whaleAccumulationNarrative: whaleNarrative,
      botSwarmImpactAnalysis: botImpactAnalysis,
      invalidationLevels,
      keyAssumptions,
      riskAdjustedPositionSizing,
    },
    suggestedTimeHorizon: timeHorizon,
    urgencyLevel: urgency,
  };
}

// ============================================================
// ROUTER: Dispatch to depth-specific analysis
// ============================================================

function ruleBasedAnalysis(input: DeepAnalysisInput): DeepAnalysisResult {
  const depth = input.depth ?? 'STANDARD';
  if (depth === 'QUICK') return quickAnalysis(input);
  if (depth === 'DEEP') return deepAnalysis(input);
  return standardAnalysis(input);
}

// ============================================================
// LLM ANALYSIS ENGINE
// ============================================================

async function llmAnalysis(input: DeepAnalysisInput): Promise<DeepAnalysisResult | null> {
  try {
    const ZAI = (await import('z-ai-web-dev-sdk')).default;
    const zai = await ZAI.create();

    const { brainAnalysis: brain, patternScan, behavioralPrediction: behavior, symbol, chain, tokenAddress } = input;

    // Build context for LLM
    const context = `
TOKEN ANALYSIS DATA:
- Symbol: ${symbol}
- Chain: ${chain}
- Address: ${tokenAddress}
- Lifecycle Phase: ${brain.lifecyclePhase} (confidence: ${(brain.lifecycleConfidence * 100).toFixed(0)}%)
- Market Regime: ${brain.regime} (confidence: ${(brain.regimeConfidence * 100).toFixed(0)}%)
- Volatility: ${brain.volatilityRegime}
- Operability: ${brain.operabilityLevel} (${brain.operabilityScore}/100)
- Bot Swarm: ${brain.botSwarmLevel}
- Whale Direction: ${brain.whaleDirection} (${(brain.whaleConfidence * 100).toFixed(0)}%)
- Smart Money: ${brain.smartMoneyFlow}
- Mean Reversion Zone: ${brain.meanReversionZone ? `$${brain.meanReversionZone.lowerBound.toFixed(4)}-$${brain.meanReversionZone.upperBound.toFixed(4)} (${(brain.meanReversionZone.probabilityOfReversion * 100).toFixed(0)}%)` : 'N/A'}
- Anomaly: ${brain.anomalyDetected ? `Yes (score: ${brain.anomalyScore.toFixed(2)})` : 'No'}
${patternScan ? `- Candlestick Patterns: ${patternScan.overallSignal} (score: ${patternScan.overallScore.toFixed(2)})
  Bullish: ${patternScan.bullishPatterns.map(p => p.pattern).join(', ') || 'none'}
  Bearish: ${patternScan.bearishPatterns.map(p => p.pattern).join(', ') || 'none'}
  Confluences: ${patternScan.confluences.map(c => `${c.pattern} on ${c.timeframes.join('+')}`).join('; ') || 'none'}` : '- Candlestick Patterns: Not scanned'}
${behavior ? `- Trader Behavior: ${behavior.netFlowDirection} (flow: ${behavior.netFlowScore.toFixed(2)}, confidence: ${(behavior.confidence * 100).toFixed(0)}%)
  Top Archetype: ${behavior.archetypeBreakdown[0]?.archetype ?? 'unknown'} (${behavior.archetypeBreakdown[0]?.dominantAction ?? 'N/A'})` : '- Trader Behavior: Not analyzed'}
- Warnings: ${brain.warnings.join('; ') || 'none'}
- Evidence: ${brain.evidence.slice(0, 5).join('; ') || 'none'}
`.trim();

    const depthInstruction = input.depth === 'DEEP'
      ? 'Provide a COMPREHENSIVE deep analysis with detailed reasoning, 8-12 bullish/bearish factors, 5 scenario narratives, specific entry/exit conditions with price levels, invalidation levels, key assumptions, and risk-adjusted position sizing. Include phase transition probabilities and whale/bot impact analysis.'
      : input.depth === 'QUICK'
      ? 'Provide a BRIEF quick scan summary. Keep it concise: 1-2 bullish/bearish factors, short summary, basic recommendation. NO scenarios.'
      : 'Provide a balanced standard analysis with moderate detail, 4-6 factors, 3 scenarios with probabilities.';

    const maxFactors = input.depth === 'DEEP' ? 12 : input.depth === 'QUICK' ? 2 : 6;

    const prompt = `You are a professional crypto analyst. ${depthInstruction}

${context}

Respond in this EXACT JSON format (no markdown, no code blocks):
{
  "summary": "${input.depth === 'DEEP' ? '4-5 sentence comprehensive narrative' : input.depth === 'QUICK' ? '1-2 sentence brief summary' : '2-3 sentence narrative summary'}",
  "riskAssessment": "${input.depth === 'DEEP' ? '2-3 sentence detailed risk assessment with specific risk categories' : '1-2 sentence risk assessment'}",
  "recommendation": "STRONG_BUY|BUY|HOLD|REDUCE|SELL|AVOID",
  "confidence": 0.0-1.0,
  "bullishFactors": ["factor1", "factor2" ${input.depth === 'DEEP' ? ', "factor3", "factor4", "factor5"' : ''}],
  "bearishFactors": ["factor1", "factor2" ${input.depth === 'DEEP' ? ', "factor3", "factor4", "factor5"' : ''}],
  "keyMonitorPoints": ["point1", "point2" ${input.depth === 'DEEP' ? ', "point3", "point4"' : ''}],
  ${input.depth === 'QUICK' ? '' : `"bullScenario": {"probability": 0.0-1.0, "targetPct": number, "description": "${input.depth === 'DEEP' ? 'Detailed bull case with specific catalysts and price targets' : 'Bull case description'}"},
  "baseScenario": {"probability": 0.0-1.0, "targetPct": number, "description": "${input.depth === 'DEEP' ? 'Detailed base case with range expectations' : 'Base case description'}"},
  "bearScenario": {"probability": 0.0-1.0, "targetPct": number, "description": "${input.depth === 'DEEP' ? 'Detailed bear case with specific risks and invalidation levels' : 'Bear case description'}"},`}
  "riskLevel": "VERY_LOW|LOW|MEDIUM|HIGH|VERY_HIGH|EXTREME",
  "riskScore": 0-100,
  "maxPositionPct": 1-10,
  "timeHorizon": "text",
  "urgency": "IMMEDIATE|HIGH|MEDIUM|LOW"${input.depth === 'DEEP' ? ',\\n  "entryConditions": ["condition1", "condition2", "condition3", "condition4"],\\n  "exitConditions": ["condition1", "condition2", "condition3", "condition4"],\\n  "invalidationLevel": "price or condition that invalidates the thesis",\\n  "keyAssumptions": ["assumption1", "assumption2", "assumption3", "assumption4"],\\n  "phaseTransitionProbability": 0.0-1.0,\\n  "whaleNarrative": "detailed whale accumulation/distribution narrative",\\n  "botImpactAnalysis": "detailed bot swarm impact analysis",\\n  "stressTestBlackSwan": -50 to -80,\\n  "stressTestLiquidityCrash": -30 to -60,\\n  "kellyFraction": 0.0-1.0' : ''}\n}`;

    const completion = await zai.chat.completions.create({
      messages: [
        { role: 'system', content: 'You are a professional cryptocurrency analyst. Always respond with valid JSON only, no markdown.' },
        { role: 'user', content: prompt },
      ],
      temperature: 0.3,
      max_tokens: input.depth === 'DEEP' ? 2500 : input.depth === 'QUICK' ? 800 : 1500,
    });

    const rawContent = completion.choices[0]?.message?.content;
    if (!rawContent) return null;

    // Parse LLM response
    let parsed: Record<string, unknown>;
    try {
      const jsonStr = rawContent.replace(/```json?\n?/g, '').replace(/```/g, '').trim();
      parsed = JSON.parse(jsonStr);
    } catch {
      return null;
    }

    // Map LLM response to our type
    const rec = String(parsed.recommendation || 'HOLD') as ActionRecommendation;
    const rl = String(parsed.riskLevel || 'MEDIUM') as RiskLevel;

    const result: DeepAnalysisResult = {
      tokenAddress,
      symbol: symbol || '',
      chain,
      analyzedAt: new Date(),
      depth: input.depth ?? 'STANDARD',
      source: 'LLM',
      summary: String(parsed.summary || ''),
      riskAssessment: String(parsed.riskAssessment || ''),
      recommendation: rec,
      recommendationConfidence: Number(parsed.confidence || 0.5),
      justification: [`LLM analysis: ${rec} with ${(Number(parsed.confidence || 0.5) * 100).toFixed(0)}% confidence`],
      bullishFactors: Array.isArray(parsed.bullishFactors) ? (parsed.bullishFactors as string[]).slice(0, maxFactors) : [],
      bearishFactors: Array.isArray(parsed.bearishFactors) ? (parsed.bearishFactors as string[]).slice(0, maxFactors) : [],
      neutralFactors: [],
      keyMonitorPoints: Array.isArray(parsed.keyMonitorPoints) ? (parsed.keyMonitorPoints as string[]).slice(0, 8) : [],
      scenarios: input.depth === 'QUICK' ? {
        bull: { probability: 0, targetPct: 0, description: '' },
        base: { probability: 0, targetPct: 0, description: '' },
        bear: { probability: 0, targetPct: 0, description: '' },
      } : {
        bull: {
          probability: Number((parsed.bullScenario as Record<string, unknown>)?.probability || 0.35),
          targetPct: Number((parsed.bullScenario as Record<string, unknown>)?.targetPct || 20),
          description: String((parsed.bullScenario as Record<string, unknown>)?.description || ''),
        },
        base: {
          probability: Number((parsed.baseScenario as Record<string, unknown>)?.probability || 0.35),
          targetPct: Number((parsed.baseScenario as Record<string, unknown>)?.targetPct || 5),
          description: String((parsed.baseScenario as Record<string, unknown>)?.description || ''),
        },
        bear: {
          probability: Number((parsed.bearScenario as Record<string, unknown>)?.probability || 0.30),
          targetPct: Number((parsed.bearScenario as Record<string, unknown>)?.targetPct || -15),
          description: String((parsed.bearScenario as Record<string, unknown>)?.description || ''),
        },
      },
      riskLevel: rl,
      riskScore: Number(parsed.riskScore || 50),
      maxRecommendedPositionPct: Number(parsed.maxPositionPct || 5),
      suggestedTimeHorizon: String(parsed.timeHorizon || '1-3 days'),
      urgencyLevel: String(parsed.urgency || 'MEDIUM') as DeepAnalysisResult['urgencyLevel'],
      llmRawAnalysis: rawContent,
    };

    // Add DEEP-specific fields from LLM
    if (input.depth === 'DEEP') {
      result.detailedAnalysis = {
        phaseTransitionProbability: Number(parsed.phaseTransitionProbability || 0.2),
        whaleAccumulationNarrative: String(parsed.whaleNarrative || ''),
        botSwarmImpactAnalysis: String(parsed.botImpactAnalysis || ''),
        invalidationLevels: Array.isArray(parsed.invalidationLevel) ? (parsed.invalidationLevel as string[]) : [String(parsed.invalidationLevel || 'N/A')],
        keyAssumptions: Array.isArray(parsed.keyAssumptions) ? (parsed.keyAssumptions as string[]) : ['Market conditions remain stable'],
        riskAdjustedPositionSizing: {
          conservativePct: Math.max(1, Number(parsed.maxPositionPct || 3) * 0.5),
          moderatePct: Number(parsed.maxPositionPct || 5) * 0.75,
          aggressivePct: Number(parsed.maxPositionPct || 5),
          kellyFraction: Number(parsed.kellyFraction || 0.1),
        },
      };
      result.stressTestResults = {
        blackSwanPct: Number(parsed.stressTestBlackSwan || -60),
        liquidityCrashPct: Number(parsed.stressTestLiquidityCrash || -40),
        flashCrashRecoveryHours: 24,
        maxDrawdownEstimate: Number(parsed.stressTestBlackSwan || -60) * 0.7,
      };
    }

    return result;
  } catch (error) {
    console.warn('[DeepAnalysis] LLM analysis failed, using rule-based fallback:', error instanceof Error ? error.message : String(error));
    return null;
  }
}

// ============================================================
// HYBRID: Merge LLM + Rule-Based (depth-aware)
// ============================================================

function mergeAnalyses(llm: DeepAnalysisResult, rule: DeepAnalysisResult): DeepAnalysisResult {
  const isDeep = rule.depth === 'DEEP';
  // Weight risk scoring based on depth
  const riskWeight = isDeep ? { rule: 0.5, llm: 0.5 } : { rule: 0.6, llm: 0.4 };
  const mergedRisk = rule.riskScore * riskWeight.rule + llm.riskScore * riskWeight.llm;
  const mergedConfidence = (llm.recommendationConfidence + rule.recommendationConfidence) / 2;

  const result: DeepAnalysisResult = {
    ...llm,
    source: 'HYBRID',
    riskScore: Math.round(mergedRisk),
    riskLevel: mergedRisk <= 20 ? 'VERY_LOW' : mergedRisk <= 40 ? 'LOW' : mergedRisk <= 60 ? 'MEDIUM' : mergedRisk <= 80 ? 'HIGH' : mergedRisk <= 90 ? 'VERY_HIGH' : 'EXTREME',
    recommendationConfidence: mergedConfidence,
    bullishFactors: [...new Set([...llm.bullishFactors, ...rule.bullishFactors])],
    bearishFactors: [...new Set([...llm.bearishFactors, ...rule.bearishFactors])],
    neutralFactors: [...new Set([...llm.neutralFactors, ...rule.neutralFactors])],
    keyMonitorPoints: [...new Set([...llm.keyMonitorPoints, ...rule.keyMonitorPoints])].slice(0, 8),
    justification: [...llm.justification, ...rule.justification],
    scenarios: rule.depth === 'QUICK' ? rule.scenarios : {
      bull: { probability: (llm.scenarios.bull.probability + rule.scenarios.bull.probability) / 2, targetPct: (llm.scenarios.bull.targetPct + rule.scenarios.bull.targetPct) / 2, description: llm.scenarios.bull.description || rule.scenarios.bull.description },
      base: { probability: (llm.scenarios.base.probability + rule.scenarios.base.probability) / 2, targetPct: (llm.scenarios.base.targetPct + rule.scenarios.base.targetPct) / 2, description: llm.scenarios.base.description || rule.scenarios.base.description },
      bear: { probability: (llm.scenarios.bear.probability + rule.scenarios.bear.probability) / 2, targetPct: (llm.scenarios.bear.targetPct + rule.scenarios.bear.targetPct) / 2, description: llm.scenarios.bear.description || rule.scenarios.bear.description },
    },
  };

  // Preserve DEEP-specific fields from rule-based if LLM didn't provide them
  if (isDeep) {
    result.extendedScenarios = rule.extendedScenarios || llm.extendedScenarios;
    result.stressTestResults = rule.stressTestResults || llm.stressTestResults;
    result.detailedAnalysis = rule.detailedAnalysis || llm.detailedAnalysis;
  }

  return result;
}

// ============================================================
// ENGINE CLASS
// ============================================================

class DeepAnalysisEngine {
  /**
   * Run deep analysis on a token.
   * Tries LLM first, falls back to rule-based, or merges both.
   */
  async analyze(input: DeepAnalysisInput | AnalysisInput, thinkingDepth?: ThinkingDepth): Promise<DeepAnalysisResult> {
    // Convert AnalysisInput to DeepAnalysisInput if needed
    let analysisInput: DeepAnalysisInput;
    if ('currentPrice' in input) {
      // It's an AnalysisInput - convert to DeepAnalysisInput
      const ai = input as AnalysisInput;
      analysisInput = {
        tokenAddress: ai.tokenAddress,
        symbol: ai.symbol,
        chain: ai.chain,
        brainAnalysis: {
          tokenAddress: ai.tokenAddress,
          chain: ai.chain,
          lifecyclePhase: ai.lifecyclePhase,
          lifecycleConfidence: ai.lifecycleConfidence,
          regime: ai.regime as 'BULL' | 'BEAR' | 'SIDEWAYS' | 'TRANSITION',
          regimeConfidence: ai.regimeConfidence,
          volatilityRegime: 'NORMAL',
          operabilityLevel: ai.operabilityScore >= 60 ? 'PREMIUM' : ai.operabilityScore >= 40 ? 'GOOD' : ai.operabilityScore >= 20 ? 'RISKY' : 'UNOPERABLE',
          operabilityScore: ai.operabilityScore,
          isOperable: ai.operabilityScore >= 20,
          botSwarmLevel: ai.botSwarmLevel as 'NONE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL',
          whaleDirection: ai.whaleDirection as 'ACCUMULATING' | 'DISTRIBUTING' | 'NEUTRAL' | 'ROTATING',
          whaleConfidence: 0.5,
          smartMoneyFlow: ai.netBehaviorFlow as 'INFLOW' | 'OUTFLOW' | 'NEUTRAL',
          meanReversionZone: null,
          anomalyDetected: false,
          anomalyScore: 0,
          isTransitioning: false,
          warnings: [],
          evidence: [],
        } as any,
        patternScan: ai.patternScan,
        depth: thinkingDepth ?? ai.dataReliability?.sampleSufficiency === 'OPTIMAL' ? 'DEEP' : 'STANDARD',
      };
    } else {
      analysisInput = input as DeepAnalysisInput;
      if (thinkingDepth) analysisInput.depth = thinkingDepth;
    }

    // Always compute rule-based as baseline (now dispatches to depth-specific function)
    const ruleResult = ruleBasedAnalysis(analysisInput);

    // Try LLM for depth >= STANDARD
    if ((analysisInput.depth ?? 'STANDARD') !== 'QUICK') {
      const llmResult = await llmAnalysis(analysisInput);
      if (llmResult) {
        // Hybrid: merge both
        return mergeAnalyses(llmResult, ruleResult);
      }
    }

    return ruleResult;
  }

  /**
   * Run deep analysis in batch.
   */
  async analyzeBatch(inputs: DeepAnalysisInput[]): Promise<DeepAnalysisResult[]> {
    const results: DeepAnalysisResult[] = [];
    for (const input of inputs) {
      try {
        const result = await this.analyze(input);
        results.push(result);
      } catch {
        // Fallback to rule-based
        results.push(ruleBasedAnalysis(input));
      }
      await new Promise(r => setTimeout(r, 200)); // Rate limit for LLM
    }
    return results;
  }

  /**
   * Store deep analysis result in DB.
   */
  async storeResult(result: DeepAnalysisResult): Promise<void> {
    try {
      const token = await db.token.findFirst({
        where: { address: result.tokenAddress },
      });
      if (!token) return;

      await db.signal.create({
        data: {
          type: `DEEP_ANALYSIS_${result.source}`,
          direction: result.recommendation,
          confidence: Math.round(result.recommendationConfidence * 100),
          description: result.summary,
          tokenId: token.id,
          metadata: JSON.stringify({
            tokenAddress: result.tokenAddress,
            symbol: result.symbol,
            riskLevel: result.riskLevel,
            riskScore: result.riskScore,
            recommendation: result.recommendation,
            maxPositionPct: result.maxRecommendedPositionPct,
            scenarios: result.scenarios,
            bullishFactors: result.bullishFactors,
            bearishFactors: result.bearishFactors,
            keyMonitorPoints: result.keyMonitorPoints,
            urgencyLevel: result.urgencyLevel,
            source: result.source,
            depth: result.depth,
            extendedScenarios: result.extendedScenarios,
            stressTestResults: result.stressTestResults,
            detailedAnalysis: result.detailedAnalysis,
          }),
        },
      });
    } catch {
      // Storage is best-effort
    }
  }
}

export const deepAnalysisEngine = new DeepAnalysisEngine();
