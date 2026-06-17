import { NextRequest, NextResponse } from 'next/server';
import type { ThinkingDepth, DeepAnalysisResult, DeepAnalysis, DeepAnalysisInput } from '@/lib/services/strategy/deep-analysis-engine';
import type { TokenAnalysis } from '@/lib/services/brain/brain-orchestrator';
import { db } from '@/lib/db';

/**
 * Transform DeepAnalysisResult (flat API format) into DeepAnalysis (nested UI format).
 * The frontend expects the DeepAnalysis shape with verdict, phaseAssessment, etc.
 * The engine's analyze() returns DeepAnalysisResult with different field names.
 *
 * Now depth-aware: QUICK produces minimal output, STANDARD moderate, DEEP comprehensive.
 */
function transformToDeepAnalysis(result: DeepAnalysisResult): DeepAnalysis {
  // Map recommendation to verdict action
  const actionMap: Record<string, string> = {
    STRONG_BUY: 'STRONG_BUY', BUY: 'BUY', HOLD: 'HOLD',
    SELL: 'SELL', STRONG_SELL: 'STRONG_SELL', WAIT: 'WAIT',
    REDUCE: 'REDUCE', AVOID: 'AVOID',
  };

  const verdictAction = actionMap[result.recommendation] || 'HOLD';

  const isDeep = result.depth === 'DEEP';
  const isQuick = result.depth === 'QUICK';

  // Build verdict (depth-dependent)
  const verdict: DeepAnalysis['verdict'] = {
    action: verdictAction,
    confidence: result.recommendationConfidence,
    reasoning: isQuick
      ? result.summary || 'Quick scan complete'
      : result.summary || result.justification?.join('. ') || 'Analysis complete',
    summary: result.summary,
    criticalNote: result.urgencyLevel === 'IMMEDIATE' ? 'Immediate action recommended' :
      (isDeep && result.stressTestResults?.maxDrawdownEstimate && result.stressTestResults.maxDrawdownEstimate < -30)
        ? `Stress test: max drawdown estimate ${result.stressTestResults.maxDrawdownEstimate.toFixed(0)}%`
        : undefined,
  };

  // Build phase assessment (depth-dependent)
  const phaseAssessment: DeepAnalysis['phaseAssessment'] = {
    phase: result.scenarios?.base?.description?.includes('bull') ? 'GROWTH'
      : result.scenarios?.base?.description?.includes('bear') ? 'DECLINE'
      : 'GROWTH',
    confidence: result.recommendationConfidence,
    timeInPhase: result.suggestedTimeHorizon || 'Unknown',
    narrative: isQuick
      ? `Phase: ${result.scenarios?.base?.description || 'Quick assessment'}`
      : isDeep
        ? (result.detailedAnalysis?.whaleAccumulationNarrative || result.scenarios?.base?.description || 'Comprehensive phase assessment')
        : result.scenarios?.base?.description || 'Market phase assessment',
  };

  // Build pattern assessment (depth-dependent)
  const patternAssessment: DeepAnalysis['patternAssessment'] = {
    dominantPattern: result.bullishFactors?.length > result.bearishFactors?.length ? 'Bullish Momentum' : 'Bearish Pressure',
    patternSentiment: verdictAction === 'HOLD' ? 'NEUTRAL' : verdictAction.includes('BUY') ? 'BULLISH' : 'BEARISH',
    multiTfConfirmed: result.justification?.length >= 3,
    narrative: isQuick
      ? (result.justification?.slice(0, 1).join('. ') || 'Quick pattern scan')
      : isDeep
        ? (result.justification?.slice(0, 5).join('. ') || 'Detailed pattern assessment')
        : result.justification?.slice(0, 3).join('. ') || 'Pattern assessment',
  };

  // Build trader assessment (depth-dependent)
  const traderAssessment: DeepAnalysis['traderAssessment'] = {
    dominantArchetype: result.riskLevel === 'VERY_LOW' || result.riskLevel === 'LOW' ? 'HOLDER' : 'SPECULATOR',
    behaviorFlow: verdictAction.includes('BUY') ? 'ACCUMULATING' : verdictAction.includes('SELL') ? 'DISTRIBUTING' : 'NEUTRAL',
    riskFromBots: isDeep ? (result.detailedAnalysis?.botSwarmImpactAnalysis?.includes('CRITICAL') ? 'CRITICAL' : result.detailedAnalysis?.botSwarmImpactAnalysis?.includes('HIGH') ? 'HIGH' : result.riskLevel === 'HIGH' || result.riskLevel === 'VERY_HIGH' ? 'MEDIUM' : 'LOW') : (result.riskLevel === 'EXTREME' ? 'HIGH' : result.riskLevel === 'HIGH' ? 'MEDIUM' : 'LOW'),
    riskFromWhales: result.riskLevel === 'EXTREME' || result.riskLevel === 'VERY_HIGH' ? 'ELEVATED' : 'MODERATE',
    narrative: isDeep
      ? (result.detailedAnalysis?.botSwarmImpactAnalysis || result.riskAssessment || 'Detailed trader assessment')
      : isQuick
        ? 'Quick trader assessment'
        : result.riskAssessment || 'Trader behavior assessment',
  };

  // Build risk assessment (depth-dependent)
  const riskAssessment: DeepAnalysis['riskAssessment'] = {
    overallRisk: result.riskLevel || 'MEDIUM',
    keyRisks: result.bearishFactors?.slice(0, isDeep ? 8 : isQuick ? 2 : 5) || [],
    mitigatingFactors: result.bullishFactors?.slice(0, isDeep ? 8 : isQuick ? 2 : 5) || [],
    blackSwanRisk: result.riskLevel === 'EXTREME' || result.riskLevel === 'VERY_HIGH' ? 'ELEVATED' : isDeep && result.stressTestResults ? 'MODELED' : 'LOW',
  };

  // Build strategy recommendation (depth-dependent)
  const strategyRecommendation: DeepAnalysis['strategyRecommendation'] = {
    strategy: verdictAction.includes('BUY') ? 'LONG_ENTRY' : verdictAction.includes('SELL') || verdictAction === 'REDUCE' ? 'SHORT_OR_EXIT' : 'WAIT_AND_MONITOR',
    direction: verdictAction.includes('BUY') ? 'LONG' : verdictAction.includes('SELL') || verdictAction === 'REDUCE' ? 'SHORT' : 'NEUTRAL',
    confidenceLevel: result.recommendationConfidence,
    positionSizeRecommendation: isDeep && result.detailedAnalysis?.riskAdjustedPositionSizing
      ? `Conservative: ${result.detailedAnalysis.riskAdjustedPositionSizing.conservativePct}% | Moderate: ${result.detailedAnalysis.riskAdjustedPositionSizing.moderatePct}% | Aggressive: ${result.detailedAnalysis.riskAdjustedPositionSizing.aggressivePct}%`
      : `${result.maxRecommendedPositionPct || 5}% of portfolio`,
    stopLossRecommendation: `${((result.scenarios?.bear?.targetPct || -10)).toFixed(1)}% from entry`,
    takeProfitRecommendation: `${((result.scenarios?.bull?.targetPct || 15)).toFixed(1)}% from entry`,
    entryConditions: isDeep
      ? (verdictAction.includes('BUY') ? ['Wait for confirmation candle on 1H timeframe', 'Verify volume increase >150% avg', 'Check smart money flow alignment', 'Validate pattern breakout above resistance'] : ['N/A - No long entry recommended'])
      : isQuick
      ? (verdictAction.includes('BUY') ? ['Confirmation candle required'] : ['N/A'])
      : (verdictAction.includes('BUY') ? ['Wait for confirmation candle', 'Check volume increase'] : ['N/A']),
    exitConditions: isDeep
      ? (verdictAction.includes('SELL') || verdictAction === 'REDUCE' ? ['Exit on next resistance test', 'Trail stop loss at 5% below recent high', 'Monitor whale wallet movements', 'Watch for regime change signals'] : ['Hold until trend reversal signal', 'Trail stop at 10% below recent high', 'Monitor volume divergence'])
      : isQuick
      ? (verdictAction.includes('SELL') || verdictAction === 'REDUCE' ? ['Exit at resistance'] : ['Hold until reversal'])
      : (verdictAction.includes('SELL') || verdictAction === 'REDUCE' ? ['Exit on next resistance test', 'Trail stop loss'] : ['Hold until trend reversal']),
  };

  // Build evidence matrix (depth-dependent weights)
  const bullWeight = isQuick ? 0.6 : isDeep ? 0.8 : 0.7;
  const bearWeight = isQuick ? 0.5 : isDeep ? 0.7 : 0.6;
  const neutralWeight = isQuick ? 0.3 : isDeep ? 0.5 : 0.4;
  const pros = (result.bullishFactors || []).map((f, i) => ({
    factor: f,
    weight: Math.min(1, bullWeight + (isDeep ? i * 0.02 : 0)),
    explanation: f,
  }));
  const cons = (result.bearishFactors || []).map((f, i) => ({
    factor: f,
    weight: Math.min(1, bearWeight + (isDeep ? i * 0.02 : 0)),
    explanation: f,
  }));
  const neutrals = (result.neutralFactors || []).map(f => ({
    factor: f,
    weight: neutralWeight,
    explanation: f,
  }));

  // Build reasoning chain (depth-dependent)
  const maxJustification = isQuick ? 3 : isDeep ? 15 : 6;
  const reasoningChain = [
    `[RISK] Risk Level: ${result.riskLevel || 'MEDIUM'} (Score: ${result.riskScore || 50}/100)`,
    `[DATA] Source: ${result.source || 'RULE_BASED'}`,
    `[VERDICT] Confidence: ${((result.recommendationConfidence || 0.5) * 100).toFixed(0)}%`,
    ...result.justification?.slice(0, maxJustification) || [],
  ];
  if (!isQuick && result.scenarios?.bull) {
    reasoningChain.push(
      `[SCENARIO] Bull: ${(result.scenarios.bull.probability * 100).toFixed(0)}% prob, +${result.scenarios.bull.targetPct}%`,
      `[SCENARIO] Base: ${(result.scenarios.base.probability * 100).toFixed(0)}% prob, ${result.scenarios.base.targetPct > 0 ? '+' : ''}${result.scenarios.base.targetPct}%`,
      `[SCENARIO] Bear: ${(result.scenarios.bear.probability * 100).toFixed(0)}% prob, ${result.scenarios.bear.targetPct}%`,
    );
  }
  if (isDeep && result.extendedScenarios) {
    for (const sc of result.extendedScenarios) {
      reasoningChain.push(`[EXT-${sc.name}] ${(sc.probability * 100).toFixed(0)}% prob, ${sc.targetPct > 0 ? '+' : ''}${sc.targetPct}%`);
    }
  }
  if (isDeep && result.stressTestResults) {
    reasoningChain.push(
      `[STRESS] Black Swan: ${result.stressTestResults.blackSwanPct.toFixed(0)}%`,
      `[STRESS] Liquidity Crash: ${result.stressTestResults.liquidityCrashPct.toFixed(0)}%`,
      `[STRESS] Max Drawdown Est: ${result.stressTestResults.maxDrawdownEstimate.toFixed(0)}%`,
    );
  }
  if (isDeep && result.detailedAnalysis) {
    reasoningChain.push(`[PHASE] Transition Probability: ${(result.detailedAnalysis.phaseTransitionProbability * 100).toFixed(0)}%`);
    if (result.detailedAnalysis.invalidationLevels.length > 0) {
      for (const lvl of result.detailedAnalysis.invalidationLevels.slice(0, 3)) {
        reasoningChain.push(`[INVALIDATION] ${lvl}`);
      }
    }
    if (result.detailedAnalysis.keyAssumptions.length > 0) {
      for (const a of result.detailedAnalysis.keyAssumptions.slice(0, 3)) {
        reasoningChain.push(`[ASSUMPTION] ${a}`);
      }
    }
  }
  if (result.keyMonitorPoints?.length) {
    for (const pt of result.keyMonitorPoints.slice(0, isDeep ? 6 : isQuick ? 1 : 3)) {
      reasoningChain.push(`[DATA GAPS] ${pt}`);
    }
  }
  if (result.urgencyLevel) {
    reasoningChain.push(`[VERDICT] Urgency: ${result.urgencyLevel}`);
  }
  if (result.suggestedTimeHorizon) {
    reasoningChain.push(`[STRATEGY] Time Horizon: ${result.suggestedTimeHorizon}`);
  }

  return {
    tokenAddress: result.tokenAddress,
    symbol: result.symbol,
    chain: result.chain,
    depth: (result.depth as ThinkingDepth) || 'STANDARD',
    analyzedAt: result.analyzedAt,
    phaseAssessment,
    patternAssessment,
    traderAssessment,
    verdict,
    riskAssessment,
    strategyRecommendation,
    pros,
    cons,
    neutrals,
    reasoningChain,
    // Include raw result fields for additional data
    summary: result.summary,
    riskAssessmentText: result.riskAssessment,
    recommendation: result.recommendation,
    recommendationConfidence: result.recommendationConfidence,
    justification: result.justification,
    bullishFactors: result.bullishFactors,
    bearishFactors: result.bearishFactors,
    neutralFactors: result.neutralFactors,
    scenarios: result.scenarios,
    riskLevel: result.riskLevel,
    riskScore: result.riskScore,
    maxRecommendedPositionPct: result.maxRecommendedPositionPct,
    suggestedTimeHorizon: result.suggestedTimeHorizon,
    urgencyLevel: result.urgencyLevel,
    source: result.source,
    // DEEP-specific fields passed through for the UI
    extendedScenarios: result.extendedScenarios,
    stressTestResults: result.stressTestResults,
    detailedAnalysis: result.detailedAnalysis,
  } as unknown as DeepAnalysis;
}

/**
 * Build a fallback TokenAnalysis when analyzeToken() fails.
 * Provides reasonable defaults so the engine can still run all depth modes.
 */
function buildFallbackBrain(tokenAddress: string, chain: string, symbol: string): TokenAnalysis {
  return {
    tokenAddress,
    symbol,
    chain,
    analyzedAt: new Date(),
    dataFreshness: 'NO_DATA',
    candlesAvailable: 0,
    regime: 'SIDEWAYS',
    regimeConfidence: 0.3,
    volatilityRegime: 'NORMAL',
    lifecyclePhase: 'GROWTH',
    lifecycleConfidence: 0.3,
    tradingPhase: 'UNKNOWN',
    isTransitioning: false,
    netBehaviorFlow: 'NEUTRAL',
    behaviorConfidence: 0.3,
    dominantArchetype: 'UNKNOWN',
    behaviorAnomaly: false,
    botSwarmLevel: 'LOW',
    dominantBotType: null,
    whaleDirection: 'NEUTRAL',
    whaleConfidence: 0.3,
    smartMoneyFlow: 'NEUTRAL',
    operabilityScore: 30,
    operabilityLevel: 'RISKY',
    isOperable: false,
    feeEstimate: { totalCostUsd: 0, totalCostPct: 0, slippagePct: 0 },
    recommendedPositionUsd: 0,
    minimumGainPct: 0,
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
    correlatedProbability: 0,
    correlationConflict: false,
    recommendedSystems: [],
    action: 'SKIP',
    actionReason: 'Brain analysis unavailable',
    warnings: ['Brain orchestrator failed — using fallback data'],
    evidence: [],
  } as TokenAnalysis;
}

/**
 * Run the full deep analysis pipeline for a token.
 * Works with tokens from DB, DexScreener, or synthetic data.
 *
 * IMPORTANT: Passes a DeepAnalysisInput (with full brainAnalysis) directly
 * to the engine instead of an AnalysisInput. This preserves rich data from
 * analyzeToken() — anomalyDetected, isTransitioning, whaleConfidence,
 * volatilityRegime, etc. — which are critical for differentiating QUICK,
 * STANDARD, and DEEP output.
 */
async function runAnalysis(
  token: { id: string; address: string; symbol: string; chain?: string; priceUsd: number; priceChange24h: number; dna?: any | null },
  chain: string,
  depth: string,
) {
  const tokenAddress = token.address;

  // Run the brain orchestrator analysis first — this is the full TokenAnalysis
  const { analyzeToken } = await import('@/lib/services/brain/brain-orchestrator');
  let brainResult: TokenAnalysis;
  try {
    brainResult = await analyzeToken(tokenAddress, chain);
  } catch {
    brainResult = buildFallbackBrain(tokenAddress, chain, token.symbol);
  }

  // Run candlestick pattern scan
  const { candlestickPatternEngine } = await import('@/lib/services/brain/candlestick-pattern-engine');
  let patternScan;
  try {
    patternScan = await candlestickPatternEngine.scanMultiTimeframe(tokenAddress, chain);
  } catch {
    patternScan = undefined;
  }

  // Run behavioral prediction for STANDARD and DEEP modes
  let behavioralPrediction;
  if (depth !== 'QUICK') {
    try {
      const { behavioralModelEngine } = await import('@/lib/services/brain/behavioral-model-engine');
      behavioralPrediction = await behavioralModelEngine.predictBehavior(tokenAddress, chain);
    } catch {
      behavioralPrediction = undefined;
    }
  }

  // Build DeepAnalysisInput directly — preserves full brainAnalysis with
  // anomalyDetected, isTransitioning, whaleConfidence, volatilityRegime, etc.
  const deepInput: DeepAnalysisInput = {
    tokenAddress,
    symbol: brainResult.symbol || token.symbol,
    chain,
    brainAnalysis: brainResult,
    patternScan,
    behavioralPrediction,
    depth: depth as ThinkingDepth,
  };

  // Dynamically import heavy service
  const { deepAnalysisEngine } = await import('@/lib/services/strategy/deep-analysis-engine');

  // Run deep analysis — engine detects DeepAnalysisInput (no 'currentPrice')
  // and uses brainAnalysis directly instead of building a synthetic one
  return await deepAnalysisEngine.analyze(deepInput);
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { tokenAddress, chain = 'SOL', depth = 'STANDARD', autoDetect = false } = body;

    if (!tokenAddress) {
      return NextResponse.json({ error: 'tokenAddress required' }, { status: 400 });
    }

    // Get token data from DB (try all chains if autoDetect)
    let token: Awaited<ReturnType<typeof db.token.findUnique>> = null;
    if (autoDetect) {
      // Try finding the token in DB by address across all chains
      token = await db.token.findFirst({
        where: { address: tokenAddress },
        include: { dna: true },
      });
    } else {
      token = await db.token.findUnique({
        where: { address: tokenAddress },
        include: { dna: true },
      });
    }

    if (token) {
      // Token found in DB — run full analysis (use token's actual chain)
      const tokenChain = token.chain || chain;
      const result = await runAnalysis(token, tokenChain, depth);
      return NextResponse.json({ success: true, analysis: transformToDeepAnalysis(result) });
    }

    // Token NOT in DB — try DexScreener
    try {
      const { dexScreenerClient } = await import('@/lib/services/data-sources/dexscreener-client');
      const pairs = await dexScreenerClient.searchTokenPairs(tokenAddress);
      const pairData = pairs?.[0];

      if (pairData) {
        // Upsert token from DexScreener data
        const upserted = await db.token.upsert({
          where: { address: pairData.baseToken?.address || tokenAddress },
          update: {
            priceUsd: parseFloat(pairData.priceUsd || '0'),
            volume24h: pairData.volume?.h24 || 0,
            liquidity: pairData.liquidity?.usd || 0,
            priceChange24h: pairData.priceChange?.h24 || 0,
          },
          create: {
            address: pairData.baseToken?.address || tokenAddress,
            symbol: pairData.baseToken?.symbol || 'UNKNOWN',
            name: pairData.baseToken?.name || 'Unknown Token',
            chain: (pairData.chainId || chain).toUpperCase(),
            priceUsd: parseFloat(pairData.priceUsd || '0'),
            volume24h: pairData.volume?.h24 || 0,
            liquidity: pairData.liquidity?.usd || 0,
            marketCap: pairData.marketCap || 0,
            priceChange5m: pairData.priceChange?.m5 || 0,
            priceChange1h: pairData.priceChange?.h1 || 0,
            priceChange6h: pairData.priceChange?.h6 || 0,
            priceChange24h: pairData.priceChange?.h24 || 0,
            pairAddress: pairData.pairAddress || null,
            dexId: pairData.dexId || null,
          },
          include: { dna: true },
        });

        const result = await runAnalysis(upserted, chain, depth);
        return NextResponse.json({ success: true, analysis: transformToDeepAnalysis(result), source: 'dexscreener' });
      }
    } catch (fetchError) {
      console.warn('[DeepAnalysis] DexScreener lookup failed, using synthetic data:', fetchError);
    }

    // Final fallback: create synthetic token for analysis
    const syntheticToken = {
      id: 'synthetic',
      address: tokenAddress,
      symbol: tokenAddress.slice(0, 8).toUpperCase(),
      name: 'Unknown Token',
      chain: chain.toUpperCase(),
      priceUsd: 0,
      priceChange24h: 0,
      dna: null,
    };

    const result = await runAnalysis(syntheticToken as any, chain, depth);
    return NextResponse.json({ success: true, analysis: transformToDeepAnalysis(result), synthetic: true });
  } catch (error) {
    console.error('[DeepAnalysis API] Error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal error' },
      { status: 500 }
    );
  }
}

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const tokenAddress = searchParams.get('token');
    const chain = searchParams.get('chain') || 'SOL';
    const depth = (searchParams.get('depth') || 'STANDARD') as ThinkingDepth;

    if (!tokenAddress) {
      return NextResponse.json({ error: 'token parameter required' }, { status: 400 });
    }

    // Reuse POST logic via internal call
    const req = new NextRequest(new URL(request.url), {
      method: 'POST',
      body: JSON.stringify({ tokenAddress, chain, depth }),
    });
    return POST(req);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal error' },
      { status: 500 }
    );
  }
}
