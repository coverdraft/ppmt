import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// TYPES
// ============================================================

type Regime = 'BULL' | 'BEAR' | 'SIDEWAYS' | 'TRANSITION';
type VolatilityRegime = 'LOW' | 'NORMAL' | 'HIGH' | 'EXTREME';
type BotSwarmLevel = 'NONE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
type WhaleDirection = 'ACCUMULATING' | 'DISTRIBUTING' | 'NEUTRAL' | 'ROTATING';
type SmartMoneyFlow = 'INFLOW' | 'OUTFLOW' | 'NEUTRAL';
type LiquidityTrend = 'ACCUMULATING' | 'STABLE' | 'DRAINING' | 'CRITICAL_DRAIN';

type DataSource = 'live' | 'computed' | 'fallback';

interface MarketContextData {
  regime: Regime;
  volatilityRegime: VolatilityRegime;
  botSwarmLevel: BotSwarmLevel;
  whaleDirection: WhaleDirection;
  smartMoneyFlow: SmartMoneyFlow;
  liquidityTrend: LiquidityTrend;
  correlationStability: number;
  tokenCount: number;
  signalCount: number;
  chains: string[];
  computedAt: string;
  source: DataSource;
  liveTokenCount: number;
  activeTokenCount: number;
  signalBreakdown: Record<string, number>;
}

interface CachedResult {
  data: MarketContextData;
  timestamp: number;
}

// ============================================================
// 30-SECOND CACHE
// ============================================================

const CACHE_TTL_MS = 30_000;
let cachedResult: CachedResult | null = null;

// ============================================================
// DEXSCREENER CLIENT (reuse across calls)
// ============================================================

// Use canonical DexScreenerClient singleton (with caching + rate limiting)
import { dexScreenerClient } from '@/lib/services/data-sources/dexscreener-client';
import type { DexScreenerPair } from '@/lib/services/data-sources/dexscreener-client';

// ============================================================
// LIVE DATA ENRICHMENT
// ============================================================

interface LiveTokenSnapshot {
  priceChange1h: number;
  priceChange24h: number;
  volume24h: number;
  liquidity: number;
  chainId: string;
}

/**
 * Fetch live token data from DexScreener using multiple strategies:
 * 1. Trending tokens (search)
 * 2. Boosted tokens (paid visibility)
 *
 * Returns a map keyed by BOTH pairAddress AND baseToken.address
 * for maximum match probability with DB tokens.
 */
async function fetchLiveDexData(): Promise<{
  byPairAddress: Map<string, LiveTokenSnapshot>;
  byTokenAddress: Map<string, LiveTokenSnapshot>;
}> {
  const byPairAddress = new Map<string, LiveTokenSnapshot>();
  const byTokenAddress = new Map<string, LiveTokenSnapshot>();

  try {
    // Strategy 1: Trending search
    const [trendingPairs, boostedPairs] = await Promise.all([
      Promise.resolve(dexScreenerClient).then(ds => ds.searchTokenByName('trending')).catch(() => [] as DexScreenerPair[]),
      Promise.resolve(dexScreenerClient).then(ds => ds.getBoostedTokens()).catch(() => []),
    ]);

    const allPairs = [...trendingPairs, ...boostedPairs];

    // Deduplicate by pairAddress
    const seen = new Set<string>();
    for (const p of allPairs) {
      if (seen.has(p.pairAddress)) continue;
      seen.add(p.pairAddress);

      const snapshot: LiveTokenSnapshot = {
        priceChange1h: p.priceChange?.h1 ?? 0,
        priceChange24h: p.priceChange?.h24 ?? 0,
        volume24h: p.volume?.h24 ?? 0,
        liquidity: p.liquidity?.usd ?? 0,
        chainId: p.chainId,
      };

      // Index by pairAddress
      byPairAddress.set(p.pairAddress, snapshot);

      // Index by baseToken.address (lowercased for case-insensitive match)
      if (p.baseToken?.address) {
        byTokenAddress.set(p.baseToken.address.toLowerCase(), snapshot);
      }
    }
  } catch (err) {
    console.error('[/api/market/context] DexScreener live fetch failed:', err);
  }

  return { byPairAddress, byTokenAddress };
}

// ============================================================
// COMPUTATION FUNCTIONS
// ============================================================

/**
 * 1. REGIME DETECTION
 *
 * Looks at aggregate price changes across all tracked tokens.
 * - >60% tokens with positive 1h change → BULL
 * - >60% tokens with negative 1h change → BEAR
 * - Mixed with no clear direction → SIDEWAYS
 * - Extreme divergence between chains → TRANSITION
 *
 * Also cross-validates with REGIME_CHANGE predictive signals.
 */
function computeRegime(
  tokens: { priceChange1h: number; chain: string }[],
  regimeSignals: { prediction: string; confidence: number }[] = [],
): Regime {
  if (tokens.length === 0) return 'SIDEWAYS';

  const positive = tokens.filter((t) => t.priceChange1h > 0).length;
  const negative = tokens.filter((t) => t.priceChange1h < 0).length;
  const total = tokens.length;
  const posRatio = positive / total;
  const negRatio = negative / total;

  // Check for chain divergence (TRANSITION)
  const chains = [...new Set(tokens.map((t) => t.chain))];
  if (chains.length >= 2) {
    const chainDirections: Record<string, number> = {};
    for (const chain of chains) {
      const chainTokens = tokens.filter((t) => t.chain === chain);
      const avgChange =
        chainTokens.reduce((s, t) => s + t.priceChange1h, 0) /
        (chainTokens.length || 1);
      chainDirections[chain] = avgChange;
    }

    const directionValues = Object.values(chainDirections);
    const maxDir = Math.max(...directionValues);
    const minDir = Math.min(...directionValues);

    // Extreme divergence: one chain strongly positive, another strongly negative
    if (maxDir > 3 && minDir < -3 && maxDir - minDir > 8) {
      return 'TRANSITION';
    }
  }

  // Check predictive signals for regime change hints
  let signalBullScore = 0;
  let signalBearScore = 0;
  for (const signal of regimeSignals) {
    try {
      const pred = JSON.parse(signal.prediction) as Record<string, unknown>;
      const toRegime = pred.toRegime as string | undefined;
      const weighted = signal.confidence;
      if (toRegime === 'BULL') signalBullScore += weighted;
      else if (toRegime === 'BEAR') signalBearScore += weighted;
    } catch {
      // skip malformed
    }
  }

  // If signals strongly disagree with token data, lean toward signals
  if (posRatio > 0.6 && signalBearScore > signalBullScore * 2 && signalBearScore > 1) {
    return 'TRANSITION';
  }
  if (negRatio > 0.6 && signalBullScore > signalBearScore * 2 && signalBullScore > 1) {
    return 'TRANSITION';
  }

  if (posRatio > 0.6) return 'BULL';
  if (negRatio > 0.6) return 'BEAR';
  return 'SIDEWAYS';
}

/**
 * 2. VOLATILITY REGIME
 *
 * From average |priceChange1h| across tokens.
 * - avg < 2% → LOW
 * - avg 2-5% → NORMAL
 * - avg 5-10% → HIGH
 * - avg > 10% → EXTREME
 *
 * Cross-validates with VOLATILITY_REGIME predictive signals.
 */
function computeVolatilityRegime(
  tokens: { priceChange1h: number }[],
  volSignals: { prediction: string; confidence: number }[] = [],
): VolatilityRegime {
  if (tokens.length === 0) return 'NORMAL';

  const avgAbsChange =
    tokens.reduce((s, t) => s + Math.abs(t.priceChange1h), 0) / tokens.length;

  // Base computation from token data
  let result: VolatilityRegime;
  if (avgAbsChange < 2) result = 'LOW';
  else if (avgAbsChange < 5) result = 'NORMAL';
  else if (avgAbsChange < 10) result = 'HIGH';
  else result = 'EXTREME';

  // Cross-validate with VOLATILITY_REGIME signals
  if (volSignals.length > 0) {
    try {
      const latestPred = JSON.parse(volSignals[0].prediction) as Record<string, unknown>;
      const predicted = latestPred.predicted as string | undefined;
      if (predicted && ['LOW', 'NORMAL', 'HIGH', 'EXTREME'].includes(predicted)) {
        // If signal confidence is high and predicts different regime, use signal
        if (volSignals[0].confidence > 0.7) {
          result = predicted as VolatilityRegime;
        }
      }
    } catch {
      // skip malformed
    }
  }

  return result;
}

/**
 * 3. BOT SWARM LEVEL
 *
 * From average botActivityPct across tokens.
 * - avg < 10% → NONE
 * - avg 10-20% → LOW
 * - avg 20-30% → MEDIUM
 * - avg 30-40% → HIGH
 * - avg > 40% → CRITICAL
 *
 * Cross-validates with BOT_SWARM predictive signals.
 */
function computeBotSwarmLevel(
  tokens: { botActivityPct: number }[],
  botSignals: { prediction: string; confidence: number }[] = [],
): BotSwarmLevel {
  if (tokens.length === 0) return 'NONE';

  const avg =
    tokens.reduce((s, t) => s + t.botActivityPct, 0) / tokens.length;

  // Base computation from token data
  let result: BotSwarmLevel;
  if (avg < 10) result = 'NONE';
  else if (avg < 20) result = 'LOW';
  else if (avg < 30) result = 'MEDIUM';
  else if (avg < 40) result = 'HIGH';
  else result = 'CRITICAL';

  // Cross-validate with BOT_SWARM signals
  if (botSignals.length > 0) {
    try {
      const pred = JSON.parse(botSignals[0].prediction) as Record<string, unknown>;
      const level = pred.level as string | undefined;
      if (level && ['NONE', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].includes(level)) {
        if (botSignals[0].confidence > 0.6) {
          result = level as BotSwarmLevel;
        }
      }
    } catch {
      // skip malformed
    }
  }

  return result;
}

/**
 * 4. WHALE DIRECTION
 *
 * From latest WHALE_MOVEMENT signals in DB + smart money accumulation
 * patterns (smartMoneyPct).
 */
function computeWhaleDirection(
  tokens: { smartMoneyPct: number; chain: string }[],
  whaleSignals: { prediction: string; confidence: number }[] = [],
): WhaleDirection {
  if (tokens.length === 0 && whaleSignals.length === 0) return 'NEUTRAL';

  const avgSmartMoneyPct =
    tokens.length > 0
      ? tokens.reduce((s, t) => s + t.smartMoneyPct, 0) / tokens.length
      : 0;

  // Check whale signals for directional hints
  let signalAccumulationScore = 0;
  let signalDistributionScore = 0;

  for (const signal of whaleSignals) {
    try {
      const pred = JSON.parse(signal.prediction) as Record<string, unknown>;
      const direction = pred.direction as string | undefined;
      const weighted = signal.confidence;

      if (direction === 'ACCUMULATING' || direction === 'BUY') {
        signalAccumulationScore += weighted;
      } else if (direction === 'DISTRIBUTING' || direction === 'SELL') {
        signalDistributionScore += weighted;
      } else if (direction === 'ROTATING') {
        signalAccumulationScore += weighted * 0.3;
        signalDistributionScore += weighted * 0.3;
      }
    } catch {
      // Malformed prediction JSON — skip
    }
  }

  // Check for sector rotation in smart money across chains
  const chains = [...new Set(tokens.map((t) => t.chain))];
  if (chains.length >= 2) {
    const chainSmartMoney: Record<string, number> = {};
    for (const chain of chains) {
      const chainTokens = tokens.filter((t) => t.chain === chain);
      chainSmartMoney[chain] =
        chainTokens.reduce((s, t) => s + t.smartMoneyPct, 0) /
        (chainTokens.length || 1);
    }

    const values = Object.values(chainSmartMoney);
    const maxSm = Math.max(...values);
    const minSm = Math.min(...values);

    if (maxSm - minSm > 15 && maxSm > 12) {
      return 'ROTATING';
    }
  }

  // Combine smart money % and whale signals
  if (avgSmartMoneyPct > 15 && signalAccumulationScore > signalDistributionScore) {
    return 'ACCUMULATING';
  }
  if (avgSmartMoneyPct > 15) {
    return 'ACCUMULATING';
  }
  if (avgSmartMoneyPct < 5 && signalDistributionScore > signalAccumulationScore) {
    return 'DISTRIBUTING';
  }
  if (avgSmartMoneyPct < 5) {
    return 'DISTRIBUTING';
  }

  if (signalAccumulationScore > signalDistributionScore * 2) {
    return 'ACCUMULATING';
  }
  if (signalDistributionScore > signalAccumulationScore * 2) {
    return 'DISTRIBUTING';
  }

  return 'NEUTRAL';
}

/**
 * 5. SMART MONEY FLOW
 *
 * From aggregate smartMoneyPct + SMART_MONEY_POSITIONING signals.
 */
function computeSmartMoneyFlow(
  tokens: { smartMoneyPct: number }[],
  smSignals: { prediction: string; confidence: number }[] = [],
): SmartMoneyFlow {
  if (tokens.length === 0) return 'NEUTRAL';

  const avg =
    tokens.reduce((s, t) => s + t.smartMoneyPct, 0) / tokens.length;

  // Base from token data
  let result: SmartMoneyFlow;
  if (avg > 12) result = 'INFLOW';
  else if (avg < 5) result = 'OUTFLOW';
  else result = 'NEUTRAL';

  // Cross-validate with signals
  if (smSignals.length > 0) {
    try {
      const pred = JSON.parse(smSignals[0].prediction) as Record<string, unknown>;
      const netDirection = pred.netDirection as string | undefined;
      if (netDirection && ['INFLOW', 'OUTFLOW', 'NEUTRAL'].includes(netDirection)) {
        if (smSignals[0].confidence > 0.6) {
          result = netDirection as SmartMoneyFlow;
        }
      }
    } catch {
      // skip malformed
    }
  }

  return result;
}

/**
 * 6. LIQUIDITY TREND
 *
 * From aggregate liquidity analysis across tokens + LIQUIDITY_DRAIN signals.
 */
function computeLiquidityTrend(
  tokens: { liquidity: number; volume24h: number }[],
  liqSignals: { prediction: string; confidence: number }[] = [],
): LiquidityTrend {
  if (tokens.length === 0) return 'STABLE';

  const avgLiquidity =
    tokens.reduce((s, t) => s + t.liquidity, 0) / tokens.length;
  const avgVolume =
    tokens.reduce((s, t) => s + t.volume24h, 0) / tokens.length;

  // Volume-to-liquidity ratio as a proxy for liquidity health
  const volLiqRatio = avgLiquidity > 0 ? avgVolume / avgLiquidity : 0;

  // Base computation from token data
  let result: LiquidityTrend;
  if (avgLiquidity < 1000) result = 'CRITICAL_DRAIN';
  else if (volLiqRatio > 5) result = 'DRAINING';
  else if (avgLiquidity > 50000 && volLiqRatio < 0.5) result = 'ACCUMULATING';
  else if (volLiqRatio > 2) result = 'DRAINING';
  else result = 'STABLE';

  // Cross-validate with LIQUIDITY_DRAIN signals
  if (liqSignals.length > 0) {
    try {
      const pred = JSON.parse(liqSignals[0].prediction) as Record<string, unknown>;
      const trend = pred.trend as string | undefined;
      if (trend && ['ACCUMULATING', 'STABLE', 'DRAINING', 'CRITICAL_DRAIN'].includes(trend)) {
        if (liqSignals[0].confidence > 0.6) {
          result = trend as LiquidityTrend;
        }
      }
    } catch {
      // skip malformed
    }
  }

  return result;
}

/**
 * 7. CORRELATION STABILITY
 *
 * Computes price correlation between SOL and ETH tokens.
 * Returns a value between 0 and 1.
 *
 * Also considers CORRELATION_BREAK signals.
 */
function computeCorrelationStability(
  tokens: { priceChange1h: number; chain: string }[],
  corrSignals: { prediction: string; confidence: number }[] = [],
): number {
  const solTokens = tokens.filter(
    (t) => t.chain.toUpperCase() === 'SOL',
  );
  const ethTokens = tokens.filter(
    (t) => t.chain.toUpperCase() === 'ETH',
  );

  // Need at least 3 tokens per chain for meaningful correlation
  if (solTokens.length < 3 || ethTokens.length < 3) {
    const chains = [...new Set(tokens.map((t) => t.chain))];
    if (chains.length <= 1) return 1.0;
    return 0.5;
  }

  const solChanges = solTokens.map((t) => t.priceChange1h);
  const ethChanges = ethTokens.map((t) => t.priceChange1h);

  const n = Math.min(solChanges.length, ethChanges.length);
  if (n < 3) return 0.5;

  const xSlice = solChanges.slice(0, n);
  const ySlice = ethChanges.slice(0, n);

  const meanX = xSlice.reduce((s, v) => s + v, 0) / n;
  const meanY = ySlice.reduce((s, v) => s + v, 0) / n;

  let numerator = 0;
  let denomX = 0;
  let denomY = 0;

  for (let i = 0; i < n; i++) {
    const dx = xSlice[i] - meanX;
    const dy = ySlice[i] - meanY;
    numerator += dx * dy;
    denomX += dx * dx;
    denomY += dy * dy;
  }

  const denominator = Math.sqrt(denomX * denomY);
  if (denominator === 0) return 0.5;

  const correlation = numerator / denominator;
  let stability = Math.min(1, Math.max(0, Math.abs(correlation)));

  // If CORRELATION_BREAK signals exist with high confidence, reduce stability
  if (corrSignals.length > 0) {
    const avgConfidence = corrSignals.reduce((s, sig) => s + sig.confidence, 0) / corrSignals.length;
    // Break signals reduce stability proportionally
    stability = stability * (1 - avgConfidence * 0.5);
  }

  return Math.min(1, Math.max(0, stability));
}

// ============================================================
// HELPER: Fetch predictive signals by type
// ============================================================

async function fetchSignalsByTypes(
  signalTypes: string[],
  take = 20,
): Promise<Record<string, { prediction: string; confidence: number }[]>> {
  const result: Record<string, { prediction: string; confidence: number }[]> = {};

  try {
    const { db } = await import('@/lib/db');
    const signals = await db.predictiveSignal.findMany({
      where: {
        signalType: { in: signalTypes },
        OR: [
          { validUntil: null },
          { validUntil: { gte: new Date() } },
        ],
      },
      orderBy: { createdAt: 'desc' },
      take: take * signalTypes.length, // enough per type
      select: {
        signalType: true,
        prediction: true,
        confidence: true,
      },
    });

    for (const signal of signals) {
      if (!result[signal.signalType]) {
        result[signal.signalType] = [];
      }
      result[signal.signalType].push({
        prediction: signal.prediction,
        confidence: signal.confidence,
      });
    }
  } catch (err) {
    console.error('[/api/market/context] Failed to fetch signals:', err);
  }

  return result;
}

// ============================================================
// HELPER: Build market context from tokens + signals
// ============================================================

function buildMarketContext(
  tokens: Array<{
    chain: string;
    priceChange1h: number;
    priceChange24h: number;
    volume24h: number;
    liquidity: number;
    marketCap: number;
    botActivityPct: number;
    smartMoneyPct: number;
  }>,
  signalsMap: Record<string, { prediction: string; confidence: number }[]>,
  source: DataSource,
  liveTokenCount: number,
  activeTokenCount: number,
  chains: string[],
): Omit<MarketContextData, 'tokenCount' | 'signalCount' | 'signalBreakdown'> {
  const regimeSignals = signalsMap['REGIME_CHANGE'] || [];
  const volSignals = signalsMap['VOLATILITY_REGIME'] || [];
  const botSignals = signalsMap['BOT_SWARM'] || [];
  const whaleSignals = signalsMap['WHALE_MOVEMENT'] || [];
  const smSignals = signalsMap['SMART_MONEY_POSITIONING'] || [];
  const liqSignals = signalsMap['LIQUIDITY_DRAIN'] || [];
  const corrSignals = signalsMap['CORRELATION_BREAK'] || [];

  return {
    regime: computeRegime(tokens, regimeSignals),
    volatilityRegime: computeVolatilityRegime(tokens, volSignals),
    botSwarmLevel: computeBotSwarmLevel(tokens, botSignals),
    whaleDirection: computeWhaleDirection(tokens, whaleSignals),
    smartMoneyFlow: computeSmartMoneyFlow(tokens, smSignals),
    liquidityTrend: computeLiquidityTrend(tokens, liqSignals),
    correlationStability: computeCorrelationStability(tokens, corrSignals),
    chains,
    computedAt: new Date().toISOString(),
    source,
    liveTokenCount,
    activeTokenCount,
  };
}

// ============================================================
// MAIN HANDLER
// ============================================================

export async function GET() {
  // Return cached result if still fresh
  if (cachedResult && Date.now() - cachedResult.timestamp < CACHE_TTL_MS) {
    return NextResponse.json({
      data: cachedResult.data,
      error: null,
      source: cachedResult.data.source,
    });
  }

  let source: DataSource = 'live';

  try {
    const { db } = await import('@/lib/db');
    // ---------------------------------------------------------
    // Parallel: fetch DB tokens + ALL signal types + live DexScreener
    // ---------------------------------------------------------

    const signalTypes = [
      'REGIME_CHANGE',
      'BOT_SWARM',
      'WHALE_MOVEMENT',
      'LIQUIDITY_DRAIN',
      'CORRELATION_BREAK',
      'SMART_MONEY_POSITIONING',
      'VOLATILITY_REGIME',
    ];

    const [dbTokens, signalsMap, liveDex] = await Promise.all([
      db.token.findMany({
        take: 500,
        select: {
          chain: true,
          priceChange1h: true,
          priceChange24h: true,
          volume24h: true,
          liquidity: true,
          marketCap: true,
          botActivityPct: true,
          smartMoneyPct: true,
          address: true,
          pairAddress: true,
        },
      }),
      fetchSignalsByTypes(signalTypes, 20),
      fetchLiveDexData(),
    ]);

    // ---------------------------------------------------------
    // Enrich DB tokens with live data where available
    // FIX: Use both pairAddress and token address for matching
    // ---------------------------------------------------------

    let liveTokenCount = 0;
    let activeTokenCount = 0;
    const enrichedTokens = dbTokens.map((t) => {
      // Try matching by pairAddress first, then by token address
      let live = t.pairAddress
        ? liveDex.byPairAddress.get(t.pairAddress)
        : undefined;

      if (!live && t.address) {
        live = liveDex.byTokenAddress.get(t.address.toLowerCase());
      }

      // Count "live" tokens: those with DexScreener real-time match
      // OR tokens with pairAddress (enriched by DexScreener/DexPaprika)
      // OR tokens with liquidity AND volume (have real market data from CoinGecko)
      const isLive = !!(live || t.pairAddress);
      const isActive = !!(t.liquidity > 0 && t.volume24h > 0);

      if (isLive) liveTokenCount++;
      if (isActive) activeTokenCount++;

      return {
        chain: t.chain,
        priceChange1h: live?.priceChange1h ?? t.priceChange1h,
        priceChange24h: live?.priceChange24h ?? t.priceChange24h,
        volume24h: live?.volume24h ?? t.volume24h,
        liquidity: live?.liquidity ?? t.liquidity,
        marketCap: t.marketCap,
        botActivityPct: t.botActivityPct,
        smartMoneyPct: t.smartMoneyPct,
      };
    });

    // Determine source tier — only mark as 'live' if we have enriched tokens
    if (liveTokenCount > 0 || activeTokenCount > 0) {
      source = 'live';
    } else if (liveDex.byPairAddress.size > 0 || liveDex.byTokenAddress.size > 0) {
      source = 'computed';
    } else if (dbTokens.length > 0) {
      source = 'computed';
    } else {
      source = 'fallback';
    }

    // ---------------------------------------------------------
    // Compute all market context metrics (with signal cross-validation)
    // ---------------------------------------------------------

    const chains = [...new Set(dbTokens.map((t) => t.chain.toUpperCase()))] as string[];

    const totalSignalCount = await db.predictiveSignal.count({
      where: {
        OR: [
          { validUntil: null },
          { validUntil: { gte: new Date() } },
        ],
      },
    });

    // Signal breakdown for display
    const signalBreakdown: Record<string, number> = {};
    for (const [type, sigs] of Object.entries(signalsMap)) {
      signalBreakdown[type] = sigs.length;
    }

    const contextBase = buildMarketContext(enrichedTokens, signalsMap, source, liveTokenCount, activeTokenCount, chains);

    const data: MarketContextData = {
      ...contextBase,
      tokenCount: dbTokens.length,
      signalCount: totalSignalCount,
      signalBreakdown,
    };

    // Update cache
    cachedResult = { data, timestamp: Date.now() };

    return NextResponse.json({ data, error: null, source });
  } catch (error) {
    console.error('[/api/market/context] Error computing market context:', error);

    // ---------------------------------------------------------
    // Fallback: try DB-only computation without live data
    // ---------------------------------------------------------

    try {
      const { db: dbFallback } = await import('@/lib/db');
      const signalTypes = [
        'REGIME_CHANGE', 'BOT_SWARM', 'WHALE_MOVEMENT',
        'LIQUIDITY_DRAIN', 'CORRELATION_BREAK',
        'SMART_MONEY_POSITIONING', 'VOLATILITY_REGIME',
      ];

      const [dbTokens, signalsMap] = await Promise.all([
        dbFallback.token.findMany({
          take: 500,
          select: {
            chain: true,
            priceChange1h: true,
            priceChange24h: true,
            volume24h: true,
            liquidity: true,
            marketCap: true,
            botActivityPct: true,
            smartMoneyPct: true,
          },
        }),
        fetchSignalsByTypes(signalTypes, 20),
      ]);

      const chains = [...new Set(dbTokens.map((t) => t.chain.toUpperCase()))] as string[];

      const signalBreakdown: Record<string, number> = {};
      for (const [type, sigs] of Object.entries(signalsMap)) {
        signalBreakdown[type] = sigs.length;
      }

      const totalSignalCount = await dbFallback.predictiveSignal.count({
        where: {
          OR: [
            { validUntil: null },
            { validUntil: { gte: new Date() } },
          ],
        },
      });

      const contextBase = buildMarketContext(dbTokens, signalsMap, 'computed', 0, 0, chains);

      const data: MarketContextData = {
        ...contextBase,
        tokenCount: dbTokens.length,
        signalCount: totalSignalCount,
        signalBreakdown,
      };

      // Update cache even on fallback
      cachedResult = { data, timestamp: Date.now() };

      return NextResponse.json({
        data,
        error: null,
        source: 'computed' as const,
      });
    } catch (dbError) {
      console.error('[/api/market/context] DB fallback also failed:', dbError);

      return NextResponse.json(
        {
          data: null,
          error: 'Failed to compute market context from live source and database',
          source: 'fallback' as const,
        },
        { status: 500 },
      );
    }
  }
}
