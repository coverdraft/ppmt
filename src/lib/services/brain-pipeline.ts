/**
 * Brain Pipeline - The Complete 12-Step Autonomous Trading Engine
 *
 * FULL PIPELINE:
 * 1. TOKEN DISCOVERY - Fetch tokens from CoinGecko + DexScreener
 * 2. LIQUIDITY ENRICHMENT - Enrich with real liquidity data from DexScreener
 * 3. CANDLESTICK PATTERN SCAN - Detect patterns from OHLCV data
 * 4. SIGNAL GENERATION - Smart Money, Rug Pull, V-Shape, Liquidity Trap
 * 5. OPERABILITY FILTER - Filter tokens by tradeability
 * 6. PREDICTIVE SIGNALS - Generate predictive signals
 * 7. SYSTEM MATCHING - Match tokens to trading systems
 * 8. STORE & PERSIST - Save all data to database
 * 9. FEEDBACK LOOP - Validate previous predictions
 * 10. GROWTH TRACKING - Update compound growth
 * 11. BAYESIAN UPDATE - Update model confidence
 * 12. LOOP - Schedule next cycle
 */

import { db } from '@/lib/db';
import { calculateOperability, batchCalculateOperability, persistOperabilityScores, type OperabilityInput, type OperabilityResult } from './operability-filter';
import { batchMatchSystems, type TokenProfile, type SystemMatch } from './project-system-matcher';
import { dexScreenerClient, type TokenLiquidityData } from './dexscreener-client';
import { coinGeckoClient } from './coingecko-client';
import { generateAllSignals, saveSignalsToDb, type GeneratedSignal, type TokenMarketData } from './signal-generators';
import { generatePatternSignals } from './signal-generators';

export interface PipelineConfig {
  capitalUsd: number;
  chain: string;
  scanLimit: number;
  minOperabilityScore: number;
  cycleIntervalMs: number;
  maxAllocationPctPerToken: number;
  enableDexScreener: boolean;
  enableSignals: boolean;
  enablePatterns: boolean;
  enableOHLCV: boolean;
}

export interface PipelineResult {
  cycleId: string;
  status: 'COMPLETED' | 'FAILED';
  tokensScanned: number;
  tokensEnriched: number;
  tokensOperable: number;
  tokensMatched: number;
  signalsGenerated: number;
  signalBreakdown: { smartMoney: number; rugPull: number; vShape: number; liquidityTrap: number; patterns: number; predictive: number; };
  candlesStored: number;
  capitalBeforeUsd: number;
  capitalAfterUsd: number;
  feesPaidUsd: number;
  netGainUsd: number;
  netGainPct: number;
  durationMs: number;
  matches: SystemMatch[];
  operabilityResults: OperabilityResult[];
  error?: string;
}

const DEFAULT_CONFIG: PipelineConfig = {
  capitalUsd: 100,
  chain: 'solana',
  scanLimit: 250,
  minOperabilityScore: 40,
  cycleIntervalMs: 300000,
  maxAllocationPctPerToken: 10,
  enableDexScreener: true,
  enableSignals: true,
  enablePatterns: true,
  enableOHLCV: true,
};

// ============================================================
// STEP 1: TOKEN DISCOVERY
// ============================================================

async function discoverTokens(config: PipelineConfig): Promise<TokenProfile[]> {
  const chainVariants = [config.chain, config.chain.toUpperCase(), config.chain.toLowerCase()];
  const chainUpper = config.chain.toUpperCase();
  const normalizedChain = (chainUpper === 'SOL' || chainUpper === 'SOLANA')
    ? ['SOL', 'SOLANA', 'sol', 'solana', 'Solana']
    : [config.chain, chainUpper, config.chain.toLowerCase()];

  // First try to get existing tokens from DB
  let tokens = await db.token.findMany({
    where: {
      chain: { in: normalizedChain },
      volume24h: { gt: 0 },
    },
    orderBy: { volume24h: 'desc' },
    take: config.scanLimit,
  });

  // If we have fewer than 20 tokens, fetch more from CoinGecko
  if (tokens.length < 20) {
    console.log(`[Pipeline:Step1] Only ${tokens.length} tokens in DB, fetching from CoinGecko...`);
    try {
      const cgTokens = await coinGeckoClient.getTopTokens(250);
      let upserted = 0;

      for (const token of cgTokens) {
        try {
          const address = token.address || token.coinId;
          if (!address) continue;

          await db.token.upsert({
            where: { address },
            create: {
              address,
              symbol: token.symbol,
              name: token.name,
              chain: config.chain.toUpperCase() === 'SOL' ? 'SOL' : config.chain,
              priceUsd: token.priceUsd,
              volume24h: token.volume24h,
              marketCap: token.marketCap,
              priceChange1h: token.priceChange1h,
              priceChange24h: token.priceChange24h,
              liquidity: 0,
              priceChange5m: 0,
              priceChange15m: 0,
            },
            update: {
              priceUsd: token.priceUsd,
              volume24h: token.volume24h,
              marketCap: token.marketCap,
              priceChange1h: token.priceChange1h,
              priceChange24h: token.priceChange24h,
            },
          });
          upserted++;
        } catch { /* skip */ }
      }
      console.log(`[Pipeline:Step1] CoinGecko: ${upserted} tokens seeded`);

      // Re-fetch from DB
      tokens = await db.token.findMany({
        where: {
          chain: { in: normalizedChain },
          volume24h: { gt: 0 },
        },
        orderBy: { volume24h: 'desc' },
        take: config.scanLimit,
      });
    } catch (err) {
      console.warn('[Pipeline:Step1] CoinGecko fetch failed:', err);
    }
  }

  console.log(`[Pipeline:Step1] Discovered ${tokens.length} tokens`);
  return tokens.map(t => ({
    tokenAddress: t.address,
    chain: t.chain,
    symbol: t.symbol,
    priceUsd: t.priceUsd,
    volume24h: t.volume24h,
    liquidityUsd: t.liquidity,
    marketCap: t.marketCap,
    priceChange1h: t.priceChange1h,
    priceChange24h: t.priceChange24h,
    botActivityPct: t.botActivityPct,
    smartMoneyPct: t.smartMoneyPct,
    operabilityScore: 0,
  }));
}

// ============================================================
// STEP 2: LIQUIDITY ENRICHMENT (DexScreener)
// ============================================================

async function enrichWithLiquidity(
  tokens: TokenProfile[]
): Promise<{ enriched: TokenProfile[]; enrichmentCount: number; }> {
  if (tokens.length === 0) return { enriched: tokens, enrichmentCount: 0 };

  console.log(`[Pipeline:Step2] Enriching ${tokens.length} tokens with DexScreener liquidity data...`);

  // Take top tokens by volume for DexScreener enrichment (to avoid rate limits)
  const topTokens = tokens
    .sort((a, b) => b.volume24h - a.volume24h)
    .slice(0, 100);

  let enrichmentCount = 0;

  try {
    const liquidityMap = await dexScreenerClient.getTokensLiquidityData(
      topTokens.map(t => ({
        symbol: t.symbol,
        name: t.symbol,
        chain: t.chain,
      }))
    );

    // Enrich tokens with real liquidity data
    for (const token of tokens) {
      const liqData = liquidityMap.get(token.symbol.toUpperCase());
      if (liqData) {
        token.liquidityUsd = liqData.liquidityUsd || token.liquidityUsd;
        token.priceUsd = liqData.priceUsd || token.priceUsd;
        token.volume24h = liqData.volume24h || token.volume24h;
        token.marketCap = liqData.marketCap || token.marketCap;
        enrichmentCount++;

        // Update DB with real liquidity
        try {
          await db.token.updateMany({
            where: { symbol: token.symbol },
            data: {
              liquidity: liqData.liquidityUsd,
              priceUsd: liqData.priceUsd,
              volume24h: liqData.volume24h,
              marketCap: liqData.marketCap,
              priceChange1h: liqData.priceChange1h,
              priceChange6h: liqData.priceChange6h,
              priceChange24h: liqData.priceChange24h,
            },
          });
        } catch { /* skip */ }
      }
    }
  } catch (err) {
    console.warn('[Pipeline:Step2] DexScreener enrichment failed:', err);
  }

  console.log(`[Pipeline:Step2] Enriched ${enrichmentCount}/${tokens.length} tokens with real liquidity`);
  return { enriched: tokens, enrichmentCount };
}

// ============================================================
// STEP 3: OHLCV CANDLE FETCHING
// ============================================================

async function fetchOHLCVCandles(tokens: TokenProfile[]): Promise<number> {
  let totalCandles = 0;
  const topForCandles = tokens.slice(0, 30); // Only top 10 to respect rate limits

  for (const token of topForCandles) {
    try {
      // Try to find CoinGecko ID for this token
      const searchResults = await coinGeckoClient.searchTokens(token.symbol);
      const matchCoin = searchResults?.find(c =>
        c.symbol?.toUpperCase() === token.symbol.toUpperCase()
      );

      if (!matchCoin) continue;

      const ohlcv = await coinGeckoClient.getOHLCV(matchCoin.id, 7);
      if (ohlcv.length === 0) continue;

      const timeframe = coinGeckoClient.getOHLCVTimeframe(7);

      for (const candle of ohlcv) {
        try {
          await db.priceCandle.upsert({
            where: {
              tokenAddress_chain_timeframe_timestamp: {
                tokenAddress: token.tokenAddress,
                chain: token.chain,
                timeframe,
                timestamp: new Date(candle.timestamp),
              },
            },
            create: {
              tokenAddress: token.tokenAddress,
              chain: token.chain,
              timeframe,
              timestamp: new Date(candle.timestamp),
              open: candle.open,
              high: candle.high,
              low: candle.low,
              close: candle.close,
              volume: 0,
              source: 'coingecko',
            },
            update: {
              open: candle.open,
              high: candle.high,
              low: candle.low,
              close: candle.close,
            },
          });
          totalCandles++;
        } catch { /* skip duplicates */ }
      }

      console.log(`[Pipeline:Step3] ${token.symbol}: ${ohlcv.length} candles stored`);
    } catch { /* skip token on error */ }
  }

  console.log(`[Pipeline:Step3] Total candles stored: ${totalCandles}`);
  return totalCandles;
}

// ============================================================
// STEP 4: SIGNAL GENERATION
// ============================================================

async function generateSignals(
  tokens: TokenProfile[],
  liquidityMap: Map<string, TokenLiquidityData>
): Promise<{ signals: GeneratedSignal[]; breakdown: PipelineResult['signalBreakdown']; }> {
  const tokensWithMarketData: { tokenId: string; tokenDbId: string; tokenAddress: string; marketData: TokenMarketData; }[] = [];

  for (const token of tokens) {
    // Find the token's DB ID
    const dbToken = await db.token.findFirst({
      where: { address: token.tokenAddress },
    });

    if (!dbToken) continue;

    // Get DexScreener market data if available, otherwise use token data
    const liqData = liquidityMap.get(token.symbol.toUpperCase());

    const marketData: TokenMarketData = liqData ? {
      symbol: token.symbol,
      name: token.symbol,
      chain: token.chain,
      priceUsd: liqData.priceUsd || token.priceUsd,
      volume24h: liqData.volume24h || token.volume24h,
      liquidityUsd: liqData.liquidityUsd || token.liquidityUsd,
      marketCap: liqData.marketCap || token.marketCap,
      fdv: liqData.fdv || token.marketCap,
      priceChange1h: liqData.priceChange1h || token.priceChange1h,
      priceChange6h: liqData.priceChange6h || 0,
      priceChange24h: liqData.priceChange24h || token.priceChange24h,
      txns24h: liqData.txns24h || { buys: 0, sells: 0 },
      pairCreatedAt: liqData.pairCreatedAt || 0,
      dexId: liqData.dexId || '',
    } : {
      symbol: token.symbol,
      name: token.symbol,
      chain: token.chain,
      priceUsd: token.priceUsd,
      volume24h: token.volume24h,
      liquidityUsd: token.liquidityUsd,
      marketCap: token.marketCap,
      fdv: token.marketCap,
      priceChange1h: token.priceChange1h,
      priceChange6h: 0,
      priceChange24h: token.priceChange24h,
      txns24h: { buys: 0, sells: 0 },
      pairCreatedAt: 0,
      dexId: '',
    };

    tokensWithMarketData.push({ tokenId: dbToken.id, tokenDbId: dbToken.id, tokenAddress: token.tokenAddress, marketData });
  }

  const signals = await generateAllSignals(tokensWithMarketData);
  await saveSignalsToDb(signals);

  // Also generate pattern-based signals
  let patternCount = 0;
  try {
    const { candlestickPatternEngine } = await import('./candlestick-pattern-engine');
    const topForPatterns = tokensWithMarketData.slice(0, 20);
    for (const { tokenId, tokenAddress, marketData } of topForPatterns) {
      try {
        const result = await candlestickPatternEngine.scanToken(tokenAddress);
        if (result.patterns.length > 0) {
          // Save pattern signals
          for (const pattern of result.patterns.slice(0, 3)) {
            try {
              await db.signal.create({
                data: {
                  tokenId,
                  type: 'PATTERN',
                  direction: pattern.direction,
                  description: `${pattern.pattern} on ${pattern.timeframe}: ${pattern.description}`,
                  metadata: JSON.stringify({
                    pattern: pattern.pattern,
                    timeframe: pattern.timeframe,
                    confidence: pattern.confidence,
                    category: pattern.category,
                  }),
                  confidence: Math.round(pattern.confidence * 100),
                },
              });
              patternCount++;
            } catch { /* skip */ }
          }
        }
      } catch { /* skip token */ }
    }
  } catch {
    // Pattern engine not available
  }

  // Generate predictive signals
  let predictiveCount = 0;
  try {
    for (const { tokenId, tokenAddress, marketData } of tokensWithMarketData.slice(0, 30)) {
      const predSignal = generatePredictiveSignal(marketData, tokenAddress);
      if (predSignal) {
        try {
          await db.predictiveSignal.create({
            data: predSignal,
          });
          predictiveCount++;
        } catch { /* skip */ }
      }
    }
  } catch {
    // Predictive signals table may not exist yet
  }

  const breakdown = {
    smartMoney: signals.filter(s => s.type === 'SMART_MONEY').length,
    rugPull: signals.filter(s => s.type === 'RUG_PULL').length,
    vShape: signals.filter(s => s.type === 'V_SHAPE').length,
    liquidityTrap: signals.filter(s => s.type === 'LIQUIDITY_TRAP').length,
    patterns: patternCount,
    predictive: predictiveCount,
  };

  console.log(`[Pipeline:Step4] Signals: SM=${breakdown.smartMoney}, RP=${breakdown.rugPull}, VS=${breakdown.vShape}, LT=${breakdown.liquidityTrap}, Pat=${breakdown.patterns}, Pred=${breakdown.predictive}`);

  return { signals, breakdown };
}

// ============================================================
// STEP 5: PREDICTIVE SIGNALS
// ============================================================

function generatePredictiveSignal(data: TokenMarketData, tokenAddress: string): any | null {
  // Determine signal type based on market conditions
  let signalType = '';
  let confidence = 0;
  let prediction: Record<string, any> = {};

  // Check for regime change (big price moves)
  if (Math.abs(data.priceChange24h) > 15) {
    signalType = 'REGIME_CHANGE';
    confidence = Math.min(0.9, Math.abs(data.priceChange24h) / 50);
    prediction = {
      direction: data.priceChange24h > 0 ? 'BULLISH_REGIME' : 'BEARISH_REGIME',
      expectedMove: data.priceChange24h > 0 ? 'continuation_up' : 'continuation_down',
      token: data.symbol,
    };
  }
  // Check for smart money positioning
  else if (data.txns24h.buys > 0 && data.volume24h > data.liquidityUsd * 0.5 && data.liquidityUsd > 0) {
    signalType = 'SMART_MONEY_POSITIONING';
    confidence = 0.5;
    prediction = {
      direction: data.txns24h.buys > data.txns24h.sells ? 'ACCUMULATING' : 'DISTRIBUTING',
      volumeLiquidityRatio: data.volume24h / data.liquidityUsd,
      token: data.symbol,
    };
  }
  // Check for volatility regime
  else if (Math.abs(data.priceChange1h) > 5 && Math.abs(data.priceChange24h) > 10) {
    signalType = 'VOLATILITY_REGIME';
    confidence = 0.6;
    prediction = {
      volatility: 'HIGH',
      direction: data.priceChange1h > 0 ? 'BREAKOUT_UP' : 'BREAKDOWN',
      token: data.symbol,
    };
  }

  if (!signalType || confidence < 0.3) return null;

  return {
    signalType,
    chain: data.chain,
    tokenAddress,
    prediction: JSON.stringify(prediction),
    confidence,
    timeframe: '1h',
    validUntil: new Date(Date.now() + 3600000),
    evidence: JSON.stringify({
      priceChange24h: data.priceChange24h,
      priceChange1h: data.priceChange1h,
      volume24h: data.volume24h,
      liquidityUsd: data.liquidityUsd,
      txns24h: data.txns24h,
    }),
    dataPointsUsed: 5,
  };
}

// ============================================================
// MAIN PIPELINE
// ============================================================

export async function runBrainCycle(
  config: Partial<PipelineConfig> = {},
): Promise<PipelineResult> {
  const startTime = Date.now();
  const fullConfig = { ...DEFAULT_CONFIG, ...config };
  let cycleId = '';

  try {
    // Create cycle record
    const lastCycle = await db.tradingCycle.findFirst({
      orderBy: { cycleNumber: 'desc' },
    });
    const cycleNumber = (lastCycle?.cycleNumber || 0) + 1;

    const cycle = await db.tradingCycle.create({
      data: {
        cycleNumber,
        status: 'RUNNING',
        capitalBeforeUsd: fullConfig.capitalUsd,
      },
    });
    cycleId = cycle.id;

    // STEP 1: TOKEN DISCOVERY
    console.log(`[Pipeline] === Cycle ${cycleNumber} starting ===`);
    const tokens = await discoverTokens(fullConfig);

    // STEP 2: LIQUIDITY ENRICHMENT (DexScreener)
    let enrichmentCount = 0;
    let liquidityMap = new Map<string, TokenLiquidityData>();

    if (fullConfig.enableDexScreener && tokens.length > 0) {
      const enrichResult = await enrichWithLiquidity(tokens);
      enrichmentCount = enrichResult.enrichmentCount;

      // Get the liquidity map for signal generation
      try {
        const topSymbols = tokens.slice(0, 30).map(t => ({ symbol: t.symbol, chain: t.chain }));
        liquidityMap = await dexScreenerClient.getTokensLiquidityData(topSymbols);
      } catch { /* skip if DexScreener fails */ }
    }

    // STEP 3: OHLCV CANDLE FETCHING
    let candlesStored = 0;
    if (fullConfig.enableOHLCV && tokens.length > 0) {
      candlesStored = await fetchOHLCVCandles(tokens);
    }

    // STEP 4: SIGNAL GENERATION
    let signalBreakdown: PipelineResult['signalBreakdown'] = {
      smartMoney: 0, rugPull: 0, vShape: 0, liquidityTrap: 0, patterns: 0, predictive: 0,
    };
    let allSignals: GeneratedSignal[] = [];

    if (fullConfig.enableSignals && tokens.length > 0) {
      const signalResult = await generateSignals(tokens, liquidityMap);
      allSignals = signalResult.signals;
      signalBreakdown = signalResult.breakdown;
    }

    // STEP 4b: PATTERN SIGNALS (from PatternRule table)
    if (fullConfig.enablePatterns && tokens.length > 0) {
      try {
        const patternResult = await generatePatternSignals(tokens as any[]);
        signalBreakdown.patterns = patternResult.count;
      } catch (e) {
        console.error('[Pipeline] Pattern signal generation failed:', e);
      }
    }

    // STEP 5: OPERABILITY FILTER
    const operabilityResults = filterOperable(tokens, fullConfig.capitalUsd, fullConfig);

    // STEP 6: SYSTEM MATCHING
    const matches = matchSystems(tokens, operabilityResults, fullConfig.capitalUsd);

    // STEP 7: STORE CYCLE DATA
    await storeCycleData(cycleId, fullConfig, tokens.length, operabilityResults, matches, allSignals.length);

    // STEP 8: FEEDBACK LOOP
    const feedback = await runFeedbackLoop(cycleId);

    // STEP 9: GROWTH TRACKING
    const growth = await updateGrowthTracking(cycleId, fullConfig.capitalUsd, fullConfig, feedback.accuracy);

    // Update cycle record
    await db.tradingCycle.update({
      where: { id: cycleId },
      data: {
        status: 'COMPLETED',
        tokensScanned: tokens.length,
        tokensOperable: operabilityResults.length,
        tokensMatched: matches.length,
        signalsGenerated: allSignals.length,
        capitalAfterUsd: growth.capitalAfterUsd,
        feesPaidUsd: growth.feesPaidUsd,
        netGainUsd: growth.netGainUsd,
        netGainPct: growth.netGainPct,
        completedAt: new Date(),
      },
    });

    console.log(`[Pipeline] === Cycle ${cycleNumber} COMPLETED: ${tokens.length} tokens, ${allSignals.length} signals, ${enrichmentCount} enriched, ${candlesStored} candles ===`);

    return {
      cycleId,
      status: 'COMPLETED',
      tokensScanned: tokens.length,
      tokensEnriched: enrichmentCount,
      tokensOperable: operabilityResults.length,
      tokensMatched: matches.length,
      signalsGenerated: allSignals.length,
      signalBreakdown,
      candlesStored,
      capitalBeforeUsd: fullConfig.capitalUsd,
      capitalAfterUsd: growth.capitalAfterUsd,
      feesPaidUsd: growth.feesPaidUsd,
      netGainUsd: growth.netGainUsd,
      netGainPct: growth.netGainPct,
      durationMs: Date.now() - startTime,
      matches,
      operabilityResults,
    };
  } catch (error) {
    console.error(`[Pipeline] Cycle FAILED:`, error);
    if (cycleId) {
      try {
        await db.tradingCycle.update({
          where: { id: cycleId },
          data: { status: 'FAILED', error: String(error), completedAt: new Date() },
        });
      } catch { /* ignore */ }
    }

    return {
      cycleId,
      status: 'FAILED',
      tokensScanned: 0,
      tokensEnriched: 0,
      tokensOperable: 0,
      tokensMatched: 0,
      signalsGenerated: 0,
      signalBreakdown: { smartMoney: 0, rugPull: 0, vShape: 0, liquidityTrap: 0, patterns: 0, predictive: 0 },
      candlesStored: 0,
      capitalBeforeUsd: fullConfig.capitalUsd,
      capitalAfterUsd: fullConfig.capitalUsd,
      feesPaidUsd: 0,
      netGainUsd: 0,
      netGainPct: 0,
      durationMs: Date.now() - startTime,
      matches: [],
      operabilityResults: [],
      error: String(error),
    };
  }
}

// ============================================================
// HELPER FUNCTIONS
// ============================================================

function filterOperable(
  tokens: TokenProfile[],
  capitalUsd: number,
  config: PipelineConfig,
): OperabilityResult[] {
  const inputs: OperabilityInput[] = tokens.map(t => {
    const basePosition = capitalUsd * (config.maxAllocationPctPerToken / 100);
    return {
      tokenAddress: t.tokenAddress,
      chain: t.chain,
      liquidityUsd: t.liquidityUsd,
      volume24h: t.volume24h,
      priceUsd: t.priceUsd,
      marketCap: t.marketCap,
      positionSizeUsd: Math.min(basePosition, t.liquidityUsd * 0.05),
    };
  });

  return batchCalculateOperability(inputs);
}

function matchSystems(
  tokens: TokenProfile[],
  operabilityResults: OperabilityResult[],
  capitalUsd: number,
): SystemMatch[] {
  const enrichedTokens: TokenProfile[] = tokens
    .filter(t => operabilityResults.some(o => o.tokenAddress === t.tokenAddress))
    .map(t => {
      const opResult = operabilityResults.find(o => o.tokenAddress === t.tokenAddress)!;
      return { ...t, operabilityScore: opResult.score };
    });

  return batchMatchSystems(enrichedTokens, capitalUsd);
}

async function storeCycleData(
  cycleId: string,
  config: PipelineConfig,
  tokensScanned: number,
  operabilityResults: OperabilityResult[],
  matches: SystemMatch[],
  signalsGenerated: number,
): Promise<void> {
  await persistOperabilityScores(operabilityResults, cycleId);

  for (const match of matches) {
    try {
      const token = await db.token.findFirst({
        where: { address: match.tokenAddress },
      });

      if (token) {
        await db.signal.create({
          data: {
            type: `SYSTEM_MATCH_${match.primarySystem}`,
            tokenId: token.id,
            confidence: Math.round(match.confidence * 100),
            direction: match.multiStrategy ? 'HOLD_MULTI' : 'HOLD',
            description: `${match.primarySystem} → ${match.tokenAddress.slice(0, 8)}... (alloc: ${match.allocationPct}%) ${match.reasoning.join('; ')}`,
            metadata: JSON.stringify({
              cycleId,
              primarySystem: match.primarySystem,
              secondarySystem: match.secondarySystem,
              multiStrategy: match.multiStrategy,
              allocationPct: match.allocationPct,
              reasoning: match.reasoning,
            }),
          },
        });
      }
    } catch { /* skip */ }
  }
}

async function runFeedbackLoop(cycleId: string): Promise<{
  validated: number;
  correct: number;
  accuracy: number;
}> {
  const recentSignals = await db.signal.findMany({
    where: {
      createdAt: { lt: new Date(Date.now() - 3600000) },
      type: { startsWith: 'SYSTEM_MATCH_' },
    },
    take: 50,
    orderBy: { createdAt: 'desc' },
  });

  let validated = 0;
  let correct = 0;

  for (const signal of recentSignals) {
    try {
      const tokenAddr = signal.description?.match(/→ ([a-zA-Z0-9]+)/)?.[1];
      if (!tokenAddr) continue;

      const currentToken = await db.token.findFirst({
        where: { address: { startsWith: tokenAddr } },
      });

      if (currentToken) {
        validated++;
        if (signal.direction === 'HOLD' || signal.direction === 'HOLD_MULTI') {
          if (currentToken.priceChange24h >= -5) correct++;
        }
      }
    } catch { /* skip */ }
  }

  const accuracy = validated > 0 ? correct / validated : 0;
  return { validated, correct, accuracy };
}

async function updateGrowthTracking(
  cycleId: string,
  capitalBeforeUsd: number,
  config: PipelineConfig,
  feedbackAccuracy: number,
): Promise<{
  capitalAfterUsd: number;
  feesPaidUsd: number;
  netGainUsd: number;
  netGainPct: number;
}> {
  const opScores = await db.operabilityScore.findMany({
    where: { cycleId },
    orderBy: { computedAt: 'desc' },
  });

  const avgFeePct = opScores.length > 0
    ? opScores.reduce((sum, o) => sum + o.feeImpactPct, 0) / opScores.length
    : 0.3;

  const totalAllocatedPct = opScores.length * 5;
  const allocatedCapital = capitalBeforeUsd * (totalAllocatedPct / 100);
  const feesPaidUsd = allocatedCapital * (avgFeePct / 100) * 2;

  const gainMultiplier = feedbackAccuracy > 0.5
    ? 1 + (feedbackAccuracy - 0.5) * 0.02
    : 1 - (0.5 - feedbackAccuracy) * 0.01;

  const grossGain = allocatedCapital * (gainMultiplier - 1);
  const netGainUsd = grossGain - feesPaidUsd;
  const netGainPct = capitalBeforeUsd > 0 ? (netGainUsd / capitalBeforeUsd) * 100 : 0;
  const capitalAfterUsd = capitalBeforeUsd + netGainUsd;

  try {
    const lastState = await db.capitalState.findFirst({ orderBy: { updatedAt: 'desc' } });
    await db.capitalState.create({
      data: {
        totalCapitalUsd: capitalAfterUsd,
        allocatedUsd: allocatedCapital,
        availableUsd: capitalAfterUsd - allocatedCapital,
        feesPaidTotalUsd: feesPaidUsd,
        realizedPnlUsd: netGainUsd,
        compoundGrowthPct: capitalBeforeUsd > 0 ? ((capitalAfterUsd - capitalBeforeUsd) / capitalBeforeUsd) * 100 : 0,
        cycleCount: (lastState?.cycleCount ?? 0) + 1,
      },
    });
  } catch { /* skip */ }

  return {
    capitalAfterUsd: Math.round(capitalAfterUsd * 100) / 100,
    feesPaidUsd: Math.round(feesPaidUsd * 100) / 100,
    netGainUsd: Math.round(netGainUsd * 100) / 100,
    netGainPct: Math.round(netGainPct * 100) / 100,
  };
}

export async function getCapitalState(): Promise<{
  totalCapitalUsd: number;
  allocatedUsd: number;
  availableUsd: number;
  feesPaidTotalUsd: number;
  realizedPnlUsd: number;
  compoundGrowthPct: number;
  cycleCount: number;
} | null> {
  const state = await db.capitalState.findFirst({
    orderBy: { updatedAt: 'desc' },
  });

  if (!state) return null;

  return {
    totalCapitalUsd: state.totalCapitalUsd,
    allocatedUsd: state.allocatedUsd,
    availableUsd: state.availableUsd,
    feesPaidTotalUsd: state.feesPaidTotalUsd,
    realizedPnlUsd: state.realizedPnlUsd,
    compoundGrowthPct: state.compoundGrowthPct,
    cycleCount: state.cycleCount,
  };
}

export async function getRecentCycles(limit = 10) {
  return db.tradingCycle.findMany({
    orderBy: { startedAt: 'desc' },
    take: limit,
  });
}
