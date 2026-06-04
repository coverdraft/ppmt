/**
 * Brain Orchestrator - CryptoQuant Terminal
 * 
 * THE CENTRAL NERVOUS SYSTEM that wires together:
 * 
 * 1. Big Data Engine (regime, anomalies, whale forecast, bot swarm)
 * 2. Token Lifecycle Engine (phase detection, transitions)
 * 3. Behavioral Model Engine (trader behavior prediction)
 * 4. Wallet Profiler (smart money, whale, sniper scoring)
 * 5. Bot Detection (8 bot type classifiers)
 * 6. OHLCV Pipeline (historical price data)
 * 7. Operability Score (fee-aware trade filtering)
 * 8. Trading System Matcher (lifecycle → system selection)
 * 9. Feedback Loop (continuous improvement)
 * 10. Candlestick Pattern Engine (30+ patterns, multi-timeframe)
 * 11. Deep Analysis + LLM (z-ai-sdk + rule-based fallback)
 * 12. Cross-Correlation Engine (P(outcome | trader + pattern + phase))
 * 
 * The orchestrator:
 * - Collects data from DexScreener + on-chain sources
 * - Runs all analytical engines
 * - Produces a unified TokenAnalysis with actionable intelligence
 * - Feeds results to trading system selection
 * - Stores predictive signals for validation
 */

import {
  detectMarketRegime,
  detectBotSwarm,
  forecastWhaleMovement,
  detectAnomalies,
  analyzeSmartMoneyPositioning,
  calculateMeanReversionZones,
  type MarketContext,
  type PredictiveOutput,
  type EngineInput,
} from './big-data-engine';

import {
  buildWalletProfile,
  detectBehavioralPatterns,
  type WalletProfile,
  type TraderAnalytics,
} from './wallet-profiler';

import {
  detectBot,
  batchDetectBots,
  type BotDetectionResult,
  type TraderMetrics,
} from './bot-detection';

import { tokenLifecycleEngine } from './token-lifecycle-engine';
import { behavioralModelEngine } from './behavioral-model-engine';
import { feedbackLoopEngine } from './feedback-loop-engine';
import { ohlcvPipeline, TIMEFRAME_CONTEXTS, type OHLCVSeries } from './ohlcv-pipeline';
import { smartMoneyTracker, type SmartMoneySignal } from './smart-money-tracker';
import { buySellPressureService, type PressureSignal } from './buy-sell-pressure';
import {
  calculateOperabilityScore,
  quickOperabilityCheck,
  type OperabilityResult,
  type OperabilityInput,
} from './operability-score';

import { candlestickPatternEngine, type PatternScanResult } from './candlestick-pattern-engine';
import { deepAnalysisEngine, type DeepAnalysisResult } from './deep-analysis-engine';
import { crossCorrelationEngine, type CrossCorrelationResult } from './cross-correlation-engine';

// ============================================================
// TYPES
// ============================================================

export type UnifiedPhase = 'GENESIS' | 'INCIPIENT' | 'GROWTH' | 'FOMO' | 'DECLINE' | 'LEGACY';

/**
 * Phase mapping between the 6 lifecycle phases and the 7 trading system phases
 */
const LIFECYCLE_TO_TRADING_PHASE: Record<UnifiedPhase, string> = {
  'GENESIS': 'GENESIS',
  'INCIPIENT': 'LAUNCH',
  'GROWTH': 'EARLY',
  'FOMO': 'GROWTH',
  'DECLINE': 'MATURE',
  'LEGACY': 'LEGACY',
};

export interface TokenAnalysis {
  tokenAddress: string;
  symbol: string;
  chain: string;
  analyzedAt: Date;
  
  // === PHASE 1: DATA COLLECTION ===
  dataFreshness: 'LIVE' | 'RECENT' | 'STALE' | 'NO_DATA';
  candlesAvailable: number;
  
  // === PHASE 2: MARKET CONTEXT ===
  regime: 'BULL' | 'BEAR' | 'SIDEWAYS' | 'TRANSITION';
  regimeConfidence: number;
  volatilityRegime: 'LOW' | 'NORMAL' | 'HIGH' | 'EXTREME';
  
  // === PHASE 3: TOKEN LIFECYCLE ===
  lifecyclePhase: UnifiedPhase;
  lifecycleConfidence: number;
  tradingPhase: string; // Mapped to trading system phase
  isTransitioning: boolean;
  
  // === PHASE 4: TRADER BEHAVIOR ===
  netBehaviorFlow: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  behaviorConfidence: number;
  dominantArchetype: string;
  behaviorAnomaly: boolean;
  
  // === PHASE 5: BOT & WHALE INTELLIGENCE ===
  botSwarmLevel: 'NONE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  dominantBotType: string | null;
  whaleDirection: 'ACCUMULATING' | 'DISTRIBUTING' | 'NEUTRAL' | 'ROTATING';
  whaleConfidence: number;
  smartMoneyFlow: 'INFLOW' | 'OUTFLOW' | 'NEUTRAL';
  
  // === PHASE 6: OPERABILITY ===
  operabilityScore: number;
  operabilityLevel: string;
  isOperable: boolean;
  feeEstimate: {
    totalCostUsd: number;
    totalCostPct: number;
    slippagePct: number;
  };
  recommendedPositionUsd: number;
  minimumGainPct: number;
  
  // === PHASE 7: PREDICTIVE SIGNALS ===
  meanReversionZone: {
    upperBound: number;
    lowerBound: number;
    probabilityOfReversion: number;
  } | null;
  anomalyDetected: boolean;
  anomalyScore: number;
  
  // === PHASE 8: CANDLESTICK PATTERNS (NEW) ===
  patternScanResult: PatternScanResult | null;
  patternSignal: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  patternScore: number; // -1 to 1
  dominantPattern: string | null;
  patternConfluences: number;
  
  // === PHASE 9: DEEP ANALYSIS (NEW) ===
  deepAnalysis: DeepAnalysisResult | null;
  deepRecommendation: string | null;
  deepRiskLevel: string | null;
  deepRiskScore: number;
  
  // === PHASE 10: CROSS-CORRELATION (NEW) ===
  crossCorrelation: CrossCorrelationResult | null;
  correlatedOutcome: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  correlatedProbability: number;
  correlationConflict: boolean;
  
  // === PHASE 11: RECOMMENDED ACTION ===
  recommendedSystems: string[];    // Which trading systems to apply
  action: 'TRADE' | 'WATCH' | 'AVOID' | 'SKIP';
  actionReason: string;
  
  // === WARNINGS ===
  warnings: string[];
  evidence: string[];
}

/**
 * Creates a safe default TokenAnalysis for positions restored from DB.
 * Used when the full analysis isn't persisted (brainAnalysis is not stored in DB).
 * All values are neutral/conservative defaults — no unsafe `{}` cast.
 */
export function createEmptyTokenAnalysis(overrides?: Partial<TokenAnalysis>): TokenAnalysis {
  return {
    tokenAddress: overrides?.tokenAddress ?? '',
    symbol: overrides?.symbol ?? '',
    chain: overrides?.chain ?? 'SOL',
    analyzedAt: overrides?.analyzedAt ?? new Date(),
    dataFreshness: 'STALE',
    candlesAvailable: 0,
    regime: 'SIDEWAYS',
    regimeConfidence: 0.5,
    volatilityRegime: 'NORMAL',
    lifecyclePhase: 'GROWTH',
    lifecycleConfidence: 0.5,
    tradingPhase: 'growth',
    isTransitioning: false,
    netBehaviorFlow: 'NEUTRAL',
    behaviorConfidence: 0.5,
    dominantArchetype: 'UNKNOWN',
    behaviorAnomaly: false,
    botSwarmLevel: 'LOW',
    dominantBotType: null,
    whaleDirection: 'NEUTRAL',
    whaleConfidence: 0.5,
    smartMoneyFlow: 'NEUTRAL',
    operabilityScore: overrides?.operabilityScore ?? 50,
    operabilityLevel: 'GOOD',
    isOperable: true,
    feeEstimate: { totalCostUsd: 0, totalCostPct: 0, slippagePct: 0 },
    recommendedPositionUsd: 0,
    minimumGainPct: 1,
    meanReversionZone: null,
    anomalyDetected: false,
    anomalyScore: 0,
    patternScanResult: null,
    patternSignal: 'NEUTRAL',
    patternScore: 0,
    dominantPattern: null,
    patternConfluences: 0,
    deepAnalysis: null,
    deepRecommendation: null,
    deepRiskLevel: null,
    deepRiskScore: 0,
    crossCorrelation: null,
    correlatedOutcome: 'NEUTRAL',
    correlatedProbability: 0.5,
    correlationConflict: false,
    recommendedSystems: [],
    action: 'WATCH',
    actionReason: 'Restored from DB — analysis not available',
    warnings: ['Restored position — brain analysis was not persisted'],
    evidence: [],
    ...overrides,
  };
}

export interface BatchAnalysisResult {
  results: TokenAnalysis[];
  operableTokens: TokenAnalysis[];
  tradeableTokens: TokenAnalysis[];
  watchlistTokens: TokenAnalysis[];
  avoidTokens: TokenAnalysis[];
  summary: {
    total: number;
    operable: number;
    tradeable: number;
    byPhase: Record<string, number>;
    byRegime: Record<string, number>;
    avgOperability: number;
  };
}

// ============================================================
// TRADER ANALYTICS BUILDER
// ============================================================

/**
 * Build TraderAnalytics from on-chain transaction data.
 * This is the bridge between raw on-chain data and the wallet profiler.
 */
export function buildTraderAnalyticsFromTransactions(
  transactions: Array<{
    action: string;
    valueUsd: number;
    pnlUsd?: number;
    holdTimeMin?: number;
    entryRank?: number;
    exitMultiplier?: number;
    slippageBps?: number;
    isFrontrun: boolean;
    isSandwich: boolean;
    blockTime: Date;
    tokenAddress: string;
    dex?: string;
  }>,
  walletData: {
    totalHoldingsUsd: number;
    uniqueTokensTraded: number;
    preferredDexes: string[];
    preferredChains: string[];
    washTradeScore?: number;
    copyTradeScore?: number;
  }
): TraderAnalytics {
  if (transactions.length === 0) {
    return {
      totalTrades: 0, winRate: 0, avgPnlUsd: 0, totalPnlUsd: 0,
      avgHoldTimeMin: 0, avgTradeSizeUsd: 0, avgEntryRank: 0,
      earlyEntryCount: 0, avgExitMultiplier: 0, totalHoldingsUsd: walletData.totalHoldingsUsd,
      uniqueTokensTraded: walletData.uniqueTokensTraded,
      preferredDexes: walletData.preferredDexes,
      preferredChains: walletData.preferredChains,
      sharpeRatio: 0, profitFactor: 0, maxDrawdown: 0, consistencyScore: 0,
      washTradeScore: walletData.washTradeScore || 0,
      copyTradeScore: walletData.copyTradeScore || 0,
      frontrunCount: 0, sandwichCount: 0,
      tradingHourPattern: new Array(24).fill(0),
      isActive247: false, avgTimeBetweenTradesMin: 0,
    };
  }
  
  // Calculate basic metrics
  const buys = transactions.filter(t => ['BUY', 'SWAP'].includes(t.action.toUpperCase()));
  const sells = transactions.filter(t => ['SELL', 'SWAP'].includes(t.action.toUpperCase()));
  
  const pnlTrades = transactions.filter(t => t.pnlUsd !== undefined && t.pnlUsd !== null);
  const wins = pnlTrades.filter(t => (t.pnlUsd || 0) > 0);
  const totalPnl = pnlTrades.reduce((s, t) => s + (t.pnlUsd || 0), 0);
  const totalWinPnl = wins.reduce((s, t) => s + (t.pnlUsd || 0), 0);
  const totalLossPnl = pnlTrades.filter(t => (t.pnlUsd || 0) < 0).reduce((s, t) => s + Math.abs(t.pnlUsd || 0), 0);
  
  const avgHoldTime = transactions.filter(t => t.holdTimeMin).reduce((s, t) => s + (t.holdTimeMin || 0), 0) / (transactions.filter(t => t.holdTimeMin).length || 1);
  const avgTradeSize = transactions.reduce((s, t) => s + t.valueUsd, 0) / transactions.length;
  const avgEntryRank = transactions.filter(t => t.entryRank).reduce((s, t) => s + (t.entryRank || 0), 0) / (transactions.filter(t => t.entryRank).length || 1);
  
  // Timing analysis
  const hourCounts = new Array(24).fill(0);
  for (const tx of transactions) {
    const hour = new Date(tx.blockTime).getUTCHours();
    hourCounts[hour]++;
  }
  // Normalize
  const maxHourCount = Math.max(...hourCounts, 1);
  const tradingHourPattern = hourCounts.map(c => c / maxHourCount);
  
  // Active hours (hours with >10% of max activity)
  const activeHours = hourCounts.filter(c => c > maxHourCount * 0.1).length;
  const isActive247 = activeHours >= 22; // Active in 22+ out of 24 hours
  
  // Time between trades
  const sortedTx = [...transactions].sort((a, b) => new Date(a.blockTime).getTime() - new Date(b.blockTime).getTime());
  let totalGap = 0;
  let gapCount = 0;
  for (let i = 1; i < sortedTx.length; i++) {
    const gap = (new Date(sortedTx[i].blockTime).getTime() - new Date(sortedTx[i-1].blockTime).getTime()) / 60000;
    if (gap < 1440) { // Ignore gaps > 24h
      totalGap += gap;
      gapCount++;
    }
  }
  const avgTimeBetweenTrades = gapCount > 0 ? totalGap / gapCount : 0;
  
  // Consistency score (low coefficient of variation in trade timing = consistent)
  const gaps: number[] = [];
  for (let i = 1; i < sortedTx.length; i++) {
    const gap = (new Date(sortedTx[i].blockTime).getTime() - new Date(sortedTx[i-1].blockTime).getTime()) / 60000;
    if (gap < 1440) gaps.push(gap);
  }
  const avgGap = gaps.length > 0 ? gaps.reduce((s, g) => s + g, 0) / gaps.length : 0;
  const gapStdDev = gaps.length > 1 
    ? Math.sqrt(gaps.reduce((s, g) => s + (g - avgGap) ** 2, 0) / (gaps.length - 1))
    : 0;
  const consistencyScore = avgGap > 0 ? Math.max(0, 1 - (gapStdDev / avgGap)) : 0;
  
  // Sharpe ratio approximation
  const returns = pnlTrades.map(t => t.pnlUsd || 0);
  const avgReturn = returns.length > 0 ? returns.reduce((s, r) => s + r, 0) / returns.length : 0;
  const returnStdDev = returns.length > 1
    ? Math.sqrt(returns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / (returns.length - 1))
    : 1;
  const sharpeRatio = returnStdDev > 0 ? avgReturn / returnStdDev : 0;
  
  // Max drawdown approximation
  let peak = 0;
  let maxDD = 0;
  let cumPnl = 0;
  for (const t of pnlTrades) {
    cumPnl += (t.pnlUsd || 0);
    if (cumPnl > peak) peak = cumPnl;
    const dd = peak - cumPnl;
    if (dd > maxDD) maxDD = dd;
  }
  
  return {
    totalTrades: transactions.length,
    winRate: pnlTrades.length > 0 ? wins.length / pnlTrades.length : 0,
    avgPnlUsd: pnlTrades.length > 0 ? totalPnl / pnlTrades.length : 0,
    totalPnlUsd: totalPnl,
    avgHoldTimeMin: avgHoldTime,
    avgTradeSizeUsd: avgTradeSize,
    avgEntryRank,
    earlyEntryCount: transactions.filter(t => (t.entryRank || 999) < 20).length,
    avgExitMultiplier: transactions.filter(t => t.exitMultiplier).reduce((s, t) => s + (t.exitMultiplier || 0), 0) / (transactions.filter(t => t.exitMultiplier).length || 1),
    totalHoldingsUsd: walletData.totalHoldingsUsd,
    uniqueTokensTraded: walletData.uniqueTokensTraded,
    preferredDexes: walletData.preferredDexes,
    preferredChains: walletData.preferredChains,
    sharpeRatio,
    profitFactor: totalLossPnl > 0 ? totalWinPnl / totalLossPnl : totalWinPnl > 0 ? 999 : 0,
    maxDrawdown: maxDD,
    consistencyScore,
    washTradeScore: walletData.washTradeScore || 0,
    copyTradeScore: walletData.copyTradeScore || 0,
    frontrunCount: transactions.filter(t => t.isFrontrun).length,
    sandwichCount: transactions.filter(t => t.isSandwich).length,
    tradingHourPattern,
    isActive247,
    avgTimeBetweenTradesMin: avgTimeBetweenTrades,
  };
}

/**
 * Convert TraderAnalytics to TraderMetrics for bot detection
 */
export function analyticsToMetrics(analytics: TraderAnalytics): TraderMetrics {
  return {
    totalTrades: analytics.totalTrades,
    avgTimeBetweenTradesMin: analytics.avgTimeBetweenTradesMin,
    consistencyScore: analytics.consistencyScore,
    isActive247: analytics.isActive247,
    isActiveAtNight: analytics.tradingHourPattern.slice(0, 6).some(h => h > 0.3) || 
                     analytics.tradingHourPattern.slice(22).some(h => h > 0.3),
    avgSlippageBps: 0, // Not in TraderAnalytics - would need transaction-level data
    frontrunCount: analytics.frontrunCount,
    sandwichCount: analytics.sandwichCount,
    washTradeScore: analytics.washTradeScore,
    copyTradeScore: analytics.copyTradeScore,
    mevExtractionUsd: 0, // Not in TraderAnalytics
    avgHoldTimeMin: analytics.avgHoldTimeMin,
    tradingHourPattern: analytics.tradingHourPattern,
    block0EntryCount: 0, // Not in TraderAnalytics - would need on-chain data
    avgBlockToTrade: 0,  // Not in TraderAnalytics
    priorityFeeUsd: 0,   // Not in TraderAnalytics
    justInTimeCount: 0,  // Not in TraderAnalytics
    multiHopCount: 0,    // Not in TraderAnalytics
    sameTokenPairCount: 0, // Not in TraderAnalytics
    selfTradeCount: 0,   // Not in TraderAnalytics
  };
}

// ============================================================
// MAIN ORCHESTRATOR
// ============================================================

/**
 * Run the complete brain analysis on a single token.
 * This is the main entry point - collects data, runs all engines, produces unified analysis.
 */
export async function analyzeToken(
  tokenAddress: string,
  chain: string = 'SOL',
  positionSizeUsd: number = 10,
  expectedGainPct: number = 5
): Promise<TokenAnalysis> {
  const warnings: string[] = [];
  const evidence: string[] = [];
  const analyzedAt = new Date();
  
  // === PHASE 1: MULTI-TIMEFRAME DATA COLLECTION ===
  let candlesAvailable = 0;
  let dataFreshness: TokenAnalysis['dataFreshness'] = 'NO_DATA';
  let multiTfData: Map<string, OHLCVSeries> | null = null;
  
  try {
    // Multi-timeframe collection: 5m, 1h, 4h, 1d for comprehensive analysis
    multiTfData = await ohlcvPipeline.getMultiTimeframeSeries(tokenAddress, 'FULL', 100);
    
    // Primary timeframe for data freshness: use 1h as anchor
    const primarySeries = multiTfData.get('1h');
    candlesAvailable = primarySeries?.count ?? 0;
    if (candlesAvailable >= 50) dataFreshness = 'LIVE';
    else if (candlesAvailable >= 10) dataFreshness = 'RECENT';
    else if (candlesAvailable > 0) dataFreshness = 'STALE';
    
    // Also check other timeframes
    const tf5m = multiTfData.get('5m');
    const tf1d = multiTfData.get('1d');
    if (tf5m && tf5m.count > 0) {
      evidence.push(`5m: ${tf5m.count} candles available`);
    }
    if (tf1d && tf1d.count > 0) {
      evidence.push(`1d: ${tf1d.count} candles available`);
    }
    
    if (candlesAvailable < 10) {
      warnings.push(`Only ${candlesAvailable} 1h candles available - trying backfill`);
      try {
        await ohlcvPipeline.backfillToken(tokenAddress, chain, ['5m', '1h', '4h', '1d']);
        multiTfData = await ohlcvPipeline.getMultiTimeframeSeries(tokenAddress, 'FULL', 100);
        const newSeries = multiTfData.get('1h');
        candlesAvailable = newSeries?.count ?? 0;
        if (candlesAvailable >= 10) dataFreshness = 'RECENT';
      } catch {
        warnings.push('Backfill failed for this token');
      }
    }
  } catch (error) {
    warnings.push('Could not access candle data');
  }
  
  // === PHASE 2: MARKET CONTEXT (Multi-Timeframe Analysis) ===
  let regime: TokenAnalysis['regime'] = 'SIDEWAYS';
  let regimeConfidence = 0;
  let volatilityRegime: TokenAnalysis['volatilityRegime'] = 'NORMAL';
  
  try {
    // Use multi-timeframe data for regime detection
    // Primary: 1h for regime, 5m for short-term, 1d for long-term confirmation
    const series1h = multiTfData?.get('1h') ?? await ohlcvPipeline.getCandleSeries(tokenAddress, '1h', 100);
    
    if (series1h.closes.length >= 50) {
      const regimeResult = detectMarketRegime(series1h.closes);
      regime = regimeResult.regime;
      regimeConfidence = regimeResult.confidence;
      evidence.push(...regimeResult.evidence.slice(0, 5));
      
      // Multi-timeframe regime confirmation
      const series1d = multiTfData?.get('1d');
      if (series1d && series1d.closes.length >= 20) {
        const dailyRegime = detectMarketRegime(series1d.closes);
        // If daily and hourly agree, increase confidence
        if (dailyRegime.regime === regime) {
          regimeConfidence = Math.min(1, regimeConfidence * 1.3);
          evidence.push(`Multi-TF regime confirmed: ${regime} on 1h+1d`);
        } else {
          regimeConfidence *= 0.7;
          evidence.push(`Multi-TF regime divergence: 1h=${regime}, 1d=${dailyRegime.regime}`);
        }
      }
      
      // Derive volatility from ATR
      const normalizedATR = regimeResult.metrics.volatility;
      if (normalizedATR > 5) volatilityRegime = 'EXTREME';
      else if (normalizedATR > 3) volatilityRegime = 'HIGH';
      else if (normalizedATR < 1.5) volatilityRegime = 'LOW';
    }
  } catch {
    warnings.push('Could not compute market regime');
  }
  
  // === PHASE 3: TOKEN LIFECYCLE ===
  let lifecyclePhase: UnifiedPhase = 'INCIPIENT';
  let lifecycleConfidence = 0;
  let tradingPhase = 'LAUNCH';
  let isTransitioning = false;
  
  try {
    const phaseResult = await tokenLifecycleEngine.detectPhase(tokenAddress, chain);
    lifecyclePhase = phaseResult.phase as UnifiedPhase;
    lifecycleConfidence = phaseResult.probability;
    tradingPhase = LIFECYCLE_TO_TRADING_PHASE[lifecyclePhase] || 'LAUNCH';
    
    // Check for transition
    const transitionResult = await tokenLifecycleEngine.detectTransition(tokenAddress);
    if (transitionResult) {
      // A transition exists if `from` and `to` differ
      isTransitioning = transitionResult.from !== transitionResult.to;
      if (isTransitioning) {
        evidence.push(`Phase transitioning: ${transitionResult.from} → ${transitionResult.to}`);
      }
    }
  } catch {
    warnings.push('Could not detect lifecycle phase');
  }
  
  // === PHASE 4: TRADER BEHAVIOR ===
  let netBehaviorFlow: TokenAnalysis['netBehaviorFlow'] = 'NEUTRAL';
  let behaviorConfidence = 0;
  let dominantArchetype = 'UNKNOWN';
  let behaviorAnomaly = false;
  let behaviorResult: Awaited<ReturnType<typeof behavioralModelEngine.predictBehavior>> | null = null;
  
  try {
    behaviorResult = await behavioralModelEngine.predictBehavior(tokenAddress, chain);
    // Map netFlowScore (-1 to 1) to BULLISH/BEARISH/NEUTRAL
    netBehaviorFlow = behaviorResult.netFlowScore > 0.15 ? 'BULLISH' 
      : behaviorResult.netFlowScore < -0.15 ? 'BEARISH' 
      : 'NEUTRAL';
    behaviorConfidence = behaviorResult.confidence;
    if (behaviorResult.archetypeBreakdown.length > 0) {
      dominantArchetype = behaviorResult.archetypeBreakdown
        .sort((a, b) => b.volumeShare - a.volumeShare)[0].archetype;
    }
    
    // Check for anomaly
    try {
      const anomalyResult = await behavioralModelEngine.detectBehaviorAnomaly(tokenAddress, chain);
      if (anomalyResult) {
        behaviorAnomaly = anomalyResult.deviationScore > 2; // z-score > 2 = anomaly
        if (behaviorAnomaly) {
          evidence.push(`Behavior anomaly: predicted=${anomalyResult.predictedDirection} observed=${anomalyResult.observedDirection} deviation=${anomalyResult.deviationScore.toFixed(2)}`);
        }
      }
    } catch {
      // Anomaly detection is optional
    }
  } catch {
    warnings.push('Could not predict trader behavior');
  }
  
  // === PHASE 5: BOT & WHALE (from DB data if available) ===
  let botSwarmLevel: TokenAnalysis['botSwarmLevel'] = 'NONE';
  let dominantBotType: string | null = null;
  let whaleDirection: TokenAnalysis['whaleDirection'] = 'NEUTRAL';
  let whaleConfidence = 0;
  let smartMoneyFlow: TokenAnalysis['smartMoneyFlow'] = 'NEUTRAL';
  
  try {
    const { db } = await import('@/lib/db');
    
    // Get token DNA for bot/whale info
    const token = await db.token.findUnique({
      where: { address: tokenAddress },
      include: { dna: true },
    });
    
    if (token) {
      // Bot swarm from token data
      const botPct = token.botActivityPct;
      if (botPct > 80) botSwarmLevel = 'CRITICAL';
      else if (botPct > 60) botSwarmLevel = 'HIGH';
      else if (botPct > 40) botSwarmLevel = 'MEDIUM';
      else if (botPct > 20) botSwarmLevel = 'LOW';
      
      // Smart money flow from token data
      const smPct = token.smartMoneyPct;
      if (smPct > 30) smartMoneyFlow = 'INFLOW';
      else if (smPct < 10) smartMoneyFlow = 'OUTFLOW';
      
      // If we have DNA, get more detail
      if (token.dna) {
        const dna = token.dna;
        if (dna.botActivityScore > 70) botSwarmLevel = 'HIGH';
        if (dna.mevPct > 30) dominantBotType = 'MEV_EXTRACTOR';
        else if (dna.sniperPct > 30) dominantBotType = 'SNIPER_BOT';
        else if (dna.copyBotPct > 30) dominantBotType = 'COPY_BOT';
        
        evidence.push(`DNA: Bot=${dna.botActivityScore.toFixed(0)} SM=${dna.smartMoneyScore.toFixed(0)} Whale=${dna.whaleScore.toFixed(0)}`);
      }
      
      // Whale direction from recent transactions
      const recentWhaleTx = await db.traderTransaction.findMany({
        where: {
          tokenAddress,
          trader: { isWhale: true },
        },
        orderBy: { blockTime: 'desc' },
        take: 20,
      });
      
      if (recentWhaleTx.length > 0) {
        const whaleBuys = recentWhaleTx.filter(t => t.action === 'BUY').length;
        const whaleSells = recentWhaleTx.filter(t => t.action === 'SELL').length;
        const netBuys = whaleBuys - whaleSells;
        
        if (netBuys > 3) {
          whaleDirection = 'ACCUMULATING';
          whaleConfidence = Math.min(0.9, netBuys / recentWhaleTx.length);
        } else if (netBuys < -3) {
          whaleDirection = 'DISTRIBUTING';
          whaleConfidence = Math.min(0.9, Math.abs(netBuys) / recentWhaleTx.length);
        } else if (whaleBuys > 0 && whaleSells > 0) {
          whaleDirection = 'ROTATING';
          whaleConfidence = 0.4;
        }
        
        evidence.push(`Whale: ${whaleBuys} buys, ${whaleSells} sells → ${whaleDirection}`);
      }
    }
  } catch (error) {
    warnings.push('Could not access token DB data for bot/whale analysis');
  }
  
  // === PHASE 6: OPERABILITY ===
  let operabilityScore = 0;
  let operabilityLevel = 'UNOPERABLE';
  let isOperable = false;
  let feeEstimate = { totalCostUsd: 0, totalCostPct: 0, slippagePct: 0 };
  let recommendedPositionUsd = 0;
  let minimumGainPct = 0;
  
  try {
    const { db } = await import('@/lib/db');
    const token = await db.token.findUnique({ where: { address: tokenAddress } });
    
    if (token) {
      const operInput: OperabilityInput = {
        tokenAddress,
        symbol: token.symbol,
        chain: token.chain as 'SOL' | 'ETH' | string,
        priceUsd: token.priceUsd,
        liquidityUsd: token.liquidity,
        volume24h: token.volume24h,
        marketCap: token.marketCap,
        positionSizeUsd,
        expectedGainPct,
        botActivityPct: token.botActivityPct,
        holderCount: token.holderCount,
        priceChange24h: token.priceChange24h,
        dexId: token.dexId || undefined,
        pairCreatedAt: token.createdAt ? new Date(token.createdAt).getTime() : undefined,
      };
      
      const operResult = calculateOperabilityScore(operInput);
      operabilityScore = operResult.overallScore;
      operabilityLevel = operResult.level;
      isOperable = operResult.isOperable;
      feeEstimate = {
        totalCostUsd: operResult.feeEstimate.totalCostUsd,
        totalCostPct: operResult.feeEstimate.totalCostPct,
        slippagePct: operResult.feeEstimate.slippagePct,
      };
      recommendedPositionUsd = operResult.recommendedPositionUsd;
      minimumGainPct = operResult.minimumGainPct;
      warnings.push(...operResult.warnings);
    } else {
      warnings.push('Token not found in DB - cannot assess operability');
    }
  } catch {
    warnings.push('Could not compute operability score');
  }
  
  // === PHASE 7: PREDICTIVE SIGNALS ===
  let meanReversionZone: TokenAnalysis['meanReversionZone'] = null;
  let anomalyDetected = false;
  let anomalyScore = 0;
  
  try {
    // Use multi-timeframe data for predictive signals
    const series1h = multiTfData?.get('1h') ?? await ohlcvPipeline.getCandleSeries(tokenAddress, '1h', 100);
    if (series1h.closes.length >= 20) {
      // Mean reversion zones
      const mrz = calculateMeanReversionZones(series1h.closes);
      if (mrz.probabilityOfReversion > 0.5) {
        meanReversionZone = {
          upperBound: mrz.upperBound,
          lowerBound: mrz.lowerBound,
          probabilityOfReversion: mrz.probabilityOfReversion,
        };
        evidence.push(`Mean reversion zone: $${mrz.lowerBound.toFixed(4)} - $${mrz.upperBound.toFixed(4)} (${(mrz.probabilityOfReversion * 100).toFixed(0)}%)`);
      }
      
      // Anomaly detection on volume
      if (series1h.volumes.length >= 20) {
        const recentVolumes = series1h.volumes.slice(-10);
        const baselineVolumes = series1h.volumes.slice(-30, -10);
        if (baselineVolumes.length >= 5) {
          const anomalyResult = detectAnomalies(recentVolumes, baselineVolumes);
          anomalyDetected = anomalyResult.aggregate.isAnomaly;
          anomalyScore = anomalyResult.aggregate.anomalyScore;
          if (anomalyDetected) {
            evidence.push(`Volume anomaly detected (score: ${anomalyScore.toFixed(2)})`);
          }
        }
      }
    }
    
    // Short-term anomaly check on 5m candles (if available)
    const series5m = multiTfData?.get('5m');
    if (series5m && series5m.volumes.length >= 20) {
      const recent5m = series5m.volumes.slice(-10);
      const baseline5m = series5m.volumes.slice(-30, -10);
      if (baseline5m.length >= 5) {
        const shortTermAnomaly = detectAnomalies(recent5m, baseline5m);
        if (shortTermAnomaly.aggregate.isAnomaly && !anomalyDetected) {
          anomalyDetected = true;
          anomalyScore = shortTermAnomaly.aggregate.anomalyScore * 0.8; // Slightly lower confidence on 5m
          evidence.push(`5m volume anomaly detected (score: ${anomalyScore.toFixed(2)})`);
        }
      }
    }
  } catch {
    // Predictive signals are optional
  }
  
  // === PHASE 8: CANDLESTICK PATTERN SCAN (NEW) ===
  let patternScanResult: PatternScanResult | null = null;
  let patternSignal: 'BULLISH' | 'BEARISH' | 'NEUTRAL' = 'NEUTRAL';
  let patternScore = 0;
  let dominantPattern: string | null = null;
  let patternConfluences = 0;

  try {
    patternScanResult = await candlestickPatternEngine.scanToken(tokenAddress, chain);
    patternSignal = patternScanResult.overallSignal;
    patternScore = patternScanResult.overallScore;
    dominantPattern = patternScanResult.dominantPattern;
    patternConfluences = patternScanResult.confluences.length;

    if (patternScanResult.patterns.length > 0) {
      evidence.push(`Patterns: ${patternScanResult.bullishPatterns.length}B/${patternScanResult.bearishPatterns.length}R = ${patternSignal} (${patternScore.toFixed(2)})`);
    }
    if (patternConfluences > 0) {
      evidence.push(`Pattern confluences: ${patternScanResult.confluences.map(c => `${c.pattern}(${c.timeframes.join('+')})`).join(', ')}`);
    }
  } catch (error) {
    warnings.push('Candlestick pattern scan failed');
  }

  // === PHASE 9: DEEP ANALYSIS + LLM (NEW) ===
  let deepAnalysis: DeepAnalysisResult | null = null;
  let deepRecommendation: string | null = null;
  let deepRiskLevel: string | null = null;
  let deepRiskScore = 50;

  try {
    // Build a partial analysis for the deep analysis engine
    const partialAnalysis: TokenAnalysis = {
      tokenAddress, symbol: '', chain, analyzedAt,
      dataFreshness, candlesAvailable,
      regime, regimeConfidence, volatilityRegime,
      lifecyclePhase, lifecycleConfidence, tradingPhase, isTransitioning,
      netBehaviorFlow, behaviorConfidence, dominantArchetype, behaviorAnomaly,
      botSwarmLevel, dominantBotType, whaleDirection, whaleConfidence, smartMoneyFlow,
      operabilityScore, operabilityLevel, isOperable, feeEstimate,
      recommendedPositionUsd, minimumGainPct,
      meanReversionZone, anomalyDetected, anomalyScore,
      patternScanResult, patternSignal, patternScore, dominantPattern, patternConfluences,
      deepAnalysis: null, deepRecommendation: null, deepRiskLevel: null, deepRiskScore: 50,
      crossCorrelation: null, correlatedOutcome: 'NEUTRAL', correlatedProbability: 0.33, correlationConflict: false,
      recommendedSystems: [], action: 'SKIP', actionReason: '',
      warnings, evidence,
    };

    deepAnalysis = await deepAnalysisEngine.analyze({
      tokenAddress,
      symbol: '',
      chain,
      brainAnalysis: partialAnalysis,
      patternScan: patternScanResult ?? undefined,
      behavioralPrediction: behaviorResult ?? undefined,
      depth: 'STANDARD',
    });

    deepRecommendation = deepAnalysis.recommendation;
    deepRiskLevel = deepAnalysis.riskLevel;
    deepRiskScore = deepAnalysis.riskScore;

    evidence.push(`Deep analysis (${deepAnalysis.source}): ${deepRecommendation} risk=${deepRiskLevel}`);
  } catch (error) {
    warnings.push('Deep analysis failed');
  }

  // === PHASE 10: CROSS-CORRELATION P(outcome | trader + pattern + phase) (NEW) ===
  let crossCorrelation: CrossCorrelationResult | null = null;
  let correlatedOutcome: 'BULLISH' | 'BEARISH' | 'NEUTRAL' = 'NEUTRAL';
  let correlatedProbability = 0.33;
  let correlationConflict = false;

  try {
    if (patternScanResult && behaviorResult) {
      const corrInput = crossCorrelationEngine.buildInput(
        {
          tokenAddress, symbol: '', chain, analyzedAt,
          dataFreshness, candlesAvailable,
          regime, regimeConfidence, volatilityRegime,
          lifecyclePhase, lifecycleConfidence, tradingPhase, isTransitioning,
          netBehaviorFlow, behaviorConfidence, dominantArchetype, behaviorAnomaly,
          botSwarmLevel, dominantBotType, whaleDirection, whaleConfidence, smartMoneyFlow,
          operabilityScore, operabilityLevel, isOperable, feeEstimate,
          recommendedPositionUsd, minimumGainPct,
          meanReversionZone, anomalyDetected, anomalyScore,
          patternScanResult, patternSignal, patternScore, dominantPattern, patternConfluences,
          deepAnalysis, deepRecommendation, deepRiskLevel, deepRiskScore,
          crossCorrelation: null, correlatedOutcome: 'NEUTRAL', correlatedProbability: 0.33, correlationConflict: false,
          recommendedSystems: [], action: 'SKIP', actionReason: '',
          warnings, evidence,
        },
        patternScanResult,
        behaviorResult,
      );

      crossCorrelation = await crossCorrelationEngine.predict(corrInput);
      correlatedOutcome = crossCorrelation.prediction.outcome;
      correlatedProbability = crossCorrelation.prediction.probability;
      correlationConflict = crossCorrelation.conflictDetected;

      evidence.push(`Cross-correlation: P(${correlatedOutcome})=${(correlatedProbability * 100).toFixed(0)}%${correlationConflict ? ' [CONFLICT]' : ''}`);
    }
  } catch (error) {
    warnings.push('Cross-correlation prediction failed');
  }

  // === PHASE 11: RECOMMENDED ACTION (enhanced with new engines) ===
  let recommendedSystems: string[] = [];
  let action: TokenAnalysis['action'] = 'SKIP';
  let actionReason = '';
  
  // System recommendation based on lifecycle phase
  const phaseSystemMap: Record<UnifiedPhase, string[]> = {
    'GENESIS': ['alpha-hunter', 'bot-aware'],
    'INCIPIENT': ['alpha-hunter', 'smart-money', 'bot-aware'],
    'GROWTH': ['smart-money', 'technical', 'adaptive'],
    'FOMO': ['bot-aware', 'defensive', 'technical'],
    'DECLINE': ['defensive', 'deep-research'],
    'LEGACY': ['defensive', 'deep-research'],
  };
  
  recommendedSystems = phaseSystemMap[lifecyclePhase] || ['technical'];
  
  // Enhanced action decision using all engines
  const deepSaysAvoid = deepRecommendation && ['AVOID', 'SELL', 'REDUCE'].includes(deepRecommendation);
  const deepSaysBuy = deepRecommendation && ['STRONG_BUY', 'BUY'].includes(deepRecommendation);
  const correlationSaysBearish = correlatedOutcome === 'BEARISH' && correlatedProbability > 0.45;
  const allEnginesBullish = patternSignal === 'BULLISH' && netBehaviorFlow === 'BULLISH' && correlatedOutcome === 'BULLISH';

  if (!isOperable) {
    action = 'SKIP';
    actionReason = `Not operable: ${operabilityLevel} score (${operabilityScore}/100)`;
  } else if (deepSaysAvoid || (correlationSaysBearish && correlationConflict)) {
    action = 'AVOID';
    actionReason = deepSaysAvoid 
      ? `Deep analysis: ${deepRecommendation} (risk: ${deepRiskLevel})`
      : `Cross-correlation conflict + bearish (${(correlatedProbability * 100).toFixed(0)}%)`;
  } else if (operabilityLevel === 'RISKY' || operabilityLevel === 'MARGINAL') {
    action = 'WATCH';
    actionReason = `Marginally operable (${operabilityLevel}) - watch for better entry`;
  } else if (botSwarmLevel === 'CRITICAL') {
    action = 'AVOID';
    actionReason = 'Critical bot swarm detected - retail will be front-run';
  } else if (regime === 'BEAR' && lifecyclePhase === 'DECLINE') {
    action = 'AVOID';
    actionReason = 'Bear regime + decline phase = high loss probability';
  } else if (behaviorAnomaly && netBehaviorFlow === 'BEARISH') {
    action = 'WATCH';
    actionReason = 'Behavioral anomaly + bearish flow - wait for clarity';
  } else if (isOperable && ['PREMIUM', 'GOOD'].includes(operabilityLevel) && (deepSaysBuy || allEnginesBullish)) {
    action = 'TRADE';
    actionReason = `Multi-engine confirm: Operable (${operabilityLevel}) + ${deepSaysBuy ? 'deep=BUY' : 'allBullish'} + ${regime} regime + ${lifecyclePhase} phase`;
  } else if (isOperable && ['PREMIUM', 'GOOD'].includes(operabilityLevel)) {
    action = 'TRADE';
    actionReason = `Operable (${operabilityLevel}) + ${regime} regime + ${lifecyclePhase} phase`;
  } else {
    action = 'WATCH';
    actionReason = `Operable but marginal conditions (${operabilityLevel}, ${regime}, ${lifecyclePhase})`;
  }
  
  return {
    tokenAddress,
    symbol: '', // Filled by caller from DB
    chain,
    analyzedAt,
    dataFreshness,
    candlesAvailable,
    regime,
    regimeConfidence,
    volatilityRegime,
    lifecyclePhase,
    lifecycleConfidence,
    tradingPhase,
    isTransitioning,
    netBehaviorFlow,
    behaviorConfidence,
    dominantArchetype,
    behaviorAnomaly,
    botSwarmLevel,
    dominantBotType,
    whaleDirection,
    whaleConfidence,
    smartMoneyFlow,
    operabilityScore,
    operabilityLevel,
    isOperable,
    feeEstimate,
    recommendedPositionUsd,
    minimumGainPct,
    meanReversionZone,
    anomalyDetected,
    anomalyScore,
    patternScanResult,
    patternSignal,
    patternScore,
    dominantPattern,
    patternConfluences,
    deepAnalysis,
    deepRecommendation,
    deepRiskLevel,
    deepRiskScore,
    crossCorrelation,
    correlatedOutcome,
    correlatedProbability,
    correlationConflict,
    recommendedSystems,
    action,
    actionReason,
    warnings,
    evidence,
  };
}

/**
 * Run batch analysis on multiple tokens
 */
export async function analyzeBatch(
  tokenAddresses: string[],
  chain: string = 'SOL',
  positionSizeUsd: number = 10,
  expectedGainPct: number = 5
): Promise<BatchAnalysisResult> {
  const results: TokenAnalysis[] = [];
  
  // Process sequentially to avoid rate limits
  for (const address of tokenAddresses) {
    try {
      const analysis = await analyzeToken(address, chain, positionSizeUsd, expectedGainPct);
      
      // Fill symbol from DB
      try {
        const { db } = await import('@/lib/db');
        const token = await db.token.findUnique({ where: { address }, select: { symbol: true } });
        if (token) analysis.symbol = token.symbol;
      } catch { /* ignore */ }
      
      results.push(analysis);
    } catch (error) {
      console.error(`[Brain] Failed to analyze ${address}:`, error);
    }
    
    // Small delay to avoid rate limiting
    await new Promise(r => setTimeout(r, 200));
  }
  
  // Categorize
  const operableTokens = results.filter(r => r.isOperable);
  const tradeableTokens = results.filter(r => r.action === 'TRADE');
  const watchlistTokens = results.filter(r => r.action === 'WATCH');
  const avoidTokens = results.filter(r => ['AVOID', 'SKIP'].includes(r.action));
  
  // Summary
  const byPhase: Record<string, number> = {};
  const byRegime: Record<string, number> = {};
  for (const r of results) {
    byPhase[r.lifecyclePhase] = (byPhase[r.lifecyclePhase] || 0) + 1;
    byRegime[r.regime] = (byRegime[r.regime] || 0) + 1;
  }
  
  return {
    results,
    operableTokens,
    tradeableTokens,
    watchlistTokens,
    avoidTokens,
    summary: {
      total: results.length,
      operable: operableTokens.length,
      tradeable: tradeableTokens.length,
      byPhase,
      byRegime,
      avgOperability: results.length > 0
        ? results.reduce((s, r) => s + r.operabilityScore, 0) / results.length
        : 0,
    },
  };
}

/**
 * Profile a wallet: build analytics → run profiler → run bot detection → store results
 */
export async function profileWallet(
  address: string,
  chain: string = 'SOL'
): Promise<{
  profile: WalletProfile;
  botDetection: BotDetectionResult;
  stored: boolean;
}> {
  const { db } = await import('@/lib/db');
  const { DataIngestionPipeline } = await import('./data-ingestion');
  
  // 1. Get transaction history from on-chain
  const pipeline = new DataIngestionPipeline();
  const history = await pipeline.getWalletHistory(address, chain);
  
  // 2. Get existing wallet data from DB or create defaults
  let trader = await db.trader.findUnique({ where: { address } });
  
  const walletData = {
    totalHoldingsUsd: trader?.totalHoldingsUsd || 0,
    uniqueTokensTraded: trader?.uniqueTokensTraded || 0,
    preferredDexes: trader ? JSON.parse(trader.preferredDexes || '[]') : [],
    preferredChains: trader ? JSON.parse(trader.preferredChains || '[]') : [chain],
    washTradeScore: trader?.washTradeScore || 0,
    copyTradeScore: trader?.copyTradeScore || 0,
  };
  
  // 3. Build analytics from transactions
  const analytics = buildTraderAnalyticsFromTransactions(
    history.transactions.map(tx => ({
      action: tx.action,
      valueUsd: tx.valueUsd,
      pnlUsd: undefined, // Not available from ingestion
      holdTimeMin: undefined,
      entryRank: undefined,
      exitMultiplier: undefined,
      slippageBps: tx.slippageBps,
      isFrontrun: tx.isFrontrun,
      isSandwich: tx.isSandwich,
      blockTime: tx.blockTime,
      tokenAddress: tx.tokenAddress,
      dex: tx.dex,
    })),
    walletData
  );
  
  // 4. Run wallet profiler
  const profile = buildWalletProfile(address, chain, analytics);
  
  // 5. Run bot detection
  const metrics = analyticsToMetrics(analytics);
  const botDetection = detectBot(metrics);
  
  // 6. Store results in DB
  let stored = false;
  try {
    await db.trader.upsert({
      where: { address },
      create: {
        address,
        chain,
        primaryLabel: profile.primaryLabel,
        labelConfidence: profile.labelConfidence,
        isBot: botDetection.isBot,
        botType: botDetection.botType,
        botConfidence: botDetection.confidence,
        botDetectionSignals: JSON.stringify(botDetection.signals.map(s => s.type)),
        smartMoneyScore: profile.smartMoneyScore,
        isSmartMoney: profile.smartMoneyScore > 60,
        whaleScore: profile.whaleScore,
        isWhale: profile.whaleScore > 50,
        sniperScore: profile.sniperScore,
        isSniper: profile.sniperScore > 50,
        totalTrades: analytics.totalTrades,
        winRate: analytics.winRate,
        avgPnl: analytics.avgPnlUsd,
        totalPnl: analytics.totalPnlUsd,
        sharpeRatio: analytics.sharpeRatio,
        profitFactor: analytics.profitFactor,
        consistencyScore: analytics.consistencyScore,
        washTradeScore: analytics.washTradeScore,
        copyTradeScore: analytics.copyTradeScore,
        frontrunCount: analytics.frontrunCount,
        sandwichCount: analytics.sandwichCount,
        isActive247: analytics.isActive247,
        avgTimeBetweenTrades: analytics.avgTimeBetweenTradesMin,
        tradingHourPattern: JSON.stringify(analytics.tradingHourPattern),
        preferredDexes: JSON.stringify(analytics.preferredDexes),
        preferredChains: JSON.stringify(analytics.preferredChains),
        totalHoldingsUsd: analytics.totalHoldingsUsd,
        uniqueTokensTraded: analytics.uniqueTokensTraded,
        avgHoldTimeMin: analytics.avgHoldTimeMin,
        avgTradeSizeUsd: analytics.avgTradeSizeUsd,
        maxDrawdown: analytics.maxDrawdown,
        lastAnalyzed: new Date(),
      },
      update: {
        primaryLabel: profile.primaryLabel,
        labelConfidence: profile.labelConfidence,
        isBot: botDetection.isBot,
        botType: botDetection.botType,
        botConfidence: botDetection.confidence,
        smartMoneyScore: profile.smartMoneyScore,
        isSmartMoney: profile.smartMoneyScore > 60,
        whaleScore: profile.whaleScore,
        isWhale: profile.whaleScore > 50,
        sniperScore: profile.sniperScore,
        isSniper: profile.sniperScore > 50,
        totalTrades: analytics.totalTrades,
        winRate: analytics.winRate,
        avgPnl: analytics.avgPnlUsd,
        totalPnl: analytics.totalPnlUsd,
        sharpeRatio: analytics.sharpeRatio,
        profitFactor: analytics.profitFactor,
        consistencyScore: analytics.consistencyScore,
        washTradeScore: analytics.washTradeScore,
        copyTradeScore: analytics.copyTradeScore,
        frontrunCount: analytics.frontrunCount,
        sandwichCount: analytics.sandwichCount,
        isActive247: analytics.isActive247,
        avgTimeBetweenTrades: analytics.avgTimeBetweenTradesMin,
        tradingHourPattern: JSON.stringify(analytics.tradingHourPattern),
        lastAnalyzed: new Date(),
      },
    });
    stored = true;
  } catch (error) {
    console.error('[Brain] Failed to store wallet profile:', error);
  }
  
  return { profile, botDetection, stored };
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const brainOrchestrator = {
  analyzeToken,
  analyzeBatch,
  profileWallet,
  buildTraderAnalyticsFromTransactions,
  analyticsToMetrics,
};
